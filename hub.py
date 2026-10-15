"""Shared pipeline: fetch -> dedupe -> LLM summarize -> rank -> static build (pre-rendered HTML, archives, RSS, weekly).
Concept inspired by AISafetyHot-Hub (CC BY-NC 4.0) and AIHOT (MIT); code written from scratch."""
import json, os, re, sys, time, math, glob, html, hashlib, difflib, datetime as dt, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET, concurrent.futures as cf
import config as C
try:
    import feedparser
except ImportError:
    feedparser = None
from config import SITE, SOURCES, SECTIONS, PROMPT, FIELDS

UA = {"User-Agent": "Mozilla/5.0 (compatible; HubBot/1.0; +%s)" % SITE["repo"]}
NOW = dt.datetime.now(dt.timezone.utc)
CST = dt.timezone(dt.timedelta(hours=8))
TODAY = NOW.astimezone(CST).strftime("%Y-%m-%d")
KEEP_DAYS = 30
MAX_NEW = int(os.getenv("MAX_NEW", "60"))
TAGS = getattr(C, "TAGS", []); SYN = getattr(C, "TAG_SYNONYMS", {}); WEIGHTS = getattr(C, "SOURCE_WEIGHTS", {})
EV_BONUS = {"已获批": 1.5, "同行评审": 1.0, "预印本": 0.0, "新闻": 0.0}
FRESH_BONUS, FRESH_HALF_LIFE = 2.0, 7  # rank bonus for newly published items, halves every N days
FEATURED_MAX_AGE = 14  # 今日精选 only picks items published within N days of the day they were collected
E = lambda s: html.escape(str(s if s is not None else ""), quote=True)

# ---------------------------------------------------------------- helpers
def get(url, timeout=60, headers=None):
    req = urllib.request.Request(url, headers=dict(UA, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def get_polite(url, tries=4, delay=3):
    """GET honoring 429/503 Retry-After with exponential backoff."""
    for a in range(tries):
        try:
            time.sleep(delay if a else 0)
            return get(url)
        except urllib.error.HTTPError as ex:
            if ex.code not in (429, 503) or a == tries - 1: raise
            wait = int(ex.headers.get("Retry-After") or 0) or delay * 2 ** (a + 1)
            print(f"[backoff] {ex.code} wait {min(wait, 60)}s {url[:60]}", file=sys.stderr); time.sleep(min(wait, 60))

def clean(s, n=900):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]

def norm(t): return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (t or "").lower())[:120]

def kw_ok(text, kws):
    if not kws: return True
    t = (text or "").lower()
    for k in kws:
        k = k.lower()
        if (re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", t) if k.isascii() else k in t): return True
    return False

def bigrams(s):
    s = norm(s); return {s[i:i + 2] for i in range(len(s) - 1)} or {s}

def similar(a, b):
    """True if two titles likely describe the same item/event."""
    na, nb = norm(a), norm(b)
    if not na or not nb: return False
    if na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.88: return True
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / max(1, len(A | B)) >= 0.55

def norm_tags(tags):
    out = []
    for t in tags or []:
        t = SYN.get(str(t).strip(), str(t).strip())
        if t in TAGS and t not in out: out.append(t)
    return out[:3]

def signal(it):
    m = re.search(r"([\d,]+)", it.get("pop") or "")
    return int(m.group(1).replace(",", "")) if m else 0

def age_days(it):
    """Days between publication and the day the item was collected (stable over time, so archive pages don't reshuffle)."""
    try: day = dt.date.fromisoformat(it.get("day") or TODAY)
    except ValueError: day = NOW.astimezone(CST).date()
    return max(0, (day - parse_date(it.get("date") or "").astimezone(CST).date()).days)

def rank(it):
    w = WEIGHTS.get(it.get("source"), WEIGHTS.get(it.get("source_group"), 1.0))
    fresh = FRESH_BONUS * 0.5 ** (age_days(it) / FRESH_HALF_LIFE)
    return round((it.get("score") or 5) + w + EV_BONUS.get(it.get("evidence"), 0) + min(2.0, 0.6 * math.log10(signal(it) + 1)) + fresh, 2)

def top_picks(items, n=5):
    """Highest-ranked recent items; older backfill only fills the gap when there aren't enough recent ones."""
    ranked = sorted(items, key=rank, reverse=True)
    return ([i for i in ranked if age_days(i) <= FEATURED_MAX_AGE] + [i for i in ranked if age_days(i) > FEATURED_MAX_AGE])[:n]

def zh_date(iso):
    try: d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception: return ""
    if d.tzinfo: d = d.astimezone(CST)
    return (f"{d.year}年" if d.year != NOW.astimezone(CST).year else "") + f"{d.month}月{d.day}日"

def parse_date(s):
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception: return NOW

# ---------------------------------------------------------------- fetchers
def f_rss(src):
    f = feedparser.parse(get(src["url"]))
    out = []
    for e in f.entries[: src.get("limit", 40)]:
        pub = e.get("published_parsed") or e.get("updated_parsed")
        d = dt.datetime(*pub[:6], tzinfo=dt.timezone.utc) if pub else NOW
        it = dict(title=clean(e.get("title"), 300), link=e.get("link", ""), desc=clean(e.get("summary", "")), date=d.isoformat())
        m = re.search(r"Points: (\d+)", e.get("summary", ""))
        if m: it["pop"] = f"▲ {m.group(1)} points"
        s = e.get("source") or {}
        if s.get("title"):  # Google News: real outlet
            it["outlet"] = s["title"]; it["outlet_home"] = s.get("href", "")
            it["title"] = re.sub(r"\s+-\s+" + re.escape(s["title"]) + r"\s*$", "", it["title"])
            it["desc"] = ""
        out.append(it)
    return out

def f_arxiv(src):
    """arXiv API (polite, backoff); fallback to OAI-PMH recent records."""
    try:
        f = feedparser.parse(get_polite(src["url"]))
        if f.entries:
            return [dict(title=clean(e.title, 300), link=e.link.split("v")[0] if re.search(r"v\d+$", e.link) else e.link, desc=clean(e.get("summary")),
                         date=dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc).isoformat()) for e in f.entries]
    except Exception as ex:
        print(f"[warn] arXiv API: {ex}; trying OAI-PMH", file=sys.stderr)
    out, since = [], (NOW - dt.timedelta(days=3)).strftime("%Y-%m-%d")
    ns = {"o": "http://www.openarchives.org/OAI/2.0/", "a": "http://arxiv.org/OAI/arXiv/"}
    for st in src.get("oai_sets", []):
        url = f"https://oaipmh.arxiv.org/oai?verb=ListRecords&metadataPrefix=arXiv&set={st}&from={since}"
        for _ in range(4):
            try: root = ET.fromstring(get_polite(url, delay=5))
            except Exception as ex: print(f"[warn] OAI {st}: {ex}", file=sys.stderr); break
            for r in root.iter("{%s}record" % ns["o"]):
                a = r.find(".//a:arXiv", ns)
                if a is None: continue
                created = a.findtext("a:created", "", ns)
                if created < since: continue  # skip old papers that were merely updated
                aid = a.findtext("a:id", "", ns)
                out.append(dict(title=clean(a.findtext("a:title", "", ns), 300), link=f"https://arxiv.org/abs/{aid}", desc=clean(a.findtext("a:abstract", "", ns)), date=created + "T00:00:00+00:00"))
            tok = root.find(".//o:resumptionToken", ns)
            if tok is None or not (tok.text or "").strip(): break
            url = "https://oaipmh.arxiv.org/oai?verb=ListRecords&resumptionToken=" + urllib.parse.quote(tok.text.strip())
    return out

def f_github(src):
    since = (NOW - dt.timedelta(days=src.get("days", 7))).strftime("%Y-%m-%d")
    q = urllib.parse.quote(f"{src.get('q','')} created:>{since}".strip())
    h = {"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"]} if os.getenv("GITHUB_TOKEN") else {}
    j = json.loads(get(f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page={src.get('limit',20)}", headers=h))
    return [dict(title=r["full_name"], link=r["html_url"], desc=clean(r.get("description") or "") + f" (language: {r.get('language')}, homepage: {r.get('homepage') or ''}, topics: {', '.join(r.get('topics') or [])})",
                 date=r["created_at"], pop=f"★ {r['stargazers_count']}", image=r["owner"]["avatar_url"], shot=f"https://opengraph.githubassets.com/1/{r['full_name']}") for r in j.get("items", [])]

def f_hf(src):
    return [dict(title=m["id"], link=f"https://huggingface.co/{m['id']}", desc="Hugging Face model; tags: " + ", ".join(m.get("tags", [])[:15]),
                 date=m.get("createdAt") or NOW.isoformat(), pop=f"♥ {m.get('likes',0)}") for m in json.loads(get(src["url"]))]

MON = {m: i for i, m in enumerate(["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1)}
def f_pubmed(src):
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    ids = json.loads(get(base + "esearch.fcgi?db=pubmed&retmode=json&sort=pub_date&retmax=%d&term=%s" % (src.get("limit", 25), urllib.parse.quote(src["term"]))))["esearchresult"]["idlist"]
    if not ids: return []
    root = ET.fromstring(get(base + "efetch.fcgi?db=pubmed&retmode=xml&id=" + ",".join(ids)))
    out = []
    for art in root.iter("PubmedArticle"):
        pmid = art.findtext(".//PMID")
        title = clean("".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else "")
        abstract = clean(" ".join("".join(x.itertext()) for x in art.findall(".//Abstract/AbstractText")), 1200)
        journal = art.findtext(".//Journal/Title", "")
        d = None
        for path in (".//ArticleDate", ".//JournalIssue/PubDate", ".//PubMedPubDate[@PubStatus='pubmed']"):
            n = art.find(path)
            if n is not None and n.findtext("Year"):
                mo = n.findtext("Month") or "1"; mo = int(mo) if mo.isdigit() else MON.get(mo[:3].lower(), 1)
                d = dt.datetime(int(n.findtext("Year")), mo, int(n.findtext("Day") or 1), tzinfo=dt.timezone.utc); break
        doi = next((x.text for x in art.findall(".//ArticleIdList/ArticleId") if x.get("IdType") == "doi"), "")
        out.append(dict(title=title, link=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", desc=f"[{journal}] {abstract}", date=(d or NOW).isoformat(),
                        badge=("DOI " + doi) if doi else f"PMID {pmid}", outlet=f"PubMed · {journal}" if journal else None))
    return out

FETCH = dict(rss=f_rss, arxiv=f_arxiv, github=f_github, hf=f_hf, pubmed=f_pubmed)

def fetch_source(src):
    out, err = [], None
    try: out = FETCH[src.get("kind", "rss")](src)
    except Exception as ex:
        err = str(ex)[:200]; print(f"[warn] {src['name']}: {ex}", file=sys.stderr)
    res = []
    for it in out:
        if not it.get("title") or not kw_ok(it["title"] + " " + it.get("desc", ""), src.get("keywords")): continue
        it["source"] = it.pop("outlet", None) or src["name"]; it["source_group"] = src["name"]
        it["home"] = it.pop("outlet_home", "") or ""
        it["evidence_hint"] = src.get("evidence", "")
        it["thin"] = bool(src.get("title_only")) or len(it.get("desc", "")) < 80
        it.setdefault("badge", ""); it.setdefault("pop", None)
        if not it["badge"]:
            m = re.search(r"arxiv\.org/abs/([\d.]+)", it["link"]) or re.search(r"(10\.\d{4,}/[^\s?#%]+)", it["link"])
            if m: it["badge"] = ("arXiv " if "arxiv" in it["link"] else "DOI ") + m.group(1)
            elif "nature.com/articles/" in it["link"]: it["badge"] = "DOI 10.1038/" + it["link"].rsplit("/", 1)[-1]
        it["id"] = hashlib.sha1((it["link"] or it["title"]).encode()).hexdigest()[:12]
        res.append(it)
    print(f"[src] {src['name']}: {len(res)}" + (f" (error: {err})" if err else ""), file=sys.stderr)
    return src, res, err

# ---------------------------------------------------------------- LLM
def llm_raw(messages, want="array"):
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    body = json.dumps({"model": os.getenv("LLM_MODEL", "deepseek-v4-flash"), "messages": messages, "temperature": 0.2}).encode()
    for a in range(3):
        try:
            req = urllib.request.Request(base + "/chat/completions", data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["LLM_API_KEY"]})
            with urllib.request.urlopen(req, timeout=240) as r:
                txt = json.loads(r.read())["choices"][0]["message"]["content"]
            if want == "text": return txt
            return json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
        except Exception as ex:
            print(f"[llm retry {a}] {ex}", file=sys.stderr); time.sleep(5)
    return None

def enrich(batch):
    payload = [dict(i=k, title=b["title"], source=b["source"], desc=(b.get("desc") or "")[:900], evidence_hint=b.get("evidence_hint", ""), title_only=b.get("thin", False)) for k, b in enumerate(batch)]
    sysmsg = (PROMPT + "\n\n可选 section: " + " / ".join(SECTIONS) + "\n可选 tags（只能从中选择 1-3 个，不得自创）: " + "、".join(TAGS) +
              "\n若 title_only=true（只有标题、无正文），summary 只能复述标题信息，key_points 必须为空数组 []。" +
              "\n每条输出 JSON 对象，字段: i, " + FIELDS + "。只输出一个 JSON 数组，不要其他文字。")
    res = llm_raw([{"role": "system", "content": sysmsg}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    out = []
    for r in res or []:
        try:
            b = batch[int(r["i"])]
            if r.get("keep") is False and "_pid" not in b: continue
            r.pop("i", None); b.update(r)
            b["tags"] = norm_tags(b.get("tags"))
            if b.get("thin"): b["key_points"] = []
            out.append(b)
        except Exception: pass
    return out

def enrich_all(items, workers=4):
    with cf.ThreadPoolExecutor(workers) as ex:
        return [i for lst in ex.map(enrich, [items[i:i + 8] for i in range(0, len(items), 8)]) for i in lst]

# ---------------------------------------------------------------- dedupe / merge
def add_also(target, it):
    target.setdefault("also", [])
    if it["link"] != target["link"] and all(a["link"] != it["link"] for a in target["also"]):
        target["also"].append(dict(source=it["source"], link=it["link"]))

def cluster(items, pool):
    """Merge items describing the same event (new items vs each other and vs recent pool). Returns surviving new items."""
    keep = []
    for it in sorted(items, key=rank, reverse=True):
        t2 = it.get("zh_title") or it.get("name") or it["title"]
        hit = next((p for p in pool + keep if similar(it["title"], p["title"]) or similar(t2, p.get("zh_title") or p.get("name") or p["title"])), None)
        if hit: add_also(hit, it)
        else: keep.append(it)
    return keep

# ---------------------------------------------------------------- status
def update_status(results):
    path = "data/status.json"
    st = json.load(open(path)) if os.path.exists(path) else {"runs": [], "streak": {}}
    run = {"time": NOW.isoformat(), "sources": {}}
    for src, res, err in results:
        n = src["name"]; c = run["sources"].setdefault(n, {"fetched": 0, "error": None})
        c["fetched"] += len(res); c["error"] = c["error"] or err
    for n, c in run["sources"].items():
        st["streak"][n] = 0 if c["fetched"] else st["streak"].get(n, 0) + 1
    st["runs"] = ([run] + st["runs"])[:14]
    json.dump(st, open(path, "w"), ensure_ascii=False, indent=1)
    bad = [n for n, k in st["streak"].items() if k >= 3]
    with open("data/alerts.md", "w") as f:
        if bad: f.write("以下数据源连续 3 次以上抓取为 0：\n\n" + "\n".join(f"- {n}: 连续 {st['streak'][n]} 次，最近错误: {run['sources'].get(n, {}).get('error')}" for n in bad) + f"\n\n(run {NOW.isoformat()})\n")
    return st

# ---------------------------------------------------------------- main
def load_archive():
    arc = {}
    for p in sorted(glob.glob("data/archive/*.json")): arc[os.path.basename(p)[:-5]] = json.load(open(p))
    return arc

def save_archive_day(day, items):
    os.makedirs("data/archive", exist_ok=True)
    json.dump(items, open(f"data/archive/{day}.json", "w"), ensure_ascii=False, indent=0)

def main():
    os.makedirs("data", exist_ok=True)
    db = json.load(open("data/items.json")) if os.path.exists("data/items.json") else []
    with cf.ThreadPoolExecutor(8) as ex: results = list(ex.map(fetch_source, SOURCES))
    update_status(results)
    seen = {d["id"] for d in db}
    for d in db: seen.update(hashlib.sha1(a["link"].encode()).hexdigest()[:12] for a in d.get("also", []))
    new = []
    for src, res, _ in results:
        maxage = NOW - dt.timedelta(days=src.get("max_age", 10))
        for it in res:
            if it["id"] in seen or parse_date(it["date"]) < maxage: continue
            dup = next((p for p in db + new if similar(it["title"], p["title"])), None)
            seen.add(it["id"])
            if dup: add_also(dup, it); continue
            new.append(it)
    by = {}
    for it in new: by.setdefault(it["source_group"], []).append(it)
    pick = []
    while len(pick) < MAX_NEW and any(by.values()):
        for k in list(by):
            if by[k] and len(pick) < MAX_NEW: pick.append(by[k].pop(0))
    print(f"new candidates {len(new)}, processing {len(pick)}", file=sys.stderr)
    done = enrich_all(pick)
    for d in done:
        d["day"] = TODAY; d.pop("desc", None); d.pop("evidence_hint", None); d["rank"] = rank(d)
    recent = [d for d in db if d.get("day", "") >= (NOW - dt.timedelta(days=3)).strftime("%Y-%m-%d")]
    done = cluster(done, recent)
    cutoff = (NOW - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    db = [d for d in db if d.get("day", TODAY) >= cutoff] + done
    db.sort(key=lambda d: (d.get("day", ""), rank(d)), reverse=True)
    json.dump(db, open("data/items.json", "w"), ensure_ascii=False, indent=1)
    # permanent archive: rewrite archive files for days present in rolling db (captures merges), never delete old ones
    for day in {d["day"] for d in db}: save_archive_day(day, [d for d in db if d["day"] == day])
    conf_items = None
    if os.path.exists("conf.py"):
        import conf; conf_items = conf.run(enrich_all)
    build(db, conf_items)
    print(f"added {len(done)}, total {len(db)}", file=sys.stderr)

# ---------------------------------------------------------------- render
def fav(link, home=""):
    h = urllib.parse.urlparse(home or link).hostname or ""
    return f"https://www.google.com/s2/favicons?sz=32&domain={h}"

def cite_text(i):
    ref = i.get("badge") or i["link"]
    return f"{i['title']}. {i['source']}, {(i.get('date') or '')[:10]}. {ref.replace('DOI ', 'https://doi.org/') if ref.startswith('DOI ') else ref}"

def card(i, featured=False, top=False):
    health = SITE["mode"] == "health"
    tags = i.get("tags") or []
    title = (i.get("zh_title") or i["title"]) if health else (i.get("name") or i["title"])
    txt = " ".join([title, i["title"], i.get("summary") or "", i["source"], " ".join(tags)]).lower()
    attrs = ('data-top="1" ' if top else '') + f'data-sec="{E(i.get("section"))}" data-tags="{E("|".join(tags))}" data-text="{E(txt)}"'
    meta = (f'<div class="meta"><img src="{fav(i["link"], i.get("home"))}" alt="" loading="lazy" onerror="this.remove()"><span>{E(i["source"])}</span>'
            f'<span>·</span><span>发布于 {zh_date(i.get("date",""))}</span>' + (f'<span>·</span><span class="mono">{E(i["pop"])}</span>' if i.get("pop") else "") +
            (f'<span class="badge" title="{E(i["badge"])}">{E(i["badge"])}</span>' if health and i.get("badge") else "") + "</div>")
    t = f'<a class="t stretch" href="{E(i["link"])}" target="_blank" rel="noopener">{E(title)}</a>'
    if health and title != i["title"]: t += f'<div class="orig">{E(i["title"])}</div>'
    s = f'<p class="s">{E(i.get("summary"))}</p>'
    kp = "".join(f"<li>{E(k)}</li>" for k in (i.get("key_points") or [])[:3])
    kp = f'<ul class="kp">{kp}</ul>' if kp and not i.get("thin") else ""
    chips = ""
    if health:
        ev = i.get("evidence") or ""
        chips += f'<span class="chip {"p" if i.get("type")=="论文" else ""}">{E(i.get("type"))}</span>' if i.get("type") else ""
        if ev: chips += f'<span class="chip w">{"预印本 · 未经同行评审" if ev=="预印本" else E(ev)}</span>'
    chips += "".join(f'<button class="chip tag {"p" if not health and "AI" in t_ else ""}" data-tag="{E(t_)}">{E(t_)}</button>' for t_ in tags)
    if not health:
        chips += "".join(f'<span class="chip w">{E(p)}</span>' for p in (i.get("platform") or [])) + (f'<span class="chip w">{E(i["pricing"])}</span>' if i.get("pricing") and i["pricing"] != "未知" else "")
    if i.get("thin"): chips += '<span class="chip w" title="原始来源只有标题，摘要仅依据标题生成">仅基于标题</span>'
    extra = ""
    if i.get("also"):
        extra += f'<div class="also">{len(i["also"]) + 1} 个来源报道：' + " ".join(f'<a href="{E(a["link"])}" target="_blank" rel="noopener">{E(a["source"])}</a>' for a in [dict(source=i["source"], link=i["link"])] + i["also"][:6]) + "</div>"
    if health and (i.get("type") == "论文" or i.get("badge")):
        extra += f'<button class="cite" data-cite="{E(cite_text(i))}">复制引用 / DOI</button>'
    why = f'<div class="why">有意思在哪：{E(i["why"])}</div>' if not health and i.get("why") else ""
    cls = "card" + (" reg" if i.get("type") == "监管" else "")
    body = meta + t + s + kp + f'<div class="chips">{chips}</div>' + why + extra
    if health and featured:
        side = (f'<div class="img" style="background-image:url(\'{E(i["image"])}\')"></div>' if i.get("image") else
                f'<div class="img logo"><img src="{fav(i["link"], i.get("home")).replace("sz=32", "sz=64")}" alt="" onerror="this.remove()"><div>{E(i["source"])}</div><div class="ev">{E(i.get("evidence") or "")}</div></div>')
        return f'<article class="{cls} feat" {attrs}>{side}<div class="txt">{body}</div></article>'
    if not health and featured:
        gm = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/?$", i["link"])
        img = i.get("shot") or (f"https://opengraph.githubassets.com/1/{gm.group(1)}" if gm else None)
        shot = f'<div class="shot" style="background-image:url(\'{E(img)}\')"></div>' if img else f'<div class="shot">{E(title[:1])}</div>'
        return f'<article class="card big" {attrs}>{shot}<div class="inner">{body}</div></article>'
    if not health:
        ico = f'<img class="ico" src="{E(i["image"])}" alt="" loading="lazy">' if i.get("image") else f'<div class="ico">{E(title[:1])}</div>'
        return f'<article class="{cls}" {attrs}><div class="row">{ico}<div class="body">{body}</div></div></article>'
    return f'<article class="{cls}" {attrs}>{body}</article>'

def day_label(d):
    if d == TODAY: return f"今日收录 · {zh_date(d)}"
    return f"{zh_date(d)} 收录"

def page(title, desc, url, body, depth=0, extra_head=""):
    base = "../" * depth
    tpl = open("template.html", encoding="utf-8").read()
    head = (f'<title>{E(title)}</title><meta name="description" content="{E(desc)}"><link rel="canonical" href="{E(url)}">'
            f'<meta property="og:type" content="website"><meta property="og:title" content="{E(title)}"><meta property="og:description" content="{E(desc)}">'
            f'<meta property="og:url" content="{E(url)}"><meta property="og:site_name" content="{E(SITE["title"])}"><meta name="twitter:card" content="summary">'
            f'<link rel="alternate" type="application/rss+xml" title="{E(SITE["title"])}" href="{base}feed.xml">' + extra_head)
    return tpl.replace("{{HEAD}}", head).replace("{{MODE}}", SITE["mode"]).replace("{{BODY}}", body).replace("{{BASE}}", base)

def header(base, tabs=True):
    t = ""
    if tabs:
        t = '<nav class="tabs" id="tabs">' + "".join(f'<button class="tab{" on" if s=="全部" else ""}" data-s="{E(s)}">{E(s)}</button>' for s in ["全部"] + SECTIONS) + "</nav>"
    else: t = '<nav class="tabs"></nav>'
    return (f'<header><div class="wrap"><a class="brand" href="{base}./">{SITE["wordmark"]}</a>{t}<div class="links"><a href="{base}weekly/">周报</a><a href="{base}archive/">归档</a>'
            f'<a href="{base}feed.xml">RSS</a><a href="{SITE["repo"]}" target="_blank" rel="noopener">GitHub</a></div></div></header>')

def footer(base, st=None):
    srcs = "、".join(sorted({s["name"] for s in SOURCES}))
    return (f'<footer><div class="wrap">{E(SITE.get("disclaimer",""))}<br>数据来源：{E(srcs)} · <a href="{base}status.html">数据源状态</a><br>'
            f'更新于 <span class="mono">{NOW.astimezone(CST).strftime("%Y-%m-%d %H:%M")} (UTC+8)</span> · <a href="{SITE["repo"]}" target="_blank">{SITE["repo"]}</a> · '
            f'灵感来自 <a href="https://github.com/wuyoscar/AISafetyHot-Hub" target="_blank">AISafetyHot-Hub</a> 与 <a href="https://github.com/KKKKhazix/AIHOT" target="_blank">AIHOT</a></div></footer>')

def feed_list(db, conf_items):
    days = sorted({d["day"] for d in db}, reverse=True)
    h = ""
    for d in days:
        g = sorted([i for i in db if i["day"] == d], key=rank, reverse=True)
        topids = {i["id"] for i in top_picks([i for i in db if i["day"] == days[0]])}
        h += f'<section class="dayg" data-day="{d}"><div class="day">{E(day_label(d))} · <span class="n">{len(g)}</span> 条</div><div class="feed">' + "".join(card(i, top=i["id"] in topids) for i in g) + "</div></section>"
    return h

def featured(db):
    latest = max((d["day"] for d in db), default=TODAY)
    top = top_picks([i for i in db if i["day"] == latest])
    if not top: return "", latest, 0
    if SITE["mode"] == "health":
        h = card(top[0], True) + '<div class="mini">' + "".join(card(i) for i in top[1:]) + "</div>"
    else:
        h = '<div class="grid">' + "".join(card(i, True) for i in top[:4]) + "</div>" + ('<div class="mini">' + card(top[4]) + "</div>" if len(top) > 4 else "")
    return f'<div id="featWrap"><h2>今日精选 · Top 5</h2>{h}</div>', latest, len([i for i in db if i["day"] == latest])

def rss(path, title, link, items):
    def fmt(iso):
        return parse_date(iso).strftime("%a, %d %b %Y %H:%M:%S +0000")
    xs = "".join(f'<item><title>{E(i.get("zh_title") or i.get("name") or i["title"])}</title><link>{E(i["link"])}</link><guid isPermaLink="false">{SITE["repo"]}#{i["id"]}</guid>'
                 f'<pubDate>{fmt(i.get("date") or NOW.isoformat())}</pubDate><category>{E(i.get("section"))}</category>' + "".join(f"<category>{E(t)}</category>" for t in i.get("tags") or []) +
                 f'<description>{E((i.get("summary") or "") + " — " + i["source"])}</description></item>' for i in items[:80])
    open(path, "w").write(f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{E(title)}</title><link>{E(link)}</link><description>{E(SITE["tagline"])}</description>'
                          f'<lastBuildDate>{fmt(NOW.isoformat())}</lastBuildDate>{xs}</channel></rss>')

def build(db=None, conf_items=None):
    if db is None: db = json.load(open("data/items.json"))
    for i in db:  # normalize legacy items
        i["tags"] = norm_tags(i.get("tags")); i["rank"] = rank(i)
        if i.get("thin"): i["key_points"] = []
    os.makedirs("site", exist_ok=True)
    url = SITE["url"]
    feat, latest, n = featured(db)
    tools = ('<div class="tools"><input id="q" type="search" placeholder="搜索标题、摘要、来源、标签…" aria-label="搜索"><span id="tagf" class="tagf" hidden></span></div>')
    sub = ('<div id="paperSub" class="sub" hidden><button data-k="feed" class="on">预印本与期刊</button><button data-k="conf">顶会论文</button></div><div id="confPanel" hidden></div>'
           if conf_items is not None or os.path.exists("site/conf.json") else "")
    random_btn = '<button class="pill" id="rnd">随机看一个</button>' if SITE["mode"] == "apps" else ""
    hero = (f'<section class="hero"><div><h1><span class="mono">{latest}</span></h1><p>{E(SITE["tagline"])} · 今日 {n} 条</p></div>{random_btn}</section>')
    body = header("") + f'<main class="wrap">{hero}{tools}{feat}{sub}<div id="feed">{feed_list(db, conf_items)}</div><div id="empty" class="empty" hidden>没有匹配的内容</div></main>' + footer("")
    open("site/index.html", "w").write(page(SITE["title"], SITE["tagline"] + "：" + "、".join(SECTIONS), url, body))
    # conference data, loaded on demand
    if conf_items is not None:
        json.dump(conf_items, open("site/conf.json", "w"), ensure_ascii=False, separators=(",", ":"))
    # RSS: all + per section
    rss("site/feed.xml", SITE["title"], url, db)
    for k, s in enumerate(SECTIONS):
        slug = C.SECTION_SLUGS[k]
        rss(f"site/feed-{slug}.xml", f'{SITE["title"]} · {s}', url, [i for i in db if i.get("section") == s])
    # archive pages (permanent store)
    arc = load_archive()
    for day, items in arc.items():
        for i in items: i["tags"] = norm_tags(i.get("tags"))
        items = sorted(items, key=rank, reverse=True)
        os.makedirs(f"site/{day}", exist_ok=True)
        b = header("../", tabs=False) + f'<main class="wrap"><section class="hero"><div><h1><span class="mono">{day}</span></h1><p>{E(SITE["tagline"])} · 当日收录 {len(items)} 条 · <a href="../archive/">全部归档</a></p></div></section><div class="feed">' + "".join(card(i) for i in items) + "</div></main>" + footer("../")
        open(f"site/{day}/index.html", "w").write(page(f'{day} · {SITE["title"]}', f'{day} 收录 {len(items)} 条', url + day + "/", b, 1))
    rows = "".join(f'<li><a href="../{d}/"><span class="mono">{d}</span> · {len(arc[d])} 条</a></li>' for d in sorted(arc, reverse=True))
    os.makedirs("site/archive", exist_ok=True)
    open("site/archive/index.html", "w").write(page(f'归档 · {SITE["title"]}', "每日收录归档", url + "archive/", header("../", False) + f'<main class="wrap"><section class="hero"><div><h1>每日归档</h1><p>共 {len(arc)} 天</p></div></section><ul class="list">{rows}</ul></main>' + footer("../"), 1))
    build_weekly(url)
    build_status(url)
    open("site/.nojekyll", "w").write("")

def build_weekly(url):
    os.makedirs("site/weekly", exist_ok=True)
    weeks = sorted(glob.glob("data/weekly/*.json"), reverse=True)
    rows, ritems = "", []
    for p in weeks:
        w = json.load(open(p)); wk = w["week"]
        refs = "".join(f'<li id="r{n+1}">[{n+1}] <a href="{E(i["link"])}" target="_blank" rel="noopener">{E(i["title"])}</a> <span class="muted">— {E(i["source"])}</span></li>' for n, i in enumerate(w["items"]))
        paras = "".join(f"<p>{E(x)}</p>" for x in w["summary"].split("\n") if x.strip())
        paras = re.sub(r"\[(\d+)\]", r'<a href="#r\1">[\1]</a>', paras)
        os.makedirs(f"site/weekly/{wk}", exist_ok=True)
        b = header("../../", False) + f'<main class="wrap"><section class="hero"><div><h1>周报 <span class="mono">{wk}</span></h1><p>{E(w["range"])}{" · 进行中" if w.get("partial") else ""} · <a href="../">全部周报</a></p></div></section><article class="card prose">{paras}<h2>本周条目</h2><ol class="refs">{refs}</ol></article></main>' + footer("../../")
        open(f"site/weekly/{wk}/index.html", "w").write(page(f'周报 {wk} · {SITE["title"]}', w["summary"][:120], url + f"weekly/{wk}/", b, 2))
        rows += f'<li><a href="{wk}/"><span class="mono">{wk}</span> · {E(w["range"])}</a></li>'
        ritems.append(dict(id="w" + wk, title=f'周报 {wk}', link=url + f"weekly/{wk}/", date=w["generated"], section="周报", summary=w["summary"][:300], source=SITE["title"], tags=[]))
    open("site/weekly/index.html", "w").write(page(f'周报 · {SITE["title"]}', "每周综述", url + "weekly/", header("../", False) + f'<main class="wrap"><section class="hero"><div><h1>周报</h1><p><a href="feed.xml">周报 RSS</a></p></div></section><ul class="list">{rows or "<li>暂无</li>"}</ul></main>' + footer("../"), 1))
    rss("site/weekly/feed.xml", f'{SITE["title"]} · 周报', url + "weekly/", ritems)

def build_status(url):
    st = json.load(open("data/status.json")) if os.path.exists("data/status.json") else {"runs": [], "streak": {}}
    runs = st["runs"][:7]
    names = sorted({n for r in runs for n in r["sources"]})
    th = "".join(f'<th class="mono">{parse_date(r["time"]).astimezone(CST).strftime("%m-%d %H:%M")}</th>' for r in runs)
    tr = "".join(f'<tr><td>{E(n)}</td>' + "".join(f'<td class="mono{" bad" if not r["sources"].get(n, {}).get("fetched") else ""}" title="{E(r["sources"].get(n, {}).get("error") or "")}">{r["sources"].get(n, {}).get("fetched", "–")}</td>' for r in runs) + f'<td class="mono">{st["streak"].get(n, 0)}</td></tr>' for n in names)
    b = header("", False) + f'<main class="wrap"><section class="hero"><div><h1>数据源状态</h1><p>每次运行各来源抓取条数（过滤后）；连续 3 次为 0 会自动开 issue</p></div></section><div class="card" style="overflow-x:auto"><table class="st"><tr><th>来源</th>{th}<th>连续为0</th></tr>{tr}</table></div></main>' + footer("")
    open("site/status.html", "w").write(page(f'数据源状态 · {SITE["title"]}', "数据源状态", url + "status.html", b))
    json.dump(st, open("site/status.json", "w"), ensure_ascii=False)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        ci = None
        if os.path.exists("conf.py"):
            import conf; ci = conf.shown(json.load(open("data/conf.json")))
        build(None, ci)
    else:
        main()

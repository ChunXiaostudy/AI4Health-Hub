"""Shared pipeline: fetch -> dedupe -> LLM summarize -> rank -> static build (pre-rendered HTML, archives, RSS, weekly).
Concept inspired by AISafetyHot-Hub (CC BY-NC 4.0) and AIHOT (MIT); code written from scratch."""
import json, os, re, sys, time, math, glob, html, hashlib, difflib, shutil, datetime as dt, urllib.request, urllib.parse, urllib.error
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
ABS_MAX = 2500  # stored original abstract length (shown + translated on the item page)
E = lambda s: html.escape(str(s if s is not None else ""), quote=True)

# ---------------------------------------------------------------- helpers
class Challenged(Exception): pass

def get(url, timeout=60, headers=None):
    req = urllib.request.Request(url, headers=dict(UA, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    if b"Client Challenge" in body[:4000]:  # bot-check page served with 200 (nature.com / springer.com from CI): an error, not content
        raise Challenged("Client Challenge (bot check) from " + (urllib.parse.urlparse(url).hostname or ""))
    return body

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
    d = parse_date(it.get("date") or "", None)
    if d is None: return FEATURED_MAX_AGE + 1  # unknown publication date: no freshness bonus, not eligible as a recent pick
    return max(0, (day - d.astimezone(CST).date()).days)

def rank_parts(it):
    return {"内容": it.get("score") or 5, "来源": WEIGHTS.get(it.get("source"), WEIGHTS.get(it.get("source_group"), 1.0)),
            "证据": EV_BONUS.get(it.get("evidence"), 0), "热度": min(2.0, 0.6 * math.log10(signal(it) + 1)),
            "新鲜度": FRESH_BONUS * 0.5 ** (age_days(it) / FRESH_HALF_LIFE)}

def rank(it):
    return round(sum(rank_parts(it).values()), 2)

TIERS = [(9, "里程碑", "hi"), (7, "重要", "hi"), (5, "值得一看", "mid"), (3, "增量", "lo"), (0, "边缘", "lo")]
def tier(score):
    return next((name, cls) for lo, name, cls in TIERS if (score or 0) >= lo)

def score_tip(it):
    return f"综合排序分 {rank(it)} = " + " + ".join(f"{k} {round(v, 1):g}" for k, v in rank_parts(it).items())

def top_picks(items, n=5):
    """Highest-ranked recent items; older backfill only fills the gap when there aren't enough recent ones."""
    ranked = sorted(items, key=rank, reverse=True)
    return ([i for i in ranked if age_days(i) <= FEATURED_MAX_AGE] + [i for i in ranked if age_days(i) > FEATURED_MAX_AGE])[:n]

SEC_KW = [("落地与监管", ["fda", "nmpa", "监管", "审批", "指南", "治理", "部署", "伦理", "教育"]), ("产业动态", ["融资", "并购", "收购", "万美元", "亿美元", "亿元", "raises", "funding round"]),
          ("影像与病理", ["影像", "病理", "内镜", "超声", "imaging", "pathology"]), ("大模型与智能体", ["大模型", "语言模型", "llm", "agent", "智能体", "对话"]),
          ("生命科学与药物", ["药物", "蛋白", "基因", "细胞", "分子"]), ("数字健康", ["可穿戴", "心理", "公共卫生", "流感", "远程"])]
def section_of(it):
    """Topic section; migrates items labelled with the old content-form sections and backs up off-list LLM answers."""
    if it.get("section") in SECTIONS: return it["section"]
    if it.get("type") == "监管": return "落地与监管"
    if it.get("type") == "融资": return "产业动态"
    for t in it.get("tags") or []:
        if t in getattr(C, "TAG_SECTION", {}): return C.TAG_SECTION[t]
    if it.get("section") in getattr(C, "OLD_SECTION", {}): return C.OLD_SECTION[it["section"]]
    txt = " ".join([it.get("zh_title") or "", it.get("title") or "", it.get("summary") or ""]).lower()
    for sec, kws in SEC_KW:
        if any(k in txt for k in kws): return sec
    return "落地与监管" if it.get("type") == "观点" else "临床预测与决策"

PAPER_SOURCES = {s["name"] for s in SOURCES if s.get("evidence") in ("预印本", "同行评审")} | {"顶会论文"}
def form_of(it):
    """Mutually exclusive content form: 开源 by source, then 综述与观点 by type, then 研究论文 by source, else 新闻与监管."""
    g = it.get("source_group") or it.get("source")
    if g in getattr(C, "OPEN_SOURCES", ()): return "开源模型与代码"
    if it.get("type") == "观点": return "综述与观点"
    if g in PAPER_SOURCES: return "研究论文"
    return "新闻与监管"

def zh_date(iso):
    try: d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception: return ""
    if d.tzinfo: d = d.astimezone(CST)
    return (f"{d.year}年" if d.year != NOW.astimezone(CST).year else "") + f"{d.month}月{d.day}日"

def parse_date(s, default=NOW):
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception: return default

# ---------------------------------------------------------------- fetchers
def f_rss(src):
    f = feedparser.parse(get(src["url"]))
    out = []
    for e in f.entries[: src.get("limit", 40)]:
        pub = e.get("published_parsed") or e.get("updated_parsed")
        d = dt.datetime(*pub[:6], tzinfo=dt.timezone.utc) if pub else NOW
        it = dict(title=clean(e.get("title"), 300), link=e.get("link", ""), desc=clean(e.get("summary", ""), ABS_MAX), date=d.isoformat())
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
            return [dict(title=clean(e.title, 300), link=e.link.split("v")[0] if re.search(r"v\d+$", e.link) else e.link, desc=clean(e.get("summary"), ABS_MAX),
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
        abstract = clean(" ".join("".join(x.itertext()) for x in art.findall(".//Abstract/AbstractText")), ABS_MAX)
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

META_KEYS = ("citation_abstract", "dc.description", "og:description", "description")
NO_META = ("github.com", "huggingface.co", "news.google.com", "medrxiv.org", "biorxiv.org", "pubmed.ncbi.nlm.nih.gov")
TEASER_RE = re.compile(r"^[^,]{2,80}, Published online: \d{1,2} \w+ \d{4}; doi:\S+", re.I)
def is_teaser(s):
    """Nature-family RSS description ("<journal>, Published online: <date>; doi:<doi> <title> <standfirst>"): not an abstract."""
    return bool(TEASER_RE.match(s or ""))

PREPRINT_HOSTS = ("biorxiv.org", "medrxiv.org")
def norm_badge(it):
    """bioRxiv / medRxiv links end in a version (…/10.64898/2026.10.05.756921v1); the DOI itself has no version."""
    host = urllib.parse.urlparse(it.get("link") or "").hostname or ""
    if (it.get("badge") or "").startswith("DOI ") and host.endswith(PREPRINT_HOSTS): it["badge"] = re.sub(r"v\d+$", "", it["badge"])
    return it

SPRINGER_PREFIXES = ("10.1038/", "10.1007/", "10.1186/")  # Nature, Springer, BioMed Central
def springer_api_abstract(doi):
    """Springer Nature Meta API (needs SPRINGER_API_KEY). The official programmatic route: nature.com pages and the
    citation export intermittently answer GitHub Actions with a "Client Challenge" bot check, the API does not."""
    key = os.getenv("SPRINGER_API_KEY")
    if not key: return ""
    url = "https://api.springernature.com/meta/v2/json?" + urllib.parse.urlencode({"q": "doi:" + doi, "api_key": key})
    try: recs = json.loads(get(url, timeout=20)).get("records") or []
    except urllib.error.HTTPError as ex: raise RuntimeError(f"HTTP Error {ex.code}") from None  # never let the URL (with the key) reach logs
    except urllib.error.URLError as ex: raise RuntimeError(f"URL error {str(ex.reason)[:40]}") from None
    def text(v):  # "abstract" is a string or a nested {"h1": "Abstract", "p": [...]} structure
        if isinstance(v, dict): return " ".join(text(x) for k, x in v.items() if k != "h1")
        if isinstance(v, list): return " ".join(text(x) for x in v)
        return str(v or "")
    return clean(text(recs[0].get("abstract")), ABS_MAX) if recs else ""

def springer_abstract(doi):
    """Springer Nature citation export (RIS): no key needed, but like nature.com pages it sometimes serves a bot check to CI."""
    ris = get(f"https://citation-needed.springer.com/v2/references/{doi}?format=refman&flavour=citation", timeout=20).decode("utf-8", "ignore")
    return clean(" ".join(l[6:] for l in ris.splitlines() if l.startswith("AB  -")), ABS_MAX)

def fill_abstract(it):
    """RSS often carries only a one-line teaser (e.g. Nature journals); get the abstract from Springer's citation export
    (Nature journals) or the article page's meta tags."""
    host = urllib.parse.urlparse(it["link"]).hostname or ""
    cur = "" if is_teaser(it.get("desc")) else (it.get("desc") or "")
    if it.get("thin_src") or len(cur) >= 300 or any(host.endswith(h) for h in NO_META): return it
    errs = []
    m = re.search(r"nature\.com/articles/([\w.-]+)", it["link"])
    if m:
        for name, fn in ([("springer-api", springer_api_abstract)] if os.getenv("SPRINGER_API_KEY") else []) + [("springer", springer_abstract)]:
            try:
                ab = fn("10.1038/" + m.group(1))
                if len(ab) >= 200: it["desc"] = ab; it["thin"] = False; it.pop("_abs_err", None); return it
                errs.append(f"{name}: empty")
            except Exception as ex: errs.append(f"{name}: {str(ex)[:60]}")
    s, page_err = None, ""
    for a in range(2):  # publishers drop some requests under load; one polite retry
        try: s = get(it["link"], timeout=15).decode("utf-8", "ignore"); break
        except Challenged as ex: page_err = f"page: {ex}"; break
        except Exception as ex: page_err = f"page: {str(ex)[:80]}"; time.sleep(2)
    best = cur
    if s is not None:
        for k in META_KEYS:
            mm = re.search(r'<meta[^>]+(?:name|property)="' + re.escape(k) + r'"[^>]+content="([^"]*)"', s, re.I)
            txt = clean(mm.group(1), ABS_MAX) if mm else ""
            if len(txt) > len(best) and not txt.lower().startswith("abstract page for"): best = txt
        if len(best) > len(cur):
            it["desc"] = best; it["thin"] = len(best) < 80
        if len(best) < 200:  # keep what the page actually was (e.g. a stub served to CI), to tell blocking from layout changes
            t = re.search(r"<title[^>]*>(.*?)</title>", s, re.I | re.S)
            body = clean(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.I | re.S), 100)
            page_err = f"page: no abstract in meta ({len(s)} bytes; title: {clean(t.group(1), 60) if t else '-'}; text: {body})"
    errs += [page_err] if page_err else []
    if errs: it["_abs_err"] = "; ".join(errs)
    else: it.pop("_abs_err", None)
    return it

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
        it["thin_src"] = bool(src.get("title_only"))
        it["thin"] = it["thin_src"] or len(it.get("desc", "")) < 80
        it.setdefault("badge", ""); it.setdefault("pop", None)
        if not it["badge"]:
            m = re.search(r"arxiv\.org/abs/([\d.]+)", it["link"]) or re.search(r"(10\.\d{4,}/[^\s?#%]+)", it["link"])
            if m: it["badge"] = ("arXiv " if "arxiv" in it["link"] else "DOI ") + m.group(1)
            elif "nature.com/articles/" in it["link"]: it["badge"] = "DOI 10.1038/" + it["link"].rsplit("/", 1)[-1]
        norm_badge(it)
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
    sysmsg = (PROMPT + "\n\n可选 section（按方向）:\n" + "\n".join(f"- {k}：{v}" for k, v in getattr(C, "SECTION_HINTS", {}).items()) + "\n可选 tags（只能从中选择 1-3 个，不得自创）: " + "、".join(TAGS) +
              "\n若 title_only=true（只有标题、无正文），summary 只能复述标题信息，key_points 必须为空数组 []。" +
              "\n每条输出 JSON 对象，字段: i, " + FIELDS + "。只输出一个 JSON 数组，不要其他文字。" +
              "\n\n" + getattr(C, "RUBRIC", ""))
    res = llm_raw([{"role": "system", "content": sysmsg}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    for b in batch: b["_verdict"] = None  # None = no answer (call failed / item missing from the reply), not a rejection
    out = []
    for r in res or []:
        try:
            b = batch[int(r["i"])]
            b["_verdict"] = r.get("keep") is not False
            if r.get("keep") is False and "_pid" not in b: continue
            r.pop("i", None); b.update(r)
            b["tags"] = norm_tags(b.get("tags")); b["section"] = section_of(b)
            if b.get("thin"): b["key_points"] = []
            out.append(b)
        except Exception: pass
    return out

TRANSLATE = """你是严谨的医学翻译。把每条英文摘要忠实、完整地译成中文：不增删信息、不总结、不评论；保留所有数字、单位、统计量（如 AUROC、95% CI、P 值）；基因、药物、模型、数据集等专有名词首次出现时可用括号保留英文。
若原文含 Background/Methods/Findings/Results/Interpretation/Conclusions 等小标题，用【背景】【方法】【结果】【结论】等分段标记开头。原文已是中文则原样返回。
只输出一个 JSON 数组，每项 {"i": 序号, "zh": 译文}，不要其他文字。"""
def translate(batch):
    res = llm_raw([{"role": "system", "content": TRANSLATE}, {"role": "user", "content": json.dumps([dict(i=k, text=b["abstract"]) for k, b in enumerate(batch)], ensure_ascii=False)}])
    for r in res or []:
        try: batch[int(r["i"])]["zh_abstract"] = str(r["zh"]).strip()
        except Exception: pass

def translate_all(items, workers=4):
    todo = [i for i in items if len(i.get("abstract") or "") >= 200 and not i.get("zh_abstract")]
    with cf.ThreadPoolExecutor(workers) as ex: list(ex.map(translate, [todo[k:k + 4] for k in range(0, len(todo), 4)]))
    print(f"translated {sum(1 for i in todo if i.get('zh_abstract'))}/{len(todo)} abstracts", file=sys.stderr)

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

POOL_FIELDS = ("id", "title", "link", "date", "source", "source_group", "desc", "evidence_hint", "thin")
BACKFILL_MAX = int(os.getenv("BACKFILL_MAX", "60"))  # older paper items re-processed per run once an abstract is found
BACKFILL_DAYS = 7  # try each item on at most this many distinct days (at most once a day): indexes like OpenAlex lag a few days

def europepmc_abstract(doi):
    q = urllib.parse.quote(f'DOI:"{doi}"')
    r = json.loads(get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={q}&resultType=core&format=json", timeout=20))
    hit = (r.get("resultList") or {}).get("result") or []
    return clean(hit[0].get("abstractText") or "", ABS_MAX) if hit else ""

def pubmed_abstract(pmid):
    root = ET.fromstring(get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&retmode=xml&id=" + pmid, timeout=20))
    return clean(" ".join("".join(x.itertext()) for x in root.iter("AbstractText")), ABS_MAX)

def openalex_abstract(doi):
    r = json.loads(get("https://api.openalex.org/works/doi:" + urllib.parse.quote(doi), timeout=20))
    pos = sorted((p, w) for w, ps in (r.get("abstract_inverted_index") or {}).items() for p in ps)
    return clean(" ".join(w for _, w in pos), ABS_MAX)

def preprint_abstract(server, doi):
    c = json.loads(get(f"https://api.biorxiv.org/details/{server}/{re.sub(r'v\d+$', '', doi)}", timeout=20)).get("collection") or []
    return clean(c[-1].get("abstract") or "", ABS_MAX) if c else ""  # last entry = latest version

def find_abstract(it, fresh):
    """Abstract for a paper item: same-run feed entry > Springer Nature API / citation export or article page meta >
    bioRxiv/medRxiv API > Springer Nature API (Springer DOIs not on nature.com) > PubMed > Europe PMC > OpenAlex.
    Returns (abstract, why it failed); the reason is kept on the item so CI failures are visible in items.json."""
    f = fresh.get(it["id"])
    if f and len(f.get("desc") or "") >= 200 and not is_teaser(f["desc"]): return f["desc"], ""
    x = dict(link=it["link"], desc="")
    fill_abstract(x)
    if len(x["desc"]) >= 200: return x["desc"], ""
    errs = [x.get("_abs_err") or "page: skipped"]
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", it["link"])
    doi = (it.get("badge") or "")[4:] if (it.get("badge") or "").startswith("DOI ") else ""
    pre = re.search(r"(biorxiv|medrxiv)\.org/", it["link"])
    spr = doi.startswith(SPRINGER_PREFIXES) and os.getenv("SPRINGER_API_KEY") and not re.search(r"nature\.com/articles/", it["link"])  # nature links: done in fill_abstract
    tries = (([(pre.group(1) + "-api", lambda d: preprint_abstract(pre.group(1), d), doi)] if pre and doi else []) +
             ([("springer-api", springer_api_abstract, doi)] if spr else []) +
             ([("pubmed", pubmed_abstract, m.group(1))] if m else []) + ([("europepmc", europepmc_abstract, doi), ("openalex", openalex_abstract, doi)] if doi else []))
    for name, fn, key in tries:
        try:
            ab = fn(key)
            if len(ab) >= 200: return ab, ""
            errs.append(f"{name}: empty")
        except Exception as ex:
            errs.append(f"{name}: {str(ex)[:60]}")
    if not tries: errs.append("no DOI / PMID")
    return "", "; ".join(errs)

def backfill(db, results):
    """Paper items without an abstract: find it, redo summary/score from it, translate.
    Each item is tried at most once a day, on up to BACKFILL_DAYS distinct days; a bot-check failure is transient and
    does not use up the day."""
    fresh = {it["id"]: it for _, res, _ in results for it in res}
    for d in db:  # the old per-run counter was used up within hours by repeated manual runs; restart under the per-day budget
        d.pop("abstract_tries", None)
    todo = [d for d in db if not d.get("abstract") and (d.get("source_group") or d.get("source")) in PAPER_SOURCES
            and (TODAY not in d.get("abstract_days", []) or "Challenge" in (d.get("abstract_err") or ""))
            and len(d.get("abstract_days", [])) < BACKFILL_DAYS]
    todo = sorted(todo, key=lambda d: d.get("day", ""), reverse=True)[:BACKFILL_MAX]
    with cf.ThreadPoolExecutor(3) as ex: found = list(ex.map(lambda d: find_abstract(d, fresh), todo))
    got, errs = [], {}
    for d, (ab, err) in zip(todo, found):
        if TODAY not in d.get("abstract_days", []): d["abstract_days"] = d.get("abstract_days", []) + [TODAY]
        if len(ab) >= 200:
            d["abstract"] = ab; d["thin"] = False; d.pop("abstract_err", None); d.pop("abstract_days", None); got.append(d)
        else:
            d["abstract_err"] = err
            k = err.split(";")[0][:50]; errs[k] = errs.get(k, 0) + 1
    for k, n in sorted(errs.items(), key=lambda x: -x[1]): print(f"[backfill] {n}× {k}", file=sys.stderr)
    redo = [dict(title=d["title"], source=d["source"], desc=d["abstract"][:900], evidence_hint=d.get("evidence") or "", thin=False, _pid=d["id"]) for d in got]
    by = {d["id"]: d for d in got}
    for r in enrich_all(redo) if redo else []:
        d = by[r["_pid"]]
        for k in ("zh_title", "summary", "key_points", "tags", "section", "type", "score", "reason"):
            if r.get(k) not in (None, "", []): d[k] = r[k]
    translate_all(got)
    print(f"backfill: tried {len(todo)}, found abstracts {len(got)}", file=sys.stderr)

def save_pool(pick):
    """Raw LLM inputs + the model's verdict, for building the evaluation set (see evalset.py). Kept KEEP_DAYS days.
    One row per id per day (re-runs overwrite); model_keep is None when the LLM gave no answer for the item."""
    os.makedirs("data/pool", exist_ok=True)
    path = f"data/pool/{TODAY}.jsonl"
    rows = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip(): r = json.loads(line); rows[r["id"]] = r
    for it in pick:
        row = {k: it.get(k) for k in POOL_FIELDS}
        v = it.get("_verdict")
        row.update(day=TODAY, model_keep=v, model_score=it.get("score") if v else None)
        if v is None and rows.get(it["id"], {}).get("model_keep") is not None: continue  # keep an earlier real verdict
        rows[it["id"]] = row
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values())
    cutoff = (NOW - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for p in glob.glob("data/pool/*.jsonl"):
        if os.path.basename(p)[:10] < cutoff: os.remove(p)

REJECTED = "data/rejected.json"
def load_rejected():
    return json.load(open(REJECTED)) if os.path.exists(REJECTED) else {}

def save_rejected(rej, pick):
    """Ids the LLM explicitly dropped ({id: day}), so they are not sent to the LLM again on every run. Kept KEEP_DAYS days."""
    for it in pick:
        if it.get("_verdict") is False: rej[it["id"]] = TODAY
    cutoff = (NOW - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    rej = {k: v for k, v in rej.items() if v >= cutoff}
    json.dump(rej, open(REJECTED, "w"), indent=0, sort_keys=True)
    return rej

def main():
    os.makedirs("data", exist_ok=True)
    db = json.load(open("data/items.json")) if os.path.exists("data/items.json") else []
    for d in db:
        d["section"] = section_of(d); norm_badge(d)
        if is_teaser(d.get("abstract")):  # RSS teaser stored as abstract by older runs: drop it so backfill looks for the real one
            for k in ("abstract", "zh_abstract", "abstract_days", "abstract_err"): d.pop(k, None)
    with cf.ThreadPoolExecutor(8) as ex: results = list(ex.map(fetch_source, SOURCES))
    update_status(results)
    rejected = load_rejected()
    seen = {d["id"] for d in db} | set(rejected)
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
    with cf.ThreadPoolExecutor(6) as ex: list(ex.map(fill_abstract, pick))
    done = enrich_all(pick)
    save_pool(pick)
    save_rejected(rejected, pick)
    failed = sum(1 for it in pick if it.get("_verdict") is None)
    if failed: print(f"[warn] LLM gave no answer for {failed}/{len(pick)} items; they will be retried next run", file=sys.stderr)
    for d in done:
        d.pop("_verdict", None)
        ab = d.pop("desc", None) or ""
        if not d.get("thin") and len(ab) >= 200 and not is_teaser(ab): d["abstract"] = ab
        d["day"] = TODAY; d.pop("evidence_hint", None); d.pop("thin_src", None); d["rank"] = rank(d)
    translate_all(done)
    backfill(db + done, results)  # new papers whose page fetch failed get the API fallbacks in the same run
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
        import conf; conf_items = conf.run(enrich_all, translate_all)
    build(db, conf_items)
    print(f"added {len(done)}, total {len(db)}", file=sys.stderr)

# ---------------------------------------------------------------- render
FAV_DIR = "data/fav"  # favicons cached in the repo and served from site/fav/ (google.com is unreachable for many readers)
def fav_host(link, home=""):
    return re.sub(r"[^a-z0-9.-]", "", (urllib.parse.urlparse(home or link).hostname or "").lower())

def fetch_favicons(hosts):
    """Download missing favicons once; an empty file marks a host without one so it is not retried every build."""
    os.makedirs(FAV_DIR, exist_ok=True)
    def one(h):
        try: data = get(f"https://www.google.com/s2/favicons?sz=32&domain={h}", timeout=10)
        except Exception: data = b""
        open(f"{FAV_DIR}/{h}.png", "wb").write(data)
    todo = [h for h in hosts if h and not os.path.exists(f"{FAV_DIR}/{h}.png")]
    with cf.ThreadPoolExecutor(8) as ex: list(ex.map(one, todo))
    if todo: print(f"favicons fetched {len(todo)}", file=sys.stderr)
    os.makedirs("site/fav", exist_ok=True)
    for p in glob.glob(f"{FAV_DIR}/*.png"):
        if os.path.getsize(p): shutil.copyfile(p, "site/fav/" + os.path.basename(p))

def fav_img(i, base="", lazy=True):
    h = fav_host(i["link"], i.get("home"))
    p = f"{FAV_DIR}/{h}.png"
    if not h or not os.path.exists(p) or not os.path.getsize(p): return ""
    return f'<img src="{base}fav/{h}.png" alt=""' + (' loading="lazy"' if lazy else "") + ' onerror="this.remove()">'

def cite_text(i):
    ref = i.get("badge") or i["link"]
    return f"{i['title']}. {i['source']}, {(i.get('date') or '')[:10]}. {ref.replace('DOI ', 'https://doi.org/') if ref.startswith('DOI ') else ref}"

def card(i, featured=False, top=False, base=""):
    health = SITE["mode"] == "health"
    tags = i.get("tags") or []
    title = (i.get("zh_title") or i["title"]) if health else (i.get("name") or i["title"])
    txt = " ".join([title, i["title"], i.get("summary") or "", i["source"], " ".join(tags)]).lower()
    attrs = ('data-top="1" ' if top else '') + f'data-sec="{E(section_of(i))}" data-form="{E(form_of(i))}" data-tags="{E("|".join(tags))}" data-text="{E(txt)}"'
    ev = (i.get("evidence") or "") if health else ""
    tn, tc = tier(i.get("score"))
    # health cards: score badge leads the meta row; evidence is a quiet marker next to the date, not a chip
    sb = (f'<span class="sb {tc}" title="评分 {E(i["score"])} · {tn}｜{E(score_tip(i))}"><b>{E(i["score"])}</b>{tn}</span>'
          if health and i.get("score") and not featured else "")
    evm = ""
    if ev: evm = f'<span class="evm {"ok" if ev == "同行评审" else "warn" if ev == "预印本" else ""}">{"预印本 · 未经同行评审" if ev == "预印本" else E(ev)}</span>'
    if health and i.get("thin"): evm += '<span class="evm thin" title="原始来源只有标题，摘要仅依据标题生成">仅基于标题</span>'
    meta = (f'<div class="meta">{sb}{fav_img(i, base)}<span>{E(i["source"])}</span>'
            f'<span>·</span><span>发布于 {zh_date(i.get("date",""))}</span>' + (f'<span>·</span><span class="mono">{E(i["pop"])}</span>' if i.get("pop") else "") + evm +
            (f'<span class="badge" title="{E(i["badge"])}">{E(i["badge"])}</span>' if health and i.get("badge") else "") +
            f'<a class="ext" href="{E(i["link"])}" target="_blank" rel="noopener" title="打开原文">原文 ↗</a></div>')
    t = f'<a class="t stretch" href="{base}items/{E(i["id"])}/">{E(title)}</a>'
    orig = f'<div class="orig">{E(i["title"])}</div>' if health and title != i["title"] else ""
    s = f'<p class="s">{E(i.get("summary"))}</p>'
    kp = "".join(f"<li>{E(k)}</li>" for k in (i.get("key_points") or [])[:3])
    kp = f'<ul class="kp">{kp}</ul>' if kp and not i.get("thin") else ""
    chips = ""
    if health and i.get("type"): chips += f'<span class="chip ty">{E(i["type"])}</span>'
    chips += "".join(f'<button class="chip tag {"p" if not health and "AI" in t_ else ""}" data-tag="{E(t_)}">{E(t_)}</button>' for t_ in tags)
    if not health:
        chips += "".join(f'<span class="chip w">{E(p)}</span>' for p in (i.get("platform") or [])) + (f'<span class="chip w">{E(i["pricing"])}</span>' if i.get("pricing") and i["pricing"] != "未知" else "")
        if i.get("thin"): chips += '<span class="chip w" title="原始来源只有标题，摘要仅依据标题生成">仅基于标题</span>'
    extra = ""
    if i.get("also"):
        extra += f'<div class="also">{len(i["also"]) + 1} 个来源报道：' + " ".join(f'<a href="{E(a["link"])}" target="_blank" rel="noopener">{E(a["source"])}</a>' for a in [dict(source=i["source"], link=i["link"])] + i["also"][:6]) + "</div>"
    cite = f'<button class="cite" data-cite="{E(cite_text(i))}">复制引用 / DOI</button>' if health and (i.get("type") == "论文" or i.get("badge")) else ""
    why = f'<div class="why">有意思在哪：{E(i["why"])}</div>' if not health and i.get("why") else ""
    if health and i.get("reason") and not featured: why = f'<div class="why">评分理由：{E(i["reason"])}</div>'
    cls = "card" + (f" t-{tc}" if health else "") + (" thin" if health and i.get("thin") else "")
    if health and not featured:
        # secondary detail (English title, key points, reasoning, citation) folds away so the feed scans quickly
        more = orig + kp + why + (f'<div class="mf">{cite}</div>' if cite else "")
        more = f'<details class="more"><summary>{"要点与评分理由" if kp else "详情"}</summary>{more}</details>' if more else ""
        body = meta + t + s + f'<div class="chips">{chips}</div>' + extra + more
    else:
        body = meta + t + orig + s + kp + f'<div class="chips">{chips}</div>' + why + extra + cite
    if health and featured:
        tn, tc = tier(i.get("score"))
        side = (f'<div class="img scorecard {tc}" title="{E(score_tip(i))}"><div class="big"><b>{E(i.get("score") or "–")}</b><span>/10</span></div>'
                f'<div class="tn">{tn}</div><div class="ev">{E(i.get("evidence") or "")} · {E(i["source"])}</div>' +
                (f'<p class="rs">{E(i["reason"])}</p>' if i.get("reason") else "") + "</div>")
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

ICONS = {  # 18px line icons, stroke = currentColor
  "hot": '<path d="M12 3c1 3.5 5 5.5 5 10a5 5 0 0 1-10 0c0-2 1-3.5 2-4.5.3 1.6 1 2.6 2 3 0-3 0-5.5 1-8.5z"/>',
  "papers": '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5zM20 5.5A1.5 1.5 0 0 0 18.5 4H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5z"/>',
  "weekly": '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 9h16M8 3v4M16 3v4M8 13h3M8 16h6"/>',
  "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"/>',
}
NAV = [("hot", "每日 HOT", "./"), ("papers", "论文库", "papers/"), ("weekly", "周报", "weekly/"), ("archive", "归档", "archive/")]

def header(base, tabs=True, active=""):
    """Left sidebar (desktop) / bottom bar (phone) switch pages; the sticky top bar only holds the topic tabs that filter the current page."""
    ico = lambda k: f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[k]}</svg>'
    links = "".join(f'<a href="{base}{href}"{" class=on aria-current=page" if k == active else ""}>{ico(k)}<span>{name}</span></a>' for k, name, href in NAV)
    side = (f'<aside class="side"><a class="brand" href="{base}./">{SITE["wordmark"]}</a><nav class="snav">{links}</nav>'
            f'<div class="sfoot"><a href="{base}feed.xml">RSS 订阅</a><a href="{base}status.html">数据源状态</a><a href="{SITE["repo"]}" target="_blank" rel="noopener">GitHub</a></div></aside>'
            f'<nav class="bnav">{links}</nav>')
    t = ('<nav class="tabs" id="tabs">' + "".join(f'<button class="tab{" on" if s=="全部" else ""}" data-s="{E(s)}">{E(s)}</button>' for s in ["全部"] + SECTIONS) + "</nav>") if tabs else ""
    return side + f'<header{"" if tabs else " class=notabs"}><div class="wrap"><a class="brand" href="{base}./">{SITE["wordmark"]}</a>{t}</div></header>'

def footer(base, st=None):
    srcs = "、".join(sorted({s["name"] for s in SOURCES}))
    return (f'<footer><div class="wrap">{E(SITE.get("disclaimer",""))}<br>数据来源：{E(srcs)} · <a href="{base}status.html">数据源状态</a> · <a href="{base}feed.xml">RSS</a><br>'
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
        h = card(top[0], True) + '<div class="mini duo">' + "".join(card(i) for i in top[1:]) + "</div>"
    else:
        h = '<div class="grid">' + "".join(card(i, True) for i in top[:4]) + "</div>" + ('<div class="mini">' + card(top[4]) + "</div>" if len(top) > 4 else "")
    return f'<div id="featWrap"><h2>今日精选 · Top 5</h2>{h}</div>', latest, len([i for i in db if i["day"] == latest])

def rss(path, title, link, items):
    def fmt(iso):
        return parse_date(iso).strftime("%a, %d %b %Y %H:%M:%S +0000")
    xs = "".join(f'<item><title>{E(i.get("zh_title") or i.get("name") or i["title"])}</title><link>{E(SITE["url"] + "items/" + i["id"] + "/" if i.get("zh_title") else i["link"])}</link><guid isPermaLink="false">{SITE["repo"]}#{i["id"]}</guid>'
                 f'<pubDate>{fmt(i.get("date") or NOW.isoformat())}</pubDate><category>{E(i.get("section"))}</category>' + "".join(f"<category>{E(t)}</category>" for t in i.get("tags") or []) +
                 f'<description>{E((i.get("summary") or "") + " — " + i["source"])}</description></item>' for i in items[:80])
    open(path, "w").write(f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{E(title)}</title><link>{E(link)}</link><description>{E(SITE["tagline"])}</description>'
                          f'<lastBuildDate>{fmt(NOW.isoformat())}</lastBuildDate>{xs}</channel></rss>')

def build(db=None, conf_items=None):
    if db is None: db = json.load(open("data/items.json"))
    for i in db:  # normalize legacy items
        i["tags"] = norm_tags(i.get("tags")); i["section"] = section_of(i); i["rank"] = rank(i); norm_badge(i)
        if i.get("thin"): i["key_points"] = []
    os.makedirs("site", exist_ok=True)
    fetch_favicons({fav_host(i["link"], i.get("home")) for i in db + (conf_items or []) + [x for v in load_archive().values() for x in v]})
    url = SITE["url"]
    feat, latest, n = featured(db)
    tools = ('<div class="tools"><label class="search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg><input id="q" type="search" placeholder="搜索标题、摘要、来源、标签…" aria-label="搜索"><kbd>/</kbd></label><span id="tagf" class="tagf" hidden></span></div>')
    forms = ["全部形式"] + list(getattr(C, "FORMS", []))
    sub = '<div id="formBar" class="sub">' + "".join(f'<button data-f="{E(f)}"{" class=on" if k == 0 else ""}>{E(f)}</button>' for k, f in enumerate(forms)) + '</div>'
    random_btn = '<button class="pill" id="rnd">随机看一个</button>' if SITE["mode"] == "apps" else ""
    ld = dt.date.fromisoformat(latest)
    hero = (f'<section class="hero"><div><h1>{ld.month}月{ld.day}日<small>周{"一二三四五六日"[ld.weekday()]} · {ld.year}</small></h1>'
            f'<p>{E(SITE["tagline"])} · 今日 <b>{n}</b> 条</p></div>{random_btn}</section>')
    body = header("", active="hot") + f'<main class="wrap">{hero}{tools}{sub}{feat}<div id="feed">{feed_list(db, conf_items)}</div><div id="empty" class="empty" hidden>没有匹配的内容</div></main>' + footer("")
    open("site/index.html", "w").write(page(SITE["title"], SITE["tagline"] + "：" + "、".join(SECTIONS), url, body))
    # conference data, loaded on demand
    if conf_items is not None:
        for c in conf_items: c["section"] = section_of(c)
        # same card() as the daily feed, pre-rendered; the library page only filters and paginates these
        rows = [dict(id=c["id"], sec=c["section"], c=c["conf"], y=c.get("year"), tg=c["tags"], h=re.sub(r' data-text="[^"]*"', "", card(c, base="../")),
                     q=" ".join([c["zh_title"], c["title"], c.get("summary") or "", c["source"], " ".join(c["tags"])]).lower()) for c in conf_items]
        json.dump(rows, open("site/conf.json", "w"), ensure_ascii=False, separators=(",", ":"))
    build_papers(url, conf_items, tools)
    build_items(url, load_archive(), conf_items)
    # RSS: all + per topic section + per form (papers / models / opinion keep their pre-v3 URLs)
    rss("site/feed.xml", SITE["title"], url, db)
    for k, s in enumerate(SECTIONS):
        slug = C.SECTION_SLUGS[k]
        rss(f"site/feed-{slug}.xml", f'{SITE["title"]} · {s}', url, [i for i in db if i.get("section") == s])
    for f, slug in getattr(C, "FORM_SLUGS", {}).items():
        rss(f"site/feed-{slug}.xml", f'{SITE["title"]} · {f}', url, [i for i in db if form_of(i) == f])
    if "落地与监管" in SECTIONS:  # old 临床落地与监管 feed URL
        rss("site/feed-clinical-regulatory.xml", f'{SITE["title"]} · 落地与监管', url, [i for i in db if i.get("section") == "落地与监管"])
    # archive pages (permanent store)
    arc = load_archive()
    for day, items in arc.items():
        for i in items: i["tags"] = norm_tags(i.get("tags")); norm_badge(i)
        items = sorted(items, key=rank, reverse=True)
        os.makedirs(f"site/{day}", exist_ok=True)
        b = header("../", tabs=False, active="archive") + f'<main class="wrap"><section class="hero"><div><h1><span class="mono">{day}</span></h1><p>{E(SITE["tagline"])} · 当日收录 {len(items)} 条 · <a href="../archive/">全部归档</a></p></div></section><div class="feed">' + "".join(card(i, base="../") for i in items) + "</div></main>" + footer("../")
        open(f"site/{day}/index.html", "w").write(page(f'{day} · {SITE["title"]}', f'{day} 收录 {len(items)} 条', url + day + "/", b, 1))
    rows = "".join(f'<li><a href="../{d}/"><span class="mono">{d}</span> · {len(arc[d])} 条</a></li>' for d in sorted(arc, reverse=True))
    os.makedirs("site/archive", exist_ok=True)
    open("site/archive/index.html", "w").write(page(f'归档 · {SITE["title"]}', "每日收录归档", url + "archive/", header("../", False, "archive") + f'<main class="wrap"><section class="hero"><div><h1>每日归档</h1><p>共 {len(arc)} 天</p></div></section><ul class="list">{rows}</ul></main>' + footer("../"), 1))
    build_weekly(url)
    build_status(url)
    open("site/.nojekyll", "w").write("")

def zh_paras(txt):
    parts = [p.strip() for p in re.split(r"\n+|(?=【)", txt or "") if p.strip()]
    return "".join(f'<p>{E(p)}</p>' if not p.startswith("【") else f'<p><b>{E(p[:p.find("】") + 1])}</b>{E(p[p.find("】") + 1:])}</p>' for p in parts)

def item_page(i, related, url):
    tn, tc = tier(i.get("score"))
    title = i.get("zh_title") or i.get("name") or i["title"]
    meta = (f'<div class="meta">{fav_img(i, "../../", lazy=False)}<span>{E(i["source"])}</span><span>·</span><span>发布于 {zh_date(i.get("date", ""))}</span>' +
            (f'<span>·</span><span class="mono">{E(i["pop"])}</span>' if i.get("pop") else "") +
            (f'<span>·</span><span>收录于 <a href="../../{E(i["day"])}/">{E(i["day"])}</a></span>' if i.get("day") else "") + "</div>")
    digest = f'<p>{E(i.get("summary"))}</p>' + (f'<ul class="kp">' + "".join(f"<li>{E(k)}</li>" for k in i["key_points"][:3]) + "</ul>" if i.get("key_points") and not i.get("thin") else "")
    abstract = ""
    if i.get("zh_abstract"):
        abstract = f'<h2>中文摘要</h2><div class="abs">{zh_paras(i["zh_abstract"])}</div>'
        if i.get("abstract"): abstract += f'<details class="orig-abs"><summary>查看英文原文摘要</summary><p>{E(i["abstract"])}</p></details>'
    elif i.get("abstract"):
        abstract = f'<h2>原文摘要</h2><div class="abs"><p>{E(i["abstract"])}</p></div>'
    else:
        abstract = '<p class="muted note">此条目暂无原文摘要（新闻 / 仅标题条目，或收录于摘要功能上线前），请阅读原文。</p>'
    also = ""
    if i.get("also"):
        also = "<h2>其他来源报道</h2><ul class=\"list\">" + "".join(f'<li><a href="{E(a["link"])}" target="_blank" rel="noopener">{E(a["source"])} ↗</a></li>' for a in i["also"]) + "</ul>"
    side = (f'<a class="btn" href="{E(i["link"])}" target="_blank" rel="noopener">阅读原文 ↗</a>'
            f'<div class="box scorecard {tc}"><div class="big"><b>{E(i.get("score") or "–")}</b><span>/10</span></div><div class="tn">{tn}</div>' +
            (f'<p class="rs">{E(i["reason"])}</p>' if i.get("reason") else "") + f'<div class="ev">{E(score_tip(i))}</div></div>'
            f'<dl class="box kv"><dt>方向</dt><dd><a href="../../#tab={urllib.parse.quote(section_of(i))}">{E(section_of(i))}</a></dd><dt>形式</dt><dd>{E(form_of(i))}</dd>'
            f'<dt>证据</dt><dd>{E(i.get("evidence") or "—")}</dd><dt>来源</dt><dd>{E(i["source"])}</dd>' +
            (f'<dt>标识</dt><dd class="mono">{E(i["badge"])}</dd>' if i.get("badge") else "") + "</dl>" +
            ('<div class="chips">' + "".join(f'<span class="chip">{E(t)}</span>' for t in i.get("tags") or []) + "</div>" if i.get("tags") else "") +
            (f'<button class="cite" data-cite="{E(cite_text(i))}">复制引用 / DOI</button>' if i.get("badge") or i.get("type") == "论文" else ""))
    relh = "同方向的高分顶会论文" if i.get("source_group") == "顶会论文" else "同方向的其他条目"
    rel = (f'<h2 class="relh">{relh}</h2><div class="feed">' + "".join(card(r, base="../../") for r in related) + "</div>") if related else ""
    body = (header("../../", False, "papers" if i.get("source_group") == "顶会论文" else "hot") + f'<main class="wrap item"><a class="back" href="../../{"papers/" if i.get("source_group") == "顶会论文" else ""}">← 返回{"论文库" if i.get("source_group") == "顶会论文" else "首页"}</a><div class="igrid"><article class="imain">{meta}<h1>{E(title)}</h1>'
            + (f'<div class="orig">{E(i["title"])}</div>' if title != i["title"] else "") +
            f'<h2>AI 导读</h2>{digest}{abstract}{also}<p class="muted note">导读与中文摘要由 AI 生成 / 翻译，仅供参考，请以原文为准；不构成医疗建议。</p></article>'
            f'<aside class="iside">{side}</aside></div>{rel}</main>' + footer("../../"))
    return page(f'{title} · {SITE["title"]}', i.get("summary") or title, url + f'items/{i["id"]}/', body, 2)

def build_items(url, arc, conf_items=None):
    """One static page per item (permanent: every archived day, plus every conference paper), with Chinese abstract and a link out."""
    n = 0
    by_sec = {}
    for c in conf_items or []: by_sec.setdefault(c["section"], []).append(c)
    for sec, cs in by_sec.items():
        for c in cs:
            os.makedirs(f'site/items/{c["id"]}', exist_ok=True)
            open(f'site/items/{c["id"]}/index.html', "w").write(item_page(c, [r for r in cs if r["id"] != c["id"]][:4], url)); n += 1
    for day, items in arc.items():
        for i in items:
            i["tags"] = norm_tags(i.get("tags")); i["section"] = section_of(i); norm_badge(i)
        for i in items:
            related = sorted([r for r in items if r["id"] != i["id"] and r["section"] == i["section"]], key=rank, reverse=True)[:4]
            os.makedirs(f'site/items/{i["id"]}', exist_ok=True)
            open(f'site/items/{i["id"]}/index.html', "w").write(item_page(i, related, url)); n += 1
    print(f"item pages {n}", file=sys.stderr)

def build_papers(url, conf_items, tools):
    """顶会论文库: its own page (not a daily feed), filtered by the same topic tabs; data loaded from conf.json on demand."""
    if conf_items is None and not os.path.exists("site/conf.json"): return
    n = len(conf_items) if conf_items is not None else len(json.load(open("site/conf.json")))
    os.makedirs("site/papers", exist_ok=True)
    b = (header("../", active="papers") + f'<main class="wrap"><section class="hero"><div><h1>顶会论文库</h1><p>2025–2026 年 AI / 数据挖掘 / 视觉 / NLP 顶会中的医疗 AI 论文 · 共 {n} 篇 · 按方向、会议、年份、标签筛选</p></div></section>'
         f'{tools}<div id="confPanel" data-src="../conf.json"></div></main>' + footer("../"))
    open("site/papers/index.html", "w").write(page(f'顶会论文库 · {SITE["title"]}', f"医疗 AI 顶会论文 {n} 篇", url + "papers/", b, 1))

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
        b = header("../../", False, "weekly") + f'<main class="wrap"><section class="hero"><div><h1>周报 <span class="mono">{wk}</span></h1><p>{E(w["range"])}{" · 进行中" if w.get("partial") else ""} · <a href="../">全部周报</a></p></div></section><article class="card prose">{paras}<h2>本周条目</h2><ol class="refs">{refs}</ol></article></main>' + footer("../../")
        open(f"site/weekly/{wk}/index.html", "w").write(page(f'周报 {wk} · {SITE["title"]}', w["summary"][:120], url + f"weekly/{wk}/", b, 2))
        rows += f'<li><a href="{wk}/"><span class="mono">{wk}</span> · {E(w["range"])}</a></li>'
        ritems.append(dict(id="w" + wk, title=f'周报 {wk}', link=url + f"weekly/{wk}/", date=w["generated"], section="周报", summary=w["summary"][:300], source=SITE["title"], tags=[]))
    open("site/weekly/index.html", "w").write(page(f'周报 · {SITE["title"]}', "每周综述", url + "weekly/", header("../", False, "weekly") + f'<main class="wrap"><section class="hero"><div><h1>周报</h1><p><a href="feed.xml">周报 RSS</a></p></div></section><ul class="list">{rows or "<li>暂无</li>"}</ul></main>' + footer("../"), 1))
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

"""Shared fetch -> LLM summarize -> static build pipeline.
Concept inspired by AISafetyHot-Hub (CC BY-NC 4.0) and AIHOT (MIT); code written from scratch."""
import json, os, re, sys, time, hashlib, html, datetime as dt, urllib.request, urllib.parse, concurrent.futures as cf
import feedparser
from config import SITE, SOURCES, SECTIONS, PROMPT, FIELDS

UA = {"User-Agent": "Mozilla/5.0 (HubBot; +https://github.com/ChunXiaostudy)"}
NOW = dt.datetime.now(dt.timezone.utc)
KEEP_DAYS = 30
MAX_NEW = int(os.getenv("MAX_NEW", "60"))

def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def clean(s, n=600):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]

def norm(t): return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (t or "").lower())[:80]

def kw_ok(text, kws):
    if not kws: return True
    t = text.lower()
    return any(re.search(r"\b" + re.escape(k) + r"\b", t) if k.isascii() else k in t for k in kws)

def fetch_source(src):
    out = []
    try:
        kind = src.get("kind", "rss")
        if kind == "rss":
            f = feedparser.parse(get(src["url"]))
            for e in f.entries[: src.get("limit", 40)]:
                pub = e.get("published_parsed") or e.get("updated_parsed")
                d = dt.datetime(*pub[:6], tzinfo=dt.timezone.utc) if pub else NOW
                out.append(dict(title=clean(e.get("title"), 300), link=e.get("link", ""), desc=clean(e.get("summary", "")), date=d.isoformat(), pop=(lambda m: f"▲ {m.group(1)} points" if m else None)(re.search(r"Points: (\d+)", e.get("summary", "")))))
        elif kind == "github":
            since = (NOW - dt.timedelta(days=src.get("days", 7))).strftime("%Y-%m-%d")
            q = urllib.parse.quote(f"{src.get('q','')} created:>{since}".strip())
            j = json.loads(get(f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page={src.get('limit',20)}"))
            for r in j.get("items", []):
                out.append(dict(title=r["full_name"], link=r["html_url"], desc=clean(r.get("description") or "") + f" (language: {r.get('language')}, homepage: {r.get('homepage') or ''})",
                                date=r["created_at"], pop=f"★ {r['stargazers_count']}", image=r["owner"]["avatar_url"]))
        elif kind == "hf":
            j = json.loads(get(src["url"]))
            for m in j:
                out.append(dict(title=m["id"], link=f"https://huggingface.co/{m['id']}", desc="Hugging Face model; tags: " + ", ".join(m.get("tags", [])[:12]),
                                date=m.get("createdAt") or NOW.isoformat(), pop=f"♥ {m.get('likes',0)}"))
        elif kind == "pubmed":
            ids = json.loads(get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&sort=pub_date&retmax=%d&term=%s" % (src.get("limit", 25), urllib.parse.quote(src["term"]))))["esearchresult"]["idlist"]
            if ids:
                s = json.loads(get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id=" + ",".join(ids)))["result"]
                for i in ids:
                    r = s.get(i, {})
                    doi = next((a["value"] for a in r.get("articleids", []) if a.get("idtype") == "doi"), "")
                    out.append(dict(title=clean(r.get("title")), link=f"https://pubmed.ncbi.nlm.nih.gov/{i}/", desc=f"Journal: {r.get('fulljournalname','')}. {r.get('pubdate','')}",
                                    date=NOW.isoformat(), pop=None, badge=("DOI " + doi) if doi else f"PMID {i}"))
    except Exception as ex:
        print(f"[warn] {src['name']}: {ex}", file=sys.stderr)
    res = []
    for it in out:
        if not kw_ok(it["title"] + " " + it["desc"], src.get("keywords")): continue
        it["source"] = src["name"]; it["evidence_hint"] = src.get("evidence", "")
        it.setdefault("badge", "")
        if not it["badge"]:
            m = re.search(r"arxiv\.org/abs/([\d.]+)", it["link"]) or re.search(r"(10\.\d{4,}/[^\s?#%]+)", it["link"])
            if not m and "nature.com/articles/" in it["link"]: it["badge"] = "DOI 10.1038/" + it["link"].rsplit("/", 1)[-1]
            if m: it["badge"] = ("arXiv " if "arxiv" in it["link"] else "DOI ") + m.group(1)
        it["id"] = hashlib.sha1((it["link"] or it["title"]).encode()).hexdigest()[:12]
        res.append(it)
    print(f"[src] {src['name']}: {len(res)}", file=sys.stderr)
    return res

def llm(messages):
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    body = json.dumps({"model": os.getenv("LLM_MODEL", "deepseek-v4-flash"), "messages": messages, "temperature": 0.2}).encode()
    for a in range(3):
        try:
            req = urllib.request.Request(base + "/chat/completions", data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["LLM_API_KEY"]})
            with urllib.request.urlopen(req, timeout=180) as r:
                txt = json.loads(r.read())["choices"][0]["message"]["content"]
            m = re.search(r"\[.*\]", txt, re.S)
            return json.loads(m.group(0))
        except Exception as ex:
            print(f"[llm retry {a}] {ex}", file=sys.stderr); time.sleep(5)
    return None

def enrich(batch):
    payload = [dict(i=k, title=b["title"], source=b["source"], desc=b["desc"][:500], evidence_hint=b["evidence_hint"]) for k, b in enumerate(batch)]
    res = llm([{"role": "system", "content": PROMPT + "\n\n可选 section: " + " / ".join(SECTIONS) + "\n每条输出 JSON 对象，字段: i, " + FIELDS + "。只输出一个 JSON 数组，不要其他文字。"},
               {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    out = []
    for r in res or []:
        try:
            b = batch[int(r["i"])]
            if r.get("keep") is False and "_pid" not in b: continue
            r.pop("i", None); b.update(r); out.append(b)
        except Exception: pass
    return out

def main():
    os.makedirs("data", exist_ok=True)
    db_path = "data/items.json"
    db = json.load(open(db_path)) if os.path.exists(db_path) else []
    seen_ids = {d["id"] for d in db}; seen_t = {norm(d["title"]) for d in db}
    with cf.ThreadPoolExecutor(8) as ex:
        raw = [i for lst in ex.map(fetch_source, SOURCES) for i in lst]
    cutoff = NOW - dt.timedelta(days=KEEP_DAYS)
    new = []
    for it in raw:
        t = norm(it["title"])
        if it["id"] in seen_ids or t in seen_t: continue
        if dt.datetime.fromisoformat(it["date"].replace("Z", "+00:00")) < cutoff: continue
        seen_ids.add(it["id"]); seen_t.add(t); new.append(it)
    # round-robin across sources so one big feed doesn't crowd others out
    by = {}
    for it in new: by.setdefault(it["source"], []).append(it)
    pick = []
    while len(pick) < MAX_NEW and any(by.values()):
        for k in list(by):
            if by[k] and len(pick) < MAX_NEW: pick.append(by[k].pop(0))
    print(f"new candidates {len(new)}, processing {len(pick)}", file=sys.stderr)
    batches = [pick[i:i + 8] for i in range(0, len(pick), 8)]
    with cf.ThreadPoolExecutor(4) as ex:
        done = [i for lst in ex.map(enrich, batches) for i in lst]
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d")
    for d in done:
        d["day"] = today; d.pop("desc", None); d.pop("evidence_hint", None)
    db = [d for d in db if d.get("day", today) >= (NOW - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")] + done
    db.sort(key=lambda d: (d.get("day", ""), d.get("score", 0)), reverse=True)
    json.dump(db, open(db_path, "w"), ensure_ascii=False, indent=1)
    conf_items = []
    if os.path.exists("conf.py"):
        import conf
        conf_items = conf.run(lambda items: [x for lst in cf.ThreadPoolExecutor(6).map(enrich, [items[i:i + 8] for i in range(0, len(items), 8)]) for x in lst])
    build(db, conf_items)
    print(f"added {len(done)}, total {len(db)}", file=sys.stderr)
    if not done and not db: sys.exit(1)

def build(db, conf_items=None):
    os.makedirs("site", exist_ok=True)
    meta = dict(SITE, sections=SECTIONS, sources=sorted({s["name"] for s in SOURCES}), updated=NOW.isoformat())
    json.dump({"meta": meta, "items": db, "conf": conf_items or []}, open("site/data.json", "w"), ensure_ascii=False)
    tpl = open("template.html", encoding="utf-8").read()
    open("site/index.html", "w").write(tpl.replace("{{TITLE}}", html.escape(SITE["title"])).replace("{{DESC}}", html.escape(SITE["tagline"])).replace("{{MODE}}", SITE["mode"]))
    items = "".join(f"<item><title>{html.escape(d.get('zh_title') or d['title'])}</title><link>{html.escape(d['link'])}</link><guid>{d['id']}</guid><description>{html.escape(d.get('summary',''))}</description></item>" for d in db[:60])
    open("site/feed.xml", "w").write(f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{html.escape(SITE["title"])}</title><link>{SITE["url"]}</link><description>{html.escape(SITE["tagline"])}</description>{items}</channel></rss>')
    open("site/.nojekyll", "w").write("")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        import conf; conf.CAP = 0; build(json.load(open("data/items.json")), conf.run(lambda x: []) if conf.os.getenv("NOFETCH") is None else [])
    else: main()

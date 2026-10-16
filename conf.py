"""Top-conference (CCF-A + MICCAI) and medical-AI venue (ISBI / MIDL / CHIL / ML4H) papers via Semantic Scholar bulk search
(MIDL / ML4H from PMLR proceedings).
Persistent store data/conf.json (no 30-day expiry). Only items with LLM enrichment are shown."""
import json, math, os, re, sys, time, urllib.request, urllib.parse, hashlib, html
VENUES = {  # short: (S2 venue name, CCF rank)
 "ACL": ("Annual Meeting of the Association for Computational Linguistics", "CCF-A"),
 "EMNLP": ("Conference on Empirical Methods in Natural Language Processing", "CCF-B"),
 "NAACL": ("North American Chapter of the Association for Computational Linguistics", "CCF-B"),
 "AAAI": ("AAAI Conference on Artificial Intelligence", "CCF-A"),
 "IJCAI": ("International Joint Conference on Artificial Intelligence", "CCF-A"),
 "NeurIPS": ("Neural Information Processing Systems", "CCF-A"),
 "ICML": ("International Conference on Machine Learning", "CCF-A"),
 "ICLR": ("International Conference on Learning Representations", "无CCF评级"),
 "CVPR": ("Computer Vision and Pattern Recognition", "CCF-A"),
 "ICCV": ("IEEE International Conference on Computer Vision", "CCF-A"),
 "KDD": ("Knowledge Discovery and Data Mining", "CCF-A"),
 "WWW": ("The Web Conference", "CCF-A"),
 "SIGIR": ("Annual International ACM SIGIR Conference on Research and Development in Information Retrieval", "CCF-A"),
 "MM": ("ACM Multimedia", "CCF-A"),
 "MICCAI": ("International Conference on Medical Image Computing and Computer-Assisted Intervention", "CCF-B"),
 # 医疗 AI 专门会议：论文本身都属医疗领域，不再按标题关键词过滤。S2 的 venue 参数接受逗号分隔的多个名称
 "ISBI": ("IEEE International Symposium on Biomedical Imaging", "无CCF评级"),
 "CHIL": ("ACM Conference on Health, Inference, and Learning (CHIL)", "无CCF评级"),
 # MIDL / ML4H 论文在 S2 上多被记成 arXiv 预印本（venue / publicationVenue 均为 "arXiv.org"），按 venue 只能搜到 1 篇；
 # 两者都只在 PMLR 出版，改为直接读 PMLR 卷的 bibliography.bib（一卷一个请求，带摘要，无引用数）
 "MIDL": (None, "无CCF评级"),
 "ML4H": (None, "无CCF评级"),
}
PMLR = {"MIDL": r"\bMIDL\b|Medical Imaging with Deep Learning", "ML4H": r"\bML4H\b|Machine Learning for Health Symposium"}  # PMLR index title patterns
MED_VENUES = {"MICCAI", "ISBI", "MIDL", "CHIL", "ML4H"}
YEARS = "2025-2026"
TITLE_RE = re.compile(r"\b(medical|medicine|clinical|clinic|health|healthcare|patient|patients|disease|diagnos\w*|radiolog\w*|patholog\w*|biomedical|EHR|electronic health|hospital|surgical|surgery|cancer|tumou?r|MRI|CT|X-ray|chest|ECG|EEG|drug|molecul\w*|protein|genom\w*|cell|mental health|depression|alzheimer\w*|oncolog\w*|dermatolog\w*|ophthalm\w*|retina\w*|histopatholog\w*|lesion|ultrasound|endoscop\w*|pharma\w*|therap\w*)\b", re.I)
Q = "medical | clinical | healthcare | health | patient | radiology | pathology | biomedical | disease | diagnosis | surgical | drug | protein | EHR"
CAP = int(os.getenv("CONF_MAX", "120"))  # LLM-enrich at most this many new papers per run
TRANSLATE_CAP = int(os.getenv("CONF_TRANSLATE", "100"))  # translate this many abstracts per run (highest score first), backfilling gradually

def s2(params):
    url = "https://api.semanticscholar.org/graph/v1/paper/search/bulk?" + urllib.parse.urlencode(params)
    for a in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AI4Health-Hub"}), timeout=60) as r:
                return json.loads(r.read())
        except Exception as ex:
            print(f"[s2 retry {a}] {ex}", file=sys.stderr); time.sleep(5 * (a + 1))
    return {}

def http(url, tries=3):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AI4Health-Hub"}), timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as ex:
            if a == tries - 1: raise
            print(f"[pmlr retry {a}] {ex}", file=sys.stderr); time.sleep(5 * (a + 1))

def bib_entries(text):
    """@InProceedings entries of a PMLR bibliography.bib as dicts (brace-balanced values, outer braces dropped)."""
    out = []
    for m in re.finditer(r"@InProceedings\{([^,]+),", text):
        e, i = {"key": m.group(1).strip()}, m.end()
        while True:
            f = re.compile(r"\s*(\w+)\s*=\s*\{").match(text, i)
            if not f: break
            depth, j = 1, f.end()
            while depth and j < len(text):
                depth += {"{": 1, "}": -1}.get(text[j], 0); j += 1
            e[f.group(1).lower()] = text[f.end():j - 1]
            i = j + (text[j] == "," if j < len(text) else 0)
        out.append(e)
    return out

def tex(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"\\[`'^\"~]|[{}]", "", s or ""))).strip()

def pmlr_volumes(index):
    """{short: [(volume, conference year or None)]} for MIDL / ML4H volumes on the PMLR index page, newest first."""
    vols = {k: [] for k in PMLR}
    for li in re.findall(r"<li>(.*?)</li>", index, re.S):
        v = re.search(r'href="v(\d+)"', li)
        t = " ".join(re.sub(r"<[^>]+>", " ", li).split())
        for short, pat in PMLR.items():
            if v and re.search(pat, t):
                y = re.search(r"\b(20\d\d)\b", t)
                vols[short].append((int(v.group(1)), int(y.group(1)) if y else None))
    return {k: sorted(x, reverse=True) for k, x in vols.items()}

def fetch_pmlr(short, rank, vols):
    """Papers of one PMLR-only venue within YEARS. The index title's year is the conference year (ML4H 2025 is
    published in 2026); volumes without a year in the title are judged by the bib year."""
    lo, hi = (int(x) for x in YEARS.split("-"))
    out = {}
    for vol, year in vols[:4]:
        if year is not None and not lo <= year <= hi: continue
        try: entries = bib_entries(http(f"https://proceedings.mlr.press/v{vol}/assets/bib/bibliography.bib"))
        except Exception as ex: print(f"[conf] {short} v{vol}: {ex}", file=sys.stderr); continue
        time.sleep(1)
        for e in entries:
            y, title = year or int(e.get("year") or 0), tex(e.get("title"))
            if not lo <= y <= hi or not title or title.lower() in ("preface", "front matter"): continue
            url = e.get("url") or f"https://proceedings.mlr.press/v{vol}/{e['key'].split('-')[-1]}.html"
            mo = (re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", (e.get("month") or "").lower()) or [None])[0]
            mon = "%02d" % (["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"].index(mo) + 1) if mo else "01"
            pid = hashlib.sha1(url.encode()).hexdigest()   # 40 hex like an S2 paperId, so as_item ids stay unique
            out[pid] = dict(pid=pid, title=title, abstract=tex(e.get("abstract"))[:900], conf=short, rank=rank, conf_label=f"{short} {y}",
                            year=y, date=f"{y}-{mon}-01", link=url, cites=0)
    return out

def label(p, short):
    ids = p.get("externalIds") or {}
    doi = ids.get("DOI", "") or ""
    tag = ""
    if short in ("ACL", "EMNLP", "NAACL") and "findings" in doi.lower(): tag = " Findings"
    return f"{short} {p.get('year')}{tag}"

def fetch_all():
    out = {}
    try: vols = pmlr_volumes(http("https://proceedings.mlr.press/"))
    except Exception as ex: print(f"[conf] PMLR index: {ex}", file=sys.stderr); vols = {k: [] for k in PMLR}
    for short, (venue, rank) in VENUES.items():
        if venue is None:
            got = fetch_pmlr(short, rank, vols[short]); out.update(got)
            print(f"[conf] {short}: {len(got)} (PMLR v{','.join(str(v) for v, _ in vols[short][:4])})", file=sys.stderr); continue
        token, n = None, 0
        while True:
            prm = dict(query=Q, venue=venue, year=YEARS, fields="title,venue,year,externalIds,publicationDate,abstract,url,citationCount", sort="citationCount:desc")
            if token: prm["token"] = token
            j = s2(prm); time.sleep(1.2)
            for p in j.get("data", []):
                if not p.get("title") or (short not in MED_VENUES and not TITLE_RE.search(p["title"])): continue
                ids = p.get("externalIds") or {}
                link = f"https://doi.org/{ids['DOI']}" if ids.get("DOI") else (f"https://arxiv.org/abs/{ids['ArXiv']}" if ids.get("ArXiv") else p.get("url"))
                out[p["paperId"]] = dict(pid=p["paperId"], title=p["title"], abstract=(p.get("abstract") or "")[:900], conf=short, rank=rank, conf_label=label(p, short),
                                         year=p.get("year"), date=p.get("publicationDate") or f"{p.get('year')}-01-01", link=link, cites=p.get("citationCount") or 0)
                n += 1
            token = j.get("token")
            if not token: break
        print(f"[conf] {short}: {n}", file=sys.stderr)
    return out

def run(llm_enrich, translate=None):
    path = "data/conf.json"
    store = json.load(open(path)) if os.path.exists(path) else {}
    fetched = fetch_all()
    seen = {(c["conf"], c["title"].lower()) for c in store.values()}   # same paper found earlier under another id (S2 vs PMLR)
    for pid, c in fetched.items():
        if pid in store: store[pid]["cites"] = c["cites"]
        elif (c["conf"], c["title"].lower()) not in seen: store[pid] = c
    todo = [c for c in store.values() if "zh_title" not in c and not c.get("skip")]
    todo.sort(key=lambda c: -c["cites"]); by = {}
    for c in todo: by.setdefault(c["conf_label"], []).append(c)
    pick = []
    while len(pick) < CAP and any(by.values()):
        for k in list(by):
            if by[k] and len(pick) < CAP: pick.append(by[k].pop(0))
    print(f"[conf] fetched {len(fetched)}, store {len(store)}, pending {len(todo)}, enriching {len(pick)}", file=sys.stderr)
    items = [dict(title=c["title"], source=c["conf_label"], desc=c["abstract"], thin=not c["abstract"], evidence_hint="同行评审", _pid=c["pid"]) for c in pick]
    for r in llm_enrich(items):
        c = store[r["_pid"]]
        if r.get("keep") is False: c["skip"] = True; continue
        for k in ("zh_title", "summary", "key_points", "tags", "reason", "section"): c[k] = r.get(k)
        c["score"] = r.get("score", 5)
    if translate:
        todo = [c for c in store.values() if "zh_title" in c and not c.get("skip") and not c.get("zh_abstract") and len(c.get("abstract") or "") >= 200]
        todo.sort(key=lambda c: (-(c.get("score") or 0), -(c.get("cites") or 0)))
        translate(todo[:TRANSLATE_CAP])
    json.dump(store, open(path, "w"), ensure_ascii=False, indent=0)
    return shown(store)

def as_item(c):
    """A stored conference paper in the same shape as a daily item, so it renders with hub.card() and gets an item page."""
    from hub import norm_tags
    thin = not c.get("abstract")
    cites = c.get("cites") or 0
    return dict(id="c" + c["pid"][:11], title=c["title"], zh_title=c["zh_title"], link=c["link"], date=c.get("date") or "", source=c["conf_label"],
                source_group="顶会论文", conf=c["conf"], ccf=c["rank"], year=c.get("year"), cites=cites, evidence="同行评审", type="论文",
                summary=c.get("summary"), key_points=[] if thin else (c.get("key_points") or [])[:3], tags=norm_tags(c.get("tags")),
                score=c.get("score") or 5, reason=c.get("reason"), section=c.get("section"), thin=thin, badge=f'{c["conf_label"]} · {c["rank"]}',
                pop=f"引用 {cites}" if cites else None, abstract=None if thin else c["abstract"], zh_abstract=c.get("zh_abstract"),
                sc=round((c.get("score") or 5) + min(2, 0.6 * math.log10(cites + 1)), 2))

def shown(store):
    out = [as_item(c) for c in store.values() if "zh_title" in c and not c.get("skip")]
    out.sort(key=lambda x: -x["sc"])
    return out

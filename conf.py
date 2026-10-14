"""Top-conference (CCF-A + MICCAI) health/medical-AI papers via Semantic Scholar bulk search.
Persistent store data/conf.json (no 30-day expiry). Only items with LLM enrichment are shown."""
import json, os, re, sys, time, urllib.request, urllib.parse, hashlib
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
}
YEARS = "2025-2026"
TITLE_RE = re.compile(r"\b(medical|medicine|clinical|clinic|health|healthcare|patient|patients|disease|diagnos\w*|radiolog\w*|patholog\w*|biomedical|EHR|electronic health|hospital|surgical|surgery|cancer|tumou?r|MRI|CT|X-ray|chest|ECG|EEG|drug|molecul\w*|protein|genom\w*|cell|mental health|depression|alzheimer\w*|oncolog\w*|dermatolog\w*|ophthalm\w*|retina\w*|histopatholog\w*|lesion|ultrasound|endoscop\w*|pharma\w*|therap\w*)\b", re.I)
Q = "medical | clinical | healthcare | health | patient | radiology | pathology | biomedical | disease | diagnosis | surgical | drug | protein | EHR"
CAP = int(os.getenv("CONF_MAX", "120"))  # LLM-enrich at most this many new papers per run

def s2(params):
    url = "https://api.semanticscholar.org/graph/v1/paper/search/bulk?" + urllib.parse.urlencode(params)
    for a in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AI4Health-Hub"}), timeout=60) as r:
                return json.loads(r.read())
        except Exception as ex:
            print(f"[s2 retry {a}] {ex}", file=sys.stderr); time.sleep(5 * (a + 1))
    return {}

def label(p, short):
    ids = p.get("externalIds") or {}
    doi = ids.get("DOI", "") or ""
    tag = ""
    if short in ("ACL", "EMNLP", "NAACL") and "findings" in doi.lower(): tag = " Findings"
    return f"{short} {p.get('year')}{tag}"

def fetch_all():
    out = {}
    for short, (venue, rank) in VENUES.items():
        token, n = None, 0
        while True:
            prm = dict(query=Q, venue=venue, year=YEARS, fields="title,venue,year,externalIds,publicationDate,abstract,url,citationCount", sort="citationCount:desc")
            if token: prm["token"] = token
            j = s2(prm); time.sleep(1.2)
            for p in j.get("data", []):
                if not p.get("title") or not TITLE_RE.search(p["title"]): continue
                ids = p.get("externalIds") or {}
                link = f"https://doi.org/{ids['DOI']}" if ids.get("DOI") else (f"https://arxiv.org/abs/{ids['ArXiv']}" if ids.get("ArXiv") else p.get("url"))
                out[p["paperId"]] = dict(pid=p["paperId"], title=p["title"], abstract=(p.get("abstract") or "")[:900], conf=short, rank=rank, conf_label=label(p, short),
                                         year=p.get("year"), date=p.get("publicationDate") or f"{p.get('year')}-01-01", link=link, cites=p.get("citationCount") or 0)
                n += 1
            token = j.get("token")
            if not token: break
        print(f"[conf] {short}: {n}", file=sys.stderr)
    return out

def run(llm_enrich):
    path = "data/conf.json"
    store = json.load(open(path)) if os.path.exists(path) else {}
    fetched = fetch_all()
    for pid, c in fetched.items():
        if pid in store: store[pid]["cites"] = c["cites"]
        else: store[pid] = c
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
        for k in ("zh_title", "summary", "key_points", "tags"): c[k] = r.get(k)
        c["score"] = r.get("score", 5)
    json.dump(store, open(path, "w"), ensure_ascii=False, indent=0)
    return shown(store)

def shown(store):
    from hub import norm_tags
    out = []
    for c in store.values():
        if "zh_title" not in c or c.get("skip"): continue
        thin = not c.get("abstract")
        doi = next(iter(re.findall(r"doi\.org/(.+)$", c["link"])), "")
        out.append(dict(id="c" + c["pid"][:11], t=c["title"], zt=c["zh_title"], s=c.get("summary"), kp=[] if thin else (c.get("key_points") or [])[:3], tg=norm_tags(c.get("tags")),
                        l=c["link"], d=c["date"], c=c["conf"], cl=c["conf_label"], r=c["rank"], y=c.get("year"), ci=c.get("cites", 0), th=thin, doi=doi,
                        sc=round((c.get("score") or 5) + min(2, 0.6 * __import__("math").log10((c.get("cites") or 0) + 1)), 2)))
    out.sort(key=lambda x: -x["sc"])
    return out

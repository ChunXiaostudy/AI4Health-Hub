import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hub, evalset, conf

def test_kw_ok_case_insensitive():
    assert hub.kw_ok("New MRI model for stroke", ["MRI"])
    assert hub.kw_ok("deep learning on ehr data", ["EHR"])
    assert hub.kw_ok("A CT scan study", ["CT"])
    assert not hub.kw_ok("Selecting the best ACT strategy", ["CT"])
    assert hub.kw_ok("Hospital uses AI triage", ["AI"])
    assert not hub.kw_ok("Fair housing report", ["AI"])
    assert hub.kw_ok("医学影像新进展", ["医学影像"])
    assert hub.kw_ok("anything", None)

def test_similar():
    assert hub.similar("FDA clears AI ECG tool from Acme", "FDA clears AI ECG tool from Acme Inc.")
    assert hub.similar("美国FDA批准首个AI心电图工具", "FDA批准首个AI心电图工具")
    assert not hub.similar("FDA clears AI ECG tool", "New pathology foundation model released")

def test_norm_tags():
    if hub.TAGS:
        t = hub.TAGS[0]
        assert hub.norm_tags([t, t, "不存在的标签"]) == [t]

def test_freshness():
    base = dict(score=8, source="NEJM AI", evidence="同行评审", day="2026-10-14")
    fresh, old = dict(base, id="a", date="2026-10-13T00:00:00Z"), dict(base, id="b", date="2026-08-25T00:00:00Z")
    assert hub.age_days(fresh) == 1 and hub.age_days(old) == 50
    assert hub.rank(fresh) > hub.rank(old)
    weak_fresh = dict(base, id="c", score=4, source="medRxiv", evidence="预印本", date="2026-10-14T00:00:00Z")
    assert [i["id"] for i in hub.top_picks([old, weak_fresh], n=1)] == ["c"]
    assert [i["id"] for i in hub.top_picks([old], n=5)] == ["b"]

def test_rubric_in_prompt():
    assert "评分标准" in hub.C.RUBRIC

def test_eval_metrics():
    pairs = [(2, True, 8), (2, True, 5), (1, True, 6), (0, True, 7), (0, False, None)]
    m = evalset.metrics(pairs, threshold=7)
    assert m["n"] == 5 and m["false_keep"] == 1 and m["false_drop"] == 0
    assert m["precision"] == 0.5 and m["recall"] == 0.5
    assert abs(evalset.spearman([1, 2, 3], [10, 20, 30]) - 1) < 1e-9
    assert evalset.spearman([1, 1, 1], [1, 2, 3]) is None

def test_stratified_covers_groups():
    pool = [dict(id=str(i), source_group="A" if i % 2 else "B", model_keep=i % 3 > 0, model_score=i % 10) for i in range(40)]
    out = evalset.stratified(pool, 6)
    assert len(out) == 6 and {r["source_group"] for r in out} == {"A", "B"} and any(not r["model_keep"] for r in out)

def test_score_display():
    it = dict(score=8, source="NEJM AI", evidence="同行评审", day="2026-10-14", date="2026-10-14T00:00:00Z", id="x", title="t", link="l",
              reason="多中心外部验证", tags=[], section="研究论文", type="论文")
    assert hub.tier(8) == ("重要", "hi") and hub.tier(5)[0] == "值得一看" and hub.tier(None)[0] == "边缘"
    assert abs(sum(hub.rank_parts(it).values()) - hub.rank(it)) < 0.01 and "综合排序分" in hub.score_tip(it)
    assert "评分 8" in hub.card(it) and "评分理由" in hub.card(it)
    f = hub.card(it, featured=True)
    assert "scorecard" in f and "多中心外部验证" in f

def test_sections_and_forms():
    assert hub.section_of(dict(section="影像与病理")) == "影像与病理"
    assert hub.section_of(dict(section="研究论文", tags=["病理", "肿瘤"])) == "影像与病理"
    assert hub.section_of(dict(section="临床落地与监管", type="监管", tags=["医学影像"])) == "落地与监管"
    assert hub.section_of(dict(section="产业与融资", tags=[])) == "产业动态"
    assert hub.section_of(dict(section="研究论文", tags=[], zh_title="单细胞基因调控")) == "生命科学与药物"
    f = lambda **k: hub.form_of(k)
    assert f(type="模型", source_group="bioRxiv") == "研究论文"          # a paper proposing a model is still a paper
    assert f(type="模型", source_group="GitHub") == "开源模型与代码"
    assert f(type="观点", source_group="NEJM AI") == "综述与观点"
    assert f(type="监管", source_group="Google News · FDA AI") == "新闻与监管"
    assert f(type="融资", source_group="Fierce Healthcare") == "新闻与监管"
    assert {f(type=t, source_group=g) for t in ("论文", "模型", "监管", "融资", "观点") for g in ("arXiv", "GitHub", "STAT News")} <= set(hub.C.FORMS)
    assert len(hub.C.SECTION_SLUGS) == len(hub.SECTIONS)

def test_item_page():
    it = dict(score=8, source="NEJM AI", evidence="同行评审", day="2026-10-14", date="2026-10-14T00:00:00Z", id="abc123", title="Orig title", link="https://x.org/a",
              zh_title="中文标题", summary="导读", key_points=["要点一"], reason="外部验证", tags=["医学影像"], type="论文", source_group="NEJM AI",
              abstract="Background: x. " * 20, zh_abstract="【背景】背景内容。【结果】结果内容。")
    assert 'href="items/abc123/"' in hub.card(it) and 'href="../items/abc123/"' in hub.card(it, base="../") and "原文 ↗" in hub.card(it)
    h = hub.item_page(it, [], "https://example.org/")
    assert "中文摘要" in h and "<b>【结果】</b>" in h and "阅读原文" in h and "查看英文原文摘要" in h and "https://x.org/a" in h
    h2 = hub.item_page(dict(it, zh_abstract=None, abstract=None), [], "https://example.org/")
    assert "暂无原文摘要" in h2

def test_conf_paper_uses_shared_card():
    c = dict(pid="a" * 40, title="Paper", zh_title="论文", link="https://doi.org/10.1/x", date="2025-05-31", conf="NeurIPS", conf_label="NeurIPS 2025",
             rank="CCF-A", year="2025", cites=49, summary="导读", key_points=["k"], tags=["医学影像"], score=8, abstract="A" * 300, zh_abstract="【背景】中文")
    it = conf.as_item(c)
    assert hub.form_of(it) == "研究论文" and hub.section_of(it) == "影像与病理"
    h = hub.card(it, base="../")
    assert 'href="../items/caaaaaaaaaaa/"' in h and "评分 8" in h and "原文 ↗" in h and "引用 49" in h and "NeurIPS 2025 · CCF-A" in h
    p = hub.item_page(it, [], "https://example.org/")
    assert "中文摘要" in p and "返回论文库" in p and "收录于" not in p

def test_backfill_uses_feed_and_redoes_summary():
    old = dict(id="p1", title="T", source="Lancet Digital Health", source_group="Lancet Digital Health", link="https://x/1", summary="old", score=5, day="2026-10-15")
    news = dict(id="n1", title="N", source="STAT News", source_group="STAT News", link="https://x/2", summary="s", day="2026-10-15")
    fresh = dict(id="p1", desc="Background: " + "x" * 300)
    calls = {}
    e, t = hub.enrich_all, hub.translate_all
    hub.enrich_all = lambda items: [dict(i, summary="new", score=8, reason="r") for i in items]
    hub.translate_all = lambda items: calls.setdefault("t", [i["id"] for i in items])
    try: hub.backfill([old, news], [(None, [fresh], None)])
    finally: hub.enrich_all, hub.translate_all = e, t
    assert old["abstract"].startswith("Background") and old["summary"] == "new" and old["score"] == 8 and calls["t"] == ["p1"]
    assert "abstract_days" not in news and "abstract_days" not in old   # news never backfilled; success clears the retry log

def test_backfill_retries_once_a_day_and_records_why():
    p = dict(id="p2", title="T", source="npj Digital Medicine", source_group="npj Digital Medicine", link="https://x/3", badge="DOI 10.1/x", day="2026-10-15", abstract_tries=3)
    fns = hub.fill_abstract, hub.europepmc_abstract, hub.openalex_abstract
    hub.fill_abstract = lambda x: x.update(_abs_err="page: HTTP Error 403: Forbidden")
    hub.europepmc_abstract = lambda doi: ""
    hub.openalex_abstract = lambda doi: "Background: " + "y" * 300
    try:
        ab, err = hub.find_abstract(p, {})
        assert ab.startswith("Background") and err == ""
        hub.openalex_abstract = lambda doi: (_ for _ in ()).throw(ValueError("404"))
        hub.backfill([p], [])     # an exhausted old counter no longer blocks it
        assert p["abstract_days"] == [hub.TODAY] and "abstract_tries" not in p
        assert p["abstract_err"] == "page: HTTP Error 403: Forbidden; europepmc: empty; openalex: 404"
        hub.backfill([p], [])     # not retried twice on the same day
        assert p["abstract_days"] == [hub.TODAY]
        p["abstract_days"] = ["d%d" % k for k in range(hub.BACKFILL_DAYS)]
        hub.backfill([p], [])     # gives up after BACKFILL_DAYS distinct days
        assert len(p["abstract_days"]) == hub.BACKFILL_DAYS
    finally: hub.fill_abstract, hub.europepmc_abstract, hub.openalex_abstract = fns

def test_verdicts_rejected_and_pool():
    batch = [dict(id=k, title=k, source="arXiv", desc="d") for k in ("keep", "drop", "missing")]
    raw = hub.llm_raw
    hub.llm_raw = lambda *a, **k: [dict(i=0, keep=True, score=6, tags=[]), dict(i=1, keep=False)]
    try: out = hub.enrich(batch)
    finally: hub.llm_raw = raw
    assert [b["id"] for b in out] == ["keep"] and [b["_verdict"] for b in batch] == [True, False, None]
    cwd, tmp = os.getcwd(), tempfile.mkdtemp()
    os.chdir(tmp)
    try:
        os.makedirs("data")
        hub.save_pool(batch); hub.save_pool(batch)   # re-run on the same day does not duplicate rows
        rows = [json.loads(l) for l in open(f"data/pool/{hub.TODAY}.jsonl")]
        assert [(r["id"], r["model_keep"]) for r in rows] == [("keep", True), ("drop", False), ("missing", None)]
        rej = hub.save_rejected({"old": "2000-01-01"}, batch)
        assert rej == {"drop": hub.TODAY} and hub.load_rejected() == rej   # failures are retried, expired ids pruned
    finally: os.chdir(cwd)

def test_unknown_date_gets_no_freshness():
    it = dict(score=6, source="arXiv", day="2026-10-14", date="not a date")
    assert hub.age_days(it) > hub.FEATURED_MAX_AGE and hub.rank_parts(it)["新鲜度"] < 1

def test_local_favicons():
    cwd, tmp = os.getcwd(), tempfile.mkdtemp()
    os.chdir(tmp)
    try:
        os.makedirs("data/fav")
        open("data/fav/www.nature.com.png", "wb").write(b"png"); open("data/fav/empty.org.png", "wb").write(b"")
        assert hub.fav_img(dict(link="https://www.nature.com/articles/x"), "../") .startswith('<img src="../fav/www.nature.com.png"')
        assert hub.fav_img(dict(link="https://empty.org/a")) == "" and hub.fav_img(dict(link="https://unknown.org/a")) == ""
        assert "google.com" not in hub.card(dict(id="x", title="t", link="https://www.nature.com/a", source="s", tags=[]))
    finally: os.chdir(cwd)

def test_arxiv_link_keeps_id():
    assert hub.arxiv_link("http://arxiv.org/abs/2610.01234v2") == "https://arxiv.org/abs/2610.01234"
    assert hub.arxiv_link("https://arxiv.org/abs/2610.01234") == "https://arxiv.org/abs/2610.01234"

def test_parse_array_tolerates_bad_replies():
    assert hub.parse_array('```json\n[{"i": 0}, {"i": 1}]\n```') == [{"i": 0}, {"i": 1}]
    assert hub.parse_array('[{"i": 0}]\n\n[{"i": 9}]') == [{"i": 0}]                      # "Extra data"
    assert hub.parse_array('[{"i": 0, "s": "a"}, {"i": 1, "s": "说"好""}, {"i": 2}]') == [{"i": 0, "s": "a"}, {"i": 2}]  # one broken item
    try: hub.parse_array("sorry"); assert False
    except ValueError: pass

def test_enrich_all_retries_missing():
    batch = [dict(id=k, title=k, source="arXiv", desc="d") for k in "abc"]
    raw, calls = hub.llm_raw, []
    def fake(msgs, **k):
        n = len(json.loads(msgs[1]["content"])); calls.append(n)
        return [dict(i=0, keep=True, score=6, tags=[])] if n == 3 else [dict(i=j, keep=True, score=6, tags=[]) for j in range(n)]
    hub.llm_raw = fake
    try: out = hub.enrich_all(batch)
    finally: hub.llm_raw = raw
    assert calls == [3, 2] and sorted(b["id"] for b in out) == ["a", "b", "c"]

def test_select_round_robin_best_first():
    mk = lambda g, t, d="": dict(source_group=g, title=t, desc=d)
    new = [mk("arXiv", "weather forecasting"), mk("arXiv", "deep learning for clinical radiology", "x" * 300), mk("arXiv", "a third"), mk("NEJM AI", "trial")]
    pick = hub.select(new, 3)
    assert [p["source_group"] for p in pick] == ["NEJM AI", "arXiv", "arXiv"]   # heavier source first in the round
    assert pick[1]["title"] == "deep learning for clinical radiology"            # best candidate of a source first

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("ok", k)

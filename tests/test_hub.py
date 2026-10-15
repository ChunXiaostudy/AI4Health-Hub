import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hub, evalset

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

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("ok", k)

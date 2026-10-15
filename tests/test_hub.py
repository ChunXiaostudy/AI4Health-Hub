import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hub

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

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("ok", k)

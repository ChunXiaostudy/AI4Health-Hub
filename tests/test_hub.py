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

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("ok", k)

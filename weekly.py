"""Weekly digest: LLM writes a connected Chinese summary of the week's top items (from the permanent archive)."""
import json, os, sys, glob, datetime as dt
import hub
from config import SITE

def main(which="last"):
    today = dt.datetime.now(hub.CST).date()
    monday = today - dt.timedelta(days=today.weekday())
    if which == "last": monday -= dt.timedelta(days=7)
    sunday = monday + dt.timedelta(days=6)
    y, w, _ = monday.isocalendar(); wk = f"{y}-W{w:02d}"
    items = []
    for p in glob.glob("data/archive/*.json"):
        d = os.path.basename(p)[:-5]
        if monday.isoformat() <= d <= sunday.isoformat(): items += json.load(open(p))
    if not items: print("no items for", wk); return
    for i in items: i["rank"] = hub.rank(i)
    top = sorted(items, key=lambda i: i["rank"], reverse=True)[:20]
    lst = [dict(n=k + 1, title=i.get("zh_title") or i.get("name") or i["title"], section=i.get("section"), source=i["source"], summary=i.get("summary"),
                evidence=i.get("evidence"), why=i.get("why")) for k, i in enumerate(top)]
    tone = "严谨克制，不给任何医疗建议，预印本需注明未经同行评审" if SITE["mode"] == "health" else "轻松有趣，点出亮点"
    msg = [{"role": "system", "content": f"你是周报编辑，语气{tone}。根据给定的本周条目写一篇 400-700 字的中文周报综述：按主题串联成 3-5 段连贯文字（不要列表），"
                                          "只使用条目中给出的事实，不得编造数字或结论；提到某条时在句末用 [n] 标注其编号。直接输出正文段落，段落之间空行。"},
           {"role": "user", "content": json.dumps(lst, ensure_ascii=False)}]
    text = hub.llm_raw(msg, want="text")
    if not text: sys.exit("LLM failed")
    os.makedirs("data/weekly", exist_ok=True)
    json.dump(dict(week=wk, range=f"{monday} ~ {sunday}", partial=which != "last", generated=dt.datetime.now(dt.timezone.utc).isoformat(), summary=text.strip(),
                   items=[dict(title=x["title"], link=i["link"], source=i["source"]) for x, i in zip(lst, top)]), open(f"data/weekly/{wk}.json", "w"), ensure_ascii=False, indent=1)
    print("wrote", wk, len(top))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "last")

"""Scoring evaluation set: measure how well the LLM score matches your own judgment.

  python evalset.py sample [--n 100]          # draw unlabeled items into eval/labels.csv
  (open eval/labels.csv, fill the `label` column with 精选 / 收录 / 不收)
  python evalset.py run --offline             # score what production already gave (no API key needed)
  python evalset.py run [--threshold 7]       # re-score labeled items with the current prompt + rubric (needs LLM_* env)

Items come from data/pool/*.jsonl (raw LLM inputs saved daily by hub.py, including rejected ones);
before the pool has data, data/archive/*.json is used as a fallback (title + LLM summary as text, kept items only)."""
import argparse, csv, glob, json, os, random, sys, datetime as dt

CSV_PATH = "eval/labels.csv"
COLS = ["label", "note", "id", "source", "title", "desc", "link", "day", "model_score", "model_keep", "evidence_hint", "thin", "desc_src"]
LEVEL = {"精选": 2, "收录": 1, "不收": 0, "2": 2, "1": 1, "0": 0}
NAME = {2: "精选", 1: "收录", 0: "不收"}

# ---------------------------------------------------------------- io
def load_csv(path=CSV_PATH):
    if not os.path.exists(path): return []
    with open(path, encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))

def save_csv(rows, path=CSV_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel / Numbers open Chinese correctly
        w = csv.DictWriter(f, COLS, extrasaction="ignore"); w.writeheader(); w.writerows(rows)

def candidates():
    rows = {}
    for p in sorted(glob.glob("data/pool/*.jsonl")):
        for line in open(p, encoding="utf-8"):
            r = json.loads(line); r["desc_src"] = "raw"
            if r.get("model_keep") is None: continue  # LLM gave no answer (failed call), not a rejection
            rows[r["id"]] = r
    for p in sorted(glob.glob("data/archive/*.json")):
        for i in json.load(open(p, encoding="utf-8")):
            if i["id"] in rows: continue
            rows[i["id"]] = dict(i, desc=" ".join([i.get("summary") or ""] + (i.get("key_points") or [])), model_keep=True,
                                 model_score=i.get("score"), evidence_hint=i.get("evidence_hint") or i.get("evidence", ""), desc_src="summary")
    return list(rows.values())

# ---------------------------------------------------------------- sample
def stratified(pool, n, seed=0):
    """Round-robin over (source_group, score bucket) so the sample covers all sources and score levels, rejects included."""
    rnd = random.Random(seed); groups = {}
    for r in pool:
        s = r.get("model_score")
        b = "rejected" if not r.get("model_keep") else ("hi" if (s or 0) >= 7 else "mid" if (s or 0) >= 5 else "lo")
        groups.setdefault((r.get("source_group") or r.get("source"), b), []).append(r)
    for g in groups.values(): rnd.shuffle(g)
    keys = sorted(groups); out = []
    while len(out) < n and any(groups.values()):
        for k in keys:
            if groups[k] and len(out) < n: out.append(groups[k].pop())
    return out

def cmd_sample(a):
    have = load_csv(); seen = {r["id"] for r in have}
    pool = [r for r in candidates() if r["id"] not in seen]
    new = stratified(pool, a.n, a.seed)
    for r in new: r["label"] = ""; r["note"] = ""
    save_csv(have + new)
    print(f"added {len(new)} unlabeled rows to {CSV_PATH} (total {len(have) + len(new)}, labeled {sum(1 for r in have if r.get('label'))})")
    if any(r["desc_src"] == "summary" for r in new): print("note: some rows use the LLM summary as text because data/pool is still empty", file=sys.stderr)

# ---------------------------------------------------------------- metrics
def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0.0] * len(xs); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]: j += 1
        for k in range(i, j + 1): r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r

def spearman(a, b):
    if len(a) < 3: return None
    ra, rb = ranks(a), ranks(b); ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return cov / (va * vb) ** 0.5 if va and vb else None

def metrics(pairs, threshold=7):
    """pairs: [(gold level 0/1/2, model_keep bool, model_score int|None)]"""
    n = len(pairs)
    keep_ok = sum((g > 0) == k for g, k, _ in pairs)
    pred = [k and (s or 0) >= threshold for g, k, s in pairs]; gold = [g == 2 for g, _, _ in pairs]
    tp = sum(p and q for p, q in zip(pred, gold))
    prec = tp / sum(pred) if sum(pred) else None; rec = tp / sum(gold) if sum(gold) else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
    by = {}
    for g, k, s in pairs:
        if k and s is not None: by.setdefault(g, []).append(s)
    return dict(n=n, keep_acc=keep_ok / n if n else None, false_keep=sum(g == 0 and k for g, k, _ in pairs), false_drop=sum(g > 0 and not k for g, k, _ in pairs),
                precision=prec, recall=rec, f1=f1, spearman=spearman([g for g, _, _ in pairs], [(s or 0) if k else 0 for _, k, s in pairs]),
                mean_by_label={NAME[g]: round(sum(v) / len(v), 2) for g, v in sorted(by.items(), reverse=True)},
                hist={s: sum(1 for _, k, x in pairs if k and x == s) for s in range(1, 11)})

def report(rows, preds, threshold, mode):
    pairs, bad = [], []
    for r in rows:
        p = preds.get(r["id"])
        if p is None: continue
        g, (k, s) = LEVEL[r["label"].strip()], p
        pairs.append((g, k, s))
        if (g == 2 and (not k or (s or 0) < threshold - 1)) or (g == 0 and k and (s or 0) >= threshold - 1) or (g == 1 and (not k or (s or 0) >= threshold)):
            bad.append((g, k, s, r))
    m = metrics(pairs, threshold)
    f = lambda x: "—" if x is None else f"{x:.2f}"
    L = [f"# 打分评测报告 · {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
         f"模式：{mode} · 精选门槛 score ≥ {threshold} · 已标注 {len(rows)} 条，参与评测 {m['n']} 条" + (f"（{len(rows) - m['n']} 条模型无返回，已跳过）" if len(rows) > m["n"] else ""), "",
         "| 指标 | 数值 | 说明 |", "|---|---|---|",
         f"| 收录判断准确率 | {f(m['keep_acc'])} | 收 / 不收 与你一致的比例 |",
         f"| 误收 / 漏收 | {m['false_keep']} / {m['false_drop']} | 你标「不收」它收了 / 你标要收它扔了 |",
         f"| 精选 precision | {f(m['precision'])} | 它判为精选的里面，你也认为是精选的比例 |",
         f"| 精选 recall | {f(m['recall'])} | 你标的精选里，它也打到门槛以上的比例 |",
         f"| 精选 F1 | {f(m['f1'])} | |",
         f"| Spearman 相关 | {f(m['spearman'])} | 打分高低与你的判断是否同向（1 为完全一致） |", "",
         "各标签平均分：" + " · ".join(f"{k} {v}" for k, v in m["mean_by_label"].items()), "",
         "分数分布：" + " ".join(f"{s}分×{c}" for s, c in m["hist"].items() if c), ""]
    if bad:
        L += [f"## 主要分歧（{len(bad)} 条，最多列 25 条）", "", "| 你的标注 | 模型 | 来源 | 标题 |", "|---|---|---|---|"]
        for g, k, s, r in sorted(bad, key=lambda x: -abs(x[0] * 4 - ((x[2] or 0) if x[1] else 0) / 2.5))[:25]:
            L.append(f"| {NAME[g]} | {'不收' if not k else f'{s} 分'} | {r['source']} | [{r['title'][:70].replace('|', '/')}]({r['link']}) |")
    return "\n".join(L) + "\n", m

def cmd_run(a):
    rows = [r for r in load_csv() if r.get("label", "").strip() in LEVEL]
    if not rows: sys.exit(f"no labeled rows in {CSV_PATH}; run `python evalset.py sample` and fill the label column first")
    if a.offline:
        preds = {r["id"]: (r["model_keep"] in ("True", "true", "1"), int(float(r["model_score"])) if r.get("model_score") else None) for r in rows}
        mode = "离线（生产环境当时给的分）"
    else:
        import hub
        items = [dict(title=r["title"], source=r["source"], desc=r["desc"], evidence_hint=r.get("evidence_hint", ""), thin=r.get("thin") in ("True", "true", "1"), _pid=r["id"]) for r in rows]
        out = hub.enrich_all(items)
        preds = {i["_pid"]: (i.get("keep") is not False, int(i["score"]) if str(i.get("score", "")).strip().isdigit() else None) for i in out}
        mode = f"在线重打分（{os.getenv('LLM_MODEL', 'deepseek-v4-flash')}）"
    text, _ = report(rows, preds, a.threshold, mode)
    os.makedirs("eval", exist_ok=True); open("eval/last_report.md", "w", encoding="utf-8").write(text)
    if os.getenv("GITHUB_STEP_SUMMARY"): open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8").write(text)
    print(text)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("sample"); s.add_argument("--n", type=int, default=100); s.add_argument("--seed", type=int, default=0); s.set_defaults(f=cmd_sample)
    r = sp.add_parser("run"); r.add_argument("--threshold", type=int, default=7); r.add_argument("--offline", action="store_true"); r.set_defaults(f=cmd_run)
    a = ap.parse_args(); a.f(a)

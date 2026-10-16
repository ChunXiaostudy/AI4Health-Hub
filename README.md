# AI4Health-Hub

医疗 AI 每日动态，按方向分为：影像与病理、大模型与智能体、临床预测与决策、生命科学与药物、数字健康、落地与监管、产业动态；每个方向内可再按形式筛选（研究论文 / 综述与观点 / 开源模型与代码 / 新闻与监管，四类互斥）。顶会论文在单独的「论文库」页面 `/papers/`。内容由 LLM 自动摘要，仅供参考，不构成医疗建议；预印本未经同行评审。

- 网站: https://chunxiaostudy.github.io/AI4Health-Hub/
- 每日 07:30（北京时间）由 GitHub Actions 自动抓取 → LLM 中文摘要/分类/打分 → 构建静态站 → 发布到 GitHub Pages。
- 数据源见 `config.py`，历史数据保存在 `data/items.json`（保留 30 天）。

## 运行
```
pip install feedparser
LLM_API_KEY=... LLM_BASE_URL=... python hub.py   # 输出到 site/
```
Secrets: `LLM_API_KEY`, `LLM_BASE_URL`；可选变量 `LLM_MODEL`（默认 deepseek-v4-flash）。

## 致谢
思路参考 [wuyoscar/AISafetyHot-Hub](https://github.com/wuyoscar/AISafetyHot-Hub)（内容 CC BY-NC 4.0）与 [KKKKhazix/AIHOT](https://github.com/KKKKhazix/AIHOT)（MIT）。本仓库代码为独立重写，未复制其内容。条目版权归原始来源所有。

代码以 MIT 许可发布。

## 顶会论文
`conf.py` 通过 Semantic Scholar bulk search API 检索 2025–2026 年顶会（ACL/AAAI/IJCAI/NeurIPS/ICML/CVPR/ICCV/KDD/WWW/SIGIR/MM，及 EMNLP/NAACL/ICLR/MICCAI；CCF 等级按实际标注）中标题含医疗/临床/生物医学关键词的论文，持久保存在 `data/conf.json`（不过期），每日刷新，每次最多新摘要 120 篇（`CONF_MAX`）。展示时经 `conf.as_item()` 转成与每日条目相同的结构，用同一个 `hub.card()` 渲染（预渲染进 `site/conf.json`，论文库页只做筛选和分页），并同样生成 `/items/<id>/` 详情页；摘要中文翻译每次按分数从高到低补 100 篇（`CONF_TRANSLATE`），未翻译的先显示英文原文摘要。

## 结构（v2）
- `hub.py`：抓取 → 去重（标题相似度 + 事件合并，显示「N 个来源报道」）→ LLM 摘要（固定标签枚举）→ 综合排序（LLM 分 + 来源权重 + 证据级别 + 星标/引用/HN 分）→ 预渲染静态 HTML。
- 每个条目有站内详情页 `/items/<id>/`：AI 导读、原文摘要的中文全文翻译（结构化摘要保留【背景】【方法】【结果】【结论】）、评分卡、方向/形式/证据、复制引用、「阅读原文」按钮。原文摘要（≤2500 字）存为 `abstract`，译文存为 `zh_abstract`（`hub.translate_all`，只翻译 ≥200 字的摘要）。摘要功能上线前收录的论文类条目由 `hub.backfill()` 逐步补齐（每次最多 60 条，依次尝试：本次抓到的同一条 RSS 全文 → Nature 系期刊用 Springer 引用导出（RIS）/ 其他文章页 meta → bioRxiv / medRxiv 官方 API → PubMed / Europe PMC / OpenAlex），补到摘要后重新生成导读与评分并翻译。新收录的论文在同一次运行里就会进入补摘要；Nature 系 RSS 的「期刊名, Published online: …; doi:…」简介不算摘要。依次尝试的来源还包括 OpenAlex；每条每天最多尝试一次，最多尝试 7 个不同的日子（新刊文章要过几天才会进 OpenAlex 等索引），失败原因记在条目的 `abstract_err` 字段。NEJM AI 因 RSS 只有一句话且站点拒绝抓取，通常补不到。
- `data/items.json` 滚动 30 天；`data/archive/YYYY-MM-DD.json` 永久归档 → `/YYYY-MM-DD/` 与 `/archive/`。
- `data/status.json` 记录每次各来源条数 → `/status.html`；连续 3 次为 0 自动开/更新 issue。
- RSS：`feed.xml`；按方向 `feed-{imaging,llm-agents,clinical-prediction,life-science,digital-health,regulation,industry}.xml`；按形式 `feed-{papers,models,news,opinion}.xml`；周报 `weekly/feed.xml`。旧的 `feed-clinical-regulatory.xml` 继续生成（= 落地与监管）。
- `weekly.py` + `.github/workflows/weekly.yml`：每周一生成周报 `/weekly/YYYY-Www/`。
- 测试：`python tests/test_hub.py`。

## 打分标准与评测集
- 评分标准写在 [`prompts/score_rubric.md`](prompts/score_rubric.md)，会拼进 LLM 的系统提示。改标准只改这个文件。
- `hub.py` 每天把送进 LLM 的原始输入和模型判定（收 / 不收、分数）存到 `data/pool/YYYY-MM-DD.jsonl`（保留 30 天），包括被模型丢掉的条目。
- 评测流程：
  1. `python evalset.py sample --n 100`：从 pool（为空时退回归档）按来源 × 分数段分层抽样，追加到 `eval/labels.csv`。
  2. 用 Excel / Numbers 打开 `eval/labels.csv`，在 `label` 列填 **精选 / 收录 / 不收**（`note` 列可写理由），保存后提交。建议先标 100–200 条。
  3. `python evalset.py run --offline`：评测生产环境当时打的分，不需要 API Key。
  4. 改了 `score_rubric.md` 后，在 Actions 里手动运行 **Scoring Eval**（或本地设好 `LLM_*` 后 `python evalset.py run`），用新提示词重新打分，对比报告里的精选 precision / recall、Spearman 和「主要分歧」列表。
- 报告写到 `eval/last_report.md`，在 Actions 里也会显示在运行摘要页。

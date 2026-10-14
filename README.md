# AI4Health-Hub

医疗 AI 每日动态：研究论文、模型与开源、临床落地与监管、产业与融资、观点与综述。内容由 LLM 自动摘要，仅供参考，不构成医疗建议；预印本未经同行评审。

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
`conf.py` 通过 Semantic Scholar bulk search API 检索 2025–2026 年顶会（ACL/AAAI/IJCAI/NeurIPS/ICML/CVPR/ICCV/KDD/WWW/SIGIR/MM，及 EMNLP/NAACL/ICLR/MICCAI；CCF 等级按实际标注）中标题含医疗/临床/生物医学关键词的论文，持久保存在 `data/conf.json`（不过期），每日刷新，每次最多新摘要 120 篇（`CONF_MAX`）。

## 结构（v2）
- `hub.py`：抓取 → 去重（标题相似度 + 事件合并，显示「N 个来源报道」）→ LLM 摘要（固定标签枚举）→ 综合排序（LLM 分 + 来源权重 + 证据级别 + 星标/引用/HN 分）→ 预渲染静态 HTML。
- `data/items.json` 滚动 30 天；`data/archive/YYYY-MM-DD.json` 永久归档 → `/YYYY-MM-DD/` 与 `/archive/`。
- `data/status.json` 记录每次各来源条数 → `/status.html`；连续 3 次为 0 自动开/更新 issue。
- RSS：`feed.xml`、各分区 `feed-<section>.xml`、周报 `weekly/feed.xml`。
- `weekly.py` + `.github/workflows/weekly.yml`：每周一生成周报 `/weekly/YYYY-Www/`。
- 测试：`python tests/test_hub.py`。

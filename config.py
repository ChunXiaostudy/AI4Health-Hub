import os
SITE = dict(mode="health", title="AI4Health Hub · 医疗 AI 每日动态", wordmark="AI<b>4</b>Health", tagline="医疗 AI 研究与落地日报",
  url="https://chunxiaostudy.github.io/AI4Health-Hub/", repo="https://github.com/ChunXiaostudy/AI4Health-Hub",
  disclaimer="本站内容由程序自动抓取并经 LLM 摘要，仅供科研与信息参考，不构成任何医疗建议；预印本未经同行评审。")
MED = ["medical","clinical","patient","patients","health","healthcare","disease","diagnosis","radiology","pathology","biomedical","drug","protein","genomic","genomics","cancer","tumor","EHR","hospital","MRI","CT","x-ray","ECG","surgical","molecule","cell","medicine","clinician","medical imaging","health record","electronic health"]
AIK = ["AI", "artificial intelligence", "machine learning", "deep learning", "algorithm", "LLM", "language model", "neural network", "foundation model", "generative"]
HEALTH = ["health", "healthcare", "medical", "medicine", "clinical", "clinician", "patient", "patients", "hospital", "disease", "diagnosis", "drug", "protein", "genomics",
          "biology", "biomedical", "radiology", "pathology", "cancer", "doctor", "therapeutics", "life sciences", "biotech", "pharma",
          "antibiotic", "antimicrobial", "molecule", "molecular", "genome", "cell", "medicine"]
ZH_AI = ["AI", "人工智能", "大模型", "算法", "智能体", "机器学习", "深度学习"]
GN_EN = "&hl=en-US&gl=US&ceid=US:en"; GN_ZH = "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
# 主导航按「方向」分区；内容形式（论文 / 模型与代码 / 新闻 / 观点与综述）是第二层筛选，由 type 推出
SECTION_HINTS = {
  "大模型与智能体": "医学大语言模型、临床 NLP、电子病历文本、医患对话、临床智能体与 AI 助手",
  "影像与病理": "放射影像、病理、内镜、眼科、超声、手术视频等以图像为主的研究与产品",
  "临床预测与决策": "基于结构化数据或生理信号的风险预测、预后、分诊、临床决策支持、ICU / 急诊",
  "生命科学与药物": "药物发现、蛋白质、基因组与单细胞、分子与类器官等生物医学基础研究",
  "数字健康": "可穿戴、远程医疗、慢病与心理健康管理、公共卫生与流行病预测",
  "落地与监管": "部署与实施经验、FDA / NMPA 等监管与审批、证据标准、安全公平与隐私、治理与医学教育",
  "产业动态": "融资、并购、产品发布、医疗机构与企业合作、市场动态",
}
SECTIONS = list(SECTION_HINTS)
# 内容形式（互斥）：开源按信源判定；其余先看是否为综述/观点，再看是否来自论文类信源（SOURCES 里 evidence 为预印本/同行评审的）
FORMS = ["研究论文", "综述与观点", "开源模型与代码", "新闻与监管"]
FORM_SLUGS = {"研究论文": "papers", "综述与观点": "opinion", "开源模型与代码": "models", "新闻与监管": "news"}
OPEN_SOURCES = {"GitHub", "Hugging Face", "Hugging Face 数据集"}
# 旧数据迁移 / 兜底：按标签推断方向（按顺序取第一个命中的）
TAG_SECTION = {"监管政策": "落地与监管", "医疗器械": "落地与监管", "临床落地": "落地与监管", "公平与安全": "落地与监管", "隐私与联邦学习": "落地与监管",
  "融资与并购": "产业动态", "医学影像": "影像与病理", "病理": "影像与病理", "眼科": "影像与病理", "手术": "影像与病理",
  "医学大模型": "大模型与智能体", "临床NLP": "大模型与智能体", "智能体": "大模型与智能体", "电子病历": "大模型与智能体",
  "药物发现": "生命科学与药物", "基因组学": "生命科学与药物", "蛋白质": "生命科学与药物",
  "心理健康": "数字健康", "公共卫生": "数字健康", "数字健康": "数字健康",
  "临床预测": "临床预测与决策", "临床决策支持": "临床预测与决策", "生理信号": "临床预测与决策", "肿瘤": "临床预测与决策", "神经科学": "临床预测与决策"}
OLD_SECTION = {"产业与融资": "产业动态", "临床落地与监管": "落地与监管"}
SOURCES = [
 dict(name="arXiv", kind="arxiv", oai_sets=["cs:cs:AI","cs:cs:CV","cs:cs:CL","eess:eess:IV","q-bio:q-bio:QM"], url="https://export.arxiv.org/api/query?search_query=(cat:cs.AI+OR+cat:cs.CV+OR+cat:cs.CL+OR+cat:cs.LG+OR+cat:eess.IV+OR+cat:q-bio.QM)+AND+(abs:medical+OR+abs:clinical+OR+abs:radiology+OR+abs:pathology+OR+abs:biomedical+OR+abs:patient)&sortBy=submittedDate&sortOrder=descending&max_results=60", keywords=MED, limit=60, evidence="预印本"),
 dict(name="medRxiv", url="https://connect.medrxiv.org/medrxiv_xml.php?subject=Health_Informatics", evidence="预印本"),
 dict(name="bioRxiv", url="https://connect.biorxiv.org/biorxiv_xml.php?subject=bioinformatics", keywords=["deep learning","machine learning","language model","neural","AI","foundation model","transformer"], evidence="预印本"),
 dict(name="PubMed", kind="pubmed", max_age=30, reldate=3, limit=60, term='("artificial intelligence"[tiab] OR "deep learning"[tiab] OR "large language model"[tiab]) AND hasabstract', evidence="同行评审"),
 dict(name="Nature Medicine", max_age=60, url="https://www.nature.com/nm.rss", evidence="同行评审"),
 dict(name="npj Digital Medicine", max_age=60, url="https://www.nature.com/npjdigitalmed.rss", evidence="同行评审"),
 dict(name="Lancet Digital Health", max_age=60, url="https://www.thelancet.com/rssfeed/landig_current.xml", evidence="同行评审"),
 dict(name="NEJM AI", max_age=60, url="https://ai.nejm.org/action/showFeed?type=etoc&feed=rss&jc=ai", evidence="同行评审"),
 dict(name="Hugging Face", kind="hf", max_age=30, days=30, min_likes=3, queries=["medical", "clinical", "radiology", "pathology", "biomedical", "health", "medgemma"]),
 dict(name="GitHub", kind="github", q="medical OR clinical OR healthcare OR biomedical", days=14, limit=15),
 dict(name="STAT News", url="https://www.statnews.com/feed/", keywords=["AI","artificial intelligence","algorithm","machine learning","LLM","digital health","FDA"]),
 dict(name="Fierce Healthcare", url="https://www.fiercehealthcare.com/rss/xml", keywords=["AI","artificial intelligence","algorithm","machine learning","LLM","digital health"]),
 dict(name="Google News · FDA AI", title_only=True, url="https://news.google.com/rss/search?q=FDA+(AI+OR+%22artificial+intelligence%22)+medical+device+when:3d&hl=en-US&gl=US&ceid=US:en", limit=15),
 dict(name="Google News · NMPA AI", title_only=True, url="https://news.google.com/rss/search?q=%E5%9B%BD%E5%AE%B6%E8%8D%AF%E7%9B%91%E5%B1%80+%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+%E5%8C%BB%E7%96%97%E5%99%A8%E6%A2%B0+when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", limit=10),
 # ---- 重点期刊：被拦截 RSS 的期刊（JAMA Netw Open / Radiology: AI / JAMIA / IEEE TMI）走 PubMed 期刊检索
 dict(name="PubMed · 重点期刊", kind="pubmed", max_age=30, reldate=7, limit=40, evidence="同行评审",
      term='("JAMA Netw Open"[ta] OR "Radiol Artif Intell"[ta] OR "J Am Med Inform Assoc"[ta] OR "IEEE Trans Med Imaging"[ta]) AND ("artificial intelligence"[tiab] OR "machine learning"[tiab] OR "deep learning"[tiab] OR "large language model"[tiab] OR "neural network"[tiab] OR "natural language processing"[tiab])'),
 dict(name="Nature Biomedical Engineering", max_age=60, url="https://www.nature.com/natbiomedeng.rss", keywords=AIK, evidence="同行评审"),
 dict(name="Nature Machine Intelligence", max_age=60, url="https://www.nature.com/natmachintell.rss", keywords=MED, evidence="同行评审"),
 dict(name="eClinicalMedicine", max_age=60, url="https://www.thelancet.com/rssfeed/eclinm_current.xml", keywords=AIK, evidence="同行评审"),
 dict(name="Cell Reports Medicine", max_age=60, url="https://www.cell.com/cell-reports-medicine/inpress.rss", keywords=AIK, evidence="同行评审"),
 dict(name="Medical Image Analysis", max_age=60, url="https://rss.sciencedirect.com/publication/science/13618415", evidence="同行评审"),
 dict(name="Hugging Face Daily Papers", kind="hf_papers", max_age=14, keywords=MED, evidence="预印本"),
 # ---- 大厂 / 实验室 / 创业公司官方博客（没有 RSS 的走 Google News site: 检索）
 dict(name="Google Research", max_age=30, url="https://research.google/blog/rss/", keywords=HEALTH),
 dict(name="Google DeepMind", max_age=30, url="https://deepmind.google/blog/rss.xml", keywords=HEALTH),
 dict(name="Microsoft Research", max_age=30, url="https://www.microsoft.com/en-us/research/feed/", keywords=HEALTH),
 dict(name="OpenAI", max_age=30, url="https://openai.com/news/rss.xml", keywords=HEALTH),
 dict(name="NVIDIA Blog", max_age=30, url="https://blogs.nvidia.com/feed/", keywords=HEALTH),
 dict(name="Google News · AI 公司动态", title_only=True, limit=15, keywords=HEALTH,
      url="https://news.google.com/rss/search?q=(site:anthropic.com+OR+site:isomorphiclabs.com+OR+site:abridge.com+OR+site:tempus.com+OR+site:hippocraticai.com+OR+site:owkin.com)+(health+OR+medical+OR+clinical+OR+drug)+when:7d" + GN_EN),
 # ---- 监管机构官方源
 dict(name="FDA 新闻稿", max_age=14, url="https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", keywords=AIK),
 dict(name="FDA AI 器械清单", kind="fda_ai", max_age=14, window=90, url="https://www.fda.gov/medical-devices/software-medical-device-samd/artificial-intelligence-enabled-medical-devices"),
 dict(name="EMA", max_age=14, url="https://www.ema.europa.eu/en/news.xml", keywords=AIK),
 dict(name="MHRA", max_age=14, url="https://www.gov.uk/government/organisations/medicines-and-healthcare-products-regulatory-agency.atom", keywords=AIK),
 dict(name="WHO", max_age=14, url="https://www.who.int/rss-feeds/news-english.xml", keywords=AIK),
 # ---- 中文媒体（36氪 / 机器之心 / 量子位 / 动脉网直连不稳定、RSSHub 公共实例 403，先走 Google News 中文检索）
 dict(name="Google News · 中文医疗AI", title_only=True, limit=20, keywords=ZH_AI,
      url="https://news.google.com/rss/search?q=(%E5%8C%BB%E7%96%97+%E5%A4%A7%E6%A8%A1%E5%9E%8B)+OR+%E5%8C%BB%E7%96%97AI+OR+%E5%8C%BB%E7%96%97%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+when:2d" + GN_ZH),
 dict(name="Google News · 中文医疗AI融资", title_only=True, limit=15, keywords=ZH_AI,
      url="https://news.google.com/rss/search?q=(%E5%8C%BB%E7%96%97AI+OR+%E5%8C%BB%E7%96%97%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+OR+AI%E5%88%B6%E8%8D%AF)+%E8%9E%8D%E8%B5%84+when:3d" + GN_ZH),
 # ---- 产业与融资（MobiHealthNews / Healthcare IT News / Fierce Biotech / Endpoints 直连 403，走 Google News site: 检索）
 dict(name="Google News · 行业媒体", title_only=True, limit=20,
      url="https://news.google.com/rss/search?q=(site:mobihealthnews.com+OR+site:healthcareitnews.com+OR+site:fiercebiotech.com+OR+site:endpts.com)+(AI+OR+%22artificial+intelligence%22)+when:3d" + GN_EN),
 dict(name="MedCity News", url="https://medcitynews.com/feed/", keywords=AIK),
 # ---- 社区热度 / 临床试验 / 数据集
 dict(name="Hacker News", kind="hn", days=7, min_points=20, queries=["medical AI", "healthcare AI", "clinical AI", "AI doctor", "AI drug discovery", "FDA AI", "AI radiology"]),
 dict(name="ClinicalTrials.gov", kind="ctgov", days=7, max_age=14, limit=50,
      term='"artificial intelligence" OR "machine learning" OR "deep learning" OR "large language model" OR "neural network"'),
 dict(name="Hugging Face 数据集", kind="hf", repo="datasets", max_age=30, days=30, min_likes=3, queries=["medical", "clinical", "radiology", "pathology", "biomedical", "health"]),
]
PROMPT = """你是严谨克制的医疗 AI 情报编辑。根据给定条目（仅依据其中提供的信息）生成中文结构化摘要。
规则：只陈述来源中的事实，绝不编造数字、结论或机构；不夸大；绝不给出任何医疗建议或诊疗推荐；预印本须视为未经同行评审。
若条目与 AI/算法在医疗健康、生物医学中的应用无关，keep=false；AI 只是顺带提及、信息几乎为零的宣传稿或旧闻重复报道，也 keep=false。拿不准时倾向 keep=false。"""
RUBRIC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "score_rubric.md"), encoding="utf-8").read()
FIELDS = """keep(bool), zh_title(中文标题,≤40字), summary(一句话摘要,≤80字), key_points(3条要点数组,每条≤30字), section(从可选 section 中按研究/事件所属方向选一，见各方向说明), type(论文/模型/监管/融资/观点 之一，表示内容形式), tags(从可选 tags 中选 1-3 个), evidence(预印本/同行评审/已获批/新闻 之一, 参考 evidence_hint；FDA/NMPA 批准类为 已获批), score(1-10 重要性整数，严格按下方评分标准), reason(打分理由,≤40字,点明对应评分标准中的档位与加减分依据,如「多中心外部验证+临床结局，属重要档」)"""

SECTION_SLUGS = ["llm-agents", "imaging", "clinical-prediction", "life-science", "digital-health", "regulation", "industry"]
TAGS = ["医学影像", "病理", "临床NLP", "医学大模型", "药物发现", "基因组学", "蛋白质", "多模态", "数据集", "基准", "智能体", "生成模型",
        "电子病历", "临床决策支持", "临床预测", "心理健康", "公共卫生", "生理信号", "手术", "眼科", "肿瘤", "神经科学", "可解释性",
        "公平与安全", "隐私与联邦学习", "医疗器械", "监管政策", "临床落地", "数字健康", "融资与并购"]
TAG_SYNONYMS = {"分割": "医学影像", "图像分割": "医学影像", "超声": "医学影像", "CT": "医学影像", "MRI": "医学影像", "神经影像": "医学影像", "放射科": "医学影像", "放射学": "医学影像",
  "报告生成": "医学影像", "医学VQA": "多模态", "视觉语言模型": "多模态", "多模态融合": "多模态", "精神健康": "心理健康", "电子健康记录": "电子病历", "EHR": "电子病历",
  "临床决策": "临床决策支持", "扩散模型": "生成模型", "分子生成": "药物发现", "蛋白质设计": "蛋白质", "蛋白质结构": "蛋白质", "联邦学习": "隐私与联邦学习",
  "隐私保护": "隐私与联邦学习", "脑电": "生理信号", "心电图": "生理信号", "ECG": "生理信号", "EEG": "生理信号", "手术视频": "手术", "手术导航": "手术",
  "多智能体": "智能体", "Agent": "智能体", "单细胞": "基因组学", "单细胞组学": "基因组学", "生物信息学": "基因组学", "监管": "监管政策", "医疗AI监管": "监管政策",
  "上市后监测": "监管政策", "FDA": "监管政策", "NMPA": "监管政策", "落地": "临床落地", "临床应用": "临床落地", "实施科学": "临床落地", "实施评估": "临床落地",
  "远程医疗": "数字健康", "慢病管理": "数字健康", "肿瘤筛查": "肿瘤", "肿瘤预后": "肿瘤", "癌症": "肿瘤", "阿尔茨海默病": "神经科学", "神经科": "神经科学",
  "公平性": "公平与安全", "患者安全": "公平与安全", "安全": "公平与安全", "临床预测模型": "临床预测", "风险分层": "临床预测", "基准测试": "基准", "评测": "基准",
  "基础模型": "医学大模型", "大语言模型": "医学大模型", "LLM": "医学大模型", "融资": "融资与并购", "并购": "融资与并购"}
SOURCE_WEIGHTS = {"NEJM AI": 3.0, "Nature Medicine": 3.0, "Lancet Digital Health": 3.0, "npj Digital Medicine": 2.5, "PubMed": 1.5, "medRxiv": 1.0, "bioRxiv": 1.0,
  "arXiv": 1.0, "Hugging Face": 1.0, "GitHub": 1.0, "STAT News": 1.5, "Fierce Healthcare": 1.0, "Google News · FDA AI": 0.5, "Google News · NMPA AI": 0.5,
  "PubMed · 重点期刊": 2.5, "Nature Biomedical Engineering": 2.5, "Nature Machine Intelligence": 2.5, "eClinicalMedicine": 2.0, "Cell Reports Medicine": 2.0,
  "Medical Image Analysis": 2.0, "Hugging Face Daily Papers": 1.5, "Google Research": 2.0, "Google DeepMind": 2.5, "Microsoft Research": 2.0, "OpenAI": 2.0,
  "NVIDIA Blog": 1.0, "Google News · AI 公司动态": 1.0, "FDA 新闻稿": 2.5, "FDA AI 器械清单": 3.0, "EMA": 2.0, "MHRA": 2.0, "WHO": 2.0,
  "Google News · 中文医疗AI": 0.5, "Google News · 中文医疗AI融资": 0.5, "Google News · 行业媒体": 1.0, "MedCity News": 1.0,
  "Hacker News": 1.0, "ClinicalTrials.gov": 1.5, "Hugging Face 数据集": 1.0}

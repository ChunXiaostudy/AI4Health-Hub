SITE = dict(mode="health", title="AI4Health Hub · 医疗 AI 每日动态", wordmark="AI<b>4</b>Health", tagline="医疗 AI 研究与落地日报",
  url="https://chunxiaostudy.github.io/AI4Health-Hub/", repo="https://github.com/ChunXiaostudy/AI4Health-Hub",
  disclaimer="本站内容由程序自动抓取并经 LLM 摘要，仅供科研与信息参考，不构成任何医疗建议；预印本未经同行评审。")
MED = ["medical","clinical","patient","patients","health","healthcare","disease","diagnosis","radiology","pathology","biomedical","drug","protein","genomic","genomics","cancer","tumor","EHR","hospital","MRI","CT","x-ray","ECG","surgical","molecule","cell","medicine","clinician"]
SECTIONS = ["研究论文", "模型与开源", "临床落地与监管", "产业与融资", "观点与综述"]
SOURCES = [
 dict(name="arXiv", url="https://export.arxiv.org/api/query?search_query=(cat:cs.AI+OR+cat:cs.CV+OR+cat:cs.CL+OR+cat:cs.LG+OR+cat:eess.IV+OR+cat:q-bio.QM)+AND+(abs:medical+OR+abs:clinical+OR+abs:radiology+OR+abs:pathology+OR+abs:biomedical+OR+abs:patient)&sortBy=submittedDate&sortOrder=descending&max_results=60", keywords=MED, limit=60, evidence="预印本"),
 dict(name="medRxiv", url="https://connect.medrxiv.org/medrxiv_xml.php?subject=Health_Informatics", evidence="预印本"),
 dict(name="bioRxiv", url="https://connect.biorxiv.org/biorxiv_xml.php?subject=bioinformatics", keywords=["deep learning","machine learning","language model","neural","AI","foundation model","transformer"], evidence="预印本"),
 dict(name="PubMed", kind="pubmed", term='("artificial intelligence"[tiab] OR "deep learning"[tiab] OR "large language model"[tiab]) AND hasabstract', evidence="同行评审"),
 dict(name="Nature Medicine", url="https://www.nature.com/nm.rss", evidence="同行评审"),
 dict(name="npj Digital Medicine", url="https://www.nature.com/npjdigitalmed.rss", evidence="同行评审"),
 dict(name="Lancet Digital Health", url="https://www.thelancet.com/rssfeed/landig_current.xml", evidence="同行评审"),
 dict(name="NEJM AI", url="https://ai.nejm.org/action/showFeed?type=etoc&feed=rss&jc=ai", evidence="同行评审"),
 dict(name="Hugging Face", kind="hf", url="https://huggingface.co/api/models?search=medical&sort=trendingScore&limit=15"),
 dict(name="GitHub", kind="github", q="medical OR clinical OR healthcare OR biomedical", days=14, limit=15),
 dict(name="STAT News", url="https://www.statnews.com/feed/", keywords=["AI","artificial intelligence","algorithm","machine learning","LLM","digital health","FDA"]),
 dict(name="Fierce Healthcare", url="https://www.fiercehealthcare.com/rss/xml", keywords=["AI","artificial intelligence","algorithm","machine learning","LLM","digital health"]),
 dict(name="Google News · FDA AI", url="https://news.google.com/rss/search?q=FDA+(AI+OR+%22artificial+intelligence%22)+medical+device+when:3d&hl=en-US&gl=US&ceid=US:en", limit=15),
 dict(name="Google News · NMPA AI", url="https://news.google.com/rss/search?q=%E5%9B%BD%E5%AE%B6%E8%8D%AF%E7%9B%91%E5%B1%80+%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+%E5%8C%BB%E7%96%97%E5%99%A8%E6%A2%B0+when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", limit=10),
]
PROMPT = """你是严谨克制的医疗 AI 情报编辑。根据给定条目（仅依据其中提供的信息）生成中文结构化摘要。
规则：只陈述来源中的事实，绝不编造数字、结论或机构；不夸大；绝不给出任何医疗建议或诊疗推荐；预印本须视为未经同行评审。
若条目与 AI/算法在医疗健康、生物医学中的应用无关，keep=false。"""
FIELDS = """keep(bool), zh_title(中文标题,≤40字), summary(一句话摘要,≤80字), key_points(3条要点数组,每条≤30字), section(从可选section选一), type(论文/模型/监管/融资/观点 之一), tags(细分领域标签数组,如 医学影像/病理/临床NLP/药物发现/基因组学/医学大模型/数据集/基准, ≤3个), evidence(预印本/同行评审/已获批/新闻 之一, 参考 evidence_hint；FDA/NMPA 批准类为 已获批), score(1-10 重要性整数)"""

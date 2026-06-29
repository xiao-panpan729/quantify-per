# -*- coding: utf-8 -*-
"""
信源日报聚合层 — 追加"公众号观点聚合"+ "宏观共振判断"到 sources.md。

读取现有 sources.md、今日公众号文章（deep_signals.json 结构化信号优先）、
宏观/流动性/情绪快照，调用 LLM 生成作者观点 vs 宏观数据的共振/分歧分析。

Usage:
  python gen_daily_brief.py                    # 默认今日，追加聚合区块
  python gen_daily_brief.py --date 20260611    # 指定日期

依赖: gen_source_summary.py --ai 先生成 sources.md, signal_deep_reader.py 生成 deep_signals.json
"""
import sys, json, os, re
from pathlib import Path
from datetime import datetime, timedelta
# 规则主题分类器（代替LLM分类）
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from tools.topic_classifier import classify_articles, format_groups_summary
from tools.variable_taxonomy import lookup_variable, add_candidates

# ─── 只处理"宏观/观点"类信源，排除"情绪热点"类（如盘前纪要/一思一记等） ───
# 同步于 _fetch_articles.py 的 ACCOUNT_CATEGORIES
MACRO_ACCOUNTS = {
    '表舅是养基大户', 'laoduo', '海里的小龙龙', '亨特研究笔记',
    '卓哥投研笔记', '猫笔刀', '滚雪球的猫菲特闲唠嗑', '滚雪球的猫菲特',
}

PROJECT_ROOT = Path(__file__).parent.resolve()
SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking"
WECHAT_DIR = PROJECT_ROOT / "wechat_articles"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "sources"
DEEP_SIGNALS_DIR = SIGNALS_DIR / "_signals" / "daily_signals"
SOURCES_TRACKING_DIR = SIGNALS_DIR / "_sources"
PROCESSED_FILE = SOURCES_TRACKING_DIR / "processed_articles.json"

# ─── US ETF 类别 → A股概念板块映射（语义映射，非数据驱动） ───
US_SECTOR_TO_A_SHARE = {
    "Healthcare & Biotech": ["医药", "创新药", "CXO", "生物医药", "医疗器械", "医疗"],
    "Semiconductors (VanEck)": ["半导体", "芯片", "集成电路"],
    "Broad Market": ["大盘"],
    "Consumer & Retail": ["消费", "零售"],
    "Energy & Materials": ["能源", "有色", "钢铁", "煤炭", "材料"],
    "Defense & Industrial": ["军工", "工业", "制造"],
    "Finance & Fintech": ["金融", "银行", "券商", "保险"],
    "Real Estate & Infrastructure": ["地产", "基建"],
    "China & Emerging Markets": ["中概", "港股", "恒生"],
}

# US 行业方向 → 中文名称映射（海外映射渲染用）
_US_SECTOR_CN = {
    "Healthcare & Biotech": "医疗健康",
    "Semiconductors (VanEck)": "半导体",
    "Broad Market": "大盘",
    "Consumer & Retail": "消费零售",
    "Energy & Materials": "能源材料",
    "Defense & Industrial": "工业国防",
    "Finance & Fintech": "金融",
    "Real Estate & Infrastructure": "地产基建",
    "China & Emerging Markets": "中概新兴市场",
    # 以下是 ETF category 映射（us_sector_momentum.json 中的 category）
    "Healthcare": "医疗健康",
    "Semiconductors": "半导体",
    "Technology": "科技",
    "Consumer": "消费",
    "Energy": "能源",
    "Financial": "金融",
    "Industrial": "工业",
    "Utilities": "公用事业",
    "Real Estate": "地产",
    "Materials": "材料",
    "Broad Market": "大盘",
}

# 美股明星股分类中文映射（对应 star_stocks.py US_STAR_STOCKS 的 key）
_US_STOCK_CATEGORY_CN = {
    "Magnificent 7": "科技七巨头",
    "Semiconductor Chain": "半导体",
    "AI & Software": "AI软件",
    "Finance": "金融",
    "Healthcare": "医疗健康",
    "Energy": "能源",
    "Consumer & Retail": "消费零售",
    "Industrial & Defense": "工业国防",
    "Crypto & Alts": "加密资产",
}

# 概念链名称 → A股板块关键词映射（与 US_SECTOR_TO_A_SHARE 互补，用于明星股映射）
CONCEPT_CHAIN_TO_A_SHARE = {
    "半导体产业链": ["半导体", "芯片", "集成电路"],
    "AI算力链": ["AI算力", "光模块", "服务器", "PCB", "液冷"],
    "AI应用层": ["AI应用", "软件", "SaaS"],
    "英伟达链": ["英伟达链", "光模块", "PCB", "铜缆连接", "服务器"],
    "苹果链": ["苹果链", "消费电子", "果链"],
    "特斯拉/EV链": ["特斯拉", "新能源汽车", "锂电"],
    "生物科技": ["生物医药", "创新药", "CXO"],
    "清洁能源": ["光伏", "风电", "储能"],
    "金矿/贵金属": ["黄金", "贵金属"],
    "国防军工": ["军工", "航天"],
    "中国互联网": ["中概", "港股", "互联网"],
}

# ─── 聚合新闻关键词 → 话题映射（华尔街见闻/东财头条聚类用） ───
HEADLINE_TOPIC_PATTERNS = {
    "韩国|KOSPI|三星电子|SK海力士|首尔综指|韩元|韩国.*跌|韩国.*涨|韩国.*暴跌": "🇰🇷 韩国市场",
    "美联储|加息|降息|PCE|美债|沃什|点阵图|FedWatch|贝森特": "🇺🇸 美联储/美国宏观",
    "美光|HBM|存储|DRAM|NAND|内存|闪存|海力士": "💾 存储芯片",
    "台积电|TSMC|晶圆|代工|3nm|7nm|涨价.*芯片": "🔬 台积电/半导体代工",
    "英伟达|NVIDIA|GPU|PCB|Rubin|H100|B200|AI芯片": "🤖 英伟达/AI硬件",
    "伊朗|霍尔木兹|原油|油价|石油|美伊": "🛢️ 地缘/原油",
    "日本央行|BOJ|日元|套息|中性利率|企业服务价格": "🇯🇵 日本宏观",
    "人民币|中间价|离岸|汇率|CNY|CNH": "🇨🇳 人民币汇率",
    "A股|上证|创业板|科创板|深成指|IPO|北向": "🇨🇳 A股市场",
    "AI|人工智能|大模型|OpenAI|Claude|vibecoding|agent|多模态": "🤖 AI产业",
    "黄金|白银|贵金属|金价": "🥇 贵金属",
    "港股|恒生|恒科|中概|H股|恒指|恒生科技": "🇭🇰 港股/中概",
    "地产|房地产|楼市|住房|按揭|房企": "🏠 房地产",
    "创新药|CXO|生物医药|药明|医药|和誉|礼来": "💊 医药/创新药",
    "新能源|光伏|锂电|电车|比亚迪|宁德|太阳能": "🔋 新能源",
    "通胀|CPI|PPI|物价|通缩": "📊 通胀数据",
    "比特币|加密货币|区块链|eth|BTC|加密": "₿ 加密货币",
    "财报|业绩|营收|利润|指引|资本开支|capex": "📋 业绩/财报季",
    "关税|制裁|贸易战|出口管制|封锁": "⚖️ 贸易/制裁",
    "PCB|胜宏|沪电|深南": "🔧 PCB产业链",
}

# 模块加载时预编译，避免每次 compute_headline_heat 都重新编译
_HEADLINE_REGEX = [(re.compile(p, re.IGNORECASE), t) for p, t in HEADLINE_TOPIC_PATTERNS.items()]

# ─── 外资机构关键词（聚合消息→foreign_views提取用） ───
FOREIGN_BANK_PATTERNS = [
    (r"高盛|Goldman\s*Sachs", "高盛"),
    (r"摩根士丹利|大摩|Morgan\s*Stanley", "摩根士丹利"),
    (r"摩根大通|小摩|JP\s*Morgan|JPMorgan", "摩根大通"),
    ("瑞银|UBS", "瑞银"),
    ("花旗|Citi", "花旗"),
    (r"美林|Merrill\s*Lynch", "美林"),
    (r"德银|Deutsche\s*Bank", "德银"),
    ("巴克莱|Barclays", "巴克莱"),
    ("野村|Nomura", "野村"),
    (r"瑞信|Credit\s*Suisse", "瑞信"),
]

FOREIGN_SECTOR_PATTERNS = [
    ("医药|创新药|CXO|药明|生物医药|礼来|辉瑞|医疗器械|功率半导", "医药/创新药"),
    ("半导体|芯片|AI|算力|GPU|HBM|存储|台积电|英伟达|中芯|设备|材料|晶圆", "半导体/AI"),
    ("消费|白酒|茅台|零售|旅游|免税|宠物|食品|饮料", "消费"),
    ("新能源|光伏|锂电|电车|比亚迪|宁德|储能|风电", "新能源/锂电"),
    ("金融|银行|保险|券商|证券|财富管理", "金融/银行"),
    ("地产|房地产|楼市|物业|基建|建材", "地产链"),
    ("能源|石油|原油|天然气|煤炭|电力", "能源/大宗"),
    ("汽车|整车|汽配|智能驾驶|华为|小米汽车|出行", "汽车"),
    ("互联网|电商|腾讯|阿里|美团|拼多多|字节|抖音|快手|美团", "互联网/平台"),
    ("军工|航天|船舶|装备", "军工"),
    ("消费电子|手机|PC|MR|VR|AR|折叠屏", "消费电子"),
]

NARRATIVES_DIR = PROJECT_ROOT / "narratives"


def load_processed_articles() -> set:
    """加载已处理文章的文件路径集合"""
    if not PROCESSED_FILE.exists():
        return set()
    try:
        data = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
        return set(data.get("processed", {}).keys())
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"  ⚠ 读取已处理文章列表失败: {e}")
        return set()

def save_processed_articles(articles: list):
    """标记文章为已处理，追加到 processed_articles.json"""
    proc = {}
    if PROCESSED_FILE.exists():
        try:
            proc = json.loads(PROCESSED_FILE.read_text(encoding="utf-8")).get("processed", {})
        except (json.JSONDecodeError, FileNotFoundError):
            proc = {}
    today = datetime.now().strftime("%Y-%m-%d")
    for art in articles:
        fpath = art.get("file", "")
        if fpath and fpath not in proc:
            proc[fpath] = {
                "processed_date": today,
                "title": art.get("title", ""),
                "author": art.get("author", ""),
            }
    SOURCES_TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_FILE.write_text(
        json.dumps({"version": 2, "processed": proc}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─── 宏观 JSON 路径 ───
MACRO_PATHS = {
    "sentiment": SIGNALS_DIR / "_macro" / "sentiment_shock.json",
    "liquidity": SIGNALS_DIR / "_macro" / "liquidity_monitor.json",
    "japan": SIGNALS_DIR / "_macro" / "japan_macro.json",
    "us_macro": SIGNALS_DIR / "_macro" / "us_macro_sensitivity.json",
    "sector_momentum": SIGNALS_DIR / "_macro" / "us_sector_momentum.json",
}


def load_json(path):
    """安全读取 JSON，失败返回空 dict"""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        pass


def _get_covered_from_previous_reports(today_str: str) -> set:
    """
    扫描之前日报的"本期覆盖文章"表格，建立已覆盖文章的 (author, title_prefix) 集合。
    用于 get_unread_articles() 的二次去重，防止同一篇文章出现在多日报中。
    """
    covered = set()
    today = datetime.strptime(today_str, "%Y%m%d")

    # 只看昨天和前天的报告
    for i in range(1, 3):
        date = today - timedelta(days=i)
        report_path = OUTPUT_DIR / f"{date.strftime('%Y%m%d')}_sources.md"
        if not report_path.exists():
            continue

        content = report_path.read_text(encoding="utf-8", errors="replace")
        in_table = False
        for line in content.split("\n"):
            if "本期覆盖文章" in line:
                in_table = True
                continue
            if in_table:
                if line.startswith("| ---"):
                    continue
                if line.startswith("|") and line.count("|") >= 3:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4:
                        author = parts[1]
                        title = parts[3] if len(parts) >= 4 else ""
                        if author and title and author not in ("作者", "------"):
                            covered.add((author, title[:40]))
                elif not line.startswith("|"):
                    break
    return covered


def get_unread_articles(today_str: str, force: bool = False) -> list:
    """
    取 wechat_articles/ 下未处理过的文章。
    去重依据：
      1. processed_articles.json（文件名精确匹配）
      2. 前日报的"本期覆盖文章"表格（作者+标题前缀匹配）
    force=True 时忽略已读标记，全量返回。
    """
    if not WECHAT_DIR.exists():
        return []

    processed = load_processed_articles() if not force else set()
    today_date = datetime.strptime(today_str, "%Y%m%d")

    # ── 二次去重：扫描前日报表的"本期覆盖文章" ──
    prev_covered = set() if force else _get_covered_from_previous_reports(today_str)

    # ── 收集文章 ──
    all_files = []
    for author_dir in sorted(WECHAT_DIR.iterdir()):
        if not author_dir.is_dir() or author_dir.name.startswith("_"):
            continue
        if author_dir.name not in MACRO_ACCOUNTS:
            continue
        for f in author_dir.glob("*.txt"):
            all_files.append((f, author_dir.name))

    all_files.sort(key=lambda x: x[0].name, reverse=True)

    result = []
    for f, author in all_files:
        # 只看最近2天（昨天+今天）
        fname = f.name
        if len(fname) >= 8:
            try:
                f_date = datetime.strptime(fname[:8], "%Y%m%d")
                if f_date < today_date - timedelta(days=2):
                    continue
            except ValueError:
                pass

        # 去重1：文件名精确匹配 processed_articles.json
        rel_path = str(f.relative_to(PROJECT_ROOT))
        if rel_path in processed:
            continue

        content = f.read_text(encoding="utf-8", errors="replace")
        title = ""
        for line in content.split("\n")[:3]:
            if line.startswith("标题:"):
                title = line.replace("标题:", "").strip()
                break
        if not title:
            title = f.stem

        result.append({
            "author": author,
            "title": title,
            "file": rel_path,
            "content": content,
        })

    # 去重2：前日报已覆盖的按 (作者+标题前缀) 过滤
    if prev_covered:
        before = len(result)
        result = [a for a in result if (a["author"], a["title"][:40]) not in prev_covered]
        if len(result) < before:
            print(f"  前报道去重: 过滤 {before - len(result)} 篇已覆盖")

    return result


def match_deep_signals(articles: list, deep_data: dict) -> list:
    """将 deep_signals 的结构化信号匹配到文章列表"""
    if not deep_data or "articles" not in deep_data:
        return articles

    signal_map = {}
    for da in deep_data["articles"]:
        author = da.get("article_source", "")
        title = da.get("article_title", "")
        key = f"{author}|{title}"
        signal_map[key] = da.get("signals", [])

    for art in articles:
        key = f"{art['author']}|{art['title']}"
        if key in signal_map:
            art["deep_signals"] = signal_map[key]
        else:
            # 尝试模糊匹配（取标题前15字）
            for k, v in signal_map.items():
                if art["author"] in k and art["title"][:15] in k:
                    art["deep_signals"] = v
                    break
    return articles


# ─── US 股票中英文名映射 ───
US_NAMES_PATH = PROJECT_ROOT / "tools" / "us_market" / "us_stock_names.json"

def _load_us_names() -> dict:
    if not US_NAMES_PATH.exists():
        return {}
    try:
        return json.loads(US_NAMES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

_US_TICKER_INFO = None

def _get_us_ticker_info() -> dict:
    global _US_TICKER_INFO
    if _US_TICKER_INFO is None:
        _US_TICKER_INFO = {}
        data = _load_us_names()
        for _cat in ('stocks', 'etfs'):
            for _sym, _info in data.get(_cat, {}).items():
                _US_TICKER_INFO[_sym] = _info
    return _US_TICKER_INFO


def extract_us_stock_mentions(articles: list) -> list:
    """
    扫描文章内容，提取公众号提到的 US 股票/ETF。
    匹配模式：中文名(TICKER)、TICKER+涨跌百分比上下文。
    返回 [{symbol, cn_name, en_name, author, context}]。
    """
    if not _get_us_ticker_info():
        return []

    mentions = []
    seen = set()
    ticker_re = re.compile(r'\b([A-Z]{1,5}(?:\.[A-Z])?)\b')
    pct_re = re.compile(r'[涨跌](\d+\.?\d*)%?|(\d+\.?\d*)%[的]?[涨跌幅]')

    for art in articles:
        content = art.get("content", "")
        if not content:
            continue
        author = art.get("author", "")
        title = art.get("title", "")

        # 策略A: 中文名(TICKER) 或 中文名(NYSE:TICKER)
        for m in re.finditer(
            r'[一-鿿（）]+[（(]\s*(?:NYSE|NASDAQ|US)?:?\s*([A-Z]{1,5}(?:\.[A-Z])?)\s*[）)]',
            content
        ):
            sym = m.group(1)
            if sym not in _get_us_ticker_info():
                continue
            key = f"{sym}|{author}"
            if key in seen:
                continue
            seen.add(key)
            info = _get_us_ticker_info()[sym]
            ctx = content[max(0, m.start()-15):m.end()+30].replace('\n', ' ')
            mentions.append({
                "symbol": sym, "cn_name": info.get("cn", sym),
                "en_name": info.get("en", sym),
                "author": author, "title": title,
                "context": ctx.strip(),
            })

        # 策略B: TICKER + 附近有涨跌百分比
        for m in ticker_re.finditer(content):
            sym = m.group(1)
            if sym not in _get_us_ticker_info() or f"{sym}|{author}" in seen:
                continue
            start = max(0, m.start()-40)
            end = min(len(content), m.end()+40)
            ctx = content[start:end]
            if pct_re.search(ctx) or '涨幅' in ctx or '涨' in ctx or '跌' in ctx:
                seen.add(f"{sym}|{author}")
                info = _get_us_ticker_info()[sym]
                mentions.append({
                    "symbol": sym, "cn_name": info.get("cn", sym),
                    "en_name": info.get("en", sym),
                    "author": author, "title": title,
                    "context": ctx.replace('\n', ' ').strip(),
                })

    return mentions


def format_us_mentions(mentions: list) -> str:
    """将 US 股票提及渲染为 markdown 区块"""
    if not mentions:
        return ""
    lines = ["\n---\n", "## 🇺🇸 文章提及美股\n"]
    lines.append("| 代码 | 名称 | 来源 | 上下文 |")
    lines.append("|------|------|------|--------|")
    for m in mentions:
        name = f"{m['cn_name']}({m['en_name']})" if m['cn_name'] != m['en_name'] else m['en_name']
        lines.append(f"| {m['symbol']} | {name} | {m['author']} | {m['context'][:60]} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_macro_snapshot() -> str:
    """汇总宏观快照文本"""
    parts = []
    sent = load_json(MACRO_PATHS["sentiment"])
    if sent:
        level = sent.get("impact_level", "?")
        net = sent.get("net_impact", 0)
        parts.append(f"消息面冲击: {level} (净影响 {net})")

    liq = load_json(MACRO_PATHS["liquidity"])
    if liq:
        pressure = liq.get("pressure", "?")
        regime = liq.get("regime", "?")
        parts.append(f"全球流动性: {regime} (压力 {pressure})")

    jp = load_json(MACRO_PATHS["japan"])
    if jp:
        cp = jp.get("carry_pressure", "?")
        cr = jp.get("carry_regime", "?")
        yen = jp.get("yen_signal", "?")
        parts.append(f"日本套息: {cr} (压力 {cp}, 日元 {yen})")

    us = load_json(MACRO_PATHS["us_macro"])
    if us:
        env = us.get("environment", {})
        env_name = env.get("environment", "?") if isinstance(env, dict) else "?"
        parts.append(f"US宏观: {env_name}")

    return "\n".join(parts) if parts else "宏观数据暂缺"


def _load_cross_mapping() -> dict:
    """加载 US→A股 cross_mapping 数据，返回 {us_etf: [(cn_sector, correlation), ...]}"""
    path = SIGNALS_DIR / "_macro" / "us_cn_mapping.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mappings = data.get("all_mappings", data.get("top_mappings", []))
        result = {}
        for m in mappings:
            etf = m.get("us_etf", "")
            sector = m.get("cn_sector", "")
            corr = m.get("correlation", 0)
            if etf and sector:
                result.setdefault(etf, []).append((sector, corr))
        return result
    except (json.JSONDecodeError, FileNotFoundError, KeyError):
        return {}


def _load_concept_chains() -> dict:
    """加载概念链并构建 reverse index: 美股→概念链 查找表"""
    path = PROJECT_ROOT / "tools" / "us_market" / "concept_chains.json"
    if not path.exists():
        return {"stock_to_chains": {}, "chain_to_etfs": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        concepts = data.get("concepts", {})
        stock_to_chains = {}
        chain_to_etfs = {}
        for name, chain in concepts.items():
            chain_to_etfs[name] = chain.get("source_etfs", [])
            for sym in chain.get("stocks", []):
                stock_to_chains.setdefault(sym.upper(), []).append(name)
        return {"stock_to_chains": stock_to_chains, "chain_to_etfs": chain_to_etfs}
    except Exception:
        return {"stock_to_chains": {}, "chain_to_etfs": {}}


def build_overseas_mapping(covered_topics: set) -> list:
    """
    增强型海外映射引擎 — 聚合 ETF + 明星股 + 概念链 + cross_mapping.

    流程:
      1. 加载 us_sector_momentum.json (52 ETFs)、us_star_momentum.json (64 stocks)、
         us_concept_momentum.json、us_cn_mapping.json、概念链索引
      2. ETF → A股方向: 先查 cross_mapping (correlation)，回退 US_SECTOR_TO_A_SHARE
      3. 明星股 → 概念链 → A股方向: 通过 stock_to_chains 找链，回退 CONCEPT_CHAIN_TO_A_SHARE
      4. 按 A股方向聚合评分 (ETF贡献 capped 10 + 明星股贡献 capped 10 + cross_mapping bonus)
      5. 筛选评分 > 1.0，最多返回 8 个方向
      6. 检查公众号覆盖情况

    Returns: list of dicts:
        {
            "a_share_direction": str,
            "composite_score": float,
            "us_etf_signals": list,
            "us_star_signals": list,
            "concept_chains": list[str],
            "covered": bool,
        }
    """
    etf_data = load_json(SIGNALS_DIR / "_macro" / "us_sector_momentum.json")
    star_data = load_json(SIGNALS_DIR / "_macro" / "us_star_momentum.json")
    concept_data = load_json(SIGNALS_DIR / "_macro" / "us_concept_momentum.json")
    cross_map = _load_cross_mapping()
    chain_index = _load_concept_chains()

    etfs = etf_data.get("etfs", []) if etf_data else []
    stars = star_data.get("stocks", []) if star_data else []
    chains = concept_data.get("chains", []) if concept_data else []

    if not etfs and not stars:
        return []

    # Build chain avg_x1 lookup
    chain_avg_x1 = {c.get("chain", ""): c.get("avg_x1", 0) for c in chains}

    direction_signals = {}

    def _ensure_dir(dir_name):
        if dir_name not in direction_signals:
            direction_signals[dir_name] = {
                "a_share_direction": dir_name,
                "us_etf_signals": [],
                "us_star_signals": [],
                "concept_chains": [],
                "composite_score": 0.0,
                "covered": False,
            }
        return direction_signals[dir_name]

    # 2a. Process ETFs → A-share directions
    for e in etfs:
        sym = e.get("symbol", "")
        cat = e.get("category", "")

        # Find A-share directions via cross_mapping
        mapped_directions = set()
        if sym in cross_map:
            for sector, corr in cross_map[sym][:3]:
                if abs(corr) >= 0.25:
                    for dir_name, keywords in US_SECTOR_TO_A_SHARE.items():
                        if any(kw in sector for kw in keywords):
                            mapped_directions.add(dir_name)

        # Fallback: static category mapping
        if not mapped_directions and cat in US_SECTOR_TO_A_SHARE:
            mapped_directions.add(cat)

        for d in mapped_directions:
            entry = _ensure_dir(d)
            # Avoid duplicate ETF
            if not any(es["symbol"] == sym for es in entry["us_etf_signals"]):
                # 查中文名
                etf_info = _get_us_ticker_info().get(sym, {})
                cn_name = etf_info.get("cn", e.get("name", ""))
                entry["us_etf_signals"].append({
                    "symbol": sym,
                    "name": e.get("name", ""),
                    "cn_name": cn_name,
                    "category": _US_SECTOR_CN.get(e.get("category", ""), e.get("category", "")),
                    "x1": e.get("x1", 0),
                    "daily_chg": e.get("daily_chg", 0),
                    "week_chg": e.get("week_chg", 0),
                    "x1_trend": e.get("x1_trend", ""),
                })

    # 2b. Process star stocks → concept chains → A-share directions
    for s in stars:
        sym = s.get("symbol", "")

        # Find chains this stock belongs to
        stock_chains = chain_index["stock_to_chains"].get(sym.upper(), [])

        for chain_name in stock_chains:
            # Map chain name → A-share direction(s)
            dir_name = chain_name
            if dir_name not in US_SECTOR_TO_A_SHARE and dir_name not in CONCEPT_CHAIN_TO_A_SHARE:
                # Try partial match
                for known_dir in CONCEPT_CHAIN_TO_A_SHARE:
                    if known_dir in dir_name or dir_name in known_dir:
                        dir_name = known_dir
                        break
                else:
                    continue  # skip chains we can't map

            entry = _ensure_dir(dir_name)
            if not any(ss["symbol"] == sym for ss in entry["us_star_signals"]):
                # 查中文名+行业分类
                stock_info = _get_us_ticker_info().get(sym, {})
                cn_name = stock_info.get("cn", s.get("name", ""))
                stock_category = _US_STOCK_CATEGORY_CN.get(s.get("category", ""), s.get("category", ""))
                entry["us_star_signals"].append({
                    "symbol": sym,
                    "name": s.get("name", ""),
                    "cn_name": cn_name,
                    "category": stock_category,
                    "x1": s.get("x1", 0),
                    "daily_chg": s.get("daily_chg", 0),
                    "week_chg": s.get("week_chg", 0),
                    "x1_trend": s.get("x1_trend", ""),
                })
                if chain_name not in entry["concept_chains"]:
                    entry["concept_chains"].append(chain_name)

    # 2c. Add concept chain momentum data
    for c in chains:
        chain_name = c.get("chain", "")
        if chain_name in direction_signals:
            direction_signals[chain_name]["concept_avg_x1"] = c.get("avg_x1", 0)

    # 3. Compute composite scores
    for name, entry in direction_signals.items():
        etf_score = sum(abs(s.get("x1", 0) or 0) for s in entry["us_etf_signals"])
        etf_score = min(etf_score, 10)
        # Bonus for strong daily movers
        strong_movers = sum(1 for s in entry["us_etf_signals"]
                            if abs(s.get("daily_chg", 0) or 0) > 2)
        etf_score += strong_movers * 1.5

        star_score = sum(abs(s.get("x1", 0) or 0) for s in entry["us_star_signals"])
        star_score = min(star_score, 10)

        chain_score = entry.get("concept_avg_x1", 0) or 0

        entry["composite_score"] = round(etf_score + star_score + max(chain_score, 0), 1)

        # Check coverage against Chinese articles
        dir_keywords = (
            US_SECTOR_TO_A_SHARE.get(name, [])
            + CONCEPT_CHAIN_TO_A_SHARE.get(name, [])
        )
        entry["covered"] = (
            any(any(kw in topic for kw in dir_keywords) for topic in covered_topics)
            if covered_topics and dir_keywords else False
        )

    # 4. Collect ATH (创历史新高) data across all ETFs and stocks
    ath_etfs = []
    for e in etfs:
        if e.get("ath"):
            etf_info = _get_us_ticker_info().get(e["symbol"], {})
            cn_cat = _US_SECTOR_CN.get(e.get("category", ""), e.get("category", ""))
            ath_etfs.append({
                "symbol": e["symbol"],
                "name": e.get("name", ""),
                "cn_name": etf_info.get("cn", ""),
                "category": cn_cat,
                "x1": e.get("x1", 0),
                "ath_distance": e.get("ath_distance", 0),
                "week_chg": e.get("week_chg", 0),
                "month_chg": e.get("month_chg", 0),
            })
    ath_stocks = []
    for s in stars:
        if s.get("ath"):
            cn_cat = _US_STOCK_CATEGORY_CN.get(s.get("category", ""), s.get("category", ""))
            ath_stocks.append({
                "symbol": s["symbol"],
                "name": s.get("name", ""),
                "cn_name": s.get("cn_name", ""),
                "category": cn_cat,
                "sub_sector": s.get("sub_sector", ""),
                "sub_rank": s.get("sub_rank", 0),
                "x1": s.get("x1", 0),
                "ath_distance": s.get("ath_distance", 0),
            })
    ath_etfs.sort(key=lambda x: x["ath_distance"], reverse=True)
    ath_stocks.sort(key=lambda x: x["ath_distance"], reverse=True)

    # 5. Sort by composite score descending
    sorted_dirs = sorted(
        direction_signals.values(),
        key=lambda x: -x["composite_score"]
    )

    # 6. Filter: only show directions with meaningful score
    filtered = [d for d in sorted_dirs if d["composite_score"] > 1.0]

    result = filtered[:8]  # max 8 directions
    # Attach ATH data to the list (backward compatible: callers use len()/iteration unchanged)
    result.ath_data = {
        "ath_etfs": ath_etfs,
        "ath_stocks": ath_stocks,
    }
    return result


def load_headlines() -> list:
    """从 sentiment_shock.json 加载聚合新闻头条列表"""
    data = load_json(MACRO_PATHS["sentiment"])
    if not data:
        return []
    return data.get("_headlines", [])


def compute_headline_heat(headlines: list, max_clusters: int = 10) -> list:
    """
    按关键词聚类头条，返回热度排序的话题列表。
    每个元素: {topic, heat, samples: [str]}
    """
    if not headlines:
        return []

    titles = []
    for h in headlines:
        if isinstance(h, dict):
            t = h.get("title", "")
        elif isinstance(h, str):
            t = h
        else:
            continue
        if t:
            titles.append(t)

    heat = {}
    for regex, topic_name in _HEADLINE_REGEX:
        count = 0
        samples = []
        for t in titles:
            if regex.search(t):
                count += 1
                if len(samples) < 3:
                    samples.append(t[:80])
        if count >= 2:  # 最少2条才算有热度
            heat[topic_name] = {"count": count, "samples": samples}

    result = sorted(heat.items(), key=lambda x: -x[1]["count"])
    return [{"topic": k, "heat": v["count"], "samples": v["samples"]} for k, v in result[:max_clusters]]


def render_headline_heat_section(headline_heat: list, covered_topics: set = None) -> str:
    """
    生成聚合新闻热点区块（放在消息面要点和话题深度分析之间）。
    只展示公众号未覆盖的纯头条热点话题。
    """
    if not headline_heat:
        return ""

    # 过滤：去掉公众号已覆盖的话题
    if covered_topics:
        # 提取公众号话题中的纯文本关键词（去除非中文字符的短标记）
        covered_kws = set()
        for ct in covered_topics:
            # 取每个 topic 的实义词（2字以上中文片段）
            for part in re.findall(r'[一-鿿]{2,}', ct):
                covered_kws.add(part)

        filtered = []
        for h in headline_heat:
            # 提取头条话题中的中文实义词
            h_parts = re.findall(r'[一-鿿]{2,}', h["topic"])
            # 检查是否有任何实义词出现在公众号话题中
            is_covered = any(
                hp in ck or ck in hp
                for hp in h_parts
                for ck in covered_kws
            )
            if not is_covered:
                filtered.append(h)
    else:
        filtered = headline_heat

    if not filtered:
        return ""

    lines = ["\n---\n", "## 📊 聚合新闻热点\n"]
    lines.append("以下话题在华尔街见闻/东方财富等新闻源中高频讨论，公众号未充分覆盖：\n")
    lines.append("| 话题 | 提及频次 | 代表消息 |")
    lines.append("|------|---------|---------|")
    for h in filtered[:8]:
        sample = h["samples"][0][:60] + "…" if h["samples"] and len(h["samples"][0]) > 60 else (h["samples"][0] if h["samples"] else "")
        lines.append(f"| {h['topic']} | {h['heat']}条 | {sample} |")
    lines.append("")
    return "\n".join(lines)


def extract_prev_views(date_str: str) -> str:
    """从昨日 sources.md 提取作者观点作为历史对比"""
    try:
        prev_date = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=1)
    except ValueError:
        return ""
    # 尝试昨天，如果昨天没有再往前推
    for offset in range(1, 4):
        d = prev_date - timedelta(days=offset - 1)
        prev_path = OUTPUT_DIR / f"{d.strftime('%Y%m%d')}_sources.md"
        if not prev_path.exists():
            continue
        text = prev_path.read_text(encoding="utf-8")
        # 提取旧格式：## 📰 公众号观点聚合 区块
        section_m = re.search(r"## 📰 公众号观点聚合(.*?)(?=## |$)", text, re.DOTALL)
        views = []
        if section_m:
            section = section_m.group(1)
            for line in section.split("\n"):
                m = re.match(r'-\s[🔴🟢⚪]\s\*\*(.+?)\*\*:\s(.+)', line)
                if m:
                    views.append(f"  {m.group(1)}: {m.group(2).strip()}")
        else:
            # 提取新格式：## 📰 热点事件分类 区块
            # 使用明确的后继section名作为边界，避免 ### 子标题误匹配
            section_m = re.search(
                r"## 📰 热点事件分类(.*?)(?=## 🆕 今日增量信号|## 🔗 Dorian|## 🆚 宏观|$)",
                text, re.DOTALL
            )
            if section_m:
                section = section_m.group(1)
                # 从作者观点表中提取：| 🟢 作者 | ... 格式
                for line in section.split("\n"):
                    m = re.match(r'\|\s[🔴🟢⚪]\s(.+?)\s\|\s(偏多|偏空|中性)\s\|', line)
                    if m:
                        author = m.group(1).strip()
                        stance = m.group(2).strip()
                        # 找"核心观点"列
                        parts = line.split("|")
                        for i, p in enumerate(parts):
                            if p.strip() in ('偏多', '偏空', '中性'):
                                if i + 1 < len(parts):
                                    detail = parts[i + 1].strip()
                                    views.append(f"  {author} ({stance}): {detail}")
                                break
        if views:
            prev_date_str = d.strftime("%Y-%m-%d")
            return f"昨日（{prev_date_str}）作者观点回顾：\n" + "\n".join(views)
    return ""


def extract_foreign_views_from_headlines(headlines: list) -> list:
    """从聚合新闻头条中提取外资机构观点，按机构名匹配→板块分类→信息量判断。"""
    if not headlines:
        return []
    views = []
    seen = set()
    for h in headlines:
        title = h.get("title", "") if isinstance(h, dict) else str(h)
        if not title or not isinstance(title, str):
            continue
        title_clean = title.strip()[:200]
        if not title_clean or title_clean in seen:
            continue
        seen.add(title_clean)

        matched_banks = []
        for pattern, zh_name in FOREIGN_BANK_PATTERNS:
            if re.search(pattern, title_clean, re.IGNORECASE):
                if zh_name not in matched_banks:
                    matched_banks.append(zh_name)
        if not matched_banks:
            continue

        sector = "其他"
        for pattern, sector_name in FOREIGN_SECTOR_PATTERNS:
            if re.search(pattern, title_clean, re.IGNORECASE):
                sector = sector_name
                break

        # 信息量：≥35字且有完整观点表述算"详细"，否则"标题"
        quality = "详细" if len(title_clean) >= 35 else "标题"
        source = h.get("source", "") if isinstance(h, dict) else ""
        raw_time = h.get("time", "") if isinstance(h, dict) else ""
        time_str = str(raw_time)[:20] if raw_time else ""

        views.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "bank": "、".join(matched_banks),
            "sector": sector,
            "summary": title_clean[:120],
            "quality": quality,
            "source": source,
            "time": time_str,
        })
    views.sort(key=lambda v: (0 if v["quality"] == "详细" else 1))
    return views


def _parse_daily_snapshot(existing_text: str, date_str: str) -> list:
    """从已存在的每日快照 markdown 中解析已有的 views 条目"""
    existing = []
    current_sector = "其他"
    header_pattern = re.compile(r'^###\s+(.+)$')
    for line in existing_text.split("\n"):
        sec_m = header_pattern.match(line)
        if sec_m:
            current_sector = sec_m.group(1).strip()
            continue
        if not line.startswith("|"):
            continue
        if line.startswith("|---") or "机构" in line:
            continue
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if len(parts) >= 5:
            quality = "详细" if "详细" in parts[2] else "标题"
            existing.append({
                "date": date_str,
                "bank": parts[0],
                "sector": current_sector,
                "summary": parts[1],
                "quality": quality,
                "source": parts[3],
                "time": parts[4],
            })
    return existing


def save_foreign_views_snapshot(views: list, date_str: str):
    """保存外资观点快照到 foreign_views/daily/ 并增量更新 _index.md

    午后更新时自动合并（不覆盖），按 summary[:40] 去重。
    """
    if not views:
        return
    daily_dir = NARRATIVES_DIR / "foreign_views" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_file = daily_dir / f"{date_str}.md"

    # ── 午后更新：快照已存在 → 合并增量 ──
    is_merge = False
    if daily_file.exists():
        existing = _parse_daily_snapshot(daily_file.read_text(encoding="utf-8"), date_str)
        existing_keys = {e["summary"][:40] for e in existing}
        new_views = [v for v in views if v["summary"][:40] not in existing_keys]
        if not new_views:
            print(f"  📝 外资观点: 当日快照已存在且无新增，跳过")
            return
        print(f"  📝 外资观点午后更新: +{len(new_views)} 条（原有 {len(existing)} 条）")
        views = existing + new_views
        is_merge = True

    sectors = {}
    for v in views:
        sectors.setdefault(v["sector"], []).append(v)

    quality_icon = {"详细": "🟢", "标题": "🟡"}
    lines = [f"# 外资机构观点快照 — {date_str}\n"]
    lines.append("> 来源：聚合消息（华尔街见闻/东方财富/财联社/金十）自动提取\n")
    for sec, items in sorted(sectors.items(), key=lambda x: -len(x[1])):
        lines.append(f"\n### {sec}\n")
        lines.append("| 机构 | 观点摘要 | 质量 | 来源 | 时间 |")
        lines.append("|------|---------|:----:|------|:----:|")
        for v in items:
            qi = quality_icon.get(v["quality"], "⚪")
            lines.append(f"| {v['bank']} | {v['summary']} | {qi}{v['quality']} | {v['source']} | {v['time']} |")
    daily_file.write_text("\n".join(lines), encoding="utf-8")
    n_total = len(views)
    print(f"  📝 外资观点快照: {n_total} 条（{'合并' if is_merge else '新建'}）→ daily/{date_str}.md")

    # 增量更新 _index.md（带 emoji 前缀的板块标题匹配）
    INDEX_SECTOR_EMOJI = {
        "医药/创新药": "🔴", "消费": "🟡", "半导体/AI": "🟢",
        "新能源/锂电": "🟡", "金融/银行": "🟡", "地产链": "⚪",
    }
    index_file = NARRATIVES_DIR / "foreign_views" / "_index.md"
    if not index_file.exists():
        return
    content = index_file.read_text(encoding="utf-8")
    any_change = False
    for sec, items in sectors.items():
        emoji = INDEX_SECTOR_EMOJI.get(sec, "")
        if emoji:
            sec_header = f"### {emoji} {sec}"
        else:
            continue  # 不在 _index.md 板块列表中的跳过
        if sec_header not in content:
            continue
        sec_idx = content.index(sec_header)
        rest = content[sec_idx + len(sec_header):]
        next_sec = re.search(r'\n### ', rest)
        table_end = sec_idx + len(sec_header) + next_sec.start() if next_sec else len(content)
        existing_summaries = set(re.findall(r'\| [^|]+ \| [^|]+ \| ([^|]+) \|', content[sec_idx:table_end]))

        new_rows = []
        for v in items:
            dup = any(v["summary"][:40] in es for es in existing_summaries)
            if dup:
                continue
            quality_note = f"[{v['quality']}] " if v["quality"] == "标题" else ""
            new_rows.append(f"| {v['date']} | {v['bank']} | {quality_note}{v['summary']} | → | {v['source']} |")

        if not new_rows:
            continue
        sec_changed = False
        for sep in ['\n\n', '\n---\n']:
            if sep in content[sec_idx:table_end]:
                insert_point = content.index(sep, sec_idx)
                content = content[:insert_point] + "\n" + "\n".join(new_rows) + content[insert_point:]
                sec_changed = True
                any_change = True
                break
        if not sec_changed:
            content = content[:table_end] + "\n" + "\n".join(new_rows) + "\n" + content[table_end:]
            any_change = True

    if any_change:
        index_file.write_text(content, encoding="utf-8")
        print(f"  📋 _index.md: 新增 {sum(len(v) for v in sectors.values())} 条观点至 {len(sectors)} 个板块")


def build_llm_prompt(articles: list, macro_text: str, is_incremental: bool,
                     prev_article_titles: set = None, prev_views_text: str = "",
                     date_str: str = "", uncoupled_signals: list = None,
                     headline_heat: list = None, groups: dict = None) -> str:
    """
    构建 LLM prompt — 规则分组 + 轻量LLM。
    先用 topic_classifier 按主题分组，再把每组文章标题+关键段落喂给 LLM，
    让 LLM 只做"提取观点+识别标的"的轻量工作，不做分类。
    """
    # ── 1. 规则主题分类 ──
    if groups is None:
        groups = classify_articles(articles)
    if not groups:
        return "（今日无文章可分析）"

    # ── 2. 按重要性+作者覆盖数排序 ──
    rank = {"high": 0, "medium": 1, "low": 2}
    sorted_topics = sorted(
        groups.items(),
        key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
    )

    # ── 3. 构建 prompt：每组一段，含全文关键内容 ──
    blocks = [f"今日日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    blocks.append("")

    for topic, info in sorted_topics:
        authors_str = "、".join(sorted(info["authors"]))
        blocks.append(f"===== 【{topic}】— {len(info['articles'])}篇 — {len(info['authors'])}位作者: {authors_str} =====")
        for a in info["articles"]:
            # 取文章开头关键部分（~1500字）+ 结尾（~500字）
            full = a["content_full"] if "content_full" in a and a["content_full"] else a.get("content", "")
            # 跳过元数据行
            body = full
            for prefix in ["标题:", "时间:", "链接:", "=================================="]:
                if body.startswith(prefix):
                    nl = body.find("\n", 0)
                    if nl > 0:
                        body = body[nl+1:]
            # 压缩：去空行，保留前2000字+尾500字
            body_clean = re.sub(r"\n{3,}", "\n\n", body.strip())
            if len(body_clean) > 2500:
                snippet = body_clean[:2000] + "\n...（中略）...\n" + body_clean[-500:]
            else:
                snippet = body_clean
            blocks.append(f"\n【{a['author']}】《{a['title']}》")
            blocks.append(snippet)
        blocks.append("")

    blocks.append("\n===== 宏观/流动性快照 =====")
    blocks.append(macro_text if macro_text else "（数据暂缺）")

    # ── 海外量化信号（US→A 映射全量） ──
    if uncoupled_signals:
        blocks.append("\n===== 海外量化信号（US→A 映射）=====")
        sig_lines = []
        for s in uncoupled_signals:
            # New format: a_share_direction + us_etf_signals/us_star_signals
            if "a_share_direction" in s:
                dir_name = s["a_share_direction"]
                score = s.get("composite_score", 0)
                n_etf = len(s.get("us_etf_signals", []))
                n_star = len(s.get("us_star_signals", []))
                # Pick strongest ETF for detail
                etf_detail = ""
                if s["us_etf_signals"]:
                    best = max(s["us_etf_signals"], key=lambda x: abs(x.get("x1", 0) or 0))
                    etf_detail = f" ({best.get('symbol','')} x₁={best['x1']:.1f})"
                sig_lines.append(
                    f"- {dir_name} (评分{score:.1f}, {n_etf}ETF{n_star}星){etf_detail}"
                    f" → A股关注"
                )
            else:
                # Old format fallback (for safety)
                a_share = "、".join(s.get("a_share_sectors", []))
                sig_lines.append(
                    f"- {s.get('us_category', '?')} ({s.get('trigger_etf_name', '')} "
                    f"日涨跌={s.get('daily_chg', 0):+.1f}%) → {a_share}"
                )
        blocks.append("\n".join(sig_lines))
        blocks.append("\n注：以上为 US→A 股量化映射信号，可在话题分析中适当关注。")

    if prev_views_text:
        blocks.append(f"\n===== 历史观点对比 =====")
        blocks.append(prev_views_text)

    # ── 聚合新闻头条热度（话题发现源） ──
    if headline_heat:
        blocks.append("\n===== 聚合新闻头条热度 =====")
        for hh in headline_heat[:10]:
            blocks.append(f"- {hh['topic']}: {hh['heat']}条提及")
            for s in hh['samples']:
                blocks.append(f"  · {s}")
        blocks.append("\n注：以上为华尔街见闻/东方财富等新闻源高频话题，是今日市场聚焦。")
        blocks.append("公众号已覆盖的 → 引用作者交叉验证。公众号未覆盖的 → 也可列入消息面要点，标注新闻热度即可。")

    # ── 4. 给 LLM 的指令（消息面精炼 + 话题深度分析JSON，Dorian技巧嵌入到JSON字段中） ──
    blocks.append("""
===== 分析指令 =====
输出分两部分，严格按顺序：

=== 第一部分：消息面精炼 ===
用 ```msg ``` 包裹，格式为 6 条以内的 markdown 要点：

**① 🇮🇷 主题名 → 一句话驱动**
- 核心事实（具体数字/金额/百分比）
- 如有公众号覆盖：作者名"原话"+作者名"原话"
- 如公众号未覆盖但新闻热度高：标注"聚合新闻热"
- ⏰ 时间节点/关键日期

**② ...**

要求：
- 每条 3 行以内
- 必须有 emoji 国旗/分类标识
- 公众号覆盖的话题 → 引用作者原话交叉验证
- 聚合新闻高频但公众号未覆盖 → 标注热度+新闻事实（不要硬凑公众号观点）
- 不能只是转载新闻标题，要提炼"所以呢"

=== 第二部分：话题深度分析 JSON ===
输出一个 JSON 数组（用 ```json ``` 包裹），每个元素代表一个主题：
{
  "topic": "主题名",
  "importance": "high/medium/low",
  "importance_reason": "N位信源同时指向，当前市场绝对主线（说明理由，不要重复前面importance字段的"高/中/低"前缀）",
  "variable_type": "核心变量/结构性变量/null（真正的核心变量才标「核心变量」，次要但仍重要的标「结构性变量」）",
  "consensus_detail": "一句话共识/分歧判断（如：高共识，AI上游景气度最强）",
  "is_variable": true/false,
  "author_details": [
    {
      "author": "作者名（短名，如laoduo/猫笔刀/卓哥/表舅/中信建投，不要带emoji）",
      "key_point": "一句话核心观点（嵌入Dorian式的判断：是核心矛盾还是传导链？是对标历史还是情景概率？）",
      "narrative_change": "叙事变化（🆕 新叙事/⬆️ 加强/⬇️ 减弱/➡️ 维持/🟡 情绪过热信号/🔄 转向）",
      "stocks": ["股票或ETF代码"]
    }
  ]
}

要求：
- 作者名不要带emoji，不要带"偏多/偏空/中性"
- 叙事变化列体现Dorian技巧——是变盘信号？是历史对标？还是情景概率改变？
- variable_type 区分"核心变量"和"结构性变量"，宁缺毋滥
- 没有把握的字段留null
- 宁少勿滥，不重要的话题不写
""")

    return "\n".join(blocks)


def parse_llm_topics(llm_text: str) -> list:
    """从 LLM 输出中提取结构化主题列表（JSON 数组）"""
    if not llm_text:
        return []
    # 找 ```json [...] ``` 代码块
    m = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', llm_text)
    if m:
        try:
            topics = json.loads(m.group(1))
            if isinstance(topics, list):
                return topics
        except json.JSONDecodeError:
            pass
    # 找裸 [...]（无代码块包裹）
    m = re.search(r'(\[[\s\S]*?\])', llm_text)
    if m:
        try:
            topics = json.loads(m.group(1))
            if isinstance(topics, list):
                return topics
        except json.JSONDecodeError:
            pass
    return []


def parse_llm_json(raw: str) -> dict:
    """从 LLM 输出中提取 JSON"""
    # 尝试直接解析
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 尝试找 {...}
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {}


def _render_topic(topic: dict) -> list:
    """渲染单个话题 —— v2 demo 格式：覆盖→重要性→三列表(作者|核心观点|叙事变化)→共识判断→是否为变量"""
    lines = []
    imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}
    imp_label = {"high": "高", "medium": "中", "low": "低"}

    variable_type = topic.get("variable_type", "")
    topic_name = topic.get("topic", "未命名主题")
    title = f"{imp_icon.get(topic.get('importance', 'medium'), '⚪')} {topic_name}"
    if variable_type:
        title += f" — {variable_type}"
    lines.append(f"### {title}")
    lines.append("")

    # 覆盖：作者数 + 具体名单
    authors = topic.get("author_details", [])
    author_names = "、".join(a.get("author", "") for a in authors) if authors else "?"
    lines.append(f'<div class="meta-line"><strong>覆盖</strong>: {len(authors)}位作者 ({author_names})</div>')

    # 重要性判断（去重LLM可能在前缀重复的"高 —"）
    imp = topic.get("importance", "medium")
    reason = topic.get("importance_reason", "")
    if reason:
        reason = re.sub(r'^(高|中|低)\s*[—\-]\s*', '', reason).strip()
        lines.append(f'<div class="meta-line"><strong>重要性判断</strong>: {imp_label.get(imp, "")} — {reason}</div>')

    # 作者观点表（三列：作者 | 核心观点 | 叙事变化）
    if authors:
        lines.append("")
        lines.append("| 作者 | 核心观点 | 叙事变化 |")
        lines.append("|------|---------|---------|")
        for a in authors:
            name = a.get("author", "")
            key_point = a.get("key_point", "")
            narrative_change = a.get("narrative_change", "")
            lines.append(f"| {name} | {key_point} | {narrative_change} |")

    # 共识判断
    consensus = topic.get("consensus_detail", "")
    if consensus:
        lines.append("")
        lines.append(f'<div class="consensus-line"><strong>共识判断</strong>: {consensus}</div>')

    # 是否为变量（variable_type 存在即视为变量）
    vt = topic.get("variable_type", "")
    is_var = topic.get("is_variable", False) or bool(vt)
    if is_var and vt:
        lines.append(f"**是否为变量**: **是，{vt}**")

    lines.append("> 🔍 **联网验证**: 待 Claude Code 联网搜索后追加")
    lines.append("")
    return lines


ENHANCED_MAPPING_HEADER = "## 🪝 海外映射"

def render_enhanced_overseas_mapping(mapping_signals: list) -> str:
    """生成增强版海外映射区块 — ATH新高榜 + 综合评分表 + Top方向钻取"""
    if not mapping_signals:
        return ""
    lines = ["\n---\n", f"{ENHANCED_MAPPING_HEADER}\n"]
    lines.append(
        "以下将 US 市场板块异动 & 明星股动量通过概念链映射到 A 股方向，\n"
    )

    # ── ① 🏆 创历史新高（从附加的 ath_data 读取） ──
    ath_data = getattr(mapping_signals, "ath_data", None)
    if ath_data and (ath_data["ath_etfs"] or ath_data["ath_stocks"]):
        lines.append("### 🏆 创历史新高\n")
        if ath_data["ath_etfs"]:
            etf_parts = []
            for e in ath_data["ath_etfs"][:6]:
                cn = e.get("cn_name", "")
                name_part = f"{e['name']}({cn})" if cn else e["name"]
                etf_parts.append(f"{name_part} ({e['symbol']}) {e.get('category','')} x₁={e['x1']:.1f}")
            lines.append(f"**ETF新高**: {' | '.join(etf_parts)}")
            lines.append("")
        if ath_data["ath_stocks"]:
            stock_parts = []
            for s in ath_data["ath_stocks"][:8]:
                cn = s.get("cn_name", "")
                name_part = f"{cn}({s['name']})" if cn else s["name"]
                sub = s.get("sub_sector", "")
                sub_part = f" [{sub}]" if sub else ""
                stock_parts.append(f"{name_part} ({s['symbol']}){sub_part} x₁={s['x1']:.1f}")
            lines.append(f"**个股新高**: {' | '.join(stock_parts)}")
            lines.append("")
        lines.append("---\n")

    # ── ② 综合评分表 ──
    lines.append("### 📊 US→A 股映射综合评分\n")
    lines.append("| A股方向 | 评分 | US驱动 | 覆盖状态 |")
    lines.append("|---------|------|--------|----------|")
    for s in mapping_signals[:6]:
        dir_name = _US_SECTOR_CN.get(s["a_share_direction"], s["a_share_direction"])
        score = s["composite_score"]
        n_etf = len(s["us_etf_signals"])
        n_star = len(s["us_star_signals"])
        trigger_parts = []
        if n_etf > 0:
            trigger_parts.append(f"{n_etf}ETF")
        if n_star > 0:
            trigger_parts.append(f"{n_star}星")
        trigger_str = "+".join(trigger_parts) if trigger_parts else "-"
        covered_str = "✅ 已覆盖" if s.get("covered") else "⚠️ 未覆盖"
        lines.append(f"| {dir_name} | {score:.1f} | {trigger_str} | {covered_str} |")
    lines.append("")

    # ── Top 方向钻取 ──
    for s in mapping_signals[:4]:
        dir_name = _US_SECTOR_CN.get(s["a_share_direction"], s["a_share_direction"])
        lines.append(f"**{dir_name}** (评分 {s['composite_score']:.1f})")

        # US ETF signals
        if s["us_etf_signals"]:
            sorted_etfs = sorted(s["us_etf_signals"], key=lambda x: -abs(x.get("x1", 0) or 0))
            etf_detail = "  ".join(
                f"{e.get('cn_name', e['name'])} ({e['symbol']}) x₁={e['x1']:.1f} 日{e.get('daily_chg',0):+.1f}%"
                for e in sorted_etfs[:4]
            )
            lines.append(f"  · US ETF: {etf_detail}")

        # Star stock signals
        if s["us_star_signals"]:
            sorted_stars = sorted(s["us_star_signals"], key=lambda x: -abs(x.get("x1", 0) or 0))
            star_detail = "  ".join(
                f"{e.get('cn_name', e['symbol'])} ({e['symbol']}) [{e.get('category', '?')}] x₁={e['x1']:.1f}"
                for e in sorted_stars[:4]
            )
            lines.append(f"  · 明星股: {star_detail}")

        # Concept chains
        if s.get("concept_chains"):
            lines.append(f"  · 概念链: {'→'.join(s['concept_chains'][:3])}")

        # Coverage note
        if not s.get("covered"):
            lines.append(f"  · ⚠️ 公众号未覆盖此方向，存在预期差")

        lines.append("")

    lines.append("> 🔍 **外资观点**: 待 Claude Code 联网搜索后追加\n")
    return "\n".join(lines)



def generate_topic_analysis(articles: list, llm_text: str, groups: dict = None) -> str:
    """生成话题深度分析（不含Dorian独立章节——Dorian技巧已嵌入到话题块的叙事变化列中）"""
    lines = []

    topics = parse_llm_topics(llm_text)
    if topics:
        lines.extend(["\n---\n", "## 🧠 话题深度分析\n"])
        for t in topics:
            lines.extend(_render_topic(t))
    else:
        if groups is None:
            groups = classify_articles(articles)
        if groups:
            lines.extend(["\n---\n", "## 🧠 话题深度分析\n"])
            rank = {"high": 0, "medium": 1, "low": 2}
            sorted_topics = sorted(
                groups.items(),
                key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
            )
            for topic, info in sorted_topics:
                imp = info["importance"]
                imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(imp, "⚪")
                imp_label = {"high": "高重要性", "medium": "一般", "low": "低"}.get(imp, "")
                author_count = len(info["authors"])
                author_names = "、".join(sorted(info["authors"]))
                lines.append(f"### {imp_icon} {topic}")
                lines.append(f"> {imp_label} · {author_count}位作者提及: {author_names}")
                lines.append("")
                for a in info["articles"]:
                    lines.append(f"- **{a['author']}** 《{a['title'][:50]}》")
                if info["importance"] == "high" and author_count >= 3:
                    lines.append(f"> ⚡ **重点信号**: {author_count}位作者同题，为当前市场核心矛盾")
                lines.append("")
        else:
            lines.append("（暂无分类文章）\n")

    return "\n".join(lines) + "\n"


def format_article_table(articles: list) -> str:
    """生成简洁文章清单表格（放末尾），日期格式 MMDD，列名为 作者|日期|内容"""
    # 作者名缩写映射
    AUTHOR_SHORT = {
        "中信建投证券研究": "中信建投",
        "亨特研究笔记": "亨特",
        "卓哥投研笔记": "卓哥",
        "表舅是养基大户": "表舅",
    }
    lines = ["\n---\n", "## 📋 本期覆盖文章\n"]
    lines.append("| 作者 | 日期 | 内容 |")
    lines.append("|------|------|------|")
    for art in articles:
        # 从文件名提取日期格式化为 MMDD
        fname = art["file"].split("\\")[-1] if "\\" in art["file"] else art["file"].split("/")[-1]
        if len(fname) >= 8 and fname[:8].isdigit():
            date_str = fname[4:8]  # YYYYMMDD → MMDD
        else:
            date_str = "??"
        # 缩写作者名
        author = AUTHOR_SHORT.get(art["author"], art["author"])
        # 精简标题
        title = art["title"]
        # 去掉"首席经济学家XXX："前缀
        title = re.sub(r'^首席经济学家\S+[：:]?\s*', '', title)
        # 去掉"中信建投"前缀（包括"中信建投：""中信建投 |"等变体）
        title = re.sub(r'^中信建投\s*[：|]?\s*', '', title)
        if len(title) > 36:
            title = title[:35] + "…"
        if not title.strip():
            title = "（标题暂缺）"
        lines.append(f"| {author} | {date_str} | {title} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def _extract_msg_block(llm_text: str) -> str:
    """从 LLM 输出中提取 ```msg``` 块内容"""
    m = re.search(r'```msg\s*(.*?)\s*```', llm_text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _strip_msg_block(llm_text: str) -> str:
    """从 LLM 文本中移除 ```msg``` 块"""
    return re.sub(r'```msg\s*.*?\s*```\s*', '', llm_text, flags=re.DOTALL).strip()


def _parse_sections(content: str) -> dict:
    """按 ## 标题切分 markdown 为 named sections"""
    sections = {}
    current_header = "_preamble"
    current_lines = []
    for line in content.split("\n"):
        if line.startswith("## "):
            if current_lines:
                sections[current_header] = "\n".join(current_lines)
            current_header = line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections[current_header] = "\n".join(current_lines)
    return sections


def _find_header(sections: dict, keywords: list) -> str | None:
    """找到第一个含有任一关键词的 section header"""
    for h in sections:
        if any(kw in h for kw in keywords):
            return h
    return None


def generate_full_section(parsed: dict, articles: list, llm_text: str,
                          existing_content: str = "",
                          headline_heat: list = None,
                          covered_topics: set = None,
                          groups: dict = None,
                          enhanced_mapping_signals: list = None) -> str:
    """
    生成完整报告 — section-aware 多区块拼装。

    目标顺序:
      消息面要点(LLM替换) → 聚合新闻热点 → 话题深度分析
      → 🇺🇸美国板块(来自existing) → 🪝海外映射(新生成)
      → 🌍全球宏观(来自existing) → 🔗产业链(来自existing)
      → 📋本期覆盖文章
    """
    msg_refined = _extract_msg_block(llm_text)
    cleaned_llm_text = _strip_msg_block(llm_text)
    topic_section = generate_topic_analysis(articles, cleaned_llm_text, groups=groups)
    headline_section = render_headline_heat_section(headline_heat or [], covered_topics)
    overseas_section = render_enhanced_overseas_mapping(enhanced_mapping_signals or [])
    article_table = format_article_table(articles)

    if not existing_content:
        result = (msg_refined + "\n\n" if msg_refined else "")
        result += headline_section + "\n\n" + topic_section + "\n\n"
        result += overseas_section + "\n\n" + article_table
        return result

    # ── Parse existing content into sections ──
    sections = _parse_sections(existing_content)

    # ── Find sections by header keywords ──
    macro_header = _find_header(sections, ["全球宏观", "宏观环境"])
    us_header = _find_header(sections, ["美股板块", "个股异动", "美股"])
    chain_header = _find_header(sections, ["产业链轮动", "基本面因子", "概念链"])

    # ── Assemble in target order ──
    result_parts = []

    # 1. 消息面要点 (refined by LLM or keep original)
    msg_header = _find_header(sections, ["消息面要点"])
    if msg_header:
        if msg_refined:
            result_parts.append(f"{msg_header}\n\n{msg_refined}\n")
        else:
            result_parts.append(sections.get(msg_header, "").strip())

    # 2. 聚合新闻热点 (new)
    if headline_section.strip():
        result_parts.append(headline_section.strip())

    # 3. 话题深度分析 (new)
    if topic_section.strip():
        result_parts.append(topic_section.strip())

    # 4. 🇺🇸 US market section (from existing_content)
    if us_header and us_header in sections:
        result_parts.append(sections[us_header].strip())

    # 5. 🪝 海外映射 (new, enhanced)
    if overseas_section.strip():
        result_parts.append(overseas_section.strip())

    # 6. 🌍 全球宏观 (from existing, moved AFTER mapping)
    if macro_header and macro_header in sections:
        result_parts.append(sections[macro_header].strip())

    # 7. 🔗 产业链 / 基本面 (from existing)
    if chain_header and chain_header in sections:
        result_parts.append(sections[chain_header].strip())

    # 8. Any remaining sections not already placed
    placed = {us_header, macro_header, chain_header, msg_header}
    # Also exclude the old article table to avoid duplicates
    old_article_header = _find_header(sections, ["本期覆盖文章"])
    if old_article_header:
        placed.add(old_article_header)
    for h, content in sections.items():
        if h not in placed and h != "_preamble" and content.strip():
            result_parts.append(content.strip())

    # 9. 📋 本期覆盖文章
    result_parts.append(article_table.strip())

    result = "\n\n".join(result_parts)
    result = re.sub(r'\n(---\n){2,}', '\n---\n', result)
    return result


def generate_incremental_section(parsed: dict, old_titles: set,
                                 new_articles: list, now_str: str,
                                 headline_heat: list = None) -> str:
    """生成增量更新区块 — 聚合新闻头条热度为主 + 新增公众号文章为辅"""
    lines = ["\n---\n", f"## 🔄 午后更新 （{now_str}）\n"]

    # ── 1. 聚合新闻热点（新增话题，公众号未覆盖） ──
    hot_headlines = [h for h in (headline_heat or []) if h["heat"] >= 3]
    if hot_headlines:
        lines.append("**📊 聚合新闻高频话题**（华尔街见闻/东方财富）:\n")
        for h in hot_headlines[:6]:
            sample = h["samples"][0][:60] if h["samples"] else ""
            lines.append(f"- {h['topic']}: {h['heat']}条提及")
            if sample:
                lines.append(f"  · {sample}")
        lines.append("")

    # ── 2. 新增公众号文章（仅限真正新增的） ──
    new_files = [a["file"] for a in new_articles]
    truly_new = [a for a in new_articles if a["file"] not in old_titles]

    if truly_new:
        lines.append("**新增公众号文章**:\n")
        for a in truly_new[:10]:
            lines.append(f"- {a['author']}: {a['title'][:50]}")
        lines.append("")

        # 新增文章按话题分类
        groups = classify_articles(truly_new)
        if groups:
            lines.append("**新增话题**:\n")
            rank = {"high": 0, "medium": 1, "low": 2}
            sorted_topics = sorted(
                groups.items(),
                key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
            )
            for topic, info in sorted_topics[:5]:
                imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(info["importance"], "⚪")
                author_names = "、".join(sorted(info["authors"]))
                lines.append(f"{imp_icon} **{topic}** — {len(info['authors'])}位作者: {author_names}")
                for a in info["articles"]:
                    lines.append(f"  - {a['author']}: {a['title'][:40]}")
                lines.append("")
    elif not hot_headlines:
        lines.append("（午后无重大新增内容）\n")

    # LLM 增量分析
    llm_text = parsed.get("_llm_text", "")
    if llm_text and isinstance(parsed, dict) and hot_headlines:
        lines.extend(["---", "**AI增量分析**:", ""])
        lines.append(llm_text.strip())

    return "\n".join(lines) + "\n"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="信源日报聚合层 — 规则分类+轻量LLM追加到 sources.md")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--dry-run", action="store_true", help="只打印分类结果，不调LLM")
    parser.add_argument("--force", action="store_true", help="忽略已读标记，重新处理所有文章")
    args = parser.parse_args()

    date = args.date
    now_str = datetime.now().strftime("%H:%M")
    sources_md = OUTPUT_DIR / f"{date}_sources.md"
    deep_signals_path = DEEP_SIGNALS_DIR / f"{date}_deep_signals.json"

    if not sources_md.exists():
        print(f"[gen_daily_brief] ❌ {sources_md.name} 不存在，请先运行 gen_source_summary.py --ai")
        return

    # ── 1. 读取数据 ──
    print(f"[gen_daily_brief] 读取 sources.md + 今日文章 + 宏观数据...")
    report_content = sources_md.read_text(encoding="utf-8")
    articles = get_unread_articles(date, force=args.force)
    deep_data = load_json(deep_signals_path)
    articles = match_deep_signals(articles, deep_data)
    us_mentions = extract_us_stock_mentions(articles)
    macro_text = build_macro_snapshot()
    print(f"  公众号文章: {len(articles)} 篇")
    print(f"  Deep Signals: {'有' if deep_data else '无'}")
    if us_mentions:
        print(f"  文章提及美股: {len(us_mentions)} 条")

    # ── 2. 检测模式 ──
    processed = load_processed_articles()
    # 判断 sources.md 是否已有话题分析区——有则午后增量，无则首次完整追加
    has_sections = ("## 📰 公众号观点聚合" in report_content)
    mode = "full" if args.force else ("incremental" if has_sections else "full")
    print(f"  模式: {'增量更新' if mode == 'incremental' else '完整追加'}（今日 {len(articles)} 篇）")

    if not articles:
        print("[gen_daily_brief] ⚠ 今日无文章，跳过 LLM 分析")
        if not has_sections:
            fallback = ("\n---\n## 📋 本期覆盖文章\n"
                        "（今日暂无公众号文章）\n")
            sources_md.write_text(report_content + fallback, encoding="utf-8")
            print(f"[gen_daily_brief] ✅ 已追加空聚合区到 {sources_md.name}")
        return

    # ── 3. 规则分类展示（不依赖LLM） ──
    groups = classify_articles(articles)
    rank = {"high": 0, "medium": 1, "low": 2}
    sorted_topics = sorted(
        groups.items(),
        key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
    )
    print(f"\n  规则分类结果:")
    for topic, info in sorted_topics:
        authors_str = "、".join(sorted(info["authors"]))
        print(f"    {topic}: {len(info['articles'])}篇, {len(info['authors'])}位作者({authors_str})")

    # ── 海外映射检测：US→A股全量映射（ETF + 明星股 + 概念链） ──
    covered_topics = set(groups.keys()) if groups else set()
    enhanced_mapping_signals = build_overseas_mapping(covered_topics)
    if enhanced_mapping_signals:
        print(f"  🪝 US→A股映射增强: {len(enhanced_mapping_signals)} 个方向")
        for s in enhanced_mapping_signals[:5]:
            print(f"    {s['a_share_direction']} (评分{s['composite_score']:.1f}, "
                  f"{len(s['us_etf_signals'])}ETF + {len(s['us_star_signals'])}星)")
        ath = getattr(enhanced_mapping_signals, "ath_data", None)
        if ath:
            if ath["ath_etfs"]:
                print(f"    🏆 ETF创历史新高: {', '.join(e['symbol'] for e in ath['ath_etfs'][:5])}")
            if ath["ath_stocks"]:
                ath_stock_strs = []
                for s2 in ath["ath_stocks"][:5]:
                    cn = s2.get("cn_name", "")
                    ath_stock_strs.append(f"{s2['symbol']}({cn})")
                print(f"    🏆 个股创历史新高: {', '.join(ath_stock_strs)}")

    # ── 聚合新闻头条热度（话题发现源） ──
    headlines = load_headlines()
    headline_heat = compute_headline_heat(headlines)
    if headline_heat:
        print(f"\n  聚合新闻话题热度:")
        for hh in headline_heat[:6]:
            print(f"    {hh['topic']}: {hh['heat']}条")
        # 检查公众号覆盖情况
        uncovered = [h for h in headline_heat if not any(
            any(kw in ct for kw in h['topic'].split())
            for ct in covered_topics
        )]
        if uncovered:
            print(f"  公众号未覆盖的热点话题:")
            for h in uncovered[:4]:
                print(f"    {h['topic']}: {h['heat']}条")

    # ── 4. 外资观点提取：从聚合新闻中筛出外资机构观点 → foreign_views/ ──
    if headlines:
        foreign_views = extract_foreign_views_from_headlines(headlines)
        save_foreign_views_snapshot(foreign_views, date)
    else:
        foreign_views = []

    # ── 5. 提取昨日观点（用于LLM上下文） ──
    prev_views_text = extract_prev_views(date)

    # ── 5. 轻量 LLM 调用 ──
    if args.dry_run:
        print("\n[dry-run] 跳过LLM调用，只输出规则分类结果")
        # 只输出文章清单+规则分类
        fallback_lines = ["\n---\n", "## 📋 本期覆盖文章\n"]
        fallback_lines.append("| 作者 | 日期 | 文章 |")
        fallback_lines.append("|------|------|------|")
        for art in articles:
            date_str = art["file"][-20:-12] if len(art["file"]) > 12 else "?"
            fallback_lines.append(f"| {art['author']} | {date_str} | {art['title'][:40]} |")
        fallback_lines.append("")
        for topic, info in sorted_topics:
            imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(info["importance"], "⚪")
            author_names = "、".join(sorted(info["authors"]))
            fallback_lines.append(f"### {imp_icon} {topic} — {len(info['authors'])}位作者: {author_names}")
            for a in info["articles"]:
                fallback_lines.append(f"- **{a['author']}** 《{a['title'][:50]}》")
            fallback_lines.append("")
        us_md = format_us_mentions(us_mentions)
        if us_md:
            fallback_lines.append(us_md)
        section = "\n".join(fallback_lines) + "\n"
        sources_md.write_text(report_content + section, encoding="utf-8")
        print(f"[gen_daily_brief] ✅ dry-run 分类已追加到 {sources_md.name}")
        return

    prompt = build_llm_prompt(articles, macro_text,
                              is_incremental=(mode == "incremental"),
                              prev_article_titles=processed,
                              prev_views_text=prev_views_text,
                              date_str=date,
                              uncoupled_signals=enhanced_mapping_signals,
                              headline_heat=headline_heat,
                              groups=groups)

    print(f"\n  调用 LLM 进行观点摘要（轻量）...")
    try:
        from ai_analyzer import call_llm
        system_prompt = "你是一个交易员视角的市场分析助手。把公众号文章按主题分组，提取各作者核心观点和股票代码，输出Dorian六步分析。用中文，交易员的语言，短句。"
        llm_text, provider = call_llm(system_prompt, prompt, max_tokens=4096)
        print(f"  LLM: {provider} ({len(llm_text)} chars)")
    except Exception as e:
        print(f"  ⚠ LLM 调用失败: {e}")
        llm_text = ""

    # ── 5b. 变量分类器匹配 ──
    candidate_count = 0
    if llm_text:
        topics = parse_llm_topics(llm_text)
        unmatched = []
        for topic in topics:
            topic_name = topic.get("topic", "")
            if not topic_name:
                continue
            match_kws = [topic_name]
            for ad in topic.get("author_details", []):
                key_point = ad.get("key_point", "")
                if key_point:
                    match_kws.append(key_point[:30])
            results = lookup_variable(match_kws)
            if results:
                best = results[0]
                topic["_taxonomy"] = {
                    "var_id": best["id"],
                    "level": best["level"],
                    "chain": best.get("chain", ""),
                    "narratives": best.get("narratives", []),
                    "pricing": best.get("pricing", ""),
                    "validation": best.get("validation", []),
                }
            else:
                unmatched.append(topic_name)

        if unmatched:
            candidate_count = add_candidates(unmatched, f"gen_daily_brief_{date}")
            matched = len(topics) - len(unmatched)
            print(f"  变量分类器: {matched}/{len(topics)} 话题已匹配")

    if candidate_count > 0:
        print(f"  ⚠ {candidate_count} 个新话题（待分类） → /taxonomy-review")

    # ── 6. 生成 Markdown（话题深度分析插在消息面要点后，文章清单放末尾） ──
    us_md = format_us_mentions(us_mentions)
    if mode == "full":
        parsed = {"_llm_text": llm_text} if llm_text else {}
        section = generate_full_section(parsed, articles, llm_text,
                                        existing_content=report_content,
                                        headline_heat=headline_heat,
                                        covered_topics=covered_topics,
                                        groups=groups,
                                        enhanced_mapping_signals=enhanced_mapping_signals)
        section += us_md
        sources_md.write_text(section, encoding="utf-8")
    else:
        parsed = {"_llm_text": llm_text} if llm_text else {}
        section = generate_incremental_section(parsed, processed, articles, now_str, headline_heat=headline_heat)
        section += us_md
        sources_md.write_text(report_content + section, encoding="utf-8")

    if not args.force:
        save_processed_articles(articles)
    print(f"[gen_daily_brief] ✅ 聚合层已追加到 {sources_md.name}")

    # ── 7. 事件流：从海外映射信号提取结构化事件 ──
    if enhanced_mapping_signals:
        try:
            from tools.event_stream import EventStream, extract_mapping_events
            events = extract_mapping_events(enhanced_mapping_signals, date)
            if events:
                es = EventStream()
                es.add_events(events, date)
                es.save_latest(date)
                print(f"  [事件流] 海外映射 → {len(events)} 个事件")
        except Exception as e:
            print(f"  [事件流] ⚠ 提取失败（不影响主流程）: {e}")

    # ── 7. 打印摘要 ──
    print(f"\n  📊 本期覆盖: {len(articles)} 篇文章, {len(groups)} 个主题")
    for topic, info in sorted_topics[:5]:
        imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(info["importance"], "⚪")
        print(f"    {imp_icon} {topic} ({len(info['authors'])}位作者)")
    if llm_text:
        print(f"  🤖 AI观点摘要: {len(llm_text)} chars")


if __name__ == "__main__":
    main()

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
    '卓哥投研笔记', '灰岩金融科技', '猫笔刀', '中信建投证券研究',
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
US_SECTOR_X1_THRESHOLD = 3.0  # 美国偏强但A股无人讨论的信号检测阈值 (deprecated, 保留兼容)
US_DAILY_CHG_THRESHOLD = 2.0   # 日异动阈值 (%): US ETF 日涨跌超过此值触发话题桥
US_WEEK_CHG_THRESHOLD = 5.0   # 周涨幅阈值 (%): US ETF 周涨跌超过此值触发话题桥

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
    except Exception:
        return set()

def save_processed_articles(articles: list):
    """标记文章为已处理，追加到 processed_articles.json"""
    proc = {}
    if PROCESSED_FILE.exists():
        try:
            proc = json.loads(PROCESSED_FILE.read_text(encoding="utf-8")).get("processed", {})
        except Exception:
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
    except Exception:
        pass
    return {}


def get_unread_articles(today_str: str, force: bool = False) -> list:
    """
    取 wechat_articles/ 下未处理过的文章。
    去重依据：processed_articles.json（文件名精确匹配）。
    force=True 时忽略已读标记，全量返回。
    """
    if not WECHAT_DIR.exists():
        return []

    processed = load_processed_articles() if not force else set()
    today_date = datetime.strptime(today_str, "%Y%m%d")

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
        # 只看最近5天
        fname = f.name
        if len(fname) >= 8:
            try:
                f_date = datetime.strptime(fname[:8], "%Y%m%d")
                if f_date < today_date - timedelta(days=5):
                    continue
            except ValueError:
                pass

        # 去重：文件名精确匹配 processed_articles.json
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
    except Exception:
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
    except Exception:
        return {}


def detect_uncoupled_signals(us_momentum: dict, covered_topics: set,
                              threshold=US_SECTOR_X1_THRESHOLD) -> list:
    """
    检测 US 异动且公众号未覆盖的板块信号。

    逻辑:
      1. 按 category 分组
      2. 检查组内是否有 ETF 日涨跌 > 2% 或 周涨跌 > 5%
      3. 有 → 查 cross_mapping 找关联 A 股板块 (回退硬编码映射)
      4. 查公众号覆盖 → 未覆盖则生成信号
      5. 最多返回 2 条, 没有异动就跳过

    us_momentum: us_sector_momentum.json 的 dict (含 etfs 列表, 每项有 x1/daily_chg/week_chg/category)
    covered_topics: 当日公众号文章已覆盖的话题集合
    """
    etfs = us_momentum.get("etfs", []) if us_momentum else []
    if not etfs:
        return []

    # 预加载 cross_mapping
    cross_map = _load_cross_mapping()

    # 按 category 分组
    cat_groups = {}
    for e in etfs:
        cat = e.get("category", "Other")
        cat_groups.setdefault(cat, []).append(e)

    signals = []
    for cat, items in cat_groups.items():
        # 检查该 category 是否有异动
        has_daily = any(
            (e.get("daily_chg") or 0) > US_DAILY_CHG_THRESHOLD
            for e in items if e.get("daily_chg") is not None
        )
        has_weekly = any(
            (e.get("week_chg") or 0) > US_WEEK_CHG_THRESHOLD
            for e in items if e.get("week_chg") is not None
        )
        if not has_daily and not has_weekly:
            continue

        # 找该 category 下异动最强的 ETF
        best = max(items, key=lambda e: max(
            abs(e.get("daily_chg", 0) or 0),
            abs(e.get("week_chg", 0) or 0),
        ))
        best_sym = best["symbol"]

        # 查 cross_mapping 找关联 A 股板块
        a_share_sectors = []
        if best_sym in cross_map:
            # 取相关系数最高的前 3 个板块
            mapped = sorted(cross_map[best_sym], key=lambda x: -abs(x[1]))[:3]
            a_share_sectors = [s for s, _ in mapped]

        # 回退硬编码映射
        if not a_share_sectors:
            a_share_sectors = US_SECTOR_TO_A_SHARE.get(cat, [])

        if not a_share_sectors:
            continue

        # 检查公众号是否已覆盖
        covered = any(
            any(kw in topic for kw in a_share_sectors)
            for topic in covered_topics
        )
        if covered:
            continue

        signals.append({
            "us_category": cat,
            "trigger_etf": best_sym,
            "trigger_etf_name": best.get("name", ""),
            "daily_chg": best.get("daily_chg", 0),
            "week_chg": best.get("week_chg", 0),
            "month_chg": best.get("month_chg", 0),
            "a_share_sectors": a_share_sectors[:3],
            "from_cross_mapping": best_sym in cross_map,
        })

        # 最多 2 条
        if len(signals) >= 2:
            break

    return signals


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

    # ── 海外量化信号（公众号未覆盖的 US→A 映射） ──
    if uncoupled_signals:
        blocks.append("\n===== 海外量化信号（公众号未覆盖）=====")
        sig_lines = []
        for s in uncoupled_signals:
            a_share = "、".join(s["a_share_sectors"])
            sig_lines.append(
                f"- {s['us_category']} ({s.get('trigger_etf_name', s.get('trigger_etf', ''))} "
                f"日涨跌={s['daily_chg']:+.1f}%, 周涨跌={s['week_chg']:+.1f}%) "
                f"→ A股映射: {a_share}"
            )
        blocks.append("\n".join(sig_lines))
        blocks.append("\n注：以上板块在 US 市场势能强（x₁≥3），但今日8个公众号未讨论。"
                       "如涉及A股映射板块，可在话题分析中适当关注。")

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


OVERSEAS_MAPPING_HEADER = "## 🪝 海外映射"

def render_overseas_mapping(signals: list) -> str:
    """生成海外映射区块 — US 异动但公众号未覆盖的板块信号"""
    if not signals:
        return ""
    lines = ["\n---\n", f"{OVERSEAS_MAPPING_HEADER}\n"]
    lines.append("以下 US 板块出现异动但公众号未充分覆盖，可能存在映射信号：\n")
    lines.append("| US板块 | 触发标的 | 日涨跌 | 周涨跌 | 月涨跌 | A股映射 |")
    lines.append("|--------|---------|--------|--------|--------|--------|")
    for s in signals:
        cat = s["us_category"]
        etf = s.get("trigger_etf_name", s.get("trigger_etf", ""))
        dc = f"{s['daily_chg']:+.2f}%" if isinstance(s.get('daily_chg'), (int, float)) else "-"
        wc = f"{s['week_chg']:+.2f}%" if isinstance(s.get('week_chg'), (int, float)) else "-"
        mc = f"{s['month_chg']:+.2f}%" if isinstance(s.get('month_chg'), (int, float)) else "-"
        a_s = "、".join(s["a_share_sectors"])
        lines.append(f"| {cat} | {etf} | {dc} | {wc} | {mc} | {a_s} |")
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


def generate_full_section(parsed: dict, articles: list, llm_text: str,
                          existing_content: str = "",
                          uncoupled_signals: list = None,
                          headline_heat: list = None,
                          covered_topics: set = None,
                          groups: dict = None) -> str:
    """生成完整报告 — 消息面要点(LLM精炼) → 聚合新闻热点 → 话题深度分析 → 海外映射 → 其余数据 → 文章清单"""
    msg_refined = _extract_msg_block(llm_text)
    cleaned_llm_text = _strip_msg_block(llm_text)
    topic_section = generate_topic_analysis(articles, cleaned_llm_text, groups=groups)
    headline_section = render_headline_heat_section(headline_heat or [], covered_topics)
    overseas_section = render_overseas_mapping(uncoupled_signals or [])
    article_table = format_article_table(articles)

    if existing_content:
        msg_match = re.search(
            r'(## 🔴 消息面要点.*?)(?=\n## )',
            existing_content, re.DOTALL
        )
        if msg_match and msg_refined:
            refined_section = "## 🔴 消息面要点\n\n" + msg_refined + "\n"
            rest = existing_content[msg_match.end():]
            # 去掉旧 LLM 生成的区块（话题分析/Dorian/文章清单），避免重复
            for header in ['## 🧠 话题深度分析', '## 🔗 Dorian 六步拆解', '## 🔗 Dorian', '## 📋 本期覆盖文章']:
                rest = re.sub(
                    rf'\n{re.escape(header)}.*?(?=\n## +|\Z)',
                    '', rest, flags=re.DOTALL
                )
            result = refined_section + "\n\n" + headline_section.strip() + "\n\n" + topic_section.strip() + "\n\n" + overseas_section.strip() + "\n\n" + rest.strip()
        elif msg_match:
            msg_part = msg_match.group(1)
            rest = existing_content[msg_match.end():]
            for header in ['## 🧠 话题深度分析', '## 🔗 Dorian 六步拆解', '## 🔗 Dorian', '## 📋 本期覆盖文章', '## 🪝 海外映射']:
                rest = re.sub(
                    rf'\n{re.escape(header)}.*?(?=\n## +|\Z)',
                    '', rest, flags=re.DOTALL
                )
            result = msg_part + "\n\n" + headline_section.strip() + "\n\n" + topic_section.strip() + "\n\n" + overseas_section.strip() + "\n\n" + rest.strip()
        else:
            result = existing_content + "\n" + headline_section + "\n" + topic_section + "\n" + overseas_section
        result += article_table
        result = re.sub(r'\n(---\n){2,}', '\n---\n', result)
        return result
    else:
        result = topic_section + "\n" + overseas_section
        result += article_table
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
    mode = "full" if args.force else ("incremental" if processed else "full")
    has_sections = "## 📰 热点事件分类" in report_content or "## 📰 公众号观点聚合" in report_content
    print(f"  模式: {'增量更新' if mode == 'incremental' else '完整追加'}（已处理 {len(processed)} 篇，今日 {len(articles)} 篇）")

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

    # ── 海外映射检测：US 强势但公众号未覆盖的板块 ──
    us_momentum = load_json(SIGNALS_DIR / "_macro" / "us_sector_momentum.json")
    covered_topics = set(groups.keys()) if groups else set()
    uncoupled_signals = detect_uncoupled_signals(us_momentum, covered_topics)
    if uncoupled_signals:
        print(f"  🪝 US→A股映射信号: {len(uncoupled_signals)} 个板块未覆盖")
        for s in uncoupled_signals:
            a_share = "、".join(s["a_share_sectors"])
            etf = s.get("trigger_etf_name", s.get("trigger_etf", ""))
            print(f"    {s['us_category']} ({etf} 日{s['daily_chg']:+.1f}% 周{s['week_chg']:+.1f}%) → {a_share}")

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
                              uncoupled_signals=uncoupled_signals,
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
                                        uncoupled_signals=uncoupled_signals,
                                        headline_heat=headline_heat,
                                        covered_topics=covered_topics,
                                        groups=groups)
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

    # ── 7. 打印摘要 ──
    print(f"\n  📊 本期覆盖: {len(articles)} 篇文章, {len(groups)} 个主题")
    for topic, info in sorted_topics[:5]:
        imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(info["importance"], "⚪")
        print(f"    {imp_icon} {topic} ({len(info['authors'])}位作者)")
    if llm_text:
        print(f"  🤖 AI观点摘要: {len(llm_text)} chars")


if __name__ == "__main__":
    main()

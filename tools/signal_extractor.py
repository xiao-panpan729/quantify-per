# -*- coding: utf-8 -*-
"""
信号事件流提取器 — 从 9 个数据源 + 微信文章中提取结构化信号事件,
通过知识图谱映射到 50 条叙事链, 输出 JSON 事件流供专家系统消费.

用法:
  python tools/signal_extractor.py                  # 默认今日
  python tools/signal_extractor.py --date 20260608  # 指定日期
  python tools/signal_extractor.py --no-cache        # 跳过去重缓存

输出:
  signals/tracking/_signals/daily_signals/{YYYYMMDD}_signals.json
  signals/tracking/_signals/daily_signals/{YYYYMMDD}_signals.md
"""
import json
import sys
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter, defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking"
OUTPUT_DIR = SIGNALS_DIR / "_signals" / "daily_signals"
KG_PATH = SIGNALS_DIR / "_macro" / "industry_kg.json"
FRESHNESS_CACHE = OUTPUT_DIR / "_freshness_cache.json"

# 50 条叙事链中文名
CHAIN_NAMES = {
    "narrative#1": "IC设计", "narrative#2": "半导体设备", "narrative#3": "半导体材料",
    "narrative#3a": "SiC衬底", "narrative#4": "先进封装", "narrative#5": "EDA/IP",
    "narrative#6": "功率半导体", "narrative#7": "半导体配套", "narrative#8": "MLCC/PCB/载板",
    "narrative#10": "AI应用", "narrative#11": "AI基础设施", "narrative#12": "光模块/CPO",
    "narrative#13": "5G/6G通信", "narrative#14": "消费电子", "narrative#15": "AI穿戴",
    "narrative#16": "面板", "narrative#17": "智能驾驶", "narrative#18": "新能源车",
    "narrative#19": "汽车电子", "narrative#20": "充电桩", "narrative#21": "汽车零部件",
    "narrative#22": "人形机器人", "narrative#23": "机器人核心部件", "narrative#24": "工业机器人",
    "narrative#25": "工业互联网", "narrative#26": "低空经济", "narrative#27": "商业航天",
    "narrative#28": "国防军工", "narrative#29": "光伏", "narrative#30": "风电",
    "narrative#31": "电池", "narrative#32": "锂资源", "narrative#33": "电网/电力设备",
    "narrative#34": "储能", "narrative#35": "信创", "narrative#36": "数据要素",
    "narrative#37": "数字货币", "narrative#38": "创新药", "narrative#39": "疫苗",
    "narrative#40": "医疗器械", "narrative#41": "中药", "narrative#42": "贵金属",
    "narrative#43": "小金属/战略金属", "narrative#44": "有色金属（铜铝）",
    "narrative#45": "能源金属", "narrative#46": "新材料", "narrative#47": "化工新材料",
    "narrative#48": "游戏", "narrative#49": "数字内容/IP", "narrative#50": "养殖/农业",
}

# ── 映射表: 结构化源 → 叙事链 ──

# 突发事件类型 → 叙事链
SHOCK_TYPE_CHAINS = {
    "sector_chip": ["narrative#2", "narrative#1", "narrative#3"],
    "sector_semiconductor": ["narrative#2", "narrative#1", "narrative#3"],
    "sector_ai_tech": ["narrative#11", "narrative#1", "narrative#10"],
    "sector_new_energy": ["narrative#29", "narrative#31", "narrative#34"],
    "sector_automotive": ["narrative#18", "narrative#19", "narrative#20"],
    "sector_pharma": ["narrative#38", "narrative#40"],
    "sector_internet": ["narrative#10", "narrative#48"],
    "sector_realestate": [],
    "macro_geopolitical": ["narrative#42", "narrative#28"],
    "macro_tariff": ["narrative#35", "narrative#5"],
    "macro_trade_deal": ["narrative#18", "narrative#29"],
    "macro_cmc_list": ["narrative#5", "narrative#35"],
    "macro_policy_easing": [],
    "macro_policy_tightening": [],
    "market_fed_hawkish": [],
    "market_fed_dovish": [],
    "market_vix_spike": [],
    "market_blackswan": [],
}

# US ETF 类别 → 叙事链
ETF_CATEGORY_CHAINS = {
    "Tech & AI": ["narrative#11", "narrative#1", "narrative#10"],
    "Semiconductor Chain": ["narrative#2", "narrative#1", "narrative#3"],
    "Healthcare & Biotech": ["narrative#38", "narrative#40"],
    "Energy & Materials": ["narrative#45", "narrative#46"],
    "Defense & Industrial": ["narrative#28"],
    "Consumer & Retail": ["narrative#14"],
    "China & Emerging Markets": ["narrative#31", "narrative#18"],
    "Crypto & Alternatives": ["narrative#37"],
    "Broad Market": [],
    "GICS Sectors": [],
    "Finance & Fintech": [],
    "Real Estate & Infrastructure": [],
}

# US 明星股类别 → 叙事链
STAR_CATEGORY_CHAINS = {
    "Magnificent 7": ["narrative#11", "narrative#10"],
    "Semiconductor Chain": ["narrative#2", "narrative#1", "narrative#3"],
    "AI & Software": ["narrative#10", "narrative#11"],
    "Consumer & Retail": ["narrative#14"],
    "Healthcare": ["narrative#38"],
    "Energy": ["narrative#45"],
    "Industrial & Defense": ["narrative#28"],
    "Crypto & Alts": ["narrative#37"],
    "Finance": [],
}

# US 概念链 → 叙事链
CONCEPT_CHAIN_MAP = {
    "英伟达链": ["narrative#11", "narrative#1"],
    "半导体产业链": ["narrative#2", "narrative#1", "narrative#3"],
    "AI算力链": ["narrative#11"],
    "苹果链": ["narrative#14"],
    "云计算/数据基建": ["narrative#11"],
    "软件/SaaS": ["narrative#10"],
    "AI应用层": ["narrative#10"],
    "加密货币/区块链": ["narrative#37"],
    "量子计算": ["narrative#1", "narrative#11"],
    "元宇宙/XR": ["narrative#15"],
    "传统能源": ["narrative#45"],
    "金融科技": ["narrative#37"],
    "大型金融": [],
    "零售消费": ["narrative#14"],
}

# 垃圾前缀 — KG 产品名以这些字符开头时跳过滤
SKIP_PREFIXES = frozenset(["%", ")", "-", "#", "'", " "])
MIN_PRODUCT_LEN = 3  # 过滤2字短词("IP"/"AI"/"PU"到处匹配产生杂音)

# ═══════════════════════════════════════════════
# 同义词映射 — 产品名/术语变体 → 规范名
# 用于 KG 匹配时补齐别名, 如 "WF6" → "六氟化钨"
# ═══════════════════════════════════════════════

SYNONYM_MAP = {
    # 半导体/芯片
    "芯片": ["chip", "晶片", "半导体芯片", "集成电路"],
    "半导体设备": ["半导体设备", "芯片设备", "fab设备"],
    "碳化硅": ["SiC", "sic", "碳化硅衬底", "碳化硅晶圆"],
    "氮化镓": ["GaN", "gan", "氮化镓功率"],
    "光刻机": ["光刻", "lithography", "EUV", "DUV", "光刻设备"],
    "刻蚀设备": ["刻蚀", "etch", "等离子刻蚀", "介质刻蚀"],
    "薄膜沉积": ["PVD", "CVD", "ALD", "薄膜沉积设备"],
    "IGBT": ["igbt", "绝缘栅双极型晶体管"],
    "HBM": ["hbm", "高带宽存储", "高带宽储存"],
    "先进封装": ["先进封装", "Chiplet", "chiplet", "2.5D封装", "3D封装", "CoWoS", "cowos", "TSV"],
    "EDA": ["eda", "电子设计自动化", "IP授权"],
    # 化工材料
    "六氟化钨": ["WF6", "wf6", "六氟化钨气体"],
    "光刻胶": ["光刻胶", "光阻", "光刻胶材料"],
    "电子特气": ["电子特气", "电子气体", "特种气体"],
    "硅片": ["硅片", "硅晶圆", "12寸硅片", "大硅片"],
    "靶材": ["溅射靶材", "靶材"],
    # AI/算力
    "AI": ["ai", "人工智能", "AI大模型", "GPT", "大模型", "LLM", "AIGC", "深度求索"],
    "物理AI": ["物理ai", "具身智能", "embodied AI", "physical AI"],
    "AI算力": ["算力", "算力基础设施", "HPC", "高性能计算"],
    "液冷": ["液冷", "液体冷却", "浸没式冷却", "冷却液", "散热"],
    "光模块": ["光模块", "光通信", "光器件", "光收发"],
    "CPO": ["cpo", "共封装光学", "硅光模块", "硅光子"],
    # 新能源
    "光伏": ["光伏", "太阳能", "光储", "光伏组件", "光伏逆变器"],
    "锂电池": ["锂电", "锂电池", "动力电池", "电池"],
    "固态电池": ["固态电池", "半固态电池", "全固态电池"],
    "储能": ["储能", "大储", "户储", "储能系统", "储能电池"],
    "氢能": ["氢能", "氢能源", "燃料电池", "电解水"],
    # 智能驾驶
    "智能驾驶": ["自动驾驶", "无人驾驶", "FSD", "ADAS", "智驾"],
    # 军工
    "军工": ["国防", "军事", "军品", "军用"],
    # 信创/国产替代
    "信创": ["信创", "国产替代", "自主可控", "国产化"],
}

# ═══════════════════════════════════════════════
# US 个股 → 概念链 → 叙事链 映射索引
# 从 concept_chains.json 构建: 个股ticker → 所属概念 → 叙事链
# ═══════════════════════════════════════════════

# US 概念链名称 → 叙事链映射 (与 CONCEPT_CHAIN_MAP 同步增强)
STOCK_CONCEPT_CHAIN_MAP = {
    "英伟达链": ["narrative#11", "narrative#1", "narrative#12"],
    "半导体产业链": ["narrative#2", "narrative#1", "narrative#3"],
    "AI算力链": ["narrative#11", "narrative#12"],
    "苹果链": ["narrative#14", "narrative#15"],
    "特斯拉/EV链": ["narrative#18", "narrative#17", "narrative#19"],
    "云计算/数据基建": ["narrative#11", "narrative#35"],
    "软件/SaaS": ["narrative#10"],
    "AI应用层": ["narrative#10"],
    "加密货币/区块链": ["narrative#37"],
    "量子计算": ["narrative#1", "narrative#11"],
    "元宇宙/XR": ["narrative#15"],
    "传统能源": ["narrative#45", "narrative#44"],
    "金融科技": ["narrative#37"],
    "大型金融": [],
    "零售消费": ["narrative#14"],
    "生物科技": ["narrative#38", "narrative#39"],
    "基因/精准医疗": ["narrative#38", "narrative#40"],
    "清洁能源": ["narrative#29", "narrative#34"],
    "光伏链": ["narrative#29"],
    "金矿/贵金属": ["narrative#42"],
    "钢铁/基础材料": ["narrative#43", "narrative#44"],
    "网络安全": ["narrative#35", "narrative#5"],
    "区域银行": [],
    "军工/国防": ["narrative#28"],
    "消费/零售": ["narrative#14"],
    "通信/网络": ["narrative#13"],
}  # fmt:skip

# 个股级精确映射 (覆盖大盘/明星股/ETF重仓)
STOCK_TO_CHAINS = {
    # Magnificent 7
    "NVDA": ["narrative#11", "narrative#1"],
    "AAPL": ["narrative#14", "narrative#15"],
    "MSFT": ["narrative#10", "narrative#11"],
    "GOOGL": ["narrative#10", "narrative#11", "narrative#13"],
    "AMZN": ["narrative#10", "narrative#11"],
    "META": ["narrative#10", "narrative#15"],
    "TSLA": ["narrative#18", "narrative#17"],
    # 半导体
    "TSM": ["narrative#1", "narrative#2"],
    "AVGO": ["narrative#1", "narrative#11"],
    "ASML": ["narrative#2"],
    "AMD": ["narrative#1", "narrative#11"],
    "QCOM": ["narrative#1", "narrative#13"],
    "AMAT": ["narrative#2"],
    "LRCX": ["narrative#2"],
    "KLAC": ["narrative#2"],
    "MU": ["narrative#1", "narrative#11"],
    "MRVL": ["narrative#1", "narrative#12"],
    "INTC": ["narrative#1"],
    "NXPI": ["narrative#6"],
    "ON": ["narrative#6"],
    # AI 基础设施
    "SMCI": ["narrative#11"],
    "ANET": ["narrative#11", "narrative#12"],
    "CRDO": ["narrative#12"],
    "COHR": ["narrative#12"],
    "VRT": ["narrative#11"],
    "DELL": ["narrative#11"],
    # 软件/AI 应用
    "PLTR": ["narrative#10", "narrative#35"],
    "SNOW": ["narrative#10", "narrative#11"],
    "CRM": ["narrative#10"],
    "NOW": ["narrative#10"],
    "ADBE": ["narrative#10"],
    # 医药
    "AMGN": ["narrative#38"],
    "GILD": ["narrative#38"],
    "VRTX": ["narrative#38"],
    "REGN": ["narrative#38"],
    "MRNA": ["narrative#39"],
    "BNTX": ["narrative#39"],
    # 新能源/光伏
    "ENPH": ["narrative#29", "narrative#34"],
    "FSLR": ["narrative#29"],
    "SEDG": ["narrative#29"],
    # 军工/贵金属
    "NEM": ["narrative#42"],
    "GOLD": ["narrative#42"],
    # 消费
    "HD": ["narrative#14"],
    "WMT": ["narrative#14"],
    "COST": ["narrative#14"],
    # 防御/工业
    "SLB": ["narrative#45"],
    "HAL": ["narrative#45"],
    "BKR": ["narrative#45"],
}

# 流动性 tightening → 影响的高 beta 链
HIGH_BETA_CHAINS = ["narrative#11", "narrative#18", "narrative#29", "narrative#31", "narrative#34"]

# ═══════════════════════════════════════════════
# KG 未覆盖的关键词 → 叙事链 硬编码映射 (兜底)
# 同义词解析不到 KG 中已有产品时, 直接指定链
# ═══════════════════════════════════════════════

FALLBACK_CHAINS = {
    # 化工/材料 (KG缺)
    "六氟化钨": ["narrative#47", "narrative#46"],
    "WF6": ["narrative#47", "narrative#46"],
    # 信创/国产化 (KG缺)
    "信创": ["narrative#35"],
    "国产替代": ["narrative#35", "narrative#5"],
    "自主可控": ["narrative#35", "narrative#5"],
    # 军工 (KG缺)
    "军工": ["narrative#28"],
    "国防": ["narrative#28"],
    "军品": ["narrative#28"],
    "军用": ["narrative#28"],
    # 智能驾驶 (KG缺)
    "自动驾驶": ["narrative#17"],
    "无人驾驶": ["narrative#17"],
    "FSD": ["narrative#17"],
    "智能驾驶": ["narrative#17"],
    # 大模型/AI (KG中的大模型链不对)
    "大模型": ["narrative#10", "narrative#11"],
    "LLM": ["narrative#10", "narrative#11"],
    # 液冷 (KG中的液冷产品挂了错链)
    "液冷": ["narrative#11"],
    "液体冷却": ["narrative#11"],
    "浸没式冷却": ["narrative#11"],
    # 其他补充
    "人形机器人": ["narrative#22"],
    "低空经济": ["narrative#26"],
    "商业航天": ["narrative#27"],
    "物理AI": ["narrative#22", "narrative#10"],
    "具身智能": ["narrative#22", "narrative#10"],
    "锂电池": ["narrative#31"],
    "锂电": ["narrative#31"],
    "储能": ["narrative#34"],
    "算力": ["narrative#11"],
    "AI算力": ["narrative#11"],
    "硫磺": ["narrative#47"],
    "H100": ["narrative#11"],
    "H200": ["narrative#11"],
    # 行业景气
    "产能扩张": ["narrative#7"],
    "供给紧张": ["narrative#7"],
    "供不应求": ["narrative#7"],
    "涨价": ["narrative#47", "narrative#43"],
}


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _date_str(dt=None):
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y%m%d")


def _signal_id(source: str, idx: int, date: str) -> str:
    return f"{source}_{idx}_{date}"


def _chain_name(chain_id: str) -> str:
    return CHAIN_NAMES.get(chain_id, chain_id)


def _clean(text: str, maxlen=120) -> str:
    if not text:
        return ""
    return str(text).replace("�", "").replace("\n", " ").strip()[:maxlen]


# ═══════════════════════════════════════════════
# DataSourceReader — 读取所有原始数据源
# ═══════════════════════════════════════════════

class DataSourceReader:
    """读取 9 个 JSON 数据源 + 微信文章标题"""

    def __init__(self, date: str):
        self.date = date
        self._raw = {}

    def read_all(self) -> dict:
        self._raw = {
            "shock": self._read_shock(),
            "liquidity": self._read_liquidity(),
            "macro": self._read_macro_snapshot(),
            "us_macro": self._read_us_macro(),
            "japan": self._read_japan(),
            "us_etf": self._read_us_etf(),
            "us_stars": self._read_us_stars(),
            "us_concepts": self._read_us_concepts(),
            "fundamental": self._read_fundamentals(),
            "wechat": self._read_wechat_articles(),
        }
        return self._raw

    def _read_shock(self):
        return _read_json(SIGNALS_DIR / "_macro/sentiment_shock.json")

    def _read_liquidity(self):
        return _read_json(SIGNALS_DIR / "_macro/liquidity_monitor.json")

    def _read_macro_snapshot(self):
        return _read_json(SIGNALS_DIR / "_macro" / "macro_snapshot.json")

    def _read_us_macro(self):
        return _read_json(SIGNALS_DIR / "_macro/us_macro_sensitivity.json")

    def _read_japan(self):
        return _read_json(SIGNALS_DIR / "_macro/japan_macro.json")

    def _read_us_etf(self):
        return _read_json(SIGNALS_DIR / "_macro/us_sector_momentum.json")

    def _read_us_stars(self):
        return _read_json(SIGNALS_DIR / "_macro/us_star_momentum.json")

    def _read_us_concepts(self):
        return _read_json(SIGNALS_DIR / "_macro/us_concept_momentum.json")

    def _read_fundamentals(self):
        return _read_json(SIGNALS_DIR / "_funds/fundamental_profile.json")

    def _read_wechat_articles(self):
        """读取各公众号最新文章的完整正文内容"""
        wechat_dir = PROJECT_ROOT / "wechat_articles"
        if not wechat_dir.exists():
            return []
        articles = []
        for src_dir in sorted(wechat_dir.iterdir()):
            if not src_dir.is_dir():
                continue
            files = sorted(src_dir.glob("*.txt"), reverse=True)
            for f in files[:3]:  # 每个源取前3篇
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                # 解析标题和正文
                title = ""
                body = text
                lines = text.split("\n", 4)
                if lines[0].startswith("标题:"):
                    title = lines[0][3:].strip()
                if len(lines) >= 5:
                    # 跳过 标题/时间/链接/分割线 四行, 取正文
                    body = lines[4].strip()
                else:
                    body = text[:2000]
                if not body:
                    continue
                articles.append({
                    "source": src_dir.name,
                    "title": title,
                    "content": body,
                    "file": f.name,
                })
        return articles


# ═══════════════════════════════════════════════
# KGMatcher — 产品→叙事链映射
# ═══════════════════════════════════════════════

class KGMatcher:
    """通过 knowledge graph 产品名子字符串匹配, 将文本映射到叙事链
    支持同义词扩展: 如 'WF6' → 六氟化钨链 """

    def __init__(self, kg_path: Path = KG_PATH):
        self.kg = _read_json(kg_path) or {}
        self.product_graph = self.kg.get("product_graph", {})
        # 预处理: 收集有效产品名
        self.products = []  # [(name_lower, chains)]
        # 用于同义词反向查找: 别名 → 规范产品名
        self._synonym_lookup = {}  # {alias_lower: canonical_lower}

        # 1. 从 KG 加载产品
        for pname, entry in self.product_graph.items():
            chains = entry.get("chains", [])
            if not chains:
                continue
            clean_name = pname.strip()
            if len(clean_name) < MIN_PRODUCT_LEN:
                continue
            if clean_name[0] in SKIP_PREFIXES and not entry.get("_supplement"):
                continue
            self.products.append((clean_name.lower(), chains))

        # 2. 从同义词表扩展
        self._build_synonyms()

    def _build_synonyms(self):
        """为同义词建立 别名→链ID 预解析

        在 __init__ 时直接查 product_graph, 找到规范名对应的链,
        存为 {alias_lower: [chain_ids]} 供 match 时快速使用.
        解决了规范名与KG内产品名不完全一致的问题 (如"碳化硅" vs "碳化硅(SiC)").
        """
        self.synonym_aliases = {}  # {alias_lower: [chain_ids]}

        # 先收集所有产品名 用于模糊匹配
        all_product_names = list(self.product_graph.keys())

        for canonical, aliases in SYNONYM_MAP.items():
            cl = canonical.strip().lower()

            # 在 product_graph 中查找规范名对应的链
            chains = None

            # 1) 精确匹配
            for pname in all_product_names:
                if pname.strip().lower() == cl:
                    chains = self.product_graph[pname].get("chains", [])
                    break

            # 2) 子串匹配: canonical 是产品名的一部分
            #    选长度差最小的 (最接近 canonical, 避免"液冷"匹配到"G工业液冷")
            if not chains:
                best_diff = float("inf")
                best_pname = None
                for pname in all_product_names:
                    pnl = pname.strip().lower()
                    if cl and cl in pnl:
                        diff = len(pnl) - len(cl)
                        if diff < best_diff:
                            best_diff = diff
                            best_pname = pname
                if best_pname:
                    chains = self.product_graph[best_pname].get("chains", [])

            # 3) 反向包容: 产品名是 canonical 的一部分
            #    选最长的产品名 (匹配最具体)
            if not chains:
                best_len = 0
                best_pname = None
                for pname in all_product_names:
                    pnl = pname.strip().lower()
                    if pnl and pnl in cl and len(pnl) > best_len:
                        best_len = len(pnl)
                        best_pname = pname
                if best_pname:
                    chains = self.product_graph[best_pname].get("chains", [])

            if not chains:
                continue  # 规范名在 KG 中无对应, 跳过

            for alias in aliases:
                al = alias.strip().lower()
                if al and len(al) >= MIN_PRODUCT_LEN:
                    already_covered = any(pnl == al for pnl, _ in self.products)
                    if not already_covered:
                        self.synonym_aliases[al] = chains

    def match(self, text: str, top_n=3) -> list:
        """返回 [(chain_id, vote, matched_products), ...]"""
        if not text or not self.products:
            return []
        text_lower = text.lower()
        votes = Counter()
        detail = defaultdict(list)

        # 直接匹配 KG 产品名
        for pname_lower, chains in self.products:
            if pname_lower in text_lower:
                for c in chains:
                    votes[c] += 1
                    if len(detail[c]) < 3:
                        detail[c].append(pname_lower)

        # 同义词匹配: 文本中别名 → 预解析链
        if self.synonym_aliases:
            for alias_lower, alias_chains in self.synonym_aliases.items():
                if alias_lower in text_lower:
                    for c in alias_chains:
                        votes[c] += 1
                        if len(detail[c]) < 3:
                            detail[c].append(f"[同义词]{alias_lower}")

        # FALLBACK_CHAINS 兜底: KG 和同义词都覆盖不到的关键词
        for keyword, fallback_chains in FALLBACK_CHAINS.items():
            if keyword.lower() in text_lower:
                for c in fallback_chains:
                    votes[c] += 1
                    if len(detail[c]) < 3:
                        detail[c].append(f"[兜底]{keyword}")

        if not votes:
            return []
        result = [(c, v, detail[c]) for c, v in votes.most_common(top_n)]
        return result

    def match_to_chain_ids(self, text: str, top_n=2) -> list:
        """返回 [chain_id, ...] 列表, 仅含匹配有效的链"""
        matches = self.match(text, top_n=top_n)
        return [c for c, _, _ in matches]


# ═══════════════════════════════════════════════
# FreshnessTracker — 去重缓存
# ═══════════════════════════════════════════════

class FreshnessTracker:
    """基于 signal pattern hash 的 7 天去重"""

    def __init__(self, cache_path: Path = FRESHNESS_CACHE):
        self.cache_path = cache_path
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._seen = self._load()
        self._changed = False

    def _load(self) -> dict:
        data = _read_json(self.cache_path)
        if data and isinstance(data, dict):
            return data.get("patterns", {})
        return {}

    def check(self, raw_text: str) -> bool:
        """True = 新鲜(未见过), False = 已存在"""
        if not raw_text:
            return False
        key = hashlib.md5(raw_text[:60].encode("utf-8")).hexdigest()
        today = _date_str()
        if key in self._seen:
            seen_date = self._seen[key]
            # 7 天过期
            try:
                seen_dt = datetime.strptime(seen_date, "%Y%m%d")
                if datetime.now() - seen_dt < timedelta(days=7):
                    self._seen[key] = today  # 刷新
                    return False
            except ValueError:
                pass
        self._seen[key] = today
        self._changed = True
        return True

    def save(self):
        if self._changed:
            self.cache_path.write_text(
                json.dumps({"patterns": self._seen}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._changed = False


# ═══════════════════════════════════════════════
# SignalExtractor — 从各源提取信号
# ═══════════════════════════════════════════════

class SignalExtractor:
    """从所有数据源提取离散信号事件, 映射到叙事链"""

    def __init__(self, date: str, kg: KGMatcher, freshness: FreshnessTracker):
        self.date = date
        self.kg = kg
        self.freshness = freshness
        self.signals = []
        self._idx = 0

    def _make_signal(self, source: str, source_label: str, raw_text: str,
                     direction: str, impact, kg_chains: list,
                     kg_match_method: str = "direct_code",
                     confidence: float = 0.6) -> dict:
        raw_clean = _clean(raw_text, 200)
        fresh = self.freshness.check(raw_clean)
        self._idx += 1
        return {
            "id": _signal_id(source, self._idx, self.date),
            "source": source,
            "source_label": source_label,
            "timestamp": self.date,
            "raw_text": raw_clean,
            "direction": direction,
            "impact": impact,
            "kg_chains": kg_chains[:5],
            "kg_match_method": kg_match_method,
            "confidence": round(confidence, 2),
            "fresh": fresh,
        }

    def extract_all(self, sources: dict) -> list:
        self.signals = []
        extractors = [
            ("shock", self._from_shocks),
            ("liquidity", self._from_liquidity),
            ("macro", self._from_macro),
            ("us_macro", self._from_us_macro),
            ("japan", self._from_japan),
            ("us_etf", self._from_us_etf),
            ("us_stars", self._from_us_stars),
            ("us_concepts", self._from_us_concepts),
            ("fundamental", self._from_fundamentals),
            ("wechat", self._from_wechat),
        ]
        for key, extractor in extractors:
            data = sources.get(key)
            if data:
                try:
                    extractor(data)
                except Exception as e:
                    print(f"  [警告] {key} 提取异常: {e}")
        return self.signals

    # ── 1. 突发事件 ──

    def _from_shocks(self, data: dict):
        net_impact = data.get("net_impact", 0)
        # 整体冲击信号
        if net_impact and abs(net_impact) > 0:
            level = data.get("impact_level", "neutral")
            sig = self._make_signal(
                source="sentiment_shock",
                source_label="消息面整体冲击",
                raw_text=f"净冲击 {net_impact} ({level})",
                direction="negative" if net_impact < 0 else "positive",
                impact=net_impact,
                kg_chains=HIGH_BETA_CHAINS if abs(net_impact) > 3 else [],
                kg_match_method="direct_code",
                confidence=min(abs(net_impact) / 10 + 0.5, 0.9),
            )
            self.signals.append(sig)

        for shock in data.get("shocks", []):
            stype = shock.get("type", "")
            sdir = shock.get("text_direction", 0)
            imp = shock.get("impact", 0)
            direction = "negative" if (imp < 0 or sdir < 0) else "positive" if (imp > 0 or sdir > 0) else "neutral"
            if imp == 0 and sdir == 0:
                direction = "neutral"

            # 预定义映射
            chains = list(SHOCK_TYPE_CHAINS.get(stype, []))
            # 辅助: KG 文本匹配扩充
            for title in shock.get("sample_titles", [])[:2]:
                kg_chains = self.kg.match_to_chain_ids(title)
                for c in kg_chains:
                    if c not in chains:
                        chains.append(c)

            sample = shock.get("sample_titles", [])
            raw = sample[0] if sample else shock.get("label", "")
            confidence = 0.7
            if shock.get("direction_confirmed"):
                confidence = 0.85
            # unique_count > count/2 说明信息密度高
            total = shock.get("count", 0)
            unique = shock.get("unique_count", 0)
            if total > 0 and unique / total > 0.5:
                confidence = min(confidence + 0.1, 0.95)

            sig = self._make_signal(
                source="sentiment_shock",
                source_label=shock.get("label", stype),
                raw_text=raw,
                direction=direction,
                impact=imp,
                kg_chains=chains,
                kg_match_method="direct_code+kg",
                confidence=confidence,
            )
            self.signals.append(sig)

    # ── 2. 流动性 ──

    def _from_liquidity(self, data: dict):
        regime = data.get("regime", "")
        pressure = data.get("pressure", 0)
        label = data.get("regime_label", "")

        # 状态信号
        is_tight = regime == "tight" or pressure > 0.5
        is_loose = regime == "loose" or pressure < -0.3
        if is_tight:
            direction = "negative"
            chains = HIGH_BETA_CHAINS
            conf = min(0.5 + pressure, 0.9)
            sig = self._make_signal(
                source="liquidity", source_label="流动性收紧",
                raw_text=f"流动性压力 {pressure} → {label}",
                direction=direction, impact=-1,
                kg_chains=chains, confidence=conf,
            )
            self.signals.append(sig)
        elif is_loose:
            sig = self._make_signal(
                source="liquidity", source_label="流动性宽松",
                raw_text=f"流动性压力 {pressure} → {label}",
                direction="positive", impact=1,
                kg_chains=HIGH_BETA_CHAINS, confidence=min(0.5 - pressure, 0.85),
            )
            self.signals.append(sig)

        # 因子级信号
        for fkey, finfo in data.get("factors", {}).items():
            score = finfo.get("score", 0)
            if score == 0 or abs(score) < 0.5:
                continue
            fdir = "positive" if score > 0 else "negative"
            flabel = finfo.get("label", fkey)
            chains_map = {
                "btc": ["narrative#37"],
                "vix": [],
                "dxy": ["narrative#42"],
                "m2": HIGH_BETA_CHAINS,
                "credit_impulse": HIGH_BETA_CHAINS,
            }
            fchains = chains_map.get(fkey, [])
            sig = self._make_signal(
                source="liquidity", source_label=f"流动性因子:{flabel}",
                raw_text=f"{flabel} score={score:.2f} latest={finfo.get('latest', '?')}",
                direction=fdir, impact=-1 if fdir == "negative" else 1,
                kg_chains=fchains, confidence=min(abs(score) * 0.3 + 0.5, 0.85),
            )
            self.signals.append(sig)

    # ── 3. 中国宏观 ──

    def _from_macro(self, data: dict):
        env = data.get("environment", "?")
        score = data.get("score", 0)
        macro = data.get("macro", {})
        parts = [f"{k}={v}" for k, v in macro.items() if isinstance(v, (int, float))]
        raw = f"宏观环境:{env} score={score} " + " | ".join(parts[:6])

        direction = "positive" if score > 0 else "negative" if score < 0 else "neutral"
        sig = self._make_signal(
            source="macro", source_label=f"中国宏观:{env}",
            raw_text=raw, direction=direction, impact=score,
            kg_chains=[], confidence=max(abs(score) * 0.1 + 0.5, 0.5),
        )
        self.signals.append(sig)

    # ── 4. US 宏观 ──

    def _from_us_macro(self, data: dict):
        env_info = data.get("environment", {})
        env_label = env_info.get("environment", "?")
        env_score = env_info.get("score", 0)
        details = env_info.get("details", {})
        latest = env_info.get("latest", {})
        parts = [f"{k}={v}" for k, v in latest.items()]
        raw = f"US宏观:{env_label} score={env_score} " + " | ".join(parts)

        direction = "negative" if env_score < 0 else "positive" if env_score > 0 else "neutral"
        sig = self._make_signal(
            source="us_macro", source_label=f"US宏观:{env_label}",
            raw_text=raw, direction=direction, impact=env_score,
            kg_chains=[], confidence=max(abs(env_score) * 0.15 + 0.5, 0.5),
        )
        self.signals.append(sig)

        # 因子级
        factor_chain_map = {
            "FEDFUNDS": HIGH_BETA_CHAINS,
            "US_CPI": HIGH_BETA_CHAINS,
            "ISM_PMI": ["narrative#25", "narrative#24"],
            "NONFARM": ["narrative#14", "narrative#21"],
        }
        for fname, fdir in details.items():
            if fdir == 0:
                continue
            fchains = factor_chain_map.get(fname, [])
            fval = latest.get(fname, "?")
            fd = "positive" if fdir > 0 else "negative"
            fconf = 0.55 + abs(fdir) * 0.15
            sig = self._make_signal(
                source="us_macro", source_label=f"US宏观:{fname}",
                raw_text=f"{fname}={fval} 方向={fd}",
                direction=fd, impact=fdir,
                kg_chains=fchains, confidence=min(fconf, 0.85),
            )
            self.signals.append(sig)

    # ── 5. 日本套息 ──

    def _from_japan(self, data: dict):
        regime = data.get("carry_regime", "?")
        pressure = data.get("carry_pressure", 0)
        boj = data.get("boj_rate", "?")
        boj_sig = data.get("boj_signal", "?")
        advice = data.get("a_share_impact", "")

        regime_dir_map = {"unwind": ("negative", -2), "building": ("negative", -1),
                          "stable": ("neutral", 0), "easing": ("positive", 1)}
        direction, impact = regime_dir_map.get(regime, ("neutral", 0))

        raw = f"日本套息:{regime} pressure={pressure:.3f} BOJ={boj}({boj_sig})"
        if advice:
            raw += f" → {_clean(advice, 60)}"

        chains = HIGH_BETA_CHAINS if regime in ("unwind", "building") else []
        conf = 0.6 + abs(pressure) * 0.5 if pressure else 0.6

        sig = self._make_signal(
            source="japan", source_label=f"日本套息:{regime}",
            raw_text=raw, direction=direction, impact=impact,
            kg_chains=chains, confidence=min(conf, 0.9),
        )
        self.signals.append(sig)

    # ── 6. US ETF 动量 ──

    def _from_us_etf(self, data: dict):
        cats = defaultdict(list)
        # 先做个股级匹配
        for etf in data.get("etfs", []):
            symbol = etf.get("symbol", "")
            cat = etf.get("category", "")
            x1 = etf.get("x1", 0)
            cats[cat].append(x1)

            # 个股级映射 (ETF 权重股或关键 ETF)
            stock_chains = STOCK_TO_CHAINS.get(symbol)
            if stock_chains and abs(x1) >= 0.5:
                direction = "positive" if x1 > 0 else "negative"
                sig = self._make_signal(
                    source="us_etf", source_label=f"US ETF:{symbol}",
                    raw_text=f"{symbol}({etf.get('name','?')}) x1={x1:.2f}",
                    direction=direction, impact=1 if x1 > 0 else -1,
                    kg_chains=stock_chains,
                    confidence=min(0.5 + abs(x1) * 0.04, 0.85),
                )
                self.signals.append(sig)

        # 类别级聚合
        for cat, x1s in cats.items():
            if not x1s or cat not in ETF_CATEGORY_CHAINS:
                continue
            avg_x1 = sum(x1s) / len(x1s)
            if abs(avg_x1) < 0.5:
                continue
            direction = "positive" if avg_x1 > 0 else "negative"
            chains = ETF_CATEGORY_CHAINS[cat]
            n_pos = sum(1 for x in x1s if x > 0)
            conf = 0.5 + abs(avg_x1) * 0.03 + (n_pos / len(x1s)) * 0.2
            sig = self._make_signal(
                source="us_etf", source_label=f"US ETF:{cat}",
                raw_text=f"{cat} avg_x1={avg_x1:.2f} ({n_pos}/{len(x1s)} positive)",
                direction=direction, impact=1 if avg_x1 > 0 else -1,
                kg_chains=chains, confidence=min(conf, 0.9),
            )
            self.signals.append(sig)

    # ── 7. US 明星股动量 ──

    def _from_us_stars(self, data: dict):
        cats = defaultdict(list)
        for stock in data.get("stocks", []):
            symbol = stock.get("symbol", "")
            cat = stock.get("category", "")
            x1 = stock.get("x1", 0)
            cats[cat].append((symbol, x1))

            # 个股级映射: 优先级 STOCK_TO_CHAINS > KG文本匹配 > 类别级
            stock_chains = list(STOCK_TO_CHAINS.get(symbol, []))
            if stock_chains and abs(x1) >= 0.5:
                direction = "positive" if x1 > 0 else "negative"
                sig = self._make_signal(
                    source="us_stars", source_label=f"US明星:{symbol}",
                    raw_text=f"{symbol}({stock.get('name','?')}) x1={x1:.2f}",
                    direction=direction, impact=1 if x1 > 0 else -1,
                    kg_chains=stock_chains,
                    confidence=min(0.5 + abs(x1) * 0.03, 0.85),
                )
                self.signals.append(sig)

        for cat, items in cats.items():
            if cat not in STAR_CATEGORY_CHAINS:
                continue
            x1s = [x for _, x in items]
            avg_x1 = sum(x1s) / len(x1s)
            if abs(avg_x1) < 0.5:
                continue
            direction = "positive" if avg_x1 > 0 else "negative"
            chains = STAR_CATEGORY_CHAINS[cat]
            n_pos = sum(1 for _, x in items if x > 0)
            # 突出的个股
            standout = max(items, key=lambda t: abs(t[1]))
            conf = 0.5 + abs(avg_x1) * 0.02 + abs(standout[1]) * 0.01
            sig = self._make_signal(
                source="us_stars", source_label=f"US明星:{cat}",
                raw_text=f"{cat} avg_x1={avg_x1:.2f} standout={standout[0]}({standout[1]:.1f})",
                direction=direction, impact=1 if avg_x1 > 0 else -1,
                kg_chains=chains, confidence=min(conf, 0.9),
            )
            self.signals.append(sig)

    # ── 8. US 概念链 ──

    def _from_us_concepts(self, data: dict):
        for chain_info in data.get("chains", []):
            cname = chain_info.get("chain", "")
            avg_x1 = chain_info.get("avg_x1", 0)
            if abs(avg_x1) < 0.5:
                continue
            direction = "positive" if avg_x1 > 0 else "negative"
            # 优先用增强版映射, 回退到旧版
            chains = STOCK_CONCEPT_CHAIN_MAP.get(cname, CONCEPT_CHAIN_MAP.get(cname, []))
            top3 = chain_info.get("top3", [])
            top_str = ",".join(f"{t.get('symbol','?')}({t.get('x1',0):.1f})" for t in top3[:2])
            conf = 0.5 + abs(avg_x1) * 0.04
            sig = self._make_signal(
                source="us_concepts", source_label=f"US概念:{cname}",
                raw_text=f"{cname} avg_x1={avg_x1:.2f} top={top_str}",
                direction=direction, impact=1 if avg_x1 > 0 else -1,
                kg_chains=chains, confidence=min(conf, 0.85),
            )
            self.signals.append(sig)

    # ── 9. 基本面因子 ──

    def _from_fundamentals(self, data: dict):
        series = data.get("factor_premium_series", [])
        if not series:
            return
        latest = series[-1]
        fm = data.get("factor_momentum", {})

        # 从 factor_premium_series 提取方向
        dir_map = {}
        # 比较最新两期
        if len(series) >= 2:
            prev = series[-2]
            for k, v in latest.items():
                if k == "window_end" or not isinstance(v, (int, float)):
                    continue
                pv = prev.get(k, 0)
                if isinstance(pv, (int, float)):
                    dir_map[k] = "positive" if v > pv else "negative" if v < pv else "neutral"

        # 热点因子
        hot = fm.get("hot_factors", "")
        hot_chains = []
        if "ROE" in hot or "营收" in hot:
            hot_chains.extend(["narrative#31", "narrative#18"])
        if "现金流" in hot:
            hot_chains.extend(["narrative#7", "narrative#21"])
        if "净利润" in hot:
            hot_chains.extend(["narrative#38", "narrative#40"])
        if hot_chains:
            sig = self._make_signal(
                source="fundamental", source_label="基本面热点因子",
                raw_text=f"热点因子: {_clean(hot, 60)}",
                direction="positive", impact=1,
                kg_chains=hot_chains, confidence=0.65,
            )
            self.signals.append(sig)

        # 各因子信号
        for fname, fdir in dir_map.items():
            if fdir == "neutral":
                continue
            val = latest.get(fname, 0)
            chains = []
            if "ROE" in fname:
                chains = ["narrative#31", "narrative#18"]
            elif "毛利率" in fname:
                chains = ["narrative#38", "narrative#40"]
            elif "营收" in fname or "净利润" in fname:
                chains = ["narrative#10", "narrative#11"]
            elif "现金流" in fname:
                chains = ["narrative#7", "narrative#21"]
            sig = self._make_signal(
                source="fundamental", source_label=f"基本面:{fname}",
                raw_text=f"{fname} {val:+.4f} 方向={fdir}",
                direction=fdir, impact=1 if fdir == "positive" else -1,
                kg_chains=chains, confidence=0.6,
            )
            self.signals.append(sig)

    # ── 10. 微信文章正文 ──

    def _from_wechat(self, data: list):
        for item in data:
            title = item.get("title", "")
            content = item.get("content", "")
            src_name = item.get("source", "")
            if not content:
                continue
            # 用正文做 KG 匹配, 比仅标题丰富得多
            chains = self.kg.match_to_chain_ids(content, top_n=5)
            if not chains:
                continue
            # 匹配到的产品越多 → 置信度越高
            confidence = min(0.5 + len(chains) * 0.06, 0.85)
            # raw_text 存标题 + 正文前 60 字
            raw = f"{title}: {_clean(content, 60)}" if title else _clean(content, 80)
            sig = self._make_signal(
                source="wechat", source_label=f"公众号:{src_name}",
                raw_text=raw,
                direction="neutral", impact=0,
                kg_chains=chains,
                kg_match_method="kg_text_match",
                confidence=confidence,
            )
            self.signals.append(sig)


# ═══════════════════════════════════════════════
# 汇总与输出
# ═══════════════════════════════════════════════

def build_chain_summary(signals: list) -> dict:
    """按叙事链汇总信号"""
    summary = {}
    chain_data = defaultdict(lambda: {"signal_count": 0, "directions": defaultdict(int),
                                       "confidences": [], "signals": []})
    for sig in signals:
        for c in sig.get("kg_chains", []):
            cd = chain_data[c]
            cd["signal_count"] += 1
            cd["directions"][sig.get("direction", "neutral")] += 1
            cd["confidences"].append(sig.get("confidence", 0.5))
            cd["signals"].append(sig["id"])

    for cid, cd in sorted(chain_data.items()):
        avg_conf = sum(cd["confidences"]) / len(cd["confidences"]) if cd["confidences"] else 0
        dirs = dict(cd["directions"])
        dominant = max(dirs, key=dirs.get) if dirs else "neutral"
        summary[cid] = {
            "chain_name": _chain_name(cid),
            "signal_count": cd["signal_count"],
            "directions": dirs,
            "avg_confidence": round(avg_conf, 2),
            "dominant_direction": dominant,
            "signal_ids": cd["signals"][:10],
        }
    # 按信号数降序
    return dict(sorted(summary.items(), key=lambda x: -x[1]["signal_count"]))


def build_markdown(date: str, signals: list, chain_summary: dict, stats: dict) -> str:
    """生成人类可读的信号摘要"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# 信号事件流 {date}", f"生成: {now}", ""]

    # 统计头
    lines.append(f"**总信号**: {stats['total_signals']} | 新鲜: {stats['fresh_events']} | "
                 f"重复: {stats['repeat_events']} | 激活链: {stats['chains_activated']}")
    lines.append(f"**数据源**: {stats['sources_loaded']} 个加载")
    lines.append("")

    # 链汇总
    lines.append("## 叙事链信号汇总")
    lines.append("")
    lines.append(f"{'链ID':<15} {'名称':<14} {'信号数':>6} {'主导方向':<10} {'置信度':>6}")
    lines.append("-" * 55)
    for cid, info in chain_summary.items():
        bar = "■" * min(info["signal_count"], 10) + "□" * max(0, 10 - min(info["signal_count"], 10))
        lines.append(f"{cid:<15} {info['chain_name']:<14} {info['signal_count']:>6} "
                     f"{info['dominant_direction']:<10} {info['avg_confidence']:.2f} {bar}")
    lines.append("")

    # 各源信号明细
    lines.append("## 信号明细")
    lines.append("")
    # 按源分组
    by_source = defaultdict(list)
    for sig in signals:
        by_source[sig["source"]].append(sig)

    source_labels = {
        "sentiment_shock": "突发事件", "liquidity": "流动性", "macro": "中国宏观",
        "us_macro": "US宏观", "japan": "日本套息", "us_etf": "US ETF动量",
        "us_stars": "US明星股", "us_concepts": "US概念链", "fundamental": "基本面",
        "wechat": "公众号",
    }

    for sname, sigs in sorted(by_source.items()):
        label = source_labels.get(sname, sname)
        fresh_n = sum(1 for s in sigs if s.get("fresh"))
        lines.append(f"### {label} ({len(sigs)}条, {fresh_n}新鲜)")
        lines.append("")
        for s in sigs:
            chains_str = ", ".join(f"{_chain_name(c)}" for c in s.get("kg_chains", [])[:3])
            fresh_mark = "★" if s.get("fresh") else " "
            lines.append(f"- [{fresh_mark}] {s['source_label']} | {s['direction']:>8} "
                         f"(conf={s['confidence']:.2f}) | {chains_str}")
            lines.append(f"  {_clean(s['raw_text'], 80)}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════

def main():
    import sys as _sys
    if _sys.platform == "win32" and hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8")
        _sys.stderr.reconfigure(encoding="utf-8")

    import argparse
    parser = argparse.ArgumentParser(description="信号事件流提取器")
    parser.add_argument("--date", type=str, default=_date_str(), help="日期 YYYYMMDD")
    parser.add_argument("--no-cache", action="store_true", help="跳过去重缓存")
    parser.add_argument("--incremental", action="store_true", help="增量模式: 跳过已有JSON源, 只处理新增公众号文章")
    parser.add_argument("--full-history", action="store_true", help="回填所有可用日期")
    args = parser.parse_args()

    date = args.date

    # 初始化
    kg = KGMatcher()
    freshness = FreshnessTracker()
    if args.no_cache:
        freshness.check = lambda _txt: True
        freshness.save = lambda: None

    print(f"信号事件流提取 — {date}")
    print(f"  KG: {KG_PATH} ({len(kg.products)} 产品节点 + {len(kg.synonym_aliases)} 同义词别名)")
    print()

    if args.full_history:
        sources_dir = PROJECT_ROOT / "reports" / "sources"
        md_files = sorted(sources_dir.glob("*_sources.md"), reverse=True)
        print(f"  回填模式: 发现 {len(md_files)} 个源报告")
        for mf in md_files:
            fdate = mf.stem[:8] if len(mf.stem) >= 8 else "unknown"
            if fdate == "unknown":
                continue
            print(f"\n  处理 {fdate}...")
            _run_single(fdate, kg, freshness, incremental=False)
        freshness.save()
        print(f"\n回填完成, 共处理 {len(md_files)} 天")
        return

    result = _run_single(date, kg, freshness, incremental=args.incremental)
    freshness.save()

    if result:
        sig_count = len(result["signals"])
        chain_count = len(result["chain_summary"])
        fresh_count = sum(1 for s in result["signals"] if s.get("fresh"))
        repeat_count = sig_count - fresh_count
        mode = "增量" if args.incremental else "全量"
        print(f"\n  [OK] 输出: {result['json_path']}")
        print(f"  [OK] Markdown: {result['md_path']}")
        print(f"  [OK] 信号: {sig_count} 条 ({mode}, 新鲜 {fresh_count}, 重复 {repeat_count})")
        print(f"  [OK] 激活链: {chain_count} 条")
        print(f"  [OK] 数据源: {result['stats']['sources_loaded']} 个")


def _run_single(date: str, kg: KGMatcher, freshness: FreshnessTracker,
                 incremental: bool = False) -> dict:
    """运行单日信号提取

    incremental=True: 跳过JSON数据源重读, 仅处理公众号新增文章,
                      与已有信号合并后重新输出.
    """
    # 检查是否已有输出
    existing_path = OUTPUT_DIR / f"{date}_signals.json"
    existing_signals = []
    if incremental and existing_path.exists():
        old_data = _read_json(existing_path)
        if old_data and old_data.get("signals"):
            existing_signals = old_data["signals"]
            print(f"  已有 {len(existing_signals)} 条信号, 增量模式仅处理公众号文章")

    # 1. 读取源
    reader = DataSourceReader(date)
    sources = reader.read_all()

    if incremental:
        # 增量模式: 只保留 wechat 源, 其余用已有信号
        sources = {"wechat": sources.get("wechat", [])}
        sources_loaded = 1 if sources["wechat"] else 0
    else:
        sources_loaded = sum(1 for v in sources.values() if v)

    print(f"  数据源: {sources_loaded}/10 个加载")

    # 2. 提取信号
    extractor = SignalExtractor(date, kg, freshness)
    new_signals = extractor.extract_all(sources)

    if incremental:
        # 合并: 去重 (按 signal id)
        existing_ids = {s["id"] for s in existing_signals}
        merged = list(existing_signals)
        for s in new_signals:
            if s["id"] not in existing_ids:
                merged.append(s)
        signals = merged
        print(f"  合并: 原有 {len(existing_signals)} + 新增({len(new_signals)}) = {len(signals)} 条")
    else:
        signals = new_signals

    if not signals:
        print("  无信号产出")
        return {}

    # 3. 构建汇总
    chain_summary = build_chain_summary(signals)
    fresh_count = sum(1 for s in signals if s.get("fresh"))

    stats = {
        "total_signals": len(signals),
        "sources_loaded": sources_loaded,
        "chains_activated": len(chain_summary),
        "fresh_events": fresh_count,
        "repeat_events": len(signals) - fresh_count,
    }

    output = {
        "date": date,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "statistics": stats,
        "signals": signals,
        "chain_summary": chain_summary,
        "source_report": f"reports/sources/{date}_sources.md",
    }

    # 4. 输出
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = OUTPUT_DIR / f"{date}_signals.json"
    json_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md_content = build_markdown(date, signals, chain_summary, stats)
    md_path = OUTPUT_DIR / f"{date}_signals.md"
    md_path.write_text(md_content, encoding="utf-8")

    return {
        "json_path": json_path,
        "md_path": md_path,
        "signals": signals,
        "chain_summary": chain_summary,
        "stats": stats,
    }


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tools/ima_foreign_views.py — IMA 知识库外资研报观点抽取

每天早上自动搜索"实时外资研报"+"机构调研纪要"两个知识库，
提取最新外资行报告标题，按机构+板块分类，
更新 narratives/foreign_views/_index.md + 生成当日快照。

用法:
  python tools/ima_foreign_views.py              # 搜索并更新
  python tools/ima_foreign_views.py --dry-run    # 预览不写文件
  python tools/ima_foreign_views.py --status     # 看上次更新状态

集成:
  加入 run_daily.bat 的 US 市场段之后:
    python tools/ima_foreign_views.py
"""
import sys
import os
import json
import re
from datetime import datetime, date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# ─── 知识库 ID ───────────────────────────────────────────────
KB_REALTIME = "SXW2ebSVvSslytuU-NLb8yqN2kYCbwKHJBJnUo6NwmQ="  # 实时外资研报(14.7K)
KB_RESEARCH = "vo-DpN2WWW53uy1dZlbD15dI0DLkaZzIpeEXDC7Seno="   # 机构调研纪要(31.6K)

KB_LABELS = {
    KB_REALTIME: "实时外资研报",
    KB_RESEARCH: "机构调研纪要",
}

# ─── 搜索策略 ────────────────────────────────────────────────
def _build_realtime_queries() -> list[str]:
    """构建实时外资研报搜索词，日期部分自动跟随当前月份"""
    now = datetime.now()
    month_num = now.strftime("%Y%m")     # "202606"
    month_eng = now.strftime("%B %Y")    # "June 2026"
    return [
        "Goldman Sachs",
        "Morgan Stanley",
        "JPMorgan",
        "UBS",
        "Citigroup",
        month_num,     # "202606"  (标题常带 YYYYMM)
        month_eng,     # "June 2026"
    ]

def _build_research_queries() -> list[str]:
    """构建机构调研纪要搜索词"""
    now = datetime.now()
    month_cn = f"{now.month}月"  # "6月"
    return [
        "专家交流",
        "调研纪要",
        "行业深度",
        month_cn,
    ]

# 机构名 → 标题关键词映射（中英文，用于分类识别）
TITLE_BANK_KEYWORDS = {
    "gs_goldman_sachs": ["高盛", "goldman sachs", "goldman"],
    "ms_morgan_stanley": ["摩根士丹利", "大摩", "morgan stanley"],
    "jpm_jp_morgan": ["摩根大通", "小摩", "jpmorgan", "jp morgan"],
    "ubs": ["瑞银", "ubs"],
    "citi": ["花旗", "citigroup", "citi"],
}

BANK_LABELS = {
    "gs_goldman_sachs": "高盛",
    "ms_morgan_stanley": "摩根士丹利",
    "jpm_jp_morgan": "摩根大通",
    "ubs": "瑞银",
    "citi": "花旗",
}

# ─── 板块分类关键词（中英文） ─────────────────────────────────
SECTOR_KEYWORDS = {
    "宏观/策略": [
        "宏观", "GDP", "CPI", "利率", "汇率", "人民币", "美联储",
        "降息", "加息", "通胀", "策略", "展望", "年度", "下半年",
        "投资策略", "全球经济", "市场展望",
        "Macro", "Outlook", "Strategy", "Global", "Economic",
        "Interest Rate", "Inflation", "Fed", "Policy",
    ],
    "半导体/AI": [
        "半导体", "AI", "芯片", "算力", "GPU", "ASIC", "HBM",
        "存储", "DRAM", "NAND", "光模块", "封装", "COWOS",
        "人工智能", "大模型", "算力基础设施",
        "Semiconductor", "Semicon", "AI", "GPU", "HBM",
        "DRAM", "NAND", "ASIC", "Chip", "Compute",
        "Data Center", "Cloud", "Server",
    ],
    "医药/创新药": [
        "医药", "创新药", "医疗", "CXO", "Biotech",
        "制药", "生物医药", "医疗器械", "医保",
        "Pharma", "Biotech", "Healthcare", "Drug",
        "Medical", "Therapeutics", "Clinical",
    ],
    "消费": [
        "消费", "白酒", "茅台", "五粮液", "零售", "电商",
        "旅游", "食品", "免税", "医美", "教育", "游戏",
        "Consumer", "Retail", "E-commerce", "Luxury",
        "Food", "Beverage", "Travel", "Gaming",
    ],
    "新能源/锂电": [
        "光伏", "锂电", "新能源", "风电", "储能", "电池",
        "电动车", "新能源汽车", "绿电", "氢能",
        "Solar", "Lithium", "Battery", "EV", "New Energy",
        "Wind", "Energy Storage", "Renewable",
    ],
    "互联网/平台": [
        "腾讯", "阿里", "美团", "拼多多", "互联网", "平台",
        "字节", "快手", "百度", "电商平台",
        "Tencent", "Alibaba", "Meituan", "Baidu", "Pinduoduo",
        "Internet", "Platform", "E-commerce",
    ],
    "金融/银行": [
        "银行", "保险", "券商", "金融", "信贷", "证券",
        "财富管理", "资本",
        "Bank", "Insurance", "Financial", "Credit",
        "Wealth", "Capital Market",
    ],
    "地产链": [
        "地产", "房地产", "基建", "建材", "钢铁", "水泥",
        "建筑", "装修",
        "Property", "Real Estate", "Infrastructure",
        "Construction", "Steel",
    ],
    "汽车/出行": [
        "汽车", "新能源车", "智能驾驶", "自动驾驶", "出行",
        "整车", "零部件", "汽配",
        "Auto", "Automotive", "EV", "Autonomous Driving",
        "Vehicle", "Parts",
    ],
    "周期/大宗": [
        "大宗", "原油", "石油", "天然气", "煤炭", "铜",
        "铝", "锂", "稀土", "周期", "化工",
        "Commodity", "Oil", "Gas", "Copper", "Aluminum",
        "Lithium", "Chemical", "Metal", "Mining",
    ],
}

# 板块 emoji 映射
SECTOR_EMOJI = {
    "宏观/策略": "",  # 无 emoji
    "半导体/AI": "🟢",
    "医药/创新药": "🔴",
    "消费": "🟡",
    "新能源/锂电": "🟡",
    "互联网/平台": "🔵",
    "金融/银行": "🟡",
    "地产链": "⚪",
    "汽车/出行": "🔵",
    "周期/大宗": "🟤",
    "其他/未分类": "⚪",
}

SECTOR_ORDER = [
    "宏观/策略", "半导体/AI", "医药/创新药", "消费",
    "新能源/锂电", "互联网/平台", "金融/银行",
    "地产链", "汽车/出行", "周期/大宗", "其他/未分类",
]

# 缓存路径
CACHE_DIR = Path("signals/tracking/_macro")
CACHE_FILE = CACHE_DIR / "ima_foreign_views_cache.json"

# narratives 路径
NARRATIVES_DIR = Path("narratives/foreign_views")
INDEX_FILE = NARRATIVES_DIR / "_index.md"
DAILY_DIR = NARRATIVES_DIR / "daily"


# ═══════════════════════════════════════════════════════════════
#  核心逻辑
# ═══════════════════════════════════════════════════════════════

def classify_sector(title: str) -> str:
    """根据标题关键词分类到板块"""
    title_lower = title.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in title_lower:
                return sector
    return "其他/未分类"


def extract_bank_from_title(title: str) -> str | None:
    """从标题反推机构名（中英文关键词匹配）"""
    title_lower = title.lower()
    for key, keywords in TITLE_BANK_KEYWORDS.items():
        for kw in keywords:
            if kw in title_lower:
                return key
    return None


def search_kb(kb, kb_id: str, queries: list[str], limit: int = 10):
    """搜索单个知识库，返回 {query: [results]}"""
    out = {}
    for q in queries:
        try:
            results = kb.search_knowledge(kb_id, q, limit=limit)
            out[q] = results if results else []
        except Exception as e:
            err = str(e)
            # 220021 = 限流
            if "220021" in err or "次数" in err:
                out[q] = {"error": "rate_limit", "msg": str(e)}
            else:
                out[q] = {"error": "other", "msg": str(e)}
    return out


def process_results(raw: dict) -> list[dict]:
    """将原始搜索结果转为统一条目列表"""
    entries = []
    for query, results in raw.items():
        if not isinstance(results, list):
            continue  # 跳过错误条目
        for r in results:
            if not isinstance(r, dict):
                continue
            title = (r.get("title") or "").strip()
            if not title:
                continue
            # 去后缀
            title_clean = re.sub(r"\.(pdf|docx?|pptx?|xlsx?)$", "", title, flags=re.I).strip()
            entries.append({
                "title": title_clean,
                "media_id": r.get("media_id", ""),
                "media_type": r.get("media_type"),
                "matched_query": query,
                "bank": extract_bank_from_title(title_clean) or "unknown",
                "sector": classify_sector(title_clean),
            })
    return entries


def dedup_entries(entries: list[dict]) -> list[dict]:
    """基于 title 去重（保留第一个出现的）"""
    seen = set()
    out = []
    for e in entries:
        key = e["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


# ═══════════════════════════════════════════════════════════════
#  Markdown 输出
# ═══════════════════════════════════════════════════════════════

def render_daily_snapshot(entries: list[dict], today: str) -> str:
    """生成当日快照 Markdown"""
    lines = [
        f"# 外资研报当日快照 — {today}",
        "",
        f"> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 数据来源：IMA 实时外资研报 + 机构调研纪要",
        f"> 注意：仅可获取报告标题，无法阅读全文",
        "",
        "---",
        "",
    ]

    # 按机构分组
    by_bank: dict[str, list] = {}
    for e in entries:
        by_bank.setdefault(e["bank"], []).append(e)

    for bank_key in ["gs_goldman_sachs", "ms_morgan_stanley", "jpm_jp_morgan", "ubs", "citi"]:
        bank_entries = by_bank.get(bank_key, [])
        label = BANK_LABELS.get(bank_key, bank_key)
        lines.append(f"## {label}")
        lines.append("")
        if not bank_entries:
            lines.append("（今日无新报告标题匹配）")
            lines.append("")
            continue
        for e in bank_entries:
            sector_tag = f"`{e['sector']}`"
            lines.append(f"- {e['title']}  — {sector_tag}")
        lines.append("")

    # 按板块汇总
    lines.extend(["---", "", "## 板块覆盖热度", "", "| 板块 | 报告数 |", "|---|---|"])
    by_sector: dict[str, int] = {}
    for e in entries:
        by_sector[e["sector"]] = by_sector.get(e["sector"], 0) + 1
    for sec in SECTOR_ORDER:
        cnt = by_sector.get(sec, 0)
        if cnt:
            emoji = SECTOR_EMOJI.get(sec, "")
            lines.append(f"| {emoji} {sec} | {cnt} |")
    lines.append("")

    return "\n".join(lines)


def update_index_md(entries: list[dict], today: str):
    """向 _index.md 追加今日新观点"""
    if not os.path.exists(INDEX_FILE):
        print(f"[ima] _index.md not found at {INDEX_FILE}, skipping")
        return

    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 按板块分组
    by_sector: dict[str, list] = {}
    for e in entries:
        by_sector.setdefault(e["sector"], []).append(e)

    new_rows_added = 0
    for sec in SECTOR_ORDER:
        sec_entries = by_sector.get(sec, [])
        if not sec_entries:
            continue

        # 查找板块表格位置
        # 格式: ### emoji 板块名  或  ### 板块名
        pattern = None
        emoji = SECTOR_EMOJI.get(sec, "")
        if emoji:
            pattern = f"### {emoji} {sec}"
        else:
            pattern = f"### {sec}"

        # 尝试带 emoji 版本，再试无 emoji
        section_marker = f"### {emoji} {sec}" if emoji else f"### {sec}"
        alt_marker = f"### {sec}"

        pos = content.find(section_marker)
        if pos == -1 and section_marker != alt_marker:
            pos = content.find(alt_marker)

        if pos == -1:
            continue  # 找不到板块区块

        # 找到表格区域（### 后面的第一个表格行）
        # 简单策略：在 ### 后找 |---| 行
        after_section = content[pos:]
        table_start = after_section.find("|---|")
        if table_start == -1:
            continue

        # 表格头之后的第一行数据之后就是插入点
        table_body_start = after_section.find("\n", table_start + 5)
        if table_body_start == -1:
            continue

        # 找到第一个空行之后（表格数据行末尾）
        insert_rel = table_body_start + 1
        # 跳到第一个非空行后的空行
        lines_after = after_section[insert_rel:].split("\n")
        insert_offset = 0
        for line in lines_after:
            if line.strip() == "":
                break
            insert_offset += len(line) + 1
        else:
            # 没找到空行，追加到末尾
            insert_offset = len(lines_after)

        insert_abs = pos + insert_rel + insert_offset

        # 构建新行
        new_rows = []
        for e in sec_entries:
            bank_label = BANK_LABELS.get(e["bank"], e["bank"])
            # 从标题提取简短观点摘要（去掉机构名前缀，中英文）
            title_short = e["title"]
            # 收集该机构的所有已知名（中文+英文关键词）
            known_names = [bank_label] + TITLE_BANK_KEYWORDS.get(e["bank"], [])
            for name in sorted(known_names, key=len, reverse=True):
                for sep in [" - ", "：", ":", " | ", "——", "·", " "]:
                    prefix = f"{name}{sep}"
                    if title_short.lower().startswith(prefix.lower()):
                        title_short = title_short[len(prefix):].strip()
                        break
                else:
                    continue
                break
            # 也处理 【高盛】 格式
            for name in known_names:
                if title_short.startswith(f"【{name}】"):
                    title_short = title_short[len(f"【{name}】"):].strip()
                    break
            title_short = title_short[:60]  # 截断

            # 来源标记
            source = f"IMA/{KB_LABELS.get(KB_REALTIME, '实时外资研报')}"

            new_rows.append(
                f"| {today} | {bank_label} | {title_short} | → 待确认 | {source} |"
            )

        if new_rows:
            insert_text = "\n" + "\n".join(new_rows) + "\n"
            content = content[:insert_abs] + insert_text + content[insert_abs:]
            new_rows_added += len(new_rows)

    # 更新生成日期
    today_header = f"> 生成日期：{today}"
    content = re.sub(
        r"> 生成日期：\d{4}-\d{2}-\d{2}",
        today_header,
        content,
    )

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[ima] _index.md 更新完毕，新增 {new_rows_added} 行")


# ═══════════════════════════════════════════════════════════════
#  缓存管理
# ═══════════════════════════════════════════════════════════════

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {"last_run": None, "last_date": None, "entries_count": 0, "history": []}


def save_cache(data: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def cmd_search(dry_run: bool = False):
    """执行搜索并更新"""
    today = date.today().isoformat()
    print(f"[ima] === 外资研报观点抽取 ({today}) ===")

    # 1. 搜索
    from ima_mcp.knowledge_base import KnowledgeBase
    kb = KnowledgeBase()

    all_entries = []

    # 实时外资研报 — 按英文行名 + 日期扫描
    realtime_queries = _build_realtime_queries()
    print(f"[ima] 搜索 {KB_LABELS[KB_REALTIME]} ({len(realtime_queries)} 个查询)...")
    realtime_raw = search_kb(kb, KB_REALTIME, realtime_queries, limit=10)
    realtime_entries = process_results({q: r for q, r in realtime_raw.items() if isinstance(r, list)})
    all_entries.extend(realtime_entries)

    # 检查限流
    for q, r in realtime_raw.items():
        if isinstance(r, dict) and r.get("error") == "rate_limit":
            print(f"[ima] ⚠️  限流，跳过剩余搜索")
            break

    # 机构调研纪要 — 按关键词搜
    research_queries = _build_research_queries()
    print(f"[ima] 搜索 {KB_LABELS[KB_RESEARCH]} ({len(research_queries)} 个查询)...")
    research_raw = search_kb(kb, KB_RESEARCH, research_queries, limit=10)
    research_entries = process_results({q: r for q, r in research_raw.items() if isinstance(r, list)})
    all_entries.extend(research_entries)

    all_entries = dedup_entries(all_entries)

    print(f"[ima] 共获取 {len(all_entries)} 条唯一报告")

    if dry_run:
        print(f"\n[ima] 🔍 DRY RUN — 不写文件\n")
        print(render_daily_snapshot(all_entries, today))
        # 展示板块分布
        print("\n## 板块分布")
        by_sector = {}
        for e in all_entries:
            by_sector[e["sector"]] = by_sector.get(e["sector"], 0) + 1
        for sec in SECTOR_ORDER:
            if by_sector.get(sec):
                print(f"  {SECTOR_EMOJI.get(sec,'')} {sec}: {by_sector[sec]}")
        return

    # 2. 写当日快照
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    daily_file = DAILY_DIR / f"{today}.md"
    daily_content = render_daily_snapshot(all_entries, today)
    daily_file.write_text(daily_content, encoding="utf-8")
    print(f"[ima] 当日快照 → {daily_file}")

    # 3. 更新 _index.md
    if entries := [e for e in all_entries if e["sector"] != "其他/未分类"]:
        update_index_md(entries, today)
    else:
        print(f"[ima] 无有效板块分类条目，跳过 _index.md 更新")

    # 4. 更新缓存
    cache = load_cache()
    cache["last_run"] = datetime.now().isoformat()
    cache["last_date"] = today
    cache["entries_count"] = len(all_entries)
    cache["history"].append({
        "date": today,
        "count": len(all_entries),
        "sectors": {s: c for s, c in
                     {sec: sum(1 for e in all_entries if e["sector"] == sec)
                      for sec in SECTOR_ORDER}.items()
                     if c > 0},
    })
    # 只保留最近30天
    cache["history"] = cache["history"][-30:]
    save_cache(cache)

    print(f"[ima] ✅ 完成 — {len(all_entries)} 条报告，{len([e for e in all_entries if e['sector'] != '其他/未分类'])} 条已分类")


def cmd_status():
    """查看上次更新状态"""
    cache = load_cache()
    if not cache.get("last_run"):
        print("[ima] 尚未运行过")
        return

    print(f"[ima] 上次更新: {cache.get('last_run')}")
    print(f"[ima] 日期: {cache.get('last_date')}")
    print(f"[ima] 报告数: {cache.get('entries_count')}")
    print(f"\n最近更新历史:")
    for h in cache.get("history", [])[-7:]:
        sectors_str = " | ".join(f"{s}:{c}" for s, c in h.get("sectors", {}).items())
        print(f"  {h['date']} — {h['count']} 条 ({sectors_str})")

    # 检查今日快照
    today = date.today().isoformat()
    daily_path = DAILY_DIR / f"{today}.md"
    if daily_path.exists():
        print(f"\n今日快照: ✅ {daily_path}")
    else:
        print(f"\n今日快照: ❌ 未生成，需运行 python tools/ima_foreign_views.py")


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--dry-run":
            cmd_search(dry_run=True)
            return
        elif cmd == "--status":
            cmd_status()
            return
        elif cmd == "--help":
            print(__doc__)
            return

    cmd_search(dry_run=False)


if __name__ == "__main__":
    main()

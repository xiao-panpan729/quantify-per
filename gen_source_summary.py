# -*- coding: utf-8 -*-
"""
信源聚合摘要生成器 — 读取 update_sources.bat 拉取的全部数据，生成结构化报告。
Usage:
  python gen_source_summary.py          # 纯数据摘要
  python gen_source_summary.py --ai     # 摘要 + AI分析（NVIDIA V4 Flash）
"""
import sys, json, os
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.resolve()
SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking"
WECHAT_DIR = PROJECT_ROOT / "wechat_articles"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "sources"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Force UTF-8 on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

today = datetime.now().strftime("%Y%m%d")
today_dt = datetime.now()
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
output_path = OUTPUT_DIR / f"{today}_sources.md"

# ─── 文章最大天数（超过此天数的旧文章不显示） ───
MAX_ARTICLE_AGE_DAYS = 5


def _data_source_status() -> list:
    """数据源完整性自检报告 — 每个源：名称/状态/条数/备注"""
    status = []
    # 公众号
    wechat_count = 0
    if WECHAT_DIR.exists():
        for d in WECHAT_DIR.iterdir():
            if d.is_dir():
                wechat_count += len(list(d.glob("*.txt")))
    wc_status = "✅" if wechat_count > 0 else "❌"
    wc_note = "" if wechat_count > 0 else "MPTEXT API 可能过期"
    status.append(("公众号文章", wc_status, str(wechat_count) if wechat_count > 0 else "0", wc_note))
    # JSON 数据源
    json_checks = [
        ("消息面冲击", "_macro/sentiment_shock.json", "shocks"),
        ("全球流动性", "_macro/liquidity_monitor.json", "pressure"),
        ("中国宏观快照", "_macro/macro_snapshot.json", "environment"),
        ("US宏观环境", "_macro/us_macro_sensitivity.json", "environment"),
        ("日本宏观+套息", "_macro/japan_macro.json", "carry_pressure"),
        ("US ETF动量", "_macro/us_sector_momentum.json", "etfs"),
        ("US明星股动量", "_macro/us_star_momentum.json", "stocks"),
        ("概念链轮动", "_macro/us_concept_momentum.json", "chains"),
        ("基本面因子", "_funds/fundamental_profile.json", "factor_premium_series"),
    ]
    for name, fname, key in json_checks:
        fp = SIGNALS_DIR / fname
        if not fp.exists():
            status.append((name, "❌", "文件不存在", ""))
            continue
        data = _read_json(fp)
        if data is None:
            status.append((name, "❌", "读取失败", ""))
            continue
        if isinstance(data, dict):
            val = data.get(key)
            if val in (None, [], {}, ""):
                status.append((name, "⚠️", "空数据", "API可能异常"))
            else:
                status.append((name, "✅", "有数据", ""))
        elif isinstance(data, list):
            status.append((name, "✅", f"{len(data)}条", ""))
        else:
            status.append((name, "⚠️", "格式异常", ""))
    return status


def _read_json(path):
    """安全读取JSON，失败返回None"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _clean(s, maxlen=80):
    """清理乱码文本"""
    if not s:
        return ""
    s = str(s).replace("�", "").replace("\n", " ").strip()
    return s[:maxlen] + ("..." if len(s) > maxlen else "")


def _format_article_listing(wechat_dir: Path, today_dt: datetime) -> list:
    """生成微信公众号最新观点区块 — 排除样式参考号 + 日期过滤 + 只显示最近5天"""
    lines = []
    lines.append("---")
    lines.append("")
    lines.append("## 📰 微信公众号最新观点")
    lines.append("")
    if not wechat_dir.exists():
        lines.append("（wechat_articles 目录不存在）")
        lines.append("")
        return lines

    # 排除已从当前管道移除的信源（eg. 一思一记/盘前/盘前纪要/安静拆主线）
    excluded = {'一思一记', '盘前', '盘前纪要', '安静拆主线', '中信建投证券研究'}
    src_dirs = sorted(d for d in wechat_dir.iterdir() if d.is_dir() and d.name not in excluded)
    any_article = False

    def _format_one_dir(account_dirs: list):
        nonlocal any_article
        for src_dir in account_dirs:
            files = sorted(src_dir.glob("*.txt"), reverse=True)
            recent = []
            for f in files:
                stem = f.stem
                if len(stem) >= 8:
                    try:
                        f_date = datetime.strptime(stem[:8], "%Y%m%d")
                        if (today_dt - f_date).days <= MAX_ARTICLE_AGE_DAYS:
                            recent.append(f)
                    except ValueError:
                        pass
                if len(recent) >= 2:
                    break
            if not recent:
                continue
            any_article = True
            latest = recent[0]
            stem = latest.stem
            date_part = stem[:8] if len(stem) >= 8 else ""
            title = _clean(stem[9:].replace("_", " ") if len(stem) > 9 else stem, 60)
            dt = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}" if date_part else "?"
            name_display = src_dir.name
            lines.append(f"- **{name_display}** ({dt}): {title}")
            if len(recent) > 1:
                s2 = recent[1].stem
                t2 = _clean(s2[9:].replace("_", " ") if len(s2) > 9 else s2, 50)
                lines.append(f"  └ {t2}")

    _format_one_dir(src_dirs)

    if not any_article:
        lines.append("（最近5天无更新）")
    lines.append("")
    return lines


# ═══════════════════════════════════════════
# 摘要构建
# ═══════════════════════════════════════════

blocks = []
blocks.append(f"# 信源聚合报告 {now_str}")
blocks.append("")

# ─── 1. 消息面冲击 ───
blocks.append("---")
blocks.append("")
blocks.append("## 🔴 消息面要点")
blocks.append("")
shock = _read_json(SIGNALS_DIR / "_macro/sentiment_shock.json")
if shock:
    level = shock.get("impact_level", "?")
    net = shock.get("net_impact", "?")
    icons = {"negative": "🔴", "positive": "🟢", "neutral": "🟡"}
    blocks.append(f"**净冲击**: {net} {icons.get(level, '')}({level})")
    sources = shock.get("sources", {})
    if sources:
        src_str = " | ".join(f"{k}={v}" for k, v in sources.items())
        blocks.append(f"**信源**: {src_str}")
    # 排序列5条最重要的（按impact绝对值降序 → count降序）
    shocks = shock.get("shocks", [])
    shocks_sorted = sorted(
        shocks,
        key=lambda x: (abs(x.get("impact", 0)), x.get("count", 0)),
        reverse=True,
    )[:5]
    for s in shocks_sorted:
        label = _clean(s.get("label", "?"), 30)
        cnt = s.get("count", 0)
        imp = s.get("impact", "?")
        samples = s.get("sample_titles", [])[:2]
        blocks.append(f"\n**{label}** ({cnt}条, impact={imp})")
        for t in samples:
            blocks.append(f"> {_clean(t, 120)}")
else:
    blocks.append("（无数据，请先运行 shock_detector.py）")
blocks.append("")

# ─── 3. 全球宏观环境一览（合并：流动性 + 中国 + US + 日本） ───
blocks.append("---")
blocks.append("")
blocks.append("## 🌍 全球宏观环境一览")
blocks.append("")
blocks.append("| 地区 | 指标 | 当前值 | 信号/趋势 |")
blocks.append("|------|------|--------|-----------|")

liq = _read_json(SIGNALS_DIR / "_macro/liquidity_monitor.json")
if liq:
    p = liq.get("pressure", "?")
    regime = liq.get("regime", "?")
    blocks.append(f"| 🌍 全球 | 流动性压力 | {p} | {regime} |")
    for k, v in liq.get("factors", {}).items():
        lbl = v.get("label", k)
        lat = v.get("latest", v.get("raw", "?"))
        score = v.get("score", "?")
        if isinstance(lat, float):
            blocks.append(f"| | {lbl} | {lat:.2f} | score={score} |")
        else:
            blocks.append(f"| | {lbl} | {lat} | score={score} |")
else:
    blocks.append("| 🌍 全球 | （无数据） | - | - |")

# 中国宏观 — 从 macro_sensitivity.json 读取
cn_macro = _read_json(SIGNALS_DIR / "_macro/macro_sensitivity.json")
if cn_macro:
    env = cn_macro.get("environment", "?")
    if isinstance(env, dict):
        env_name = env.get("environment", env.get("regime", "?"))
        env_score = env.get("score", "?")
    else:
        env_name = str(env)
        env_score = cn_macro.get("score", "?")
    blocks.append(f"| 🇨🇳 中国 | 宏观环境 | {_clean(env_name, 15)} | 评分={env_score} |")
    # 关键宏观数据
    macro_data = cn_macro.get("macro", {})
    for k in ["M2", "CPI", "PMI", "SHIBOR"]:
        if k in macro_data:
            v = macro_data[k]
            blocks.append(f"| | {k} | {v} | - |")
else:
    blocks.append("| 🇨🇳 中国 | （无数据） | - | - |")

# US宏观
usm = _read_json(SIGNALS_DIR / "_macro/us_macro_sensitivity.json")
US_FIELD_CN = {
    "FEDFUNDS": "联邦基金利率(%)", "US_CPI": "CPI同比(%)",
    "ISM_PMI": "ISM制造业PMI", "NONFARM": "非农就业(万)",
    "US_GDP": "GDP增速(%)", "US_PCE": "核心PCE(%)"
}
if usm:
    env = usm.get("environment", {})
    env_name = env.get("environment", "?") if isinstance(env, dict) else "?"
    blocks.append(f"| 🇺🇸 美国 | 宏观环境 | {_clean(env_name, 15)} | 评分={env.get('score', '?')} |")
    lat = env.get("latest", {}) if isinstance(env, dict) else {}
    if lat:
        for k, v in lat.items():
            cn_name = US_FIELD_CN.get(k, k)
            blocks.append(f"| | {cn_name} | {v} | - |")
else:
    blocks.append("| 🇺🇸 美国 | （无数据） | - | - |")

# 日本宏观
jap = _read_json(SIGNALS_DIR / "_macro/japan_macro.json")
if jap:
    boj = jap.get("boj_rate", "?")
    boj_sig = jap.get("boj_signal", "?")
    blocks.append(f"| 🇯🇵 日本 | 央行政策利率 | {boj} | {boj_sig} |")
    cpi = jap.get("japan_cpi", "?")
    blocks.append(f"| | 核心CPI | {cpi} | - |")
    cp = jap.get("carry_pressure", "?")
    cr = jap.get("carry_regime", "?")
    blocks.append(f"| | 套息压力 | {cp} | {cr} |")
    yen_s = jap.get("yen_signal", "?")
    blocks.append(f"| | 日元信号 | {yen_s} | - |")
else:
    blocks.append("| 🇯🇵 日本 | （无数据） | - | - |")

blocks.append("")
# 宏观一句话总结（由 Claude Code 分析步骤写入）
blocks.append("> 📝 **宏观总结**: 待 Claude Code 联网分析后追加")
blocks.append("")

# ─── 4. US ETF + 明星股动量（合并） ───
blocks.append("---")
blocks.append("")
blocks.append("## 🇺🇸 美股板块 & 个股异动")
blocks.append("")

# ETF 中文名映射
ETF_CN = {
    "S&P 500": "标普500", "Nasdaq 100": "纳斯达克100", "Dow Jones": "道琼斯",
    "Russell 2000": "罗素2000", "Technology": "科技板块", "Semiconductor": "半导体",
    "Financial": "金融", "Healthcare": "医疗健康", "Energy": "能源",
    "Consumer": "消费品", "Industrial": "工业", "Materials": "原材料",
    "Utilities": "公用事业", "Real Estate": "房地产",
    "Communication": "通信服务", "Biotech": "生物科技", "China Internet": "中概互联",
    "Gold Miners": "金矿", "Silver": "白银", "Oil & Gas": "油气",
    "Aerospace": "航空航天", "Cyber Security": "网络安全", "Cloud": "云计算",
    "Robotics": "机器人", "Electric Vehicle": "电动车", "Clean Energy": "清洁能源",
    "Homebuilder": "住宅建筑", "Transport": "交通运输", "Water": "水资源",
}

etf = _read_json(SIGNALS_DIR / "_macro/us_sector_momentum.json")
if etf:
    items = []
    for e in etf.get("etfs", []):
        name = e.get("name", "?")
        items.append({
            "name": name,
            "cn": ETF_CN.get(name, name),
            "x1": e.get("x1", 0),
            "close": e.get("close", "?"),
            "category": e.get("category", ""),
        })
    items.sort(key=lambda x: x["x1"], reverse=True)
    # 只展示 Top 6 和 Bottom 3
    blocks.append("**板块 ETF**（按 x₁ 势能排序，仅列强弱分明者）:\n")
    blocks.append("| ETF | 中文 | 势能(x₁) | 方向 |")
    blocks.append("|-----|------|---------|------|")
    for it in items[:6]:
        direction = "🔥 强势" if it["x1"] >= 4 else ("✅ 偏强" if it["x1"] >= 1 else "⚪ 中性")
        blocks.append(f"| {it['name']} | {it['cn']} | {it['x1']:.1f} | {direction} |")
    blocks.append("| ... | | | |")
    for it in items[-3:]:
        direction = "❄️ 弱势" if it["x1"] <= -2 else "🔻 偏弱"
        blocks.append(f"| {it['name']} | {it['cn']} | {it['x1']:.1f} | {direction} |")
    # 总结
    top_names = "、".join(f"{i['cn']}" for i in items[:3])
    bot_names = "、".join(f"{i['cn']}" for i in items[-3:])
    blocks.append(f"\n> 📝 **板块总结**: 最强为 {top_names}；最弱为 {bot_names}。详细分析待 Claude Code 追加。")
else:
    blocks.append("**板块 ETF**: （无数据）")
blocks.append("")

# 明星股
blocks.append("**明星股异动**:\n")
star = _read_json(SIGNALS_DIR / "_macro/us_star_momentum.json")
STOCK_CN = {
    "Apple": "苹果", "Microsoft": "微软", "NVIDIA": "英伟达", "Alphabet": "谷歌",
    "Amazon": "亚马逊", "Meta": "Meta", "Tesla": "特斯拉", "Broadcom": "博通",
    "AMD": "AMD", "Intel": "英特尔", "Qualcomm": "高通", "TSMC": "台积电",
    "ASML": "ASML", "Applied Materials": "应用材料", "Lam Research": "泛林",
    "KLA Corp": "科磊", "Micron": "美光", "Analog Devices": "ADI",
    "Texas Instruments": "德州仪器", "Marvell": "美满电子", "Arm": "Arm",
    "Salesforce": "Salesforce", "Adobe": "Adobe", "Oracle": "甲骨文",
    "Cisco": "思科", "Palantir": "Palantir", "CrowdStrike": "CrowdStrike",
    "ServiceNow": "ServiceNow", "Uber": "Uber", "Airbnb": "爱彼迎",
    "Netflix": "奈飞", "Disney": "迪士尼", "JPMorgan": "摩根大通",
    "Bank of America": "美国银行", "Goldman Sachs": "高盛", "Morgan Stanley": "摩根士丹利",
    "Visa": "Visa", "Mastercard": "万事达", "JNJ": "强生",
    "Pfizer": "辉瑞", "Eli Lilly": "礼来", "Novo Nordisk": "诺和诺德",
    "UnitedHealth": "联合健康", "Exxon": "埃克森美孚", "Chevron": "雪佛龙",
    "Caterpillar": "卡特彼勒", "Boeing": "波音", "Lockheed": "洛克希德马丁",
    "RTX": "雷神", "GE": "通用电气", "Honeywell": "霍尼韦尔",
    "Coca-Cola": "可口可乐", "PepsiCo": "百事", "Walmart": "沃尔玛",
    "Costco": "好市多", "Home Depot": "家得宝", "Nike": "耐克",
    "Starbucks": "星巴克", "McDonald's": "麦当劳",
}
if star:
    items = []
    for s in star.get("stocks", []):
        name = s.get("name", "?")
        items.append({
            "name": name,
            "cn": STOCK_CN.get(name, name),
            "x1": s.get("x1", 0),
            "daily_chg": s.get("daily_chg", 0),
            "week_chg": s.get("week_chg", 0),
            "category": s.get("category", ""),
        })
    items.sort(key=lambda x: x["x1"], reverse=True)
    blocks.append("| 股票 | 中文 | 势能(x₁) | 日涨跌 | 周涨跌 |")
    blocks.append("|------|------|---------|--------|--------|")
    for it in items[:8]:
        d = f"{it['daily_chg']:+.1f}%" if isinstance(it['daily_chg'], (int, float)) and it['daily_chg'] != 0 else "-"
        w = f"{it['week_chg']:+.1f}%" if isinstance(it['week_chg'], (int, float)) and it['week_chg'] != 0 else "-"
        blocks.append(f"| {it['name']} | {it['cn']} | {it['x1']:.1f} | {d} | {w} |")
    blocks.append("| ... | | | | |")
    for it in items[-3:]:
        d = f"{it['daily_chg']:+.1f}%" if isinstance(it['daily_chg'], (int, float)) and it['daily_chg'] != 0 else "-"
        blocks.append(f"| {it['name']} | {it['cn']} | {it['x1']:.1f} | {d} | - |")
    # 总结
    hot_stocks = "、".join(f"{i['cn']}(x₁={i['x1']:.0f})" for i in items[:3])
    blocks.append(f"\n> 📝 **个股总结**: 势能最强 {hot_stocks}。弱势股简列，不展开。详细分析待 Claude Code 追加。")
else:
    blocks.append("**明星股**: （无数据）")
blocks.append("")

# ─── 5. 概念链 + 基本面（合并） ───
blocks.append("---")
blocks.append("")
blocks.append("## 🔗 产业链轮动 & 基本面因子")
blocks.append("")

# 概念链
cc = _read_json(SIGNALS_DIR / "_macro/us_concept_momentum.json")
if cc:
    items = [(c.get("chain", "?"), c.get("avg_x1", 0)) for c in cc.get("chains", [])]
    items.sort(key=lambda x: x[1], reverse=True)
    blocks.append("**概念链**（按平均势能）:\n")
    blocks.append("| 链名 | 势能 | 链名 | 势能 |")
    blocks.append("|------|------|------|------|")
    mid = (len(items) + 1) // 2
    left = items[:mid]
    right = items[mid:]
    for i in range(max(len(left), len(right))):
        l_text = f"| {left[i][0]} | {left[i][1]:.2f} " if i < len(left) else "| | "
        r_text = f"| {right[i][0]} | {right[i][1]:.2f} |" if i < len(right) else "| | |"
        blocks.append(l_text + r_text)
else:
    blocks.append("**概念链**: （无数据）\n")
blocks.append("")

# 基本面
fund = _read_json(SIGNALS_DIR / "_funds/fundamental_profile.json")
if fund:
    series = fund.get("factor_premium_series", [])
    if series:
        latest = series[-1]
        factor_items = [(k, v) for k, v in latest.items() if k != "window_end" and isinstance(v, (int, float))]
        blocks.append("**基本面因子溢价**:\n")
        blocks.append("| 因子 | 溢价 | 因子 | 溢价 |")
        blocks.append("|------|------|------|------|")
        mid = (len(factor_items) + 1) // 2
        left = factor_items[:mid]
        right = factor_items[mid:]
        for i in range(max(len(left), len(right))):
            l_text = f"| {_clean(left[i][0], 10)} | {left[i][1]:+.2f} " if i < len(left) else "| | "
            r_text = f"| {_clean(right[i][0], 10)} | {right[i][1]:+.2f} |" if i < len(right) else "| | |"
            blocks.append(l_text + r_text)
    fm = fund.get("factor_momentum", {})
    hot = fm.get("hot_factors", "")
    if hot:
        blocks.append(f"\n> 📝 **因子解读**: 热点因子 {_clean(hot, 40)}。详细分析待 Claude Code 追加。")
else:
    blocks.append("**基本面因子**: （无数据）")
blocks.append("")

# ─── 不再输出"微信公众号最新观点" ───
# 该区块与"本期覆盖文章"（gen_daily_brief.py 输出）内容重合，已删除。

# ─── 写入基础摘要 ───
raw_summary = "\n".join(blocks)
output_path.write_text(raw_summary, encoding="utf-8")
print(f"[OK] 摘要已生成: {output_path}")

# ═══════════════════════════════════════════
# AI 分析（--ai 模式 → 调 API 翻译成可读报告）
# ═══════════════════════════════════════════

if "--ai" in sys.argv:
    from ai_analyzer import call_llm

    # ═══ 历史快照对比 ═══
    HISTORY_FILE = SIGNALS_DIR / "_macro/momentum_history.json"
    today_str = today

    def _load_history():
        if HISTORY_FILE.exists():
            try:
                return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except: pass
        return {}

    def _save_history(etf_map, stock_map, chain_map, fund_latest):
        hist = _load_history()
        if etf_map: hist.setdefault("etfs", {})[today_str] = etf_map
        if stock_map: hist.setdefault("stocks", {})[today_str] = stock_map
        if chain_map: hist.setdefault("chains", {})[today_str] = chain_map
        if fund_latest: hist.setdefault("fundamentals", {})[today_str] = fund_latest
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")

    def _last_date(hist, section):
        dates = sorted(hist.get(section, {}).keys())
        return dates[-2] if len(dates) >= 2 else None  # 上一个非今天的日期

    # 收集当前快照
    hist = _load_history()
    prev_date = _last_date(hist, "etfs") or _last_date(hist, "stocks")

    etf_data = _read_json(SIGNALS_DIR / "_macro/us_sector_momentum.json")
    cur_etfs = {e["symbol"]: e["x1"] for e in (etf_data.get("etfs", []) if etf_data else []) if e.get("x1") is not None}
    star_data = _read_json(SIGNALS_DIR / "_macro/us_star_momentum.json")
    cur_stocks = {s["symbol"]: s["x1"] for s in (star_data.get("stocks", []) if star_data else []) if s.get("x1") is not None}
    chain_data = _read_json(SIGNALS_DIR / "_macro/us_concept_momentum.json")
    cur_chains = {c["chain"]: c["avg_x1"] for c in (chain_data.get("chains", []) if chain_data else []) if c.get("avg_x1") is not None}
    fund_data = _read_json(SIGNALS_DIR / "_funds/fundamental_profile.json")
    fund_latest = {}
    if fund_data:
        series = fund_data.get("factor_premium_series", [])
        if series:
            fund_latest = {k: v for k, v in series[-1].items() if k != "window_end" and isinstance(v, (int, float))}

    # 生成对比文本
    change_lines = []
    if prev_date:
        prev_etfs = hist.get("etfs", {}).get(prev_date, {})
        prev_stocks = hist.get("stocks", {}).get(prev_date, {})
        prev_chains = hist.get("chains", {}).get(prev_date, {})

        # ETF 变化（只看 Top/Bottom 变化大的）
        etf_changes = []
        all_etf_syms = set(list(cur_etfs.keys())[:10] + list(cur_etfs.keys())[-10:])
        for sym in all_etf_syms:
            cur = cur_etfs.get(sym)
            prev = prev_etfs.get(sym)
            if cur is not None and prev is not None and abs(cur - prev) >= 1.5:
                direction = "↑" if cur > prev else "↓"
                etf_changes.append(f"  {sym}: {prev:.1f} → {cur:.1f} ({direction} {abs(cur-prev):.1f})")
        if etf_changes:
            change_lines.append(f"【ETF 变化 vs {prev_date}】")
            change_lines.extend(etf_changes)

        # 明星股变化
        stock_changes = []
        all_stock_syms = set(list(cur_stocks.keys())[:5] + list(cur_stocks.keys())[-5:])
        for sym in all_stock_syms:
            cur = cur_stocks.get(sym)
            prev = prev_stocks.get(sym)
            if cur is not None and prev is not None and abs(cur - prev) >= 1.5:
                direction = "↑" if cur > prev else "↓"
                stock_changes.append(f"  {sym}: {prev:.1f} → {cur:.1f} ({direction} {abs(cur-prev):.1f})")
        if stock_changes:
            change_lines.append(f"【明星股变化 vs {prev_date}】")
            change_lines.extend(stock_changes)

        # 概念链变化
        chain_changes = []
        all_chains = set(list(cur_chains.keys())[:3] + list(cur_chains.keys())[-3:])
        for ch in all_chains:
            cur = cur_chains.get(ch)
            prev = prev_chains.get(ch)
            if cur is not None and prev is not None and abs(cur - prev) >= 0.5:
                direction = "↑" if cur > prev else "↓"
                chain_changes.append(f"  {ch}: {prev:.2f} → {cur:.2f} ({direction} {abs(cur-prev):.2f})")
        if chain_changes:
            change_lines.append(f"【概念链变化 vs {prev_date}】")
            change_lines.extend(chain_changes)

    change_text = "\n".join(change_lines) if change_lines else f"（首次快照，无 {prev_date or '历史'} 对比数据）"

    # 保存本轮快照（在调用 LLM 之前存，不影响 content）
    _save_history(cur_etfs, cur_stocks, cur_chains, fund_latest)

    # 把数据块+变化发给 LLM（纯数据快照，公众号观点由 gen_daily_brief 负责）
    analysis_prompt = f"""你是一个市场分析报告编辑，任务是下面量化数据改写为中文简报。
标注【数据】【解读】分清原始数据和你的推理。不提公众号观点。

【US ETF动量 / 明星股动量】
翻译名称+行业分类+分数。有历史对比写变化幅度。标【数据】【解读】。

【概念链轮动】本期分数，标【数据】【解读】。

【基本面因子溢价】正=奖励负=惩罚，标【数据】【解读】。

中文+英文符号，↑↓方向。不写"x₁="。500-700字。不写"综上所述"。

=== 本期数据 ===
{raw_summary}
=== 历史变化（vs {prev_date or '无'}）===
{change_text}"""
    # ────── end of analysis_prompt ──────


    print("\n  [AI] 翻译数据为可读报告...", end=" ", flush=True)
    try:
        raw_response, provider = call_llm(
            system_prompt="你是市场分析编辑，擅长把枯燥的量化数据翻译成直观、可读的中文简报。",
            user_message=analysis_prompt,
            max_tokens=4096
        )
        if raw_response:
            # 用AI输出覆盖报表
            ai_output = f"# 信源聚合报告 {now_str}\n\n---\n\n{raw_response.strip()}"
            output_path.write_text(ai_output, encoding="utf-8")
            print(f"✓ ({provider})")
            print(f"  → {output_path}")
        else:
            print("✗ AI返回为空，保留原始数据摘要")
    except Exception as e:
        print(f"✗ {e}")

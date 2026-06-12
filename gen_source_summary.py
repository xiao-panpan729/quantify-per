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
now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
output_path = OUTPUT_DIR / f"{today}_sources.md"


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


# ═══════════════════════════════════════════
# 摘要构建
# ═══════════════════════════════════════════

blocks = []
blocks.append(f"# 信源聚合报告 {now_str}")
blocks.append("")

# ─── 1. 公众号 ───
blocks.append("---")
blocks.append("")
blocks.append("## 📰 微信公众号最新观点")
blocks.append("")
if WECHAT_DIR.exists():
    src_dirs = sorted(d for d in WECHAT_DIR.iterdir() if d.is_dir())
    for src_dir in src_dirs:
        files = sorted(src_dir.glob("*.txt"), reverse=True)
        if not files:
            blocks.append(f"- **{src_dir.name}**: （空）")
            continue
        latest = files[0]
        stem = latest.stem
        date_part = stem[:8] if len(stem) >= 8 else ""
        title = _clean(stem[9:].replace("_", " ") if len(stem) > 9 else stem, 60)
        dt = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}" if date_part else "?"
        blocks.append(f"- **{src_dir.name}** ({dt}): {title}")
        # 第二篇
        if len(files) > 1:
            s2 = files[1].stem
            t2 = _clean(s2[9:].replace("_", " ") if len(s2) > 9 else s2, 50)
            blocks.append(f"  └ {t2}")
else:
    blocks.append("（wechat_articles 目录不存在）")
blocks.append("")

# ─── 2. 消息面冲击 ───
blocks.append("---")
blocks.append("")
blocks.append("## 🔴 消息面突发事件")
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
    for s in shock.get("shocks", []):
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

# ─── 3. 流动性 ───
blocks.append("---")
blocks.append("")
blocks.append("## 🌍 全球流动性")
blocks.append("")
liq = _read_json(SIGNALS_DIR / "_macro/liquidity_monitor.json")
if liq:
    p = liq.get("pressure", "?")
    regime = liq.get("regime", "?")
    blocks.append(f"**流动性压力**: {p} → {regime} ({_clean(liq.get('regime_label', ''), 20)})")
    for k, v in liq.get("factors", {}).items():
        lbl = v.get("label", k)
        score = v.get("score", "?")
        lat = v.get("latest", v.get("raw", "?"))
        blocks.append(f"  - {lbl}: {lat} (score={score})")
else:
    blocks.append("（无数据）")
blocks.append("")

# ─── 4. 中国宏观 ───
blocks.append("---")
blocks.append("")
blocks.append("## 🇨🇳 中国宏观快照")
blocks.append("")
snap = _read_json(SIGNALS_DIR / "_macro/macro_snapshot.json")
if snap:
    env = snap.get("environment", "?")
    score = snap.get("score", "?")
    blocks.append(f"**宏观环境**: {env} (score={score:+d})")
    macro = snap.get("macro", {})
    if macro:
        parts = []
        for k, v in macro.items():
            if isinstance(v, (int, float)):
                parts.append(f"{k}={v}")
        if parts:
            blocks.append(f"**数据**: {' | '.join(parts)}")
    bf = snap.get("bond_fx", {})
    if bf:
        bparts = [f"{k}={v}" for k, v in bf.items()]
        blocks.append(f"**债券/汇率**: {' | '.join(bparts)}")
    comm = snap.get("commodity", {})
    if comm:
        cparts = [f"{k}={v}" for k, v in comm.items()]
        blocks.append(f"**商品**: {' | '.join(cparts)}")
    liq = snap.get("liquidity", {})
    if liq:
        blocks.append(f"**流动性**: {liq.get('regime_label','?')} ({liq.get('pressure','?')})")
    sent = snap.get("sentiment", {})
    if sent:
        blocks.append(f"**消息面**: {sent.get('impact_level','?')} (net={sent.get('net_impact','?')})")
else:
    blocks.append("（无数据，请先运行 macro_sensitivity.py --classify）")
blocks.append("")

# ─── 5. US宏观 ───
blocks.append("---")
blocks.append("")
blocks.append("## 🇺🇸 US宏观环境")
blocks.append("")
usm = _read_json(SIGNALS_DIR / "_macro/us_macro_sensitivity.json")
if usm:
    env = usm.get("environment", {})
    blocks.append(f"**分类**: {_clean(env.get('environment', '?'), 20)} (score={env.get('score', '?')})")
    lat = env.get("latest", {})
    if lat:
        blocks.append("**关键数据**:")
        for k, v in lat.items():
            blocks.append(f"  - {k} = {v}")
else:
    blocks.append("（无数据）")
blocks.append("")

# ─── 6. 日本宏观 ───
blocks.append("---")
blocks.append("")
blocks.append("## 🇯🇵 日本宏观 / 套息交易")
blocks.append("")
jap = _read_json(SIGNALS_DIR / "_macro/japan_macro.json")
if jap:
    blocks.append(f"- **BOJ利率**: {jap.get('boj_rate', '?')} ({jap.get('boj_signal', '?')})")
    blocks.append(f"- **日本CPI**: {jap.get('japan_cpi', '?')}")
    blocks.append(f"- **套息压力**: {jap.get('carry_pressure', '?')} → {jap.get('carry_regime', '?')}")
    blocks.append(f"- **日元信号**: {jap.get('yen_signal', '?')}")
    advice = jap.get('a_share_impact', '')
    if advice:
        blocks.append(f"- **A股影响**: {_clean(advice, 60)}")
else:
    blocks.append("（无数据）")
blocks.append("")

# ─── 7. US ETF势能 ───
blocks.append("---")
blocks.append("")
blocks.append("## 🇺🇸 US ETF动量 Top/Bottom")
blocks.append("")
etf = _read_json(SIGNALS_DIR / "_macro/us_sector_momentum.json")
if etf:
    items = [(e.get("name", "?"), e.get("x1", 0), e.get("close", "?"))
             for e in etf.get("etfs", [])]
    items.sort(key=lambda x: x[1])
    blocks.append("\n**最弱5只**:")
    for n, x1, c in items[:5]:
        blocks.append(f"  - {n}: x₁={x1:.2f} @{c}")
    blocks.append("\n**最强5只**:")
    for n, x1, c in reversed(items[-5:]):
        blocks.append(f"  - {n}: x₁={x1:.2f} @{c}")
else:
    blocks.append("（无数据）")
blocks.append("")

# ─── 8. US明星股 ───
blocks.append("---")
blocks.append("")
blocks.append("## ⭐ US明星股动量 Top/Bottom")
blocks.append("")
star = _read_json(SIGNALS_DIR / "_macro/us_star_momentum.json")
if star:
    items = [(s.get("name", "?"), s.get("x1", 0), s.get("close", "?"))
             for s in star.get("stocks", [])]
    items.sort(key=lambda x: x[1])
    blocks.append("\n**最弱5只**:")
    for n, x1, c in items[:5]:
        blocks.append(f"  - {n}: x₁={x1:.2f} @{c}")
    blocks.append("\n**最强5只**:")
    for n, x1, c in reversed(items[-5:]):
        blocks.append(f"  - {n}: x₁={x1:.2f} @{c}")
else:
    blocks.append("（无数据）")
blocks.append("")

# ─── 9. 概念链 ───
blocks.append("---")
blocks.append("")
blocks.append("## 🔗 概念链轮动")
blocks.append("")
cc = _read_json(SIGNALS_DIR / "_macro/us_concept_momentum.json")
if cc:
    items = [(c.get("chain", "?"), c.get("avg_x1", 0))
             for c in cc.get("chains", [])]
    items.sort(key=lambda x: x[1])
    blocks.append("\n**最弱3链**:")
    for n, x1 in items[:3]:
        blocks.append(f"  - {n}: {x1:.2f}")
    blocks.append("\n**最强3链**:")
    for n, x1 in reversed(items[-3:]):
        blocks.append(f"  - {n}: {x1:.2f}")
else:
    blocks.append("（无数据）")
blocks.append("")

# ─── 10. 基本面 ───
blocks.append("---")
blocks.append("")
blocks.append("## 📊 基本面因子溢价（最新窗口）")
blocks.append("")
fund = _read_json(SIGNALS_DIR / "_funds/fundamental_profile.json")
if fund:
    series = fund.get("factor_premium_series", [])
    if series:
        latest = series[-1]
        blocks.append(f"**窗口**: {latest.get('window_end', '?')}")
        for k, v in latest.items():
            if k != "window_end" and isinstance(v, (int, float)):
                blocks.append(f"  - {_clean(k, 15)}: {v:+.4f}")
    fm = fund.get("factor_momentum", {})
    hot = fm.get("hot_factors", "")
    if hot:
        blocks.append(f"\n**热点因子**: {_clean(hot, 40)}")
else:
    blocks.append("（无数据）")
blocks.append("")

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

# -*- coding: utf-8 -*-
"""
宏观历史回溯引擎 — 对已确认的A/B节点逆推宏观状态+事件标签
============================================================

输入: signals/tracking/_macro/node_map.json (node_map.py --save 产出)
输出: 同文件原地更新，填充每个节点的 context.macro_label + context.event_label

用法:
  python tools/macro_history.py                    # 标注所有 A/B 节点
  python tools/macro_history.py --sector 黄金概念   # 只标注指定板块
  python tools/macro_history.py --min-grade B      # B级以上节点
  python tools/macro_history.py --dry-run          # 预览，不写文件

数据来源 (全部通过已有工具的内部函数，不改动原文件):
  - 中国宏观: tools.macro_sensitivity.fetch_macro() → M2/SHIBOR/CPI/PMI 完整历史
  - US宏观:   tools.us_market.macro_sensitivity.fetch_us_macro() → FEDFUNDS/CPI/ISM/NFP
  - 日本宏观: tools.japan_macro.build_japan_macro_df() → BOJ/CPI/FXY/套息压力
  - 流动性:   通过 akshare 直取 BTC/VIX/DXY 历史价格 + M2 → 合成历史压力指数
  - 事件驱动: tools.sentiment.shock_detector 的 keyword 匹配 (since 回填)
"""

import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking"
NODE_MAP_PATH = SIGNALS_DIR / "_macro" / "node_map.json"


# ══════════════════════════════════════════════════════════════
# 宏观数据获取 (缓存全量,只调一次)
# ══════════════════════════════════════════════════════════════

_macro_cache: dict[str, pd.DataFrame] = {}


def _get_china_macro() -> pd.DataFrame | None:
    """中国宏观: M2, SHIBOR, CPI, PMI — 月频, MonthEnd索引"""
    if "china" in _macro_cache:
        return _macro_cache["china"]
    try:
        from tools.macro_sensitivity import fetch_macro
        df = fetch_macro()
        _macro_cache["china"] = df
        return df
    except Exception:
        return None


def _get_us_macro() -> pd.DataFrame | None:
    """US宏观: FEDFUNDS, US_CPI, ISM_PMI, NONFARM — 月频"""
    if "us" in _macro_cache:
        return _macro_cache["us"]
    try:
        from tools.us_market.macro_sensitivity import fetch_us_macro
        df = fetch_us_macro()
        _macro_cache["us"] = df
        return df
    except Exception:
        return None


def _get_japan_macro() -> pd.DataFrame | None:
    """日本宏观: BOJ_rate, JP_CPI, carry_pressure, carry_regime — 月频"""
    if "japan" in _macro_cache:
        return _macro_cache["japan"]
    try:
        from tools.japan_macro import build_japan_macro_df, compute_carry_pressure
        df = build_japan_macro_df()
        df = compute_carry_pressure(df)
        _macro_cache["japan"] = df
        return df
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# 宏观状态分类 (纯函数,日期参数化)
# ══════════════════════════════════════════════════════════════

def _closest_row(df: pd.DataFrame, target_date: str) -> pd.Series | None:
    """在月频DataFrame中找到 target_date 之前最近的一行"""
    try:
        target = pd.Timestamp(target_date)
        # 找 target 之前或同月的最近数据
        before = df[df.index <= target]
        if len(before) == 0:
            return df.iloc[0]  # fallback: 用最早数据
        return before.iloc[-1]
    except Exception:
        return None


def classify_china_at(macro: pd.DataFrame, target_date: str) -> dict:
    """对中国宏观DataFrame在指定日期做环境分类"""
    row = _closest_row(macro, target_date)
    if row is None:
        return {"environment": "无数据", "score": 0, "details": {}}

    env = {}
    m2 = row["M2"]
    env["M2"] = +1 if m2 > 10 else (0 if m2 > 8 else -1)

    shibor = row["SHIBOR"]
    env["SHIBOR"] = +1 if shibor < 1.5 else (0 if shibor < 2.5 else -1)

    cpi = row["CPI"]
    env["CPI"] = +1 if cpi < 1 else (0 if cpi < 3 else -1)

    pmi = row["PMI"]
    env["PMI"] = +1 if pmi > 52 else (0 if pmi > 48 else -1)

    total = sum(env.values())
    label = "宽松" if total >= 2 else ("收紧" if total <= -2 else "中性")

    return {
        "date": str(row.name.date()) if hasattr(row.name, "date") else target_date,
        "environment": label,
        "score": total,
        "details": env,
        "values": {"M2": float(m2), "SHIBOR": float(shibor), "CPI": float(cpi), "PMI": float(pmi)},
    }


def classify_us_at(macro: pd.DataFrame, target_date: str) -> dict:
    """对US宏观DataFrame在指定日期做环境分类"""
    row = _closest_row(macro, target_date)
    if row is None:
        return {"environment": "无数据", "score": 0, "details": {}}

    env = {}
    fed = row["FEDFUNDS"]
    env["FEDFUNDS"] = +1 if fed < 2.0 else (0 if fed <= 4.0 else -1)

    cpi = row["US_CPI"]
    env["US_CPI"] = +1 if cpi < 2.5 else (0 if cpi <= 4.0 else -1)

    ism = row["ISM_PMI"]
    env["ISM_PMI"] = +1 if ism > 52 else (0 if ism >= 47 else -1)

    nfp = row["NONFARM"]
    env["NONFARM"] = +1 if nfp > 20 else (0 if nfp >= 10 else -1)

    total = sum(env.values())
    label = "宽松" if total >= 2 else ("收紧" if total <= -2 else "中性")

    return {
        "date": str(row.name.date()) if hasattr(row.name, "date") else target_date,
        "environment": label,
        "score": total,
        "details": env,
        "values": {"FEDFUNDS": float(fed), "US_CPI": float(cpi),
                   "ISM_PMI": float(ism), "NONFARM": float(nfp)},
    }


def classify_japan_at(df: pd.DataFrame, target_date: str) -> dict:
    """对日本宏观DataFrame在指定日期做环境分类"""
    row = _closest_row(df, target_date)
    if row is None:
        return {"regime": "无数据", "pressure": 0}

    pressure = float(row.get("carry_pressure", 0))
    regime = row.get("carry_regime", "unknown")
    boj = float(row.get("BOJ_rate", 0))
    jp_cpi = float(row.get("JP_CPI", 0))

    # 对A股的影响信号
    if regime in ("unwind",):
        a_share_signal = "收紧 — 套息平仓压制科技/成长"
    elif regime in ("building",):
        a_share_signal = "关注 — 套息压力在积累"
    elif regime in ("easing",):
        a_share_signal = "宽松 — 套息环境有利于风险资产"
    else:
        a_share_signal = "平稳"

    return {
        "date": str(row.name.date()) if hasattr(row.name, "date") else target_date,
        "regime": regime,
        "pressure": round(pressure, 3),
        "boj_rate": boj,
        "japan_cpi": jp_cpi,
        "a_share_signal": a_share_signal,
    }


# ══════════════════════════════════════════════════════════════
# 国内流动性+信用环境 (基于中国宏观数据中的M2+SHIBOR)
# 全球流动性由日本套息压力+US利率覆盖,不需要单独构建
# ══════════════════════════════════════════════════════════════

def _build_domestic_liquidity(cn_macro: pd.DataFrame) -> pd.DataFrame:
    """从中国宏观数据提取流动性+信用子图

    返回: DataFrame(index=MonthEnd, columns=liquidity_score/credit_score/total)
    """
    df = cn_macro.copy()

    # 流动性: M2(扩张=松) + SHIBOR(低=松)
    df["m2_score"] = df["M2"].apply(lambda x: 1.0 if x > 10 else (-1.0 if x < 8 else 0.0))
    df["shibor_score"] = df["SHIBOR"].apply(lambda x: 1.0 if x < 1.5 else (-1.0 if x > 2.5 else 0.0))

    # 信用: PMI作为信用需求代理 (高PMI=信用扩张)
    df["credit_score"] = df["PMI"].apply(lambda x: 1.0 if x > 52 else (-1.0 if x < 48 else 0.0))

    df["liquidity_score"] = (df["m2_score"] + df["shibor_score"]) / 2.0
    df["total_score"] = (df["m2_score"] + df["shibor_score"] + df["credit_score"]) / 3.0

    def _regime(p):
        if pd.isna(p): return "unknown"
        if p > 0.3: return "宽松"
        if p < -0.3: return "收紧"
        return "中性"

    df["regime"] = df["total_score"].apply(_regime)
    return df


# ══════════════════════════════════════════════════════════════
# 事件驱动标签 (keyword匹配,从shock_detector借用逻辑)
# ══════════════════════════════════════════════════════════════

# 精简关键词 → 事件类型映射 (对齐 shock_keywords.json)
EVENT_KEYWORDS = {
    "地缘冲突": ["战争", "冲突", "导弹", "军事", "开火", "核", "中东", "俄乌", "乌克兰", "以色列", "伊朗",
                 "台海", "南海", "朝鲜", "也门", "胡塞"],
    "贸易摩擦": ["关税", "贸易战", "加征", "制裁", "出口管制", "实体清单", "反倾销",
                 "301条款", "脱钩", "供应链安全"],
    "金融制裁": ["冻结", "SWIFT", "没收", "金融制裁", "资产冻结"],
    "货币/利率冲击": ["加息", "降息", "利率决议", "FOMC", "联邦基金", "缩表", "量化宽松",
                     "QE", "taper", "利率走廊", "降准", "MLF", "LPR", "逆回购"],
    "监管风暴": ["约谈", "整顿", "反垄断", "调查", "处罚", "停牌", "退市", "ST",
                 "集采", "限价", "限售", "调控", "三条红线"],
    "流动性危机": ["流动性危机", "钱荒", "爆仓", "熔断", "债灾", "信用违约", "雷曼",
                   "金融危机", "崩盘", "黑天鹅"],
    "产业政策利好": ["十四五", "十五五", "新基建", "国产替代", "自主可控", "大基金",
                    "补贴", "减税", "退税", "专项债", "新能源规划", "碳中和"],
    "突发公共事件": ["疫情", "封城", "地震", "洪水", "台风", "爆炸", "恐袭",
                    "病毒", "疫苗", "隔离"],
    "大宗商品冲击": ["油价暴涨", "油价暴跌", "OPEC", "铜价", "黄金暴涨", "粮食危机",
                    "能源危机", "天然气", "铁矿石"],
}


def _detect_events_for_window(node_start: str, node_end: str) -> list[dict]:
    """对节点时间窗口做关键词事件检测 (轻量版)"""
    # 节点窗口通常跨月,我们检索前后各扩展15天
    try:
        start_dt = pd.Timestamp(node_start) - pd.Timedelta(days=15)
        end_dt = pd.Timestamp(node_end) + pd.Timedelta(days=15)
    except Exception:
        return []

    # 尝试用 shock_detector 的舆情抓取 (只抓有 since 支持的两个源)
    events = []
    try:
        from tools.sentiment.shock_detector import fetch_wallstreetcn, fetch_eastmoney_global
        since_str = start_dt.strftime("%Y-%m-%d")

        # 华尔街见闻
        try:
            articles = fetch_wallstreetcn(since=since_str)
            for a in articles[:50]:
                title = a.get("title", "") or ""
                content = a.get("content", "") or ""
                text = title + " " + (content[:200] if content else "")
                matched = _match_events(text)
                for m in matched:
                    events.append({"source": "wallstreetcn", "event_type": m,
                                   "title": title[:80],
                                   "date": a.get("published_at", "")[:10]})
        except Exception:
            pass

        # 东方财富全球
        try:
            articles = fetch_eastmoney_global(since=since_str)
            for a in articles[:50]:
                title = a.get("title", "") or ""
                content = a.get("content", "") or ""
                text = title + " " + (content[:200] if content else "")
                matched = _match_events(text)
                for m in matched:
                    events.append({"source": "eastmoney", "event_type": m,
                                   "title": title[:80],
                                   "date": a.get("published_at", "")[:10]})
        except Exception:
            pass
    except Exception:
        pass

    return events


def _match_events(text: str) -> list[str]:
    """返回文本匹配到的事件类型列表"""
    matched = []
    for event_type, keywords in EVENT_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                matched.append(event_type)
                break
    return matched


# ══════════════════════════════════════════════════════════════
# 主标注流程
# ══════════════════════════════════════════════════════════════

def annotate_nodes(node_map: dict, min_grade: str = "B",
                   fetch_events: bool = False) -> dict:
    """
    对所有A/B节点的 context.macro_label 和 context.event_label 进行填充。

    Args:
        node_map: node_map.json 的内容
        min_grade: 最低等级要求 (A/B/C/D)
        fetch_events: 是否联网抓取历史事件 (慢,默认False)

    Returns:
        更新后的 node_map, annotated 计数
    """
    grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
    min_rank = grade_rank.get(min_grade, 3)

    # ── 预加载所有宏观数据 ──
    print("加载宏观数据...")
    cn_macro = _get_china_macro()
    if cn_macro is not None:
        print(f"  中国宏观: {len(cn_macro)}个月 ({cn_macro.index[0].date()} ~ {cn_macro.index[-1].date()})")

    us_macro = _get_us_macro()
    if us_macro is not None:
        print(f"  US宏观:   {len(us_macro)}个月 ({us_macro.index[0].date()} ~ {us_macro.index[-1].date()})")

    jp_macro = _get_japan_macro()
    if jp_macro is not None:
        print(f"  日本宏观: {len(jp_macro)}个月 ({jp_macro.index[0].date()} ~ {jp_macro.index[-1].date()})")

    print("构建国内流动性...")
    liq_df = _build_domestic_liquidity(cn_macro) if cn_macro is not None else None
    if liq_df is not None:
        print(f"  流动性:   {len(liq_df)}个月 (基于M2+SHIBOR+PMI)")

    total_annotated = 0
    total_nodes = 0

    for sector in node_map.get("sectors", []):
        for node in sector.get("nodes", []):
            total_nodes += 1
            grade = node.get("quality", {}).get("grade", "D")
            if grade_rank.get(grade, 0) < min_rank:
                continue

            ctx = node.get("context", {})
            window = node.get("window", "")
            if not window or "-" not in window:
                continue
            parts = window.split("-")
            start_date = "-".join(parts[:3])  # "YYYY-MM-DD" from "YYYY-MM-DD-YYYY-MM-DD"

            # ── 中国宏观 ──
            if cn_macro is not None:
                ctx["macro_label"] = classify_china_at(cn_macro, start_date)

            # ── US宏观 ──
            if us_macro is not None:
                ctx["us_macro"] = classify_us_at(us_macro, start_date)

            # ── 日本宏观 ──
            if jp_macro is not None:
                ctx["japan_macro"] = classify_japan_at(jp_macro, start_date)

            # ── 国内流动性(从中国宏观提取) ──
            if liq_df is not None:
                liq_row = _closest_row(liq_df, start_date)
                if liq_row is not None:
                    ctx["liquidity"] = {
                        "date": str(liq_row.name.date()) if hasattr(liq_row.name, "date") else start_date,
                        "regime": liq_row.get("regime", "unknown"),
                        "liquidity_score": round(float(liq_row.get("liquidity_score", 0)), 2),
                        "credit_score": round(float(liq_row.get("credit_score", 0)), 2),
                        "total_score": round(float(liq_row.get("total_score", 0)), 2),
                    }

            # ── 事件标签 ──
            if fetch_events:
                end_date = window.split("-")[-1] if "-" in window else start_date
                events = _detect_events_for_window(start_date, end_date)
                # 聚合事件类型
                event_types = list(set(e["event_type"] for e in events))
                ctx["event_label"] = {
                    "event_types": event_types,
                    "event_count": len(events),
                    "events": events[:10],
                }

            total_annotated += 1

    node_map["macro_annotated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    node_map["macro_annotated_count"] = total_annotated
    return node_map, total_annotated, total_nodes


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="宏观历史回溯 — 节点标注")
    parser.add_argument("--min-grade", default="B", choices=["A", "B", "C"],
                        help="最低节点等级 (default: B)")
    parser.add_argument("--sector", help="只标注指定板块")
    parser.add_argument("--fetch-events", action="store_true",
                        help="联网抓取历史事件 (慢, ~3s/节点)")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览标注结果，不写文件")
    args = parser.parse_args()

    if not NODE_MAP_PATH.exists():
        print(f"未找到节点地图: {NODE_MAP_PATH}")
        print("请先运行: python tools/node_map.py --all --save")
        sys.exit(1)

    with open(NODE_MAP_PATH, "r", encoding="utf-8") as f:
        node_map = json.load(f)

    # 过滤板块
    if args.sector:
        node_map["sectors"] = [s for s in node_map["sectors"]
                               if args.sector in s.get("sector", "")]
        if not node_map["sectors"]:
            print(f"未找到板块: {args.sector}")
            sys.exit(1)
        print(f"标注板块: {node_map['sectors'][0]['sector']}")

    t0 = time.time()

    # 统计待标注节点
    grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
    min_rank = grade_rank.get(args.min_grade, 3)
    pending = sum(1 for s in node_map.get("sectors", [])
                  for n in s.get("nodes", [])
                  if grade_rank.get(n.get("quality", {}).get("grade", "D"), 0) >= min_rank)
    print(f"待标注节点: {pending} ({args.min_grade}级及以上)")

    node_map, annotated, total = annotate_nodes(
        node_map, min_grade=args.min_grade, fetch_events=args.fetch_events
    )

    elapsed = time.time() - t0
    print(f"\n标注完成: {annotated}/{total} 节点 (过滤等级: {args.min_grade})")
    print(f"耗时: {elapsed:.1f}s")

    if args.dry_run:
        # 预览几个节点
        for sector in node_map.get("sectors", [])[:2]:
            for node in sector.get("nodes", [])[:3]:
                ctx = node.get("context", {})
                ml = ctx.get("macro_label", {})
                um = ctx.get("us_macro", {})
                jm = ctx.get("japan_macro", {})
                liq = ctx.get("liquidity", {})
                ev = ctx.get("event_label", {})
                print(f"\n  [{sector['sector']}] {node['window']} "
                      f"({node.get('quality', {}).get('grade', '?')}级)")
                print(f"    中国: {ml.get('environment','?')} (score={ml.get('score',0):+d}) "
                      f"M2={ml.get('values',{}).get('M2','?')}% "
                      f"CPI={ml.get('values',{}).get('CPI','?')}%")
                print(f"    US:   {um.get('environment','?')} (score={um.get('score',0):+d}) "
                      f"FED={um.get('values',{}).get('FEDFUNDS','?')}% "
                      f"ISM={um.get('values',{}).get('ISM_PMI','?')}")
                print(f"    日本: {jm.get('regime','?')} "
                      f"BOJ={jm.get('boj_rate','?')}% "
                      f"pressure={jm.get('pressure','?')}")
                print(f"    流动性: {liq.get('regime','?')} "
                      f"L={liq.get('liquidity_score','?')} "
                      f"C={liq.get('credit_score','?')}")
                if ctx.get("event_label"):
                    print(f"    事件: {ev.get('event_types',[])}")
    else:
        with open(NODE_MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(node_map, f, ensure_ascii=False, indent=2)
        print(f"保存: {NODE_MAP_PATH}")

    print("完成.")

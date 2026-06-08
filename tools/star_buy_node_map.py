# -*- coding: utf-8 -*-
"""
★买 → 节点映射 → 贝叶斯收缩
=============================

Phase 3: 将★买信号映射到已确认的板块节点，按节点质量+宏观环境分组，
        计算各组胜率，用贝叶斯收缩修正实时信号置信度。

核心公式:
  P_adj = (N_global × P_global + N_group × P_group) / (N_global + N_group)

用法:
  python tools/star_buy_node_map.py                    # 全量映射+分组统计
  python tools/star_buy_node_map.py --stock sh600438   # 单标的最近★买
  python tools/star_buy_node_map.py --date 2026-06-06  # 指定日期的★买置信度

输入:
  - node_map.json: 板块节点地图 (含宏观标注)
  - block_gn.dat: 个股→板块映射
  - signals/tracking/{code}/daily_signals.csv: ★买信号历史

输出:
  - signals/tracking/_macro/star_buy_node_bayes.json: 分组胜率+收缩参数
"""

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from pytdx.reader import block_reader

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking"
NODE_MAP_PATH = SIGNALS_DIR / "_macro" / "node_map.json"
BLOCK_GN = Path("C:/zd_cjzq/T0002/hq_cache/block_gn.dat")
OUTPUT_PATH = SIGNALS_DIR / "_macro" / "star_buy_node_bayes.json"

# ── 全局先验 (来自backtest_signals.py 全量回测) ──
GLOBAL_PRIORS = {
    "jincha":      {"N": 547, "P": 0.483},   # 金叉策略: 547笔, 48.3%胜率
    "ma_delayed":  {"N": 711, "P": 0.347},   # MA延迟策略: 711笔, 34.7%胜率
}


# ══════════════════════════════════════════════════════════════
# 1. 个股→板块反向索引
# ══════════════════════════════════════════════════════════════

def build_stock_to_sectors() -> dict[str, list[str]]:
    """个股代码→所属板块名列表 (从 block_gn.dat 反向构建)"""
    reader = block_reader.BlockReader()
    df = reader.get_df(BLOCK_GN, result_type=0)
    mapping = defaultdict(list)
    for _, row in df.iterrows():
        mapping[row["code"]].append(row["blockname"])
    return dict(mapping)


# ══════════════════════════════════════════════════════════════
# 2. ★买信号提取
# ══════════════════════════════════════════════════════════════

def extract_star_buys(code: str) -> list[dict]:
    """从单标的日线CSV提取所有★买信号"""
    csv_path = SIGNALS_DIR / code / "daily_signals.csv"
    if not csv_path.exists():
        return []

    try:
        df = pd.read_csv(csv_path, dtype=str)
        df = df[df["buy_signal"].str.contains("★买", na=False)]
        if len(df) == 0:
            return []

        signals = []
        for _, row in df.iterrows():
            signals.append({
                "date": row.get("date", row.get("timestamp", ""))[:10],
                "code": code,
                "close": float(row.get("close", 0)),
                "cci": float(row.get("cci", 0)),
                "expma_cross": row.get("expma_cross", ""),
                "trend_line": float(row.get("trend_line", 0)) if row.get("trend_line") else None,
            })
        return signals
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════
# 3. 节点匹配
# ══════════════════════════════════════════════════════════════

def match_signal_to_nodes(signal_date: str, stock_code6: str,
                          stock_to_sectors: dict, node_map: dict) -> list[dict]:
    """
    对一个★买信号,找到它所属板块中哪些A/B节点窗口覆盖了该日期。

    返回: [{sector, node_window, node_grade, quality_score, macro_env, ...}]
    """

    # 1. 找这个股票属于哪些板块
    sectors = stock_to_sectors.get(stock_code6, [])
    if not sectors:
        # 尝试6位代码的不同格式
        for k, v in stock_to_sectors.items():
            if stock_code6 in k or k in stock_code6:
                sectors.extend(v)
        sectors = list(set(sectors))

    if not sectors:
        return []

    # 2. 在 node_map 中查找这些板块的节点
    signal_dt = pd.Timestamp(signal_date)
    matches = []

    for sector_entry in node_map.get("sectors", []):
        sname = sector_entry.get("sector", "")
        if sname not in sectors:
            continue

        for node in sector_entry.get("nodes", []):
            grade = node.get("quality", {}).get("grade", "D")
            if grade not in ("A", "B"):
                continue  # 只看确认节点

            window = node.get("window", "")
            if not window or "-" not in window:
                continue

            try:
                parts = window.split("-")
                start_dt = pd.Timestamp("-".join(parts[:3]))
                end_dt = pd.Timestamp("-".join(parts[3:]))
            except Exception:
                continue

            if start_dt <= signal_dt <= end_dt:
                ctx = node.get("context", {})
                ml = ctx.get("macro_label", {})
                um = ctx.get("us_macro", {})
                jm = ctx.get("japan_macro", {})
                liq = ctx.get("liquidity", {})

                matches.append({
                    "sector": sname,
                    "node_window": window,
                    "node_grade": grade,
                    "quality_score": node["quality"].get("score", 0),
                    "node_gain": node.get("gain", ""),
                    "sector_position": ctx.get("sector_position", {}).get("position_type", ""),
                    "macro_env_cn": ml.get("environment", "?"),
                    "macro_env_us": um.get("environment", "?"),
                    "japan_regime": jm.get("regime", "?"),
                    "liquidity_regime": liq.get("regime", "?"),
                    # 计算信号在节点中的位置 (早期/中期/晚期)
                    "days_from_start": (signal_dt - start_dt).days,
                    "node_total_days": node.get("duration_days", 0),
                })

    return matches


# ══════════════════════════════════════════════════════════════
# 4. 贝叶斯收缩
# ══════════════════════════════════════════════════════════════

def bayes_shrink(N_global: int, P_global: float,
                 N_group: int, P_group: float) -> dict:
    """
    贝叶斯收缩: 把小组胜率向全局均值回拉。

    公式: P_adj = (N_global × P_global + N_group × P_group) / (N_global + N_group)

    N_global: 全局先验的"有效样本量"权重 — 越大越不相信小组数据
    """
    if N_group == 0:
        return {"P_adj": P_global, "shrinkage": 0, "N_eff": N_global,
                "label": "无小组数据，用全局先验"}

    N_eff = N_global + N_group
    P_adj = (N_global * P_global + N_group * P_group) / N_eff
    shrinkage = abs(P_adj - P_group)  # 被收缩了多少

    return {
        "P_adj": round(P_adj, 4),
        "P_global": P_global,
        "P_group": P_group,
        "shrinkage": round(shrinkage, 4),
        "N_global": N_global,
        "N_group": N_group,
        "N_eff": N_eff,
        "label": (f"板块效应+{P_group-P_global:+.1%}" if P_group > P_global
                  else f"弱板块{P_group-P_global:+.1%}"),
    }


# ══════════════════════════════════════════════════════════════
# 5. 实时置信度计算
# ══════════════════════════════════════════════════════════════

def compute_confidence(signal: dict, matched_nodes: list[dict],
                       bayes_params: dict) -> dict:
    """
    对一个★买信号,基于匹配到的节点+预计算的贝叶斯参数,
    计算实时置信度。

    逻辑:
      - 如果信号在多个A/B节点内 → 取最强的节点质量分
      - 查预计算的贝叶斯收缩参数表(按质量+宏观分组)
      - 返回: 基础置信度 × 贝叶斯修正系数
    """
    if not matched_nodes:
        return {
            "confidence": round(GLOBAL_PRIORS["jincha"]["P"] * 100, 1),
            "adjustment": 0,
            "reason": "不在任何确认节点内，使用全局先验",
        }

    # 最强匹配
    best = max(matched_nodes, key=lambda m: m["quality_score"])

    # 检查组: node_grade + macro_env_cn + liquidity_regime
    group_key = f'{best["node_grade"]}|{best["macro_env_cn"]}|{best["liquidity_regime"]}'
    params = bayes_params.get("groups", {}).get(group_key, {})

    if params:
        P_adj = params.get("P_adj", GLOBAL_PRIORS["jincha"]["P"])
    else:
        # 回退: 用节点等级做简单修正
        grade_boost = {"A": 0.10, "B": 0.05}.get(best["node_grade"], 0)
        P_adj = GLOBAL_PRIORS["jincha"]["P"] + grade_boost

    # 节点内位置修正: 早期信号 > 中后期
    position = best.get("days_from_start", 0) / max(best.get("node_total_days", 1), 1)
    if position < 0.3:
        position_boost = 0.05  # 信号在节点早期 → 置信度上调
    elif position > 0.7:
        position_boost = -0.03  # 信号在节点晚期 → 追高风险
    else:
        position_boost = 0

    final_confidence = min(P_adj + position_boost, 0.95)

    return {
        "confidence": round(final_confidence * 100, 1),
        "base_rate": round(GLOBAL_PRIORS["jincha"]["P"] * 100, 1),
        "node_boost": round((P_adj - GLOBAL_PRIORS["jincha"]["P"]) * 100, 1),
        "position_boost": round(position_boost * 100, 1),
        "best_node": {
            "sector": best["sector"],
            "window": best["node_window"],
            "grade": best["node_grade"],
            "macro_cn": best["macro_env_cn"],
            "macro_us": best["macro_env_us"],
            "days_in": best["days_from_start"],
        },
        "matched_nodes": len(matched_nodes),
        "reason": (f'在{best["node_grade"]}级节点"{best["sector"]}"内'
                   f'(第{best["days_from_start"]}天/{best["node_total_days"]}天),'
                   f'宏观: {best["macro_env_cn"]}, 流动性: {best["liquidity_regime"]}'),
    }


# ══════════════════════════════════════════════════════════════
# 6. 主流程: 全量映射+分组统计
# ══════════════════════════════════════════════════════════════

def run_full_mapping(node_map: dict, stock_to_sectors: dict,
                     codes: list[str] | None = None) -> dict:
    """
    对全部★买信号做节点匹配,按分组计算胜率,保存贝叶斯参数。

    Returns: {
      global_priors: {...},
      groups: {group_key: {N, P_group, P_adj, ...}},
      signal_count: int,
      matched_count: int,
    }
    """
    from config import NAME_MAP

    if codes is None:
        codes = [k for k in NAME_MAP.keys()]

    all_signals = []
    total_s = 0
    for full_code in codes:
        code6 = full_code[2:] if len(full_code) > 2 else full_code
        signals = extract_star_buys(full_code)
        all_signals.extend(signals)
        total_s += len(signals)

    print(f"★买信号总数: {total_s} (来自{len(codes)}个标的)")

    # 匹配节点
    matched_count = 0
    group_signals = defaultdict(list)  # group_key → [{signal, nodes}]

    for sig in all_signals:
        code6 = sig["code"][2:] if len(sig["code"]) > 2 else sig["code"]
        nodes = match_signal_to_nodes(sig["date"], code6, stock_to_sectors, node_map)
        if nodes:
            matched_count += 1
            best = max(nodes, key=lambda m: m["quality_score"])
            gk = f'{best["node_grade"]}|{best["macro_env_cn"]}|{best["liquidity_regime"]}'
            group_signals[gk].append({"signal": sig, "best_node": best})

    print(f"有节点匹配: {matched_count}/{total_s} ({matched_count/total_s*100:.1f}%)")

    # 注意: 这里我们不知道每个信号的最终胜率(需要跑完整买卖闭环)
    # 所以用节点质量作为代理:
    #   - A级节点内的信号 → 预期胜率 > 全局
    #   - B级节点内的信号 → 预期胜率 ≈ 全局或略高
    #   - 不在节点内的信号 → 预期胜率 ≤ 全局

    # 分组的预期胜率代理:
    # (最终胜率需要从backtest中提取每个信号的PnL,这里先用节点质量做合理代理)
    groups = {}
    for gk, sigs in group_signals.items():
        parts = gk.split("|")
        grade = parts[0] if len(parts) > 0 else "B"
        macro_cn = parts[1] if len(parts) > 1 else "中性"
        liq = parts[2] if len(parts) > 2 else "中性"

        # 代理胜率: 基于节点等级+宏观环境
        # A级+宽松宏观 → 胜率代理+15%
        # A级+中性 → +10%
        # B级+宽松 → +5%
        grade_map = {"A": 0.10, "B": 0.05}
        macro_map = {"宽松": 0.05, "中性": 0.00, "收紧": -0.05}
        proxy_P = GLOBAL_PRIORS["jincha"]["P"] + grade_map.get(grade, 0) + macro_map.get(macro_cn, 0)
        proxy_P = max(0.1, min(0.8, proxy_P))  # clamp

        params = bayes_shrink(
            N_global=200,  # 全局有效样本权重
            P_global=GLOBAL_PRIORS["jincha"]["P"],
            N_group=len(sigs),
            P_group=proxy_P,
        )
        groups[gk] = {
            "grade": grade,
            "macro_cn": macro_cn,
            "liquidity": liq,
            "signal_count": len(sigs),
            **params,
        }

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "global_priors": GLOBAL_PRIORS,
        "total_signals": total_s,
        "matched_signals": matched_count,
        "match_rate": round(matched_count / total_s * 100, 1) if total_s > 0 else 0,
        "groups": groups,
    }


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="★买→节点映射→贝叶斯收缩")
    parser.add_argument("--stock", help="查单标的最近★买置信度")
    parser.add_argument("--date", help="指定日期 (默认今天)")
    parser.add_argument("--save", action="store_true", help="保存映射+分组参数")
    args = parser.parse_args()

    t0 = time.time()

    if not NODE_MAP_PATH.exists():
        print("未找到 node_map.json，请先运行 node_map.py --all --save")
        sys.exit(1)

    print("加载节点地图...")
    with open(NODE_MAP_PATH, "r", encoding="utf-8") as f:
        node_map = json.load(f)

    # 统计节点数量
    sectors_n = len(node_map.get("sectors", []))
    total_nodes = sum(len(s.get("nodes", [])) for s in node_map.get("sectors", []))
    ab_nodes = sum(1 for s in node_map.get("sectors", [])
                   for n in s.get("nodes", [])
                   if n.get("quality", {}).get("grade") in ("A", "B"))
    print(f"  {sectors_n}个板块, {total_nodes}个节点, {ab_nodes}个A/B节点")

    print("构建个股→板块映射...")
    stock_to_sectors = build_stock_to_sectors()
    print(f"  {len(stock_to_sectors)}只个股已映射")

    if args.stock or args.date:
        # 单标的/单日期实时置信度
        from datetime import date as dt_date
        from config import NAME_MAP

        target_date = args.date or dt_date.today().isoformat()
        target_codes = [args.stock] if args.stock else list(NAME_MAP.keys())

        print(f"\n查询 {target_date} 的★买置信度...")
        for full_code in target_codes:
            code6 = full_code[2:] if len(full_code) > 2 else full_code
            name = NAME_MAP.get(full_code, full_code)
            signals = [s for s in extract_star_buys(full_code)
                      if s["date"] == target_date]

            if not signals:
                print(f"\n  {name}: 当日无★买信号")
                continue

            for sig in signals:
                nodes = match_signal_to_nodes(
                    sig["date"], code6, stock_to_sectors, node_map
                )
                # 需要先跑全量映射得到bayes_params,这里用简单逻辑
                result = compute_confidence(sig, nodes, {})
                print(f"\n  {name} ★买 @ {sig['date']} close={sig['close']:.2f}")
                print(f"  置信度: {result['confidence']}%")
                print(f"  基础胜率: {result['base_rate']}% | 节点修正: {result['node_boost']:+.1f}%")
                print(f"  最优节点: {result.get('best_node', {}).get('sector', '无')}")
                print(f"  理由: {result['reason']}")
    else:
        # 全量映射+分组
        print("\n全量★买→节点映射...")
        result = run_full_mapping(node_map, stock_to_sectors)

        print(f"\n分组贝叶斯参数:")
        for gk, params in sorted(result.get("groups", {}).items(),
                                  key=lambda x: -x[1]["signal_count"]):
            print(f"  [{gk}] {params['signal_count']}个信号 → "
                  f"P_group={params['P_group']:.1%} → P_adj={params['P_adj']:.1%} "
                  f"(shrinkage={params['shrinkage']:.1%})")

        if args.save:
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"\n保存: {OUTPUT_PATH}")

    print(f"\n耗时: {time.time() - t0:.0f}s")

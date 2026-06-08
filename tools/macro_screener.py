# -*- coding: utf-8 -*-
"""
Macro Screener v0.1 — Sector momentum × macro environment overlay filter
=========================================================================

Merges sector momentum (X₁) with macro sensitivity betas, then filters
based on current macro environment.

Usage:
  python tools/macro_screener.py                    # Top 20 with overlay
  python tools/macro_screener.py --top 50           # Top 50
  python tools/macro_screener.py --all              # All sectors
  python tools/macro_screener.py --json             # JSON output (for automation)
"""

import json, sys
from pathlib import Path

SIGNALS_DIR = Path(__file__).parent.parent / "signals" / "tracking"


def load_data():
    """Load sector momentum + macro sensitivity caches"""
    with open(SIGNALS_DIR / "_macro" / "sector_momentum_cache.json", "r", encoding="utf-8") as f:
        mom = json.load(f)
    with open(SIGNALS_DIR / "_macro" / "macro_sensitivity.json", "r", encoding="utf-8") as f:
        macro = json.load(f)
    return mom, macro


def build_merged(mom, macro):
    """Merge momentum scores with macro betas, keyed by 880xxx code"""
    env = macro["environment"]
    code_to_name = {}

    # Build reverse map: sector name → 880 code from momentum cache
    for sname, sv in mom["sector_scores"].items():
        code = sv["code_880"]
        code_to_name[code] = {"name": sname, "x1": sv["x1"]}

    # Merge with macro betas
    merged = []
    for code, mv in macro["sectors"].items():
        if code not in code_to_name:
            continue
        entry = {
            "code": code,
            "name": mv["name"],
            "x1": code_to_name[code]["x1"],
            "r_squared": mv["r_squared"],
            "betas": mv["total"],
            "z_scores": mv["z_scores"],
        }
        merged.append(entry)

    return merged, env


def filter_by_environment(merged, env):
    """Tag each sector as '推荐', '中性', or '回避' based on macro environment"""
    label = env["environment"]
    score = env["score"]

    for s in merged:
        pmi = s["betas"]["PMI"]
        shibor = s["betas"]["SHIBOR"]
        cpi = s["betas"]["CPI"]
        m2 = s["betas"]["M2"]
        r2 = s["r_squared"]

        if label == "宽松":
            if pmi > 0.03 and shibor < -0.02 and r2 > 0.05:
                s["filter"] = "推荐"
                s["filter_reason"] = "PMI+ & SHIBOR-"
            elif pmi < -0.08 and r2 > 0.05:
                s["filter"] = "回避"
                s["filter_reason"] = "PMI-"
            else:
                s["filter"] = "中性"
                s["filter_reason"] = ""

        elif label == "收紧":
            if cpi < -0.02 and shibor < -0.02 and r2 > 0.05:
                s["filter"] = "推荐"
                s["filter_reason"] = "CPI- & SHIBOR-"
            elif m2 > 0.05 and r2 > 0.05:
                s["filter"] = "回避"
                s["filter_reason"] = "M2+"
            else:
                s["filter"] = "中性"
                s["filter_reason"] = ""

        else:  # 中性
            s["filter"] = "中性"
            s["filter_reason"] = ""

    return merged


def print_report(merged, env, top_n=20):
    """Print formatted overlay filter report"""
    label = env["environment"]
    latest = env["latest"]

    print("=" * 60)
    print(f"  Macro Screener — 板块动量 × 宏观分层过滤")
    print("=" * 60)
    print(f"  宏观环境: {label} (score={env['score']:+d})")
    print(f"  最新数据: M2={latest['M2']:.1f}%  SHIBOR={latest['SHIBOR']:.2f}%  "
          f"CPI={latest['CPI']:.1f}%  PMI={latest['PMI']:.1f}")
    print(f"  板块覆盖: {len(merged)} 个共有板块")
    print()

    # Sort by X₁ descending (momentum ranking)
    ranked = sorted(merged, key=lambda x: x["x1"], reverse=True)

    # Count filter categories in top N
    top = ranked[:top_n]
    rec_count = sum(1 for s in top if s["filter"] == "推荐")
    avoid_count = sum(1 for s in top if s["filter"] == "回避")
    neutral_count = sum(1 for s in top if s["filter"] == "中性")

    print(f"  X₁ Top {top_n}:  推荐 {rec_count} / 中性 {neutral_count} / 回避 {avoid_count}")
    print()

    # Print table: 推荐 first, then 中性, then 回避
    for fltr, fltr_label in [("推荐", "▼ 推荐买入（宏观共振）"),
                              ("中性", "— 中性（无宏观信号）"),
                              ("回避", "▲ 建议回避（宏观背离）")]:
        subset = [s for s in top if s["filter"] == fltr]
        if not subset:
            continue
        print(f"  {fltr_label}:")
        print(f"    {'板块':14s}  {'X₁':>7s}  {'R²':>5s}  {'PMI':>7s}  {'SHIBOR':>7s}  {'CPI':>7s}  {'M2':>7s}")
        for s in subset:
            b = s["betas"]
            print(f"    {s['name']:14s}  {s['x1']:+7.3f}  {s['r_squared']:.3f}  "
                  f"{b['PMI']:+7.3f}  {b['SHIBOR']:+7.3f}  {b['CPI']:+7.3f}  {b['M2']:+7.3f}")
        print()

    # Summary
    print(f"  {'─' * 50}")
    print(f"  Tip: 在 X₁ 前排板块中，只做「推荐」类，多看「回避」类是否有持仓")
    print(f"       当前 {label} 环境 → {'利好 PMI+ 板块' if label == '宽松' else '利好防守型板块'}")
    print("=" * 60)


def main():
    top_n = 30
    output_json = False

    for arg in sys.argv[1:]:
        if arg.startswith("--top"):
            parts = arg.split("=")
            top_n = int(parts[1]) if len(parts) > 1 else 30
        elif arg == "--all":
            top_n = 9999
        elif arg == "--json":
            output_json = True

    mom, macro = load_data()
    merged, env = build_merged(mom, macro)
    merged = filter_by_environment(merged, env)

    if output_json:
        ranked = sorted(merged, key=lambda x: x["x1"], reverse=True)[:top_n]
        out = {
            "update_time": macro.get("update_time", ""),
            "environment": env,
            "top_n": top_n,
            "sectors": [
                {
                    "code": s["code"],
                    "name": s["name"],
                    "x1": s["x1"],
                    "r_squared": s["r_squared"],
                    "filter": s["filter"],
                    "betas": s["betas"],
                }
                for s in ranked
            ],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_report(merged, env, top_n)


if __name__ == "__main__":
    main()

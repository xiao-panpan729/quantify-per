# -*- coding: utf-8 -*-
"""
US→A-Share 跨市场映射引擎 v1.0
==============================

三步流程:
  1. 数据加载 — US ETF + A股板块日线收益
  2. 相关矩阵 — 每个 US ETF × A股板块的 Pearson r
  3. 领先滞后 — US→CN 的 optimal lag 检测

映射逻辑:
  - US 收市 (4:00 PM ET) ≈ 北京凌晨 4:00
  - CN 开市 (9:30 AM BJT) ≈ 5.5小时后
  - 预期 lag=1: US收盘价→次日CN走势

用法:
  python tools/us_market/cross_mapping.py              # 全量映射
  python tools/us_market/cross_mapping.py --top 15     # Top 15 映射对
  python tools/us_market/cross_mapping.py --etf SMH    # 单ETF钻取
  python tools/us_market/cross_mapping.py --sector 芯片 # 单板块钻取
"""

import argparse, json, time, warnings, sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pytdx.reader import TdxDailyBarReader
from scipy.stats import pearsonr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import akshare as ak
from tools.us_market.etf_momentum import US_ETF_UNIVERSE

warnings.filterwarnings('ignore')

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKING_DIR = PROJECT_ROOT / "signals" / "tracking"
VIPDOC = Path("C:/zd_cjzq/vipdoc")

MAX_LAG = 3          # max lead-lag days
MIN_OVERLAP = 100    # minimum overlapping trading days
TOP_SECTORS = 50     # top N A-share sectors by X_1


# ══════════════════════════════════════════════════════════════
# Data loading
# ══════════════════════════════════════════════════════════════

def load_us_etf_returns() -> dict[str, pd.Series]:
    """拉取所有 US ETF 日线，返回 {symbol: daily_return_series}"""
    print("  [US ETFs] loading daily returns...")
    etf_returns = {}
    all_etfs = []
    for cat, etfs in US_ETF_UNIVERSE.items():
        for sym, name in etfs.items():
            all_etfs.append((sym, name))

    for sym, name in all_etfs:
        try:
            df = ak.stock_us_daily(symbol=sym, adjust="qfq")
            if df is None or len(df) < MIN_OVERLAP:
                print(f"    [{sym}] skip: insufficient data")
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            ret = df["close"].pct_change().dropna()
            etf_returns[sym] = ret
        except Exception as e:
            print(f"    [{sym}] error: {e}")
        time.sleep(0.2)
    print(f"    loaded {len(etf_returns)} ETFs")
    return etf_returns


def load_top_cn_sectors(n: int = TOP_SECTORS) -> list[dict]:
    """加载 Top N A股板块（按 sector_momentum X_1 排名）"""
    cache_path = TRACKING_DIR / "_macro" / "sector_momentum_cache.json"
    if not cache_path.exists():
        print("  [WARN] sector_momentum_cache.json not found, falling back to tdxzs")
        return _load_sectors_from_tdxzs(n)

    with open(cache_path, "r", encoding="utf-8") as f:
        cache = json.load(f)

    sector_scores = cache.get("sector_scores", {})
    sorted_sectors = sorted(sector_scores.items(),
                            key=lambda x: x[1].get("x1", 0), reverse=True)

    result = []
    for name, info in sorted_sectors[:n]:
        code = info.get("code_880", "")
        if code:
            result.append({"name": name, "code": code, "x1": info.get("x1", 0)})
    print(f"  [CN sectors] top {len(result)} by X_1 momentum")
    return result


def _load_sectors_from_tdxzs(n: int) -> list[dict]:
    """Fallback: 从 tdxzs.cfg 加载前 N 个板块"""
    path = Path("C:/zd_cjzq/T0002/hq_cache/tdxzs.cfg")
    if not path.exists():
        return []
    sectors = []
    with open(path, 'r', encoding='gbk', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('['):
                continue
            parts = line.split('|')
            if len(parts) >= 4 and parts[1].strip().startswith('880'):
                sectors.append({'name': parts[0].strip(), 'code': parts[1].strip()})
    return sectors[:n]


def load_cn_sector_returns(code: str) -> pd.Series | None:
    """读取 A股板块日线收益"""
    path = VIPDOC / "sh" / "lday" / f"sh{code}.day"
    if not path.exists():
        return None
    try:
        reader = TdxDailyBarReader()
        df = reader.get_df(str(path))
        closes = df["close"].astype(float)
        closes.index = pd.to_datetime(closes.index)
        ret = closes.pct_change().dropna()
        if len(ret) < MIN_OVERLAP:
            return None
        return ret.sort_index()
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# Correlation & Lead-Lag
# ══════════════════════════════════════════════════════════════

def compute_pair_correlation(us_ret: pd.Series, cn_ret: pd.Series) -> dict | None:
    """计算一对 US ETF × CN sector 的 Pearson r"""
    # Align by calendar date
    common_dates = us_ret.index.intersection(cn_ret.index)
    if len(common_dates) < MIN_OVERLAP:
        return None

    us = us_ret.loc[common_dates]
    cn = cn_ret.loc[common_dates]
    r, p = pearsonr(us, cn)
    return {"correlation": round(r, 4), "p_value": round(p, 6), "n_overlap": len(common_dates)}


def compute_lead_lag(us_ret: pd.Series, cn_ret: pd.Series,
                     max_lag: int = MAX_LAG) -> dict:
    """检测 US→CN 领先滞后天数"""
    results = {}
    for lag in range(max_lag + 1):
        if lag == 0:
            us_shifted = us_ret
        else:
            us_shifted = us_ret.shift(lag)
        pair = compute_pair_correlation(us_shifted, cn_ret)
        if pair:
            results[lag] = pair["correlation"]

    if not results:
        return {"optimal_lag": None, "peak_correlation": 0, "lag_correlations": {}}

    optimal = max(results, key=results.get)
    return {
        "optimal_lag": optimal,
        "peak_correlation": round(results[optimal], 4),
        "lag_correlations": {k: round(v, 4) for k, v in sorted(results.items())},
    }


# ══════════════════════════════════════════════════════════════
# Main mapping pipeline
# ══════════════════════════════════════════════════════════════

def build_mapping_table(us_returns: dict, cn_sectors: list[dict],
                        min_corr: float = 0.25,
                        lead_lag_all: bool = True) -> list[dict]:
    """构建完整映射表。

    核心逻辑: US收市(北京时间凌晨4点) → CN开市(9:30)。
    同一日历日对比(lag=0)是错误的, 应该用 US前一天→CN当日(lag=1)。
    因此对每对先做lead-lag, 用峰值相关作为该对的相关系数。
    """
    print(f"\n  Computing mappings: {len(us_returns)} ETFs × {len(cn_sectors)} sectors...")
    print(f"  (using lead-lag: US(T-lag) → CN(T), lag=0/1/2)")
    mappings = []
    total = len(us_returns) * len(cn_sectors)
    count = 0

    for sym, us_ret in us_returns.items():
        etf_count = 0
        for sec in cn_sectors:
            count += 1
            cn_ret = load_cn_sector_returns(sec["code"])
            if cn_ret is None:
                continue

            if lead_lag_all:
                # 先做lead-lag，用最优lag的相关系数作为主相关
                ll = compute_lead_lag(us_ret, cn_ret)
                if ll is None or ll["peak_correlation"] is None:
                    continue
                peak_r = ll["peak_correlation"]
                if abs(peak_r) < min_corr:
                    continue
                # 获取最优lag下的p值
                opt_lag = ll["optimal_lag"]
                if opt_lag is not None and opt_lag > 0:
                    us_shifted = us_ret.shift(opt_lag)
                else:
                    us_shifted = us_ret
                pair_corr = compute_pair_correlation(us_shifted, cn_ret)
                p_val = pair_corr["p_value"] if pair_corr else 1.0
                n_overlap = pair_corr["n_overlap"] if pair_corr else 0

                mappings.append({
                    "us_etf": sym,
                    "us_name": _get_etf_name(sym),
                    "cn_sector": sec["name"],
                    "cn_code": sec["code"],
                    "cn_x1": sec.get("x1"),
                    "correlation": peak_r,
                    "p_value": p_val,
                    "n_overlap": n_overlap,
                    "optimal_lag": opt_lag,
                    "lag_correlations": ll.get("lag_correlations", {}),
                })
                etf_count += 1
            else:
                pair = compute_pair_correlation(us_ret, cn_ret)
                if pair is None or abs(pair["correlation"]) < min_corr:
                    continue
                ll = compute_lead_lag(us_ret, cn_ret) if abs(pair["correlation"]) > 0.35 else None
                mappings.append({
                    "us_etf": sym, "us_name": _get_etf_name(sym),
                    "cn_sector": sec["name"], "cn_code": sec["code"],
                    "cn_x1": sec.get("x1"),
                    "correlation": pair["correlation"],
                    "p_value": pair["p_value"], "n_overlap": pair["n_overlap"],
                    "optimal_lag": ll["optimal_lag"] if ll else None,
                    "lag_correlations": ll.get("lag_correlations", {}) if ll else {},
                })
                etf_count += 1

        print(f"    [{sym}] {etf_count} significant pairs (|r|>{min_corr})")

    # Sort by absolute correlation
    mappings.sort(key=lambda m: abs(m["correlation"]), reverse=True)

    # Summary stats
    strong = [m for m in mappings if abs(m["correlation"]) >= 0.50]
    moderate = [m for m in mappings if 0.35 <= abs(m["correlation"]) < 0.50]
    weak = [m for m in mappings if abs(m["correlation"]) < 0.35]

    lags = [m["optimal_lag"] for m in mappings
            if m.get("optimal_lag") is not None]
    avg_lag = np.mean(lags) if lags else 0.0
    unique_lags, lag_counts = (np.unique(lags, return_counts=True)
                               if lags else ([], []))

    summary = {
        "total_pairs_analyzed": count,
        "significant_pairs": len(mappings),
        "strong_mappings": len(strong),
        "moderate_mappings": len(moderate),
        "weak_mappings": len(weak),
        "avg_lead_lag": round(float(avg_lag), 1),
        "lag_distribution": {int(k): int(v) for k, v in zip(unique_lags, lag_counts)},
    }

    return mappings, summary


def _get_etf_name(sym: str) -> str:
    for cat, etfs in US_ETF_UNIVERSE.items():
        if sym in etfs:
            return etfs[sym]
    return sym


# ══════════════════════════════════════════════════════════════
# Output
# ══════════════════════════════════════════════════════════════

def save_mapping(mappings: list[dict], summary: dict):
    """保存 JSON 映射表"""
    out = {
        "date": datetime.now().strftime("%Y%m%d"),
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "top_mappings": mappings[:50],   # top 50
        "all_mappings": mappings,        # all
    }
    path = TRACKING_DIR / "_macro" / "us_cn_mapping.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[JSON] {path}")


def print_top_mappings(mappings: list[dict], top_n: int = 20):
    """打印 Top N 映射对"""
    print(f"\n{'='*90}")
    print(f"  US→A-Share 跨市场映射 Top {top_n}")
    print(f"{'='*90}")
    print(f"{'排名':<5} {'US ETF':<8} {'A股板块':<18} {'相关':>7} {'领先':>5} {'强度':<8}")
    print(f"{'-'*90}")

    for i, m in enumerate(mappings[:top_n], 1):
        corr = m["correlation"]
        lag_info = ""
        strength = ""
        if m.get("lead_lag") and m["lead_lag"]["optimal_lag"] is not None:
            lag_info = f"T+{m['lead_lag']['optimal_lag']}"
            peak = m["lead_lag"]["peak_correlation"]
            lag_info += f"({peak:.2f})"

        if abs(corr) >= 0.50:
            strength = "强"
        elif abs(corr) >= 0.35:
            strength = "中"
        else:
            strength = "弱"

        sign = "+" if corr > 0 else ""
        print(f"{i:<5} {m['us_etf']:<8} {m['cn_sector']:<18} {sign}{corr:>6.3f} {lag_info:>5}   {strength:<8}")


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="US→A-Share 跨市场映射")
    parser.add_argument("--top", type=int, default=20, help="打印 Top N (默认20)")
    parser.add_argument("--sectors", type=int, default=TOP_SECTORS,
                        help=f"Top N A股板块 (默认{TOP_SECTORS})")
    parser.add_argument("--min-corr", type=float, default=0.25,
                        help="最小相关系数阈值 (默认0.25)")
    parser.add_argument("--etf", type=str, help="钻取单个ETF映射")
    parser.add_argument("--sector", type=str, help="钻取单个板块映射")
    parser.add_argument("--no-save", action="store_true", help="不保存JSON")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 60)
    print("US→A-Share 跨市场映射引擎 v1.0")
    print("=" * 60)

    # 1. Load data
    print("\n[1/3] Loading data...")
    us_returns = load_us_etf_returns()
    cn_sectors = load_top_cn_sectors(args.sectors)

    if not us_returns:
        print("  [ERR] No US ETF data loaded")
        return
    if not cn_sectors:
        print("  [ERR] No CN sector data loaded")
        return

    # 2. Build mapping
    print(f"\n[2/3] Building mapping table (|r| > {args.min_corr})...")
    mappings, summary = build_mapping_table(us_returns, cn_sectors, args.min_corr)

    # 3. Output
    print(f"\n[3/3] Results")
    print(f"  Significant pairs: {summary['significant_pairs']}")
    print(f"  Strong (|r|>=0.50): {summary['strong_mappings']}")
    print(f"  Moderate (0.35-0.50): {summary['moderate_mappings']}")
    print(f"  Weak (0.25-0.35): {summary['weak_mappings']}")
    if summary['avg_lead_lag'] > 0:
        print(f"  Avg lead-lag: US leads CN by {summary['avg_lead_lag']:.1f} days")

    if args.etf:
        filtered = [m for m in mappings if m["us_etf"].upper() == args.etf.upper()]
        print_top_mappings(filtered, len(filtered))
    elif args.sector:
        filtered = [m for m in mappings if args.sector in m["cn_sector"]]
        print_top_mappings(filtered, len(filtered))
    else:
        print_top_mappings(mappings, args.top)

    if not args.no_save and mappings:
        save_mapping(mappings, summary)

    elapsed = time.time() - t0
    print(f"\nDone: {elapsed:.1f}s")


if __name__ == "__main__":
    main()

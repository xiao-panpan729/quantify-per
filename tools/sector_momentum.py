# -*- coding: utf-8 -*-
"""
板块势能评分系统 v2.0 — 基于板块指数直接计算
=============================================

修复 v1.0 核心问题：
  v1.0: 对成分股逐股算分再平均 → 数值偏高（培育钻石26.2 vs 通达信9.68）
  v2.0: 直接读取板块指数（880xxx）日线，应用原公式输出 X_1

数据来源:
  - 板块名称↔880xxx映射: C:/zd_cjzq/T0002/hq_cache/tdxzs.cfg
  - 板块指数日线: C:/zd_cjzq/vipdoc/sh/lday/sh880xxx.day
  - 通达信公式: tools/tdx_formulas/动量评分V3_完整公式_RIS势能2.md

输出: X_1（通达信 RSI势能2 指标值），直接与板块指数RPS排名可比
"""

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from pytdx.reader import TdxDailyBarReader


# ── 配置 ──
VIPDOC = Path("C:/zd_cjzq/vipdoc")
HQ_CACHE = Path("C:/zd_cjzq/T0002/hq_cache")
TDXZS_CFG = HQ_CACHE / "tdxzs.cfg"
OUTPUT_DIR = Path(__file__).parent.parent / "reports" / "sector_momentum"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── 向量化公式函数 ──

def _sma(arr: np.ndarray, period: int) -> np.ndarray:
    """向量化 SMA，返回同长度数组"""
    ret = np.full_like(arr, np.nan)
    cum = np.cumsum(arr)
    ret[period - 1] = cum[period - 1] / period
    ret[period:] = (cum[period:] - cum[:-period]) / period
    return ret


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """向量化 EMA（递归实现，与通达信一致）"""
    alpha = 2.0 / (period + 1)
    ret = np.full_like(arr, np.nan)
    ret[0] = arr[0]
    for i in range(1, len(arr)):
        ret[i] = alpha * arr[i] + (1 - alpha) * ret[i - 1]
    return ret


def _safe_div(num: np.ndarray, denom: np.ndarray, fallback: float = 0.0) -> np.ndarray:
    """安全除法：分母无效时回退为 fallback，不传播 NaN"""
    mask = np.isfinite(denom) & (denom != 0)
    result = np.full_like(denom, fallback, dtype=np.float64)
    np.divide(num, denom, out=result, where=mask)
    return result


def _pct_change(arr: np.ndarray, lag: int = 1) -> np.ndarray:
    """全系列百分比变化: (arr[i]/arr[i-lag] - 1) * 100，SMA前N-1根NaN安全"""
    n = len(arr)
    result = np.zeros(n, dtype=np.float64)
    denom = arr[:-lag]
    num_arr = arr[lag:]
    ratio = _safe_div(num_arr, denom, 1.0)
    result[lag:] = (ratio - 1.0) * 100.0
    return result


def calc_index_x1(close: np.ndarray, volume: np.ndarray) -> float:
    """
    对单个板块指数，应用通达信 RSI势能2 公式，返回 X_1。

    公式源码: tools/tdx_formulas/动量评分V3_完整公式_RIS势能2.md
    """
    n = len(close)
    if n < 65:
        return 0.0

    # ── 均线 ──
    ma3 = _sma(close, 3)
    ma5 = _sma(close, 5)
    ma13 = _sma(close, 13)
    ma20 = _sma(close, 20)
    ma60 = _sma(close, 60)
    vol_ma5 = _sma(volume, 5)

    # ── 超短强度（逐 bar 变周期 3/5） ──
    # 超短周期 := IF(VOL/MA(VOL,5)>1.5, 3, 5)
    vol_ratio = _safe_div(volume, vol_ma5, 1.0)
    us_period = np.where(vol_ratio > 1.5, 3, 5).astype(int)
    ma_us = np.where(us_period == 3, ma3, ma5)

    us_pct = _pct_change(ma_us, 1)
    us_strength = np.degrees(np.arctan(us_pct))

    # ── 短期强度：EMA(ATAN((MA13/REF(MA13,1)-1)*100)*57.3, 2) ──
    short_pct = _pct_change(ma13, 1)
    short_atan = np.degrees(np.arctan(short_pct))
    short_ema = _ema(short_atan, 2)

    # ── 中期强度：EMA(ATAN((MA20/REF(MA20,1)-1)*100)*57.3, 3) ──
    mid_pct = _pct_change(ma20, 1)
    mid_atan = np.degrees(np.arctan(mid_pct))
    mid_ema = _ema(mid_atan, 3)

    # ── 长期强度：ATAN((MA60/REF(MA60,20)-1)*100)*57.3 ──
    long_pct = _pct_change(ma60, 20)
    long_atan = np.degrees(np.arctan(long_pct))

    # ── 量能因子（逐 bar） ──
    vol_r1 = _safe_div(volume[1:], volume[:-1], 1.0)
    vol_r5 = _safe_div(vol_ma5[1:], vol_ma5[:-1], 1.0)

    vol_factor = np.ones(n, dtype=np.float64)
    vol_factor[1:] = np.maximum(vol_r1, vol_r5)
    vol_factor = np.nan_to_num(vol_factor, nan=1.0)

    boost = np.where(vol_factor > 1.8, 1.25, np.where(vol_factor > 1.3, 1.15, 1.0))

    # ── 势能评分（全系列，用于 BARSLAST） ──
    score_series = (
        us_strength * 0.45 * boost
        + short_ema * 0.3
        + mid_ema * 0.2 * 0.85
        + long_atan * 0.05
    ) * (100.0 / 60.0)

    # EMA 激活前段可能有 NaN（从 arr[0]=0 出发，中间无 NaN 但结果仍是 finite）
    score_series = np.nan_to_num(score_series, nan=0.0)

    # ── 连续强势天数：BARSLAST(势能评分<REF(势能评分,1)) ──
    score_diff = np.diff(score_series)
    decline_idx = np.where(score_diff < 0)[0]
    if len(decline_idx) > 0:
        consecutive_strong = n - 1 - decline_idx[-1]
    else:
        consecutive_strong = n

    # ── X_1 = (势能评分 + 连续强势天数*0.015) * 0.1 ──
    x1 = (score_series[-1] + consecutive_strong * 0.015) * 0.1
    return round(x1, 2)


# ── 加载板块映射 ──

def load_sector_index_map() -> list[dict]:
    """
    从 tdxzs.cfg 加载板块→880xxx代码映射。

    文件格式（GBK，'|'分隔）:
      name|880xxx|category|sub_category|flag|sort_order

    category:
      4 = 概念板块（主排名，269个）
      3 = 行业板块（辅助，32个）
      5 = 风格板块（158个）
      2 = 区域板块（145个）

    返回: [{name, code_880, category}, ...]
    """
    if not TDXZS_CFG.exists():
        print(f"[错误] tdxzs.cfg 不存在: {TDXZS_CFG}")
        return []

    with open(TDXZS_CFG, "rb") as f:
        raw = f.read()
    text = raw.decode("gbk")
    lines = text.strip().split("\n")

    sectors = []
    for line in lines:
        parts = line.split("|")
        if len(parts) < 3:
            continue
        name = parts[0]
        code_880 = parts[1]
        category = int(parts[2])
        # 只取概念板块（主排名）和行业板块（辅助）
        if category in (4, 3):
            sectors.append({
                "name": name,
                "code_880": code_880,
                "category": category,
                "category_label": "概念板块" if category == 4 else "行业板块",
            })

    return sectors


# ── 计算全量板块评分 ──

def calc_all_sector_scores(sectors: list[dict]) -> list[dict]:
    """对每个板块指数计算 X_1"""
    results = []
    total = len(sectors)
    reader = TdxDailyBarReader()
    tick = time.time()

    print(f"  计算 {total} 个板块指数势能...", flush=True)

    for idx, sec in enumerate(sectors, 1):
        code_880 = sec["code_880"]
        fpath = VIPDOC / "sh" / "lday" / f"sh{code_880}.day"

        if not fpath.exists():
            sec["x1"] = 0.0
            sec["close"] = 0.0
            results.append(sec)
            continue

        try:
            df = reader.get_df(str(fpath))
            if df is None or len(df) < 65:
                sec["x1"] = 0.0
                sec["close"] = 0.0
                results.append(sec)
                continue

            close = df["close"].to_numpy(dtype=np.float64)
            volume = df["volume"].to_numpy(dtype=np.float64)

            x1 = calc_index_x1(close, volume)
            sec["x1"] = x1
            sec["close"] = round(float(df["close"].iloc[-1]), 2)
            results.append(sec)

        except Exception as e:
            sec["x1"] = 0.0
            sec["close"] = 0.0
            results.append(sec)

        if idx % 50 == 0:
            elapsed = time.time() - tick
            print(f"  ... {idx}/{total}, 耗时 {elapsed:.0f}s", flush=True)

    elapsed = time.time() - tick
    print(f"  完成! 总耗时 {elapsed:.0f}s", flush=True)
    return results


# ── 报告输出 ──

def report_top_bottom(results: list[dict], top_n: int = 30):
    """终端输出排名"""
    df = pd.DataFrame(results)

    for cat_label in ["概念板块", "行业板块"]:
        sub = df[df["category_label"] == cat_label].copy()
        if sub.empty:
            continue
        sub = sub.sort_values("x1", ascending=False)

        print(f"\n{'='*60}")
        print(f"  {cat_label} — X_1 势能排名（通达信 RSI势能2 值）")
        print(f"{'='*60}")

        # Top
        top = sub.head(top_n)
        print(f"\n  ▲ Top {top_n}:")
        print(f"  {'排名':>4} {'板块名称':<18} {'X_1':>8} {'最新价':>10}")
        print(f"  {'-'*42}")
        for i, (_, row) in enumerate(top.iterrows(), 1):
            print(f"  {i:>4} {row['name']:<18} {row['x1']:>8.2f} {row['close']:>10.2f}")

        # Bottom
        bot = sub.tail(top_n).iloc[::-1]
        print(f"\n  ▼ Bottom {top_n}:")
        print(f"  {'排名':>4} {'板块名称':<18} {'X_1':>8} {'最新价':>10}")
        print(f"  {'-'*42}")
        for i, (_, row) in enumerate(bot.iterrows(), 1):
            print(f"  {i:>4} {row['name']:<18} {row['x1']:>8.2f} {row['close']:>10.2f}")


def save_results(results: list[dict]):
    """保存 JSON + CSV"""
    import json
    from datetime import date

    today = date.today().strftime("%Y%m%d")
    df = pd.DataFrame(results)

    # JSON
    jpath = OUTPUT_DIR / f"{today}_sector_momentum_v2.json"
    jdata = {
        "date": today,
        "total_sectors": len(results),
        "sectors": results,
    }
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(jdata, f, ensure_ascii=False, indent=2)
    print(f"\n[JSON] {jpath}")

    # CSV
    cpath = OUTPUT_DIR / f"{today}_sector_momentum_v2.csv"
    cols = ["name", "code_880", "category_label", "x1", "close"]
    df[cols].to_csv(cpath, index=False, encoding="utf-8-sig")
    print(f"[CSV]  {cpath}")

    # Markdown 报告
    mpath = OUTPUT_DIR / f"{today}_sector_momentum_v2.md"
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(f"# 板块势能评分报告 v2 ({today})\n\n")
        f.write("> X_1 = 通达信 RSI势能2 指标值（板块指数直接计算）\n\n")

        for cat_label in ["概念板块", "行业板块"]:
            sub = df[df["category_label"] == cat_label].sort_values("x1", ascending=False)
            if sub.empty:
                continue
            top30 = sub.head(30)
            bot30 = sub.tail(30).iloc[::-1]

            f.write(f"## {cat_label} — Top 30\n\n")
            f.write("| 排名 | 板块 | X_1 | 最新价 |\n")
            f.write("|------|------|-----|--------|\n")
            for i, (_, row) in enumerate(top30.iterrows(), 1):
                f.write(f"| {i} | {row['name']} | {row['x1']:.2f} | {row['close']:.2f} |\n")

            f.write(f"\n## {cat_label} — Bottom 30\n\n")
            f.write("| 排名 | 板块 | X_1 | 最新价 |\n")
            f.write("|------|------|-----|--------|\n")
            for i, (_, row) in enumerate(bot30.iterrows(), 1):
                f.write(f"| {i} | {row['name']} | {row['x1']:.2f} | {row['close']:.2f} |\n")

            f.write("\n")

    print(f"[MD]   {mpath}")


def search_sector(results: list[dict], keyword: str):
    """搜索特定板块"""
    for r in results:
        if keyword in r["name"]:
            print(f"  {r['category_label']} | {r['name']} (sh{r['code_880']}): X_1={r['x1']:.2f}")


def compare_with_tdx(results: list[dict]):
    """与用户提供的通达信截图对比验证"""
    # 用户截图中的 Top 10 概念板块
    tdx_top10 = {
        "培育钻石": 9.68,
        "超级电容": 5.99,
        "复合铜箔": 4.75,
        "AI手机PC": 4.38,
        "铜缆高速连接": 4.26,
        "CPO概念": 3.76,
        "光通信": 3.67,
        "超临界发电": 3.07,
        "玻璃基板": 2.94,
        "PCB概念": 2.80,
    }

    print(f"\n{'='*60}")
    print(f"  ▼ 与通达信 RPS 排名对比验证（概念板块 Top 10）")
    print(f"{'='*60}")
    print(f"  {'板块名称':<18} {'通达信X_1':>10} {'我的X_1':>10} {'偏差':>8}")
    print(f"  {'-'*48}")

    lookup = {r["name"]: r["x1"] for r in results if r["category_label"] == "概念板块"}
    diffs = []
    for name, tdx_val in tdx_top10.items():
        my_val = lookup.get(name, 0)
        diff = my_val - tdx_val
        diffs.append(abs(diff))
        marker = " OK" if abs(diff) < 0.3 else (" ~?" if abs(diff) < 1.0 else " DIFF")
        print(f"  {name:<18} {tdx_val:>10.2f} {my_val:>10.2f} {diff:>+7.2f}{marker}")

    avg_diff = sum(diffs) / len(diffs)
    print(f"  {'-'*48}")
    print(f"  {'平均绝对偏差':<18} {avg_diff:>10.2f}")
    print()


# ── 个股↔板块映射缓存 ──

CACHE_FILE = Path(__file__).parent.parent / "signals" / "tracking" / "sector_momentum_cache.json"


def build_stock_sector_cache(results: list[dict]) -> dict:
    """
    构建个股↔板块映射 + 板块X_1评分 的联合缓存。

    返回:
      {
        "date": "20260603",
        "sector_scores": { "培育钻石": {"code_880":"880754","x1":9.68,"close":2376.53}, ... },
        "stock_sectors": { "600438": ["光伏","HJT电池",...], "000100": ["芯片","OLED",...], ... }
      }
    """
    from datetime import date
    from pytdx.reader.block_reader import BlockReader

    # ── 板块评分表 ──
    sector_scores = {}
    for r in results:
        if r.get("category_label") == "概念板块":
            sector_scores[r["name"]] = {
                "code_880": r["code_880"],
                "x1": r["x1"],
                "close": r.get("close", 0),
            }

    # ── 个股↔板块反向表 ──
    stock_sectors = {}
    gn_path = HQ_CACHE / "block_gn.dat"
    if gn_path.exists():
        try:
            reader = BlockReader()
            df_gn = reader.get_df(str(gn_path), result_type=0)
            for _, row in df_gn.iterrows():
                code = row["code"]
                name = row["blockname"]
                if name in sector_scores:  # 只收录有评分的概念板块
                    stock_sectors.setdefault(code, []).append(name)
        except Exception as e:
            print(f"  [警告] 个股板块映射构建失败: {e}")

    cache = {
        "date": date.today().strftime("%Y%m%d"),
        "sector_scores": sector_scores,
        "stock_sectors": stock_sectors,
    }
    return cache


def save_sector_cache(cache: dict):
    """持久化板块映射缓存"""
    import json

    # ensure parent dir exists (reports/sector_momentum for backward compat)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # primary cache (for other scripts to read)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"[CACHE] {CACHE_FILE} ({len(cache['stock_sectors'])}只个股)")

    # also save to reports dir for reference
    rpath = OUTPUT_DIR / f"{cache['date']}_sector_cache.json"
    with open(rpath, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"[CACHE] {rpath}")


def query_stock_sector_momentum(code: str, cache: dict = None) -> dict:
    """
    查询某只个股的板块势能信息。

    参数:
      code: 6位代码 (如 '600438') 或 8位全码 (如 'sh600438')
      cache: 可选预加载的缓存字典，不传则自动读缓存文件

    返回:
      {
        "sectors": ["光伏","HJT电池",...],
        "avg_x1": 2.5,
        "max_x1": 5.2,
        "top_sector": "光伏",
        "sector_details": {"光伏":{"x1":5.2,...}, ...}
      }
    """
    import json

    if cache is None:
        if not CACHE_FILE.exists():
            return {"sectors": [], "avg_x1": 0, "max_x1": 0, "top_sector": None, "sector_details": {}}
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

    # 统一转为6位代码
    code6 = code[-6:] if len(code) > 6 else code

    sector_names = cache.get("stock_sectors", {}).get(code6, [])
    sector_scores = cache.get("sector_scores", {})

    details = {}
    scores = []
    for sname in sector_names:
        if sname in sector_scores:
            s = sector_scores[sname]
            details[sname] = s
            scores.append(s["x1"])

    if not scores:
        return {"sectors": sector_names, "avg_x1": 0, "max_x1": 0, "top_sector": None, "sector_details": {}}

    best_idx = int(np.argmax(scores))
    return {
        "sectors": sector_names,
        "avg_x1": round(float(np.mean(scores)), 2),
        "max_x1": round(float(max(scores)), 2),
        "top_sector": sector_names[best_idx] if best_idx < len(sector_names) else None,
        "sector_details": details,
    }


# ── 主入口 ──

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="板块势能评分系统 v2")
    parser.add_argument("--search", help="搜索特定板块")
    parser.add_argument("--top", type=int, default=30, help="Top N")
    parser.add_argument("--save", action="store_true", help="保存结果 + 构建个股板块映射缓存")
    parser.add_argument("--verify", action="store_true", help="与通达信截图对比验证")
    parser.add_argument("--cache-only", action="store_true", help="只重建缓存（需要已有评分结果）")
    args = parser.parse_args()

    t0 = time.time()

    print("加载板块映射 (tdxzs.cfg)...")
    sectors = load_sector_index_map()
    if not sectors:
        print("  [错误] 无法加载板块映射，退出")
        sys.exit(1)

    cat_counts = {}
    for s in sectors:
        cat_counts[s["category_label"]] = cat_counts.get(s["category_label"], 0) + 1
    for label, cnt in cat_counts.items():
        print(f"  {label}: {cnt}个")

    print("\n计算板块势能评分（880xxx 指数日线直接计算）...")
    results = calc_all_sector_scores(sectors)

    if args.search:
        for kw in args.search.split(","):
            kw = kw.strip()
            print(f"\n搜索: {kw}")
            search_sector(results, kw)
        sys.exit(0)

    report_top_bottom(results, top_n=args.top)

    if args.verify:
        compare_with_tdx(results)

    if args.cache_only:
        # 从已有 v2 JSON 加载结果，只重建缓存
        import json as _json
        from datetime import date as _date
        today_str = _date.today().strftime("%Y%m%d")
        cache_json_path = OUTPUT_DIR / f"{today_str}_sector_momentum_v2.json"
        if not cache_json_path.exists():
            print(f"  [错误] 未找到今日评分结果: {cache_json_path}")
            print(f"  请先运行: python tools/sector_momentum.py --save")
            sys.exit(1)
        with open(cache_json_path, "r", encoding="utf-8") as f:
            prev = _json.load(f)
        results = prev.get("sectors", [])
        print(f"从 {cache_json_path.name} 加载 {len(results)} 个板块评分")
        print("\n构建个股板块映射缓存...")
        cache = build_stock_sector_cache(results)
        save_sector_cache(cache)
        elapsed = time.time() - t0
        print(f"\n总耗时: {elapsed:.0f}s")
        sys.exit(0)

    if args.save:
        save_results(results)
        print("\n构建个股板块映射缓存...")
        cache = build_stock_sector_cache(results)
        save_sector_cache(cache)
        print("  完成! 可供 volume_leader_screener 查表使用")

    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.0f}s")

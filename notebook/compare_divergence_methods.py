# -*- coding: utf-8 -*-
"""
对比 czsc 两种背驰算法的信号质量和后续走势：
1. 笔力度背驰 (cxt_first_buy — power_price/volume/length)
2. MACD面积背驰 (zdy_macd_bc — 中枢+MACD红绿柱面积)

问题：信号出现后，真的反转了还是继续原方向？
"""
import os, sys, csv, json
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from notebook.explore_xgb import (
    load_csv, to_df, rows_to_bars, _dt_clean,
    _detect_segments, _compute_macd_divergence
)
from czsc import CZSC, Freq
from notebook.chanlun.signals import get_structure_info, get_zs_info

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACKING = os.path.join(PROJECT, "signals", "tracking")


def compare_codes(codes, look_ahead=20):
    """遍历标的，对比两种背驰的信号 + 后续走势"""
    records = []

    for ci, code in enumerate(codes):
        if ci % 5 == 0:
            print(f"  [{ci+1}/{len(codes)}] {code}")

        try:
            daily_rows = load_csv(code, "daily")
            if len(daily_rows) < 200:
                continue

            bars_daily = rows_to_bars(daily_rows, code, Freq.D)
            c_daily = CZSC(bars_daily, max_bi_num=500)
            struct = get_structure_info(c_daily)
            bi_list = struct.get("bi_list", [])
            daily_zs = get_zs_info(c_daily)

            row_dates = [_dt_clean(r.get("date", "")) for r in daily_rows]
            segments = _detect_segments(bi_list)

            # 方法1: 段内笔力度背驰 (czsc一买)
            bar_bili = _detect_bili_divergence(bi_list, row_dates, segments)

            # 方法2: MACD面积背驰
            bar_macd = _compute_macd_divergence(bi_list, row_dates, daily_rows, segments, th=0.5)

            # 统计每个信号出现后的走势
            close_vals = [float(r.get("close", 0) or 0) for r in daily_rows]
            n = len(daily_rows)

            for i in range(n):
                bili_v = bar_bili.get(i, 0)
                macd_v = bar_macd.get(i, 0)

                if abs(bili_v) < 0.01 and abs(macd_v) < 0.01:
                    continue

                # 未来 look_ahead 天的涨跌幅
                future_idx = min(i + look_ahead, n - 1)
                if future_idx <= i or close_vals[i] <= 0:
                    continue
                fwd_ret = (close_vals[future_idx] - close_vals[i]) / close_vals[i]

                # 未来20天内最大涨幅和最大跌幅
                future_prices = close_vals[i:future_idx+1]
                max_up = max(future_prices) / close_vals[i] - 1 if len(future_prices) > 1 else 0
                max_down = min(future_prices) / close_vals[i] - 1 if len(future_prices) > 1 else 0

                records.append({
                    "code": code,
                    "date": row_dates[i],
                    "close": close_vals[i],
                    "bili_div": round(bili_v, 4),
                    "macd_div": round(macd_v, 4),
                    "fwd_ret": round(fwd_ret, 4),
                    "max_up": round(max_up, 4),
                    "max_down": round(max_down, 4),
                    "agree": abs(bili_v) > 0.01 and abs(macd_v) > 0.01 and (bili_v * macd_v > 0),
                    "bili_only": abs(bili_v) > 0.01 and abs(macd_v) < 0.01,
                    "macd_only": abs(bili_v) < 0.01 and abs(macd_v) > 0.01,
                })

        except Exception as e:
            continue

    return pd.DataFrame(records)


def _detect_bili_divergence(bi_list, row_dates, segments):
    """笔力度背驰 — 模拟 cxt_first_buy/一买的背驰逻辑"""
    n = len(row_dates)
    bar_div = {i: 0.0 for i in range(n)}

    for s_bi, e_bi, seg_dir in segments:
        bis = bi_list[s_bi:e_bi]
        if len(bis) < 3:
            continue
        first, last = bis[0], bis[-1]

        chg_f = abs(first.get("change", 0) or 0)
        chg_l = abs(last.get("change", 0) or 0)
        vol_f = first.get("power_volume", 0) or 0
        vol_l = last.get("power_volume", 0) or 0
        len_f = first.get("length", 0) or 1
        len_l = last.get("length", 0) or 1

        r_price = 1 - chg_l / chg_f if chg_f > 0.001 else 0
        r_vol = 1 - vol_l / vol_f if vol_f > 0.001 else 0
        r_len = 1 - len_l / len_f

        if seg_dir == -1 and r_price > 0 and (r_vol > 0 or r_len > 0):
            score = min(1.0, r_price)
        elif seg_dir == 1 and r_price > 0 and (r_vol > 0 or r_len > 0):
            score = max(-1.0, -r_price)
        else:
            continue

        sdt = _dt_clean(last.get("sdt", ""))
        edt = _dt_clean(last.get("edt", ""))
        for i, rd in enumerate(row_dates):
            if sdt <= rd <= edt:
                bar_div[i] = score

    return bar_div


# ================================
# 主流程
# ================================
all_codes = sorted([
    d for d in os.listdir(TRACKING)
    if os.path.isdir(os.path.join(TRACKING, d))
    and os.path.exists(os.path.join(TRACKING, d, "daily_signals.csv"))
])
print(f"标的: {len(all_codes)} 只\n")

df = compare_codes(all_codes, look_ahead=20)
print(f"\n总信号事件: {len(df)}")

# === 信号重合度 ===
total = len(df)
agree = df["agree"].sum()
bili_only = df["bili_only"].sum()
macd_only = df["macd_only"].sum()
both_signal = (df["bili_div"].abs() > 0.01) | (df["macd_div"].abs() > 0.01)

print(f"\n{'='*60}")
print(f"信号重合度分析")
print(f"{'='*60}")
print(f"  两种同时有信号: {agree} ({agree/total*100:.1f}%)")
print(f"  仅笔力度有信号: {bili_only} ({bili_only/total*100:.1f}%)")
print(f"  仅MACD面积有信号: {macd_only} ({macd_only/total*100:.1f}%)")

# === 后续走势 ===
print(f"\n{'='*60}")
print(f"底背驰(看涨)信号后走势")
print(f"{'='*60}")

for label, mask in [
    ("笔力度底背驰", df["bili_div"] > 0.01),
    ("MACD底背驰", df["macd_div"] > 0.01),
    ("两者共同", (df["bili_div"] > 0.01) & (df["macd_div"] > 0.01)),
    ("仅笔力度", (df["bili_div"] > 0.01) & (df["macd_div"].abs() < 0.01)),
    ("仅MACD面积", (df["macd_div"] > 0.01) & (df["bili_div"].abs() < 0.01)),
]:
    sub = df[mask]
    if len(sub) < 3:
        continue
    avg_ret = sub["fwd_ret"].mean()
    win_rate = (sub["fwd_ret"] > 0).mean()
    avg_up = sub["max_up"].mean()
    avg_down = sub["max_down"].mean()
    print(f"  {label:<20s}: n={len(sub):>4d} | 平均收益={avg_ret:>+.2%} | 胜率={win_rate:>6.2%} | "
          f"最大涨幅={avg_up:>+.2%} | 最大回撤={avg_down:>+.2%}")

print(f"\n{'='*60}")
print(f"顶背驰(看跌)信号后走势")
print(f"{'='*60}")

for label, mask in [
    ("笔力度顶背驰", df["bili_div"] < -0.01),
    ("MACD顶背驰", df["macd_div"] < -0.01),
    ("两者共同", (df["bili_div"] < -0.01) & (df["macd_div"] < -0.01)),
    ("仅笔力度", (df["bili_div"] < -0.01) & (df["macd_div"].abs() < 0.01)),
    ("仅MACD面积", (df["macd_div"] < -0.01) & (df["bili_div"].abs() < 0.01)),
]:
    sub = df[mask]
    if len(sub) < 3:
        continue
    avg_ret = sub["fwd_ret"].mean()
    win_rate = (sub["fwd_ret"] < 0).mean()  # 看跌预期下跌=胜
    avg_up = sub["max_up"].mean()
    avg_down = sub["max_down"].mean()
    print(f"  {label:<20s}: n={len(sub):>4d} | 平均收益={avg_ret:>+.2%} | 看跌胜率={win_rate:>6.2%} | "
          f"最大涨幅={avg_up:>+.2%} | 最大回撤={avg_down:>+.2%}")

# === 相关性 ===
both = df[(df["bili_div"].abs() > 0.001) & (df["macd_div"].abs() > 0.001)]
if len(both) > 5:
    corr = both["bili_div"].corr(both["macd_div"])
    print(f"\n{'='*60}")
    print(f"两种背驰分数的相关性 (仅同时有信号时)")
    print(f"{'='*60}")
    print(f"  Pearson r = {corr:.4f}")
    print(f"  (n = {len(both)})")
else:
    print(f"\n两种背驰同时有信号的样本太少，无法计算相关性")

print(f"\n完成。")

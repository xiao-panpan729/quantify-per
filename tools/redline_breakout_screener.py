# -*- coding: utf-8 -*-
"""
牛熊红线突破 × 量化势能 组合选股器
====================================

三条件筛选：
1. 长期被牛熊红线（MA221 + 3σ）压制 → 蓄力
2. 股价突破牛熊红线 → 点火
3. 量化势能 (x₁) ≥ 8 → 爆发确认

用法:
    python tools/redline_breakout_screener.py
    python tools/redline_breakout_screener.py --top 20
    python tools/redline_breakout_screener.py --x1-threshold 9
    python tools/redline_breakout_screener.py --suppression 0.7
"""

import sys
import os
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import config

# Windows 终端编码
if sys.stdout.encoding and sys.stdout.encoding.lower() in ('gbk', 'cp936'):
    sys.stdout.reconfigure(encoding='utf-8')

from tools.x1_screener import load_all_a_stocks
from tools.narrative_lookup import query_stock_narrative

# ── 参数 ──
SUPPRESSION_WINDOW = 60    # 回看 N 天评估"长期压制"
SUPPRESSION_RATIO = 0.80   # 压制天数 / 总天数 ≥ 此值
X1_THRESHOLD = 8           # 势能门槛
MAX_ABOVE_DAYS = 60        # 突破后最多多少天（太久了就不算"刚突破"）
MIN_TOTAL_BARS = 250       # 最少需要 250 根日线
MAX_RED_CROSSINGS = 3      # 过去120天红线穿越次数上限（排除反复横跳）
MAX_X1_ABOVE8_EVENTS = 3   # 过去120天势能>8独立事件次数上限（排除势能走完一轮又回来）

# 板块映射缓存
_SECTOR_CACHE = None


def load_sector_map():
    """加载板块映射（从 x1_screener 已缓存的 mapping）"""
    global _SECTOR_CACHE
    if _SECTOR_CACHE is not None:
        return _SECTOR_CACHE
    try:
        from tools.x1_screener import load_sector_mapping
        _SECTOR_CACHE = load_sector_mapping()
    except Exception:
        _SECTOR_CACHE = {}
    return _SECTOR_CACHE


def calc_bull_bear_line(closes, period=221, std_mult=3):
    """
    牛熊红线 = MA(CLOSE, N) + M * STD(CLOSE, N)
    O(n) 前缀和实现
    返回 (ma_line, red_line)
    """
    n = len(closes)
    if n < period:
        return None, None

    pref = np.cumsum(closes)
    pref2 = np.cumsum(closes ** 2)
    ma_line = np.zeros(n)
    red_line = np.zeros(n)

    for i in range(n):
        start = max(0, i - period + 1)
        cnt = i + 1 - start
        s = pref[i] - (pref[start - 1] if start > 0 else 0)
        m = s / cnt
        var = (pref2[i] - (pref2[start - 1] if start > 0 else 0)) / cnt - m * m
        var = max(0, var)
        std = var ** 0.5
        ma_line[i] = round(m, 4)
        red_line[i] = round(m + std_mult * std, 4)

    return ma_line, red_line


def main():
    t_start = time.time()

    # ── 加载全 A 股日线 + 预计算 x1 ──
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 加载全 A 股数据 + 预计算势能（首次约 60s）...")
    stock_data = load_all_a_stocks(precompute_x1=True)
    n_total = len(stock_data)
    print(f"  加载完毕: {n_total} 只")

    # ── 加载板块映射 ──
    sector_map = load_sector_map()
    n_sectors = len(sector_map.get('stock_sectors', {})) if sector_map else 0
    print(f"  板块映射: {n_sectors} 只")

    # ── 逐股筛选 ──
    candidates = []
    t_scan = time.time()
    checked = 0

    for code, info in stock_data.items():
        dates = info['dates']
        close = info['close']
        name = info.get('name', '')
        x1_series = info.get('x1_series', None)

        n_bars = len(dates)
        if n_bars < MIN_TOTAL_BARS:
            continue

        # 计算牛熊红线
        bb_ma, bb_red = calc_bull_bear_line(close)
        if bb_red is None:
            continue

        checked += 1
        if checked % 500 == 0:
            print(f"  扫描进度: {checked}/{n_total}...", end='\r')

        last_idx = n_bars - 1
        cur_close = float(close[last_idx])
        cur_red = float(bb_red[last_idx])
        cur_x1 = float(x1_series[last_idx]) if x1_series is not None else 0

        # ── 条件 1: 突破红线 ──
        if cur_close <= cur_red:
            continue

        # ── 条件 2: 势能达标 ──
        if cur_x1 < X1_THRESHOLD:
            continue

        # ── 计算突破天数（从最后一根往前推，直到跌破红线） ──
        days_above = 0
        for i in range(last_idx, -1, -1):
            if close[i] > bb_red[i]:
                days_above += 1
            else:
                break

        if days_above > MAX_ABOVE_DAYS:
            continue

        # ── 新鲜度检查：过去 N 天内的红线穿越次数 + 势能>8事件次数 ──
        freshness_window = 120
        fw_start = max(0, last_idx - freshness_window)
        crossings = 0
        x1_above8_events = 0
        was_above = close[fw_start] > bb_red[fw_start]
        was_x1_above8 = x1_series[fw_start] >= 8 if x1_series is not None else False

        for i in range(fw_start + 1, last_idx + 1):
            is_above = close[i] > bb_red[i]
            if is_above != was_above:
                crossings += 1
                was_above = is_above

            if x1_series is not None:
                is_x1_above8 = x1_series[i] >= 8
                if is_x1_above8 and not was_x1_above8:
                    x1_above8_events += 1
                was_x1_above8 = is_x1_above8

        if crossings > MAX_RED_CROSSINGS:
            continue
        if x1_above8_events > MAX_X1_ABOVE8_EVENTS:
            continue

        # ── 条件 3: 突破前被长期压制（取突破前 SUPPRESSION_WINDOW 天） ──
        window_end = max(0, last_idx - days_above)  # 压制窗口截止到突破日
        window_start = max(0, window_end - SUPPRESSION_WINDOW)
        below_mask = close[window_start:window_end] < bb_red[window_start:window_end]
        below_count = np.sum(below_mask)
        total_in_window = window_end - window_start
        ratio = below_count / total_in_window if total_in_window > 0 else 0

        if ratio < SUPPRESSION_RATIO:
            continue

        # ── 压制深度（红线下方时的最大偏离） ──
        below_indices = np.where(below_mask)[0]
        if len(below_indices) > 0 and total_in_window > 0:
            below_close = close[window_start:window_end][below_indices]
            below_red = bb_red[window_start:window_end][below_indices]
            deviations = (below_close - below_red) / below_red * 100
            max_deviation = float(np.min(deviations))
            avg_deviation = float(np.mean(deviations))
        else:
            max_deviation = 0.0
            avg_deviation = 0.0

        # ── 突破强度（突破后的势能变化） ──
        if x1_series is not None and days_above > 1:
            break_idx = last_idx - days_above + 1
            x1_at_break = float(x1_series[break_idx]) if break_idx >= 0 else 0
            x1_momentum = cur_x1 - x1_at_break
        else:
            x1_momentum = cur_x1

        distance_pct = (cur_close - cur_red) / cur_red * 100

        # 板块（6位代码匹配）
        code_6 = code[2:] if len(code) > 6 else code
        sectors = sector_map.get('stock_sectors', {}).get(code_6, [])

        # 叙事链评分
        narr = query_stock_narrative(code_6)
        raw_grade = narr['best_grade'] or 'D'
        narrative_grade = '未覆盖' if raw_grade == 'U' else raw_grade
        narrative_chain = narr['best_chain'] or '—'

        candidates.append({
            'code': code,
            'name': name,
            'close': cur_close,
            'bb_red': cur_red,
            'distance': distance_pct,
            'x1': cur_x1,
            'x1_momentum': x1_momentum,
            'suppression': ratio,
            'days_above': days_above,
            'max_deviation': max_deviation,
            'avg_deviation': avg_deviation,
            'crossings': crossings,
            'x1_above8_events': x1_above8_events,
            'sectors': sectors,
            'narrative_grade': narrative_grade,
            'narrative_chain': narrative_chain,
        })

    print(f"\n  扫描完成: {checked} 只达标检查, 用时 {time.time()-t_scan:.1f}s")

    # ── 排序 ──
    # 主排序：势能降序；次排序：压制比降序（压制越久越好）
    candidates.sort(key=lambda x: (-x['x1'], -x['suppression'], x['days_above']))

    # ── 输出 ──
    elapsed = time.time() - t_start
    print(f"\n{'='*130}")
    print(f"牛熊红线突破 × 量化势能 组合选股")
    print(f"条件: 势能≥{X1_THRESHOLD} | 压制比≥{SUPPRESSION_RATIO} ({SUPPRESSION_WINDOW}天) | 突破≤{MAX_ABOVE_DAYS}天")
    print(f"新鲜度: 穿越≤{MAX_RED_CROSSINGS}次 | 势能>8事件≤{MAX_X1_ABOVE8_EVENTS}次 (回看120天)")
    print(f"全市场 {n_total} 只 → 通过 {len(candidates)} 只 | 总耗时 {elapsed:.1f}s")
    print(f"{'='*130}")

    if not candidates:
        print("当前无符合条件标的。")
        return

    # 表头
    hdr = f"{'#':>3} {'代码':>8} {'名称':>6} {'收盘':>8} {'红线':>8} {'偏离%':>6} {'势能':>5} {'穿越':>4} {'势能>8':>6} {'压制比':>5} {'突破天':>5} {'叙事':>4}"
    print(hdr)
    print('-' * 130)

    for i, r in enumerate(candidates[:TOP_N]):
        print(
            f"{i+1:3d} {r['code']:>8s} {r['name']:>6s} "
            f"{r['close']:>8.2f} {r['bb_red']:>8.2f} "
            f"{r['distance']:>+6.2f}% {r['x1']:>5.1f} "
            f"{r['crossings']:>4d} {r['x1_above8_events']:>6d} "
            f"{r['suppression']:.0%}  {r['days_above']:>3d}天 "
            f"{r['narrative_grade']:>4s}"
        )

    if len(candidates) > TOP_N:
        print(f"\n  ... 还有 {len(candidates)-TOP_N} 只标的未显示（--top N 查看更多）")

    # ── 汇总统计 ──
    print(f"\n{'─'*130}")
    print(f"汇总: 共 {len(candidates)} 只通过 | "
          f"势能均值: {np.mean([r['x1'] for r in candidates]):.1f} | "
          f"压制比均值: {np.mean([r['suppression'] for r in candidates]):.0%} | "
          f"突破天数中位数: {np.median([r['days_above'] for r in candidates]):.0f}天")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='牛熊红线 × 量化势能 组合选股')
    parser.add_argument('--top', type=int, default=30, help='显示前 N 只')
    parser.add_argument('--x1-threshold', type=float, default=8, help='势能门槛 (默认 8)')
    parser.add_argument('--suppression', type=float, default=0.8, help='压制比门槛 (默认 0.8)')
    parser.add_argument('--suppression-window', type=int, default=60, help='压制回看天数 (默认 60)')
    parser.add_argument('--max-above', type=int, default=60, help='突破后最长时间 (默认 60天)')
    parser.add_argument('--max-crossings', type=int, default=3, help='红线穿越上限 (默认 3次)')
    parser.add_argument('--max-x1-events', type=int, default=3, help='势能>8事件上限 (默认 3次)')
    args = parser.parse_args()

    SUPPRESSION_WINDOW = args.suppression_window
    SUPPRESSION_RATIO = args.suppression
    X1_THRESHOLD = args.x1_threshold
    MAX_ABOVE_DAYS = args.max_above
    MAX_RED_CROSSINGS = args.max_crossings
    MAX_X1_ABOVE8_EVENTS = args.max_x1_events
    TOP_N = args.top

    main()

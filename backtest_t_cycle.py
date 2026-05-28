# -*- coding: utf-8 -*-
"""
做T配对回测 — CCI顶背驰(1分钟) vs 减仓卖(5分钟)

卖信号 → 买回信号, 算完整周期收益
"""

import sys, os, json, csv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collections import defaultdict
from signal_engine import (read_bars_lc1, read_bars,
                           _calc_signals_from_arrays, _calc_pe_rolling,
                           TREND_PERIOD_MIN_SHORT, TREND_PERIOD_MIN)

# ─── 参数 ───
TOTAL_BARS_1M = 6000      # 1分钟: 25天
TOTAL_BARS_5M = 1200      # 5分钟: 25天 × 48根/天 = 1200
SKIP_BARS = 200
MIN_PRICE_FACTOR = 10000
T_STOP_PCT = 2.0          # 涨超2%创新高强制止损买回
LOOKAHEAD = 80            # 最大等待80根bar (1分钟=80分钟, 5分钟=400分钟)

TDX_BASE = r'C:\zd_cjzq\vipdoc'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_universe():
    path = os.path.join(BASE_DIR, 'signals', 'tracking', 'volume_leader_universe.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    codes = data.get('universe', [])
    names = {}
    try:
        from config import NAME_MAP
        names.update(NAME_MAP)
    except ImportError:
        pass
    csv_path = os.path.join(BASE_DIR, 'signals', 'tracking', 'stock_names.csv')
    if os.path.exists(csv_path):
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                code = r.get('code', '').strip()
                name = r.get('name', '').strip()
                if code and name:
                    names[code] = name
    return [(code, names.get(code, code)) for code in codes]


def load_1min(code):
    mkt = code[:2]
    path = f'{TDX_BASE}/{mkt}/minline/{code}.lc1'
    bars = read_bars_lc1(path)
    if not bars: return None
    needed = TOTAL_BARS_1M + SKIP_BARS + 200
    if len(bars) < needed: return None
    return bars[-needed:]


def load_5min(code):
    mkt = code[:2]
    path = f'{TDX_BASE}/{mkt}/fzline/{code}.lc5'
    bars = read_bars(path)
    if not bars: return None
    needed = TOTAL_BARS_5M + SKIP_BARS + 100
    if len(bars) < needed: return None
    return bars[-needed:]


def compute_signals(bars, trend_period):
    opens = [float(bar[1]) for bar in bars]
    highs = [float(bar[2]) for bar in bars]
    lows = [float(bar[3]) for bar in bars]
    closes = [float(bar[4]) for bar in bars]
    vols = [bar[6] for bar in bars]
    amts = [bar[5] for bar in bars]
    timestamps = [bar[0] for bar in bars]
    rows = _calc_signals_from_arrays(opens, highs, lows, closes, vols, amts, timestamps, trend_period)
    if rows:
        rows = _calc_pe_rolling(rows)
    return rows


def run_t_cycle(rows, code, name, period_label, sell_signal_type):
    """
    做T配对回测: 卖信号入场 → 买回信号/止损出场

    sell_signal_type: 'cci_div' | 'reduce_5m'
    """
    if not rows or len(rows) < SKIP_BARS + LOOKAHEAD:
        return []

    n = len(rows)
    trades = []
    in_trade = False
    sell_idx = None
    sell_price = None

    for i in range(SKIP_BARS, n):
        r = rows[i]
        bar_close = float(r['close'])
        bar_high = float(r['high'])

        if not in_trade:
            # 检测卖出信号
            hit = False
            if sell_signal_type == 'cci_div':
                hit = (r.get('cci_divergence', '') or '').strip() == '顶背驰'
            elif sell_signal_type == 'reduce_5m':
                has_star = bool((r.get('sell_signal', '') or '').strip())
                if has_star:
                    try:
                        close_below_ma5 = float(r['close']) < float(r.get('ma5', 0))
                    except (ValueError, TypeError):
                        close_below_ma5 = False
                    hit = close_below_ma5

            if hit:
                in_trade = True
                sell_idx = i
                sell_price = bar_close
        else:
            # 检测买回信号
            has_golden = (r.get('expma_cross', '') or '').strip() == '金叉'
            has_buy = bool((r.get('buy_signal', '') or '').strip())

            buy_ok = has_buy or has_golden  # ★买 或 金叉 = 买回

            # 止损: 涨超2%
            rise_pct = (bar_close - sell_price) / sell_price * 100
            stop_hit = rise_pct >= T_STOP_PCT

            # 超时: 超过LOOKAHEAD根bar还没买回
            too_long = (i - sell_idx) >= LOOKAHEAD

            if buy_ok or stop_hit or too_long:
                buyback_price = bar_close
                ret_pct = (sell_price - buyback_price) / sell_price * 100

                if stop_hit:
                    reason = '止损'
                elif too_long:
                    reason = '超时'
                elif has_buy and has_golden:
                    reason = '★买+金叉'
                elif has_buy:
                    reason = '★买'
                else:
                    reason = '金叉'

                # 持仓期间的最大浮盈/浮亏
                mfe = 0.0  # 最大浮盈 (作为空头, 跌了=赚)
                mae = 0.0  # 最大浮亏 (涨了=亏)
                for j in range(sell_idx + 1, i + 1):
                    c = float(rows[j]['close'])
                    pnl = (sell_price - c) / sell_price * 100
                    if pnl > mfe:
                        mfe = pnl
                    if pnl < mae:
                        mae = pnl

                trades.append({
                    'code': code, 'name': name, 'period': period_label,
                    'sell_date': str(rows[sell_idx].get('date', '')),
                    'buy_date': str(r.get('date', '')),
                    'sell_price': round(sell_price / MIN_PRICE_FACTOR, 4),
                    'buyback_price': round(buyback_price / MIN_PRICE_FACTOR, 4),
                    'ret_pct': round(ret_pct, 2),
                    'mfe_pct': round(mfe, 2),
                    'mae_pct': round(mae, 2),
                    'hold_bars': i - sell_idx,
                    'exit_reason': reason,
                })
                in_trade = False
                sell_idx = None
                sell_price = None

    return trades


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def main():
    stocks = load_universe()
    print(f'标的: {len(stocks)}只')
    print()

    # ─── 第一轮: 1分钟 CCI顶背驰做T ───
    print('=' * 100)
    print('  [1/2] 1分钟 CCI顶背驰做T — 卖出: CCI顶背驰 → 买回: ★买/金叉')
    print('=' * 100)
    t1_trades = []
    ok1 = 0
    for code, name in stocks:
        bars = load_1min(code)
        if not bars: continue
        rows = compute_signals(bars, TREND_PERIOD_MIN_SHORT)
        if not rows or len(rows) < SKIP_BARS + LOOKAHEAD: continue
        trades = run_t_cycle(rows, code, name, '1m', 'cci_div')
        t1_trades.extend(trades)
        ok1 += 1
        if trades:
            wins = sum(1 for t in trades if t['ret_pct'] > 0)
            n = len(trades)
            avg_ret = sum(t['ret_pct'] for t in trades) / n
            avg_hold = sum(t['hold_bars'] for t in trades) / n
            print(f'  [{ok1:>2}] {code} {name:<10}  {n}笔  胜率:{wins/n*100:.0f}%  均收益:{avg_ret:+.2f}%  均持:{avg_hold:.0f}根')
        else:
            print(f'  [{ok1:>2}] {code} {name:<10}  0笔')

    print(f'\n  有效标的: {ok1}')

    # ─── 第二轮: 5分钟 减仓卖做T ───
    print(f'\n{"="*100}')
    print(f'  [2/2] 5分钟 减仓卖做T — 卖出: ★卖+close<MA5 → 买回: ★买/金叉')
    print(f'{"="*100}')
    t5_trades = []
    ok5 = 0
    for code, name in stocks:
        bars = load_5min(code)
        if not bars: continue
        rows = compute_signals(bars, TREND_PERIOD_MIN_SHORT)
        if not rows or len(rows) < SKIP_BARS + LOOKAHEAD: continue
        trades = run_t_cycle(rows, code, name, '5m', 'reduce_5m')
        t5_trades.extend(trades)
        ok5 += 1
        if trades:
            wins = sum(1 for t in trades if t['ret_pct'] > 0)
            n = len(trades)
            avg_ret = sum(t['ret_pct'] for t in trades) / n
            avg_hold = sum(t['hold_bars'] for t in trades) / n
            print(f'  [{ok5:>2}] {code} {name:<10}  {n}笔  胜率:{wins/n*100:.0f}%  均收益:{avg_ret:+.2f}%  均持:{avg_hold:.0f}根')
        else:
            print(f'  [{ok5:>2}] {code} {name:<10}  0笔')

    print(f'\n  有效标的: {ok5}')

    # ─── 汇总对比 ───
    for label, trades in [('1分钟 CCI顶背驰做T', t1_trades), ('5分钟 减仓卖做T', t5_trades)]:
        if not trades:
            print(f'\n  {label}: 无配对')
            continue
        n = len(trades)
        wins = sum(1 for t in trades if t['ret_pct'] > 0)
        wr = wins / n * 100
        avg_ret = sum(t['ret_pct'] for t in trades) / n
        avg_mfe = sum(t['mfe_pct'] for t in trades) / n
        avg_mae = sum(t['mae_pct'] for t in trades) / n
        avg_hold = sum(t['hold_bars'] for t in trades) / n
        max_ret = max(t['ret_pct'] for t in trades)
        min_ret = min(t['ret_pct'] for t in trades)

        # 分出場方式
        by_reason = defaultdict(list)
        for t in trades:
            by_reason[t['exit_reason']].append(t)

        print(f'\n{"="*100}')
        print(f'  {label}')
        print(f'{"="*100}')
        print(f'  总配对: {n}笔  胜率: {wr:.1f}%  均收益: {avg_ret:+.2f}%  MFE: {avg_mfe:+.2f}%  MAE: {avg_mae:+.2f}%  均持: {avg_hold:.0f}根')
        print(f'  收益范围: {min_ret:+.2f}% ~ {max_ret:+.2f}%')
        print(f'\n  {"出场方式":<18} {"笔数":>5} {"胜率":>7} {"均收益":>8} {"均持(根)":>9}')
        print(f'  {"-"*50}')
        for reason in sorted(by_reason):
            items = by_reason[reason]
            nn = len(items)
            w = len([t for t in items if t['ret_pct'] > 0]) / nn * 100
            a = sum(t['ret_pct'] for t in items) / nn
            ah = sum(t['hold_bars'] for t in items) / nn
            print(f'  {reason:<18} {nn:>5} {w:>6.1f}% {a:>+7.2f}% {ah:>8.0f}根')

        # 收益分布
        print(f'\n  {"收益分布":}')
        bins = [(-100, -5), (-5, -2), (-2, -1), (-1, 0), (0, 1), (1, 2), (2, 5), (5, 100)]
        for lo, hi in bins:
            count = sum(1 for t in trades if lo <= t['ret_pct'] < hi)
            if count > 0:
                bar = '#' * max(1, count // 3)
            else:
                bar = ''
            label = f'{lo}~{hi}%'
            print(f'    {label:>10}: {count:>4} {bar}')

    # ─── 结论 ───
    print(f'\n{"="*100}')
    print(f'  ★ 对比总结')
    print(f'{"="*100}')
    for label, trades in [('1分钟 CCI顶背驰', t1_trades), ('5分钟 减仓卖', t5_trades)]:
        if not trades:
            continue
        n = len(trades)
        wins = sum(1 for t in trades if t['ret_pct'] > 0)
        avg_ret = sum(t['ret_pct'] for t in trades) / n
        avg_mfe = sum(t['mfe_pct'] for t in trades) / n
        print(f'  {label:<20}  {n:>4}笔  胜率:{wins/n*100:.0f}%  均收益:{avg_ret:+.2f}%  MFE:{avg_mfe:+.2f}%')
    print()


if __name__ == '__main__':
    main()

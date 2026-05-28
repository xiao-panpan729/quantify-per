# -*- coding: utf-8 -*-
"""
1分钟卖信号对比回测 v3 — 成交量强者universe 全量回测

对比:
  - sell_any (裸★卖)
  - sell_death (死叉)
  - sell_cci_div (CCI顶背驰)
  - sell_reduce (减仓: ★卖+close<MA5+环境过滤)

数据源: 直读通达信 .lc1, 内存算信号, 不写CSV
标的: volume_leader_universe.json (~43只)
"""

import sys, os, json, csv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collections import defaultdict
from signal_engine import (read_bars_lc1,
                           _calc_signals_from_arrays, _calc_pe_rolling,
                           TREND_PERIOD_MIN_SHORT)

# ─── 参数 ───
TOTAL_BARS = 6000       # 25天 × 240根/天
SKIP_BARS = 200
LOOKAHEAD = 40          # 卖信号后看未来N根bar (40分钟)
MIN_PRICE_FACTOR = 10000

# 减仓环境过滤参数
NO_GOLDEN_WINDOW = 100   # 100根1分钟 ≈ 100分钟

TDX_BASE = r'C:\zd_cjzq\vipdoc'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_universe():
    """从 volume_leader_universe.json 加载标的列表"""
    path = os.path.join(BASE_DIR, 'signals', 'tracking', 'volume_leader_universe.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    codes = data.get('universe', [])

    # 加载名称映射
    names = {}
    # 先从 config.NAME_MAP
    try:
        from config import NAME_MAP
        names.update(NAME_MAP)
    except ImportError:
        pass
    # 再从 stock_names.csv
    csv_path = os.path.join(BASE_DIR, 'signals', 'tracking', 'stock_names.csv')
    if os.path.exists(csv_path):
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                code = r.get('code', '').strip()
                name = r.get('name', '').strip()
                if code and name:
                    names[code] = name

    stocks = []
    for code in codes:
        name = names.get(code, code)
        stocks.append((code, name))
    return stocks


def load_bars(code):
    """直读通达信1分钟线, 返回最后 TOTAL_BARS+buffer 条"""
    mkt = code[:2]
    path = f'{TDX_BASE}/{mkt}/minline/{code}.lc1'
    bars = read_bars_lc1(path)
    if not bars:
        return None
    needed = TOTAL_BARS + SKIP_BARS + 200
    if len(bars) < needed:
        return None  # 数据不够
    return bars[-needed:]


def compute_signals(bars):
    """在内存中计算全部信号"""
    opens = [float(bar[1]) for bar in bars]
    highs = [float(bar[2]) for bar in bars]
    lows = [float(bar[3]) for bar in bars]
    closes = [float(bar[4]) for bar in bars]
    vols = [bar[6] for bar in bars]
    amts = [bar[5] for bar in bars]
    timestamps = [bar[0] for bar in bars]

    rows = _calc_signals_from_arrays(
        opens, highs, lows, closes, vols, amts, timestamps, TREND_PERIOD_MIN_SHORT)
    if rows:
        rows = _calc_pe_rolling(rows)
    return rows


def _close_below(row, ma_col):
    try:
        return float(row['close']) < float(row.get(ma_col, 0))
    except (ValueError, TypeError):
        return False


def _no_recent_golden(rows, i, n=100):
    for j in range(i - 1, max(0, i - n - 1), -1):
        cross = (rows[j].get('expma_cross', '') or '').strip()
        if cross == '金叉':
            return False
        if cross == '死叉':
            return True
    return True


def backtest_sell(rows, code, name, sell_mode):
    """回测卖信号效果"""
    if not rows or len(rows) < SKIP_BARS + LOOKAHEAD:
        return []

    n = len(rows)

    is_below_ema50 = []
    for r in rows:
        try:
            c = float(r.get('close', 0))
            e50 = float(r.get('expma50', 0) or 0)
            is_below_ema50.append(e50 > 0 and c < e50)
        except (ValueError, TypeError):
            is_below_ema50.append(False)

    signals = []
    for i in range(SKIP_BARS, n - LOOKAHEAD):
        r = rows[i]

        hit = False
        if sell_mode == 'sell_any':
            hit = bool((r.get('sell_signal', '') or '').strip())
        elif sell_mode == 'sell_death':
            hit = (r.get('expma_cross', '') or '').strip() == '死叉'
        elif sell_mode == 'sell_cci_div':
            hit = (r.get('cci_divergence', '') or '').strip() == '顶背驰'
        elif sell_mode == 'sell_reduce':
            has_star = bool((r.get('sell_signal', '') or '').strip())
            hit = has_star and _close_below(r, 'ma5') and \
                  _no_recent_golden(rows, i, NO_GOLDEN_WINDOW) and \
                  is_below_ema50[i]

        if not hit:
            continue

        entry_price = float(r['close'])

        max_drop = 0.0
        max_rise = 0.0
        has_death = False

        for j in range(i + 1, i + LOOKAHEAD + 1):
            c = float(rows[j]['close'])
            ret = (c - entry_price) / entry_price * 100
            if ret < max_drop:
                max_drop = ret
            if ret > max_rise:
                max_rise = ret
            if not has_death:
                cross = (rows[j].get('expma_cross', '') or '').strip()
                if cross == '死叉':
                    has_death = True

        t_gain = abs(max_drop) if max_drop < 0 else 0.0

        signals.append({
            'code': code,
            'name': name,
            'date': str(r.get('date', '')),
            'entry_price': round(entry_price / MIN_PRICE_FACTOR, 4),
            'max_drop': round(max_drop, 2),
            'max_rise': round(max_rise, 2),
            't_gain': round(t_gain, 2),
            'has_death': has_death,
            'win': max_drop < 0,
            't_win': max_drop <= -1.0,
            't_strong': max_drop <= -2.0,
        })

    return signals


def print_mode_row(label, sigs, pad_label=30):
    """打印一行统计"""
    if not sigs:
        print(f'  {label:<{pad_label}} {"-" * 6}     -        -        -        -        -        -')
        return None
    n = len(sigs)
    win_n = sum(1 for s in sigs if s['win'])
    t_wins = sum(1 for s in sigs if s['t_win'])
    t_strong = sum(1 for s in sigs if s['t_strong'])
    avg_drop = sum(s['max_drop'] for s in sigs) / n
    avg_rise = sum(s['max_rise'] for s in sigs) / n
    avg_t = sum(s['t_gain'] for s in sigs) / n
    death_n = sum(1 for s in sigs if s['has_death'])
    print(f'  {label:<{pad_label}} {n:>5} {win_n/n*100:>6.1f}% {t_wins/n*100:>7.1f}% {t_strong/n*100:>6.1f}% {avg_drop:>+7.2f}% {avg_rise:>+7.2f}% {avg_t:>+7.2f}% {death_n:>4}({death_n/n*100:.0f}%)')
    return {'n': n, 'wr': win_n/n*100, 't_win_rate': t_wins/n*100, 'avg_drop': avg_drop, 'avg_rise': avg_rise,
            'avg_t': avg_t, 'death_rate': death_n/n*100}


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def main():
    SELL_MODES = {
        'sell_any':      '裸★卖',
        'sell_death':    '死叉',
        'sell_cci_div':  'CCI顶背驰',
        'sell_reduce':   '减仓(★卖+破MA5+无金叉100+EMA50下)',
    }

    all_results = {m: [] for m in SELL_MODES}
    by_stock = {m: defaultdict(list) for m in SELL_MODES}

    stocks = load_universe()
    print(f'  标的数量: {len(stocks)}  每只取{TOTAL_BARS}根1分钟bar (≈{TOTAL_BARS/240:.0f}天)')
    print(f'  卖信号后观察: {LOOKAHEAD}根 (={LOOKAHEAD}分钟)')
    print()

    ok_count = 0
    skip_count = 0
    for code, name in stocks:
        bars = load_bars(code)
        if not bars:
            skip_count += 1
            continue

        rows = compute_signals(bars)
        if not rows or len(rows) < SKIP_BARS + LOOKAHEAD:
            skip_count += 1
            continue

        ok_count += 1
        sig_counts = []
        for mode in SELL_MODES:
            sigs = backtest_sell(rows, code, name, mode)
            all_results[mode].extend(sigs)
            by_stock[mode][code].extend(sigs)
            sig_counts.append(len(sigs))

        bar_ts = str(rows[0].get('date', '?'))
        bar_ts2 = str(rows[-1].get('date', '?'))
        print(f'  [{ok_count:>2}/{len(stocks)}] {code} {name:<10}  {len(rows)}根 [{bar_ts[:8]}~{bar_ts2[:8]}]  '
              f'裸★{sig_counts[0]}, 死叉{sig_counts[1]}, CCI{sig_counts[2]}, 减仓{sig_counts[3]}')

    print(f'\n  有效: {ok_count}  跳过(无数据/数据不足): {skip_count}')

    # ─── 总览报告 ───
    days = TOTAL_BARS / 240
    print(f'\n{"="*120}')
    print(f'  1分钟卖信号对比回测 — {TOTAL_BARS}根bar(≈{days:.0f}天) — LOOKAHEAD={LOOKAHEAD}根')
    print(f'  减仓环境: 无金叉窗口={NO_GOLDEN_WINDOW}根(≈{NO_GOLDEN_WINDOW}分钟), EMA50=1分钟自身')
    print(f'  标的: 成交量强者universe × {ok_count}只')
    print(f'{"="*120}')

    header = f'  {"模式":<30} {"信号":>5} {"胜率":>7} {"做T>1%":>8} {"强T>2%":>7} {"均跌幅":>8} {"均反弹":>8} {"均T空间":>8} {"后死叉":>7}'
    print(f'\n{header}')
    print(f'  {"-"*100}')
    stats = {}
    for mode, label in SELL_MODES.items():
        stats[mode] = print_mode_row(label, all_results[mode], pad_label=30)

    # ─── 按标的前10和后10 ───
    print(f'\n  ── 各标的 CCI顶背驰 效果排行 (按胜率) ──')
    stock_ranks = []
    for code, name in stocks:
        sigs = by_stock['sell_cci_div'].get(code, [])
        if len(sigs) >= 3:
            wr = sum(1 for s in sigs if s['win']) / len(sigs) * 100
            t_gain = sum(s['t_gain'] for s in sigs) / len(sigs)
            stock_ranks.append((code, name, len(sigs), wr, t_gain))
    stock_ranks.sort(key=lambda x: -x[3])

    print(f'  {"代码":<12} {"名称":<12} {"信号":>5} {"胜率":>7} {"做T>1%":>7} {"均T空间":>8}')
    print(f'  {"-"*55}')
    for code, name, n, wr, t_gain in stock_ranks[:10]:
        t_rate = sum(1 for s in by_stock['sell_cci_div'][code] if s['t_win']) / n * 100
        print(f'  {code:<12} {name:<12} {n:>5} {wr:>6.1f}% {t_rate:>6.1f}% {t_gain:>+7.2f}%')
    if len(stock_ranks) > 12:
        print(f'  {"..."}')
        for code, name, n, wr, t_gain in stock_ranks[-12:]:
            t_rate = sum(1 for s in by_stock['sell_cci_div'][code] if s['t_win']) / n * 100
            print(f'  {code:<12} {name:<12} {n:>5} {wr:>6.1f}% {t_rate:>6.1f}% {t_gain:>+7.2f}%')

    # ─── 综合排行 ───
    print(f'\n  {"="*120}')
    print(f'  ★ 综合排行 (胜率×1 + 做T>1%×2 + 均T空间×10 + 后死叉率×0.5)')
    print(f'  {"="*120}')
    print()
    ranked = []
    for mode, label in SELL_MODES.items():
        sigs = all_results[mode]
        if not sigs:
            continue
        s = stats[mode]
        if not s:
            continue
        score = s['wr'] + s['t_win_rate'] * 2 + s['avg_t'] * 10 + s['death_rate'] * 0.5
        ranked.append((score, label, s))

    for score, label, s in sorted(ranked, reverse=True):
        bar = '█' * max(1, int(score / 2))
        print(f'  {label:<30} ★{score:5.0f} {bar}')
        print(f'    {s["n"]}个信号  胜率:{s["wr"]:.0f}%  做T>1%:{s["t_win_rate"]:.1f}%  均T空间:{s["avg_t"]:+.2f}%  后死叉率:{s["death_rate"]:.0f}%')
        print()


if __name__ == '__main__':
    main()

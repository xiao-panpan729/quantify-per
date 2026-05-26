"""信号配对回测子系统 — 历史信号的出入场统计"""
import sys
import os
import csv
import json
import argparse
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.volume_leader.shared import load_universe, TRACKING_DIR, MIN_PRICE_FACTOR


# ══════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════

PRICE_F = MIN_PRICE_FACTOR  # 分钟线价格因子
PERIODS = ['min5', 'min15', 'min30']
SKIP_BARS = 200             # 跳过前N根bar（指标未收敛）
BAND_LOOKBACK = 40          # 波段最低点最远回看根数 (min5:40=200分钟=半天)


# ══════════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════════

def _load_csv(code, period):
    path = TRACKING_DIR / code / f'{period}_signals.csv'
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    return rows


def _load_daily_zones(code):
    """返回 {date_str: zone}，zone ∈ {strong, secondary, weak}"""
    rows = _load_csv(code, 'daily')
    if not rows:
        return {}
    zones = {}
    for r in rows:
        d = r.get('date', '').strip()
        if not d:
            continue
        try:
            c = float(r.get('close', 0))
            e12 = float(r.get('expma12', 0))
            e50 = float(r.get('expma50', 0))
        except (ValueError, TypeError):
            continue
        if e12 > 0 and c > e12:
            zones[d] = 'strong'
        elif e50 > 0 and c > e50:
            zones[d] = 'secondary'
        else:
            zones[d] = 'weak'
    return zones


# ══════════════════════════════════════════════════════════════════
# 信号检测
# ══════════════════════════════════════════════════════════════════

def _has_star_buy(row):
    return bool((row.get('buy_signal', '') or '').strip())


def _has_star_sell(row):
    return bool((row.get('sell_signal', '') or '').strip())


def _has_golden(row):
    return (row.get('expma_cross', '') or '').strip() == '金叉'


def _ma_above(row, fast, slow):
    """快速均线 > 慢速均线"""
    try:
        return float(row.get(fast, 0)) > float(row.get(slow, 0))
    except (ValueError, TypeError):
        return False


def _check_ma10x20_cross(rows, i):
    """当前bar MA10 刚刚上穿 MA20（事件，非状态）"""
    if i < 1:
        return False
    try:
        p10 = float(rows[i - 1].get('ma10', 0))
        p20 = float(rows[i - 1].get('ma20', 0))
        c10 = float(rows[i].get('ma10', 0))
        c20 = float(rows[i].get('ma20', 0))
        if p10 <= 0 or p20 <= 0 or c10 <= 0 or c20 <= 0:
            return False
        return p10 <= p20 and c10 > c20
    except (ValueError, TypeError):
        return False


def _star_in_recent(rows, i, n=20):
    """前N根bar内出现过★买（含当前）"""
    for j in range(max(0, i - n), i + 1):
        if _has_star_buy(rows[j]):
            return True
    return False


# ── 入场模式定义 ──
#   lambda(r): 状态模式（只看当前bar自身）
#   字符串key: 事件模式（需要看前后bar上下文）
ENTRY_MODES = {
    'any':                  lambda r: _has_star_buy(r) or _has_golden(r),
    'star':                 lambda r: _has_star_buy(r),
    'golden':               lambda r: _has_golden(r),
    'star+golden':          lambda r: _has_star_buy(r) and _has_golden(r),
    'star+ma5':             lambda r: _has_star_buy(r) and _ma_above(r, 'ma5', 'ma10'),
    'star+ma5+ma10':        lambda r: _has_star_buy(r) and _ma_above(r, 'ma5', 'ma10') and _ma_above(r, 'ma10', 'ma20'),
    'star+ma5+ma10+safe':   None,   # handled by _check_entry_ctx (无死叉事件 + 60分黄线上方)
    'golden+ma5':           lambda r: _has_golden(r) and _ma_above(r, 'ma5', 'ma10'),
    'golden+ma5+ma10':      lambda r: _has_golden(r) and _ma_above(r, 'ma5', 'ma10') and _ma_above(r, 'ma10', 'ma20'),
    'star+golden+ma5':      lambda r: _has_star_buy(r) and _has_golden(r) and _ma_above(r, 'ma5', 'ma10'),
    # ── 事件模式：MA10金叉MA20那一刻 + ★买在附近 ──
    'ma10x20+star':         None,   # handled by _check_entry_ctx
    'ma10x20+star+ma5':     None,
}

# 所有可用模式（含事件模式）
ALL_ENTRY_MODES = list(ENTRY_MODES.keys())


def _is_t_point(row):
    """做T点：★卖"""
    sell = (row.get('sell_signal', '') or '').strip()
    return bool(sell)


MIN60_CACHE = {}


def _no_recent_death(rows, i, n=20):
    """最近n根内，最后一个expma_cross事件不是死叉"""
    for j in range(i - 1, max(i - n - 1, 0), -1):
        cross = (rows[j].get('expma_cross', '') or '').strip()
        if cross == '死叉':
            return False
        if cross == '金叉':
            return True
    return True


def _get_min60_above(code, bar_date):
    """60分钟 close > expma50（黄线上方），带缓存"""
    if code not in MIN60_CACHE:
        rows = _load_csv(code, 'min60')
        if not rows:
            MIN60_CACHE[code] = {}
            return False
        MIN60_CACHE[code] = {}
        for r in rows:
            d = r.get('date', '').strip()
            try:
                c = float(r.get('close', 0))
                e50 = float(r.get('expma50', 0) or 0)
                MIN60_CACHE[code][d] = c > e50
            except (ValueError, TypeError):
                MIN60_CACHE[code][d] = False
    return MIN60_CACHE[code].get(bar_date, False)


def _check_entry_ctx(entry_mode, rows, i, row, code=None):
    """上下文相关的入场检测（事件模式）"""
    if entry_mode == 'ma10x20+star':
        return _check_ma10x20_cross(rows, i) and _star_in_recent(rows, i, 20)
    if entry_mode == 'ma10x20+star+ma5':
        return _check_ma10x20_cross(rows, i) and _star_in_recent(rows, i, 20) and _ma_above(row, 'ma5', 'ma10')
    if entry_mode == 'star+ma5+ma10+safe':
        ok = _has_star_buy(row) and _ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')
        if not ok:
            return False
        bar_date = row.get('date', '').strip()
        return _no_recent_death(rows, i, 20) and (_get_min60_above(code, bar_date) if code else True)
    return False


def _is_exit(row):
    """清仓条件：死叉"""
    cross = (row.get('expma_cross', '') or '').strip()
    return cross == '死叉'


def _entry_price(row):
    """入场价：折中点 (low + close) / 2"""
    return (float(row['low']) + float(row['close'])) / 2


def _exit_price(row):
    """出场价：(high + close) / 2"""
    return (float(row['high']) + float(row['close'])) / 2


def _calc_band_low(rows, entry_idx):
    """计算开仓bar的波段最低点：前一个死叉到开仓bar之间的最低low"""
    best = float(rows[entry_idx]['low'])
    for i in range(entry_idx - 1, max(entry_idx - BAND_LOOKBACK, 0), -1):
        lo = float(rows[i]['low'])
        if lo < best:
            best = lo
        if _is_exit(rows[i]):
            break
    return best


# ══════════════════════════════════════════════════════════════════
# 单标的回测
# ══════════════════════════════════════════════════════════════════

def backtest_stock(code, name, period, months=None, entry_mode='any'):
    """对单只标的单周期跑完整回测，返回 trade list"""
    rows = _load_csv(code, period)
    if not rows or len(rows) < SKIP_BARS:
        return []

    daily_zones = _load_daily_zones(code)
    entry_fn = ENTRY_MODES.get(entry_mode)
    is_ctx_mode = entry_fn is None  # 事件模式需要上下文

    trades = []
    in_trade = False
    entry_idx = None
    entry_price_val = None
    band_low = None
    t_point = None          # 做T点 (price, idx)
    entry_date = None

    for i in range(SKIP_BARS, len(rows)):
        r = rows[i]
        bar_date = r.get('date', '').strip()
        bar_low = float(r['low'])
        bar_close = float(r['close'])
        zone = daily_zones.get(bar_date, 'weak')

        if months:
            try:
                d = datetime.strptime(bar_date, '%Y%m%d')
                cutoff = datetime.now() - __import__('datetime').timedelta(days=months * 30)
                if d < cutoff:
                    continue
            except Exception:
                pass

        if not in_trade:
            # ── 寻找开仓 ──
            if is_ctx_mode:
                ok = _check_entry_ctx(entry_mode, rows, i, r, code)
            else:
                ok = entry_fn(r) if entry_fn else (_has_star_buy(r) or _has_golden(r))
            if ok and zone in ('strong', 'secondary'):
                in_trade = True
                entry_idx = i
                entry_ts = r.get('timestamp', '').strip()
                entry_price_val = _entry_price(r)
                band_low = _calc_band_low(rows, i)
                t_point = None
                entry_date = bar_date
        else:
            # ── 持仓中，检查平仓 ──
            # 优先级: 止损 > 死叉 > ★卖
            exit_reason = None
            exit_price_val = None

            # 1. 止损：盘中跌破波段最低点
            if bar_low < band_low:
                exit_reason = '止损'
                exit_price_val = band_low

            # 2. 死叉清仓
            elif _is_exit(r):
                exit_reason = '死叉'
                exit_price_val = _exit_price(r)

            # 3. ★卖做T（不结束交易）
            elif _is_t_point(r) and t_point is None:
                t_point = (time_to_datetime(r.get('timestamp', '')), _exit_price(r), i)
                # 继续持仓，不结束交易

            if exit_reason:
                # 计算持仓期间统计
                exit_actual = exit_price_val or bar_close
                ret_pct = (exit_actual - entry_price_val) / entry_price_val * 100

                # 最大有利/不利偏移
                mfe = 0.0
                mae = 0.0
                for j in range(entry_idx + 1, i + 1):
                    h = float(rows[j]['high'])
                    l = float(rows[j]['low'])
                    mfe = max(mfe, (h - entry_price_val) / entry_price_val * 100)
                    mae = min(mae, (l - entry_price_val) / entry_price_val * 100)

                hold_bars = i - entry_idx

                trade = {
                    'code': code,
                    'name': name,
                    'period': period,
                    'entry_ts': entry_ts,
                    'entry_date': entry_date,
                    'exit_date': bar_date,
                    'entry_price': round(entry_price_val / PRICE_F, 4),
                    'exit_price': round(exit_actual / PRICE_F, 4),
                    'ret_pct': round(ret_pct, 2),
                    'mfe_pct': round(mfe, 2),
                    'mae_pct': round(mae, 2),
                    'hold_bars': hold_bars,
                    'exit_reason': exit_reason,
                    'zone': zone if exit_reason == '死叉' else daily_zones.get(entry_date, 'weak'),
                    't_price': round(t_point[1] / PRICE_F, 4) if t_point else None,
                    't_date': t_point[0] if t_point else None,
                }
                trades.append(trade)

                in_trade = False
                entry_idx = None
                entry_price_val = None
                band_low = None
                t_point = None

    return trades


# ══════════════════════════════════════════════════════════════════
# 跨周期共振标签
# ══════════════════════════════════════════════════════════════════

RESONANCE_CACHE = {}  # {(code, period): rows}


def _tag_resonance(trades):
    """为每笔 min5 交易打上 min15/min30 金叉共振标签"""
    for t in trades:
        t['resonance'] = 'n/a'
        if t['period'] != 'min5':
            continue

        code = t['code']
        entry_date = t['entry_date']

        has_m15 = False
        has_m30 = False

        # min15
        cache_key = (code, 'min15')
        if cache_key not in RESONANCE_CACHE:
            RESONANCE_CACHE[cache_key] = _load_csv(code, 'min15')
        m15_rows = RESONANCE_CACHE[cache_key]
        has_m15 = _expma_bullish_on_date(m15_rows, entry_date) if m15_rows else False

        # min30
        cache_key = (code, 'min30')
        if cache_key not in RESONANCE_CACHE:
            RESONANCE_CACHE[cache_key] = _load_csv(code, 'min30')
        m30_rows = RESONANCE_CACHE[cache_key]
        has_m30 = _expma_bullish_on_date(m30_rows, entry_date) if m30_rows else False

        if has_m15 and has_m30:
            t['resonance'] = 'm15+m30'
        elif has_m15:
            t['resonance'] = 'm15'
        else:
            t['resonance'] = 'none'


def _expma_bullish_on_date(rows, date_str):
    """检查指定日期是否有 expma12 > expma50（金叉状态）"""
    for r in rows:
        if r.get('date', '').strip() != date_str:
            continue
        try:
            e12 = float(r.get('expma12', 0))
            e50 = float(r.get('expma50', 0))
            if e12 > 0 and e50 > 0 and e12 > e50:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _print_resonance(trades):
    """打印共振分组统计"""
    min5_trades = [t for t in trades if t['period'] == 'min5']
    if not min5_trades:
        return

    groups = defaultdict(list)
    for t in min5_trades:
        groups[t.get('resonance', 'none')].append(t)

    print(f'\n  ★ 跨周期共振分析 (min5入场 × 上级金叉状态)')
    print(f'  {"共振级别":<16} {"笔数":>6} {"胜率":>7} {"均收益":>8} {"死叉胜率":>7} {"死叉均收":>8} {"止损笔数":>7}')
    print(f'  {"-"*78}')
    for level in ['m15+m30', 'm15', 'none']:
        items = groups.get(level, [])
        if not items:
            continue
        n = len(items)
        wr = len([t for t in items if t['ret_pct'] > 0]) / n * 100
        avg = sum(t['ret_pct'] for t in items) / n
        dead = [t for t in items if t['exit_reason'] == '死叉']
        dead_wr = len([t for t in dead if t['ret_pct'] > 0]) / len(dead) * 100 if dead else 0
        dead_avg = sum(t['ret_pct'] for t in dead) / len(dead) if dead else 0
        stop_n = len([t for t in items if t['exit_reason'] == '止损'])
        label = {'m15+m30': '15分+30分双共振', 'm15': '仅15分金叉', 'none': '无上级共振'}.get(level, level)
        print(f'  {label:<16} {n:>6} {wr:>6.1f}% {avg:>+7.2f}% {dead_wr:>6.1f}% {dead_avg:>+7.2f}% {stop_n:>7}')


def time_to_datetime(ts_str):
    """将 YYYYMMDDHHMM 转成可读字符串"""
    try:
        s = ts_str.strip()
        if len(s) >= 12:
            return f'{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}'
        elif len(s) >= 8:
            return f'{s[:4]}-{s[4:6]}-{s[6:8]}'
    except Exception:
        pass
    return ts_str


# ══════════════════════════════════════════════════════════════════
# 汇总统计
# ══════════════════════════════════════════════════════════════════

def summarize(trades, label=''):
    """按出场方式分组统计"""
    groups = defaultdict(list)
    for t in trades:
        groups[t['exit_reason']].append(t)
    groups['全部'] = trades

    report = {}
    for reason, items in groups.items():
        if not items:
            continue
        n = len(items)
        wins = [t for t in items if t['ret_pct'] > 0]
        wr = len(wins) / n * 100
        rets = [t['ret_pct'] for t in items]
        avg_ret = sum(rets) / n
        max_ret = max(rets)
        min_ret = min(rets)
        avg_hold = sum(t['hold_bars'] for t in items) / n
        avg_mfe = sum(t['mfe_pct'] for t in items) / n
        avg_mae = sum(t['mae_pct'] for t in items) / n

        # 有做T点 vs 无做T点
        has_t = [t for t in items if t.get('t_price')]
        no_t = [t for t in items if not t.get('t_price')]

        report[reason] = {
            'n': n, 'wr': round(wr, 1), 'avg_ret': round(avg_ret, 2),
            'max_ret': round(max_ret, 2), 'min_ret': round(min_ret, 2),
            'avg_hold': round(avg_hold, 1), 'avg_mfe': round(avg_mfe, 2),
            'avg_mae': round(avg_mae, 2),
            'n_t': len(has_t), 'n_no_t': len(no_t),
            'avg_ret_t': round(sum(t['ret_pct'] for t in has_t) / len(has_t), 2) if has_t else None,
            'avg_ret_no_t': round(sum(t['ret_pct'] for t in no_t) / len(no_t), 2) if no_t else None,
        }
    return report


# ══════════════════════════════════════════════════════════════════
# 报告输出
# ══════════════════════════════════════════════════════════════════

def print_report(all_trades, by_stock, label=''):
    """终端表格输出"""
    header = f' 信号配对回测报告 {label} '
    print(f'\n{"="*90}')
    print(f'{header:=^90}')
    print(f'{"="*90}')

    # ── 总览 ──
    total = all_trades
    if not total:
        print('\n  (无符合条件的交易)\n')
        return

    print(f'\n  总交易: {len(total)} 笔  |  ', end='')
    for reason in ['死叉', '止损', '全部']:
        if reason in by_stock:
            r = by_stock.get(reason, {})
            if isinstance(r, dict) and r.get('n'):
                wins = [t for t in total if t['ret_pct'] > 0]
                overall_wr = len(wins) / len(total) * 100 if total else 0
    avg_all = sum(t['ret_pct'] for t in total) / len(total) if total else 0
    print(f'平均收益: {avg_all:+.2f}%  |  ', end='')
    wr_all = len([t for t in total if t['ret_pct'] > 0]) / len(total) * 100 if total else 0
    print(f'胜率: {wr_all:.1f}%')

    # ── 按出场方式 ──
    outcomes = defaultdict(list)
    for t in total:
        outcomes[t['exit_reason']].append(t)

    for reason in ['止损', '死叉']:
        items = outcomes.get(reason, [])
        if not items:
            continue
        n = len(items)
        avg = sum(t['ret_pct'] for t in items) / n
        wr = len([t for t in items if t['ret_pct'] > 0]) / n * 100
        label_map = {'死叉': '清仓(死叉)', '止损': '止损(破波段低点)'}
        print(f'  {label_map.get(reason, reason)}: {n}笔  胜率{wr:.0f}%  均收益{avg:+.2f}%')

    # ── 收益分布 ──
    print(f'\n  收益分布:')
    bins = [(-100, -5), (-5, -2), (-2, 0), (0, 2), (2, 5), (5, 10), (10, 1000)]
    for lo, hi in bins:
        count = sum(1 for t in total if lo <= t['ret_pct'] < hi)
        bar = '█' * max(1, count)
        label = f'{lo}~{hi}%' if hi < 1000 else f'>{lo}%'
        print(f'    {label:>10}: {count:>3} {bar}')

    # ── 按周期 ──
    print(f'\n  {"周期":<8} {"笔数":>5} {"胜率":>7} {"均收益":>8} {"均持(根)":>9} {"有T":>5} {"T收益":>8} {"无T收益":>8}')
    print(f'  {"-"*75}')
    for period in PERIODS:
        pt = [t for t in total if t['period'] == period]
        if not pt:
            continue
        n = len(pt)
        wr = len([t for t in pt if t['ret_pct'] > 0]) / n * 100
        avg = sum(t['ret_pct'] for t in pt) / n
        avg_h = sum(t['hold_bars'] for t in pt) / n
        t_trades = [t for t in pt if t.get('t_price')]
        nt_trades = [t for t in pt if not t.get('t_price')]
        t_avg = sum(t['ret_pct'] for t in t_trades) / len(t_trades) if t_trades else None
        nt_avg = sum(t['ret_pct'] for t in nt_trades) / len(nt_trades) if nt_trades else None
        t_str = f'{t_avg:>+7.2f}%' if t_avg is not None else '       -'
        nt_str = f'{nt_avg:>+7.2f}%' if nt_avg is not None else '       -'
        print(f'  {period:<8} {n:>5} {wr:>6.0f}% {avg:>+7.2f}% {avg_h:>8.1f} '
              f'{len(t_trades):>5} {t_str:>8} {nt_str:>8}')

    # ── 按标的 ──
    print(f'\n  {"标的":<12} {"笔数":>5} {"胜率":>7} {"均收益":>8} {"均MFE":>7} {"均MAE":>7}')
    print(f'  {"-"*55}')
    stocks = defaultdict(list)
    for t in total:
        stocks[t['code']].append(t)
    for code in sorted(stocks, key=lambda c: sum(t['ret_pct'] for t in stocks[c]) / len(stocks[c]), reverse=True):
        items = stocks[code]
        n = len(items)
        wr = len([t for t in items if t['ret_pct'] > 0]) / n * 100
        avg = sum(t['ret_pct'] for t in items) / n
        avg_mfe = sum(t['mfe_pct'] for t in items) / n
        avg_mae = sum(t['mae_pct'] for t in items) / n
        name = items[0]['name'][:8]
        print(f'  {code:<12} {n:>5} {wr:>6.0f}% {avg:>+7.2f}% {avg_mfe:>+6.2f}% {avg_mae:>+6.2f}%')

    # ── 最近交易明细 ──
    print(f'\n  最近10笔交易:')
    print(f'  {"日期":<12} {"标的":<12} {"周期":<6} {"入场":>8} {"出场":>8} {"收益":>8} {"方式":<6} {"持根":>5} {"T价":>8}')
    print(f'  {"-"*90}')
    for t in sorted(total, key=lambda x: x['entry_date'], reverse=True)[:10]:
        t_str = f'{t.get("t_price","")}' if t.get('t_price') else '-'
        print(f'  {t["entry_date"]:<12} {t["code"]:<12} {t["period"]:<6} '
              f'{t["entry_price"]:>8.2f} {t["exit_price"]:>8.2f} {t["ret_pct"]:>+7.2f}% '
              f'{t["exit_reason"]:<6} {t["hold_bars"]:>5} {t_str:>8}')


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='信号配对回测')
    parser.add_argument('--code', type=str, help='单标的回测 (如 sh600176)')
    parser.add_argument('--period', type=str, choices=PERIODS, default='min5', help='单周期 (默认min5)')
    parser.add_argument('--months', type=int, default=6, help='回测时间范围(月, 默认6)')
    parser.add_argument('--entry', type=str, choices=ALL_ENTRY_MODES, default='any',
                        help=f'入场模式: {", ".join(ALL_ENTRY_MODES)}')
    parser.add_argument('--compare', action='store_true', help='对比所有入场模式')
    parser.add_argument('--save', action='store_true', help='保存结果到JSON')
    parser.add_argument('--detail', action='store_true', help='输出全部交易明细')
    args = parser.parse_args()

    universe = load_universe()
    if not universe:
        print('[backtest] universe 为空，请先运行 volume_leader_screener.py --sync-universe')
        return

    if args.code:
        stock = next((s for s in universe if s['code'] == args.code), None)
        if not stock:
            print(f'[backtest] {args.code} 不在 universe 中')
            return
        universe = [stock]

    periods_to_run = [args.period]

    # ── compare 模式：跑全部入场模式，输出对比表 ──
    if args.compare:
        mode_names = [m for m in ALL_ENTRY_MODES if m != 'any']
        all_mode_results = {}

        for mode in mode_names:
            all_trades = []
            for stock in universe:
                code, name = stock['code'], stock['name']
                for period in periods_to_run:
                    trades = backtest_stock(code, name, period, months=args.months, entry_mode=mode)
                    all_trades.extend(trades)

            if all_trades:
                n = len(all_trades)
                wr = len([t for t in all_trades if t['ret_pct'] > 0]) / n * 100
                avg = sum(t['ret_pct'] for t in all_trades) / n
                dead_trades = [t for t in all_trades if t['exit_reason'] == '死叉']
                dead_n = len(dead_trades)
                dead_wr = len([t for t in dead_trades if t['ret_pct'] > 0]) / dead_n * 100 if dead_n else 0
                dead_avg = sum(t['ret_pct'] for t in dead_trades) / dead_n if dead_n else 0
                stop_trades = [t for t in all_trades if t['exit_reason'] == '止损']
                stop_n = len(stop_trades)
                stop_avg = sum(t['ret_pct'] for t in stop_trades) / stop_n if stop_n else 0
                all_mode_results[mode] = {
                    'n': n, 'wr': wr, 'avg': avg,
                    'dead_n': dead_n, 'dead_wr': dead_wr, 'dead_avg': dead_avg,
                    'stop_n': stop_n, 'stop_avg': stop_avg,
                }

        # 打印对比表
        print(f'\n{"="*100}')
        print(f'  入场模式对比 — {args.months}个月 — {args.period}')
        print(f'{"="*100}')
        print(f'  {"模式":<16} {"总笔数":>6} {"总胜率":>7} {"总均收":>8} {"死叉笔数":>7} {"死叉胜率":>7} {"死叉均收":>8} {"止损笔数":>7} {"止损均收":>8}')
        print(f'  {"-"*98}')
        for mode in mode_names:
            r = all_mode_results.get(mode)
            if not r:
                continue
            print(f'  {mode:<16} {r["n"]:>6} {r["wr"]:>6.1f}% {r["avg"]:>+7.2f}% '
                  f'{r["dead_n"]:>7} {r["dead_wr"]:>6.1f}% {r["dead_avg"]:>+7.2f}% '
                  f'{r["stop_n"]:>7} {r["stop_avg"]:>+7.2f}%')
        print()
        return

    # ── 普通模式 ──
    all_trades = []
    by_period = defaultdict(list)
    by_stock = {}

    for stock in universe:
        code, name = stock['code'], stock['name']
        for period in periods_to_run:
            trades = backtest_stock(code, name, period, months=args.months, entry_mode=args.entry)
            all_trades.extend(trades)
            by_period[period].extend(trades)

    if not all_trades:
        print(f'\n[backtest] 未找到符合条件的交易（{args.months}个月内）')
        return

    # 跨周期共振标签（仅 min5）
    _tag_resonance(all_trades)

    # 分组统计
    by_stock['全部'] = summarize(all_trades).get('全部', {})
    for reason in ['死叉', '止损']:
        subset = [t for t in all_trades if t['exit_reason'] == reason]
        by_stock[reason] = summarize(subset).get(reason, {})

    # 输出报告
    label = f'{args.months}个月 [{args.entry}]'
    if args.code:
        label += f' [{args.code}]'
    print_report(all_trades, by_stock, label)
    _print_resonance(all_trades)

    # 详细交易列表
    if args.detail and all_trades:
        print(f'\n{"="*90}')
        print(f'  全部交易明细 ({len(all_trades)}笔)')
        print(f'{"="*90}')
        for t in sorted(all_trades, key=lambda x: x['entry_date'], reverse=True):
            t_info = f' T@{t["t_price"]}' if t.get('t_price') else ''
            print(f'  {t["entry_date"]} {t["code"]:<12} {t["period"]} '
                  f'入场{t["entry_price"]:.2f} → {t["exit_reason"]}@{t["exit_price"]:.2f} '
                  f'{t["ret_pct"]:+.2f}% (MFE{t["mfe_pct"]:+.1f}% MAE{t["mae_pct"]:+.1f}%) '
                  f'持{t["hold_bars"]}根{t_info}')

    # 保存
    if args.save:
        out_path = TRACKING_DIR / 'backtest_report.json'
        report = {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'params': {'months': args.months, 'period': args.period if args.code else 'all'},
            'summary': {k: v for k, v in by_stock.items() if v},
            'trades': all_trades,
        }
        json.dump(report, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'\n[backtest] 已保存: {out_path}')


if __name__ == '__main__':
    main()

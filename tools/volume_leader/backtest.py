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


# ── 内存信号计算（用于没有预计算信号列的周期，如min1） ──

def _enrich_signals(rows):
    """在内存中为rows补充 buy_signal/sell_signal/expma_cross/cci_divergence/cci_extreme

    基于已有的基础指标列(trend_line/expma12/expma50/cci/close)实时计算，
    不写CSV。只处理信号列为空的行。
    """
    if not rows:
        return rows

    # 检查是否已经有信号数据
    sample = rows[min(SKIP_BARS, len(rows) - 1)]
    has_buy = any((r.get('buy_signal', '') or '').strip() for r in rows[SKIP_BARS:SKIP_BARS + 100])
    has_sell = any((r.get('sell_signal', '') or '').strip() for r in rows[SKIP_BARS:SKIP_BARS + 100])
    has_cross = any((r.get('expma_cross', '') or '').strip() for r in rows[SKIP_BARS:SKIP_BARS + 100])
    if has_buy and has_sell and has_cross:
        return rows  # 已有信号，跳过

    n = len(rows)

    # 提取数组
    try:
        trend = [float(r.get('trend_line', 0) or 0) for r in rows]
        e12 = [float(r.get('expma12', 0) or 0) for r in rows]
        e50 = [float(r.get('expma50', 0) or 0) for r in rows]
        cci_vals = [float(r.get('cci', 0) or 0) for r in rows]
        closes = [float(r.get('close', 0) or 0) for r in rows]
    except (ValueError, TypeError):
        return rows

    # ── ★买/★卖 ──
    # 优先用trend_line；如果trend_line为常量（如min1全是50），退化为CCI极值+背驰
    trend_vals = [v for v in trend if v != 0]
    trend_range = max(trend_vals) - min(trend_vals) if trend_vals else 0

    buy_set = set()
    sell_set = set()

    if trend_range > 10:
        # 正常trend_line模式
        in_high = False
        for i in range(1, n):
            if trend[i - 1] <= 11 < trend[i]:
                buy_set.add(i)
            if trend[i] >= 90:
                in_high = True
            if in_high and trend[i - 1] >= 90 > trend[i]:
                sell_set.add(i)
                in_high = False
    else:
        # 退化模式：CCI极值+回撤 → 信号
        for i in range(2, n):
            if cci_vals[i] <= -200 and cci_vals[i] > cci_vals[i-1] and cci_vals[i-1] <= -200:
                buy_set.add(i)
            if cci_vals[i] >= 200 and cci_vals[i] < cci_vals[i-1] and cci_vals[i-1] >= 200:
                sell_set.add(i)

    # ── EXPMA金叉/死叉 ──
    golden_set = set()
    death_set = set()
    for i in range(1, n):
        if e12[i] > 0 and e50[i] > 0:
            if e12[i - 1] <= e50[i - 1] and e12[i] > e50[i]:
                golden_set.add(i)
            elif e12[i - 1] >= e50[i - 1] and e12[i] < e50[i]:
                death_set.add(i)

    # ── CCI极值 ──
    CCI_LEVELS = [200, 250, 300]
    for i in range(n):
        val = cci_vals[i]
        eh = 0
        el = 0
        for lvl in CCI_LEVELS:
            if val >= lvl:
                eh = lvl
            if val <= -lvl:
                el = -lvl
        if eh > 0:
            rows[i]['cci_extreme'] = f'CCI+{eh}'
        elif el < 0:
            rows[i]['cci_extreme'] = f'CCI{el}'
        else:
            rows[i]['cci_extreme'] = ''

    # ── CCI背驰（简化版：5根窗口内检测价格-CCI背离） ──
    LOOKBACK = 5
    for i in range(LOOKBACK, n):
        # 顶背驰: 价格新高但CCI高点降低
        peak_cci_idx = None
        for j in range(i - LOOKBACK, i + 1):
            if abs(cci_vals[j]) >= 200:
                if cci_vals[j] > 0 and (peak_cci_idx is None or cci_vals[j] > cci_vals[peak_cci_idx]):
                    peak_cci_idx = j
        pos_div = False
        if peak_cci_idx is not None and peak_cci_idx < i:
            if closes[i] > closes[peak_cci_idx] and cci_vals[i] < cci_vals[peak_cci_idx] * 0.7:
                pos_div = True

        # 底背驰: 价格新低但CCI低点抬高
        trough_cci_idx = None
        for j in range(i - LOOKBACK, i + 1):
            if abs(cci_vals[j]) >= 200:
                if cci_vals[j] < 0 and (trough_cci_idx is None or cci_vals[j] < cci_vals[trough_cci_idx]):
                    trough_cci_idx = j
        neg_div = False
        if trough_cci_idx is not None and trough_cci_idx < i:
            if closes[i] < closes[trough_cci_idx] and cci_vals[i] > cci_vals[trough_cci_idx] * 0.7:
                neg_div = True

        if pos_div:
            rows[i]['cci_divergence'] = '顶背驰'
        elif neg_div:
            rows[i]['cci_divergence'] = '底背驰'
        else:
            rows[i]['cci_divergence'] = ''

    # ── 写入信号列 ──
    for i in range(n):
        rows[i]['buy_signal'] = '★买' if i in buy_set else ''
        rows[i]['sell_signal'] = '★卖' if i in sell_set else ''
        if i in golden_set:
            rows[i]['expma_cross'] = '金叉'
        elif i in death_set:
            rows[i]['expma_cross'] = '死叉'
        else:
            rows[i]['expma_cross'] = ''

    return rows


def load_tracking_universe():
    """加载14只跟踪标的"""
    from config import NAME_MAP
    return [{'code': k, 'name': v} for k, v in NAME_MAP.items()]


# ══════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════

PRICE_F = MIN_PRICE_FACTOR  # 分钟线价格因子
PERIODS = ['min1', 'min5', 'min15', 'min30']
SKIP_BARS = 200             # 跳过前N根bar（指标未收敛）
BAND_LOOKBACK = 40          # 波段最低点最远回看根数 (min5:40=200分钟=半天)


# ══════════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════════

def _load_csv(code, period, enrich=True):
    path = TRACKING_DIR / code / f'{period}_signals.csv'
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    if enrich and rows:
        rows = _enrich_signals(rows)
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


def _close_below(row, ma_col):
    """收盘价 < 均线"""
    try:
        return float(row['close']) < float(row.get(ma_col, 0))
    except (ValueError, TypeError):
        return False


def _has_cci_top_divergence(row):
    """CCI顶背驰"""
    return (row.get('cci_divergence', '') or '').strip() == '顶背驰'


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
    'star+ma5+ma10+safe+jincha': None,
}

# 所有可用模式（含事件模式）
ALL_ENTRY_MODES = list(ENTRY_MODES.keys())

# ── 卖信号模式定义 ──
SELL_MODES = {
    'sell_any':                  lambda r: _has_star_sell(r),
    'sell_death':                lambda r: (r.get('expma_cross', '') or '').strip() == '死叉',
    'sell_ma_death':             lambda r: _has_star_sell(r) and not _ma_above(r, 'ma5', 'ma10'),
    'sell_ma_death_safe':        None,
    'sell_ma_death_safe_min5':   None,
    'sell_ma_death_safe_min30':  None,
    'sell_break_ma5':            lambda r: _has_star_sell(r) and _close_below(r, 'ma5'),
    'sell_break_ma5_safe':       None,
    'sell_break_ma5_safe_min5':  None,
    'sell_break_ma5_safe_min30': None,
    'sell_break_ma10':           lambda r: _has_star_sell(r) and _close_below(r, 'ma10'),
    'sell_break_ma10_safe':      None,
    'sell_break_ma10_safe_min5': None,
    'sell_break_ma10_safe_min30': None,
    # ── CCI背驰系列（第一层做T基准） ──
    'sell_cci_div':                  lambda r: _has_star_sell(r) and _has_cci_top_divergence(r),
    'sell_cci_div_safe':             None,
    'sell_cci_div_break_ma5_safe':   None,
    # ── 清仓级（第三层：最优组合+EXPMA死叉） ──
    'sell_break_ma5_safe_death':     None,
}
ALL_SELL_MODES = list(SELL_MODES.keys())


def _is_t_point(row):
    """做T点：★卖"""
    sell = (row.get('sell_signal', '') or '').strip()
    return bool(sell)


MIN60_CACHE = {}
MIN15_CACHE = {}


def _ma5_cross_above_ma10(rows, i):
    """当前bar MA5 刚刚上穿 MA10"""
    if i < 1:
        return False
    try:
        p5, p10 = float(rows[i-1].get('ma5',0)), float(rows[i-1].get('ma10',0))
        c5, c10 = float(rows[i].get('ma5',0)), float(rows[i].get('ma10',0))
        if any(v <= 0 for v in (p5, p10, c5, c10)):
            return False
        return p5 <= p10 and c5 > c10
    except (ValueError, TypeError):
        return False


def _ma5_cross_below_ma10(rows, i):
    """当前bar MA5 刚刚下穿 MA10"""
    if i < 1:
        return False
    try:
        p5, p10 = float(rows[i-1].get('ma5',0)), float(rows[i-1].get('ma10',0))
        c5, c10 = float(rows[i].get('ma5',0)), float(rows[i].get('ma10',0))
        if any(v <= 0 for v in (p5, p10, c5, c10)):
            return False
        return p5 >= p10 and c5 < c10
    except (ValueError, TypeError):
        return False


def _close_cross_above_ema50(rows, i):
    """当前bar close 刚刚上穿 expma50"""
    if i < 1:
        return False
    try:
        pc = float(rows[i-1].get('close',0))
        pe50 = float(rows[i-1].get('expma50',0))
        cc = float(rows[i].get('close',0))
        ce50 = float(rows[i].get('expma50',0))
        if any(v <= 0 for v in (pc, pe50, cc, ce50)):
            return False
        return pc <= pe50 and cc > ce50
    except (ValueError, TypeError):
        return False


def _close_cross_below_ema50(rows, i):
    """当前bar close 刚刚下穿 expma50"""
    if i < 1:
        return False
    try:
        pc = float(rows[i-1].get('close',0))
        pe50 = float(rows[i-1].get('expma50',0))
        cc = float(rows[i].get('close',0))
        ce50 = float(rows[i].get('expma50',0))
        if any(v <= 0 for v in (pc, pe50, cc, ce50)):
            return False
        return pc >= pe50 and cc < ce50
    except (ValueError, TypeError):
        return False


def _no_recent_death(rows, i, n=20):
    """最近n根内，最后一个expma_cross事件不是死叉"""
    for j in range(i - 1, max(i - n - 1, 0), -1):
        cross = (rows[j].get('expma_cross', '') or '').strip()
        if cross == '死叉':
            return False
        if cross == '金叉':
            return True
    return True


def _no_recent_golden(rows, i, n=20):
    """最近n根内，最后一个expma_cross事件不是金叉"""
    for j in range(i - 1, max(i - n - 1, 0), -1):
        cross = (rows[j].get('expma_cross', '') or '').strip()
        if cross == '金叉':
            return False
        if cross == '死叉':
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


def _get_min15_below_ema50(code, bar_date):
    """15分钟 close < expma50（黄线下方），带缓存"""
    if code not in MIN15_CACHE:
        rows = _load_csv(code, 'min15')
        if not rows:
            MIN15_CACHE[code] = {}
            return False
        MIN15_CACHE[code] = {}
        for r in rows:
            d = r.get('date', '').strip()
            try:
                c = float(r.get('close', 0))
                e50 = float(r.get('expma50', 0) or 0)
                MIN15_CACHE[code][d] = c < e50
            except (ValueError, TypeError):
                MIN15_CACHE[code][d] = False
    return MIN15_CACHE[code].get(bar_date, False)


PERIOD_CACHE = {}


def _get_period_below_ema50(period, code, bar_date):
    """通用：X分钟 close < expma50（黄线下方），带缓存"""
    key = f'{code}_{period}'
    if key not in PERIOD_CACHE:
        rows = _load_csv(code, period)
        if not rows:
            PERIOD_CACHE[key] = {}
            return False
        PERIOD_CACHE[key] = {}
        for r in rows:
            d = r.get('date', '').strip()
            try:
                c = float(r.get('close', 0))
                e50 = float(r.get('expma50', 0) or 0)
                PERIOD_CACHE[key][d] = c < e50
            except (ValueError, TypeError):
                PERIOD_CACHE[key][d] = False
    return PERIOD_CACHE[key].get(bar_date, False)


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
    if entry_mode == 'star+ma5+ma10+safe+jincha':
        ok = _has_star_buy(row) and _ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')
        if not ok:
            return False
        bar_date = row.get('date', '').strip()
        env_ok = _no_recent_death(rows, i, 20) and (_get_min60_above(code, bar_date) if code else True)
        if not env_ok:
            return False
        expma12 = float(row.get('expma12', 0) or 0)
        expma50 = float(row.get('expma50', 0) or 0)
        return expma12 > expma50
    return False


def _check_sell_ctx(sell_mode, rows, i, row, code=None):
    """卖信号上下文检测（环境过滤：无金叉事件 + X分黄线下方）

    模式命名约定: sell_{condition}_safe[_min5|_min30]
    - 无周期后缀 = min15 黄线
    - _min5 = 5分钟黄线
    - _min30 = 30分钟黄线
    """
    # 解析周期后缀
    period = 'min15'
    base = sell_mode
    if sell_mode.endswith('_min5'):
        period = 'min5'
        base = sell_mode[:-5]
    elif sell_mode.endswith('_min30'):
        period = 'min30'
        base = sell_mode[:-6]

    # 提取基础条件
    if base == 'sell_ma_death_safe':
        ok = _has_star_sell(row) and not _ma_above(row, 'ma5', 'ma10')
    elif base == 'sell_break_ma5_safe':
        ok = _has_star_sell(row) and _close_below(row, 'ma5')
    elif base == 'sell_break_ma10_safe':
        ok = _has_star_sell(row) and _close_below(row, 'ma10')
    elif base == 'sell_cci_div_safe':
        ok = _has_star_sell(row) and _has_cci_top_divergence(row)
    elif base == 'sell_cci_div_break_ma5_safe':
        ok = _has_star_sell(row) and _has_cci_top_divergence(row) and _close_below(row, 'ma5')
    elif base == 'sell_break_ma5_safe_death':
        ok = _has_star_sell(row) and _close_below(row, 'ma5')
    else:
        return False

    if not ok:
        return False
    bar_date = row.get('date', '').strip()
    env_ok = _no_recent_golden(rows, i, 20) and (_get_period_below_ema50(period, code, bar_date) if code else True)
    if not env_ok:
        return False

    # EXPMA死叉额外检查（清仓级）
    if base == 'sell_break_ma5_safe_death':
        expma12 = float(row.get('expma12', 0) or 0)
        expma50 = float(row.get('expma50', 0) or 0)
        if not (expma50 > 0 and expma12 < expma50):
            return False

    return True


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
# 卖信号回测
# ══════════════════════════════════════════════════════════════════

def backtest_sell_stock(code, name, period, months=None, sell_mode='sell_any', lookahead=40):
    """卖信号过滤效果回测：★卖后N根bar内的价格行为"""
    rows = _load_csv(code, period)
    if not rows or len(rows) < SKIP_BARS + lookahead:
        return []

    entry_fn = SELL_MODES.get(sell_mode)
    is_ctx_mode = entry_fn is None

    signals = []
    for i in range(SKIP_BARS, len(rows) - lookahead):
        r = rows[i]
        bar_date = r.get('date', '').strip()

        if months:
            try:
                d = datetime.strptime(bar_date, '%Y%m%d')
                cutoff = datetime.now() - __import__('datetime').timedelta(days=months * 30)
                if d < cutoff:
                    continue
            except Exception:
                pass

        # 检测卖信号
        if is_ctx_mode:
            ok = _check_sell_ctx(sell_mode, rows, i, r, code)
        else:
            ok = entry_fn(r)

        if not ok:
            continue

        # 记录卖信号
        entry_price = float(r['close'])

        # 看未来N根bar：最大跌幅 / 最大反弹 / 是否出现死叉
        max_drop = 0.0
        max_rise = 0.0
        has_death = False
        death_bar_idx = None

        for j in range(i + 1, i + lookahead + 1):
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
                    death_bar_idx = j - i

        signal = {
            'code': code,
            'name': name,
            'period': period,
            'date': bar_date,
            'entry_price': round(entry_price / PRICE_F, 4),
            'max_drop_pct': round(max_drop, 2),
            'max_rise_pct': round(max_rise, 2),
            'has_death': has_death,
            'death_bar': death_bar_idx,
            'success': max_drop <= -2.0 or has_death,
        }
        signals.append(signal)

    return signals


# ══════════════════════════════════════════════════════════════════
# 买→卖配对回测
# ══════════════════════════════════════════════════════════════════

def backtest_pair(code, name, period, months=None, buy_mode='star+ma5+ma10+safe', sell_mode='sell_any'):
    """买信号入场 → 卖信号出场 配对回测

    buy_mode: ENTRY_MODES 中的入场模式
    sell_mode: SELL_MODES 中的出场模式
    Returns: trades list
    """
    rows = _load_csv(code, period)
    if not rows or len(rows) < SKIP_BARS:
        return []

    daily_zones = _load_daily_zones(code)
    buy_entry_fn = ENTRY_MODES.get(buy_mode)
    buy_is_ctx = buy_entry_fn is None
    sell_fn = SELL_MODES.get(sell_mode)
    sell_is_ctx = sell_fn is None

    trades = []
    in_trade = False
    entry_idx = None
    entry_price_val = None
    entry_date = None

    for i in range(SKIP_BARS, len(rows)):
        r = rows[i]
        bar_date = r.get('date', '').strip()
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
            # 买信号入场
            if buy_is_ctx:
                ok = _check_entry_ctx(buy_mode, rows, i, r, code)
            else:
                ok = buy_entry_fn(r)
            if ok and zone in ('strong', 'secondary'):
                in_trade = True
                entry_idx = i
                entry_price_val = float(r['close'])
                entry_date = bar_date
        else:
            # 卖信号出场
            if sell_is_ctx:
                ok = _check_sell_ctx(sell_mode, rows, i, r, code)
            else:
                ok = sell_fn(r)

            if ok:
                exit_price_val = float(r['close'])
                ret_pct = (exit_price_val - entry_price_val) / entry_price_val * 100

                # 持仓期间最大偏移
                mfe = 0.0
                mae = 0.0
                for j in range(entry_idx + 1, i + 1):
                    h = float(rows[j]['high'])
                    l = float(rows[j]['low'])
                    mfe = max(mfe, (h - entry_price_val) / entry_price_val * 100)
                    mae = min(mae, (l - entry_price_val) / entry_price_val * 100)

                trade = {
                    'code': code,
                    'name': name,
                    'period': period,
                    'entry_date': entry_date,
                    'exit_date': bar_date,
                    'entry_price': round(entry_price_val / PRICE_F, 4),
                    'exit_price': round(exit_price_val / PRICE_F, 4),
                    'ret_pct': round(ret_pct, 2),
                    'mfe_pct': round(mfe, 2),
                    'mae_pct': round(mae, 2),
                    'hold_bars': i - entry_idx,
                    'buy_mode': buy_mode,
                    'sell_mode': sell_mode,
                }
                trades.append(trade)

                in_trade = False
                entry_idx = None
                entry_price_val = None

    return trades


# ══════════════════════════════════════════════════════════════════
# 做T配对回测：卖信号→买信号（逆向配对）
# ══════════════════════════════════════════════════════════════════

T_STOP_PCT = 2.0  # 做T创新高止损线


def backtest_t_cycle(code, name, period, months=None, sell_signal='cci_div', buy_mode='star+ma5+ma10+safe',
                      no_stop=False):
    """做T完整周期回测：卖信号入场 → 买信号/止损出场

    sell_signal: 'cci_div' | 'star_sell' | 'sell_reduce'
    buy_mode: ENTRY_MODES key — 买回信号
    no_stop: True=不设止损，纯信号到信号
    """
    rows = _load_csv(code, period)
    if not rows or len(rows) < SKIP_BARS:
        return []

    daily_zones = _load_daily_zones(code)
    buy_entry_fn = ENTRY_MODES.get(buy_mode)
    buy_is_ctx = buy_entry_fn is None

    # ── 卖信号检测 ──
    if sell_signal == 'sell_reduce':
        sell_is_ctx = True  # 需要上下文
        sig_name = '减仓'
    elif sell_signal == 'cci_div':
        sell_is_ctx = False
        sell_detect_fn = lambda r: _has_cci_top_divergence(r)
    else:
        sell_is_ctx = False
        sell_detect_fn = lambda r: _has_star_sell(r)

    trades = []
    in_trade = False
    sell_idx = None
    sell_price = None

    for i in range(SKIP_BARS, len(rows)):
        r = rows[i]
        bar_date = r.get('date', '').strip()
        bar_close = float(r['close'])

        if months:
            try:
                d = datetime.strptime(bar_date, '%Y%m%d')
                cutoff = datetime.now() - __import__('datetime').timedelta(days=months * 30)
                if d < cutoff:
                    continue
            except Exception:
                pass

        zone = daily_zones.get(bar_date, 'weak')

        if not in_trade:
            # 检测卖出信号
            if sell_is_ctx:
                ok = _has_star_sell(r) and _close_below(r, 'ma5') and \
                     _no_recent_golden(rows, i, 20) and \
                     (_get_period_below_ema50('min15', code, bar_date) if code else True)
            else:
                ok = sell_detect_fn(r)

            if ok:
                in_trade = True
                sell_idx = i
                sell_price = bar_close
        else:
            # 检测买回信号
            buy_ok = False
            if buy_is_ctx:
                buy_ok = _check_entry_ctx(buy_mode, rows, i, r, code)
            else:
                buy_ok = buy_entry_fn(r)

            # 止损（可选）
            stop_hit = False
            if not no_stop and (bar_close - sell_price) / sell_price * 100 >= T_STOP_PCT:
                stop_hit = True

            if buy_ok or stop_hit:
                buyback_price = bar_close
                ret_pct = (sell_price - buyback_price) / sell_price * 100

                # 持仓期间最大偏移
                mfe = 0.0
                mae = 0.0
                for j in range(sell_idx + 1, i + 1):
                    l = float(rows[j]['low'])
                    h = float(rows[j]['high'])
                    mfe = max(mfe, (sell_price - l) / sell_price * 100)
                    mae = min(mae, (sell_price - h) / sell_price * 100)

                reason = '止损' if stop_hit else f'买回({buy_mode})'
                trades.append({
                    'code': code, 'name': name, 'period': period,
                    'entry_date': rows[sell_idx].get('date', '').strip(),
                    'exit_date': bar_date,
                    'sell_price': round(sell_price / PRICE_F, 4),
                    'buyback_price': round(buyback_price / PRICE_F, 4),
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


def print_t_cycle_report(all_trades, months, period, sell_signal, buy_mode):
    """打印做T配对回测报告"""
    sig_label = {'cci_div': 'CCI顶背驰', 'star_sell': '★卖'}.get(sell_signal, sell_signal)
    buy_label = {'star+ma5+ma10+safe': 'MA试错', 'star+ma5+ma10+safe+jincha': '金叉'}.get(buy_mode, buy_mode)

    print(f'\n{"="*100}')
    print(f'  做T配对回测: {sig_label}卖出 → {buy_label}买回 — {months}个月 — {period}')
    print(f'  (止损: 涨超{T_STOP_PCT}%创新高强制买回)')
    print(f'{"="*100}')

    if not all_trades:
        print('\n  (无符合条件的做T配对)\n')
        return

    n = len(all_trades)
    wins = [t for t in all_trades if t['ret_pct'] > 0]
    wr = len(wins) / n * 100
    avg_ret = sum(t['ret_pct'] for t in all_trades) / n
    avg_hold = sum(t['hold_bars'] for t in all_trades) / n

    # 按出场方式分组
    by_reason = defaultdict(list)
    for t in all_trades:
        by_reason[t['exit_reason']].append(t)

    avg_mfe = sum(t.get('mfe_pct', 0) for t in all_trades) / n
    avg_mae = sum(t.get('mae_pct', 0) for t in all_trades) / n
    print(f'\n  总配对: {n}笔  胜率: {wr:.1f}%  均收益: {avg_ret:+.2f}%  均持仓: {avg_hold:.0f}根  MFE:{avg_mfe:+.2f}%  MAE:{avg_mae:+.2f}%')
    print(f'\n  {"出场方式":<20} {"笔数":>5} {"胜率":>7} {"均收益":>8} {"均持(根)":>9}')
    print(f'  {"-"*55}')
    for reason in sorted(by_reason):
        items = by_reason[reason]
        nn = len(items)
        w = len([t for t in items if t['ret_pct'] > 0]) / nn * 100
        a = sum(t['ret_pct'] for t in items) / nn
        ah = sum(t['hold_bars'] for t in items) / nn
        print(f'  {reason:<20} {nn:>5} {w:>6.1f}% {a:>+7.2f}% {ah:>8.0f}根')

    # 收益分布
    print(f'\n  {"收益分布":}')
    bins = [(-100, -3), (-3, -2), (-2, -1), (-1, 0), (0, 1), (1, 2), (2, 5), (5, 100)]
    for lo, hi in bins:
        count = sum(1 for t in all_trades if lo <= t['ret_pct'] < hi)
        bar = '#' * max(1, count * 3)
        label = f'{lo}~{hi}%'
        print(f'    {label:>10}: {count:>3} {bar}')

    # 最近5笔
    print(f'\n  最近5笔:')
    for t in sorted(all_trades, key=lambda x: x['entry_date'], reverse=True)[:5]:
        print(f'    {t["entry_date"]} {t["code"]:<12} 卖{t["sell_price"]:.2f} → {t["exit_reason"]}@{t["buyback_price"]:.2f}  {t["ret_pct"]:+.2f}%  持{t["hold_bars"]}根')

    print()


# ══════════════════════════════════════════════════════════════════
# ★卖聚集效应回测（做T信号密集度）
# ══════════════════════════════════════════════════════════════════

CLUSTER_LOOKAHEAD = 20  # 做T看未来20根bar


def backtest_sell_cluster(code, name, period, months=None, boundary='ma_death', signal_type='star_sell'):
    """在上涨阶段窗口内统计做T信号的聚集效应

    boundary:
      - 'ma_death': 窗口 = MA5金叉MA10 → MA5死叉MA10
      - 'expma_break': 窗口 = close上穿expma50 → close下穿expma50
    signal_type:
      - 'star_sell': ★卖
      - 'cci_div': CCI顶背驰
      - 'cci_cross_130': CCI从峰值(>200)跌破130
    """
    rows = _load_csv(code, period)
    if not rows or len(rows) < SKIP_BARS + CLUSTER_LOOKAHEAD:
        return []

    # 选择窗口边界检测函数
    if boundary == 'ma_death':
        cross_up_fn = _ma5_cross_above_ma10
        cross_down_fn = _ma5_cross_below_ma10
        in_window_fn = lambda r: _ma_above(r, 'ma5', 'ma10')
    else:  # 'expma_break'
        cross_up_fn = _close_cross_above_ema50
        cross_down_fn = _close_cross_below_ema50
        in_window_fn = lambda r: float(r.get('close', 0)) > float(r.get('expma50', 0) or 0)

    # 选择信号检测函数
    if signal_type == 'cci_div':
        has_signal_fn = _has_cci_top_divergence
        sig_name = 'CCI顶背驰'
    elif signal_type == 'cci_cross_130':
        has_signal_fn = None  # 需要上下文，在循环中特殊处理
        sig_name = 'CCI跌破130'
    else:
        has_signal_fn = _has_star_sell
        sig_name = '★卖'

    # 第一步：扫描信号，标注所属窗口
    windows = []  # [(start_i, end_i, [signal_indices])]
    current_start = None
    current_sells = []
    cci_was_above_200 = False  # 用于cci_cross_130

    for i in range(SKIP_BARS, len(rows)):
        r = rows[i]
        bar_date = r.get('date', '').strip()

        if months:
            try:
                d = datetime.strptime(bar_date, '%Y%m%d')
                cutoff = datetime.now() - __import__('datetime').timedelta(days=months * 30)
                if d < cutoff:
                    continue
            except Exception:
                pass

        if current_start is None:
            if cross_up_fn(rows, i) or (in_window_fn(r) and not cross_down_fn(rows, i)):
                current_start = i
                current_sells = []
                cci_was_above_200 = False
        else:
            # 窗口结束条件：死叉/下穿 或 窗口状态丢失
            if cross_down_fn(rows, i) or not in_window_fn(r):
                if current_sells:
                    windows.append((current_start, i, current_sells))
                current_start = None
                current_sells = []
                cci_was_above_200 = False
            else:
                # ── 信号检测 ──
                detected = False
                if signal_type == 'cci_cross_130':
                    # CCI从峰值(>200)跌破130
                    try:
                        curr_cci = float(r.get('cci', 0))
                        prev_cci = float(rows[i-1].get('cci', 0)) if i > 0 else 0
                    except (ValueError, TypeError):
                        curr_cci = 0
                        prev_cci = 0
                    if curr_cci > 200:
                        cci_was_above_200 = True
                    if cci_was_above_200 and prev_cci >= 130 and curr_cci < 130:
                        detected = True
                        cci_was_above_200 = False  # 重置，等下次再上200
                else:
                    detected = has_signal_fn(r)

                if detected:
                    current_sells.append(i)

    # 处理最后一个未闭合窗口
    if current_start is not None and current_sells:
        windows.append((current_start, len(rows) - 1, current_sells))

    # 第二步：对每个信号测量做T效果
    results = []
    for win_start, win_end, sell_indices in windows:
        total_sells = len(sell_indices)
        for rank, si in enumerate(sell_indices, 1):
            if si >= len(rows) - CLUSTER_LOOKAHEAD:
                continue
            r = rows[si]
            entry_price = float(r['close'])
            bar_date = r.get('date', '').strip()

            # 未来N根bar的最大跌幅/最大反弹
            max_drop = 0.0
            max_rise = 0.0
            for j in range(si + 1, min(si + CLUSTER_LOOKAHEAD + 1, len(rows))):
                c = float(rows[j]['close'])
                ret = (c - entry_price) / entry_price * 100
                if ret < max_drop:
                    max_drop = ret
                if ret > max_rise:
                    max_rise = ret

            # 窗口结束时的收益（结构最终走向）
            win_close = float(rows[win_end]['close']) if win_end < len(rows) else float(rows[-1]['close'])
            win_end_ret = (win_close - entry_price) / entry_price * 100

            drop_gt_1pct = max_drop <= -1.0
            drop_gt_2pct = max_drop <= -2.0

            results.append({
                'code': code,
                'name': name,
                'period': period,
                'date': bar_date,
                'entry_price': round(entry_price / PRICE_F, 4),
                'max_drop': round(max_drop, 2),
                'max_rise': round(max_rise, 2),
                'win_end_ret': round(win_end_ret, 2),
                'drop_gt_1pct': drop_gt_1pct,
                'drop_gt_2pct': drop_gt_2pct,
                'rank': rank,
                'total_in_window': total_sells,
                'window_bars': win_end - win_start,
            })

    return results


def print_cluster_report(all_results, boundary, months, period, signal_type='star_sell'):
    """打印做T信号聚集效应报告"""
    if not all_results:
        print('\n  (无符合条件的信号)\n')
        return

    sig_label = {'cci_div': 'CCI顶背驰', 'cci_cross_130': 'CCI跌破130', 'star_sell': '★卖'}.get(signal_type, '★卖')
    boundary_label = {'ma_death': 'MA5>MA10→死叉', 'expma_break': 'close>EXPMA黄线→跌破'}[boundary]

    print(f'\n{"="*100}')
    print(f'  {sig_label}聚集效应 — {months}个月 — {period} — 窗口边界: {boundary_label}')
    print(f'{"="*100}')

    # ── 按排名分组 ──
    by_rank = defaultdict(list)
    for r in all_results:
        by_rank[r['rank']].append(r)

    print(f'\n  {"─"*80}')
    print(f'  【按出现顺序】第N个{sig_label}的做T效果')
    print(f'  {"排名":<10} {"信号数":>6} {"跌>1%":>8} {"跌>2%":>8} {"均跌幅":>8} {"均反弹":>8}')
    print(f'  {"-"*60}')
    for rank in sorted(by_rank):
        items = by_rank[rank]
        n = len(items)
        d1 = len([s for s in items if s['drop_gt_1pct']])
        d2 = len([s for s in items if s['drop_gt_2pct']])
        avg_drop = sum(s['max_drop'] for s in items) / n
        avg_rise = sum(s['max_rise'] for s in items) / n
        print(f'  第{rank}个{sig_label}   {n:>5} {d1/n*100:>7.1f}% {d2/n*100:>7.1f}% {avg_drop:>+7.2f}% {avg_rise:>+7.2f}%')

    # ── 按窗口内总数分组 ──
    by_total = defaultdict(list)
    for r in all_results:
        by_total[r['total_in_window']].append(r)

    print(f'\n  {"─"*80}')
    print(f'  【按窗口信号密度】窗口内共N个{sig_label}时的做T效果')
    print(f'  {"窗口内总数":<12} {"窗口数":>6} {"信号数":>6} {"跌>1%":>8} {"跌>2%":>8} {"均跌幅":>8} {"均反弹":>8}')
    print(f'  {"-"*65}')
    for total in sorted(by_total):
        items = by_total[total]
        n = len(items)
        d1 = len([s for s in items if s['drop_gt_1pct']])
        d2 = len([s for s in items if s['drop_gt_2pct']])
        avg_drop = sum(s['max_drop'] for s in items) / n
        avg_rise = sum(s['max_rise'] for s in items) / n
        n_windows = len(set((s['code'], s['date'].split()[0] if ' ' in str(s.get('window_start','')) else '') for s in items))
        # count unique windows using a simple heuristic
        unique_wins = set()
        for s in items:
            unique_wins.add(f"{s['code']}_{s['date']}")
        n_windows_approx = len(unique_wins)  # rough; per-signal unique date
        # Better: count by start dates
        print(f'  共{total}个{sig_label}      {n_windows_approx:>5} {n:>5} {d1/n*100:>7.1f}% {d2/n*100:>7.1f}% {avg_drop:>+7.2f}% {avg_rise:>+7.2f}%')

    # ── 聚集 vs 单次 直接对比 ──
    single = [s for s in all_results if s['total_in_window'] == 1]
    clustered = [s for s in all_results if s['total_in_window'] >= 2]
    clustered_3 = [s for s in all_results if s['total_in_window'] >= 3]

    print(f'\n  {"─"*80}')
    print(f'  【聚集 vs 单次 对比】')
    for label, items in [(f'单次(窗口内仅1个{sig_label})', single),
                          (f'聚集(窗口内≥2个{sig_label})', clustered),
                          (f'强聚集(窗口内≥3个{sig_label})', clustered_3)]:
        if not items:
            continue
        n = len(items)
        d1 = len([s for s in items if s['drop_gt_1pct']])
        d2 = len([s for s in items if s['drop_gt_2pct']])
        avg_drop = sum(s['max_drop'] for s in items) / n
        avg_rise = sum(s['max_rise'] for s in items) / n
        print(f'  {label:<28} {n:>4}个信号  跌>1%:{d1/n*100:5.1f}%  跌>2%:{d2/n*100:5.1f}%  均跌幅:{avg_drop:+6.2f}%  均反弹:{avg_rise:+6.2f}%')

    # ── 失败去向：单次信号的盈亏分布 ──
    if single:
        print(f'\n  {"─"*80}')
        print(f'  【单次{sig_label}的盈亏去向】')
        # 分类: 成功做T(跌>2%) / 勉强(跌1-2%) / 横盘(±1%) / 小卖飞(涨1-2%) / 大卖飞(涨>2%)
        success = [s for s in single if s['max_drop'] <= -2.0]
        marginal = [s for s in single if -2.0 < s['max_drop'] <= -1.0]
        flat = [s for s in single if -1.0 < s['max_drop'] <= 0 and s['max_rise'] <= 1.0]
        small_fly = [s for s in single if s['max_rise'] > 1.0 and s['max_rise'] <= 2.0 and s['max_drop'] > -2.0]
        big_fly = [s for s in single if s['max_rise'] > 2.0 and s['max_drop'] > -2.0]
        # 剩余: 跌<2% 但 涨>1% 其中 涨1-2%算小卖飞，涨>2%算大卖飞... 上面条件可能有重叠，用剩余兜底
        for label, items, icon in [
            ('成功做T(跌>2%)', success, 'o'),
            ('勉强(跌1~2%)', marginal, '~'),
            ('横盘(±1%内)', flat, '-'),
            ('小卖飞(涨1~2%)', small_fly, '!'),
            ('大卖飞(涨>2%)', big_fly, 'X'),
        ]:
            if not items:
                continue
            n = len(items)
            pct = n / len(single) * 100
            avg_d = sum(s['max_drop'] for s in items) / n
            avg_r = sum(s['max_rise'] for s in items) / n
            print(f'  [{icon}] {label:<22} {n:>4}个 ({pct:5.1f}%)  均跌幅:{avg_d:+6.2f}%  均反弹:{avg_r:+6.2f}%')

        # ── 结构最终去向：信号到窗口结束的全程收益 ──
        print(f'\n  {"─"*80}')
        print(f'  【单次{sig_label}到窗口结束的结构最终去向】')
        win_up = [s for s in single if s['win_end_ret'] > 2.0]
        win_up_small = [s for s in single if 0 < s['win_end_ret'] <= 2.0]
        win_down_small = [s for s in single if -2.0 < s['win_end_ret'] <= 0]
        win_down = [s for s in single if s['win_end_ret'] <= -2.0]
        for label, items, icon in [
            ('结构转跌(跌>2% 做T成功)', win_down, 'o'),
            ('结构小跌(跌0~2%)', win_down_small, '~'),
            ('结构小涨(涨0~2%)', win_up_small, '!'),
            ('结构续涨(涨>2% 卖飞)', win_up, 'X'),
        ]:
            if not items:
                continue
            n = len(items)
            pct = n / len(single) * 100
            avg_wr = sum(s['win_end_ret'] for s in items) / n
            avg_bars = sum(s['window_bars'] for s in items) / n
            print(f'  [{icon}] {label:<26} {n:>4}个 ({pct:5.1f}%)  均收益:{avg_wr:+6.2f}%  均窗口:{avg_bars:6.0f}根')

    print()


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
    parser.add_argument('--sell-compare', action='store_true', help='对比所有卖信号过滤模式')
    parser.add_argument('--pair', action='store_true', help='买→卖配对: MA/金叉 × 裸★卖/减仓 交叉统计')
    parser.add_argument('--t-cycle', action='store_true', help='做T配对: CCI顶背驰/★卖 → MA试错/金叉买回')
    parser.add_argument('--t-sell', type=str, choices=['cci_div', 'star_sell'], default='cci_div',
                        help='做T卖出信号: cci_div=CCI顶背驰, star_sell=★卖')
    parser.add_argument('--t-buy', type=str, choices=['star+ma5+ma10+safe', 'star+ma5+ma10+safe+jincha'], default='star+ma5+ma10+safe',
                        help='做T买回信号: MA试错 / 金叉')
    parser.add_argument('--cluster', action='store_true', help='做T信号聚集效应: 上涨窗口内信号密度与做T效果')
    parser.add_argument('--cluster-signal', type=str, choices=['star_sell', 'cci_div', 'cci_cross_130'], default='star_sell',
                        help='聚集信号类型: star_sell=★卖, cci_div=CCI顶背驰, cci_cross_130=CCI>200峰值后跌破130')
    parser.add_argument('--cluster-boundary', type=str, choices=['ma_death', 'expma_break', 'both'], default='both',
                        help='聚集窗口边界: ma_death=MA5金叉→死叉, expma_break=close上穿→下穿expma50, both=两种都跑')
    parser.add_argument('--universe', type=str, choices=['volume_leader', 'tracking'], default='volume_leader',
                        help='标的范围: volume_leader=量领强者, tracking=14只跟踪标的')
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

    # ── compare 模式 ──
    if args.compare:
        comp_universe = load_tracking_universe() if args.universe == 'tracking' else universe
        mode_names = [m for m in ALL_ENTRY_MODES if m != 'any']
        all_mode_results = {}

        for mode in mode_names:
            all_trades = []
            for stock in comp_universe:
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

    # ── sell-compare 模式 ──
    if args.sell_compare:
        sc_universe = load_tracking_universe() if args.universe == 'tracking' else universe
        ulabel = '14只跟踪标的' if args.universe == 'tracking' else '量领强者'
        print(f'\n{"="*100}')
        print(f'  卖信号过滤模式对比 [{ulabel}] — {args.months}个月 — {args.period}')
        print(f'{"="*100}')
        print(f'  {"模式":<18} {"信号数":>6} {"命中率":>7} {"均跌幅":>8} {"死叉率":>7} {"假信号率":>8} {"均反弹":>8}')
        print(f'  {"-"*82}')
        for mode in ALL_SELL_MODES:
            all_signals = []
            for stock in sc_universe:
                code, name = stock['code'], stock['name']
                for period in periods_to_run:
                    sigs = backtest_sell_stock(code, name, period, months=args.months, sell_mode=mode)
                    all_signals.extend(sigs)
            if not all_signals:
                continue
            n = len(all_signals)
            success_n = len([s for s in all_signals if s['success']])
            hit_rate = success_n / n * 100
            avg_drop = sum(s['max_drop_pct'] for s in all_signals) / n
            death_n = len([s for s in all_signals if s['has_death']])
            death_rate = death_n / n * 100
            false_n = len([s for s in all_signals if s['max_rise_pct'] > 2.0])
            false_rate = false_n / n * 100
            avg_rise = sum(s['max_rise_pct'] for s in all_signals) / n
            print(f'  {mode:<18} {n:>6} {hit_rate:>6.1f}% {avg_drop:>+7.2f}% '
                  f'{death_rate:>6.1f}% {false_rate:>7.1f}% {avg_rise:>+7.2f}%')
        print()
        return

    # ── pair 模式：买→卖配对 交叉统计 ──
    if args.pair:
        combos = [
            ('star+ma5+ma10+safe',       'sell_any',            'MA试错→裸★卖'),
            ('star+ma5+ma10+safe',       'sell_break_ma5_safe', 'MA试错→减仓'),
            ('star+ma5+ma10+safe',       'sell_death',          'MA试错→死叉'),
            ('star+ma5+ma10+safe+jincha', 'sell_any',           '金叉→裸★卖'),
            ('star+ma5+ma10+safe+jincha', 'sell_break_ma5_safe', '金叉→减仓'),
            ('star+ma5+ma10+safe+jincha', 'sell_death',         '金叉→死叉'),
        ]
        print(f'\n{"="*100}')
        print(f'  买→卖配对回测 — {args.months}个月 — {args.period}')
        print(f'  (MA试错=★买+MA5>10>20+无死叉+60分黄线  金叉=MA试错+EXPMA金叉)')
        print(f'  (裸★卖=★卖  减仓=★卖+close<MA5+无金叉+15分黄线下)')
        print(f'{"="*100}')
        print(f'  {"组合":<20} {"笔数":>5} {"胜率":>7} {"均收益":>8} {"均持(根)":>9} {"均MFE":>7} {"均MAE":>7}')
        print(f'  {"-"*75}')
        for buy_mode, sell_mode, label in combos:
            all_trades = []
            for stock in universe:
                code, name = stock['code'], stock['name']
                for period in periods_to_run:
                    trades = backtest_pair(code, name, period, months=args.months,
                                           buy_mode=buy_mode, sell_mode=sell_mode)
                    all_trades.extend(trades)
            if not all_trades:
                continue
            n = len(all_trades)
            wr = len([t for t in all_trades if t['ret_pct'] > 0]) / n * 100
            avg = sum(t['ret_pct'] for t in all_trades) / n
            avg_hold = sum(t['hold_bars'] for t in all_trades) / n
            avg_mfe = sum(t['mfe_pct'] for t in all_trades) / n
            avg_mae = sum(t['mae_pct'] for t in all_trades) / n
            print(f'  {label:<20} {n:>5} {wr:>6.1f}% {avg:>+7.2f}% {avg_hold:>8.1f} {avg_mfe:>+6.2f}% {avg_mae:>+6.2f}%')
        print()
        return

    # ── t-cycle 模式：做T配对回测 ──
    if args.t_cycle:
        tc_universe = load_tracking_universe() if args.universe == 'tracking' else universe
        combos = [
            ('sell_reduce', 'star', '减仓→★买(无止损)'),
        ]
        for sell_sig, buy_mode, label in combos:
            all_trades = []
            for stock in tc_universe:
                code, name = stock['code'], stock['name']
                for period in periods_to_run:
                    trades = backtest_t_cycle(code, name, period, months=args.months,
                                             sell_signal=sell_sig, buy_mode=buy_mode, no_stop=True)
                    all_trades.extend(trades)
            print_t_cycle_report(all_trades, args.months, args.period, sell_sig, buy_mode)
        return

    # ── cluster 模式：做T信号聚集效应 ──
    if args.cluster:
        cluster_universe = load_tracking_universe() if args.universe == 'tracking' else universe
        boundaries = ['ma_death', 'expma_break'] if args.cluster_boundary == 'both' else [args.cluster_boundary]
        for boundary in boundaries:
            all_results = []
            for stock in cluster_universe:
                code, name = stock['code'], stock['name']
                for period in periods_to_run:
                    results = backtest_sell_cluster(code, name, period, months=args.months,
                                                   boundary=boundary, signal_type=args.cluster_signal)
                    all_results.extend(results)
            print_cluster_report(all_results, boundary, args.months, args.period, args.cluster_signal)
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

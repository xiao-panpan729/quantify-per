"""信号配对回测子系统 — 历史信号的出入场统计"""
import sys
import os
import csv
import json
import argparse
from pathlib import Path
from datetime import datetime, date, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.volume_leader.shared import load_universe, TRACKING_DIR, MIN_PRICE_FACTOR
from tools.volume_leader.filter_engine import (
    has_star_buy, has_star_sell, has_golden,
    check_ma_chain, check_expma_golden,
    check_no_recent_death, check_no_recent_golden,
    check_close_below_ma, check_pe_gate, has_cci_top_divergence,
)


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

# _has_star_buy / _has_star_sell / _has_golden / _ma_above / _close_below / _has_cci_top_divergence
# → 已迁移到 filter_engine，本地保留别名以兼容 ENTRY_MODES / SELL_MODES 的 lambda 引用
_has_star_buy = has_star_buy
_has_star_sell = has_star_sell
_has_golden = has_golden
_has_cci_top_divergence = has_cci_top_divergence

def _ma_above(row, fast, slow):
    """快速均线 > 慢速均线（保留自定义参数，filter_engine.check_ma_chain 只做默认三链）"""
    try:
        return float(row.get(fast, 0)) > float(row.get(slow, 0))
    except (ValueError, TypeError):
        return False

def _close_below(row, ma_col):
    return check_close_below_ma(row, ma_col)




# ── 入场模式定义 ──
#   lambda(r): 状态模式（只看当前bar自身）
#   None: 上下文模式（需要看前后bar上下文，由 _check_entry_ctx 处理）
ENTRY_MODES = {
    # ── 逐层诊断模式（临时，测完删） ──
    'L0_star':                   None,   # ★买裸信号（无任何过滤）
    'L1_star+ma':                None,   # ★买 + MA5>10>20（当根）
    'L2_star+ma+nodeath':        None,   # + 无死叉(20根)
    'L3_star+ma+nodeath+min60':  None,   # + 60分黄线（=完整MA级）
    'L4_star+ma+nodeath+min60+pe_d': None,  # + 日线PE门禁
    'L5_star+ma+nodeath+min60+pe_d+jincha': None,  # + 5分EXPMA金叉（金叉级）
    # ── 路径对比测试 (★买后的结构路径: MA vs 金叉 vs 15分共振) — 临时 ──
    'T1_ma+nodeath+min60+pe_d':                None,  # =L4基线: ★买+MA+无死叉+60分黄线+日线PE
    'T2_jincha+min60+pe_d':                     None,  # ★买+5分金叉+60分黄线+日线PE (无MA无死叉)
    'T3_jincha+nodeath+min60+pe_d':             None,  # ★买+5分金叉+无死叉+60分黄线+日线PE
    'T4_ma+nodeath+15jincha+min60+pe_d':        None,  # ★买+MA+无死叉+15分同日金叉+60分黄线+日线PE
    'T5_jincha+nodeath+15jincha+min60+pe_d':    None,  # ★买+5分金叉+无死叉+15分同日金叉+60分黄线+日线PE
    # ── 追赶期测试 (★买→等结构确认→入场，非当根) — 临时 ──
    'Z1_star_wait_jincha+min60+pe_d':          None,  # ★买→12根内等5分金叉→入场 + 60分+PE
    'Z2_star_wait_ma+min60+pe_d':              None,  # ★买→12根内等MA理顺→入场 + 60分+PE
    'Z3_star_wait_ma_wait_jincha+min60+pe_d':  None,  # ★买→12根内MA理顺→等金叉→入场 + 60分+PE
    # ── 正式模式 ──
    # 基准 (MA链)
    'star+ma5+ma10+safe':        None,   # MA级: ★买+MA链+无死叉+60分黄线上
    'star+ma5+ma10+safe+jincha': None,   # 金叉级: MA级+EXPMA金叉

    # PE门禁 (5分钟周期)
    'star+ma5+ma10+safe+pe':     None,   # MA级+PE(5分)
    'star+ma5+ma10+safe+jincha+pe': None, # 金叉级+PE(5分) ★金叉级最佳

    # PE门禁 (日线周期)
    'star+ma5+ma10+safe+pe_d':     None,  # MA级+PE(日线) ★MA级最佳
    'star+ma5+ma10+safe+jincha+pe_d': None, # 金叉级+PE(日线)

    # CCI速率 (叠加在MA链上, 冗余验证用)
    'star+ma5+ma10+safe+cci_rate':     None,  # MA级+CCI速率
    'star+ma5+ma10+safe+jincha+cci_rate': None,  # 金叉级+CCI速率
    'star+ma5+ma10+safe+pe_d+cci_rate':    None,  # MA级+PE(日线)+CCI速率
    'star+ma5+ma10+safe+jincha+pe+cci_rate': None,  # 金叉级+PE(5分)+CCI速率

    # ★ CCI速率替代MA链 (核心对比: CCI速率能否独立工作?)
    'star+cci_rate+safe':           None,  # CCI速率+60分黄线 (无MA链)
    'star+cci_rate+safe+pe':        None,  # +PE(5分)
    'star+cci_rate+safe+pe_d':      None,  # +PE(日线)
}

ALL_ENTRY_MODES = list(ENTRY_MODES.keys())

# ── 卖信号模式定义 ──
SELL_MODES = {
    # 减仓卖: ★卖+close<MA5+无金叉+周期黄线下
    'sell_break_ma5_safe':       None,
    'sell_break_ma5_safe_min5':  None,
    'sell_break_ma5_safe_min30': None,
    # CCI背驰系列（做T用）
    'sell_cci_div':                  lambda r: _has_star_sell(r) and _has_cci_top_divergence(r),
    'sell_cci_div_safe':             None,
    'sell_cci_div_break_ma5_safe':   None,
}
ALL_SELL_MODES = list(SELL_MODES.keys())


MIN60_CACHE = {}
MIN15_CACHE = {}
DAILY_PE_CACHE = {}  # {code: {date: pe_chg_5}}


def _pe_not_rising(row):
    """PE非升熵 → filter_engine.check_pe_gate"""
    return check_pe_gate(row)


def _cci_recovery_ok(rows, i, min_speed=5.0):
    """CCI回弹速率门禁: 从最近极端低点(≤-100)恢复的速度 ≥ min_speed CCI点/根

    衡量V型反转质量: 快速回弹=强需求, 慢速回弹=弱需求。
    无极端低点时放行(没有超卖就不需要回弹验证)。
    """
    lookback = min(30, i)
    cci_vals = []
    for j in range(i - lookback, i + 1):
        try:
            cci_vals.append(float(rows[j].get('cci', 0) or 0))
        except (ValueError, TypeError):
            cci_vals.append(0)
    if not cci_vals:
        return False
    min_cci = min(cci_vals)
    if min_cci > -100:
        return True  # 无极端低点, 不需要回弹验证
    min_pos = cci_vals.index(min_cci)
    bars_from_min = len(cci_vals) - 1 - min_pos
    if bars_from_min < 1:
        return False  # 正在最低点, 尚未回弹
    speed = (cci_vals[-1] - min_cci) / bars_from_min
    return speed >= min_speed


def _get_daily_pe_ok(code, bar_date):
    """日线PE非升熵 (用daily CSV的pe_chg_5)"""
    if code not in DAILY_PE_CACHE:
        rows = _load_csv(code, 'daily')
        if not rows:
            DAILY_PE_CACHE[code] = {}
            return True
        DAILY_PE_CACHE[code] = {}
        for r in rows:
            d = r.get('date', '').strip()
            try:
                pe_chg = float(r.get('pe_chg_5', 0) or 0)
                DAILY_PE_CACHE[code][d] = pe_chg >= -0.02
            except (ValueError, TypeError):
                DAILY_PE_CACHE[code][d] = True
    return DAILY_PE_CACHE[code].get(bar_date, True)


def _no_recent_death(rows, i, n=20):
    """→ filter_engine.check_no_recent_death"""
    return check_no_recent_death(rows, i, n)


def _no_recent_golden(rows, i, n=20):
    """→ filter_engine.check_no_recent_golden"""
    return check_no_recent_golden(rows, i, n)


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


def _get_min15_jincha(code, bar_ts):
    """15分钟 EXPMA12 > EXPMA50（金叉状态），用时间戳精确定位对应15分bar"""
    if not code or not bar_ts:
        return False
    data = _cascade_load_csv(code, 'min15')
    rows = data.get('rows', []) if data else []
    if not rows:
        return False
    idx = _find_bar_by_ts(rows, bar_ts)
    if idx < 0:
        return False
    r = rows[idx]
    try:
        return check_expma_golden(r)
    except (ValueError, TypeError):
        return False


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


# ── 共振级联缓存 ──
RESONANCE_CACHE = {}  # {code: {date: resonance_type}}


def _check_resonance(code, bar_date):
    """检测15/30分钟共振: 返回 'double'(15+30) / 'single'(仅15或30) / ''

    使用±1天窗口匹配, 对齐monitor实时级联检测。
    15分和30分金叉不必精确同天, 差1天也算共振。
    """
    if code not in RESONANCE_CACHE:
        RESONANCE_CACHE[code] = {}
        for period in ('min15', 'min30'):
            rows = _load_csv(code, period)
            if rows:
                for r in rows:
                    d = r.get('date', '').strip()
                    cross = (r.get('expma_cross', '') or '').strip()
                    if cross == '金叉':
                        RESONANCE_CACHE[code].setdefault(d, set()).add(period)

    # ±1天窗口: 收集 entry_date 前后1天内的所有金叉周期
    try:
        dt = datetime.strptime(bar_date, '%Y%m%d')
    except ValueError:
        return ''
    all_perms = set()
    for offset in (-1, 0, 1):
        d = (dt + timedelta(days=offset)).strftime('%Y%m%d')
        all_perms.update(RESONANCE_CACHE[code].get(d, set()))

    if 'min15' in all_perms and 'min30' in all_perms:
        return 'double'
    if all_perms:
        return 'single'
    return ''


# ── Cascade回测（模拟 monitor.py _check_cascade 逻辑） ──
_CASCADE_CSV_CACHE = {}  # {code+period: rows}


def _cascade_load_csv(code, period):
    """带缓存的CSV加载，避免重复读盘"""
    key = f'{code}_{period}'
    if key not in _CASCADE_CSV_CACHE:
        rows = _load_csv(code, period, enrich=False)
        if rows:
            # 构建 timetsamp→row 的快速索引
            ts_map = {}
            for r in rows:
                ts = r.get('timestamp', '').strip()
                if ts:
                    ts_map[ts] = r
            _CASCADE_CSV_CACHE[key] = {'rows': rows, 'ts_map': ts_map}
    return _CASCADE_CSV_CACHE.get(key, {})


def _find_bar_by_ts(rows, target_ts):
    """找到 timestamp <= target_ts 的最近bar"""
    for i in range(len(rows) - 1, -1, -1):
        ts = rows[i].get('timestamp', '').strip()
        if ts and ts <= target_ts:
            return i
    return -1


def _check_cascade_bt(min5_rows, entry_idx, code, m15_window=8, signal_type='golden'):
    """向未来看: 入场后N根K线内15/30分是否有金叉/★买跟随

    参数:
        m15_window: 15分向未来看几根 (默认8)
        signal_type: 'golden'=只检金叉, 'starbuy'=只检★买
    min30固定6根。
    返回: 'both' / 'min15_only' / 'min30_only' / ''
    """
    entry_ts = min5_rows[entry_idx].get('timestamp', '').strip()
    if not entry_ts:
        return ''

    m15_check = _has_golden if signal_type == 'golden' else _has_star_buy
    # 30分只用金叉（用户指定"金叉共振"）
    m30_check = _has_golden

    m15_found = False
    m15_data = _cascade_load_csv(code, 'min15')
    m15_rows = m15_data.get('rows', []) if m15_data else []
    if m15_rows and entry_ts:
        idx15 = _find_bar_by_ts(m15_rows, entry_ts)
        if idx15 >= 0:
            for j in range(idx15 + 1, min(len(m15_rows), idx15 + 1 + m15_window)):
                if m15_check(m15_rows[j]):
                    m15_found = True
                    break

    m30_found = False
    m30_data = _cascade_load_csv(code, 'min30')
    m30_rows = m30_data.get('rows', []) if m30_data else []
    if m30_rows and entry_ts:
        idx30 = _find_bar_by_ts(m30_rows, entry_ts)
        if idx30 >= 0:
            for j in range(idx30 + 1, min(len(m30_rows), idx30 + 1 + 6)):
                if m30_check(m30_rows[j]):
                    m30_found = True
                    break

    if m15_found and m30_found:
        return 'both'
    if m15_found:
        return 'min15_only'
    if m30_found:
        return 'min30_only'
    return ''


def _check_entry_ctx(entry_mode, rows, i, row, code=None):
    """上下文相关的入场检测

    模式名编码:
      star+ma5+ma10+safe [+jincha] [+pe|pe_d] [+cci_rate]
        - MA链: MA5>MA10>MA20 + 无死叉 + 60分黄线上
      star+cci_rate+safe [+pe|pe_d]
        - CCI速率替代MA链: CCI回弹速率≥5 + 60分黄线上 (无MA/死叉要求)
        - jincha不适用(没有MA基础)
      - pe: PE(5分)非升熵
      - pe_d: PE(日线)非升熵
    """
    bar_date = row.get('date', '').strip()

    # ── ★买必须 ──
    if not _has_star_buy(row):
        return False

    # ── 逐层诊断模式 (L0~L5) — 临时 ──
    if entry_mode.startswith('L0_'):
        return True  # ★买裸信号，无任何过滤
    if entry_mode.startswith('L1_'):
        return _ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')
    if entry_mode.startswith('L2_'):
        if not (_ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')):
            return False
        return _no_recent_death(rows, i, 20)
    if entry_mode.startswith('L3_'):
        if not (_ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')):
            return False
        if not _no_recent_death(rows, i, 20):
            return False
        return _get_min60_above(code, bar_date) if code else True
    if entry_mode.startswith('L4_'):
        if not (_ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')):
            return False
        if not _no_recent_death(rows, i, 20):
            return False
        if code and not _get_min60_above(code, bar_date):
            return False
        if not _get_daily_pe_ok(code, bar_date):
            return False
        return True
    if entry_mode.startswith('L5_'):
        if not (_ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')):
            return False
        if not _no_recent_death(rows, i, 20):
            return False
        if code and not _get_min60_above(code, bar_date):
            return False
        if not _get_daily_pe_ok(code, bar_date):
            return False
        if not check_expma_golden(row):
            return False
        return True

    # ── 路径对比测试 (T1~T5) — 临时 ──
    # 所有T模式都有60分黄线+日线PE，变量是入口结构
    bar_ts = row.get('timestamp', '').strip()

    if entry_mode.startswith('T1_'):  # =L4: ★买+MA+无死叉+60分+PE
        if not (_ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')):
            return False
        if not _no_recent_death(rows, i, 20):
            return False
        if code and not _get_min60_above(code, bar_date):
            return False
        if not _get_daily_pe_ok(code, bar_date):
            return False
        return True

    if entry_mode.startswith('T2_'):  # ★买+5分金叉+60分+PE (无MA无死叉)
        if not check_expma_golden(row):
            return False
        if code and not _get_min60_above(code, bar_date):
            return False
        if not _get_daily_pe_ok(code, bar_date):
            return False
        return True

    if entry_mode.startswith('T3_'):  # ★买+5分金叉+无死叉+60分+PE
        if not check_expma_golden(row):
            return False
        if not _no_recent_death(rows, i, 20):
            return False
        if code and not _get_min60_above(code, bar_date):
            return False
        if not _get_daily_pe_ok(code, bar_date):
            return False
        return True

    if entry_mode.startswith('T4_'):  # ★买+MA+无死叉+15分金叉+60分+PE
        if not (_ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')):
            return False
        if not _no_recent_death(rows, i, 20):
            return False
        if code and not _get_min60_above(code, bar_date):
            return False
        if not _get_daily_pe_ok(code, bar_date):
            return False
        if code and not _get_min15_jincha(code, bar_ts):
            return False
        return True

    if entry_mode.startswith('T5_'):  # ★买+5分金叉+无死叉+15分金叉+60分+PE
        if not check_expma_golden(row):
            return False
        if not _no_recent_death(rows, i, 20):
            return False
        if code and not _get_min60_above(code, bar_date):
            return False
        if not _get_daily_pe_ok(code, bar_date):
            return False
        if code and not _get_min15_jincha(code, bar_ts):
            return False
        return True

    # ── CCI速率模式: 用CCI回弹替代MA链 ──
    if entry_mode.startswith('star+cci_rate'):
        if not _cci_recovery_ok(rows, i):
            return False
        # 60分黄线上 (安全网)
        if code and not _get_min60_above(code, bar_date):
            return False
        # PE门禁
        has_pe_5m = '+pe' in entry_mode and '+pe_d' not in entry_mode
        if has_pe_5m and not _pe_not_rising(row):
            return False
        if '+pe_d' in entry_mode and code and not _get_daily_pe_ok(code, bar_date):
            return False
        return True

    # ── MA链模式: MA5>10>20 + 无死叉 + 60分黄线 ──
    ok = _ma_above(row, 'ma5', 'ma10') and _ma_above(row, 'ma10', 'ma20')
    if not ok:
        return False

    env_ok = _no_recent_death(rows, i, 20) and (_get_min60_above(code, bar_date) if code else True)
    if not env_ok:
        return False

    # ── PE门禁 (5分钟周期, 不含+pe_d) ──
    has_pe_5m = '+pe' in entry_mode and '+pe_d' not in entry_mode
    if has_pe_5m and not _pe_not_rising(row):
        return False

    # ── PE门禁 (日线周期) ──
    if '+pe_d' in entry_mode:
        if code and not _get_daily_pe_ok(code, bar_date):
            return False

    # ── CCI回弹速率门禁 (MA链之上的附加条件) ──
    if '+cci_rate' in entry_mode and not _cci_recovery_ok(rows, i):
        return False

    # ── 金叉门禁 ──
    if '+jincha' in entry_mode:
        if not check_expma_golden(row):
            return False

    return True


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
    if base == 'sell_break_ma5_safe':
        ok = _has_star_sell(row) and _close_below(row, 'ma5')
    elif base == 'sell_cci_div_safe':
        ok = _has_star_sell(row) and _has_cci_top_divergence(row)
    elif base == 'sell_cci_div_break_ma5_safe':
        ok = _has_star_sell(row) and _has_cci_top_divergence(row) and _close_below(row, 'ma5')
    else:
        return False

    if not ok:
        return False
    bar_date = row.get('date', '').strip()
    return _no_recent_golden(rows, i, 20) and (_get_period_below_ema50(period, code, bar_date) if code else True)


def _is_sell_reduce(rows, i, code=None):
    """减仓卖出（与 monitor.py 对齐）：
    路径1: 死叉 → 无条件出场
    路径2: ★卖 + close<MA5 + 无金叉(20根) + 15分黄线下
    """
    row = rows[i]
    cross = (row.get('expma_cross', '') or '').strip()
    if cross == '死叉':
        return True

    if not _has_star_sell(row):
        return False
    if not _close_below(row, 'ma5'):
        return False
    if not _no_recent_golden(rows, i, 20):
        return False
    bar_date = row.get('date', '').strip()
    if code and not _get_period_below_ema50('min15', code, bar_date):
        return False
    return True


def _entry_price(row):
    """入场价：折中点 (low + close) / 2"""
    return (float(row['low']) + float(row['close'])) / 2


def _exit_price(row):
    """出场价：收盘价"""
    return float(row['close'])


def _extract_entry_factors(row, entry_mode):
    """从入场bar提取10维因子（per-bar滚动值，不依赖外部文件）

    返回 dict，可直接 **spread 到 trade dict 中
    """
    f = {}

    # ── F1: 入场级别 ──
    f['f_entry_level'] = '金叉级' if 'jincha' in entry_mode else 'MA级'

    # ── F2: MA链长 (5>10>20>60>120>250 连续对数) ──
    ma_pairs = [('ma5', 'ma10'), ('ma10', 'ma20'), ('ma20', 'ma60'),
                ('ma60', 'ma120'), ('ma120', 'ma250')]
    chain = 0
    for fast, slow in ma_pairs:
        try:
            if float(row.get(fast, 0) or 0) > float(row.get(slow, 0) or 0):
                chain += 1
            else:
                break
        except (ValueError, TypeError):
            break
    f['f_ma_chain'] = chain

    # ── F3: CCI状态 ──
    try:
        cci = float(row.get('cci', 0) or 0)
    except (ValueError, TypeError):
        cci = 0
    if cci <= -200:
        f['f_cci_state'] = '极端低位'
    elif cci < -100:
        f['f_cci_state'] = '低位'
    elif cci <= 100:
        f['f_cci_state'] = '中位'
    elif cci < 200:
        f['f_cci_state'] = '高位'
    else:
        f['f_cci_state'] = '极端高位'

    # ── F4: 量能状态 ──
    vol_regime = '正常'
    if (row.get('vol_堆', '') or '').strip():
        vol_regime = '地量堆'
    elif (row.get('vol_llv100', '') or '').strip():
        vol_regime = '百日地量'
    elif (row.get('vol_缩50', '') or '').strip():
        vol_regime = '缩量50'
    elif (row.get('vol_突放', '') or '').strip():
        vol_regime = '放量突破'
    elif (row.get('vol_梯度升', '') or '').strip():
        vol_regime = '梯度放量'
    f['f_vol_regime'] = vol_regime

    # ── F5/6: 排列熵级别+趋势 ──
    pe_level = (row.get('pe_level', '') or '').strip()
    f['f_pe_level'] = pe_level if pe_level else '无数据'
    try:
        pe_chg = float(row.get('pe_chg_5', 0) or 0)
        if pe_chg < -0.02:
            f['f_pe_trend'] = '降熵'
        elif pe_chg > 0.02:
            f['f_pe_trend'] = '升熵'
        else:
            f['f_pe_trend'] = '平稳'
    except (ValueError, TypeError):
        f['f_pe_trend'] = '无数据'

    # ── F7: 价格位置 (vs MA250 偏差) ──
    try:
        close = float(row['close'])
        ma250 = float(row.get('ma250', 0) or 0)
        if ma250 > 0:
            ratio = close / ma250
            if ratio > 1.30:
                f['f_price_pos'] = '高位(>30%溢价)'
            elif ratio < 0.85:
                f['f_price_pos'] = '低位(<85%折价)'
            else:
                f['f_price_pos'] = '中位'
        else:
            f['f_price_pos'] = '无数据'
    except (ValueError, TypeError):
        f['f_price_pos'] = '无数据'

    # ── F8: EXPMA白黄位置 ──
    try:
        close = float(row['close'])
        e12 = float(row.get('expma12', 0) or 0)
        e50 = float(row.get('expma50', 0) or 0)
        if e12 > 0 and close > e12:
            f['f_expma_pos'] = '白线上'
        elif e50 > 0 and close > e50:
            f['f_expma_pos'] = '白黄间'
        elif e50 > 0:
            f['f_expma_pos'] = '黄线下'
        else:
            f['f_expma_pos'] = '无数据'
    except (ValueError, TypeError):
        f['f_expma_pos'] = '无数据'

    # ── F9: 当日★买/金叉状态 ──
    has_buy = bool((row.get('buy_signal', '') or '').strip())
    has_golden = (row.get('expma_cross', '') or '').strip() == '金叉'
    if has_buy and has_golden:
        f['f_signal_combo'] = '★买+金叉'
    elif has_golden:
        f['f_signal_combo'] = '金叉'
    elif has_buy:
        f['f_signal_combo'] = '★买'
    else:
        f['f_signal_combo'] = '无锚点'

    return f


def _calc_band_low(rows, entry_idx):
    """计算开仓bar的波段最低点：最近金叉→开仓bar之间的最低low（本上涨波段的起点）"""
    best = float(rows[entry_idx]['low'])
    for i in range(entry_idx - 1, max(entry_idx - BAND_LOOKBACK, 0), -1):
        lo = float(rows[i]['low'])
        if lo < best:
            best = lo
        cross = (rows[i].get('expma_cross', '') or '').strip()
        if cross == '金叉':
            break
    return best


# ══════════════════════════════════════════════════════════════════
# 单标的回测
# ══════════════════════════════════════════════════════════════════

def backtest_stock(code, name, period, months=None, entry_mode='star+ma5+ma10+safe'):
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
    entry_date = None
    entry_factors = None
    pending_star = None      # 追赶模式: ★买触发bar索引
    pending_ma_done = False  # Z3专用: MA已理顺，等金叉阶段
    star_idx = None          # Z3专用: ★买bar索引(用于band_low计算,不被覆盖)

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
            # 追赶模式: ★买触发 → 追赶窗口等结构确认 → 入场
            #   Z1: ★买→等5分金叉(12根)→入场
            #   Z2: ★买→等MA理顺(12根)→入场
            #   Z3: ★买→12根内MA理顺→等金叉→入场 (两阶段)
            if entry_mode.startswith('Z1_') or entry_mode.startswith('Z2_') or entry_mode.startswith('Z3_'):
                is_z2 = entry_mode.startswith('Z2_')
                is_z3 = entry_mode.startswith('Z3_')

                # ★买触发 (有新★买时重置追赶起点)
                if _has_star_buy(r) and zone in ('strong', 'secondary'):
                    if code and not _get_min60_above(code, bar_date):
                        pending_star = None
                        pending_ma_done = False
                        star_idx = None
                    elif not _get_daily_pe_ok(code, bar_date):
                        pending_star = None
                        pending_ma_done = False
                        star_idx = None
                    else:
                        pending_star = i  # ★买触发，开始追赶
                        pending_ma_done = False
                        star_idx = i      # 记住★买bar(用于band_low)

                # 追赶中: 根据模式等不同条件
                if pending_star is not None:
                    waited = i - pending_star
                    ma_ok = _ma_above(r, 'ma5', 'ma10') and _ma_above(r, 'ma10', 'ma20')
                    jincha_ok = _has_golden(r)

                    if is_z2:
                        # Z2: MA理顺即入场 (12根超时)
                        if waited > 12:
                            pending_star = None
                        elif waited > 0 and ma_ok:
                            in_trade = True
                            entry_idx = i
                            entry_ts = r.get('timestamp', '').strip()
                            entry_price_val = _entry_price(r)
                            band_low = _calc_band_low(rows, pending_star)
                            entry_date = bar_date
                            entry_factors = _extract_entry_factors(r, entry_mode)
                            pending_star = None

                    elif is_z3:
                        # Z3: 先等MA理顺(12根)→再等金叉(12根)
                        if not pending_ma_done:
                            if waited > 12:
                                pending_star = None  # MA阶段超时
                                star_idx = None
                            elif waited > 0 and ma_ok:
                                pending_ma_done = True  # MA理顺，进入等金叉阶段
                                pending_star = i  # 记录MA理顺bar，用于金叉阶段超时计时
                        else:
                            # MA已理顺，等金叉 (从MA理顺起算12根超时)
                            waited_jincha = i - pending_star
                            if waited_jincha > 12:
                                pending_star = None
                                pending_ma_done = False
                                star_idx = None
                            elif jincha_ok:
                                in_trade = True
                                entry_idx = i
                                entry_ts = r.get('timestamp', '').strip()
                                entry_price_val = _entry_price(r)
                                band_low = _calc_band_low(rows, star_idx)  # 用★买bar算止损
                                entry_date = bar_date
                                entry_factors = _extract_entry_factors(r, entry_mode)
                                pending_star = None
                                pending_ma_done = False
                                star_idx = None

                    else:
                        # Z1: 等金叉 (12根超时) — 原逻辑
                        if waited > 12:
                            pending_star = None
                        elif waited > 0 and jincha_ok:
                            in_trade = True
                            entry_idx = i
                            entry_ts = r.get('timestamp', '').strip()
                            entry_price_val = _entry_price(r)
                            band_low = _calc_band_low(rows, pending_star)
                            entry_date = bar_date
                            entry_factors = _extract_entry_factors(r, entry_mode)
                            pending_star = None
            elif is_ctx_mode:
                ok = _check_entry_ctx(entry_mode, rows, i, r, code)
                if ok and zone in ('strong', 'secondary'):
                    in_trade = True
                    entry_idx = i
                    entry_ts = r.get('timestamp', '').strip()
                    entry_price_val = _entry_price(r)
                    band_low = _calc_band_low(rows, i)
                    entry_date = bar_date
                    entry_factors = _extract_entry_factors(r, entry_mode)
            else:
                ok = entry_fn(r) if entry_fn else (_has_star_buy(r) or _has_golden(r))
                if ok and zone in ('strong', 'secondary'):
                    in_trade = True
                    entry_idx = i
                    entry_ts = r.get('timestamp', '').strip()
                    entry_price_val = _entry_price(r)
                    band_low = _calc_band_low(rows, i)
                    entry_date = bar_date
                    entry_factors = _extract_entry_factors(r, entry_mode)
        else:
            # ── 持仓中，检查平仓 ──
            # 止损 或 减仓卖(★卖+close<MA5+safe / 死叉)
            exit_reason = None
            exit_price_val = None

            # 1. 止损：盘中跌破波段最低点
            if bar_low < band_low:
                exit_reason = '止损'
                exit_price_val = band_low

            # 2. 减仓卖出
            elif _is_sell_reduce(rows, i, code):
                exit_reason = '减仓卖'
                exit_price_val = _exit_price(r)

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

                # 共振级联检测 (15+30分金叉)
                resonance = _check_resonance(code, entry_date) if code else ''
                # 向未来看: 入场后15分金叉跟随 (默认8根)
                forward = _check_cascade_bt(rows, entry_idx, code) if code else ''
                # 多窗口对比: 12/8/6/4 + ★买版
                fwd_variants = {}
                if code:
                    for w in ('12', '8', '6', '4'):
                        fwd_variants[f'm15_w{w}'] = _check_cascade_bt(rows, entry_idx, code, m15_window=int(w))
                    fwd_variants['starbuy'] = _check_cascade_bt(rows, entry_idx, code, signal_type='starbuy')

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
                    'zone': daily_zones.get(entry_date, 'weak'),
                    'entry_mode': entry_mode,
                    'f_resonance': resonance,
                    'f_forward': forward,
                    'f_forward_all': json.dumps(fwd_variants, ensure_ascii=False),
                    **(entry_factors if entry_factors else {}),
                }
                trades.append(trade)

                in_trade = False
                entry_idx = None
                entry_price_val = None
                band_low = None
                pending_star = None
                pending_ma_done = False
                star_idx = None
                entry_factors = None

    return trades


# ══════════════════════════════════════════════════════════════════
# 买→卖配对回测
# ══════════════════════════════════════════════════════════════════

def backtest_pair(code, name, period, months=None, buy_mode='star+ma5+ma10+safe', sell_mode='sell_break_ma5_safe'):
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
    entry_factors = None

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
                entry_factors = _extract_entry_factors(r, buy_mode)
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

                forward = _check_cascade_bt(rows, entry_idx, code) if code else ''

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
                    'f_forward': forward,
                    **(entry_factors if entry_factors else {}),
                }
                trades.append(trade)

                in_trade = False
                entry_idx = None
                entry_price_val = None
                entry_factors = None

    return trades


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

        report[reason] = {
            'n': n, 'wr': round(wr, 1), 'avg_ret': round(avg_ret, 2),
            'max_ret': round(max_ret, 2), 'min_ret': round(min_ret, 2),
            'avg_hold': round(avg_hold, 1), 'avg_mfe': round(avg_mfe, 2),
            'avg_mae': round(avg_mae, 2),
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

    avg_all = sum(t['ret_pct'] for t in total) / len(total) if total else 0
    wr_all = len([t for t in total if t['ret_pct'] > 0]) / len(total) * 100 if total else 0
    print(f'\n  总交易: {len(total)} 笔  |  平均收益: {avg_all:+.2f}%  |  胜率: {wr_all:.1f}%')

    # ── 按出场方式 ──
    outcomes = defaultdict(list)
    for t in total:
        outcomes[t['exit_reason']].append(t)

    for reason in sorted(outcomes):
        items = outcomes[reason]
        if not items:
            continue
        n = len(items)
        avg = sum(t['ret_pct'] for t in items) / n
        wr = len([t for t in items if t['ret_pct'] > 0]) / n * 100
        print(f'  {reason}: {n}笔  胜率{wr:.0f}%  均收益{avg:+.2f}%')

    # ── 未来共振对比 (入场后15/30分是否有信号跟随) ──
    has_fwd = [t for t in total if t.get('f_forward')]
    no_fwd = [t for t in total if not t.get('f_forward')]
    if has_fwd:
        print(f'\n  +++ 未来共振: 入场后15/30分金叉/★买跟随 对比 +++')
        print(f'  {"状态":<20} {"笔数":>5} {"胜率":>7} {"均收益":>8} {"均MFE":>7} {"均MAE":>7}')
        print(f'  {"-"*60}')
        for label, grp in [('有未来共振', has_fwd), ('无未来共振', no_fwd)]:
            n = len(grp)
            wr = len([t for t in grp if t['ret_pct'] > 0]) / n * 100
            avg = sum(t['ret_pct'] for t in grp) / n
            avg_mfe = sum(t['mfe_pct'] for t in grp) / n
            avg_mae = sum(t['mae_pct'] for t in grp) / n
            print(f'  {label:<20} {n:>5} {wr:>6.0f}% {avg:>+7.2f}% {avg_mfe:>+6.2f}% {avg_mae:>+6.2f}%')
        # 子类型
        for ftype in ['both', 'min15_only', 'min30_only']:
            grp = [t for t in total if t.get('f_forward') == ftype]
            if grp:
                n = len(grp)
                wr = len([t for t in grp if t['ret_pct'] > 0]) / n * 100
                avg = sum(t['ret_pct'] for t in grp) / n
                print(f'    └{ftype:<12} {n:>5} {wr:>6.0f}% {avg:>+7.2f}%')

        # ── 多窗口/信号类型对比 ──
        print(f'\n  +++ 15分窗口/信号类型 对比 +++')
        print(f'  {"变体":<16} {"通过":>5} {"总笔":>5} {"通过率":>7} {"胜率(通过)":>10} {"均收(通过)":>9}')
        print(f'  {"-"*60}')
        variants = ['m15_w12', 'm15_w8', 'm15_w6', 'm15_w4', 'starbuy']
        vlabels = {'m15_w12': '15分12根', 'm15_w8': '15分8根', 'm15_w6': '15分6根', 'm15_w4': '15分4根', 'starbuy': '★买版'}
        for v in variants:
            passed = [t for t in total if json.loads(t.get('f_forward_all', '{}')).get(v)]
            n_pass = len(passed)
            if n_pass == 0:
                continue
            wr = len([t for t in passed if t['ret_pct'] > 0]) / n_pass * 100
            avg = sum(t['ret_pct'] for t in passed) / n_pass
            rate = n_pass / len(total) * 100
            print(f'  {vlabels.get(v, v):<16} {n_pass:>5} {len(total):>5} {rate:>6.1f}% {wr:>9.0f}% {avg:>+8.2f}%')
    # ── 收益分布 ──
    print(f'\n  收益分布:')
    bins = [(-100, -5), (-5, -2), (-2, 0), (0, 2), (2, 5), (5, 10), (10, 1000)]
    for lo, hi in bins:
        count = sum(1 for t in total if lo <= t['ret_pct'] < hi)
        bar = '█' * max(1, count)
        label = f'{lo}~{hi}%' if hi < 1000 else f'>{lo}%'
        print(f'    {label:>10}: {count:>3} {bar}')

    # ── 按周期 ──
    print(f'\n  {"周期":<8} {"笔数":>5} {"胜率":>7} {"均收益":>8} {"均持(根)":>9}')
    print(f'  {"-"*48}')
    for period in PERIODS:
        pt = [t for t in total if t['period'] == period]
        if not pt:
            continue
        n = len(pt)
        wr = len([t for t in pt if t['ret_pct'] > 0]) / n * 100
        avg = sum(t['ret_pct'] for t in pt) / n
        avg_h = sum(t['hold_bars'] for t in pt) / n
        print(f'  {period:<8} {n:>5} {wr:>6.0f}% {avg:>+7.2f}% {avg_h:>8.1f}')

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
    print(f'  {"日期":<12} {"标的":<12} {"周期":<6} {"入场":>8} {"出场":>8} {"收益":>8} {"方式":<6} {"持根":>5}')
    print(f'  {"-"*78}')
    for t in sorted(total, key=lambda x: x['entry_date'], reverse=True)[:10]:
        print(f'  {t["entry_date"]:<12} {t["code"]:<12} {t["period"]:<6} '
              f'{t["entry_price"]:>8.2f} {t["exit_price"]:>8.2f} {t["ret_pct"]:>+7.2f}% '
              f'{t["exit_reason"]:<6} {t["hold_bars"]:>5}')


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='信号配对回测')
    parser.add_argument('--code', type=str, help='单标的回测 (如 sh600176)')
    parser.add_argument('--period', type=str, choices=PERIODS, default='min5', help='单周期 (默认min5)')
    parser.add_argument('--months', type=int, default=6, help='回测时间范围(月, 默认6)')
    parser.add_argument('--entry', type=str, choices=ALL_ENTRY_MODES, default='star+ma5+ma10+safe',
                        help=f'入场模式: {", ".join(ALL_ENTRY_MODES)}')
    parser.add_argument('--compare', action='store_true', help='对比入场模式 (MA级 vs 金叉级)')
    parser.add_argument('--sell-compare', action='store_true', help='对比卖信号模式 (用 MA级入场 + 不同卖模式)')
    parser.add_argument('--pair', action='store_true', help='买→卖配对: MA级/金叉级 × 减仓卖/CCI做T 交叉统计')
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
        all_mode_results = {}

        for mode in ALL_ENTRY_MODES:
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
                stop_trades = [t for t in all_trades if t['exit_reason'] == '止损']
                stop_n = len(stop_trades)
                reduce_trades = [t for t in all_trades if t['exit_reason'] == '减仓卖']
                reduce_n = len(reduce_trades)
                reduce_wr = len([t for t in reduce_trades if t['ret_pct'] > 0]) / reduce_n * 100 if reduce_n else 0
                all_mode_results[mode] = {
                    'n': n, 'wr': wr, 'avg': avg,
                    'stop_n': stop_n, 'reduce_n': reduce_n, 'reduce_wr': reduce_wr,
                }

        print(f'\n{"="*100}')
        print(f'  入场模式对比 — {args.months}个月 — {args.period}')
        print(f'{"="*100}')
        print(f'  {"模式":<28} {"总笔数":>6} {"总胜率":>7} {"总均收":>8} {"减仓笔数":>7} {"减仓胜率":>7} {"止损笔数":>7}')
        print(f'  {"-"*85}')
        for mode in ALL_ENTRY_MODES:
            r = all_mode_results.get(mode)
            if not r:
                continue
            print(f'  {mode:<28} {r["n"]:>6} {r["wr"]:>6.1f}% {r["avg"]:>+7.2f}% '
                  f'{r["reduce_n"]:>7} {r["reduce_wr"]:>6.1f}% {r["stop_n"]:>7}')
        print()
        return

    # ── sell-compare 模式：用 MA级入场 + 不同卖模式 ──
    if args.sell_compare:
        sc_universe = load_tracking_universe() if args.universe == 'tracking' else universe
        print(f'\n{"="*100}')
        print(f'  卖信号模式对比 (MA级入场) — {args.months}个月 — {args.period}')
        print(f'{"="*100}')
        print(f'  {"卖模式":<28} {"笔数":>5} {"胜率":>7} {"均收益":>8} {"均持(根)":>9} {"均MFE":>7} {"均MAE":>7}')
        print(f'  {"-"*82}')
        for sell_mode in ALL_SELL_MODES:
            all_trades = []
            for stock in sc_universe:
                code, name = stock['code'], stock['name']
                for period in periods_to_run:
                    trades = backtest_pair(code, name, period, months=args.months,
                                           buy_mode='star+ma5+ma10+safe', sell_mode=sell_mode)
                    all_trades.extend(trades)
            if not all_trades:
                continue
            n = len(all_trades)
            wr = len([t for t in all_trades if t['ret_pct'] > 0]) / n * 100
            avg = sum(t['ret_pct'] for t in all_trades) / n
            avg_hold = sum(t['hold_bars'] for t in all_trades) / n
            avg_mfe = sum(t['mfe_pct'] for t in all_trades) / n
            avg_mae = sum(t['mae_pct'] for t in all_trades) / n
            print(f'  {sell_mode:<28} {n:>5} {wr:>6.1f}% {avg:>+7.2f}% {avg_hold:>8.1f} {avg_mfe:>+6.2f}% {avg_mae:>+6.2f}%')
        print()
        return

    # ── pair 模式：买→卖配对 交叉统计 ──
    if args.pair:
        combos = [
            ('star+ma5+ma10+safe',       'sell_break_ma5_safe',       'MA级→减仓卖'),
            ('star+ma5+ma10+safe',       'sell_break_ma5_safe_min5',  'MA级→减仓卖(min5)'),
            ('star+ma5+ma10+safe',       'sell_break_ma5_safe_min30', 'MA级→减仓卖(min30)'),
            ('star+ma5+ma10+safe',       'sell_cci_div_safe',         'MA级→CCI做T'),
            ('star+ma5+ma10+safe+jincha', 'sell_break_ma5_safe',      '金叉级→减仓卖'),
            ('star+ma5+ma10+safe+jincha', 'sell_cci_div_safe',        '金叉级→CCI做T'),
        ]
        print(f'\n{"="*100}')
        print(f'  买→卖配对回测 — {args.months}个月 — {args.period}')
        print(f'{"="*100}')
        print(f'  {"组合":<24} {"笔数":>5} {"胜率":>7} {"均收益":>8} {"均持(根)":>9} {"均MFE":>7} {"均MAE":>7}')
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
            print(f'  {label:<24} {n:>5} {wr:>6.1f}% {avg:>+7.2f}% {avg_hold:>8.1f} {avg_mfe:>+6.2f}% {avg_mae:>+6.2f}%')
        print()
        return

    # ── 普通模式 ──
    all_trades = []
    by_period = defaultdict(list)

    for stock in universe:
        code, name = stock['code'], stock['name']
        for period in periods_to_run:
            trades = backtest_stock(code, name, period, months=args.months, entry_mode=args.entry)
            all_trades.extend(trades)
            by_period[period].extend(trades)

    if not all_trades:
        print(f'\n[backtest] 未找到符合条件的交易（{args.months}个月内）')
        return

    # 分组统计
    by_stock = {}
    by_stock['全部'] = summarize(all_trades).get('全部', {})

    # 输出报告
    label = f'{args.months}个月 [{args.entry}]'
    if args.code:
        label += f' [{args.code}]'
    print_report(all_trades, by_stock, label)

    # 详细交易列表
    if args.detail and all_trades:
        print(f'\n{"="*90}')
        print(f'  全部交易明细 ({len(all_trades)}笔)')
        print(f'{"="*90}')
        for t in sorted(all_trades, key=lambda x: x['entry_date'], reverse=True):
            print(f'  {t["entry_date"]} {t["code"]:<12} {t["period"]} '
                  f'入场{t["entry_price"]:.2f} → {t["exit_reason"]}@{t["exit_price"]:.2f} '
                  f'{t["ret_pct"]:+.2f}% (MFE{t["mfe_pct"]:+.1f}% MAE{t["mae_pct"]:+.1f}%) '
                  f'持{t["hold_bars"]}根')

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

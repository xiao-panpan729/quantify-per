"""盘中实时监控 — 强势区间滤网 + 离散信号检测 + 多周期共振"""
import sys
import os
import time
import csv
import json
from datetime import datetime, timedelta
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.volume_leader.shared import (
    load_universe, append_trade,
    LOOKBACK_BARS, MIN_PRICE_FACTOR,
)
from tools.volume_leader.fetcher import fetch_today_5min, fetch_1min_pytdx
from tools.volume_leader import trade_db

import signal_engine as se
from signal_engine import (_calc_signals_from_arrays, _calc_pe_rolling, calc_volume_indicators,
                           TREND_PERIOD_MIN_SHORT, TREND_PERIOD_MIN, read_bars_lc1)
from cycle_engine.indicators import extract_anchors, signal_quality, analyze_trend_pe
from cycle_engine.constants import Direction

# ─── 常量 ───
SCAN_INTERVAL = 300          # 扫描间隔（秒），匹配5分钟K线节奏
DEDUP_WINDOW = 300           # 同一信号5分钟内不重复弹
PRICE_LIMIT_5_15 = 0.01      # 5→15分钟级联涨幅限制 1%
PRICE_LIMIT_15_30 = 0.02     # 15→30分钟级联涨幅限制 2%
MIN60_BREAK_BARS = 3         # 60分钟破黄线几根后判定出局
STATE_PATH = 'signals/tracking/monitor_state.json'
PE_GATE_LOG = 'signals/tracking/pe_gate_log.jsonl'  # PE门禁误杀追踪
DAILY_DIRECTION_CACHE = {}
MIN1_CACHE = {}  # code → {'rows': [...], 'last_ts': str}  1分钟信号内存缓存
DAILY_PE_CACHE = {}  # code → {date_str: bool}  日线PE非升熵缓存



# ─── 信号类型定义 ───
SIGNAL_GOLDEN = '金叉'
SIGNAL_DEATH = '死叉'
SIGNAL_STAR_BUY = '★买'
SIGNAL_STAR_SELL = '★卖'
SIGNAL_CCI_TOP_DIV = 'CCI顶背驰'


# ========================================================================
#  数据加载
# ========================================================================

def _load_csv(code, period='min5'):
    """读取历史 CSV，返回最后 LOOKBACK_BARS 行的 dict list"""
    csv_path = f'signals/tracking/{code}/{period}_signals.csv'
    if not os.path.exists(csv_path):
        return None
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if len(rows) > LOOKBACK_BARS:
        rows = rows[-LOOKBACK_BARS:]
    return rows


def _load_daily_direction(code):
    """读日线 CSV 判断方向"""
    if code in DAILY_DIRECTION_CACHE:
        return DAILY_DIRECTION_CACHE[code]
    csv_path = f'signals/tracking/{code}/daily_signals.csv'
    if not os.path.exists(csv_path):
        return Direction.BULLISH
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))[-60:]
    import numpy as np
    closes = np.array([float(r.get('close', 0)) for r in rows if r.get('close')])
    if len(closes) < 20:
        return Direction.BULLISH
    from signal_engine import calc_expma
    ema12 = calc_expma(closes, 12)
    ema50 = calc_expma(closes, 50)
    if len(ema12) > 0 and len(ema50) > 0:
        last_close = closes[-1]
        if last_close > ema12[-1] > ema50[-1]:
            direction = Direction.BULLISH
        elif last_close < ema12[-1] < ema50[-1]:
            direction = Direction.BEARISH
        else:
            direction = Direction.NEUTRAL
    else:
        direction = Direction.BULLISH
    DAILY_DIRECTION_CACHE[code] = direction
    return direction


def _daily_pe_ok(code):
    """日线PE非升熵 (最新一根日线的pe_chg_5 ≥ -0.02)"""
    if code in DAILY_PE_CACHE:
        return DAILY_PE_CACHE[code]
    rows = _load_csv(code, 'daily')
    if not rows or len(rows) < 2:
        DAILY_PE_CACHE[code] = True
        return True
    last = rows[-1]
    try:
        pe_chg = float(last.get('pe_chg_5', 0) or 0)
        ok = pe_chg >= -0.02
    except (ValueError, TypeError):
        ok = True
    DAILY_PE_CACHE[code] = ok
    return ok


# ─── 共振检测缓存（同日/异日共振） ───
_RESONANCE_CACHE = {}  # code → {date: set('min15'|'min30')}


def _check_resonance(code, bar_date):
    """检测15+30金叉共振: 返回 'same_day'(同日) / 'cross_day'(异日) / ''

    ±1天窗口匹配, 对齐 backtest.py。《实验#4》15+30双共振77.1%胜率。
    同日(offset=0) 100%胜率, 异日(offset≠0但窗口内) 并入±1天。
    """
    if code not in _RESONANCE_CACHE:
        _RESONANCE_CACHE[code] = {}
        for period in ('min15', 'min30'):
            rows = _load_csv(code, period)
            if rows:
                for r in rows:
                    d = r.get('date', '').strip()
                    cross = (r.get('expma_cross', '') or '').strip()
                    if cross == '金叉' and d:
                        _RESONANCE_CACHE[code].setdefault(d, set()).add(period)

    try:
        dt = datetime.strptime(bar_date, '%Y%m%d')
    except ValueError:
        return ''

    # 先检查严格同一天
    same_day = _RESONANCE_CACHE[code].get(bar_date, set())
    if 'min15' in same_day and 'min30' in same_day:
        return 'same_day'

    # ±1天窗口
    all_perms = set()
    for offset in (-1, 1):
        d = (dt + timedelta(days=offset)).strftime('%Y%m%d')
        all_perms.update(_RESONANCE_CACHE[code].get(d, set()))

    if 'min15' in all_perms and 'min30' in all_perms:
        return 'cross_day'
    return ''


def _log_pe_gate_kill(code, name, bar, bar_ts, would_be_level, fail_reason, pe_val):
    """记录被PE门禁过滤的信号，供事后验证是否误杀"""
    from tools.volume_leader.shared import MIN_PRICE_FACTOR
    close_raw = float(bar.get('close', 0) or 0)
    price = close_raw / MIN_PRICE_FACTOR if close_raw > 100 else close_raw
    record = {
        'time': bar_ts,
        'code': code,
        'name': name,
        'price': round(price, 3),
        'would_be_level': would_be_level,
        'fail_reason': fail_reason,
        'pe_val': round(pe_val, 4),
        'ma5': round(float(bar.get('ma5', 0) or 0) / MIN_PRICE_FACTOR, 3),
        'ma10': round(float(bar.get('ma10', 0) or 0) / MIN_PRICE_FACTOR, 3),
        'ma20': round(float(bar.get('ma20', 0) or 0) / MIN_PRICE_FACTOR, 3),
        'verified': None,
        'verified_at': None,
        'verified_result': None,
    }
    with open(PE_GATE_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def _load_1min_signals(code):
    """加载1分钟信号: pytdx(今天实时) + 历史.lc1(暖机) → 内存算信号 → 缓存

    缓存策略: 先轻量拉pytdx查最新时间戳, 未变则直接返回缓存。
    时间戳变了才读.lc1 + 合并 + 重算信号。
    不写CSV, 不污染数据。
    """
    from tools.volume_leader.shared import code_to_market
    market, code6, _ = code_to_market(code)
    mkt = code[:2]
    lc1_path = f'C:/zd_cjzq/vipdoc/{mkt}/minline/{code}.lc1'

    # 1. 轻量拉pytdx今天数据, 用于缓存判定
    today_df = fetch_1min_pytdx(market, code6)

    if today_df is not None and len(today_df) > 0:
        latest_ts = str(int(today_df.iloc[-1]['timestamp']))
    else:
        # 非交易时间，用.lc1文件修改时间做缓存key
        try:
            latest_ts = str(int(os.path.getmtime(lc1_path)))
        except OSError:
            latest_ts = '0'

    # 2. 缓存命中 — 直接返回
    cache = MIN1_CACHE.get(code)
    if cache and cache.get('last_ts') == latest_ts and cache.get('rows'):
        return cache['rows']

    # 3. 缓存未命中: 读历史.lc1 + 合并pytdx + 算信号
    try:
        hist_bars = read_bars_lc1(lc1_path)
    except Exception:
        return None
    if not hist_bars or len(hist_bars) < 250:
        return None

    HIST_N = min(550, len(hist_bars))
    hist_bars = hist_bars[-HIST_N:]

    # 合并今天实时数据
    all_bars = list(hist_bars)
    if today_df is not None and len(today_df) > 0:
        existing_ts = {b[0] for b in all_bars}
        for _, row in today_df.iterrows():
            ts = int(row['timestamp'])
            if ts in existing_ts:
                continue
            all_bars.append((
                ts,
                int(row['open'] * MIN_PRICE_FACTOR),
                int(row['high'] * MIN_PRICE_FACTOR),
                int(row['low'] * MIN_PRICE_FACTOR),
                int(row['close'] * MIN_PRICE_FACTOR),
                int(row['amount']),
                int(row['volume']),
                0,
            ))

    # 4. 算信号
    opens = [float(b[1]) for b in all_bars]
    highs = [float(b[2]) for b in all_bars]
    lows = [float(b[3]) for b in all_bars]
    closes = [float(b[4]) for b in all_bars]
    vols = [b[6] for b in all_bars]
    amts = [b[5] for b in all_bars]
    timestamps = [b[0] for b in all_bars]

    rows = _calc_signals_from_arrays(
        opens, highs, lows, closes, vols, amts, timestamps, TREND_PERIOD_MIN_SHORT)
    if rows:
        rows = _calc_pe_rolling(rows)

    MIN1_CACHE[code] = {'rows': rows, 'last_ts': latest_ts}
    return rows


def _rows_to_arrays(rows):
    """dict list → numpy arrays"""
    import numpy as np
    opens = np.array([float(r.get('open', 0)) for r in rows])
    highs = np.array([float(r.get('high', 0)) for r in rows])
    lows = np.array([float(r.get('low', 0)) for r in rows])
    closes = np.array([float(r.get('close', 0)) for r in rows])
    vols = np.array([float(r.get('volume', 0)) for r in rows])
    amts = np.array([float(r.get('amount', 0)) for r in rows])
    tss = [int(r.get('timestamp', 0)) for r in rows]
    return opens, highs, lows, closes, vols, amts, tss


def _today_df_to_rows(df):
    """pytdx 返回的今日 DataFrame → dict list"""
    rows = []
    for _, r in df.iterrows():
        ts_val = r['timestamp']
        if isinstance(ts_val, str) and '.' in ts_val:
            ts_val = ts_val.split('.')[0]
        elif not isinstance(ts_val, (int, str)):
            ts_val = str(int(float(ts_val)))
        date_val = r['date']
        if isinstance(date_val, str) and '.' in date_val:
            date_val = date_val.split('.')[0]
        elif not isinstance(date_val, (int, str)):
            date_val = str(int(float(date_val)))
        rows.append({
            'timestamp': str(int(ts_val)),
            'date': str(int(date_val)),
            'open': str(r['open']),
            'high': str(r['high']),
            'low': str(r['low']),
            'close': str(r['close']),
            'volume': str(r['volume']),
            'amount': str(r['amount']),
        })
    return rows


def _resample_to_period(df_5min, period):
    """将5分钟DataFrame合成为15分钟或30分钟K线"""
    import pandas as pd

    if period == 'min15':
        n = 3
    elif period == 'min30':
        n = 6
    else:
        return df_5min

    if len(df_5min) < n:
        return None

    df = df_5min.reset_index(drop=True)
    result_rows = []
    for i in range(0, len(df) - n + 1, n):
        chunk = df.iloc[i:i + n]
        ts_val = chunk.iloc[0]['timestamp']
        ts_str = str(int(float(ts_val))) if not isinstance(ts_val, (int, str)) else str(int(ts_val))
        date_val = chunk.iloc[0]['date']
        date_str = str(int(float(date_val))) if not isinstance(date_val, (int, str)) else str(int(date_val))
        result_rows.append({
            'timestamp': ts_str,
            'date': date_str,
            'open': float(chunk.iloc[0]['open']),
            'high': float(chunk['high'].max()),
            'low': float(chunk['low'].min()),
            'close': float(chunk.iloc[-1]['close']),
            'volume': float(chunk['volume'].sum()),
            'amount': float(chunk['amount'].sum()),
        })
    return pd.DataFrame(result_rows)


def _merge_and_compute(hist_rows, today_df, trend_period=None):
    """拼接历史+今日 → 全量重算信号 → 返回合并后的 dict list"""
    import numpy as np

    if trend_period is None:
        trend_period = TREND_PERIOD_MIN_SHORT

    h_opens, h_highs, h_lows, h_closes, h_vols, h_amts, h_tss = _rows_to_arrays(hist_rows)

    t_rows = _today_df_to_rows(today_df)
    t_opens, t_highs, t_lows, t_closes, t_vols, t_amts, t_tss = _rows_to_arrays(t_rows)
    t_opens = t_opens * MIN_PRICE_FACTOR
    t_highs = t_highs * MIN_PRICE_FACTOR
    t_lows = t_lows * MIN_PRICE_FACTOR
    t_closes = t_closes * MIN_PRICE_FACTOR

    all_opens = np.concatenate([h_opens, t_opens])
    all_highs = np.concatenate([h_highs, t_highs])
    all_lows = np.concatenate([h_lows, t_lows])
    all_closes = np.concatenate([h_closes, t_closes])
    all_vols = np.concatenate([h_vols, t_vols])
    all_amts = np.concatenate([h_amts, t_amts])

    if len(t_opens) == 0:
        return hist_rows
    if len(all_closes) < 30:
        return hist_rows + t_rows

    all_rows = _calc_signals_from_arrays(
        all_opens, all_highs, all_lows, all_closes,
        all_vols, all_amts,
        [str(int(ts)) for ts in np.concatenate([np.array(h_tss), np.array(t_tss)])],
        trend_period
    )
    if all_rows:
        all_rows = _calc_pe_rolling(all_rows)
        all_rows = calc_volume_indicators(all_rows)
    else:
        return hist_rows + t_rows

    STR_FIELDS = ['buy_signal', 'sell_signal', 'expma_cross', 'cci', 'cci_extreme',
                  'cci_retreat', 'cci_divergence', 'red_line_cross']
    for r in all_rows:
        for k in STR_FIELDS:
            v = r.get(k)
            if v is not None and not isinstance(v, str):
                r[k] = '' if (isinstance(v, float) and (v != v)) else str(v)

    return all_rows


# ========================================================================
#  强势区间检查
# ========================================================================

def _check_strength_zone(code):
    """
    检查标的当前是否在强势区间。

    基于 60分钟 EXPMA 黄线 + 日线位置：
    - 'strong':    日线 > EXPMA白线(12)  → 真强势
    - 'secondary': 日线 > EXPMA黄线(50)  → 二级强势
    - 'out':       60分钟跌破黄线 + 3根K线回不来 → 不看
    - 'restored':  之前 out，现在 30/60分钟重新站上黄线 → 恢复关注

    Returns dict: {zone, detail, daily_close, daily_ema12, daily_ema50}
    """
    import numpy as np

    zone = 'secondary'  # 默认
    detail = ''

    # ── 1. 检查 60分钟 ──
    min60_rows = _load_csv(code, 'min60')
    if min60_rows and len(min60_rows) >= MIN60_BREAK_BARS:
        # 取最近 N 根 60分钟K线
        recent_60 = min60_rows[-MIN60_BREAK_BARS:]
        below_count = 0
        for r in recent_60:
            c = float(r.get('close', 0))
            e50 = float(r.get('expma50', 0))
            if e50 > 0 and c < e50:
                below_count += 1

        if below_count >= MIN60_BREAK_BARS:
            # 60分钟破位，检查恢复迹象
            # 优先检查 60分钟本身是否已回黄线上方
            last_60 = min60_rows[-1]
            c60 = float(last_60.get('close', 0))
            e50_60 = float(last_60.get('expma50', 0))
            if e50_60 > 0 and c60 > e50_60:
                zone = 'restored'
                detail = '60分已回黄线上方，恢复关注'
                return {'zone': zone, 'detail': detail}

            # 其次检查 30分钟是否已回黄线上方
            min30_rows = _load_csv(code, 'min30')
            if min30_rows and len(min30_rows) >= 3:
                last_30 = min30_rows[-1]
                c30 = float(last_30.get('close', 0))
                e50_30 = float(last_30.get('expma50', 0))
                if e50_30 > 0 and c30 > e50_30:
                    zone = 'restored'
                    detail = '60分破位但30分已回黄线上方'
                    return {'zone': zone, 'detail': detail}

            zone = 'out'
            detail = f'60分钟连续{below_count}根跌破黄线，不看'
            return {'zone': zone, 'detail': detail}

    # ── 2. 检查日线 ──
    daily_rows = _load_csv(code, 'daily')
    if daily_rows and len(daily_rows) >= 2:
        last_d = daily_rows[-1]
        dc = float(last_d.get('close', 0))
        de12 = float(last_d.get('expma12', 0))
        de50 = float(last_d.get('expma50', 0))
        if de12 > 0 and dc > de12:
            zone = 'strong'
            detail = '日线真强势(>白线)'
        elif de50 > 0 and dc > de50:
            zone = 'secondary'
            detail = '日线二级强势(白黄之间)'
        else:
            zone = 'secondary'
            detail = '日线偏弱但60分未破位'

    return {'zone': zone, 'detail': detail}


# ========================================================================
#  离散信号检测 — 核心
# ========================================================================

def _find_window_start(all_rows, idx):
    """
    从 idx 往前找当前窗口的起点。
    窗口起点定义：最近一次 MA5金叉 或 close上穿EXPMA黄线。
    用于判断 CCI顶背驰 是否是该窗口内的唯一信号。
    """
    for i in range(idx - 1, max(idx - 80, -1), -1):
        if i < 1:
            break
        curr = all_rows[i]
        prev = all_rows[i - 1]
        # MA5 金叉 (ma5 从下方上穿 ma10)
        ma5_c = float(curr.get('ma5', 0) or 0)
        ma10_c = float(curr.get('ma10', 0) or 0)
        ma5_p = float(prev.get('ma5', 0) or 0)
        ma10_p = float(prev.get('ma10', 0) or 0)
        if ma5_p > 0 and ma10_p > 0 and ma5_c > 0 and ma10_c > 0:
            if ma5_p <= ma10_p and ma5_c > ma10_c:
                return i
        # close 上穿 EXPMA 黄线
        close_c = float(curr.get('close', 0) or 0)
        ema50_c = float(curr.get('expma50', 0) or 0)
        close_p = float(prev.get('close', 0) or 0)
        ema50_p = float(prev.get('expma50', 0) or 0)
        if ema50_p > 0 and ema50_c > 0 and close_p > 0 and close_c > 0:
            if close_p <= ema50_p and close_c > ema50_c:
                return i
    return max(idx - 60, 0)


def _calc_entry_band_low(all_rows, entry_idx, lookback=80):
    """入场时计算波段最低点：最近金叉→开仓bar之间的最低low（本上涨波段起点）"""
    best = float(all_rows[entry_idx]['low'])
    for i in range(entry_idx - 1, max(entry_idx - lookback, -1), -1):
        lo = float(all_rows[i]['low'])
        if lo < best:
            best = lo
        cross = (all_rows[i].get('expma_cross', '') or '').strip()
        if cross == '金叉':
            break
    return best


def _count_cci_top_divergence(all_rows, start_idx, end_idx):
    """统计 (start_idx, end_idx] 范围内 CCI顶背驰 出现次数"""
    count = 0
    for i in range(start_idx + 1, end_idx + 1):
        if i >= len(all_rows):
            break
        cci_div = (all_rows[i].get('cci_divergence', '') or '').strip()
        if cci_div == '顶背驰':
            count += 1
    return count


def _detect_signals_on_latest(all_rows, only_new=True, lookback=1):
    """
    检测K线上的离散信号。

    only_new=True, lookback=1: 实时模式，只检查最新1根是否新出现信号
    only_new=False, lookback>1: 快照模式，往回找最近N根内有信号的bar

    Returns dict:
        {buy_signals: [{type, bar_ts, price, idx}],
         sell_signals: [{type, bar_ts, price, idx}]}
    """
    if not all_rows or len(all_rows) < 2:
        return {'buy_signals': [], 'sell_signals': []}

    buy_signals = []
    sell_signals = []

    # 检查范围：从最后1根往前 lookback 根
    check_range = min(lookback, len(all_rows) - 1)

    for offset in range(check_range):
        curr = all_rows[-(1 + offset)]
        prev = all_rows[-(2 + offset)] if len(all_rows) >= (2 + offset) else {}

        curr_close = float(curr.get('close', 0))
        price = curr_close / MIN_PRICE_FACTOR if curr_close > 100 else curr_close

        # ── 金叉 ──
        has_golden = curr.get('expma_cross', '') == '金叉'
        is_new_golden = has_golden and prev.get('expma_cross', '') != '金叉'
        if has_golden and (not only_new or is_new_golden):
            buy_signals.append({'type': SIGNAL_GOLDEN, 'price': price,
                                'bar_ts': curr.get('timestamp', ''), 'idx': len(all_rows) - 1 - offset})
        # ── 死叉 ──
        has_death = curr.get('expma_cross', '') == '死叉'
        is_new_death = has_death and prev.get('expma_cross', '') != '死叉'
        if has_death and (not only_new or is_new_death):
            sell_signals.append({'type': SIGNAL_DEATH, 'price': price,
                                 'bar_ts': curr.get('timestamp', ''), 'idx': len(all_rows) - 1 - offset})

        # ── ★买 ──
        has_star_buy = bool(curr.get('buy_signal', '').strip())
        is_new_star_buy = has_star_buy and not bool(prev.get('buy_signal', '').strip())
        if has_star_buy and (not only_new or is_new_star_buy):
            buy_signals.append({'type': SIGNAL_STAR_BUY, 'price': price,
                                'bar_ts': curr.get('timestamp', ''), 'idx': len(all_rows) - 1 - offset})
        # ── ★卖 ──
        has_star_sell = bool(curr.get('sell_signal', '').strip())
        is_new_star_sell = has_star_sell and not bool(prev.get('sell_signal', '').strip())
        if has_star_sell and (not only_new or is_new_star_sell):
            sell_signals.append({'type': SIGNAL_STAR_SELL, 'price': price,
                                 'bar_ts': curr.get('timestamp', ''), 'idx': len(all_rows) - 1 - offset})
        # ── CCI顶背驰 ──
        has_cci_top = (curr.get('cci_divergence', '') or '').strip() == '顶背驰'
        is_new_cci_top = has_cci_top and (prev.get('cci_divergence', '') or '').strip() != '顶背驰'
        if has_cci_top and (not only_new or is_new_cci_top):
            sell_signals.append({'type': SIGNAL_CCI_TOP_DIV, 'price': price,
                                 'bar_ts': curr.get('timestamp', ''), 'idx': len(all_rows) - 1 - offset})

        # 找到了就停（取最近的信号）
        if buy_signals or sell_signals:
            break

    return {'buy_signals': buy_signals, 'sell_signals': sell_signals}


def _last_signal_bars(all_rows, signal_type, n=10):
    """
    回溯最近 n 根K线，找到所有出现过 signal_type 的 bar。

    Returns list of {bar_ts, price, idx} 按时间倒序。
    """
    result = []
    for i in range(len(all_rows) - 1, max(len(all_rows) - n - 1, -1), -1):
        r = all_rows[i]
        match = False
        if signal_type == SIGNAL_GOLDEN and r.get('expma_cross', '') == '金叉':
            match = True
        elif signal_type == SIGNAL_DEATH and r.get('expma_cross', '') == '死叉':
            match = True
        elif signal_type == SIGNAL_STAR_BUY and r.get('buy_signal', '').strip():
            match = True
        elif signal_type == SIGNAL_STAR_SELL and r.get('sell_signal', '').strip():
            match = True
        elif signal_type == SIGNAL_CCI_TOP_DIV and (r.get('cci_divergence', '') or '').strip() == '顶背驰':
            match = True

        if match:
            c = float(r.get('close', 0))
            price = c / MIN_PRICE_FACTOR if c > 100 else c
            result.append({'bar_ts': r.get('timestamp', ''), 'price': price, 'idx': i})

    return result


# ========================================================================
#  单周期扫描
# ========================================================================

def _scan_one_period(code, name, hist_rows, today_df, direction, period, trend_period=None, force=False):
    """
    扫描单个周期。

    force=False (实时):
      只查最新1根是否「新出现」信号。合并历史+新bar重算。

    force=True (快照):
      直接从CSV查最新N根有无信号。min5=1根, min15=2根, min30=2根。
    """
    if trend_period is None:
        trend_period = TREND_PERIOD_MIN_SHORT

    if force:
        # ── 快照模式：直接用CSV数据，不合并不重算 ──
        all_rows = hist_rows
        if not all_rows or len(all_rows) < 2:
            return None
        # 不同周期不同窗口: min5=1根(5min), min15=2根(30min), min30=2根(60min)
        lookback_map = {'min5': 1, 'min15': 2, 'min30': 2}
        lb = lookback_map.get(period, 1)
        signals = _detect_signals_on_latest(all_rows, only_new=False, lookback=lb)
    else:
        # ── 实时模式：合并新bar + 重算信号 ──
        all_rows = _merge_and_compute(hist_rows, today_df, trend_period)
        if not all_rows or len(all_rows) < 2:
            return None
        signals = _detect_signals_on_latest(all_rows, only_new=True, lookback=1)

    # 信号质量评分（用于跨标的比较）
    try:
        anchors = extract_anchors(all_rows)
        trend_pe = analyze_trend_pe(all_rows, lookback=60)
        trend = {'direction': direction}
        sq = signal_quality(anchors, all_rows, None, trend, lookback_klines=500, trend_pe=trend_pe)
    except Exception:
        sq = None

    buy_level = sq.get('buy_level', 0) if sq else 0
    sell_level = sq.get('sell_level', 0) if sq else 0
    details = sq.get('details', []) if sq else []

    # 最新价格
    last_close = float(all_rows[-1].get('close', 0))
    price = last_close / MIN_PRICE_FACTOR if last_close > 100 else last_close

    return {
        'code': code,
        'name': name,
        'price': round(price, 4),
        'buy_signals': signals['buy_signals'],
        'sell_signals': signals['sell_signals'],
        'buy_level': round(buy_level, 1),
        'sell_level': round(sell_level, 1),
        'details': details,
        'direction': direction,
        'all_rows': all_rows,
        'hist_len': len(hist_rows),
    }


# ========================================================================
#  多周期共振 + 价格约束
# ========================================================================

def _check_cascade(signals_by_period, side='buy'):
    """
    检测多周期金叉/★买 的级联传导，带价格涨幅约束。

    规则：
    - 15分钟先出金叉/★买 → 30分钟内也出 → 涨幅 < 2% → 15+30共振
    - min5已在金叉级信号中保证，不再重复检查（冗余）

    Returns dict:
        {cascade_type: '15+30'|None,
         periods_confirmed: ['min15','min30'],
         price_ok: bool,
         detail: str}
    """
    if side == 'buy':
        check_types = [SIGNAL_GOLDEN, SIGNAL_STAR_BUY]
    else:
        check_types = [SIGNAL_DEATH, SIGNAL_STAR_SELL]

    # 收集各周期最近的相关信号
    has_signal = {}
    signal_info = {}
    LOOKBACK_MAP = {'min15': 8, 'min30': 6}
    for period in ['min15', 'min30']:
        ps = signals_by_period.get(period)
        if not ps:
            has_signal[period] = False
            continue

        sig_list = ps.get('buy_signals' if side == 'buy' else 'sell_signals', [])
        all_rows = ps.get('all_rows', [])
        recent_signals = []
        for st in check_types:
            recent_signals.extend(_last_signal_bars(all_rows, st, n=LOOKBACK_MAP.get(period, 5)))

        if sig_list:
            has_signal[period] = True
            signal_info[period] = {
                'latest': sig_list[0],
                'recent': recent_signals[:3],
                'price': ps.get('price', 0),
            }
        elif recent_signals:
            has_signal[period] = True
            signal_info[period] = {
                'latest': recent_signals[0],
                'recent': recent_signals[:3],
                'price': ps.get('price', 0),
            }
        else:
            has_signal[period] = False

    # ── 级联判断 ──
    cascade_type = None
    periods_confirmed = []
    price_ok = True
    detail_parts = []

    m15_ok = has_signal.get('min15', False)
    m30_ok = has_signal.get('min30', False)

    if m15_ok and m30_ok:
        p15 = signal_info['min15']['price']
        p30 = signal_info['min30']['price']
        chg = abs(p30 - p15) / p15 if p15 > 0 else 0
        if chg <= PRICE_LIMIT_15_30:
            cascade_type = '15+30'
            periods_confirmed = ['min15', 'min30']
            detail_parts.append(f'15+30共振 涨幅{chg*100:.1f}%')
        else:
            price_ok = False
            detail_parts.append(f'15→30涨幅{chg*100:.1f}% > 2% 不追')

    return {
        'cascade_type': cascade_type,
        'periods_confirmed': periods_confirmed,
        'price_ok': price_ok,
        'detail': ' | '.join(detail_parts) if detail_parts else '',
        'has_15min': m15_ok,
        'has_30min': m30_ok,
        'signal_prices': {p: signal_info[p]['price'] for p in signal_info},
    }


def _detect_signals_catchup(all_rows, hist_len):
    """补扫模式：扫描所有新bar（索引 >= hist_len）上是否有新出现的信号。

    与 _detect_signals_on_latest 的区别：
    - 不只看最新一根，而是遍历所有新bar
    - 只检查"新出现"的信号（prev没有该信号）
    - 返回所有找到的信号（不提前break）
    """
    if not all_rows or len(all_rows) < 2 or hist_len >= len(all_rows):
        return {'buy_signals': [], 'sell_signals': []}

    buy_signals = []
    sell_signals = []

    for i in range(hist_len, len(all_rows)):
        curr = all_rows[i]
        prev = all_rows[i - 1]

        curr_close = float(curr.get('close', 0))
        price = curr_close / MIN_PRICE_FACTOR if curr_close > 100 else curr_close

        # 金叉（新出现）
        if curr.get('expma_cross', '') == '金叉' and prev.get('expma_cross', '') != '金叉':
            buy_signals.append({'type': SIGNAL_GOLDEN, 'price': price,
                                'bar_ts': curr.get('timestamp', ''), 'idx': i})
        # 死叉（新出现）
        if curr.get('expma_cross', '') == '死叉' and prev.get('expma_cross', '') != '死叉':
            sell_signals.append({'type': SIGNAL_DEATH, 'price': price,
                                 'bar_ts': curr.get('timestamp', ''), 'idx': i})
        # ★买（新出现）
        if bool(curr.get('buy_signal', '').strip()) and not bool(prev.get('buy_signal', '').strip()):
            buy_signals.append({'type': SIGNAL_STAR_BUY, 'price': price,
                                'bar_ts': curr.get('timestamp', ''), 'idx': i})
        # ★卖（新出现）
        if bool(curr.get('sell_signal', '').strip()) and not bool(prev.get('sell_signal', '').strip()):
            sell_signals.append({'type': SIGNAL_STAR_SELL, 'price': price,
                                 'bar_ts': curr.get('timestamp', ''), 'idx': i})
        # CCI顶背驰（新出现）
        curr_div = (curr.get('cci_divergence', '') or '').strip()
        prev_div = (prev.get('cci_divergence', '') or '').strip()
        if curr_div == '顶背驰' and prev_div != '顶背驰':
            sell_signals.append({'type': SIGNAL_CCI_TOP_DIV, 'price': price,
                                 'bar_ts': curr.get('timestamp', ''), 'idx': i})

    return {'buy_signals': buy_signals, 'sell_signals': sell_signals}


# ========================================================================
#  Monitor 类
# ========================================================================

class Monitor:
    def __init__(self, interval=SCAN_INTERVAL, use_toast=True, entry_filter='ma', sell_filter='all'):
        self.interval = interval
        self.use_toast = use_toast
        self.entry_filter = entry_filter  # 'any'(测试用) | 'ma'(试错) | 'jincha'(买) | 'resonance'(买完) | 'all'(全显)
        self.sell_filter = sell_filter    # 'none' | 'sell_t'(做T仅日志) | 'sell_reduce'(减仓通知) | 'all'(全部)
        self._last_alert = {}       # key: (code, alert_type) → bar_ts
        self._last_zone = {}        # code → zone string
        self._signal_memory = {}    # code → {period: {signal_bar_ts, price}} 用于级联追踪
        self._active_stocks = {}    # code → {name, filter_level, added_ts, no_signal_count} 高频监控活跃池
        self._load_state()

        if use_toast:
            try:
                from win10toast import ToastNotifier
                self._toaster = ToastNotifier()
                self._toast_ok = True
            except ImportError:
                print('[monitor] win10toast 未安装，使用控制台输出')
                self._toast_ok = False
        else:
            self._toast_ok = False

    def _load_state(self):
        """加载上次监控状态"""
        if os.path.exists(STATE_PATH):
            try:
                state = json.load(open(STATE_PATH, 'r', encoding='utf-8'))
                self._last_alert = {tuple(k.split('|')): v for k, v in state.get('alerts', {}).items()}
                self._last_zone = state.get('zones', {})
                self._signal_memory = state.get('signal_memory', {})
                self._last_scan_ts = state.get('last_scan_ts', {})
                self._active_stocks = state.get('active_stocks', {})
            except Exception:
                self._last_scan_ts = {}
                self._active_stocks = {}
        else:
            self._last_scan_ts = {}
            self._active_stocks = {}

    def _save_state(self):
        """持久化当前状态"""
        state = {
            'alerts': {'|'.join(k): v for k, v in self._last_alert.items()},
            'zones': self._last_zone,
            'signal_memory': self._signal_memory,
            'last_scan_ts': self._last_scan_ts,
            'active_stocks': self._active_stocks,
        }
        json.dump(state, open(STATE_PATH, 'w', encoding='utf-8'))

    def _is_new_alert(self, code, alert_type, bar_ts):
        """检查是否应该弹窗：同一 alert_type 的 bar_ts 变化了才弹"""
        key = (code, alert_type)
        last_ts = self._last_alert.get(key)
        if last_ts == bar_ts:
            return False
        self._last_alert[key] = bar_ts
        return True

    def _notify(self, signal):
        """弹窗通知"""
        title = signal.get('title', '★信号')
        body = (
            f"{signal['code']} {signal['name']}\n"
            f"价格: {signal.get('price', '?')}\n"
            f"{signal.get('detail', '')}\n"
            f"信号: {signal.get('signal_types', '')}\n"
            f"质量: 买{signal.get('buy_level', 0)}/卖{signal.get('sell_level', 0)}"
        )
        if self._toast_ok:
            self._toaster.show_toast(title, body, duration=10, threaded=True)
        else:
            print(f'\n═══ {title} ══════════')
            print(body)
            print('═══════════════════════\n')

    def _record(self, signal):
        """写入 JSONL + SQLite 交易台账"""
        entry_conds = signal.get('entry_conditions', {})
        record = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'code': signal['code'],
            'name': signal['name'],
            'price': signal.get('price', ''),
            'direction': signal.get('direction', ''),
            'signal_types': signal.get('signal_types', ''),
            'cascade_type': signal.get('cascade_type', ''),
            'periods_confirmed': signal.get('periods_confirmed', []),
            'buy_level': signal.get('buy_level', 0),
            'sell_level': signal.get('sell_level', 0),
            'zone': signal.get('zone', ''),
            'detail': signal.get('detail', ''),
            'filter_level': signal.get('filter_level', ''),
            'resonance_tag': signal.get('resonance_tag', ''),
            'entry_conditions': entry_conds,
        }
        append_trade(record)

        # 入场：写入 SQLite 交易台账
        if signal.get('direction') == 'buy':
            trade_db.record_entry(record)
        # 出场：CCI顶背驰做T不结束交易，★卖减仓/死叉减仓结束交易
        elif signal.get('direction') == 'sell':
            exit_reason = signal.get('exit_reason', '')
            if exit_reason == 'CCI顶背驰做T':
                trade_db.record_t_point(
                    signal['code'],
                    record['time'],
                    signal.get('price', 0),
                )
            else:
                trade_db.record_exit(
                    signal['code'],
                    record['time'],
                    signal.get('price', 0),
                    exit_reason or '减仓',
                )

    # ─── 活跃标的高频监控 ───

    def _activate_stock(self, code, name, filter_level):
        """加入高频监控活跃池"""
        now_ts = time.time()
        if code not in self._active_stocks:
            print(f'  [活跃] {code} {name} 加入高频监控({filter_level}级)')
        self._active_stocks[code] = {
            'name': name,
            'filter_level': filter_level,
            'added_ts': now_ts,
            'no_signal_count': 0,
        }
        self._save_state()

    def _deactivate_stock(self, code):
        """从高频监控活跃池移除"""
        info = self._active_stocks.pop(code, None)
        if info:
            print(f'  [活跃] {code} {info["name"]} 移出高频监控')
            self._save_state()

    def _fast_scan(self, code, name):
        """轻量级快扫描：检查 vol 结构 + 60分趋势 + 信号状态，不调 _merge_and_compute"""
        # ─── a) 成交量结构: 从 today_5min raw 数据算 vr5 + 梯度 ───
        vol_status = ''
        vr5 = 0
        try:
            import pandas as pd
            today_5min = fetch_today_5min(code)
            if today_5min is not None and len(today_5min) >= 3:
                vol = today_5min['volume'].values
                vol_ma5 = vol[-5:].mean() if len(vol) >= 5 else vol.mean()
                vr5 = vol[-1] / vol_ma5 if vol_ma5 > 0 else 1
                vol_down = all(vol[i] < vol[i-1] for i in range(-min(3, len(vol)), 0))
                if vr5 < 0.6:
                    vol_status = '缩量回调' if not vol_down else '梯度缩量'
                elif vr5 > 1.5:
                    vol_status = '放量'
                else:
                    vol_status = '量平'
                if vol_down and vr5 < 1.0:
                    vol_status = '梯度缩量'
        except Exception:
            vol_status = '?'

        # ─── b) 60分趋势: 检查黄线是否完好 ───
        trend_ok = False
        expma50_val = 0
        try:
            rows = _load_csv(code, 'min60')
            if rows:
                r = rows[-1]
                c = float(r.get('close', 0) or 0)
                e50 = float(r.get('expma50', 0) or 0)
                if c > 0 and e50 > 0:
                    c_price = c / MIN_PRICE_FACTOR if c > 100 else c
                    e50_price = e50 / MIN_PRICE_FACTOR if e50 > 100 else e50
                    trend_ok = c_price > e50_price
                    expma50_val = e50_price
        except Exception:
            pass
        trend_status = '黄线完好' if trend_ok else '黄线破损'

        # ─── c) 信号状态: ★买之后是否有★卖/死叉 ───
        has_exit_signal = False
        info = self._signal_memory.get(code, {}).get('min5', {})
        buy_bar = info.get('last_buy_bar', '')
        if buy_bar and code in self._signal_memory:
            for period in ('min5', 'min15', 'min30'):
                p_info = self._signal_memory[code].get(period, {})
                sell_bar = p_info.get('last_sell_bar', '')
                if sell_bar and sell_bar > buy_bar:
                    has_exit_signal = True
                    break

        # ─── d) 组合标签 ───
        vol_shrink = '缩量' in vol_status
        if has_exit_signal:
            label = '🔴 趋势转弱减仓'
        elif not trend_ok:
            label = '🔶 趋势走弱关注'
        elif vol_shrink:
            label = '✅ 结构完好持有'
        else:
            label = '⚠️ 量价背离警惕'

        active_min = 0
        try:
            added = self._active_stocks.get(code, {}).get('added_ts', 0)
            if added:
                active_min = int((time.time() - added) / 60)
        except Exception:
            pass

        print(f'  [高频] {code} {name} | {label} | vol:{vr5:.2f} {vol_status} | '
              f'{trend_status}:{expma50_val:.3f} | 活跃{active_min}m')
        return label

    # ─── 主扫描逻辑 ───

    def scan_one(self, code, name, force=False):
        """
        对单只标的执行完整扫描：
        1. 强势区间滤网
        2. 各周期离散信号检测
        3. 多周期共振 + 价格约束
        4. 返回可行动信号 或 None

        force=False: 实时模式，只检测新bar上的新信号
        force=True: 测试模式，报告当前最新bar上存在的信号
        """
        # ──── Step 1: 强势区间 ────
        zone_info = _check_strength_zone(code)
        zone = zone_info['zone']
        prev_zone = self._last_zone.get(code)
        zone_changed = (zone != prev_zone)
        self._last_zone[code] = zone
        if zone_changed:
            self._save_state()

        # 出局 → 跳过不扫，但第一次出局要弹提醒
        if zone == 'out':
            if zone_changed:
                return {
                    'code': code, 'name': name,
                    'direction': 'out',
                    'title': '出局警示',
                    'detail': zone_info['detail'],
                    'signal_types': '60分钟破位',
                    'zone': 'out',
                    'buy_level': 0, 'sell_level': 0,
                    'price': 0,
                }
            return None

        # 恢复关注 → 继续往下扫信号，同时携带恢复标记
        restored = zone_changed and prev_zone == 'out'

        # ──── Step 2: 获取今日数据 ────
        today_5min = fetch_today_5min(code)
        if today_5min is None or len(today_5min) == 0:
            return None

        # 记录扫描进度（用于开机补扫判断）
        if not force:
            last_bar_ts = str(int(float(today_5min.iloc[-1]['timestamp'])))
            _last_scan_catchup_ts = self._last_scan_ts.get(code, '')
            self._last_scan_ts[code] = last_bar_ts

        direction = _load_daily_direction(code)

        # ──── Step 3: 各周期扫描 ────
        PERIODS = ['min5', 'min15', 'min30']
        TREND_PERIOD_MAP = {
            'min5': TREND_PERIOD_MIN_SHORT,
            'min15': TREND_PERIOD_MIN_SHORT,
            'min30': TREND_PERIOD_MIN,
        }

        signals_by_period = {}
        for period in PERIODS:
            hist_rows = _load_csv(code, period)
            if not hist_rows:
                continue

            if period == 'min5':
                today_df = today_5min
            else:
                today_df = _resample_to_period(today_5min, period)
                if today_df is None or len(today_df) == 0:
                    continue

            # 去重：今日数据中已在历史CSV中的bar（force模式跳过，用全部今日bar）
            if force:
                new_bars = today_df
            else:
                hist_ts_set = set(r.get('timestamp', '') for r in hist_rows)
                new_bars = today_df[~today_df['timestamp'].astype(str).isin(hist_ts_set)]
            if len(new_bars) == 0:
                continue

            tp = TREND_PERIOD_MAP.get(period, TREND_PERIOD_MIN_SHORT)
            result = _scan_one_period(code, name, hist_rows, new_bars, direction, period, tp, force=force)
            if result:
                result['period'] = period
                signals_by_period[period] = result

        if not signals_by_period:
            return None

        # ──── Step 4: 更新信号记忆，检测新的离散信号 ────
        latest_price = None
        all_new_buy = []   # 收集所有新的买信号
        all_new_sell = []  # 收集所有新的卖信号

        for period, ps in signals_by_period.items():
            if ps.get('price') and latest_price is None:
                latest_price = ps['price']

            for sig in ps.get('buy_signals', []):
                alert_type = f'{period}_{sig["type"]}'
                if force or self._is_new_alert(code, alert_type, sig['bar_ts']):
                    all_new_buy.append({**sig, 'period': period})

            for sig in ps.get('sell_signals', []):
                alert_type = f'{period}_{sig["type"]}'
                if force or self._is_new_alert(code, alert_type, sig['bar_ts']):
                    all_new_sell.append({**sig, 'period': period})

            # 更新信号记忆
            if code not in self._signal_memory:
                self._signal_memory[code] = {}
            if period not in self._signal_memory[code]:
                self._signal_memory[code][period] = {}

            all_buy = ps.get('buy_signals', [])
            if all_buy:
                self._signal_memory[code][period]['last_buy_bar'] = all_buy[0]['bar_ts']
                self._signal_memory[code][period]['last_buy_price'] = all_buy[0]['price']

            all_sell = ps.get('sell_signals', [])
            if all_sell:
                self._signal_memory[code][period]['last_sell_bar'] = all_sell[0]['bar_ts']
                self._signal_memory[code][period]['last_sell_price'] = all_sell[0]['price']

        # ──── Step 4.3: 开机补扫（如果有多根未扫描过的bar，全部检查一遍） ────
        if not force:
            _catchup_new_bars = 0
            _catchup_last_ts = _last_scan_catchup_ts
            if _catchup_last_ts:
                for _, row in today_5min.iterrows():
                    ts = str(int(float(row['timestamp'])))
                    if ts > _catchup_last_ts:
                        _catchup_new_bars += 1
            else:
                _catchup_new_bars = len(today_5min)
            if _catchup_new_bars > 1:
                for period, ps in signals_by_period.items():
                    _all_rows = ps.get('all_rows', [])
                    _hist_len = ps.get('hist_len', 0)
                    if not _all_rows or _hist_len >= len(_all_rows):
                        continue
                    _catchup = _detect_signals_catchup(_all_rows, _hist_len)
                    for _sig in _catchup.get('buy_signals', []):
                        _type = f'{period}_{_sig["type"]}'
                        if self._is_new_alert(code, _type, _sig['bar_ts']):
                            _sig['_catchup'] = True
                            all_new_buy.append({**_sig, 'period': period})
                    for _sig in _catchup.get('sell_signals', []):
                        _type = f'{period}_{_sig["type"]}'
                        if self._is_new_alert(code, _type, _sig['bar_ts']):
                            _sig['_catchup'] = True
                            all_new_sell.append({**_sig, 'period': period})
        # ──── Step 4.4: 1分钟 CCI顶背驰 检测(做T信号) ────
        min1_rows = _load_1min_signals(code)
        if min1_rows and len(min1_rows) >= 250:
            MIN1_SKIP = min(200, len(min1_rows) // 3)
            for offset in range(5):  # 最近5根1分钟bar
                idx = len(min1_rows) - 1 - offset
                if idx < MIN1_SKIP:
                    continue
                r = min1_rows[idx]
                cci_div = (r.get('cci_divergence', '') or '').strip()
                if cci_div != '顶背驰':
                    continue
                # 确保是新出现的(前一根bar不是顶背驰)
                prev_r = min1_rows[idx - 1] if idx > 0 else {}
                if (prev_r.get('cci_divergence', '') or '').strip() == '顶背驰':
                    continue
                close_price = float(r.get('close', 0))
                price = close_price / MIN_PRICE_FACTOR if close_price > 100 else close_price
                bar_ts = r.get('timestamp', '')
                alert_type = 'min1_CCI顶背驰'
                if not self._is_new_alert(code, alert_type, bar_ts):
                    continue
                all_new_sell.append({
                    'type': '1m_CCI顶背驰',
                    'price': price,
                    'bar_ts': bar_ts,
                    'period': 'min1',
                    'filter_level': 'sell_t_1m',
                    'notify': False,
                })

        # ──── Step 4.5: 买侧入场过滤 — 三级体系: MA / 金叉 / 共振 ────
        if self.entry_filter in ('any', 'ma', 'jincha', 'resonance', 'all') and all_new_buy:
            min5_ps = signals_by_period.get('min5')
            if min5_ps:
                all_rows = min5_ps.get('all_rows', [])
                for sig in all_new_buy:
                    sig.setdefault('filter_level', 'any')
                    if sig.get('period') != 'min5' or sig['type'] != SIGNAL_STAR_BUY:
                        continue
                    idx = sig.get('idx', -1)
                    bar = all_rows[idx] if 0 <= idx < len(all_rows) else all_rows[-1]
                    ma5 = float(bar.get('ma5', 0) or 0)
                    ma10 = float(bar.get('ma10', 0) or 0)
                    ma20 = float(bar.get('ma20', 0) or 0)
                    if not (ma5 > ma10 > ma20):
                        continue
                    # 条件2：最近20根内无死叉事件
                    has_death = False
                    for j in range(20):
                        pos = idx - j
                        if pos < 0:
                            break
                        cross = (all_rows[pos].get('expma_cross', '') or '').strip()
                        if cross == '死叉':
                            has_death = True
                            break
                        if cross == '金叉':
                            break
                    if has_death:
                        continue
                    # 条件3：60分钟在expma50黄线上方
                    min60_rows = _load_csv(code, 'min60')
                    min60_ok = False
                    if min60_rows:
                        last60 = min60_rows[-1]
                        c60 = float(last60.get('close', 0) or 0)
                        e50_60 = float(last60.get('expma50', 0) or 0)
                        min60_ok = c60 > e50_60
                    if not min60_ok:
                        continue

                    # PE门禁: MA级 → 日线PE非升熵 (回测验证: 均收益+0.96%)
                    if not _daily_pe_ok(code):
                        # 记录被过滤信号，供事后验证
                        daily_rows = _load_csv(code, 'daily')
                        dpe = float(daily_rows[-1].get('pe_chg_5', 0) or 0) if daily_rows else 0
                        _log_pe_gate_kill(code, name, bar, sig.get('bar_ts', ''),
                                          'ma', 'daily_pe_rising', dpe)
                        continue

                    sig['filter_level'] = 'ma'  # ← MA级通过（试错）

                    # 条件4：5分钟EXPMA金叉 → 升级金叉级（买）
                    expma12 = float(bar.get('expma12', 0) or 0)
                    expma50 = float(bar.get('expma50', 0) or 0)
                    if expma12 > expma50:
                        # PE门禁: 金叉级 → 5分钟PE非升熵 (回测验证: 胜率+10.2%)
                        pe_chg_5m = float(bar.get('pe_chg_5', 0) or 0)
                        if pe_chg_5m >= -0.02:  # 非升熵
                            sig['filter_level'] = 'jincha'
                        else:
                            # 记录被PE过滤的金叉级信号
                            _log_pe_gate_kill(code, name, bar, sig.get('bar_ts', ''),
                                              'jincha', 'min5_pe_rising', pe_chg_5m)
                        # 5min PE升熵 → 不升级, 保留MA级

                    # ── ±1天共振检测（由回测验证，不参与弹窗决策，仅标签） ──
                    bar_date = bar.get('date', '').strip()
                    if bar_date:
                        rt = _check_resonance(code, bar_date)
                        if rt:
                            sig['resonance_tag'] = '同日共振' if rt == 'same_day' else '异日共振'

            # 根据模式过滤信号
            if self.entry_filter == 'ma':
                all_new_buy = [s for s in all_new_buy if s.get('filter_level') in ('ma', 'jincha')]
            elif self.entry_filter == 'jincha':
                all_new_buy = [s for s in all_new_buy if s.get('filter_level') == 'jincha']
            elif self.entry_filter == 'resonance':
                # 先保留jincha，后续由cascade升级或丢弃
                all_new_buy = [s for s in all_new_buy if s.get('filter_level') == 'jincha']
            # 'all'模式: 全部保留，级别已标记; 'any'模式: 全部保留不过滤

        # ──── Step 4.6: 卖侧出场过滤 — 两层体系: 做T(日志) / 减仓(通知) ────
        if self.sell_filter != 'none' and all_new_sell:
            min5_ps = signals_by_period.get('min5')
            if min5_ps:
                all_rows = min5_ps.get('all_rows', [])
                for sig in all_new_sell:
                    # 死叉 = 直接减仓级，可行动
                    if sig['type'] == SIGNAL_DEATH:
                        sig['filter_level'] = 'sell_reduce'
                        sig['notify'] = True
                        continue
                    sig.setdefault('filter_level', '')
                    sig.setdefault('notify', False)
                    idx = sig.get('idx', -1)
                    if idx < 0 or idx >= len(all_rows):
                        continue
                    bar = all_rows[idx]
                    close = float(bar.get('close', 0) or 0)
                    expma50 = float(bar.get('expma50', 0) or 0)

                    # ── 做T层: CCI顶背驰 + close>EXPMA黄线 + single(窗口内唯一) → 日志 ──
                    if sig['type'] == SIGNAL_CCI_TOP_DIV and sig.get('period') == 'min5':
                        if not (expma50 > 0 and close > expma50):
                            continue
                        ws = _find_window_start(all_rows, idx)
                        cci_count = _count_cci_top_divergence(all_rows, ws, idx)
                        if cci_count > 1:
                            continue
                        sig['filter_level'] = 'sell_t'
                        sig['notify'] = False
                        continue

                    # ── 减仓层: ★卖 + close<MA5 + 无金叉(20根) + 15分黄线下 → 通知 ──
                    if sig.get('period') != 'min5' or sig['type'] != SIGNAL_STAR_SELL:
                        continue
                    ma5 = float(bar.get('ma5', 0) or 0)
                    if not (ma5 > 0 and close < ma5):
                        continue
                    has_golden = False
                    for j in range(20):
                        pos = idx - j
                        if pos < 0:
                            break
                        cross = (all_rows[pos].get('expma_cross', '') or '').strip()
                        if cross == '金叉':
                            has_golden = True
                            break
                        if cross == '死叉':
                            break
                    if has_golden:
                        continue
                    min15_rows = _load_csv(code, 'min15')
                    min15_ok = False
                    if min15_rows:
                        last15 = min15_rows[-1]
                        c15 = float(last15.get('close', 0) or 0)
                        e50_15 = float(last15.get('expma50', 0) or 0)
                        if e50_15 > 0:
                            min15_ok = c15 < e50_15
                    if not min15_ok:
                        continue
                    sig['filter_level'] = 'sell_reduce'
                    sig['notify'] = True

            # 根据sell_filter模式过滤信号
            if self.sell_filter == 'sell_t':
                all_new_sell = [s for s in all_new_sell if s.get('filter_level') in ('sell_t', 'sell_t_1m')]
            elif self.sell_filter == 'sell_reduce':
                all_new_sell = [s for s in all_new_sell if s.get('filter_level') == 'sell_reduce']
            elif self.sell_filter == 'none':
                all_new_sell = []
            # 'all': 全部保留（sell_t日志 + sell_reduce通知）

        # ──── Step 4.7: 补扫信号详细日志 ────
        try:
            if _catchup_new_bars > 1:
                _catchup_buys = [s for s in all_new_buy if s.get('_catchup')]
                _catchup_sells = [s for s in all_new_sell if s.get('_catchup')]
                if _catchup_buys or _catchup_sells:
                    parts = []
                    # 买侧详细
                    buy_detail = []
                    for s in _catchup_buys:
                        lv = s.get('filter_level', '')
                        if lv in ('ma', 'jincha', 'resonance'):
                            lv_label = {'ma': 'MA', 'jincha': '金叉', 'resonance': '共振'}[lv]
                            buy_detail.append(f'{s["type"]}({lv_label}级)')
                        else:
                            buy_detail.append(s['type'])
                    if buy_detail:
                        parts.append('买:' + ','.join(buy_detail))
                    # 卖侧详细
                    sell_detail = []
                    for s in _catchup_sells:
                        lv = s.get('filter_level', '')
                        if lv in ('sell_reduce', 'sell_t'):
                            lv_label = {'sell_reduce': '减仓', 'sell_t': '做T'}[lv]
                            sell_detail.append(f'{s["type"]}({lv_label})')
                        else:
                            sell_detail.append(s['type'])
                    if sell_detail:
                        parts.append('卖:' + ','.join(sell_detail))
                    print(f'  [补扫] {code} {name}: {_catchup_new_bars}根遗漏bar, {", ".join(parts)}')
        except Exception as _e:
            pass

        # ──── Step 4.8: 持仓止损检查 ────
        if self.sell_filter != 'none':
            open_entries = trade_db.get_open_entries(code)
            if open_entries:
                entry = open_entries[0]
                band_low_val = entry.get('band_low')
                if band_low_val is not None and band_low_val > 0:
                    min5_ps = signals_by_period.get('min5')
                    if min5_ps:
                        all_rows_5m = min5_ps.get('all_rows', [])
                        if all_rows_5m:
                            latest_bar = all_rows_5m[-1]
                            latest_low = float(latest_bar.get('low', 0) or 0)
                            if latest_low > 0 and latest_low < band_low_val:
                                price = latest_low / MIN_PRICE_FACTOR if latest_low > 100 else latest_low
                                all_new_sell.append({
                                    'type': '止损',
                                    'price': round(price, 4),
                                    'bar_ts': latest_bar.get('timestamp', ''),
                                    'period': 'min5',
                                    'filter_level': 'sell_reduce',
                                    'notify': True,
                                })

        # ──── Step 5: 决定是否弹窗 ────
        # 确定主导方向
        if direction in Direction.BULLISH_DIRS:
            primary_side = 'buy'
        elif direction in Direction.BEARISH_DIRS:
            primary_side = 'sell'
        else:
            buy_total = sum(ps.get('buy_level', 0) for ps in signals_by_period.values())
            sell_total = sum(ps.get('sell_level', 0) for ps in signals_by_period.values())
            primary_side = 'buy' if buy_total >= sell_total else 'sell'

        # 止损信号强制优先，覆盖日线方向
        if primary_side == 'buy' and all_new_sell:
            has_stop = any(s.get('type') == '止损' for s in all_new_sell)
            if has_stop:
                primary_side = 'sell'

        new_signals = all_new_buy if primary_side == 'buy' else all_new_sell

        if not new_signals:
            self._save_state()
            return None

        # ──── Step 6: 共振级联检测 ────
        cascade = _check_cascade(signals_by_period, primary_side)

        # 共振/全模式：多周期级联确认（升级jincha→resonance）
        if self.entry_filter in ('resonance', 'all') and primary_side == 'buy' and new_signals:
            if cascade['cascade_type']:
                for sig in new_signals:
                    if sig.get('filter_level') == 'jincha':
                        sig['filter_level'] = 'resonance'
            elif self.entry_filter == 'resonance':
                new_signals = []
                self._save_state()
                return None

        # 汇总信号质量
        total_buy = sum(ps.get('buy_level', 0) for ps in signals_by_period.values())
        total_sell = sum(ps.get('sell_level', 0) for ps in signals_by_period.values())

        # 构建标题和信号描述
        side_label = '买' if primary_side == 'buy' else '卖'
        filter_tag = ''
        if primary_side == 'buy':
            if self.entry_filter == 'all':
                levels = [s.get('filter_level', 'any') for s in new_signals]
                # 忽略'any'级，取最高级别
                effective = [lv for lv in levels if lv in ('ma', 'jincha', 'resonance')]
                if not effective:
                    # 全部是'any'级 → 不弹窗（只记录日志）
                    self._save_state()
                    return None
                lv = 'resonance' if 'resonance' in effective else ('jincha' if 'jincha' in effective else 'ma')
                filter_tag = {'resonance': '[共振]', 'jincha': '[金叉]', 'ma': '[MA]'}[lv]
            elif self.entry_filter == 'ma':
                filter_tag = '[MA]'
            elif self.entry_filter == 'jincha':
                filter_tag = '[金叉]'
            elif self.entry_filter == 'resonance':
                filter_tag = '[共振]'
            elif self.entry_filter == 'any':
                filter_tag = '[any]'

        if primary_side == 'sell':
            if self.sell_filter == 'all':
                levels = [s.get('filter_level', 'sell_t') for s in new_signals]
                effective = [lv for lv in levels if lv in ('sell_reduce', 'sell_t', 'sell_t_1m')]
                if not effective:
                    self._save_state()
                    return None
                lv = 'sell_reduce' if 'sell_reduce' in effective else ('sell_t' if 'sell_t' in effective else 'sell_t_1m')
                filter_tag = {'sell_reduce': '[减仓]', 'sell_t': '[做T]', 'sell_t_1m': '[做T]'}[lv]
            elif self.sell_filter == 'sell_t':
                filter_tag = '[做T]'
            elif self.sell_filter == 'sell_reduce':
                filter_tag = '[减仓]'

        # 提取共振标签（同日/异日，由回测数据验证，纯显示不参与弹窗决策）
        resonance_tag = ''
        if primary_side == 'buy' and new_signals:
            for s in new_signals:
                rt = s.get('resonance_tag', '')
                if rt:
                    resonance_tag = rt
                    break

        if cascade['cascade_type']:
            title = f'★{side_label} {filter_tag}[{cascade["cascade_type"]}]'
            signal_types = ' + '.join(cascade['periods_confirmed'])
            signal_types += f' 共振'
        elif resonance_tag:
            title = f'★{side_label} {filter_tag}[{resonance_tag}]'
            signal_types = resonance_tag
        elif new_signals:
            periods_str = ','.join(s['period'] for s in new_signals)
            title = f'★{side_label} {filter_tag}[{periods_str}]'
            signal_types = ','.join(f'{s["period"]}.{s["type"]}' for s in new_signals)
        else:
            signal_types = ''
            title = f'★{side_label} {filter_tag}'

        detail_parts = [zone_info['detail']]
        if restored:
            detail_parts.insert(0, '【恢复关注】')
        if resonance_tag:
            detail_parts.append(f'{resonance_tag}(±1天窗口)')
        if cascade['detail']:
            detail_parts.append(cascade['detail'])

        if restored and not new_signals:
            title = '恢复关注'
            signal_types = '重新站上黄线，等待信号'

        # ── 提取入场条件快照（从第一个买信号的5分钟bar） ──
        entry_conditions = {}
        exit_reason = ''
        if primary_side == 'buy' and new_signals:
            min5_ps = signals_by_period.get('min5', {})
            min5_rows = min5_ps.get('all_rows', [])
            first_sig = new_signals[0]
            idx = first_sig.get('idx', -1)
            if 0 <= idx < len(min5_rows):
                bar = min5_rows[idx]
                band_low = _calc_entry_band_low(min5_rows, idx)
                entry_conditions = {
                    'bar_ts': int(bar.get('timestamp', 0) or 0),
                    'ma5': round(float(bar.get('ma5', 0) or 0), 4),
                    'ma10': round(float(bar.get('ma10', 0) or 0), 4),
                    'ma20': round(float(bar.get('ma20', 0) or 0), 4),
                    'expma12': round(float(bar.get('expma12', 0) or 0), 4),
                    'expma50': round(float(bar.get('expma50', 0) or 0), 4),
                    'expma_cross': (bar.get('expma_cross', '') or '').strip(),
                    'close_price': round(float(bar.get('close', 0) or 0), 4),
                    'volume': int(float(bar.get('volume', 0) or 0)),
                    'min60_above_expma50': zone_info.get('min60_ok', False),
                    'band_low': round(band_low, 4),
                }
        elif primary_side == 'sell' and new_signals:
            first_sell = new_signals[0]
            st = first_sell.get('type', '')
            fl = first_sell.get('filter_level', '')
            if fl in ('sell_t', 'sell_t_1m'):
                exit_reason = 'CCI顶背驰做T'
            elif st == SIGNAL_DEATH:
                exit_reason = '死叉减仓'
            elif st == SIGNAL_STAR_SELL:
                exit_reason = '★卖减仓'
            else:
                exit_reason = st

        self._save_state()

        # 卖侧: sell_t 仅日志不弹窗, sell_reduce 弹通知
        if primary_side == 'sell' and new_signals:
            should_notify = any(s.get('notify', False) for s in new_signals)
        else:
            should_notify = True

        return {
            'code': code,
            'name': name,
            'price': latest_price or 0,
            'direction': primary_side,
            'title': title,
            'detail': ' | '.join(detail_parts),
            'signal_types': signal_types,
            'cascade_type': cascade['cascade_type'] or '',
            'periods_confirmed': cascade['periods_confirmed'],
            'buy_level': round(total_buy, 1),
            'sell_level': round(total_sell, 1),
            'zone': zone,
            'restored': restored,
            'filter_level': filter_tag,
            'resonance_tag': resonance_tag,
            'entry_conditions': entry_conditions,
            'exit_reason': exit_reason,
            'notify': should_notify,
            'details': [ps.get('details', []) for ps in signals_by_period.values()],
        }

    # ─── 主循环 ───

    def run(self):
        """主循环 — 全量300s扫描 + 活跃标的高频60s快扫描"""
        print(f'[monitor] 启动 — 扫描间隔={self.interval}s  买侧过滤={self.entry_filter}  卖侧过滤={self.sell_filter}')
        universe = load_universe()
        print(f'[monitor] 候选池: {len(universe)} 只')
        if self._active_stocks:
            print(f'[monitor] 活跃池: {len(self._active_stocks)} 只 (高频监控)')
            for c, info in self._active_stocks.items():
                print(f'   {c} {info["name"]} ({info["filter_level"]}级)')

        while True:
            cycle_start = time.time()
            alerts_this_round = 0

            # ─── 全量扫描（所有标的） ───
            try:
                for stock in universe:
                    code, name = stock['code'], stock['name']
                    try:
                        signal = self.scan_one(code, name)
                        if signal and signal.get('direction') != 'out':
                            if signal.get('notify', True):
                                self._notify(signal)
                            self._record(signal)
                            alerts_this_round += 1
                            print(f'  [{datetime.now().strftime("%H:%M:%S")}] {code} {name} '
                                  f'{signal["title"]} {signal.get("signal_types","")}')
                            # 买信号 → 加入活跃池
                            if signal.get('direction') == 'buy' and signal.get('filter_level'):
                                self._activate_stock(code, name, signal['filter_level'])
                            # 卖信号 → 从活跃池移除（已无持仓需要守护）
                            elif signal.get('direction') == 'sell' and code in self._active_stocks:
                                self._deactivate_stock(code)
                        elif code in self._active_stocks:
                            # 活跃标的本轮无买信号 → 递增计数
                            info = self._active_stocks[code]
                            info['no_signal_count'] = info.get('no_signal_count', 0) + 1
                            if info['no_signal_count'] >= 3:
                                self._deactivate_stock(code)
                    except Exception as e:
                        continue

            except KeyboardInterrupt:
                print('\n[monitor] 用户中断，退出')
                break
            except Exception as e:
                print(f'[monitor] 循环异常: {e}')

            elapsed = time.time() - cycle_start
            print(f'[monitor] 本轮 {alerts_this_round}条信号, {elapsed:.1f}s, '
                  f'下次扫描 {self.interval}s 后')

            # ─── 活跃标的高频快扫描子循环 ───
            if self._active_stocks:
                fast_count = 0
                while time.time() - cycle_start < self.interval - 10:
                    for code, info in list(self._active_stocks.items()):
                        try:
                            self._fast_scan(code, info['name'])
                        except Exception:
                            pass
                    fast_count += 1
                    sleep_remain = self.interval - (time.time() - cycle_start) - 5
                    if sleep_remain > 10:
                        time.sleep(min(55, sleep_remain))
                    else:
                        break
                if fast_count:
                    print(f'[monitor] 高频监控 {len(self._active_stocks)}只活跃标的, {fast_count}轮快扫')
            else:
                time.sleep(max(1, self.interval - elapsed))


def main():
    import argparse
    parser = argparse.ArgumentParser(description='实时 ★买/★卖 监控')
    parser.add_argument('--interval', type=int, default=SCAN_INTERVAL, help='扫描间隔(秒)')
    parser.add_argument('--no-toast', action='store_true', help='禁用系统通知')
    parser.add_argument('--once', action='store_true', help='只扫一轮')
    parser.add_argument('--filter', dest='entry_filter', type=str, default='ma',
                        choices=['any', 'ma', 'jincha', 'resonance', 'all'],
                        help='买侧过滤: any=裸★买(测试) ma=MA级(试错) jincha=金叉级(买) resonance=共振级(买完) all=三级同时弹')
    parser.add_argument('--sell-filter', dest='sell_filter', type=str, default='all',
                        choices=['none', 'sell_t', 'sell_reduce', 'all'],
                        help='卖侧过滤: none=不监测 sell_t=做T(仅日志) sell_reduce=减仓(通知) all=全部')
    args = parser.parse_args()

    trade_db.init_db()
    monitor = Monitor(interval=args.interval, use_toast=not args.no_toast, entry_filter=args.entry_filter, sell_filter=args.sell_filter)

    if args.once:
        universe = load_universe()
        filter_label = {'any': '裸★买', 'ma': 'MA级(试错)', 'jincha': '金叉级(买)', 'resonance': '共振级(买完)', 'all': 'MA/金叉/共振三级'}[args.entry_filter]
        sell_filter_label = {'none': '不监测', 'sell_t': '做T(仅日志)', 'sell_reduce': '减仓(通知)', 'all': '做T+减仓'}[args.sell_filter]
        print(f'[monitor] 快照扫描: {len(universe)} 只  买侧:{filter_label}  卖侧:{sell_filter_label}')
        print()

        out_stocks = []
        signal_stocks = []
        quiet_stocks = []

        for stock in universe:
            code, name = stock['code'], stock['name']
            try:
                signal = monitor.scan_one(code, name, force=True)
                if signal:
                    direction = signal.get('direction', '')
                    if direction == 'out':
                        out_stocks.append((code, name, signal))
                    elif direction == 'restored':
                        signal_stocks.append((code, name, signal))
                    else:
                        signal_stocks.append((code, name, signal))
                else:
                    # 查一下zone
                    from tools.volume_leader.monitor import _check_strength_zone
                    zone_info = _check_strength_zone(code)
                    quiet_stocks.append((code, name, zone_info.get('zone', '?')))
            except Exception as e:
                quiet_stocks.append((code, name, f'ERR:{e}'))

        # ── OUT 区 ──
        if out_stocks:
            print(f'--- 出局（不看）: {len(out_stocks)} 只 ---')
            for code, name, sig in out_stocks:
                print(f'  {code} {name}: {sig["detail"]}')
            print()

        # ── 有信号区 ──
        if signal_stocks:
            print(f'--- 有信号: {len(signal_stocks)} 只 ---')
            print(f'  {"代码":<12} {"名称":<10} {"级别":<8} {"信号":<28} {"买/卖分":>10} {"区域":<16}')
            print(f'  {"-"*90}')
            for code, name, sig in signal_stocks:
                direction = sig.get('direction', '')
                if direction == 'restored':
                    print(f'  {code:<12} {name:<10} [恢复关注] {sig["detail"]}')
                else:
                    cascade = sig.get('cascade_type', '')
                    cascade_str = f' [{cascade}]' if cascade else ''
                    signals = sig.get('signal_types', '')
                    scores = f'{sig["buy_level"]}/{sig["sell_level"]}'
                    zone = sig.get('zone', '?')
                    fl = sig.get('filter_level', '')
                    print(f'  {code:<12} {name:<10} {fl:8s} {signals + cascade_str:<28} {scores:>10} {zone:<16}')
            print()

        # ── 无信号区（在强势区间但没最近信号） ──
        if quiet_stocks:
            print(f'--- 观望（强势区间无最近信号）: {len(quiet_stocks)} 只 ---')
            # 按zone分组
            strong = [(c,n) for c,n,z in quiet_stocks if z == 'strong']
            secondary = [(c,n) for c,n,z in quiet_stocks if z == 'secondary']
            other = [(c,n,z) for c,n,z in quiet_stocks if z not in ('strong', 'secondary')]
            if strong:
                names = ','.join(c for c,_ in strong)
                print(f'  真强势({len(strong)}): {names}')
            if secondary:
                names = ','.join(c for c,_ in secondary)
                print(f'  二级强势({len(secondary)}): {names}')
            if other:
                for c,n,z in other:
                    print(f'  {c} {n}: {z}')

        print()
        print(f'[monitor] 完成 — OUT:{len(out_stocks)} 信号:{len(signal_stocks)} 观望:{len(quiet_stocks)}')
    else:
        monitor.run()


if __name__ == '__main__':
    main()

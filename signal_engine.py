# -*- coding: utf-8 -*-
"""
信号计算引擎 v1.2
计算 EXPMA、MACD、分时出击趋势线、★买★卖信号
支持日线和分钟线数据（通达信二进制格式）

日线: 价格 = raw / 1000
分钟线: 价格 = raw / 10000 (v4.4 升级，之前为 /100)

v1.2: 抽取核心计算函数消除三函数重复、O(n²)→O(n)算法优化、合并冗余常量
"""

import struct
import os
import csv
import json
from collections import deque
from datetime import datetime

# ========== 常量 ==========

DAY_PRICE_FACTOR = 1000   # 日线价格编码: price * 1000
MIN_PRICE_FACTOR = 10000  # 分钟线价格编码: price * 10000 (v4.4, 精度0.0001元)

# 分时出击参数
TREND_PERIOD_DAILY = 55       # 日线 LLV/HHV 周期
TREND_PERIOD_MIN = 55         # 30-60分钟线 LLV/HHV 周期
TREND_PERIOD_MIN_SHORT = 40   # 5-15分钟线 LLV/HHV 周期

# SMA 参数 (通达信 SMA(X, N, M))
SMA1_N, SMA1_M = 5, 1
SMA2_N, SMA2_M = 3, 1

# 趋势线 EMA 周期
TREND_EMA_PERIOD = 3

# EXPMA 参数
EXPMA_FAST = 12
EXPMA_SLOW = 50

# MACD 参数
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL_PERIOD = 9

# 牛熊红线参数
BULL_BEAR_PERIOD = 221   # 年线周期
BULL_BEAR_STD_MULT = 3   # 标准差倍数

# CCI 参数
CCI_PERIOD = 14           # CCI 标准周期
CCI_EXTREME_LEVELS = [200, 250, 300]  # 极限值等级(不同品种敏感度不同)

# CCI 背驰检测阈值
CCI_DIV_POS_THRESHOLD = 0.7   # 正背驰: CCI从高点回落至此比例以下
CCI_DIV_NEG_THRESHOLD = 1.5   # 负背驰: CCI从低点回升至此比例以上


# ========== 数据读取 ==========

def read_bars(filepath):
    """
    读取通达信二进制文件（日线/lc5/lc15/lc30/lc60通用）
    每条 32 字节: 8 个 uint32
    日线: (date_YYYYMMDD, open, high, low, close, amount, volume, reserved) 价格*1000
    分钟线: (date_YYYYMMDD, open, high, low, close, amount, volume, time_HHMM)
            注意: 分钟线前4字节是日期(YYYYMMDD), 最后4字节是时间(HHMM, 如935=09:35)
            需合并为 YYYYMMDDHHMM 才能精确定位到具体分钟
    """
    bars = []
    if not os.path.exists(filepath):
        return bars
    with open(filepath, 'rb') as f:
        while True:
            raw = f.read(32)
            if len(raw) < 32:
                break
            bars.append(struct.unpack('<8I', raw))
    return bars


def read_bars_lc1(filepath):
    """
    读取通达信1分钟线(.lc1)二进制文件

    lc1格式: HHfffffII (不同于lc5的8I格式)
    - HH: 日期(ushort) + 分钟数(ushort)
    - fffff: open/high/low/close/amount (IEEE754 float)
    - II: volume(int) + reserved(int)

    日期解码: year=num//2048+2004, month=(num%2048)//100, day=(num%2048)%100
    时间解码: hour=num//60, minute=num%60
    """
    bars = []
    if not os.path.exists(filepath):
        return bars
    with open(filepath, 'rb') as f:
        while True:
            raw = f.read(32)
            if len(raw) < 32:
                break
            date_num, minutes, open_f, high_f, low_f, close_f, amount_f, volume, reserved = \
                struct.unpack('<HHfffffII', raw)
            year = date_num // 2048 + 2004
            month = (date_num % 2048) // 100
            day = (date_num % 2048) % 100
            hour = minutes // 60
            minute = minutes % 60
            timestamp = year * 100000000 + month * 1000000 + day * 10000 + hour * 100 + minute
            bars.append((timestamp,
                         int(open_f * 10000),
                         int(high_f * 10000),
                         int(low_f * 10000),
                         int(close_f * 10000),
                         int(amount_f),
                         volume,
                         0))
    return bars


# ========== 指标计算 ==========

def calc_expma(values, period):
    """EMA(X, N): Y = 2/(N+1)*X + (N-1)/(N+1)*Y'"""
    result = []
    k = 2.0 / (period + 1)
    for i, v in enumerate(values):
        if i == 0:
            result.append(float(v))
        else:
            result.append(float(v) * k + result[-1] * (1 - k))
    return result


def calc_ma(values, period):
    """简单移动平均 MA(X, N): O(n) 滑动窗口"""
    result = []
    total = 0.0
    for i, v in enumerate(values):
        total += float(v)
        if i >= period:
            total -= float(values[i - period])
            result.append(round(total / period, 4))
        else:
            result.append(round(total / (i + 1), 4))
    return result


def _rolling_min(values, period):
    """O(n) 滚动最小值，使用单调双端队列"""
    n = len(values)
    result = [0.0] * n
    dq = deque()
    for i in range(n):
        while dq and dq[0] <= i - period:
            dq.popleft()
        while dq and values[dq[-1]] >= values[i]:
            dq.pop()
        dq.append(i)
        result[i] = values[dq[0]]
    return result


def _rolling_max(values, period):
    """O(n) 滚动最大值，使用单调双端队列"""
    n = len(values)
    result = [0.0] * n
    dq = deque()
    for i in range(n):
        while dq and dq[0] <= i - period:
            dq.popleft()
        while dq and values[dq[-1]] <= values[i]:
            dq.pop()
        dq.append(i)
        result[i] = values[dq[0]]
    return result


def calc_sma(values, n, m):
    """通达信 SMA(X, N, M) = (M*X + (N-M)*Y') / N"""
    result = []
    for i, v in enumerate(values):
        if i == 0:
            result.append(float(v))
        else:
            result.append((m * float(v) + (n - m) * result[-1]) / float(n))
    return result


def calc_macd(closes, fast=MACD_FAST, slow=MACD_SLOW, sig=MACD_SIGNAL_PERIOD):
    """MACD: DIF = EMA(12) - EMA(26), DEA = EMA(DIF,9), HIST = 2*(DIF-DEA)"""
    ema_f = calc_expma(closes, fast)
    ema_s = calc_expma(closes, slow)
    dif = [f - s for f, s in zip(ema_f, ema_s)]
    dea = calc_expma(dif, sig)
    hist = [2 * (d - a) for d, a in zip(dif, dea)]
    return dif, dea, hist


def calc_cci(highs, lows, closes, period=CCI_PERIOD):
    """
    CCI (Commodity Channel Index) 标准公式:
    TP = (H + L + C) / 3
    CCI = (TP - MA_TP) / (0.015 * MD)
    O(n) 实现，使用滑动窗口累积和
    """
    n = len(closes)
    tp = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]

    # MA of TP — 滑动窗口累积和
    ma_tp = [0.0] * n
    tp_sum = 0.0
    for i in range(n):
        tp_sum += tp[i]
        if i >= period:
            tp_sum -= tp[i - period]
            ma_tp[i] = tp_sum / period
        else:
            ma_tp[i] = tp_sum / (i + 1)

    # Mean Deviation & CCI — 单次遍历
    cci = [0.0] * n
    for i in range(n):
        start = max(0, i - period + 1)
        cnt = i + 1 - start
        dev_sum = 0.0
        for j in range(start, i + 1):
            dev_sum += abs(tp[j] - ma_tp[i])
        md = dev_sum / cnt
        if md == 0:
            cci[i] = 0.0
        else:
            cci[i] = (tp[i] - ma_tp[i]) / (0.015 * md)

    return cci


def detect_cci_extreme(cci, levels=CCI_EXTREME_LEVELS):
    """
    CCI 极限值检测:
    返回: list[dict], 每个bar记录极限值状态
      'extreme_high': 达到的最高正极限等级 (200/250/300), 0=无
      'extreme_low':  达到的最低负极限等级 (-200/-250/-300), 0=无
      'from_extreme_high': 从正极限回落(前一天>=level, 当天<level)
      'from_extreme_low':  从负极限反弹(前一天<=-level, 当天>-level)
    """
    n = len(cci)
    result = []
    for i in range(n):
        val = cci[i]
        eh = 0
        el = 0
        for lvl in levels:
            if val >= lvl:
                eh = lvl
            if val <= -lvl:
                el = -lvl

        fe_h = {}
        fe_l = {}
        if i > 0:
            prev = cci[i - 1]
            for lvl in levels:
                if prev >= lvl and val < lvl:
                    fe_h[lvl] = True
                if prev <= -lvl and val > -lvl:
                    fe_l[lvl] = True

        result.append({
            'extreme_high': eh,
            'extreme_low': el,
            'from_extreme_high': fe_h,
            'from_extreme_low': fe_l,
        })
    return result


def detect_cci_divergence(cci, closes, lookback=5):
    """
    CCI 背驰检测:
    正背驰: 价格新高但 CCI 高点降低 → 冲高回落风险大
    负背驰: 价格新低但 CCI 低点抬高 → 探底回升概率高
    返回: list[dict], 每个bar含 'pos_div'/'neg_div' bool
    """
    n = len(cci)
    result = [{'pos_div': False, 'neg_div': False} for _ in range(n)]

    for i in range(lookback, n):
        recent_max_cci_idx = None
        recent_min_cci_idx = None
        for j in range(i - lookback, i + 1):
            if abs(cci[j]) >= CCI_EXTREME_LEVELS[0]:
                if cci[j] > 0 and (recent_max_cci_idx is None or cci[j] > cci[recent_max_cci_idx]):
                    recent_max_cci_idx = j
                if cci[j] < 0 and (recent_min_cci_idx is None or cci[j] < cci[recent_min_cci_idx]):
                    recent_min_cci_idx = j

        if recent_max_cci_idx is not None and recent_max_cci_idx < i:
            peak_price = closes[recent_max_cci_idx]
            peak_cci_val = cci[recent_max_cci_idx]
            if closes[i] > peak_price and cci[i] < peak_cci_val * CCI_DIV_POS_THRESHOLD:
                result[i]['pos_div'] = True

        if recent_min_cci_idx is not None and recent_min_cci_idx < i:
            trough_price = closes[recent_min_cci_idx]
            trough_cci_val = cci[recent_min_cci_idx]
            if closes[i] < trough_price and cci[i] > trough_cci_val * CCI_DIV_NEG_THRESHOLD:
                result[i]['neg_div'] = True

    return result


def calc_bull_bear_line(closes, period=BULL_BEAR_PERIOD, std_mult=BULL_BEAR_STD_MULT):
    """
    牛熊红线 = MA(CLOSE, N) + M * STD(CLOSE, N)
    O(n) 实现，使用前缀和避免每步重建窗口
    """
    n = len(closes)
    fcloses = [float(c) for c in closes]

    # 前缀和 & 平方和
    pref = [0.0]
    pref2 = [0.0]
    for c in fcloses:
        pref.append(pref[-1] + c)
        pref2.append(pref2[-1] + c * c)

    ma_line = [0.0] * n
    red_line = [0.0] * n
    for i in range(n):
        start = max(0, i - period + 1)
        cnt = i + 1 - start
        s = pref[i + 1] - pref[start]
        m = s / cnt
        variance = (pref2[i + 1] - pref2[start]) / cnt - m * m
        if variance < 0:
            variance = 0.0
        std = variance ** 0.5
        ma_line[i] = round(m, 4)
        red_line[i] = round(m + std_mult * std, 4)

    return ma_line, red_line


def detect_red_line_cross(closes, red_line):
    """
    检测价格穿越牛熊红线
    返回: (break_above_indices, break_below_indices)
    """
    up_indices = []
    down_indices = []
    for i in range(1, len(closes)):
        if float(closes[i - 1]) <= red_line[i - 1] < float(closes[i]):
            up_indices.append(i)
        elif float(closes[i - 1]) >= red_line[i - 1] > float(closes[i]):
            down_indices.append(i)
    return up_indices, down_indices


def calc_trend_line(highs, lows, closes, period):
    """
    分时出击趋势线:
    1. RSV = (C - LLV(L,N)) / (HHV(H,N) - LLV(L,N)) * 100
    2. SMA1 = SMA(RSV, 5, 1)
    3. SMA2 = SMA(SMA1, 3, 1)
    4. V11 = 3*SMA1 - 2*SMA2
    5. Trend = EMA(V11, 3)
    O(n) 实现，LLV/HHV 使用单调双端队列
    """
    n = len(closes)
    fhighs = [float(h) for h in highs]
    flows = [float(l) for l in lows]

    llv = _rolling_min(flows, period)
    hhv = _rolling_max(fhighs, period)

    rsv = []
    for i in range(n):
        diff = hhv[i] - llv[i]
        if diff == 0:
            rsv.append(50.0)
        else:
            rsv.append((float(closes[i]) - llv[i]) / diff * 100.0)

    sma1 = calc_sma(rsv, SMA1_N, SMA1_M)
    sma2 = calc_sma(sma1, SMA2_N, SMA2_M)
    v11 = [3.0 * s1 - 2.0 * s2 for s1, s2 in zip(sma1, sma2)]
    trend = calc_expma(v11, TREND_EMA_PERIOD)
    return trend


# ========== 信号检测 ==========

def detect_star_signals(trend_line):
    """
    ★买: 趋势线上穿 11（前值≤11, 当天>11）
    ★卖: 先上穿90进入高位, 再下穿90触发（必须先到过90+）
    """
    buy_indices = []
    sell_indices = []
    in_high_zone = False

    for i in range(1, len(trend_line)):
        if trend_line[i - 1] <= 11 < trend_line[i]:
            buy_indices.append(i)

        if trend_line[i] >= 90:
            in_high_zone = True

        if in_high_zone and trend_line[i - 1] >= 90 > trend_line[i]:
            sell_indices.append(i)
            in_high_zone = False

    return buy_indices, sell_indices


def detect_expma_cross(e12, e50):
    """EXPMA 金叉(上穿) / 死叉(下穿)"""
    golden = []
    death = []
    for i in range(1, len(e12)):
        if e12[i - 1] <= e50[i - 1] and e12[i] > e50[i]:
            golden.append(i)
        elif e12[i - 1] >= e50[i - 1] and e12[i] < e50[i]:
            death.append(i)
    return golden, death


# ========== CCI 标签格式化 ==========

def _format_cci_labels(cci_extreme_i, cci_div_i):
    """CCI 状态文字化，供 _calc_signals_from_arrays 调用"""
    eh = cci_extreme_i['extreme_high']
    el = cci_extreme_i['extreme_low']
    fe_h = cci_extreme_i['from_extreme_high']

    if eh > 0:
        ext_label = 'CCI+%d' % eh
    elif el < 0:
        ext_label = 'CCI%d' % el
    else:
        ext_label = ''

    retreat_label = ''
    if fe_h:
        retreat_label = '回撤+%d' % max(fe_h.keys())

    if cci_div_i['pos_div']:
        div_label = '顶背驰'
    elif cci_div_i['neg_div']:
        div_label = '底背驰'
    else:
        div_label = ''

    return ext_label, retreat_label, div_label


# ========== 核心信号计算（日线/分钟线共用） ==========

def _calc_signals_from_arrays(opens, highs, lows, closes, vols, amts,
                               timestamps, trend_period):
    """
    核心信号计算 — 从已缩放的 OHLCV 数组计算全部指标和信号。

    参数:
      opens/highs/lows/closes: 价格序列（float，已缩放为实际价格或原始编码值）
      vols/amts: 成交量/成交额序列（原始值）
      timestamps: 时间戳序列（int，日线=YYYYMMDD，分钟线=YYYYMMDDHHMM）
      trend_period: 分时出击趋势线周期

    返回: list[dict]，每根 bar 一行，含全部 30 列基础字段（不含量能指标）
    """
    n = len(closes)

    # ── 指标计算 ──
    e12 = calc_expma(closes, EXPMA_FAST)
    e50 = calc_expma(closes, EXPMA_SLOW)
    dif, dea, hist = calc_macd(closes)
    trend = calc_trend_line(highs, lows, closes, trend_period)
    bb_ma, bb_red = calc_bull_bear_line(closes)
    cci_vals = calc_cci(highs, lows, closes)
    cci_extreme = detect_cci_extreme(cci_vals)
    cci_div = detect_cci_divergence(cci_vals, closes)
    buy_idx, sell_idx = detect_star_signals(trend)
    golden_idx, death_idx = detect_expma_cross(e12, e50)
    red_up_idx, red_down_idx = detect_red_line_cross(closes, bb_red)

    # ── 均线 ──
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    ma120 = calc_ma(closes, 120)
    ma250 = calc_ma(closes, 250)

    # ── 信号集合转 set，O(1) 查表 ──
    buy_set = set(buy_idx)
    sell_set = set(sell_idx)
    golden_set = set(golden_idx)
    death_set = set(death_idx)
    red_up_set = set(red_up_idx)
    red_down_set = set(red_down_idx)

    # ── 逐行组装 ──
    results = []
    for i in range(n):
        ext_label, retreat_label, div_label = _format_cci_labels(
            cci_extreme[i], cci_div[i])

        ts = timestamps[i]
        row = {
            'timestamp': ts,
            'date': int(str(ts)[:8]) if len(str(ts)) >= 8 else ts,
            'open': round(float(opens[i]), 4),
            'high': round(float(highs[i]), 4),
            'low': round(float(lows[i]), 4),
            'close': round(float(closes[i]), 4),
            'expma12': round(e12[i], 4),
            'expma50': round(e50[i], 4),
            'macd_dif': round(dif[i], 6),
            'macd_dea': round(dea[i], 6),
            'macd_hist': round(hist[i], 6),
            'trend_line': round(trend[i], 2),
            'bb_ma221': bb_ma[i],
            'bb_red_line': bb_red[i],
            'red_line_cross': ('突破红线' if i in red_up_set else
                               ('跌破红线' if i in red_down_set else '')),
            'buy_signal': '★买' if i in buy_set else '',
            'sell_signal': '★卖' if i in sell_set else '',
            'expma_cross': '金叉' if i in golden_set else ('死叉' if i in death_set else ''),
            'cci': round(cci_vals[i], 1),
            'cci_extreme': ext_label,
            'cci_retreat': retreat_label,
            'cci_divergence': div_label,
            'ma5': ma5[i],
            'ma10': ma10[i],
            'ma20': ma20[i],
            'ma60': ma60[i],
            'ma120': ma120[i],
            'ma250': ma250[i],
            'volume': vols[i],
            'amount': amts[i],
        }
        results.append(row)
    return results


# ========== 公开全量计算接口 ==========

def calc_daily_all(filepath):
    """日线全量信号计算（薄壳，核心逻辑在 _calc_signals_from_arrays）"""
    bars = read_bars(filepath)
    if not bars:
        return []

    p_o = [bar[1] / DAY_PRICE_FACTOR for bar in bars]
    p_h = [bar[2] / DAY_PRICE_FACTOR for bar in bars]
    p_l = [bar[3] / DAY_PRICE_FACTOR for bar in bars]
    p_c = [bar[4] / DAY_PRICE_FACTOR for bar in bars]
    vols = [bar[6] for bar in bars]
    amts = [bar[5] for bar in bars]
    timestamps = [bar[0] for bar in bars]

    return _calc_signals_from_arrays(
        p_o, p_h, p_l, p_c, vols, amts, timestamps, TREND_PERIOD_DAILY)


def calc_min_all(filepath, period='min30', trend_period=None):
    """
    分钟线全量信号计算（lc5/lc15/lc30/lc60通用）
    period: 周期标识，用于选择趋势计算参数
    trend_period: 可选，覆盖自动选择
    """
    if trend_period is None:
        trend_period = TREND_PERIOD_MIN_SHORT if period in ('min1', 'min5', 'min15') else TREND_PERIOD_MIN

    bars = read_bars(filepath)
    if not bars:
        return []

    opens = [float(bar[1]) for bar in bars]
    highs = [float(bar[2]) for bar in bars]
    lows = [float(bar[3]) for bar in bars]
    closes = [float(bar[4]) for bar in bars]
    vols = [bar[6] for bar in bars]
    amts = [bar[5] for bar in bars]
    timestamps = [int(str(bar[0]) + str(bar[7]).zfill(4)) for bar in bars]

    return _calc_signals_from_arrays(
        opens, highs, lows, closes, vols, amts, timestamps, trend_period)


def calc_min1_all(filepath, period='min1'):
    """1分钟线全量信号计算（使用 lc1 格式专用读取器）"""
    bars = read_bars_lc1(filepath)
    if not bars:
        return []

    opens = [float(bar[1]) for bar in bars]
    highs = [float(bar[2]) for bar in bars]
    lows = [float(bar[3]) for bar in bars]
    closes = [float(bar[4]) for bar in bars]
    vols = [bar[6] for bar in bars]
    amts = [bar[5] for bar in bars]
    timestamps = [bar[0] for bar in bars]

    return _calc_signals_from_arrays(
        opens, highs, lows, closes, vols, amts, timestamps, TREND_PERIOD_MIN_SHORT)


# ========== 量能指标后处理 ==========

def _rolling_min_mask(values, window):
    """
    O(n) 滚动窗口最小值检测。
    返回 bool 数组: values[i] 是 values[i-window+1:i+1] 中的最小值时为 True。
    """
    n = len(values)
    result = [False] * n
    dq = deque()
    for i in range(n):
        while dq and dq[0] <= i - window:
            dq.popleft()
        while dq and values[dq[-1]] >= values[i]:
            dq.pop()
        dq.append(i)
        if dq[0] == i:
            result[i] = True
    return result


def calc_volume_indicators(rows):
    """
    量能指标后处理 — 在基础信号计算之后执行。

    每行新增 11 列量能指标:
      vol_ma5 / vol_ma60  — 均量线
      vr5 / vr60           — 量比（当前量/均量）
      vol_llv100           — 百日地量标志（近5根内出现LLV100）
      vol_llv10            — 十日地量标志
      vol_堆               — 地量堆（近5根中十日地量>=3次）
      vol_缩50             — 缩量过半（vr5<0.5）
      vol_突放             — 放量突破（C>前高 + vr5>1.5）
      vol_梯度升/梯度降    — 成交量连续3日递增/递减
    """
    n = len(rows)
    if n == 0:
        return rows

    vols = [float(r.get('volume', 0) or 0) for r in rows]

    # 均量线
    vol_ma5_list = calc_ma(vols, 5)
    vol_ma60_list = calc_ma(vols, 60)

    # 量比
    for i in range(n):
        rows[i]['vol_ma5'] = round(vol_ma5_list[i], 0)
        rows[i]['vol_ma60'] = round(vol_ma60_list[i], 0)
        v = vols[i]
        ma5 = vol_ma5_list[i]
        ma60 = vol_ma60_list[i]
        rows[i]['vr5'] = round(v / ma5, 2) if ma5 > 0 else 1.0
        rows[i]['vr60'] = round(v / ma60, 2) if ma60 > 0 else 1.0

    # 百日地量 / 十日地量 — O(n) 预计算 + O(1) 查表
    is_llv100 = _rolling_min_mask(vols, 100)
    is_llv10 = _rolling_min_mask(vols, 10)

    for i in range(n):
        if vols[i] <= 0:
            rows[i]['vol_llv100'] = 0
            rows[i]['vol_llv10'] = 0
            continue

        # 近5根内是否有百日低点
        llv100_flag = 0
        for j in range(max(0, i - 4), i + 1):
            if is_llv100[j] and vols[j] > 0:
                llv100_flag = 1
                break
        rows[i]['vol_llv100'] = llv100_flag

        llv10_flag = 0
        for j in range(max(0, i - 4), i + 1):
            if is_llv10[j] and vols[j] > 0:
                llv10_flag = 1
                break
        rows[i]['vol_llv10'] = llv10_flag

    # 地量堆: 含当前位置的后6根中十日地量 >= 3 次
    for i in range(n):
        end = min(n, i + 6)
        cnt = sum(1 for j in range(i, end) if rows[j].get('vol_llv10', 0) == 1)
        rows[i]['vol_堆'] = 1 if cnt >= 3 else 0

    # 缩量过半
    for i in range(n):
        vr5 = rows[i].get('vr5', 1.0)
        rows[i]['vol_缩50'] = 1 if (vr5 is not None and float(vr5) < 0.5) else 0

    # 放量突破: C > 前5根最高 + vr5 > 1.5
    for i in range(n):
        if i < 5:
            rows[i]['vol_突放'] = 0
            continue
        c_cur = float(rows[i].get('close', 0) or 0)
        h_prev = max(float(rows[j].get('high', 0) or 0) for j in range(i - 5, i))
        vr5_val = float(rows[i].get('vr5', 1.0) or 1.0)
        rows[i]['vol_突放'] = 1 if c_cur > h_prev and vr5_val > 1.5 else 0

    # 梯度放量 / 梯度缩量（连续3根递增/递减）
    for i in range(2, n):
        v0, v1, v2 = vols[i - 2], vols[i - 1], vols[i]
        rows[i]['vol_梯度升'] = 1 if v0 < v1 < v2 else 0
        rows[i]['vol_梯度降'] = 1 if v0 > v1 > v2 else 0
    for i in range(min(2, n)):
        rows[i]['vol_梯度升'] = 0
        rows[i]['vol_梯度降'] = 0

    return rows


# ========== CSV 读写 ==========

SIGNAL_HEADERS = [
    'timestamp', 'date', 'open', 'high', 'low', 'close',
    'expma12', 'expma50', 'macd_dif', 'macd_dea', 'macd_hist',
    'trend_line', 'bb_ma221', 'bb_red_line', 'red_line_cross',
    'buy_signal', 'sell_signal', 'expma_cross',
    'cci', 'cci_extreme', 'cci_retreat', 'cci_divergence',
    'ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma250',
    'volume', 'amount',
    'vol_ma5', 'vol_ma60', 'vr5', 'vr60',
    'vol_llv100', 'vol_llv10', 'vol_堆', 'vol_缩50',
    'vol_突放', 'vol_梯度升', 'vol_梯度降',
]

# 向后兼容别名
DAILY_HEADERS = SIGNAL_HEADERS
MIN_HEADERS = SIGNAL_HEADERS


def write_csv(filepath, rows, headers, mode='w'):
    """写入 CSV。mode='w' 覆盖，mode='a' 追加（不写表头）"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, mode, newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if mode == 'w':
            writer.writeheader()
        writer.writerows(rows)


def append_csv(filepath, rows, headers):
    """追加 CSV（兼容旧接口，内部调 write_csv）"""
    write_csv(filepath, rows, headers, mode='a')


def read_csv(filepath):
    """读取 CSV 返回 list[dict]"""
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return list(reader)


# ========== 快照生成 ==========

def build_snapshot(code, market, periods_data, name=None):
    """
    构建最新状态快照
    code: 股票代码
    market: 市场
    periods_data: dict, key=period, value=list[dict]信号数据
    name: 中文名称（可选），来自 config.NAME_MAP
    """
    snapshot = {'name': name or ''}

    for period, rows in periods_data.items():
        if not rows:
            snapshot[period] = None
            continue

        last = rows[-1]
        # 一次性类型标准化，避免后续到处 float()
        c = float(last.get('close', 0) or 0)
        e12 = float(last.get('expma12', 0) or 0)
        e50 = float(last.get('expma50', 0) or 0)
        dif_v = float(last.get('macd_dif', 0) or 0)
        dea_v = float(last.get('macd_dea', 0) or 0)
        bb_red = float(last.get('bb_red_line', 0) or 0)

        info = {
            'expma12': round(e12, 2),
            'expma50': round(e50, 2),
            'expma_status': '多头' if e12 > e50 else '空头',
            'macd_dif': round(dif_v, 4),
            'macd_dea': round(dea_v, 4),
            'macd_status': '多头' if (dif_v > 0 and dea_v > 0) else '空头',
            'trend_line': last.get('trend_line', ''),
            'signal': (last.get('buy_signal') or last.get('sell_signal') or '无'),
            'expma_cross': last.get('expma_cross', ''),
        }

        if period == 'daily':
            info['date'] = str(last.get('date', ''))
            info['close'] = c
            info['bb_ma221'] = last.get('bb_ma221', '')
            info['bb_red_line'] = bb_red
            info['red_line_distance'] = round(
                (c - bb_red) / bb_red * 100, 2) if bb_red else 0
            info['red_line_cross'] = last.get('red_line_cross', '')
        else:
            # 分钟线：附加近50根信号统计
            lookback = min(50, len(rows))
            recent = rows[-lookback:]
            info['buy_count_50'] = sum(1 for r in recent if r.get('buy_signal'))
            info['sell_count_50'] = sum(1 for r in recent if r.get('sell_signal'))
            info['death_cross_count_50'] = sum(
                1 for r in recent if r.get('expma_cross') == '死叉')

        snapshot[period] = info

    return snapshot


def save_snapshot(filepath, all_snapshots):
    """保存 latest.json"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    data = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'stocks': all_snapshots,
    }
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ========== 文件路径工具 ==========

def get_data_path(code, market, period):
    """获取数据文件路径"""
    base = r'D:\quantify-per'
    ext_map = {
        'daily': '.day', 'min1': '.lc1', 'min5': '.lc5',
        'min15': '.lc15', 'min30': '.lc30', 'min60': '.lc60',
    }
    dir_map = {
        'daily': 'lday', 'min1': 'one', 'min5': 'five',
        'min15': 'fifteen', 'min30': 'thirty', 'min60': 'sixty',
    }
    ext = ext_map.get(period, '.day')
    dir_name = dir_map.get(period, 'lday')
    return os.path.join(base, dir_name, market, f"{code}{ext}")


def get_signal_path(code, period, fmt='csv'):
    """获取信号输出文件路径"""
    base = r'D:\quantify-per\signals\tracking'
    period_file = {
        'daily': 'daily_signals', 'min1': 'min1_signals',
        'min5': 'min5_signals', 'min15': 'min15_signals',
        'min30': 'min30_signals', 'min60': 'min60_signals',
    }
    fname = period_file.get(period, period) + '.' + fmt
    return os.path.join(base, code, fname)

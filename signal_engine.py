# -*- coding: utf-8 -*-
"""
信号计算引擎 v1.1
计算 EXPMA、MACD、分时出击趋势线、★买★卖信号
支持日线和分钟线数据（通达信二进制格式）

日线: 价格 = raw / 1000
分钟线: 价格 = raw / 10000 (v4.4 升级，之前为 /100)
"""

import struct
import os
import csv
import json
from datetime import datetime

# ========== 常量 ==========

DAY_PRICE_FACTOR = 1000  # 日线价格编码: price * 1000
MIN_PRICE_FACTOR = 10000 # 分钟线价格编码: price * 10000 (v4.4, 精度0.0001元)

# 分时出击参数
TREND_PERIOD_DAILY = 55   # 日线 LLV/HHV 周期
TREND_PERIOD_MIN = 55     # 30-60分钟线 LLV/HHV 周期
TREND_PERIOD_MIN_SHORT = 40  # 5-15分钟线 LLV/HHV 周期

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
BULL_BEAR_PERIOD = 221  # 年线周期
BULL_BEAR_STD_MULT = 3  # 标准差倍数

# CCI 参数
CCI_PERIOD = 14         # CCI 标准周期
CCI_EXTREME_LEVELS = [200, 250, 300]  # 极限值等级(不同品种敏感度不同)


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
            # 解码日期和时间
            year = date_num // 2048 + 2004
            month = (date_num % 2048) // 100
            day = (date_num % 2048) % 100
            hour = minutes // 60
            minute = minutes % 60
            timestamp = year * 100000000 + month * 1000000 + day * 10000 + hour * 100 + minute
            # 价格 × 10000 转为整数（与lc5信号格式对齐）
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
    """简单移动平均 MA(X, N): 最近N根K线收盘价之和/N"""
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
    MA_TP = SMA(TP, N)  (简单移动平均)
    MD = mean(|TP_i - MA_tp| for i in window)  (平均绝对偏差)
    CCI = (TP - MA_TP) / (0.015 * MD)
    
    返回: cci 列表
    """
    n = len(closes)
    tp = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
    
    # MA of TP (SMA)
    ma_tp = []
    for i in range(n):
        start = max(0, i - period + 1)
        ma_tp.append(sum(tp[start:i+1]) / (i + 1 - start))
    
    # Mean Deviation
    md = []
    for i in range(n):
        start = max(0, i - period + 1)
        if i < period - 1:
            md.append(0.0)
        else:
            deviations = [abs(tp[j] - ma_tp[i]) for j in range(start, i + 1)]
            md.append(sum(deviations) / len(deviations))
    
    # CCI
    cci = []
    for i in range(n):
        if md[i] == 0:
            cci.append(0.0)
        else:
            cci.append((tp[i] - ma_tp[i]) / (0.015 * md[i]))
    
    return cci


def detect_cci_extreme(cci, levels=CCI_EXTREME_LEVELS):
    """
    CCI 极限值检测:
    返回: dict, 每个bar记录极限值状态
      'extreme_high': 达到的最高正极限等级 (200/250/300), 0=无
      'extreme_low':  达到的最低负极限等级 (-200/-250/-300), 0=无
      'from_extreme_high': 从正极限回落(前一天>=level, 当天<level)
      'from_extreme_low':  从负极限反弹(前一天<=-level, 当天>-level)
    """
    n = len(cci)
    result = []
    for i in range(n):
        val = cci[i]
        
        # 当前达到的极限等级
        eh = 0
        el = 0
        for lvl in levels:
            if val >= lvl:
                eh = lvl
            if val <= -lvl:
                el = -lvl
        
        # 从极限值回撤检测 (右侧信号: 出了极限值之后再看分时出击)
        fe_h = {}   # {level: bool} 从哪个级别回落
        fe_l = {}
        if i > 0:
            prev = cci[i-1]
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
    
    简化版: 在最近 lookback 根 bar 内:
    - 如果出现 CCI 极限值后, CCI 开始下降但价格继续涨 → 正背驰
    - 如果出现 CCI 负极限值后, CCI 开始回升但价格继续跌 → 负背驰
    
    返回: list[dict], 每个bar一个dict
      'pos_div': 正背驰(True/False) — 价格创新高, CCI走弱
      'neg_div': 负背驰(True/False) — 价格创新低, CCI走强
    """
    n = len(cci)
    result = [{'pos_div': False, 'neg_div': False} for _ in range(n)]
    
    for i in range(lookback, n):
        # 找最近的 CCI 极限高点
        recent_max_cci_idx = None
        recent_min_cci_idx = None
        for j in range(i - lookback, i + 1):
            if abs(cci[j]) >= CCI_EXTREME_LEVELS[0]:  # 至少到过200
                if cci[j] > 0 and (recent_max_cci_idx is None or cci[j] > cci[recent_max_cci_idx]):
                    recent_max_cci_idx = j
                if cci[j] < 0 and (recent_min_cci_idx is None or cci[j] < cci[recent_min_cci_idx]):
                    recent_min_cci_idx = j
        
        # 正背驰检查: 有过CCI高点, 之后价格更高但CCI更低
        if recent_max_cci_idx is not None and recent_max_cci_idx < i:
            peak_price = closes[recent_max_cci_idx]
            peak_cci_val = cci[recent_max_cci_idx]
            current_price = closes[i]
            current_cci_val = cci[i]
            
            # 价格创了新高但CCI从高点明显回落
            if current_price > peak_price and current_cci_val < peak_cci_val * 0.7:
                result[i]['pos_div'] = True
        
        # 负背驰检查: 有过CCI低点, 之后价格更低但CCI走高
        if recent_min_cci_idx is not None and recent_min_cci_idx < i:
            trough_price = closes[recent_min_cci_idx]
            trough_cci_val = cci[recent_min_cci_idx]
            current_price = closes[i]
            current_cci_val = cci[i]
            
            if current_price < trough_price and current_cci_val > trough_cci_val * 1.5:
                result[i]['neg_div'] = True
    
    return result


def calc_bull_bear_line(closes, period=BULL_BEAR_PERIOD, std_mult=BULL_BEAR_STD_MULT):
    """
    牛熊红线 = MA(CLOSE, N) + M * STD(CLOSE, N)
    通达信: M221:=MA(CLOSE,221); 牛熊红线:M221+3*STD(CLOSE,221)
    本质: 价格过去N个bar的极端上边界（99.7%分位）
    返回: (ma_line, red_line) 两个列表
    """
    n = len(closes)
    ma_line = []
    red_line = []
    for i in range(n):
        start = max(0, i - period + 1)
        window = [float(closes[j]) for j in range(start, i + 1)]
        m = sum(window) / len(window)
        variance = sum((x - m) ** 2 for x in window) / len(window)
        std = variance ** 0.5
        ma_line.append(round(m, 4))
        red_line.append(round(m + std_mult * std, 4))
    return ma_line, red_line


def detect_red_line_cross(closes, red_line):
    """
    检测价格穿越牛熊红线
    返回: (break_above_indices, break_below_indices)
    break_above: 前一天收盘<=红线, 当天收盘>红线（向上穿越）
    break_below: 前一天收盘>=红线, 当天收盘<红线（向下跌破）
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
    """
    n = len(closes)
    llv = []
    hhv = []
    for i in range(n):
        start = max(0, i - period + 1)
        llv.append(float(min(lows[start:i + 1])))
        hhv.append(float(max(highs[start:i + 1])))

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
    in_high_zone = False  # 是否已进入过高位区(>=90)

    for i in range(1, len(trend_line)):
        # ★买: 上穿 11
        if trend_line[i - 1] <= 11 < trend_line[i]:
            buy_indices.append(i)

        # 高位区状态跟踪
        if trend_line[i] >= 90:
            in_high_zone = True

        # ★卖: 下穿 90 (且之前到过90+)
        if in_high_zone and trend_line[i - 1] >= 90 > trend_line[i]:
            sell_indices.append(i)
            in_high_zone = False  # 重置, 需要再次上穿90才能再触发

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


def count_signals_in_range(signal_indices, end_idx, lookback=50):
    """统计 end_idx 前 lookback 根 bar 内的信号次数"""
    start = max(0, end_idx - lookback)
    return sum(1 for idx in signal_indices if start < idx <= end_idx)


# ========== 全量信号计算 ==========

def calc_daily_all(filepath):
    """
    日线全量信号计算
    返回: list[dict] 每根 bar 一行
    """
    bars = read_bars(filepath)
    if not bars:
        return []

    n = len(bars)
    dates = [bar[0] for bar in bars]
    opens = [bar[1] for bar in bars]
    highs = [bar[2] for bar in bars]
    lows = [bar[3] for bar in bars]
    closes = [bar[4] for bar in bars]
    amts = [bar[5] for bar in bars]   # bar[5]=amount (pytdx <IIIIIfII)
    vols = [bar[6] for bar in bars]   # bar[6]=volume (pytdx <IIIIIfII)

    # 转实际价格
    p_o = [o / DAY_PRICE_FACTOR for o in opens]
    p_h = [h / DAY_PRICE_FACTOR for h in highs]
    p_l = [l / DAY_PRICE_FACTOR for l in lows]
    p_c = [c / DAY_PRICE_FACTOR for c in closes]

    # 指标
    e12 = calc_expma(p_c, EXPMA_FAST)
    e50 = calc_expma(p_c, EXPMA_SLOW)
    dif, dea, hist = calc_macd(p_c)
    trend = calc_trend_line(highs, lows, closes, TREND_PERIOD_DAILY)
    bb_ma, bb_red = calc_bull_bear_line(p_c)
    cci_vals = calc_cci(p_h, p_l, p_c)
    cci_extreme = detect_cci_extreme(cci_vals)
    cci_div = detect_cci_divergence(cci_vals, p_c)
    buy_idx, sell_idx = detect_star_signals(trend)
    golden_idx, death_idx = detect_expma_cross(e12, e50)
    red_up_idx, red_down_idx = detect_red_line_cross(p_c, bb_red)

    # 简单移动平均 (judge_trend + signal_quality 直接读取, 避免重复计算)
    ma5 = calc_ma(p_c, 5)
    ma10 = calc_ma(p_c, 10)
    ma20 = calc_ma(p_c, 20)
    ma60 = calc_ma(p_c, 60)
    ma120 = calc_ma(p_c, 120)
    ma250 = calc_ma(p_c, 250)

    results = []
    for i in range(n):
        # CCI 极限值状态文字化
        eh = cci_extreme[i]['extreme_high']
        el = cci_extreme[i]['extreme_low']
        fe_h = cci_extreme[i]['from_extreme_high']
        fe_l = cci_extreme[i]['from_extreme_low']

        ext_label = ''
        if eh > 0:
            ext_label = 'CCI+%d' % eh
        elif el < 0:
            ext_label = 'CCI%d' % el

        # 从极限回撤
        retreat_label = ''
        max_retreat_lvl = max(fe_h.keys()) if fe_h else 0
        if max_retreat_lvl > 0:
            retreat_label = '回撤+%d' % max_retreat_lvl

        # 背驰
        div_label = ''
        if cci_div[i]['pos_div']:
            div_label = '顶背驰'
        elif cci_div[i]['neg_div']:
            div_label = '底背驰'

        row = {
            'timestamp': dates[i],
            'date': dates[i],
            'open': round(p_o[i], 4),
            'high': round(p_h[i], 4),
            'low': round(p_l[i], 4),
            'close': round(p_c[i], 4),
            'expma12': round(e12[i], 4),
            'expma50': round(e50[i], 4),
            'macd_dif': round(dif[i], 6),
            'macd_dea': round(dea[i], 6),
            'macd_hist': round(hist[i], 6),
            'trend_line': round(trend[i], 2),
            'bb_ma221': bb_ma[i],
            'bb_red_line': bb_red[i],
            'red_line_cross': '突破红线' if i in red_up_idx else ('跌破红线' if i in red_down_idx else ''),
            'buy_signal': '★买' if i in buy_idx else '',
            'sell_signal': '★卖' if i in sell_idx else '',
            'expma_cross': '金叉' if i in golden_idx else ('死叉' if i in death_idx else ''),
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


def calc_min_all(filepath, period='min30', trend_period=None):
    """
    分钟线全量信号计算（使用原始值）
    返回: list[dict]
    period: 周期标识，用于选择趋势计算参数
    trend_period: 可选，覆盖自动选择
    """
    if trend_period is None:
        trend_period = TREND_PERIOD_MIN_SHORT if period in ('min1', 'min5', 'min15') else TREND_PERIOD_MIN
    bars = read_bars(filepath)
    if not bars:
        return []

    n = len(bars)
    # 分钟线: bar[0]=日期(YYYYMMDD), bar[7]=时间(HHMM, 如935=09:35)
    # 合并为 YYYYMMDDHHMM 便于精确定位
    timestamps = [int(str(bar[0]) + str(bar[7]).zfill(4)) for bar in bars]
    opens = [float(bar[1]) for bar in bars]
    highs = [float(bar[2]) for bar in bars]
    lows = [float(bar[3]) for bar in bars]
    closes = [float(bar[4]) for bar in bars]
    vols = [bar[6] for bar in bars]   # bar[6]=volume (pytdx)
    amts = [bar[5] for bar in bars]   # bar[5]=amount (pytdx)
    e12 = calc_expma(closes, EXPMA_FAST)
    e50 = calc_expma(closes, EXPMA_SLOW)
    dif, dea, hist = calc_macd(closes)
    trend = calc_trend_line(highs, lows, closes, trend_period)
    cci_vals = calc_cci(highs, lows, closes)
    cci_extreme = detect_cci_extreme(cci_vals)
    cci_div = detect_cci_divergence(cci_vals, closes)
    buy_idx, sell_idx = detect_star_signals(trend)
    golden_idx, death_idx = detect_expma_cross(e12, e50)

    # 短周期MA
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    ma120 = calc_ma(closes, 120)
    ma250 = calc_ma(closes, 250)

    # 布林带 / 牛熊红线
    bb_ma, bb_red = calc_bull_bear_line(closes)
    red_up_idx, red_down_idx = detect_red_line_cross(closes, bb_red)

    results = []
    for i in range(n):
        # CCI 状态文字化
        eh = cci_extreme[i]['extreme_high']
        el = cci_extreme[i]['extreme_low']
        fe_h = cci_extreme[i]['from_extreme_high']

        ext_label = ''
        if eh > 0:
            ext_label = '+%d' % eh
        elif el < 0:
            ext_label = '%d' % el

        retreat_label = ''
        if fe_h:
            max_retreat_lvl = max(fe_h.keys())
            retreat_label = '回撤+%d' % max_retreat_lvl

        div_label = ''
        if cci_div[i]['pos_div']:
            div_label = '顶背驰'
        elif cci_div[i]['neg_div']:
            div_label = '底背驰'

        ts = timestamps[i]
        row = {
            'timestamp': ts,
            'date': int(str(ts)[:8]) if len(str(ts)) >= 8 else ts,
            'open': float(opens[i]),
            'high': float(highs[i]),
            'low': float(lows[i]),
            'close': float(closes[i]),
            'expma12': e12[i],
            'expma50': e50[i],
            'macd_dif': dif[i],
            'macd_dea': dea[i],
            'macd_hist': hist[i],
            'trend_line': round(trend[i], 2),
            'bb_ma221': bb_ma[i],
            'bb_red_line': bb_red[i],
            'red_line_cross': '突破红线' if i in red_up_idx else ('跌破红线' if i in red_down_idx else ''),
            'buy_signal': '★买' if i in buy_idx else '',
            'sell_signal': '★卖' if i in sell_idx else '',
            'expma_cross': '金叉' if i in golden_idx else ('死叉' if i in death_idx else ''),
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


def calc_min1_all(filepath, period='min1'):
    """
    1分钟线全量信号计算（使用 lc1 格式专用读取器）
    返回: list[dict]
    """
    bars = read_bars_lc1(filepath)  # 使用 lc1 专用解析器
    trend_period = TREND_PERIOD_MIN_SHORT  # min1 永远用短周期
    if not bars:
        return []

    n = len(bars)
    # lc1 数据已预处理: bar[0]=timestamp(YYYYMMDDHHMM), bar[1..4]=价格(×10000)
    timestamps = [bar[0] for bar in bars]
    opens = [float(bar[1]) for bar in bars]
    highs = [float(bar[2]) for bar in bars]
    lows = [float(bar[3]) for bar in bars]
    closes = [float(bar[4]) for bar in bars]
    vols = [bar[6] for bar in bars]
    amts = [bar[5] for bar in bars]
    
    e12 = calc_expma(closes, EXPMA_FAST)
    e50 = calc_expma(closes, EXPMA_SLOW)
    dif, dea, hist = calc_macd(closes)
    trend = calc_trend_line(highs, lows, closes, trend_period)
    cci_vals = calc_cci(highs, lows, closes)
    cci_extreme = detect_cci_extreme(cci_vals)
    cci_div = detect_cci_divergence(cci_vals, closes)
    buy_idx, sell_idx = detect_star_signals(trend)
    golden_idx, death_idx = detect_expma_cross(e12, e50)

    # MA 系列
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    ma120 = calc_ma(closes, 120)
    ma250 = calc_ma(closes, 250)

    # 布林带 / 牛熊红线
    bb_ma, bb_red = calc_bull_bear_line(closes)
    red_up_idx, red_down_idx = detect_red_line_cross(closes, bb_red)

    results = []
    for i in range(n):
        eh = cci_extreme[i]['extreme_high']
        el = cci_extreme[i]['extreme_low']
        fe_h = cci_extreme[i]['from_extreme_high']

        ext_label = ''
        if eh > 0:
            ext_label = '+%d' % eh
        elif el < 0:
            ext_label = '%d' % el

        retreat_label = ''
        if fe_h:
            max_retreat_lvl = max(fe_h.keys())
            retreat_label = '回撤+%d' % max_retreat_lvl

        div_label = ''
        if cci_div[i]['pos_div']:
            div_label = '顶背驰'
        elif cci_div[i]['neg_div']:
            div_label = '底背驰'

        ts = timestamps[i]
        row = {
            'timestamp': ts,
            'date': int(str(ts)[:8]) if len(str(ts)) >= 8 else ts,
            'open': float(opens[i]),
            'high': float(highs[i]),
            'low': float(lows[i]),
            'close': float(closes[i]),
            'expma12': e12[i],
            'expma50': e50[i],
            'macd_dif': dif[i],
            'macd_dea': dea[i],
            'macd_hist': hist[i],
            'trend_line': round(trend[i], 2),
            'bb_ma221': bb_ma[i],
            'bb_red_line': bb_red[i],
            'red_line_cross': '突破红线' if i in red_up_idx else ('跌破红线' if i in red_down_idx else ''),
            'buy_signal': '★买' if i in buy_idx else '',
            'sell_signal': '★卖' if i in sell_idx else '',
            'expma_cross': '金叉' if i in golden_idx else ('死叉' if i in death_idx else ''),
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


# ========== CSV 读写 ==========

DAILY_HEADERS = [
    'timestamp', 'date', 'open', 'high', 'low', 'close',
    'expma12', 'expma50', 'macd_dif', 'macd_dea', 'macd_hist',
    'trend_line', 'bb_ma221', 'bb_red_line', 'red_line_cross',
    'buy_signal', 'sell_signal', 'expma_cross',
    'cci', 'cci_extreme', 'cci_retreat', 'cci_divergence',
    'ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma250',
    'volume', 'amount'
]

MIN_HEADERS = [
    'timestamp', 'date', 'open', 'high', 'low', 'close',
    'expma12', 'expma50', 'macd_dif', 'macd_dea', 'macd_hist',
    'trend_line', 'bb_ma221', 'bb_red_line', 'red_line_cross',
    'buy_signal', 'sell_signal', 'expma_cross',
    'cci', 'cci_extreme', 'cci_retreat', 'cci_divergence',
    'ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma250',
    'volume', 'amount'
]


def write_csv(filepath, rows, headers):
    """写入 CSV（覆盖）"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def append_csv(filepath, rows, headers):
    """追加 CSV"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writerows(rows)


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
    periods_data: dict, key=period('daily'/'min30'/'min60'), value=list[dict]信号数据
    name: 中文名称（可选），来自 config.NAME_MAP
    """
    snapshot = {
        'name': name or '',
    }

    for period, rows in periods_data.items():
        if not rows:
            snapshot[period] = None
            continue

        last = rows[-1]
        info = {}

        if period == 'daily':
            info['date'] = str(last['date'])
            info['close'] = last['close']
            info['expma12'] = last['expma12']
            info['expma50'] = last['expma50']
            info['expma_status'] = '多头' if float(last['expma12']) > float(last['expma50']) else '空头'
            info['macd_dif'] = last['macd_dif']
            info['macd_dea'] = last['macd_dea']
            info['macd_status'] = '多头' if (float(last['macd_dif']) > 0 and float(last['macd_dea']) > 0) else '空头'
            info['trend_line'] = last['trend_line']
            info['bb_ma221'] = last['bb_ma221']
            info['bb_red_line'] = last['bb_red_line']
            info['red_line_distance'] = round((float(last['close']) - float(last['bb_red_line'])) / float(last['bb_red_line']) * 100, 2)
            info['signal'] = last['buy_signal'] if last['buy_signal'] else (last['sell_signal'] if last['sell_signal'] else '无')
            info['red_line_cross'] = last['red_line_cross']
            info['expma_cross'] = last['expma_cross']
        else:
            # 分钟线
            info['expma12'] = round(float(last['expma12']), 2)
            info['expma50'] = round(float(last['expma50']), 2)
            info['expma_status'] = '多头' if float(last['expma12']) > float(last['expma50']) else '空头'
            info['macd_dif'] = round(float(last['macd_dif']), 4)
            info['macd_dea'] = round(float(last['macd_dea']), 4)
            info['macd_status'] = '多头' if (float(last['macd_dif']) > 0 and float(last['macd_dea']) > 0) else '空头'
            info['trend_line'] = last['trend_line']
            info['signal'] = last['buy_signal'] if last['buy_signal'] else (last['sell_signal'] if last['sell_signal'] else '无')
            info['expma_cross'] = last['expma_cross']

            # 统计最近50根bar内的信号次数
            n = len(rows)
            lookback = min(50, n)
            recent = rows[-lookback:]
            info['buy_count_50'] = sum(1 for r in recent if r['buy_signal'])
            info['sell_count_50'] = sum(1 for r in recent if r['sell_signal'])
            info['death_cross_count_50'] = sum(1 for r in recent if r['expma_cross'] == '死叉')

        snapshot[period] = info

    return snapshot


def save_snapshot(filepath, all_snapshots):
    """保存 latest.json"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    data = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'stocks': all_snapshots
    }
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ========== 文件路径工具 ==========

def get_data_path(code, market, period):
    """获取数据文件路径"""
    base = r'D:\quantify-per'
    ext_map = {
        'daily': '.day',
        'min1': '.lc1',
        'min5': '.lc5',
        'min15': '.lc15',
        'min30': '.lc30',
        'min60': '.lc60',
    }
    dir_map = {
        'daily': 'lday',
        'min1': 'one',
        'min5': 'five',
        'min15': 'fifteen',
        'min30': 'thirty',
        'min60': 'sixty',
    }
    ext = ext_map.get(period, '.day')
    dir_name = dir_map.get(period, 'lday')
    return os.path.join(base, dir_name, market, f"{code}{ext}")


def get_signal_path(code, period, fmt='csv'):
    """获取信号输出文件路径"""
    base = r'D:\quantify-per\signals\tracking'
    period_file = {
        'daily': 'daily_signals',
        'min1': 'min1_signals',
        'min5': 'min5_signals',
        'min15': 'min15_signals',
        'min30': 'min30_signals',
        'min60': 'min60_signals',
    }
    fname = period_file.get(period, period) + '.' + fmt
    return os.path.join(base, code, fname)

# -*- coding: utf-8 -*-
"""
周期循环引擎 v3.5 — Cycle Engine (三层架构版 + 完整共振链)

核心理念:
  不是先评分再做建议，而是先定位再评分。
  
  三层架构:
    第一层: 价格位置 — K线在 EXPMA 白线/黄线的什么位置？高位/中位/低位？
    第二层: 趋势方向 — 上涨/震荡/下跌？
    第三层: 循环适配 — 在已知位置+方向下，信号质量如何？
  
  共振链 (v3.5 新增):
    min5/min15 → min30/min60 (一级) — 同评分看上层金叉/死叉
    min30/min60 → daily (二级) — 同评分看日线金叉/死叉
  
  三个问题按顺序回答，每个标的状态自然浮现。

设计原则 (来自用户第一性原理):
  - 位置决定风险，方向决定策略，循环决定时机
  - 科创芯片: 高位加速区 + 上涨态 + 信号散乱 = 持有/减仓，不是买入
  - 恒生科技: 低位区 + 下跌态 + 买信号密集 = 触底酝酿，等转折信号
  - 不是循环好就值得操作，而是"位置+方向+循环"三者共振

作者: 小草 (EasyClaw) + WorkBuddy (v4 Pro)
日期: 2026-05-07 (WorkBuddy 共振链改造)
"""

import os
import sys
import csv
import json
import math
from itertools import permutations, combinations
from pathlib import Path

# ============================================================
# 配置
# ============================================================

BASE = Path('D:/quantify-per')
SNAPSHOT_DIR = BASE / 'signals' / 'tracking'
OUTPUT_PATH = BASE / 'signals' / 'tracking' / 'cycle_report.json'

PERIODS = ['min1', 'min5', 'min15', 'min30', 'min60', 'daily']
PERIOD_LABELS = {
    'min1': '1分钟', 'min5': '5分钟', 'min15': '15分钟', 'min30': '30分钟',
    'min60': '60分钟', 'daily': '日线',
}

# 回溯 K 线数量
KLINES_LOOKBACK = {
    'min1': 500, 'min5': 500, 'min15': 500, 'min30': 500,
    'min60': 500, 'daily': 0,
}


# ============================================================
# 数据读取
# ============================================================

def read_csv(code, period):
    fpath = SNAPSHOT_DIR / code / f'{period}_signals.csv'
    if not fpath.exists():
        return []
    with open(fpath, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    max_k = KLINES_LOOKBACK.get(period, 200)
    if max_k > 0 and len(rows) > max_k:
        rows = rows[-max_k:]
    return rows


def get_all_codes():
    codes = []
    for d in SNAPSHOT_DIR.iterdir():
        if d.is_dir() and (d / 'daily_signals.csv').exists():
            codes.append(d.name)
    return sorted(codes)


def get_name_map():
    try:
        sys.path.insert(0, str(BASE))
        from config import NAME_MAP
        return NAME_MAP
    except:
        return {}


# ============================================================
# 排列熵分析 — 检测趋势线的有序/无序状态（非预期解检测）
# ============================================================

def _permutation_entropy(values, m=3, delay=1):
    """
    排列熵：度量时间序列的有序/无序程度

    参数:
        values: list[float]，趋势线序列（0-100）
        m: 嵌入维度，3 即每3个点一组看排列图案
        delay: 延迟步长，默认1

    返回:
        pe: 归一化排列熵 (0~1)
            1 = 完全随机/无序（震荡无序）
            0 = 完全有序/有方向（趋势明确）
    """
    n = len(values)
    if n < m * 2:
        return 0.5  # 数据不足，返回中性值

    # 切分子序列
    sub_seqs = []
    for i in range(n - (m - 1) * delay):
        seq = tuple(values[i + j * delay] for j in range(m))
        sub_seqs.append(seq)

    # 对每个子序列按大小排序，得到排列图案
    patterns = []
    for seq in sub_seqs:
        sorted_idx = tuple(sorted(range(m), key=lambda x: seq[x]))
        patterns.append(sorted_idx)

    # 统计每种图案的频率
    total = len(patterns)
    freq = {}
    for p in patterns:
        freq[p] = freq.get(p, 0) + 1

    # 香农熵
    pe = 0.0
    for count in freq.values():
        f = count / total
        pe -= f * math.log(f) if f > 0 else 0

    # 归一化到 [0,1]
    pe /= math.log(math.factorial(m))

    return pe


def analyze_trend_pe(raw_rows, lookback=60):
    """
    对趋势线做排列熵分析，检测循环结构是否正在被打破

    取最新 lookback 根K线的 trend_line，前半段→pe_front，后半段→pe_back

    **熵值绝对值分级**（pe_back 当前值）:
      > 0.70 = 高熵区（无序震荡）
      0.40~0.70 = 中熵区（过渡态）
      < 0.40 = 低熵区（高度有序/方向明确）

    **结合变化趋势** pe_ratio = pe_back / pe_front:
      高熵 + 平稳 → 高熵震荡（无序无方向）
      高熵 + 降熵 → 开始降熵（刚从无序转向有序）
      中熵 + 降熵 → 持续性降熵（已经在走方向）
      中熵 + 升熵 → 熵增中（方向在退化回震荡）
      低熵 + 降熵 → 极致压缩（方向极度明确）
      低熵 + 平稳 → 低熵锁定（方向维持但不再加速）
      低熵 + 升熵 → 触底回升（有序结构开始松动）

    **新增输出字段**:
      pe_level: 熵值绝对水平 high/mid/low
      pe_phase: 所处阶段标签（开始降熵/持续降熵/熵增中等）
      pe_velocity: 变化烈度（急速/显著/温和/平稳）
      trending: 降熵中=True, 升熵中=False
    """
    if not raw_rows or len(raw_rows) < lookback:
        return {'pe_front': 0.5, 'pe_back': 0.5, 'pe_ratio': 1.0,
                'pe_level': 'mid', 'pe_phase': '数据不足', 'pe_velocity': '--',
                'trending': False, 'label': '数据不足'}

    recent = raw_rows[-lookback:]
    trend_vals = []
    for r in recent:
        tv = r.get('trend_line', None)
        if tv is not None and tv != '' and safe_float(tv) > 0:
            trend_vals.append(safe_float(tv))

    if len(trend_vals) < lookback * 0.5:
        return {'pe_front': 0.5, 'pe_back': 0.5, 'pe_ratio': 1.0,
                'pe_level': 'mid', 'pe_phase': '数据不足', 'pe_velocity': '--',
                'trending': False, 'label': '数据不足'}

    half = len(trend_vals) // 2
    front = trend_vals[:half]
    back = trend_vals[half:]

    pe_front = _permutation_entropy(front, m=3)
    pe_back = _permutation_entropy(back, m=3)
    pe_ratio = pe_back / pe_front if pe_front > 0 else 1.0

    # ── 趋势线方向（用于标注降熵朝向）──
    half_len = len(trend_vals) // 2
    front_tl = sum(trend_vals[:half_len]) / half_len if half_len > 0 else 0
    back_tl = sum(trend_vals[half_len:]) / (len(trend_vals) - half_len) if len(trend_vals) > half_len else 0
    tl_rising = back_tl > front_tl  # 趋势线在上升

    # ── 熵值绝对水平 ──
    if pe_back > 0.70:
        pe_level = 'high'
    elif pe_back < 0.40:
        pe_level = 'low'
    else:
        pe_level = 'mid'

    # ── 变化烈度 ──
    if pe_ratio < 0.70:
        pe_velocity = '急速'
        velo = 'rapid'
    elif pe_ratio < 0.85:
        pe_velocity = '显著'
        velo = 'strong'
    elif pe_ratio < 0.95:
        pe_velocity = '温和'
        velo = 'mild'
    elif pe_ratio <= 1.05:
        pe_velocity = '平稳'
        velo = 'stable'
    elif pe_ratio < 1.20:
        pe_velocity = '温和'
        velo = 'mild_rev'
    elif pe_ratio < 1.50:
        pe_velocity = '显著'
        velo = 'strong_rev'
    else:
        pe_velocity = '急速'
        velo = 'rapid_rev'

    # ── 阶段标签（状态机）──
    # 方向词：只有"结构突破"用上破/下破（"破"字已含方向），其余标签去掉箭头
    dir_word = '上破' if tl_rising else '下破'

    if pe_level == 'high' and pe_ratio < 0.95:
        pe_phase = '方向形成中'  # 高熵区开始降熵=方向刚从无序中显现
        trending = True
    elif pe_level == 'mid' and pe_ratio < 0.85:
        pe_phase = f'结构{dir_word}'  # 中熵区快速降熵=结构正在被打破
        trending = True
    elif pe_level == 'mid' and pe_ratio < 0.95:
        pe_phase = '趋势强化'  # 中熵区温和降熵=方向在持续
        trending = True
    elif pe_level == 'low' and pe_ratio < 0.85:
        pe_phase = '蓄力压缩'  # 低熵区继续降=蓄力到极致
        trending = True
    elif pe_level == 'low' and pe_ratio < 0.95:
        pe_phase = '趋势锁定'  # 低熵区温和降=方向锁定
        trending = True
    elif pe_level == 'low' and pe_ratio <= 1.05:
        pe_phase = '趋势延续'  # 低熵平稳=有序结构保持
        trending = False
    elif pe_level == 'low' and pe_ratio > 1.05:
        pe_phase = '趋势松动'  # 低熵回升=有序结构开始松动
        trending = False
    elif pe_level == 'mid' and pe_ratio > 1.05:
        pe_phase = '趋势衰减'  # 中熵升熵=方向在退化
        trending = False
    elif pe_level == 'high' and pe_ratio > 1.05:
        pe_phase = '无序放大'  # 高熵继续升=无序在扩散
        trending = False
    elif pe_level == 'high':
        pe_phase = '无序震荡'  # 高熵平稳=持续无序
        trending = False
    elif pe_level == 'mid':
        pe_phase = '方向不明'  # 中熵平稳=没有明确方向
        trending = False
    else:
        pe_phase = '过渡'
        trending = False

    # 短标签（用于总览表）— 直接用阶段名，不加emoji前缀，文字本身已经说明一切
    short_label = pe_phase

    return {
        'pe_front': round(pe_front, 4),
        'pe_back': round(pe_back, 4),
        'pe_ratio': round(pe_ratio, 4),
        'pe_level': pe_level,
        'pe_phase': pe_phase,
        'pe_velocity': velo,
        'trending': trending,
        'label': short_label,
        'tl_dir': '↑' if tl_rising else '↓',
    }


# ============================================================
# 第一层: 价格位置判断
# ============================================================

def judge_position(daily_rows):
    """
    判断日线价格在 EXPMA 体系中的位置
    
    用 EXPMA12(白线) 和 EXPMA50(黄线) 作为价格锚点:
      - 高位区: 价格在 EXPMA12 之上 (强势拉升/加速区)
      - 中位区: 价格在 EXPMA12 和 EXPMA50 之间 (正常波动区)
      - 低位区: 价格在 EXPMA50 之下 (弱势/超跌区)
    
    结合 BB 中轨辅助判断极端位置
    
    Returns:
        dict: {zone, label, description, risk_level}
    """
    if not daily_rows:
        return {'zone': 'unknown', 'label': '未知', 'description': '无日线数据', 'risk_level': 'high'}

    last = daily_rows[-1]
    close = safe_float(last.get('close', 0))
    expma12 = safe_float(last.get('expma12', 0))
    expma50 = safe_float(last.get('expma50', 0))
    bb_mid = safe_float(last.get('bb_ma221', 0))
    bb_red = safe_float(last.get('bb_red_line', 0))

    if not close or not expma12 or not expma50:
        return {'zone': 'unknown', 'label': '数据不足', 'description': '', 'risk_level': 'high'}

    # 计算价格相对位置
    # 用白线/黄线的价差做参考
    # 核心: 价格在黄线之下 = 低位，白线之上且偏离大 = 高位
    spread = abs(expma12 - expma50)
    if spread > 0.01:
        if expma12 > expma50:
            # 多头排列: 白线在上
            if close > expma12:
                relative_pos = 1.0 + (close - expma12) / spread
            elif close > expma50:
                relative_pos = (close - expma50) / (expma12 - expma50)
            else:
                relative_pos = -1.0  # 在黄线之下
        else:
            # 空头排列: 黄线在上，白线在下
            if close > expma50:
                relative_pos = (close - expma50) / spread + 1.5
            elif close > expma12:
                relative_pos = (close - expma12) / (expma50 - expma12) + 0.5
            else:
                relative_pos = -1.0  # 在白线之下
    else:
        relative_pos = 0

    # 偏离白线的幅度
    deviation_from_white = (close - expma12) / expma12 * 100 if expma12 else 0
    deviation_from_yellow = (close - expma50) / expma50 * 100 if expma50 else 0

    # 用绝对偏离判断，不看 relative_pos（空头排列时相对位置会翻转）
    if deviation_from_yellow > 8:
        zone = 'high'
        label = '高位加速区'
        description = f'价格远超EXPMA50(+{deviation_from_yellow:.0f}%)，加速拉升中'
        risk_level = 'critical'
    elif deviation_from_yellow > 3 and deviation_from_white > 0:
        zone = 'high'
        label = '强势高位'
        description = f'价格在EXPMA50上方(+{deviation_from_yellow:.0f}%)，强势运行'
        risk_level = 'medium'
    elif abs(deviation_from_white) < 1.5:
        zone = 'mid'
        label = '白线附近'
        description = '价格贴近EXPMA12，正常波动中枢'
        risk_level = 'low'
    elif deviation_from_yellow > -2:
        zone = 'mid'
        label = '中位区'
        description = f'价格在EXPMA12与EXPMA50之间'
        risk_level = 'low'
    elif deviation_from_yellow > -6:
        zone = 'low'
        label = '弱势低位'
        description = f'价格在EXPMA50下方({deviation_from_yellow:.0f}%)，弱势运行'
        risk_level = 'high'
    else:
        zone = 'low'
        label = '超跌深坑'
        description = f'价格远低于EXPMA50({deviation_from_yellow:.0f}%)，严重超跌'
        risk_level = 'critical'

    # BB 中轨辅助
    if bb_mid and zone == 'low' and close < bb_mid * 0.85:
        label = 'BB下轨超跌'
        description = '价格跌破BB中轨15%+，极度超跌区域'
        risk_level = 'critical'

    return {
        'zone': zone,
        'label': label,
        'description': description,
        'risk_level': risk_level,
        'close': close,
        'expma12': expma12,
        'expma50': expma50,
        'deviation_white_pct': round(deviation_from_white, 1),
        'deviation_yellow_pct': round(deviation_from_yellow, 1),
    }


# ============================================================
# 第二层: 趋势方向判断
# ============================================================

def judge_trend(code, daily_rows, daily_buy_level=0):
    """
    0-16 评分体系判断趋势方向 — 带日线闭环 + EXPMA

    EXPMA: 0~2分
      - 2: expma12 > expma50 (白线在上)
      - 1: 粘合（差距 < 股价×0.5%）
      - 0: expma12 < expma50 (黄线在上)

    MACD: 0~4分
      - dif_ratio = |DIF| / close
      - clearly_off = dif_ratio > 0.01
      - 4: clearly_off + dif>0 + dif>dea (0轴上,强势多头)
      - 3: dif>0 + dif>dea (0轴上金叉,未完全远离)
      - 2: dif<0 + dif>dea (0轴下金叉,弱势) 或 默认
      - 1: dif<dea (死叉,不论上下)
      - 0: clearly_off + dif<0 + dif<dea (0轴下,强势空头)

    MA排列: 0~6分
      - 链式递进: 从5开始检查连续 short>long 到第几级断裂
      - 链长0→0, 1→1, 2→2, 3→3, 4→4, 5→6(完美排列奖励)
      - 5>10>20>60>120>250 全部顺序 = 满分6分

    日线闭环: 0~4分（只计买侧, daily_buy_level >= 4.0=4分, >=3.5=3分, >=3.0=2分）
      - 只取★买侧的闭环质量, 不取max(buy,sell)

    总分 0~16 → 方向:
      13-16: bullish    10-12: bullish_bias
      7-9: neutral      4-6: bearish_bias
      0-3: bearish
    """
    if not daily_rows:
        return {'direction': 'unknown', 'label': '无数据', 'confidence': 0}

    last = daily_rows[-1]
    close = safe_float(last.get('close', 0))
    expma12 = safe_float(last.get('expma12', 0))
    expma50 = safe_float(last.get('expma50', 0))
    macd_dif = safe_float(last.get('macd_dif', 0))
    macd_dea = safe_float(last.get('macd_dea', 0))

    details = []

    # ── EXPMA: 0~2分 ──
    expma_score = 1  # 默认粘合
    if expma12 and expma50 and close > 0:
        expma_gap = abs(expma12 - expma50) / close
        if close > expma12 > expma50 and expma_gap > 0.005:
            expma_score = 2
            details.append('EXPMA多头')
        elif expma12 > expma50:
            # 白线>黄线但价格跌破白线，结构弱化
            expma_score = 1
            details.append('EXPMA偏多(价破白线)')
        elif expma12 < expma50 and expma_gap > 0.005:
            expma_score = 0
            details.append('EXPMA空头')
        else:
            expma_score = 1
            details.append('EXPMA粘合')
    else:
        details.append('EXPMA未知')

    # ── MACD: 0~4分 ──
    macd_score = 2
    if close > 0 and macd_dif is not None and macd_dea is not None:
        dif_ratio = abs(macd_dif) / close
        clearly_off = dif_ratio > 0.01

        if clearly_off and macd_dif > 0 and macd_dif > macd_dea:
            macd_score = 4
            details.append('MACD多头强')
        elif macd_dif > 0 and macd_dif > macd_dea:
            macd_score = 3
            details.append('MACD金叉(0轴上)')
        elif macd_dif < 0 and macd_dif > macd_dea:
            macd_score = 2
            details.append('MACD金叉(0轴下)')
        elif clearly_off and macd_dif < 0 and macd_dif < macd_dea:
            macd_score = 0
            details.append('MACD空头强')
        elif macd_dif < macd_dea:
            macd_score = 1
            details.append('MACD死叉')
        else:
            macd_score = 2
            details.append('MACD中性')
    else:
        details.append('MACD未知')

    # ── MA排列: 0~6分（直接从CSV读，不复算了）──
    chain_periods = [5, 10, 20, 60, 120, 250]
    ma_fields = {5: 'ma5', 10: 'ma10', 20: 'ma20', 60: 'ma60', 120: 'ma120', 250: 'ma250'}
    ma_vals = {}
    for period in chain_periods:
        v = safe_float(last.get(ma_fields[period], 0))
        if v > 0:
            ma_vals[period] = v

    # 链式递进: 从短到长检查连续 short>long，断裂即停
    chain_length = 0
    for i in range(len(chain_periods) - 1):
        sp, lp = chain_periods[i], chain_periods[i + 1]
        if sp in ma_vals and lp in ma_vals and ma_vals[sp] > ma_vals[lp]:
            chain_length += 1
        else:
            break

    ma_score = 6 if chain_length >= 5 else chain_length

    if chain_length > 0:
        chain_label = '→'.join(str(p) for p in chain_periods[:chain_length + 1])
        details.append(f'均线多头排列({chain_label})')
    elif 5 in ma_vals and 10 in ma_vals:
        details.append('均线无序')
    else:
        details.append('均线数据不足')

    # ── 日线闭环: 0~4分（只计买侧, 来自analyze_period的buy_level） ──
    cycle_score = 0
    if daily_buy_level >= 4.0:
        cycle_score = 4
        details.append('日线★买(最强出击)')
    elif daily_buy_level >= 3.5:
        cycle_score = 3
        details.append('日线★买(短期确认)')
    elif daily_buy_level >= 3.0:
        cycle_score = 2
        details.append('日线★买(加强闭环)')

    # ── 总分 0~16 ──
    total_score = expma_score + macd_score + ma_score + cycle_score

    if total_score >= 13:
        direction = 'bullish'
        label = '上涨趋势'
    elif total_score >= 10:
        direction = 'bullish_bias'
        label = '偏多震荡'
    elif total_score >= 7:
        direction = 'neutral'
        label = '横盘震荡'
    elif total_score >= 4:
        direction = 'bearish_bias'
        label = '偏空震荡'
    else:
        direction = 'bearish'
        label = '下跌趋势'

    confidence = abs(total_score - 8) / 8 * 100

    return {
        'direction': direction,
        'label': label,
        'confidence': round(confidence),
        'score': total_score,
        'expma_score': expma_score,
        'macd_score': macd_score,
        'ma_score': ma_score,
        'cycle_score': cycle_score,
        'daily_buy_level': daily_buy_level,
        'details': details,
        'close': close,
        'macd_dif': macd_dif,
        'macd_dea': macd_dea,
    }


# ============================================================
# 第三层: 循环适配（在已知位置+方向下）
# ============================================================

def extract_anchors(rows):
    """提取 ★买/★卖 定位点"""
    anchors = []
    for r in rows:
        buy = r.get('buy_signal', '').strip()
        sell = r.get('sell_signal', '').strip()
        if buy:
            anchors.append({
                'type': 'buy',
                'ts': r.get('timestamp', ''),
                'close': safe_float(r.get('close', 0)),
                'cci': r.get('cci', '')[:8],
                'has_ema': bool(r.get('expma_cross', '').strip()),
                'has_ext': bool(r.get('cci_extreme', '').strip()),
                'has_div': bool(r.get('cci_divergence', '').strip()),
            })
        elif sell:
            anchors.append({
                'type': 'sell',
                'ts': r.get('timestamp', ''),
                'close': safe_float(r.get('close', 0)),
                'cci': r.get('cci', '')[:8],
                'has_ema': bool(r.get('expma_cross', '').strip()),
                'has_ext': bool(r.get('cci_extreme', '').strip()),
                'has_div': bool(r.get('cci_divergence', '').strip()),
            })
    return anchors


def price_effectiveness(anchors, raw_rows, look_forward=5):
    """
    计算 ★买/★卖 之后的价格有效性
    
    对每个锚点，看它之后 look_forward 根 K 线的价格变化
    ★买后价格应该涨，★卖后价格应该跌
    ★买后涨得多 + ★卖后跌得少 = 好标的
    
    Returns:
        dict: {buy_avg_pct, sell_avg_pct, buy_hit_rate, sell_hit_rate, score}
    """
    if not anchors or not raw_rows:
        return {'buy_avg_pct': 0, 'sell_avg_pct': 0,
                'buy_hit_rate': 0, 'sell_hit_rate': 0, 'effectiveness': 0,
                'buy_samples': 0, 'sell_samples': 0}

    # 建立时间戳→价格映射
    ts_map = {}
    for i, r in enumerate(raw_rows):
        ts_map[r.get('timestamp', '')] = (i, safe_float(r.get('close', 0)))

    buy_gains = []
    sell_gains = []

    for a in anchors:
        ts = a['ts']
        if ts not in ts_map:
            continue
        idx, entry_close = ts_map[ts]
        if not entry_close:
            continue

        # 找锚点后 look_forward 根 K 线
        future_idx = min(idx + look_forward, len(raw_rows) - 1)
        if future_idx <= idx:
            continue
        exit_close = safe_float(raw_rows[future_idx].get('close', 0))
        if not exit_close:
            continue

        pct = (exit_close - entry_close) / entry_close * 100
        if a['type'] == 'buy':
            buy_gains.append(pct)
        else:
            sell_gains.append(pct)

    def avg(lst):
        return round(sum(lst) / len(lst), 1) if lst else 0

    def hit_rate(lst, expect_positive):
        if not lst:
            return 0
        correct = sum(1 for v in lst if v > 0) if expect_positive else sum(1 for v in lst if v < 0)
        return round(correct / len(lst) * 100)

    buy_avg = avg(buy_gains)
    sell_avg = avg(sell_gains)
    buy_hit = hit_rate(buy_gains, True)
    sell_hit = hit_rate(sell_gains, False)

    # 价格有效性评分
    effectiveness = 0
    if buy_avg > 0:
        effectiveness += min(buy_avg / 2, 2.5)  # 每2%涨1分，上限2.5
    if sell_avg < 0:
        effectiveness += min(abs(sell_avg) / 2, 1.5)  # 跌2%加1分，上限1.5
    effectiveness += buy_hit / 100  # 满分1
    effectiveness += sell_hit / 200  # 满分0.5
    effectiveness = round(effectiveness, 1)

    return {
        'buy_avg_pct': buy_avg,
        'sell_avg_pct': sell_avg,
        'buy_hit_rate': buy_hit,
        'sell_hit_rate': sell_hit,
        'effectiveness': effectiveness,
        'buy_samples': len(buy_gains),
        'sell_samples': len(sell_gains),
    }


def signal_quality(anchors, raw_rows, position, trend, lookback_klines=20, trend_pe=None):
    """
    第四层: 信号质量递进分析
    
    不是看"有没有信号"，而是看"信号递进到了哪个级别"
    
    四要素:
      1. ★买/★卖密集度 — 最近N根K线内出了几次定位点
      2. 金叉/死叉跟随速度 — ★买后多久出金叉(K线数)
      3. 底部/顶部价格方向 — 定位点低点是否抬升或下移
      4. 闭环完整性 — 定位点+金叉/死叉成对出现的次数
    
    不同趋势下权重不同:
      上涨(做多): ★买密集度 + 金叉跟随速度 + 底部价格抬升
      下跌(做空): ★卖密集度 + 死叉跟随速度 + 顶部价格下移
      震荡: 买卖都看，交替质量
    """
    if not anchors or not raw_rows:
        pe_info = None
        if trend_pe:
            pe_info = {
                'pe_front': trend_pe['pe_front'],
                'pe_back': trend_pe['pe_back'],
                'pe_ratio': trend_pe['pe_ratio'],
                'pe_level': trend_pe['pe_level'],
                'pe_phase': trend_pe['pe_phase'],
                'pe_velocity': trend_pe['pe_velocity'],
                'trending': trend_pe['trending'],
                'pe_label': trend_pe['label'],
                'tl_dir': trend_pe['tl_dir'],
            }
        return {'level': 'none', 'label': '无信号', 'details': [], 'trend_pe': pe_info}

    # 只看最近 N 根K线
    recent_rows = raw_rows[-lookback_klines:]
    row_count = len(recent_rows)

    # 在最近N根K线中找定位点和EMA交叉
    recent_buy_anchors = []
    recent_sell_anchors = []
    recent_golden = []  # 金叉位置
    recent_dead = []    # 死叉位置
    ts_to_idx = {}

    for i, r in enumerate(recent_rows):
        ts = r.get('timestamp', '')
        ts_to_idx[ts] = i

        buy = r.get('buy_signal', '').strip()
        sell = r.get('sell_signal', '').strip()
        ema = r.get('expma_cross', '').strip()
        close = safe_float(r.get('close', 0))

        if buy:
            recent_buy_anchors.append({'ts': ts, 'close': close, 'idx': i})
        elif sell:
            recent_sell_anchors.append({'ts': ts, 'close': close, 'idx': i})

        if '金叉' in ema:
            recent_golden.append({'ts': ts, 'close': close, 'idx': i})
        elif '死叉' in ema:
            recent_dead.append({'ts': ts, 'close': close, 'idx': i})

    # MA5/MA10 交叉检测（直接从CSV读，csv已有ma5/ma10字段）
    ma5_vals = [safe_float(r.get('ma5', 0)) for r in recent_rows]
    ma10_vals = [safe_float(r.get('ma10', 0)) for r in recent_rows]

    recent_ma5_golden = []  # MA5上穿MA10
    recent_ma5_dead = []    # MA5下穿MA10
    for i in range(10, row_count):
        if ma5_vals[i] > 0 and ma10_vals[i] > 0 and ma5_vals[i-1] > 0 and ma10_vals[i-1] > 0:
            if ma5_vals[i-1] <= ma10_vals[i-1] and ma5_vals[i] > ma10_vals[i]:
                recent_ma5_golden.append({'idx': i})
            elif ma5_vals[i-1] >= ma10_vals[i-1] and ma5_vals[i] < ma10_vals[i]:
                recent_ma5_dead.append({'idx': i})

    details = []

    # --- 做多侧分析 ---
    buy_level = 0  # 0=无 1=普通 2=加强 3=最强
    buy_details = []

    if recent_buy_anchors:
        # 1. ★买密集度
        buy_count = len(recent_buy_anchors)
        buy_density = buy_count / row_count * 100  # 每百根K线的★买密度
        if buy_density >= 0.8:
            density_label = f'★买密集({buy_count}次/{row_count}K线)'
            buy_level += 1.5
        elif buy_density >= 0.4:
            density_label = f'★买正常({buy_count}次)'
            buy_level += 1.0
        else:
            density_label = f'★买稀疏({buy_count}次)'
            buy_level += 0.5
        buy_details.append(density_label)

        # 2. 金叉跟随速度: ★买后到最近金叉的距离
        best_follow = None
        for ba in recent_buy_anchors:
            for gc in recent_golden:
                if gc['idx'] > ba['idx']:
                    gap = gc['idx'] - ba['idx']
                    if best_follow is None or gap < best_follow:
                        best_follow = gap
        if best_follow is not None:
            if best_follow <= 5:
                follow_label = f'金叉跟随快(gap={best_follow})'
                buy_level += 1.5
            elif best_follow <= 12:
                follow_label = f'金叉跟随正常(gap={best_follow})'
                buy_level += 1.0
            else:
                follow_label = f'金叉跟随慢(gap={best_follow})'
                buy_level += 0.3
            buy_details.append(follow_label)

        # 3. 底部价格抬升: 每个★买相比前一个★买的低点提高
        if len(recent_buy_anchors) >= 2:
            raises = 0
            total = 0
            for i in range(1, len(recent_buy_anchors)):
                prev = recent_buy_anchors[i-1]['close']
                curr = recent_buy_anchors[i]['close']
                if prev and curr and prev > 0:
                    total += 1
                    if curr >= prev:
                        raises += 1
            if total > 0:
                raise_pct = raises / total * 100
                if raise_pct >= 80:
                    buy_details.append(f'底部抬升({raise_pct:.0f}%)')
                    buy_level += 1.0
                elif raise_pct >= 50:
                    buy_details.append(f'底部持平({raise_pct:.0f}%抬升)')
                else:
                    buy_details.append(f'底部下移({100-raise_pct:.0f}%下移)')

        # 4. 闭环成对: ★买+金叉 成对出现的次数
        pairs = 0
        for ba in recent_buy_anchors:
            for gc in recent_golden:
                if gc['idx'] > ba['idx']:
                    pairs += 1
                    break
        if pairs >= 3:
            buy_details.append(f'闭环{pairs}对(密集)')
            buy_level += 1.0
        elif pairs >= 2:
            buy_details.append(f'闭环{pairs}对')
            buy_level += 0.5
        elif pairs >= 1:
            buy_details.append(f'闭环{pairs}对')
            buy_level += 0.3

        # 5. MA5/10金叉确认（★买后→EXPMA金叉前的短期趋势确认）
        if recent_ma5_golden:
            first_ma5 = None
            for ba in recent_buy_anchors:
                for gc in recent_ma5_golden:
                    if gc['idx'] > ba['idx']:
                        if first_ma5 is None or gc['idx'] < first_ma5['idx']:
                            first_ma5 = gc
                        break
            first_expma = None
            for ba in recent_buy_anchors:
                for gc in recent_golden:
                    if gc['idx'] > ba['idx']:
                        if first_expma is None or gc['idx'] < first_expma['idx']:
                            first_expma = gc
                        break
            if first_ma5 is not None:
                if first_expma is None:
                    buy_level += 1.0
                    buy_details.append('MA5/10金叉确认(无EXPMA)')
                elif first_ma5['idx'] <= first_expma['idx']:
                    buy_level += 1.2
                    buy_details.append('MA5/10→EXPMA递进')
                else:
                    buy_level += 0.3
                    buy_details.append('MA5/10金叉滞后')

        # 6. 排列熵确认：降熵=有序→方向形成，结构突破/方向酝酿
        if trend_pe:
            if trend_pe['trending'] and trend_pe['pe_ratio'] < 0.85:
                buy_level += 1.5
                buy_details.append(f'★结构突破(pe={trend_pe["pe_back"]:.2f})')
            elif trend_pe['trending']:
                buy_level += 1.0
                buy_details.append(f'方向形成中(pe={trend_pe["pe_back"]:.2f})')
            elif trend_pe['pe_ratio'] > 1.15:
                # 升熵=回归震荡，不扣分，但标记
                buy_details.append(f'震荡回归(pe={trend_pe["pe_back"]:.2f})')

            # 加入 PE 原始数据用于输出
            buy_details.append(f'pe({trend_pe["pe_front"]:.2f}→{trend_pe["pe_back"]:.2f})')

    # --- 做空侧分析 ---
    sell_level = 0
    sell_details = []

    if recent_sell_anchors:
        sell_count = len(recent_sell_anchors)
        sell_density = sell_count / row_count * 100
        if sell_density >= 0.8:
            sell_details.append(f'★卖密集({sell_count}次/{row_count}K线)')
            sell_level += 1.5
        elif sell_density >= 0.4:
            sell_details.append(f'★卖正常({sell_count}次)')
            sell_level += 1.0
        else:
            sell_details.append(f'★卖稀疏({sell_count}次)')
            sell_level += 0.5

        best_follow = None
        for sa in recent_sell_anchors:
            for dc in recent_dead:
                if dc['idx'] > sa['idx']:
                    gap = dc['idx'] - sa['idx']
                    if best_follow is None or gap < best_follow:
                        best_follow = gap
        if best_follow is not None:
            if best_follow <= 5:
                sell_details.append(f'死叉跟随快(gap={best_follow})')
                sell_level += 1.5
            elif best_follow <= 12:
                sell_details.append(f'死叉跟随正常(gap={best_follow})')
                sell_level += 1.0
            else:
                sell_details.append(f'死叉跟随慢(gap={best_follow})')
                sell_level += 0.3

        if len(recent_sell_anchors) >= 2:
            drops = 0
            total = 0
            for i in range(1, len(recent_sell_anchors)):
                prev = recent_sell_anchors[i-1]['close']
                curr = recent_sell_anchors[i]['close']
                if prev and curr and prev > 0:
                    total += 1
                    if curr <= prev:
                        drops += 1
            if total > 0:
                drop_pct = drops / total * 100
                if drop_pct >= 80:
                    sell_details.append(f'顶部下移({drop_pct:.0f}%)')
                    sell_level += 1.0
                elif drop_pct >= 50:
                    sell_details.append(f'顶部持平({drop_pct:.0f}%下移)')
                else:
                    sell_details.append(f'顶部抬高({100-drop_pct:.0f}%抬高)')

        pairs = 0
        for sa in recent_sell_anchors:
            for dc in recent_dead:
                if dc['idx'] > sa['idx']:
                    pairs += 1
                    break
        if pairs >= 3:
            sell_details.append(f'闭环{pairs}对(密集)')
            sell_level += 1.0
        elif pairs >= 2:
            sell_details.append(f'闭环{pairs}对')
            sell_level += 0.5
        elif pairs >= 1:
            sell_details.append(f'闭环{pairs}对')
            sell_level += 0.3

        # 5. MA5/10死叉确认（★卖后→EXPMA死叉前的短期趋势确认）
        if recent_ma5_dead:
            first_ma5_d = None
            for sa in recent_sell_anchors:
                for dc in recent_ma5_dead:
                    if dc['idx'] > sa['idx']:
                        if first_ma5_d is None or dc['idx'] < first_ma5_d['idx']:
                            first_ma5_d = dc
                        break
            first_expma_d = None
            for sa in recent_sell_anchors:
                for dc in recent_dead:
                    if dc['idx'] > sa['idx']:
                        if first_expma_d is None or dc['idx'] < first_expma_d['idx']:
                            first_expma_d = dc
                        break
            if first_ma5_d is not None:
                if first_expma_d is None:
                    sell_level += 1.0
                    sell_details.append('MA5/10死叉确认(无EXPMA)')
                elif first_ma5_d['idx'] <= first_expma_d['idx']:
                    sell_level += 1.2
                    sell_details.append('MA5/10→EXPMA递进(空)')
                else:
                    sell_level += 0.3
                    sell_details.append('MA5/10死叉滞后')

        # 6. 排列熵确认（卖侧：降熵同样意味着方向形成）
        if trend_pe:
            if trend_pe['trending'] and trend_pe['pe_ratio'] < 0.85:
                sell_level += 1.5
                sell_details.append(f'★结构突破(pe={trend_pe["pe_back"]:.2f})')
            elif trend_pe['trending']:
                sell_level += 1.0
                sell_details.append(f'方向形成中(pe={trend_pe["pe_back"]:.2f})')
            elif trend_pe['pe_ratio'] > 1.15:
                sell_details.append(f'震荡回归(pe={trend_pe["pe_back"]:.2f})')
            sell_details.append(f'pe({trend_pe["pe_front"]:.2f}→{trend_pe["pe_back"]:.2f})')

    # --- 根据趋势方向选择主分析侧 ---
    direction = trend['direction']
    if direction in ('bullish', 'bullish_bias'):
        details = buy_details
        if buy_level >= 4.0:
            label = '最强出击信号'
        elif buy_level >= 3.0:
            label = '加强闭环'
        elif buy_level >= 2.0:
            label = '普通闭环'
        elif buy_level >= 1.0:
            label = '弱信号'
        else:
            label = '无出击信号'
        level = buy_level
        # 附带空头信息（做参考）
        if sell_details:
            details.append(f'[空侧参考: {" | ".join(sell_details)}]')

    elif direction in ('bearish', 'bearish_bias'):
        details = sell_details
        if sell_level >= 4.0:
            label = '最强出击信号'
        elif sell_level >= 3.0:
            label = '加强闭环'
        elif sell_level >= 2.0:
            label = '普通闭环'
        elif sell_level >= 1.0:
            label = '弱信号'
        else:
            label = '无出击信号'
        level = sell_level
        if buy_details:
            details.append(f'[多侧参考: {" | ".join(buy_details)}]')

    else:
        # 震荡看两侧
        effective_level = max(buy_level, sell_level)
        details = buy_details + sell_details
        if effective_level >= 4.0:
            label = '最强出击信号'
        elif effective_level >= 3.0:
            label = '加强闭环'
        elif effective_level >= 2.0:
            label = '普通闭环'
        elif effective_level >= 1.0:
            label = '弱信号'
        else:
            label = '无出击信号'
        level = effective_level

    # 排列熵信息
    pe_info = None
    if trend_pe:
        pe_info = {
            'pe_front': trend_pe['pe_front'],
            'pe_back': trend_pe['pe_back'],
            'pe_ratio': trend_pe['pe_ratio'],
            'pe_level': trend_pe['pe_level'],
            'pe_phase': trend_pe['pe_phase'],
            'pe_velocity': trend_pe['pe_velocity'],
            'trending': trend_pe['trending'],
            'pe_label': trend_pe['label'],
            'tl_dir': trend_pe['tl_dir'],
        }
    return {
        'level': level,
        'label': label,
        'details': details,
        'buy_level': buy_level,
        'sell_level': sell_level,
        'trend_pe': pe_info,
        'ema_cross_status': {
            'has_recent_golden': bool(recent_golden),
            'last_golden_idx': recent_golden[-1]['idx'] if recent_golden else -1,
            'golden_count': len(recent_golden),
            'has_recent_dead': bool(recent_dead),
            'last_dead_idx': recent_dead[-1]['idx'] if recent_dead else -1,
            'dead_count': len(recent_dead),
        } if recent_golden or recent_dead else None,
    }


def cycle_pattern(anchors):
    """
    分析 ★买/★卖 排列模式
    
    只看最近 10 个锚点，判断当前是:
      - alternating: 买卖交替，标准循环
      - buy_dominant: 买多卖少，多头排列
      - sell_dominant: 卖多买少，空头排列
      - mixed: 混杂无序
    """
    if len(anchors) < 3:
        return {'pattern': 'insufficient', 'label': '信号不足', 'score': 0}

    recent = anchors[-10:]
    n = len(recent)
    types = [a['type'] for a in recent]
    buy_count = types.count('buy')
    sell_count = types.count('sell')

    # 交替次数
    alternations = sum(1 for i in range(1, n) if types[i] != types[i-1])
    alt_ratio = alternations / (n - 1) if n > 1 else 0

    # 模式判断
    if buy_count >= n * 0.7:
        pattern = 'buy_dominant'
        label = f'多头排列({buy_count}买/{sell_count}卖)'
        score_base = 2.0
    elif sell_count >= n * 0.7:
        pattern = 'sell_dominant'
        label = f'空头排列({buy_count}买/{sell_count}卖)'
        score_base = 2.0
    elif alt_ratio >= 0.6:
        pattern = 'alternating'
        label = f'买卖交替(交替率{alt_ratio:.0%})'
        score_base = 3.5
    else:
        pattern = 'mixed'
        label = f'混合排列(交替率{alt_ratio:.0%})'
        score_base = 2.5

    # 闭环加分
    ema_matched = sum(1 for a in recent if a['has_ema'])
    ext_matched = sum(1 for a in recent if a['has_ext'] or a['has_div'])
    closure_bonus = min(1.0, ema_matched / n * 1.5 + ext_matched / n * 1.0)

    score = min(5.0, round(score_base + closure_bonus, 1))

    return {
        'pattern': pattern,
        'label': label,
        'alternation_ratio': round(alt_ratio, 2),
        'buy_count': buy_count,
        'sell_count': sell_count,
        'score': score,
    }


# ============================================================
# 第四层扩展: 波峰间距法 — 主导循环量级检测
# ============================================================

def _wave_peaks(values):
    """
    找趋势线的波峰位置

    波峰定义: 比前后各2个点都高的局部高点
    返回: 波峰在 values 中的索引列表
    """
    if len(values) < 5:
        return []

    window = 2
    peaks = []
    for i in range(window, len(values) - window):
        if all(values[i] >= values[i - j] for j in range(1, window + 1)) \
           and all(values[i] >= values[i + j] for j in range(1, window + 1)):
            peaks.append(i)

    # 去重: 连续满足的只取最高的那根
    if len(peaks) < 2:
        return peaks
    filtered = [peaks[0]]
    for p in peaks[1:]:
        if p - filtered[-1] <= window:
            if values[p] > values[filtered[-1]]:
                filtered[-1] = p
        else:
            filtered.append(p)
    return filtered


def _peak_intervals(peaks):
    """计算连续波峰之间的间距（K线根数）"""
    if len(peaks) < 2:
        return []
    return [peaks[i+1] - peaks[i] for i in range(len(peaks) - 1)]


def detect_dominant_cycle(code, period_results):
    """
    波峰间距法 — 检测当前主导循环量级

    从最小周期(5分钟)开始逐级向上检查:
      每个周期取 trend_line 的波峰，量间距
      间距稳定(当前/历史 < 1.5倍) → 该级别是主导量级
      间距拉长(>= 1.5倍) → 上级周期在接管，继续向上查

    Returns: dict
        dominant_cycle: 'min5'|'min15'|'min30'|'min60'|'daily'
        dominant_label: 中文标签
        detail: 各级间距变化描述
        stretched_periods: 被判定为拉长的级别列表
    """
    periods_to_check = ['min5', 'min15', 'min30', 'min60', 'daily']
    p_labels = {'min5': '5分钟', 'min15': '15分钟', 'min30': '30分钟',
                'min60': '60分钟', 'daily': '日线'}

    all_details = []
    stretched = []

    for p in periods_to_check:
        rows = read_csv(code, p)
        if not rows:
            all_details.append(f'{p_labels[p]}:无数据')
            continue

        values = [safe_float(r.get('trend_line', 0)) for r in rows if safe_float(r.get('trend_line', 0)) > 0]
        if len(values) < 30:
            all_details.append(f'{p_labels[p]}:数据不足({len(values)})')
            continue

        peaks = _wave_peaks(values)
        if len(peaks) < 3:
            all_details.append(f'{p_labels[p]}:波峰不足({len(peaks)})')
            continue

        intervals = _peak_intervals(peaks)
        if len(intervals) < 2:
            all_details.append(f'{p_labels[p]}:间距不足')
            continue

        current = intervals[-1]
        baseline = intervals[:-1]
        avg_base = sum(baseline) / len(baseline) if baseline else current

        stretch = current / avg_base if avg_base > 0 else 1.0
        all_details.append(f'{p_labels[p]}间距{current:.0f}/{avg_base:.0f}({stretch:.1f}倍)')

        if stretch < 1.5:
            return {
                'dominant_cycle': p,
                'dominant_label': p_labels[p],
                'detail': ' | '.join(all_details),
                'stretched_periods': stretched,
            }
        else:
            stretched.append(p)

    # 全线拉长 → 日线默认
    return {
        'dominant_cycle': 'daily',
        'dominant_label': '日线',
        'detail': ' | '.join(all_details) + ' → 全线拉长,日线主导',
        'stretched_periods': stretched,
    }


# ============================================================
# 第四层扩展: 量价阶段标注（仅日线级别）
# ============================================================

def analyze_volume_regime(code, daily_rows, period_results):
    """
    判断日线成交量所处的量价阶段（最小改动，不参与评分）

    分析逻辑:
      1. 计算百日地量（最近100天最低成交量）
      2. 计算地量堆密度（最近20天在1.0~1.3倍地量的占比）
      3. 结合日线★买/★卖信号状态，判断量价阶段

    量价阶段含义:
      - 底部地量区: ★卖周期末端的地量堆 → 供应枯竭，等需求
      - 缩量回调: ★买周期中的百日地量附近 → 上涨中继洗盘
      - 初步缩量: 缩到地量附近但未形成地量堆 → 观察
      - 正常放量: 量在1.3~2.0倍地量 → 正常交易
      - 显著放量: 量超过2倍地量 → 放量异常

    Returns: dict
        phase: 量价阶段标签
        detail: 中文描述
        vol_ratio: 当日量/百日地量比值
        dilangdui_density: 地量堆密度
    """
    if not daily_rows or len(daily_rows) < 30:
        return {'phase': '数据不足'}

    volumes = [safe_float(r.get('volume', 0)) for r in daily_rows
               if safe_float(r.get('volume', 0)) > 0]
    if len(volumes) < 30:
        return {'phase': '数据不足', 'detail': f'成交量数据{len(volumes)}条'}

    # 百日地量（取5分位值，避免极端低值干扰）
    lookback = min(100, len(volumes))
    recent_v = volumes[-lookback:]
    sorted_v = sorted(recent_v)
    min_v = sorted_v[int(len(sorted_v) * 0.05)]  # 5分位值作为地量水准
    cur_v = volumes[-1]
    vol_r = cur_v / min_v if min_v > 0 else 999

    # 地量堆密度：最近20天在[地量, 地量×1.3]的天数占比
    win = min(20, len(recent_v))
    watch = recent_v[-win:]
    in_pile = sum(1 for v in watch if v <= min_v * 1.3)
    pile_density = in_pile / win if win > 0 else 0

    # 日线最近的信号主导方向
    ds = period_results.get('daily', {})
    sq = ds.get('signal_quality') if ds else None
    if sq:
        buy_lv = sq.get('buy_level', 0)
        sell_lv = sq.get('sell_level', 0)
    else:
        buy_lv = 0
        sell_lv = 0

    # ---- 判断阶段 ----
    is_dilang = vol_r < 1.3
    is_pile = pile_density > 0.5
    is_bearish = sell_lv > buy_lv * 1.2  # 卖侧显著占优

    if is_dilang and is_pile and is_bearish:
        phase = '底部地量区'
        detail = f'百日地量附近(×{vol_r:.1f})，地量堆密度{pile_density:.0%}，供应枯竭'
    elif is_dilang and is_pile and not is_bearish:
        phase = '缩量回调'
        detail = f'★买周期中缩量到百日地量(×{vol_r:.1f})，地量堆{pile_density:.0%}，洗盘性质'
    elif is_dilang and not is_pile:
        phase = '初步缩量'
        detail = f'缩量到百日地量附近(×{vol_r:.1f})，未形成地量堆({pile_density:.0%})，观察'
    else:
        # 正常放量/显著放量不标注（不制造噪音）
        phase = '正常量能'
        detail = ''

    return {
        'phase': phase,
        'detail': detail,
        'vol_ratio': round(vol_r, 2),
        'dilangdui_density': round(pile_density, 2),
    }


def judge_wave_structure(code, period_results, dominant_info):
    """
    结构分析：一句话判断当前主导量级的结构状态

    1. 主导量级方向（买闭环/卖闭环/平衡）
    2. 次级别推动段 vs 修正段对比（涨跌段密度）
    3. 回调深度（最近一段回调占上涨比例）
    """
    PERIODS = ['min5', 'min15', 'min30', 'min60', 'daily']

    dc = dominant_info.get('dominant_cycle', 'min15')
    if dc not in PERIODS:
        dc = 'min15'
    dc_idx = PERIODS.index(dc)

    ds = period_results.get(dc, {}).get('signal_quality', {}) or {}
    dc_buy = ds.get('buy_level', 0) or 0
    dc_sell = ds.get('sell_level', 0) or 0

    if dc_buy >= dc_sell * 1.1:
        dc_dir = '买闭环'
    elif dc_sell >= dc_buy * 1.1:
        dc_dir = '卖闭环'
    else:
        dc_dir = '平衡'

    if dc_idx == 0:
        if dc_dir == '买闭环':
            mark = '✔ 买闭环中'
        elif dc_dir == '卖闭环':
            mark = '✗ 卖闭环中'
        else:
            mark = '○ 方向不明'
        return {'structure': f'{dc} {dc_dir} ({mark})', 'detail': '小级别主导，无次级别结构',
                'dominant': dc, 'direction': dc_dir, 'sub_level': dc, 'verdict_mark': mark,
                'retrace_pct': None}

    sub_idx = dc_idx - 1
    sub_p = PERIODS[sub_idx]
    ss = period_results.get(sub_p, {}).get('signal_quality', {}) or {}
    sub_buy = ss.get('buy_level', 0) or 0
    sub_sell = ss.get('sell_level', 0) or 0

    sub_rows = read_csv(code, sub_p)
    if not sub_rows:
        return {'structure': f'{dc} {dc_dir}', 'detail': '次级别数据不足',
                'dominant': dc, 'direction': dc_dir, 'sub_level': sub_p}

    lines = [safe_float(r.get('trend_line', 0)) for r in sub_rows
             if safe_float(r.get('trend_line', 0)) > 0]
    if len(lines) < 20:
        return {'structure': f'{dc} {dc_dir}', 'detail': '次级趋势线不足',
                'dominant': dc, 'direction': dc_dir, 'sub_level': sub_p}

    peaks = _wave_peaks(lines)
    neg = [-v for v in lines]
    valley_idxs = _wave_peaks(neg)
    events = [(p, 'peak', lines[p]) for p in peaks] + \
             [(v, 'valley', lines[v]) for v in valley_idxs]
    events.sort(key=lambda x: x[0])

    MIN_WAVE = (max(lines) - min(lines)) * 0.08
    filtered = [events[0]]
    for i in range(1, len(events)):
        if abs(events[i][2] - filtered[-1][2]) >= MIN_WAVE:
            filtered.append(events[i])

    rises, falls = [], []
    for i in range(len(filtered) - 1):
        if filtered[i][1] == 'valley' and filtered[i+1][1] == 'peak':
            rises.append({'len': filtered[i+1][0] - filtered[i][0],
                          'rng': filtered[i+1][2] - filtered[i][2]})
        elif filtered[i][1] == 'peak' and filtered[i+1][1] == 'valley':
            falls.append({'len': filtered[i+1][0] - filtered[i][0],
                          'rng': filtered[i][2] - filtered[i+1][2]})

    avg_rise_len = sum(s['len'] for s in rises) / len(rises) if rises else 0
    avg_fall_len = sum(s['len'] for s in falls) / len(falls) if falls else 0
    n_rises, n_falls = len(rises), len(falls)

    retrace_pct = None
    if len(filtered) >= 4:
        last, prev, pprev = filtered[-1], filtered[-2], filtered[-3]
        if last[1] == 'valley' and prev[1] == 'peak' and pprev[1] == 'valley':
            rise_rng = prev[2] - pprev[2]
            if rise_rng > 0:
                retrace_pct = (prev[2] - last[2]) / rise_rng * 100

    last_dir = ''
    if filtered[-1][1] == 'peak':
        last_dir = '末段上涨中'
    elif len(filtered) >= 3 and filtered[-1][1] == 'valley' and filtered[-2][1] == 'peak':
        last_dir = '末段回调中'

    if dc_dir == '买闭环':
        if n_rises >= n_falls and avg_rise_len >= avg_fall_len * 1.2:
            sub_v = f'涨段({n_rises})>跌段({n_falls}), 均长{avg_rise_len:.0f}>{avg_fall_len:.0f}'
            mark = '✔ 推动>修正'
        elif n_rises >= n_falls and avg_rise_len >= avg_fall_len:
            sub_v = f'涨段({n_rises})≥跌段({n_falls})'
            mark = '∼ 涨略强'
        elif n_rises < n_falls:
            sub_v = f'跌段({n_falls})>涨段({n_rises})'
            mark = '✗ 涨势存疑'
        else:
            sub_v = '涨跌均衡'
            mark = '∼ 中性'
        if retrace_pct is not None:
            if retrace_pct < 33:
                sub_v += f', 浅调{retrace_pct:.0f}%'
                mark += '✔'
            elif retrace_pct > 66:
                sub_v += f', 深调{retrace_pct:.0f}%'
                mark += '⚠'
            else:
                sub_v += f', 调{retrace_pct:.0f}%'
    elif dc_dir == '卖闭环':
        if n_falls >= n_rises:
            sub_v = f'跌段({n_falls})主导'
            mark = '✗ 下跌延续'
        else:
            sub_v = '跌中带反弹'
            mark = '∼ 或减速'
        if retrace_pct is not None and retrace_pct < 33:
            sub_v += ', 反弹弱'
    else:
        sub_v = '涨跌均衡'
        mark = '○ 方向不明'

    return {
        'structure': f'{dc} {dc_dir} → {sub_p} {mark}',
        'detail': f'{sub_v} | {last_dir}',
        'dominant': dc,
        'direction': dc_dir,
        'sub_level': sub_p,
        'verdict_mark': mark,
        'retrace_pct': round(retrace_pct, 1) if retrace_pct is not None else None,
    }


def detect_exponential_readiness(code, daily_rows, period_results, dominant_info):
    """
    指数级行情条件检测：三维度评分 + 信号灯

    1. 压缩率(0-3): 布林带宽低位 + 百日地量
    2. 加速度(0-3): 推调比趋势 + 回调深度趋势
    3. 周期锁定(0-4): MACD dif方向一致 + 信号质量同步

    总分0-10 → 绿灯(>=7) 黄灯(4-6) 红灯(0-3)
    """
    PERIODS = ['min5', 'min15', 'min30', 'min60', 'daily']
    sc = {'compression': 0, 'acceleration': 0, 'cycle_lock': 0}
    info = []
    persist = {'compress_days': 0, 'direction_align': '', 'total_days': 0}

    dc = dominant_info.get('dominant_cycle', 'min15')
    dc_idx = PERIODS.index(dc) if dc in PERIODS else 2
    ds = period_results.get(dc, {}).get('signal_quality', {}) or {}
    dc_buy = ds.get('buy_level', 0) or 0
    dc_sell = ds.get('sell_level', 0) or 0
    is_buy_close = dc_buy > dc_sell

    # 1. 压缩率
    if daily_rows and len(daily_rows) >= 60:
        mids = [safe_float(r.get('bb_ma221', 0)) for r in daily_rows[-120:]]
        ups = [safe_float(r.get('bb_red_line', 0)) for r in daily_rows[-120:]]
        widths = [(ups[i] - mids[i]) / mids[i] * 100
                  for i in range(len(mids)) if mids[i] > 0 < ups[i]]
        if widths:
            cur_w, min_w, max_w = widths[-1], min(widths), max(widths)
            pct = (cur_w - min_w) / (max_w - min_w) * 100 if max_w > min_w else 50
            if pct < 20:
                sc['compression'] += 2
                info.append(f'压缩:带宽极端低位(pct={pct:.0f}%)')
            elif pct < 40:
                sc['compression'] += 1
                info.append(f'压缩:带宽偏低(pct={pct:.0f}%)')
            else:
                info.append(f'压缩:带宽{pct:.0f}%分位')

            median_w = sorted(widths)[len(widths)//2]
            compress_days = 0
            for w in reversed(widths):
                if w <= median_w:
                    compress_days += 1
                else:
                    break
            persist['compress_days'] = compress_days
            info.append(f'压缩持续{compress_days}天')

            vols = [safe_float(r.get('volume', 0)) for r in daily_rows
                    if safe_float(r.get('volume', 0)) > 0]
            if vols:
                recent_v = vols[-min(100, len(vols)):]
                sorted_v = sorted(recent_v)
                min_v = sorted_v[int(len(sorted_v) * 0.05)]
                cur_v = vols[-1]
                vol_r = cur_v / min_v if min_v > 0 else 99
                if vol_r < 1.3:
                    sc['compression'] += 1
                    info.append(f'地量(×{vol_r:.1f})')

    # 2. 加速度
    sub_p = PERIODS[max(0, dc_idx - 1)]
    sub_rows = read_csv(code, sub_p)
    if sub_rows and len(sub_rows) >= 30:
        lines = [safe_float(r.get('trend_line', 0)) for r in sub_rows
                 if safe_float(r.get('trend_line', 0)) > 0]
        if len(lines) >= 30:
            peaks = _wave_peaks(lines)
            neg = [-v for v in lines]
            valley_idxs = _wave_peaks(neg)
            events = [(p, 'p', lines[p]) for p in peaks] + \
                     [(v, 'v', lines[v]) for v in valley_idxs]
            events.sort(key=lambda x: x[0])
            min_wave = (max(lines) - min(lines)) * 0.08
            filt = [events[0]]
            for i in range(1, len(events)):
                if abs(events[i][2] - filt[-1][2]) >= min_wave:
                    filt.append(events[i])
            rises, falls = [], []
            for i in range(len(filt) - 1):
                if filt[i][1] == 'v' and filt[i+1][1] == 'p':
                    rises.append({'len': filt[i+1][0] - filt[i][0],
                                  'h': filt[i+1][2] - filt[i][2]})
                elif filt[i][1] == 'p' and filt[i+1][1] == 'v':
                    falls.append({'len': filt[i+1][0] - filt[i][0],
                                  'd': filt[i][2] - filt[i+1][2]})
            if len(rises) >= 4 and len(falls) >= 3:
                mid_r, mid_f = len(rises) // 2, len(falls) // 2
                r_late, r_early = rises[mid_r:], rises[:mid_r]
                f_late, f_early = falls[mid_f:], falls[:mid_f]
                avg_rl = sum(s['h'] for s in r_late) / len(r_late)
                avg_re = sum(s['h'] for s in r_early) / len(r_early)
                avg_fl = sum(s['len'] for s in f_late) / len(f_late)
                avg_fe = sum(s['len'] for s in f_early) / len(f_early)
                acc_items = 0
                if avg_rl > avg_re * 1.2:
                    sc['acceleration'] += 1; acc_items += 1
                    info.append(f'加速:推幅↑({avg_re:.1f}→{avg_rl:.1f})')
                if avg_fl < avg_fe * 0.8:
                    sc['acceleration'] += 1; acc_items += 1
                    info.append(f'加速:调时↓({avg_fe:.0f}→{avg_fl:.0f}K)')
                depths = []
                for i in range(min(len(falls), len(rises))):
                    if rises[i]['h'] > 0:
                        depths.append(falls[i]['d'] / rises[i]['h'] * 100)
                if depths:
                    early_d = sum(depths[:len(depths)//2]) / max(len(depths)//2, 1)
                    late_d = sum(depths[len(depths)//2:]) / max(len(depths)-len(depths)//2, 1)
                    if late_d < early_d * 0.7:
                        sc['acceleration'] += 1; acc_items += 1
                        info.append(f'加速:回调深↓({early_d:.0f}%→{late_d:.0f}%)')
                if acc_items == 0:
                    info.append(f'加速:平稳({len(rises)}涨{len(falls)}跌)')
            else:
                info.append(f'加速:段不足({len(rises)}涨{len(falls)}跌)')

    # 3. 周期锁定
    dif_signs = []
    levels_ok = 0
    for p in PERIODS:
        sq = period_results.get(p, {}).get('signal_quality', {}) or {}
        if (sq.get('level', 0) or 0) >= 3:
            levels_ok += 1
        rows = read_csv(code, p)
        if rows:
            difs = [safe_float(r.get('macd_dif', 0)) for r in rows[-5:]]
            avg_dif = sum(difs) / len(difs) if difs else 0
            if avg_dif != 0:
                dif_signs.append(1 if avg_dif > 0 else -1)

    if dif_signs:
        pos = sum(1 for s in dif_signs if s > 0)
        neg = sum(1 for s in dif_signs if s < 0)
        same_pct = max(pos, neg) / len(dif_signs)
        if same_pct >= 0.8:
            sc['cycle_lock'] += 2
            info.append(f'锁定:方向一致({same_pct:.0%})')
        elif same_pct >= 0.6:
            sc['cycle_lock'] += 1
            info.append(f'锁定:偏一致({same_pct:.0%})')
        else:
            info.append(f'锁定:分歧({same_pct:.0%})')
        if levels_ok >= 4:
            sc['cycle_lock'] += 2
            info.append(f'锁定:信号{levels_ok}/5同步')
        elif levels_ok >= 3:
            sc['cycle_lock'] += 1
            info.append(f'锁定:信号{levels_ok}/5')
        else:
            info.append(f'锁定:信号仅{levels_ok}/5')

    if is_buy_close:
        persist['direction_align'] = '买闭环+'
        if sc['compression'] >= 1:
            info.append('方向:压缩+买闭环=正向')
        else:
            info.append('方向:买闭环(未压缩)')
    else:
        persist['direction_align'] = '卖闭环-'
        if sc['compression'] >= 1:
            info.append('方向:⚠ 压缩+卖闭环=负向')
        else:
            info.append('方向:卖闭环')

    total = sc['compression'] + sc['acceleration'] + sc['cycle_lock']
    if total >= 7:
        light = '🟢 绿灯'
        conclusion = '指数级条件成熟'
    elif total >= 4:
        light = '🟡 黄灯'
        conclusion = '部分条件具备'
    else:
        light = '🔴 红灯'
        conclusion = '条件不足'

    return {
        'traffic_light': light,
        'total_score': total,
        'scores': sc,
        'detail': ' | '.join(info),
        'conclusion': conclusion,
        'persist': persist,
    }


# ============================================================
# 第四层扩展 v3.8: 缠论结构分析 + 大盘系数权重
# ============================================================


def _find_local_extremes(values, window=2, find_peaks=True):
    """找局部高点(peaks)或低点(valleys)，用于缠论分型识别"""
    if len(values) < window * 2 + 1:
        return []
    extremes = []
    for i in range(window, len(values) - window):
        if find_peaks:
            if all(values[i] >= values[i - j] for j in range(1, window + 1)) \
               and all(values[i] >= values[i + j] for j in range(1, window + 1)):
                extremes.append({'idx': i, 'value': values[i]})
        else:
            if all(values[i] <= values[i - j] for j in range(1, window + 1)) \
               and all(values[i] <= values[i + j] for j in range(1, window + 1)):
                extremes.append({'idx': i, 'value': values[i]})
    if len(extremes) < 2:
        return extremes
    filtered = [extremes[0]]
    for e in extremes[1:]:
        if e['idx'] - filtered[-1]['idx'] <= window:
            if find_peaks and e['value'] > filtered[-1]['value']:
                filtered[-1] = e
            elif not find_peaks and e['value'] < filtered[-1]['value']:
                filtered[-1] = e
        else:
            filtered.append(e)
    return filtered


def detect_rs_density(code, daily_rows):
    """
    缠论结构分析 — 阻力/支撑密度检测 (v3.8 新增)

    用缠论分型理论判断趋势结构:

    上涨趋势 = 顶分型逐次抬高 + 底分型逐次抬高 → 结构支撑强
    下跌趋势 = 底分型逐次降低 + 顶分型逐次降低 → 结构阻力强

    趋势终结(分割线):
      上涨终结: 最后一个顶抬高但底没有抬高(下降)
      下跌终结: 最后一个底创新低后顶不再降低(高于前顶)

    rs_score > 0 → 结构有利做多, rs_score < 0 → 结构不利做多
    """
    if not daily_rows or len(daily_rows) < 30:
        return {'status': '数据不足', 'rs_score': 0, 'rs_label': '未知'}

    closes = [safe_float(r.get('close', 0)) for r in daily_rows]
    if not closes:
        return {'status': '数据异常', 'rs_score': 0, 'rs_label': '未知'}

    cur_price = closes[-1]
    peaks = _find_local_extremes(closes, window=2, find_peaks=True)
    valleys = _find_local_extremes(closes, window=2, find_peaks=False)

    trend_state = 'range'
    trend_label = '横盘震荡'
    uptrend_end = False
    downtrend_end = False

    if len(peaks) >= 3 and len(valleys) >= 3:
        p3 = peaks[-3:]
        v3 = valleys[-3:]
        peak_rising = p3[0]['value'] < p3[1]['value'] < p3[2]['value']
        valley_rising = v3[0]['value'] < v3[1]['value'] < v3[2]['value']
        peak_falling = p3[0]['value'] > p3[1]['value'] > p3[2]['value']
        valley_falling = v3[0]['value'] > v3[1]['value'] > v3[2]['value']

        if peak_rising and not valley_rising and v3[2]['value'] < v3[1]['value']:
            uptrend_end = True
            trend_state = 'uptrend_end'
            trend_label = '上涨终结⚠️'
        elif valley_falling and not peak_falling and p3[2]['value'] > p3[1]['value']:
            downtrend_end = True
            trend_state = 'downtrend_end'
            trend_label = '下跌终结⚠️'
        elif peak_rising and valley_rising:
            trend_state = 'uptrend'
            trend_label = '上涨趋势'
        elif peak_falling and valley_falling:
            trend_state = 'downtrend'
            trend_label = '下跌趋势'
        else:
            trend_state = 'range'
            trend_label = '横盘震荡'

    above_peaks = [p for p in peaks if p['value'] > cur_price]
    below_valleys = [v for v in valleys if v['value'] < cur_price]
    nearest_resistance = min(above_peaks, key=lambda x: x['value'] - cur_price) if above_peaks else None
    nearest_support = max(below_valleys, key=lambda x: x['value']) if below_valleys else None

    base_scores = {
        'uptrend': 1.5, 'downtrend': -1.5, 'range': 0.0,
        'uptrend_end': -0.5, 'downtrend_end': 1.0,
    }
    rs_score = base_scores.get(trend_state, 0.0)
    if nearest_resistance:
        rd = (nearest_resistance['value'] - cur_price) / cur_price * 100
        if rd < 3 and rs_score > 0:
            rs_score -= 0.3
    if nearest_support:
        sd = (cur_price - nearest_support['value']) / cur_price * 100
        if sd < 3 and rs_score < 0:
            rs_score += 0.3
    rs_score = round(max(-2.0, min(2.0, rs_score)), 2)

    if rs_score > 0.5:
        rs_label = '结构支撑强'
    elif rs_score < -0.5:
        rs_label = '结构阻力强'
    elif rs_score > 0:
        rs_label = '偏多结构'
    elif rs_score < 0:
        rs_label = '偏空结构'
    else:
        rs_label = '结构均衡'

    chan_parts = []
    if trend_state in ('uptrend_end', 'downtrend_end'):
        if uptrend_end:
            chan_parts.append(f'顶抬高{p3[-1]["value"]:.4f}>{p3[-2]["value"]:.4f}')
            chan_parts.append(f'底降低{v3[-1]["value"]:.4f}<{v3[-2]["value"]:.4f}')
            chan_parts.append('分割线:上涨趋势可能终结')
        elif downtrend_end:
            chan_parts.append(f'底新低{v3[-1]["value"]:.4f}<{v3[-2]["value"]:.4f}')
            chan_parts.append(f'顶抬高{p3[-1]["value"]:.4f}>{p3[-2]["value"]:.4f}')
            chan_parts.append('分割线:下跌趋势可能终结')
    elif trend_state == 'uptrend':
        chan_parts.append(f'顶抬高{p3[-1]["value"]-p3[-2]["value"]:.4f}')
        chan_parts.append(f'底抬高{v3[-1]["value"]-v3[-2]["value"]:.4f}')
    elif trend_state == 'downtrend':
        chan_parts.append(f'顶降低{p3[-2]["value"]-p3[-1]["value"]:.4f}')
        chan_parts.append(f'底降低{v3[-2]["value"]-v3[-1]["value"]:.4f}')
    else:
        if peaks and valleys:
            chan_parts.append(f'最近顶{peaks[-1]["value"]:.4f}')
            chan_parts.append(f'最近底{valleys[-1]["value"]:.4f}')

    return {
        'trend_state': trend_state,
        'trend_label': trend_label,
        'chan_structure': ' | '.join(chan_parts),
        'rs_score': rs_score,
        'rs_label': rs_label,
        'nearest_resistance': {
            'price': round(nearest_resistance['value'], 4),
            'distance_pct': round((nearest_resistance['value'] - cur_price) / cur_price * 100, 2),
        } if nearest_resistance else None,
        'nearest_support': {
            'price': round(nearest_support['value'], 4),
            'distance_pct': round((cur_price - nearest_support['value']) / cur_price * 100, 2),
        } if nearest_support else None,
        'pivot_summary': {
            'last_peak': round(peaks[-1]['value'], 4) if peaks else None,
            'last_valley': round(valleys[-1]['value'], 4) if valleys else None,
            'n_peaks': len(peaks),
            'n_valleys': len(valleys),
        },
    }


def get_market_coefficient():
    """
    大盘系数权重 (v3.8 精简版)

    上证指数已加入跟踪列表(sh000001)，和个股用完全相同的体系。

    输出:
      1. 基础评分: judge_trend 日线趋势方向(0-16分)
      2. 拐点: 评分 + 大盘自身主导周期方向
      3. 大盘主导周期: detect_dominant_cycle
      4. 大盘结构: judge_wave_structure

    大盘上涨(13-16) → ×1.2  大盘偏多(10-12) → ×1.1
    大盘中性(7-9)   → ×1.0  大盘偏空(4-6)   → ×0.8
    大盘下跌(0-3)   → ×0.5
    """
    code = 'sh000001'
    daily_rows = read_csv(code, 'daily')
    if not daily_rows or len(daily_rows) < 60:
        return {
            'market_trend': {'direction': 'neutral', 'score': 8, 'label': '上证数据缺失'},
            'coefficient': 1.0,
            'label': '数据不足',
        }

    # ── 大盘基础评分（和个股完全一样） ──
    trend = judge_trend(code, daily_rows, 0)
    direction = trend.get('direction', 'neutral')
    score = trend.get('score', 8)

    # ── 大盘自身周期分析（判断主导量级和结构方向） ──
    position = judge_position(daily_rows)
    placeholder_trend = {'direction': 'neutral', 'confidence': 0}
    period_results = {}
    for period in PERIODS:
        result = analyze_period(code, period, position, placeholder_trend)
        if result:
            period_results[period] = result
    period_results['daily'] = analyze_period(code, 'daily', position, trend)

    dominant_info = detect_dominant_cycle(code, period_results)
    wave_struc = judge_wave_structure(code, period_results, dominant_info)

    dc_label = dominant_info.get('dominant_label', '')
    ws_direction = wave_struc.get('direction', '') if wave_struc else ''

    # ── 拐点: 评分 + 主导周期方向（不用额外维度） ──
    # 高分(>=10偏多以上) + 自身周期走弱(卖闭环) → 从强势回落
    # 低分(<=6偏空以下) + 自身周期走强(买闭环) → 从低位回升
    if score >= 10 and ws_direction == '卖闭环':
        inflection = '高位走弱'
        inflection_adj = -0.05
    elif score <= 6 and ws_direction == '买闭环':
        inflection = '低位走强'
        inflection_adj = 0.08
    else:
        inflection = '平稳'
        inflection_adj = 0.0

    # ── 系数（评分映射 + 拐点微调） ──
    if direction == 'bullish':
        base_coeff = 1.2
    elif direction == 'bullish_bias':
        base_coeff = 1.1
    elif direction == 'neutral':
        base_coeff = 1.0
    elif direction == 'bearish_bias':
        base_coeff = 0.8
    else:
        base_coeff = 0.5

    coeff = round(max(0.4, min(1.3, base_coeff + inflection_adj)), 2)
    label_parts = [trend.get('label', '')]
    if inflection != '平稳':
        label_parts.append(inflection)

    return {
        'market_trend': {
            'direction': direction,
            'score': score,
            'label': trend.get('label', ''),
        },
        'inflection': inflection,
        'inflection_adj': inflection_adj,
        'dominant_cycle': dc_label,
        'wave_direction': ws_direction,
        'coefficient': coeff,
        'label': '·'.join(label_parts),
    }


def analyze_period(code, period, position, trend):
    """
    第四层: 信号质量递进分析

    在已知位置+方向下，分析最近一段的信号是否形成了出击窗口
    """
    rows = read_csv(code, period)
    if not rows:
        return None

    anchors = extract_anchors(rows)

    # 排列熵分析：对趋势线做有序/无序检测
    trend_pe = analyze_trend_pe(rows, lookback=60)

    if not anchors:
        return {'period': period, 'period_label': PERIOD_LABELS[period],
                'anchors': 0, 'signal_quality': None, 'price_eff': None,
                'trend_pe': trend_pe}

    # 历史价格有效性（全量统计）
    pe = price_effectiveness(anchors, rows)

    # 最近N根K线的信号质量（递进分析，传入排列熵）
    sq = signal_quality(anchors, rows, position, trend,
                        lookback_klines=KLINES_LOOKBACK.get(period, 20),
                        trend_pe=trend_pe)

    return {
        'period': period,
        'period_label': PERIOD_LABELS[period],
        'anchors': len(anchors),
        'signal_quality': sq,
        'price_eff': pe,
    }


# ============================================================
# 主分析函数: 三层架构
# ============================================================

def analyze(code, name=''):
    """
    三层架构分析:
    1. 价格位置
    2. 趋势方向 (带日线闭环信号)
    3. 循环适配
    """
    daily_rows = read_csv(code, 'daily')

    # 第一层: 价格位置
    position = judge_position(daily_rows)

    # 先算日线闭环信号(placeholder趋势 → 日线 signal_quality → 买侧闭环level)
    placeholder_trend = {'direction': 'neutral', 'confidence': 0}
    daily_pre = analyze_period(code, 'daily', position, placeholder_trend)
    daily_buy_level = 0
    if daily_pre and daily_pre.get('signal_quality'):
        sq = daily_pre['signal_quality']
        daily_buy_level = sq.get('buy_level', 0)  # 只取买侧，不取max(buy,sell)

    # 第二层: 趋势方向 (传入日线买侧闭环level)
    trend = judge_trend(code, daily_rows, daily_buy_level)

    # 第三层: 各周期循环适配
    period_results = {}
    for period in PERIODS:
        result = analyze_period(code, period, position, trend)
        if result:
            period_results[period] = result

    # 日线用真实趋势重算（覆盖placeholder结果）
    period_results['daily'] = analyze_period(code, 'daily', position, trend) or daily_pre

    # ABCD 级别匹配: 日线MACD状态 → 最低操作周期
    macd_score = trend.get('macd_score', 2)
    if macd_score == 4:
        abcd_min_idx = 1  # A级: min5+, 一信号即可
    elif macd_score == 3:
        abcd_min_idx = 1  # B级: min5+, 需要★买+2金叉
    elif macd_score == 1:
        abcd_min_idx = 2  # C级: min15+, 需要★买+2金叉
    else:
        abcd_min_idx = 3  # D级: min30+, 等大级别底部

    # 主导量级检测: 波峰间距法
    dominant_info = detect_dominant_cycle(code, period_results)
    dominant_idx = PERIODS.index(dominant_info['dominant_cycle'])

    # 取高者: ABCD级别 vs 主导量级 → 实际最低操作级别
    actual_min_idx = max(abcd_min_idx, dominant_idx)

    # 找出最佳操作级别: 信号质量最高的（过滤低于实际最低操作级别的周期）
    best = None
    for i, period in enumerate(PERIODS):
        if i < actual_min_idx:
            continue  # 低于实际最低操作级别，跳过
        p = period_results.get(period)
        if not p or not p.get('signal_quality'):
            continue
        sq = p['signal_quality']
        if best is None or sq['level'] > best['signal_quality']['level']:
            best = p

    # 量价阶段标注
    volume_info = analyze_volume_regime(code, daily_rows, period_results)

    # 结构分析：主导量级方向+次级别浪结构+回调深度
    wave_structure = judge_wave_structure(code, period_results, dominant_info)

    # 指数级行情条件检测
    exp_readiness = detect_exponential_readiness(code, daily_rows, period_results, dominant_info)

    # 新增 v3.8: 缠论结构分析(阻支密度)
    rs_density = detect_rs_density(code, daily_rows)

    # 新增 v3.8: 大盘系数权重
    market_coeff = get_market_coefficient()

    # 综合操作建议（含主导量级+大盘系数）
    advice = _generate_advice(position, trend, best, period_results, dominant_info, market_coeff)

    return {
        'code': code,
        'name': name,
        'position': position,
        'trend': trend,
        'periods': period_results,
        'best_period': best,
        'advice': advice,
        'volume_regime': volume_info,
        'wave_structure': wave_structure,
        'exp_readiness': exp_readiness,
        'rs_density': rs_density,
        'market_coeff': market_coeff,
    }


def _grade_trend_signal(position, trend, best, all_periods, dominant_info=None, market_coeff=None):
    """
    按日线趋势+分钟信号强度分级，返回分级定性和建议

    分级逻辑:
      日线上涨+分钟闭环 → 可操作（🔴）
      日线中性+分钟强   → 中性偏强（🟡）
      日线中性+分钟弱   → 中性（🟢）
      日线下跌+分钟闭环 → 谨慎观望（⚪）
      日线下跌+分钟弱   → 观望（⚪）

    新增 v3.4: 跨周期共振检测
      5-15分钟评分相同时，检查30/60分钟有无活跃金叉
      有共振者分数上调，分级升档

    新增 v3.6: 主导量级(波峰间距法)
      传入 dominant_info 后自动写入描述

    返回:
      dict: {grade, grade_label, action, reason, min_signal_summary,
             wait_condition, resonance_score, dominant_cycle}
    """
    direction = trend['direction']
    zone = position['zone']
    risk = position['risk_level']
    close_price = position.get('close', 0)
    expma12 = position.get('expma12', 0)
    
    # Internal: find strongest minute signal for grading logic
    max_min_level = 0
    best_min_label = ''
    best_min_period = ''
    
    for period, p in all_periods.items():
        if not p or not p.get('signal_quality'):
            continue
        sq = p['signal_quality']
        lv = sq['level']
        if lv > max_min_level:
            max_min_level = lv
            best_min_label = sq['label']
            best_min_period = PERIOD_LABELS.get(period, period)
    
    # Smart signal summary: find important closed-loop for direction guidance
    min_signal_details = []
    sig_avail = {}
    for p in ['daily', 'min60', 'min30', 'min15', 'min5']:
        pp = all_periods.get(p)
        if pp and pp.get('signal_quality') and pp['signal_quality']['level'] >= 2.0:
            sig_avail[p] = pp['signal_quality']
    
    # Daily is the directional anchor (most important for direction)
    if 'daily' in sig_avail:
        d = sig_avail['daily']
        bl, sl = d.get('buy_level', 0), d.get('sell_level', 0)
        if bl >= 3.0 and sl >= 3.0:
            # 加净方向偏向: 买强/卖强/均势，让交替不矛盾
            if bl > sl + 0.5:
                pat = '★买卖交替(买略强)'
            elif sl > bl + 0.5:
                pat = '★买卖交替(卖略强)'
            else:
                pat = '★买卖交替(均势)'
        elif bl >= 3.0:
            pat = '★买密集'
        elif sl >= 3.0:
            pat = '★卖密集'
        elif bl >= 2.0 and sl >= 2.0:
            pat = '买卖博弈'
        elif bl >= 2.0:
            pat = '偏多'
        elif sl >= 2.0:
            pat = '偏空'
        elif bl > sl:
            pat = '偏多(弱)'
        elif sl > bl:
            pat = '偏空(弱)'
        else:
            pat = '无方向'
        min_signal_details.append(f'日线{pat}')
    
    # Best minute anchor (highest priority with >=2.0 signal)
    min_anchor = ''
    for p in ['min60', 'min30', 'min15']:
        if p in sig_avail:
            d = sig_avail[p]
            pn = p.replace('min', '') + '分'
            bl, sl = d.get('buy_level', 0), d.get('sell_level', 0)
            if bl >= 3.0 and sl >= 2.0:
                min_anchor = f'{pn}★买(买卖博弈)'
            elif bl >= 3.0:
                min_anchor = f'{pn}★买密集'
            elif sl >= 3.0:
                min_anchor = f'{pn}★卖密集'
            elif bl >= 2.0:
                min_anchor = f'{pn}偏多'
            elif sl >= 2.0:
                min_anchor = f'{pn}偏空'
            else:
                min_anchor = f'{pn}方向不明'
            break

    # 5-15min combo: if both have strong signals, override minute anchor
    if 'min15' in sig_avail and 'min5' in sig_avail:
        if sig_avail['min15']['level'] >= 3.5 and sig_avail['min5']['level'] >= 3.5:
            b15, s15 = sig_avail['min15'].get('buy_level',0), sig_avail['min15'].get('sell_level',0)
            b5, s5 = sig_avail['min5'].get('buy_level',0), sig_avail['min5'].get('sell_level',0)
            total_b = b15 + b5
            total_s = s15 + s5
            if total_b > total_s:
                min_anchor = '5-15分共振★买'
            elif total_s > total_b:
                min_anchor = '5-15分共振★卖'
            else:
                min_anchor = '5-15分共振博弈'
    
    if min_anchor:
        min_signal_details.append(min_anchor)

    # ===== 跨周期共振检测 =====
    # 层级结构: 5-15分钟(超短线) → 30-60分钟(短线) → 日线 → 周线
    # 规则: 同级信号强度相同时，上有层级金叉共振者更强
    """
    层级分组判断金叉共振:
      [5min, 15min] → 上层 [30min, 60min]
      [30min, 60min] → 上层 [daily]
      [daily] → 上层 [weekly] (暂无周线数据，预留)
    
    两个层级都有活跃金叉 = 共振
    """
    resonance_score = 0  # 共振加分，0=无，0.5=部分共振，1.0=强共振
    resonance_desc = ''
    
    def _check_golden(period):
        """检查某周期是否有活跃金叉"""
        p = all_periods.get(period)
        if not p or not p.get('signal_quality'):
            return False
        ecs = p['signal_quality'].get('ema_cross_status')
        if not ecs:
            return False
        has_golden = ecs.get('has_recent_golden', False)
        # 活跃金叉：last_golden_idx 在最近的数据中
        if has_golden and ecs.get('last_golden_idx', -1) >= 0:
            return True
        return False
    
    def _check_dead(period):
        """检查某周期是否有活跃死叉"""
        p = all_periods.get(period)
        if not p or not p.get('signal_quality'):
            return False
        ecs = p['signal_quality'].get('ema_cross_status')
        if not ecs:
            return False
        return ecs.get('has_recent_dead', False) and ecs.get('last_dead_idx', -1) >= 0
    
    # 判断上层周期共振
    short_golden = _check_golden('min5') or _check_golden('min15')
    mid_golden = _check_golden('min30') or _check_golden('min60')
    daily_golden = _check_golden('daily')
    
    # 方向匹配：上涨/偏多看金叉，下跌/偏空看死叉
    if direction in ('bullish', 'bullish_bias'):
        has_active = short_golden and mid_golden
        if has_active:
            resonance_score = 0.8
            resonance_desc = '多周期金叉共振'
        elif mid_golden:
            resonance_score = 0.3
            resonance_desc = '短线层金叉活跃'
    elif direction in ('bearish', 'bearish_bias'):
        has_active = _check_dead('min5') or _check_dead('min15')
        has_mid_dead = _check_dead('min30') or _check_dead('min60')
        if has_active and has_mid_dead:
            resonance_score = 0.8
            resonance_desc = '多周期死叉共振'
    # 中性方向: 看哪边共振更强
    has_bull_resonance = short_golden and mid_golden
    has_bear_resonance = (_check_dead('min5') or _check_dead('min15')) and (_check_dead('min30') or _check_dead('min60'))
    
    # 中性方向下判断金叉vs死叉数量对比
    def _golden_dead_ratio():
        """计算30+60分钟金叉数 vs 死叉数的绝对值"""
        gc = 0; dc = 0
        for p in ['min30','min60']:
            pp = all_periods.get(p)
            if not pp or not pp.get('signal_quality'): continue
            ecs = pp['signal_quality'].get('ema_cross_status')
            if not ecs: continue
            gc += ecs.get('golden_count', 0)
            dc += ecs.get('dead_count', 0)
        return gc, dc
    
    if has_bull_resonance and not has_bear_resonance:
        resonance_score = 0.7
        resonance_desc = '多周期金叉共振(中性背景)'
    elif has_bear_resonance and not has_bull_resonance:
        resonance_score = -0.5
        resonance_desc = '多周期死叉共振(警示)'
    elif has_bull_resonance and has_bear_resonance:
        # 都有活跃时，看最近的金叉vs死叉哪个更新
        def _last_status():
            """返回30+60分钟最近的金叉和死叉的idx"""
            last_g = -1; last_d = -1
            for p in ['min30','min60']:
                pp = all_periods.get(p)
                if not pp or not pp.get('signal_quality'): continue
                ecs = pp['signal_quality'].get('ema_cross_status')
                if not ecs: continue
                if ecs.get('last_golden_idx', -1) > last_g:
                    last_g = ecs['last_golden_idx']
                if ecs.get('last_dead_idx', -1) > last_d:
                    last_d = ecs['last_dead_idx']
            return last_g, last_d
        last_g, last_d = _last_status()
        if last_g > last_d and last_g >= 0:
            resonance_score = 0.6
            resonance_desc = '最后活动为金叉'
        elif last_d >= last_g and last_d >= 0:
            resonance_score = -0.2
            resonance_desc = '最后活动为死叉(偏空)'
        else:
            resonance_score = 0.2
            resonance_desc = '金叉死叉均活跃'

    # 第二级共振: 当最佳周期是 min30/min60 时，检查日线金叉/死叉
    if best and best.get('period') in ('min30', 'min60'):
        if direction in ('bullish', 'bullish_bias') and daily_golden:
            if resonance_score < 0.5:
                resonance_score = max(resonance_score, 0.3)
            if '日线' not in resonance_desc:
                resonance_desc += ('; ' if resonance_desc else '') + '日线金叉共振'
        elif direction in ('bearish', 'bearish_bias') and _check_dead('daily'):
            if resonance_score < 0.5:
                resonance_score = max(resonance_score, 0.3)
            if '日线' not in resonance_desc:
                resonance_desc += ('; ' if resonance_desc else '') + '日线死叉共振'
        elif direction == 'neutral':
            if daily_golden and not _check_dead('daily'):
                if resonance_score < 0.5:
                    resonance_score = max(resonance_score, 0.3)
                if '日线' not in resonance_desc:
                    resonance_desc += ('; ' if resonance_desc else '') + '日线金叉活跃'
            elif _check_dead('daily') and not daily_golden:
                if resonance_score > -0.5:
                    resonance_score = min(resonance_score, -0.2)
                if '日线' not in resonance_desc:
                    resonance_desc += ('; ' if resonance_desc else '') + '日线死叉活跃'

    # best 可能是日线或分钟线
    best_label = ''
    best_level = 0
    if best and best.get('signal_quality'):
        best_label = best['signal_quality']['label']
        best_level = best['signal_quality']['level']

    # ── 大盘系数调整: 调整等级阈值用于分级判断 ──
    mc = (market_coeff or {}).get('coefficient', 1.0) if isinstance(market_coeff, dict) else 1.0
    adj_level = round(best_level * mc, 1)
    adj_max_level = round(max_min_level * mc, 1)

    # 日线等级
    bullish_directions = ('bullish', 'bullish_bias')
    bearish_directions = ('bearish', 'bearish_bias')
    
    # 极端位置优先处理
    if zone == 'high' and risk == 'critical':
        if direction in bullish_directions:
            dc_wait = ''
            if dominant_info and dominant_info.get('dominant_label'):
                dc_wait = dominant_info['dominant_label']
            else:
                dc_wait = '分钟'
            return _grade_output('observe_strong', '强势观望', '持有/减仓',
                '多头趋势+高位加速区，强势标的等回调入场',
                min_signal_details, f'回调EXPMA12后{dc_wait}找★买')
        else:
            return _grade_output('avoid', '风险', '回避',
                '高位+弱势=风险极大',
                min_signal_details, '等下跌动能释放完毕')
    
    if zone == 'low' and risk == 'critical':
        if direction in bearish_directions:
            if adj_level >= 3.0:
                return _grade_output('observe_weak', '弱势观望', '关注抄底',
                    '超跌+信号积累，但趋势未转多，等转折确认',
                    min_signal_details, '等日线MACD金叉+★买出现')
            else:
                return _grade_output('avoid', '风险', '等待',
                    '超跌但信号不充分，勿接飞刀',
                    min_signal_details, '等60分钟/日线出现★买+金叉闭环')
        else:
            return _grade_output('observe', '观望', '轻仓试多',
                '低位+方向好转，可逐步建仓',
                min_signal_details, '等5-15分钟信号加强确认')
    
    # 日线定档
    if direction in bullish_directions:
        if adj_level >= 3.0:
            # 偏多但要检查结构：MACD不能死叉、价格不能在白线下
            if direction == 'bullish_bias':
                macd_ok = trend.get('macd_score', 0) >= 2
                price_ok = close_price > expma12 if close_price and expma12 else True
                if not macd_ok or not price_ok:
                    weak_reason = []
                    if not macd_ok: weak_reason.append('MACD死叉')
                    if not price_ok: weak_reason.append('价破EXPMA白线')
                    return _grade_output('observe', '关注', '轻仓试错',
                        f'{best_period_label(best)}有{best_label}，但{"+".join(weak_reason)}，只轻仓试错等确认',
                        min_signal_details, '等MACD走好+价格站回白线', resonance_score)
            if resonance_score >= 0.7:
                return _grade_output('actionable', '可操作', '顺势做多',
                    f'{best_period_label(best)}有{best_label}+{resonance_desc}，共振确认',
                    min_signal_details, '', resonance_score)
            else:
                return _grade_output('actionable', '可操作', '顺势做多',
                    f'{best_period_label(best)}有{best_label}，顺势跟进',
                    min_signal_details, '', resonance_score)
        elif adj_level >= 2.0:
            if resonance_score >= 0.7:
                return _grade_output('neutral_strong', '中性偏强', '关注做多',
                    f'{best_period_label(best)}有{best_label}+{resonance_desc}，信号可信度提高',
                    min_signal_details, '等信号加强后加仓', resonance_score)
            else:
                return _grade_output('observe', '关注', '等待加强',
                    f'{best_period_label(best)}有信号但级别不够，等加强再动手',
                    min_signal_details, '等★买密集+金叉出现', resonance_score)
        else:
            if resonance_score >= 0.7:
                return _grade_output('observe', '关注', '观察共振',
                    f'无强信号但{resonance_desc}，观察后续',
                    min_signal_details, '等分钟级出现★买+金叉闭环', resonance_score)
            else:
                return _grade_output('observe', '关注', '观望',
                    '多头但无出击信号，等待',
                    min_signal_details, '等分钟级出现★买+金叉闭环', resonance_score)

    elif direction in bearish_directions:
        if adj_level >= 3.0:
            if resonance_score >= 0.7:
                return _grade_output('avoid', '风险', '回避',
                    f'下跌+{resonance_desc}，调整确认，不可逆势',
                    min_signal_details, '等日线MACD金叉+★买+金叉确认', resonance_score)
            else:
                return _grade_output('observe_weak', '弱势观望', '等待转折',
                    '下跌趋势+信号积累中，等转折确认',
                    min_signal_details, '等日线MACD金叉+★买+金叉确认', resonance_score)
        elif adj_level >= 2.0:
            if resonance_score >= 0.7:
                return _grade_output('avoid', '风险', '回避',
                    f'下跌+{resonance_desc}，趋势延续',
                    min_signal_details, '等60分钟/日线出现★买+金叉闭环', resonance_score)
            else:
                return _grade_output('avoid', '观望', '等待',
                    '下跌趋势延续，等底部结构成形',
                    min_signal_details, '等60分钟/日线出现★买+金叉闭环', resonance_score)
        else:
            return _grade_output('avoid', '观望', '不参与',
                '空头+无信号，勿抄底',
                min_signal_details, '等日线MACD转正+★买出现', resonance_score)

    # 震荡/中性
    else:
        if adj_max_level >= 4.0:
            # 有共振加分 → 升档或加强描述
            if resonance_score >= 0.7:
                return _grade_output('actionable', '可操作', '顺势做多',
                    f'5-15分钟闭环密集({best_min_period}:{best_min_label})+{resonance_desc}，可择机做多',
                    min_signal_details, '', resonance_score)
            elif resonance_score >= 0.3:
                return _grade_output('resonant_strong', '共振偏强', '高抛低吸/偏多',
                    f'5-15分钟闭环密集({best_min_period}:{best_min_label})+{resonance_desc}，偏多操作',
                    min_signal_details, '日线趋势转多后可加仓', resonance_score)
            else:
                return _grade_output('neutral_strong', '中性偏强', '高抛低吸',
                    f'5-15分钟闭环密集({best_min_period}:{best_min_label})，等待日线向上选方向',
                    min_signal_details, '日线趋势转多后可加仓', resonance_score)
        elif adj_max_level >= 3.0:
            if resonance_score >= 0.7:
                return _grade_output('neutral_strong', '共振偏强', '高抛低吸/偏多',
                    f'{best_min_period}有{best_min_label}+{resonance_desc}，可偏多操作',
                    min_signal_details, '等日线MACD金叉确认方向', resonance_score)
            else:
                return _grade_output('neutral_bias', '中性偏强', '高抛低吸',
                    f'{best_min_period}有{best_min_label}，日线横盘中可做T',
                    min_signal_details, '等日线MACD金叉确认方向', resonance_score)
        elif adj_max_level >= 2.0:
            return _grade_output('neutral', '中性', '小仓做T',
                f'{best_min_period}有{best_min_label}，轻仓参与',
                min_signal_details, '等分钟级信号加强再加大仓位')
        else:
            return _grade_output('neutral_weak', '中性', '观望',
                '震荡但信号不足',
                min_signal_details, '等5-15分钟出现★买+金叉闭环')


def _grade_output(grade, grade_label, action, reason, min_details, wait, resonance_score=0):
    return {
        'grade': grade,
        'grade_label': grade_label,
        'action': action,
        'reason': reason,
        'min_signal_summary': ' → '.join(min_details) if min_details else '无分钟闭环信号',
        'wait_condition': wait,
        'resonance_score': resonance_score,
    }


def best_period_label(best):
    """取最佳周期的中文名"""
    if best is None:
        return '无'
    return best.get('period_label', '未知')


_actions = {
    'bullish_strong': '出击加注' if False else '顺势做多',  # placeholder, will refine
}


def _generate_advice(position, trend, best, all_periods, dominant_info=None, market_coeff=None):
    """旧版兼容，现在是_grade_trend_signal的薄封装，透传所有字段"""
    g = _grade_trend_signal(position, trend, best, all_periods, dominant_info, market_coeff)
    wait_part = f" 提示: {g['wait_condition']}" if g['wait_condition'] else ''

    # 主导量级补充文案
    dominant_note = ''
    if dominant_info and dominant_info.get('dominant_cycle'):
        dc = dominant_info['dominant_label']
        stretched = dominant_info.get('stretched_periods', [])
        if stretched:
            ignore_list = ', '.join(stretched)
            trend_d = trend.get('direction', '')
            if trend_d in ('bullish', 'bullish_bias'):
                dominant_note = f' | {dc}主导(小级卖信号暂不采信)'
            elif trend_d in ('bearish', 'bearish_bias', 'bearish'):
                dominant_note = f' | {dc}主导(小级买信号暂不采信)'
            else:
                dominant_note = f' | {dc}主导(小级反向暂不采信)'
        else:
            dominant_note = f' | 主导量级{dc}'

    return {
        'grade': g['grade'],
        'grade_label': g['grade_label'],
        'action': g['action'],
        'reason': g['reason'] + wait_part + dominant_note,
        'min_signal_summary': g['min_signal_summary'],
        'wait_condition': g['wait_condition'],
        'resonance_score': g.get('resonance_score', 0),
        'dominant_cycle': dominant_info,
        'confidence': '高' if g['grade'] in ('actionable','resonant_strong','observe_strong','neutral_strong') else '中',
    }


# ============================================================
# 批量分析与输出
# ============================================================

def analyze_all():
    codes = get_all_codes()
    name_map = get_name_map()
    results = [analyze(code, name_map.get(code, code)) for code in codes]
    # 按分级排序: 可操作→共振偏强→强势观望→中性偏强→中性→关注→弱势→观望
    grade_order = {'observe_strong': 0, 'actionable': 1, 'resonant_strong': 2, 'neutral_strong': 3, 'neutral_bias': 4,
                   'neutral': 5, 'neutral_weak': 6, 'observe': 7, 'observe_weak': 8, 'avoid': 9}
    def sort_key(r):
        g = r.get('advice', {}).get('grade', 'neutral')
        rs = r.get('advice', {}).get('resonance_score', 0)
        return (grade_order.get(g, 99), -rs)  # -rs 让高分排在前面
    results.sort(key=sort_key)
    return results


def _fmt_price_eff(pe):
    if not pe or pe['buy_samples'] + pe['sell_samples'] == 0:
        return '无样本'
    parts = []
    if pe['buy_samples']:
        parts.append(f"★买后均{pe['buy_avg_pct']:+.1f}%({pe['buy_hit_rate']}%涨)")
    if pe['sell_samples']:
        parts.append(f"★卖后均{pe['sell_avg_pct']:+.1f}%({pe['sell_hit_rate']}%跌)")
    return ', '.join(parts)


G = {
    'actionable': ('🔴', '可操作', '日线上涨+分钟闭环确认'),
    'resonant_strong': ('🟠', '共振偏强', '日线横盘+有共振'),
    'observe_strong': ('🟠', '强势观望', '多头趋势但暂无买点'),
    'neutral_strong': ('🟡', '中性偏强', '日线横盘+分钟信号密集'),
    'neutral_bias': ('🟡', '中性偏强', '日线横盘+分钟有信号'),
    'neutral': ('🟢', '中性', '日线横盘+分钟信号一般'),
    'neutral_weak': ('🟢', '中性偏弱', '日线横盘+分钟无信号'),
    'observe': ('⚪', '关注', '等待确认'),
    'observe_weak': ('⚪', '弱势观望', '下跌趋势等待转折'),
    'avoid': ('⚪', '观望', '弱势建议回避'),
}


def _fmt_signal_icon(level):
    if level >= 4.0: return '🔥🔥🔥'
    if level >= 3.0: return '🔥🔥'
    if level >= 2.0: return '🔥'
    if level >= 1.0: return '⚡'
    return '--'


def _fmt_periods_detail(period_results, best):
    """生成各周期详情行"""
    lines = []
    lines.append('  [各周期信号]')
    for period in PERIODS:
        p = period_results.get(period) if period_results else None
        if not p or not p.get('signal_quality'):
            lines.append(f'    [{PERIOD_LABELS.get(period,period):>4}] --  无出击信号')
            continue
        sq = p['signal_quality']
        pe = p.get('price_eff')
        fire = _fmt_signal_icon(sq.get('level', 0))
        mk = ' <<<' if best and best['period'] == period else ''
        details = ', '.join(sq.get('details', []))
        lines.append(f'    [{p.get("period_label",""):>4}] {fire} {sq["label"]:>8} | {details}{mk}')
        price_str = _fmt_price_eff(pe)
        if price_str and price_str != '无样本':
            lines.append(f'          价格: {price_str}')
    return lines


def format_report(results):
    lines = []
    lines.append('=' * 92)
    lines.append('[周期循环分析] Cycle Engine v3.8 — 多层共振链 + 缠论结构 + 大盘系数')
    lines.append('=' * 92)

    # 大盘系数（全局，第一个结果中有）
    mc = results[0].get('market_coeff', {}) if results else {}
    if mc:
        m_label = mc.get('label', '')
        m_trend = mc.get('market_trend', {})
        m_score = m_trend.get('score', '?')
        m_inflect = mc.get('inflection', '')
        m_dc = mc.get('dominant_cycle', '')
        m_ws = mc.get('wave_direction', '')
        lines.append(f'  大盘环境: {m_label} (评分{m_score})')
        if m_inflect and m_inflect != '平稳':
            lines.append(f'  拐点: {m_inflect}')
        if m_dc:
            lines.append(f'  大盘主导: {m_dc} | {m_ws}')
        lines.append('')

    # 按分级分组
    grade_order = ['observe_strong', 'actionable', 'resonant_strong', 'neutral_strong', 'neutral_bias', 'neutral', 'neutral_weak', 'observe', 'observe_weak', 'avoid']
    by_grade = {}
    for r in results:
        g = r.get('advice', {}).get('grade', 'neutral')
        by_grade.setdefault(g, []).append(r)

    for gk in grade_order:
        grp = by_grade.get(gk, [])
        if not grp:
            continue
        icon, label, desc = G.get(gk, ('','',''))
        lines.append(f'\n{"─" * 92}')
        lines.append(f'{icon} [{label}] ({len(grp)} 只) — {desc}')
        lines.append(f'{"─" * 92}')

        for r in grp:
            pos = r['position']
            trd = r['trend']
            code = r['code']
            name = r['name']
            adv = r.get('advice', {})

            close = pos.get('close', '?')
            trend_lbl = trd.get('label', '?')
            action = adv.get('action', '?')
            reason = adv.get('reason', '?')
            summary = adv.get('min_signal_summary', '?')
            wc = adv.get('wait_condition', '')

            lines.append(f'\n  * {code} {name}')
            lines.append(f'    收盘: {close} | 日线: {trend_lbl} | 分钟闭环: {summary}')

            # 主导量级展示
            dc = adv.get('dominant_cycle')
            if dc and dc.get('dominant_cycle'):
                dc_label = dc['dominant_label']
                dc_detail = dc.get('detail', '')
                lines.append(f'    主导量级: {dc_label} | {dc_detail}')
                stretched = dc.get('stretched_periods', [])
                if stretched:
                    lines.append(f'    ⚠ 忽略{",".join(stretched)}反向信号(被{dc_label}趋势吸收)')

            # 量价阶段标注
            vi = r.get('volume_regime')
            if vi and vi.get('phase') not in ('数据不足', '正常放量'):
                lines.append(f'    量价: {vi["phase"]} | {vi["detail"]}')

            # 结构分析（一句话）
            ws = r.get('wave_structure')
            if ws:
                lines.append(f'    结构: {ws["structure"]}')
                lines.append(f'          {ws["detail"]}')

            # 指数级条件检测
            er = r.get('exp_readiness')
            if er:
                p = er.get('persist', {})
                p_str = ''
                cd = p.get('compress_days', 0)
                if cd > 0:
                    p_str += f'压缩{cd}天 '
                if p.get('direction_align', ''):
                    p_str += p['direction_align']
                p_full = f' [{p_str.strip()}]' if p_str else ''
                lines.append(f'    量级引擎: {er["traffic_light"]} ({er["total_score"]}/10){p_full}')
                lines.append(f'              {er["detail"]}')

            # 缠论结构分析 (v3.8 新增)
            rs = r.get('rs_density')
            if rs and rs.get('rs_label') not in ('未知', '结构均衡'):
                parts = [f'{rs["rs_label"]} ({rs["rs_score"]})']
                nr = rs.get('nearest_resistance')
                ns = rs.get('nearest_support')
                if nr:
                    parts.append(f'上压{nr["price"]}(-{nr["distance_pct"]}%)')
                if ns:
                    parts.append(f'下撑{ns["price"]}(+{ns["distance_pct"]}%)')
                lines.append(f'    缠论结构: {" | ".join(parts)}')
                cs = rs.get('chan_structure', '')
                if cs:
                    lines.append(f'              {cs}')

            lines.append(f'    → {action}: {reason}')
            if wc:
                lines.append(f'    等: {wc}')

            # 各周期详细信号
            lines.extend(_fmt_periods_detail(r.get('periods', {}), r.get('best_period')))

    lines.append(f'\n{"=" * 92}')
    lines.append(f'[分析完成] {len(results)} 只标的')
    lines.append(f'{"=" * 92}')

    return '\n'.join(lines)


def save_results(results):
    clean = []
    for r in results:
        # 对 periods 做轻量化：只保留排列熵+信号质量数据
        periods_clean = {}
        for pname, pdata in r.get('periods', {}).items():
            if not pdata:
                continue
            period_entry = {
                'signal_quality': None,
                'trend_pe': None,
            }
            sq = pdata.get('signal_quality')
            if sq and isinstance(sq, dict):
                period_entry['signal_quality'] = {
                    'level': sq.get('level', 0),
                    'label': sq.get('label', ''),
                    'details': sq.get('details', []),
                    'buy_level': sq.get('buy_level', 0),
                    'sell_level': sq.get('sell_level', 0),
                    'trend_pe': sq.get('trend_pe'),
                }
            else:
                # 直接存 trend_pe（无信号但PE数据还在）
                period_entry['trend_pe'] = pdata.get('trend_pe')
            periods_clean[pname] = period_entry

        clean.append({
            'code': r['code'],
            'name': r['name'],
            'position': {k: v for k, v in r['position'].items()
                        if isinstance(v, (str, int, float, bool, type(None)))},
            'trend': {k: v for k, v in r['trend'].items()
                     if isinstance(v, (str, int, float, bool, list, type(None)))},
            'periods': periods_clean,
            'best_period': r['best_period']['period'] if r['best_period'] else None,
            'best_signal_level': r['best_period']['signal_quality']['level'] if r['best_period'] and r['best_period'].get('signal_quality') else 0,
            'advice': r['advice'],
            'rs_density': r.get('rs_density'),
            'market_coeff': r.get('market_coeff'),
        })
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    print(f'\n[Saving] {OUTPUT_PATH}')


# ============================================================
# 辅助函数
# ============================================================

def safe_float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    if len(sys.argv) > 1 and sys.argv[1] in ('--help', '-h', '/?'):
        print(__doc__)
        sys.exit(0)

    elif len(sys.argv) > 1 and sys.argv[1] == '--save':
        results = analyze_all()
        print(format_report(results))
        save_results(results)

    elif len(sys.argv) > 1:
        code = sys.argv[1]
        nm = get_name_map()
        result = analyze(code, nm.get(code, code))
        print(format_report([result]))

    else:
        results = analyze_all()
        print(format_report(results))

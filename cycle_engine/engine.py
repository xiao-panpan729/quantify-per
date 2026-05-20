# -*- coding: utf-8 -*-
"""
cycle_engine 综合引擎 — 单周期分析 / 单标的分析 / 全量分析 / 大盘系数
"""
import json
import time
from .utils import (read_csv, get_all_codes, get_name_map,
                     SNAPSHOT_DIR, PERIODS, PERIOD_LABELS, KLINES_LOOKBACK)
from .indicators import (analyze_trend_pe, judge_position, judge_trend,
                          extract_anchors, price_effectiveness, signal_quality,
                          check_rhythm_integrity, scan_resonance)
from .cycle_structure import (cycle_pattern, detect_dominant_cycle,
                               analyze_volume_regime, judge_wave_structure,
                               detect_exponential_readiness, detect_rs_density)
from .grading import _generate_advice


# ============================================================
# 大盘系数（模块级缓存，一次会话只算一次）
# ============================================================

def get_market_coefficient():
    """
    大盘系数权重 (v3.8 精简版)

    上证指数已加入跟踪列表(sh000001)，和个股用完全相同的体系。

    输出:
      1. 基础评分: judge_trend 日线趋势方向(0-16分)
      2. 拐点: 评分 + 大盘自身主导周期方向
      3. 大盘主导周期: detect_dominant_cycle
      4. 大盘结构: judge_wave_structure

    大盘上涨(13-16) → x1.2  大盘偏多(10-12) → x1.1
    大盘中性(7-9)   → x1.0  大盘偏空(4-6)   → x0.8
    大盘下跌(0-3)   → x0.5
    """
    # 批次内缓存：同一 run 内多次调用不重复算
    if get_market_coefficient._cache is not None:
        return get_market_coefficient._cache

    code = 'sh000001'
    cached_rows = {p: read_csv(code, p) for p in PERIODS}
    daily_rows = cached_rows.get('daily', [])
    if not daily_rows or len(daily_rows) < 60:
        result = {
            'market_trend': {'direction': 'neutral', 'score': 8, 'label': '上证数据缺失'},
            'coefficient': 1.0,
            'label': '数据不足',
        }
        get_market_coefficient._cache = result
        return result

    # 大盘基础评分（和个股完全一样）
    trend = judge_trend(code, daily_rows, 0)
    direction = trend.get('direction', 'neutral')
    score = trend.get('score', 8)

    # 大盘自身周期分析
    position = judge_position(daily_rows)
    placeholder_trend = {'direction': 'neutral', 'confidence': 0}
    period_results = {}
    for period in PERIODS:
        result = analyze_period(code, period, position, placeholder_trend,
                                _rows=cached_rows.get(period))
        if result:
            period_results[period] = result
    period_results['daily'] = analyze_period(code, 'daily', position, trend,
                                              _rows=cached_rows.get('daily'))

    dominant_info = detect_dominant_cycle(code, period_results,
                                           _cached_rows=cached_rows)
    wave_struc = judge_wave_structure(code, period_results, dominant_info,
                                       _cached_rows=cached_rows)

    dc_label = dominant_info.get('dominant_label', '')
    ws_direction = wave_struc.get('direction', '') if wave_struc else ''

    # 拐点: 评分 + 主导周期方向
    if score >= 10 and ws_direction == '卖闭环':
        inflection = '高位走弱'
        inflection_adj = -0.05
    elif score <= 6 and ws_direction == '买闭环':
        inflection = '低位走强'
        inflection_adj = 0.08
    else:
        inflection = '平稳'
        inflection_adj = 0.0

    # 系数
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

    result = {
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
    get_market_coefficient._cache = result
    return result

get_market_coefficient._cache = None


# ============================================================
# 单周期分析
# ============================================================

def analyze_period(code, period, position, trend, _rows=None):
    """
    第四层: 信号质量递进分析

    在已知位置+方向下，分析最近一段的信号是否形成了出击窗口。
    _rows: 可选，预读取的行数据，避免重复读 CSV
    """
    rows = _rows if _rows is not None else read_csv(code, period)
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

    优化: 所有周期 CSV 只读一次，通过 _rows/_cached_rows 向下传递
    """
    # ── 一次性预读全部周期 CSV ──
    cached_rows = {p: read_csv(code, p) for p in PERIODS}
    daily_rows = cached_rows.get('daily', [])

    # 第一层: 价格位置
    position = judge_position(daily_rows)

    # 先算日线闭环信号(placeholder趋势 → 日线 signal_quality → 买侧闭环level)
    placeholder_trend = {'direction': 'neutral', 'confidence': 0}
    daily_pre = analyze_period(code, 'daily', position, placeholder_trend,
                                _rows=daily_rows)
    daily_buy_level = 0
    if daily_pre and daily_pre.get('signal_quality'):
        sq = daily_pre['signal_quality']
        daily_buy_level = sq.get('buy_level', 0)

    # 第二层: 趋势方向 (传入日线买侧闭环level)
    trend = judge_trend(code, daily_rows, daily_buy_level)

    # 第三层: 各周期循环适配
    period_results = {}
    for period in PERIODS:
        result = analyze_period(code, period, position, trend,
                                _rows=cached_rows.get(period))
        if result:
            period_results[period] = result

    # 日线用真实趋势重算（覆盖placeholder结果）
    period_results['daily'] = analyze_period(code, 'daily', position, trend,
                                              _rows=daily_rows) or daily_pre

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
    dominant_info = detect_dominant_cycle(code, period_results,
                                           _cached_rows=cached_rows)
    dominant_idx = PERIODS.index(dominant_info['dominant_cycle'])

    # ── 跨周期对称增强/压制 ──
    # 节奏完整 → 增强小周期同向信号；节奏破坏 → 压制小周期同向信号
    # 节奏破坏时反向信号可能为反转信号，标记关注
    direction = trend.get('direction', 'neutral')
    rhythm = check_rhythm_integrity(period_results, direction)
    resonance = scan_resonance(period_results, rhythm, direction)

    bullish_dirs = ('bullish', 'bullish_bias')
    bearish_dirs = ('bearish', 'bearish_bias')
    is_bullish = direction in bullish_dirs
    is_bearish = direction in bearish_dirs
    rhythm_verdict = rhythm.get('verdict', 'intact')
    res_confirmed = resonance.get('resonance_confirmed', False)
    res_side = resonance.get('resonance_side', 'neutral')

    for i, period in enumerate(PERIODS):
        p = period_results.get(period)
        if not p or not p.get('signal_quality'):
            continue
        sq = p['signal_quality']
        curr_buy = sq.get('buy_level', 0)
        curr_sell = sq.get('sell_level', 0)
        if curr_buy < 1.0 and curr_sell < 1.0:
            continue

        for j in range(i + 1, min(i + 3, len(PERIODS))):
            larger = period_results.get(PERIODS[j])
            if not larger or not larger.get('signal_quality'):
                continue
            lsq = larger['signal_quality']
            gap = j - i  # 1=大一级, 2=大两级

            # ── 同向增强: 大周期 rhythm intact + 小周期同向信号 ──
            if is_bullish and curr_buy > 1.0 and lsq.get('buy_level', 0) > 1.0:
                if rhythm_verdict in ('intact', 'tactical_broken'):
                    if macd_score >= 3:
                        boost = 0.10 if gap == 1 else 0.20
                    elif macd_score == 1:
                        boost = 0.20 if gap == 1 else 0.35
                    else:
                        boost = 0.30 if gap == 1 else 0.50
                    gain = curr_buy * boost
                    sq['buy_level'] = min(10, curr_buy + gain)
                    sq.setdefault('details', []).append(
                        f'同向增强:大{PERIODS[j]}买→买强度+{(boost*100):.0f}%')
                else:
                    discount = 0.30 if gap == 1 else 0.50
                    sq['buy_level'] = max(0, curr_buy - curr_buy * discount)
                    sq.setdefault('details', []).append(
                        f'节奏破坏:大{PERIODS[j]}卖→买强度降{(discount*100):.0f}%')

            elif is_bearish and curr_sell > 1.0 and lsq.get('sell_level', 0) > 1.0:
                if rhythm_verdict in ('intact', 'tactical_broken'):
                    if macd_score >= 3:
                        boost = 0.10 if gap == 1 else 0.20
                    elif macd_score == 1:
                        boost = 0.20 if gap == 1 else 0.35
                    else:
                        boost = 0.30 if gap == 1 else 0.50
                    gain = curr_sell * boost
                    sq['sell_level'] = min(10, curr_sell + gain)
                    sq.setdefault('details', []).append(
                        f'同向增强:大{PERIODS[j]}卖→卖强度+{(boost*100):.0f}%')
                else:
                    discount = 0.30 if gap == 1 else 0.50
                    sq['sell_level'] = max(0, curr_sell - curr_sell * discount)
                    sq.setdefault('details', []).append(
                        f'节奏破坏:大{PERIODS[j]}买→卖强度降{(discount*100):.0f}%')

            # ── 反向压制: 大周期反方向信号 → 压小周期 ──
            if curr_buy > 1.0 and lsq.get('sell_level', 0) > 1.0:
                if macd_score >= 3:
                    discount = 0.10 if gap == 1 else 0.20
                elif macd_score == 1:
                    discount = 0.40 if gap == 1 else 0.65
                else:
                    discount = 0.55 if gap == 1 else 0.80
                sq['buy_level'] = max(0, curr_buy - curr_buy * discount)
                sq.setdefault('details', []).append(
                    f'反向压制:大{PERIODS[j]}卖→买强度降{(discount*100):.0f}%')

            if curr_sell > 1.0 and lsq.get('buy_level', 0) > 1.0:
                if macd_score >= 3:
                    discount = 0.10 if gap == 1 else 0.20
                elif macd_score == 1:
                    discount = 0.40 if gap == 1 else 0.65
                else:
                    discount = 0.55 if gap == 1 else 0.80
                sq['sell_level'] = max(0, curr_sell - curr_sell * discount)
                sq.setdefault('details', []).append(
                    f'反向压制:大{PERIODS[j]}买→卖强度降{(discount*100):.0f}%')

    # ── 5+15 共振增强 ──
    if res_confirmed:
        m5 = period_results.get('min5')
        m15 = period_results.get('min15')
        if m5 and m5.get('signal_quality'):
            m5_sq = m5['signal_quality']
            boost = 0.20 if rhythm_verdict == 'intact' else 0.10
            if res_side == 'buy':
                m5_sq['buy_level'] = min(10, m5_sq.get('buy_level', 0) * 1.20)
                m5_sq.setdefault('details', []).append('5+15买共振确认✓')
            elif res_side == 'sell':
                m5_sq['sell_level'] = min(10, m5_sq.get('sell_level', 0) * 1.20)
                m5_sq.setdefault('details', []).append('5+15卖共振确认✓')
            elif res_side in ('buy_reversal', 'sell_reversal'):
                m5_sq.setdefault('details', []).append('⚠5+15反向共振=反转预警')
        if m15 and m15.get('signal_quality'):
            m15_sq = m15['signal_quality']
            if res_side == 'buy':
                m15_sq['buy_level'] = min(10, m15_sq.get('buy_level', 0) * 1.20)
                m15_sq.setdefault('details', []).append('5+15买共振确认✓')
            elif res_side == 'sell':
                m15_sq['sell_level'] = min(10, m15_sq.get('sell_level', 0) * 1.20)
                m15_sq.setdefault('details', []).append('5+15卖共振确认✓')

    # 更新 level 和 label
    for period in PERIODS:
        p = period_results.get(period)
        if not p or not p.get('signal_quality'):
            continue
        sq = p['signal_quality']
        bl = sq.get('buy_level', 0)
        sl = sq.get('sell_level', 0)
        if direction in bullish_dirs:
            lv = bl
        elif direction in bearish_dirs:
            lv = sl
        else:
            lv = max(bl, sl) if bl > 1.0 or sl > 1.0 else 0
        if lv >= 4.0:
            sq['label'] = '最强出击信号'
        elif lv >= 3.0:
            sq['label'] = '加强闭环'
        elif lv >= 2.0:
            sq['label'] = '普通闭环'
        elif lv >= 1.0:
            sq['label'] = '弱信号'
        else:
            sq['label'] = '无出击信号'
        sq['level'] = lv

    # 取高者: ABCD级别 vs 主导量级 → 实际最低操作级别
    actual_min_idx = max(abcd_min_idx, dominant_idx)

    # 找出最佳操作级别（用压制调整后的 buy/sell level）
    best = None
    for i, period in enumerate(PERIODS):
        if i < actual_min_idx:
            continue
        p = period_results.get(period)
        if not p or not p.get('signal_quality'):
            continue
        sq = p['signal_quality']
        if best is None or sq['level'] > best['signal_quality']['level']:
            best = p

    # 量价阶段标注
    volume_info = analyze_volume_regime(code, daily_rows, period_results)

    # 结构分析：主导量级方向+次级别浪结构+回调深度
    wave_structure = judge_wave_structure(code, period_results, dominant_info,
                                           _cached_rows=cached_rows)

    # 指数级行情条件检测
    exp_readiness = detect_exponential_readiness(
        code, daily_rows, period_results, dominant_info,
        _cached_rows=cached_rows)

    # 缠论结构分析(阻支密度)
    rs_density = detect_rs_density(code, daily_rows)

    # 大盘系数权重
    market_coeff = get_market_coefficient()

    # 综合操作建议
    advice = _generate_advice(position, trend, best, period_results,
                               dominant_info, market_coeff, rhythm, resonance)

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
        'rhythm': rhythm,
        'resonance': resonance,
    }


def analyze_all():
    codes = get_all_codes()
    name_map = get_name_map()
    results = [analyze(code, name_map.get(code, code)) for code in codes]
    # 按分级排序: 可操作→共振偏强→强势观望→中性偏强→中性→关注→弱势→观望
    grade_order = {'observe_strong': 0, 'actionable': 1, 'resonant_strong': 2,
                   'neutral_strong': 3, 'neutral_bias': 4,
                   'neutral': 5, 'neutral_weak': 6, 'observe': 7,
                   'observe_weak': 8, 'avoid': 9}
    def sort_key(r):
        g = r.get('advice', {}).get('grade', 'neutral')
        rs = r.get('advice', {}).get('resonance_score', 0)
        return (grade_order.get(g, 99), -rs)
    results.sort(key=sort_key)
    return results

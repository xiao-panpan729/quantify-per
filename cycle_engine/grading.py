# -*- coding: utf-8 -*-
"""
cycle_engine 评分与建议 — 趋势信号评分 / 操作建议生成
"""
from .utils import PERIOD_LABELS

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



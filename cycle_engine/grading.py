# -*- coding: utf-8 -*-
"""
cycle_engine 评分与建议 — 趋势信号评分 / 操作建议生成
"""
from .utils import PERIOD_LABELS
from .constants import Direction, RhythmVerdict


# ============================================================
# 跨周期共振检测辅助函数
# ============================================================

def _check_golden(all_periods, period):
    """检查某周期是否有活跃金叉（金叉必须比死叉更新）"""
    p = all_periods.get(period)
    if not p or not p.get('signal_quality'):
        return False
    ecs = p['signal_quality'].get('ema_cross_status')
    if not ecs:
        return False
    if not (ecs.get('has_recent_golden', False) and ecs.get('last_golden_idx', -1) >= 0):
        return False
    # 金叉必须比死叉更新，否则死叉已覆盖
    last_dead = ecs.get('last_dead_idx', -1)
    if last_dead >= 0 and ecs['last_golden_idx'] < last_dead:
        return False
    return True


def _check_dead(all_periods, period):
    """检查某周期是否有活跃死叉（死叉必须比金叉更新）"""
    p = all_periods.get(period)
    if not p or not p.get('signal_quality'):
        return False
    ecs = p['signal_quality'].get('ema_cross_status')
    if not ecs:
        return False
    if not (ecs.get('has_recent_dead', False) and ecs.get('last_dead_idx', -1) >= 0):
        return False
    # 死叉必须比金叉更新，否则金叉已覆盖
    last_golden = ecs.get('last_golden_idx', -1)
    if last_golden >= 0 and ecs['last_dead_idx'] < last_golden:
        return False
    return True


def _golden_dead_ratio(all_periods):
    """计算30+60分钟金叉数 vs 死叉数的绝对值"""
    gc = 0
    dc = 0
    for p in ['min30', 'min60']:
        pp = all_periods.get(p)
        if not pp or not pp.get('signal_quality'):
            continue
        ecs = pp['signal_quality'].get('ema_cross_status')
        if not ecs:
            continue
        gc += ecs.get('golden_count', 0)
        dc += ecs.get('dead_count', 0)
    return gc, dc


def _last_cross_status(all_periods):
    """返回30+60分钟最近的金叉和死叉的idx"""
    last_g = -1
    last_d = -1
    for p in ['min30', 'min60']:
        pp = all_periods.get(p)
        if not pp or not pp.get('signal_quality'):
            continue
        ecs = pp['signal_quality'].get('ema_cross_status')
        if not ecs:
            continue
        if ecs.get('last_golden_idx', -1) > last_g:
            last_g = ecs['last_golden_idx']
        if ecs.get('last_dead_idx', -1) > last_d:
            last_d = ecs['last_dead_idx']
    return last_g, last_d


# ============================================================
# 信号摘要构建
# ============================================================

def _build_signal_summary(all_periods, trend):
    """
    从各周期信号质量数据中提取摘要信息。
    返回: (max_min_level, best_min_label, best_min_period, min_signal_details)
    """
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

    # 收集有意义的闭环信号 (level >= 2.0)
    sig_avail = {}
    for p in ['daily', 'min60', 'min30', 'min15', 'min5']:
        pp = all_periods.get(p)
        if pp and pp.get('signal_quality') and pp['signal_quality']['level'] >= 2.0:
            sig_avail[p] = pp['signal_quality']

    min_signal_details = []

    # 日线方向锚
    if 'daily' in sig_avail:
        d = sig_avail['daily']
        bl, sl = d.get('buy_level', 0), d.get('sell_level', 0)
        if bl >= 3.0 and sl >= 3.0:
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

    # 最佳分钟锚 (取第一个 level >= 2.0 的)
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

    # 5-15分钟共振覆盖
    if 'min15' in sig_avail and 'min5' in sig_avail:
        if sig_avail['min15']['level'] >= 3.5 and sig_avail['min5']['level'] >= 3.5:
            b15, s15 = sig_avail['min15'].get('buy_level', 0), sig_avail['min15'].get('sell_level', 0)
            b5, s5 = sig_avail['min5'].get('buy_level', 0), sig_avail['min5'].get('sell_level', 0)
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

    return max_min_level, best_min_label, best_min_period, min_signal_details


# ============================================================
# 跨周期共振检测
# ============================================================

def _detect_resonance(all_periods, direction, best):
    """
    检测多周期金叉/死叉共振。
    返回: (resonance_score, resonance_desc)
      resonance_score: 正=偏多共振, 负=偏空共振
    """
    resonance_score = 0
    resonance_desc = ''

    short_golden = _check_golden(all_periods, 'min5') or _check_golden(all_periods, 'min15')
    mid_golden = _check_golden(all_periods, 'min30') or _check_golden(all_periods, 'min60')
    daily_golden = _check_golden(all_periods, 'daily')

    bullish_directions = Direction.BULLISH_DIRS
    bearish_directions = Direction.BEARISH_DIRS

    # 方向匹配：上涨/偏多看金叉，下跌/偏空看死叉
    if direction in bullish_directions:
        if short_golden and mid_golden:
            resonance_score = 0.8
            resonance_desc = '多周期金叉共振'
        elif mid_golden:
            resonance_score = 0.3
            resonance_desc = '短线层金叉活跃'
    elif direction in bearish_directions:
        has_active = _check_dead(all_periods, 'min5') or _check_dead(all_periods, 'min15')
        has_mid_dead = _check_dead(all_periods, 'min30') or _check_dead(all_periods, 'min60')
        if has_active and has_mid_dead:
            resonance_score = 0.8
            resonance_desc = '多周期死叉共振'

    # 中性方向：看哪边共振更强
    has_bull_resonance = short_golden and mid_golden
    has_bear_resonance = (_check_dead(all_periods, 'min5') or _check_dead(all_periods, 'min15')) and \
                         (_check_dead(all_periods, 'min30') or _check_dead(all_periods, 'min60'))

    if direction == Direction.NEUTRAL:
        if has_bull_resonance and not has_bear_resonance:
            resonance_score = 0.7
            resonance_desc = '多周期金叉共振(中性背景)'
        elif has_bear_resonance and not has_bull_resonance:
            resonance_score = -0.5
            resonance_desc = '多周期死叉共振(警示)'
        elif has_bull_resonance and has_bear_resonance:
            last_g, last_d = _last_cross_status(all_periods)
            if last_g > last_d and last_g >= 0:
                resonance_score = 0.6
                resonance_desc = '最后活动为金叉'
            elif last_d >= last_g and last_d >= 0:
                resonance_score = -0.2
                resonance_desc = '最后活动为死叉(偏空)'
            else:
                resonance_score = 0.2
                resonance_desc = '金叉死叉均活跃'

    # 第二级共振: 最佳周期是 min30/min60 时检查日线
    if best and best.get('period') in ('min30', 'min60'):
        if direction in bullish_directions and daily_golden:
            if resonance_score < 0.5:
                resonance_score = max(resonance_score, 0.3)
            resonance_desc += ('; ' if resonance_desc else '') + '日线金叉共振'
        elif direction in bearish_directions and _check_dead(all_periods, 'daily'):
            if resonance_score < 0.5:
                resonance_score = max(resonance_score, 0.3)
            resonance_desc += ('; ' if resonance_desc else '') + '日线死叉共振'
        elif direction == 'neutral':
            if daily_golden and not _check_dead(all_periods, 'daily'):
                if resonance_score < 0.5:
                    resonance_score = max(resonance_score, 0.3)
                resonance_desc += ('; ' if resonance_desc else '') + '日线金叉活跃'
            elif _check_dead(all_periods, 'daily') and not daily_golden:
                if resonance_score > -0.5:
                    resonance_score = min(resonance_score, -0.2)
                resonance_desc += ('; ' if resonance_desc else '') + '日线死叉活跃'

    return resonance_score, resonance_desc


# ============================================================
# 趋势信号分级
# ============================================================

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


def _grade_trend_signal(position, trend, best, all_periods, dominant_info=None,
                           market_coeff=None, rhythm=None, resonance=None):
    """
    ABCD 分级 — 基于节奏完整性的对称递进阶梯

    上涨阶梯: A+ → A → A- → B → C → D+ → D → D-
    下跌阶梯: D- → D → D+ → C → B → A- → A → A+

    升降依据 (两个固定级别):
      30分钟 = 战术节奏 | 日线 = 战略节奏
      rhythm_verdict: intact → tactical_broken → strategic_broken → fully_broken
      res_confirmed: 5+15共振确认
      30+60%死叉/金叉能否形成共振
    """
    direction = trend['direction']
    zone = position['zone']
    risk = position['risk_level']

    # rhythm / resonance
    rhythm_verdict = (rhythm or {}).get('verdict', RhythmVerdict.INTACT)
    res_confirmed = (resonance or {}).get('resonance_confirmed', False)
    res_side = (resonance or {}).get('side', 'neutral')
    tactical = (rhythm or {}).get('tactical', {})
    strategic = (rhythm or {}).get('strategic', {})
    t_cross = tactical.get('cross_status', {})
    s_cross = strategic.get('cross_status', {})

    # 30+60是否同向(买/卖)共振: 两者都金叉或都死叉
    has_30_60_golden = (_check_golden(all_periods, 'min30') and _check_golden(all_periods, 'min60'))
    has_30_60_dead = (_check_dead(all_periods, 'min30') and _check_dead(all_periods, 'min60'))
    has_30_60_buy_res = has_30_60_golden and res_confirmed and res_side in ('buy',)
    has_30_60_sell_res = has_30_60_dead and res_confirmed and res_side in ('sell',)

    # 日线交叉状态
    daily_golden = _check_golden(all_periods, 'daily')
    daily_dead = _check_dead(all_periods, 'daily')

    # 信号摘要
    max_min_level, best_min_label, best_min_period, min_signal_details = \
        _build_signal_summary(all_periods, trend)
    resonance_score, resonance_desc = _detect_resonance(all_periods, direction, best)

    best_level = 0
    if best and best.get('signal_quality'):
        best_level = best['signal_quality']['level']
    mc = (market_coeff or {}).get('coefficient', 1.0) if isinstance(market_coeff, dict) else 1.0
    adj_level = round(best_level * mc, 1)

    bullish_dirs = Direction.BULLISH_DIRS
    bearish_dirs = Direction.BEARISH_DIRS
    is_bullish = direction in bullish_dirs
    is_bearish = direction in bearish_dirs
    dc_wait = dominant_info['dominant_label'] if (dominant_info and dominant_info.get('dominant_label')) else '分钟'

    # ══════════════════════════════════════════════════
    # 上涨阶梯: A+ → A → A- → B → C → D+ → D → D-
    # ══════════════════════════════════════════════════
    if is_bullish:

        # ── 上涨+高位critical → 用节奏完整性判断能否继续操作 ──
        if zone == 'high' and risk == 'critical':
            if has_30_60_buy_res:
                return _grade_output('actionable', 'A+', '顺势做多',
                    '高位+节奏完整+30-60-5-15全共振买，趋势强势加速',
                    min_signal_details, '注意极端位置，移动止盈', resonance_score)
            if rhythm_verdict == RhythmVerdict.INTACT and res_confirmed and res_side in ('buy',):
                return _grade_output('actionable', 'A假', '顺势做多',
                    '高位+节奏完整+5-15买共振确认，可谨慎做多',
                    min_signal_details, '位置风险：严格止损，不加仓追高', resonance_score)
            if rhythm_verdict == RhythmVerdict.INTACT:
                return _grade_output('observe_strong', 'B', '持有/减仓',
                    '多头趋势+高位+节奏完整，等回调加仓',
                    min_signal_details, f'回调EXPMA12后{dc_wait}找★买')
            if rhythm_verdict == RhythmVerdict.TACTICAL_BROKEN:
                return _grade_output('observe_strong', 'B', '持有/减仓',
                    '战术节奏破坏，减仓观望等修复',
                    min_signal_details, f'等30分钟节奏修复后{dc_wait}找★买')
            if rhythm_verdict == RhythmVerdict.STRATEGIC_BROKEN:
                return _grade_output('observe', 'C', '减仓',
                    '战略节奏破坏，日线趋势可能反转',
                    min_signal_details, '等日线金叉确认后再看')
            # fully_broken
            if has_30_60_sell_res:
                return _grade_output('avoid', 'D-', '清仓/回避',
                    '上涨节奏全破+30-60-5-15全共振卖，趋势可能反转',
                    min_signal_details, '等完整底部结构出现', resonance_score)
            return _grade_output('avoid', 'D', '回避',
                '上涨节奏全破，等重新筑底',
                min_signal_details, '等30分钟金叉+日线金叉出现')

        # ── 上涨+非极端位置 → 正常递进 ──
        if has_30_60_buy_res:
            return _grade_output('actionable', 'A+', '顺势做多',
                '节奏完整+30-60-5-15全共振买，趋势强劲',
                min_signal_details, '', resonance_score)
        if rhythm_verdict == 'intact' and res_confirmed and res_side in ('buy',):
            return _grade_output('actionable', 'A', '顺势做多',
                '节奏完整+5-15买共振确认，可继续做多',
                min_signal_details, '', resonance_score)
        if rhythm_verdict == RhythmVerdict.INTACT and adj_level >= 3.0:
            return _grade_output('actionable', 'A-', '顺势做多',
                '节奏完整+分钟信号强，趋势健康',
                min_signal_details, '', resonance_score)
        if rhythm_verdict == RhythmVerdict.INTACT:
            return _grade_output('observe_strong', 'B', '持有',
                '节奏完整但信号一般，持有观察',
                min_signal_details, '等分钟信号加强')
        if rhythm_verdict == RhythmVerdict.TACTICAL_BROKEN:
            return _grade_output('observe_strong', 'B', '持有/减仓',
                '战术节奏破坏，暂时观望等修复',
                min_signal_details, f'等30分钟节奏修复后{dc_wait}找★买')
        if rhythm_verdict == RhythmVerdict.STRATEGIC_BROKEN:
            return _grade_output('observe', 'C', '减仓',
                '战略节奏破坏，日线趋势可能变',
                min_signal_details, '等日线恢复确认')
        # fully_broken
        if has_30_60_sell_res:
            return _grade_output('avoid', 'D-', '清仓/回避',
                '节奏全破+30-60-5-15全共振卖，趋势反转确认',
                min_signal_details, '等日线金叉+★买+金叉闭环', resonance_score)
        return _grade_output('avoid', 'D', '回避',
            '节奏全破，等重新筑底',
            min_signal_details, '等30分钟+日线金叉出现')

    # ══════════════════════════════════════════════════
    # 下跌阶梯: D- → D真 → D假 → D+ → C → B → A- → A → A+
    # ══════════════════════════════════════════════════
    elif is_bearish:

        # ── 下跌+低位critical → 用节奏完整性判断是否可抄底 ──
        if zone == 'low' and risk == 'critical':
            if has_30_60_sell_res:
                return _grade_output('avoid', 'D-', '回避/减仓',
                    '超跌+节奏完整+30-60-5-15全共振卖，趋势加速下行',
                    min_signal_details, '不要接飞刀，等完整底部结构', resonance_score)
            # 低位+卖共振但非全共振 → D假（对称于A假：低位假跌破/诱空洗盘）
            if rhythm_verdict == 'intact' and res_confirmed and res_side in ('sell',):
                return _grade_output('avoid', 'D假', '回避/关注反转',
                    '超跌+卖共振确认，但低位极端可能是诱空洗盘(假跌破)',
                    min_signal_details, '等30分钟金叉+★买确认，警惕假跌破陷阱', resonance_score)
            # 下跌节奏破坏 = 可能反转
            if (rhythm_verdict in RhythmVerdict.STRATEGIC_OR_FULLY
                    and res_confirmed
                    and res_side in ('buy_reversal', 'buy')):
                if has_30_60_golden:
                    return _grade_output('observe', 'B', '强关注反转',
                        '超跌+节奏全面翻转+30-60金叉共振+5-15买确认，重要转折',
                        min_signal_details, '等二次回踩确认后可试多', resonance_score)
                return _grade_output('observe', 'C', '关注反转',
                    '超跌+节奏破环+5-15买共振=潜在反转',
                    min_signal_details, '等30分钟金叉确认后再行动', resonance_score)
            if rhythm_verdict in RhythmVerdict.TACTICAL_OR_STRATEGIC:
                return _grade_output('observe_weak', 'D+', '等待转折',
                    '节奏开始松动但不充分，等确认',
                    min_signal_details, '等30+60分钟买闭环共振出现')
            return _grade_output('avoid', 'D', '回避',
                '超跌+节奏完整下跌，不具备反转条件',
                min_signal_details, '等60分钟/日线出现★买+金叉闭环')

        # ── 下跌+非极端位置 → 正常递进 ──
        if has_30_60_sell_res:
            return _grade_output('avoid', 'D-', '回避',
                '节奏完整+30-60-5-15全共振卖，下跌趋势强劲',
                min_signal_details, '等日线金叉+★买+金叉闭环', resonance_score)
        if rhythm_verdict == 'intact' and res_confirmed and res_side in ('sell',):
            return _grade_output('avoid', 'D真', '回避',
                '节奏完整+5-15卖共振确认，真下跌趋势延续',
                min_signal_details, '等60分钟/日线出现★买+金叉闭环')
        if rhythm_verdict == RhythmVerdict.INTACT:
            return _grade_output('avoid', 'D', '回避',
                '节奏完整下跌，不参与',
                min_signal_details, '等节奏破坏信号出现')
        if rhythm_verdict == RhythmVerdict.TACTICAL_BROKEN:
            return _grade_output('observe_weak', 'D+', '等待',
                '战术节奏松动，关注但不动手',
                min_signal_details, '等30+60分钟买闭环共振')
        if rhythm_verdict == RhythmVerdict.STRATEGIC_BROKEN:
            return _grade_output('observe', 'C', '观望',
                '战略节奏破坏，可能接近反转',
                min_signal_details, '等日线金叉确认', resonance_score)
        # fully_broken
        if has_30_60_golden and res_confirmed and res_side in ('buy',):
            return _grade_output('observe', 'B', '强关注',
                '节奏翻转+30-60金叉共振+5-15买确认，重要转折',
                min_signal_details, '等二次探底确认后可入场', resonance_score)
        return _grade_output('observe', 'C', '观望',
            '节奏翻转中，等确认',
            min_signal_details, '等30分钟金叉+日线金叉')

    # ══════════════════════════════════════════════════
    # 中性/震荡 → 原逻辑保留，加 rhythm 判断
    # ══════════════════════════════════════════════════
    else:
        if adj_level >= 4.0 and resonance_score >= 0.7:
            return _grade_output('actionable', 'A-', '顺势做多',
                '中性但信号极强+共振确认，可试多',
                min_signal_details, '', resonance_score)
        if adj_level >= 3.0 and resonance_score >= 0.7:
            return _grade_output('neutral_strong', 'B', '高抛低吸',
                '中性+信号强+共振，偏多操作',
                min_signal_details, '等日线明确方向', resonance_score)
        if adj_level >= 3.0:
            return _grade_output('neutral_bias', 'C', '高抛低吸',
                '中性+信号强，日线横盘中可做T',
                min_signal_details, '等日线MACD金叉确认')
        return _grade_output('neutral_weak', 'C', '观望',
            '震荡但信号不足，等待',
            min_signal_details, '等分钟级出现★买+金叉闭环')


# ============================================================
# 操作建议生成（对外接口）
# ============================================================

def _generate_advice(position, trend, best, all_periods, dominant_info=None, market_coeff=None,
                     rhythm=None, resonance=None):
    """旧版兼容，_grade_trend_signal 的薄封装，透传所有字段"""
    g = _grade_trend_signal(position, trend, best, all_periods, dominant_info,
                            market_coeff, rhythm, resonance)
    wait_part = f" 提示: {g['wait_condition']}" if g['wait_condition'] else ''

    dominant_note = ''
    if dominant_info and dominant_info.get('dominant_cycle'):
        dc = dominant_info['dominant_label']
        db = dominant_info.get('dominant_buy')
        ds = dominant_info.get('dominant_sell')
        br = dominant_info.get('buy_rate', 0)
        sr = dominant_info.get('sell_rate', 0)
        if db and ds and db == ds:
            dominant_note = f' | 主导周期{dc}'
        elif db and ds and db != ds:
            dominant_note = f' | 买看{PERIOD_LABELS.get(db, db)}卖看{PERIOD_LABELS.get(ds, ds)}'
        elif db and br >= 0.5:
            dominant_note = f' | 做多{PERIOD_LABELS.get(db, db)}主导'
        elif ds and sr >= 0.5:
            dominant_note = f' | 做空{PERIOD_LABELS.get(ds, ds)}主导'
        else:
            dominant_note = f' | 主导周期{dc}'

    return {
        'grade': g['grade'],
        'grade_label': g['grade_label'],
        'action': g['action'],
        'reason': g['reason'] + wait_part + dominant_note,
        'min_signal_summary': g['min_signal_summary'],
        'wait_condition': g['wait_condition'],
        'resonance_score': g.get('resonance_score', 0),
        'dominant_cycle': dominant_info,
        'confidence': '高' if g['grade'] in ('actionable', 'resonant_strong', 'observe_strong', 'neutral_strong') else '中',
    }

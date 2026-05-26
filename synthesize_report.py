# -*- coding: utf-8 -*-
"""
synthesize_report.py — 三层聚合引擎
读 cycle_report.json + hht_report.json → 交叉分析 → 输出每标的操作动作
输出: signals/tracking/synthesized_report.json
"""

import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

# ─── 常量 ───

ENTROPY_RISE_PHASES = {'趋势松动', '趋势衰减', '无序放大', '震荡放大', '逆向崩退'}

PERIODS = ['min5', 'min15', 'min30', 'daily']

PERIOD_CN = {'min1': '1分', 'min5': '5分', 'min15': '15分', 'min30': '30分', 'min60': '60分', 'daily': '日线'}
def period_cn(pk):
    return PERIOD_CN.get(pk, pk)

HHT_STABLE = 1.5

# advice.grade → ABCD 映射
GRADE_TO_ABCD = {
    'actionable':       'A',
    'resonant_strong':  'A',
    'observe_strong':   'B',
    'neutral_strong':   'B',
    'neutral_bias':     'B',
    'neutral':          'B',
    'neutral_weak':     'C',
    'observe':          'C',
    'observe_weak':     'C',
    'avoid':            'D',
}


# ════════════════════════════════════════════
# 数据加载
# ════════════════════════════════════════════

def load_cycle_report():
    path = os.path.join(BASE, 'signals', 'tracking', 'cycle_report.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {item['code']: item for item in data} if isinstance(data, list) else {}
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f'[WARN] 无法加载 cycle_report.json: {e}')
        return {}


def load_hht_report():
    path = os.path.join(BASE, 'signals', 'tracking', 'hht_report.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {item['code']: item for item in data} if isinstance(data, list) else {}
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f'[WARN] 无法加载 hht_report.json: {e}')
        return {}


def load_name_map():
    try:
        sys.path.insert(0, BASE)
        from config import NAME_MAP
        return NAME_MAP
    except ImportError:
        return {}


# ════════════════════════════════════════════
# 核心判断函数
# ════════════════════════════════════════════

def derive_abcd(advice_grade):
    """用系统已有的综合标签映射 ABCD"""
    return GRADE_TO_ABCD.get(advice_grade, 'D')


def classify_pe(pe_data):
    """PE数据 → 降熵 / 升熵 / 方向形成中 / 无PE数据"""
    if not pe_data:
        return '无PE数据'

    pe_phase = pe_data.get('pe_phase', '')
    pe_ratio = pe_data.get('pe_ratio', 1.0)
    trending = pe_data.get('trending', False)

    if pe_phase in {'逆向崩退'}:
        return '升熵'
    if trending or pe_ratio < 0.95:
        return '降熵'
    if pe_ratio > 1.05 or pe_phase in ENTROPY_RISE_PHASES:
        return '升熵'
    return '方向形成中'


def compute_hht_stability(hht_period):
    """HHT稳定性：直接用summary里的freq_stability（HHT自己选的最核心那个IMF）"""
    if not hht_period:
        return 999.0
    return hht_period.get('summary', {}).get('freq_stability', 999.0)


def get_daily_pe(cycle_item):
    daily = cycle_item.get('periods', {}).get('daily', {})
    sq = daily.get('signal_quality') or {}
    return sq.get('trend_pe')


def get_daily_hht(hht_item):
    if not hht_item:
        return None
    return hht_item.get('periods', {}).get('daily')


def compute_grade(abcd, advice_grade, cycle_item, hht_item):
    daily_pe = get_daily_pe(cycle_item)
    daily_hht = get_daily_hht(hht_item)
    pe_state = classify_pe(daily_pe)
    stab = compute_hht_stability(daily_hht)

    base = {
        'abcd': abcd,
        'advice_grade': advice_grade,
        'pe_state': pe_state,
        'hht_stability': round(stab, 2) if stab < 999 else None,
    }

    # B/C/D 不细分
    if abcd != 'A':
        base['sub_grade'] = ''
        base['reason'] = f'{abcd}级({advice_grade})'
        return base

    # A 类细分
    if pe_state == '升熵' or pe_state == '无PE数据':
        sub = '假'
        reason = f'A假(PE{pe_state})'
    elif pe_state == '方向形成中':
        sub = '-'
        reason = 'A-(PE方向形成中)'
    elif stab < HHT_STABLE:
        sub = '+'
        reason = f'A+(HHT稳fs={stab:.2f})'
    else:
        sub = '-'
        reason = f'A-(HHT散fs={stab:.2f})'

    base['sub_grade'] = sub
    base['reason'] = reason
    return base


def determine_signal_direction(cycle_item):
    """每周期信号方向 + 整体方向"""
    per_period = {}
    for pk in ['min5', 'min15', 'min30']:
        pp = cycle_item.get('periods', {}).get(pk)
        if not pp:
            per_period[pk] = 'no_data'
            continue
        sq = pp.get('signal_quality') or {}
        bl = sq.get('buy_level', 0)
        sl = sq.get('sell_level', 0)
        diff = bl - sl
        if diff >= 1.0:
            per_period[pk] = 'buy_bias'
        elif diff <= -1.0:
            per_period[pk] = 'sell_bias'
        else:
            per_period[pk] = 'balanced'

    # 整体方向：多数票
    votes = [v for v in per_period.values() if v != 'no_data']
    buy_n = votes.count('buy_bias')
    sell_n = votes.count('sell_bias')
    if buy_n > sell_n:
        overall = 'buy'
    elif sell_n > buy_n:
        overall = 'sell'
    else:
        overall = 'balanced'

    # min5+min15
    m5m15 = [v for v in [per_period.get('min5'), per_period.get('min15')] if v and v != 'no_data']
    b2 = m5m15.count('buy_bias')
    s2 = m5m15.count('sell_bias')
    min5_min15 = 'buy' if b2 > s2 else ('sell' if s2 > b2 else 'balanced')

    return {
        'per_period': per_period,
        'overall': overall,
        'min5_min15': min5_min15,
        'buy_dominant': buy_n > 0 and buy_n >= sell_n,
        'sell_dominant': sell_n > 0 and sell_n >= buy_n,
    }


def determine_action(grade_label, signal_info, pe_state, trend_direction):
    """决策树 → 买/卖/持有/减仓/加仓/观望/回避"""
    overall = signal_info['overall']
    m5m15 = signal_info['min5_min15']
    per = signal_info['per_period']

    if grade_label == 'A+':
        if m5m15 == 'buy' and per.get('min5') == 'buy_bias' and per.get('min15') == 'buy_bias':
            return '加仓'
        if overall == 'buy':
            return '买'
        return '持有'

    if grade_label in ('A-',):
        if overall == 'sell':
            return '减仓'
        return '持有'

    if grade_label in ('A假',):
        return '观望'

    if grade_label == 'B':
        if pe_state == '降熵' and overall == 'buy':
            return '持有'
        if pe_state == '升熵' and overall == 'sell':
            return '减仓'
        return '持有'

    if grade_label == 'C':
        return '观望'

    if grade_label == 'D':
        return '回避'

    return '观望'


def build_period_detail(pk, cycle_item, hht_item, trend_direction):
    """构造单周期详情"""
    pp = cycle_item.get('periods', {}).get(pk)
    sq = (pp or {}).get('signal_quality') or {}
    tp = sq.get('trend_pe') or {}

    hp = None
    if hht_item:
        hp = hht_item.get('periods', {}).get(pk)

    stab = compute_hht_stability(hp)
    stab_label = ''
    if hp:
        stab_label = hp.get('summary', {}).get('stability_label', '')

    bl = sq.get('buy_level', 0)
    sl = sq.get('sell_level', 0)
    diff = bl - sl
    if diff >= 1.0:
        bias = 'buy_bias'
    elif diff <= -1.0:
        bias = 'sell_bias'
    else:
        bias = 'balanced'

    # 信号建议
    is_up = trend_direction in ('bullish', 'bullish_bias')
    is_down = trend_direction in ('bearish', 'bearish_bias')
    if bias == 'buy_bias' and is_up:
        rec = '顺向买'
    elif bias == 'buy_bias' and (is_down or not is_up):
        rec = '逆势买'
    elif bias == 'sell_bias' and is_down:
        rec = '顺向卖'
    elif bias == 'sell_bias' and (is_up or not is_down):
        rec = '逆势卖'
    else:
        rec = '持有'

    return {
        'buy_level': bl,
        'sell_level': sl,
        'pe_phase': tp.get('pe_phase', ''),
        'pe_ratio': tp.get('pe_ratio', 1.0),
        'hht_stability': round(stab, 2) if stab < 999 else None,
        'hht_stability_label': stab_label,
        'signal_bias': bias,
        'recommendation': rec,
    }


def build_structure_status(daily_pe):
    s = classify_pe(daily_pe)
    if s == '降熵':
        phase = daily_pe.get('pe_phase', '') if daily_pe else ''
        ratio = daily_pe.get('pe_ratio', 0) if daily_pe else 0
        return f'降熵({phase},r={ratio:.2f})'
    if s == '升熵':
        phase = daily_pe.get('pe_phase', '') if daily_pe else ''
        ratio = daily_pe.get('pe_ratio', 0) if daily_pe else 0
        return f'升熵({phase},r={ratio:.2f})'
    if s == '方向形成中':
        return '方向形成中'
    return '无PE数据'


def build_momentum_status(daily_hht):
    if not daily_hht:
        return '无HHT数据'
    stab = compute_hht_stability(daily_hht)
    if stab > HHT_STABLE:
        return f'HHT散乱(fs={stab:.2f})'
    if stab < 0.7:
        return f'HHT锁紧(fs={stab:.2f})'
    return f'HHT正常(fs={stab:.2f})'


# ════════════════════════════════════════════
# 聚合
# ════════════════════════════════════════════

def synthesize_one(code, cycle_item, hht_item):
    trend = cycle_item.get('trend', {})
    advice = cycle_item.get('advice', {})
    advice_grade = advice.get('grade', 'avoid')
    abcd = derive_abcd(advice_grade)

    daily_pe = get_daily_pe(cycle_item)
    daily_hht = get_daily_hht(hht_item)
    pe_state = classify_pe(daily_pe)

    grade_info = compute_grade(abcd, advice_grade, cycle_item, hht_item)
    # grade_label: A+/A-/A假/B/C/D
    grade_label = abcd
    sub = grade_info.get('sub_grade', '')
    if sub:
        grade_label = abcd + sub if sub != '假' else 'A假'

    signal_info = determine_signal_direction(cycle_item)
    trend_direction = trend.get('direction', 'neutral')
    action = determine_action(grade_label, signal_info, pe_state, trend_direction)

    # 主导周期信号摘要
    best_period = cycle_item.get('best_period', '')
    adv = cycle_item.get('advice', {})
    dc = adv.get('dominant_cycle', {})
    dominant = dc.get('dominant_cycle', best_period)
    min_summary = adv.get('min_signal_summary', '')

    # 取主导周期的信号质量
    dom_period = cycle_item.get('periods', {}).get(dominant, {})
    dom_sq = (dom_period or {}).get('signal_quality') or {}
    dom_bl = dom_sq.get('buy_level', 0)
    dom_sl = dom_sq.get('sell_level', 0)
    dom_label = dom_sq.get('label', '')

    # 提炼具体信号描述
    signal_desc = ''
    if dom_bl >= dom_sl + 1.0:
        signal_desc = '买信号(level=%.1f)' % dom_bl
    elif dom_sl >= dom_bl + 1.0:
        signal_desc = '卖信号(level=%.1f)' % dom_sl
    else:
        signal_desc = '买卖均衡(买%.1f/卖%.1f)' % (dom_bl, dom_sl)

    # 构造输出
    period_signals = {}
    for pk in PERIODS:
        period_signals[pk] = build_period_detail(pk, cycle_item, hht_item, trend_direction)

    return {
        'code': code,
        'name': cycle_item.get('name', ''),
        'action': action,
        'grade': grade_label,
        'grade_detail': grade_info,
        'structure_status': build_structure_status(daily_pe),
        'momentum_status': build_momentum_status(daily_hht),
        'signal_direction': signal_info['overall'],
        'signal_detail': {
            'min5': signal_info['per_period'].get('min5', 'no_data'),
            'min15': signal_info['per_period'].get('min15', 'no_data'),
            'min30': signal_info['per_period'].get('min30', 'no_data'),
            'overall': signal_info['overall'],
            'min5_min15': signal_info['min5_min15'],
        },
        'signal_summary': '%s %s [%s]' % (period_cn(dominant), signal_desc, dom_label),
        'dominant_period': dominant,
        'best_period': best_period,
        'trend': {
            'direction': trend_direction,
            'score': trend.get('score', 0),
            'zone_advice': trend.get('zone_advice', ''),
            'zone_label': trend.get('zone_label', ''),
        },
        'period_signals': period_signals,
    }


def synthesize_all():
    cycle_data = load_cycle_report()
    hht_data = load_hht_report()

    if not cycle_data:
        print('[ERROR] cycle_report.json 无数据，退出')
        return {}

    name_map = load_name_map()
    output = {}

    for code, item in cycle_data.items():
        hht_item = hht_data.get(code)
        if not hht_item:
            print(f'[WARN] {code} 无HHT数据，降级处理')

        result = synthesize_one(code, item, hht_item)
        if result:
            # 用 config NAME_MAP 校正名称
            if code in name_map:
                result['name'] = name_map[code]
            output[code] = result

    return output


def save_synthesized(data):
    path = os.path.join(BASE, 'signals', 'tracking', 'synthesized_report.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'[OK] synthesized_report.json 已保存 ({len(data)} 标的)')


# ════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════

def main():
    data = synthesize_all()
    if data:
        save_synthesized(data)
        print()
        for code, item in data.items():
            name = item['name']
            grade = item['grade']
            action = item['action']
            struct = item['structure_status']
            momentum = item['momentum_status']
            sig = item['signal_direction']
            print(f'{code} {name:<10} | {grade:<4} | {action:<4} | {struct:<28} | {momentum:<20} | 信号:{sig}')


if __name__ == '__main__':
    main()

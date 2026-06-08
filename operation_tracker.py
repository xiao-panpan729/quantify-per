# -*- coding: utf-8 -*-
"""
战役级操作追踪系统 — 半自动模式 v1.0

用法:
    python operation_tracker.py --scan          # 扫描新战役建议（潜在开仓机会）
    python operation_tracker.py --status        # 显示所有活跃战役
    python operation_tracker.py --suggest       # 对活跃战役检测事件/平仓信号
    python operation_tracker.py --list          # 列出全部战役

数据: signals/tracking/operation_records.json
依赖: cycle_report.json (由 run_cycle.py 生成)
"""

import json
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CYCLE_REPORT = os.path.join(BASE, 'signals/tracking/_signals/cycle_report.json')
ANALYSIS_HISTORY = os.path.join(BASE, 'signals/tracking/_signals/analysis_history.json')
OP_RECORDS = os.path.join(BASE, 'signals/tracking/_funds/operation_records.json')

# ─── 辅助 ───

def period_cn(p):
    M = {'daily': '日线', 'min60': '60分钟', 'min30': '30分钟',
         'min15': '15分钟', 'min5': '5分钟', 'min1': '1分钟'}
    return M.get(p, p)

def trend_cn(t):
    M = {'bullish': '上涨', 'bullish_bias': '偏多', 'bearish': '下跌',
         'bearish_bias': '偏空', 'oscillating': '震荡', 'neutral': '中性'}
    return M.get(t, t)

def advice_cn(a):
    if not a: return '-'
    M = {'加仓追击': '加仓', '顺势做多': '做多', '持有(可轻仓跟)': '持有',
         '持有/减仓': '减仓', '高抛低吸': '高抛', '小仓做T': '做T',
         '观望': '观望', '等待': '等待', '不参与': '不参与', '关注抄底': '抄底'}
    return M.get(a, a)

def pct_color(pct):
    if pct is None: return '-'
    s = '%+.1f%%' % pct
    return s

# ─── 数据加载 ───

def load_cycle_report():
    if not os.path.exists(CYCLE_REPORT):
        print('[战役] ❌ cycle_report.json 未找到，请先运行 run_cycle.py')
        return []
    with open(CYCLE_REPORT, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_analysis_history():
    if not os.path.exists(ANALYSIS_HISTORY):
        return {'records': []}
    with open(ANALYSIS_HISTORY, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_op_records():
    if not os.path.exists(OP_RECORDS):
        return {'campaigns': []}
    with open(OP_RECORDS, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_op_records(records):
    os.makedirs(os.path.dirname(OP_RECORDS), exist_ok=True)
    with open(OP_RECORDS, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

# ─── 信号检测 ───

def get_signal_strength(item, direction='buy'):
    """
    从 cycle_report 数据提取信号强度摘要。
    返回 {period: {level, label, ...}}
    """
    periods = item.get('periods', {})
    if isinstance(periods, list):
        periods = {}
    result = {}
    for pk in ['min5', 'min15', 'min30', 'min60', 'daily']:
        pp = periods.get(pk, {})
        sq = pp.get('signal_quality', {}) or {}
        if not sq:
            continue
        level = sq.get('level', 0)
        label = sq.get('label', '')
        details = sq.get('details', [])
        buy_level = sq.get('buy_level', 0)
        sell_level = sq.get('sell_level', 0)
        pe = sq.get('trend_pe', {}) or {}
        pe_phase = pe.get('pe_phase', '') if isinstance(pe, dict) else ''
        result[pk] = {
            'level': level,
            'label': label,
            'buy_level': buy_level,
            'sell_level': sell_level,
            'pe_phase': pe_phase,
            'details': details,
        }
    return result

def get_hht_state(item, hht_lookup):
    """从 hht_data 提取各周期 HHT 状态"""
    code = item['code']
    h = hht_lookup.get(code, {})
    result = {}
    for pk in ['min5', 'min15', 'min30', 'min60', 'daily']:
        hpd = h.get('periods', {}).get(pk, {})
        hs = hpd.get('summary', {})
        if hs:
            result[pk] = {
                'fs': hs.get('freq_stability', 1.0),
                'er': hs.get('energy_ratio', 1.0),
                'label': hs.get('stability_label', ''),
                'dir': hs.get('trend_dir', ''),
            }
    return result

def detect_entry_signals(item):
    """
    检测某标的的开仓信号强度。
    返回 (score, reasons) — score 0~10, reasons 是理由列表
    """
    reasons = []
    score = 0

    t = item.get('trend', {})
    trend_score = t.get('score', 0)
    trend_dir = t.get('direction', '')
    adv = item.get('advice', {})
    dc = adv.get('dominant_cycle', {})
    dominant = dc.get('dominant_cycle', 'min5')
    action = adv.get('action', '')
    signal_strength = get_signal_strength(item)

    # 1. 趋势评分（最高3分）— 基于 zone_advice 验证结论
    zone_adv = t.get('zone_advice', '')
    if zone_adv == 'sweet_spot':
        score += 3
        reasons.append(f'趋势强(评分{trend_score}/14 sweet_spot)')
    elif zone_adv == 'fragile_high_uptrend':
        score += 2
        reasons.append(f'趋势虚高续涨(评分{trend_score}/14 高位续涨)')
    elif zone_adv == 'fragile_high_trap':
        score += 0
        reasons.append(f'趋势虚高陷阱(评分{trend_score}/14 高位陷阱)')
    elif zone_adv == 'fragile_high':
        score += 1
        reasons.append(f'趋势虚高(评分{trend_score}/14 虚高警示)')
    elif trend_score >= 7:
        score += 1
        reasons.append(f'趋势中性(评分{trend_score}/14)')

    # 2. 主导量级（最高2分）— min30+ 才是有效大级别开仓
    dom_order = {'min5': 0, 'min15': 1, 'min30': 2, 'min60': 3, 'daily': 4}
    dom_score = dom_order.get(dominant, 0)
    if dom_score >= 3:
        score += 2
        reasons.append('%s主导(大级别)' % period_cn(dominant))
    elif dom_score == 2:
        score += 1
        reasons.append('%s主导(中级别)' % period_cn(dominant))

    # 3. 多头方向（最高2分）
    if trend_dir in ('bullish',):
        score += 2
        reasons.append('明确上涨')
    elif trend_dir in ('bullish_bias',):
        score += 1
        reasons.append('偏多')

    # 4. 日线/60分 buy_level（最高3分）
    for pk in ['daily', 'min60', 'min30']:
        ps = signal_strength.get(pk, {})
        bl = ps.get('buy_level', 0)
        if bl >= 4.0:
            score += 3
            reasons.append('%s买信号强(level=%.1f)' % (period_cn(pk), bl))
            break
        elif bl >= 3.0:
            score += 2
            reasons.append('%s买信号(level=%.1f)' % (period_cn(pk), bl))
            break

    # 5. 操作建议加分（最高1分）
    if action in ('顺势做多', '加仓追击'):
        if score < 1:
            reasons.append('建议做多')
        score = max(score, 1)  # 保底1分

    # 6. 降级：等待/回避
    if action in ('等待', '观望', '不参与', '回避', '关注抄底'):
        score = max(0, score - 2)
        if action in ('回避', '不参与'):
            score = 0
        reasons.append('建议%s' % advice_cn(action))

    return min(score, 10), reasons


def detect_reduce_signal(item, hht_lookup):
    """
    检测减仓信号。
    返回 (has_signal, reasons)
    """
    reasons = []
    signal_strength = get_signal_strength(item)
    hht_state = get_hht_state(item, hht_lookup)

    # 1. 卖侧信号（sell_level >= 3.0 且有意义的周期）
    for pk in ['daily', 'min60', 'min30']:
        ps = signal_strength.get(pk, {})
        sl = ps.get('sell_level', 0)
        if sl >= 3.5:
            reasons.append('%s卖信号强(level=%.1f)' % (period_cn(pk), sl))
            break
        elif sl >= 3.0 and ps.get('level', 0) >= 3.0:
            reasons.append('%s卖信号(level=%.1f)' % (period_cn(pk), sl))
            break

    # 2. HHT 循环破位（频率散乱或破位）
    for pk in ['daily', 'min60', 'min30']:
        h = hht_state.get(pk, {})
        label = h.get('label', '')
        if '循环' in label and '破' in label:
            reasons.append('%s%s' % (period_cn(pk), label))
            break

    # 3. 排列熵结构溃散
    for pk in ['daily', 'min60', 'min30']:
        ps = signal_strength.get(pk, {})
        phase = ps.get('pe_phase', '')
        if '下破' in phase:
            reasons.append('%s结构下破' % period_cn(pk))
            break

    return len(reasons) > 0, reasons


def detect_add_signal(item, hht_lookup):
    """
    检测加仓信号（减仓后的回补）。
    返回 (has_signal, reasons)
    """
    reasons = []
    signal_strength = get_signal_strength(item)
    hht_state = get_hht_state(item, hht_lookup)

    # 1. 买入信号增强
    for pk in ['min30', 'min60', 'daily']:
        ps = signal_strength.get(pk, {})
        bl = ps.get('buy_level', 0)
        if bl >= 4.0:
            reasons.append('%s买信号增强(level=%.1f)' % (period_cn(pk), bl))
            break

    # 2. HHT 蓄力后爆发
    for pk in ['daily', 'min60', 'min30']:
        h = hht_state.get(pk, {})
        label = h.get('label', '')
        if '突破' in label or ('蓄力' in label):
            reasons.append('%s%s' % (period_cn(pk), label))
            break

    return len(reasons) > 0, reasons


def detect_close_signal(item, hht_lookup):
    """
    检测平仓信号（战役终结）。
    返回 (has_signal, reasons)
    """
    reasons = []
    t = item.get('trend', {})
    trend_score = t.get('score', 0)
    trend_dir = t.get('direction', '')
    signal_strength = get_signal_strength(item)
    hht_state = get_hht_state(item, hht_lookup)

    # 1. 趋势逆转
    if trend_score <= 3 and trend_dir in ('bearish', 'bearish_bias'):
        reasons.append(f'趋势逆转(评分%d/14)' % trend_score)

    # 2. 日线级卖信号压倒买信号
    for pk in ['daily', 'min60']:
        ps = signal_strength.get(pk, {})
        bl = ps.get('buy_level', 0)
        sl = ps.get('sell_level', 0)
        if sl > bl + 1.0:
            reasons.append('%s空头压过多头(sell=%.1f > buy=%.1f)' % (period_cn(pk), sl, bl))
            break

    # 3. 日线结构溃散（升熵方向）
    daily_ps = signal_strength.get('daily', {})
    if daily_ps.get('pe_phase', '') in ('逆向崩退', '趋势松动', '趋势衰减', '无序放大'):
        reasons.append('日线结构溃散')

    # 4. 操作建议变为回避/观望
    action = item.get('advice', {}).get('action', '')
    if action in ('回避', '不参与'):
        reasons.append('建议%s' % advice_cn(action))

    return len(reasons) > 0, reasons


# ─── HHT 数据加载 ───

def load_hht_data():
    hht_path = os.path.join(BASE, 'signals/tracking/_signals/hht_report.json')
    if os.path.exists(hht_path):
        try:
            raw = json.load(open(hht_path, 'r', encoding='utf-8'))
            return {r['code']: r for r in raw}
        except:
            pass
    return {}

# ─── 扫描新战役建议 ───

def scan_new_campaigns(records):
    """扫描所有标的，找可开仓机会"""
    data = load_cycle_report()
    hht_data = load_hht_data()
    existing = {c['code'] for c in records['campaigns'] if c['status'] == 'active'}

    suggestions = []
    for item in data:
        code = item['code']
        name = item.get('name', '')
        if code in existing:
            continue

        entry_score, reasons = detect_entry_signals(item)
        if entry_score < 4:
            continue  # 不够强

        adv = item.get('advice', {})
        dc = adv.get('dominant_cycle', {})
        dominant = dc.get('dominant_cycle', 'min5')
        action = adv.get('action', '')
        t = item.get('trend', {})
        p = item.get('position', {})
        close = p.get('close', '?')

        suggestions.append({
            'code': code,
            'name': name,
            'entry_score': entry_score,
            'dominant_level': dominant,
            'close': close,
            'trend_score': t.get('score', 0),
            'trend_dir': t.get('direction', ''),
            'advice': action,
            'reasons': reasons,
        })

    return suggestions


def scan_active_campaigns(records):
    """扫描活跃战役，检测事件和平仓信号"""
    data = load_cycle_report()
    hht_data = load_hht_data()
    data_map = {item['code']: item for item in data}

    events = []
    for camp in records['campaigns']:
        if camp['status'] != 'active':
            continue
        code = camp['code']
        item = data_map.get(code)
        if not item:
            events.append({
                'campaign_id': camp['id'],
                'code': code,
                'name': camp['name'],
                'type': 'no_data',
                'reasons': ['无 cycle 数据'],
            })
            continue

        # 检测平仓信号
        has_close, close_reasons = detect_close_signal(item, hht_data)
        if has_close:
            events.append({
                'campaign_id': camp['id'],
                'code': code,
                'name': camp['name'],
                'type': 'close',
                'reasons': close_reasons,
            })
            continue  # 平仓优先级最高，不再检查其他

        # 检测减仓信号
        has_reduce, reduce_reasons = detect_reduce_signal(item, hht_data)
        if has_reduce:
            events.append({
                'campaign_id': camp['id'],
                'code': code,
                'name': camp['name'],
                'type': 'reduce',
                'reasons': reduce_reasons,
            })

        # 检测加仓信号
        has_add, add_reasons = detect_add_signal(item, hht_data)
        if has_add:
            events.append({
                'campaign_id': camp['id'],
                'code': code,
                'name': camp['name'],
                'type': 'add',
                'reasons': add_reasons,
            })

    return events


# ─── 战役状态估算 ───

def estimate_pnl(campaign, current_item):
    """估算当前战役的盈亏百分比"""
    open_price = campaign.get('open', {}).get('price')
    if not open_price or not current_item:
        return None
    p = current_item.get('position', {})
    close = p.get('close')
    if not close:
        return None
    direction = campaign.get('direction', 'long')
    if direction == 'long':
        return (close - open_price) / open_price * 100
    else:
        return (open_price - close) / open_price * 100


def campaign_duration(campaign):
    """计算战役持续天数"""
    open_date_str = campaign.get('open', {}).get('date', '')
    if not open_date_str:
        return 0
    try:
        open_dt = datetime.strptime(open_date_str, '%Y%m%d')
    except:
        return 0

    close = campaign.get('close')
    if close and close.get('date'):
        try:
            close_dt = datetime.strptime(close['date'], '%Y%m%d')
            return (close_dt - open_dt).days
        except:
            pass
    return (datetime.now() - open_dt).days


# ─── 输出模块 ───

def show_status():
    """显示所有活跃战役状态"""
    records = load_op_records()
    data = load_cycle_report()
    data_map = {item['code']: item for item in data}

    active = [c for c in records['campaigns'] if c['status'] == 'active']
    closed = [c for c in records['campaigns'] if c['status'] == 'closed']

    if not active and not closed:
        print('[战役] 暂无战役记录')
        return

    print('')
    print('═' * 60)
    print('  战役状态总览')
    print('═' * 60)
    print('')

    if active:
        print('── 活跃战役 (%d) ──' % len(active))
        print('')
        print('| 标的 | 方向 | 级别 | 开仓日期 | 开仓价 | 现价 | 盈亏 | 持续 | 建议 |')
        print('|------|------|------|---------|-------|------|------|------|------|')
        for camp in active:
            code = camp['code']
            name = camp['name'][:6]
            direction = '多' if camp['direction'] == 'long' else '空'
            d = camp.get('open', {})
            open_date = d.get('date', '?')[-4:]
            open_price = d.get('price', 0)
            open_period = period_cn(d.get('period', '?'))
            event_count = len(camp.get('events', []))
            item = data_map.get(code)
            close_price = item.get('position', {}).get('close', '?') if item else '?'
            pnl = estimate_pnl(camp, item)
            pnl_str = pct_color(pnl) if pnl is not None else '-'
            days = campaign_duration(camp)

            # 操作建议
            adv = item.get('advice', {}).get('action', '') if item else ''
            adv_str = advice_cn(adv) if adv else '-'

            print('| `%s` %s | %s | %s | %s | %.3f | %s | %s | %d天 | %s |' %
                  (code, name, direction, open_period, open_date,
                   open_price, str(close_price)[:7] if isinstance(close_price, float) else '?',
                   pnl_str, days, adv_str))

        print('')

        # 活跃战役详情
        print('── 活跃战役详情 ──')
        print('')
        for camp in active:
            code = camp['code']
            name = camp['name']
            d = camp.get('open', {})
            print('■ `%s` %s' % (code, name))
            print('  开仓: %s %s %s (价格=%.3f)' % (
                d.get('date', '?'), period_cn(d.get('period', '?')),
                d.get('signal', '?'), d.get('price', 0)))
            print('  理由: %s' % d.get('reason', ''))
            item = data_map.get(code)
            if item:
                pnl = estimate_pnl(camp, item)
                if pnl is not None:
                    print('  当前盈亏: %s' % pct_color(pnl))
            events = camp.get('events', [])
            if events:
                for ev in events:
                    ev_type = {'reduce': '⬇减仓', 'add': '⬆加仓', 'adjust': '调整'}.get(ev.get('type', ''), ev.get('type', ''))
                    print('  事件: %s %s %s (价格=%.3f, 比例=%s) %s' % (
                        ev.get('date', '?'), ev_type, period_cn(ev.get('period', '?')),
                        ev.get('price', 0), ev.get('pct_change', '?'), ev.get('reason', '')))
            print('')
    else:
        print('暂无活跃战役。')
        print('')

    if closed:
        print('── 已结束战役 (%d) ──' % len(closed))
        print('')
        win = sum(1 for c in closed if (c.get('stats') or {}).get('total_pct', 0) > 0)
        lose = sum(1 for c in closed if (c.get('stats') or {}).get('total_pct', 0) <= 0)
        print('  胜: %d  负: %d  胜率: %d%%' % (win, lose, win / max(win + lose, 1) * 100))
        for camp in closed:
            code = camp['code']
            direction = '多' if camp['direction'] == 'long' else '空'
            stats = camp.get('stats', {}) or {}
            total_pct = stats.get('total_pct', 0)
            days = stats.get('duration_days', 0)
            print('  `%s` %s | %s | %.1f%% | %d天 | 信号:%s→%s' %
                  (code, camp['name'][:6], direction, total_pct, days,
                   camp.get('open', {}).get('signal', '?'),
                   camp.get('close', {}).get('signal', '?') if camp.get('close') else '?'))
        print('')
    print('─' * 60)
    print('')


def show_suggestions():
    """扫描并显示战役建议（新开仓 + 活跃战役事件）"""
    records = load_op_records()

    # ── 新战役建议 ──
    suggestions = scan_new_campaigns(records)
    if suggestions:
        print('')
        print('═' * 60)
        print('  ★ 新战役建议（可开仓机会）')
        print('═' * 60)
        print('')
        print('| 标的 | 评分 | 趋势 | 主导 | 收盘 | 信号理由 |')
        print('|------|:----:|------|:----:|:----:|---------|')
        suggestions.sort(key=lambda s: s['entry_score'], reverse=True)
        for s in suggestions:
            print('| `%s` %s | **%d/10** | %s(%d) | %s | %.3f | %s |' %
                  (s['code'], s['name'][:6], s['entry_score'],
                   trend_cn(s['trend_dir']), s['trend_score'],
                   period_cn(s['dominant_level']), s['close'],
                   '; '.join(s['reasons'][:3])))
        print('')
        print('确认开仓: python operation_tracker.py --confirm <code>')
        print('')
    else:
        print('[战役] 暂无开仓机会')
        print('')

    # ── 活跃战役事件 ──
    events = scan_active_campaigns(records)
    if events:
        print('═' * 60)
        print('  活跃战役事件信号')
        print('═' * 60)
        print('')
        for ev in events:
            type_cn = {'reduce': '⬇减仓', 'add': '⬆加仓', 'close': '■平仓', 'no_data': '⚠无数据'}.get(ev['type'], ev['type'])
            reasons_str = '; '.join(ev['reasons'])
            print('  `%s` %s → %s' % (ev['code'], ev['name'][:10], type_cn))
            for r in ev['reasons']:
                print('    · %s' % r)
            print('')
        print('─' * 60)
        print('')
    else:
        print('[战役] 活跃战役暂无新增事件')
        print('')


def list_all():
    """列出所有战役记录"""
    records = load_op_records()
    if not records['campaigns']:
        print('[战役] 暂无战役记录')
        return
    print('')
    print('═' * 60)
    print('  全部战役列表 (%d)' % len(records['campaigns']))
    print('═' * 60)
    print('')
    for i, camp in enumerate(records['campaigns'], 1):
        status = '🟢活跃' if camp['status'] == 'active' else '🔴已结束'
        d = camp.get('open', {})
        direction = '多' if camp['direction'] == 'long' else '空'
        print('%d. `%s` %s | %s | %s | %s %s' %
              (i, camp['code'], camp['name'][:8], status, direction,
               d.get('date', '?'), period_cn(d.get('period', '?'))))

        if camp.get('events'):
            print('   事件: %d 条' % len(camp['events']))
        if camp.get('stats') and camp['stats'].get('total_pct') is not None:
            s = camp['stats']
            print('   统计: %.1f%% | %d天 | 回撤%.1f%%' %
                  (s.get('total_pct', 0), s.get('duration_days', 0), s.get('max_drawdown', 0)))
        print('')
    print('─' * 60)
    print('')


# ─── 确认开仓 ───

def confirm_campaign(code):
    """创建新战役（用户确认后执行）"""
    data = load_cycle_report()
    data_map = {item['code']: item for item in data}
    item = data_map.get(code)
    if not item:
        print('[战役] ❌ 未找到标的: %s' % code)
        return

    records = load_op_records()
    # 检查是否已有活跃战役
    for camp in records['campaigns']:
        if camp['code'] == code and camp['status'] == 'active':
            print('[战役] ⚠️ %s 已有活跃战役，不能重复开仓' % code)
            return

    name = item.get('name', '')
    p = item.get('position', {})
    close = p.get('close', 0)
    adv = item.get('advice', {})
    dc = adv.get('dominant_cycle', {})
    dominant = dc.get('dominant_cycle', 'min15')
    t = item.get('trend', {})
    trend_score = t.get('score', 0)

    today = datetime.now().strftime('%Y%m%d')
    same_code_count = len([c for c in records['campaigns'] if c['code'] == code]) + 1
    campaign_id = '%s_%s_%d' % (code, today, same_code_count)

    entry_signal = 'EXPMA金叉+评分%d' % trend_score
    signal_strength = get_signal_strength(item)

    # 找信号最强的级别
    best_period = 'min30'
    for pk in ['daily', 'min60', 'min30', 'min15']:
        ps = signal_strength.get(pk, {})
        bl = ps.get('buy_level', 0)
        if bl >= 3.5:
            best_period = pk
            break

    direction = 'long'
    entry_score, reasons = detect_entry_signals(item)

    campaign = {
        'id': campaign_id,
        'code': code,
        'name': name,
        'direction': direction,
        'status': 'active',
        'open': {
            'date': today,
            'period': best_period,
            'signal': entry_signal,
            'price': round(close, 4) if isinstance(close, (int, float)) else close,
            'score': trend_score,
            'dominant_level': dominant,
            'reason': '; '.join(reasons),
            'user_confirmed': True,
        },
        'events': [],
        'close': None,
        'stats': None,
    }

    records['campaigns'].append(campaign)
    save_op_records(records)
    print('[战役] ✅ 战役已创建: %s %s | 级别=%s | 价格=%.3f | 理由=%s' %
          (code, name, period_cn(best_period), close, '; '.join(reasons)))


# ─── 确认事件（减仓/加仓） ───

def confirm_event(campaign_id, event_type):
    """为活跃战役记录一个事件（reduce/add）"""
    records = load_op_records()
    data = load_cycle_report()
    data_map = {item['code']: item for item in data}

    # 找战役
    camp = None
    for c in records['campaigns']:
        if c['id'] == campaign_id:
            camp = c
            break
    if not camp:
        print('[战役] ❌ 未找到战役: %s' % campaign_id)
        return
    if camp['status'] != 'active':
        print('[战役] ⚠️ 战役 %s 已结束' % campaign_id)
        return

    code = camp['code']
    item = data_map.get(code)
    if not item:
        print('[战役] ❌ 标的 %s 无数据' % code)
        return

    today = datetime.now().strftime('%Y%m%d')
    p = item.get('position', {})
    close = p.get('close', 0)
    adv = item.get('advice', {})
    dc = adv.get('dominant_cycle', {})
    dom_level = dc.get('dominant_cycle', 'min30')

    # 根据事件类型检测具体信号
    if event_type == 'reduce':
        _, reasons = detect_reduce_signal(item, load_hht_data())
    elif event_type == 'add':
        _, reasons = detect_add_signal(item, load_hht_data())
    else:
        print('[战役] ❌ 不支持的事件类型: %s（支持: reduce, add）' % event_type)
        return

    reason_str = '; '.join(reasons) if reasons else ('%s信号' % (event_type))

    event = {
        'date': today,
        'type': event_type,
        'period': dom_level,
        'price': round(close, 4) if isinstance(close, (int, float)) else close,
        'pct_change': '-30%' if event_type == 'reduce' else '+20%',
        'reason': reason_str,
        'user_confirmed': True,
    }

    if 'events' not in camp:
        camp['events'] = []
    camp['events'].append(event)
    save_op_records(records)

    ev_type_cn = '减仓' if event_type == 'reduce' else '加仓'
    print('[战役] ✅ 事件已记录: %s %s | %s | 价格=%.3f | 比例=%s | %s' %
          (code, ev_type_cn, period_cn(dom_level), close, event['pct_change'], reason_str))


# ─── 平仓 ───

def close_campaign(campaign_id):
    """结束一个活跃战役，计算统计"""
    records = load_op_records()
    data = load_cycle_report()
    data_map = {item['code']: item for item in data}

    camp = None
    for c in records['campaigns']:
        if c['id'] == campaign_id:
            camp = c
            break
    if not camp:
        print('[战役] ❌ 未找到战役: %s' % campaign_id)
        return
    if camp['status'] != 'active':
        print('[战役] ⚠️ 战役 %s 尚未激活或已结束' % campaign_id)
        return

    code = camp['code']
    item = data_map.get(code)
    today = datetime.now().strftime('%Y%m%d')

    # 计算平仓价格和统计
    if item:
        p = item.get('position', {})
        close_price = p.get('close', 0)
        adv = item.get('advice', {})
        dc = adv.get('dominant_cycle', {})
        close_period = dc.get('dominant_cycle', 'min30')
        close_reason = '用户确认平仓'
        _, reasons = detect_close_signal(item, load_hht_data())
        if reasons:
            close_reason = '; '.join(reasons)
    else:
        close_price = camp.get('open', {}).get('price', 0)
        close_period = 'min30'
        close_reason = '无数据'

    open_price = camp.get('open', {}).get('price', 0)
    direction = camp.get('direction', 'long')

    if open_price > 0 and close_price > 0:
        if direction == 'long':
            total_pct = (close_price - open_price) / open_price * 100
        else:
            total_pct = (open_price - close_price) / open_price * 100
    else:
        total_pct = 0

    # 统计
    open_date_str = camp.get('open', {}).get('date', today)
    try:
        open_dt = datetime.strptime(open_date_str, '%Y%m%d')
        close_dt = datetime.strptime(today, '%Y%m%d')
        duration_days = (close_dt - open_dt).days
    except:
        duration_days = 0

    # 简单回撤估算（基于事件中的建仓/减仓价）
    max_dd = 0
    events = camp.get('events', [])
    for ev in events:
        if ev['type'] == 'reduce':
            ev_price = ev.get('price', 0)
            if ev_price > 0 and open_price > 0:
                dd = (open_price - ev_price) / open_price * 100
                if dd < max_dd:
                    max_dd = dd

    camp['close'] = {
        'date': today,
        'period': close_period,
        'signal': '平仓',
        'price': round(close_price, 4) if isinstance(close_price, (int, float)) else close_price,
        'reason': close_reason,
    }
    camp['stats'] = {
        'total_pct': round(total_pct, 2),
        'duration_days': duration_days,
        'max_drawdown': round(abs(min(max_dd, 0)), 2),
    }
    camp['status'] = 'closed'
    save_op_records(records)

    print('[战役] ✅ 战役已结束: %s %s | 开仓=%.3f→平仓=%.3f | %.1f%% | %d天 | 回撤%.1f%%' %
          (code, camp.get('name', '')[:6], open_price, close_price, total_pct, duration_days, abs(min(max_dd, 0))))
    print('  理由: %s' % close_reason)


# ─── 主入口 ───

def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if len(sys.argv) < 2:
        print('用法:')
        print('  python operation_tracker.py --scan                # 扫描新战役+事件')
        print('  python operation_tracker.py --status              # 显示战役状态')
        print('  python operation_tracker.py --list                # 列出全部战役')
        print('  python operation_tracker.py --confirm <code>      # 确认开仓')
        print('  python operation_tracker.py --event <id> <type>   # 记录事件(reduce/add)')
        print('  python operation_tracker.py --close <id>          # 结束战役')
        return

    cmd = sys.argv[1]

    if cmd == '--status':
        show_status()
    elif cmd == '--scan':
        show_suggestions()
    elif cmd == '--list':
        list_all()
    elif cmd == '--confirm':
        if len(sys.argv) < 3:
            print('用法: python operation_tracker.py --confirm <code>')
            return
        confirm_campaign(sys.argv[2])
        show_status()
    elif cmd == '--event':
        if len(sys.argv) < 4:
            print('用法: python operation_tracker.py --event <campaign_id> <type>')
            print('示例: python operation_tracker.py --event sh513310_20260514_1 reduce')
            return
        confirm_event(sys.argv[2], sys.argv[3])
        show_status()
    elif cmd == '--close':
        if len(sys.argv) < 3:
            print('用法: python operation_tracker.py --close <campaign_id>')
            print('示例: python operation_tracker.py --close sh513310_20260514_1')
            return
        close_campaign(sys.argv[2])
        show_status()
    else:
        print('未知命令: %s' % cmd)
        print('可用: --scan, --status, --list, --confirm, --event, --close')


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""
cycle_engine 报告层 — 格式化输出 / 保存结果
"""
import json
import os
from .utils import OUTPUT_PATH, PERIODS, PERIOD_LABELS

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



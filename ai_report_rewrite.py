# -*- coding: utf-8 -*-
"""
AI 自然语言日报生成器 — v6

数据源: cycle_report.json（位置/趋势/评分）+ synthesized_report.json（ABCD/结构/HHT/信号）
表格全部代码生成，深度分析由 AI 撰写。

生成: reports/daily/YYYYMMDD_v3_nl.md
"""
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE))

from ai_analyzer import call_llm, load_persona, load_framework
from config import NAME_MAP

CYCLE_REPORT = BASE / 'signals' / 'tracking' / '_signals' / 'cycle_report.json'
SYNTH_REPORT = BASE / 'signals' / 'tracking' / '_signals' / 'synthesized_report.json'
SCORE_HISTORY = BASE / 'signals' / 'tracking' / '_signals' / 'score_history.json'
HHT_REPORT = BASE / 'signals' / 'tracking' / '_signals' / 'hht_report.json'
REPORT_DIR = BASE / 'reports' / 'daily'

DIR_EN_MAP = {
    'bullish': '上涨', 'bullish_bias': '偏多', 'neutral': '中性',
    'bearish_bias': '偏空', 'bearish': '下跌',
}

GRADE_ORDER = {'A': 0, 'A-': 1, 'A假': 2, 'B': 3, 'C': 4, 'D': 5}
ADVICE_GRADE_MAP = {
    'actionable': '可操作', 'observe_strong': '强势追踪',
    'neutral_bias': '中性偏强', 'observe': '关注',
    'observe_weak': '观望', 'avoid': '回避',
}


def load_json(path):
    if not path.exists():
        return None
    try:
        return json.load(open(path, 'r', encoding='utf-8'))
    except:
        return None


def hht_label_from_code(code, hht_labels):
    return hht_labels.get(code, '-')


def get_stock_name(code, cycle, synth):
    for s in cycle:
        if s.get('code') == code:
            return s.get('name', code)
    if synth and code in synth:
        return synth[code].get('name', code)
    return code


# ─── 表格生成 ───

def build_table(cycle, synth, hht_labels):
    """从 synthesized_report 取数据，生成标的跟踪总览表"""
    if not isinstance(synth, dict):
        synth = {}

    # 组装每只标的的数据
    stocks = []
    for s in cycle:
        code = s.get('code', '')
        synth_item = synth.get(code, {})

        name = s.get('name', '') or synth_item.get('name', '')
        trend_dir = synth_item.get('trend', {}).get('direction', '')
        trend_dir_cn = DIR_EN_MAP.get(trend_dir, trend_dir)
        score = synth_item.get('trend', {}).get('score', '?')

        grade = synth_item.get('grade', '')
        action = synth_item.get('action', '')
        adv_grade = synth_item.get('grade_detail', {}).get('advice_grade', '')

        # 位置：从 cycle 取
        pos_info = s.get('position', {})
        if isinstance(pos_info, dict):
            position = pos_info.get('label', '—')
            close = pos_info.get('close', '?')
        else:
            position = str(pos_info) if pos_info else '—'
            close = s.get('trend', {}).get('close', '?')
        close_str = f'{close:.3f}' if isinstance(close, (int, float)) else '?'

        # HHT
        hht = hht_label_from_code(code, hht_labels)

        # 结构状态 / 信号描述
        structure = synth_item.get('structure_status', '—')
        signal_summary = synth_item.get('signal_summary', '—')
        dominant = synth_item.get('dominant_period', '?')
        if dominant.startswith('min'):
            dominant = dominant.replace('min', '')

        # 分类判断（基于四级分类 × 三级买侧）
        if action in ('趋势加满', '趋势买', '震荡买', '抄底买', '反转加满'):
            cat = '可操作'
        elif action.endswith('试错') or action in ('趋势试错', '震荡试错', '反弹试错', '筑底试错', '持有'):
            cat = '中性偏强'
        elif action == '减仓':
            cat = '关注'
        else:
            cat = '观望'

        stocks.append({
            'code': code, 'name': name, 'close': close_str,
            'trend': f'{trend_dir_cn} {grade}' if grade else trend_dir_cn,
            'signal': signal_summary,
            'structure': structure,
            'hht': hht,
            'dom': f'{dominant}分钟' if dominant else '?',
            'action': action or '—',
            'cat': cat,
            'score': score,
        })

    # 排序：A→B→C→D，同级别按评分降序
    def sort_key(stk):
        g = stk.get('trend', '').split()[-1] if stk.get('trend') else ''
        g_order = GRADE_ORDER.get(g, 99)
        sc = stk.get('score', 0)
        if not isinstance(sc, (int, float)):
            sc = 0
        # 分类排序
        cat_order = {'可操作': 0, '强势追踪': 1, '中性偏强': 2, '关注': 3, '观望': 4}
        return (cat_order.get(stk['cat'], 9), g_order, -sc)

    stocks.sort(key=sort_key)

    # 组装表格
    cat_labels_order = ['可操作', '中性偏强', '关注', '观望']
    cat_descs = {
        '可操作': ('🔴', 'A级信号确认，共振加满/金叉买'),
        '中性偏强': ('🟡', 'B级偏多，MA试错或持有观察'),
        '关注': ('⚪', '减仓信号或方向不明，等待'),
        '观望': ('⚪', '弱势建议回避/观望'),
    }

    hdr = '| 标的 收盘 | 日线趋势 | 分钟闭环 | 结构状态 | HHT | 主导周期 | 操作建议 |'
    sep = '|----------|----------|----------|--------|-----|----------|----------|'

    lines = ['## 一、标的跟踪总览', '']
    for cl in cat_labels_order:
        group = [stk for stk in stocks if stk['cat'] == cl]
        if not group:
            continue
        icon, desc = cat_descs.get(cl, ('', ''))
        lines.append(f'### {icon} {cl} ({len(group)} 只) — {desc}')
        lines.append('')
        lines.append(hdr)
        lines.append(sep)
        for stk in group:
            lines.append(
                f'| {stk["name"]} {stk["code"]} {stk["close"]}'
                f' | {stk["trend"]}'
                f' | {stk["signal"]}'
                f' | {stk["structure"]}'
                f' | {stk["hht"]}'
                f' | {stk["dom"]}'
                f' | {stk["action"]} |'
            )
        lines.append('')
    return '\n'.join(lines)


# ─── 分数对比表 ───

def build_score_table(cycle, score_data):
    history = (score_data or {}).get('history', [])
    if len(history) < 2:
        return ''
    t_scores = history[-1].get('scores', {})
    y_scores = history[-2].get('scores', {})
    lines = ['---', '', '## 分数起伏（今日 vs 昨日）', '',
             f'> 对比 {history[-2]["date"]} → 今日 {history[-1]["date"]}，跟踪趋势评分变化', '',
             '| 标的 | 昨日 | 今日 | 变动 | 方向 |', '|------|------|------|------|------|']
    for s in cycle:
        code = s.get('code', '')
        name = s.get('name', '')
        n = t_scores.get(code, {})
        p = y_scores.get(code, {})
        now = n.get('score') if isinstance(n, dict) else n
        prev = p.get('score') if isinstance(p, dict) else p
        if now is None or prev is None:
            continue
        diff = now - prev
        ds = f'🔻{abs(diff)}' if diff < 0 else (f'🔺{diff}' if diff > 0 else '➖')
        td = n.get('direction', '') if isinstance(n, dict) else ''
        yd = p.get('direction', '') if isinstance(p, dict) else ''
        tdc = DIR_EN_MAP.get(td, td)
        ydc = DIR_EN_MAP.get(yd, yd)
        dr = '→' if td == yd else f'{ydc}→{tdc}'
        lines.append(f'| {name} {code} | {prev} | {now} | {ds} | {dr} |')
    return '\n'.join(lines)


# ─── 深度分析（AI） ───

def build_prompt():
    persona = load_persona()
    framework = load_framework()
    parts = []
    if persona:
        parts.append(persona)
    if framework:
        parts.append('\n\n---\n\n## 分析框架\n\n')
        parts.append(framework)
    parts.append('''

---

## 输出要求

根据今日量化数据，写一份"重点标的深度分析"。用三级标题 `###` 分隔。

写作要求：
- 像交易搭档在跟小潘潘说话：直白、有观点、有行动指引
- 不要写表格，只写自然语言段落
- 每只标的 3-5 句：趋势判断 + 关键信号/风险 + 操作建议
- 评分显著变化的要重点说明原因
- 强势标的简略带过，只说风险
- 弱势标的合并点评
- 把指标翻译成人话
''')
    return '\n'.join(parts)


def main():
    date_str = datetime.now().strftime('%Y%m%d')
    print('[加载] 数据...')

    cycle = load_json(CYCLE_REPORT)
    if not cycle:
        print('[错误] 找不到 cycle_report.json')
        sys.exit(1)
    # 排除 volume_leader 等动态标的，仅保留固定跟踪标的
    cycle = [r for r in cycle if r.get('code') in NAME_MAP]
    synth = load_json(SYNTH_REPORT)
    score_data = load_json(SCORE_HISTORY)
    hht_raw = load_json(HHT_REPORT)
    hht_labels = {}
    if hht_raw:
        for r in hht_raw:
            periods = r.get('periods', {})
            daily = periods.get('daily', {})
            summary = daily.get('summary', {})
            label = summary.get('stability_label', '-')
            code = r.get('code', '')
            if label and label != '-':
                hht_labels[code] = label
    print(f'  {len(cycle)} 标的 | synth {len(synth) if isinstance(synth,dict) else 0} | HHT {len(hht_labels)}')

    table_section = build_table(cycle, synth, hht_labels)
    score_section = build_score_table(cycle, score_data)
    print(f'  表格 {len(table_section)} | 分数 {len(score_section)}')

    # AI 深度分析
    context_lines = [f'日期: {date_str}', '']

    # 大盘
    dp = next((s for s in cycle if s.get('code') == 'sh000001'), None)
    if dp:
        t = dp.get('trend', {})
        context_lines.append(f'大盘: {DIR_EN_MAP.get(t.get("direction",""),t.get("direction",""))} {t.get("score","?")}/14分')
        context_lines.append(f'大盘建议: {dp.get("advice",{}).get("action","?") if isinstance(dp.get("advice"),dict) else "?"}')
        context_lines.append(f'大盘HHT: {hht_labels.get("sh000001","-")}')
        context_lines.append('')

    # 评分变化
    history = (score_data or {}).get('history', [])
    if len(history) >= 2:
        t_scores = history[-1].get('scores', {})
        y_scores = history[-2].get('scores', {})
        big = []
        for s in cycle:
            code = s.get('code', '')
            n = t_scores.get(code, {})
            p = y_scores.get(code, {})
            now = n.get('score') if isinstance(n, dict) else n
            prev = p.get('score') if isinstance(p, dict) else p
            if now is not None and prev is not None and abs(now - prev) >= 2:
                big.append(f'{s.get("name","")}({code}): {prev}→{now} ({now-prev:+.1f})')
        if big:
            context_lines.append('评分显著变化:')
            for b in big:
                context_lines.append(f'  {b}')
            context_lines.append('')

    # 每只标的
    for s in cycle:
        code = s.get('code', '')
        name = s.get('name', '')
        syn = (synth or {}).get(code, {})
        t = syn.get('trend', s.get('trend', {}))
        pos_info = s.get('position', {})
        close = pos_info.get('close', '?') if isinstance(pos_info, dict) else s.get('trend', {}).get('close', '?')
        close_str = f'{close:.3f}' if isinstance(close, (int, float)) else '?'
        adv = s.get('advice', {})
        adv_action = adv.get('action', '') if isinstance(adv, dict) else str(adv)

        context_lines.append(f'【{name}】收盘{close_str}')
        context_lines.append(f'趋势:{t.get("direction","?")} 评分:{t.get("score","?")}/14 [{t.get("zone_label","")}]')
        context_lines.append(f'操作级别:{syn.get("grade","?")} 建议:{adv_action}')
        context_lines.append(f'HHT:{hht_labels.get(code,"-")} 信号:{syn.get("signal_summary","-")} 结构:{syn.get("structure_status","-")}')

        pos_label = pos_info.get('label', '') if isinstance(pos_info, dict) else str(pos_info) if pos_info else ''
        if pos_label:
            context_lines.append(f'位置:{pos_label}')

        me = s.get('magnitude_engine', {})
        if me and me.get('description'):
            context_lines.append(f'量级引擎:{me["description"]}')
        context_lines.append('')

    context = '\n'.join(context_lines)
    print(f'  AI上下文 {len(context)} 字符')

    print('[AI] 深度分析...')
    system_prompt = build_prompt()
    user_msg = f'请分析以下今日数据，写重点标的深度分析。\n\n{context}'
    deep, provider = call_llm(system_prompt, user_msg, max_tokens=4096)
    print(f'  [{provider}] {len(deep)} 字符')

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = REPORT_DIR / f'{date_str}_v3_nl.md'
    full = f'''# 每日交易日报（AI 自然语言版）

**日期**: {date_str}
**生成**: ai_report_rewrite.py → {provider}

---

{table_section}

---

{score_section}

---

## 三、重点标的深度分析

{deep}

---

*数据来源：synthesized_report.json + cycle_report.json。深度分析由 AI 撰写。*
'''
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(full)
    print(f'[保存] {output_path}')


if __name__ == '__main__':
    main()

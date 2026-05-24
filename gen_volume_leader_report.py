# -*- coding: utf-8 -*-
"""
成交量领导者 AI 日报生成器

从 cycle_report.json 过滤出 volume leader 标的，构建总览表，
复用 ai_analyzer 调用 LLM 生成自然语言分析报告。

生成: reports/volume_leader/YYYYMMDD_volume_leader_report.md
"""

import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
import config
from ai_analyzer import call_llm, load_persona, load_framework

_BASE = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking')
CYCLE_REPORT = os.path.join(_BASE, 'cycle_report.json')
SYNTH_REPORT = os.path.join(_BASE, 'synthesized_report.json')
SCORE_HISTORY = os.path.join(_BASE, 'score_history.json')
UNIVERSE_PATH = os.path.join(_BASE, 'volume_leader_universe.json')
NAME_CACHE = os.path.join(_BASE, 'stock_names.csv')
REPORT_DIR = os.path.join(config.PROJECT_ROOT, 'reports', 'volume_leader')


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_universe_codes():
    if not os.path.exists(UNIVERSE_PATH):
        return set()
    with open(UNIVERSE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return set(data.get('universe', []))


def load_names():
    """从 stock_names.csv 加载名称映射"""
    names = {}
    if not os.path.exists(NAME_CACHE):
        return names
    import pandas as pd
    df = pd.read_csv(NAME_CACHE, encoding='utf-8', dtype=str)
    for _, row in df.iterrows():
        names[row['code']] = row.get('name', '')
    return names


def build_summary_table(cycle_items, synth_data, names):
    """构建成交量领导者总览表"""
    lines = ['## 一、成交量领导者总览', '']
    lines.append(f'**{datetime.now().strftime("%Y-%m-%d")}** | {len(cycle_items)} 只标的')
    lines.append('')
    lines.append('| 代码 | 名称 | 收盘 | 趋势 | 评分 | ABCD | 操作建议 | 主导周期 | 结构状态 |')
    lines.append('|:---|:---|---:|:---|---:|:---|:---|:---|:---|')

    DIR_MAP = {
        'bullish': '上涨', 'bullish_bias': '偏多', 'neutral': '中性',
        'bearish_bias': '偏空', 'bearish': '下跌',
    }

    for item in cycle_items:
        code = item.get('code', '')
        name = names.get(code, item.get('name', code))
        pos = item.get('position', {})
        close = pos.get('close', '-') if isinstance(pos, dict) else '-'
        close_str = f'{close:.2f}' if isinstance(close, (int, float)) else '-'

        trend = item.get('trend', {})
        score = trend.get('score', '-')
        direction = DIR_MAP.get(trend.get('direction', ''), trend.get('direction', '-'))

        adv = item.get('advice', {})
        grade = adv.get('grade_label', '-') if isinstance(adv, dict) else '-'
        action = adv.get('action', '-') if isinstance(adv, dict) else '-'

        dom_cycle = (adv.get('dominant_cycle', {}) if isinstance(adv, dict) else {})
        dom_label = dom_cycle.get('dominant_label', '-') if isinstance(dom_cycle, dict) else '-'

        # structure from synth if available, otherwise from cycle_report rs_density
        syn = (synth_data or {}).get(code, {}) if isinstance(synth_data, dict) else {}
        structure = syn.get('structure_status', '')
        if not structure:
            rs = item.get('rs_density', {})
            if isinstance(rs, dict) and rs.get('chan_structure'):
                structure = rs['chan_structure']
            elif isinstance(rs, dict) and rs.get('rs_label'):
                structure = rs['rs_label']

        lines.append(f'| {code} | {name} | {close_str} | {direction} | {score} | {grade} | {action} | {dom_label} | {structure} |')

    return '\n'.join(lines)


def build_score_trend(cycle_items, score_data):
    """构建评分趋势表"""
    history = (score_data or {}).get('history', [])
    if len(history) < 2:
        return ''

    today = history[-1].get('scores', {})
    yesterday = history[-2].get('scores', {})

    lines = ['', '## 二、评分变化', '']
    lines.append('| 代码 | 名称 | 昨日 | 今日 | 变动 |')
    lines.append('|:---|---:|---:|---:|:---:|')

    changed = []
    unchanged = []
    for item in cycle_items:
        code = item.get('code', '')
        name = item.get('name', code)
        t = today.get(code, {})
        y = yesterday.get(code, {})
        now = t.get('score') if isinstance(t, dict) else t
        prev = y.get('score') if isinstance(y, dict) else y
        if now is not None and prev is not None:
            diff = now - prev
            if abs(diff) >= 0.5:
                direction = '↑' if diff > 0 else ('↓' if diff < 0 else '→')
                changed.append((code, name, prev, now, diff, direction))
            else:
                unchanged.append((code, name, prev, now, diff))

    for code, name, prev, now, diff, direction in changed:
        lines.append(f'| {code} | {name} | {prev:.1f} | {now:.1f} | {direction} {diff:+.1f} |')
    for code, name, prev, now, diff in unchanged:
        lines.append(f'| {code} | {name} | {prev:.1f} | {now:.1f} | → |')

    return '\n'.join(lines)


def build_ai_context(cycle_items, synth_data):
    """为 AI 分析构建品种上下文"""
    lines = []
    for item in cycle_items:
        code = item.get('code', '')
        name = item.get('name', code)
        pos = item.get('position', {})
        close = pos.get('close', '-') if isinstance(pos, dict) else '-'
        close_str = f'{close:.2f}' if isinstance(close, (int, float)) else '?'

        trend = item.get('trend', {})
        syn = (synth_data or {}).get(code, {}) if isinstance(synth_data, dict) else {}
        adv = item.get('advice', {})
        action = adv.get('action', '-') if isinstance(adv, dict) else '-'

        lines.append(f'【{name}】{code} 收盘{close_str}')
        lines.append(f'  趋势:{trend.get("direction","?")} 评分:{trend.get("score","?")}/16')
        lines.append(f'  ABCD:{syn.get("grade","?")} 建议:{action}')
        lines.append(f'  信号:{syn.get("signal_summary","-")} 结构:{syn.get("structure_status","-")}')

        me = item.get('magnitude_engine', {})
        if me and me.get('description'):
            lines.append(f'  量级引擎:{me["description"]}')
        lines.append('')

    return '\n'.join(lines)


def main():
    date_str = datetime.now().strftime('%Y%m%d')
    print('[加载] 数据...')

    cycle = load_json(CYCLE_REPORT)
    if not cycle:
        print('[错误] 找不到 cycle_report.json，请先运行 cycle_engine.py --save')
        sys.exit(1)

    universe = load_universe_codes()
    if not universe:
        print('[错误] universe 为空，请先运行 volume_leader_screener.py --sync-universe')
        sys.exit(1)

    # 过滤到 volume leader 标的
    vl_items = [r for r in cycle if r.get('code') in universe]
    if not vl_items:
        print('[信息] cycle_report.json 中没有 volume leader 标的，请先运行 update_volume_leaders.py + cycle_engine.py --save')
        sys.exit(0)

    synth = load_json(SYNTH_REPORT)
    score_data = load_json(SCORE_HISTORY)
    names = load_names()

    # 补全名称（prefer stock_names.csv, fallback cycle_report）
    for item in vl_items:
        code = item.get('code', '')
        if code in names and not item.get('name'):
            item['name'] = names[code]

    # 按评分降序
    vl_items.sort(key=lambda x: x.get('trend', {}).get('score', 0), reverse=True)

    print(f'  volume leader: {len(vl_items)} 只标的')

    # ─── 构建报告 ───
    table_section = build_summary_table(vl_items, synth, names)
    score_section = build_score_trend(vl_items, score_data)

    # ─── AI 分析 ───
    print('[AI] 深度分析...')
    ai_ctx = build_ai_context(vl_items, synth)
    system_prompt = (
        load_persona() + '\n' + load_framework() + '\n\n'
        '你正在分析的是"成交量领导者"选股池——这些是全市场成交额TOP50且逼近历史新高的强势标的。'
        '请识别出重点关注的标的（趋势清晰+信号明确），给出操作优先级和风险提示。'
        '成交额大=机构参与度高，逼近新高=上方无套牢盘压力，同时有假突破风险需警惕。'
    )
    user_msg = f'请分析以下成交量领导者数据，识别重点标的和操作机会。\n\n{ai_ctx}'
    deep, provider = call_llm(system_prompt, user_msg, max_tokens=4096)
    print(f'  [{provider}] {len(deep)} 字符')

    # ─── 保存报告 ───
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f'{date_str}_volume_leader_report.md')

    total_universe = len(universe)
    top3 = [r for r in vl_items if r.get('trend', {}).get('score', 0) >= 13]
    strong = [r for r in vl_items if 10 <= r.get('trend', {}).get('score', 0) < 13]

    report = f'''# 成交量领导者 AI 日报

**日期**: {datetime.now().strftime('%Y-%m-%d')}
**生成**: gen_volume_leader_report.py → {provider}
**宇宙**: {total_universe} 只标的 | 趋势强势(≥13分): {len(top3)} 只 | 偏多(10-12分): {len(strong)} 只

---

{table_section}
{score_section}

---

## 三、AI 深度分析

{deep}

---

*数据来源：cycle_report.json + volume_leader_universe.json。深度分析由 AI 撰写。*
*成交量领导者定义：全市场当日成交额TOP50 + 近180日内创历史新高（原始价格，距历史最高≤20%）。*
'''

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f'[保存] {report_path}')
    return report_path


if __name__ == '__main__':
    main()

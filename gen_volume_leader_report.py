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
import csv as csv_module
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
import config
from ai_analyzer import call_llm, load_persona, load_framework
from tools.volume_leader.filter_engine import (
    check_ma_chain, check_expma_golden,
    check_no_recent_death, check_close_above_ma, check_pe_gate,
)

_BASE = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking')
CYCLE_REPORT = os.path.join(_BASE, '_signals', 'cycle_report.json')
SYNTH_REPORT = os.path.join(_BASE, '_signals', 'synthesized_report.json')
SCORE_HISTORY = os.path.join(_BASE, '_signals', 'score_history.json')
UNIVERSE_PATH = os.path.join(_BASE, '_funds', 'volume_leader_universe.json')
NAME_CACHE = os.path.join(_BASE, '_funds', 'stock_names.csv')
REPORT_DIR = os.path.join(config.PROJECT_ROOT, 'reports', 'volume_leader')


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


SIGNAL_STAR_BUY = '★买'
TRACKING_DIR = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking')


def _load_csv(code, period='min5'):
    """读取信号CSV，返回list[dict]"""
    path = os.path.join(TRACKING_DIR, code, f'{period}_signals.csv')
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv_module.DictReader(f)
        return list(reader)


def _pe_not_rising_5m(row):
    """→ filter_engine.check_pe_gate"""
    return check_pe_gate(row)


DAILY_PE_CACHE = {}

def _daily_pe_ok(code, bar_date):
    """日线PE非升熵 (用daily CSV的pe_chg_5)"""
    if code not in DAILY_PE_CACHE:
        rows = _load_csv(code, 'daily')
        if not rows:
            DAILY_PE_CACHE[code] = {}
            return True
        DAILY_PE_CACHE[code] = {}
        for r in rows:
            d = r.get('date', '').strip()
            DAILY_PE_CACHE[code][d] = check_pe_gate(r)
    return DAILY_PE_CACHE[code].get(bar_date, True)


def compute_filter_level(code):
    """从5分钟信号CSV计算当前 filter level: None / 'ma' / 'jincha' / 'resonance'

    PE门禁 (基于回测验证):
    - MA级: 日线PE升熵 → 不展示 (MA级最佳=+pe_d, 均收益+0.96%)
    - 金叉级/共振级: 5分钟PE升熵 → 不展示 (金叉级最佳=+pe, 胜率+10.2%)
    """
    rows = _load_csv(code, 'min5')
    if not rows or len(rows) < 2:
        return None

    # 找最近一根有★买的bar（回溯最近96根=约1天）
    star_i = -1
    for i in range(len(rows) - 1, max(len(rows) - 96, -1), -1):
        row = rows[i]
        if (row.get('buy_signal', '') or '').strip() == SIGNAL_STAR_BUY:
            star_i = i
            break
    if star_i < 0:
        return None

    star = rows[star_i]
    if not check_ma_chain(star):
        return None  # 只有裸★买，不展示

    if not check_no_recent_death(rows, star_i, 20):
        return None

    # 60分钟expma黄线上方
    min60 = _load_csv(code, 'min60')
    if not min60 or not check_close_above_ma(min60[-1], 'expma50'):
        return None

    bar_date = star.get('date', '').strip()

    # 5分钟EXPMA金叉？
    if not check_expma_golden(star):
        # ── MA级: 日线PE门禁 ──
        if not _daily_pe_ok(code, bar_date):
            return None  # 日线混沌, MA级也不做
        return 'ma'  # MA级(试错)

    # ── 金叉级以上: 5分钟PE门禁 ──
    if not _pe_not_rising_5m(star):
        return None  # 微观结构混沌, 金叉也不可靠

    # 共振检测：15分 + 30分金叉
    min15 = _load_csv(code, 'min15')
    min30 = _load_csv(code, 'min30')
    m15_jincha = False
    m30_jincha = False
    for csv_rows, period in [(min15, 'min15'), (min30, 'min30')]:
        if csv_rows:
            last = csv_rows[-1]
            ec = (last.get('expma_cross', '') or '').strip()
            if ec == '金叉':
                if period == 'min15':
                    m15_jincha = True
                else:
                    m30_jincha = True
    if m15_jincha or m30_jincha:
        return 'resonance'  # 共振级(买完)
    return 'jincha'  # 金叉级(买)


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
    lines.append('| 代码 | 名称 | 收盘 | 趋势 | 评分 | 操作级别 | 操作建议 | 入场信号 | 主导周期 | 结构状态 |')
    lines.append('|:---|:---|---:|:---|---:|:---|:---|:---|:---|:---|')

    DIR_MAP = {
        'bullish': '上涨', 'bullish_bias': '偏多', 'neutral': '中性',
        'bearish_bias': '偏空', 'bearish': '下跌',
    }

    FILTER_LABEL = {'ma': 'MA级(试错)', 'jincha': '金叉级(买)', 'resonance': '共振级(买完)'}

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

        # filter level 入场信号
        fl = compute_filter_level(code)
        fl_str = FILTER_LABEL.get(fl, '')

        lines.append(f'| {code} | {name} | {close_str} | {direction} | {score} | {grade} | {action} | {fl_str} | {dom_label} | {structure} |')

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
        lines.append(f'  趋势:{trend.get("direction","?")} 评分:{trend.get("score","?")}/14 [{trend.get("zone_label","")}]')
        lines.append(f'  操作级别:{syn.get("grade","?")} 建议:{action}')
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
        print('[错误] 找不到 cycle_report.json，请先运行 run_cycle.py --save')
        sys.exit(1)

    universe = load_universe_codes()
    if not universe:
        print('[错误] universe 为空，请先运行 volume_leader_screener.py --sync-universe')
        sys.exit(1)

    # 过滤到 volume leader 标的
    vl_items = [r for r in cycle if r.get('code') in universe]
    if not vl_items:
        print('[信息] cycle_report.json 中没有 volume leader 标的，请先运行 update_volume_leaders.py + run_cycle.py --save')
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

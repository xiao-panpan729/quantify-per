# -*- coding: utf-8 -*-
"""
生成 cycle_engine v3.0 的可读 .md 报告 — v4 补全版
v4 改进:
  1. 每日跟踪验证表: 自动读取昨日建议 → 对比今日涨跌 → 填充实际结果+次日关注
  2. 回撤可视化: 文本柱状图 + 风险等级标注
  3. 全量12只: 无信号标的也列出持仓状态
"""
import json
import sys
import os
import re
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import NAME_MAP

DATA = 'signals/tracking/cycle_report.json'
data = json.load(open(DATA, 'r', encoding='utf-8'))

# ════════════════ 分数历史 ════════════════
SCORE_HISTORY = 'signals/tracking/score_history.json'

def load_score_history():
    if os.path.exists(SCORE_HISTORY):
        try:
            return json.load(open(SCORE_HISTORY, 'r', encoding='utf-8'))
        except:
            return {}
    return {}

def save_score_history():
    """保存今日分数快照"""
    hist = {'date': date_str, 'scores': {}}
    for item in data:
        code = item['code']
        t = item['trend']
        hist['scores'][code] = {
            'score': t.get('score', 0),
            'direction': t.get('direction', 'unknown'),
            'name': item.get('name', ''),
            'expma_score': t.get('expma_score', 0),
            'macd_score': t.get('macd_score', 0),
            'ma_score': t.get('ma_score', 0),
            'cycle_score': t.get('cycle_score', 0),
        }
    # 保存最新快照
    json.dump(hist, open(SCORE_HISTORY, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

# ════════════════ 翻译函数 ════════════════

def level_label(score):
    if score >= 4.0: return '🔥🔥 加强闭环'
    elif score >= 3.0: return '🔥 普通闭环'
    elif score >= 2.0: return '⚡ 弱信号'
    else: return '-- 无出击信号'

def period_cn(p):
    M = {'daily':'日线','min60':'60分钟','min30':'30分钟','min15':'15分钟','min5':'5分钟','week':'周线'}
    return M.get(p, p)

def zone_cn(z):
    M = {'high':'高位','mid':'中位','low':'低位','unknown':'未知'}
    return M.get(z, z)

def trend_cn(t):
    M = {'bullish':'上涨','bullish_bias':'偏多','bearish':'下跌','bearish_bias':'偏空',
         'oscillating':'震荡','neutral':'中性','unknown':'未知'}
    return M.get(t, t)

def advice_cn(a):
    M = {'加仓追击':'加仓追击','顺势做多':'顺势做多','持有(可轻仓跟)':'持有(可轻仓跟)','持有/减仓':'持有/减仓',
         '高抛低吸':'高抛低吸','小仓做T':'小仓做T','观望':'观望','等待':'等待','不参与':'不参与'}
    return M.get(a, a)

def price_color_str(change_pct):
    if change_pct is None: return ''
    if isinstance(change_pct, str):
        try: change_pct = float(change_pct.replace('%',''))
        except: return change_pct
    s = ('%+.2f%%' % change_pct)
    return s

def dd_risk_tag(dd):
    """回撤风险标签"""
    if isinstance(dd, (int,float)):
        if dd <= -25: return '🔴极危'
        elif dd <= -18: return '🟠高危'
        elif dd <= -10: return '🟡注意'
        elif dd <= -5: return '🟢可控'
        else: return '✅安全'
    return '-'

def dd_bar(dd_val, max_dd=35):
    """回撤文本柱状图"""
    try:
        d = abs(dd_val)
        if d < 0.1: return ''
        width = min(int(d / max_dd * 20), 20)
        if dd_val <= -18: ch = '█'
        elif dd_val <= -10: ch = '▓'
        elif dd_val <= -5: ch = '▒'
        else: ch = '░'
        return '%s%.1f%%' % (ch * width, abs(dd_val))
    except:
        return ''

GRADE_ORDER = ['observe_strong', 'actionable', 'resonant_strong', 'neutral_strong', 'neutral_bias', 'neutral', 'neutral_weak', 'observe', 'avoid']
GRADE_INFO = {
    'observe_strong': ('🟠 强势追踪', '多头趋势+高位加速区,持减仓等回调'),
    'actionable':   ('🔴 可操作',   '日线上涨 + 分钟闭环确认'),
    'resonant_strong':('🟡🟡 共振偏强', '中性 + 分钟密集 + 跨周期金叉共振 → 偏多'),
    'neutral_strong': ('🟡 中性偏强', '日线横盘 + 分钟信号密集'),
    'neutral_bias':  ('🟡 中性偏强', '日线横盘 + 分钟有信号'),
    'neutral':       ('🟢 中性',     '日线横盘 + 分钟信号一般'),
    'neutral_weak':  ('🟢 中性偏弱', '日线横盘 + 分钟无信号'),
    'observe':       ('⚪ 关注',     '等待转折确认'),
    'avoid':         ('⚪ 观望',     '弱势建议回避'),
}

# ════════════════ 解析昨日报告（提取每只标的的建议和收盘价）════════════════

def parse_yesterday_advice(yesterday_date_str):
    """
    从昨日的 v3 报告中解析出每只标的的: 操作建议 + 理由
    返回 {code: {'action': str, 'reason': str}}
    """
    y_path = os.path.join('reports/daily', '%s_v3.md' % yesterday_date_str)
    result = {}
    if not os.path.exists(y_path):
        return result

    text = open(y_path, 'r', encoding='utf-8').read()
    # 匹配新格式: | code name price | trend | summary | action |
    pattern = r'\|\s*(sz\w+|sh\w+)\s+\S+?\s*\|\s*[^|]+\s*\|\s*[^|]+\s*\|\s*([^|]+?)\s*\|'
    for m in re.finditer(pattern, text):
        code = m.group(1).strip()
        action = m.group(2).strip()
        reason = ''
        if '(' in action and action.endswith(')'):
            idx = action.index('(')
            reason = action[idx+1:-1].strip()[:50]
            action = action[:idx].strip()
        result[code] = {'action': action, 'reason': reason}
    # 如果新格式没匹配到，尝试旧格式（管道符被小时段摘要破坏的老格式）
    if not result:
        for line in text.split('\n'):
            line = line.strip()
            if not line.startswith('| sz') and not line.startswith('| sh'):
                continue
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) < 4:
                continue
            code_m = re.match(r'(sz\w+|sh\w+)', parts[0])
            if not code_m:
                continue
            code = code_m.group(1)
            action = parts[-1]  # 最后一个是操作建议
            reason = ''
            if '(' in action and action.endswith(')'):
                idx = action.index('(')
                reason = action[idx+1:-1].strip()[:50]
                action = action[:idx].strip()
            result[code] = {'action': action, 'reason': reason}
    return result


def get_yesterday_close_from_report(yesterday_date_str, code):
    """从昨日报告中提取该标的的收盘价"""
    y_path = os.path.join('reports/daily', '%s_v3.md' % yesterday_date_str)
    if not os.path.exists(y_path): return None
    text = open(y_path, 'r', encoding='utf-8').read()
    # 匹配 ### code name x.xxx [+x.xx%]
    pattern = r'%s\s+\S+?\s+([\d.]+)' % code.replace('.', r'\.')
    m = re.search(pattern, text)
    if m:
        try: return float(m.group(1))
        except: pass
    return None

# ════════════════ 按grade分组 ════════════════

by_grade = {}
for item in data:
    g = item.get('advice', {}).get('grade', 'neutral')
    by_grade.setdefault(g, []).append(item)

lines = []

def table_rows(items):
    rows = []
    for item in items:
        c = item['code']; n = item.get('name', ''); t = item['trend']
        adv = item.get('advice', {}); p = item.get('position', {})
        close = p.get('close', '?'); change = p.get('change_pct', None)
        trend_dir = trend_cn(t.get('direction', '?')); action = adv.get('action', '?')
        summary = adv.get('min_signal_summary', '?')
        wc = adv.get('wait_condition', '')
        close_str = ('%.3f' % close) if isinstance(close, (int,float)) else str(close)
        if change is not None: close_str += ' ' + price_color_str(change)
        stock_cell = '%s %s %s' % (c, n[:6], close_str)
        # 替换 summary 中的 | 为 ·，避免破坏 markdown 表格
        summary = summary.replace('|', '·')
        if wc:
            summary += ' · 等: ' + wc

        # 主导量级
        dc = adv.get('dominant_cycle', {})
        if dc and dc.get('dominant_cycle'):
            dc_label = dc['dominant_label']
            stretched = dc.get('stretched_periods', [])
            if stretched:
                ignore = ','.join(p.replace('min','') for p in stretched)
                dc_str = f'{dc_label} (忽略{ignore}反向)'
            else:
                dc_str = dc_label
        else:
            dc_str = '-'

        rows.append('| %s | %s | %s | %s | %s |' % (stock_cell, trend_dir, summary, dc_str, advice_cn(action)))
    return rows

# ════════════════ 正文开始 ════════════════
now = datetime.now()
date_str = now.strftime('%Y%m%d')
time_str = now.strftime('%Y-%m-%d %H:%M')
yesterday_str = (now - timedelta(days=1)).strftime('%Y%m%d')
# 如果当天不是交易日（周末/假日），往前找最近的有报告的交易日
if not os.path.exists(os.path.join('reports/daily', '%s_v3.md' % yesterday_str)):
    reports = sorted([f.replace('_v3.md','') for f in os.listdir('reports/daily') if f.endswith('_v3.md')], reverse=True)
    if reports:
        yesterday_str = reports[0]

lines.append('# 周期循环分析报告 (Cycle Engine v3.0)')
lines.append('')
lines.append('**生成时间**: %s' % time_str)
lines.append('**框架**: 信号质量递进（★买密集度 + 金叉跟随速度 + 底部价格方向 + 闭环完整性）')
lines.append('**数据源**: cycle_engine.py 实时计算（非快照）')
lines.append('')
lines.append('---')
lines.append('')

# ── 一、标的跟踪总览 ──
lines.append('## 一、标的跟踪总览')
lines.append('')
total = len(data)
lines.append('> 共 %d 只标的，按 日线趋势 + 分钟信号强度 分级。' % total)
lines.append('')

for gk in GRADE_ORDER:
    grp = by_grade.get(gk, [])
    if not grp: continue
    title, desc = GRADE_INFO.get(gk, ('', ''))
    lines.append('### %s (%d 只) — %s' % (title, len(grp), desc))
    lines.append('')
    lines.append('| 标的 收盘 | 日线趋势 | 分钟闭环 | 主导量级 | 操作建议 |')
    lines.append('|----------|----------|----------|----------|----------|')
    for r in table_rows(grp):
        lines.append(r)
    lines.append('')

lines.append('---')
lines.append('')

# ── 分数起伏对比 ──
lines.append('### 分数起伏（今日 vs 昨日）')
lines.append('')
history = load_score_history()
prev_scores = history.get('scores', {})
prev_date = history.get('date', '无')
lines.append('> 对比 %s → 今日 %s，跟踪趋势评分变化' % (prev_date[:4] if prev_date != '无' else '--', date_str[:4]))
lines.append('')
lines.append('| 标的 | 昨日总分 | 今日总分 | 变动 | 方向变化 |')
lines.append('|------|---------|---------|------|---------|')
for item in data:
    code = item['code']
    name = item.get('name', '')[:6]
    t = item['trend']
    today_score = t.get('score', 0)
    today_dir = t.get('direction', '')
    prev = prev_scores.get(code, {})
    prev_score = prev.get('score', '新')
    prev_dir = prev.get('direction', '')
    if prev_score != '新':
        diff = today_score - prev_score
        diff_s = ('%+d' % diff) if diff != 0 else '0'
        if diff > 0: diff_s = '🔺' + diff_s
        elif diff < 0: diff_s = '🔻' + diff_s
        else: diff_s = '➖' + diff_s
        dir_changed = '→' if prev_dir == today_dir else ('%s→%s' % (trend_cn(prev_dir)[:2], trend_cn(today_dir)[:2]))
        lines.append('| `%s` %s | %s | %d | %s | %s |' % (code, name, str(prev_score) if isinstance(prev_score, int) else prev_score, today_score, diff_s, dir_changed))
    else:
        lines.append('| `%s` %s | (首日) | %d | - | %s |' % (code, name, today_score, trend_cn(today_dir)))
lines.append('')

lines.append('---')
lines.append('')
lines.append('## 二、重点标的深度分析')
lines.append('')

for item in data:
    c = item['code']; n = item.get('name', ''); p = item['position']; t = item['trend']
    adv = item.get('advice', {})
    best_p = item.get('best_period', '')
    if isinstance(best_p, dict): best_p = best_p.get('period', '')
    lv = item.get('best_signal_level', 0)
    if isinstance(lv, str): lv = float(lv)

    zone = p.get('zone', '?'); risk = p.get('risk_level', '?')
    close = p.get('close', '?'); change = p.get('change_pct', None)
    expma12 = p.get('expma12', '?'); expma50 = p.get('expma50', '?')
    dev_w = p.get('deviation_white_pct', '?'); dev_y = p.get('deviation_yellow_pct', '?')
    trend_dir = trend_cn(t.get('direction', '?')); trend_conf = t.get('confidence', '?')
    trend_detail = t.get('details', t.get('detail', ''))
    action = adv.get('action', '?'); reason = adv.get('reason', ''); conf = adv.get('confidence', '?')

    periods = item.get('period_results', {})
    if isinstance(periods, dict): period_detail = periods
    elif isinstance(periods, list): period_detail = {p_['period']: p_ for p_ in periods}
    else: period_detail = {}

    close_str = ('%s %.3f' % (n, close)) if isinstance(close, (int,float)) else n
    if change is not None: close_str += '  %s' % price_color_str(change)
    close_str += '  [%s %s]' % (zone_cn(zone), level_label(lv))
    lines.append('### %s %s' % (c, close_str))
    lines.append('')

    trend_summary = '%s (置信度%s%%)' % (trend_dir, trend_conf)
    if isinstance(trend_detail, str) and trend_detail:
        trend_summary += ' | ' + trend_detail[:80]
    lines.append('- **趋势**: %s' % trend_summary)
    lines.append('  - 位置: EXPMA12=%s, EXPMA50=%s, 偏离%s%% / %s%%  |  最佳周期: %s  |  建议: **%s** (置信度:%s)' %
        (expma12, expma50, dev_w, dev_y, period_cn(best_p) if best_p else '-', advice_cn(action), conf))
    if reason: lines.append('  - %s' % reason)

    for ptype in ['min5','min15','min30','min60','daily']:
        if ptype in period_detail:
            pd_ = period_detail[ptype]
            sig_str = pd_.get('signal_label', '--')
            price_line = pd_.get('price_line', '')
            timestamps = pd_.get('signals_timeline', pd_.get('timestamps', []))
            ts_str = ''
            if timestamps and isinstance(timestamps, list) and len(timestamps) > 0:
                ts_str = ' | ' + ' '.join(timestamps[:3])
            if price_line:
                lines.append('- **%s**: %s | 价格有效性: %s%s' % (period_cn(ptype), sig_str, price_line, ts_str))
            else:
                lines.append('- **%s**: %s%s' % (period_cn(ptype), sig_str, ts_str))

    lines.append('')
    lines.append('---')
    lines.append('')

# ════════════════ 三、每日跟踪验证（核心改进） ════════════════
lines.append('## 三、每日跟踪验证')
lines.append('')

# 解析昨日数据
yest_advices = parse_yesterday_advice(yesterday_str)

lines.append('> 昨日(%s)建议 vs 今日(%s)实际表现 — 自动对比验证' % (yesterday_str[-4:], date_str[-4:]))
lines.append('')
lines.append('| 标的 | 昨日建议 | 今日收盘 | 涨跌 | 实际结果 | 验证 | 次日关注 |')
lines.append('|------|--------|--------|------|--------|------|--------|')

verify_hits = 0; verify_total = 0

for item in data:
    code = item['code']
    name = item.get('name', '')[:6]
    p = item.get('position', {})
    today_close = p.get('close', None)
    today_change = p.get('change_pct', None)

    # 今日建议
    adv = item.get('advice', {})
    today_action = adv.get('action', '?')
    today_reason = adv.get('reason', '')
    wc_adv = adv.get('wait_condition', '')

    # 昨日建议
    yest = yest_advices.get(code, {})
    yest_action = yest.get('action', '无记录')
    yest_reason = yest.get('reason', '')

    verify_total += 1

    # 计算实际结果
    yest_close = get_yesterday_close_from_report(yesterday_str, code)
    today_close_val = float(today_close) if isinstance(today_close, (int,float)) else None

    # 涨跌幅计算（优先用change_pct，没有则用昨日收盘对比）
    if today_change is not None:
        chg = today_change; chg_s = price_color_str(today_change)
    elif yest_close is not None and today_close_val is not None:
        chg = (today_close_val - yest_close) / yest_close * 100
        chg_s = '%+.2f%%' % chg
    else:
        chg = None; chg_s = '-'

    if today_close_val is not None and chg is not None:
        actual = ''; verify_mark = ''
        if yest_action == '无记录':
            actual = '(首日)'
            verify_mark = '-'
        elif chg >= 1.5 and yest_action in ('顺势做多', '加仓追击'):
            actual = '✅ 盈利 +%s%%' % ('%.1f' % abs(chg))
            verify_mark = '命中'; verify_hits += 1
        elif chg >= 0.3 and yest_action in ('高抛低吸', '高抛低吸/偏多', '持有(可轻仓跟)'):
            actual = '✅ 小盈 +%s%%' % ('%.1f' % abs(chg))
            verify_mark = '命中'; verify_hits += 1
        elif chg >= -0.5 and yest_action in ('观望', '等待', '持有/减仓'):
            actual = '⚪ 横盘 %s' % chg_s
            verify_mark = '中性'
        elif chg <= -1.5 and yest_action in ('观望', '等待', '不参与', '关注抄底'):
            actual = '✅ 回避正确 %s' % chg_s
            verify_mark = '命中'; verify_hits += 1
        elif chg <= -1.5:
            actual = '❌ 亏损 %s' % chg_s
            verify_mark = '失误'
        elif abs(chg) < 1.5:
            actual = '➖ 波动 %s' % chg_s
            verify_mark = '待观察'
        else:
            actual = '➖ %s' % chg_s
            verify_mark = '待观察'

        # 次日关注（今日建议）
        next_focus = today_action
        if wc_adv:
            next_focus += ' (%s)' % wc_adv[:25]
        elif today_reason:
            next_focus += ' (%s)' % today_reason[:25]

        lines.append('| `%s` %s | %s | %.3f | %s | %s | **%s** | %s |' %
                      (code, name, yest_action[:8], float(today_close) if isinstance(today_close,(int,float)) else 0,
                       chg_s or '-', actual, verify_mark, next_focus[:30]))
    else:
        lines.append('| `%s` %s | %s | ? | ? | (无价格数据) | - | %s |' %
                      (code, name, yest_action[:8] if yest_action else '-', today_action[:30]))

lines.append('')
if verify_total > 0:
    lines.append('> 命中率: **%d/%d (%.0f%%)**' % (verify_hits, verify_total, verify_hits/max(verify_total,1)*100))
lines.append('')
lines.append('---')
lines.append('')

# ════════════════ 四、回测统计 + 回撤风险分析 ════════════════
lines.append('## 四、回测统计与回撤风险')
lines.append('')
lines.append('> ★信号法：每个原始★信号向后合并到低点被破为止，单独计算胜率（低点不破=结构延续）。')
lines.append('> 50%回调法作为对比参考。根据标的趋势方向智能展示：上涨/偏多→重点看★买 | 下跌/偏空→重点看★卖 | 中性→平衡展示')
lines.append('> **注**: 全部12只标的均展示，无新信号的标注"近期无★信号"。')
lines.append('')

bt_path = 'signals/tracking/backtest_report.json'
bt_data = {}
if os.path.exists(bt_path):
    bt_data = json.load(open(bt_path, 'r', encoding='utf-8'))

# ── 4a. 回撤风险总览（文本柱状图）──
dd_all = []  # 收集所有回撤值用于全局排序
for item in data:
    code = item['code']
    if code not in bt_data: continue
    bt = bt_data[code]
    for period in ['min5','min15','min30','min60','daily']:
        if period not in bt: continue
        pdata = bt[period]
        for sig_type in ['buy', 'sell']:
            per = pdata.get('per_signal', {}).get(sig_type, {})
            if per and per.get('count', 0) > 0:
                dd = per.get('avg_retreat', 0)
                avg_p = per.get('avg_pct', 0)
                wr = per.get('win_rate', 0)
                cnt = per.get('count', 0)
                dd_all.append({
                    'code': code, 'period': period, 'sig_type': sig_type,
                    'dd': dd, 'avg_pct': avg_p, 'wr': wr, 'cnt': cnt,
                })

# 按回撤绝对值降序排列（最大的风险排前面）
dd_all.sort(key=lambda x: abs(x['dd']), reverse=True)

lines.append('### 4.1 回撤风险排名 TOP-10')
lines.append('')
lines.append('| 排名 | 标的 | 周期 | 方向 | 均利润 | **均回撤** | 胜率 | 笔数 | 风险 | 回撤分布 |')
lines.append('|:---:|------|------|------|-------:|--------:|-----:|----:|:----:|--------|')

for rank, d in enumerate(dd_all[:10], 1):
    sig_label = '★买→卖' if d['sig_type'] == 'buy' else '★卖→买'
    bar = dd_bar(d['dd'])
    tag = dd_risk_tag(d['dd'])
    name = NAME_MAP.get(d['code'], '')[:6]
    lines.append('| %d | `%s` %s | %s | %s | %+5.1f%% | **%6.1f%%** | %3.0f%% | %3d | %s | %s |' %
                  (rank, d['code'], name, period_cn(d['period']), sig_label,
                   d['avg_pct'], d['dd'], d['wr'], d['cnt'], tag, bar))

lines.append('')

# ── 4b. 各标的详细回测表 ──
lines.append('### 4.2 各标的全周期回测明细')
lines.append('')

for item in data:
    code = item['code']
    name = item.get('name', '')[:6]
    trend_dir = item.get('trend', {}).get('direction', 'neutral')
    adv = item.get('advice', {}); action = adv.get('action', '?')
    reason = adv.get('reason', ''); p = item.get('position', {})
    close = p.get('close', '?')
    best_p = item.get('best_period', '')
    if isinstance(best_p, dict): best_p = best_p.get('period', '')
    expma_status = p.get('expma_status', '?') or '?'
    macd_status = p.get('macd_status', '?') or '?'

    lines.append(f'\n#### `{code}` {name} — 趋势:{trend_cn(trend_dir)} | 建议:**{advice_cn(action)}**')

    if code in bt_data:
        bt = bt_data[code]
        lines.append('')
        lines.append('| 周期 | 信号 | 次数 | 胜率 | 均涨跌 | 最大 | 最差 | **回撤** | 持仓 | 均合段 | 50%对比 |')
        lines.append('|------|------|------|------|-------|------|------|:------:|------|--------|--------|')

        show_buy = trend_dir in ('bullish', 'bullish_bias', 'neutral')
        show_sell = trend_dir in ('bearish', 'bearish_bias', 'neutral')
        has_any_row = False

        for period in ['min5','min15','min30','min60','daily']:
            if period not in bt: continue
            pdata = bt[period]
            for sig_type, label in [('buy','★买'), ('sell','★卖')]:
                show = (sig_type == 'buy' and show_buy) or (sig_type == 'sell' and show_sell)
                if not show: continue
                per = pdata.get('per_signal', {}).get(sig_type, {})
                m50 = pdata.get('merge_50', {}).get(sig_type, {})
                if not per or per.get('count', 0) == 0: continue

                has_any_row = True
                per_cnt = per['count']; per_wr = per['win_rate']
                per_avg = per['avg_pct']; per_mx = per['max_pct']
                per_mn = per['min_pct']; per_rt = per['avg_retreat']
                per_br = per['avg_bars']; per_raw = per.get('raw_cycles', per_cnt)
                per_merge = per.get('avg_merged_signals', 1)

                m50_wr = m50.get('win_rate', '-') if m50 else '-'
                m50_avg = m50.get('avg_pct', '-') if m50 else '-'
                m50_str = f'{m50_wr}%/{m50_avg:+.1f}%' if isinstance(m50_wr, (int,float)) else '-'
                dd_tag = dd_risk_tag(per_rt)

                lines.append('| %s | %s→%s | %d(%d次) | %.0f%% | %+.1f%% | %.1f%% | %.1f%% | **%.1f%%** %s | %.0f根 | 合%.1f段 | %s |' %
                    (period_cn(period), label, '★卖' if sig_type=='buy' else '★买',
                     per_cnt, per_raw, per_wr, per_avg, per_mx, per_mn, per_rt, dd_tag, per_br, per_merge, m50_str))

        if not has_any_row:
            lines.append('| *(近期无新★信号)* | | | | | | | | | | |')

        lines.append('')
    else:
        cs = ('%.3f' % close) if isinstance(close, (int,float)) else close

        # 主导量级展示
        dc = adv.get('dominant_cycle')
        if dc and dc.get('dominant_cycle'):
            dc_label = dc['dominant_label']
            dc_detail = dc.get('detail', '')
            stretched = dc.get('stretched_periods', [])
            dc_parts = [f'**{dc_label}**']
            if stretched:
                ignore_str = ','.join(p.replace('min','') for p in stretched)
                dc_parts.append(f'忽略{ignore_str}级反向信号')
            if dc_detail:
                dc_parts.append(f'({dc_detail})')
            lines.append(f'\n> **主导量级**: {" | ".join(dc_parts)}')

        lines.append(f'\n> **当前状态**: EXPMA={expma_status} | MACD={macd_status} | 收盘={cs} | 最佳={period_cn(best_p) if best_p else "-"} | {reason or advice_cn(action)}')
        lines.append('')

# ════════════════ 写入文件 ════════════════
report = '\n'.join(lines)
out_path = 'reports/daily/%s_v3.md' % date_str
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(report)
save_score_history()
print('已生成: ' + out_path)
import webbrowser
webbrowser.open(os.path.abspath(out_path))

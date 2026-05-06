# -*- coding: utf-8 -*-
"""生成 cycle_engine v3.0 的可读 .md 报告 — v2 优化版"""
import json
import sys
import os
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA = 'signals/tracking/cycle_report.json'
data = json.load(open(DATA, 'r', encoding='utf-8'))

# ───── 翻译 ─────
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
    M = {'bullish':'上涨','bullish_bias':'偏多','bearish':'下跌','bearish_bias':'偏空','oscillating':'震荡','neutral':'中性','unknown':'未知'}
    return M.get(t, t)

def advice_cn(a):
    M = {'加仓追击':'加仓追击','顺势做多':'顺势做多','持有(可轻仓跟)':'持有(可轻仓跟)','持有/减仓':'持有/减仓',
         '高抛低吸':'高抛低吸','小仓做T':'小仓做T','观望':'观望','等待':'等待','不参与':'不参与'}
    return M.get(a, a)

def price_color_str(change_pct):
    """收盘涨跌幅带颜色标记"""
    if change_pct is None: return ''
    if isinstance(change_pct, str):
        try: change_pct = float(change_pct.replace('%',''))
        except: return change_pct
    s = ('%+.2f%%' % change_pct)
    return s

# ───── 分组 ─────
strong, weak, none = [], [], []
for item in data:
    lv = item.get('best_signal_level', 0)
    if isinstance(lv, str): lv = float(lv)
    if lv >= 3.0: strong.append(item)
    elif lv >= 2.0: weak.append(item)
    else: none.append(item)

lines = []

# ───── 表头 ─────
def table_rows(items, show_signal=True):
    rows = []
    for item in items:
        c = item['code']
        n = item.get('name', '')
        t = item['trend']
        adv = item.get('advice', {})
        p = item.get('position', {})
        best_p = item.get('best_period', '')
        lv = item.get('best_signal_level', 0)
        if isinstance(lv, str): lv = float(lv)
        action = adv.get('action', '?')
        trend_dir = trend_cn(t.get('direction', '?'))
        close = p.get('close', '?')
        change = p.get('change_pct', None)
        # 收盘价 + 涨跌幅
        close_str = ('%.3f' % close) if isinstance(close, (int,float)) else str(close)
        if change is not None:
            close_str += ' ' + price_color_str(change)
        # 趋势+最佳周期
        trend_cell = '%s（%s）' % (trend_dir, period_cn(best_p)) if best_p else trend_dir
        # 标的列合并收盘价
        stock_cell = '%s %s %s' % (c, n[:6], close_str)
        if show_signal:
            sig = '%s (%.1f)' % (level_label(lv), lv)
            rows.append('| %s | %s | %s | %s' % (stock_cell, trend_cell, sig, advice_cn(action)))
        else:
            rows.append('| %s | %s | %s' % (stock_cell, trend_cell, advice_cn(action)))
    return rows

# ══════════════════════ 正文 ══════════════════════
lines.append('# 周期循环分析报告 (Cycle Engine v3.0)')
lines.append('')
lines.append('**生成时间**: 2026-05-06 ~17:30')
lines.append('**框架**: 信号质量递进（★买密集度 + 金叉跟随速度 + 底部价格方向 + 闭环完整性）')
lines.append('**数据源**: cycle_engine.py 实时计算（非快照）')
lines.append('')
lines.append('---')
lines.append('')

# 一、总览
lines.append('## 一、标的跟踪总览')
lines.append('')

# 摘要
s_sum = '大盘环境上涨趋势，%d只出现加强闭环信号' % len(strong) if len(strong) > 0 else '暂无加强闭环'
lines.append('> %s。%d只接近闭环，%d只观望。' % (s_sum, len(weak), len(none)))
lines.append('')

lines.append('### 🔴 可操作 (信号级别 ≥ 3.0)')
lines.append('')
lines.append('| 标的 收盘 | 日线趋势+最佳周期 | 信号结论 | 操作建议 |')
lines.append('|----------|------------------|----------|----------|')
for r in table_rows(strong):
    lines.append(r)

lines.append('')
lines.append('### 🟡 接近闭环 (信号级别 2.0~2.9)')
lines.append('')
lines.append('| 标的 收盘 | 日线趋势+最佳周期 | 信号结论 | 操作建议 |')
lines.append('|----------|------------------|----------|----------|')
for r in table_rows(weak):
    lines.append(r)

lines.append('')
lines.append('### ⚪ 观望 (信号级别 < 2.0)')
lines.append('')
lines.append('| 标的 收盘 | 日线趋势+最佳周期 | 操作建议 |')
lines.append('|----------|------------------|----------|')
for r in table_rows(none, show_signal=False):
    lines.append(r)

lines.append('')
lines.append('---')
lines.append('')

# 二、深度分析
lines.append('## 二、重点标的深度分析')
lines.append('')

for item in data:
    c = item['code']
    n = item.get('name', '')
    p = item['position']
    t = item['trend']
    adv = item.get('advice', {})
    best_p = item.get('best_period', '')
    lv = item.get('best_signal_level', 0)
    if isinstance(lv, str): lv = float(lv)

    zone = p.get('zone', '?')
    risk = p.get('risk_level', '?')
    close = p.get('close', '?')
    change = p.get('change_pct', None)
    expma12 = p.get('expma12', '?')
    expma50 = p.get('expma50', '?')
    dev_w = p.get('deviation_white_pct', '?')
    dev_y = p.get('deviation_yellow_pct', '?')

    trend_dir = trend_cn(t.get('direction', '?'))
    trend_conf = t.get('confidence', '?')
    trend_detail = t.get('details', t.get('detail', ''))

    action = adv.get('action', '?')
    reason = adv.get('reason', '')
    conf = adv.get('confidence', '?')

    # 分周期明细
    periods = item.get('period_results', {})
    if isinstance(periods, dict): period_detail = periods
    elif isinstance(periods, list): period_detail = {p['period']: p for p in periods}
    else: period_detail = {}

    # 标题 + 收盘价+涨跌幅
    close_str = ('%s %.3f' % (n, close)) if isinstance(close, (int,float)) else n
    if change is not None:
        close_str += '  %s' % price_color_str(change)
    close_str += '  [%s %s]' % (zone_cn(zone), level_label(lv))
    lines.append('### %s %s' % (c, close_str))
    lines.append('')

    # 一行为主的信息行
    trend_summary = '%s (置信度%s%%)' % (trend_dir, trend_conf)
    if isinstance(trend_detail, str) and trend_detail:
        trend_summary += ' | ' + trend_detail[:80]
    lines.append('- **趋势**: %s' % trend_summary)
    lines.append('  - 位置: EXPMA12=%s, EXPMA50=%s, 偏离%s%% / %s%%  |  最佳周期: %s  |  建议: **%s** (置信度:%s)' %
        (expma12, expma50, dev_w, dev_y, period_cn(best_p), advice_cn(action), conf))
    if reason:
        lines.append('  - %s' % reason)
    lines.append('')

    # 各周期明细
    for ptype in ['min5','min15','min30','min60','daily']:
        if ptype in period_detail:
            pd = period_detail[ptype]
            sig_str = pd.get('signal_label', '--')
            price_line = pd.get('price_line', '')
            # 信号时间戳
            timestamps = pd.get('signals_timeline', pd.get('timestamps', []))
            ts_str = ''
            if timestamps and isinstance(timestamps, list) and len(timestamps) > 0:
                ts_str = ' | ' + ' '.join(timestamps[:3])  # 最多列3个
            if price_line:
                lines.append('- **%s**: %s | 价格有效性: %s%s' % (period_cn(ptype), sig_str, price_line, ts_str))
            else:
                lines.append('- **%s**: %s%s' % (period_cn(ptype), sig_str, ts_str))

    lines.append('')
    lines.append('---')
    lines.append('')

lines.append('## 三、关键差异（旧版 vs 新版）')
lines.append('')
lines.append('| 对比维度 | 旧版 (CCI+分时出击) | 新版 (Cycle Engine v3.0) |')
lines.append('|----------|---------------------|-------------------------|')
lines.append('| 信号判断 | CCI左侧信号判定 | ★买密集度+金叉速度+底部抬升+闭环完整性 |')
lines.append('| 趋势判断 | 日线MACD+EXPMA | 多层校验（温度计+置信度+近期涨跌幅） |')
lines.append('| 位置判断 | 仅价格区段 | 偏离白线/黄线百分比定量 |')
lines.append('| 操作建议 | 通用模板 | 信号级别+趋势方向双控 |')
lines.append('| 涨跌幅 | 未标注 | 每个标的收盘+涨跌幅 |')
lines.append('')

report = '\n'.join(lines)
out_path = 'reports/daily/20260506_v3.md'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(report)
print('已生成: ' + out_path)

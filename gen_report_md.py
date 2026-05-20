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

DATA_PATH = 'signals/tracking/cycle_report.json'
HHT_PATH = 'signals/tracking/hht_report.json'
SYNTH_PATH = 'signals/tracking/synthesized_report.json'

_data_cache = None
_hht_cache = None
_synth_cache = None

def _load_hht():
    if os.path.exists(HHT_PATH):
        try:
            raw = json.load(open(HHT_PATH, 'r', encoding='utf-8'))
            return {r['code']: r for r in raw}
        except:
            pass
    return {}

def _load_synth():
    if os.path.exists(SYNTH_PATH):
        try:
            return json.load(open(SYNTH_PATH, 'r', encoding='utf-8'))
        except:
            pass
    return {}

def _ensure_data():
    global _data_cache, _hht_cache, _synth_cache
    if _data_cache is None:
        _data_cache = json.load(open(DATA_PATH, 'r', encoding='utf-8'))
    if _hht_cache is None:
        _hht_cache = _load_hht()
    if _synth_cache is None:
        _synth_cache = _load_synth()
    return _data_cache, _hht_cache

def _get_data():
    _ensure_data()
    return _data_cache

def _get_hht():
    _ensure_data()
    return _hht_cache

def _get_synth():
    _ensure_data()
    return _synth_cache

def _hht_summary(code):
    """取标的最重要的HHT状态，返回紧凑标签（含方向）"""
    h = _get_hht().get(code)
    if not h:
        return '-'
    periods = h.get('periods', {})
    for pkey in ['daily', 'min60', 'min30', 'min15', 'min5']:
        pd = periods.get(pkey, {})
        s = pd.get('summary', {})
        if not s:
            continue
        sl = s.get('stability_label', '')
        # 从标签中提取方向（↑/↓）
        direction = ''
        if sl.startswith(('↑', '↓')):
            direction = sl[0]
        if '循环破位' in sl:
            return '⚠%s循环破位' % direction
        if '突破' in sl:
            return '⚡%s能量爆发' % direction
        if '压缩' in sl:
            return '🔒蓄力'
        if '动能增强' in sl:
            return '📈%s动能' % direction
        if '频率散乱' in sl:
            return '⇄方向切换'
    dp = periods.get('daily', {})
    ds = dp.get('summary', {})
    if ds: return '✓正常'
    return '-'

# ════════════════ 分数历史 ════════════════
SCORE_HISTORY = 'signals/tracking/score_history.json'

def load_score_history():
    """返回最近两次有数据的日期: {prev_date, prev_scores}, 如果只有一次数据则 prev_scores 为空"""
    if os.path.exists(SCORE_HISTORY):
        try:
            raw = json.load(open(SCORE_HISTORY, 'r', encoding='utf-8'))
            entries = raw.get('history', [])
            entries.sort(key=lambda e: e['date'])
            if len(entries) >= 2:
                return {'date': entries[-1]['date'], 'scores': entries[-2]['scores']}
            elif len(entries) == 1:
                return {'date': entries[0]['date'], 'scores': {}}
        except:
            pass
    return {'date': '无', 'scores': {}}

def save_score_history(date_str):
    """追加今日分数快照（保留历史用于跨日对比）"""
    raw = {}
    if os.path.exists(SCORE_HISTORY):
        try:
            raw = json.load(open(SCORE_HISTORY, 'r', encoding='utf-8'))
        except:
            pass
    entries = raw.get('history', [])
    # 如果今天已经有记录，更新；否则追加
    today_entry = {'date': date_str, 'scores': {}}
    for item in _get_data():
        code = item['code']
        t = item['trend']
        today_entry['scores'][code] = {
            'score': t.get('score', 0),
            'direction': t.get('direction', 'unknown'),
            'name': item.get('name', ''),
            'expma_score': t.get('expma_score', 0),
            'macd_score': t.get('macd_score', 0),
            'ma_score': t.get('ma_score', 0),
            'cycle_score': t.get('cycle_score', 0),
        }
    # 替换同日记录或追加
    replaced = False
    for i, e in enumerate(entries):
        if e['date'] == date_str:
            entries[i] = today_entry
            replaced = True
            break
    if not replaced:
        entries.append(today_entry)
    raw['history'] = entries
    json.dump(raw, open(SCORE_HISTORY, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

# ════════════════ 翻译函数 ════════════════

def level_label(score):
    if score >= 6.0: return '🔥🔥 加强出击'
    elif score >= 4.0: return '🔥 出击信号'
    elif score >= 3.0: return '🔥 加强信号'
    elif score >= 2.0: return '⚡ 普通信号'
    elif score >= 1.0: return '⚡ 信号弱'
    else: return '-- 无信号'

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

def _fmt_dominant_note(dominant_info, trend_dir):
    """主导量级文字：返回 (label, note)。
    label: '15分钟' 等, note: '小级卖信号暂不采信' 等, 无主导时返回 ('', '')"""
    if not dominant_info or not dominant_info.get('dominant_cycle'):
        return '', ''
    label = dominant_info['dominant_label']
    stretched = dominant_info.get('stretched_periods', [])
    if not stretched:
        return label, ''
    if trend_dir in ('bullish', 'bullish_bias'):
        note = '小级卖信号暂不采信'
    elif trend_dir in ('bearish', 'bearish_bias', 'bearish'):
        note = '小级买信号暂不采信'
    else:
        note = '小级反向暂不采信'
    return label, note


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

def build_report_lines():
    # ════════════════ 按grade分组 ════════════════
    
    by_grade = {}
    for item in _get_data():
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
            summary = summary.replace('|', '→')
            if wc:
                summary += ' → ' + wc
    
            # 主导量级
            dc_label, dc_note = _fmt_dominant_note(adv.get('dominant_cycle', {}), t.get('direction', ''))
            if dc_label:
                dc_str = f'{dc_label}主导({dc_note})' if dc_note else dc_label
            else:
                dc_str = '-'
    
            # 排列熵总览：优先日线的pe_label，含完整状态机标签
            periods = item.get('periods', {})
            if isinstance(periods, dict):
                best_pe = '➖-'
                for pe_check_p in ['daily', 'min60', 'min30', 'min15', 'min5']:
                    pp = periods.get(pe_check_p, {})
                    if not pp: continue
                    sq = pp.get('signal_quality', {}) or {}
                    pe = sq.get('trend_pe', None) if isinstance(sq, dict) else None
                    if not pe: pe = pp.get('trend_pe')
                    if pe and isinstance(pe, dict):
                        # 优先显示异常状态：结构溃散/逆向崩退
                        phase = pe.get('pe_phase', '')
                        if phase:
                            best_pe = phase
                            break
                        # 否则用简标签
                        label = pe.get('pe_label', '')
                        if label:
                            best_pe = label
                            break
            else:
                best_pe = '-'
    
            hht_tag = _hht_summary(c)
            # 合成等级（A+/A-/A假/B/C/D）
            synth = _get_synth().get(c, {})
            synth_grade = synth.get('grade', '')
            if synth_grade:
                trend_dir = '%s %s' % (trend_dir, synth_grade)
            rows.append('| %s | %s | %s | %s | %s | %s | %s |' % (stock_cell, trend_dir, summary, best_pe, hht_tag, dc_str, advice_cn(action)))
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

    hht_data = _get_hht()  # HHT 分析数据，供报告各段使用
    
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
    total = len(_get_data())
    lines.append('> 共 %d 只标的，按 日线趋势 + 分钟信号强度 分级。' % total)
    lines.append('')
    
    for gk in GRADE_ORDER:
        grp = by_grade.get(gk, [])
        if not grp: continue
        title, desc = GRADE_INFO.get(gk, ('', ''))
        lines.append('### %s (%d 只) — %s' % (title, len(grp), desc))
        lines.append('')
        lines.append('| 标的 收盘 | 日线趋势 | 分钟闭环 | 结构状态 | HHT | 主导量级 | 操作建议 |')
        lines.append('|----------|----------|----------|--------|-----|----------|----------|')
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
    lines.append('> 对比 %s → 今日 %s，跟踪趋势评分变化' % (prev_date[-4:] if prev_date != '无' else '--', date_str[-4:]))
    lines.append('')
    lines.append('| 标的 | 昨日总分 | 今日总分 | 变动 | 方向变化 |')
    lines.append('|------|---------|---------|------|---------|')
    for item in _get_data():
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
    
    def _synthesize(item):
        """生成一句话总结：位置+趋势+结构+能量 → 操作"""
        p = item.get('position', {})
        t = item.get('trend', {})
        adv = item.get('advice', {})
        periods = item.get('periods', {})
        if isinstance(periods, list): periods = {}
    
        zone = zone_cn(p.get('zone', '?'))
        dev_y = p.get('deviation_yellow_pct', 0)
        trend_dir = trend_cn(t.get('direction', '?'))
        action = adv.get('action', '?')
    
        # 最重要的结构信号
        best_phase = ''
        for pk in ['daily', 'min60', 'min30', 'min15', 'min5']:
            pp = periods.get(pk, {}) if isinstance(periods, dict) else {}
            sq = pp.get('signal_quality', {}) or {}
            pe = sq.get('trend_pe') or pp.get('trend_pe', {})
            phase = pe.get('pe_phase', '') if isinstance(pe, dict) else ''
            if '突破' in phase or '压缩' in phase or '强化' in phase:
                best_phase = f'{period_cn(pk)}{phase}'
                break
        if not best_phase:
            for pk in ['daily', 'min60', 'min30', 'min15', 'min5']:
                pp = periods.get(pk, {}) if isinstance(periods, dict) else {}
                sq = pp.get('signal_quality', {}) or {}
                pe = sq.get('trend_pe') or pp.get('trend_pe', {})
                phase = pe.get('pe_phase', '') if isinstance(pe, dict) else ''
                if phase:
                    best_phase = f'{period_cn(pk)}{phase}'
                    break
    
        # 最重要的HHT状态（带方向）
        hht_str = ''
        h = hht_data.get(item['code'])
        if h:
            for pk in ['daily', 'min60', 'min30', 'min15', 'min5']:
                hpd = h.get('periods', {}).get(pk, {})
                hs = hpd.get('summary', {})
                sl = hs.get('stability_label', '')
                # 从标签提取方向
                direction = ''
                if sl.startswith(('↑', '↓')):
                    direction = sl[0]
                if '循环破位' in sl:
                    hht_str = f'{direction}{period_cn(pk)}循环破位'
                    break
                if '突破' in sl:
                    hht_str = f'{direction}{period_cn(pk)}能量爆发'
                    break
    
        parts = [f'价格{zone}(偏离{dev_y}%)', f'趋势{trend_dir}']
        if best_phase: parts.append(best_phase)
        if hht_str: parts.append(hht_str)
        parts.append(f'→ {action}')
        return '，'.join(parts)
    
    for item in _get_data():
        c = item['code']; n = item.get('name', ''); p = item['position']; t = item['trend']
        adv = item.get('advice', {})
        best_p = item.get('best_period', '')
        if isinstance(best_p, dict): best_p = best_p.get('period', '')
        lv = item.get('best_signal_level', 0)
        if isinstance(lv, str): lv = float(lv)
    
        zone = p.get('zone', '?')
        close = p.get('close', '?'); change = p.get('change_pct', None)
        expma12 = p.get('expma12', '?'); expma50 = p.get('expma50', '?')
        dev_w = p.get('deviation_white_pct', '?'); dev_y = p.get('deviation_yellow_pct', '?')
        trend_dir = trend_cn(t.get('direction', '?'))
        trend_detail = t.get('details', [])
        trend_score = t.get('score', 0)
        action = adv.get('action', '?'); conf = adv.get('confidence', '?')
    
        periods = item.get('periods', {})
        if isinstance(periods, dict): period_detail = periods
        elif isinstance(periods, list): period_detail = {p_['period']: p_ for p_ in periods}
        else: period_detail = {}
    
        close_str = ('%s %.3f' % (n, close)) if isinstance(close, (int,float)) else n
        if change is not None: close_str += '  %s' % price_color_str(change)
        lines.append('### %s %s  [%s %s]' % (c, close_str, zone_cn(zone), level_label(lv)))
        lines.append('')
    
        # ── 一句话总结 ──
        lines.append('> %s' % _synthesize(item))
        lines.append('')

        # ── 三层评估（synthesize_report 输出）──
        synth = _get_synth().get(c)
        if synth:
            grade = synth.get('grade', '?')
            action = synth.get('action', '?')
            struct_s = synth.get('structure_status', '?')
            momentum_s = synth.get('momentum_status', '?')
            sig_summary = synth.get('signal_summary', '')
            lines.append('**三层**: %s | 结构:%s | 动能:%s | %s | → **%s**' % (
                grade, struct_s, momentum_s, sig_summary, action))
            lines.append('')
    
        # ── 趋势（含评分+明细） ──
        trend_parts = ['%s %d/16' % (trend_dir, trend_score)]
        if isinstance(trend_detail, list) and trend_detail:
            trend_parts.append(' | ' + ' '.join(trend_detail))
        lines.append('- **趋势**: %s' % ''.join(trend_parts))
    
        # ── 位置 ──
        pos_parts = ['EXPMA12=%s EXPMA50=%s' % (expma12, expma50)]
        pos_parts.append('偏离+%s%%/+%s%%' % (dev_w, dev_y))
        pos_parts.append('最佳周期:%s' % period_cn(best_p) if best_p else '-')
        lines.append('- **位置**: %s' % ' | '.join(pos_parts))
    
        # ── 结构（排列熵各周期一行） ──
        pe_parts = []
        for pe_p in ['daily','min60','min30','min15','min5']:
            pp = period_detail.get(pe_p, {})
            sq = pp.get('signal_quality', {}) or {}
            pe = sq.get('trend_pe') or pp.get('trend_pe', {})
            if isinstance(pe, dict) and pe.get('pe_phase'):
                pe_parts.append('%s %s' % (period_cn(pe_p), pe['pe_phase']))
        if pe_parts:
            lines.append('- **结构**: %s' % ' | '.join(pe_parts))
    
        # ── HHT（各周期一行，含 fs/er 数值和方向） ──
        h = hht_data.get(c)
        if h:
            hht_parts = []
            for hp in ['daily','min60','min30','min15','min5']:
                hpd = h.get('periods', {}).get(hp, {})
                hs = hpd.get('summary', {})
                sl = hs.get('stability_label', '')
                if sl:
                    fs = hs.get('freq_stability', '')
                    er = hs.get('energy_ratio', '')
                    fb = hs.get('false_breakout')
                    fb_tag = ''
                    if fb is True:
                        fb_tag = ' ⚠假突破'
                    elif fb is False:
                        fb_tag = ' ✓有效突破'
                    hht_parts.append('%s %s(fs=%.2f,er=%.2f)%s' % (
                        period_cn(hp), sl,
                        fs if isinstance(fs, (int, float)) else 1.0,
                        er if isinstance(er, (int, float)) else 1.0,
                        fb_tag))
            if hht_parts:
                lines.append('- **HHT**: %s' % ' | '.join(hht_parts))
    
        # ── 信号（各周期信号质量一行） ──
        sig_parts = []
        for sp in ['daily','min60','min30','min15','min5']:
            pp = period_detail.get(sp, {})
            sq = pp.get('signal_quality', {}) or {}
            label = sq.get('label', '--') if isinstance(sq, dict) else '--'
            sig_parts.append('%s %s' % (period_cn(sp), label))
        lines.append('- **信号**: %s' % ' | '.join(sig_parts))
    
        # ── 主导量级 + 建议 ──
        dc_label, dc_note = _fmt_dominant_note(adv.get('dominant_cycle'), t.get('direction', ''))
        if dc_label:
            dc_str = f'{dc_label}主导({dc_note})' if dc_note else dc_label
        else:
            dc_str = ''
        lines.append('- **主导**: %s | **建议**: %s (置信度:%s)' % (dc_str or '-', advice_cn(action), conf))
    
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
    
    for item in _get_data():
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
    
    # ════════════════ 四、战役状态追踪 ════════════════
    OP_PATH = 'signals/tracking/operation_records.json'
    if os.path.exists(OP_PATH):
        try:
            op_data = json.load(open(OP_PATH, 'r', encoding='utf-8'))
            active_camps = [c for c in op_data.get('campaigns', []) if c['status'] == 'active']
            if active_camps:
                data_map = {item['code']: item for item in _get_data()}
                lines.append('## 四、战役状态追踪')
                lines.append('')
                lines.append('> 当前活跃战役 — 开仓→事件→平仓闭环跟踪')
                lines.append('')
    
                for camp in active_camps:
                    code = camp['code']
                    name = camp.get('name', '')[:8]
                    item = data_map.get(code)
                    d = camp.get('open', {})
                    direction = '多' if camp['direction'] == 'long' else '空'
                    lines.append('### %s — %s | %s | 开仓%s' % (code, direction, period_cn(d.get('period', '?')), d.get('date', '')[-4:]))
                    lines.append('')
                    lines.append('| 项目 | 内容 |')
                    lines.append('|------|------|')
                    lines.append('| 标的 | %s %s |' % (code, camp.get('name', '')))
                    lines.append('| 方向 | %s |' % direction)
                    lines.append('| 开仓级别 | %s |' % period_cn(d.get('period', '?')))
                    lines.append('| 开仓信号 | %s |' % d.get('signal', '?'))
                    lines.append('| 开仓价格 | %.3f |' % d.get('price', 0))
                    lines.append('| 开仓理由 | %s |' % d.get('reason', ''))
    
                    if item:
                        p = item.get('position', {})
                        close = p.get('close', 0)
                        open_price = d.get('price', 0)
                        if close and open_price:
                            pnl = (close - open_price) / open_price * 100
                            lines.append('| 当前盈亏 | %.3f (%+.1f%%) |' % (close, pnl))
                        adv = item.get('advice', {})
                        action = adv.get('action', '')
                        lines.append('| 今日建议 | %s |' % (advice_cn(action) if action else '-'))
                        wc = adv.get('wait_condition', '')
                        if wc:
                            lines.append('| 等待条件 | %s |' % wc)
    
                    events = camp.get('events', [])
                    if events:
                        lines.append('')
                        lines.append('**事件记录:**')
                        lines.append('')
                        lines.append('| 日期 | 事件 | 级别 | 价格 | 比例 | 理由 |')
                        lines.append('|------|------|------|------|:----:|------|')
                        for ev in events:
                            ev_type = {'reduce': '减仓', 'add': '加仓', 'adjust': '调整'}.get(ev.get('type', ''), ev.get('type', ''))
                            lines.append('| %s | %s | %s | %.3f | %s | %s |' %
                                (ev.get('date', '?'), ev_type, period_cn(ev.get('period', '?')),
                                 ev.get('price', 0), ev.get('pct_change', '?'), ev.get('reason', '')))
                    lines.append('')
    
                # 已结束战役摘要
                closed_camps = [c for c in op_data.get('campaigns', []) if c['status'] == 'closed']
                if closed_camps:
                    lines.append('---')
                    lines.append('')
                    win = sum(1 for c in closed_camps if (c.get('stats') or {}).get('total_pct', 0) > 0)
                    total = len(closed_camps)
                    lines.append('已结束战役: 共 %d 个，胜 %d 负 %d，胜率 %d%%' % (total, win, total - win, win / max(total, 1) * 100))
                    lines.append('')
                    for camp in closed_camps:
                        s = camp.get('stats', {}) or {}
                        lines.append('- `%s` %s | %.1f%% | %d天 | 回撤%.1f%%' % (
                            camp['code'], camp.get('name', '')[:6],
                            s.get('total_pct', 0), s.get('duration_days', 0), s.get('max_drawdown', 0)))
                    lines.append('')
    
                lines.append('---')
                lines.append('')
        except Exception:
            pass  # 安静失败，不阻塞报告生成
    
    # ════════════════ 五、回测统计 + 回撤风险分析 ════════════════
    lines.append('## 五、回测统计与回撤风险')
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
    for item in _get_data():
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
    
    lines.append('### 5.1 回撤风险排名 TOP-10')
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
    lines.append('### 5.2 各标的全周期回测明细')
    lines.append('')
    
    for item in _get_data():
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
                    dc_parts.append(f'小级反向暂不采信')
                if dc_detail:
                    dc_parts.append(f'({dc_detail})')
                lines.append(f'\n> **主导量级**: {" | ".join(dc_parts)}')
    
            lines.append(f'\n> **当前状态**: EXPMA={expma_status} | MACD={macd_status} | 收盘={cs} | 最佳={period_cn(best_p) if best_p else "-"} | {reason or advice_cn(action)}')
            lines.append('')

    return lines, date_str

def append_params_reference(lines):
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 📊 参数参考')
    lines.append('')
    lines.append('### HHT 循环状态')
    lines.append('')
    lines.append('| 条件 | 标签 | 含义 |')
    lines.append('|------|------|------|')
    lines.append('| `er>2.0 + fs<0.7` | ↑↓突破(能量暴增+循环锁定) | 最理想突破：放量+结构锁定 |')
    lines.append('| `er>2.0` | ↑↓突破(能量暴增) | 单纯放量，结构未必锁定 |')
    lines.append('| `fs>1.8` | ↑↓循环破位 | 频率比历史大1.8倍，节奏已乱 |')
    lines.append('| `fs<0.6` | 循环压缩(蓄力) | 频率收窄，蓄势待发 |')
    lines.append('| `fs>1.5` | ↑↓频率散乱(方向切换) | 方向切换中 |')
    lines.append('| `er>1.5` | ↑↓动能增强 | 温和放量 |')
    lines.append('| `er<0.5` | 动能枯竭 | 缩量衰竭 |')
    lines.append('')
    lines.append('> `fs`=频率稳定性：`<0.6`蓄力 → `0.6~1.5`正常 → `>1.5`散乱 → `>1.8`循环破位')
    lines.append('> `er`=能量比：`<0.5`枯竭 → `0.5~1.5`正常 → `>1.5`增强 → `>2.0`暴增')
    lines.append('')
    lines.append('### 0-16 趋势评分')
    lines.append('')
    lines.append('| 维度 | 分值 | 逻辑 |')
    lines.append('|------|:----:|------|')
    lines.append('| EXPMA | 0~2 | e12>e50=2，粘合=1，空头=0 |')
    lines.append('| MACD | 0~4 | 0轴+金叉死叉，强势>0.01% |')
    lines.append('| MA排列 | 0~6 | 5→10→20→60→120→250链式递进，断链即停 |')
    lines.append('| 日线闭环 | 0~4 | buy_level≥4→4分，≥3.5→3分 |')
    lines.append('')
    lines.append('> **方向**: `13~16`上涨 | `10~12`偏多 | `7~9`中性 | `4~6`偏空 | `0~3`下跌')
    lines.append('')
    lines.append('### 主导量级')
    lines.append('')
    lines.append('通过波峰间距检测主导循环周期。小级别信号与主导量级反向 → 「小级暂不采信」')
    lines.append('')
    lines.append('### 结构状态（排列熵）')
    lines.append('')
    lines.append('> ⬇降熵 | 方向形成中 → 结构上破/结构下破 → 顺向蓄力 → 蓄力压缩/趋势锁定 → 趋势延续')
    lines.append('> ⬆升熵 | 趋势松动 → 趋势衰减 → 无序放大（结构溃散过程）')
    lines.append('> ➖平稳 | 无序震荡 / 方向不明')
    lines.append('')
    lines.append('### ABCD 级别')
    lines.append('')
    lines.append('| 等级 | 条件 | 最小操作 |')
    lines.append('|:----:|------|:--------:|')
    lines.append('| A | EXPMA白线上方 | 5分钟一信号 |')
    lines.append('| B | 白线~黄线之间 | 5分钟★买+2次金叉 |')
    lines.append('| C | 黄线下但MACD>0 | 15分钟★买+2次金叉 |')
    lines.append('| D | MACD<0或死叉 | 不参与，等大级别底部 |')
    lines.append('')
    lines.append('### 信号质量递进（买侧 5 维）')
    lines.append('')
    lines.append('> ★买密集(+0.5~1.5) → 金叉跟随(+0.3~1.5) → 底部抬升(+1.0) → 闭环成对(+0.3~1.0) → MA5/10金叉(+0.3~1.2)')
    lines.append('')
    lines.append('### CCI 闭环')
    lines.append('')
    lines.append('> 极值(≤-200/≥+200) → 背驰(看面积非高度) → ★买/★卖 → EXPMA金叉/死叉确认')
    lines.append('')


# ═══════════════════════════════════════
# 增量数据保存 — 积累型 analysis_history.json
# ═══════════════════════════════════════
ANALYSIS_HISTORY = 'signals/tracking/analysis_history.json'

def save_analysis_history(data, date_str):
    """将当日完整分析快照增量追加到 analysis_history.json

    结构: { records: [{ date, update_time, stocks: { code: { name, score, trend, hht, ... } } }] }
    """
    import json
    # 加载已有历史
    history = {'records': []}
    if os.path.exists(ANALYSIS_HISTORY):
        try:
            with open(ANALYSIS_HISTORY, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except:
            history = {'records': []}

    # 检查该日期是否已存在记录（避免重复追加）
    for rec in history.get('records', []):
        if rec.get('date') == date_str:
            print('[分析历史] %s 已存在，跳过' % date_str)
            return

    # 构建当日快照
    snapshot = {}
    for item in _get_data():
        code = item['code']
        hht = _get_hht().get(code, {})
        code_hht = {}
        for pk in ['daily','min60','min30','min15','min5']:
            hpd = hht.get('periods', {}).get(pk, {})
            hs = hpd.get('summary', {})
            if hs:
                code_hht[pk] = {
                    'fs': hs.get('freq_stability'),
                    'er': hs.get('energy_ratio'),
                    'label': hs.get('stability_label'),
                    'dir': hs.get('trend_dir', ''),
                }

        snapshot[code] = {
            'name': item.get('name', ''),
            'score': item.get('trend', {}).get('score'),
            'direction': item.get('trend', {}).get('direction'),
            'position': item.get('position', {}).get('zone'),
            'dominant_level': item.get('best_period'),
            'advice': item.get('advice', {}).get('action'),
            'hht': code_hht,
        }

    record = {
        'date': date_str,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'stocks': snapshot,
    }

    if 'records' not in history:
        history['records'] = []
    history['records'].append(record)

    os.makedirs(os.path.dirname(ANALYSIS_HISTORY), exist_ok=True)
    with open(ANALYSIS_HISTORY, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print('[分析历史] 追加完成: 共 %d 条记录' % len(history['records']))

if __name__ == "__main__":
    lines, date_str = build_report_lines()
    append_params_reference(lines)
    report = '\n'.join(lines)
    out_path = 'reports/daily/%s_v3.md' % date_str
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    save_score_history(date_str)
    save_analysis_history(_get_data(), date_str)
    print('已生成: ' + out_path)
    import webbrowser
    webbrowser.open(os.path.abspath(out_path))

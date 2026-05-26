# -*- coding: utf-8 -*-
"""
生成单标的 HTML 战役跟踪报告 — ECharts K线图 + 信号标注 + 趋势分析

用法:
    python gen_campaign_html.py                     # 默认 sz159740
    python gen_campaign_html.py --code sh513120     # 其他标的
    python gen_campaign_html.py --code sz159740 --days 90   # 只看近90天

输出: reports/campaign/{code}_{date}.html
"""

import json
import csv
import os
import sys
import argparse
from datetime import datetime

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SIGNALS_DIR = os.path.join(BASE_DIR, 'signals', 'tracking')

PERIODS = ['daily', 'min60', 'min30', 'min15', 'min5']
PERIOD_NAMES = {'daily': '日线', 'min60': '60分钟', 'min30': '30分钟', 'min15': '15分钟', 'min5': '5分钟'}
PRICE_FACTOR = {'daily': 1, 'min60': 10000, 'min30': 10000, 'min15': 10000, 'min5': 10000}
# 每周期最多嵌入 K 线根数
MAX_BARS = {'daily': 250, 'min60': 300, 'min30': 300, 'min15': 300, 'min5': 300}
# dataZoom 初始显示窗口 (≤ MAX_BARS)
DEFAULT_WINDOW = {'daily': 120, 'min60': 150, 'min30': 200, 'min15': 250, 'min5': 250}

SIGNAL_FIELDS = ['buy_signal', 'sell_signal', 'expma_cross', 'cci_extreme', 'cci_divergence', 'red_line_cross']

# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def safe_float(v, default=0.0):
    try:
        return float(v) if v != '' and v is not None else default
    except (ValueError, TypeError):
        return default


def fmt_price(v, period='daily'):
    """格式化价格显示"""
    if period == 'daily':
        return f'{v:.3f}'
    return f'{v / PRICE_FACTOR[period]:.3f}'


# ═══════════════════════════════════════════════
# 数据读取
# ═══════════════════════════════════════════════

def read_signal_csv(code, period):
    """读取信号 CSV，返回 (rows, is_valid)
    rows: list of dict, 每个 dict 包含所有列
    is_valid: bool, False 表示数据损坏
    """
    path = os.path.join(SIGNALS_DIR, code, f'{period}_signals.csv')
    if not os.path.exists(path):
        return [], False

    rows = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except Exception:
        return [], False

    if not rows:
        return [], False

    # 数据有效性校验: 检查 timestamp 是否为合法日期
    ts = rows[-1].get('timestamp', '')
    try:
        if period == 'daily':
            datetime.strptime(ts[:8], '%Y%m%d')
        else:
            if len(ts) >= 12:
                datetime.strptime(ts[:12], '%Y%m%d%H%M')
            elif len(ts) == 8:
                datetime.strptime(ts, '%Y%m%d')
            else:
                return rows, False
    except ValueError:
        return rows, False

    return rows, True


def read_cycle_report(code):
    """读取 cycle_report.json 中指定 code 的条目"""
    path = os.path.join(SIGNALS_DIR, 'cycle_report.json')
    if not os.path.exists(path):
        return None
    try:
        data = json.load(open(path, 'r', encoding='utf-8'))
        for item in data:
            if item.get('code') == code:
                return item
    except Exception:
        pass
    return None


def read_op_records(code):
    """读取 operation_records.json 中指定 code 的战役记录"""
    path = os.path.join(SIGNALS_DIR, 'operation_records.json')
    if not os.path.exists(path):
        return []
    try:
        data = json.load(open(path, 'r', encoding='utf-8'))
        campaigns = data.get('campaigns', [])
        return [c for c in campaigns if c.get('code') == code]
    except Exception:
        return []


# ═══════════════════════════════════════════════
# 数据处理
# ═══════════════════════════════════════════════

def process_period_data(rows, period, max_bars=None):
    """将原始 CSV rows 转换为 ECharts 可用的数据结构"""
    factor = PRICE_FACTOR[period]
    n = len(rows)
    if max_bars and n > max_bars:
        rows = rows[-max_bars:]
        n = len(rows)

    dates = []
    ohlc = []        # [open, close, low, high] — ECharts candlestick 格式
    expma12 = []
    expma50 = []
    ma5 = []
    ma10 = []
    ma20 = []
    macd_dif = []
    macd_dea = []
    macd_hist = []
    cci = []
    volumes = []
    vol_ma5 = []

    # 信号标记
    buy_signals = []      # {date, price, idx}
    sell_signals = []
    golden_cross = []     # EXPMA 金叉
    death_cross = []      # EXPMA 死叉
    cci_top = []          # CCI+200
    cci_bottom = []       # CCI-200
    cci_div_top = []      # 顶背驰
    cci_div_bot = []      # 底背驰
    red_break = []        # 突破红线
    red_fall = []         # 跌破红线

    for i, row in enumerate(rows):
        # 日期
        ts = row.get('timestamp', '')
        if period == 'daily':
            date_str = ts[:4] + '-' + ts[4:6] + '-' + ts[6:8]
        else:
            date_str = ts[:4] + '-' + ts[4:6] + '-' + ts[6:8] + ' ' + ts[8:10] + ':' + ts[10:12]
        dates.append(date_str)

        # OHLC (转换为实际价格)
        o = safe_float(row.get('open', 0)) / factor
        c = safe_float(row.get('close', 0)) / factor
        h = safe_float(row.get('high', 0)) / factor
        l = safe_float(row.get('low', 0)) / factor
        ohlc.append([round(o, 4), round(c, 4), round(l, 4), round(h, 4)])

        # 均线
        expma12.append(round(safe_float(row.get('expma12', 0)) / factor, 4))
        expma50.append(round(safe_float(row.get('expma50', 0)) / factor, 4))
        ma5.append(round(safe_float(row.get('ma5', 0)) / factor, 4))
        ma10.append(round(safe_float(row.get('ma10', 0)) / factor, 4))
        ma20.append(round(safe_float(row.get('ma20', 0)) / factor, 4))

        # MACD
        macd_dif.append(round(safe_float(row.get('macd_dif', 0)), 6))
        macd_dea.append(round(safe_float(row.get('macd_dea', 0)), 6))
        macd_hist.append(round(safe_float(row.get('macd_hist', 0)), 6))

        # CCI
        cci.append(safe_float(row.get('cci', 0)))

        # 成交量
        vol = safe_float(row.get('volume', 0))
        volumes.append(vol)
        vol_ma5.append(safe_float(row.get('vol_ma5', 0)))

        # 信号
        price_for_signal = c  # 用收盘价作为信号标记价格
        signal_info = {'date': date_str, 'price': round(price_for_signal, 4), 'idx': i}

        bs = row.get('buy_signal', '').strip()
        if bs == '★买':
            buy_signals.append(signal_info.copy())

        ss = row.get('sell_signal', '').strip()
        if ss == '★卖':
            sell_signals.append(signal_info.copy())

        ec = row.get('expma_cross', '').strip()
        if ec == '金叉':
            golden_cross.append(signal_info.copy())
        elif ec == '死叉':
            death_cross.append(signal_info.copy())

        ce = row.get('cci_extreme', '').strip()
        if '200' in ce and '不' not in ce:
            if ce.startswith('CCI-') or ce.startswith('-'):
                cci_bottom.append(signal_info.copy())
            else:
                cci_top.append(signal_info.copy())

        cd = row.get('cci_divergence', '').strip()
        if cd == '顶背驰':
            cci_div_top.append(signal_info.copy())
        elif cd == '底背驰':
            cci_div_bot.append(signal_info.copy())

        rlc = row.get('red_line_cross', '').strip()
        if rlc == '突破红线':
            red_break.append(signal_info.copy())
        elif rlc == '跌破红线':
            red_fall.append(signal_info.copy())

    return {
        'dates': dates,
        'ohlc': ohlc,
        'expma12': expma12,
        'expma50': expma50,
        'ma5': ma5,
        'ma10': ma10,
        'ma20': ma20,
        'macd_dif': macd_dif,
        'macd_dea': macd_dea,
        'macd_hist': macd_hist,
        'cci': cci,
        'volumes': volumes,
        'vol_ma5': vol_ma5,
        'buy_signals': buy_signals,
        'sell_signals': sell_signals,
        'golden_cross': golden_cross,
        'death_cross': death_cross,
        'cci_top': cci_top,
        'cci_bottom': cci_bottom,
        'cci_div_top': cci_div_top,
        'cci_div_bot': cci_div_bot,
        'red_break': red_break,
        'red_fall': red_fall,
        'n_rows': len(rows),
        'is_valid': True,
    }


def process_all_data(code, max_bars_dict):
    """读取并处理所有周期数据"""
    result = {}
    for period in PERIODS:
        rows, is_valid = read_signal_csv(code, period)
        if is_valid and rows:
            max_bars = max_bars_dict.get(period) if max_bars_dict else None
            data = process_period_data(rows, period, max_bars)
            data['is_valid'] = True
            result[period] = data
        else:
            result[period] = {'is_valid': False, 'error': '数据损坏或缺失', 'n_rows': 0}
    return result


# ═══════════════════════════════════════════════
# HTML 生成
# ═══════════════════════════════════════════════

def js_json(obj):
    """将 Python 对象转为 JSON，处理特殊浮点值"""
    return json.dumps(obj, ensure_ascii=False, default=str)


def build_overview_html(item):
    """生成战役概览面板 HTML"""
    if not item:
        return '<div class="panel"><h2>⚠ 无 cycle_report 数据</h2></div>'

    t = item.get('trend', {})
    p = item.get('position', {})
    adv = item.get('advice', {})
    dc = adv.get('dominant_cycle', {}) if adv else {}
    rs = item.get('rs_density', {})
    mc = item.get('market_coeff', {})

    score = t.get('score', 0)
    zone_label = t.get('zone_label', '')
    direction = t.get('direction', '未知')
    direction_cn = t.get('label', direction)
    grade = adv.get('grade_label', '未知')
    action = adv.get('action', '未知')
    dominant = dc.get('dominant_label', '未知')
    dominant_detail = dc.get('detail', '')
    confidence = adv.get('confidence', '未知')

    # 趋势评分条颜色
    if score >= 13:
        score_color = '#ef4444'
    elif score >= 10:
        score_color = '#f97316'
    elif score >= 7:
        score_color = '#eab308'
    else:
        score_color = '#6b7280'

    return f'''
    <div class="overview-grid">
        <div class="ov-card">
            <div class="ov-label">趋势评分</div>
            <div class="ov-value" style="color:{score_color}">{score}<span class="ov-unit">/14</span></div>
            <div class="ov-sub">{direction_cn}</div>
            <div class="ov-zone" style="color:{score_color};font-size:11px">{zone_label}</div>
        </div>
        <div class="ov-card">
            <div class="ov-label">操作级别</div>
            <div class="ov-value grade">{grade}</div>
            <div class="ov-sub">{action}</div>
        </div>
        <div class="ov-card">
            <div class="ov-label">主导量级</div>
            <div class="ov-value">{dominant}</div>
            <div class="ov-sub">{dominant_detail[:40] if dominant_detail else ''}</div>
        </div>
        <div class="ov-card">
            <div class="ov-label">置信度</div>
            <div class="ov-value">{confidence}</div>
            <div class="ov-sub">最佳周期: {item.get('best_period', '?')}</div>
        </div>
        <div class="ov-card">
            <div class="ov-label">MACD</div>
            <div class="ov-value">{'多头' if t.get('macd_score', 0) > 1 else '空头'}</div>
            <div class="ov-sub">评分: {t.get('macd_score', 0)}</div>
        </div>
        <div class="ov-card">
            <div class="ov-label">位置区间</div>
            <div class="ov-value">{p.get('label', '?')}</div>
            <div class="ov-sub">偏离EXPMA12: {p.get('deviation_white_pct', 0):+.1f}%</div>
        </div>
    </div>
    <div class="advice-box">
        <strong>操作建议:</strong> {adv.get('reason', '无')}<br>
        <strong>等待条件:</strong> {adv.get('wait_condition', '无')}<br>
        <strong>最近支撑:</strong> {rs.get('nearest_support', {}).get('price', '?')}
        | <strong>最近阻力:</strong> {rs.get('nearest_resistance', {}).get('price', '?')}
        | <strong>大盘系数:</strong> {mc.get('coefficient', '?')} ({mc.get('label', '?')})
    </div>'''


def build_signal_matrix_html(item):
    """生成信号质量矩阵表格"""
    if not item:
        return ''
    periods_data = item.get('periods', {})
    best = item.get('best_period', '')

    rows_html = ''
    for pkey in ['daily', 'min60', 'min30', 'min15', 'min5']:
        pd = periods_data.get(pkey, {})
        sq = pd.get('signal_quality', {})
        if not sq:
            continue
        bl = sq.get('buy_level', 0)
        sl = sq.get('sell_level', 0)
        tp = sq.get('trend_pe', {})
        pe_phase = tp.get('pe_phase', '-')
        tl_dir = tp.get('tl_dir', '-')
        trending = '✓' if tp.get('trending') else '✗'
        label = sq.get('label', '-')
        is_best = '⭐' if pkey == best else ''
        rows_html += f'''
        <tr class="{'best-row' if is_best else ''}">
            <td>{is_best} {PERIOD_NAMES.get(pkey, pkey)}</td>
            <td class="num">{bl:.1f}</td>
            <td class="num">{sl:.1f}</td>
            <td>{pe_phase} {tl_dir}</td>
            <td>{trending}</td>
            <td>{label}</td>
        </tr>'''

    return f'''
    <div class="panel">
        <h3>📊 信号质量矩阵</h3>
        <table class="matrix-table">
            <thead><tr>
                <th>周期</th><th>买入级</th><th>卖出级</th><th>PE阶段</th><th>趋势</th><th>评级</th>
            </tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
        <div class="hint">⭐ 标记为最佳周期 | 排列熵 | 趋势=趋势线方向确认</div>
    </div>'''


def build_event_timeline_html(all_data):
    """从所有周期提取近期信号事件，生成时间轴"""
    events = []
    for period, data in all_data.items():
        if not data.get('is_valid'):
            continue
        pname = PERIOD_NAMES.get(period, period)
        for sig_list, label, icon in [
            (data.get('buy_signals', []), '★买', '🔴'),
            (data.get('sell_signals', []), '★卖', '🟢'),
            (data.get('golden_cross', []), '金叉', '🟡'),
            (data.get('death_cross', []), '死叉', '⚫'),
        ]:
            for s in sig_list:
                events.append({
                    'date': s['date'],
                    'period': pname,
                    'event': label,
                    'price': s['price'],
                    'icon': icon,
                })

    if not events:
        return '<div class="panel"><h3>📋 近期信号事件</h3><p>近期无信号事件</p></div>'

    # 反向排序，最新在前
    events.sort(key=lambda e: e['date'], reverse=True)
    events = events[:80]  # 最多显示 80 条

    rows = ''
    for e in events:
        rows += f'''
        <tr>
            <td>{e['date']}</td>
            <td>{e['period']}</td>
            <td>{e['icon']} {e['event']}</td>
            <td class="num">{e['price']:.3f}</td>
        </tr>'''

    return f'''
    <div class="panel">
        <h3>📋 近期信号事件 (近{len(events)}条)</h3>
        <div class="event-table-wrap">
        <table class="event-table">
            <thead><tr><th>时间</th><th>周期</th><th>事件</th><th>价格</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
        </div>
    </div>'''


def build_campaign_section_html(campaigns):
    """生成战役决策记录区域"""
    if not campaigns:
        return '''
    <div class="panel placeholder">
        <h3>⚔️ 战役决策记录</h3>
        <div class="empty-state">
            <p>暂无战役记录。待 <code>operation_tracker.py</code> 启动后，此处将展示:</p>
            <ul>
                <li>入场决策 — 日期/价格/级别/理由</li>
                <li>加仓/减仓 — 触发信号与量级</li>
                <li>离场决策 — 平仓理由与盈亏统计</li>
                <li>决策验证 — 后续价格走势验证正确/错误</li>
            </ul>
        </div>
    </div>'''
    # TODO: 有战役数据时的渲染
    return ''


def build_echarts_js(period, data, chart_id):
    """生成单个 ECharts 图表的 JavaScript 代码"""
    if not data.get('is_valid'):
        return f'''
        // {period} 数据不可用
        (function() {{
            var dom = document.getElementById('{chart_id}');
            if (dom) dom.innerHTML = '<div class="chart-error">⚠ {PERIOD_NAMES.get(period, period)} 数据损坏或缺失<br>请运行 <code>python update_tracking.py sz159740</code> 重新生成</div>';
        }})();
        '''

    dates = data['dates']
    n = len(dates)
    window = min(DEFAULT_WINDOW.get(period, 200), n)
    end_pct = 100
    start_pct = max(0, 100 - (window / n) * 100)

    # 信号 markPoint 数据
    def make_markpoints(signals, color, symbol='pin'):
        """生成 ECharts markPoint data"""
        points = []
        for s in signals:
            points.append({
                'name': s.get('event', ''),
                'coord': [s['date'], s['price']],
                'symbol': symbol,
                'symbolSize': 32 if symbol == 'pin' else 20,
                'itemStyle': {'color': color},
                'label': {'show': True, 'fontSize': 10, 'color': '#fff',
                          'textShadowColor': 'rgba(0,0,0,0.5)', 'textShadowBlur': 2},
            })
        return points

    buy_mp = make_markpoints(data['buy_signals'], '#ef4444')
    sell_mp = make_markpoints(data['sell_signals'], '#22c55e')
    golden_mp = make_markpoints(data['golden_cross'], '#f59e0b', 'roundRect')
    death_mp = make_markpoints(data['death_cross'], '#6b7280', 'roundRect')

    # 构建 JS 数据数组
    dates_js = js_json(dates)
    ohlc_js = js_json(data['ohlc'])
    expma12_js = js_json(data['expma12'])
    expma50_js = js_json(data['expma50'])
    ma5_js = js_json(data['ma5'])
    ma10_js = js_json(data['ma10'])
    ma20_js = js_json(data['ma20'])
    macd_dif_js = js_json(data['macd_dif'])
    macd_dea_js = js_json(data['macd_dea'])
    macd_hist_js = js_json(data['macd_hist'])
    cci_js = js_json(data['cci'])
    vol_js = js_json(data['volumes'])
    vol_ma5_js = js_json(data['vol_ma5'])
    buy_mp_js = js_json(buy_mp)
    sell_mp_js = js_json(sell_mp)
    golden_mp_js = js_json(golden_mp)
    death_mp_js = js_json(death_mp)

    return f'''
(function() {{
    var chart = echarts.init(document.getElementById('{chart_id}'), null, {{devicePixelRatio: 1}});
    var dates = {dates_js};
    var ohlc = {ohlc_js};
    var expma12 = {expma12_js};
    var expma50 = {expma50_js};
    var macd_dif = {macd_dif_js};
    var macd_dea = {macd_dea_js};
    var macd_hist = {macd_hist_js};
    var cci = {cci_js};
    var volumes = {vol_js};
    var vol_ma5 = {vol_ma5_js};

    var option = {{
        legend: {{
            data: ['K线', 'EXPMA12', 'EXPMA50', 'MACD', 'CCI', '成交量'],
            top: 0, left: 'center',
            textStyle: {{ color: '#a0aec0', fontSize: 10 }},
            selected: {{ '成交量': false }}
        }},
        tooltip: {{
            trigger: 'axis',
            axisPointer: {{ type: 'cross' }},
            backgroundColor: 'rgba(30,30,30,0.92)',
            borderColor: '#555',
            textStyle: {{ color: '#ddd', fontSize: 11 }}
        }},
        axisPointer: {{
            link: [{{ xAxisIndex: 'all' }}]
        }},
        grid: [
            {{ left: '8%', right: '3%', top: '6%', height: '44%' }},
            {{ left: '8%', right: '3%', top: '56%', height: '10%' }},
            {{ left: '8%', right: '3%', top: '70%', height: '9%' }},
            {{ left: '8%', right: '3%', top: '83%', height: '12%' }}
        ],
        xAxis: [
            {{ type: 'category', data: dates, gridIndex: 0, axisLabel: {{ show: false }}, axisTick: {{ show: false }} }},
            {{ type: 'category', data: dates, gridIndex: 1, axisLabel: {{ show: false }}, axisTick: {{ show: false }} }},
            {{ type: 'category', data: dates, gridIndex: 2, axisLabel: {{ show: false }}, axisTick: {{ show: false }} }},
            {{ type: 'category', data: dates, gridIndex: 3, axisLabel: {{ rotate: 30, fontSize: 10, formatter: function(v) {{ return v.length > 10 ? v.slice(5,10) : v.slice(5); }} }}, axisTick: {{ show: false }} }}
        ],
        yAxis: [
            {{ gridIndex: 0, scale: true, splitLine: {{ lineStyle: {{ color: '#333' }} }}, axisLabel: {{ fontSize: 10 }} }},
            {{ gridIndex: 1, scale: true, splitLine: {{ lineStyle: {{ color: '#333' }} }}, axisLabel: {{ fontSize: 9 }} }},
            {{ gridIndex: 2, scale: true, splitLine: {{ lineStyle: {{ color: '#333' }} }}, axisLabel: {{ fontSize: 9 }},
               splitLine: {{ show: true }},
               splitArea: {{ show: true, areaStyle: {{ color: ['rgba(0,0,0,0.02)', 'rgba(0,0,0,0.05)'] }} }}
            }},
            {{ gridIndex: 3, scale: true, splitLine: {{ lineStyle: {{ color: '#333' }} }}, axisLabel: {{ fontSize: 9 }} }}
        ],
        dataZoom: [
            {{ type: 'slider', xAxisIndex: [0,1,2,3], start: {start_pct}, end: {end_pct}, height: 18, bottom: 5,
               textStyle: {{ fontSize: 10 }} }},
            {{ type: 'inside', xAxisIndex: [0,1,2,3], start: {start_pct}, end: {end_pct} }}
        ],
        series: [
            // 0. K线
            {{
                name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0,
                data: ohlc,
                itemStyle: {{
                    color: '#dc2626', color0: '#16a34a',
                    borderColor: '#dc2626', borderColor0: '#16a34a',
                    borderWidth: 1
                }},
                markPoint: {{
                    symbol: 'pin', symbolSize: 28,
                    label: {{ show: true, fontSize: 9, color: '#fff', textShadowColor: 'rgba(0,0,0,0.6)', textShadowBlur: 2 }},
                    data: {buy_mp_js}.concat({sell_mp_js})
                }}
            }},
            // 1. EXPMA12
            {{ name: 'EXPMA12', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
               data: expma12, smooth: true, symbol: 'none',
               lineStyle: {{ color: '#ffffff', width: 2 }},
               emphasis: {{ focus: 'series' }} }},
            // 2. EXPMA50
            {{ name: 'EXPMA50', type: 'line', xAxisIndex: 0, yAxisIndex: 0,
               data: expma50, smooth: true, symbol: 'none',
               lineStyle: {{ color: '#f59e0b', width: 2 }},
               emphasis: {{ focus: 'series' }} }},
            // 5. MACD DIF (grid 1)
            {{ name: 'MACD DIF', type: 'line', xAxisIndex: 1, yAxisIndex: 1,
               data: macd_dif, symbol: 'none',
               lineStyle: {{ color: '#e2e8f0', width: 0.8 }} }},
            // 6. MACD DEA (grid 1)
            {{ name: 'MACD DEA', type: 'line', xAxisIndex: 1, yAxisIndex: 1,
               data: macd_dea, symbol: 'none',
               lineStyle: {{ color: '#f59e0b', width: 0.8 }} }},
            // 7. MACD Hist (grid 1)
            {{ name: 'MACD柱', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
               data: macd_hist,
               itemStyle: {{
                   color: function(p) {{ return p.data >= 0 ? 'rgba(239,68,68,0.7)' : 'rgba(34,197,94,0.7)'; }}
               }} }},
            // 8. CCI (grid 2)
            {{ name: 'CCI', type: 'line', xAxisIndex: 2, yAxisIndex: 2,
               data: cci, symbol: 'none',
               lineStyle: {{ color: '#e2e8f0', width: 0.8 }},
               markLine: {{
                   silent: true, symbol: 'none',
                   lineStyle: {{ color: '#f59e0b', type: 'dashed', width: 0.6 }},
                   data: [{{ yAxis: 200, label: {{ formatter: '+200', fontSize: 9, color: '#f59e0b' }} }},
                          {{ yAxis: -200, label: {{ formatter: '-200', fontSize: 9, color: '#f59e0b' }} }},
                          {{ yAxis: 100, label: {{ formatter: '+100', fontSize: 8, color: '#888' }} }},
                          {{ yAxis: -100, label: {{ formatter: '-100', fontSize: 8, color: '#888' }} }}]
               }},
               markArea: {{
                   silent: true,
                   data: [
                       [{{ yAxis: 100, itemStyle: {{ color: 'rgba(239,68,68,0.04)' }} }},
                        {{ yAxis: 200 }}],
                       [{{ yAxis: -200, itemStyle: {{ color: 'rgba(34,197,94,0.04)' }} }},
                        {{ yAxis: -100 }}]
                   ]
               }}
            }},
            // 9. Volume (grid 3)
            {{ name: '成交量', type: 'bar', xAxisIndex: 3, yAxisIndex: 3,
               data: volumes,
               itemStyle: {{
                   color: function(p) {{
                       if (p.dataIndex >= ohlc.length) return 'rgba(100,100,100,0.5)';
                       var item = ohlc[p.dataIndex];
                       return item[1] >= item[0] ? 'rgba(239,68,68,0.5)' : 'rgba(34,197,94,0.5)';
                   }}
               }} }},
            // 10. Vol MA5 (grid 3)
            {{ name: '量MA5', type: 'line', xAxisIndex: 3, yAxisIndex: 3,
               data: vol_ma5, symbol: 'none',
               lineStyle: {{ color: '#f59e0b', width: 0.8, opacity: 0.6 }} }}
        ]
    }};

    chart.setOption(option);
    window.addEventListener('resize', function() {{ chart.resize(); }});
    // 存储实例供 tab 切换时 resize
    window._charts = window._charts || {{}};
    window._charts['{chart_id}'] = chart;
}})();
'''


def build_html(code, all_data, item, campaigns, date_str):
    """组装完整 HTML 页面"""
    name = item.get('name', code) if item else code

    # 构建每个周期的图表 JS
    chart_scripts = ''
    tab_buttons = ''
    tab_contents = ''
    for i, period in enumerate(PERIODS):
        chart_id = f'chart_{period}'
        active = 'active' if i == 0 else ''
        pname = PERIOD_NAMES[period]
        data = all_data.get(period, {'is_valid': False, 'dates': []})
        n = data.get('n_rows', 0) if data else 0
        # 数据显示日期范围
        dates = data.get('dates', [])
        end_label = dates[-1][:10] if dates else '?'
        if period == 'min5' and end_label < '2026-05-15':
            end_label += ' ⚠'

        tab_buttons += f'<button class="tab-btn {active}" onclick="switchTab(\'{period}\')">{pname}<span class="tab-n">({n} 至{end_label})</span></button>\n'
        tab_contents += f'<div class="tab-content {active}" id="tab_{period}"><div id="{chart_id}" class="chart-container"></div></div>\n'
        chart_scripts += build_echarts_js(period, data, chart_id) + '\n'

    # 概览面板
    overview_html = build_overview_html(item)
    # 信号质量矩阵
    matrix_html = build_signal_matrix_html(item)
    # 事件时间轴
    timeline_html = build_event_timeline_html(all_data)
    # 战役决策
    campaign_html = build_campaign_section_html(campaigns)

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{code} {name} — 战役跟踪报告 {date_str}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    background: #1a1a2e;
    color: #e2e8f0;
    font-family: -apple-system, "Microsoft YaHei", sans-serif;
    line-height: 1.6;
    padding: 0;
}}
.header {{
    background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
    padding: 18px 28px;
    border-bottom: 2px solid #e94560;
    display: flex; justify-content: space-between; align-items: center;
}}
.header h1 {{ font-size: 1.3em; color: #fff; }}
.header .code {{ color: #a0aec0; font-size: 0.85em; }}
.header .date {{ color: #e94560; font-size: 0.9em; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 16px 20px; }}

/* 概览面板 */
.overview-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
    margin-bottom: 14px;
}}
.ov-card {{
    background: #16213e;
    border-radius: 8px;
    padding: 14px 16px;
    text-align: center;
    border: 1px solid #2d3748;
}}
.ov-label {{ font-size: 0.75em; color: #a0aec0; margin-bottom: 4px; }}
.ov-value {{ font-size: 1.6em; font-weight: 700; }}
.ov-value.grade {{ font-size: 1.2em; }}
.ov-unit {{ font-size: 0.5em; color: #a0aec0; }}
.ov-sub {{ font-size: 0.72em; color: #718096; margin-top: 2px; }}
.advice-box {{
    background: #16213e;
    border: 1px solid #e94560;
    border-radius: 8px;
    padding: 12px 18px;
    margin-bottom: 14px;
    font-size: 0.88em;
    line-height: 1.8;
}}
.advice-box strong {{ color: #f59e0b; }}

/* 面板 */
.panel {{
    background: #16213e;
    border: 1px solid #2d3748;
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 14px;
}}
.panel h3 {{ font-size: 1em; color: #f59e0b; margin-bottom: 10px; }}
.panel.placeholder {{ border-color: #4a5568; opacity: 0.8; }}
.empty-state {{ color: #a0aec0; font-size: 0.85em; }}
.empty-state ul {{ margin-left: 20px; margin-top: 6px; }}
.empty-state code {{ color: #e94560; background: #1a1a2e; padding: 1px 5px; border-radius: 3px; }}
.hint {{ color: #718096; font-size: 0.75em; margin-top: 6px; }}

/* 矩阵表格 */
.matrix-table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
.matrix-table th {{ background: #0f3460; color: #a0aec0; padding: 8px 10px; text-align: left; }}
.matrix-table td {{ padding: 7px 10px; border-bottom: 1px solid #2d3748; }}
.matrix-table .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.best-row {{ background: rgba(233,69,96,0.1); }}
.best-row td:first-child {{ color: #f59e0b; }}

/* 事件表格 */
.event-table-wrap {{ max-height: 400px; overflow-y: auto; }}
.event-table {{ width: 100%; border-collapse: collapse; font-size: 0.82em; }}
.event-table th {{
    background: #0f3460; color: #a0aec0; padding: 6px 10px; text-align: left;
    position: sticky; top: 0; z-index: 1;
}}
.event-table td {{ padding: 5px 10px; border-bottom: 1px solid #2d3748; }}
.event-table .num {{ text-align: right; }}

/* Tab 切换 */
.tab-bar {{
    display: flex; gap: 4px; margin-bottom: 0;
    flex-wrap: wrap;
}}
.tab-btn {{
    background: #1a1a2e;
    color: #a0aec0;
    border: 1px solid #2d3748;
    padding: 8px 16px;
    cursor: pointer;
    font-size: 0.85em;
    border-radius: 6px 6px 0 0;
    transition: all 0.15s;
    border-bottom: none;
}}
.tab-btn:hover {{ color: #fff; background: #2d3748; }}
.tab-btn.active {{ background: #e94560; color: #fff; border-color: #e94560; }}
.tab-n {{ font-size: 0.7em; color: #718096; margin-left: 4px; }}
.tab-btn.active .tab-n {{ color: rgba(255,255,255,0.7); }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.chart-container {{
    width: 100%; height: 620px;
    background: #1a1a2e;
    border: 1px solid #2d3748;
    border-radius: 0 8px 8px 8px;
}}
.chart-error {{
    text-align: center; padding: 60px 20px; color: #f59e0b; font-size: 0.9em;
}}
.chart-error code {{ color: #e94560; }}

/* Footer */
.footer {{
    text-align: center; color: #4a5568; font-size: 0.72em;
    padding: 20px; border-top: 1px solid #2d3748; margin-top: 20px;
}}

/* Scrollbar */
::-webkit-scrollbar {{ width: 6px; }}
::-webkit-scrollbar-track {{ background: #1a1a2e; }}
::-webkit-scrollbar-thumb {{ background: #4a5568; border-radius: 3px; }}

@media (max-width: 768px) {{
    .overview-grid {{ grid-template-columns: repeat(3, 1fr); }}
    .container {{ padding: 8px; }}
    .chart-container {{ height: 450px; }}
}}
</style>
</head>
<body>

<div class="header">
    <div>
        <h1>{code} {name}</h1>
        <span class="code">战役跟踪报告 · Campaign Tracker</span>
    </div>
    <span class="date">📅 {date_str}</span>
</div>

<div class="container">

    <!-- §1 战役概览 -->
    <div class="panel"><h3>📈 战役概览</h3>
    {overview_html}
    </div>

    <!-- §2 信号质量矩阵 -->
    {matrix_html}

    <!-- §3 K线图表 -->
    <div class="panel">
        <h3>📉 K线图表</h3>
        <div class="tab-bar">{tab_buttons}</div>
        {tab_contents}
    </div>

    <!-- §4 信号事件时间轴 -->
    {timeline_html}

    <!-- §5 战役决策记录 -->
    {campaign_html}

</div>

<div class="footer">
    quantify-per 量化信号系统 · 战役跟踪 v0.1 · 生成于 {date_str} · 数据来源: signals/tracking/
</div>

<script>
// Tab 切换
function switchTab(period) {{
    document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
    document.querySelectorAll('.tab-content').forEach(function(c) {{ c.classList.remove('active'); }});
    var btn = document.querySelector('[onclick="switchTab(\\'' + period + '\\')"]');
    if (btn) btn.classList.add('active');
    var tab = document.getElementById('tab_' + period);
    if (tab) {{
        tab.classList.add('active');
        var chartId = 'chart_' + period;
        if (window._charts && window._charts[chartId]) {{
            setTimeout(function() {{ window._charts[chartId].resize(); }}, 50);
        }}
    }}
}}

// 键盘切换
document.addEventListener('keydown', function(e) {{
    var periods = {js_json(PERIODS)};
    var tabs = document.querySelectorAll('.tab-btn');
    var activeIdx = -1;
    tabs.forEach(function(t, i) {{ if (t.classList.contains('active')) activeIdx = i; }});
    if (e.key === 'ArrowRight' && activeIdx < tabs.length - 1) {{
        switchTab(periods[activeIdx + 1]);
    }} else if (e.key === 'ArrowLeft' && activeIdx > 0) {{
        switchTab(periods[activeIdx - 1]);
    }}
}});
</script>

<script>
{chart_scripts}
</script>

</body>
</html>'''
    return html


# ═══════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='生成单标的 HTML 战役跟踪报告')
    parser.add_argument('--code', default='sz159740', help='标的代码 (默认: sz159740)')
    parser.add_argument('--days', type=int, default=0, help='显示最近N天数据 (0=全部)')
    args = parser.parse_args()

    code = args.code
    date_str = datetime.now().strftime('%Y%m%d')

    print(f'[1/4] 读取信号CSV数据...')
    all_data = process_all_data(code, MAX_BARS)
    for period, data in all_data.items():
        status = f'OK {data["n_rows"]}条' if data['is_valid'] else f'ERR {data.get("error", "")}'
        print(f'  {PERIOD_NAMES[period]:6s} {status}')

    print(f'[2/4] 读取周期分析数据...')
    item = read_cycle_report(code)
    name = item.get('name', code) if item else code
    if item:
        t = item.get('trend', {})
        adv = item.get('advice', {})
        print(f'  {name}: 评分{t.get("score", "?")} {t.get("label", "?")} | {adv.get("grade_label", "?")} | 主导{adv.get("dominant_cycle", {}).get("dominant_label", "?")}')
    else:
        print(f'  WARN: 未找到 {code} 的 cycle_report 数据')

    print(f'[3/4] 读取战役记录...')
    campaigns = read_op_records(code)
    print(f'  {len(campaigns)} 条战役记录')

    print(f'[4/4] 生成 HTML...')
    html = build_html(code, all_data, item, campaigns, date_str)

    out_dir = os.path.join(BASE_DIR, 'reports', 'campaign')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{code}_{date_str}.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    size_kb = os.path.getsize(out_path) / 1024
    print(f'\n[OK] 已生成: {out_path} ({size_kb:.0f} KB)')

    # 自动打开浏览器
    import webbrowser
    webbrowser.open('file:///' + out_path.replace('\\', '/'))


if __name__ == '__main__':
    main()

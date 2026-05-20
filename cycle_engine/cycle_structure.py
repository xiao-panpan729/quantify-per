# -*- coding: utf-8 -*-
"""
cycle_engine 结构与循环层 — 循环模式 / 波峰 / 主导量级 / 量能 / 波浪结构 / RS密度
"""
import math
from .utils import safe_float, read_csv, SNAPSHOT_DIR, PERIODS


# ============================================================
# 通用工具：局部极值检测 + 波峰波谷事件提取
# ============================================================

def _find_local_extremes(values, window=2, find_peaks=True):
    """找局部高点(peaks)或低点(valleys)，用于缠论分型识别和波浪结构"""
    if len(values) < window * 2 + 1:
        return []
    extremes = []
    for i in range(window, len(values) - window):
        if find_peaks:
            if all(values[i] >= values[i - j] for j in range(1, window + 1)) \
               and all(values[i] >= values[i + j] for j in range(1, window + 1)):
                extremes.append({'idx': i, 'value': values[i]})
        else:
            if all(values[i] <= values[i - j] for j in range(1, window + 1)) \
               and all(values[i] <= values[i + j] for j in range(1, window + 1)):
                extremes.append({'idx': i, 'value': values[i]})
    if len(extremes) < 2:
        return extremes
    filtered = [extremes[0]]
    for e in extremes[1:]:
        if e['idx'] - filtered[-1]['idx'] <= window:
            if find_peaks and e['value'] > filtered[-1]['value']:
                filtered[-1] = e
            elif not find_peaks and e['value'] < filtered[-1]['value']:
                filtered[-1] = e
        else:
            filtered.append(e)
    return filtered


def _extract_wave_events(lines, min_wave_ratio=0.08):
    """
    从趋势线序列提取波峰/波谷事件，过滤微小波动。
    返回: list of (idx, type, value) 按 idx 排序
      type: 'peak' | 'valley'
    """
    if len(lines) < 20:
        return []

    peaks = _find_local_extremes(lines, window=2, find_peaks=True)
    valleys = _find_local_extremes(lines, window=2, find_peaks=False)

    events = [(e['idx'], 'peak', e['value']) for e in peaks] + \
             [(e['idx'], 'valley', e['value']) for e in valleys]
    events.sort(key=lambda x: x[0])

    if len(events) < 2:
        return events

    min_wave = (max(lines) - min(lines)) * min_wave_ratio
    filtered = [events[0]]
    for i in range(1, len(events)):
        if abs(events[i][2] - filtered[-1][2]) >= min_wave:
            filtered.append(events[i])

    return filtered


# ============================================================
# 循环模式分析
# ============================================================

def cycle_pattern(anchors):
    """
    分析 ★买/★卖 排列模式

    只看最近 10 个锚点，判断当前是:
      - alternating: 买卖交替，标准循环
      - buy_dominant: 买多卖少，多头排列
      - sell_dominant: 卖多买少，空头排列
      - mixed: 混杂无序
    """
    if len(anchors) < 3:
        return {'pattern': 'insufficient', 'label': '信号不足', 'score': 0}

    recent = anchors[-10:]
    n = len(recent)
    types = [a['type'] for a in recent]
    buy_count = types.count('buy')
    sell_count = types.count('sell')

    alternations = sum(1 for i in range(1, n) if types[i] != types[i - 1])
    alt_ratio = alternations / (n - 1) if n > 1 else 0

    if buy_count >= n * 0.7:
        pattern = 'buy_dominant'
        label = f'多头排列({buy_count}买/{sell_count}卖)'
        score_base = 2.0
    elif sell_count >= n * 0.7:
        pattern = 'sell_dominant'
        label = f'空头排列({buy_count}买/{sell_count}卖)'
        score_base = 2.0
    elif alt_ratio >= 0.6:
        pattern = 'alternating'
        label = f'买卖交替(交替率{alt_ratio:.0%})'
        score_base = 3.5
    else:
        pattern = 'mixed'
        label = f'混合排列(交替率{alt_ratio:.0%})'
        score_base = 2.5

    ema_matched = sum(1 for a in recent if a['has_ema'])
    ext_matched = sum(1 for a in recent if a['has_ext'] or a['has_div'])
    closure_bonus = min(1.0, ema_matched / n * 1.5 + ext_matched / n * 1.0)

    score = min(5.0, round(score_base + closure_bonus, 1))

    return {
        'pattern': pattern,
        'label': label,
        'alternation_ratio': round(alt_ratio, 2),
        'buy_count': buy_count,
        'sell_count': sell_count,
        'score': score,
    }


# ============================================================
# 主导循环量级检测
# ============================================================

def _signal_reliability(rows, period, check_bars=None):
    """
    计算一个K线级别的信号可靠性。

    对最近200根K线内的★买/★卖信号，检查N根后价格是否按预期方向走。

    Args:
        rows: CSV行数据列表
        period: 周期名
        check_bars: 出信号后等多少根验证（按周期自适应）

    Returns:
        dict 或 None
    """
    if not rows or len(rows) < 60:
        return None

    # 价格因子
    factor = 1000 if period == 'daily' else 10000

    # 验证根数按周期自适应
    check_map = {'min5': 12, 'min15': 8, 'min30': 8, 'min60': 4, 'daily': 5}
    cb = check_bars or check_map.get(period, 8)
    lookback = 200  # 回看200根K线

    if len(rows) < lookback + cb:
        lookback = max(len(rows) - cb - 1, 50)

    recent = rows[-(lookback + cb):]

    buy_signals = []
    sell_signals = []

    for i, r in enumerate(recent):
        buy = str(r.get('buy_signal', '')).strip()
        sell = str(r.get('sell_signal', '')).strip()
        if buy == '★买':
            buy_signals.append(i)
        if sell == '★卖':
            sell_signals.append(i)

    if not buy_signals and not sell_signals:
        return None

    # 验证买入信号
    buy_ok = 0
    buy_moves = []
    for idx in buy_signals:
        if idx + cb < len(recent):
            entry = safe_float(recent[idx].get('close', 0)) / factor
            exit_ = safe_float(recent[idx + cb].get('close', 0)) / factor
            if exit_ > entry:
                buy_ok += 1
            buy_moves.append((exit_ - entry) / entry * 100)

    # 验证卖出信号
    sell_ok = 0
    sell_moves = []
    for idx in sell_signals:
        if idx + cb < len(recent):
            entry = safe_float(recent[idx].get('close', 0)) / factor
            exit_ = safe_float(recent[idx + cb].get('close', 0)) / factor
            if exit_ < entry:
                sell_ok += 1
            sell_moves.append((entry - exit_) / entry * 100)

    total_buy = len(buy_signals)
    total_sell = len(sell_signals)
    buy_rate = buy_ok / total_buy if total_buy > 0 else 0
    sell_rate = sell_ok / total_sell if total_sell > 0 else 0

    avg_buy_move = (sum(buy_moves) / len(buy_moves)) if buy_moves else 0
    avg_sell_move = (sum(sell_moves) / len(sell_moves)) if sell_moves else 0

    return {
        'period': period,
        'buy_signals': total_buy,
        'buy_correct': buy_ok,
        'buy_rate': round(buy_rate, 2),
        'avg_buy_move': round(avg_buy_move, 2),
        'sell_signals': total_sell,
        'sell_correct': sell_ok,
        'sell_rate': round(sell_rate, 2),
        'avg_sell_move': round(avg_sell_move, 2),
    }


def detect_dominant_cycle(code, period_results, _cached_rows=None):
    """
    信号可靠性比较法 — 检测当前主导周期。

    核心理念:
      哪个级别的★买/★卖信号最近最靠谱，那个级别就是主导周期。
      "打开这个级别的图，信号说买就涨、说卖就跌，照它做能赚钱。"

    与原版兼容:
      - 仍返回 dominant_cycle / dominant_label
      - 新增 dominant_buy / dominant_sell 侧输出
      - stretched_periods 保留(空列表)，外部引用不崩

    Returns: dict
        dominant_cycle: 'min5'|'min15'|'min30'|'min60'|'daily'
        dominant_label: 中文标签
        dominant_buy: 买入侧最佳周期
        dominant_sell: 卖出侧最佳周期
        detail: '做多15分钟(★买75%均+1.2%) 做空30分钟(★卖80%均-2.1%)'
        stretched_periods: []  (兼容原字段)
        buy_rate: 综合★买兑现率
        sell_rate: 综合★卖兑现率
    """
    p_labels = {'min5': '5分钟', 'min15': '15分钟', 'min30': '30分钟',
                'min60': '60分钟', 'daily': '日线'}
    periods_to_check = ['min5', 'min15', 'min30', 'min60', 'daily']

    results = []
    for p in periods_to_check:
        rows = (_cached_rows.get(p) if _cached_rows else read_csv(code, p))
        if not rows:
            continue
        r = _signal_reliability(rows, p)
        if r:
            results.append(r)

    if not results:
        # 兜底返回日线
        return {
            'dominant_cycle': 'daily',
            'dominant_label': '日线',
            'dominant_buy': None,
            'dominant_sell': None,
            'detail': '所有周期信号不足，默认日线',
            'stretched_periods': [],
            'buy_rate': 0,
            'sell_rate': 0,
        }

    # 评分函数：兑现率 × 信号数量加权
    def _score(rate, n):
        return rate * min(n, 20) / 20

    minute_results = [r for r in results if r['period'] != 'daily']
    daily_only = [r for r in results if r['period'] == 'daily']

    if minute_results:
        # 综合最佳
        best_overall = max(minute_results,
                           key=lambda r: _score(
                               (r['buy_rate'] + r['sell_rate']) / 2,
                               r['buy_signals'] + r['sell_signals']))
        # 做多最佳
        buy_candidates = [r for r in minute_results if r['buy_signals'] > 0]
        best_buy = max(buy_candidates,
                       key=lambda r: _score(r['buy_rate'], r['buy_signals'])) \
                   if buy_candidates else None
        # 做空最佳
        sell_candidates = [r for r in minute_results if r['sell_signals'] > 0]
        best_sell = max(sell_candidates,
                        key=lambda r: _score(r['sell_rate'], r['sell_signals'])) \
                    if sell_candidates else None
    else:
        best_overall = daily_only[0] if daily_only else results[0]
        best_buy = best_overall if best_overall.get('buy_signals', 0) > 0 else None
        best_sell = best_overall if best_overall.get('sell_signals', 0) > 0 else None

    def _label(r):
        return p_labels.get(r['period'], r['period']) if r else '无'

    def _detail(r, side):
        if not r:
            return ''
        if side == 'buy':
            return f"★买{r['buy_rate']:.0%}均{r['avg_buy_move']:+.2f}%"
        else:
            return f"★卖{r['sell_rate']:.0%}均{r['avg_sell_move']:+.2f}%"

    # 构建detail
    parts = []
    if best_buy:
        parts.append(f"做多{_label(best_buy)}({_detail(best_buy, 'buy')})")
    if best_sell:
        parts.append(f"做空{_label(best_sell)}({_detail(best_sell, 'sell')})")
    detail = ' '.join(parts) if parts else '无有效信号'

    return {
        'dominant_cycle': best_overall['period'],
        'dominant_label': _label(best_overall),
        'dominant_buy': best_buy['period'] if best_buy else None,
        'dominant_sell': best_sell['period'] if best_sell else None,
        'detail': detail,
        'stretched_periods': [],
        'buy_rate': best_buy['buy_rate'] if best_buy else 0,
        'sell_rate': best_sell['sell_rate'] if best_sell else 0,
    }


# ============================================================
# 量价阶段标注
# ============================================================

def analyze_volume_regime(code, daily_rows, period_results):
    """
    判断日线成交量所处的量价阶段（最小改动，不参与评分）

    分析逻辑:
      1. 计算百日地量（最近100天最低成交量）
      2. 计算地量堆密度（最近20天在1.0~1.3倍地量的占比）
      3. 结合日线★买/★卖信号状态，判断量价阶段

    Returns: dict
        phase: 量价阶段标签
        detail: 中文描述
        vol_ratio: 当日量/百日地量比值
        dilangdui_density: 地量堆密度
    """
    if not daily_rows or len(daily_rows) < 30:
        return {'phase': '数据不足'}

    volumes = [safe_float(r.get('volume', 0)) for r in daily_rows
               if safe_float(r.get('volume', 0)) > 0]
    if len(volumes) < 30:
        return {'phase': '数据不足', 'detail': f'成交量数据{len(volumes)}条'}

    lookback = min(100, len(volumes))
    recent_v = volumes[-lookback:]
    sorted_v = sorted(recent_v)
    min_v = sorted_v[int(len(sorted_v) * 0.05)]
    cur_v = volumes[-1]
    vol_r = cur_v / min_v if min_v > 0 else 999

    win = min(20, len(recent_v))
    watch = recent_v[-win:]
    in_pile = sum(1 for v in watch if v <= min_v * 1.3)
    pile_density = in_pile / win if win > 0 else 0

    ds = period_results.get('daily') or {}
    sq = ds.get('signal_quality') if ds else None
    if sq:
        buy_lv = sq.get('buy_level', 0)
        sell_lv = sq.get('sell_level', 0)
    else:
        buy_lv = 0
        sell_lv = 0

    is_dilang = vol_r < 1.3
    is_pile = pile_density > 0.5
    is_bearish = sell_lv > buy_lv * 1.2

    if is_dilang and is_pile and is_bearish:
        phase = '底部地量区'
        detail = f'百日地量附近(×{vol_r:.1f})，地量堆密度{pile_density:.0%}，供应枯竭'
    elif is_dilang and is_pile and not is_bearish:
        phase = '缩量回调'
        detail = f'★买周期中缩量到百日地量(×{vol_r:.1f})，地量堆{pile_density:.0%}，洗盘性质'
    elif is_dilang and not is_pile:
        phase = '初步缩量'
        detail = f'缩量到百日地量附近(×{vol_r:.1f})，未形成地量堆({pile_density:.0%})，观察'
    else:
        phase = '正常量能'
        detail = ''

    return {
        'phase': phase,
        'detail': detail,
        'vol_ratio': round(vol_r, 2),
        'dilangdui_density': round(pile_density, 2),
    }


# ============================================================
# 波浪结构分析
# ============================================================

def judge_wave_structure(code, period_results, dominant_info, _cached_rows=None):
    """
    结构分析：一句话判断当前主导量级的结构状态

    1. 主导量级方向（买闭环/卖闭环/平衡）
    2. 次级别推动段 vs 修正段对比（涨跌段密度）
    3. 回调深度（最近一段回调占上涨比例）
    """
    dc = dominant_info.get('dominant_cycle', 'min15')
    if dc not in PERIODS:
        dc = 'min15'
    dc_idx = PERIODS.index(dc)

    ds = (period_results.get(dc) or {}).get('signal_quality') or {}
    dc_buy = ds.get('buy_level', 0) or 0
    dc_sell = ds.get('sell_level', 0) or 0

    if dc_buy >= dc_sell * 1.1:
        dc_dir = '买闭环'
    elif dc_sell >= dc_buy * 1.1:
        dc_dir = '卖闭环'
    else:
        dc_dir = '平衡'

    if dc_idx == 0:
        if dc_dir == '买闭环':
            mark = '✔ 买闭环中'
        elif dc_dir == '卖闭环':
            mark = '✗ 卖闭环中'
        else:
            mark = '○ 方向不明'
        return {'structure': f'{dc} {dc_dir} ({mark})',
                'detail': '小级别主导，无次级别结构',
                'dominant': dc, 'direction': dc_dir, 'sub_level': dc,
                'verdict_mark': mark, 'retrace_pct': None}

    sub_idx = dc_idx - 1
    sub_p = PERIODS[sub_idx]
    ss = (period_results.get(sub_p) or {}).get('signal_quality') or {}
    sub_buy = ss.get('buy_level', 0) or 0
    sub_sell = ss.get('sell_level', 0) or 0

    sub_rows = (_cached_rows.get(sub_p) if _cached_rows
                else read_csv(code, sub_p))
    if not sub_rows:
        return {'structure': f'{dc} {dc_dir}', 'detail': '次级别数据不足',
                'dominant': dc, 'direction': dc_dir, 'sub_level': sub_p}

    lines = [safe_float(r.get('trend_line', 0)) for r in sub_rows
             if safe_float(r.get('trend_line', 0)) > 0]
    if len(lines) < 20:
        return {'structure': f'{dc} {dc_dir}', 'detail': '次级趋势线不足',
                'dominant': dc, 'direction': dc_dir, 'sub_level': sub_p}

    events = _extract_wave_events(lines)

    rises, falls = [], []
    for i in range(len(events) - 1):
        if events[i][1] == 'valley' and events[i + 1][1] == 'peak':
            rises.append({'len': events[i + 1][0] - events[i][0],
                          'rng': events[i + 1][2] - events[i][2]})
        elif events[i][1] == 'peak' and events[i + 1][1] == 'valley':
            falls.append({'len': events[i + 1][0] - events[i][0],
                          'rng': events[i][2] - events[i + 1][2]})

    avg_rise_len = sum(s['len'] for s in rises) / len(rises) if rises else 0
    avg_fall_len = sum(s['len'] for s in falls) / len(falls) if falls else 0
    n_rises, n_falls = len(rises), len(falls)

    retrace_pct = None
    if len(events) >= 4:
        last, prev, pprev = events[-1], events[-2], events[-3]
        if last[1] == 'valley' and prev[1] == 'peak' and pprev[1] == 'valley':
            rise_rng = prev[2] - pprev[2]
            if rise_rng > 0:
                retrace_pct = (prev[2] - last[2]) / rise_rng * 100

    last_dir = ''
    if events[-1][1] == 'peak':
        last_dir = '末段上涨中'
    elif len(events) >= 3 and events[-1][1] == 'valley' and events[-2][1] == 'peak':
        last_dir = '末段回调中'

    if dc_dir == '买闭环':
        if n_rises >= n_falls and avg_rise_len >= avg_fall_len * 1.2:
            sub_v = f'涨段({n_rises})>跌段({n_falls}), 均长{avg_rise_len:.0f}>{avg_fall_len:.0f}'
            mark = '✔ 推动>修正'
        elif n_rises >= n_falls and avg_rise_len >= avg_fall_len:
            sub_v = f'涨段({n_rises})≥跌段({n_falls})'
            mark = '∼ 涨略强'
        elif n_rises < n_falls:
            sub_v = f'跌段({n_falls})>涨段({n_rises})'
            mark = '✗ 涨势存疑'
        else:
            sub_v = '涨跌均衡'
            mark = '∼ 中性'
        if retrace_pct is not None:
            if retrace_pct < 33:
                sub_v += f', 浅调{retrace_pct:.0f}%'
                mark += '✔'
            elif retrace_pct > 66:
                sub_v += f', 深调{retrace_pct:.0f}%'
                mark += '⚠'
            else:
                sub_v += f', 调{retrace_pct:.0f}%'
    elif dc_dir == '卖闭环':
        if n_falls >= n_rises:
            sub_v = f'跌段({n_falls})主导'
            mark = '✗ 下跌延续'
        else:
            sub_v = '跌中带反弹'
            mark = '∼ 或减速'
        if retrace_pct is not None and retrace_pct < 33:
            sub_v += ', 反弹弱'
    else:
        sub_v = '涨跌均衡'
        mark = '○ 方向不明'

    return {
        'structure': f'{dc} {dc_dir} → {sub_p} {mark}',
        'detail': f'{sub_v} | {last_dir}',
        'dominant': dc,
        'direction': dc_dir,
        'sub_level': sub_p,
        'verdict_mark': mark,
        'retrace_pct': round(retrace_pct, 1) if retrace_pct is not None else None,
    }


# ============================================================
# 指数级行情条件检测
# ============================================================

def detect_exponential_readiness(code, daily_rows, period_results,
                                  dominant_info, _cached_rows=None):
    """
    指数级行情条件检测：三维度评分 + 信号灯

    1. 压缩率(0-3): 布林带宽低位 + 百日地量
    2. 加速度(0-3): 推调比趋势 + 回调深度趋势
    3. 周期锁定(0-4): MACD dif方向一致 + 信号质量同步

    总分0-10 → 绿灯(>=7) 黄灯(4-6) 红灯(0-3)
    """
    sc = {'compression': 0, 'acceleration': 0, 'cycle_lock': 0}
    info = []
    persist = {'compress_days': 0, 'direction_align': '', 'total_days': 0}

    dc = dominant_info.get('dominant_cycle', 'min15')
    dc_idx = PERIODS.index(dc) if dc in PERIODS else 2
    ds = (period_results.get(dc) or {}).get('signal_quality') or {}
    dc_buy = ds.get('buy_level', 0) or 0
    dc_sell = ds.get('sell_level', 0) or 0
    is_buy_close = dc_buy > dc_sell

    # 1. 压缩率
    if daily_rows and len(daily_rows) >= 60:
        mids = [safe_float(r.get('bb_ma221', 0)) for r in daily_rows[-120:]]
        ups = [safe_float(r.get('bb_red_line', 0)) for r in daily_rows[-120:]]
        widths = [(ups[i] - mids[i]) / mids[i] * 100
                  for i in range(len(mids)) if mids[i] > 0 < ups[i]]
        if widths:
            cur_w, min_w, max_w = widths[-1], min(widths), max(widths)
            pct = (cur_w - min_w) / (max_w - min_w) * 100 if max_w > min_w else 50
            if pct < 20:
                sc['compression'] += 2
                info.append(f'压缩:带宽极端低位(pct={pct:.0f}%)')
            elif pct < 40:
                sc['compression'] += 1
                info.append(f'压缩:带宽偏低(pct={pct:.0f}%)')
            else:
                info.append(f'压缩:带宽{pct:.0f}%分位')

            median_w = sorted(widths)[len(widths) // 2]
            compress_days = 0
            for w in reversed(widths):
                if w <= median_w:
                    compress_days += 1
                else:
                    break
            persist['compress_days'] = compress_days
            info.append(f'压缩持续{compress_days}天')

            vols = [safe_float(r.get('volume', 0)) for r in daily_rows
                    if safe_float(r.get('volume', 0)) > 0]
            if vols:
                recent_v = vols[-min(100, len(vols)):]
                sorted_v = sorted(recent_v)
                min_v = sorted_v[int(len(sorted_v) * 0.05)]
                cur_v = vols[-1]
                vol_r = cur_v / min_v if min_v > 0 else 99
                if vol_r < 1.3:
                    sc['compression'] += 1
                    info.append(f'地量(×{vol_r:.1f})')

    # 2. 加速度
    sub_p = PERIODS[max(0, dc_idx - 1)]
    sub_rows = (_cached_rows.get(sub_p) if _cached_rows
                else read_csv(code, sub_p))
    if sub_rows and len(sub_rows) >= 30:
        lines = [safe_float(r.get('trend_line', 0)) for r in sub_rows
                 if safe_float(r.get('trend_line', 0)) > 0]
        if len(lines) >= 30:
            events = _extract_wave_events(lines)
            rises, falls = [], []
            for i in range(len(events) - 1):
                if events[i][1] == 'valley' and events[i + 1][1] == 'peak':
                    rises.append({'len': events[i + 1][0] - events[i][0],
                                  'h': events[i + 1][2] - events[i][2]})
                elif events[i][1] == 'peak' and events[i + 1][1] == 'valley':
                    falls.append({'len': events[i + 1][0] - events[i][0],
                                  'd': events[i][2] - events[i + 1][2]})
            if len(rises) >= 4 and len(falls) >= 3:
                mid_r, mid_f = len(rises) // 2, len(falls) // 2
                r_late, r_early = rises[mid_r:], rises[:mid_r]
                f_late, f_early = falls[mid_f:], falls[:mid_f]
                avg_rl = sum(s['h'] for s in r_late) / len(r_late)
                avg_re = sum(s['h'] for s in r_early) / len(r_early)
                avg_fl = sum(s['len'] for s in f_late) / len(f_late)
                avg_fe = sum(s['len'] for s in f_early) / len(f_early)
                acc_items = 0
                if avg_rl > avg_re * 1.2:
                    sc['acceleration'] += 1
                    acc_items += 1
                    info.append(f'加速:推幅↑({avg_re:.1f}→{avg_rl:.1f})')
                if avg_fl < avg_fe * 0.8:
                    sc['acceleration'] += 1
                    acc_items += 1
                    info.append(f'加速:调时↓({avg_fe:.0f}→{avg_fl:.0f}K)')
                depths = []
                for i in range(min(len(falls), len(rises))):
                    if rises[i]['h'] > 0:
                        depths.append(falls[i]['d'] / rises[i]['h'] * 100)
                if depths:
                    early_d = sum(depths[:len(depths) // 2]) / max(len(depths) // 2, 1)
                    late_d = sum(depths[len(depths) // 2:]) / max(len(depths) - len(depths) // 2, 1)
                    if late_d < early_d * 0.7:
                        sc['acceleration'] += 1
                        acc_items += 1
                        info.append(f'加速:回调深↓({early_d:.0f}%→{late_d:.0f}%)')
                if acc_items == 0:
                    info.append(f'加速:平稳({len(rises)}涨{len(falls)}跌)')
            else:
                info.append(f'加速:段不足({len(rises)}涨{len(falls)}跌)')

    # 3. 周期锁定
    dif_signs = []
    levels_ok = 0
    for p in PERIODS:
        sq = (period_results.get(p) or {}).get('signal_quality') or {}
        if (sq.get('level', 0) or 0) >= 3:
            levels_ok += 1
        rows = (_cached_rows.get(p) if _cached_rows
                else read_csv(code, p))
        if rows:
            difs = [safe_float(r.get('macd_dif', 0)) for r in rows[-5:]]
            avg_dif = sum(difs) / len(difs) if difs else 0
            if avg_dif != 0:
                dif_signs.append(1 if avg_dif > 0 else -1)

    if dif_signs:
        pos = sum(1 for s in dif_signs if s > 0)
        neg = sum(1 for s in dif_signs if s < 0)
        same_pct = max(pos, neg) / len(dif_signs)
        if same_pct >= 0.8:
            sc['cycle_lock'] += 2
            info.append(f'锁定:方向一致({same_pct:.0%})')
        elif same_pct >= 0.6:
            sc['cycle_lock'] += 1
            info.append(f'锁定:偏一致({same_pct:.0%})')
        else:
            info.append(f'锁定:分歧({same_pct:.0%})')
        if levels_ok >= 4:
            sc['cycle_lock'] += 2
            info.append(f'锁定:信号{levels_ok}/5同步')
        elif levels_ok >= 3:
            sc['cycle_lock'] += 1
            info.append(f'锁定:信号{levels_ok}/5')
        else:
            info.append(f'锁定:信号仅{levels_ok}/5')

    if is_buy_close:
        persist['direction_align'] = '买闭环+'
        if sc['compression'] >= 1:
            info.append('方向:压缩+买闭环=正向')
        else:
            info.append('方向:买闭环(未压缩)')
    else:
        persist['direction_align'] = '卖闭环-'
        if sc['compression'] >= 1:
            info.append('方向:⚠ 压缩+卖闭环=负向')
        else:
            info.append('方向:卖闭环')

    total = sc['compression'] + sc['acceleration'] + sc['cycle_lock']
    if total >= 7:
        light = '\U0001f7e2 绿灯'
        conclusion = '指数级条件成熟'
    elif total >= 4:
        light = '\U0001f7e1 黄灯'
        conclusion = '部分条件具备'
    else:
        light = '\U0001f534 红灯'
        conclusion = '条件不足'

    return {
        'traffic_light': light,
        'total_score': total,
        'scores': sc,
        'detail': ' | '.join(info),
        'conclusion': conclusion,
        'persist': persist,
    }


# ============================================================
# 缠论结构分析 — 阻力/支撑密度检测
# ============================================================

def detect_rs_density(code, daily_rows):
    """
    缠论结构分析 — 阻力/支撑密度检测 (v3.8 新增)

    用缠论分型理论判断趋势结构:

    上涨趋势 = 顶分型逐次抬高 + 底分型逐次抬高 → 结构支撑强
    下跌趋势 = 底分型逐次降低 + 顶分型逐次降低 → 结构阻力强

    趋势终结(分割线):
      上涨终结: 最后一个顶抬高但底没有抬高(下降)
      下跌终结: 最后一个底创新低后顶不再降低(高于前顶)

    rs_score > 0 → 结构有利做多, rs_score < 0 → 结构不利做多
    """
    if not daily_rows or len(daily_rows) < 30:
        return {'status': '数据不足', 'rs_score': 0, 'rs_label': '未知'}

    closes = [safe_float(r.get('close', 0)) for r in daily_rows]
    if not closes:
        return {'status': '数据异常', 'rs_score': 0, 'rs_label': '未知'}

    cur_price = closes[-1]
    peaks = _find_local_extremes(closes, window=2, find_peaks=True)
    valleys = _find_local_extremes(closes, window=2, find_peaks=False)

    trend_state = 'range'
    trend_label = '横盘震荡'
    uptrend_end = False
    downtrend_end = False

    if len(peaks) >= 3 and len(valleys) >= 3:
        p3 = peaks[-3:]
        v3 = valleys[-3:]
        peak_rising = p3[0]['value'] < p3[1]['value'] < p3[2]['value']
        valley_rising = v3[0]['value'] < v3[1]['value'] < v3[2]['value']
        peak_falling = p3[0]['value'] > p3[1]['value'] > p3[2]['value']
        valley_falling = v3[0]['value'] > v3[1]['value'] > v3[2]['value']

        if peak_rising and not valley_rising and v3[2]['value'] < v3[1]['value']:
            uptrend_end = True
            trend_state = 'uptrend_end'
            trend_label = '上涨终结⚠️'
        elif valley_falling and not peak_falling and p3[2]['value'] > p3[1]['value']:
            downtrend_end = True
            trend_state = 'downtrend_end'
            trend_label = '下跌终结⚠️'
        elif peak_rising and valley_rising:
            trend_state = 'uptrend'
            trend_label = '上涨趋势'
        elif peak_falling and valley_falling:
            trend_state = 'downtrend'
            trend_label = '下跌趋势'
        else:
            trend_state = 'range'
            trend_label = '横盘震荡'

    above_peaks = [p for p in peaks if p['value'] > cur_price]
    below_valleys = [v for v in valleys if v['value'] < cur_price]
    nearest_resistance = min(above_peaks, key=lambda x: x['value'] - cur_price) if above_peaks else None
    nearest_support = max(below_valleys, key=lambda x: x['value']) if below_valleys else None

    base_scores = {
        'uptrend': 1.5, 'downtrend': -1.5, 'range': 0.0,
        'uptrend_end': -0.5, 'downtrend_end': 1.0,
    }
    rs_score = base_scores.get(trend_state, 0.0)
    if nearest_resistance:
        rd = (nearest_resistance['value'] - cur_price) / cur_price * 100
        if rd < 3 and rs_score > 0:
            rs_score -= 0.3
    if nearest_support:
        sd = (cur_price - nearest_support['value']) / cur_price * 100
        if sd < 3 and rs_score < 0:
            rs_score += 0.3
    rs_score = round(max(-2.0, min(2.0, rs_score)), 2)

    if rs_score > 0.5:
        rs_label = '结构支撑强'
    elif rs_score < -0.5:
        rs_label = '结构阻力强'
    elif rs_score > 0:
        rs_label = '偏多结构'
    elif rs_score < 0:
        rs_label = '偏空结构'
    else:
        rs_label = '结构均衡'

    chan_parts = []
    if trend_state in ('uptrend_end', 'downtrend_end'):
        if uptrend_end:
            chan_parts.append(f'顶抬高{p3[-1]["value"]:.4f}>{p3[-2]["value"]:.4f}')
            chan_parts.append(f'底降低{v3[-1]["value"]:.4f}<{v3[-2]["value"]:.4f}')
            chan_parts.append('分割线:上涨趋势可能终结')
        elif downtrend_end:
            chan_parts.append(f'底新低{v3[-1]["value"]:.4f}<{v3[-2]["value"]:.4f}')
            chan_parts.append(f'顶抬高{p3[-1]["value"]:.4f}>{p3[-2]["value"]:.4f}')
            chan_parts.append('分割线:下跌趋势可能终结')
    elif trend_state == 'uptrend':
        chan_parts.append(f'顶抬高{p3[-1]["value"]-p3[-2]["value"]:.4f}')
        chan_parts.append(f'底抬高{v3[-1]["value"]-v3[-2]["value"]:.4f}')
    elif trend_state == 'downtrend':
        chan_parts.append(f'顶降低{p3[-2]["value"]-p3[-1]["value"]:.4f}')
        chan_parts.append(f'底降低{v3[-2]["value"]-v3[-1]["value"]:.4f}')
    else:
        if peaks and valleys:
            chan_parts.append(f'最近顶{peaks[-1]["value"]:.4f}')
            chan_parts.append(f'最近底{valleys[-1]["value"]:.4f}')

    return {
        'trend_state': trend_state,
        'trend_label': trend_label,
        'chan_structure': ' | '.join(chan_parts),
        'rs_score': rs_score,
        'rs_label': rs_label,
        'nearest_resistance': {
            'price': round(nearest_resistance['value'], 4),
            'distance_pct': round((nearest_resistance['value'] - cur_price) / cur_price * 100, 2),
        } if nearest_resistance else None,
        'nearest_support': {
            'price': round(nearest_support['value'], 4),
            'distance_pct': round((cur_price - nearest_support['value']) / cur_price * 100, 2),
        } if nearest_support else None,
        'pivot_summary': {
            'last_peak': round(peaks[-1]['value'], 4) if peaks else None,
            'last_valley': round(valleys[-1]['value'], 4) if valleys else None,
            'n_peaks': len(peaks),
            'n_valleys': len(valleys),
        },
    }

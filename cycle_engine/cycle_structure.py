# -*- coding: utf-8 -*-
"""
cycle_engine 结构与循环层 — 循环模式 / 波峰 / 主导量级 / 量能 / 波浪结构 / RS密度
"""
import math
from .utils import safe_float, read_csv, SNAPSHOT_DIR

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

    # 交替次数
    alternations = sum(1 for i in range(1, n) if types[i] != types[i-1])
    alt_ratio = alternations / (n - 1) if n > 1 else 0

    # 模式判断
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

    # 闭环加分
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
# 第四层扩展: 波峰间距法 — 主导循环量级检测
# ============================================================


def _wave_peaks(values):
    """
    找趋势线的波峰位置

    波峰定义: 比前后各2个点都高的局部高点
    返回: 波峰在 values 中的索引列表
    """
    if len(values) < 5:
        return []

    window = 2
    peaks = []
    for i in range(window, len(values) - window):
        if all(values[i] >= values[i - j] for j in range(1, window + 1)) \
           and all(values[i] >= values[i + j] for j in range(1, window + 1)):
            peaks.append(i)

    # 去重: 连续满足的只取最高的那根
    if len(peaks) < 2:
        return peaks
    filtered = [peaks[0]]
    for p in peaks[1:]:
        if p - filtered[-1] <= window:
            if values[p] > values[filtered[-1]]:
                filtered[-1] = p
        else:
            filtered.append(p)
    return filtered



def _peak_intervals(peaks):
    """计算连续波峰之间的间距（K线根数）"""
    if len(peaks) < 2:
        return []
    return [peaks[i+1] - peaks[i] for i in range(len(peaks) - 1)]



def detect_dominant_cycle(code, period_results):
    """
    波峰间距法 — 检测当前主导循环量级

    从最小周期(5分钟)开始逐级向上检查:
      每个周期取 trend_line 的波峰，量间距
      间距稳定(当前/历史 < 1.5倍) → 该级别是主导量级
      间距拉长(>= 1.5倍) → 上级周期在接管，继续向上查

    Returns: dict
        dominant_cycle: 'min5'|'min15'|'min30'|'min60'|'daily'
        dominant_label: 中文标签
        detail: 各级间距变化描述
        stretched_periods: 被判定为拉长的级别列表
    """
    periods_to_check = ['min5', 'min15', 'min30', 'min60', 'daily']
    p_labels = {'min5': '5分钟', 'min15': '15分钟', 'min30': '30分钟',
                'min60': '60分钟', 'daily': '日线'}

    all_details = []
    stretched = []

    for p in periods_to_check:
        rows = read_csv(code, p)
        if not rows:
            all_details.append(f'{p_labels[p]}:无数据')
            continue

        values = [safe_float(r.get('trend_line', 0)) for r in rows if safe_float(r.get('trend_line', 0)) > 0]
        if len(values) < 30:
            all_details.append(f'{p_labels[p]}:数据不足({len(values)})')
            continue

        peaks = _wave_peaks(values)
        if len(peaks) < 3:
            all_details.append(f'{p_labels[p]}:波峰不足({len(peaks)})')
            continue

        intervals = _peak_intervals(peaks)
        if len(intervals) < 2:
            all_details.append(f'{p_labels[p]}:间距不足')
            continue

        current = intervals[-1]
        baseline = intervals[:-1]
        avg_base = sum(baseline) / len(baseline) if baseline else current

        stretch = current / avg_base if avg_base > 0 else 1.0
        all_details.append(f'{p_labels[p]}间距{current:.0f}/{avg_base:.0f}({stretch:.1f}倍)')

        if stretch < 1.5:
            return {
                'dominant_cycle': p,
                'dominant_label': p_labels[p],
                'detail': ' | '.join(all_details),
                'stretched_periods': stretched,
            }
        else:
            stretched.append(p)

    # 全线拉长 → 日线默认
    return {
        'dominant_cycle': 'daily',
        'dominant_label': '日线',
        'detail': ' | '.join(all_details) + ' → 全线拉长,日线主导',
        'stretched_periods': stretched,
    }


# ============================================================
# 第四层扩展: 量价阶段标注（仅日线级别）
# ============================================================


def analyze_volume_regime(code, daily_rows, period_results):
    """
    判断日线成交量所处的量价阶段（最小改动，不参与评分）

    分析逻辑:
      1. 计算百日地量（最近100天最低成交量）
      2. 计算地量堆密度（最近20天在1.0~1.3倍地量的占比）
      3. 结合日线★买/★卖信号状态，判断量价阶段

    量价阶段含义:
      - 底部地量区: ★卖周期末端的地量堆 → 供应枯竭，等需求
      - 缩量回调: ★买周期中的百日地量附近 → 上涨中继洗盘
      - 初步缩量: 缩到地量附近但未形成地量堆 → 观察
      - 正常放量: 量在1.3~2.0倍地量 → 正常交易
      - 显著放量: 量超过2倍地量 → 放量异常

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

    # 百日地量（取5分位值，避免极端低值干扰）
    lookback = min(100, len(volumes))
    recent_v = volumes[-lookback:]
    sorted_v = sorted(recent_v)
    min_v = sorted_v[int(len(sorted_v) * 0.05)]  # 5分位值作为地量水准
    cur_v = volumes[-1]
    vol_r = cur_v / min_v if min_v > 0 else 999

    # 地量堆密度：最近20天在[地量, 地量×1.3]的天数占比
    win = min(20, len(recent_v))
    watch = recent_v[-win:]
    in_pile = sum(1 for v in watch if v <= min_v * 1.3)
    pile_density = in_pile / win if win > 0 else 0

    # 日线最近的信号主导方向
    ds = period_results.get('daily') or {}
    sq = ds.get('signal_quality') if ds else None
    if sq:
        buy_lv = sq.get('buy_level', 0)
        sell_lv = sq.get('sell_level', 0)
    else:
        buy_lv = 0
        sell_lv = 0

    # ---- 判断阶段 ----
    is_dilang = vol_r < 1.3
    is_pile = pile_density > 0.5
    is_bearish = sell_lv > buy_lv * 1.2  # 卖侧显著占优

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
        # 正常放量/显著放量不标注（不制造噪音）
        phase = '正常量能'
        detail = ''

    return {
        'phase': phase,
        'detail': detail,
        'vol_ratio': round(vol_r, 2),
        'dilangdui_density': round(pile_density, 2),
    }



def judge_wave_structure(code, period_results, dominant_info):
    """
    结构分析：一句话判断当前主导量级的结构状态

    1. 主导量级方向（买闭环/卖闭环/平衡）
    2. 次级别推动段 vs 修正段对比（涨跌段密度）
    3. 回调深度（最近一段回调占上涨比例）
    """
    PERIODS = ['min5', 'min15', 'min30', 'min60', 'daily']

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
        return {'structure': f'{dc} {dc_dir} ({mark})', 'detail': '小级别主导，无次级别结构',
                'dominant': dc, 'direction': dc_dir, 'sub_level': dc, 'verdict_mark': mark,
                'retrace_pct': None}

    sub_idx = dc_idx - 1
    sub_p = PERIODS[sub_idx]
    ss = (period_results.get(sub_p) or {}).get('signal_quality') or {}
    sub_buy = ss.get('buy_level', 0) or 0
    sub_sell = ss.get('sell_level', 0) or 0

    sub_rows = read_csv(code, sub_p)
    if not sub_rows:
        return {'structure': f'{dc} {dc_dir}', 'detail': '次级别数据不足',
                'dominant': dc, 'direction': dc_dir, 'sub_level': sub_p}

    lines = [safe_float(r.get('trend_line', 0)) for r in sub_rows
             if safe_float(r.get('trend_line', 0)) > 0]
    if len(lines) < 20:
        return {'structure': f'{dc} {dc_dir}', 'detail': '次级趋势线不足',
                'dominant': dc, 'direction': dc_dir, 'sub_level': sub_p}

    peaks = _wave_peaks(lines)
    neg = [-v for v in lines]
    valley_idxs = _wave_peaks(neg)
    events = [(p, 'peak', lines[p]) for p in peaks] + \
             [(v, 'valley', lines[v]) for v in valley_idxs]
    events.sort(key=lambda x: x[0])

    MIN_WAVE = (max(lines) - min(lines)) * 0.08
    filtered = [events[0]]
    for i in range(1, len(events)):
        if abs(events[i][2] - filtered[-1][2]) >= MIN_WAVE:
            filtered.append(events[i])

    rises, falls = [], []
    for i in range(len(filtered) - 1):
        if filtered[i][1] == 'valley' and filtered[i+1][1] == 'peak':
            rises.append({'len': filtered[i+1][0] - filtered[i][0],
                          'rng': filtered[i+1][2] - filtered[i][2]})
        elif filtered[i][1] == 'peak' and filtered[i+1][1] == 'valley':
            falls.append({'len': filtered[i+1][0] - filtered[i][0],
                          'rng': filtered[i][2] - filtered[i+1][2]})

    avg_rise_len = sum(s['len'] for s in rises) / len(rises) if rises else 0
    avg_fall_len = sum(s['len'] for s in falls) / len(falls) if falls else 0
    n_rises, n_falls = len(rises), len(falls)

    retrace_pct = None
    if len(filtered) >= 4:
        last, prev, pprev = filtered[-1], filtered[-2], filtered[-3]
        if last[1] == 'valley' and prev[1] == 'peak' and pprev[1] == 'valley':
            rise_rng = prev[2] - pprev[2]
            if rise_rng > 0:
                retrace_pct = (prev[2] - last[2]) / rise_rng * 100

    last_dir = ''
    if filtered[-1][1] == 'peak':
        last_dir = '末段上涨中'
    elif len(filtered) >= 3 and filtered[-1][1] == 'valley' and filtered[-2][1] == 'peak':
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



def detect_exponential_readiness(code, daily_rows, period_results, dominant_info):
    """
    指数级行情条件检测：三维度评分 + 信号灯

    1. 压缩率(0-3): 布林带宽低位 + 百日地量
    2. 加速度(0-3): 推调比趋势 + 回调深度趋势
    3. 周期锁定(0-4): MACD dif方向一致 + 信号质量同步

    总分0-10 → 绿灯(>=7) 黄灯(4-6) 红灯(0-3)
    """
    PERIODS = ['min5', 'min15', 'min30', 'min60', 'daily']
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

            median_w = sorted(widths)[len(widths)//2]
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
    sub_rows = read_csv(code, sub_p)
    if sub_rows and len(sub_rows) >= 30:
        lines = [safe_float(r.get('trend_line', 0)) for r in sub_rows
                 if safe_float(r.get('trend_line', 0)) > 0]
        if len(lines) >= 30:
            peaks = _wave_peaks(lines)
            neg = [-v for v in lines]
            valley_idxs = _wave_peaks(neg)
            events = [(p, 'p', lines[p]) for p in peaks] + \
                     [(v, 'v', lines[v]) for v in valley_idxs]
            events.sort(key=lambda x: x[0])
            min_wave = (max(lines) - min(lines)) * 0.08
            filt = [events[0]]
            for i in range(1, len(events)):
                if abs(events[i][2] - filt[-1][2]) >= min_wave:
                    filt.append(events[i])
            rises, falls = [], []
            for i in range(len(filt) - 1):
                if filt[i][1] == 'v' and filt[i+1][1] == 'p':
                    rises.append({'len': filt[i+1][0] - filt[i][0],
                                  'h': filt[i+1][2] - filt[i][2]})
                elif filt[i][1] == 'p' and filt[i+1][1] == 'v':
                    falls.append({'len': filt[i+1][0] - filt[i][0],
                                  'd': filt[i][2] - filt[i+1][2]})
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
                    sc['acceleration'] += 1; acc_items += 1
                    info.append(f'加速:推幅↑({avg_re:.1f}→{avg_rl:.1f})')
                if avg_fl < avg_fe * 0.8:
                    sc['acceleration'] += 1; acc_items += 1
                    info.append(f'加速:调时↓({avg_fe:.0f}→{avg_fl:.0f}K)')
                depths = []
                for i in range(min(len(falls), len(rises))):
                    if rises[i]['h'] > 0:
                        depths.append(falls[i]['d'] / rises[i]['h'] * 100)
                if depths:
                    early_d = sum(depths[:len(depths)//2]) / max(len(depths)//2, 1)
                    late_d = sum(depths[len(depths)//2:]) / max(len(depths)-len(depths)//2, 1)
                    if late_d < early_d * 0.7:
                        sc['acceleration'] += 1; acc_items += 1
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
        rows = read_csv(code, p)
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
        light = '🟢 绿灯'
        conclusion = '指数级条件成熟'
    elif total >= 4:
        light = '🟡 黄灯'
        conclusion = '部分条件具备'
    else:
        light = '🔴 红灯'
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
# 第四层扩展 v3.8: 缠论结构分析 + 大盘系数权重
# ============================================================



def _find_local_extremes(values, window=2, find_peaks=True):
    """找局部高点(peaks)或低点(valleys)，用于缠论分型识别"""
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




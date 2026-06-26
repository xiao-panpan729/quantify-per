# -*- coding: utf-8 -*-
"""
cycle_engine 指标层 — 排列熵(结构状态) / 位置判断 / 趋势评分 / 信号质量 / 锚点
"""
import math
from .utils import safe_float, read_csv, SNAPSHOT_DIR
from .constants import Direction, RhythmVerdict

from signal_engine import _permutation_entropy


def analyze_trend_pe(raw_rows, lookback=60):
    """
    对趋势线做排列熵分析，检测结构状态。

    v3: 优先读取 CSV 中已计算的 PE 轨迹列（pe/pe_level/pe_chg_5），
    对 PE 时间序列做轨迹分析（看熵的走势而非前后两半对比）；
    若 PE 列缺失则回退到旧版 front/back 两半计算。

    输出字段（旧版兼容 + 新版轨迹补充）:
      pe_front / pe_back / pe_ratio: 旧版兼容，从 PE 轨迹推算
      pe_level / pe_phase / pe_velocity: 沿用旧版状态机标签
      pe_trajectory: 轨迹详情（新）
    """
    default = {'pe_front': 0.5, 'pe_back': 0.5, 'pe_ratio': 1.0,
               'pe_level': 'mid', 'pe_phase': '数据不足', 'pe_velocity': '--',
               'trending': False, 'label': '数据不足',
               'tl_dir': '--',
               'pe_trajectory': None}

    if not raw_rows or len(raw_rows) < 20:
        return default

    # ── 尝试读 PE 轨迹 ──
    pe_series = []
    pe_levels = []
    for r in raw_rows:
        p = r.get('pe', None)
        if p is None or p == '':
            continue
        try:
            pe_series.append((float(p), r.get('pe_level', '')))
        except (ValueError, TypeError):
            continue

    if len(pe_series) >= 20:
        return _analyze_pe_from_trajectory(raw_rows, pe_series, lookback)

    # ── 回退: 旧版 front/back 两半计算 ──
    return _analyze_pe_legacy(raw_rows, lookback)


def _analyze_pe_from_trajectory(raw_rows, pe_series, lookback=60):
    """
    从 PE 轨迹分析结构状态。
    pe_series: list[(pe_value, pe_level_str)]
    """
    vals = [p[0] for p in pe_series]
    levels = [p[1] for p in pe_series]

    current_pe = vals[-1]
    current_level = levels[-1] if levels else 'mid'

    # PE 变化
    pe_5_ago = vals[-5] if len(vals) >= 5 else vals[0]
    pe_10_ago = vals[-10] if len(vals) >= 10 else vals[0]
    pe_20_ago = vals[-20] if len(vals) >= 20 else vals[0]

    chg_5 = current_pe - pe_5_ago
    chg_20 = current_pe - pe_20_ago

    # 趋势线方向
    trend_vals = []
    for r in raw_rows[-30:]:
        tv = r.get('trend_line', None)
        if tv is not None and tv != '':
            f_tv = safe_float(tv)
            if f_tv > 0:
                trend_vals.append(f_tv)
    if len(trend_vals) >= 10:
        half = len(trend_vals) // 2
        tl_rising = sum(trend_vals[half:]) / (len(trend_vals) - half) > sum(trend_vals[:half]) / half
    else:
        tl_rising = False

    # ── 变化烈度 ──
    abs_chg = abs(chg_5)
    if abs_chg > 0.10:
        velo = 'rapid'
    elif abs_chg > 0.05:
        velo = 'strong'
    elif abs_chg > 0.02:
        velo = 'mild'
    else:
        velo = 'stable'

    # ── 阶段标签（状态机，基于 PE 轨迹）──
    # 降熵 = 结构趋于有序, 升熵 = 结构趋于溃散
    falling = chg_5 < -0.02
    rising = chg_5 > 0.02
    stable = not falling and not rising

    pe_level = current_level if current_level else ('high' if current_pe > 0.70 else ('low' if current_pe < 0.40 else 'mid'))

    if falling and pe_level == 'high':
        pe_phase = '方向形成中'
        trending = True
    elif falling and pe_level == 'mid' and abs_chg > 0.05:
        if tl_rising:
            pe_phase = '结构上破'
        else:
            pe_phase = '结构下破'
        trending = True
    elif falling and pe_level == 'mid':
        pe_phase = '顺向蓄力'
        trending = True
    elif falling and pe_level == 'low' and abs_chg > 0.05:
        pe_phase = '蓄力压缩'
        trending = True
    elif falling and pe_level == 'low':
        pe_phase = '趋势锁定'
        trending = True
    elif stable and pe_level == 'low':
        pe_phase = '趋势延续'
        trending = False
    elif rising and pe_level == 'low':
        pe_phase = '趋势松动'
        trending = False
    elif rising and pe_level in ('mid', 'high') and abs_chg > 0.05:
        pe_phase = '趋势衰减' if pe_level == 'mid' else '无序放大'
        trending = False
    elif rising and pe_level in ('mid', 'high'):
        pe_phase = '趋势衰减' if pe_level == 'mid' else '无序放大'
        trending = False
    elif stable and pe_level == 'high':
        pe_phase = '无序震荡'
        trending = False
    elif stable and pe_level == 'mid':
        pe_phase = '方向不明'
        trending = False
    else:
        pe_phase = '过渡'
        trending = False

    # 最近20根PE的最小/最大值
    recent_vals = vals[-20:]
    pe_min_20 = min(recent_vals)
    pe_max_20 = max(recent_vals)

    # 轨迹方向词
    if falling:
        traj_dir = 'falling'
    elif rising:
        traj_dir = 'rising'
    else:
        traj_dir = 'stable'

    return {
        'pe_front': round(pe_20_ago, 4),
        'pe_back': round(current_pe, 4),
        'pe_ratio': round(current_pe / pe_20_ago if pe_20_ago > 0 else 1.0, 4),
        'pe_level': pe_level,
        'pe_phase': pe_phase,
        'pe_velocity': velo,
        'trending': trending,
        'label': pe_phase,
        'tl_dir': '↑' if tl_rising else '↓',
        'pe_trajectory': {
            'current': round(current_pe, 4),
            'chg_5': round(chg_5, 4),
            'chg_20': round(chg_20, 4),
            'min_20': round(pe_min_20, 4),
            'max_20': round(pe_max_20, 4),
            'direction': traj_dir,
            'velocity': velo,
        },
    }


def _analyze_pe_legacy(raw_rows, lookback=60):
    """旧版 front/back 两半 PE 计算（PE 列缺失时回退使用）"""
    recent = raw_rows[-lookback:]
    trend_vals = []
    for r in recent:
        tv = r.get('trend_line', None)
        if tv is not None and tv != '' and safe_float(tv) > 0:
            trend_vals.append(safe_float(tv))

    if len(trend_vals) < lookback * 0.5:
        return {'pe_front': 0.5, 'pe_back': 0.5, 'pe_ratio': 1.0,
                'pe_level': 'mid', 'pe_phase': '数据不足', 'pe_velocity': '--',
                'trending': False, 'label': '数据不足', 'tl_dir': '--',
                'pe_trajectory': None}

    half = len(trend_vals) // 2
    front = trend_vals[:half]
    back = trend_vals[half:]

    pe_front = _permutation_entropy(front, m=3)
    pe_back = _permutation_entropy(back, m=3)
    pe_ratio = pe_back / pe_front if pe_front > 0 else 1.0

    front_tl = sum(trend_vals[:half]) / half if half > 0 else 0
    back_tl = sum(trend_vals[half:]) / (len(trend_vals) - half) if len(trend_vals) > half else 0
    tl_rising = back_tl > front_tl

    if pe_back > 0.70:
        pe_level = 'high'
    elif pe_back < 0.40:
        pe_level = 'low'
    else:
        pe_level = 'mid'

    if pe_ratio < 0.70:
        velo = 'rapid'
    elif pe_ratio < 0.85:
        velo = 'strong'
    elif pe_ratio < 0.95:
        velo = 'mild'
    elif pe_ratio <= 1.05:
        velo = 'stable'
    elif pe_ratio < 1.20:
        velo = 'mild_rev'
    elif pe_ratio < 1.50:
        velo = 'strong_rev'
    else:
        velo = 'rapid_rev'

    if pe_level == 'high' and pe_ratio < 0.95:
        pe_phase = '方向形成中'; trending = True
    elif pe_level == 'mid' and pe_ratio < 0.85:
        pe_phase = '结构突破' if tl_rising else '逆向崩退'; trending = True
    elif pe_level == 'mid' and pe_ratio < 0.95:
        pe_phase = '顺向蓄力'; trending = True
    elif pe_level == 'low' and pe_ratio < 0.85:
        pe_phase = '蓄力压缩'; trending = True
    elif pe_level == 'low' and pe_ratio < 0.95:
        pe_phase = '趋势锁定'; trending = True
    elif pe_level == 'low' and pe_ratio <= 1.05:
        pe_phase = '趋势延续'; trending = False
    elif pe_level == 'low' and pe_ratio > 1.05:
        pe_phase = '趋势松动'; trending = False
    elif pe_level == 'mid' and pe_ratio > 1.05:
        pe_phase = '趋势衰减'; trending = False
    elif pe_level == 'high' and pe_ratio > 1.05:
        pe_phase = '无序放大'; trending = False
    elif pe_level == 'high':
        pe_phase = '无序震荡'; trending = False
    elif pe_level == 'mid':
        pe_phase = '方向不明'; trending = False
    else:
        pe_phase = '过渡'; trending = False

    return {
        'pe_front': round(pe_front, 4),
        'pe_back': round(pe_back, 4),
        'pe_ratio': round(pe_ratio, 4),
        'pe_level': pe_level,
        'pe_phase': pe_phase,
        'pe_velocity': velo,
        'trending': trending,
        'label': pe_phase,
        'tl_dir': '↑' if tl_rising else '↓',
        'pe_trajectory': None,
    }


# ============================================================
# 第一层: 价格位置判断
# ============================================================

def judge_position(daily_rows):
    """
    判断日线价格在 EXPMA 体系中的位置
    
    用 EXPMA12(白线) 和 EXPMA50(黄线) 作为价格锚点:
      - 高位区: 价格在 EXPMA12 之上 (强势拉升/加速区)
      - 中位区: 价格在 EXPMA12 和 EXPMA50 之间 (正常波动区)
      - 低位区: 价格在 EXPMA50 之下 (弱势/超跌区)
    
    结合 BB 中轨辅助判断极端位置
    
    Returns:
        dict: {zone, label, description, risk_level}
    """
    if not daily_rows:
        return {'zone': 'unknown', 'label': '未知', 'description': '无日线数据', 'risk_level': 'high'}

    last = daily_rows[-1]
    close = safe_float(last.get('close', 0))
    expma12 = safe_float(last.get('expma12', 0))
    expma50 = safe_float(last.get('expma50', 0))
    bb_mid = safe_float(last.get('bb_ma221', 0))
    bb_red = safe_float(last.get('bb_red_line', 0))

    if not close or not expma12 or not expma50:
        return {'zone': 'unknown', 'label': '数据不足', 'description': '', 'risk_level': 'high'}

    # 偏离白线/黄线的幅度
    deviation_from_white = (close - expma12) / expma12 * 100 if expma12 else 0
    deviation_from_yellow = (close - expma50) / expma50 * 100 if expma50 else 0

    # 用绝对偏离判断，不看 relative_pos（空头排列时相对位置会翻转）
    if deviation_from_yellow > 8:
        zone = 'high'
        label = '高位加速区'
        description = f'价格远超EXPMA50(+{deviation_from_yellow:.0f}%)，加速拉升中'
        risk_level = 'critical'
    elif deviation_from_yellow > 3 and deviation_from_white > 0:
        zone = 'high'
        label = '强势高位'
        description = f'价格在EXPMA50上方(+{deviation_from_yellow:.0f}%)，强势运行'
        risk_level = 'medium'
    elif abs(deviation_from_white) < 1.5:
        zone = 'mid'
        label = '白线附近'
        description = '价格贴近EXPMA12，正常波动中枢'
        risk_level = 'low'
    elif deviation_from_yellow > -2:
        zone = 'mid'
        label = '中位区'
        description = f'价格在EXPMA12与EXPMA50之间'
        risk_level = 'low'
    elif deviation_from_yellow > -6:
        zone = 'low'
        label = '弱势低位'
        description = f'价格在EXPMA50下方({deviation_from_yellow:.0f}%)，弱势运行'
        risk_level = 'high'
    else:
        zone = 'low'
        label = '超跌深坑'
        description = f'价格远低于EXPMA50({deviation_from_yellow:.0f}%)，严重超跌'
        risk_level = 'critical'

    # BB 中轨辅助
    if bb_mid and zone == 'low' and close < bb_mid * 0.85:
        label = 'BB下轨超跌'
        description = '价格跌破BB中轨15%+，极度超跌区域'
        risk_level = 'critical'

    return {
        'zone': zone,
        'label': label,
        'description': description,
        'risk_level': risk_level,
        'close': close,
        'expma12': expma12,
        'expma50': expma50,
        'deviation_white_pct': round(deviation_from_white, 1),
        'deviation_yellow_pct': round(deviation_from_yellow, 1),
    }


# ============================================================
# 第二层: 趋势方向判断
# ============================================================

def judge_trend(code, daily_rows, daily_net_score=0.0, min30_rows=None, min60_rows=None):
    """
    0-14 评分体系判断趋势方向

    MACD: 0~4分（0轴锚定·位置+交叉解耦）
    MA排列: 0~6分
    日线闭环: 0~4分（波段累积扣分制·来时路, 含30/60共振）

    总分 0~14 → 方向:
      13-14: bullish    10-12: bullish_bias
      7-9: neutral      4-6: bearish_bias
      0-3: bearish
    """
    if not daily_rows:
        return {'direction': 'unknown', 'label': '无数据', 'confidence': 0}

    last = daily_rows[-1]
    close = safe_float(last.get('close', 0))
    expma12 = safe_float(last.get('expma12', 0))
    expma50 = safe_float(last.get('expma50', 0))
    macd_dif = safe_float(last.get('macd_dif', 0))
    macd_dea = safe_float(last.get('macd_dea', 0))

    details = []

    # ── MACD: 0~4分（0轴锚定·位置+交叉解耦）──
    # 位置分(铁律): dif>0=+1, dea>0=+1
    # 交叉分:
    #   0轴上(新生):    金叉+2→4  死叉+1→3
    #   0轴上(水下带上): 金叉+0.5→2.5  死叉0→2
    #   过渡态:         金叉+1→2  死叉0→1
    #   0轴下(长期深水): 金叉+1→1  死叉-1→0
    #   0轴下(1月内上过0轴): 金叉+1.5→1.5  (回踩再攻)
    macd_score = 2
    if close > 0 and macd_dif is not None and macd_dea is not None:
        pos_score = 0
        if macd_dif > 0: pos_score += 1
        if macd_dea > 0: pos_score += 1

        is_golden = macd_dif > macd_dea
        dif_above = macd_dif > 0
        dea_above = macd_dea > 0

        # ── 追溯最近一次金叉事件的来源（0轴上新生 / 水下带上）──
        golden_origin = 'earned'
        if dif_above and dea_above:
            for j in range(len(daily_rows)-1, max(0, len(daily_rows)-500), -1):
                prev_dif_j = safe_float(daily_rows[j-1].get('macd_dif', 0))
                prev_dea_j = safe_float(daily_rows[j-1].get('macd_dea', 0))
                cur_dif_j = safe_float(daily_rows[j].get('macd_dif', 0))
                cur_dea_j = safe_float(daily_rows[j].get('macd_dea', 0))
                if prev_dif_j <= prev_dea_j and cur_dif_j > cur_dea_j:
                    golden_origin = 'earned' if cur_dea_j > 0 else 'carried'
                    break
                if cur_dif_j < 0 and cur_dea_j < 0:
                    golden_origin = 'carried'
                    break

        # ── 0轴下金叉：检测1月内是否上过0轴（回踩再攻 vs 长期深水）──
        recently_above_zero = False
        if not dif_above and not dea_above and is_golden:
            lookback = min(20, len(daily_rows)-1)
            for j in range(len(daily_rows)-1, len(daily_rows)-1-lookback, -1):
                prev_dif_j = safe_float(daily_rows[j].get('macd_dif', 0))
                prev_dea_j = safe_float(daily_rows[j].get('macd_dea', 0))
                if prev_dif_j > 0 and prev_dea_j > 0:
                    recently_above_zero = True
                    break

        # ── 评分 ──
        if dif_above and dea_above:
            if is_golden:
                if golden_origin == 'carried':
                    cross_score = 0.5
                    macd_score = pos_score + cross_score
                    details.append(f'MACD金叉(0轴下带上·待确认) {macd_score:.1f}/4')
                else:
                    cross_score = 2
                    macd_score = pos_score + cross_score
                    details.append(f'MACD金叉(0轴上) {macd_score}/4')
            else:
                if golden_origin == 'carried':
                    cross_score = 0
                    macd_score = pos_score + cross_score
                    details.append(f'MACD死叉(0轴上·弱) {macd_score:.1f}/4')
                else:
                    cross_score = 1
                    macd_score = pos_score + cross_score
                    details.append(f'MACD死叉(0轴上) {macd_score}/4')
        elif not dif_above and not dea_above:
            if is_golden:
                if recently_above_zero:
                    cross_score = 1.5
                    macd_score = pos_score + cross_score
                    details.append(f'MACD金叉(0轴下·回踩再攻) {macd_score:.1f}/4')
                else:
                    cross_score = 1
                    macd_score = pos_score + cross_score
                    details.append(f'MACD金叉(0轴下) {macd_score}/4')
            else:
                cross_score = -1
                macd_score = max(0, pos_score + cross_score)
                details.append(f'MACD空头(0轴下) {macd_score}/4')
        else:
            if is_golden:
                cross_score = 1
                macd_score = pos_score + cross_score
                details.append(f'MACD金叉(过渡·单线上穿) {macd_score}/4')
            else:
                cross_score = 0
                macd_score = pos_score + cross_score
                details.append(f'MACD死叉(过渡·单线先破) {macd_score}/4')
    else:
        details.append('MACD未知')

    # ── MA排列: 0~6分（短线区拆价格+均线各0.5分，长线区整分）──
    chain_periods = [5, 10, 20, 60, 120, 250]
    ma_fields = {5: 'ma5', 10: 'ma10', 20: 'ma20', 60: 'ma60', 120: 'ma120', 250: 'ma250'}
    ma_vals = {}
    for period in chain_periods:
        v = safe_float(last.get(ma_fields[period], 0))
        if v > 0:
            ma_vals[period] = v

    ma_sub_score = 0.0
    sub_items = []

    # 5/10区: close>5MA(0.5) + 5MA>10MA(0.5)
    if ma_vals.get(5) and close > ma_vals[5]:
        ma_sub_score += 0.5
        sub_items.append('价>5MA')
    if ma_vals.get(5) and ma_vals.get(10) and ma_vals[5] > ma_vals[10]:
        ma_sub_score += 0.5
        sub_items.append('5>10')

    # 10/20区: close>10MA(0.5) + 10MA>20MA(0.5)
    if ma_vals.get(10) and close > ma_vals[10]:
        ma_sub_score += 0.5
        sub_items.append('价>10MA')
    if ma_vals.get(10) and ma_vals.get(20) and ma_vals[10] > ma_vals[20]:
        ma_sub_score += 0.5
        sub_items.append('10>20')

    # 20/60区: close>20MA(0.5) + 20MA>60MA(0.5)
    if ma_vals.get(20) and close > ma_vals[20]:
        ma_sub_score += 0.5
        sub_items.append('价>20MA')
    if ma_vals.get(20) and ma_vals.get(60) and ma_vals[20] > ma_vals[60]:
        ma_sub_score += 0.5
        sub_items.append('20>60')

    # 60/120区: 60MA>120MA(1.0)
    if ma_vals.get(60) and ma_vals.get(120) and ma_vals[60] > ma_vals[120]:
        ma_sub_score += 1.0
        sub_items.append('60>120')

    # 120/250区: 120MA>250MA(1.0)
    if ma_vals.get(120) and ma_vals.get(250) and ma_vals[120] > ma_vals[250]:
        ma_sub_score += 1.0
        sub_items.append('120>250')

    # 满分加成: 8个子项全满足 → +1
    if ma_sub_score >= 5.0:
        ma_sub_score += 1.0

    ma_score = ma_sub_score  # 保留浮点，不做 int() 舍入

    # 细节
    if ma_score >= 5:
        details.append(f'均线多头排列({",".join(sub_items)})')
    elif ma_score >= 3:
        details.append(f'均线偏多({",".join(sub_items) if sub_items else "无明显排列"})')
    elif ma_score > 0:
        details.append(f'均线偏弱(得分{ma_score}/6)')
    else:
        details.append('均线无序')

    # ── 日线闭环: 0~4分（波段累积扣分制·来时路）──
    # 每项独立追踪余额，扣分不超过该项累积。★卖为结构性扣分，直接计入。
    items = {}  # {name: balance}
    cycle_items = []

    # 找最近★卖作为锚点
    last_sell_idx = None
    for i in range(len(daily_rows) - 1, -1, -1):
        if str(daily_rows[i].get('sell_signal', '')).strip():
            last_sell_idx = i
            break

    # 累积参考点: ★卖之前那根K线（如无★卖则用最新）
    ref_row = daily_rows[last_sell_idx - 1] if last_sell_idx and last_sell_idx > 0 else last
    ref_close = safe_float(ref_row.get('close', 0))
    ref_e12 = safe_float(ref_row.get('expma12', 0))
    ref_e50 = safe_float(ref_row.get('expma50', 0))

    # --- 累积(★卖前的状态) ---
    # 1) EXPMA多头: 金叉期内=+2
    if ref_e12 and ref_e50 and ref_close > 0 and ref_e12 > ref_e50:
        items['EXPMA'] = 2.0
        cycle_items.append('EXPMA+2')
    elif ref_e12 and ref_e50:
        cycle_items.append('EXPMA空头')

    # 2) 60分钟次级别★买(★卖前240根内)
    if min60_rows:
        end_idx = len(min60_rows)
        if last_sell_idx is not None:
            sell_date = str(daily_rows[last_sell_idx].get('date', ''))[:10]
            for j in range(len(min60_rows) - 1, -1, -1):
                if str(min60_rows[j].get('date', ''))[:10] <= sell_date:
                    end_idx = j
                    break
        start_idx = max(0, end_idx - 240)
        buy60_count = sum(1 for r in min60_rows[start_idx:end_idx]
                         if str(r.get('buy_signal', '')).strip())
        if buy60_count > 0:
            bonus = min(buy60_count, 4) * 0.5
            items['60min★买'] = bonus
            cycle_items.append(f'60min★买×{buy60_count}+{bonus:.1f}')

    # 3) 30/60共振 — 扫描累积期内是否存在金叉共振
    if min30_rows and min60_rows:
        pre_end_30 = len(min30_rows)
        pre_end_60 = len(min60_rows)
        if last_sell_idx is not None:
            sell_date = str(daily_rows[last_sell_idx].get('date', ''))[:10]
            for j in range(len(min30_rows)):
                if str(min30_rows[j].get('date', ''))[:10] > sell_date:
                    pre_end_30 = j
                    break
            for j in range(len(min60_rows)):
                if str(min60_rows[j].get('date', ''))[:10] > sell_date:
                    pre_end_60 = j
                    break
        pre30_gold = any(
            safe_float(r.get('expma12', 0)) > safe_float(r.get('expma50', 0))
            for r in min30_rows[max(0, pre_end_30-200):pre_end_30]
        )
        pre60_gold = any(
            safe_float(r.get('expma12', 0)) > safe_float(r.get('expma50', 0))
            for r in min60_rows[max(0, pre_end_60-100):pre_end_60]
        )
        if pre30_gold and pre60_gold:
            items['30/60共振'] = 1.0
            cycle_items.append('30/60共振+1')

    # --- 扣分(来时路: 每项独立扣, 余额不低于0) ---
    if last_sell_idx is not None:
        # ★卖: 结构性扣分, 直接计入总分
        sell_count = sum(1 for r in daily_rows[last_sell_idx:]
                        if str(r.get('sell_signal', '')).strip())
        items['★卖'] = -1.0 * sell_count
        cycle_items.append(f'★卖×{sell_count}-{sell_count}')

        # 破白线 → 扣 EXPMA
        if expma12 and close < expma12 and 'EXPMA' in items:
            items['EXPMA'] = max(0, items['EXPMA'] - 0.5)
            cycle_items.append('破白线-0.5')

        # 破黄线 → 扣 EXPMA
        if expma50 and close < expma50 and 'EXPMA' in items:
            items['EXPMA'] = max(0, items['EXPMA'] - 0.5)
            cycle_items.append('破黄线-0.5')

        # EXPMA死叉 → 扣 EXPMA
        if expma12 and expma50 and expma12 < expma50 and 'EXPMA' in items:
            items['EXPMA'] = max(0, items['EXPMA'] - 0.5)
            cycle_items.append('EXPMA死叉-0.5')

        # 30/60死叉共振 — 无条件(无需日线死叉触发)
        if min30_rows and min60_rows and '30/60共振' in items:
            e30_12 = safe_float(min30_rows[-1].get('expma12', 999))
            e30_50 = safe_float(min30_rows[-1].get('expma50', 0))
            e60_12 = safe_float(min60_rows[-1].get('expma12', 999))
            e60_50 = safe_float(min60_rows[-1].get('expma50', 0))
            if e30_12 < e30_50 and e60_12 < e60_50:
                items['30/60共振'] = max(0, items['30/60共振'] - 1.0)
                cycle_items.append('30/60死叉共振-1')

    cycle_score = max(0, min(4, int(sum(items.values()) + 0.5)))  # 四舍五入

    if cycle_score >= 3:
        details.append(f'日线闭环强(余额:{dict(items)} {",".join(cycle_items)})')
    elif cycle_score >= 1:
        details.append(f'日线闭环弱(余额:{dict(items)} {",".join(cycle_items)})')
    else:
        details.append(f'日线闭环无(余额:{dict(items)} {",".join(cycle_items)})')

    # ── 总分 0~14 ──
    total_score = macd_score + ma_score + cycle_score

    if total_score >= 13:
        direction = Direction.BULLISH
        label = '上涨趋势'
    elif total_score >= 10:
        direction = Direction.BULLISH_BIAS
        label = '偏多震荡'
    elif total_score >= 7:
        direction = Direction.NEUTRAL
        label = '横盘震荡'
    elif total_score >= 4:
        direction = Direction.BEARISH_BIAS
        label = '偏空震荡'
    else:
        direction = Direction.BEARISH
        label = '下跌趋势'

    confidence = abs(total_score - 8) / 8 * 100

    # 评分操作建议区（基于回测验证）
    # 8-10: sweet_spot 顺势窗口 | 11+: fragile_high 虚高警示
    # 0-2: fragile_low 筑底观察 | 3-7: neutral 中性等待
    if total_score >= 11:
        zone_advice = 'fragile_high'
        zone_label = '虚高警示'
    elif total_score >= 8:
        zone_advice = 'sweet_spot'
        zone_label = '顺势窗口'
    elif total_score >= 3:
        zone_advice = 'neutral'
        zone_label = '中性等待'
    else:
        zone_advice = 'fragile_low'
        zone_label = '筑底观察'

    return {
        'direction': direction,
        'label': label,
        'confidence': round(confidence),
        'score': total_score,
        'macd_score': macd_score,
        'ma_score': ma_score,
        'cycle_score': cycle_score,
        'daily_net_score': daily_net_score,
        'zone_advice': zone_advice,
        'zone_label': zone_label,
        'details': details,
        'close': close,
        'macd_dif': macd_dif,
        'macd_dea': macd_dea,
    }


# ============================================================
# 第三层: 循环适配（在已知位置+方向下）
# ============================================================

def extract_anchors(rows):
    """提取 ★买/★卖 定位点"""
    anchors = []
    for r in rows:
        buy = r.get('buy_signal', '').strip()
        sell = r.get('sell_signal', '').strip()
        if buy:
            anchors.append({
                'type': 'buy',
                'ts': r.get('timestamp', ''),
                'close': safe_float(r.get('close', 0)),
                'cci': r.get('cci', '')[:8],
                'has_ema': bool(r.get('expma_cross', '').strip()),
                'has_ext': bool(r.get('cci_extreme', '').strip()),
                'has_div': bool(r.get('cci_divergence', '').strip()),
            })
        elif sell:
            anchors.append({
                'type': 'sell',
                'ts': r.get('timestamp', ''),
                'close': safe_float(r.get('close', 0)),
                'cci': r.get('cci', '')[:8],
                'has_ema': bool(r.get('expma_cross', '').strip()),
                'has_ext': bool(r.get('cci_extreme', '').strip()),
                'has_div': bool(r.get('cci_divergence', '').strip()),
            })
    return anchors



def price_effectiveness(anchors, raw_rows, look_forward=5):
    """
    计算 ★买/★卖 之后的价格有效性
    
    对每个锚点，看它之后 look_forward 根 K 线的价格变化
    ★买后价格应该涨，★卖后价格应该跌
    ★买后涨得多 + ★卖后跌得少 = 好标的
    
    Returns:
        dict: {buy_avg_pct, sell_avg_pct, buy_hit_rate, sell_hit_rate, score}
    """
    if not anchors or not raw_rows:
        return {'buy_avg_pct': 0, 'sell_avg_pct': 0,
                'buy_hit_rate': 0, 'sell_hit_rate': 0, 'effectiveness': 0,
                'buy_samples': 0, 'sell_samples': 0}

    # 建立时间戳→价格映射
    ts_map = {}
    for i, r in enumerate(raw_rows):
        ts_map[r.get('timestamp', '')] = (i, safe_float(r.get('close', 0)))

    buy_gains = []
    sell_gains = []

    for a in anchors:
        ts = a['ts']
        if ts not in ts_map:
            continue
        idx, entry_close = ts_map[ts]
        if not entry_close:
            continue

        # 找锚点后 look_forward 根 K 线
        future_idx = min(idx + look_forward, len(raw_rows) - 1)
        if future_idx <= idx:
            continue
        exit_close = safe_float(raw_rows[future_idx].get('close', 0))
        if not exit_close:
            continue

        pct = (exit_close - entry_close) / entry_close * 100
        if a['type'] == 'buy':
            buy_gains.append(pct)
        else:
            sell_gains.append(pct)

    def avg(lst):
        return round(sum(lst) / len(lst), 1) if lst else 0

    def hit_rate(lst, expect_positive):
        if not lst:
            return 0
        correct = sum(1 for v in lst if v > 0) if expect_positive else sum(1 for v in lst if v < 0)
        return round(correct / len(lst) * 100)

    buy_avg = avg(buy_gains)
    sell_avg = avg(sell_gains)
    buy_hit = hit_rate(buy_gains, True)
    sell_hit = hit_rate(sell_gains, False)

    # 价格有效性评分
    effectiveness = 0
    if buy_avg > 0:
        effectiveness += min(buy_avg / 2, 2.5)  # 每2%涨1分，上限2.5
    if sell_avg < 0:
        effectiveness += min(abs(sell_avg) / 2, 1.5)  # 跌2%加1分，上限1.5
    effectiveness += buy_hit / 100  # 满分1
    effectiveness += sell_hit / 200  # 满分0.5
    effectiveness = round(effectiveness, 1)

    return {
        'buy_avg_pct': buy_avg,
        'sell_avg_pct': sell_avg,
        'buy_hit_rate': buy_hit,
        'sell_hit_rate': sell_hit,
        'effectiveness': effectiveness,
        'buy_samples': len(buy_gains),
        'sell_samples': len(sell_gains),
    }



def signal_quality(anchors, raw_rows, position, trend, lookback_klines=20, trend_pe=None):
    """
    第四层: 信号质量递进分析
    
    不是看"有没有信号"，而是看"信号递进到了哪个级别"
    
    四要素:
      1. ★买/★卖密集度 — 最近N根K线内出了几次定位点
      2. 金叉/死叉跟随速度 — ★买后多久出金叉(K线数)
      3. 底部/顶部价格方向 — 定位点低点是否抬升或下移
      4. 闭环完整性 — 定位点+金叉/死叉成对出现的次数
    
    不同趋势下权重不同:
      上涨(做多): ★买密集度 + 金叉跟随速度 + 底部价格抬升
      下跌(做空): ★卖密集度 + 死叉跟随速度 + 顶部价格下移
      震荡: 买卖都看，交替质量
    """
    if not anchors or not raw_rows:
        pe_info = None
        if trend_pe:
            pe_info = {
                'pe_front': trend_pe['pe_front'],
                'pe_back': trend_pe['pe_back'],
                'pe_ratio': trend_pe['pe_ratio'],
                'pe_level': trend_pe['pe_level'],
                'pe_phase': trend_pe['pe_phase'],
                'pe_velocity': trend_pe['pe_velocity'],
                'trending': trend_pe['trending'],
                'pe_label': trend_pe['label'],
                'tl_dir': trend_pe['tl_dir'],
            }
        return {'level': 'none', 'label': '无信号', 'details': [], 'trend_pe': pe_info}

    # 只看最近 N 根K线
    recent_rows = raw_rows[-lookback_klines:]
    row_count = len(recent_rows)

    # 在最近N根K线中找定位点和EMA交叉
    recent_buy_anchors = []
    recent_sell_anchors = []
    recent_golden = []  # 金叉位置
    recent_dead = []    # 死叉位置
    ts_to_idx = {}

    for i, r in enumerate(recent_rows):
        ts = r.get('timestamp', '')
        ts_to_idx[ts] = i

        buy = r.get('buy_signal', '').strip()
        sell = r.get('sell_signal', '').strip()
        ema = r.get('expma_cross', '').strip()
        close = safe_float(r.get('close', 0))

        if buy:
            recent_buy_anchors.append({'ts': ts, 'close': close, 'idx': i})
        elif sell:
            recent_sell_anchors.append({'ts': ts, 'close': close, 'idx': i})

        if '金叉' in ema:
            recent_golden.append({'ts': ts, 'close': close, 'idx': i})
        elif '死叉' in ema:
            recent_dead.append({'ts': ts, 'close': close, 'idx': i})

    # MA5/MA10 交叉检测（直接从CSV读，csv已有ma5/ma10字段）
    ma5_vals = [safe_float(r.get('ma5', 0)) for r in recent_rows]
    ma10_vals = [safe_float(r.get('ma10', 0)) for r in recent_rows]

    recent_ma5_golden = []  # MA5上穿MA10
    recent_ma5_dead = []    # MA5下穿MA10
    for i in range(10, row_count):
        if ma5_vals[i] > 0 and ma10_vals[i] > 0 and ma5_vals[i-1] > 0 and ma10_vals[i-1] > 0:
            if ma5_vals[i-1] <= ma10_vals[i-1] and ma5_vals[i] > ma10_vals[i]:
                recent_ma5_golden.append({'idx': i})
            elif ma5_vals[i-1] >= ma10_vals[i-1] and ma5_vals[i] < ma10_vals[i]:
                recent_ma5_dead.append({'idx': i})

    # ── 趋势线数据（一次性计算，买侧+卖侧共用） ──
    trend_vals = [safe_float(r.get('trend_line', 50)) for r in recent_rows]

    # ========== 波段起止检测（按波算密度，不按全窗口） ==========
    # 买侧波起：最近一个★卖或死叉之后（"这一段下跌"的起点）
    buy_wave_start = 0
    for i, r in enumerate(recent_rows):
        if r.get('sell_signal', '').strip() or '死叉' in r.get('expma_cross', ''):
            buy_wave_start = i + 1
    # 卖侧波起：最近一个★买或金叉之后（"这一段上涨"的起点）
    sell_wave_start = 0
    for i, r in enumerate(recent_rows):
        if r.get('buy_signal', '').strip() or '金叉' in r.get('expma_cross', ''):
            sell_wave_start = i + 1

    details = []

    # --- 做多侧分析 ---
    direction = trend.get('direction', 'bullish')  # 默认上涨
    buy_level = 0  # 0=无 1=普通 2=加强 3=最强
    buy_details = []

    if recent_buy_anchors:
        # 1. ★买密集度（按波段：波起之后有几次★买）
        wave_buys = [a for a in recent_buy_anchors if a['idx'] >= buy_wave_start]
        buy_count = len(wave_buys)
        if buy_count >= 3:
            density_label = f'★买密集({buy_count}次/波)'
            buy_level += 1.5
        elif buy_count == 2:
            density_label = f'★买连续({buy_count}次/波)'
            buy_level += 1.0
        elif buy_count == 1:
            density_label = f'★买单次'
            buy_level += 0.5
        else:
            density_label = '★买无(波内)'
            buy_level += 0
        buy_details.append(density_label)

        # 2. 金叉跟随速度: ★买后到最近金叉的距离
        best_follow = None
        for ba in recent_buy_anchors:
            for gc in recent_golden:
                if gc['idx'] > ba['idx']:
                    gap = gc['idx'] - ba['idx']
                    if best_follow is None or gap < best_follow:
                        best_follow = gap
        if best_follow is not None:
            if best_follow <= 5:
                follow_label = f'历史金叉跟随快(gap={best_follow})'
                buy_level += 1.5
            elif best_follow <= 12:
                follow_label = f'历史金叉跟随正常(gap={best_follow})'
                buy_level += 1.0
            else:
                follow_label = f'历史金叉跟随慢(gap={best_follow})'
                buy_level += 0.3
            buy_details.append(follow_label)

        # 3. 底部价格抬升: 每个★买相比前一个★买的低点提高
        if len(recent_buy_anchors) >= 2:
            raises = 0
            total = 0
            for i in range(1, len(recent_buy_anchors)):
                prev = recent_buy_anchors[i-1]['close']
                curr = recent_buy_anchors[i]['close']
                if prev and curr and prev > 0:
                    total += 1
                    if curr >= prev:
                        raises += 1
            if total > 0:
                raise_pct = raises / total * 100
                if raise_pct >= 80:
                    buy_details.append(f'底部抬升({raise_pct:.0f}%)')
                    buy_level += 1.0
                elif raise_pct >= 50:
                    buy_details.append(f'底部持平({raise_pct:.0f}%抬升)')
                else:
                    buy_details.append(f'底部下移({100-raise_pct:.0f}%下移)')

        # 4. 闭环成对: ★买+金叉 成对出现的次数
        pairs = 0
        for ba in recent_buy_anchors:
            for gc in recent_golden:
                if gc['idx'] > ba['idx']:
                    pairs += 1
                    break
        if pairs >= 3:
            buy_details.append(f'闭环{pairs}对(密集)')
            buy_level += 1.0
        elif pairs >= 2:
            buy_details.append(f'闭环{pairs}对')
            buy_level += 0.5
        elif pairs >= 1:
            buy_details.append(f'闭环{pairs}对')
            buy_level += 0.3

        # 5. MA5/10金叉确认（★买后→EXPMA金叉前的短期趋势确认）
        if recent_ma5_golden:
            first_ma5 = None
            for ba in recent_buy_anchors:
                for gc in recent_ma5_golden:
                    if gc['idx'] > ba['idx']:
                        if first_ma5 is None or gc['idx'] < first_ma5['idx']:
                            first_ma5 = gc
                        break
            first_expma = None
            for ba in recent_buy_anchors:
                for gc in recent_golden:
                    if gc['idx'] > ba['idx']:
                        if first_expma is None or gc['idx'] < first_expma['idx']:
                            first_expma = gc
                        break
            if first_ma5 is not None:
                if first_expma is None:
                    buy_level += 1.0
                    buy_details.append('MA5/10金叉确认(无EXPMA)')
                elif first_ma5['idx'] <= first_expma['idx']:
                    buy_level += 1.2
                    buy_details.append('MA5/10→EXPMA递进')
                else:
                    buy_level += 0.3
                    buy_details.append('MA5/10金叉滞后')

        # 6. 排列熵确认：降熵=有序→方向形成，结构突破/方向酝酿
        if trend_pe:
            if trend_pe['trending'] and trend_pe['pe_ratio'] < 0.85:
                buy_level += 1.5
                buy_details.append(f'★结构上破(熵={trend_pe["pe_back"]:.2f})')
            elif trend_pe['trending']:
                buy_level += 1.0
                buy_details.append(f'方向形成中(熵={trend_pe["pe_back"]:.2f})')
            elif trend_pe['pe_ratio'] > 1.15:
                # 升熵=回归震荡，不扣分，但标记
                buy_details.append(f'震荡回归(熵={trend_pe["pe_back"]:.2f})')

            # 加入熵原始数据用于输出
            buy_details.append(f'熵值({trend_pe["pe_front"]:.2f}→{trend_pe["pe_back"]:.2f})')

        # 7. 量能确认维度（买侧）
        if recent_buy_anchors:
            direction = trend['direction']
            has_oversold = any(t < 10 for t in trend_vals)

            if has_oversold:
                # 超卖区(趋势线<10)量能三维确认，各0.5共1.5
                vol_score = 0.0
                vol_parts = []

                # 维度1: 百日地量出现在★买附近 (0.5)
                has_百地 = any(safe_float(r.get('vol_llv100', 0)) >= 1 for r in recent_rows)
                if has_百地:
                    vol_score += 0.5
                    vol_parts.append('百日地量')

                # 维度2: 地量堆出现在波内 (0.5)
                has_堆 = any(safe_float(r.get('vol_堆', 0)) >= 1 for r in recent_rows)
                if has_堆:
                    vol_score += 0.5
                    vol_parts.append('地量堆')

                # 维度3: 地量堆量不超百日地量20% (0.5)
                stack_vols = [float(r.get('volume', 0)) for r in recent_rows
                              if safe_float(r.get('vol_堆', 0)) >= 1 and float(r.get('volume', 0)) > 0]
                llv100_vols = [float(r.get('volume', 0)) for r in recent_rows
                               if safe_float(r.get('vol_llv100', 0)) >= 1 and float(r.get('volume', 0)) > 0]
                if stack_vols and llv100_vols:
                    avg_stack = sum(stack_vols) / len(stack_vols)
                    avg_llv100 = sum(llv100_vols) / len(llv100_vols)
                    if avg_stack <= avg_llv100 * 1.2:
                        vol_score += 0.5
                        vol_parts.append(f'缩量确认({avg_stack/avg_llv100:.2f}x)')

                if vol_score > 0:
                    buy_level += vol_score
                    buy_details.append(f'超卖量能{"+".join(vol_parts)}({vol_score:.1f})')
            else:
                # 非超卖区：简单放量/缩量检查
                has_突放 = any(safe_float(r.get('vol_突放', 0)) >= 1 for r in recent_rows)
                if has_突放 and direction in Direction.BULLISH_DIRS:
                    buy_level += 1.0
                    buy_details.append('放量突破确认')
                else:
                    shrinks = sum(1 for r in recent_rows if safe_float(r.get('vol_缩50', 0)) >= 1)
                    if shrinks >= 2 and direction in Direction.BULLISH_DIRS:
                        buy_level += 0.5
                        buy_details.append('回调缩量(调整健康)')
                    else:
                        grads = sum(1 for r in recent_rows if safe_float(r.get('vol_梯度升', 0)) >= 1)
                        if grads >= 3:
                            buy_level += 0.3
                            buy_details.append('梯度放量')

            # 8. 趋势线上穿0（极端超卖反转）
            for i in range(1, len(trend_vals)):
                if trend_vals[i-1] <= 0 < trend_vals[i]:
                    buy_level += 0.8
                    buy_details.append('趋势线上穿0(极端反转)')
                    break

    # --- 做空侧分析 ---
    sell_level = 0
    sell_details = []

    if recent_sell_anchors:
        wave_sells = [a for a in recent_sell_anchors if a['idx'] >= sell_wave_start]
        sell_count = len(wave_sells)
        if sell_count >= 3:
            sell_details.append(f'★卖密集({sell_count}次/波)')
            sell_level += 1.5
        elif sell_count == 2:
            sell_details.append(f'★卖连续({sell_count}次/波)')
            sell_level += 1.0
        elif sell_count == 1:
            sell_details.append(f'★卖单次')
            sell_level += 0.5
        else:
            sell_details.append('★卖无(波内)')
            sell_level += 0

        best_follow = None
        for sa in recent_sell_anchors:
            for dc in recent_dead:
                if dc['idx'] > sa['idx']:
                    gap = dc['idx'] - sa['idx']
                    if best_follow is None or gap < best_follow:
                        best_follow = gap
        if best_follow is not None:
            if best_follow <= 5:
                sell_details.append(f'历史死叉跟随快(gap={best_follow})')
                sell_level += 1.5
            elif best_follow <= 12:
                sell_details.append(f'历史死叉跟随正常(gap={best_follow})')
                sell_level += 1.0
            else:
                sell_details.append(f'历史死叉跟随慢(gap={best_follow})')
                sell_level += 0.3

        if len(recent_sell_anchors) >= 2:
            drops = 0
            total = 0
            for i in range(1, len(recent_sell_anchors)):
                prev = recent_sell_anchors[i-1]['close']
                curr = recent_sell_anchors[i]['close']
                if prev and curr and prev > 0:
                    total += 1
                    if curr <= prev:
                        drops += 1
            if total > 0:
                drop_pct = drops / total * 100
                if drop_pct >= 80:
                    sell_details.append(f'顶部下移({drop_pct:.0f}%)')
                    sell_level += 1.0
                elif drop_pct >= 50:
                    sell_details.append(f'顶部持平({drop_pct:.0f}%下移)')
                else:
                    sell_details.append(f'顶部抬高({100-drop_pct:.0f}%抬高)')

        pairs = 0
        for sa in recent_sell_anchors:
            for dc in recent_dead:
                if dc['idx'] > sa['idx']:
                    pairs += 1
                    break
        if pairs >= 3:
            sell_details.append(f'闭环{pairs}对(密集)')
            sell_level += 1.0
        elif pairs >= 2:
            sell_details.append(f'闭环{pairs}对')
            sell_level += 0.5
        elif pairs >= 1:
            sell_details.append(f'闭环{pairs}对')
            sell_level += 0.3

        # 5. MA5/10死叉确认（★卖后→EXPMA死叉前的短期趋势确认）
        if recent_ma5_dead:
            first_ma5_d = None
            for sa in recent_sell_anchors:
                for dc in recent_ma5_dead:
                    if dc['idx'] > sa['idx']:
                        if first_ma5_d is None or dc['idx'] < first_ma5_d['idx']:
                            first_ma5_d = dc
                        break
            first_expma_d = None
            for sa in recent_sell_anchors:
                for dc in recent_dead:
                    if dc['idx'] > sa['idx']:
                        if first_expma_d is None or dc['idx'] < first_expma_d['idx']:
                            first_expma_d = dc
                        break
            if first_ma5_d is not None:
                if first_expma_d is None:
                    sell_level += 1.0
                    sell_details.append('MA5/10死叉确认(无EXPMA)')
                elif first_ma5_d['idx'] <= first_expma_d['idx']:
                    sell_level += 1.2
                    sell_details.append('MA5/10→EXPMA递进(空)')
                else:
                    sell_level += 0.3
                    sell_details.append('MA5/10死叉滞后')

        # 6. 排列熵确认（卖侧：降熵同样意味着方向形成）
        if trend_pe:
            if trend_pe['trending'] and trend_pe['pe_ratio'] < 0.85:
                sell_level += 1.5
                sell_details.append(f'★结构下破(熵={trend_pe["pe_back"]:.2f})')
            elif trend_pe['trending']:
                sell_level += 1.0
                sell_details.append(f'方向形成中(熵={trend_pe["pe_back"]:.2f})')
            elif trend_pe['pe_ratio'] > 1.15:
                sell_details.append(f'震荡回归(熵={trend_pe["pe_back"]:.2f})')
            sell_details.append(f'熵值({trend_pe["pe_front"]:.2f}→{trend_pe["pe_back"]:.2f})')

        direction = trend['direction']

        # 7. 量能确认维度（卖侧）
        direction = trend['direction']
        if recent_sell_anchors:
            has_overbought = any(t > 90 for t in trend_vals)

            if has_overbought:
                # 超买区(趋势线>90)量能三维确认，各0.5共1.5 ★卖侧负分
                vol_score_s = 0.0
                vol_parts_s = []

                # 维度7a': 百日高量出现在★卖附近 (0.5)
                has_百高 = any(safe_float(r.get('vol_hhv100', 0)) >= 1 for r in recent_rows)
                if has_百高:
                    vol_score_s += 0.5
                    vol_parts_s.append('百日高量')

                # 维度7b': 放量堆存在于波内 (0.5)
                has_放堆 = any(safe_float(r.get('vol_放堆', 0)) >= 1 for r in recent_rows)
                if has_放堆:
                    vol_score_s += 0.5
                    vol_parts_s.append('放量堆')

                # 维度7c': 高量均价 < 波内均价 → 越放量越跌 (0.5)
                all_vol_price = [(float(r.get('volume', 0)), float(r.get('close', 0)))
                                 for r in recent_rows if float(r.get('volume', 0)) > 0]
                if len(all_vol_price) >= 3:
                    all_vol_price.sort(key=lambda x: x[0], reverse=True)
                    top_n = max(3, len(all_vol_price) // 3)
                    high_vol_avg = sum(v[1] for v in all_vol_price[:top_n]) / top_n
                    wave_avg = sum(v[1] for v in all_vol_price) / len(all_vol_price)
                    if high_vol_avg < wave_avg:
                        vol_score_s += 0.5
                        vol_parts_s.append(f'放量下跌({high_vol_avg/wave_avg:.2f}x)')

                if vol_score_s > 0:
                    sell_level += vol_score_s
                    sell_details.append(f'超买量能{"+".join(vol_parts_s)}({vol_score_s:.1f})')
            else:
                # 非超买区：简单放量阴线检查
                has_放量阴 = any(
                    safe_float(r.get('vr5', 1.0)) > 1.5
                    and safe_float(r.get('close', 0)) < safe_float(r.get('open', 0))
                    for r in recent_rows
                )
                if has_放量阴:
                    sell_level += 0.8
                    sell_details.append('放量阴线(风险)')

            # 趋势线下穿100（极端超买反转）
            for i in range(1, len(trend_vals)):
                if trend_vals[i-1] >= 100 > trend_vals[i]:
                    sell_level += 0.8
                    sell_details.append('趋势线下穿100(极端反转)')
                    break

    # --- 根据趋势方向选择主分析侧 ---
    if direction in Direction.BULLISH_DIRS:
        details = buy_details
        if buy_level >= 8.0:
            label = '加强出击'
        elif buy_level >= 6.0:
            label = '出击信号'
        elif buy_level >= 4.0:
            label = '加强信号'
        elif buy_level >= 2.0:
            label = '普通信号'
        elif buy_level >= 1.0:
            label = '信号弱'
        else:
            label = '无信号'
        level = buy_level
        # 附带空头信息（做参考）
        if sell_details:
            details.append(f'[空侧参考: {" | ".join(sell_details)}]')

    elif direction in Direction.BEARISH_DIRS:
        details = sell_details
        if sell_level >= 8.0:
            label = '加强出击'
        elif sell_level >= 6.0:
            label = '出击信号'
        elif sell_level >= 4.0:
            label = '加强信号'
        elif sell_level >= 2.0:
            label = '普通信号'
        elif sell_level >= 1.0:
            label = '信号弱'
        else:
            label = '无信号'
        level = sell_level
        if buy_details:
            details.append(f'[多侧参考: {" | ".join(buy_details)}]')

    else:
        # 震荡看两侧
        effective_level = max(buy_level, sell_level)
        details = buy_details + sell_details
        if effective_level >= 8.0:
            label = '加强出击'
        elif effective_level >= 6.0:
            label = '出击信号'
        elif effective_level >= 4.0:
            label = '加强信号'
        elif effective_level >= 2.0:
            label = '普通信号'
        elif effective_level >= 1.0:
            label = '信号弱'
        else:
            label = '无信号'
        level = effective_level

    # 排列熵信息
    pe_info = None
    if trend_pe:
        pe_info = {
            'pe_front': trend_pe['pe_front'],
            'pe_back': trend_pe['pe_back'],
            'pe_ratio': trend_pe['pe_ratio'],
            'pe_level': trend_pe['pe_level'],
            'pe_phase': trend_pe['pe_phase'],
            'pe_velocity': trend_pe['pe_velocity'],
            'trending': trend_pe['trending'],
            'pe_label': trend_pe['label'],
            'tl_dir': trend_pe['tl_dir'],
        }
    return {
        'level': level,
        'label': label,
        'details': details,
        'buy_level': buy_level,
        'sell_level': sell_level,
        'net_score': buy_level - sell_level,
        'trend_pe': pe_info,
        'ema_cross_status': {
            'has_recent_golden': bool(recent_golden),
            'last_golden_idx': recent_golden[-1]['idx'] if recent_golden else -1,
            'golden_count': len(recent_golden),
            'has_recent_dead': bool(recent_dead),
            'last_dead_idx': recent_dead[-1]['idx'] if recent_dead else -1,
            'dead_count': len(recent_dead),
        } if recent_golden or recent_dead else None,
    }


# ============================================================
# 节奏完整性检查 — 在 30分钟(战术) + 日线(战略) 两个固定级别
# ============================================================

def check_rhythm_integrity(period_results, direction):
    """
    在固定级别(30分/日线)上判断节奏完整性。

    节奏线 = EXPMA12(白线): 价格日常行动基线
    旋律线 = EXPMA50(黄线): 趋势生死线

    上涨: 价格在白线上方 + 黄线未有效跌破 + 无死叉(最近交叉)
    下跌: 价格在白线下方 + 黄线未有效突破 + 无金叉(最近交叉)

    交叉判断看最近一次: 如果最近一次是金叉=节奏完整(涨)，最近一次是死叉=节奏破坏(涨)

    Returns:
      dict: {
        'tactical':  {'intact': bool, ...},  # 30分钟
        'strategic': {'intact': bool, ...},  # 日线
        'verdict': 'intact'|'tactical_broken'|'strategic_broken'|'fully_broken'
      }
    """
    bullish_dirs = Direction.BULLISH_DIRS
    bearish_dirs = Direction.BEARISH_DIRS
    is_bullish = direction in bullish_dirs

    def _check_one(period_key, label):
        pp = period_results.get(period_key) if period_results else None
        if not pp:
            return {'intact': True, 'rhythm_line_ok': True, 'melody_line_ok': True,
                    'cross_ok': True, 'note': '无数据，默认完整'}

        sq = pp.get('signal_quality')
        if not sq:
            return {'intact': True, 'rhythm_line_ok': True, 'melody_line_ok': True,
                    'cross_ok': True, 'note': '无信号质量数据'}

        ecs = sq.get('ema_cross_status') or {}

        # 交叉确认: 看最近一次交叉是什么
        # 涨: 最近一次是金叉=完整，最近一次是死叉=破坏
        # 跌: 最近一次是死叉=完整，最近一次是金叉=破坏
        has_golden = ecs.get('has_recent_golden', False)
        has_dead = ecs.get('has_recent_dead', False)
        last_golden = ecs.get('last_golden_idx', -1)
        last_dead = ecs.get('last_dead_idx', -1)

        if is_bullish:
            if has_dead and (last_dead > last_golden):
                # 最近交叉是死叉 → 节奏破坏
                cross_ok = False
            else:
                cross_ok = True  # 最近是金叉或无交叉=节奏完好
        else:
            if has_golden and (last_golden > last_dead):
                # 最近交叉是金叉 → 节奏破坏（对下跌趋势而言）
                cross_ok = False
            else:
                cross_ok = True

        # 节奏线和旋律线: 目前从结构走，价格关系后续补充精确比较
        rhythm_line_ok = True
        melody_line_ok = True

        intact = rhythm_line_ok and melody_line_ok and cross_ok

        return {
            'intact': intact,
            'rhythm_line_ok': rhythm_line_ok,
            'melody_line_ok': melody_line_ok,
            'cross_ok': cross_ok,
            'label': label,
            'cross_status': {
                'has_recent_golden': has_golden,
                'has_recent_dead': has_dead,
                'last_golden_idx': last_golden,
                'last_dead_idx': last_dead,
                'golden_count': ecs.get('golden_count', 0),
                'dead_count': ecs.get('dead_count', 0),
            }
        }

    tactical = _check_one('min30', '30分钟(战术)')
    strategic = _check_one('daily', '日线(战略)')

    # 判定
    if strategic['intact'] and tactical['intact']:
        verdict = RhythmVerdict.INTACT
    elif strategic['intact'] and not tactical['intact']:
        verdict = RhythmVerdict.TACTICAL_BROKEN
    elif not strategic['intact'] and tactical['intact']:
        verdict = RhythmVerdict.STRATEGIC_BROKEN
    else:
        verdict = RhythmVerdict.FULLY_BROKEN

    return {
        'tactical': tactical,
        'strategic': strategic,
        'verdict': verdict,
        'direction': direction,
    }


# ============================================================
# 共振扫描 — 破坏事件前后的5+15共振确认
# ============================================================

def scan_resonance(period_results, rhythm, direction):
    """
    扫描 5+15 分钟是否有同向共振闭环，用于增强/压制判断。

    节奏完整时: 检查同向共振 → 增强
    节奏破坏时: 检查反向共振 → 可能反转信号

    Returns:
      dict: {resonance_confirmed, resonance_score, side: 'buy'|'sell'|'mixed'}
    """
    min5 = (period_results or {}).get('min5')
    min15 = (period_results or {}).get('min15')

    min5_sq = min5.get('signal_quality') if min5 else None
    min15_sq = min15.get('signal_quality') if min15 else None

    m5_buy = min5_sq.get('buy_level', 0) if min5_sq else 0
    m5_sell = min5_sq.get('sell_level', 0) if min5_sq else 0
    m15_buy = min15_sq.get('buy_level', 0) if min15_sq else 0
    m15_sell = min15_sq.get('sell_level', 0) if min15_sq else 0

    bullish_dirs = Direction.BULLISH_DIRS
    bearish_dirs = Direction.BEARISH_DIRS
    is_bullish = direction in bullish_dirs
    is_bearish = direction in bearish_dirs

    verdict = rhythm.get('verdict', RhythmVerdict.INTACT)
    resonance_confirmed = False
    resonance_score = 0.0
    side = 'neutral'

    if verdict in RhythmVerdict.INTACT_OR_TACTICAL:
        # 节奏完整或仅战术破坏 → 检查同向共振
        if is_bullish:
            # 买侧共振: 5+15 同时有买信号
            if m5_buy >= 2.0 and m15_buy >= 2.0:
                resonance_confirmed = True
                resonance_score = min(m5_buy + m15_buy, 10.0)
                side = 'buy'
        elif is_bearish:
            # 卖侧共振: 5+15 同时有卖信号
            if m5_sell >= 2.0 and m15_sell >= 2.0:
                resonance_confirmed = True
                resonance_score = min(m5_sell + m15_sell, 10.0)
                side = 'sell'
    elif verdict in RhythmVerdict.STRATEGIC_OR_FULLY:
        # 战略破坏 → 检查是否反向共振（可能反转信号）
        if is_bullish:
            # 上涨趋势但战略破坏 → 检查卖共振
            if m5_sell >= 2.0 and m15_sell >= 2.0:
                resonance_confirmed = True
                resonance_score = min(m5_sell + m15_sell, 10.0)
                side = 'sell_reversal'
        elif is_bearish:
            # 下跌趋势但战略破坏 → 检查买共振
            if m5_buy >= 2.0 and m15_buy >= 2.0:
                resonance_confirmed = True
                resonance_score = min(m5_buy + m15_buy, 10.0)
                side = 'buy_reversal'

    return {
        'resonance_confirmed': resonance_confirmed,
        'resonance_score': resonance_score,
        'side': side,
        'details': f'5分买{m5_buy:.1f}/卖{m5_sell:.1f} + 15分买{m15_buy:.1f}/卖{m15_sell:.1f}',
    }

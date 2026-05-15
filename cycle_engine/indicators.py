# -*- coding: utf-8 -*-
"""
cycle_engine 指标层 — 排列熵 / 位置判断 / 趋势评分 / 信号质量 / 锚点
"""
import math
from .utils import safe_float, read_csv, SNAPSHOT_DIR

def _permutation_entropy(values, m=3, delay=1):
    """
    排列熵：度量时间序列的有序/无序程度

    参数:
        values: list[float]，趋势线序列（0-100）
        m: 嵌入维度，3 即每3个点一组看排列图案
        delay: 延迟步长，默认1

    返回:
        pe: 归一化排列熵 (0~1)
            1 = 完全随机/无序（震荡无序）
            0 = 完全有序/有方向（趋势明确）
    """
    n = len(values)
    if n < m * 2:
        return 0.5  # 数据不足，返回中性值

    # 切分子序列
    sub_seqs = []
    for i in range(n - (m - 1) * delay):
        seq = tuple(values[i + j * delay] for j in range(m))
        sub_seqs.append(seq)

    # 对每个子序列按大小排序，得到排列图案
    patterns = []
    for seq in sub_seqs:
        sorted_idx = tuple(sorted(range(m), key=lambda x: seq[x]))
        patterns.append(sorted_idx)

    # 统计每种图案的频率
    total = len(patterns)
    freq = {}
    for p in patterns:
        freq[p] = freq.get(p, 0) + 1

    # 香农熵
    pe = 0.0
    for count in freq.values():
        f = count / total
        pe -= f * math.log(f) if f > 0 else 0

    # 归一化到 [0,1]
    pe /= math.log(math.factorial(m))

    return pe


def analyze_trend_pe(raw_rows, lookback=60):
    """
    对趋势线做排列熵分析，检测循环结构是否正在被打破

    取最新 lookback 根K线的 trend_line，前半段→pe_front，后半段→pe_back

    **熵值绝对值分级**（pe_back 当前值）:
      > 0.70 = 高熵区（无序震荡）
      0.40~0.70 = 中熵区（过渡态）
      < 0.40 = 低熵区（高度有序/方向明确）

    **结合变化趋势** pe_ratio = pe_back / pe_front:
      高熵 + 平稳 → 高熵震荡（无序无方向）
      高熵 + 降熵 → 开始降熵（刚从无序转向有序）
      中熵 + 降熵 → 持续性降熵（已经在走方向）
      中熵 + 升熵 → 熵增中（方向在退化回震荡）
      低熵 + 降熵 → 极致压缩（方向极度明确）
      低熵 + 平稳 → 低熵锁定（方向维持但不再加速）
      低熵 + 升熵 → 触底回升（有序结构开始松动）

    **新增输出字段**:
      pe_level: 熵值绝对水平 high/mid/low
      pe_phase: 所处阶段标签（开始降熵/持续降熵/熵增中等）
      pe_velocity: 变化烈度（急速/显著/温和/平稳）
      trending: 降熵中=True, 升熵中=False
    """
    if not raw_rows or len(raw_rows) < lookback:
        return {'pe_front': 0.5, 'pe_back': 0.5, 'pe_ratio': 1.0,
                'pe_level': 'mid', 'pe_phase': '数据不足', 'pe_velocity': '--',
                'trending': False, 'label': '数据不足'}

    recent = raw_rows[-lookback:]
    trend_vals = []
    for r in recent:
        tv = r.get('trend_line', None)
        if tv is not None and tv != '' and safe_float(tv) > 0:
            trend_vals.append(safe_float(tv))

    if len(trend_vals) < lookback * 0.5:
        return {'pe_front': 0.5, 'pe_back': 0.5, 'pe_ratio': 1.0,
                'pe_level': 'mid', 'pe_phase': '数据不足', 'pe_velocity': '--',
                'trending': False, 'label': '数据不足'}

    half = len(trend_vals) // 2
    front = trend_vals[:half]
    back = trend_vals[half:]

    pe_front = _permutation_entropy(front, m=3)
    pe_back = _permutation_entropy(back, m=3)
    pe_ratio = pe_back / pe_front if pe_front > 0 else 1.0

    # ── 趋势线方向（用于标注降熵朝向）──
    half_len = len(trend_vals) // 2
    front_tl = sum(trend_vals[:half_len]) / half_len if half_len > 0 else 0
    back_tl = sum(trend_vals[half_len:]) / (len(trend_vals) - half_len) if len(trend_vals) > half_len else 0
    tl_rising = back_tl > front_tl  # 趋势线在上升

    # ── 熵值绝对水平 ──
    if pe_back > 0.70:
        pe_level = 'high'
    elif pe_back < 0.40:
        pe_level = 'low'
    else:
        pe_level = 'mid'

    # ── 变化烈度 ──
    if pe_ratio < 0.70:
        pe_velocity = '急速'
        velo = 'rapid'
    elif pe_ratio < 0.85:
        pe_velocity = '显著'
        velo = 'strong'
    elif pe_ratio < 0.95:
        pe_velocity = '温和'
        velo = 'mild'
    elif pe_ratio <= 1.05:
        pe_velocity = '平稳'
        velo = 'stable'
    elif pe_ratio < 1.20:
        pe_velocity = '温和'
        velo = 'mild_rev'
    elif pe_ratio < 1.50:
        pe_velocity = '显著'
        velo = 'strong_rev'
    else:
        pe_velocity = '急速'
        velo = 'rapid_rev'

    # ── 阶段标签（状态机）──
    # 方向词：只有"结构突破"用上破/下破（"破"字已含方向），其余标签去掉箭头
    dir_word = '上破' if tl_rising else '下破'

    if pe_level == 'high' and pe_ratio < 0.95:
        pe_phase = '方向形成中'  # 高熵区开始降熵=方向刚从无序中显现
        trending = True
    elif pe_level == 'mid' and pe_ratio < 0.85:
        pe_phase = f'结构{dir_word}'  # 中熵区快速降熵=结构正在被打破
        trending = True
    elif pe_level == 'mid' and pe_ratio < 0.95:
        pe_phase = '趋势强化'  # 中熵区温和降熵=方向在持续
        trending = True
    elif pe_level == 'low' and pe_ratio < 0.85:
        pe_phase = '蓄力压缩'  # 低熵区继续降=蓄力到极致
        trending = True
    elif pe_level == 'low' and pe_ratio < 0.95:
        pe_phase = '趋势锁定'  # 低熵区温和降=方向锁定
        trending = True
    elif pe_level == 'low' and pe_ratio <= 1.05:
        pe_phase = '趋势延续'  # 低熵平稳=有序结构保持
        trending = False
    elif pe_level == 'low' and pe_ratio > 1.05:
        pe_phase = '趋势松动'  # 低熵回升=有序结构开始松动
        trending = False
    elif pe_level == 'mid' and pe_ratio > 1.05:
        pe_phase = '趋势衰减'  # 中熵升熵=方向在退化
        trending = False
    elif pe_level == 'high' and pe_ratio > 1.05:
        pe_phase = '无序放大'  # 高熵继续升=无序在扩散
        trending = False
    elif pe_level == 'high':
        pe_phase = '无序震荡'  # 高熵平稳=持续无序
        trending = False
    elif pe_level == 'mid':
        pe_phase = '方向不明'  # 中熵平稳=没有明确方向
        trending = False
    else:
        pe_phase = '过渡'
        trending = False

    # 短标签（用于总览表）— 直接用阶段名，不加emoji前缀，文字本身已经说明一切
    short_label = pe_phase

    return {
        'pe_front': round(pe_front, 4),
        'pe_back': round(pe_back, 4),
        'pe_ratio': round(pe_ratio, 4),
        'pe_level': pe_level,
        'pe_phase': pe_phase,
        'pe_velocity': velo,
        'trending': trending,
        'label': short_label,
        'tl_dir': '↑' if tl_rising else '↓',
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

    # 计算价格相对位置
    # 用白线/黄线的价差做参考
    # 核心: 价格在黄线之下 = 低位，白线之上且偏离大 = 高位
    spread = abs(expma12 - expma50)
    if spread > 0.01:
        if expma12 > expma50:
            # 多头排列: 白线在上
            if close > expma12:
                relative_pos = 1.0 + (close - expma12) / spread
            elif close > expma50:
                relative_pos = (close - expma50) / (expma12 - expma50)
            else:
                relative_pos = -1.0  # 在黄线之下
        else:
            # 空头排列: 黄线在上，白线在下
            if close > expma50:
                relative_pos = (close - expma50) / spread + 1.5
            elif close > expma12:
                relative_pos = (close - expma12) / (expma50 - expma12) + 0.5
            else:
                relative_pos = -1.0  # 在白线之下
    else:
        relative_pos = 0

    # 偏离白线的幅度
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

def judge_trend(code, daily_rows, daily_buy_level=0):
    """
    0-16 评分体系判断趋势方向 — 带日线闭环 + EXPMA

    EXPMA: 0~2分
      - 2: expma12 > expma50 (白线在上)
      - 1: 粘合（差距 < 股价×0.5%）
      - 0: expma12 < expma50 (黄线在上)

    MACD: 0~4分
      - dif_ratio = |DIF| / close
      - clearly_off = dif_ratio > 0.01
      - 4: clearly_off + dif>0 + dif>dea (0轴上,强势多头)
      - 3: dif>0 + dif>dea (0轴上金叉,未完全远离)
      - 2: dif<0 + dif>dea (0轴下金叉,弱势) 或 默认
      - 1: dif<dea (死叉,不论上下)
      - 0: clearly_off + dif<0 + dif<dea (0轴下,强势空头)

    MA排列: 0~6分
      - 链式递进: 从5开始检查连续 short>long 到第几级断裂
      - 链长0→0, 1→1, 2→2, 3→3, 4→4, 5→6(完美排列奖励)
      - 5>10>20>60>120>250 全部顺序 = 满分6分

    日线闭环: 0~4分（只计买侧, daily_buy_level >= 4.0=4分, >=3.5=3分, >=3.0=2分）
      - 只取★买侧的闭环质量, 不取max(buy,sell)

    总分 0~16 → 方向:
      13-16: bullish    10-12: bullish_bias
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

    # ── EXPMA: 0~2分 ──
    expma_score = 1  # 默认粘合
    if expma12 and expma50 and close > 0:
        expma_gap = abs(expma12 - expma50) / close
        if close > expma12 > expma50 and expma_gap > 0.005:
            expma_score = 2
            details.append('EXPMA多头')
        elif expma12 > expma50:
            # 白线>黄线但价格跌破白线，结构弱化
            expma_score = 1
            details.append('EXPMA偏多(价破白线)')
        elif expma12 < expma50 and expma_gap > 0.005:
            expma_score = 0
            details.append('EXPMA空头')
        else:
            expma_score = 1
            details.append('EXPMA粘合')
    else:
        details.append('EXPMA未知')

    # ── MACD: 0~4分 ──
    macd_score = 2
    if close > 0 and macd_dif is not None and macd_dea is not None:
        dif_ratio = abs(macd_dif) / close
        clearly_off = dif_ratio > 0.01

        if clearly_off and macd_dif > 0 and macd_dif > macd_dea:
            macd_score = 4
            details.append('MACD多头强')
        elif macd_dif > 0 and macd_dif > macd_dea:
            macd_score = 3
            details.append('MACD金叉(0轴上)')
        elif macd_dif < 0 and macd_dif > macd_dea:
            macd_score = 2
            details.append('MACD金叉(0轴下)')
        elif clearly_off and macd_dif < 0 and macd_dif < macd_dea:
            macd_score = 0
            details.append('MACD空头强')
        elif macd_dif < macd_dea:
            macd_score = 1
            details.append('MACD死叉')
        else:
            macd_score = 2
            details.append('MACD中性')
    else:
        details.append('MACD未知')

    # ── MA排列: 0~6分（直接从CSV读，不复算了）──
    chain_periods = [5, 10, 20, 60, 120, 250]
    ma_fields = {5: 'ma5', 10: 'ma10', 20: 'ma20', 60: 'ma60', 120: 'ma120', 250: 'ma250'}
    ma_vals = {}
    for period in chain_periods:
        v = safe_float(last.get(ma_fields[period], 0))
        if v > 0:
            ma_vals[period] = v

    # 链式递进: 从短到长检查连续 short>long，断裂即停
    chain_length = 0
    for i in range(len(chain_periods) - 1):
        sp, lp = chain_periods[i], chain_periods[i + 1]
        if sp in ma_vals and lp in ma_vals and ma_vals[sp] > ma_vals[lp]:
            chain_length += 1
        else:
            break

    ma_score = 6 if chain_length >= 5 else chain_length

    if chain_length > 0:
        chain_label = '→'.join(str(p) for p in chain_periods[:chain_length + 1])
        details.append(f'均线多头排列({chain_label})')
    elif 5 in ma_vals and 10 in ma_vals:
        details.append('均线无序')
    else:
        details.append('均线数据不足')

    # ── 日线闭环: 0~4分（只计买侧, 来自analyze_period的buy_level） ──
    cycle_score = 0
    if daily_buy_level >= 4.0:
        cycle_score = 4
        details.append('日线★买(最强出击)')
    elif daily_buy_level >= 3.5:
        cycle_score = 3
        details.append('日线★买(短期确认)')
    elif daily_buy_level >= 3.0:
        cycle_score = 2
        details.append('日线★买(加强闭环)')

    # ── 总分 0~16 ──
    total_score = expma_score + macd_score + ma_score + cycle_score

    if total_score >= 13:
        direction = 'bullish'
        label = '上涨趋势'
    elif total_score >= 10:
        direction = 'bullish_bias'
        label = '偏多震荡'
    elif total_score >= 7:
        direction = 'neutral'
        label = '横盘震荡'
    elif total_score >= 4:
        direction = 'bearish_bias'
        label = '偏空震荡'
    else:
        direction = 'bearish'
        label = '下跌趋势'

    confidence = abs(total_score - 8) / 8 * 100

    return {
        'direction': direction,
        'label': label,
        'confidence': round(confidence),
        'score': total_score,
        'expma_score': expma_score,
        'macd_score': macd_score,
        'ma_score': ma_score,
        'cycle_score': cycle_score,
        'daily_buy_level': daily_buy_level,
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

    details = []

    # --- 做多侧分析 ---
    buy_level = 0  # 0=无 1=普通 2=加强 3=最强
    buy_details = []

    if recent_buy_anchors:
        # 1. ★买密集度
        buy_count = len(recent_buy_anchors)
        buy_density = buy_count / row_count * 100  # 每百根K线的★买密度
        if buy_density >= 0.8:
            density_label = f'★买密集({buy_count}次/{row_count}K线)'
            buy_level += 1.5
        elif buy_density >= 0.4:
            density_label = f'★买正常({buy_count}次)'
            buy_level += 1.0
        else:
            density_label = f'★买稀疏({buy_count}次)'
            buy_level += 0.5
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
                follow_label = f'金叉跟随快(gap={best_follow})'
                buy_level += 1.5
            elif best_follow <= 12:
                follow_label = f'金叉跟随正常(gap={best_follow})'
                buy_level += 1.0
            else:
                follow_label = f'金叉跟随慢(gap={best_follow})'
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
                buy_details.append(f'★结构突破(pe={trend_pe["pe_back"]:.2f})')
            elif trend_pe['trending']:
                buy_level += 1.0
                buy_details.append(f'方向形成中(pe={trend_pe["pe_back"]:.2f})')
            elif trend_pe['pe_ratio'] > 1.15:
                # 升熵=回归震荡，不扣分，但标记
                buy_details.append(f'震荡回归(pe={trend_pe["pe_back"]:.2f})')

            # 加入 PE 原始数据用于输出
            buy_details.append(f'pe({trend_pe["pe_front"]:.2f}→{trend_pe["pe_back"]:.2f})')

        # 7. 量能确认维度（买侧）
        if recent_buy_anchors:
            # 地量堆+★买 = 最强底部确认
            has_堆 = any(safe_float(r.get('vol_堆', 0)) >= 1 for r in recent_rows)
            if has_堆:
                buy_level += 1.5
                buy_details.append('地量堆+★买(底部确认)')
            else:
                has_百地 = any(safe_float(r.get('vol_llv100', 0)) >= 1 for r in recent_rows)
                if has_百地:
                    buy_level += 1.0
                    buy_details.append('百日地量+★买(底部)')
                else:
                    has_突放 = any(safe_float(r.get('vol_突放', 0)) >= 1 for r in recent_rows)
                    if has_突放 and direction in ('bullish', 'bullish_bias'):
                        buy_level += 1.0
                        buy_details.append('放量突破确认')
                    else:
                        shrinks = sum(1 for r in recent_rows if safe_float(r.get('vol_缩50', 0)) >= 1)
                        if shrinks >= 2 and direction in ('bullish', 'bullish_bias'):
                            buy_level += 0.5
                            buy_details.append('回调缩量(调整健康)')
                        else:
                            grads = sum(1 for r in recent_rows if safe_float(r.get('vol_梯度升', 0)) >= 1)
                            if grads >= 3:
                                buy_level += 0.3
                                buy_details.append('梯度放量')

    # --- 做空侧分析 ---
    sell_level = 0
    sell_details = []

    if recent_sell_anchors:
        sell_count = len(recent_sell_anchors)
        sell_density = sell_count / row_count * 100
        if sell_density >= 0.8:
            sell_details.append(f'★卖密集({sell_count}次/{row_count}K线)')
            sell_level += 1.5
        elif sell_density >= 0.4:
            sell_details.append(f'★卖正常({sell_count}次)')
            sell_level += 1.0
        else:
            sell_details.append(f'★卖稀疏({sell_count}次)')
            sell_level += 0.5

        best_follow = None
        for sa in recent_sell_anchors:
            for dc in recent_dead:
                if dc['idx'] > sa['idx']:
                    gap = dc['idx'] - sa['idx']
                    if best_follow is None or gap < best_follow:
                        best_follow = gap
        if best_follow is not None:
            if best_follow <= 5:
                sell_details.append(f'死叉跟随快(gap={best_follow})')
                sell_level += 1.5
            elif best_follow <= 12:
                sell_details.append(f'死叉跟随正常(gap={best_follow})')
                sell_level += 1.0
            else:
                sell_details.append(f'死叉跟随慢(gap={best_follow})')
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
                sell_details.append(f'★结构突破(pe={trend_pe["pe_back"]:.2f})')
            elif trend_pe['trending']:
                sell_level += 1.0
                sell_details.append(f'方向形成中(pe={trend_pe["pe_back"]:.2f})')
            elif trend_pe['pe_ratio'] > 1.15:
                sell_details.append(f'震荡回归(pe={trend_pe["pe_back"]:.2f})')
            sell_details.append(f'pe({trend_pe["pe_front"]:.2f}→{trend_pe["pe_back"]:.2f})')

        # 7. 量能确认维度（卖侧）
        if recent_sell_anchors:
            # 放量阴线（vr5>1.5 + 收盘<开盘）
            has_放量阴 = any(
                safe_float(r.get('vr5', 1.0)) > 1.5
                and safe_float(r.get('close', 0)) < safe_float(r.get('open', 0))
                for r in recent_rows
            )
            if has_放量阴 and direction in ('bearish', 'bearish_bias'):
                sell_level += 0.8
                sell_details.append('放量阴线(风险)')

    # --- 根据趋势方向选择主分析侧 ---
    direction = trend['direction']
    if direction in ('bullish', 'bullish_bias'):
        details = buy_details
        if buy_level >= 4.0:
            label = '最强出击信号'
        elif buy_level >= 3.0:
            label = '加强闭环'
        elif buy_level >= 2.0:
            label = '普通闭环'
        elif buy_level >= 1.0:
            label = '弱信号'
        else:
            label = '无出击信号'
        level = buy_level
        # 附带空头信息（做参考）
        if sell_details:
            details.append(f'[空侧参考: {" | ".join(sell_details)}]')

    elif direction in ('bearish', 'bearish_bias'):
        details = sell_details
        if sell_level >= 4.0:
            label = '最强出击信号'
        elif sell_level >= 3.0:
            label = '加强闭环'
        elif sell_level >= 2.0:
            label = '普通闭环'
        elif sell_level >= 1.0:
            label = '弱信号'
        else:
            label = '无出击信号'
        level = sell_level
        if buy_details:
            details.append(f'[多侧参考: {" | ".join(buy_details)}]')

    else:
        # 震荡看两侧
        effective_level = max(buy_level, sell_level)
        details = buy_details + sell_details
        if effective_level >= 4.0:
            label = '最强出击信号'
        elif effective_level >= 3.0:
            label = '加强闭环'
        elif effective_level >= 2.0:
            label = '普通闭环'
        elif effective_level >= 1.0:
            label = '弱信号'
        else:
            label = '无出击信号'
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




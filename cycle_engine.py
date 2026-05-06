# -*- coding: utf-8 -*-
"""
周期循环引擎 v2.0 — Cycle Engine (三层架构版)

核心理念:
  不是先评分再做建议，而是先定位再评分。
  
  三层架构:
    第一层: 价格位置 — K线在 EXPMA 白线/黄线的什么位置？高位/中位/低位？
    第二层: 趋势方向 — 上涨/震荡/下跌？
    第三层: 循环适配 — 在已知位置+方向下，信号质量如何？
  
  三个问题按顺序回答，每个标的状态自然浮现。

设计原则 (来自用户第一性原理):
  - 位置决定风险，方向决定策略，循环决定时机
  - 科创芯片: 高位加速区 + 上涨态 + 信号散乱 = 持有/减仓，不是买入
  - 恒生科技: 低位区 + 下跌态 + 买信号密集 = 触底酝酿，等转折信号
  - 不是循环好就值得操作，而是"位置+方向+循环"三者共振

作者: 小草 (EasyClaw) + v4 Pro
日期: 2026-05-06 (重写版)
"""

import os
import sys
import csv
import json
from pathlib import Path

# ============================================================
# 配置
# ============================================================

BASE = Path('D:/quantify-per')
SNAPSHOT_DIR = BASE / 'signals' / 'tracking'
OUTPUT_PATH = BASE / 'signals' / 'tracking' / 'cycle_report.json'

PERIODS = ['min5', 'min15', 'min30', 'min60', 'daily']
PERIOD_LABELS = {
    'min5': '5分钟', 'min15': '15分钟', 'min30': '30分钟',
    'min60': '60分钟', 'daily': '日线',
}

# 回溯 K 线数量
KLINES_LOOKBACK = {
    'min5': 500, 'min15': 500, 'min30': 400,
    'min60': 300, 'daily': 250,  # 日线约 1 年
}


# ============================================================
# 数据读取
# ============================================================

def read_csv(code, period):
    fpath = SNAPSHOT_DIR / code / f'{period}_signals.csv'
    if not fpath.exists():
        return []
    with open(fpath, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    max_k = KLINES_LOOKBACK.get(period, 200)
    if max_k > 0 and len(rows) > max_k:
        rows = rows[-max_k:]
    return rows


def get_all_codes():
    codes = []
    for d in SNAPSHOT_DIR.iterdir():
        if d.is_dir() and (d / 'daily_signals.csv').exists():
            codes.append(d.name)
    return sorted(codes)


def get_name_map():
    try:
        sys.path.insert(0, str(BASE))
        from config import NAME_MAP
        return NAME_MAP
    except:
        return {}


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

def judge_trend(code, daily_rows):
    """
    判断整体趋势方向: 上涨/震荡/下跌
    
    三要素:
      - EXPMA: 白线 vs 黄线
      - MACD: DIF vs DEA vs 0轴
      - 近期价格走势: 最近20根K线的方向
    
    多数投票决定方向
    """
    if not daily_rows:
        return {'direction': 'unknown', 'label': '无数据', 'confidence': 0}

    last = daily_rows[-1]
    close = safe_float(last.get('close', 0))
    expma12 = safe_float(last.get('expma12', 0))
    expma50 = safe_float(last.get('expma50', 0))
    macd_dif = safe_float(last.get('macd_dif', 0))
    macd_dea = safe_float(last.get('macd_dea', 0))

    votes_up = 0
    votes_down = 0
    details = []

    # 要素1: EXPMA
    if expma12 and expma50:
        if expma12 > expma50:
            votes_up += 1
            details.append('EXPMA多头')
        elif expma12 < expma50:
            votes_down += 1
            details.append('EXPMA空头')
        else:
            details.append('EXPMA粘合')

    # 要素2: MACD
    if macd_dif is not None and macd_dea is not None:
        if macd_dif > macd_dea and macd_dif > 0:
            votes_up += 1
            details.append('MACD强势多头')
        elif macd_dif > macd_dea:
            # 0轴下的金叉: 偏多但弱
            votes_up += 0.3
            details.append('MACD弱金叉(0轴下)')
        elif macd_dif < macd_dea and macd_dif < 0:
            votes_down += 1
            details.append('MACD强势空头')
        elif macd_dif < macd_dea:
            # 0轴上的死叉: 偏空但弱
            votes_down += 0.3
            details.append('MACD弱死叉(0轴上)')
        else:
            details.append('MACD粘合')

    # 要素3: 近期价格走势 (最近20根K线)
    if len(daily_rows) >= 20:
        recent = daily_rows[-20:]
        first_close = safe_float(recent[0].get('close', 0))
        if first_close and close:
            pct_change = (close - first_close) / first_close * 100
            if pct_change > 3:
                votes_up += 1
                details.append(f'近20日+{pct_change:.1f}%')
            elif pct_change < -3:
                votes_down += 1
                details.append(f'近20日{pct_change:.1f}%')
            else:
                details.append(f'近20日{pct_change:+.1f}%(横盘)')

    # 投票结果
    total_up = votes_up
    total_down = votes_down

    if total_up >= 2.5:
        direction = 'bullish'
        label = '上涨趋势'
    elif total_down >= 2.5:
        direction = 'bearish'
        label = '下跌趋势'
    elif total_up >= 1.5:
        direction = 'bullish_bias'
        label = '偏多震荡'
    elif total_down >= 1.5:
        direction = 'bearish_bias'
        label = '偏空震荡'
    else:
        direction = 'neutral'
        label = '横盘震荡'

    confidence = abs(total_up - total_down) / max(total_up + total_down, 1) * 100

    return {
        'direction': direction,
        'label': label,
        'confidence': round(confidence),
        'votes_up': total_up,
        'votes_down': total_down,
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


def signal_quality(anchors, raw_rows, position, trend, lookback_klines=20):
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
        return {'level': 'none', 'label': '无信号', 'details': []}

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

    return {
        'level': level,
        'label': label,
        'details': details,
        'buy_level': buy_level,
        'sell_level': sell_level,
    }


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


def analyze_period(code, period, position, trend):
    """
    第四层: 信号质量递进分析
    
    在已知位置+方向下，分析最近一段的信号是否形成了出击窗口
    """
    rows = read_csv(code, period)
    if not rows:
        return None

    anchors = extract_anchors(rows)
    if not anchors:
        return {'period': period, 'period_label': PERIOD_LABELS[period],
                'anchors': 0, 'signal_quality': None, 'price_eff': None}

    # 历史价格有效性（全量统计）
    pe = price_effectiveness(anchors, rows)

    # 最近N根K线的信号质量（递进分析）
    sq = signal_quality(anchors, rows, position, trend, lookback_klines=20)

    return {
        'period': period,
        'period_label': PERIOD_LABELS[period],
        'anchors': len(anchors),
        'signal_quality': sq,
        'price_eff': pe,
    }


# ============================================================
# 主分析函数: 三层架构
# ============================================================

def analyze(code, name=''):
    """
    三层架构分析:
    1. 价格位置
    2. 趋势方向
    3. 循环适配
    """
    daily_rows = read_csv(code, 'daily')

    # 第一层: 价格位置
    position = judge_position(daily_rows)

    # 第二层: 趋势方向
    trend = judge_trend(code, daily_rows)

    # 第三层: 各周期循环适配
    period_results = {}
    for period in PERIODS:
        result = analyze_period(code, period, position, trend)
        if result:
            period_results[period] = result

    # 找出最佳操作级别: 信号质量最高的
    best = None
    for period in PERIODS:
        p = period_results.get(period)
        if not p or not p.get('signal_quality'):
            continue
        sq = p['signal_quality']
        # v3.1: raw level 最高优先，去掉 >= 2.0 门槛，让低级别信号也能参与比较
        if best is None or sq['level'] > best['signal_quality']['level']:
            best = p

    # 综合操作建议
    advice = _generate_advice(position, trend, best)

    return {
        'code': code,
        'name': name,
        'position': position,
        'trend': trend,
        'periods': period_results,
        'best_period': best,
        'advice': advice,
    }


def _generate_advice(position, trend, best):
    """根据位置+方向+信号质量，生成操作建议"""
    direction = trend['direction']
    zone = position['zone']
    risk = position['risk_level']

    if best is None:
        return {'action': '观望', 'reason': '无有效信号', 'confidence': '低'}

    sq = best.get('signal_quality', {})
    level = sq.get('level', 0)
    label = sq.get('label', '')

    # 核心决策矩阵: 位置 + 方向 + 信号质量递进级别
    if zone == 'high' and risk == 'critical':
        if direction in ('bullish', 'bullish_bias'):
            if level >= 3.0:
                return {'action': '持有(可轻仓跟)', 'reason': f'高位但{best["period_label"]}有{label}，顺势轻仓', 'confidence': '中'}
            else:
                return {'action': '持有/减仓', 'reason': '高位加速区，不适合追高。持有者可分批止盈', 'confidence': '高'}
        else:
            return {'action': '回避', 'reason': '高位+弱势=风险极大', 'confidence': '高'}

    if zone == 'low' and risk == 'critical':
        if direction in ('bearish', 'bearish_bias'):
            if level >= 3.0:
                return {'action': '关注抄底', 'reason': f'超跌+{best["period_label"]}{label}，等转折确认后轻仓试错', 'confidence': '中'}
            else:
                return {'action': '等待', 'reason': '超跌但信号不充分，勿接飞刀', 'confidence': '高'}
        else:
            return {'action': '轻仓试多', 'reason': '低位+方向好转，可逐步建仓', 'confidence': '中'}

    if direction in ('bullish', 'bullish_bias'):
        if level >= 4.0:
            return {'action': '出击加注', 'reason': f'{best["period_label"]}最强出击信号，顺势跟进', 'confidence': '高'}
        elif level >= 3.0:
            return {'action': '顺势做多', 'reason': f'{best["period_label"]}加强闭环，可跟', 'confidence': '高'}
        elif level >= 2.0:
            return {'action': '等待加强', 'reason': f'{best["period_label"]}有信号但级别不够，等加强再动手', 'confidence': '中'}
        else:
            return {'action': '观望', 'reason': '多头但无出击信号，等待', 'confidence': '中'}

    if direction in ('bearish', 'bearish_bias'):
        if level >= 4.0:
            return {'action': '关注转折', 'reason': f'{best["period_label"]}底部信号密集，可能触底反弹', 'confidence': '中'}
        elif level >= 3.0:
            return {'action': '等待确认', 'reason': '底部信号积累中，等转折确认', 'confidence': '中'}
        elif level >= 2.0:
            return {'action': '等待', 'reason': '下跌趋势延续，等底部结构成形', 'confidence': '高'}
        else:
            return {'action': '不参与', 'reason': '空头+无信号，勿抄底', 'confidence': '高'}

    # 震荡
    if level >= 3.0:
        return {'action': '高抛低吸', 'reason': f'{best["period_label"]}信号适配好，适合做T', 'confidence': '中'}
    elif level >= 2.0:
        return {'action': '小仓做T', 'reason': f'{best["period_label"]}有信号，轻仓参与', 'confidence': '低'}
    else:
        return {'action': '观望', 'reason': '震荡但信号不足', 'confidence': '低'}


# ============================================================
# 批量分析与输出
# ============================================================

def analyze_all():
    codes = get_all_codes()
    name_map = get_name_map()
    results = [analyze(code, name_map.get(code, code)) for code in codes]
    # 按方向+信号质量排序
    def sort_key(r):
        dir_order = 0 if r['trend']['direction'] in ('bullish','bullish_bias') else \
                    1 if r['trend']['direction'] == 'neutral' else 2
        best = r.get('best_period')
        sq_level = best['signal_quality']['level'] if best and best.get('signal_quality') else 0
        return (dir_order, -sq_level)
    results.sort(key=sort_key)
    return results


def _fmt_price_eff(pe):
    if not pe or pe['buy_samples'] + pe['sell_samples'] == 0:
        return '无样本'
    parts = []
    if pe['buy_samples']:
        parts.append(f"★买后均{pe['buy_avg_pct']:+.1f}%({pe['buy_hit_rate']}%涨)")
    if pe['sell_samples']:
        parts.append(f"★卖后均{pe['sell_avg_pct']:+.1f}%({pe['sell_hit_rate']}%跌)")
    return ', '.join(parts)


def format_report(results):
    lines = []
    lines.append('=' * 92)
    lines.append('[周期循环分析] Cycle Engine v2.0 — 三层架构版')
    lines.append('=' * 92)

    for r in results:
        pos = r['position']
        trd = r['trend']
        code = r['code']
        name = r['name']

        lines.append(f'\n{"─" * 92}')
        lines.append(f'* {code} {name}')
        lines.append(f'  收盘: {pos.get("close","?")}')

        # 第一层: 价格位置
        zone_icon = {'high': '[高位]', 'mid': '[中位]', 'low': '[低位]', 'unknown': '[未知]'}
        risk_icon = {'critical': '!!高危', 'high': '!偏高', 'medium': ' 中等', 'low': ' 低'}
        lines.append(f'  [第一层 价格位置] {zone_icon.get(pos["zone"],"?")} '
                     f'{pos["label"]} 风险:{risk_icon.get(pos["risk_level"],"?")}')
        lines.append(f'    EXPMA12={pos.get("expma12","?")} '
                     f'EXPMA50={pos.get("expma50","?")} '
                     f'偏离白线{pos.get("deviation_white_pct","?"):+}% '
                     f'偏离黄线{pos.get("deviation_yellow_pct","?"):+}%')
        lines.append(f'    {pos.get("description","")}')

        # 第二层: 趋势方向
        dir_icon = {'bullish': '[上涨]', 'bullish_bias': '[偏多]', 'neutral': '[震荡]',
                    'bearish_bias': '[偏空]', 'bearish': '[下跌]'}
        lines.append(f'  [第二层 趋势方向] {dir_icon.get(trd["direction"],"?")} '
                     f'{trd["label"]} (置信度{trd["confidence"]}%)')
        lines.append(f'    {" | ".join(trd["details"])}')
        lines.append(f'    MACD: DIF={trd.get("macd_dif","?")} DEA={trd.get("macd_dea","?")}')

        # 第三层: 各周期信号质量
        lines.append(f'  [第三层 信号质量递进]')
        best = r.get('best_period')
        for period in PERIODS:
            p = r['periods'].get(period)
            if not p:
                continue
            sq = p.get('signal_quality')
            pe = p.get('price_eff')
            if not sq:
                continue
            marker = ' <<<' if (best and best['period'] == period) else ''

            # 信号级别标识
            level_mark = {4: '🔥🔥🔥', 3: '🔥🔥', 2: '🔥', 1: '⚡', 0: '--'}
            fire = level_mark.get(int(sq.get('level', 0)), '--')

            # 价格有效性
            price_str = _fmt_price_eff(pe)

            # 信号质量详情
            sq_details = ', '.join(sq.get('details', []))

            lines.append(f'    [{p["period_label"]:>4}] {fire} {sq["label"]:>8}'
                         f' | {sq_details}')
            if price_str:
                lines.append(f'          价格: {price_str}')

        # 第四行: 操作建议
        advice = r.get('advice', {})
        lines.append(f'  >>> 操作建议: {advice.get("action","?")} '
                     f'(置信度:{advice.get("confidence","?")})')
        lines.append(f'  >>> {advice.get("reason","")}')

    lines.append(f'\n{"=" * 92}')
    lines.append(f'[分析完成] {len(results)} 只标的')
    lines.append(f'{"=" * 92}')

    return '\n'.join(lines)


def save_results(results):
    clean = []
    for r in results:
        clean.append({
            'code': r['code'],
            'name': r['name'],
            'position': {k: v for k, v in r['position'].items()
                        if isinstance(v, (str, int, float, bool, type(None)))},
            'trend': {k: v for k, v in r['trend'].items()
                     if isinstance(v, (str, int, float, bool, list, type(None)))},
            'best_period': r['best_period']['period'] if r['best_period'] else None,
            'best_signal_level': r['best_period']['signal_quality']['level'] if r['best_period'] and r['best_period'].get('signal_quality') else 0,
            'advice': r['advice'],
        })
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    print(f'\n[Saving] {OUTPUT_PATH}')


# ============================================================
# 辅助函数
# ============================================================

def safe_float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    if len(sys.argv) > 1 and sys.argv[1] in ('--help', '-h', '/?'):
        print(__doc__)
        sys.exit(0)

    elif len(sys.argv) > 1 and sys.argv[1] == '--save':
        results = analyze_all()
        print(format_report(results))
        save_results(results)

    elif len(sys.argv) > 1:
        code = sys.argv[1]
        nm = get_name_map()
        result = analyze(code, nm.get(code, code))
        print(format_report([result]))

    else:
        results = analyze_all()
        print(format_report(results))

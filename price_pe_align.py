# -*- coding: utf-8 -*-
"""
price_pe_align.py — 价格-结构对齐分析
独立模块，读 signal CSV（含 PE 列）→ 检测价格阶段 → 对齐 PE 轨迹 → 输出综合评估

用法:
    from price_pe_align import analyze
    result = analyze(rows, period='daily')  # rows 来自 signal CSV
    # → {'price_stage': 'trend_extension', 'pe_stage': 'locked',
    #    'alignment': 'healthy', 'assessment': '趋势健康，结构锁定'}

设计原则:
  - 不依赖 cycle_engine 或 gen_report_md
  - 纯数据驱动，只读 CSV rows
  - 输出为 dict，任何模块都可引用
"""

import os


# ════════════════════════════════════════════
# 价格阶段检测
# ════════════════════════════════════════════

def _detect_price_stage(rows, lookback=20):
    """
    从价格数据检测当前所处阶段。

    判断逻辑（从近到远）:
      - 近5根持续新高 + 距20日高点<2% → trend_extension (趋势延伸)
      - 近5根突破20日高点 → breakout (平台突破)
      - 距20日高点回撤>3% + 近5根下行 → pullback (高位回调)
      - 距20日低点<2% + 横盘 → consolidation (横盘整理)
      - 跌破20日低点 + 持续下行 → breakdown (破位下行)
      - 默认 → consolidation (方向不明)
    """
    n = len(rows)
    if n < lookback + 5:
        return 'data_insufficient'

    recent = rows[-5:]
    lookback_rows = rows[-lookback:]

    closes = []
    highs = []
    lows = []
    for r in rows:
        c = r.get('close', 0) or 0
        h = r.get('high', 0) or 0
        lo = r.get('low', 0) or 0
        closes.append(float(c))
        highs.append(float(h))
        lows.append(float(lo))

    recent_closes = closes[-5:]
    recent_highs = highs[-5:]
    recent_lows = lows[-5:]

    lb_high = max(highs[-lookback:])
    lb_low = min(lows[-lookback:])
    current = closes[-1]
    if lb_high <= 0 or lb_low <= 0 or current <= 0:
        return 'data_insufficient'

    # 距高点和低点的距离
    dist_from_high = (current - lb_high) / lb_high * 100
    dist_from_low = (current - lb_low) / lb_low * 100

    # 近5日方向
    recent_trend = recent_closes[-1] - recent_closes[0]
    trend_up = all(recent_closes[i] >= recent_closes[i - 1] * 0.995 for i in range(1, 5))
    trend_down = all(recent_closes[i] <= recent_closes[i - 1] * 1.005 for i in range(1, 5))

    # 判断突破
    if dist_from_high >= -1.0 and current > max(highs[-10:-5]) * 1.01:
        return 'breakout'
    if dist_from_high >= -1.0 and trend_up:
        return 'trend_extension'
    if dist_from_high >= -2.0 and recent_trend > 0:
        return 'trend_extension'

    # 判断回调
    if dist_from_high < -3.0 and trend_down:
        return 'pullback'
    if dist_from_high < -2.0 and recent_trend < -1.0:
        return 'pullback'

    # 判断破位
    if dist_from_low < -2.0 and trend_down:
        return 'breakdown'

    # 横盘
    if abs(dist_from_high - dist_from_low) < 5.0:
        return 'consolidation'

    if trend_up:
        return 'trend_extension'
    if trend_down:
        return 'pullback'

    return 'consolidation'


# ════════════════════════════════════════════
# PE 轨迹分析
# ════════════════════════════════════════════

def _analyze_pe_trajectory(rows):
    """
    从 PE 列分析 PE 轨迹。
    需要 rows 已含 pe / pe_level / pe_chg_5 列。
    """
    pe_vals = []
    pe_levels = []
    pe_chgs = []
    for r in rows:
        p = r.get('pe', None)
        if p is None or p == '':
            continue
        pe_vals.append(float(p))
        pe_levels.append(r.get('pe_level', ''))
        pc = r.get('pe_chg_5', None)
        pe_chgs.append(float(pc) if pc not in (None, '') else 0.0)

    if len(pe_vals) < 20:
        return {'pe_stage': 'insufficient_data', 'pe_current': None,
                'pe_level': '', 'pe_trend': '', 'pe_velocity': ''}

    current_pe = pe_vals[-1]
    current_level = pe_levels[-1] if pe_levels else ''

    # PE 近期趋势
    pe_5 = pe_vals[-5:] if len(pe_vals) >= 5 else pe_vals
    pe_10_ago = pe_vals[-10] if len(pe_vals) >= 10 else pe_vals[0]
    pe_20_ago = pe_vals[-20] if len(pe_vals) >= 20 else pe_vals[0]

    pe_trend_short = pe_5[-1] - pe_5[0]
    pe_trend_long = pe_vals[-1] - pe_20_ago

    # PE 阶段
    if abs(pe_trend_short) < 0.02:
        if current_pe < 0.40:
            pe_stage = 'locked'
        elif current_pe > 0.70:
            pe_stage = 'collapsing'
        else:
            pe_stage = 'neutral'
    elif pe_trend_short < -0.03:
        pe_stage = 'structuring'
    elif pe_trend_short > 0.03:
        if current_pe > 0.70:
            pe_stage = 'collapsing'
        else:
            pe_stage = 'loosening'
    else:
        pe_stage = 'neutral'

    # PE 变化速度
    if abs(pe_trend_short) < 0.01:
        velocity = 'stable'
    elif abs(pe_trend_short) < 0.05:
        velocity = 'mild'
    else:
        velocity = 'rapid'

    pe_trend_dir = 'falling' if pe_trend_short < 0 else ('rising' if pe_trend_short > 0 else 'flat')

    return {
        'pe_stage': pe_stage,
        'pe_current': round(current_pe, 4),
        'pe_level': current_level,
        'pe_trend': pe_trend_dir,
        'pe_velocity': velocity,
        'pe_chg_5': round(pe_trend_short, 4),
        'pe_chg_20': round(pe_trend_long, 4),
    }


# ════════════════════════════════════════════
# 价格-PE 对齐
# ════════════════════════════════════════════

def _align(price_stage, pe_info):
    """
    对齐价格阶段和 PE 轨迹，输出综合评估。

    对齐矩阵:
                    price_stage
                    breakout  trend_ext  consol.  pullback  breakdown
    pe_structuring   ✅确认     ⚠异常     🔄形成中  🔄转变中  ✅确认
    pe_locked        🔄延续     ✅健康     ➖平稳   ⚠松动    ❌背离
    pe_loosening     ⚠可疑     ⚠衰减     ⚠散乱   ✅确认    ✅确认
    pe_collapsing    ❌假突破   ❌背离     ❌溃散   ❌溃散    ✅确认
    pe_neutral       ➖观望     ➖观望     ➖观望   ➖观望    ➖观望
    """
    ps = price_stage
    pe_s = pe_info['pe_stage']

    key = (ps, pe_s)

    alignment_map = {
        # breakout + structuring = 真正的突破正在发生
        ('breakout', 'structuring'): ('confirming', '平台突破确认，结构正在有序化，趋势可能加速'),
        ('breakout', 'locked'): ('healthy', '突破后结构锁定，可加仓'),
        ('breakout', 'loosening'): ('divergent', '涨但结构松动，突破可信度降低，观望'),
        ('breakout', 'collapsing'): ('divergent', '假突破风险，价格涨但结构在溃散'),
        ('breakout', 'neutral'): ('neutral', '突破中结构方向不明，等待确认'),

        ('trend_extension', 'locked'): ('healthy', '趋势健康，结构锁紧，安心持有'),
        ('trend_extension', 'structuring'): ('healthy', '趋势延伸中结构继续有序化，强势'),
        ('trend_extension', 'loosening'): ('divergent', '趋势延伸但结构松动，注意减仓'),
        ('trend_extension', 'collapsing'): ('divergent', '趋势延续中结构溃散，即将反转'),
        ('trend_extension', 'neutral'): ('neutral', '趋势延续中，结构平稳'),

        ('consolidation', 'structuring'): ('confirming', '横盘中结构有序化，方向即将出现'),
        ('consolidation', 'locked'): ('neutral', '横盘+结构锁定，等待方向选择'),
        ('consolidation', 'loosening'): ('neutral', '横盘+结构松动，方向不明'),
        ('consolidation', 'collapsing'): ('divergent', '横盘+结构溃散，大概率向下破位'),
        ('consolidation', 'neutral'): ('neutral', '横盘待变'),

        ('pullback', 'structuring'): ('confirming', '回调中结构有序化=下跌形成，减仓回避'),
        ('pullback', 'locked'): ('divergent', '回调但结构仍锁=该跌不跌，可能止跌'),
        ('pullback', 'loosening'): ('confirming', '回调+结构松动=趋势衰减，减仓'),
        ('pullback', 'collapsing'): ('confirming', '回调+结构溃散=趋势可能结束，离场'),
        ('pullback', 'neutral'): ('neutral', '正常回调，关注结构变化'),

        ('breakdown', 'structuring'): ('confirming', '破位下行+结构有序化=下跌趋势形成'),
        ('breakdown', 'locked'): ('divergent', '破位但结构锁=该涨不涨，继续看跌'),
        ('breakdown', 'loosening'): ('confirming', '破位+结构松动=趋势走弱确认'),
        ('breakdown', 'collapsing'): ('confirming', '破位+结构溃散=全面走弱，一律回避'),
        ('breakdown', 'neutral'): ('confirming', '破位下行，回避'),
    }

    result = alignment_map.get(key, ('neutral', '结构状态与价格走势关系不明'))
    return {
        'alignment': result[0],
        'assessment': result[1],
    }


# ════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════

def analyze(rows, period='daily'):
    """
    对单周期 CSV rows 做价格-PE 对齐分析。

    参数:
        rows: list[dict], 来自 signal CSV 的完整行数据（需含 pe/pe_level/pe_chg_5 列）
        period: str, 周期标识（用于调参，如 lookback 长度）

    返回:
        dict: {
            'price_stage': str,      # 价格阶段
            'pe_info': dict,         # PE 轨迹详情
            'alignment': str,        # 对齐结论: healthy/confirming/divergent/neutral
            'assessment': str,       # 综合评估文本
            'action_hint': str,      # 操作提示: 持仓/减仓/回避/观望/加仓
        }
    """
    if not rows:
        return {
            'price_stage': 'no_data',
            'pe_info': {},
            'alignment': 'neutral',
            'assessment': '无数据',
            'action_hint': '观望',
        }

    # 周期相关参数
    lookback_map = {
        'daily': 20,
        'min60': 40,
        'min30': 60,
        'min15': 80,
        'min5': 100,
        'min1': 120,
    }
    lookback = lookback_map.get(period, 20)

    price_stage = _detect_price_stage(rows, lookback=lookback)
    pe_info = _analyze_pe_trajectory(rows)
    alignment = _align(price_stage, pe_info)

    # 操作提示
    action_map = {
        'healthy': '持仓',
        'confirming': None,  # 根据价格方向判断
        'divergent': '减仓',
        'neutral': '观望',
    }
    action = action_map.get(alignment['alignment'], '观望')
    if action is None:
        if price_stage in ('breakout', 'trend_extension'):
            action = '持仓'
        elif price_stage in ('pullback', 'breakdown'):
            action = '减仓'
        else:
            action = '观望'

    return {
        'price_stage': price_stage,
        'pe_info': pe_info,
        'alignment': alignment['alignment'],
        'assessment': alignment['assessment'],
        'action_hint': action,
    }


def analyze_code_period(code, period, base_dir=None):
    """
    便捷函数: 从 CSV 文件读取并分析。

    参数:
        code: str, 标的代码 (e.g. 'sz399006')
        period: str, 周期 (e.g. 'daily')
        base_dir: str, signals/tracking/ 目录

    返回:
        dict, 同 analyze()
    """
    import csv
    if base_dir is None:
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'signals', 'tracking')

    csv_path = os.path.join(base_dir, code, f'{period}_signals.csv')
    if not os.path.exists(csv_path):
        return {'price_stage': 'file_missing', 'pe_info': {},
                'alignment': 'neutral', 'assessment': f'无CSV: {csv_path}',
                'action_hint': '观望'}

    rows = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    return analyze(rows, period)


def analyze_all(base_dir=None):
    """
    批量分析所有标的和周期的价格-PE 对齐状态。

    返回:
        list[dict], 每条含 code/name/period + analyze() 输出
    """
    import json
    if base_dir is None:
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'signals', 'tracking')

    # 从 cycle_report.json 获取跟踪列表
    cycle_path = os.path.join(base_dir, 'cycle_report.json')
    if not os.path.exists(cycle_path):
        return []

    try:
        with open(cycle_path, 'r', encoding='utf-8') as f:
            cycle_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    results = []
    for item in cycle_data:
        code = item['code']
        name = item.get('name', '')
        # 分析日线
        r = analyze_code_period(code, 'daily', base_dir)
        r['code'] = code
        r['name'] = name
        r['period'] = 'daily'
        results.append(r)

    return results


if __name__ == '__main__':
    # 终端快速查看
    import sys
    if len(sys.argv) > 1:
        code = sys.argv[1]
        period = sys.argv[2] if len(sys.argv) > 2 else 'daily'
        r = analyze_code_period(code, period)
    else:
        results = analyze_all()
        for r in results:
            print(f"{r['code']} {r['name']:<8} {r['period']:<6} "
                  f"价格:{r['price_stage']:<18} PE:{r['pe_info'].get('pe_stage', '?'):<14} "
                  f"对齐:{r['alignment']:<12} → {r['action_hint']}")
        sys.exit(0)

    import json as _json
    print(_json.dumps(r, ensure_ascii=False, indent=2))

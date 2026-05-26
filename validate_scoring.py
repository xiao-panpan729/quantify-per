#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
validate_scoring.py — 评分体系回测验证

对每只标的滚动计算历史评分，记录后续实际涨跌。
用来回答：0-14 评分真的能预测涨跌吗？阈值设对了吗？

输出:
  signals/tracking/score_validation.csv  — 逐日逐标的数据
  终端统计摘要
"""

import os, sys, csv, json, math
from collections import defaultdict

# ── 项目根 ──
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from config import NAME_MAP
from cycle_engine.indicators import judge_trend
from cycle_engine.constants import Direction

TRACKING_DIR = os.path.join(PROJECT_ROOT, 'signals', 'tracking')

# ── 参数 ──
WARMUP = 60          # 最少需要多少根日线才能算
LOOKAHEADS = [1, 3, 5, 10, 20]  # 后续 N 个交易日涨跌幅

DIRECTION_LABELS = {
    Direction.BULLISH: '上涨',
    Direction.BULLISH_BIAS: '偏多',
    Direction.NEUTRAL: '中性',
    Direction.BEARISH_BIAS: '偏空',
    Direction.BEARISH: '下跌',
}

def read_csv_all(code, period):
    """读取完整CSV（不截断），按 timestamp 升序"""
    fpath = os.path.join(TRACKING_DIR, code, f'{period}_signals.csv')
    if not os.path.exists(fpath):
        return []
    with open(fpath, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    # 确保按时间升序
    rows.sort(key=lambda r: r.get('timestamp', ''))
    return rows


def safe_float(v, default=None):
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError, OverflowError):
        return default


def date_cmp(daily_date, min_date):
    """比较两个日期字符串（YYYYMMDD 格式，min30/min60 取前8位）"""
    d1 = str(daily_date)[:8]
    d2 = str(min_date)[:8]
    if d1 < d2: return -1
    if d1 > d2: return 1
    return 0


def filter_minute_rows(min_rows, cutoff_date):
    """返回所有 date <= cutoff_date 的分钟线行"""
    result = []
    for r in min_rows:
        c = date_cmp(cutoff_date, r.get('date', '')[:8])
        if c >= 0:  # cutoff >= row_date → 包含
            result.append(r)
        else:
            break  # CSV 已排序，后面的更大
    return result


def compute_forward_returns(daily_rows):
    """为每根日线计算后续 N 个交易日的涨跌幅"""
    n = len(daily_rows)
    closes = [safe_float(r.get('close', 0)) for r in daily_rows]

    fwd = {}
    for lookahead in LOOKAHEADS:
        col = f'forward_{lookahead}d'
        vals = [None] * n
        for i in range(n):
            j = i + lookahead
            if j < n and closes[i] and closes[j] and closes[i] > 0:
                vals[i] = round((closes[j] / closes[i] - 1) * 100, 2)
        fwd[col] = vals
    return fwd


def analyze_one_stock(code, name, daily_rows, min30_rows, min60_rows):
    """计算该标的的所有历史评分"""
    n = len(daily_rows)
    if n < WARMUP:
        print(f'  ⏭ {code} {name}: 数据不足({n}<{WARMUP})')
        return []

    fwd = compute_forward_returns(daily_rows)
    results = []

    # 预计算每个日期的 min30/min60 切片（避免 O(n²) 重复构建）
    print(f'  → {code} {name}: {n} 条日线', end='', flush=True)

    for i in range(WARMUP, n):
        daily_slice = daily_rows[:i + 1]
        cur_date = daily_slice[-1].get('date', '')

        # 过滤分钟线
        m30 = filter_minute_rows(min30_rows, cur_date) if min30_rows else None
        m60 = filter_minute_rows(min60_rows, cur_date) if min60_rows else None

        trend = judge_trend(code, daily_slice, min30_rows=m30, min60_rows=m60)

        row_out = {
            'code': code,
            'name': name,
            'date': cur_date[:8],
            'score': trend.get('score', 0),
            'direction': DIRECTION_LABELS.get(trend.get('direction', ''), '未知'),
            'direction_raw': trend.get('direction', ''),
            'macd_score': trend.get('macd_score', 0),
            'ma_score': trend.get('ma_score', 0),
            'cycle_score': trend.get('cycle_score', 0),
            'close': safe_float(daily_rows[i].get('close', 0), 0),
            'details': '; '.join(trend.get('details', [])),
        }

        for la in LOOKAHEADS:
            col = f'forward_{la}d'
            val = fwd[col][i]
            row_out[col] = val if val is not None else ''

        results.append(row_out)

    print(f' → {len(results)} 条有效评分')
    return results


def print_stats(results):
    """打印统计摘要"""
    if not results:
        print('\n[无有效数据]')
        return

    # ═══ 1. 方向 vs 实际涨跌 ═══
    print('\n' + '=' * 70)
    print('一、方向准确率（评分日 → 后续 5 个交易日涨跌）')
    print('=' * 70)

    dir_stats = defaultdict(lambda: {'correct': 0, 'total': 0, 'returns': []})
    for r in results:
        fwd = safe_float(r.get('forward_5d'), None)
        if fwd is None:
            continue
        dire = r.get('direction_raw', '')
        dir_stats[dire]['total'] += 1
        dir_stats[dire]['returns'].append(fwd)
        # 上涨趋势 → 预期涨
        if dire in Direction.BULLISH_DIRS and fwd > 0:
            dir_stats[dire]['correct'] += 1
        elif dire in Direction.BEARISH_DIRS and fwd < 0:
            dir_stats[dire]['correct'] += 1
        elif dire == Direction.NEUTRAL:
            # 中性不判断对错
            pass

    for dire in [Direction.BULLISH, Direction.BULLISH_BIAS, Direction.NEUTRAL,
                 Direction.BEARISH_BIAS, Direction.BEARISH]:
        lbl = DIRECTION_LABELS.get(dire, dire)
        s = dir_stats[dire]
        if s['total'] == 0:
            continue
        hit = s['correct'] / max(s['total'], 1) * 100 if s['total'] > 0 else 0
        avg_ret = sum(s['returns']) / len(s['returns']) if s['returns'] else 0
        print(f'  {lbl:>6s}: {s["total"]:4d} 次, '
              f'方向正确率 {hit:5.1f}%, '
              f'平均 5日收益 {avg_ret:+.2f}%')

    # ═══ 2. 评分区间 vs 实际涨跌 ═══
    print('\n' + '=' * 70)
    print('二、评分区间 vs 后续实际收益')
    print('=' * 70)
    print(f'  {"区间":>8s}  {"次数":>6s}  {"1日":>8s}  {"3日":>8s}  '
          f'{"5日":>8s}  {"10日":>8s}  {"20日":>8s}')
    print(f'  {"-"*56}')

    buckets = [
        (13, 14, '13-14 涨'),
        (10, 12, '10-12 偏多'),
        (7, 9, '7-9 中性'),
        (4, 6, '4-6 偏空'),
        (0, 3, '0-3 跌'),
    ]

    for lo, hi, label in buckets:
        rows_in = [r for r in results if lo <= safe_float(r.get('score'), -1) <= hi]
        if not rows_in:
            continue
        n = len(rows_in)
        avg_row = [f'{label:>10s}', f'{n:>6d}']
        for la in [1, 3, 5, 10, 20]:
            col = f'forward_{la}d'
            vals = [safe_float(r.get(col), None) for r in rows_in]
            vals = [v for v in vals if v is not None]
            avg = sum(vals) / len(vals) if vals else 0
            avg_row.append(f'{avg:>+7.2f}%')
        print('  ' + '  '.join(avg_row))

    # ═══ 3. 建议（看数据说话） ═══
    print('\n' + '=' * 70)
    print('三、初步判断')
    print('=' * 70)

    # 分析每个区间的 5日收益
    bucket_returns = {}
    for lo, hi, label in buckets:
        vals = [safe_float(r.get('forward_5d'), None) for r in results
                if lo <= safe_float(r.get('score'), -1) <= hi]
        vals = [v for v in vals if v is not None]
        if vals:
            bucket_returns[label] = {
                'n': len(vals),
                'mean': sum(vals) / len(vals),
                'positive_pct': sum(1 for v in vals if v > 0) / len(vals) * 100,
            }

    # 检查高分 vs 低分的区分度
    high = bucket_returns.get('13-14 涨', {})
    low = bucket_returns.get('0-3 跌', {})

    if high and low:
        spread = high.get('mean', 0) - low.get('mean', 0)
        if spread > 2:
            print('  [OK] 高分与低分区间的收益区分度良好')
        elif spread > 0.5:
            print('  [!] 高分与低分有区分但不够明显，可考虑收窄阈值')
        else:
            print('  [FAIL] 高分与低分几乎无区分，评分可能不反映真实市场')

        print(f'     13-14分平均5日收益: {high.get("mean", 0):+.2f}%')
        print(f'     0-3分平均5日收益:  {low.get("mean", 0):+.2f}%')
        print(f'     区分度(差值):       {spread:+.2f}%')

    # 检查中性域(7-9分)是否接近零
    neutral = bucket_returns.get('7-9 中性', {})
    if neutral:
        print(f'\n  中性域(7-9分)平均收益: {neutral.get("mean", 0):+.2f}% '
              f'(理想值 ≈ 0%)')
        print(f'  正向比例: {neutral.get("positive_pct", 0):.0f}% '
              f'(理想值 ≈ 50%)')

    # ═══ 4. 按标的输出 ═══
    print('\n' + '=' * 70)
    print('四、各标的评分-收益相关系数')
    print('=' * 70)
    code_groups = defaultdict(list)
    for r in results:
        code_groups[r['code']].append(r)

    for code in sorted(code_groups.keys()):
        rows = code_groups[code]
        name = rows[0].get('name', code)
        scores = [safe_float(r.get('score'), 0) for r in rows]
        f5ds = [safe_float(r.get('forward_5d'), None) for r in rows]

        # Spearman 近似
        pairs = [(s, f) for s, f in zip(scores, f5ds) if f is not None]
        if len(pairs) < 20:
            continue
        s_vals = [p[0] for p in pairs]
        f_vals = [p[1] for p in pairs]
        n_p = len(s_vals)
        # 皮尔逊
        avg_s = sum(s_vals) / n_p
        avg_f = sum(f_vals) / n_p
        num = sum((s - avg_s) * (f - avg_f) for s, f in pairs)
        den = math.sqrt(sum((s - avg_s) ** 2 for s, _ in pairs)) * \
              math.sqrt(sum((f - avg_f) ** 2 for _, f in pairs))
        r_val = num / den if den > 0 else 0
        pos_pct = sum(1 for _, f in pairs if f > 0) / len(pairs) * 100

        print(f'  {code} {name[:12]:>12s}: r={r_val:+.3f}, '
              f'n={len(pairs)}, 正收益比例={pos_pct:.0f}%')

    return bucket_returns


def save_csv(results, fpath):
    """保存明细到 CSV"""
    if not results:
        return
    fields = ['code', 'name', 'date', 'score', 'direction', 'macd_score',
              'ma_score', 'cycle_score', 'close']
    for la in LOOKAHEADS:
        fields.append(f'forward_{la}d')
    fields.append('details')

    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(results)
    print(f'\n== 明细已保存: {fpath}')


def main():
    print('评分体系回测验证')
    print(f'标的数: {len(NAME_MAP)}')
    print(f'预热期: {WARMUP} 根日线')
    print(f'数据目录: {TRACKING_DIR}')
    print('=' * 70)

    all_results = []

    for code, name in NAME_MAP.items():
        daily = read_csv_all(code, 'daily')
        if not daily or len(daily) < WARMUP:
            print(f'  ⏭ {code} {name}: 日线数据不足')
            continue

        m30 = read_csv_all(code, 'min30')
        m60 = read_csv_all(code, 'min60')

        results = analyze_one_stock(code, name, daily, m30, m60)
        all_results.extend(results)

    if not all_results:
        print('\n[无有效数据]')
        return

    # 保存 CSV
    save_csv(all_results, os.path.join(TRACKING_DIR, 'score_validation.csv'))

    # 统计
    bucket_returns = print_stats(all_results)

    print('\n[完成]')


if __name__ == '__main__':
    main()

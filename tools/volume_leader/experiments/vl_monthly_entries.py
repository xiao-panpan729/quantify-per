# -*- coding: utf-8 -*-
"""
VL宇宙月度进出表 — 每月新进清单+进前涨幅+最终命运

方法:
  1. 逐月重建 VL Top50 (同实验#33)
  2. 每月新进 = 当月Top50 - 之前所有月的并集
  3. 进前涨幅: 从最近一次>20%回调的最低点算到进VL日
  4. 跟踪后续: 淘汰后回本情况
"""

import struct
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../..'))
import config

TDX_VIPDOC = os.path.join(config.TDX_ROOT, 'vipdoc')
DAY_PRICE_COEF = 1000

MONTH_CUTOFFS = {
    '1月': 20260129, '2月': 20260213, '3月': 20260331,
    '4月': 20260430, '5月': 20260529,
}
MONTH_LABELS = ['1月', '2月', '3月', '4月', '5月']

# 后续追踪截止日（当前最新数据日）
TRACKING_END = 20260529


def _load_names():
    names = {}
    names_csv = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking', '_funds', 'stock_names.csv')
    if os.path.exists(names_csv):
        import pandas as pd
        df = pd.read_csv(names_csv, encoding='utf-8', dtype=str)
        for _, r in df.iterrows():
            names[r['code']] = r.get('name', '')
    names.update(dict(config.NAME_MAP))
    return names

NAME_MAP = _load_names()

def get_name(code):
    return NAME_MAP.get(code, code)


def read_day_bars(filepath):
    bars = []
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        for i in range(0, len(raw), 32):
            rec = struct.unpack_from('<IIIIIfII', raw, i)
            bars.append({
                'date': int(rec[0]),
                'close': rec[4] / DAY_PRICE_COEF,
            })
    except Exception:
        pass
    return bars


def calc_runup_from_pullback(bars, entry_date_int):
    """
    从进VL日往前找，找到最近一次 >20% 回调的最低点。
    返回:
      runup_pct: 从该低点到进VL日的涨幅
      low_date: 回调低点日期
      low_close: 回调低点价格
      pullback_depth: 该次回调的深度（%）
    """
    entry_idx = None
    for i, b in enumerate(bars):
        if b['date'] == entry_date_int:
            entry_idx = i
            break
    if entry_idx is None:
        return None

    if entry_idx < 60:
        return None

    entry_close = bars[entry_idx]['close']

    # 从entry_idx往前扫描，找最近一次 >20% 回调
    # 方法: 维持一个滚动最高点，当从最高点回落>20%时，标记回调
    # 然后找到这个回调的最低点

    best_low_idx = None
    best_low_close = None
    pullback_depth = 0

    peak_close = bars[entry_idx]['close']
    peak_idx = entry_idx
    in_pullback = False
    current_low = entry_close
    current_low_idx = entry_idx

    for i in range(entry_idx, max(entry_idx - 500, -1), -1):
        close = bars[i]['close']

        if close > peak_close:
            # 创了新高，之前如果是回调则结束
            if in_pullback:
                # 检查这个回调的深度
                dd = (peak_close - current_low) / peak_close * 100
                if dd >= 20:  # 这是一个 >20% 的回调
                    best_low_idx = current_low_idx
                    best_low_close = current_low
                    pullback_depth = dd
                    break  # 找到最近的 >20% 回调
                # 不到20%的回调，忽略，继续往前
            peak_close = close
            peak_idx = i
            in_pullback = False
            current_low = close
            current_low_idx = i
        elif close < current_low:
            current_low = close
            current_low_idx = i
            dd = (peak_close - close) / peak_close * 100
            if dd >= 20:
                in_pullback = True

    if best_low_idx is not None:
        runup = (entry_close - best_low_close) / best_low_close * 100
        return {
            'runup_pct': round(runup, 2),
            'low_date': bars[best_low_idx]['date'],
            'low_close': round(best_low_close, 2),
            'pullback_depth': round(pullback_depth, 2),
            'bars_since_low': entry_idx - best_low_idx,
        }

    # 没找到20%回调 —— 说明一路上涨没有像样回调
    # 这时候取250日最低点
    lookback_start = max(0, entry_idx - 250)
    min_close = float('inf')
    min_idx = lookback_start
    for i in range(lookback_start, entry_idx):
        if bars[i]['close'] < min_close:
            min_close = bars[i]['close']
            min_idx = i
    runup = (entry_close - min_close) / min_close * 100
    return {
        'runup_pct': round(runup, 2),
        'low_date': bars[min_idx]['date'],
        'low_close': round(min_close, 2),
        'pullback_depth': 0,
        'bars_since_low': entry_idx - min_idx,
        'note': '无20%回调，取250日最低点',
    }


def track_after_elimination(bars, exit_date_int):
    """
    从退出VL日往后追踪，看是否回本。
    """
    exit_idx = None
    for i, b in enumerate(bars):
        if b['date'] == exit_date_int:
            exit_idx = i
            break
    if exit_idx is None:
        for i in range(len(bars)-1, -1, -1):
            if bars[i]['date'] <= exit_date_int:
                exit_idx = i
                break
    if exit_idx is None or exit_idx >= len(bars) - 1:
        return None

    exit_close = bars[exit_idx]['close']
    after_bars = bars[exit_idx+1:]

    if not after_bars:
        return None

    max_dd = 0
    recovered = False
    peak_val = exit_close

    for b in after_bars:
        ret = (b['close'] - exit_close) / exit_close * 100
        if b['close'] > peak_val:
            peak_val = b['close']
        dd = (peak_val - b['close']) / peak_val * 100
        if dd > max_dd:
            max_dd = dd
        if b['close'] >= exit_close and not recovered:
            recovered = True

    final_ret = (after_bars[-1]['close'] - exit_close) / exit_close * 100

    return {
        'max_drawdown': round(-max_dd, 2),
        'recovered': recovered,
        'final_return': round(final_ret, 2),
        'days_tracked': len(after_bars),
    }


def analyze():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
    with open(os.path.join(output_dir, 'vl_lifetime_analysis.json'), 'r', encoding='utf-8') as f:
        data = json.load(f)

    monthly = data['monthly_top50']

    # 逐月计算新进、留存、淘汰
    all_previous = set()
    monthly_stats = {}

    for label in MONTH_LABELS:
        current = set(monthly.get(label, []))
        new_entries = current - all_previous
        retained = current & all_previous
        eliminated = all_previous - current
        monthly_stats[label] = {
            'total': len(current),
            'new': len(new_entries),
            'new_codes': sorted(new_entries),
            'retained': len(retained),
            'eliminated': len(eliminated),
            'eliminated_codes': sorted(eliminated),
        }
        all_previous.update(current)

    print("=" * 120)
    print("VL宇宙月度进出表 (2026.01 ~ 2026.05)")
    print("=" * 120)
    print()
    print(f"{'月份':<8} {'总标的':<8} {'新进':<8} {'留存':<8} {'淘汰':<8} {'月换手率':<10}")
    print("-" * 60)
    for label in MONTH_LABELS:
        s = monthly_stats[label]
        turnover = s['new'] / s['total'] * 100 if s['total'] > 0 else 0
        print(f"{label:<8} {s['total']:<8} {s['new']:<8} {s['retained']:<8} {s['eliminated']:<8} {turnover:<10.0f}%")
    print()

    # ===== 核心产出：每月新进清单 + 进前涨幅 + 命运 =====
    print("=" * 120)
    print("每月新进标的详细清单：进前涨幅 + 后续命运")
    print("=" * 120)
    print()
    print("说明: 进前涨幅 = 从最近一次20%回调最低点 到 进VL日的涨幅")
    print("      如果一路上涨无20%回调，取250日最低点")
    print()

    # 扫描所有需要的数据
    all_codes_to_check = set()
    for s in monthly_stats.values():
        all_codes_to_check.update(s['new_codes'])

    code_data = {}

    for exchange in ['sh', 'sz']:
        lday_dir = os.path.join(TDX_VIPDOC, exchange, 'lday')
        if not os.path.isdir(lday_dir):
            continue
        for fname in os.listdir(lday_dir):
            if not fname.endswith('.day'):
                continue
            code = fname[2:8]
            label = f'{exchange}{code}'
            if label not in all_codes_to_check:
                continue
            bars = read_day_bars(os.path.join(lday_dir, fname))
            if not bars:
                continue
            code_data[label] = bars

    for label_idx, label in enumerate(MONTH_LABELS):
        s = monthly_stats[label]
        new_codes = s['new_codes']
        if not new_codes:
            print(f"  [{label}] 无新进标的")
            print()
            continue

        cutoff = MONTH_CUTOFFS[label]

        # 获取数据
        rows = []
        for code in new_codes:
            bars = code_data.get(code, [])
            if not bars:
                continue

            name = get_name(code)
            runup_info = calc_runup_from_pullback(bars, cutoff)

            # 该股票在多少个月份中出现
            months_present = [m for m in MONTH_LABELS if m in monthly and code in monthly[m]]
            n_months = len(months_present)
            last_month = months_present[-1] if months_present else label

            # 后续追踪
            eliminated = False
            fate = '仍在VL'
            recovery_info = None
            if last_month != '5月' and last_month == label:
                # 当月进当月出（只出现1个月）
                eliminated = True
                recovery_info = track_after_elimination(bars, cutoff)
                fate = '当月淘汰'
            elif last_month != '5月':
                # 出现多个月后淘汰
                eliminated = True
                exit_cutoff = MONTH_CUTOFFS[last_month]
                recovery_info = track_after_elimination(bars, exit_cutoff)
                fate = f'{last_month}淘汰'

            rec = {
                'code': code,
                'name': name,
                'runup': runup_info['runup_pct'] if runup_info else None,
                'low_date': str(runup_info['low_date']) if runup_info else '?',
                'bars_since_low': runup_info['bars_since_low'] if runup_info else 0,
                'months_present': n_months,
                'months_list': '→'.join(months_present) if months_present else label,
                'fate': fate,
                'final_return': recovery_info['final_return'] if recovery_info else None,
                'recovered': recovery_info['recovered'] if recovery_info else None,
                'max_drawdown': recovery_info['max_drawdown'] if recovery_info else None,
                'note': runup_info.get('note', '') if runup_info else '',
            }
            rows.append(rec)

        # 按进前涨幅排序
        rows_with_runup = [r for r in rows if r['runup'] is not None]
        rows_with_runup.sort(key=lambda x: x['runup'])

        print(f"  [{label}] 新进 {len(new_codes)} 只 (总{s['total']}只, 淘汰{s['eliminated']}只)")
        print(f"  {'代码':<12} {'名称':<10} {'进前涨幅':<10} {'起点(回调低点)':<14} {'历时(天)':<10} {'在VL':<10} {'命运':<20} {'最终收益':<10} {'回本':<8}")
        print(f"  {'-'*110}")

        # 先打印有涨幅数据的
        for r in rows_with_runup:
            runup_str = f"{r['runup']:+.1f}%"
            low_str = r['low_date']
            days_str = f"{r['bars_since_low']}天"
            vl_str = f"{r['months_present']}个月({r['months_list']})"
            fate_str = r['fate']
            ret_str = f"{r['final_return']:+.1f}%" if r['final_return'] is not None else '-'
            rec_str = '✓' if r.get('recovered') else ('✗' if r['recovered'] is not None else '-')
            note_str = f" [{r['note']}]" if r.get('note') else ''
            print(f"  {r['code']:<12} {r['name']:<10} {runup_str:<10} {low_str:<14} {days_str:<10} {vl_str:<10} {fate_str:<20} {ret_str:<10} {rec_str}")

        # 无涨幅数据的
        no_runup = [r for r in rows if r['runup'] is None]
        for r in no_runup:
            print(f"  {r['code']:<12} {r['name']:<10} {'数据不足':<10} {'-':<14} {'-':<10} {'-':<10} {'-':<20}")

        print()

    # ===== 月度汇总统计 =====
    print("=" * 120)
    print("月度汇总统计")
    print("=" * 120)
    print()

    for label_idx, label in enumerate(MONTH_LABELS):
        s = monthly_stats[label]
        new_codes = s['new_codes']
        if not new_codes:
            continue

        cutoff = MONTH_CUTOFFS[label]
        runups = []
        fates = {'仍在VL': 0, '当月淘汰': 0, '多个月后淘汰': 0}
        recovered = 0
        dead = 0
        total_tracked = 0

        for code in new_codes:
            bars = code_data.get(code, [])
            if not bars:
                continue
            runup_info = calc_runup_from_pullback(bars, cutoff)
            if runup_info:
                runups.append(runup_info['runup_pct'])

            months_present = [m for m in MONTH_LABELS if m in monthly and code in monthly[m]]
            last_month = months_present[-1] if months_present else label

            if last_month == '5月':
                fates['仍在VL'] += 1
            elif last_month == label:
                fates['当月淘汰'] += 1
                total_tracked += 1
                rec = track_after_elimination(bars, cutoff)
                if rec:
                    if rec['recovered']:
                        recovered += 1
                    if rec['max_drawdown'] < -25:
                        dead += 1
            else:
                fates['多个月后淘汰'] += 1
                total_tracked += 1
                exit_cutoff = MONTH_CUTOFFS[last_month]
                rec = track_after_elimination(bars, exit_cutoff)
                if rec:
                    if rec['recovered']:
                        recovered += 1
                    if rec['max_drawdown'] < -25:
                        dead += 1

        avg_runup = sum(runups) / len(runups) if runups else 0
        print(f"  [{label}]")
        print(f"    新进 {len(new_codes)} 只 | 平均进前涨幅: {avg_runup:+.1f}%")
        print(f"       → 仍在VL: {fates['仍在VL']} | 当月淘汰: {fates['当月淘汰']} | 多个月后淘汰: {fates['多个月后淘汰']}")
        if total_tracked > 0:
            print(f"       → 淘汰组追综: 回本{recovered}/{total_tracked} ({recovered/total_tracked*100:.0f}%) | 真死亡{dead}/{total_tracked} ({dead/total_tracked*100:.0f}%)")
        print()

    # 保存CSV格式数据
    import csv
    csv_path = os.path.join(output_dir, 'vl_monthly_entries.csv')
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['月份', '代码', '名称', '进前涨幅%', '起点日期', '历时天数', '在VL月数', '在VL路径', '命运', '最终收益%', '回本', '最大回撤%'])
        for label_idx, label in enumerate(MONTH_LABELS):
            s = monthly_stats[label]
            cutoff = MONTH_CUTOFFS[label]
            for code in s['new_codes']:
                bars = code_data.get(code, [])
                if not bars:
                    continue
                name = get_name(code)
                runup_info = calc_runup_from_pullback(bars, cutoff)
                runup = runup_info['runup_pct'] if runup_info else ''
                low_date = runup_info['low_date'] if runup_info else ''
                bars_since = runup_info['bars_since_low'] if runup_info else ''

                months_present = [m for m in MONTH_LABELS if m in monthly and code in monthly[m]]
                n_months = len(months_present)
                months_list = '→'.join(months_present) if months_present else ''

                last_month = months_present[-1] if months_present else label
                if last_month == '5月':
                    fate = '仍在VL'
                    ret = ''
                    rec = ''
                    mdd = ''
                elif last_month == label:
                    fate = '当月淘汰'
                    ri = track_after_elimination(bars, cutoff)
                    ret = ri['final_return'] if ri else ''
                    rec = '是' if ri and ri['recovered'] else ('否' if ri else '')
                    mdd = ri['max_drawdown'] if ri else ''
                else:
                    fate = f'{last_month}淘汰'
                    exit_cutoff = MONTH_CUTOFFS[last_month]
                    ri = track_after_elimination(bars, exit_cutoff)
                    ret = ri['final_return'] if ri else ''
                    rec = '是' if ri and ri['recovered'] else ('否' if ri else '')
                    mdd = ri['max_drawdown'] if ri else ''

                writer.writerow([label, code, name, runup, low_date, bars_since, n_months, months_list, fate, ret, rec, mdd])

    print(f"  CSV已保存: {csv_path}")
    print()

    # ===== 总结 =====
    print("=" * 120)
    print("核心发现")
    print("=" * 120)
    print()

    # 所有新进的平均涨幅
    all_runups = []
    for label in MONTH_LABELS:
        cutoff = MONTH_CUTOFFS[label]
        for code in monthly_stats[label]['new_codes']:
            bars = code_data.get(code, [])
            if not bars:
                continue
            runup_info = calc_runup_from_pullback(bars, cutoff)
            if runup_info:
                all_runups.append({
                    'code': code,
                    'label': label,
                    'runup': runup_info['runup_pct'],
                })

    # 不同涨幅区间对应的淘汰后回本率
    buckets = {'<0%': [], '0-30%': [], '30-60%': [], '60-100%': [], '100-200%': [], '>200%': []}

    for item in all_runups:
        r = item['runup']
        code = item['code']
        label = item['label']
        cutoff = MONTH_CUTOFFS[label]

        if r < 0: bucket = '<0%'
        elif r < 30: bucket = '0-30%'
        elif r < 60: bucket = '30-60%'
        elif r < 100: bucket = '60-100%'
        elif r < 200: bucket = '100-200%'
        else: bucket = '>200%'

        # 检查后续命运
        months_present = [m for m in MONTH_LABELS if m in monthly and code in monthly[m]]
        last_month = months_present[-1] if months_present else label
        bars = code_data.get(code, [])

        recovered = None
        if last_month != '5月':
            exit_cutoff = MONTH_CUTOFFS[last_month]
            ri = track_after_elimination(bars, exit_cutoff) if bars else None
            recovered = ri['recovered'] if ri else None

        buckets[bucket].append({
            'code': code,
            'runup': r,
            'recovered': recovered,
        })

    print("  进前涨幅 vs 淘汰后回本率:")
    print(f"  {'涨幅区间':<15} {'数量':<8} {'淘汰数':<8} {'回本率':<10}")
    print(f"  {'-'*45}")
    for bucket_name in ['<0%', '0-30%', '30-60%', '60-100%', '100-200%', '>200%']:
        items = buckets[bucket_name]
        n = len(items)
        n_eliminated = sum(1 for i in items if i['recovered'] is not None)
        n_recovered = sum(1 for i in items if i['recovered'] is True)
        rec_rate = n_recovered / n_eliminated * 100 if n_eliminated > 0 else 0
        print(f"  {bucket_name:<15} {n:<8} {n_eliminated:<8} {rec_rate:<10.0f}%")
    print()

    # 结论
    print("  【结论】")
    print("  进前涨幅越大的股票，淘汰后回本率越低 —— 因为涨得多透支了未来。")
    print("  但更重要的是：进前涨幅<30%的股票仍会淘汰，说明VL宇宙本身就是一个风险信号。")
    print()


if __name__ == '__main__':
    analyze()

# -*- coding: utf-8 -*-
"""
VL宇宙月度进出表 V2 — 逐只处理版（低内存）
"""

import struct
import json
import os
import sys
import csv
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../..'))
import config

TDX_VIPDOC = os.path.join(config.TDX_ROOT, 'vipdoc')
DAY_PRICE_COEF = 1000

MONTH_CUTOFFS = {'1月':20260129,'2月':20260213,'3月':20260331,'4月':20260430,'5月':20260529}
MONTH_LABELS = ['1月','2月','3月','4月','5月']

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

def read_bars(filepath):
    bars = []
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        for i in range(0, len(raw), 32):
            rec = struct.unpack_from('<IIIIIfII', raw, i)
            bars.append({
                'date': int(rec[0]),
                'open': rec[1]/DAY_PRICE_COEF,
                'high': rec[2]/DAY_PRICE_COEF,
                'low': rec[3]/DAY_PRICE_COEF,
                'close': rec[4]/DAY_PRICE_COEF,
            })
    except: pass
    return bars

def compute_cci(high, low, close, period=14):
    tp = [(h+l+c)/3 for h,l,c in zip(high,low,close)]
    cci = [0.0]*period
    for i in range(period, len(tp)):
        ma = sum(tp[i-period:i])/period
        md = sum(abs(tp[i-period+k]-ma) for k in range(period))/period
        cci.append((tp[i]-ma)/(0.015*md) if md != 0 else 0.0)
    return cci

def detect_star_buy(bars, max_signals=20):
    """简化★买检测，最多保留max_signals个最近信号"""
    n = len(bars)
    if n < 100:
        return []

    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    closes = [b['close'] for b in bars]
    cci = compute_cci(highs, lows, closes)

    signals = []
    i = 60
    processed_until = -1  # 避免无限循环：跟踪已处理过的位置
    while i < n and len(signals) < max_signals:
        if cci[i] <= -200:
            # 找极值区域最低点
            extreme_idx = i
            extreme_val = cci[i]
            for j in range(i, max(50, i-30), -1):
                if cci[j] < extreme_val:
                    extreme_val = cci[j]
                    extreme_idx = j

            # 已处理过这个极值，跳过
            if extreme_idx <= processed_until:
                i += 1
                continue

            # 找CCI上穿-100
            cross_idx = None
            for j in range(extreme_idx, min(n, extreme_idx+80)):
                if cci[j] > -100 and cci[j-1] <= -100:
                    cross_idx = j
                    break
            if cross_idx is None:
                i += 1; continue

            processed_until = cross_idx

            # 底背驰简化检测
            # 找前一个同级别极值 (往前80根)
            prev_extreme = None
            prev_price_low = None
            for j in range(extreme_idx-5, max(50, extreme_idx-80), -1):
                if cci[j] <= -150:
                    if prev_extreme is None or cci[j] < prev_extreme:
                        prev_extreme = cci[j]
                        pk = min((k for k in range(j, min(j+10,n))), key=lambda k: bars[k]['low'])
                        prev_price_low = bars[pk]['low']

            # 当前CCI极值后价格最低点
            current_price_low = min(bars[k]['low'] for k in range(extreme_idx, min(cross_idx+1, n)))

            # 背驰: 价格创新低 or CCI够深
            has_div = False
            if prev_extreme is not None and prev_price_low is not None:
                if current_price_low < prev_price_low - 0.01 and extreme_val > prev_extreme + 5:
                    has_div = True
            if not has_div and extreme_val >= -250 and cci[cross_idx] > -80:
                has_div = True

            if has_div:
                signals.append({
                    'idx': cross_idx, 'date': bars[cross_idx]['date'],
                    'close': closes[cross_idx], 'cci': round(cci[cross_idx],1),
                    'extreme_cci': round(extreme_val,1),
                })

            i = max(i + 1, cross_idx + 1)  # 确保i永远向前
        else:
            i += 1

    return signals[-max_signals:]  # 只保留最近的

def find_star_entry(bars, entry_date_int, signals):
    """找进VL前最近一次★买+250-500均线企稳"""
    entry_idx = None
    for i,b in enumerate(bars):
        if b['date'] == entry_date_int:
            entry_idx = i; break
    if entry_idx is None:
        return None

    closes = [b['close'] for b in bars]
    ma250 = [None]*250
    ma500 = [None]*500
    for i in range(250, len(closes)):
        ma250.append(sum(closes[i-250:i])/250)
    for i in range(500, len(closes)):
        ma500.append(sum(closes[i-500:i])/500)

    valid = []
    for sig in signals:
        idx = sig['idx']
        if idx >= entry_idx: continue
        c = closes[idx]
        n250 = ma250[idx] is not None and abs(c-ma250[idx])/ma250[idx]*100 <= 10
        n500 = ma500[idx] is not None and abs(c-ma500[idx])/ma500[idx]*100 <= 10
        if n250 or n500:
            valid.append({
                'idx': idx, 'date': sig['date'], 'buy_close': c,
                'runup': (closes[entry_idx]-c)/c*100,
                'near_250': n250, 'near_500': n500,
                'd250': round((c-ma250[idx])/ma250[idx]*100,1) if ma250[idx] else None,
                'd500': round((c-ma500[idx])/ma500[idx]*100,1) if ma500[idx] else None,
                'days_to_entry': entry_idx-idx,
            })

    if not valid: return None
    valid.sort(key=lambda x: -x['idx'])
    return valid[0]

def find_ma500_entry(bars, entry_date_int):
    """降级: 找首次站上MA500"""
    closes = [b['close'] for b in bars]
    entry_idx = None
    for i,b in enumerate(bars):
        if b['date'] == entry_date_int:
            entry_idx = i; break
    if entry_idx is None or entry_idx < 500:
        return None

    ma500 = [None]*500
    for i in range(500, len(closes)):
        ma500.append(sum(closes[i-500:i])/500)

    for i in range(500, entry_idx):
        if ma500[i] and closes[i] >= ma500[i] and (ma500[i-1] is not None and closes[i-1] < ma500[i-1]):
            runup = (closes[entry_idx]-closes[i])/closes[i]*100
            return {'idx': i, 'date': bars[i]['date'], 'buy_close': closes[i], 'runup': runup}
    return None

def track_fate(bars, exit_date_int):
    exit_idx = None
    for i in range(len(bars)-1, -1, -1):
        if bars[i]['date'] <= exit_date_int:
            exit_idx = i; break
    if exit_idx is None or exit_idx >= len(bars)-1:
        return None

    exit_c = bars[exit_idx]['close']
    peak = exit_c
    max_dd = 0
    recovered = False
    for b in bars[exit_idx+1:]:
        if b['close'] > peak: peak = b['close']
        dd = (peak-b['close'])/peak*100
        if dd > max_dd: max_dd = dd
        if b['close'] >= exit_c and not recovered: recovered = True
    final = (bars[-1]['close']-exit_c)/exit_c*100
    return {'max_drawdown': round(-max_dd,2), 'recovered': recovered, 'final_return': round(final,2)}


def analyze():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
    with open(os.path.join(output_dir, 'vl_lifetime_analysis.json'), 'r', encoding='utf-8') as f:
        data = json.load(f)
    monthly = data['monthly_top50']

    # 逐月新进
    all_prev = set()
    new_by_month = {}
    for label in MONTH_LABELS:
        cur = set(monthly.get(label, []))
        new_by_month[label] = sorted(cur - all_prev)
        all_prev.update(cur)

    all_codes = set()
    for v in new_by_month.values():
        all_codes.update(v)

    print("=" * 120)
    print("VL宇宙月度进出表 V2 (★买+250-500均线框架)")
    print("=" * 120)
    print()

    # 统计用
    count_star = 0
    count_ma500 = 0
    count_none = 0
    total = len(all_codes)

    # 每月的行
    month_rows = {label: [] for label in MONTH_LABELS}
    csv_rows = []

    scan_i = 0
    for exchange in ['sh','sz']:
        ldir = os.path.join(TDX_VIPDOC, exchange, 'lday')
        if not os.path.isdir(ldir): continue
        for fname in os.listdir(ldir):
            if not fname.endswith('.day'): continue
            code = fname[2:8]
            label = f'{exchange}{code}'
            if label not in all_codes: continue

            scan_i += 1
            if scan_i % 30 == 0:
                print(f"  进度: {scan_i}/{total}  ★买检测率:{count_star}/{scan_i}", end='\r')

            bars = read_bars(os.path.join(ldir, fname))
            if len(bars) < 600:
                count_none += 1; continue

            signals = detect_star_buy(bars, max_signals=15)

            # 找到这个标的是哪个月新进的
            entry_month = None
            for m in MONTH_LABELS:
                if label in new_by_month[m]:
                    entry_month = m; break
            if entry_month is None: continue
            cutoff = MONTH_CUTOFFS[entry_month]

            # 阶段1: ★买+均线
            best = find_star_entry(bars, cutoff, signals)

            entry_type = ''
            start_date = '-'
            runup_str = '-'

            if best:
                entry_type = f"★买MA250:{best['d250']}%/MA500:{best['d500']}%"
                start_date = str(best['date'])
                runup_str = f"{best['runup']:+.1f}%"
                count_star += 1
            else:
                # 阶段2: 站上MA500
                ma5 = find_ma500_entry(bars, cutoff)
                if ma5:
                    entry_type = "站上MA500(无★买)"
                    start_date = str(ma5['date'])
                    runup_str = f"{ma5['runup']:+.1f}%*"
                    count_ma500 += 1
                else:
                    entry_type = "无有效起点"
                    count_none += 1

            # 命运
            months_present = [m for m in MONTH_LABELS if m in monthly and label in monthly[m]]
            last_month = months_present[-1] if months_present else entry_month

            fate = '仍在VL'
            fate_info = None
            if last_month != '5月':
                fi = track_fate(bars, MONTH_CUTOFFS[last_month])
                fate_info = fi
                fate = '当月淘汰' if last_month == entry_month else f'{last_month}淘汰'

            ret_str = f"{fate_info['final_return']:+.1f}%" if fate_info else '-'
            rec_str = 'Y' if fate_info and fate_info['recovered'] else ('X' if fate_info else '-')
            mdd_str = f"{fate_info['max_drawdown']:.1f}%" if fate_info else '-'

            row = {
                'code': label, 'name': get_name(label),
                'entry_type': entry_type, 'start_date': start_date,
                'runup': runup_str,
                'vl_path': '→'.join(months_present) if months_present else entry_month,
                'fate': fate, 'final_return': ret_str,
                'recovered': rec_str, 'max_drawdown': mdd_str,
            }
            month_rows[entry_month].append(row)

            csv_rows.append({
                '月份': entry_month, '代码': label, '名称': get_name(label),
                '起点类型': entry_type, '起点日期': start_date, '进前涨幅': runup_str,
                '在VL路径': '→'.join(months_present) if months_present else entry_month,
                '命运': fate, '最终收益': ret_str, '回本': rec_str, '最大回撤': mdd_str,
            })

    print(f"\n  完成: {total} 只")
    print(f"    ★买+均线企稳: {count_star} ({count_star/total*100:.0f}%)")
    print(f"    仅站上MA500:  {count_ma500} ({count_ma500/total*100:.0f}%)")
    print(f"    无有效起点:   {count_none} ({count_none/total*100:.0f}%)")
    print()

    # 输出每个月的表
    for label in MONTH_LABELS:
        rows = month_rows[label]
        if not rows: continue
        # 排序: 有★买的排前面
        def sort_key(r):
            if '★买' in r['entry_type']: return 0
            if 'MA500' in r['entry_type']: return 1
            return 2
        rows.sort(key=sort_key)

        print(f"  [{label}] 新进 {len(new_by_month[label])} 只")
        print(f"  {'代码':<12} {'名称':<10} {'进前涨幅':<12} {'起点日期':<14} {'起点依据':<32} {'在VL':<16} {'命运':<10} {'最终':<10} {'回本':<6}")
        print(f"  {'-'*130}")
        for r in rows:
            print(f"  {r['code']:<12} {r['name']:<10} {r['runup']:<12} {r['start_date']:<14} {r['entry_type']:<32} {r['vl_path']:<16} {r['fate']:<10} {r['final_return']:<10} {r['recovered']:<6}")
        print()

    # 月度汇总
    print("=" * 120)
    print("月度进出汇总")
    print("=" * 120)
    print(f"{'月份':<8} {'新进':<8} {'★买起点':<10} {'仅MA500':<10} {'无起点':<8} {'仍在VL':<10} {'淘汰回本':<10} {'真死亡':<8}")
    print("-" * 72)
    for label in MONTH_LABELS:
        rows = month_rows[label]
        n_new = len(new_by_month[label])
        n_star = sum(1 for r in rows if '★买' in r['entry_type'])
        n_ma5 = sum(1 for r in rows if '站上MA500' in r['entry_type'])
        n_none = sum(1 for r in rows if '无有效' in r['entry_type'])
        n_alive = sum(1 for r in rows if r['fate'] == '仍在VL')
        n_rec = sum(1 for r in rows if r['recovered'] == 'Y')
        n_tracked = sum(1 for r in rows if r['recovered'] != '-')
        n_dead = sum(1 for r in rows if r['recovered'] == 'X')
        print(f"{label:<8} {n_new:<8} {n_star:<10} {n_ma5:<10} {n_none:<8} {n_alive:<10} {f'{n_rec}/{n_tracked}':<10} {n_dead:<8}")
    print()

    # 验证三环集团
    print("=" * 120)
    print("验证: 三环集团(sz300408)")
    print("=" * 120)
    print()
    sanhuan_bars = None
    for exchange in ['sh','sz']:
        ldir = os.path.join(TDX_VIPDOC, exchange, 'lday')
        for fname in os.listdir(ldir):
            if '300408' in fname:
                sanhuan_bars = read_bars(os.path.join(ldir, fname))
                break
    if sanhuan_bars:
        signals = detect_star_buy(sanhuan_bars, max_signals=30)
        print(f"  ★买信号数: {len(signals)}")
        for sig in signals[-6:]:
            print(f"    {sig['date']} close={sig['close']:.2f} CCI={sig['cci']:.1f} 极值={sig['extreme_cci']:.1f}")
        best = find_star_entry(sanhuan_bars, 20260529, signals)
        if best:
            print(f"\n  进VL前最近★买+均线企稳:")
            print(f"    日期: {best['date']} close={best['buy_close']:.2f}")
            print(f"    距MA250: {best['d250']}% | 距MA500: {best['d500']}%")
            print(f"    到5月进VL涨幅: {best['runup']:+.1f}% ({best['days_to_entry']}个交易日)")
        else:
            print("   ★买+均线条件未满足")

    # 保存CSV
    csv_path = os.path.join(output_dir, 'vl_monthly_entries_v2.csv')
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['月份','代码','名称','起点类型','起点日期','进前涨幅','在VL路径','命运','最终收益','回本','最大回撤'])
        w.writeheader()
        w.writerows(csv_rows)
    print(f"\n  CSV已保存: {csv_path}")

if __name__ == '__main__':
    analyze()

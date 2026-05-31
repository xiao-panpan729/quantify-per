# -*- coding: utf-8 -*-
"""
VL宇宙 — 突破级别分析

问题:
  短暂辉煌股票进VL前涨了+60%+，这些涨幅中突破的是什么级别的新高？
  60日? 120日? 180日? 250日? ATH?
  突破后还能涨多少？

方法:
  对每个短暂辉煌股票:
    1. 找进VL前6个月的起点(最低点)
    2. 从起点到进VL日，标记每根bar是否突破各类新高
    3. 记录: 突破各级新高后，到进VL日还有多少涨幅
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

# 要检查的新高级别（交易日窗口）
NH_LEVELS = {
    'NH60': 60,
    'NH120': 120,
    'NH180': 180,
    'NH250': 250,
    'NH500': 500,
    'ATH': -1,  # -1 = 全历史
}

def _load_names():
    names = {}
    names_csv = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking', 'stock_names.csv')
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


def find_breakout_levels(bars, entry_date_int):
    """
    对一段价格序列，从起点到entry_date，找出每次突破新高是在什么位置。

    返回:
      run_start: 本轮上涨起点日期
      entry_idx: 进VL日的索引
      breakouts: {level_name: {'first_break_idx': N, 'first_break_close': X, 'first_break_date': D,
                                'ret_after_break': Y%}}
    """
    # 找到进VL日索引
    entry_idx = None
    for i, b in enumerate(bars):
        if b['date'] == entry_date_int:
            entry_idx = i
            break
    if entry_idx is None:
        # 找最接近的
        for i in range(len(bars)-1, -1, -1):
            if bars[i]['date'] <= entry_date_int:
                entry_idx = i
                break
    if entry_idx is None or entry_idx < 250:
        return None  # 数据不够

    # 找起点：从entry_idx往回看，找最低点（60日均线以下的最低点）
    lookback = min(entry_idx, 250)  # 最多看250根
    start_idx = entry_idx - lookback
    min_close = float('inf')
    min_idx = start_idx

    for i in range(start_idx, entry_idx):
        if bars[i]['close'] < min_close:
            min_close = bars[i]['close']
            min_idx = i

    # 确认这段涨幅 >= 30%（否则不算"大幅上涨"）
    entry_close = bars[entry_idx]['close']
    total_gain = (entry_close - min_close) / min_close * 100
    if total_gain < 30:
        return None

    # 遍历从起点到进VL日，计算每根bar的各类新高
    breakouts = {}
    for level_name, window in NH_LEVELS.items():
        first_break_idx = None
        first_break_close = None
        first_break_date = None

        for i in range(min_idx, entry_idx + 1):
            close = bars[i]['close']

            if window == -1:  # ATH: 全历史最高
                # 从第一根bar到当前bar的最高
                hist_high = max(b['close'] for b in bars[:i+1])
                if close >= hist_high and (i == 0 or close != bars[i-1]['close']):
                    prev_hist_high = max(b['close'] for b in bars[:i]) if i > 0 else 0
                    if close > prev_hist_high:
                        if first_break_idx is None:
                            first_break_idx = i
                            first_break_close = close
                            first_break_date = bars[i]['date']
            else:
                # 窗口新高: 过去window根bar的最高
                look_start = max(0, i - window)
                prev_look_start = max(0, i - window - 1)
                window_high = max(bars[j]['close'] for j in range(look_start, i)) if i > look_start else 0
                prev_window_high = max(bars[j]['close'] for j in range(prev_look_start, i-1)) if i > look_start + 1 else 0
                current_high = bars[i]['close']

                if current_high > window_high and current_high > prev_window_high:
                    if first_break_idx is None:
                        first_break_idx = i
                        first_break_close = close
                        first_break_date = bars[i]['date']

        if first_break_idx is not None:
            # 突破后到进VL日的涨幅
            ret_after = (bars[entry_idx]['close'] - first_break_close) / first_break_close * 100
            # 突破日到进VL日的天数
            days_to_entry = entry_idx - first_break_idx

            breakouts[level_name] = {
                'first_break_idx': first_break_idx,
                'first_break_close': round(first_break_close, 2),
                'first_break_date': first_break_date,
                'ret_after_break': round(ret_after, 2),
                'days_to_entry': days_to_entry,
            }

    return {
        'run_start_idx': min_idx,
        'run_start_date': bars[min_idx]['date'],
        'entry_idx': entry_idx,
        'entry_close': round(entry_close, 2),
        'total_gain': round(total_gain, 2),
        'entry_date': entry_date_int,
        'breakouts': breakouts,
    }


def analyze():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
    with open(os.path.join(output_dir, 'vl_lifetime_analysis.json'), 'r', encoding='utf-8') as f:
        data = json.load(f)

    brief_codes = set(data['brief_glory'])
    stable_codes = set(data['stable'])
    evergreen_codes = set(data['evergreen'])
    oscillating_codes = set(data['oscillating'])
    newcomer_codes = set(data['newcomer'])
    monthly = data['monthly_top50']

    # 构建每个股票的进VL日期
    entry_dates = {}
    for code in brief_codes | stable_codes | evergreen_codes | oscillating_codes | newcomer_codes:
        for label in ['1月', '2月', '3月', '4月', '5月']:
            if label in monthly and code in monthly[label]:
                entry_dates[code] = MONTH_CUTOFFS[label]
                break

    print("=" * 100)
    print("突破级别分析：短暂辉煌股票进VL前突破的是什么级别的新高？")
    print("=" * 100)
    print()

    # 扫描
    results = {}
    for exchange in ['sh', 'sz']:
        lday_dir = os.path.join(TDX_VIPDOC, exchange, 'lday')
        if not os.path.isdir(lday_dir):
            continue
        for fname in os.listdir(lday_dir):
            if not fname.endswith('.day'):
                continue
            code = fname[2:8]
            label = f'{exchange}{code}'

            # 只分析几个组
            if label not in entry_dates:
                continue

            bars = read_day_bars(os.path.join(lday_dir, fname))
            if not bars or len(bars) < 250:
                continue

            entry_date = entry_dates[label]
            breakout_info = find_breakout_levels(bars, entry_date)
            if breakout_info:
                results[label] = breakout_info
                results[label]['category'] = (
                    'brief_glory' if label in brief_codes else
                    'stable' if label in stable_codes else
                    'evergreen' if label in evergreen_codes else
                    'oscillating' if label in oscillating_codes else
                    'newcomer'
                )

    print(f"  分析完成: {len(results)} 只 (其中短暂辉煌 {sum(1 for r in results.values() if r['category']=='brief_glory')} 只)")
    print()

    # 分类汇总
    for group_name, group_key in [
        ("短暂辉煌(125只)", "brief_glory"),
        ("稳定股(4只)", "stable"),
        ("常青树(2只)", "evergreen"),
        ("进出反复(11只)", "oscillating"),
        ("新来者(48只)", "newcomer"),
    ]:
        group = {c: r for c, r in results.items() if r['category'] == group_key}
        if not group:
            continue

        print(f"  [{group_name}] — {len(group)}只有效数据")

        # 各组突破统计
        # 统计每种新高被突破的股票数
        level_counts = defaultdict(int)
        level_avg_ret = defaultdict(list)  # 突破后平均涨幅

        for code, info in group.items():
            for level_name in NH_LEVELS:
                if level_name in info['breakouts']:
                    level_counts[level_name] += 1
                    level_avg_ret[level_name].append(
                        info['breakouts'][level_name]['ret_after_break']
                    )

        # 平均总涨幅
        avg_total_gain = sum(r['total_gain'] for r in group.values()) / len(group)
        print(f"     平均进VL前涨幅: {avg_total_gain:.1f}%")

        # 各新高级别的突破率 + 突破后剩余涨幅
        for level_name in ['NH60', 'NH120', 'NH180', 'NH250', 'NH500', 'ATH']:
            count = level_counts.get(level_name, 0)
            pct = count / len(group) * 100
            rets = level_avg_ret.get(level_name, [])
            avg_ret_after = sum(rets) / len(rets) if rets else 0
            # 突破后剩余天数
            days = [info['breakouts'][level_name]['days_to_entry']
                    for info in group.values() if level_name in info.get('breakouts', {})]
            avg_days = sum(days) / len(days) if days else 0
            print(f"     {level_name}: {count}/{len(group)} ({pct:.0f}%)突破 | 突破后平均再涨{avg_ret_after:.1f}% | {avg_days:.0f}个交易日后进VL")

        # 最常突破的组合
        print()
        # 统计这组股票里最常见的新高突破组合
        combo_count = defaultdict(int)
        for info in group.values():
            broken = tuple(sorted([k for k in NH_LEVELS if k in info.get('breakouts', {})]))
            combo_count[broken] += 1
        top_combos = sorted(combo_count.items(), key=lambda x: -x[1])[:3]
        if top_combos:
            print(f"     最常见突破组合:")
            for combo, cnt in top_combos:
                print(f"       {', '.join(combo)}: {cnt}/{len(group)} ({cnt/len(group)*100:.0f}%)")
        print()

    # 深度分析: 短暂辉煌中"突破ATH" vs "未突破ATH"的后续表现差异
    print("=" * 100)
    print("深度：突破ATH vs 未突破ATH — 后续命运差异")
    print("=" * 100)
    print()

    brief_group = {c: r for c, r in results.items() if r['category'] == 'brief_glory'}

    # 读取淘汰后数据的简单方法 — 复用entry和当前close
    # already have entry_close in results

    broke_ath = []
    not_broke_ath = []

    for code, info in brief_group.items():
        has_ath = 'ATH' in info.get('breakouts', {})
        total_gain = info['total_gain']
        ret_after_60 = info['breakouts'].get('NH60', {}).get('ret_after_break', 0)
        ret_after_ath = info['breakouts'].get('ATH', {}).get('ret_after_break', 0)

        record = {
            'total_gain': total_gain,
            'ret_after_60': ret_after_60,
            'ret_after_ath': ret_after_ath,
        }

        if has_ath:
            broke_ath.append(record)
        else:
            not_broke_ath.append(record)

    if broke_ath:
        n = len(broke_ath)
        avg_tot = sum(r['total_gain'] for r in broke_ath) / n
        avg_60 = sum(r['ret_after_60'] for r in broke_ath) / n
        avg_ath = sum(r['ret_after_ath'] for r in broke_ath) / n
        print(f"  突破ATH ({n}只):")
        print(f"    平均总涨幅: {avg_tot:.1f}%")
        print(f"    突破NH60后平均再涨: {avg_60:.1f}%")
        print(f"    突破ATH后平均再涨: {avg_ath:.1f}%")

    if not_broke_ath:
        n = len(not_broke_ath)
        avg_tot = sum(r['total_gain'] for r in not_broke_ath) / n
        avg_60 = sum(r['ret_after_60'] for r in not_broke_ath) / n
        print(f"\n  未突破ATH ({n}只):")
        print(f"    平均总涨幅: {avg_tot:.1f}%")
        print(f"    突破NH60后平均再涨: {avg_60:.1f}%")
        print(f"    (未突破ATH, 所以ATH后涨幅不适用)")

    print()

    # 关键案例展示
    print("=" * 100)
    print("关键案例：短暂辉煌中[突破ATH后]涨幅最大/最小的10只")
    print("=" * 100)
    print()

    with_ath = [(c, r) for c, r in brief_group.items() if 'ATH' in r.get('breakouts', {})]
    with_ath.sort(key=lambda x: x[1]['breakouts']['ATH']['ret_after_break'])

    print("  突破ATH后剩余空间最小的10只（突破即见顶）:")
    for code, r in with_ath[:10]:
        ath = r['breakouts']['ATH']
        name = get_name(code)
        print(f"    {code}({name}) 总涨{r['total_gain']:.0f}% → ATH突破后再涨{ath['ret_after_break']:.1f}% ({ath['days_to_entry']}天后进VL)")

    print()
    print("  突破ATH后剩余空间最大的10只（突破后还有空间）:")
    for code, r in with_ath[-10:]:
        ath = r['breakouts']['ATH']
        name = get_name(code)
        print(f"    {code}({name}) 总涨{r['total_gain']:.0f}% → ATH突破后再涨{ath['ret_after_break']:.1f}% ({ath['days_to_entry']}天后进VL)")

    print()

    # 对比: 新来者(5月入场)的突破情况
    print("=" * 100)
    print("对比：新来者(48只)进VL前的突破情况 — 它们也会重蹈覆辙吗？")
    print("=" * 100)
    print()

    new_group = {c: r for c, r in results.items() if r['category'] == 'newcomer'}
    if new_group:
        avg_gain = sum(r['total_gain'] for r in new_group.values()) / len(new_group)
        new_ath = sum(1 for r in new_group.values() if 'ATH' in r.get('breakouts', {}))
        new_250 = sum(1 for r in new_group.values() if 'NH250' in r.get('breakouts', {}))
        print(f"  平均进VL前涨幅: {avg_gain:.1f}%")
        print(f"  突破ATH: {new_ath}/{len(new_group)} ({new_ath/len(new_group)*100:.0f}%)")
        print(f"  突破250日新高: {new_250}/{len(new_group)} ({new_250/len(new_group)*100:.0f}%)")

        # 对比: 新来者的avg gain vs brief glory的avg gain
        brief_avg = sum(r['total_gain'] for r in brief_group.values()) / len(brief_group) if brief_group else 0
        print(f"\n  对比短暂辉煌进VL前涨幅: {brief_avg:.1f}%")
        print(f"  新来者进VL前涨幅: {avg_gain:.1f}%")
        print(f"  差异: {avg_gain - brief_avg:+.1f}个百分点")
        if avg_gain < brief_avg:
            print(f"  → 新来者涨幅小于短暂辉煌组，可能还有空间")
        else:
            print(f"  → 新来者涨幅大于短暂辉煌组，风险更高")

    # 保存
    summary = {
        'brief_glory': {
            'n_total': len(brief_group),
            'avg_pre_vl_gain': round(avg_total_gain, 1) if 'avg_total_gain' in dir() else 0,
            'breakout_rate': {ln: round(level_counts.get(ln, 0)/len(brief_group)*100, 1) if brief_group else 0 for ln in NH_LEVELS},
            'avg_ret_after_breakout': {ln: round(sum(level_avg_ret.get(ln, [0]))/len(level_avg_ret.get(ln, [1])) if level_avg_ret.get(ln) else 0, 1) for ln in NH_LEVELS},
        }
    }
    with open(os.path.join(output_dir, 'vl_breakout_analysis.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n  已保存: vl_breakout_analysis.json")


if __name__ == '__main__':
    analyze()

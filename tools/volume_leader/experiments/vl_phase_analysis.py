# -*- coding: utf-8 -*-
"""
VL宇宙生命周期 — 三段胜率分析 + 凸性仓位模拟

对"短暂辉煌"(125只)拆三段:
  进VL前(3个月) → 在VL中 → 被淘汰后(到现在)

分析:
  1. 每段的价格回报/胜率/回撤
  2. 不被止损能否回本
  3. 凸性仓位能否救活淘汰组
"""

import struct
import json
import os
import sys
from datetime import datetime
from collections import defaultdict

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)
sys.path.insert(0, os.path.join(_script_dir, '..'))
sys.path.insert(0, os.path.join(_script_dir, '../..'))
sys.path.insert(0, os.path.join(_script_dir, '../../..'))
import config

TDX_VIPDOC = os.path.join(config.TDX_ROOT, 'vipdoc')
DAY_PRICE_COEF = 1000

# 月截止日期（和 vl_lifetime_analysis.py 一致）
MONTH_CUTOFFS = {
    '1月': 20260129, '2月': 20260213, '3月': 20260331,
    '4月': 20260430, '5月': 20260529,
}
MONTH_LABELS = ['1月', '2月', '3月', '4月', '5月']


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
    """读取 .day 文件，返回 date_int -> close 的列表"""
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


def find_bar_at_or_before(bars, date_int):
    """找到 <= date_int 的最近 bar"""
    for b in reversed(bars):
        if b['date'] <= date_int:
            return b
    return None


def find_bar_at_or_after(bars, date_int):
    """找到 >= date_int 的最早 bar"""
    for b in bars:
        if b['date'] >= date_int:
            return b
    return None


def simulate_convex_sizing(price_series, entry_price, entry_idx=0):
    """
    模拟凸性仓位: 每跌X%加仓Y%，用二次凸函数分配仓位。
    weight(dd) = 1 + convex_coef * dd^2
    其中 dd = 浮亏比例 (正数)
    """
    if not price_series:
        return {'final_return': 0, 'max_drawdown': 0}

    # 简单凸函数: 每多跌5%，仓位加倍（二次型）
    # weight = 1 + 40 * dd^2 (dd=0.05时weight=1.1, dd=0.1时weight=1.4, dd=0.2时weight=2.6)
    convex_coef = 40

    total_shares = 0
    total_cost = 0
    peak_value = 0
    max_dd = 0

    for i, price in enumerate(price_series):
        if i == entry_idx:
            # 初始建仓 1 单位
            shares = 1 / price
            total_shares += shares
            total_cost += 1
        elif i > entry_idx and price < price_series[entry_idx]:
            # 浮亏中: 按凸函数加仓
            dd = (price_series[entry_idx] - price) / price_series[entry_idx]  # 0~1
            weight = 1 + convex_coef * (dd ** 2)
            add_invest = 0.1 * weight  # 每次加仓基准0.1单位
            shares = add_invest / price
            total_shares += shares
            total_cost += add_invest

        current_value = total_shares * price
        if current_value > peak_value:
            peak_value = current_value
        dd = (peak_value - current_value) / peak_value * 100 if peak_value > 0 else 0
        if dd > max_dd:
            max_dd = dd

    final_value = total_shares * price_series[-1]
    final_return = (final_value - total_cost) / total_cost * 100

    return {
        'final_return': round(final_return, 2),
        'max_drawdown': round(-max_dd, 2),
        'total_cost': round(total_cost, 2),
        'final_value': round(final_value, 2),
    }


def analyze_brief_glory():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
    with open(os.path.join(output_dir, 'vl_lifetime_analysis.json'), 'r', encoding='utf-8') as f:
        data = json.load(f)

    brief_codes = set(data['brief_glory'])
    stable_codes = set(data['stable'])
    evergreen_codes = set(data['evergreen'])
    oscillating_codes = set(data['oscillating'])
    newcomer_codes = set(data['newcomer'])
    monthly = data['monthly_top50']

    print("=" * 100)
    print("VL生命周期三段分析：进VL前 → 在VL中 → 被淘汰后")
    print("=" * 100)
    print()

    # Build membership matrix
    membership = {}
    for code in brief_codes | stable_codes | evergreen_codes | oscillating_codes | newcomer_codes:
        months = []
        for label in MONTH_LABELS:
            if label in monthly and code in monthly[label]:
                months.append(label)
        membership[code] = months

    def get_phases(code, bars):
        """获取三段价格序列"""
        if not bars:
            return None, None, None

        code_months = membership.get(code, [])
        if not code_months:
            return None, None, None

        first_month = code_months[0]
        last_month = code_months[-1]

        entry_cutoff = MONTH_CUTOFFS[first_month]
        exit_cutoff = MONTH_CUTOFFS[last_month]

        # 进VL前: entry_cutoff 往前推3个月
        # 找出 entry_cutoff 前90个自然日的 bar
        entry_year = entry_cutoff // 10000
        entry_month = (entry_cutoff % 10000) // 100
        entry_day = entry_cutoff % 100

        pre_year = entry_year
        pre_month = entry_month - 3
        if pre_month <= 0:
            pre_year -= 1
            pre_month += 12
        pre_cutoff = pre_year * 10000 + pre_month * 100 + entry_day

        before_bars = [b for b in bars if pre_cutoff <= b['date'] < entry_cutoff]
        during_bars = [b for b in bars if entry_cutoff <= b['date'] <= exit_cutoff]
        after_bars = [b for b in bars if b['date'] > exit_cutoff]

        return before_bars, during_bars, after_bars

    def calc_segment_metrics(bars, label=""):
        """计算一段价格序列的指标"""
        if not bars or len(bars) < 2:
            return None

        closes = [b['close'] for b in bars]
        n = len(closes)

        # 累计回报
        total_ret = (closes[-1] - closes[0]) / closes[0] * 100

        # 胜率(日): 正收益天数占比
        up_days = sum(1 for i in range(1, n) if closes[i] > closes[i-1])
        daily_win_rate = up_days / (n - 1) * 100

        # 最大回撤
        peak = closes[0]
        max_dd = 0
        for p in closes:
            if p > peak:
                peak = p
            dd = (peak - p) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # 日收益率均值
        daily_rets = [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(1, n)]
        avg_daily_ret = sum(daily_rets) / len(daily_rets)

        return {
            'bars': n,
            'total_return': round(total_ret, 2),
            'daily_win_rate': round(daily_win_rate, 1),
            'max_drawdown': round(-max_dd, 2),
            'avg_daily_ret': round(avg_daily_ret, 3),
            'start_close': closes[0],
            'end_close': closes[-1],
        }

    # Scan all TDX files and analyze
    results = {}

    total_targets = len(brief_codes) + len(stable_codes) + len(evergreen_codes) + len(oscillating_codes) + len(newcomer_codes)
    scanned = 0

    for exchange in ['sh', 'sz']:
        lday_dir = os.path.join(TDX_VIPDOC, exchange, 'lday')
        if not os.path.isdir(lday_dir):
            continue
        for fname in os.listdir(lday_dir):
            if not fname.endswith('.day'):
                continue
            code = fname[2:8]
            label = f'{exchange}{code}'

            if label not in membership:
                continue

            scanned += 1
            if scanned % 50 == 0:
                print(f"  扫描进度: {scanned}/{total_targets}", end='\r')

            bars = read_day_bars(os.path.join(lday_dir, fname))
            if not bars:
                continue

            before, during, after = get_phases(label, bars)

            before_m = calc_segment_metrics(before, "进VL前") if before else None
            during_m = calc_segment_metrics(during, "在VL中") if during else None
            after_m = calc_segment_metrics(after, "淘汰后") if after else None

            # 凸性仓位模拟: 从淘汰点开始
            convex_result = None
            if after and after_m and during_m:
                entry_price = during_m['end_close']
                after_closes = [b['close'] for b in after]
                convex_result = simulate_convex_sizing(after_closes, entry_price)

            results[label] = {
                'name': get_name(label),
                'months': membership[label],
                'before': before_m,
                'during': during_m,
                'after': after_m,
                'convex': convex_result,
                'category': (
                    'brief_glory' if label in brief_codes else
                    'stable' if label in stable_codes else
                    'evergreen' if label in evergreen_codes else
                    'oscillating' if label in oscillating_codes else
                    'newcomer'
                )
            }

    print(f"\n  分析完成: {len(results)} 只")
    print()

    # ========== 输出 ==========

    # 1. 三段对比 — 短暂辉煌
    print("=" * 100)
    print("【短暂辉煌】125只 — 三段指标")
    print("=" * 100)

    brief_results = {c: r for c, r in results.items() if r['category'] == 'brief_glory'}

    # 汇总统计
    def group_summary(group_dict, group_name):
        n = len(group_dict)
        n_before = sum(1 for r in group_dict.values() if r['before'])
        n_during = sum(1 for r in group_dict.values() if r['during'])
        n_after = sum(1 for r in group_dict.values() if r['after'])

        print(f"\n  [{group_name}] {n}只:")

        if n_before > 0:
            avg_ret = sum(r['before']['total_return'] for r in group_dict.values() if r['before']) / n_before
            avg_wr = sum(r['before']['daily_win_rate'] for r in group_dict.values() if r['before']) / n_before
            avg_dd = sum(r['before']['max_drawdown'] for r in group_dict.values() if r['before']) / n_before
            win = sum(1 for r in group_dict.values() if r['before'] and r['before']['total_return'] > 0)
            print(f"    进VL前({n_before}只有数据): 平均回报{avg_ret:.2f}% 日胜率{avg_wr:.1f}% 最大回撤{avg_dd:.1f}% 正回报{win}/{n_before}({win/n_before*100:.0f}%)")

        if n_during > 0:
            avg_ret = sum(r['during']['total_return'] for r in group_dict.values() if r['during']) / n_during
            avg_wr = sum(r['during']['daily_win_rate'] for r in group_dict.values() if r['during']) / n_during
            avg_dd = sum(r['during']['max_drawdown'] for r in group_dict.values() if r['during']) / n_during
            win = sum(1 for r in group_dict.values() if r['during'] and r['during']['total_return'] > 0)
            print(f"    在VL中({n_during}只有数据): 平均回报{avg_ret:.2f}% 日胜率{avg_wr:.1f}% 最大回撤{avg_dd:.1f}% 正回报{win}/{n_during}({win/n_during*100:.0f}%)")

        if n_after > 0:
            avg_ret = sum(r['after']['total_return'] for r in group_dict.values() if r['after']) / n_after
            avg_wr = sum(r['after']['daily_win_rate'] for r in group_dict.values() if r['after']) / n_after
            avg_dd = sum(r['after']['max_drawdown'] for r in group_dict.values() if r['after']) / n_after
            win = sum(1 for r in group_dict.values() if r['after'] and r['after']['total_return'] > 0)
            print(f"    淘汰后({n_after}只有数据): 平均回报{avg_ret:.2f}% 日胜率{avg_wr:.1f}% 最大回撤{avg_dd:.1f}% 正回报{win}/{n_after}({win/n_after*100:.0f}%)")

        # 凸性仓位
        n_convex = sum(1 for r in group_dict.values() if r['convex'])
        if n_convex > 0:
            convex_recovered = sum(1 for r in group_dict.values() if r['convex'] and r['convex']['final_return'] > 0)
            convex_avg_ret = sum(r['convex']['final_return'] for r in group_dict.values() if r['convex']) / n_convex
            print(f"    凸性仓位({n_convex}只模拟): 回本{convex_recovered}/{n_convex}({convex_recovered/n_convex*100:.0f}%) 平均最终收益{convex_avg_ret:.2f}%")

        # 对比: 简单持有 vs 凸性
        if n_convex > 0 and n_after > 0:
            simple_win = sum(1 for r in group_dict.values() if r['after'] and r['after']['total_return'] > 0)
            convex_win = sum(1 for r in group_dict.values() if r['convex'] and r['convex']['final_return'] > 0)
            print(f"    简单持有淘汰后回本: {simple_win}/{n_after} | 凸性仓位回本: {convex_win}/{n_convex}")

        return n_before, n_during, n_after

    group_summary(brief_results, "短暂辉煌")
    print()

    # 2. 各组对比
    print("=" * 100)
    print("【各组对比】— 淘汰后表现")
    print("=" * 100)
    print()

    for gname, gkey in [("短暂辉煌", "brief_glory"), ("稳定股", "stable"), ("常青树", "evergreen"), ("进出反复", "oscillating")]:
        grp = {c: r for c, r in results.items() if r['category'] == gkey}
        if not grp:
            continue

        n_after = sum(1 for r in grp.values() if r['after'])
        if n_after == 0:
            continue

        avg_after_ret = sum(r['after']['total_return'] for r in grp.values() if r['after']) / n_after
        avg_after_dd = sum(r['after']['max_drawdown'] for r in grp.values() if r['after']) / n_after
        win = sum(1 for r in grp.values() if r['after'] and r['after']['total_return'] > 0)

        n_convex = sum(1 for r in grp.values() if r['convex'])
        convex_win = sum(1 for r in grp.values() if r['convex'] and r['convex']['final_return'] > 0)
        convex_avg = sum(r['convex']['final_return'] for r in grp.values() if r['convex']) / n_convex if n_convex > 0 else 0

        print(f"  [{gname}] 淘汰后{n_after}只:")
        print(f"    简单持有: 平均{avg_after_ret:.2f}% 回本{win}/{n_after}({win/n_after*100:.0f}%) 平均回撤{avg_after_dd:.1f}%")
        if n_convex > 0:
            print(f"    凸性仓位: 回本{convex_win}/{n_convex}({convex_win/n_convex*100:.0f}%) 平均{convex_avg:.2f}%")
        print()

    # 3. 关键个例展示
    print("=" * 100)
    print("【关键个例】短暂辉煌 Top 10 最佳/最差淘汰后表现")
    print("=" * 100)
    print()

    with_after = {c: r for c, r in brief_results.items() if r['after']}
    sorted_by_after = sorted(with_after.items(), key=lambda x: x[1]['after']['total_return'])

    print("  最差10只（淘汰后跌幅最大）:")
    for code, r in sorted_by_after[:10]:
        name = r['name']
        b = r['before']
        d = r['during']
        a = r['after']
        c = r['convex']
        b_str = f"+{b['total_return']}%" if b and b['total_return'] > 0 else f"{b['total_return']}%" if b else "?"
        d_str = f"+{d['total_return']}%" if d and d['total_return'] > 0 else f"{d['total_return']}%" if d else "?"
        a_str = f"+{a['total_return']}%" if a and a['total_return'] > 0 else f"{a['total_return']}%" if a else "?"
        c_str = f"+{c['final_return']}%" if c and c['final_return'] > 0 else f"{c['final_return']}%" if c else "?"
        print(f"    {code}({name}) 进前:{b_str} 在中:{d_str} 出后:{a_str} 凸性:{c_str}")

    print()
    print("  最佳10只（淘汰后涨幅最大）:")
    for code, r in sorted_by_after[-10:]:
        name = r['name']
        b = r['before']
        d = r['during']
        a = r['after']
        c = r['convex']
        b_str = f"+{b['total_return']}%" if b and b['total_return'] > 0 else f"{b['total_return']}%" if b else "?"
        d_str = f"+{d['total_return']}%" if d and d['total_return'] > 0 else f"{d['total_return']}%" if d else "?"
        a_str = f"+{a['total_return']}%" if a and a['total_return'] > 0 else f"{a['total_return']}%" if a else "?"
        c_str = f"+{c['final_return']}%" if c and c['final_return'] > 0 else f"{c['final_return']}%" if c else "?"
        print(f"    {code}({name}) 进前:{b_str} 在中:{d_str} 出后:{a_str} 凸性:{c_str}")
    print()

    # 4. 进出反复穿越者
    print("=" * 100)
    print("【进出反复穿越者】— 从早期穿越到5月的2只")
    print("=" * 100)
    print()

    survivors = {'sz000977': '浪潮信息', 'sz300475': '香农芯创'}
    for code, name in survivors.items():
        r = results.get(code)
        if not r:
            continue
        b = r['before']
        d = r['during']
        a = r['after']
        print(f"  {code}({name}) 路径:{' → '.join(r['months'])}")
        if b: print(f"    进VL前(3月): +{b['total_return']}% 日胜率{b['daily_win_rate']}%")
        if d: print(f"    在VL中: +{d['total_return']}% 日胜率{d['daily_win_rate']}%")
        if a: print(f"    5月后: +{a['total_return']}% 日胜率{a['daily_win_rate']}%")
        print()

    # 5. 新来者前瞻
    print("=" * 100)
    print("【新来者】48只 — 它们将接受同样的考验")
    print("=" * 100)
    print()

    new_results = {c: r for c, r in results.items() if r['category'] == 'newcomer'}
    n_new = len(new_results)
    n_new_with_before = sum(1 for r in new_results.values() if r['before'])
    if n_new_with_before > 0:
        avg_pre_ret = sum(r['before']['total_return'] for r in new_results.values() if r['before']) / n_new_with_before
        avg_pre_wr = sum(r['before']['daily_win_rate'] for r in new_results.values() if r['before']) / n_new_with_before
        win_pre = sum(1 for r in new_results.values() if r['before'] and r['before']['total_return'] > 0)
        print(f"  进VL前3个月: {n_new_with_before}只有数据")
        print(f"  平均回报: {avg_pre_ret:.2f}% 日胜率: {avg_pre_wr:.1f}% 正回报: {win_pre}/{n_new_with_before}({win_pre/n_new_with_before*100:.0f}%)")

    n_new_during = sum(1 for r in new_results.values() if r['during'])
    if n_new_during > 0:
        avg_dur_ret = sum(r['during']['total_return'] for r in new_results.values() if r['during']) / n_new_during
        win_dur = sum(1 for r in new_results.values() if r['during'] and r['during']['total_return'] > 0)
        print(f"  进VL后(5月): 平均回报 {avg_dur_ret:.2f}% 正回报 {win_dur}/{n_new_during}")
    print()

    # 6. 总结
    print("=" * 100)
    print("【核心问题回答】")
    print("=" * 100)
    print()

    # 问题1: 不被止损能回本吗？
    brief_during_win = sum(1 for r in brief_results.values() if r['during'] and r['during']['total_return'] > 0)
    brief_during_n = sum(1 for r in brief_results.values() if r['during'])
    brief_after_hold_win = sum(1 for r in brief_results.values() if r['after'] and r['after']['total_return'] > 0)
    brief_after_n = sum(1 for r in brief_results.values() if r['after'])

    print(f"  Q1: 短暂辉煌在VL中如果不被止损，持有到月份结束能回本吗？")
    print(f"  A: 在VL期间正回报: {brief_during_win}/{brief_during_n} ({brief_during_win/brief_during_n*100:.0f}%)")
    print(f"     淘汰后简单持有到至今正回报: {brief_after_hold_win}/{brief_after_n} ({brief_after_hold_win/brief_after_n*100:.0f}%)")
    print(f"     → 即使在VL中没被止损，淘汰后持有至今仍有相当概率亏钱。")
    print()

    # 问题2: 凸性仓位能救吗？
    brief_convex_win = sum(1 for r in brief_results.values() if r['convex'] and r['convex']['final_return'] > 0)
    brief_convex_n = sum(1 for r in brief_results.values() if r['convex'])
    if brief_convex_n > 0:
        diff = brief_convex_win/brief_convex_n*100 - brief_after_hold_win/brief_after_n*100
        print(f"  Q2: 凸性仓位能否提高回本率？")
        print(f"  A: 简单持有淘汰后回本: {brief_after_hold_win}/{brief_after_n} ({brief_after_hold_win/brief_after_n*100:.0f}%)")
        print(f"     凸性仓位回本: {brief_convex_win}/{brief_convex_n} ({brief_convex_win/brief_convex_n*100:.0f}%)")
        print(f"     改善: {diff:+.1f}个百分点")
        if brief_convex_win > brief_after_hold_win:
            print(f"     → 凸性仓位有改善，但不是万能药。深跌的股票凸性加仓反而加大亏损。")
        else:
            print(f"     → 凸性仓位效果有限，淘汰组深跌太多，加仓反而加大亏损。")
    print()

    # 问题3: 三段胜率对比
    print(f"  Q3: 短暂辉煌三段胜率对比？")
    brief_before_win = sum(1 for r in brief_results.values() if r['before'] and r['before']['total_return'] > 0)
    brief_before_n = sum(1 for r in brief_results.values() if r['before'])
    if brief_before_n > 0:
        print(f"  A: 进VL前正回报: {brief_before_win}/{brief_before_n} ({brief_before_win/brief_before_n*100:.0f}%)")
        print(f"     在VL中正回报: {brief_during_win}/{brief_during_n} ({brief_during_win/brief_during_n*100:.0f}%)")
        print(f"     淘汰后正回报: {brief_after_hold_win}/{brief_after_n} ({brief_after_hold_win/brief_after_n*100:.0f}%)")
    print()

    # 保存结果
    summary = {
        'brief_glory': {
            'n': len(brief_results),
            'before_win_rate': round(brief_before_win/brief_before_n*100, 1) if brief_before_n > 0 else 0,
            'during_win_rate': round(brief_during_win/brief_during_n*100, 1) if brief_during_n > 0 else 0,
            'after_win_rate': round(brief_after_hold_win/brief_after_n*100, 1) if brief_after_n > 0 else 0,
            'convex_recovery_rate': round(brief_convex_win/brief_convex_n*100, 1) if brief_convex_n > 0 else 0,
        }
    }
    with open(os.path.join(output_dir, 'vl_phase_analysis.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存")


if __name__ == '__main__':
    analyze_brief_glory()

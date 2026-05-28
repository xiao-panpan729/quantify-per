# -*- coding: utf-8 -*-
"""
因子归因分析 — 三层倒推法

从回测交易结果反向提取因子驱动力，不正向穷举组合。

Layer 1: 单因子边际贡献 (lift)
Layer 2: Top/Bottom 交易指纹对比
Layer 3: 因子对交互效应

用法:
  python tools/volume_leader/factor_attribution.py                    # 默认 12个月, MA级, min5
  python tools/volume_leader/factor_attribution.py --months 6         # 6个月
  python tools/volume_leader/factor_attribution.py --entry jincha     # 金叉级入场
  python tools/volume_leader/factor_attribution.py --compare          # MA级 vs 金叉级 对比
  python tools/volume_leader/factor_attribution.py --save             # 保存JSON
"""

import sys, os, json, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.volume_leader.shared import load_universe, TRACKING_DIR

# ─── 导入回测引擎 ───
from tools.volume_leader.backtest import (
    backtest_stock, load_tracking_universe,
    PERIODS, PRICE_F, ALL_ENTRY_MODES,
)


# ══════════════════════════════════════════════════════════════════
# 因子定义
# ══════════════════════════════════════════════════════════════════

FACTORS = [
    ('f_entry_level',   '入场级别',  ['MA级', '金叉级']),
    ('f_ma_chain',      'MA链长',    [0, 1, 2, 3, 4, 5]),
    ('f_cci_state',     'CCI状态',   ['极端低位', '低位', '中位', '高位', '极端高位']),
    ('f_vol_regime',    '量能状态',  ['地量堆', '百日地量', '缩量50', '放量突破', '梯度放量', '正常']),
    ('f_pe_level',      'PE级别',    ['low', 'mid', 'high', '无数据']),
    ('f_pe_trend',      'PE趋势',    ['降熵', '升熵', '平稳', '无数据']),
    ('f_price_pos',     '价格位置',  ['高位(>30%溢价)', '中位', '低位(<85%折价)', '无数据']),
    ('f_expma_pos',     'EXPMA位置', ['白线上', '白黄间', '黄线下', '无数据']),
    ('f_signal_combo',  '信号组合',  ['★买+金叉', '金叉', '★买', '无锚点']),
    ('f_resonance',     '共振级联',  ['double', 'single', '']),
    ('zone',            '日线区域',  ['strong', 'secondary', 'weak']),
]


# ══════════════════════════════════════════════════════════════════
# 数据收集
# ══════════════════════════════════════════════════════════════════

def collect_trades(universe, months, period, entry_mode, label):
    """回测收集带因子的交易"""
    trades = []
    n_stocks = 0
    for stock in universe:
        code, name = stock['code'], stock['name']
        t = backtest_stock(code, name, period, months=months, entry_mode=entry_mode)
        for tr in t:
            tr['universe'] = label
        trades.extend(t)
        if t:
            n_stocks += 1
    return trades, n_stocks


def collect_all(months, period, entry_mode):
    """收集双宇宙交易"""
    print(f'\n[回测] {months}个月, {period}, {entry_mode}')
    print(f'{"─"*60}')

    # volume_leader universe
    vl = load_universe()
    vl_trades, vl_n = collect_trades(vl, months, period, entry_mode, '量领强者')

    # 14只跟踪标的
    tk = load_tracking_universe()
    tk_trades, tk_n = collect_trades(tk, months, period, entry_mode, '跟踪标的')

    all_trades = vl_trades + tk_trades

    if not all_trades:
        print('\n[错误] 未找到任何符合条件的交易')
        return None, None, None

    total = len(all_trades)
    wins = [t for t in all_trades if t['ret_pct'] > 0]
    wr = len(wins) / total * 100
    avg_ret = sum(t['ret_pct'] for t in all_trades) / total
    stop_n = len([t for t in all_trades if t['exit_reason'] == '止损'])
    reduce_n = len([t for t in all_trades if t['exit_reason'] == '减仓卖'])

    print(f'  量领强者: {len(vl_trades)}笔 ({vl_n}只有效)')
    print(f'  跟踪标的: {len(tk_trades)}笔 ({tk_n}只有效)')
    print(f'  合计: {total}笔  胜率:{wr:.1f}%  均收益:{avg_ret:+.2f}%  止损:{stop_n}  减仓卖:{reduce_n}')

    return all_trades, vl_trades, tk_trades


# ══════════════════════════════════════════════════════════════════
# Layer 1: 单因子边际贡献
# ══════════════════════════════════════════════════════════════════

def layer1_factor_lift(trades):
    """对每个因子的每个值，计算胜率/收益 lift vs 基准"""
    total = len(trades)
    baseline_wr = len([t for t in trades if t['ret_pct'] > 0]) / total * 100
    baseline_avg = sum(t['ret_pct'] for t in trades) / total

    print(f'\n{"="*100}')
    print(f'  Layer 1: 单因子边际贡献 (基准: {total}笔, 胜率{baseline_wr:.1f}%, 均收益{baseline_avg:+.2f}%)')
    print(f'{"="*100}')

    results = []

    for fkey, fname, fall_values in FACTORS:
        groups = defaultdict(list)
        for t in trades:
            val = t.get(fkey, '无数据')
            if val is None or val == '':
                val = '无数据'
            groups[str(val)].append(t)

        if len(groups) <= 1:
            continue

        print(f'\n  [{fname}]')
        print(f'  {"值":<22} {"笔数":>5} {"胜率":>7} {"vs基准":>7} {"均收益":>8} {"vs基准":>8} {"均MFE":>7} {"均MAE":>7}')
        print(f'  {"─"*80}')

        for val in sorted(groups, key=lambda v: len(groups[v]), reverse=True):
            items = groups[val]
            n = len(items)
            if n < 3:
                continue
            wr = len([t for t in items if t['ret_pct'] > 0]) / n * 100
            avg = sum(t['ret_pct'] for t in items) / n
            avg_mfe = sum(t['mfe_pct'] for t in items) / n
            avg_mae = sum(t['mae_pct'] for t in items) / n
            wr_lift = wr - baseline_wr
            avg_lift = avg - baseline_avg

            results.append({
                'factor': fname, 'value': val, 'n': n,
                'wr': round(wr, 1), 'wr_lift': round(wr_lift, 1),
                'avg_ret': round(avg, 2), 'avg_lift': round(avg_lift, 2),
                'avg_mfe': round(avg_mfe, 2), 'avg_mae': round(avg_mae, 2),
            })

            wr_sign = '+' if wr_lift > 0 else ''
            avg_sign = '+' if avg_lift > 0 else ''
            print(f'  {val:<22} {n:>5} {wr:>6.1f}% {wr_sign}{wr_lift:>+5.1f}% {avg:>+7.2f}% {avg_sign}{avg_lift:>+7.2f}% {avg_mfe:>+6.2f}% {avg_mae:>+6.2f}%')

    # 排名
    results.sort(key=lambda r: abs(r['avg_lift']), reverse=True)
    return results


# ══════════════════════════════════════════════════════════════════
# Layer 2: Top/Bottom 交易指纹对比
# ══════════════════════════════════════════════════════════════════

def layer2_fingerprint(trades):
    """取收益最高25%和最差25%，对比因子指纹"""
    sorted_t = sorted(trades, key=lambda t: t['ret_pct'], reverse=True)
    n = len(sorted_t)
    top_n = max(n // 4, 5)
    bottom_n = max(n // 4, 5)

    top = sorted_t[:top_n]
    bottom = sorted_t[-bottom_n:]

    top_avg = sum(t['ret_pct'] for t in top) / top_n
    bot_avg = sum(t['ret_pct'] for t in bottom) / bottom_n

    print(f'\n{"="*100}')
    print(f'  Layer 2: Top/Bottom 交易指纹对比')
    print(f'{"="*100}')
    print(f'  Top{top_n}笔: 均收益{top_avg:+.2f}%  |  Bottom{bottom_n}笔: 均收益{bot_avg:+.2f}%')
    print()

    print(f'  {"因子":<12} {"因子值":<22} {"Top占比":>8} {"Bottom占比":>8} {"驱动力":>8}')
    print(f'  {"─"*60}')

    drivers = []

    for fkey, fname, fall_values in FACTORS:
        for t_val in fall_values:
            t_val_s = str(t_val)
            top_pct = sum(1 for t in top if str(t.get(fkey, '')) == t_val_s) / top_n * 100
            bot_pct = sum(1 for t in bottom if str(t.get(fkey, '')) == t_val_s) / bottom_n * 100
            drive = top_pct - bot_pct

            if abs(drive) < 3 or (top_pct < 5 and bot_pct < 5):
                continue

            direction = '→利好' if drive > 0 else '→利空'
            print(f'  {fname:<12} {t_val_s:<22} {top_pct:>7.0f}% {bot_pct:>7.0f}% {drive:>+7.0f}% {direction}')

            drivers.append({
                'factor': fname, 'value': str(t_val),
                'top_pct': round(top_pct, 1), 'bottom_pct': round(bot_pct, 1),
                'drive': round(drive, 1),
            })

    drivers.sort(key=lambda d: abs(d['drive']), reverse=True)
    return drivers


# ══════════════════════════════════════════════════════════════════
# Layer 3: 因子对交互效应
# ══════════════════════════════════════════════════════════════════

TOP_N = 6  # 第一层选出的尖子因子数


def layer3_interaction(trades, layer1_results):
    """取Layer1中驱动力最强的top6因子，两两交叉看交互效应"""
    # 选尖子因子
    top_factors_set = set()
    for r in layer1_results[:15]:
        top_factors_set.add(r['factor'])
    top_factors = [f for f in FACTORS if f[1] in top_factors_set][:TOP_N]

    if len(top_factors) < 2:
        print('\n[Layer 3] 有效因子不足，跳过')
        return []

    print(f'\n{"="*100}')
    print(f'  Layer 3: 因子对交互效应 (尖子因子 Top{TOP_N})')
    print(f'{"="*100}')

    interactions = []
    baseline_wr = len([t for t in trades if t['ret_pct'] > 0]) / len(trades) * 100
    baseline_avg = sum(t['ret_pct'] for t in trades) / len(trades)

    for i in range(len(top_factors)):
        for j in range(i + 1, len(top_factors)):
            f1_key, f1_name, f1_vals = top_factors[i]
            f2_key, f2_name, f2_vals = top_factors[j]

            # 收窄到最常出现的值（减少格数）
            v1_counts = defaultdict(int)
            v2_counts = defaultdict(int)
            for t in trades:
                v1_counts[str(t.get(f1_key, '无数据'))] += 1
                v2_counts[str(t.get(f2_key, '无数据'))] += 1
            v1_top = [v for v, c in sorted(v1_counts.items(), key=lambda x: -x[1])[:3] if c >= 5]
            v2_top = [v for v, c in sorted(v2_counts.items(), key=lambda x: -x[1])[:3] if c >= 5]

            if len(v1_top) < 2 or len(v2_top) < 2:
                continue

            print(f'\n  [{f1_name} × {f2_name}]  基准: 胜率{baseline_wr:.1f}% 均收益{baseline_avg:+.2f}%')
            header = '  ' + ''.join(f'{v2:>12}' for v2 in v2_top)
            print(f'  {"":>14}{header}')
            print(f'  {"─"*(14 + 12*len(v2_top))}')

            for v1 in v1_top:
                row = f'  {v1:<12} '
                for v2 in v2_top:
                    cross = [t for t in trades
                             if str(t.get(f1_key, '')) == v1
                             and str(t.get(f2_key, '')) == v2]
                    if len(cross) >= 3:
                        wr = len([t for t in cross if t['ret_pct'] > 0]) / len(cross) * 100
                        avg = sum(t['ret_pct'] for t in cross) / len(cross)
                        cell = f'{wr:.0f}%/{avg:+.1f}'
                    else:
                        cell = '—'
                    row += f'{cell:>12}'
                print(row)

                for v2 in v2_top:
                    cross = [t for t in trades
                             if str(t.get(f1_key, '')) == v1
                             and str(t.get(f2_key, '')) == v2]
                    if len(cross) >= 3:
                        wr = len([t for t in cross if t['ret_pct'] > 0]) / len(cross) * 100
                        avg = sum(t['ret_pct'] for t in cross) / len(cross)
                        interactions.append({
                            'pair': f'{f1_name}×{f2_name}',
                            'v1': v1, 'v2': v2,
                            'n': len(cross), 'wr': round(wr, 1), 'avg_ret': round(avg, 2),
                        })

    # 从交互中找出最佳组合
    interactions.sort(key=lambda x: (x['wr'] * x['avg_ret']), reverse=True)

    if interactions:
        print(f'\n  ★ 最佳因子对 (按 WR×均收益 排序):')
        top_ix = interactions[:8]
        max_len = max(len(x['pair']) for x in top_ix) + max(len(x['v1']) for x in top_ix) + max(len(x['v2']) for x in top_ix)
        for ix in top_ix:
            combo = f'{ix["pair"]} [{ix["v1"]}+{ix["v2"]}]'
            print(f'  {combo:<40} {ix["n"]:>3}笔  胜率{ix["wr"]:.0f}%  均收益{ix["avg_ret"]:+.2f}%')

    return interactions


# ══════════════════════════════════════════════════════════════════
# 选股偏差分析
# ══════════════════════════════════════════════════════════════════

def universe_bias(vl_trades, tk_trades):
    """对比量领 vs 跟踪标的的信号质量差异"""
    if not vl_trades or not tk_trades:
        return

    print(f'\n{"="*100}')
    print(f'  ★ 选股偏差分析: 量领强者 vs 跟踪标的')
    print(f'{"="*100}')

    for label, trades in [('量领强者', vl_trades), ('跟踪标的', tk_trades)]:
        n = len(trades)
        if n == 0:
            continue
        wr = len([t for t in trades if t['ret_pct'] > 0]) / n * 100
        avg = sum(t['ret_pct'] for t in trades) / n
        stop_n = len([t for t in trades if t['exit_reason'] == '止损'])
        reduce_n = len([t for t in trades if t['exit_reason'] == '减仓卖'])
        avg_mfe = sum(t['mfe_pct'] for t in trades) / n
        avg_mae = sum(t['mae_pct'] for t in trades) / n
        ma_avg = sum(t.get('f_ma_chain', 0) for t in trades) / n

        # 因子分布差异
        ma3_plus = len([t for t in trades if t.get('f_ma_chain', 0) >= 3]) / n * 100
        vol_dl = len([t for t in trades if t.get('f_vol_regime', '') in ('地量堆', '百日地量')]) / n * 100

        print(f'  {label}: {n}笔  胜率:{wr:.1f}%  均收益:{avg:+.2f}%  MFE:{avg_mfe:+.2f}%  MAE:{avg_mae:+.2f}%')
        print(f'    止损:{stop_n}  减仓卖:{reduce_n}  均MA链长:{ma_avg:.1f}  MA3+: {ma3_plus:.0f}%  地量: {vol_dl:.0f}%')

    # 差值
    if len(vl_trades) > 0 and len(tk_trades) > 0:
        vl_wr = len([t for t in vl_trades if t['ret_pct'] > 0]) / len(vl_trades) * 100
        tk_wr = len([t for t in tk_trades if t['ret_pct'] > 0]) / len(tk_trades) * 100
        vl_avg = sum(t['ret_pct'] for t in vl_trades) / len(vl_trades)
        tk_avg = sum(t['ret_pct'] for t in tk_trades) / len(tk_trades)
        print(f'\n  ★ 选股偏差 (量领-跟踪): 胜率差异 {vl_wr - tk_wr:+.1f}%, 均收益差异 {vl_avg - tk_avg:+.2f}%')
        if vl_wr > tk_wr:
            print(f'  → 确认: 量领选股偏好确实拉高了信号胜率, 因子归因需分宇宙看')


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='因子归因分析 - 三层倒推法')
    parser.add_argument('--months', type=int, default=12, help='回测时间范围(月, 默认12)')
    parser.add_argument('--period', type=str, choices=PERIODS, default='min5', help='交易周期(默认min5)')
    parser.add_argument('--entry', type=str, choices=ALL_ENTRY_MODES,
                        default='star+ma5+ma10+safe', help='入场模式')
    parser.add_argument('--compare', action='store_true', help='对比 MA级 vs 金叉级 入场')
    parser.add_argument('--save', action='store_true', help='保存结果到JSON')
    args = parser.parse_args()

    if args.compare:
        print(f'\n{"="*100}')
        print(f'  入场模式对比: MA级 vs 金叉级 — {args.months}个月 — {args.period}')
        print(f'{"="*100}')

        all_layer1 = {}
        for entry_mode in ALL_ENTRY_MODES:
            label = 'MA级' if 'jincha' not in entry_mode else '金叉级'
            all_trades, vl_t, tk_t = collect_all(args.months, args.period, entry_mode)
            if not all_trades:
                continue

            results = layer1_factor_lift(all_trades)
            layer2_fingerprint(all_trades)
            if vl_t and tk_t:
                universe_bias(vl_t, tk_t)
            all_layer1[label] = results

        # 对比两种入场模式的因子驱动力差异
        if len(all_layer1) >= 2:
            print(f'\n{"="*100}')
            print(f'  ★ MA级 vs 金叉级: 因子驱动力差异')
            print(f'{"="*100}')
            for label, results in all_layer1.items():
                if results:
                    top3 = results[:3]
                    print(f'\n  [{label}] Top3驱动因子:')
                    for r in top3:
                        sign = '+' if r['avg_lift'] > 0 else ''
                        print(f'    {r["factor"]}={r["value"]}: {r["n"]}笔 WR{r["wr"]}% '
                              f'均收益{sign}{r["avg_lift"]:+.2f}% (vs基准)')
        return

    # ─── 单模式 ───
    all_trades, vl_trades, tk_trades = collect_all(args.months, args.period, args.entry)
    if not all_trades:
        return

    l1 = layer1_factor_lift(all_trades)
    l2 = layer2_fingerprint(all_trades)
    l3 = layer3_interaction(all_trades, l1)
    if vl_trades and tk_trades:
        universe_bias(vl_trades, tk_trades)

    # ─── 保存 ───
    if args.save:
        report = {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'params': {
                'months': args.months, 'period': args.period,
                'entry_mode': args.entry,
            },
            'summary': {
                'total_trades': len(all_trades),
                'baseline_wr': round(len([t for t in all_trades if t['ret_pct'] > 0]) / len(all_trades) * 100, 1),
                'baseline_avg': round(sum(t['ret_pct'] for t in all_trades) / len(all_trades), 2),
            },
            'layer1_lift': l1,
            'layer2_drivers': l2,
            'layer3_interactions': l3,
        }
        out_path = TRACKING_DIR / 'factor_attribution.json'
        json.dump(report, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'\n[保存] {out_path}')
        print(f'[查看] 结果已保存到 {out_path}, 用文本编辑器打开或用 json.load 读取')


if __name__ == '__main__':
    main()

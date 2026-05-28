# -*- coding: utf-8 -*-
"""共振信号全量扫描 — 43只量领标的 × 不限入场模式 × 不限时间

找出所有 15+30 双共振信号，统计:
  实际利润 (出场信号触发时) vs 全波利润 (MFE, 信号到最高点)

用法:
  python tools/volume_leader/scan_resonance.py
  python tools/volume_leader/scan_resonance.py --code sh601991  # 单标的详细输出
"""

import sys, os, csv, json, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.volume_leader.shared import load_universe, MIN_PRICE_FACTOR
from tools.volume_leader.backtest import (
    backtest_stock, load_tracking_universe,
    ALL_ENTRY_MODES, PRICE_F,
)

PRICE_DIV = MIN_PRICE_FACTOR


def scan_resonance(universe, label, months=None, entry_modes=None):
    """扫描所有双共振信号"""
    if entry_modes is None:
        entry_modes = ['star+ma5+ma10+safe', 'star+ma5+ma10+safe+jincha',
                       'star+ma5+ma10+safe+pe_d', 'star+ma5+ma10+safe+jincha+pe']

    all_double = []
    for stock in universe:
        code, name = stock['code'], stock['name']
        for mode in entry_modes:
            trades = backtest_stock(code, name, 'min5', months=months, entry_mode=mode)
            for t in trades:
                if t.get('f_resonance') == 'double':
                    t['_universe'] = label
                    t['_scan_mode'] = mode
                    all_double.append(t)

    # 去重: 同code+同entry_date只保留一次 (不同入场模式可能重复捕获)
    seen = set()
    unique = []
    for t in sorted(all_double, key=lambda x: x['entry_date']):
        key = (t['code'], t['entry_date'])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def main():
    parser = argparse.ArgumentParser(description='共振信号全量扫描')
    parser.add_argument('--code', type=str, help='单标的详细输出')
    parser.add_argument('--months', type=int, default=24, help='回测月数 (默认24)')
    args = parser.parse_args()

    vl = load_universe()
    tk = load_tracking_universe()

    print(f'共振信号全量扫描 — {args.months}个月 — {len(vl)}只量领 + {len(tk)}只跟踪')
    print(f'{"="*90}')

    # 扫描
    vl_double = scan_resonance(vl, '量领', months=args.months)
    tk_double = scan_resonance(tk, '跟踪', months=args.months)
    all_double = vl_double + tk_double
    all_double.sort(key=lambda x: x['entry_date'])

    if args.code:
        # 单标的详细输出
        code_trades = [t for t in all_double if t['code'] == args.code]
        if not code_trades:
            print(f'\n[信息] {args.code} 在{args.months}个月内没有双共振信号')
            # 检查是否有任何共振
            all_trades = []
            for mode in ALL_ENTRY_MODES:
                trades = backtest_stock(args.code, '?', 'min5', months=args.months, entry_mode=mode)
                all_trades.extend(trades)
            any_res = [t for t in all_trades if t.get('f_resonance', '')]
            if any_res:
                print(f'  但有 {len(any_res)} 个共振信号 (非双共振):')
                for t in any_res:
                    print(f'    {t["entry_date"]} mode={t.get("_scan_mode", t.get("entry_mode",""))} '
                          f'resonance={t.get("f_resonance","")} ret={t["ret_pct"]:+.2f}% MFE={t["mfe_pct"]:+.2f}%')
            return

        print(f'\n{args.code} 双共振信号详情:')
        print(f'{"-"*90}')
        print(f'  {"入场日":<12} {"出场日":<12} {"入场价":>8} {"出场价":>8} '
              f'{"实利%":>8} {"全波%":>8} {"持日":>5} {"出场原因":<8} {"模式"}')
        print(f'  {"-"*90}')
        for t in code_trades:
            print(f'  {t["entry_date"]:<12} {t["exit_date"]:<12} '
                  f'{t["entry_price"]:>8.3f} {t["exit_price"]:>8.3f} '
                  f'{t["ret_pct"]:>+7.2f}% {t["mfe_pct"]:>+7.2f}% '
                  f'{t["hold_bars"]:>5} {t["exit_reason"]:<8} {t.get("_scan_mode", t.get("entry_mode",""))}')
        return

    # ─── 全量统计 ───
    if not all_double:
        print('\n[信息] 未找到任何双共振信号')
        return

    # 按标的汇总
    by_code = defaultdict(list)
    for t in all_double:
        by_code[t['code']].append(t)

    print(f'\n双共振信号清单 ({len(all_double)}笔, {len(by_code)}只标的):')
    print(f'{"="*90}')
    print(f'  {"代码":<12} {"名称":<8} {"入场日":<10} {"实利%":>7} {"全波%":>7} {"持日":>5} {"出场"}')
    print(f'  {"-"*75}')

    for t in all_double:
        print(f'  {t["code"]:<12} {t["name"]:<8} {t["entry_date"]:<10} '
              f'{t["ret_pct"]:>+6.2f}% {t["mfe_pct"]:>+6.2f}% '
              f'{t["hold_bars"]:>5} {t["exit_reason"]}')

    # 统计
    n = len(all_double)
    wins = [t for t in all_double if t['ret_pct'] > 0]
    wr = len(wins) / n * 100
    avg_ret = sum(t['ret_pct'] for t in all_double) / n
    avg_mfe = sum(t['mfe_pct'] for t in all_double) / n
    avg_mae = sum(t['mae_pct'] for t in all_double) / n
    avg_hold = sum(t['hold_bars'] for t in all_double) / n

    # 按出场原因
    by_exit = defaultdict(list)
    for t in all_double:
        by_exit[t['exit_reason']].append(t)

    print(f'\n{"="*90}')
    print(f'  双共振统计 ({args.months}个月, {len(vl)}只量领+{len(tk)}只跟踪):')
    print(f'  总笔数: {n}  胜率: {wr:.1f}%  均实利: {avg_ret:+.2f}%  均全波: {avg_mfe:+.2f}%')
    print(f'  均回撤: {avg_mae:+.2f}%  均持日: {avg_hold:.0f}根')

    print(f'\n  按出场原因:')
    for reason, trades in sorted(by_exit.items(), key=lambda x: -len(x[1])):
        rn = len(trades)
        rwr = len([t for t in trades if t['ret_pct'] > 0]) / rn * 100
        ravg = sum(t['ret_pct'] for t in trades) / rn
        rmfe = sum(t['mfe_pct'] for t in trades) / rn
        print(f'    {reason:<8} {rn:>3}笔  胜率{rwr:.0f}%  均实利{ravg:+.2f}%  均全波{rmfe:+.2f}%')

    # 全波收益分布
    print(f'\n  全波收益 (MFE) 分布:')
    buckets = [(-100, 0), (0, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 999)]
    for lo, hi in buckets:
        cnt = len([t for t in all_double if lo <= t['mfe_pct'] < hi])
        if cnt > 0:
            bar = '█' * cnt
            print(f'    {lo:>4}~{hi:>4}%: {cnt:>2}笔 {bar}')

    # 实际收益分布
    print(f'\n  实际收益分布:')
    buckets2 = [(-100, -2), (-2, 0), (0, 2), (2, 5), (5, 10), (10, 20), (20, 999)]
    for lo, hi in buckets2:
        cnt = len([t for t in all_double if lo <= t['ret_pct'] < hi])
        if cnt > 0:
            bar = '█' * cnt
            print(f'    {lo:>4}~{hi:>4}%: {cnt:>2}笔 {bar}')

    # 按标的汇总
    print(f'\n  按标的:')
    for code in sorted(by_code, key=lambda c: -len(by_code[c])):
        trades = by_code[code]
        name = trades[0]['name']
        cn = len(trades)
        cavg = sum(t['ret_pct'] for t in trades) / cn
        cmfe = sum(t['mfe_pct'] for t in trades) / cn
        if cn >= 2:
            print(f'    {code} {name:<8} {cn}笔  均实利{cavg:+.2f}%  均全波{cmfe:+.2f}%')
        else:
            print(f'    {code} {name:<8} {cn}笔  实利{trades[0]["ret_pct"]:+.2f}%  全波{trades[0]["mfe_pct"]:+.2f}%')

    # 盯: 全波>50% 的信号
    huge = [t for t in all_double if t['mfe_pct'] >= 50]
    if huge:
        print(f'\n  ★ 全波≥50%的大肉信号 ({len(huge)}笔):')
        for t in huge:
            print(f'    {t["code"]} {t["name"]} {t["entry_date"]} → {t["exit_date"]} '
                  f'全波+{t["mfe_pct"]:.1f}% 实利{t["ret_pct"]:+.1f}% '
                  f'({t["exit_reason"]})')


if __name__ == '__main__':
    main()

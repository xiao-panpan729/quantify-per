# -*- coding: utf-8 -*-
"""
快照查询工具 - 直接读取 signals/tracking/ 下的 CSV 快照
不重新计算，快速调阅历史信号

用法:
  python query_snapshot.py <代码> <周期> [选项]

示例:
  python query_snapshot.py sh513310 min5 --date 20260422 --after 1420
  python query_snapshot.py sh513310 daily --last 10
  python query_snapshot.py sh513310 min30 --signals buy,sell,ema_cross
  python query_snapshot.py sh513310 min5 --cci-extreme --last 50
"""

import sys
import os
import csv
import argparse

BASE_DIR = r'D:\quantify-per'


def parse_args():
    parser = argparse.ArgumentParser(description='查询信号快照')
    parser.add_argument('code', help='标的代码, 如 sh513310, sz159740')
    parser.add_argument('period', help='周期: daily, min5, min15, min30, min60')
    parser.add_argument('--date', help='指定日期, 如 20260422')
    parser.add_argument('--after', help='时间之后(分钟线), 如 1420 表示14:20后')
    parser.add_argument('--before', help='时间之前(分钟线), 如 1500 表示15:00前')
    parser.add_argument('--last', type=int, help='显示最近N条')
    parser.add_argument('--signals', help='过滤信号类型, 逗号分隔: buy,sell,ema_cross')
    parser.add_argument('--cci-extreme', action='store_true', help='只显示CCI极值行')
    parser.add_argument('--all', action='store_true', help='显示所有行(默认只显示有信号/极值的)')
    return parser.parse_args()


def get_csv_path(code, period):
    fname = {
        'daily': 'daily_signals.csv',
        'min5': 'min5_signals.csv',
        'min15': 'min15_signals.csv',
        'min30': 'min30_signals.csv',
        'min60': 'min60_signals.csv',
    }.get(period)
    if not fname:
        print(f'[ERROR] 未知周期: {period}')
        return None
    return os.path.join(BASE_DIR, 'signals', 'tracking', code, fname)


def format_timestamp(ts, period):
    """格式化时间戳为可读形式"""
    ts = str(ts)
    if period == 'daily':
        return f'{ts[:4]}-{ts[4:6]}-{ts[6:8]}'
    elif len(ts) == 12:
        return f'{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}'
    elif len(ts) == 8:
        return f'{ts[:4]}-{ts[4:6]}-{ts[6:8]}'
    return ts


def main():
    args = parse_args()
    csv_path = get_csv_path(args.code, args.period)
    if not csv_path or not os.path.exists(csv_path):
        print(f'[ERROR] 文件不存在: {csv_path}')
        return

    with open(csv_path, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print('[WARN] 快照为空')
        return

    # 过滤
    filtered = rows

    # 日期过滤
    if args.date:
        if args.period == 'daily':
            filtered = [r for r in filtered if str(r.get('timestamp', r.get('date', ''))).startswith(args.date)]
        else:
            # 分钟线: timestamp 是 YYYYMMDDHHMM
            filtered = [r for r in filtered if str(r.get('timestamp', '')).startswith(args.date)]

    # 时间范围过滤(仅分钟线)
    if args.period != 'daily':
        if args.after:
            after_ts = int(args.after)
            filtered = [r for r in filtered if int(str(r.get('timestamp', ''))[8:12]) >= after_ts]
        if args.before:
            before_ts = int(args.before)
            filtered = [r for r in filtered if int(str(r.get('timestamp', ''))[8:12]) <= before_ts]

    # 信号过滤
    if args.signals:
        sig_types = args.signals.split(',')
        sig_map = {
            'buy': 'buy_signal',
            'sell': 'sell_signal',
            'ema_cross': 'expma_cross',
        }
        cols = [sig_map.get(s, s) for s in sig_types]
        filtered = [r for r in filtered if any(r.get(c, '').strip() for c in cols)]

    # CCI极值过滤
    if args.cci_extreme:
        filtered = [r for r in filtered if r.get('cci_extreme', '').strip()]

    # 默认只显示有信号/极值的行(除非--all)
    if not args.all and not args.signals and not args.cci_extreme:
        signal_cols = ['buy_signal', 'sell_signal', 'expma_cross', 'cci_extreme', 'cci_divergence']
        filtered = [r for r in filtered if any(r.get(c, '').strip() for c in signal_cols)]

    # 取最后N条
    if args.last:
        filtered = filtered[-args.last:]

    if not filtered:
        print('[INFO] 无匹配数据')
        return

    # 输出表头
    print(f'\n=== {args.code} {args.period} 共{len(filtered)}条 ===')
    if args.period == 'daily':
        headers = ['timestamp', 'close', 'cci', 'cci_extreme', 'cci_divergence', 'buy', 'sell', 'ema']
        print(f"{'日期':<12} {'close':>8} {'cci':>8} {'extreme':>10} {'divergence':>10} {'buy':>6} {'sell':>6} {'ema':>6}")
    else:
        headers = ['timestamp', 'raw_close', 'cci', 'cci_extreme', 'cci_divergence', 'buy', 'sell', 'ema']
        print(f"{'时间':<16} {'close':>8} {'cci':>8} {'extreme':>10} {'divergence':>10} {'buy':>6} {'sell':>6} {'ema':>6}")
    print('-' * 80)

    for r in filtered:
        ts = format_timestamp(r.get('timestamp', r.get('date', '')), args.period)
        if args.period == 'daily':
            close = r.get('close', '')
        else:
            close = r.get('raw_close', '')
        cci = r.get('cci', '')
        ext = r.get('cci_extreme', '')
        div = r.get('cci_divergence', '')
        buy = r.get('buy_signal', '')
        sell = r.get('sell_signal', '')
        ema = r.get('expma_cross', '')
        print(f'{ts:<16} {close:>8} {cci:>8} {ext:>10} {div:>10} {buy:>6} {sell:>6} {ema:>6}')


if __name__ == '__main__':
    main()

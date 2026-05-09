# -*- coding: utf-8 -*-
"""
关键K选股公式 v2 — 真实筹码版

基于通达信日线 + 本地筹码分布数据(data/chips/)的全市场扫描选股器。
替代 v1 的 Tushare cyq_perf/cyq_chips，改用 chip_loader 真实筹码峰数据。

选股条件（关键K公式）:
  1. 倍量: 当日成交量 / 前日成交量 >= 2.0x
  2. 涨幅: (当日收盘 - 前日收盘) / 前日收盘 >= 2.5%
  3. 短上影: 0 < (最高 - max(开盘,收盘)) / 前日收盘 * 100 <= 3.5%
  4. WINNER: [close*0.9, close] 区间筹码占比 > 60% (筹码锁定确认)

用法:
    python chips_selector_v2.py [--date YYYYMMDD] [--no-winner] [--code CODE]

输出:
    chips_picks/chips_picks_v2_YYYYMMDD.csv
"""

import os
import sys
import csv
import json
import struct
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 确保 quantify-per 目录可导入
sys.path.insert(0, str(Path(__file__).parent))

from chip_loader import ChipLoader
from jigou_jiancang import JigouJianCang


# ==================== 配置 ====================

PROJECT_ROOT = Path(__file__).parent
LDAY_ROOT = PROJECT_ROOT / 'lday'
OUTPUT_DIR = PROJECT_ROOT / 'chips_picks'

# 选股参数
MIN_VOLUME_RATIO = 2.0      # 倍量: 成交量 >= 前日2倍
MIN_RISE_PCT = 2.5          # 涨幅 >= 2.5%
MAX_UPPER_SHADOW = 3.5      # 上影线 <= 3.5%
MIN_WINNER_ZONE = 60.0      # WINNER区间筹码 >= 60%

# 是否启用WINNER条件
USE_WINNER = True


class ChipsSelectorV2:
    """关键K选股器 v2 — 基于本地筹码数据"""

    def __init__(self, chip_dir=None):
        self.chip_loader = ChipLoader(base_dir=chip_dir or str(PROJECT_ROOT / 'data' / 'chips'))
        self.jjc = JigouJianCang(chip_dir=chip_dir)

    # ---------- 数据读取 ----------

    def get_all_stock_codes(self):
        """
        扫描 lday 目录获取所有可用的股票代码列表
        返回: list of {'ts_code': '000001.SZ', 'symbol': '000001', 'market': 'SZ'}
        """
        stocks = []

        for market in ['sz', 'sh']:
            market_dir = LDAY_ROOT / market
            if not market_dir.exists():
                continue

            for f in os.listdir(market_dir):
                if not f.endswith('.day'):
                    continue
                # 文件名格式: sz000001.day 或 sh600000.day
                prefix = f[:-4]  # 去掉 .day
                if len(prefix) < 3:
                    continue
                code = prefix[2:]  # 去掉 sh/sz 前缀
                ts_code = f'{code}.{market.upper()}'
                stocks.append({
                    'ts_code': ts_code,
                    'symbol': code,
                    'market': market.upper(),
                    '_prefix': prefix,
                    '_market_short': market,
                })

        return sorted(stocks, key=lambda x: x['ts_code'])

    def read_tdx_day_last2(self, market_short: str, code: str):
        """
        读取通达信日线最近2条记录
        返回: list of dict 或 []
        """
        path = LDAY_ROOT / market_short / f'{market_short}{code}.day'
        if not path.exists():
            return []

        records = []
        with open(path, 'rb') as f:
            while True:
                buf = f.read(32)
                if not buf or len(buf) != 32:
                    break
                date, o, h, l, c, amount, vol, _ = struct.unpack('IIIIIIII', buf)
                records.append({
                    'date': str(date),
                    'open': o / 100.0,
                    'high': h / 100.0,
                    'low': l / 100.0,
                    'close': c / 100.0,
                    'amount': amount,
                    'vol': vol,
                })

        return records[-2:]

    # ---------- 选股条件检查 ----------

    def check_technical(self, stock: dict):
        """
        条件1-3: 技术形态检查 — 倍量 + 涨幅 + 短上影
        返回: (pass: bool, detail: dict)
        """
        ms = stock['_market_short']
        code = stock['symbol']

        records = self.read_tdx_day_last2(ms, code)
        if len(records) < 2:
            return False, {'reason': '无日线数据或不足2天'}

        today = records[-1]
        yesterday = records[-2]

        c = today['close']
        c1 = yesterday['close']
        o = today['open']
        h = today['high']
        vol = today['vol']
        vol1 = yesterday['vol']

        # 条件1: 倍量
        if vol1 <= 0:
            return False, {'reason': '前日成交量为0'}
        vol_ratio = vol / vol1
        if vol_ratio < MIN_VOLUME_RATIO:
            return False, {'reason': f'倍量不足:{vol_ratio:.2f}x'}

        # 条件2: 涨幅
        rise_pct = (c - c1) / c1 * 100
        if rise_pct < MIN_RISE_PCT:
            return False, {'reason': f'涨幅不足:{rise_pct:.2f}%'}

        # 条件3: 短上影
        upper_shadow = (h - max(o, c)) / c1 * 100
        if not (0 < upper_shadow <= MAX_UPPER_SHADOW):
            return False, {'reason': f'上影线不合格:{upper_shadow:.2f}%'}

        return True, {
            'vol_ratio': round(vol_ratio, 2),
            'rise_pct': round(rise_pct, 2),
            'upper_shadow': round(upper_shadow, 2),
            'close': c,
            'trade_date': today['date'],
        }

    def check_winner(self, stock: dict, close: float, trade_date: str):
        """
        条件4: WINNER 区间筹码检查
        计算 WINNER(close) - winner(close*0.9) > MIN_WINNER_ZONE%

        这是关键K公式的核心：当前价到下方10%区间内锁定的筹码比例，
        越高说明主力控盘越强、上方抛压越小。

        返回: (pass: bool, detail: dict)
        """
        if not USE_WINNER:
            return True, {
                'winner_close': None,
                'winner_low90': None,
                'zone_chips': None,
                'note': 'WINNER条件已禁用',
            }

        code_key = stock['_prefix']  # e.g., 'sh600438'

        # 获取筹码分布
        dist = self.chip_loader.get_distribution(code_key, trade_date)
        if dist is None:
            return False, {'reason': f'无筹码数据({code_key}, {trade_date})'}

        # 计算两个WINNER值
        w_close = self.jjc.winner(close, dist)
        w_low90 = self.jjc.winner(close * 0.9, dist)

        if w_close is None or w_low90 is None:
            return False, {'reason': 'WINNER计算失败'}

        zone_chips = w_close - w_low90

        if zone_chips <= MIN_WINNER_ZONE / 100.0:
            return False, {
                'reason': f'区间筹码不足:{zone_chips:.2%} <= {MIN_WINNER_ZONE}%',
                'winner_close': w_close,
                'winner_low90': w_low90,
                'zone_chips': zone_chips,
            }

        return True, {
            'winner_close': round(w_close, 2),
            'winner_low90': round(w_low90, 2),
            'zone_chips': round(zone_chips, 4),
            'note': f'{zone_chips:.1f}%',
        }

    # ---------- 主流程 ----------

    def select(self, trade_date: str = None):
        """
        执行全市场选股

        Args:
            trade_date: 交易日期 YYYYMMDD，默认最近交易日

        Returns:
            list of dict — 所有通过的标的
        """
        if trade_date is None:
            trade_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
            while datetime.strptime(trade_date, '%Y%m%d').weekday() >= 5:
                trade_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')

        print(f'\n{"="*60}')
        print(f'关键K选股器 v2 | 日期: {trade_date}')
        print(f'WINNER条件: {"启用" if USE_WINNER else "禁用"}')
        print(f'{"="*60}')

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # 1. 获取股票列表
        print('\n[1/3] 扫描股票列表...')
        stocks = self.get_all_stock_codes()
        total = len(stocks)
        print(f'      共 {total} 只股票')

        # 2. 逐只扫描
        print(f'\n[2/3] 执行选股公式...')
        picks = []
        rejected = {'no_data': 0, 'technical': 0, 'winner': 0}

        for i, stock in enumerate(stocks):
            if (i + 1) % 500 == 0:
                print(f'      进度: {i+1}/{total}...')

            # 技术形态检查
            tech_ok, tech_detail = self.check_technical(stock)
            if not tech_ok:
                rejected['technical'] += 1
                continue

            close = tech_detail['close']
            tdate = tech_detail['trade_date']

            # WINNER 筹码检查
            win_ok, win_detail = self.check_winner(stock, close, tdate)
            if not win_ok:
                rejected['winner'] += 1
                continue

            # 通过!
            picks.append({
                'ts_code': stock['ts_code'],
                'symbol': stock['symbol'],
                **tech_detail,
                **win_detail,
            })

        # 3. 输出结果
        print(f'\n[3/3] 选股完成')
        print(f'      ✅ 通过:   {len(picks)} 只')
        print(f'      ❌ 技术淘汰: {rejected["technical"]} 只')
        print(f'      ❌ 筹码淘汰: {rejected["winner"]} 只')

        output_file = OUTPUT_DIR / f'chips_picks_v2_{trade_date}.csv'
        if picks:
            fieldnames = list(picks[0].keys())
            with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(picks)
        else:
            with open(output_file, 'w', encoding='utf-8-sig') as f:
                f.write('无选股结果\n')

        print(f'\n📄 输出文件: {output_file}')

        # 打印 Top 15
        if picks:
            print(f'\n{"="*60}')
            print('🏆 关键K精选 Top 15:')
            print(f'{"="*60}')
            print(f'{"序号":>3s} {"代码":<10s} {"涨幅":>6s} {"倍量":>5s} {"上影":>5s} '
                  f'{"WINNER(C)":>10s} {"区间筹码":>8s}')
            print('-' * 60)
            for i, p in enumerate(picks[:15], 1):
                wc = p.get('winner_close', '-')
                zc = p.get('zone_chips', '-')
                if isinstance(wc, float): wc = f'{wc:.1f}%'
                if isinstance(zc, float): zc = f'{zc:.1f}%'
                print(f'{i:3d} {p["ts_code"]:<10s} {p["rise_pct"]:>5.2f}% '
                      f'{p["vol_ratio"]:>4.1f}x {p["upper_shadow"]:>4.2f}% '
                      f'{str(wc):>10s} {str(zc):>8s}')

        return picks


def main():
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description='关键K选股器 v2 (真实筹码版)')
    parser.add_argument('--date', type=str, help='日期 YYYYMMDD，默认最近交易日')
    parser.add_argument('--no-winner', action='store_true', help='禁用WINNER条件')
    parser.add_argument('--code', type=str, help='只测试单只股票 (如 sh600438)')
    args = parser.parse_args()

    global USE_WINNER
    if args.no_winner:
        USE_WINNER = False

    selector = ChipsSelectorV2()

    if args.code:
        # 单股测试模式
        import pandas as pd
        from pytdx.reader import TdxDailyBarReader

        reader = TdxDailyBarReader()
        ms = 'sh' if args.code.startswith('sh') else 'sz'
        code = args.code[2:]
        day_file = LDAY_ROOT / ms / f'{ms}{code}.day'
        raw_df = reader.get_df(str(day_file))
        df = raw_df.tail(5).copy().reset_index()
        if 'index' in df.columns:
            df.rename(columns={'index': 'date'}, inplace=True)

        print(f'\n=== 单股测试: {args.code} ===')

        # 测试技术条件
        test_stock = {
            'ts_code': f'{code}.{ms.upper()}',
            'symbol': code,
            'market': ms.upper(),
            '_prefix': args.code,
            '_market_short': ms,
        }
        tech_ok, tech_detail = selector.check_technical(test_stock)
        print(f'技术形态: {"✅通过" if tech_ok else "❌未通过"} - {tech_detail}')

        if tech_ok:
            win_ok, win_detail = selector.check_winner(test_stock, tech_detail['close'], tech_detail['trade_date'])
            print(f'WINNER:   {"✅通过" if win_ok else "❌未通过"} - {win_detail}')
    else:
        # 全市场扫描
        picks = selector.select(args.date)
        sys.exit(0 if picks else 1)


if __name__ == '__main__':
    main()

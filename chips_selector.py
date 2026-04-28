# -*- coding: utf-8 -*-
"""
筹码选股器 v1.0
基于通达信日线 + Tushare cyq_perf 筹码数据
每日盘后运行，输出筹码精选标的
"""
import os
import sys
import csv
import json
import struct
from datetime import datetime, timedelta
from pathlib import Path

import tushare as ts

# ========== 配置 ==========
TS_TOKEN = '3e2315b2f350827637c3087244a57aba18d8089bf00592b93fb1ffd9'
PROJECT_ROOT = Path(__file__).parent
LDAY_ROOT = PROJECT_ROOT / 'lday'
OUTPUT_DIR = PROJECT_ROOT / 'chips_picks'
CHIPS_CACHE = PROJECT_ROOT / 'data' / 'chips_cache.json'

# 选股参数
MIN_VOLUME_RATIO = 2.0      # 倍量: 成交量 >= 前日2倍
MIN_RISE_PCT = 2.5          # 涨幅 >= 2.5%
MAX_UPPER_SHADOW = 3.5      # 上影线 <= 3.5%
MIN_WINNER_RATE = 65.0      # 获利盘 >= 65% (替代 WINNER(C)-WINNER(C*0.9)>0.6)
MAX_CONCENTRATION = 0.20    # 筹码集中度 < 20% (cost95-cost5)/(cost95+cost5)

# 是否使用WINNER因子
USE_WINNER = False


def ensure_dirs():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / 'data').mkdir(parents=True, exist_ok=True)


def get_tushare_pro():
    """获取Tushare Pro实例"""
    return ts.pro_api(TS_TOKEN)


def get_stock_list():
    """获取全市场股票列表 (ts_code格式: 000001.SZ)"""
    pro = get_tushare_pro()
    df = pro.stock_basic(exchange='', list_status='L',
                         fields='ts_code,symbol,name,area,industry,market,list_date')
    return df.to_dict('records')


def fetch_cyq_perf(trade_date: str):
    """
    拉取指定日期的cyq_perf全市场数据（用于快速预筛）
    返回: {ts_code: {winner_rate, cost_5pct, cost_95pct, weight_avg, ...}}
    """
    cache_file = PROJECT_ROOT / 'data' / f'cyq_perf_{trade_date}.json'
    if cache_file.exists():
        with open(cache_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    pro = get_tushare_pro()
    all_data = {}
    offset = 0
    limit = 5000

    print(f'[筹码] 拉取 cyq_perf {trade_date} ...')
    while True:
        try:
            df = pro.cyq_perf(trade_date=trade_date, offset=offset, limit=limit)
            if df is None or len(df) == 0:
                break
            for _, row in df.iterrows():
                all_data[row['ts_code']] = {
                    'winner_rate': float(row.get('winner_rate', 0) or 0),
                    'cost_5pct': float(row.get('cost_5pct', 0) or 0),
                    'cost_95pct': float(row.get('cost_95pct', 0) or 0),
                    'cost_50pct': float(row.get('cost_50pct', 0) or 0),
                    'weight_avg': float(row.get('weight_avg', 0) or 0),
                    'his_low': float(row.get('his_low', 0) or 0),
                    'his_high': float(row.get('his_high', 0) or 0),
                }
            if len(df) < limit:
                break
            offset += limit
            print(f'  ...已拉取 {len(all_data)} 条')
        except Exception as e:
            print(f'  [错误] 拉取失败: {e}')
            break

    # 缓存到本地
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False)
    print(f'[筹码] 共 {len(all_data)} 条，已缓存')
    return all_data


def calc_winner_zone(ts_code: str, close: float, trade_date: str):
    """
    精确计算 WINNER(C) - WINNER(C*0.9)
    即：当前价到下方10%区间内的筹码占比
    使用 cyq_chips 接口逐价位累加
    """
    cache_file = PROJECT_ROOT / 'data' / f'cyq_chips_{trade_date}' / f'{ts_code.replace(".", "_")}.json'
    if cache_file.exists():
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        pro = get_tushare_pro()
        try:
            df = pro.cyq_chips(ts_code=ts_code, trade_date=trade_date)
        except Exception as e:
            return None
        if df is None or len(df) == 0:
            return None
        data = df.to_dict('records')
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

    low_bound = close * 0.9
    zone_chips = 0.0
    for row in data:
        price = float(row.get('price', 0))
        percent = float(row.get('percent', 0))
        if low_bound <= price <= close:
            zone_chips += percent

    # percent 是百分比，返回小数形式 (0-1)
    return zone_chips / 100.0


def read_tdx_day(market: str, code: str):
    """
    读取通达信日线数据
    返回最近2日的 [O, H, L, C, V, Amount] 列表
    """
    if market == 'SZ':
        path = LDAY_ROOT / 'sz' / f'sz{code}.day'
    else:
        path = LDAY_ROOT / 'sh' / f'sh{code}.day'

    if not path.exists():
        return []

    records = []
    with open(path, 'rb') as f:
        while True:
            buf = f.read(32)
            if not buf or len(buf) != 32:
                break
            # 通达信日线格式: date(4) + open(4) + high(4) + low(4) + close(4) + amount(4) + vol(4) + reserved(4)
            date, open_p, high, low, close, amount, vol, _ = struct.unpack('IIIIIIII', buf)
            records.append({
                'date': str(date),
                'open': open_p / 100.0,
                'high': high / 100.0,
                'low': low / 100.0,
                'close': close / 100.0,
                'amount': amount,
                'vol': vol,
            })

    return records[-2:]  # 只返回最近2日


def check_technical(stock: dict):
    """
    检查技术形态：倍量 + 涨幅 + 短上影
    返回: (是否通过, 详情dict)
    """
    ts_code = stock['ts_code']  # 000001.SZ
    code = stock['symbol']      # 000001
    market = stock['market']    # 主板/创业板/科创板

    # 判断市场前缀
    if ts_code.endswith('.SZ'):
        prefix = 'SZ'
    else:
        prefix = 'SH'

    records = read_tdx_day(prefix, code)
    if len(records) < 2:
        return False, {'reason': '无日线数据'}

    today = records[-1]
    yesterday = records[-2]

    c = today['close']
    c1 = yesterday['close']
    o = today['open']
    h = today['high']
    vol = today['vol']
    vol1 = yesterday['vol']

    # 1. 倍量
    if vol1 <= 0:
        return False, {'reason': '前日成交量为0'}
    vol_ratio = vol / vol1
    if vol_ratio < MIN_VOLUME_RATIO:
        return False, {'reason': f'倍量不足: {vol_ratio:.2f}x'}

    # 2. 涨幅
    rise_pct = (c - c1) / c1 * 100
    if rise_pct < MIN_RISE_PCT:
        return False, {'reason': f'涨幅不足: {rise_pct:.2f}%'}

    # 3. 短上影
    upper_shadow = (h - max(o, c)) / c1 * 100
    if not (0 < upper_shadow <= MAX_UPPER_SHADOW):
        return False, {'reason': f'上影线不合格: {upper_shadow:.2f}%'}

    return True, {
        'vol_ratio': round(vol_ratio, 2),
        'rise_pct': round(rise_pct, 2),
        'upper_shadow': round(upper_shadow, 2),
        'close': c,
    }


def check_chips(stock: dict, close: float, trade_date: str, chips_data: dict = None):
    """
    检查筹码条件：精确计算 [close*0.9, close] 区间筹码占比
    等效于通达信：WINNER(C) - WINNER(C*0.9) > 0.60

    如果 USE_WINNER=False，跳过筹码检查，直接通过

    返回: (是否通过, 详情dict)
    """
    if not USE_WINNER:
        return True, {'zone_chips': None, 'zone_chips_pct': 'N/A (禁用WINNER)'}

    ts_code = stock['ts_code']

    # 快速预筛：WINNER(C) 必须 > 60%，否则差值不可能 > 60%
    if chips_data:
        perf = chips_data.get(ts_code)
        if perf:
            winner_rate = perf.get('winner_rate', 0)
            if winner_rate < 60:
                return False, {'reason': f'WINNER(C)不足: {winner_rate:.1f}%，不可能满足差值>60%'}

    # 精确计算：调用 cyq_chips
    zone_chips = calc_winner_zone(ts_code, close, trade_date)
    if zone_chips is None:
        return False, {'reason': '无cyq_chips数据'}

    if zone_chips <= 0.60:
        return False, {'reason': f'WINNER(C)-WINNER(C*0.9)={zone_chips:.2%} <= 60%'}

    return True, {
        'zone_chips': round(zone_chips, 4),
        'zone_chips_pct': f'{zone_chips:.2%}',
    }


def select_stocks(trade_date: str = None):
    """
    主选股函数
    """
    if trade_date is None:
        trade_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
        # 如果昨天是周末，往前推
        while datetime.strptime(trade_date, '%Y%m%d').weekday() >= 5:
            trade_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')

    print(f'\n{"="*60}')
    print(f'筹码选股器启动 | 日期: {trade_date}')
    print(f'{"="*60}')

    ensure_dirs()

    # 1. 获取股票列表
    print('\n[1/4] 获取股票列表...')
    stocks = get_stock_list()
    print(f'      共 {len(stocks)} 只')

    # 2. 拉取筹码数据
    print('\n[2/4] 拉取筹码数据...')
    chips_data = fetch_cyq_perf(trade_date)

    # 3. 逐个选股
    print('\n[3/4] 运行选股公式...')
    picks = []
    rejected = {'technical': 0, 'chips_prefilter': 0, 'chips_precise': 0}

    for i, stock in enumerate(stocks):
        if i % 500 == 0:
            print(f'      已处理 {i}/{len(stocks)}...')

        # 技术形态检查（倍量 + 涨幅 + 短上影）
        tech_ok, tech_detail = check_technical(stock)
        if not tech_ok:
            rejected['technical'] += 1
            continue

        close = tech_detail['close']

        # 筹码预筛（cyq_perf 快速判断）
        if chips_data:
            perf = chips_data.get(stock['ts_code'])
            if perf and perf.get('winner_rate', 0) < 60:
                rejected['chips_prefilter'] += 1
                continue

        # 筹码精确检查（cyq_chips 计算 WINNER(C)-WINNER(C*0.9)）
        chips_ok, chips_detail = check_chips(stock, close, trade_date, chips_data)
        if not chips_ok:
            rejected['chips_precise'] += 1
            continue

        # 通过
        picks.append({
            'ts_code': stock['ts_code'],
            'name': stock['name'],
            'industry': stock.get('industry', ''),
            **tech_detail,
            **chips_detail,
        })

    print(f'\n[4/4] 选股完成')
    print(f'      通过: {len(picks)} 只')
    print(f'      技术淘汰: {rejected["technical"]} 只')
    print(f'      筹码预筛淘汰: {rejected["chips_prefilter"]} 只')
    print(f'      筹码精确淘汰: {rejected["chips_precise"]} 只')

    # 4. 输出结果
    output_file = OUTPUT_DIR / f'chips_picks_{trade_date}.csv'
    with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
        if picks:
            writer = csv.DictWriter(f, fieldnames=list(picks[0].keys()))
            writer.writeheader()
            writer.writerows(picks)
        else:
            f.write('无选股结果\n')

    print(f'\n[输出] {output_file}')
    return picks


def main():
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description='筹码选股器')
    parser.add_argument('--date', type=str, help='指定日期 YYYYMMDD，默认最近交易日')
    args = parser.parse_args()

    picks = select_stocks(args.date)

    if picks:
        print(f'\n{"="*60}')
        print('精选标的 Top 10:')
        print(f'{"="*60}')
        for i, p in enumerate(picks[:10], 1):
            print(f"{i:2d}. {p['ts_code']} {p['name']:8s} | "
                  f"涨幅:{p['rise_pct']:5.2f}% 倍量:{p['vol_ratio']:4.1f}x | "
                  f"区间筹码:{p.get('zone_chips_pct', 'N/A')}")

    return 0 if picks else 1


if __name__ == '__main__':
    sys.exit(main())

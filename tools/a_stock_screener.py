# -*- coding: utf-8 -*-
"""
全A股强势+波动率排序器

【强势定义】
  1. 近5日涨幅 > 0 且 近20日涨幅 > 0（短期+中期均上涨）
  2. 收盘在EXPMA(12)上方（多头格局）
  
【波动率较大】
  近20日振幅（H-L/L）平均 > 3%
  OR 近5日振幅 > 6%（近期活跃）

用法: python a_stock_screener.py
      python a_stock_screener.py --top 50   # 只看前50
"""

import sys
import os
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
from tdx_fetch import fetch_bars

# ====== 配置 ======
MARKET_CODES = {
    'sz': range(1, 1000),      # 000001-000999 主板
    # 实际使用时会扫描全市场，这里先列出所有板块范围
}

# 需要扫描的板块范围（沪深全市场）
MARKET_RANGES = [
    ('sz', (1, 999)),      # 000001-000999
    ('sz', (1000, 1999)),   # 001000-001999
    ('sz', (2000, 3999)),   # 002000-003999 中小板
    ('sz', (3000, 3999)),   # 300000-300999 创业板
    ('sh', (600000, 609999)),  # 主板
    ('sh', (510000, 519999)),  # ETF
    ('sh', (560000, 569999)),  # ETF
    ('sh', (588000, 588999)),  # 科创
    ('sz', (159000, 159999)),  # 深市ETF
]


def calc_expma(values, n=12):
    """计算EMA"""
    if not values:
        return []
    k = 2 / (n + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(ema[-1] * (1 - k) + v * k)
    return ema


def calc_amplitude(bars):
    """计算近N日的振幅序列"""
    amps = []
    for b in bars:
        amp = (b['high'] - b['low']) / b['low'] if b['low'] > 0 else 0
        amps.append(amp)
    return amps


def check_stock(code, market):
    """检查一只股票的强势+波动率条件"""
    try:
        # 拉60根日线（足够算各种指标）
        bars = fetch_bars(code, 'day', market=market, count=60)
        if not bars or len(bars) < 25:
            return None
        
        closes = [b['close'] for b in bars]
        highs = [b['high'] for b in bars]
        lows = [b['low'] for b in bars]
        
        # ---- 近5日和近20日涨幅 ----
        c5_pct = (closes[-1] - closes[-6]) / closes[-6] if len(bars) >= 6 else 0
        c20_pct = (closes[-1] - closes[-21]) / closes[-21] if len(bars) >= 21 else 0
        
        # ---- EXPMA(12) ----
        ema12 = calc_expma(closes, 12)
        
        # ---- 波动率：近20日振幅平均 ----
        recent_bars = bars[-20:] if len(bars) >= 20 else bars
        amp20 = calc_amplitude(recent_bars)
        avg_amp20 = sum(amp20) / len(amp20) if amp20 else 0
        
        # ---- 近5日振幅 ----
        amp5 = calc_amplitude(bars[-5:])
        max_amp5 = max(amp5) if amp5 else 0
        
        # ---- 判断条件 ----
        is_strong = (c5_pct > 0 and c20_pct > 0 and closes[-1] > ema12[-1])
        is_volatile = (avg_amp20 > 0.03 or max_amp5 > 0.06)
        
        if is_strong and is_volatile:
            return {
                'code': f'{market}{code}',
                'c5_pct': round(c5_pct * 100, 2),
                'c20_pct': round(c20_pct * 100, 2),
                'avg_amp20': round(avg_amp20 * 100, 2),
                'max_amp5': round(max_amp5 * 100, 2),
                'close': closes[-1],
            }
        elif is_strong:
            return None  # 仅记录不输出
        return None
        
    except Exception as e:
        # print(f'  {market}{code}: 错误 {e}')
        return None


def scan_all_market():
    """扫描全市场"""
    results = []
    total = sum(r2 - r1 + 1 for _, (r1, r2) in MARKET_RANGES)
    done = 0
    t0 = time.time()
    
    for market, (start, end) in MARKET_RANGES:
        for code_num in range(start, end + 1):
            code = f'{code_num:06d}'
            res = check_stock(code, market)
            if res:
                results.append(res)
                
            done += 1
            if done % 500 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed
                remain = (total - done) / rate
                print(f'  进度: {done}/{total} ({done/total*100:.1f}%)  '
                      f'已找到 {len(results)} 只  '
                      f'预计剩余 {remain/60:.0f}分钟', end='\r')
    
    return results


if __name__ == '__main__':
    print('全A股强势+波动率排序器')
    print('条件: 1)近5日涨幅>0  2)近20日涨幅>0  3)收盘>EXPMA12  4)20日振幅均值>3% 或 5日最大振幅>6%')
    print('=' * 70)
    
    # 先测试单只
    print('\n测试: 159740')
    res = check_stock('159740', 'sz')
    if res:
        print(f'  满足条件: {res}')
    else:
        print('  不满足条件')
    
    print('\n测试: 000001')
    res = check_stock('000001', 'sz')
    if res:
        print(f'  满足条件: {res}')
    else:
        print('  不满足条件')
    
    print('\n测试: 513310')
    res = check_stock('513310', 'sh')
    if res:
        print(f'  满足条件: {res}')
    else:
        print('  不满足条件')
    
    print('\n测试: 588200')
    res = check_stock('588200', 'sh')
    if res:
        print(f'  满足条件: {res}')
    else:
        print('  不满足条件')

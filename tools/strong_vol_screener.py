# -*- coding: utf-8 -*-
"""
强势+波动率排序器 v2
只扫：ETF + 常用板块（主流的，不是全A股）
"""

import sys, os
sys.path.insert(0, 'D:\\quantify-per')
sys.path.insert(0, 'D:\\quantify-per\\tools')
from tdx_fetch import fetch_bars

def calc_ema(values, n=12):
    if not values: return []
    k = 2/(n+1)
    r = [values[0]]
    for v in values[1:]:
        r.append(r[-1]*(1-k)+v*k)
    return r

def ck(code, mk, name=''):
    bars = fetch_bars(code, 'day', market=mk, count=60)
    if not bars or len(bars) < 25: return None
    c = [b['close'] for b in bars]
    if not bars:
        return None
    c5 = (c[-1]-c[-6])/c[-6] if len(bars)>=6 else 0
    c20 = (c[-1]-c[-21])/c[-21] if len(bars)>=21 else 0
    ema12 = calc_ema(c,12)
    
    # 近20日振幅均值
    amps20 = [(b['high']-b['low'])/b['low'] if b['low']>0 else 0 for b in bars[-20:]]
    avg_amp20 = sum(amps20)/len(amps20)
    
    # 近5日振幅
    amps5 = [(b['high']-b['low'])/b['low'] if b['low']>0 else 0 for b in bars[-5:]]
    max_amp5 = max(amps5)
    
    strong = (c5>0 and c20>0 and c[-1]>ema12[-1])
    vol = (avg_amp20>0.03 or max_amp5>0.06)
    
    if strong and vol:
        return {'code':f'{mk}{code}','name':name,'c5%':round(c5*100,1),'c20%':round(c20*100,1),
                'avg_amp20%':round(avg_amp20*100,1),'max_amp5%':round(max_amp5*100,1),
                'close':c[-1]}
    return None

# ====== 重点扫描列表 ======
STOCKS = [
    # ETF
    ('159740','sz','恒生科技ETF'),
    ('513310','sh','中韩半导体ETF'),
    ('588200','sh','科创芯片ETF'),
    ('159326','sz','电网设备ETF'),
    ('513120','sh','港股创新药ETF'),
    ('520600','sh','港股通汽车ETF'),
    ('510050','sh','上证50ETF'),
    ('510300','sh','沪深300ETF'),
    ('159915','sz','创业板ETF'),
    ('588000','sh','科创50ETF'),
    ('159845','sz','中证1000ETF'),
    ('513050','sh','中概互联ETF'),
    ('159941','sz','纳指ETF'),
    ('513100','sh','纳指ETF'),
    ('510880','sh','红利ETF'),
    ('159766','sz','旅游ETF'),
    ('515030','sh','新能源车ETF'),
    ('159865','sz','养殖ETF'),
    ('159766','sz','旅游ETF'),
    ('512880','sh','证券ETF'),
    ('512690','sh','酒ETF'),
    ('159928','sz','消费ETF'),
    ('159745','sz','光伏ETF'),
    ('515700','sh','新能源ETF'),
    ('512480','sh','半导体ETF'),
    ('159865','sz','养殖ETF'),
    # 你关注的个股
    ('002261','sz','拓维信息'),
    ('300118','sz','东方日升'),
    ('000100','sz','TCL科技'),
    ('002129','sz','TCL中环'),
    ('600438','sh','通威股份'),
    ('601012','sh','隆基绿能'),
    # 几个热门
    ('000858','sz','五粮液'),
    ('600519','sh','贵州茅台'),
    ('300750','sz','宁德时代'),
    ('002594','sz','比亚迪'),
    ('000333','sz','美的集团'),
]

results = []
for code, mk, name in STOCKS:
    res = ck(code, mk, name)
    if res:
        results.append(res)

# 按近20日涨幅排序
results.sort(key=lambda x: x['c20%'], reverse=True)

print('强势+波动率排序 (条件: 近5日/20日上涨 + 收盘>EXPMA12 + 振幅>3%/5日>6%)')
print('=' * 110)
print(f'{"代码":<10} {"名称":<16} {"现价":<8} {"5日%":<8} {"20日%":<8} {"20日振幅%":<10} {"5日最大振幅%":<12}')
print('-' * 110)
for r in results:
    print(f'{r["code"]:<10} {r["name"]:<16} {r["close"]:<8.3f} {r["c5%"]:<8.1f} {r["c20%"]:<8.1f} {r["avg_amp20%"]:<10.1f} {r["max_amp5%"]:<12.1f}')

if not results:
    print('无符合条件标的')

# 也列出不满足条件的标的看看怎么死
print('\n\n=== 不满足条件的标的分布 ===')
for code, mk, name in STOCKS:
    bars = fetch_bars(code, 'day', market=mk, count=60)
    if not bars or len(bars) < 25:
        print(f'{mk}{code} {name}: 数据不足')
        continue
    c = [b['close'] for b in bars]
    c5 = (c[-1]-c[-6])/c[-6] if len(bars)>=6 else 0
    c20 = (c[-1]-c[-21])/c[-21] if len(bars)>=21 else 0
    ema12 = calc_ema(c,12)
    amps20 = [(b['high']-b['low'])/b['low'] if b['low']>0 else 0 for b in bars[-20:]]
    avg_amp20 = sum(amps20)/len(amps20)
    amps5 = [(b['high']-b['low'])/b['low'] if b['low']>0 else 0 for b in bars[-5:]]
    max_amp5 = max(amps5)
    
    reasons = []
    if c5 <= 0: reasons.append(f'5日跌{abs(c5)*100:.1f}%')
    if c20 <= 0: reasons.append(f'20日跌{abs(c20)*100:.1f}%')
    if c and ema12 and c[-1] <= ema12[-1]: reasons.append(f'收盘{c[-1]:.3f}<EXPMA12({ema12[-1]:.3f})')
    if avg_amp20 <= 0.03 and max_amp5 <= 0.06: reasons.append(f'振幅不足(20日均{avg_amp20*100:.1f}%<3%)')
    
    if reasons:
        print(f'{mk}{code} {name}: {"|".join(reasons)}')

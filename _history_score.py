import sys
sys.path.insert(0, 'd:/quantify-per')
sys.stdout.reconfigure(encoding='utf-8')

from cycle_engine.utils import read_csv
from cycle_engine.indicators import judge_trend

daily = read_csv('sh000001', 'daily')
min30 = read_csv('sh000001', 'min30')
min60 = read_csv('sh000001', 'min60')

# 5/8 ~ 5/22 每个交易日
target_dates = []
for i, r in enumerate(daily):
    d = str(r.get('date', ''))[:10]
    if '20260508' <= d.replace('-','') <= '20260522':
        target_dates.append(i)

dir_cn = {'bullish':'UP','bullish_bias':'UP~','neutral':'MID','bearish_bias':'DN~','bearish':'DN'}

print(f"{'date':<12} {'close':>7} {'tot':>3} {'dir':<4} {'MACD':>5} {'MA':>5} {'cyc':>3} | cycle items")
print("-" * 95)

for idx in target_dates:
    d_rows = daily[:idx+1]
    d_date = str(d_rows[-1].get('date', ''))[:10]
    m30_rows = [r for r in min30 if str(r.get('date', ''))[:10] <= d_date]
    m60_rows = [r for r in min60 if str(r.get('date', ''))[:10] <= d_date]

    if len(d_rows) < 60:
        continue

    t = judge_trend('sh000001', d_rows, 0, m30_rows, m60_rows)
    close_val = float(d_rows[-1].get('close', 0))

    # extract cycle items dict
    items_str = ''
    for dt in t.get('details', []):
        s = str(dt)
        if '余额' in s:
            start = s.find('{')
            end = s.find('}')
            if start >= 0 and end >= 0:
                items_str = s[start:end+1]
            break

    print(f"{d_date:<12} {close_val:>7.2f} {t['score']:>3.0f} {dir_cn.get(t['direction'],'?'):<4} {t['macd_score']:>5.1f} {t['ma_score']:>5.1f} {t['cycle_score']:>3} | {items_str}")

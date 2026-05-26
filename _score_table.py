import json, sys
sys.stdout.reconfigure(encoding='utf-8')

d = json.load(open('d:/quantify-per/signals/tracking/cycle_report.json', 'r', encoding='utf-8'))

tracked = ['sh000001','sz399006','sz159740','sh520600','sh513120','sz159326','sh513310','sh588200',
           'sz002261','sz300118','sz000100','sz002129','sh600438','sh601012']

dir_cn = {'bullish':'UP','bullish_bias':'UP~','neutral':'MID','bearish_bias':'DN~','bearish':'DN'}

print(f"{'code':<10} {'name':<12} {'tot':>3} {'dir':<4} {'MACD':>5} {'MA':>5} {'cyc':>3} | items")
print("-" * 110)

for code in tracked:
    matches = [i for i in d if i['code'] == code]
    if not matches:
        continue
    item = matches[0]
    t = item['trend']
    name = item.get('name', '')[:10]
    macd = t.get('macd_score', 0)
    ma = t.get('ma_score', 0)
    cycle = t.get('cycle_score', 0)
    score = t.get('score', 0)
    direction = t.get('direction', '?')

    # Extract items from details
    items_str = ''
    for dt in t.get('details', []):
        s = str(dt)
        if '余额' in s:
            # Extract just the dict part
            start = s.find('{')
            end = s.find('}')
            if start >= 0 and end >= 0:
                items_str = s[start:end+1]
            break

    print(f"{code:<10} {name:<12} {score:>3.0f} {dir_cn.get(direction,'?'):<4} {macd:>5.1f} {ma:>5.1f} {cycle:>3} | {items_str}")

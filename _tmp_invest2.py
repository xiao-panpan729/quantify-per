import json, sys
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

# 1. Real score aspects from analyze() directly
from cycle_engine.engine import analyze
r = analyze('sh000001')
t = r['trend']
print('=== 评分明细(直接analyze) ===')
print('score:', t.get('score'))
aspects = t.get('aspects', {})
for k, v in aspects.items():
    print(f'  {k}: {v}')

# 2. score history
sh = json.load(open('signals/tracking/score_history.json','r',encoding='utf-8'))
print('\n=== 分数历史 ===')
scores = sh.get('scores', {})
s = scores.get('sh000001', {})
print('sh000001:', json.dumps(s, ensure_ascii=False))

# 3. HHT daily summary
hht_list = json.load(open('signals/tracking/hht_report.json','r',encoding='utf-8'))
for item in hht_list:
    if item.get('code') == 'sh000001':
        periods = item.get('periods', {})
        for pk in ['daily','min60','min30']:
            hp = periods.get(pk, {})
            s = hp.get('summary', {})
            regime = s.get('regime', '')
            print(f'\nHHT {pk}:')
            print(f'  regime={regime}')
            print(f'  stability_label={s.get("stability_label","")}')
            print(f'  freq_stability={s.get("freq_stability","")}')
            print(f'  energy_ratio={s.get("energy_ratio","")}')
            print(f'  trend_dir={s.get("trend_dir","")}')
        break

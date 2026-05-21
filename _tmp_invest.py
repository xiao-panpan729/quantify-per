import json, sys
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1, closefd=False)

cr = json.load(open('signals/tracking/cycle_report.json','r',encoding='utf-8'))

# 1. Score + advice + dominant level for sh000001
for item in cr:
    if item['code'] != 'sh000001':
        continue
    t = item['trend']
    pos = item['position']
    adv = item['advice']
    dd = item.get('dominant_direction', {})

    print('=== 上证指数 ===')
    print(f'收盘: {pos.get("close")}')
    print(f'评分: {t.get("score")}  方向: {t.get("direction")}  标签: {t.get("label")}')
    print(f'评分aspects: {t.get("aspects", {})}')
    print(f'advice: {adv.get("action")}  grade: {adv.get("grade")}')
    dc = adv.get('dominant_cycle', {})
    print(f'主导量级: {dc.get("dominant_cycle")} {dc.get("dominant_label")}')

# 2. Check per-period ema crosses for grading.py issue
sim = json.load(open('signals/tracking/analysis_history.json','r',encoding='utf-8'))
h = sim.get('20260520', {})
old_score = h.get('sh000001', {}).get('score', '?')
new_score = h.get('20260521', {})
if not new_score:
    # try raw snapshot
    print(f'\n昨日评分: {old_score}')
print()

# check period results
for item in cr:
    if item['code'] != 'sh000001':
        continue
    for period in ['daily','min60','min30','min15','min5']:
        p = item.get('periods', {}).get(period, {})
        sq = p.get('signal_quality', {})
        if not sq:
            continue
        ecs = sq.get('ema_cross_status') if sq else None
        if ecs:
            bl = sq.get('buy_level',0); sl = sq.get('sell_level',0)
            print(f'{period}: bl={bl:.2f} sl={sl:.2f}  '
                  f'dead_idx={ecs.get("last_dead_idx",-1)} golden_idx={ecs.get("last_golden_idx",-1)}')
    print()

# 3. HHT list format
hht_list = json.load(open('signals/tracking/hht_report.json','r',encoding='utf-8'))
print('HHT type:', type(hht_list).__name__)
if isinstance(hht_list, list):
    for item in hht_list[:3]:
        if isinstance(item, dict) and item.get('code') == 'sh000001':
            print('HHT sh000001:', json.dumps(item, ensure_ascii=False)[:300])

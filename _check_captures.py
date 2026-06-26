import sys, json, glob, os
sys.stdout.reconfigure(encoding='utf-8')

base = r'D:\ima_captures\20260625\zsxq'

groups = {}
for root, dirs, files in os.walk(base):
    if root.endswith('_topics'):
        gid = os.path.basename(os.path.dirname(root))
        groups[gid] = {'count': 0, 'latest_tid': 0, 'sample': ''}
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            with open(fp, 'r', encoding='utf-8') as fh:
                d = json.load(fh)
            for t in d.get('resp_data', {}).get('topics', []):
                groups[gid]['count'] += 1
                tid = int(t.get('topic_id', '0'))
                if tid > groups[gid]['latest_tid']:
                    groups[gid]['latest_tid'] = tid
                    groups[gid]['sample'] = t.get('talk', {}).get('text', '')[:80]

for gid, info in sorted(groups.items()):
    print(f'群组 {gid}: {info["count"]} 条')
    if info['sample']:
        print(f'  样本: {info["sample"]}')
    print()

# Deep dive Geek group
print('=== Geek 群组详情 ===')
geek_dir = os.path.join(base, '28888114545551', '_topics')
if os.path.exists(geek_dir):
    all_topics = []
    for fn in sorted(os.listdir(geek_dir)):
        fp = os.path.join(geek_dir, fn)
        with open(fp, 'r', encoding='utf-8') as fh:
            d = json.load(fh)
        all_topics.extend(d.get('resp_data', {}).get('topics', []))

    tids = sorted([int(t.get('topic_id', '0')) for t in all_topics if t.get('topic_id')])
    print(f'总话题: {len(all_topics)}')
    print(f'最早 topic_id: {tids[0]}')
    print(f'最新 topic_id: {tids[-1]}')

    # 找最早的几条
    by_tid = sorted(all_topics, key=lambda t: int(t.get('topic_id', '0')))
    print(f'\n最早 3 条:')
    for t in by_tid[:3]:
        print(f'  {t.get("topic_id")}: {t.get("talk",{}).get("owner",{}).get("name","?")} | {t.get("talk",{}).get("text","")[:80]}')
    print(f'\n最新 3 条:')
    for t in by_tid[-3:]:
        print(f'  {t.get("topic_id")}: {t.get("talk",{}).get("owner",{}).get("name","?")} | {t.get("talk",{}).get("text","")[:80]}')

import json

f_path = r'C:\Users\Administrator\.claude\projects\d--quantify-per\4ea285f4-39e5-4390-99e4-6da0fc5f6e5a.jsonl'
with open(f_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

msgs = []
for line in lines:
    try:
        obj = json.loads(line.strip())
        t = obj.get('type', '')
        if t not in ('user', 'assistant'):
            continue
        c = obj.get('message', {}).get('content', '')
        txts = []
        if isinstance(c, list):
            for x in c:
                if isinstance(x, dict) and x.get('type') == 'text':
                    txts.append(x.get('text', ''))
        else:
            txts.append(str(c))
        combined = ' '.join(txts)
        if combined and len(combined) > 15:
            msgs.append({'type': t, 'text': combined[:500]})
    except Exception as e:
        pass

for i, m in enumerate(msgs[-12:]):
    print(f"=== [{m['type']}] msg #{len(msgs)-12+i+1} ===")
    print(m['text'][:400])
    print()

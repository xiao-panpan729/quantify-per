# -*- coding: utf-8 -*-
import urllib.request, urllib.parse, json, sys, os

KEY = os.environ.get('MPTEXT_API_KEY') or ''
if not KEY:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                if k.strip() == 'MPTEXT_API_KEY':
                    KEY = v.strip()
                    break  # 必须从 .env 或环境变量设置
keywords = ['中信建投证券研究', '海里的小龙龙', '亨特研究笔记', '亨特hunter', '卓哥投研笔记', '猫笔刀', 'Dorian君', '滚雪球的猫菲特']

for kw in keywords:
    encoded = urllib.parse.quote(kw.encode('utf-8'))
    url = 'https://down.mptext.top/api/public/v1/account?keyword=' + encoded + '&begin=0&size=5'
    req = urllib.request.Request(url, headers={'X-Auth-Key': KEY, 'User-Agent': 'curl/8.0'})
    try:
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        sys.stdout.buffer.write(('=== ' + kw + ' ===\n').encode('utf-8'))
        for item in data.get('list', []):
            line = u"  fakeid=%s  昵称=%s  简介=%s\n" % (item['fakeid'], item['nickname'], item.get('signature','')[:50])
            sys.stdout.buffer.write(line.encode('utf-8'))
        if not data.get('list'):
            sys.stdout.buffer.write(('  (no result)\n').encode('utf-8'))
    except Exception as e:
        sys.stdout.buffer.write(('=== ' + kw + ' === error: ' + str(e) + '\n').encode('utf-8'))

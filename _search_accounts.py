# -*- coding: utf-8 -*-
import urllib.request, urllib.parse, json, sys

KEY = '8a1e3faf9861407aa6a00eb6d4971e0c'
keywords = [u'海里的小龙龙', u'亨特研究笔记', u'亨特hunter', u'卓哥投研笔记', u'灰岩金融科技', u'猫笔刀', u'Dorian君']

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

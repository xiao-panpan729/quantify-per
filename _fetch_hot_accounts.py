# -*- coding: utf-8 -*-
"""一次性拉取4个新公众号最近20篇文章"""
import urllib.request, urllib.parse, json, os, time, sys
from datetime import datetime

KEY = '3bd37a6253b442d9a980ceb9fcaefd8a'
OUTPUT_DIR = os.path.join('d:/quantify-per/wechat_articles')

HOT_ACCOUNTS = {
    'MzkyNDUyOTA3MQ==': '盘前纪要',
    'MzkxMjUyOTI5MQ==': '盘前',
    'MzU4Mjg3MzIyNQ==': '一思一记',
    'MzAwNjY4MjQwMA==': '安静拆主线',
}

MAX_ARTICLES = 20  # 多拉一些，覆盖一周

def api_get(url):
    headers = {'X-Auth-Key': KEY, 'User-Agent': 'Mozilla/5.0'}
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            return None

def download_article(article_url):
    encoded = urllib.parse.quote(article_url, safe='')
    url = f'https://down.mptext.top/api/public/v1/download?url={encoded}&format=text'
    raw = api_get(url)
    if not raw or len(raw.strip()) < 50:
        return None
    try:
        err_data = json.loads(raw)
        if err_data.get('base_resp', {}).get('ret', 0) != 0:
            return None
    except:
        pass
    return raw

print('拉取4个情绪热点公众号...\n')

for fakeid, name in HOT_ACCOUNTS.items():
    dir_path = os.path.join(OUTPUT_DIR, name)
    os.makedirs(dir_path, exist_ok=True)

    url = f'https://down.mptext.top/api/public/v1/article?fakeid={fakeid}&begin=0&size={MAX_ARTICLES}'
    raw = api_get(url)
    if not raw:
        print(f'  ⚠ [{name}] 请求失败')
        continue

    try:
        data = json.loads(raw)
    except:
        print(f'  ⚠ [{name}] JSON解析失败')
        continue

    articles = data.get('articles', [])
    saved = 0
    skipped = 0

    for art in articles:
        if not art.get('link') or art.get('is_deleted'):
            continue
        ts = art.get('update_time', 0)
        dt = datetime.fromtimestamp(ts) if ts else datetime.now()
        prefix = dt.strftime('%Y%m%d_%H%M')
        safe_title = ''.join(c if c.isalnum() or c in ' _-' else '_' for c in art.get('title', '')[:30])
        filename = f"{prefix}_{safe_title}.txt"
        filepath = os.path.join(dir_path, filename)

        if os.path.exists(filepath):
            skipped += 1
            continue

        content = download_article(art['link'])
        if content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"标题: {art.get('title', '')}\n")
                f.write(f"时间: {dt.strftime('%Y-%m-%d %H:%M')}\n")
                f.write(f"链接: {art['link']}\n")
                f.write("="*50 + "\n")
                f.write(content)
            saved += 1
            print(f'  [{name}] ✅ {art.get("title", "")[:25]}')
        else:
            print(f'  [{name}] ❌ {art.get("title", "")[:25]}')

    print(f'  [{name}] 完成: 新增{saved} 跳过{skipped}')
    print()

print('全部完成！')

# -*- coding: utf-8 -*-
"""批量拉取微信公众号最新文章全文（带重试+更健壮）"""
import urllib.request, urllib.parse, json, os, time, sys
from datetime import datetime
from config import PROJECT_ROOT

KEY = os.environ.get('MPTEXT_API_KEY', '8a1e3faf9861407aa6a00eb6d4971e0c')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'wechat_articles')
os.makedirs(OUTPUT_DIR, exist_ok=True)

ACCOUNTS = {
    'Mzg2MDc2NzQ3MQ==': '表舅是养基大户',
    'MzIwNTQ1ODQwMQ==': 'laoduo',
    'MzIwOTcyMzA3OA==': '海里的小龙龙',
    'Mzg4MDk2NDE2Nw==': '亨特研究笔记',
    'Mzk0MzY0OTU5Ng==': '卓哥投研笔记',
    'MzIzODg2NDQyMA==': '灰岩金融科技',
    'MzE5ODk2NjUwOA==': '猫笔刀',
}

def api_get(url, use_key=True, retries=3):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    if use_key:
        headers['X-Auth-Key'] = KEY
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=60)
            return resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5 * (attempt + 1)
                time.sleep(wait)
                continue
            return f'HTTP_ERROR:{e.code}'
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return f'ERROR:{str(e)[:50]}'

def get_article_links(fakeid, max_msgs=30):
    links = []
    begin = 0
    while begin < max_msgs:
        url = f'https://down.mptext.top/api/public/v1/article?fakeid={fakeid}&begin={begin}&size=20'
        raw = api_get(url)
        if raw.startswith('HTTP_ERROR') or raw.startswith('ERROR') or not raw.strip():
            break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            break
        articles = data.get('articles', [])
        if not articles:
            break
        for a in articles:
            if a.get('link') and not a.get('is_deleted', False):
                links.append({
                    'link': a['link'].strip(),
                    'title': (a.get('title', '') or '').strip(),
                    'time': a.get('update_time', 0),
                })
        begin += 20
        time.sleep(0.3)
    return links

def download_article(article_url):
    encoded = urllib.parse.quote(article_url, safe='')
    url = f'https://down.mptext.top/api/public/v1/download?url={encoded}&format=text'
    raw = api_get(url, use_key=False)
    if raw.startswith('HTTP_ERROR') or raw.startswith('ERROR'):
        return None
    if not raw or len(raw.strip()) < 50:
        return None
    return raw

for fakeid, name in ACCOUNTS.items():
    print(f'\n===== {name} =====')
    dir_path = os.path.join(OUTPUT_DIR, name)
    os.makedirs(dir_path, exist_ok=True)

    links = get_article_links(fakeid, max_msgs=30)
    print(f'  获取到 {len(links)} 篇文章链接')

    saved = 0
    skipped = 0
    failed = 0
    for i, art in enumerate(links[:35]):
        ts = art['time']
        dt = datetime.fromtimestamp(ts) if ts else datetime.now()
        prefix = dt.strftime('%Y%m%d_%H%M')
        safe_title = ''.join(c if c.isalnum() or c in ' _-' else '_' for c in art['title'][:30])
        filename = f"{prefix}_{safe_title}.txt"
        filepath = os.path.join(dir_path, filename)

        if os.path.exists(filepath):
            skipped += 1
            continue

        content = download_article(art['link'])
        if content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"标题: {art['title']}\n")
                f.write(f"时间: {dt.strftime('%Y-%m-%d %H:%M')}\n")
                f.write(f"链接: {art['link']}\n")
                f.write("="*50 + "\n")
                f.write(content)
            saved += 1
            sys.stdout.write(f'  [{dt.strftime("%m-%d %H:%M")}] OK {art["title"][:25]}\n')
            sys.stdout.flush()
        else:
            failed += 1
            sys.stdout.write(f'  [{dt.strftime("%m-%d %H:%M")}] XX {art["title"][:25]}\n')
            sys.stdout.flush()

        time.sleep(1.0)

    print(f'  -> 新增:{saved} 跳过:{skipped} 失败:{failed}')

print(f'\n全部完成！文件保存在: {OUTPUT_DIR}')

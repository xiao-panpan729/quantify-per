# -*- coding: utf-8 -*-
"""全量拉取单个公众号所有历史文章。供多agent并行调用。"""
import urllib.request, urllib.parse, json, os, time, sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

KEY = os.environ.get('MPTEXT_API_KEY') or ''
if not KEY:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line: continue
                k, v = line.split('=', 1)
                if k.strip() == 'MPTEXT_API_KEY': KEY = v.strip(); break
if not KEY:
    print('❌ MPTEXT_API_KEY 未设置')
    sys.exit(1)

OUTPUT_DIR = r'D:\quantify-per\wechat_articles'

def api_get(url, retries=3):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'X-Auth-Key': KEY}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            return f'HTTP_ERROR:{e.code}'
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return f'ERROR:{str(e)[:50]}'

def download_article(article_url):
    encoded = urllib.parse.quote(article_url, safe='')
    url = f'https://down.mptext.top/api/public/v1/download?url={encoded}&format=text'
    raw = api_get(url)
    if raw is None or raw.startswith('HTTP_ERROR') or raw.startswith('ERROR'):
        return None
    try:
        err_data = json.loads(raw)
        if err_data.get('base_resp', {}).get('ret', 0) != 0:
            return None
    except json.JSONDecodeError:
        pass
    if len(raw.strip()) < 50:
        return None
    return raw

def fetch_all(fakeid, name, offset=0):
    """全量分页拉取（offset: 起始文章偏移量，用于多段并行）"""
    dir_path = os.path.join(OUTPUT_DIR, name)
    os.makedirs(dir_path, exist_ok=True)

    page_size = 20
    begin = offset
    total_new = 0
    total_skip = 0
    total_fail = 0

    while True:
        url = f'https://down.mptext.top/api/public/v1/article?fakeid={fakeid}&begin={begin}&size={page_size}'
        raw = api_get(url)
        if raw.startswith('HTTP_ERROR') or raw.startswith('ERROR'):
            print(f'  ⚠ [{name}] API失败: {raw}')
            break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f'  ⚠ [{name}] JSON解析失败')
            break

        if data.get('base_resp', {}).get('ret', 0) != 0:
            print(f'  ⚠ [{name}] API错误: {data["base_resp"].get("err_msg","")}')
            break

        articles = data.get('articles', [])
        if not articles:
            print(f'  [{name}] 已无更多文章（begin={begin}）')
            break

        for a in articles:
            if not a.get('link') or a.get('is_deleted', False):
                continue
            ts = a.get('update_time', 0)
            dt = datetime.fromtimestamp(ts) if ts else datetime.now()
            prefix = dt.strftime('%Y%m%d_%H%M')
            safe_title = ''.join(c if c.isalnum() or c in ' _-' else '_' for c in (a.get('title', '') or '')[:30])
            filename = f"{prefix}_{safe_title}.txt"
            filepath = os.path.join(dir_path, filename)

            if os.path.exists(filepath):
                total_skip += 1
                continue

            content = download_article(a['link'])
            if content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(f"标题: {a.get('title', '')}\n")
                    f.write(f"时间: {dt.strftime('%Y-%m-%d %H:%M')}\n")
                    f.write(f"链接: {a['link']}\n")
                    f.write("="*50 + "\n")
                    f.write(content)
                total_new += 1
                if total_new <= 3:
                    print(f'  [{name}] NEW {dt.strftime("%m-%d %H:%M")} {a.get("title","")[:30]}')
            else:
                total_fail += 1

        if len(articles) < page_size:
            print(f'  [{name}] 最后一页（共{begin+len(articles)}篇）')
            break

        begin += page_size
        time.sleep(0.5)  # 限速

    return total_new, total_skip, total_fail

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--fakeid', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--offset', type=int, default=0, help='起始偏移量（篇数），用于多段并行')
    args = parser.parse_args()

    print(f'===== {args.name} 全量拉取 (offset={args.offset}) =====')
    new, skip, fail = fetch_all(args.fakeid, args.name, args.offset)
    print(f'[{args.name}] 新增:{new} 跳过:{skip} 失败:{fail}')

"""
_fetch_csc_history.py — 批量拉取中信建投证券研究公众号历史文章

用于补全板块产业政策事件标注所需的研报/产业分析历史数据。

与 _fetch_articles.py 共用 mptext.top API，但对中信建投单独设更大拉取量。

Usage:
  python _fetch_csc_history.py                    # 默认拉最近500篇
  python _fetch_csc_history.py --max 1000         # 拉1000篇
  python _fetch_csc_history.py --dry-run          # 只预览不下载
"""

import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'wechat_articles', '中信建投证券研究')
os.makedirs(OUTPUT_DIR, exist_ok=True)

KEY = os.environ['MPTEXT_API_KEY']  # 必须从 .env 或环境变量设置
FAKEID = 'MzI3ODAyODI0Ng=='
API_BASE = 'https://down.mptext.top/api/public/v1/article'
DOWNLOAD_API = 'https://down.mptext.top/api/public/v1/download'


def api_get(url, retries=3):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    headers['X-Auth-Key'] = KEY
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=60)
            return resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return None


def get_article_links(max_msgs=500):
    """获取公众号文章链接列表"""
    links = []
    begin = 0
    while begin < max_msgs:
        url = f'{API_BASE}?fakeid={FAKEID}&begin={begin}&size=20'
        raw = api_get(url)
        if not raw:
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
    """下载文章全文"""
    encoded = urllib.parse.quote(article_url, safe='')
    url = f'{DOWNLOAD_API}?url={encoded}&format=text'
    raw = api_get(url)
    if not raw or len(raw.strip()) < 50:
        return None
    return raw


def main():
    import argparse
    p = argparse.ArgumentParser(description='批量拉取中信建投证券研究公众号历史文章')
    p.add_argument('--max', type=int, default=500, help='最大拉取篇数 (默认500)')
    p.add_argument('--dry-run', action='store_true', help='只预览不下载')
    args = p.parse_args()

    print(f'正在获取中信建投公众号文章链接 (max={args.max})...')
    links = get_article_links(args.max)
    print(f'获取到 {len(links)} 篇文章链接')

    if not links:
        print('无文章链接，退出')
        return

    time_range = ''
    timestamps = [l['time'] for l in links if l['time']]
    if timestamps:
        oldest = datetime.fromtimestamp(min(timestamps)).strftime('%Y-%m-%d')
        newest = datetime.fromtimestamp(max(timestamps)).strftime('%Y-%m-%d')
        time_range = f'{oldest} ~ {newest}'
        print(f'时间范围: {time_range}')

    if args.dry_run:
        print(f'\n预览前20篇:')
        for l in links[:20]:
            dt = datetime.fromtimestamp(l['time']).strftime('%m-%d %H:%M') if l['time'] else '??'
            print(f'  [{dt}] {l["title"][:40]}')
        print(f'... 共 {len(links)} 篇')
        return

    # 下载去重
    saved = 0
    skipped = 0
    failed = 0
    for i, art in enumerate(links):
        ts = art['time']
        dt = datetime.fromtimestamp(ts) if ts else datetime.now()
        prefix = dt.strftime('%Y%m%d_%H%M')
        safe_title = ''.join(c if c.isalnum() or c in ' _-' else '_' for c in art['title'][:30])
        filename = f'{prefix}_{safe_title}.txt'
        filepath = os.path.join(OUTPUT_DIR, filename)

        if os.path.exists(filepath):
            skipped += 1
            continue

        content = download_article(art['link'])
        if content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"标题: {art['title']}\n")
                f.write(f"时间: {dt.strftime('%Y-%m-%d %H:%M')}\n")
                f.write(f"链接: {art['link']}\n")
                f.write('=' * 50 + '\n')
                f.write(content)
            saved += 1
        else:
            failed += 1

        if (i + 1) % 20 == 0:
            print(f'  进度: {i+1}/{len(links)}, 新增={saved}, 跳过={skipped}, 失败={failed}')

        time.sleep(1.0)

    print(f'\n完成！新增={saved}, 跳过={skipped}, 失败={failed}')
    print(f'时间范围: {time_range}')
    print(f'保存目录: {OUTPUT_DIR}')


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""并行拉取微信公众号最新文章全文（增量，每个号最近5篇）"""
import urllib.request, urllib.parse, json, os, time, sys, re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import PROJECT_ROOT

sys.stdout.reconfigure(encoding='utf-8')

KEY = os.environ.get('MPTEXT_API_KEY', '09b9d3bb2dec4d6f8e18fcf55a4853de')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'wechat_articles')
os.makedirs(OUTPUT_DIR, exist_ok=True)

MAX_ARTICLES = 5  # 每个号只看最近5篇

ACCOUNTS = {
    'Mzg2MDc2NzQ3MQ==': '表舅是养基大户',
    'MzIwNTQ1ODQwMQ==': 'laoduo',
    'MzIwOTcyMzA3OA==': '海里的小龙龙',
    'Mzg4MDk2NDE2Nw==': '亨特研究笔记',
    'Mzk0MzY0OTU5Ng==': '卓哥投研笔记',
    'MzIzODg2NDQyMA==': '灰岩金融科技',
    'MzE5ODk2NjUwOA==': '猫笔刀',
    'MzI3ODAyODI0Ng==': '中信建投证券研究',
    # ── 情绪热点（盘前/复盘/产业链吹票） ──
    'MzkyNDUyOTA3MQ==': '盘前纪要',
    'MzkxMjUyOTI5MQ==': '盘前',
    'MzU4Mjg3MzIyNQ==': '一思一记',
    'MzAwNjY4MjQwMA==': '安静拆主线',
}

# ── 公众号分类映射（下游据此区分处理） ──
ACCOUNT_CATEGORIES = {
    '表舅是养基大户': '宏观/观点',
    'laoduo':          '宏观/观点',
    '海里的小龙龙':    '宏观/观点',
    '亨特研究笔记':    '宏观/观点',
    '卓哥投研笔记':    '宏观/观点',
    '灰岩金融科技':    '宏观/观点',
    '猫笔刀':          '宏观/观点',
    '中信建投证券研究':'宏观/观点',
    '盘前纪要':        '情绪热点',
    '盘前':            '情绪热点',
    '一思一记':        '情绪热点',
    '安静拆主线':      '情绪热点',
}


def api_get(url, use_key=True, retries=3):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    if use_key:
        headers['X-Auth-Key'] = KEY
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 3 * (attempt + 1)
                time.sleep(wait)
                continue
            return f'HTTP_ERROR:{e.code}'
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return f'ERROR:{str(e)[:50]}'


def get_article_links(fakeid, account_name=""):
    """取最近 MAX_ARTICLES 篇文章链接"""
    url = f'https://down.mptext.top/api/public/v1/article?fakeid={fakeid}&begin=0&size={MAX_ARTICLES}'
    raw = api_get(url)
    if raw.startswith('HTTP_ERROR') or raw.startswith('ERROR') or not raw.strip():
        print(f'  ⚠ [{account_name}] API请求失败: {raw}')
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f'  ⚠ [{account_name}] API返回格式异常')
        return []
    # 检测API业务错误（Key过期/无效等）
    base_resp = data.get('base_resp', {})
    if base_resp.get('ret', 0) != 0:
        err_msg = base_resp.get('err_msg', '未知错误')
        print(f'  ⚠ [{account_name}] API错误: {err_msg}')
        return []
    links = []
    for a in data.get('articles', []):
        if a.get('link') and not a.get('is_deleted', False):
            links.append({
                'link': a['link'].strip(),
                'title': (a.get('title', '') or '').strip(),
                'time': a.get('update_time', 0),
            })
    return links[:MAX_ARTICLES]


def download_article(article_url):
    encoded = urllib.parse.quote(article_url, safe='')
    url = f'https://down.mptext.top/api/public/v1/download?url={encoded}&format=text'
    raw = api_get(url)
    if raw is None or raw.startswith('HTTP_ERROR') or raw.startswith('ERROR'):
        return None
    # 检测API业务错误
    try:
        err_data = json.loads(raw)
        base_resp = err_data.get('base_resp', {})
        if base_resp.get('ret', 0) != 0:
            return None
    except json.JSONDecodeError:
        pass
    if len(raw.strip()) < 50:
        return None
    return raw


def process_account(fakeid, name):
    """处理单个公众号：拉链接→下载新文章→返回统计"""
    dir_path = os.path.join(OUTPUT_DIR, name)
    os.makedirs(dir_path, exist_ok=True)

    links = get_article_links(fakeid, name)
    saved = 0
    skipped = 0
    failed = 0
    results = []

    for art in links:
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
            results.append(f'  [{dt.strftime("%m-%d %H:%M")}] OK {art["title"][:25]}')
        else:
            failed += 1
            results.append(f'  [{dt.strftime("%m-%d %H:%M")}] XX {art["title"][:25]}')

    return name, len(links), saved, skipped, failed, results


# ─── 网页信源（外资观点，零VPN） ───
WEB_OUTPUT_DIR = os.path.join(OUTPUT_DIR, '_web_sources')
os.makedirs(WEB_OUTPUT_DIR, exist_ok=True)

FOREIGN_BANK_KEYWORDS = ['高盛', 'Goldman', '摩根士丹利', '大摩', 'Morgan Stanley',
                         '摩根大通', '小摩', 'JPMorgan', '瑞银', 'UBS',
                         '花旗', 'Citi', 'Citigroup', '美银', 'BofA',
                         '外资行', '外资机构', '华尔街', '外资观点']


def fetch_web_articles(url, source_name, extract_fn=None):
    """抓取网页中命中外资行关键词的文章链接"""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp = urllib.request.urlopen(req, timeout=30)
        html = resp.read().decode('utf-8', errors='replace')

        if extract_fn:
            return extract_fn(html, url)
        return []
    except Exception as e:
        print(f'  [{source_name}] 抓取失败: {str(e)[:50]}')
        return []


def extract_reuters_links(html, base_url):
    """从 Reuters 中文网首页提取外资观点相关文章"""
    articles = []
    for m in re.finditer(r'<a[^>]*href="(https?://cn\.reuters\.com[^"]*)"[^>]*>([^<]+)</a>', html):
        link, title = m.group(1), m.group(2).strip()
        if any(kw in title for kw in FOREIGN_BANK_KEYWORDS):
            articles.append({'title': title, 'link': link, 'source': 'reuters_cn'})
    if not articles:
        for m in re.finditer(r'<a[^>]*href="(/article/[^"]*)"[^>]*>([^<]+)</a>', html):
            link = base_url.rstrip('/') + m.group(1)
            title = m.group(2).strip()
            if any(kw in title for kw in FOREIGN_BANK_KEYWORDS):
                articles.append({'title': title, 'link': link, 'source': 'reuters_cn'})
    return articles[:5]


def extract_cls_links(html, base_url):
    """从财联社网站提取外资观点相关文章"""
    articles = []
    for m in re.finditer(r'<a[^>]*href="(https?://www\.cls\.cn[^"]*)"[^>]*>([^<]+)</a>', html):
        link, title = m.group(1), m.group(2).strip()
        if any(kw in title for kw in FOREIGN_BANK_KEYWORDS):
            articles.append({'title': title, 'link': link, 'source': 'cls'})
    if not articles:
        for m in re.finditer(r'<a[^>]*href"(/[^"]*)"[^>]*>([^<]+)</a>', html):
            link = base_url.rstrip('/') + m.group(1)
            title = m.group(2).strip()
            if any(kw in title for kw in FOREIGN_BANK_KEYWORDS):
                articles.append({'title': title, 'link': link, 'source': 'cls'})
    return articles[:5]


def save_web_article(art):
    """保存单条网页文章到 _web_sources/ 目录"""
    now = datetime.now()
    prefix = now.strftime('%Y%m%d_%H%M')
    safe_title = ''.join(c if c.isalnum() or c in ' _-' else '_' for c in art['title'][:30])
    filename = f"{prefix}_{art['source']}_{safe_title}.txt"
    filepath = os.path.join(WEB_OUTPUT_DIR, filename)

    if os.path.exists(filepath):
        return 'skipped'

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"标题: {art['title']}\n")
        f.write(f"来源: {art.get('source', 'web')}\n")
        f.write(f"链接: {art.get('link', '')}\n")
        f.write(f"抓取时间: {now.strftime('%Y-%m-%d %H:%M')}\n")
        f.write("="*50 + "\n")
        f.write("（内容待完善，请手动访问链接获取全文）\n")
    return 'saved'


def fetch_all_web_sources():
    """并行抓取所有网页信源"""
    web_sources = [
        ('Reuters中文网', 'https://cn.reuters.com/', extract_reuters_links),
        ('财联社', 'https://www.cls.cn/', extract_cls_links),
    ]

    total_saved = 0
    total_skipped = 0

    for name, url, extract_fn in web_sources:
        print(f'\n===== {name} =====')
        articles = fetch_web_articles(url, name, extract_fn)
        if not articles:
            print(f'  无外资观点文章')
            continue
        for art in articles:
            result = save_web_article(art)
            if result == 'saved':
                total_saved += 1
                print(f'  OK {art["title"][:40]}')
            else:
                total_skipped += 1
                print(f'  -- {art["title"][:40]} (已存在)')

    return total_saved, total_skipped


# === 主流程：并行拉取 ===
print(f'并行拉取 {len(ACCOUNTS)} 个公众号，每个号最近 {MAX_ARTICLES} 篇\n')

total_saved = 0
total_skipped = 0
total_failed = 0

with ThreadPoolExecutor(max_workers=5) as pool:
    futures = {pool.submit(process_account, fid, name): name for fid, name in ACCOUNTS.items()}
    for fut in as_completed(futures):
        name, n_links, saved, skipped, failed, results = fut.result()
        print(f'===== {name} =====')
        print(f'  链接 {n_links} → 新增:{saved} 跳过:{skipped} 失败:{failed}')
        for r in results:
            print(r)
        total_saved += saved
        total_skipped += skipped
        total_failed += failed

print(f'\n全部完成！新增:{total_saved} 跳过:{total_skipped} 失败:{total_failed}')
print(f'文件保存在: {OUTPUT_DIR}')

# ─── 网页信源 ───
print(f'\n--- 网页信源（外资观点） ---')
web_saved, web_skipped = fetch_all_web_sources()
print(f'网页信源完成！新增:{web_saved} 跳过:{web_skipped}')

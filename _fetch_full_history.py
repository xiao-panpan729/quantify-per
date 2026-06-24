# -*- coding: utf-8 -*-
"""
全量历史公众号拉取 — 翻页拉取全部历史文章，支持多 Agent 并行

用法:
  # 全量拉取所有号（共13个：8个日报号 + 5个图谱号）
  python _fetch_full_history.py --all

  # 多 Agent 并行：各开一个窗口，各跑几个号
  # Agent 1：日报常用号
  python _fetch_full_history.py --accounts 表舅 laoduo 亨特 卓哥 猫笔刀 海里的小龙龙 灰岩
  # Agent 2：情绪热点 + 图谱核心
  python _fetch_full_history.py --accounts 猫菲特 盘前纪要 盘前 一思一记 安静拆主线
  # Agent 3：研报（已通过 _fetch_csc_history.py 拉了5310篇，补漏用）
  python _fetch_full_history.py --accounts 中信建投

  # 华尔街见闻 — 非公众号，不在本脚本范围
  # 走 tools/sentiment/shock_detector.py fetch_wallstreetcn()

  # 时间范围：默认最近6个月（设 --months 0 = 全部历史）
  python _fetch_full_history.py --all                    # 最近6个月
  python _fetch_full_history.py --all --months 3         # 最近3个月
  python _fetch_full_history.py --accounts 中信建投 --months 0  # 全部历史

  # 指定每页大小（默认50），翻页上限（默认500页=25000篇）
  python _fetch_full_history.py --all --page-size 100 --max-pages 200

  # 仅统计每个号已拉了多少、还有多少没拉（不下载）
  python _fetch_full_history.py --all --dry-run
"""
import urllib.request, urllib.parse, json, os, time, sys, argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = os.environ['MPTEXT_API_KEY']  # 必须从 .env 或环境变量设置
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'wechat_articles')

# ─── 全部公众号清单（与 _fetch_articles.py 保持一致） ───
ALL_ACCOUNTS = {
    'Mzg2MDc2NzQ3MQ==': '表舅是养基大户',
    'MzIwNTQ1ODQwMQ==': 'laoduo',
    'MzIwOTcyMzA3OA==': '海里的小龙龙',
    'Mzg4MDk2NDE2Nw==': '亨特研究笔记',
    'Mzk0MzY0OTU5Ng==': '卓哥投研笔记',
    'MzIzODg2NDQyMA==': '灰岩金融科技',
    'MzE5ODk2NjUwOA==': '猫笔刀',
    'Mzg3NjYyNzAzNQ==': '滚雪球的猫菲特闲唠嗑',
    # ─── 情绪热点（已从日报排除，但图谱需要全量历史） ───
    'MzkyNDUyOTA3MQ==': '盘前纪要',
    'MzkxMjUyOTI5MQ==': '盘前',
    'MzU4Mjg3MzIyNQ==': '一思一记',
    'MzAwNjY4MjQwMA==': '安静拆主线',
    'MzI3ODAyODI0Ng==': '中信建投证券研究',
}
# 注意: 中信建投还有独立脚本 _fetch_csc_history.py（155行），
# 本脚本走通用 API 翻页，两者不冲突，会去重。

# ─── 华尔街见闻（非公众号，走 shock_detector 的 REST API） ───
# 华尔街见闻是新闻快讯站，不是微信公众号。
# 实时快讯已在 tools/sentiment/shock_detector.py 中通过
# fetch_wallstreetcn() 拉取（游标翻页），
# 需要历史数据的话需单独写脚本调用 wallstreetcn REST API。

# 反查：短名 → fakeid
NAME_TO_FAKEID = {v: k for k, v in ALL_ACCOUNTS.items()}


def api_get(url, retries=5):
    """带重试的 GET 请求"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'X-Auth-Key': KEY,
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = min(10 * (attempt + 1), 60)
                print(f'    ⏳ 429 限流，等待 {wait}s...')
                time.sleep(wait)
                continue
            return f'HTTP_ERROR:{e.code}'
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return f'ERROR:{str(e)[:80]}'


def paginate_articles(fakeid, account_name, page_size=50, max_pages=500, existing_titles=None, cutoff_ts=0):
    """翻页拉取文章链接（只返回 cutoff_ts 之后的），返回 article 列表"""
    all_links = []
    seen_links = set()
    empty_pages = 0
    hit_cutoff = False  # API按时间倒序，遇到旧文章就提前结束

    for page in range(max_pages):
        if hit_cutoff:
            break
        begin = page * page_size
        url = (f'https://down.mptext.top/api/public/v1/article'
               f'?fakeid={fakeid}&begin={begin}&size={page_size}')
        raw = api_get(url)

        if raw.startswith('HTTP_ERROR') or raw.startswith('ERROR'):
            print(f'  ⚠ 第{page+1}页请求失败: {raw}')
            break

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f'  ⚠ 第{page+1}页返回格式异常')
            break

        base_resp = data.get('base_resp', {})
        if base_resp.get('ret', 0) != 0:
            err_msg = base_resp.get('err_msg', '')
            print(f'  ⚠ API错误 (page {page+1}): {err_msg}')
            break

        articles = data.get('articles', [])
        if not articles:
            empty_pages += 1
            if empty_pages >= 3:  # 连续3页为空 → 到底了
                break
            continue
        empty_pages = 0

        new_count = 0
        for a in articles:
            link = a.get('link', '').strip()
            if not link or a.get('is_deleted', False):
                continue
            if link in seen_links:
                continue
            seen_links.add(link)

            # 时间过滤：只取 cutoff_ts 之后的文章
            article_ts = a.get('update_time', 0)
            if cutoff_ts > 0 and article_ts < cutoff_ts:
                # API按时间倒序，遇到第一篇超期的就标记结束
                hit_cutoff = True
                break

            title = (a.get('title', '') or '').strip()
            # 如果标题已存在本地，跳过
            if existing_titles and title in existing_titles:
                continue

            all_links.append({
                'link': link,
                'title': title,
                'time': article_ts,
            })
            new_count += 1

        if page % 10 == 0:
            print(f'  第{page+1}页: 新增 {new_count} 篇（累计 {len(all_links)} 篇）')

    return all_links


def get_existing_titles(account_name):
    """读取本地已有文章标题，用于去重"""
    dir_path = os.path.join(OUTPUT_DIR, account_name)
    if not os.path.isdir(dir_path):
        return set()
    titles = set()
    for fname in os.listdir(dir_path):
        if not fname.endswith('.txt'):
            continue
        # 文件名格式: YYYYMMDD_HHMM_title.txt
        parts = fname.split('_', 2)
        if len(parts) >= 3:
            title_part = parts[2].rsplit('.', 1)[0]
            titles.add(title_part.replace('_', ' '))
    return titles


def download_article(article_url):
    """下载单篇文章正文"""
    encoded = urllib.parse.quote(article_url, safe='')
    url = f'https://down.mptext.top/api/public/v1/download?url={encoded}&format=text'
    raw = api_get(url, retries=3)
    if raw is None or raw.startswith('HTTP_ERROR') or raw.startswith('ERROR'):
        return None
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


def process_account(fakeid, name, page_size=50, max_pages=500, dry_run=False, cutoff_ts=0):
    """处理单个公众号：翻页→去重→下载"""
    print(f'\n{"="*60}')
    print(f'📡 {name}')
    print(f'{"="*60}')

    dir_path = os.path.join(OUTPUT_DIR, name)
    os.makedirs(dir_path, exist_ok=True)

    # 已有数量
    existing_count = len([f for f in os.listdir(dir_path) if f.endswith('.txt')]) if os.path.isdir(dir_path) else 0
    print(f'   本地已有: {existing_count} 篇')

    # 获取本地已有标题进行去重
    existing_titles = get_existing_titles(name) if existing_count > 0 else set()
    print(f'   去重标题库: {len(existing_titles)} 条')

    cutoff_str = datetime.fromtimestamp(cutoff_ts).strftime('%Y-%m-%d') if cutoff_ts else '不限'
    print(f'   时间范围: {cutoff_str} 至今')

    # 翻页拉取全部文章链接
    print(f'   翻页拉取中（每页{page_size}篇，上限{max_pages}页）...')
    links = paginate_articles(fakeid, name, page_size, max_pages, existing_titles, cutoff_ts)

    if not links:
        print(f'   没有新文章')
        return name, 0, 0, 0, 0, existing_count

    print(f'   待下载: {len(links)} 篇')

    if dry_run:
        print(f'   🏁 DRY RUN 模式，不下载')
        return name, len(links), 0, 0, 0, existing_count

    # 下载
    saved = 0
    skipped = 0
    failed = 0
    batch_log = []

    for i, art in enumerate(links):
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
                f.write("=" * 50 + "\n")
                f.write(content)
            saved += 1
            if saved <= 3 or (saved % 20 == 0):
                batch_log.append(f'  [{dt.strftime("%m-%d %H:%M")}] OK {art["title"][:30]}')
        else:
            failed += 1
            if failed <= 3:
                batch_log.append(f'  [{dt.strftime("%m-%d %H:%M")}] XX {art["title"][:30]}')

        # 进度
        if (i + 1) % 20 == 0 or i == len(links) - 1:
            pct = (i + 1) / len(links) * 100
            print(f'   进度: {i+1}/{len(links)} ({pct:.0f}%) | OK={saved} SKIP={skipped} FAIL={failed}')

        if (i + 1) % 50 == 0 and i + 1 < len(links):
            time.sleep(0.5)  # 每50篇喘口气

    for log in batch_log:
        print(log)

    total_now = existing_count + saved
    print(f'   ✅ {name}: 新增 {saved} 篇 | 跳过 {skipped} | 失败 {failed} | 总计 {total_now} 篇')
    return name, len(links), saved, skipped, failed, total_now


def parse_accounts(account_names):
    """把用户输入的短名解析成 (fakeid, name) 列表"""
    resolved = []
    for name in account_names:
        # 先精确匹配
        if name in NAME_TO_FAKEID:
            resolved.append((NAME_TO_FAKEID[name], name))
        else:
            # 模糊匹配
            matched = False
            for n in ALL_ACCOUNTS.values():
                if name in n:
                    resolved.append((NAME_TO_FAKEID[n], n))
                    matched = True
                    break
            if not matched:
                print(f'⚠ 未找到公众号: {name}')
    return resolved


def main():
    parser = argparse.ArgumentParser(description='全量历史公众号文章拉取')
    parser.add_argument('--all', action='store_true', help='拉取全部公众号')
    parser.add_argument('--accounts', nargs='+', help='指定公众号名称（支持模糊匹配）')
    parser.add_argument('--page-size', type=int, default=50, help='每页篇数（默认50）')
    parser.add_argument('--max-pages', type=int, default=500, help='最大翻页数（默认500）')
    parser.add_argument('--dry-run', action='store_true', help='只统计不下载')
    parser.add_argument('--months', type=int, default=6, help='拉取最近N个月（默认6，设0=全部历史）')
    parser.add_argument('--workers', type=int, default=3, help='并行下载线程数（默认3）')
    args = parser.parse_args()

    if not args.all and not args.accounts:
        parser.print_help()
        print('\n⚠ 请指定 --all 或 --accounts')
        sys.exit(1)

    # 计算时间截止点
    cutoff_ts = 0
    months_str = '全部历史'
    if args.months > 0:
        cutoff_dt = datetime.now()
        # 向前推 N 个月（粗略按30天/月）
        import calendar
        for _ in range(args.months):
            day_max = calendar.monthrange(cutoff_dt.year, cutoff_dt.month)[1]
            cutoff_dt = cutoff_dt.replace(day=min(cutoff_dt.day, day_max))
            cutoff_dt = cutoff_dt.replace(month=cutoff_dt.month - 1 if cutoff_dt.month > 1 else 12,
                                        year=cutoff_dt.year - 1 if cutoff_dt.month <= 1 else cutoff_dt.year)
        cutoff_ts = cutoff_dt.timestamp()
        months_str = f'最近{args.months}个月 ({cutoff_dt.strftime("%Y-%m-%d")} 至今)'

    if args.all:
        targets = list(ALL_ACCOUNTS.items())  # (fakeid, name)
    else:
        targets = parse_accounts(args.accounts)

    print(f'\n{"="*60}')
    print(f'🚀 全量历史拉取启动')
    print(f'   目标: {len(targets)} 个公众号')
    print(f'   每页: {args.page_size} 篇 | 最大翻页: {args.max_pages}')
    print(f'   时间范围: {months_str}')
    print(f'   模式: {"DRY RUN（仅统计）" if args.dry_run else "正常下载"}')
    print(f'   并行: {args.workers} 线程')
    print(f'{"="*60}')

    start_time = time.time()
    total_new = 0
    total_links = 0
    total_skipped = 0
    total_failed = 0

    if args.dry_run or args.workers <= 1:
        # 串行执行（dry-run 或单线程）
        for fakeid, name in targets:
            result = process_account(fakeid, name, args.page_size, args.max_pages, args.dry_run, cutoff_ts)
            _, n_links, saved, skipped, failed, total_now = result
            total_links += n_links
            total_new += saved
            total_skipped += skipped
            total_failed += failed
    else:
        # 并行执行
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(process_account, fid, name, args.page_size, args.max_pages, args.dry_run, cutoff_ts): name
                for fid, name in targets
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    _, n_links, saved, skipped, failed, total_now = future.result()
                    total_links += n_links
                    total_new += saved
                    total_skipped += skipped
                    total_failed += failed
                except Exception as e:
                    print(f'⚠ {name} 处理异常: {e}')

    elapsed = time.time() - start_time
    print(f'\n{"="*60}')
    print(f'🏁 全量拉取完成')
    print(f'   耗时: {elapsed/60:.1f} 分钟')
    print(f'   总链接: {total_links} 篇')
    print(f'   新增: {total_new} 篇')
    print(f'   跳过: {total_skipped} 篇')
    print(f'   失败: {total_failed} 篇')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()

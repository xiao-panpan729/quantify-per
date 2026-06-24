"""
知识图谱抽取 V3 — 三段式：变量变化 + 传导 + 交易指向
每条带时间，主语在前方便阅读

用法:
  python tools/kg_extract.py --save
  python tools/kg_extract.py --accounts 一思一记 --save
  python tools/kg_extract.py --dry-run
  python tools/kg_extract.py --limit 100 --offset 0 --save
  python tools/kg_extract.py --limit 100 --offset 100 --append --save
"""
import os, sys, json, glob, re, time, urllib.request, argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_env():
    env_path = os.path.join(PROJECT_ROOT, '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line: continue
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

DEEPSEEK_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
API_URL = 'https://api.deepseek.com/chat/completions'
MODEL = 'deepseek-chat'

ACCOUNT_DIRS = [
    '表舅是养基大户', 'laoduo', '海里的小龙龙', '亨特研究笔记',
    '卓哥投研笔记', '灰岩金融科技', '猫笔刀', '滚雪球的猫菲特闲唠嗑',
    '盘前纪要', '盘前', '一思一记', '安静拆主线',
]

KG_DIR = os.path.join(PROJECT_ROOT, 'signals', 'tracking', '_kg')
os.makedirs(KG_DIR, exist_ok=True)

V3_PROMPT = """你从财经文章中提取三类信息，每条必须带时间。

## 三类格式（主语在前）

### 类型1: 变量变化 — 什么变了
格式: 主语 | 涨/跌/增/减/扩/缩 | 变量变化 | 补充信息(幅度/数据) | 时间
例: DRAM价格 | 涨 | 变量变化 | Q1较Q4涨60-70% | 2026-01-08

### 类型2: 传导关系 — 导致什么
格式: 起点 | → | 终点 | 正向传导/负向传导 | 传导路径 | 时间
例: 存储涨价 | → | 消费电子BOM成本 | 正向传导 | 存储占BOM20-30% | 2026-01-08

### 类型3: 交易指向 — 利好/利空谁
格式: 标的 | 利好/利空/关注 | 交易指向 | 原因 | 时间
例: 存储芯片板块 | 利好 | 交易指向 | DRAM涨价增厚利润 | 2026-01-08

## 硬规则
1. 只输出"变了"的信息，不输出静态事实。京东需求电商平台、美团需求外卖这种不输出
2. 变量要具体，不用"市场""经济""大盘"等泛化词
3. 没有明确因-果时不输出类型2
4. 没有明确利好/利空指向时不输出类型3
5. 每条一行，不要序号前缀（不要"类型1:"、"1."等），没有就写"无"
6. 三类可以同时输出"""


def get_article_date(filepath):
    """从文件名提取日期 YYYY-MM-DD"""
    fname = os.path.basename(filepath)
    m = re.match(r'(\d{4})(\d{2})(\d{2})', fname)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    return ''


def extract_content(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
    lines = text.strip().split('\n')
    title = lines[0].replace('标题: ', '').strip() if lines[0].startswith('标题:') else ''
    url = ''
    for l in lines:
        if l.startswith('链接:'):
            url = l.replace('链接: ', '').strip()
            break
    sep = '=' * 50
    content = text.split(sep)[-1].strip() if sep in text else text
    if len(content) < 100:
        return None
    return title, url, content[:2000]


def parse_v3_output(output):
    """解析模型输出为结构化条目"""
    entries = []
    for line in output.split('\n'):
        line = line.strip()
        if not line or '|' not in line:
            continue
        parts = [p.strip() for p in line.split('|')]
        # 检测类型
        entry_type = None
        for t_keyword in ('变量变化', '交易指向'):
            if t_keyword in parts:
                entry_type = t_keyword
                break
        if not entry_type:
            for t_keyword in ('正向传导', '负向传导'):
                if t_keyword in parts:
                    entry_type = '传导'
                    break
        if not entry_type:
            continue

        # 提取时间（最后一个|后的内容尝试匹配日期）
        time_str = parts[-1].strip() if parts else ''
        if not re.match(r'\d{4}-\d{2}-\d{2}', time_str):
            time_str = ''

        entries.append({
            'type': entry_type,
            'display': ' | '.join(parts),
            'time': time_str,
            'subject': parts[0] if parts else '',
        })
    return entries


def call_v3_extract(article_text, article_date, max_retries=3):
    user_msg = f"文章日期: {article_date}\n\n{article_text}"
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": V3_PROMPT},
            {"role": "user", "content": user_msg}
        ],
        "max_tokens": 600,
        "temperature": 0.01
    }).encode()

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                API_URL, data=body,
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
                method="POST"
            )
            resp = urllib.request.urlopen(req, timeout=120)
            result = json.loads(resp.read())
            output = result["choices"][0]["message"]["content"].strip()
            if output == '无':
                return []
            return parse_v3_output(output)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            return [{'error': f'HTTP {e.code}'}]
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            return [{'error': str(e)[:80]}]
    return [{'error': 'max retries'}]


def process_article(filepath):
    extracted = extract_content(filepath)
    if extracted is None:
        return None
    title, url, content = extracted
    article_date = get_article_date(filepath)
    entries = call_v3_extract(content, article_date)
    if not entries or len(entries) == 0 or 'error' not in entries[0]:
        time.sleep(0.3)
    return {
        'file': os.path.relpath(filepath, PROJECT_ROOT),
        'title': title,
        'url': url,
        'date': article_date,
        'entries': entries,
        'entry_count': len(entries),
    }


def scan_articles(account_names=None):
    articles = []
    base = os.path.join(PROJECT_ROOT, 'wechat_articles')
    dirs = [d for d in ACCOUNT_DIRS if os.path.isdir(os.path.join(base, d))]
    for d in dirs:
        if account_names and not any(n in d for n in account_names):
            continue
        dirpath = os.path.join(base, d)
        for fname in sorted(os.listdir(dirpath)):
            if not fname.endswith('.txt'):
                continue
            articles.append(os.path.join(dirpath, fname))
    return articles


def main():
    parser = argparse.ArgumentParser(description='知识图谱抽取 V3 — 变量/传导/交易指向')
    parser.add_argument('--save', action='store_true', help='保存结果')
    parser.add_argument('--dry-run', action='store_true', help='只统计不抽取')
    parser.add_argument('--accounts', nargs='+', help='指定公众号')
    parser.add_argument('--limit', type=int, default=0, help='限制处理篇数')
    parser.add_argument('--offset', type=int, default=0, help='跳过前N篇')
    parser.add_argument('--append', action='store_true', help='追加模式')
    parser.add_argument('--workers', type=int, default=3, help='并行线程')
    args = parser.parse_args()

    articles = scan_articles(args.accounts)
    if args.offset:
        articles = articles[args.offset:]
    if args.limit:
        articles = articles[:args.limit]

    print(f'待处理文章: {len(articles)} 篇')
    if args.dry_run:
        return
    if not DEEPSEEK_KEY:
        print('❌ DEEPSEEK_API_KEY 未设置')
        return

    # API测试
    test = call_v3_extract('六氟化钨价格暴涨，利好国产厂商。', '2026-06-21')
    if test and len(test) > 0 and 'error' in test[0]:
        print(f'❌ API 测试失败: {test[0]}')
        return
    print(f'✅ API 测试通过')

    all_results = []
    success = failed = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_article, fp): fp for fp in articles}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result:
                all_results.append(result)
                success += 1
            else:
                failed += 1
            if (i + 1) % 10 == 0:
                print(f'  进度: {i+1}/{len(articles)} (OK={success} FAIL={failed})')

    elapsed = time.time() - start
    total_entries = sum(r['entry_count'] for r in all_results)

    # 分类型统计
    type_counts = {}
    for r in all_results:
        for e in r['entries']:
            if 'error' not in e:
                t = e.get('type', 'unknown')
                type_counts[t] = type_counts.get(t, 0) + 1

    print(f'\n{"="*50}')
    print(f'抽取完成')
    print(f'  耗时: {elapsed/60:.1f} 分钟')
    print(f'  成功: {success} 篇 / 失败: {failed}')
    print(f'  总条数: {total_entries}')
    for t, c in sorted(type_counts.items()):
        print(f'    {t}: {c} 条')

    if args.save:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        outpath = os.path.join(KG_DIR, f'v3_{timestamp}.json')
        with open(outpath, 'w', encoding='utf-8') as f:
            json.dump({
                'meta': {
                    'version': 'V3',
                    'total_articles': success,
                    'total_entries': total_entries,
                    'type_counts': type_counts,
                    'elapsed_min': round(elapsed/60, 1),
                    'created': timestamp,
                },
                'articles': all_results,
            }, f, ensure_ascii=False, indent=2)
        print(f'  保存: {os.path.relpath(outpath, PROJECT_ROOT)}')

        # 去重精简版（按type+subject+time去重）
        all_entries = {}
        for r in all_results:
            for e in r['entries']:
                if 'error' in e:
                    continue
                dedup_key = f"{e.get('type','')}|{e.get('subject','')}|{e.get('time','')}"
                if dedup_key not in all_entries:
                    all_entries[dedup_key] = {
                        'type': e.get('type', ''),
                        'display': e.get('display', ''),
                        'time': e.get('time', ''),
                        'subject': e.get('subject', ''),
                        'sources': [],
                    }
                all_entries[dedup_key]['sources'].append(r['title'][:30])

        simple_path = os.path.join(KG_DIR, 'v3_deduped.json')
        # 追加模式
        if args.append and os.path.exists(simple_path):
            with open(simple_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            for item in existing.get('entries', []):
                dk = f"{item['type']}|{item['subject']}|{item['time']}"
                if dk not in all_entries:
                    all_entries[dk] = item

        with open(simple_path, 'w', encoding='utf-8') as f:
            json.dump({
                'total': len(all_entries),
                'version': 'V3',
                'type_counts': {t: sum(1 for v in all_entries.values() if v['type'] == t) for t in set(v['type'] for v in all_entries.values())},
                'entries': sorted(all_entries.values(), key=lambda x: (x['time'], x['type'], x['subject'])),
            }, f, ensure_ascii=False, indent=2)
        print(f'  去重后: {len(all_entries)} 条')
        print(f'  保存: {os.path.relpath(simple_path, PROJECT_ROOT)}')


if __name__ == '__main__':
    main()

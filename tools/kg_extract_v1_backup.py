"""
知识图谱三元组抽取 — 批量跑公众号文章 → 输出三元组 JSON

用法:
  # 默认跑全部12个号（不包含中信建投）
  python tools/kg_extract.py --save

  # 只跑猫菲特
  python tools/kg_extract.py --accounts 猫菲特 --save

  # dry-run 预览统计
  python tools/kg_extract.py --dry-run
"""
import os, sys, json, glob, re, time, urllib.request, argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 加载 .env
def load_env():
    env_path = os.path.join(PROJECT_ROOT, '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

DEEPSEEK_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
API_URL = 'https://api.deepseek.com/chat/completions'
MODEL = 'deepseek-chat'

# 12个公众号路径（不含中信建投）
ACCOUNT_DIRS = [
    '表舅是养基大户', 'laoduo', '海里的小龙龙', '亨特研究笔记',
    '卓哥投研笔记', '灰岩金融科技', '猫笔刀', '滚雪球的猫菲特闲唠嗑',
    '盘前纪要', '盘前', '一思一记', '安静拆主线',
]

KG_DIR = os.path.join(PROJECT_ROOT, 'signals', 'tracking', '_kg')
os.makedirs(KG_DIR, exist_ok=True)

# 加载产业图谱用于去重
_COMPANY_NAMES = None
_PRODUCT_NAMES = None

def _load_kg_names():
    global _COMPANY_NAMES, _PRODUCT_NAMES
    if _COMPANY_NAMES is not None:
        return _COMPANY_NAMES, _PRODUCT_NAMES
    kg_path = os.path.join(PROJECT_ROOT, 'signals', 'tracking', '_macro', 'industry_kg.json')
    companies = set()
    products = set()
    if os.path.exists(kg_path):
        with open(kg_path, 'r', encoding='utf-8') as f:
            kg = json.load(f)
        pg = kg.get('product_graph', {})
        for prod_name, prod_info in pg.items():
            name = prod_name.strip()
            products.add(name)
            for c in prod_info.get('companies', []):
                companies.add(c.get('name', ''))
            for rel in prod_info.get('upstream', []):
                products.add(rel.get('product', ''))
    _COMPANY_NAMES, _PRODUCT_NAMES = companies, products
    return companies, products


def extract_content(filepath):
    """读取文章并返回 (title, url, pure_text)"""
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
    # 去重尾部空白、短内容过滤
    if len(content) < 100:
        return None
    return title, url, content[:2000]


def call_deepseek_triple(article_text, max_retries=3):
    """调用 DeepSeek V4 抽取三元组"""
    system_prompt = """你从财经文章中抽取实体关系三元组。要求：

## 输出格式
实体1 | 关系 | 实体2 | 实体2类型

## 实体2类型（限以下6种）
公司 / 产品 / 行业 / 事件 / 政策 / 技术

## 关系（限以下17种，不得发明）
生产 / 供应 / 涨价 / 降价 / 扩产 / 上市 / 采购 / 利好 / 利空
管制 / 制裁 / 替代 / 出口 / 进口 / 需求 / 投资 / 合作

## 实体质量规则（宁缺毋滥）
- 实体1和实体2必须是具体名称，禁止使用：国家名（中国/美国/日本）、作者/公众号名（表舅/猫笔刀/laoduo）、泛化概念（市场/经济/大盘/行情/利率）
- 关系必须从上述列表严格选择，不得创造
- 实体2的值不能是数字、比例、百分比（如"283%"）
- 实体名称必须完整，不得截断
- 没有实质价值的关系不要输出

## 命名规则
- 公司用全称（"韩国SK海力士"，非"海力士"）
- 产品用标准名（"六氟化钨"、"MLCC"）
- 利好/利空指向具体公司或产品，不指向泛化概念"""

    user_prompt = f"""从以下文章中提取实体关系三元组：

{article_text}"""

    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 500,
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

            triples = []
            for line in output.split('\n'):
                line = line.strip()
                if '|' in line:
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 4:
                        triples.append({
                            '实体1': parts[0],
                            '关系': parts[1],
                            '实体2': parts[2],
                            '实体2类型': parts[3],
                        })
            # 后置过滤：去除噪声三元组
            filtered = []
            for t in triples:
                e1, r, e2, t2 = t['实体1'], t['关系'], t['实体2'], t['实体2类型']
                # 自引用
                if e1 == e2:
                    continue
                # 截断/含X的实体名（中文互联网审查）
                if 'X' in e1 or 'X' in e2:
                    continue
                # 模型输出格式描述本身
                if '实体1' in e1 or '实体2' in e2 or '关系' in r:
                    continue
                # 泛化占位符
                if any(w in e1 or w in e2 for w in ['无具体', '未知', '其他']):
                    continue
                # 数字/比例当实体2（纯数字或百分数）
                if re.match(r'^[\d,.%]+$', e2):
                    continue
                # 关系不在预定义列表
                VALID_RELS = {'生产','供应','涨价','降价','扩产','上市','采购','利好','利空','管制','制裁','替代','出口','进口','需求','投资','合作'}
                if r not in VALID_RELS:
                    continue
                # 产业图谱去重：已知公司+产品的静态关系跳过
                if r in ('生产', '供应', '投资'):
                    companies, products = _load_kg_names()
                    if e1 in companies and e2 in products:
                        continue
                filtered.append(t)
            return filtered
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 10 * (attempt + 1)
                time.sleep(wait)
                continue
            err = e.read().decode('utf-8', errors='replace')[:100]
            return [{'error': f'HTTP {e.code}: {err}'}]
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            return [{'error': str(e)[:80]}]
    return [{'error': 'max retries exceeded'}]


def process_article(filepath):
    """处理单篇文章：抽三元组 → 返回结构化结果"""
    extracted = extract_content(filepath)
    if extracted is None:
        return None
    title, url, content = extracted

    triples = call_deepseek_triple(content)
    time.sleep(0.5)  # 礼貌限速

    return {
        'file': os.path.relpath(filepath, PROJECT_ROOT),
        'title': title,
        'url': url,
        'triples': triples,
        'triple_count': len(triples),
    }


def scan_articles(account_names=None):
    """扫描文章文件"""
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
            filepath = os.path.join(dirpath, fname)
            articles.append(filepath)
    return articles


def main():
    parser = argparse.ArgumentParser(description='知识图谱三元组抽取')
    parser.add_argument('--save', action='store_true', help='保存结果')
    parser.add_argument('--dry-run', action='store_true', help='只统计不抽取')
    parser.add_argument('--accounts', nargs='+', help='指定公众号')
    parser.add_argument('--limit', type=int, default=0, help='限制处理篇数')
    parser.add_argument('--offset', type=int, default=0, help='跳过前N篇（分批续跑）')
    parser.add_argument('--append', action='store_true', help='追加模式：保留已有去重结果')
    parser.add_argument('--workers', type=int, default=3, help='并行线程')
    args = parser.parse_args()

    articles = scan_articles(args.accounts)
    if args.offset > 0:
        articles = articles[args.offset:]
    if args.limit > 0:
        articles = articles[:args.limit]

    print(f'📄 待处理文章: {len(articles)} 篇')
    if args.dry_run:
        print(f'🏁 DRY RUN，不抽取')
        return

    if not DEEPSEEK_KEY:
        print('❌ DEEPSEEK_API_KEY 未设置')
        return

    # 测试调一次确认接口通
    test_triples = call_deepseek_triple('六氟化钨价格暴涨，利好国产厂商。')
    if test_triples and 'error' in test_triples[0]:
        print(f'❌ API 测试失败: {test_triples[0]}')
        return
    print(f'✅ API 测试通过')

    all_results = []
    success = 0
    failed = 0

    start = time.time()

    if args.workers > 1 and len(articles) > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_article, fp): fp for fp in articles}
            for fut in as_completed(futures):
                fp = futures[fut]
                try:
                    result = fut.result()
                    if result:
                        all_results.append(result)
                        success += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                if (success + failed) % 20 == 0:
                    print(f'  进度: {success+failed}/{len(articles)} (OK={success} FAIL={failed})')
    else:
        for i, fp in enumerate(articles):
            result = process_article(fp)
            if result:
                all_results.append(result)
                success += 1
            else:
                failed += 1
            if (i + 1) % 10 == 0:
                print(f'  进度: {i+1}/{len(articles)} (OK={success} FAIL={failed})')

    elapsed = time.time() - start

    # 统计
    total_triples = sum(r['triple_count'] for r in all_results)
    print(f'\n{"="*50}')
    print(f'🏁 抽取完成')
    print(f'   耗时: {elapsed/60:.1f} 分钟')
    print(f'   成功: {success} 篇')
    print(f'   失败: {failed} 篇')
    print(f'   三元组总数: {total_triples}')
    print(f'   平均每篇: {total_triples/max(success,1):.1f} 条')

    if args.save:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        outpath = os.path.join(KG_DIR, f'triples_{timestamp}.json')
        with open(outpath, 'w', encoding='utf-8') as f:
            json.dump({
                'meta': {
                    'total_articles': success,
                    'total_triples': total_triples,
                    'elapsed_min': round(elapsed/60, 1),
                    'created': timestamp,
                },
                'articles': all_results,
            }, f, ensure_ascii=False, indent=2)
        print(f'   保存: {os.path.relpath(outpath, PROJECT_ROOT)}')

        # 去重后精简版（支持 --append 追加模式）
        all_pairs = {}

        # 追加模式：先读已有结果
        simple_path = os.path.join(KG_DIR, 'triples_deduped.json')
        if args.append and os.path.exists(simple_path):
            with open(simple_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            for t in existing.get('triples', []):
                key = f"{t['实体1']}|{t['关系']}|{t['实体2']}"
                all_pairs[key] = t

        for r in all_results:
            for t in r['triples']:
                if 'error' in t:
                    continue
                key = f"{t['实体1']}|{t['关系']}|{t['实体2']}"
                if key not in all_pairs:
                    all_pairs[key] = {'实体1': t['实体1'], '关系': t['关系'], '实体2': t['实体2'], '实体2类型': t.get('实体2类型',''), '来源': []}
                all_pairs[key]['来源'].append(r['title'][:30])

        with open(simple_path, 'w', encoding='utf-8') as f:
            json.dump({
                'total': len(all_pairs),
                'triples': sorted(all_pairs.values(), key=lambda x: x['实体1']),
            }, f, ensure_ascii=False, indent=2)
        print(f'   去重后: {len(all_pairs)} 条唯一三元组')
        print(f'   保存: {os.path.relpath(simple_path, PROJECT_ROOT)}')


if __name__ == '__main__':
    main()

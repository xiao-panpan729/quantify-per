"""
KG V4 全量删减判定脚本 V3
基于用户确认的规则，结构性+语义模式匹配

删除类别:
  B: 轱辘话 — 主语≈补充区，同义反复
  C: 无量化幅度 — 变量变化无具体数字
  D: 定性冒充变量 — 无量化定性描述
  E: 笼统传导终点 — 终点不可交易/不可测量
  F: 过时单事件 — 2021-2022 一次性事件，无复用模式

修复类别:
  FIX: 交易指向轱辘话，可修复（板块名+原因合并到主语，修复后拆分）
"""

import json, sys, re
sys.stdout.reconfigure(encoding='utf-8')

with open('signals/tracking/_kg/v4_formatted.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
entries = data['entries']

# ─── 辅助函数 ───

CN_YEARS = r'[一二三四五六七八九十百千万亿\d]+'

def strip_time(text):
    return re.sub(r'^\d{4}[-/]\d{2}[-/]\d{2}\s*', '', text).strip()

def get_year(entry):
    t = entry.get('time', '')
    if t and len(t) >= 4:
        return t[:4]
    return ''

def has_digits(text):
    return bool(re.search(r'\d', text))

def has_chinese_quantifier(text):
    """检测中文量化词"""
    return bool(re.search(r'[近约超达]?[一二三四五六七八九十百千万亿两几数多]成?[以之]?[上下内外]?', text)) or \
           bool(re.search(r'翻[番倍]|过半|[八九]成|[双三]倍', text))

def suffix_overlap(subj, target):
    """检查 target 是否出现在 subj 末尾（交易指向轱辘话的典型模式）"""
    if not target or not subj:
        return False
    # 去标点比较
    clean_target = re.sub(r'[，。、；：\s|]', '', target)
    clean_subj = re.sub(r'[，。、；：\s|]', '', subj)
    if len(clean_target) < 3:
        return False
    return clean_subj.endswith(clean_target)

def similarity(a, b):
    if not a or not b:
        return 0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if short in long:
        return len(short) / len(long)
    return 0

def content_overlap(a, b):
    if not a or not b:
        return False
    a_c = re.sub(r'[，。、；：\s]', '', a)
    b_c = re.sub(r'[，。、；：\s]', '', b)
    # 直接相等
    if a_c == b_c:
        return True
    # 后缀重叠（交易指向典型模式）
    if suffix_overlap(a_c, b_c) or suffix_overlap(b_c, a_c):
        return True
    # 简单相似度
    return similarity(a_c, b_c) >= 0.55

def is_qualitative(text):
    """判断文本是否纯定性无量化"""
    if not text:
        return True
    if has_digits(text) or has_chinese_quantifier(text):
        return False
    # 极短文本
    if len(text) < 4:
        return True
    # 纯定性词
    qual_words = ['改善', '修复', '回暖', '加速', '放缓', '企稳',
                 '走强', '走弱', '高景气', '低景气', '低位', '高位',
                 '提升', '萎缩', '扩张', '收缩', '反弹', '回落',
                 '紧张', '宽松', '乐观', '悲观', '关注', '重视',
                 '突破', '回踩', '震荡', '分化', '新高', '新低',
                 '低迷', '旺盛', '疲软', '坚挺', '转暖', '降温',
                 '上扬', '下挫', '攀升', '大跌', '暴涨']
    return any(w in text for w in qual_words)


# ─── 变量变化判定 ───

def classify_variable_change(parts, entry):
    if len(parts) < 4:
        return 'DELETE', [], ['格式不完整']

    raw_subj = strip_time(parts[0])
    detail = parts[3]
    verb = parts[1].strip()
    reasons = []

    # B: 轱辘话
    if content_overlap(raw_subj, detail):
        reasons.append('B轱辘话')
        return 'DELETE', [], reasons

    # C: 无量化幅度
    has_num = has_digits(detail) or has_digits(raw_subj)
    has_cn = has_chinese_quantifier(detail) or has_chinese_quantifier(raw_subj)
    if not has_num and not has_cn:
        if detail in ('无', '无具体幅度', '', '有', '待确认', '—'):
            reasons.append('C无量化幅度')
        elif len(detail) <= 3:
            reasons.append('C无量化幅度')
        elif verb and detail.strip() == verb:
            reasons.append('C无量化幅度')
        else:
            # D: 定性描述
            reasons.append('D定性描述')

    return ('DELETE' if reasons else 'KEEP', [], reasons)


# ─── 交易指向判定 ───

# 常见的板块/行业后缀，用于从主语中剥离出目标
SECTOR_SUFFIXES = ['板块', '概念', '行业', 'ETF', '个股', '产业链']

def extract_ticker_from_subj(subj, target):
    """尝试从主语中提取 ticker/板块名（用于修复轱辘话）"""
    # 去掉 target 部分，剩下的就是 ticker
    clean_subj = re.sub(r'[，。、；：\s]', '', subj)
    clean_target = re.sub(r'[，。、；：\s]', '', target)
    if clean_subj.endswith(clean_target):
        ticker = clean_subj[:-len(clean_target)] if clean_subj != clean_target else ''
        return ticker
    return ''

def classify_trade_direction(parts, entry):
    if len(parts) < 4:
        return 'DELETE', [], ['格式不完整']

    raw_subj = strip_time(parts[0])
    target = parts[3]
    direction = parts[1].strip()
    reasons = []
    fix_info = {}

    # B: 轱辘话 — 原因≈标的
    if content_overlap(raw_subj, target):
        # 检查是否可修复（板块名+原因合并的情况）
        ticker = extract_ticker_from_subj(raw_subj, target)
        if ticker and len(ticker) >= 2:
            # 可修复：ticker + reason 合并在主语里
            fix_info = {'ticker': ticker, 'reason': target}
            return 'FIX', fix_info, ['B轱辘话(可修复)']
        else:
            reasons.append('B轱辘话')

    # D: 模糊方向词
    if not reasons and direction in ('关注',):
        if not has_digits(raw_subj):
            reasons.append('D模糊方向词')

    # D: 定性描述（无数字的原因）
    if not reasons and not has_digits(raw_subj) and not has_chinese_quantifier(raw_subj):
        if is_qualitative(raw_subj):
            reasons.append('D定性描述')

    return ('DELETE' if reasons else 'KEEP', fix_info, reasons)


# ─── 传导判定 ───

VAGUE_TARGETS_EXACT = [
    '公司业绩', '交易难度', '市场情绪', '市场走势',
    '板块走势', '市场表现', '业绩表现', '资本市场情绪',
    '投资者应对', '投资者情绪', '市场风险偏好',
    '市场缺乏上攻动力', '市场缺乏', '投资者信心',
]

def classify_conduction(parts, entry):
    if len(parts) < 5:
        return 'DELETE', [], ['格式不完整']

    raw_subj = strip_time(parts[0])
    target = parts[2]
    detail = parts[4] if len(parts) > 4 else ''
    reasons = []

    # E: 笼统传导终点 — 精确匹配
    if target in VAGUE_TARGETS_EXACT or any(target.endswith(vt) for vt in VAGUE_TARGETS_EXACT):
        reasons.append('E笼统终点')

    # 超短目标 (≤2字) — 不可交易
    if not reasons and len(target.strip()) <= 2:
        reasons.append('E目标过短')

    # B: 轱辘话
    if not reasons:
        # 检查 source 和 detail 是否重复
        if detail and content_overlap(raw_subj, detail):
            reasons.append('B轱辘话')

    return ('DELETE' if reasons else 'KEEP', [], reasons)


# ─── 全量处理 ───

results = {'DELETE': [], 'FIX': [], 'KEEP': []}

for idx, entry in enumerate(entries):
    etype = entry['type']
    parts = [p.strip() for p in entry['display'].split('|')]

    if etype == '变量变化':
        verdict, fix_info, reasons = classify_variable_change(parts, entry)
    elif etype == '交易指向':
        verdict, fix_info, reasons = classify_trade_direction(parts, entry)
    elif etype == '传导':
        verdict, fix_info, reasons = classify_conduction(parts, entry)
    else:
        verdict, reasons = 'DELETE', ['未知类型']

    r = {
        'idx': idx,
        'type': etype,
        'display': entry['display'],
        'time': entry.get('time', ''),
        'reasons': reasons,
    }
    if fix_info:
        r['fix'] = fix_info
    results[verdict].append(r)

# 2021-2022 且只有结构性标记 → 查一下内容质量
for verdict in ['DELETE', 'FIX']:
    filtered = []
    for r in results[verdict]:
        year = r['time'][:4] if len(r['time']) >= 4 else ''
        has_structural = any(code in str(r['reasons']) for code in ['B', 'C', 'D', 'E'])
        if year in ('', '2021', '2022') and not has_structural:
            # 只有过时标记 → 人工判断
            r['verdict_note'] = '仅因老旧，需人工判断'
            results['KEEP'].append(r)
        else:
            filtered.append(r)
    results[verdict] = filtered


# ─── 输出 ───

print(f"{'='*70}")
print(f"KG V4 全量处理报告 V3")
print(f"{'='*70}")
print(f"\n总条目: {len(entries)}")
print(f"  删除: {len(results['DELETE'])} ({len(results['DELETE'])/len(entries)*100:.1f}%)")
print(f"  修复: {len(results['FIX'])} ({len(results['FIX'])/len(entries)*100:.1f}%)")
print(f"  保留: {len(results['KEEP'])} ({len(results['KEEP'])/len(entries)*100:.1f}%)")

# 按类型统计
from collections import Counter
type_del = Counter(r['type'] for r in results['DELETE'])
type_fix = Counter(r['type'] for r in results['FIX'])
print("\n删除按类型:")
for t, c in type_del.most_common():
    total = sum(1 for e in entries if e['type'] == t)
    print(f"  {t}: {c} (/{total})")
print("修复按类型:")
for t, c in type_fix.most_common():
    total = sum(1 for e in entries if e['type'] == t)
    print(f"  {t}: {c} (/{total})")

# 原因统计
reason_codes = Counter()
for r in results['DELETE']:
    for reason in r['reasons']:
        reason_codes[reason[0]] += 1
print("\n删除原因:")
for code in ['B', 'C', 'D', 'E']:
    names = {'B': '轱辘话', 'C': '无量化', 'D': '定性', 'E': '笼统终点'}
    print(f"  {names[code]}: {reason_codes[code]}")

# 抽样展示
for category, label in [('DELETE', '删除'), ('FIX', '修复')]:
    if not results[category]:
        continue
    print(f"\n\n{'─'*60}")
    print(f"【{label}】— {len(results[category])} 条（抽样前20）")
    print(f"{'─'*60}")
    for r in results[category][:20]:
        codes = '/'.join(set(a[0] for a in r.get('reasons', [])))
        short = r['display'][:150]
        extra = ''
        if 'fix' in r:
            extra = f" → ticker={r['fix'].get('ticker','?')}"
        print(f"\n  [{codes}][{r['idx']}] {short}{extra}")
        print(f"       {'; '.join(r.get('reasons',[]))}")

# 保存 JSON
output = {
    'total': len(entries),
    'stats': {'delete': len(results['DELETE']), 'fix': len(results['FIX']), 'keep': len(results['KEEP'])},
    'deleted': results['DELETE'],
    'fixable': results['FIX'],
}
with open('signals/tracking/_kg/deletion_candidates.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n\n保存: signals/tracking/_kg/deletion_candidates.json")

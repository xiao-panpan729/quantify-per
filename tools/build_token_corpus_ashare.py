#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量构建A股K线词元语料库

数据源:
  - 3672只A股日线 (sh6xx + sz000 + sz300)
  - 合并已有板块指数语料库 (655板块 + 2指数30min)

输出:
  training_data/ashare_token_corpus.csv  — A股部分
  training_data/total_token_corpus.csv   — 合并总库
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'D:/miniconda3/Lib/site-packages')

import os, time, glob, struct
import numpy as np
from collections import Counter

# ── TDX .day 文件读取（不依赖pytdx，直接struct读）──
# 格式: 每条32字节
#   date(int32) open(int32) high(int32) low(int32) close(int32)
#   amount(float32) volume(int32) reserved(int32)
#   价格需除以100

def read_tdx_day(fpath):
    """读取通达信日线文件，返回numpy结构化数组"""
    with open(fpath, 'rb') as f:
        data = f.read()
    n = len(data) // 32
    if n == 0:
        return None
    fmt = '<IIIIIfII'  # little-endian
    records = []
    for i in range(n):
        off = i * 32
        date, o, h, l, c, amount, vol, _ = struct.unpack_from(fmt, data, off)
        records.append((date, o/100.0, h/100.0, l/100.0, c/100.0, amount, vol))
    arr = np.array(records, dtype=[
        ('date','i4'),('open','f4'),('high','f4'),('low','f4'),
        ('close','f4'),('amount','f4'),('volume','i4')
    ])
    return arr

def date_int_to_str(d):
    """YYYYMMDD int -> 'YYYY-MM-DD'"""
    y = d // 10000
    m = (d % 10000) // 100
    day = d % 100
    return f'{y:04d}-{m:02d}-{day:02d}'

# ── Token 分类器 (向量化) ──

def classify_token_vec(highs, lows, closes):
    """
    输入: 3根K线的 highs[3], lows[3], closes[3]
    输出: token名称, token_7名称
    """
    h1, h2, h3 = highs
    l1, l2, l3 = lows
    c1, c2, c3 = closes

    is_top = h2 > h1 and h2 > h3
    is_bottom = l2 < l1 and l2 < l3
    cb2_in_b1 = h2 <= h1 and l2 >= l1
    cb1_in_b2 = h1 <= h2 and l1 >= l2
    is_up = c1 < c2 < c3
    is_down = c1 > c2 > c3

    if is_top and not cb2_in_b1 and not cb1_in_b2:
        return '顶分型', '顶分型'
    if is_bottom and not cb2_in_b1 and not cb1_in_b2:
        return '底分型', '底分型'
    if cb2_in_b1 or cb1_in_b2:
        return '包含关系', '包含关系'
    if is_up:
        return '简单上涨', '方向'
    if is_down:
        return '简单下跌', '方向'
    if c1 < c2 > c3:
        if c3 > c1: return '涨跌涨', '转折'
        elif c3 < c1: return '涨跌涨_假突破', '转折'
        else: return '涨跌涨_平', '转折'
    if c1 > c2 < c3:
        if c3 < c1: return '跌涨跌_诱多', '转折'
        elif c3 > c1: return '跌涨跌_反转', '转折'
        else: return '跌涨跌_平', '转折'
    return '其他', '其他'

def tokenize_arr(arr, code):
    """将K线数组转换为Token列表"""
    n = len(arr)
    if n < 3:
        return []
    highs = arr['high']
    lows = arr['low']
    closes = arr['close']
    dates = arr['date']

    rows = []
    for i in range(n - 2):
        h = [highs[i], highs[i+1], highs[i+2]]
        l = [lows[i], lows[i+1], lows[i+2]]
        c = [closes[i], closes[i+1], closes[i+2]]
        token, token7 = classify_token_vec(h, l, c)
        d = int(dates[i+1])  # 用中间那根K线的日期
        rows.append((code, date_int_to_str(d), token, token7, float(c[1])))
    return rows

# ── 进度 ──
def fmt_time(sec):
    if sec < 60: return f'{sec:.0f}s'
    m = int(sec // 60)
    s = int(sec % 60)
    return f'{m}m{s:02d}s'

# ══════════════════════════════════════
#  批量处理A股日线
# ══════════════════════════════════════
print('=' * 60)
print('A股日线 Token 批量标注')
print('=' * 60)

# 收集所有A股日线文件
sh_files = sorted(glob.glob('C:/zd_cjzq/vipdoc/sh/lday/sh6*.day'))
sz0_files = sorted(glob.glob('C:/zd_cjzq/vipdoc/sz/lday/sz000*.day'))
sz3_files = sorted(glob.glob('C:/zd_cjzq/vipdoc/sz/lday/sz300*.day'))
all_files = sh_files + sz0_files + sz3_files

print(f'文件总数: {len(all_files)} (SH={len(sh_files)} SZ000={len(sz0_files)} SZ300={len(sz3_files)})')

all_rows = []
ok, fail = 0, 0
t0 = time.time()

for idx, fpath in enumerate(all_files):
    code = os.path.basename(fpath).replace('.day', '')
    try:
        arr = read_tdx_day(fpath)
        if arr is None or len(arr) < 3:
            fail += 1
            continue
        rows = tokenize_arr(arr, code)
        all_rows.extend(rows)
        ok += 1
    except Exception as e:
        fail += 1

    if (idx + 1) % 500 == 0:
        elapsed = time.time() - t0
        rate = (idx + 1) / elapsed
        eta = (len(all_files) - idx - 1) / rate
        print(f'  进度: {idx+1}/{len(all_files)} 成功={ok} 失败={fail} '
              f'Token={len(all_rows):,} 耗时={fmt_time(elapsed)} ETA={fmt_time(eta)}')

elapsed = time.time() - t0
print(f'\nA股日线完成: 成功={ok} 失败={fail} Token={len(all_rows):,} 耗时={fmt_time(elapsed)}')

# ── 保存A股语料 ──
import pandas as pd

out_dir = 'D:/quantify-per/training_data'
os.makedirs(out_dir, exist_ok=True)

ashare_path = f'{out_dir}/ashare_token_corpus.csv'
df_ashare = pd.DataFrame(all_rows, columns=['code','date','token','token_7','close_b2'])
df_ashare['period'] = 'daily'
df_ashare.to_csv(ashare_path, index=False, encoding='utf-8')
print(f'\nA股语料保存: {ashare_path} ({len(df_ashare):,}行)')

# ── 合并已有板块指数语料 ──
existing_path = f'{out_dir}/index_token_corpus_full.csv'
if os.path.exists(existing_path):
    df_existing = pd.read_csv(existing_path)
    # 统一列名
    df_existing = df_existing.rename(columns={'c2': 'close_b2'})
    # 确保列一致
    common_cols = ['code','date','token','token_7','close_b2','period']
    df_existing = df_existing[[c for c in common_cols if c in df_existing.columns]]
    df_ashare = df_ashare[common_cols]
    df_total = pd.concat([df_existing, df_ashare], ignore_index=True)
else:
    df_total = df_ashare

total_path = f'{out_dir}/total_token_corpus.csv'
df_total.to_csv(total_path, index=False, encoding='utf-8')
print(f'总语料库保存: {total_path} ({len(df_total):,}行, {df_total["code"].nunique()}个标的)')

# ══════════════════════════════════════
#  统计分析
# ══════════════════════════════════════
print('\n' + '=' * 60)
print('统计分析')
print('=' * 60)

print(f'\n总Token数: {len(df_total):,}')
print(f'标的数: {df_total["code"].nunique()}')
print(f'周期: {", ".join(df_total["period"].unique())}')
print(f'日期范围: {df_total["date"].min()} ~ {df_total["date"].max()}')

print('\n── Token7 分布 ──')
dist = df_total['token_7'].value_counts()
for k, v in dist.items():
    print(f'  {k:8s}: {v:>10,} ({v/len(df_total)*100:5.1f}%)')

print('\n── 细分Token分布 Top 15 ──')
dist_fine = df_total['token'].value_counts()
for k, v in dist_fine.head(15).items():
    print(f'  {k:16s}: {v:>10,} ({v/len(df_total)*100:5.1f}%)')

# ── 按A股vs板块指数分组 ──
print('\n── A股 vs 板块指数 Token7分布对比 ──')
ashare_codes = set(df_ashare['code'].unique())
df_total['source'] = df_total['code'].apply(lambda x: 'A股' if x in ashare_codes else '板块指数')
for src in ['A股', '板块指数']:
    sub = df_total[df_total['source'] == src]
    d = sub['token_7'].value_counts()
    total = len(sub)
    parts = ' | '.join(f'{k}={v/total*100:.1f}%' for k, v in d.items())
    print(f'  {src} ({total:,} tokens): {parts}')

# ── 高频3连Token词组 ──
print('\n── 高频3连Token词组 Top 20 (全量) ──')
# 按code分组，避免跨标的拼接
triples = Counter()
for code, grp in df_total.groupby('code'):
    seq = grp.sort_values('date')['token_7'].tolist()
    for i in range(len(seq) - 2):
        triples[' → '.join(seq[i:i+3])] += 1

total_tri = sum(triples.values())
for gram, cnt in triples.most_common(20):
    print(f'  {gram:40s} {cnt:>8,}次 ({cnt/total_tri*100:.2f}%)')

# ── Token转移矩阵 ──
print('\n── Token7 转移矩阵 (行=当前, 列=下一个) ──')
tokens7 = ['方向','包含关系','转折','顶分型','底分型','其他']
trans = {t: Counter() for t in tokens7}
for code, grp in df_total.groupby('code'):
    seq = grp.sort_values('date')['token_7'].tolist()
    for i in range(len(seq) - 1):
        if seq[i] in trans and seq[i+1] in trans:
            trans[seq[i]][seq[i+1]] += 1

header = f'{"从\\到":10s} ' + ' '.join(f'{t:>8s}' for t in tokens7)
print(header)
for t in tokens7:
    row_total = sum(trans[t].values())
    if row_total == 0:
        continue
    parts = ' '.join(f'{trans[t][tt]/row_total*100:>7.1f}%' for tt in tokens7)
    print(f'{t:10s} {parts}')

print(f'\n完成! 总耗时={fmt_time(time.time()-t0)}')

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量构建K线词元语料库

数据源:
  - 655个板块指数日线 (sh880xxx)
  - 6个主要指数日线 (已跑完，追加合并)
  - 上证+创业板 30分钟
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os, time, glob
from collections import defaultdict, Counter
import pandas as pd
from pytdx.reader import TdxDailyBarReader, TdxMinBarReader

# ── 分类器 ──

def classify_token(b1, b2, b3):
    h1,l1=b1['high'],b1['low']; h2,l2=b2['high'],b2['low']; h3,l3=b3['high'],b3['low']
    c1,c2,c3=b1['close'],b2['close'],b3['close']
    is_top=h2>h1 and h2>h3; is_bottom=l2<l1 and l2<l3
    cb2_in_b1=h2<=h1 and l2>=l1; cb1_in_b2=h1<=h2 and l1>=l2
    is_up=c1<c2<c3; is_down=c1>c2>c3
    if is_top and not cb2_in_b1 and not cb1_in_b2: return '顶分型'
    if is_bottom and not cb2_in_b1 and not cb1_in_b2: return '底分型'
    if cb2_in_b1 or cb1_in_b2: return '包含关系'
    if is_up: return '简单上涨'
    if is_down: return '简单下跌'
    if c1<c2>c3: return '涨跌涨' if c3>c1 else '涨跌涨_假突破' if c3<c1 else '涨跌涨_平'
    if c1>c2<c3: return '跌涨跌_诱多' if c3<c1 else '跌涨跌_反转' if c3>c1 else '跌涨跌_平'
    return '其他'

TOKEN_7_MAP = {
    '顶分型':'顶分型','底分型':'底分型','包含关系':'包含关系',
    '简单上涨':'方向','简单下跌':'方向',
    '涨跌涨':'转折','涨跌涨_假突破':'转折','涨跌涨_平':'转折',
    '跌涨跌_诱多':'转折','跌涨跌_反转':'转折','跌涨跌_平':'转折',
    '其他':'其他'
}

def tokenize_df(df, code, name, period):
    """将K线DF转换为Token序列"""
    rows = []
    n = len(df)
    for i in range(n - 2):
        b1, b2, b3 = df.iloc[i], df.iloc[i+1], df.iloc[i+2]
        t = classify_token(b1, b2, b3)
        d = b2.name
        if hasattr(d, 'strftime'):
            d = d.strftime('%Y-%m-%d')
        rows.append({
            'code': code,
            'name': name,
            'period': period,
            'date': d,
            'token': t,
            'token_7': TOKEN_7_MAP[t],
            'c1': b1['close'],
            'c2': b2['close'],
            'c3': b3['close'],
        })
    return rows

# ── 进度显示 ──
def fmt_time(sec):
    if sec < 60: return f'{sec:.0f}s'
    return f'{sec//60}m{sec%60:02d}s'

# ══════════════════════════════════════
#  第一部分: 655个板块指数日线
# ══════════════════════════════════════
print('=' * 60)
print('第一部分: 板块指数日线 (655个)')
print('=' * 60)

reader = TdxDailyBarReader()
files = sorted(glob.glob('C:/zd_cjzq/vipdoc/sh/lday/sh880*.day'))

all_rows = []
t0 = time.time()
ok, fail = 0, 0

for idx, fpath in enumerate(files):
    code = os.path.basename(fpath).replace('.day', '')
    try:
        df = reader.get_df(fpath)
        rows = tokenize_df(df, code=code, name=code, period='daily')
        all_rows.extend(rows)
        ok += 1
    except Exception as e:
        fail += 1

    if (idx + 1) % 100 == 0:
        elapsed = time.time() - t0
        print(f'  进度: {idx+1}/{len(files)} 成功={ok} 失败={fail} 耗时={fmt_time(elapsed)}')

elapsed = time.time() - t0
print(f'板块指数完成: 成功={ok} 失败={fail} Token={len(all_rows)} 耗时={fmt_time(elapsed)}')

# ══════════════════════════════════════
#  第二部分: 主要指数30分钟
# ══════════════════════════════════════
print('\n' + '=' * 60)
print('第二部分: 主要指数30分钟')
print('=' * 60)

t1 = time.time()
for code, name in [('sh000001', '上证指数'), ('sz399006', '创业板指')]:
    fp = f'signals/tracking/{code}/min30_signals.csv'
    if not os.path.exists(fp):
        print(f'  跳过 {name} (无30分钟数据)')
        continue
    df = pd.read_csv(fp)
    cols = ['open','high','low','close']
    df = df[cols]
    # 日期列处理
    if 'date' in df.columns:
        df.index = pd.to_datetime(df['date'])
    rows = tokenize_df(df, code=code, name=name, period='min30')
    all_rows.extend(rows)
    print(f'  {name} 30分钟: {len(rows)} tokens')

print(f'30分钟完成: 耗时={fmt_time(time.time()-t1)}')

# ══════════════════════════════════════
#  合并保存
# ══════════════════════════════════════
print('\n' + '=' * 60)
print('保存语料库')
print('=' * 60)

corpus = pd.DataFrame(all_rows)
out_path = 'training_data/index_token_corpus_full.csv'
corpus.to_csv(out_path, index=False, encoding='utf-8')

print(f'保存路径: {out_path}')
print(f'总Token数: {len(corpus):,}')
print(f'来源: {corpus["code"].nunique()}个标的 × {corpus["period"].nunique()}个周期')
print(f'时间范围: {corpus["date"].min()} ~ {corpus["date"].max()}')

# ── 快速统计 ──
print('\nToken7分布:')
dist = corpus['token_7'].value_counts()
for k,v in dist.items():
    print(f'  {k}: {v:,} ({v/len(corpus)*100:.1f}%)')

print('\n3连Token词组Top 20:')
seq = corpus['token_7'].tolist()
triples = Counter()
for i in range(len(seq)-2):
    triples[' → '.join(seq[i:i+3])] += 1
for gram, cnt in triples.most_common(20):
    print(f'  {gram:40} {cnt}次 ({cnt/(len(seq)-2)*100:.2f}%)')

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建指数K线词元语料库

6只指数 × 日线 → Token序列 → 落库CSV
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os, math
from collections import defaultdict, Counter
import pandas as pd
from pytdx.reader import TdxDailyBarReader

# ── 分类器 ──

def classify_token(b1, b2, b3):
    h1,l1=b1['high'],b1['low']; h2,l2=b2['high'],b2['low']; h3,l3=b3['high'],b3['low']
    c1,c2,c3=b1['close'],b2['close'],b3['close']
    is_top=h2>h1 and h2>h3
    is_bottom=l2<l1 and l2<l3
    cb2_in_b1=h2<=h1 and l2>=l1
    cb1_in_b2=h1<=h2 and l1>=l2
    is_up=c1<c2<c3
    is_down=c1>c2>c3
    if is_top and not cb2_in_b1 and not cb1_in_b2: return '顶分型'
    if is_bottom and not cb2_in_b1 and not cb1_in_b2: return '底分型'
    if cb2_in_b1 or cb1_in_b2: return '包含关系'
    if is_up: return '简单上涨'
    if is_down: return '简单下跌'
    if c1<c2>c3: return '涨跌涨' if c3>c1 else '涨跌涨_假突破' if c3<c1 else '涨跌涨_平'
    if c1>c2<c3: return '跌涨跌_诱多' if c3<c1 else '跌涨跌_反转' if c3>c1 else '跌涨跌_平'
    return '其他'

# ── 数据 ──

indices = [
    ('sh','000001','上证指数'),
    ('sz','399001','深证成指'),
    ('sz','399006','创业板指'),
    ('sh','000300','沪深300'),
    ('sh','000905','中证500'),
    ('sh','000688','科创50'),
]

reader = TdxDailyBarReader()
all_rows = []

for mkt, code, name in indices:
    p = f'C:/zd_cjzq/vipdoc/{mkt}/lday/{mkt}{code}.day'
    try:
        df = reader.get_df(p)
    except:
        print(f'  {name}: 跳过')
        continue

    n = len(df)
    print(f'  {name}: {n}条日线')

    for i in range(n - 2):
        b1, b2, b3 = df.iloc[i], df.iloc[i+1], df.iloc[i+2]
        token = classify_token(b1, b2, b3)
        # 用b2的日期作为这个token的日期（中间那根）
        d = b2.name
        if hasattr(d, 'strftime'):
            d = d.strftime('%Y-%m-%d')
        all_rows.append({
            'index': name,
            'code': f'{mkt}{code}',
            'date': d,
            'token': token,
            'token_7': {
                '顶分型':'顶分型','底分型':'底分型','包含关系':'包含关系',
                '简单上涨':'方向','简单下跌':'方向',
                '涨跌涨':'转折','涨跌涨_假突破':'转折','涨跌涨_平':'转折',
                '跌涨跌_诱多':'转折','跌涨跌_反转':'转折','跌涨跌_平':'转折',
                '其他':'其他'
            }[token],
            'close_b1': b1['close'],
            'close_b2': b2['close'],
            'close_b3': b3['close'],
        })

corpus = pd.DataFrame(all_rows)
out = 'training_data/index_token_corpus.csv'
corpus.to_csv(out, index=False, encoding='utf-8')
print(f'\n语料库已保存: {out}')
print(f'总词元数: {len(corpus)}')
print(f'指数数: {corpus["index"].nunique()}')
print()

# ── 统计 ──
print('各指数Token分布:')
for idx in corpus['index'].unique():
    sub = corpus[corpus['index']==idx]
    tt = sub['token_7'].value_counts()
    total = len(sub)
    parts = ' | '.join(f'{k}={v/total*100:.0f}%' for k,v in tt.items())
    print(f'  {idx}: {parts}')

# ── 挖高频词组 ──
print('\n高频词组(3连Token):')
for idx in corpus['index'].unique():
    sub = corpus[corpus['index']==idx]
    seq = sub['token_7'].tolist()
    triples = Counter()
    for i in range(len(seq)-2):
        triples[' → '.join(seq[i:i+3])] += 1
    total_tri = len(seq)-2
    print(f'\n  {idx} Top 10:')
    for gram, cnt in triples.most_common(10):
        print(f'    {gram:40} {cnt}次 ({cnt/total_tri*100:.1f}%)')

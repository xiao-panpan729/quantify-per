#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token序列 → 笔(词组) 分词器 + 走势叙述

层级: Token(3K线分型) → 词组(笔) → 句子(线段)
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
from collections import defaultdict

# ── 1. 读取单只指数的Token序列 ──

def load_token_sequence(code='sh880373', period='daily'):
    """从板块指数日线直接算Token序列"""
    from pytdx.reader import TdxDailyBarReader
    reader = TdxDailyBarReader()
    p = f'C:/zd_cjzq/vipdoc/sh/lday/{code}.day'
    df = reader.get_df(p)

    # 分类器
    def ct(b1,b2,b3):
        h1,l1=b1['high'],b1['low'];h2,l2=b2['high'],b2['low'];h3,l3=b3['high'],b3['low']
        c1,c2,c3=b1['close'],b2['close'],b3['close']
        t1=h2>h1 and h2>h3; btm=l2<l1 and l2<l3
        cb=h2<=h1 and l2>=l1 or h1<=h2 and l1>=l2
        if t1 and not cb: return '顶分型'
        if btm and not cb: return '底分型'
        if cb: return '包含关系'
        if c1<c2<c3: return '简单上涨'
        if c1>c2>c3: return '简单下跌'
        if c1<c2>c3: return '涨跌涨' if c3>c1 else '涨跌涨_假突破'
        if c1>c2<c3: return '跌涨跌_诱多' if c3<c1 else '跌涨跌_反转'
        return '其他'

    tokens = []
    for i in range(len(df)-2):
        t = ct(df.iloc[i], df.iloc[i+1], df.iloc[i+2])
        d = df.index[i+1]
        if hasattr(d,'strftime'): d = d.strftime('%Y-%m-%d')
        tokens.append({
            'date': d,
            'token': t,
            'token_7': {'顶分型':'顶分型','底分型':'底分型','包含关系':'包含关系',
                        '简单上涨':'方向','简单下跌':'方向',
                        '涨跌涨':'转折','涨跌涨_假突破':'转折',
                        '跌涨跌_诱多':'转折','跌涨跌_反转':'转折','其他':'其他'}[t],
            'close': df.iloc[i+1]['close'],
        })
    return tokens, df

# ── 2. 分词器：Token序列 → 笔 ──

def segment_bi(tokens):
    """
    将Token序列切分为"笔"(词组)

    规则:
    - 笔由 底分型→...→顶分型 (上涨笔) 或 顶分型→...→底分型 (下跌笔)
    - 最少跨越3个Token (即第1个分型与第2个分型之间至少隔1个)
    - 遇到同向分型优先：两个顶分型取更高的，两个底分型取更低的
    """
    # 首先标记每个token的分型类型
    records = []
    for t in tokens:
        if t['token_7'] == '顶分型':
            records.append(('top', t['date'], t['close'], t))
        elif t['token_7'] == '底分型':
            records.append(('bot', t['date'], t['close'], t))
        else:
            records.append(('mid', t['date'], t['close'], t))

    # 分词：找分型→分型对
    bis = []
    i = 0
    while i < len(records):
        if records[i][0] == 'bot':
            # 向上笔：找下一个顶分型
            bi_start = i
            best_top = None
            best_top_idx = None
            for j in range(i+1, min(i+30, len(records))):
                if records[j][0] == 'top':
                    if best_top is None or records[j][2] > best_top[2]:
                        best_top = records[j]
                        best_top_idx = j
                elif records[j][0] == 'bot' and j > i+1:
                    # 遇到新的底分型，当前笔结束在此前的best_top
                    if best_top is not None and best_top_idx - bi_start >= 3:
                        bis.append({
                            'direction': '上涨',
                            'start_date': records[bi_start][1],
                            'end_date': best_top[1],
                            'start_price': records[bi_start][2],
                            'end_price': best_top[2],
                            'change_pct': (best_top[2]/records[bi_start][2]-1)*100,
                            'tokens': [r[3] for r in records[bi_start:best_top_idx+1]],
                            'token_count': best_top_idx - bi_start + 1,
                            'strength': '强' if (best_top[2]/records[bi_start][2]-1)*100 > 5 else '弱',
                        })
                        i = best_top_idx  # 从顶分型继续
                        break
                    else:
                        # 没有找到有效的顶分型，放弃
                        i = j
                        break
            else:
                # 没找到顶分型，放弃
                i += 1
                continue
            continue

        elif records[i][0] == 'top':
            # 向下笔：找下一个底分型
            best_bot = None
            best_bot_idx = None
            for j in range(i+1, min(i+30, len(records))):
                if records[j][0] == 'bot':
                    if best_bot is None or records[j][2] < best_bot[2]:
                        best_bot = records[j]
                        best_bot_idx = j
                elif records[j][0] == 'top' and j > i+1:
                    if best_bot is not None and best_bot_idx - i >= 3:
                        bis.append({
                            'direction': '下跌',
                            'start_date': records[i][1],
                            'end_date': best_bot[1],
                            'start_price': records[i][2],
                            'end_price': best_bot[2],
                            'change_pct': (best_bot[2]/records[i][2]-1)*100,
                            'tokens': [r[3] for r in records[i:best_bot_idx+1]],
                            'token_count': best_bot_idx - i + 1,
                            'strength': '强' if (records[i][2]/best_bot[2]-1)*100 > 5 else '弱',
                        })
                        i = best_bot_idx
                        break
                    else:
                        i = j
                        break
            else:
                i += 1
                continue
            continue

        i += 1

    return bis

# ── 3. 走势叙述（讲故事的引擎） ──

def narrate_trend(bis, name='指数'):
    """从笔序列中提取"走势故事" """
    if not bis:
        return "无有效笔数据"

    lines = [f'【{name} 走势叙事】']

    # 统计整体
    up_bis = [b for b in bis if b['direction'] == '上涨']
    down_bis = [b for b in bis if b['direction'] == '下跌']
    total_pct = (bis[-1]['end_price'] / bis[0]['start_price'] - 1) * 100

    lines.append(f'时期: {bis[0]["start_date"]} ~ {bis[-1]["end_date"]}')
    lines.append(f'共识别 {len(bis)} 个笔: {len(up_bis)}涨 {len(down_bis)}跌')
    lines.append(f'区间涨跌: {total_pct:+.1f}%')

    # 按阶段分段
    # 简单方法: 连续3个同向笔 = 一段趋势
    segments = []
    current_seg = [bis[0]]
    for b in bis[1:]:
        if b['direction'] == current_seg[-1]['direction']:
            current_seg.append(b)
        else:
            segments.append(current_seg)
            current_seg = [b]
    segments.append(current_seg)

    lines.append(f'共 {len(segments)} 个走势段:')
    for si, seg in enumerate(segments):
        seg_dir = seg[0]['direction']
        seg_chg = sum(b['change_pct'] for b in seg)
        lines.append(f'')
        lines.append(f'  📍 第{si+1}段: {seg_dir} ({len(seg)}个笔, 幅度{seg_chg:+.1f}%)')

        for b in seg:
            # 检查笔内部Token构成
            t7s = [t['token_7'] for t in b['tokens']]
            dir_count = t7s.count('方向')
            contain_count = t7s.count('包含关系')
            turn_count = t7s.count('转折')

            # 描述风格
            if dir_count >= len(t7s) * 0.6:
                style = '流畅拉升' if b['direction'] == '上涨' else '流畅下跌'
            elif contain_count >= len(t7s) * 0.4:
                style = '纠结盘升' if b['direction'] == '上涨' else '阴跌盘降'
            elif turn_count >= len(t7s) * 0.3:
                style = '震荡上行' if b['direction'] == '上涨' else '震荡下行'
            else:
                style = '混搭走势'

            lines.append(f'    ╰ {b["start_date"]}~{b["end_date"]} [{b["token_count"]}K线] {style} {b["change_pct"]:+.1f}% (方向{dir_count}/包含{contain_count}/转折{turn_count})')

    return '\n'.join(lines)


# ═══════════════════════════
#  主流程
# ═══════════════════════════
if __name__ == '__main__':
    # 先用几个不同的指数试
    test_codes = [
        ('sh000001', '上证指数'),
        ('sz399006', '创业板指'),
        ('sh880373', '半导体板块'),
        ('sh880558', '白酒板块'),
    ]

    for code, name in test_codes:
        print('\n' + '=' * 70)
        tokens, df = load_token_sequence(code)
        bis = segment_bi(tokens)
        story = narrate_trend(bis, name)
        print(story)
        print(f'  笔序列: {" → ".join(b["direction"] for b in bis[:10])}...')

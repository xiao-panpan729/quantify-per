#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论层次化解析器 — 3K线原语 → 笔 → 线段 → 走势类型

层级:
  Token(3-bar原语, 6类) → 笔(词组, 3+N扩展) → 线段(句子) → 走势类型(文章)

核心改进 vs token_bi_segmenter.py:
  1. ✅ 包含处理前置（缠论正确顺序）
  2. ✅ 3+N笔检测（不固定窗口，价格驱动）
  3. ✅ 同向分型合并（取更优值）
  4. ✅ 线段构造（笔破坏检测）
  5. ✅ 走势类型分类（中枢计数）
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os, json, math
from collections import defaultdict
import pandas as pd
import numpy as np

# ════════════════════════════════════════════
# Layer 0: 包含处理
# ════════════════════════════════════════════

def _merge_bars(b1, b2, direction='up'):
    """合并两根包含K线

    direction: 合并方向
      'up'   — 上涨趋势合并：取高高(H)和低高中较高者(L)
      'down' — 下跌趋势合并：取高低(H)和低低(L)
    """
    if direction == 'up':
        new_high = max(b1['high'], b2['high'])
        new_low = max(b1['low'], b2['low'])
    else:
        new_high = min(b1['high'], b2['high'])
        new_low = min(b1['low'], b2['low'])

    new_close = b2['close']  # 保留新bar的close
    new_open = b1['open'] if direction == 'up' else b2['open']

    return {
        'open': new_open,
        'high': new_high,
        'low': new_low,
        'close': new_close,
        'date': b2['date'],  # 用后一根的日期
    }


def is_contained(b1, b2):
    """检查b2是否被b1包含（或包含b1）"""
    return (b2['high'] <= b1['high'] and b2['low'] >= b1['low']) or \
           (b2['high'] >= b1['high'] and b2['low'] <= b1['low'])


def preprocess_bars(bars):
    """批量包含处理：递归合并包含K线

    关键差异 vs 单次扫描：
    合并后的K线可能仍被前一根包含（向上合并尤其常见），
    因此每合并一次就递归检查，直至无包含关系。

    输入: bars = [{date, open, high, low, close}, ...]
    输出: clean = 无包含关系的纯净K线序列
    """
    if not bars:
        return []

    clean = [bars[0]]

    for i in range(1, len(bars)):
        bar = bars[i]
        prev = clean[-1]

        if is_contained(prev, bar):
            # 有包含关系 → 合并
            direction = 'up' if bar['high'] >= prev['high'] else 'down'
            merged = _merge_bars(prev, bar, direction)
            clean[-1] = merged  # 替换最后一条

            # ★递归检查：merged bar 是否仍被 clean[-2] 包含？
            # 例：PREV(95,55) → A(80,60) → B(85,65)
            #   A被B包含 → 合并为AB(85,65)
            #   AB still inside PREV(95,55) → 继续合并
            while len(clean) >= 2 and is_contained(clean[-2], clean[-1]):
                p2 = clean[-2]  # 前前一根
                cur = clean[-1]  # 刚合并的结果
                d = 'up' if cur['high'] >= p2['high'] else 'down'
                re_merged = _merge_bars(p2, cur, d)
                clean[-2] = re_merged
                clean.pop()  # 移除已合并的最后一根
        else:
            clean.append(dict(bar))

    return clean


# ════════════════════════════════════════════
# Layer 1: Token分类（3-bar原语 → 6类）
# ════════════════════════════════════════════

def classify_3bar(b1, b2, b3):
    """3根K线 → 缠论语义类别（6类，无"包含关系"）

    因为包含处理已前置，输入的b1/b2/b3都是无包含的clean bars
    """
    h1, l1 = b1['high'], b1['low']
    h2, l2 = b2['high'], b2['low']
    h3, l3 = b3['high'], b3['low']
    c1, c2, c3 = b1['close'], b2['close'], b3['close']

    # 分型检测（无包含干扰，直接判断）
    is_top = h2 > h1 and h2 > h3
    is_bottom = l2 < l1 and l2 < l3

    # 纯顶底分型（非包含干扰下的真实分型）
    if is_top and is_bottom:
        # 同时是顶和底 → 看收盘方向
        if c2 > c1 and c2 > c3:
            return '顶分型'
        elif c2 < c1 and c2 < c3:
            return '底分型'
        else:
            return '转折'  # 十字星变体
    if is_top:
        return '顶分型'
    if is_bottom:
        return '底分型'

    # 方向序列（收盘价三根连续）
    if c1 < c2 < c3:
        return '方向↑'
    if c1 > c2 > c3:
        return '方向↓'

    # 转折试探（涨跌涨/跌涨跌形态）
    if c1 < c2 > c3:
        return '转折'
    if c1 > c2 < c3:
        return '转折'

    return '其他'


def tokenize(bars):
    """纯净bar序列 → Token序列（步进=1滑动窗口）"""
    tokens = []
    for i in range(len(bars) - 2):
        b1, b2, b3 = bars[i], bars[i+1], bars[i+2]
        token_type = classify_3bar(b1, b2, b3)
        tokens.append({
            'date': b2['date'],
            'token': token_type,
            'close': b2['close'],
            'high': b2['high'],
            'low': b2['low'],
            'bar_idx': i + 1,  # 中间bar的索引
        })
    return tokens


# ════════════════════════════════════════════
# Layer 2: 笔检测（3+N扩展）
# ════════════════════════════════════════════

def detect_bis(tokens, bars, config=None):
    """从Token序列中检测笔

    核心算法（3+N）:
      Token的3-bar滑动窗口产生高密度分型（~45%）。直接匹配分型对会导致
      统计陷阱。正确做法：从分型定位到具体bar，在bar空间做价格匹配。

    算法:
      1. 从tokens中提取分型，定位到具体的bar（分型的中间bar）
      2. 一个分型的特征价格：底分型=bar.low, 顶分型=bar.high
      3. 从底分型出发，扫后续所有bar直至找到价格更高的顶分型=上涨笔
      4. 从顶分型出发，扫后续所有bar直至找到价格更低的底分型=下跌笔
      5. 同向分型取更优值（底取更低、顶取更高），过程中合并

    参数:
      config.min_bi_amp: 最小笔幅度（百分比，默认1.0%）
    """
    cfg = config or {}
    min_amp = cfg.get('min_bi_amp', 1.0)
    max_bi_bars = cfg.get('max_bi_bars', 300)  # 单笔最长bar数，超过强制打断

    # 从tokens中提取分型，定位到中间bar
    # bars_clean是包含处理后的K线序列
    # 分型类别的特征：中间bar的价格
    fractals = []  # [(token_idx, type, bar_idx, price, date)]
    for ti, t in enumerate(tokens):
        bi = t['bar_idx']  # 中间bar在bars_clean中的索引
        if t['token'] == '底分型':
            fractals.append((ti, 'bot', bi, t['low'], t['date']))
        elif t['token'] == '顶分型':
            fractals.append((ti, 'top', bi, t['high'], t['date']))

    if len(fractals) < 2:
        return []

    bis = []
    i = 0
    while i < len(fractals) - 1:
        ti, ftype, bar_i, price_i, date_i = fractals[i]
        tj, ftype_j, bar_j, price_j, date_j = fractals[i + 1]

        if ftype == 'bot' and ftype_j == 'top':
            # 底→顶：候选上涨笔
            # 取最高的顶(bar最高)和可能切换的更低底(bar最低)
            best_bot_price = price_i
            best_bot_bar = bar_i
            best_bot_date = date_i
            best_top_price = None
            best_top_bar = None
            best_top_date = None
            best_top_idx = None

            for j in range(i + 1, len(fractals)):
                tj2, ft2, bj2, pj2, dj2 = fractals[j]
                # 强制打断：笔长超过上限
                if best_top_price is not None and (bj2 - best_bot_bar) > max_bi_bars:
                    break
                if ft2 == 'bot':
                    if pj2 < best_bot_price and (bj2 - best_bot_bar) > 1:
                        # 更低的底 → 切换，原笔结束
                        break
                elif ft2 == 'top':
                    if bj2 - best_bot_bar > 1:  # 至少间隔1根bar
                        if best_top_price is None or pj2 > best_top_price:
                            best_top_price = pj2
                            best_top_bar = bj2
                            best_top_date = dj2
                            best_top_idx = j

            if best_top_price is not None:
                change_pct = (best_top_price / best_bot_price - 1) * 100
                if change_pct >= min_amp:
                    bis.append(_make_bi_simple(
                        '上涨', best_bot_date, best_top_date,
                        best_bot_price, best_top_price, tokens, best_bot_bar, best_top_bar))
                    i = best_top_idx + 1
                    continue

            i += 1

        elif ftype == 'top' and ftype_j == 'bot':
            # 顶→底：候选下跌笔
            best_top_price = price_i
            best_top_bar = bar_i
            best_top_date = date_i
            best_bot_price = None
            best_bot_bar = None
            best_bot_date = None
            best_bot_idx = None

            for j in range(i + 1, len(fractals)):
                tj2, ft2, bj2, pj2, dj2 = fractals[j]
                if best_bot_price is not None and (bj2 - best_top_bar) > max_bi_bars:
                    break
                if ft2 == 'top':
                    if pj2 > best_top_price and (bj2 - best_top_bar) > 1:
                        # 更高的顶 → 切换
                        break
                elif ft2 == 'bot':
                    if bj2 - best_top_bar > 1:
                        if best_bot_price is None or pj2 < best_bot_price:
                            best_bot_price = pj2
                            best_bot_bar = bj2
                            best_bot_date = dj2
                            best_bot_idx = j

            if best_bot_price is not None:
                change_pct = (best_bot_price / best_top_price - 1) * 100
                if abs(change_pct) >= min_amp:
                    bis.append(_make_bi_simple(
                        '下跌', best_top_date, best_bot_date,
                        best_top_price, best_bot_price, tokens, best_top_bar, best_bot_bar))
                    i = best_bot_idx + 1
                    continue

            i += 1

        else:
            # 同向分型：取更优
            if ftype == ftype_j:
                if ftype == 'top' and price_j > price_i:
                    i += 1
                elif ftype == 'bot' and price_j < price_i:
                    i += 1
                else:
                    i += 2
            else:
                i += 1

    return bis


def _make_bi_simple(direction, start_date, end_date,
                    start_price, end_price, tokens,
                    start_bar, end_bar):
    """从价格极值直接构造笔对象"""
    change_pct = (end_price / start_price - 1) * 100
    strength = '强' if abs(change_pct) > 5 else '弱'

    # 统计这之间的token构成
    start_ti = None
    end_ti = None
    for ti, t in enumerate(tokens):
        if t['bar_idx'] == start_bar:
            start_ti = ti
        if t['bar_idx'] == end_bar:
            end_ti = ti
    if start_ti is None or end_ti is None:
        start_ti = 0
        end_ti = len(tokens) - 1

    bi_tokens = tokens[start_ti:end_ti + 1]
    t7s = [t['token'] for t in bi_tokens]
    dir_up = t7s.count('方向↑')
    dir_dn = t7s.count('方向↓')
    turns = t7s.count('转折')
    total_dir = dir_up + dir_dn

    if total_dir >= len(t7s) * 0.5:
        fluency = '流畅'
    elif turns >= len(t7s) * 0.3:
        fluency = '震荡'
    else:
        fluency = '纠结'

    return {
        'direction': direction,
        'start_date': start_date,
        'end_date': end_date,
        'start_price': round(start_price, 2),
        'end_price': round(end_price, 2),
        'change_pct': round(change_pct, 2),
        'token_count': len(bi_tokens),
        'strength': strength,
        'fluency': fluency,
        'tokens': t7s,
        'dir_up': dir_up,
        'dir_dn': dir_dn,
        'turn_count': turns,
        '_start_bar': start_bar,
        '_end_bar': end_bar,
    }


# ════════════════════════════════════════════
# Layer 3: 线段构造
# ════════════════════════════════════════════

def build_segments(bis, config=None):
    """笔序列 → 线段（句子）

    规则:
      1. 同向笔连续 → 同一线段
      2. 反向笔力度>阈值 → 线段破坏，切分
      3. 反向笔力度弱（<阈值）→ 视为回调/反弹，归入当前线段
    """
    cfg = config or {}
    break_threshold = cfg.get('segment_break_threshold', 3.0)  # 反向笔超过此幅度才切段
    if not bis:
        return []

    segments = []
    current = [bis[0]]

    for bi in bis[1:]:
        last_dir = current[-1]['direction']

        if bi['direction'] == last_dir:
            # 同向，直接追加
            current.append(bi)
        else:
            # 反向，判断是否线段破坏
            bi_amp = abs(bi['change_pct'])
            if bi_amp >= break_threshold:
                # 强反向 → 线段破坏
                segments.append(_finalize_segment(current))
                current = [bi]
            else:
                # 弱反向 → 视为回调/反弹，归入当前线段
                current.append(bi)

    if current:
        segments.append(_finalize_segment(current))

    return segments


def _finalize_segment(bis):
    """将一组笔封装为线段"""
    start_p = bis[0]['start_price']
    end_p = bis[-1]['end_price']
    actual_change = (end_p / start_p - 1) * 100 if start_p else 0
    seg_dir = '上涨' if actual_change >= 0 else '下跌'

    total_up = sum(b['change_pct'] for b in bis if b['direction'] == '上涨')
    total_dn = sum(b['change_pct'] for b in bis if b['direction'] == '下跌')
    net_change = total_up + total_dn

    return {
        'direction': seg_dir,
        'start_date': bis[0]['start_date'],
        'end_date': bis[-1]['end_date'],
        'start_price': bis[0]['start_price'],
        'end_price': bis[-1]['end_price'],
        'change_pct': round(actual_change, 2),
        'net_change': round(net_change, 2),
        'bi_count': len(bis),
        'bi_list': bis,
        '_start_bar': bis[0]['_start_bar'],
        '_end_bar': bis[-1]['_end_bar'],
        'strength': '强' if abs(actual_change) > 10 else ('中' if abs(actual_change) > 5 else '弱'),
    }


# ════════════════════════════════════════════
# Layer 3b: 混合线段判断体系
# ════════════════════════════════════════════
#
# 核心思路：
#   线段终结的递归定义（特征序列→包含处理→方向）在硬代码中非常难写对。
#   CZSC作者判断"收益不高"所以不做。
#   但我们有大模型——让LLM做最终判断，硬代码做候选枚举。
#
# 流程：
#   1. segment_candidates() — 硬代码枚举所有"方向变化笔"作为候选终结
#   2. format_judge_prompt() — 将候选格式化为LLM可判断的交互格式
#   3. LLM/claude 阅读候选 → 用缠论规则判断哪些是真实终结
#   4. apply_judgments() — 将判断结果写回，生成最终线段
#


def segment_candidates(bis, config=None):
    """枚举所有线段终结候选点

    每个"方向变化的笔"都是一个候选。
    硬代码负责枚举，不替代LLM做判断。

    返回: [candidate, ...]
    candidate = {
        'id': int,
        'seg_direction': str,     # 当前段方向
        'seg_start_idx': int,     # 段起始在bis中的索引
        'seg_bis': [dict,...],    # 段内笔列表（含候选）
        'break_bi_idx': int,      # 候选终结笔的索引
        'break_bi': dict,         # 候选终结笔
        'seg_extreme': float,     # 段内极值（笔破坏判断用）
        'seg_net_change': float,  # 段内净值变化
        'pre_bi': dict or None,   # 候选笔的前一笔
    }
    """
    cfg = config or {}
    min_candidate_amp = cfg.get('min_candidate_amp', 1.0)  # 低于此幅度不作为候选

    if not bis:
        return []

    candidates = []

    # 逐笔扫描，遇到方向变化 → 候选
    for i in range(1, len(bis)):
        if bis[i]['direction'] != bis[i-1]['direction']:
            break_amp = abs(bis[i]['change_pct'])
            if break_amp < min_candidate_amp:
                continue

            # 走到这里的笔：方向变化 + 足够幅度
            # 确定"当前段"是从前面最近的同向段起点开始
            seg_start = _find_segment_start(bis, i)

            # 段内的笔（含候选）
            seg_bis = bis[seg_start:i+1]

            # 段的方向 = 第一笔的方向（即候选之前的同向方向）
            seg_dir = bis[seg_start]['direction']

            # 段内极值：对下跌段取最低价，对上涨段取最高价
            if seg_dir == '下跌':
                seg_extreme = min(
                    min(b['start_price'], b['end_price']) for b in bis[seg_start:i]
                )
            else:
                seg_extreme = max(
                    max(b['start_price'], b['end_price']) for b in bis[seg_start:i]
                )

            seg_net = (bis[i-1]['end_price'] / bis[seg_start]['start_price'] - 1) * 100

            candidates.append({
                'id': len(candidates),
                'seg_direction': seg_dir,
                'seg_start_idx': seg_start,
                'seg_bis': seg_bis,
                'break_bi_idx': i,
                'break_bi': bis[i],
                'seg_extreme': round(seg_extreme, 2),
                'seg_net_change': round(seg_net, 2),
                'pre_bi': bis[i-1],
            })

    return candidates


def _find_segment_start(bis, break_idx):
    """从break_idx往前找到当前段的起始笔索引

    当前段 = 从上一个"有效同向笔序列"开始
    即往前找到第一个方向变化的笔，然后取变化后的第一笔
    """
    dir_before = bis[break_idx - 1]['direction']

    for j in range(break_idx - 1, -1, -1):
        if bis[j]['direction'] != dir_before:
            return j + 1

    return 0


def format_judge_prompt(candidates, bis):
    """将所有候选格式化为LLM可判断的prompt"""
    lines = [
        '# 线段终结候选判断任务',
        '',
        '下面是一组K线经过缠论包含处理+笔检测后的笔序列，以及其中所有"方向变化的笔"作为候选线段终结。',
        '',
        '---',
        '',
    ]

    for c in candidates:
        lines.append(f'## 候选 #{c["id"]}')
        lines.append(f'')
        lines.append(f'**当前段方向**: {c["seg_direction"]}')
        lines.append(f'**段起始索引**: 笔#{c["seg_start_idx"]}')
        lines.append(f'**段内笔数**: {len(c["seg_bis"])}')
        lines.append(f'**段净值变化**: {c["seg_net_change"]:+.1f}%')
        lines.append(f'**段内极值**: {c["seg_extreme"]}')
        lines.append(f'')
        lines.append(f'段内笔序列:')
        for bi in c['seg_bis']:
            marker = ' ← 候选终结' if bi is c['break_bi'] else ''
            lines.append(
                f'  笔: {bi["direction"]} '
                f'{bi["start_date"]}~{bi["end_date"]} '
                f'{bi["change_pct"]:+.1f}% '
                f'({bi["start_price"]}→{bi["end_price"]}){marker}'
            )
        lines.append(f'')

        # 缠论判断依据
        break_bi = c['break_bi']
        pre_bi = c['pre_bi']

        lines.append(f'**缠论笔破坏判断**:')

        if c['seg_direction'] == '上涨' and break_bi['direction'] == '下跌':
            # 上涨段中出现下跌笔：判断是否跌破前一笔起点
            break_low = min(break_bi['start_price'], break_bi['end_price'])
            pre_low = min(pre_bi['start_price'], pre_bi['end_price'])
            broke = break_low < pre_low
            lines.append(f'  - 上涨段中下跌笔，跌破前笔低点({pre_low})? {"是" if broke else "否"}')
            lines.append(f'  - 下跌笔低点: {break_low}')
            lines.append(f'  笔破坏: {"✅ 有效" if broke else "❌ 未触及"}')
        elif c['seg_direction'] == '下跌' and break_bi['direction'] == '上涨':
            # 下跌段中出现上涨笔：判断是否突破前一笔高点
            break_high = max(break_bi['start_price'], break_bi['end_price'])
            pre_high = max(pre_bi['start_price'], pre_bi['end_price'])
            broke = break_high > pre_high
            lines.append(f'  - 下跌段中上涨笔，突破前笔高点({pre_high})? {"是" if broke else "否"}')
            lines.append(f'  - 上涨笔高点: {break_high}')
            lines.append(f'  笔破坏: {"✅ 有效" if broke else "❌ 未触及"}')
        else:
            lines.append(f'  - 方向: {c["seg_direction"]}段 → {break_bi["direction"]}笔')
            lines.append(f'  - skipp')

        lines.append(f'')
        lines.append(f'## 你的判断')
        lines.append(f'')
        lines.append(f'这个候选笔是否构成有效的**线段破坏**？')
        lines.append(f'')
        lines.append(f'判断依据（缠论原文）：')
        lines.append(f'1. **笔破坏**：反向笔突破前一笔的极端价格 → 预警信号')
        lines.append(f'2. **线段破坏**：笔破坏后，后续笔继续同向延伸形成新段 → 确认')
        lines.append(f'3. **力度辅助**：反向笔幅度<2%通常只是回调，>5%大概率是反转')
        lines.append(f'')
        lines.append(f'请回答：')
        lines.append(f'- **是否终结**: 是/否/不确定')
        lines.append(f'- **理由**: (50字以内)')
        lines.append(f'- **力度判断**: 回调/反转/不确定')
        lines.append(f'')
        lines.append(f'---')
        lines.append(f'')

    return '\n'.join(lines)


def apply_judgments(candidates, judgments_file=None, bis=None, judgments_map=None):
    """读取LLM判断结果，输出最终线段

    支持两种模式:
      1. judgments_file: JSON文件路径
      2. judgments_map: 直接传入dict {candidate_id: {'break': bool, ...}}

    两种模式都依赖候选列表和笔列表。
    """
    if bis is None:
        return []

    if judgments_file:
        with open(judgments_file, 'r', encoding='utf-8') as f:
            judgments = json.load(f)
    elif judgments_map:
        judgments = judgments_map
    else:
        return []

    # 构建笔索引→是否段终结的映射
    break_idxs = set()
    for c in candidates:
        cid = str(c['id'])
        if cid in judgments and judgments[cid].get('break', False):
            break_idxs.add(c['break_bi_idx'])

    # 用break_idxs切分段
    segments = []
    current = [bis[0]]

    for i in range(1, len(bis)):
        if i in break_idxs:
            # 前一段结束，新段开始
            segments.append(_finalize_segment(current))
            current = [bis[i]]
        else:
            current.append(bis[i])

    if current:
        segments.append(_finalize_segment(current))

    return segments


# ════════════════════════════════════════════
# Layer 4: 走势类型分类
# ════════════════════════════════════════════

def classify_trend_type(bis):
    """根据中枢计数判断走势类型

    中枢定义：连续3笔重叠区间
    - 0中枢 → 尚未成段
    - 1中枢 → 盘整
    - 2+中枢（不重叠）→ 趋势
    """
    if len(bis) < 3:
        return '未成段', []

    zhongshu_list = []
    for i in range(len(bis) - 2):
        # 取3笔的重叠区间
        b1, b2, b3 = bis[i], bis[i+1], bis[i+2]
        zs_high = min(b1['end_price'], b2['end_price'], b3['end_price'])
        zs_low = max(b1['start_price'], b2['start_price'], b3['start_price'])
        # 实际上中枢的高低价需要看笔内的极值
        # 对于上涨笔：极值在end_price（高点）
        # 对于下跌笔：极值在start_price（高点）
        # 简化版本统一取start/end的min/max

        ups = [b for b in (b1, b2, b3) if b['direction'] == '上涨']
        dns = [b for b in (b1, b2, b3) if b['direction'] == '下跌']

        zs_high = min(
            max(b['start_price'], b['end_price']) for b in (b1, b2, b3)
        )
        zs_low = max(
            min(b['start_price'], b['end_price']) for b in (b1, b2, b3)
        )

        if zs_high > zs_low:
            zhongshu_list.append({
                'start_idx': i,
                'end_idx': i + 2,
                'high': round(zs_high, 2),
                'low': round(zs_low, 2),
                'range': round(zs_high - zs_low, 2),
            })

    # 合并重叠中枢
    if not zhongshu_list:
        return '无中枢', []

    merged = [zhongshu_list[0]]
    for zs in zhongshu_list[1:]:
        prev = merged[-1]
        if zs['low'] <= prev['high'] and zs['high'] >= prev['low']:
            # 重叠 → 扩展
            prev['high'] = max(prev['high'], zs['high'])
            prev['low'] = min(prev['low'], zs['low'])
            prev['end_idx'] = zs['end_idx']
        else:
            merged.append(zs)

    zs_count = len(merged)
    if zs_count == 0:
        trend_type = '无中枢'
    elif zs_count == 1:
        trend_type = '盘整'
    elif zs_count >= 2:
        direction = bis[0]['direction']
        trend_type = f'{direction}趋势'
    else:
        trend_type = '盘整'

    return trend_type, merged


# ════════════════════════════════════════════
# Narrator: 走势叙述
# ════════════════════════════════════════════

def narrate(parse_tree):
    """解析树 → 走势叙述"""
    name = parse_tree.get('name', '指数')
    bis = parse_tree.get('bis', [])
    segments = parse_tree.get('segments', [])
    trend_type = parse_tree.get('trend_type', '未知')
    zhongshu = parse_tree.get('zhongshu', [])

    if not bis:
        return f'【{name} 走势叙事】\n无有效笔数据'

    lines = [f'【{name} 走势叙事】']

    # 概况
    up_bis = [b for b in bis if b['direction'] == '上涨']
    dn_bis = [b for b in bis if b['direction'] == '下跌']
    total_chg = (bis[-1]['end_price'] / bis[0]['start_price'] - 1) * 100

    raw_kline = parse_tree.get('raw_kline_count', 0)
    merged_kline = parse_tree.get('merged_kline_count', 0)
    saved = raw_kline - merged_kline if raw_kline > merged_kline else 0

    lines.append(f'{name} {bis[0]["start_date"]} ~ {bis[-1]["end_date"]}')
    lines.append(f'K线: {raw_kline}根 (包含合并节省{saved}根) → {len(bis)}笔 ({len(up_bis)}涨{len(dn_bis)}跌)')
    lines.append(f'区间涨跌: {total_chg:+.1f}% | 走势类型: {trend_type}')
    if zhongshu:
        lines.append(f'中枢: {len(zhongshu)}个')
        for zi, zs in enumerate(zhongshu):
            lines.append(f'  中枢{zi+1}: [{zs["low"]} ~ {zs["high"]}] 幅度{zs["range"]}')

    # 线段叙述
    lines.append(f'')
    lines.append(f'共 {len(segments)} 个走势段:')
    for si, seg in enumerate(segments):
        seg_dir = seg['direction']
        lines.append(f'')
        lines.append(f'  第{si+1}段: {seg_dir} ({seg["bi_count"]}笔, {seg["change_pct"]:+.1f}%) [{seg["start_date"]}~{seg["end_date"]}]')

        for bi in seg['bi_list']:
            # 描述风格
            if bi['fluency'] == '流畅':
                style = '流畅上攻' if bi['direction'] == '上涨' else '流畅下探'
            elif bi['fluency'] == '震荡':
                style = '震荡上行' if bi['direction'] == '上涨' else '震荡下行'
            else:
                style = '纠结爬升' if bi['direction'] == '上涨' else '阴跌盘降'

            # 额外说明
            extra = ''
            if bi['turn_count'] >= bi['token_count'] * 0.3:
                extra += ' 多转折'
            if bi['strength'] == '强':
                extra += ' ※强'

            lines.append(f'    ╰ {bi["start_date"]}~{bi["end_date"]} [{bi["token_count"]}Token] {style} {bi["change_pct"]:+.1f}%{extra}')

    return '\n'.join(lines)


# ════════════════════════════════════════════
# ChanlunParser 主类
# ════════════════════════════════════════════

class ChanlunParser:
    """缠论层次化解析器

    用法:
        parser = ChanlunParser()
        parser.feed(bars)
        result = parser.to_dict()
        print(parser.narrate())
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.result = {
            'raw_kline_count': 0,
            'merged_kline_count': 0,
            'bars_raw': [],
            'bars_clean': [],
            'tokens': [],
            'bis': [],
            'segments': [],
            'trend_type': '未知',
            'zhongshu': [],
        }

    def feed(self, bars, name=''):
        """输入K线 → 完整解析

        bars: [{date, open, high, low, close}, ...]
              date 可以是字符串或datetime
        """
        # 标准化bars
        raw_bars = []
        for b in bars:
            d = b['date'] if isinstance(b['date'], str) else str(b['date'])
            raw_bars.append({
                'date': d,
                'open': float(b['open']),
                'high': float(b['high']),
                'low': float(b['low']),
                'close': float(b['close']),
            })

        self.result['name'] = name
        self.result['raw_kline_count'] = len(raw_bars)
        self.result['bars_raw'] = raw_bars

        # Layer 0: 包含处理
        clean = preprocess_bars(raw_bars)
        self.result['merged_kline_count'] = len(clean)
        self.result['bars_clean'] = clean

        # Layer 1: Token分类
        tokens = tokenize(clean)
        self.result['tokens'] = tokens

        # Layer 2: 笔检测
        bis = detect_bis(tokens, clean, self.config)
        self.result['bis'] = bis

        # Layer 3: 线段
        segments = build_segments(bis, self.config)
        self.result['segments'] = segments

        # Layer 4: 走势类型
        trend_type, zhongshu = classify_trend_type(bis)
        self.result['trend_type'] = trend_type
        self.result['zhongshu'] = zhongshu

        return self.result

    def to_dict(self):
        return self.result

    def to_json(self, indent=2):
        """序列化为JSON（简化，去掉tokens细节避免过大）"""
        out = {
            'name': self.result.get('name', ''),
            'range': {
                'start': self.result['bars_raw'][0]['date'] if self.result['bars_raw'] else '',
                'end': self.result['bars_raw'][-1]['date'] if self.result['bars_raw'] else '',
            },
            'kline': {
                'raw': self.result['raw_kline_count'],
                'merged': self.result['merged_kline_count'],
            },
            'tokens': len(self.result['tokens']),
            'bis': len(self.result['bis']),
            'segments': len(self.result['segments']),
            'trend_type': self.result['trend_type'],
            'zhongshu': [
                {'low': z['low'], 'high': z['high'], 'range': z['range']}
                for z in self.result['zhongshu']
            ],
            'bis_detail': [
                {'direction': b['direction'], 'start': b['start_date'], 'end': b['end_date'],
                 'change': b['change_pct'], 'strength': b['strength'], 'fluency': b['fluency']}
                for b in self.result['bis']
            ],
            'segments_detail': [
                {'direction': s['direction'], 'start': s['start_date'], 'end': s['end_date'],
                 'change': s['change_pct'], 'bi_count': s['bi_count'], 'strength': s['strength']}
                for s in self.result['segments']
            ],
        }
        return json.dumps(out, ensure_ascii=False, indent=indent)

    def narrate(self):
        return narrate(self.result)

    def print_summary(self):
        """终端快速概览"""
        r = self.result
        print(f'  K线: {r["raw_kline_count"]}根 → 合并后{r["merged_kline_count"]}根 (节省{r["raw_kline_count"]-r["merged_kline_count"]}根)')
        print(f'  Token: {len(r["tokens"])}个')
        print(f'  笔: {len(r["bis"])}个')

        if r['bis']:
            up = sum(1 for b in r['bis'] if b['direction'] == '上涨')
            dn = sum(1 for b in r['bis'] if b['direction'] == '下跌')
            print(f'    上涨{up} 下跌{dn}')
            avg_len = sum(b['token_count'] for b in r['bis']) / len(r['bis'])
            print(f'    平均Token数/笔: {avg_len:.1f}')

        print(f'  线段: {len(r["segments"])}段')
        print(f'  走势类型: {r["trend_type"]}')
        if r['zhongshu']:
            for zi, zs in enumerate(r['zhongshu']):
                print(f'    中枢{zi+1}: [{zs["low"]} ~ {zs["high"]}]')


# ════════════════════════════════════════════
# 快捷入口
# ════════════════════════════════════════════

def parse_chanlun(code, period='daily', name=None, config=None):
    """从通达信代码直接解析

    用法:
        result = parse_chanlun('sh000001')
        print(result['trend_type'])
    """
    from pytdx.reader import TdxDailyBarReader, TdxMinBarReader

    if period == 'daily':
        mkt = code[:2]
        reader = TdxDailyBarReader()
        fp = f'C:/zd_cjzq/vipdoc/{mkt}/lday/{code}.day'
        df = reader.get_df(fp)
    else:
        mkt = code[:2]
        reader = TdxMinBarReader()
        ext_map = {'min1': 'lc1', 'min5': 'lc5'}
        dir_map = {'min1': 'minline', 'min5': 'fzline'}
        ext = ext_map.get(period, 'lc5')
        d = dir_map.get(period, 'fzline')
        fp = f'C:/zd_cjzq/vipdoc/{mkt}/{d}/{code}.{ext}'
        df = reader.get_df(fp)

    bars = []
    for idx, row in df.iterrows():
        d = idx if isinstance(idx, str) else str(idx.date())
        bars.append({
            'date': d,
            'open': row['open'],
            'high': row['high'],
            'low': row['low'],
            'close': row['close'],
        })

    parser = ChanlunParser(config=config)
    parser.feed(bars, name or code)
    return parser


def narrate_chanlun(code, name='', period='daily'):
    """单入口：code → 走势叙述"""
    parser = parse_chanlun(code, period=period, name=name or code)
    return parser.narrate()


# ════════════════════════════════════════════
# 验证函数
# ════════════════════════════════════════════

def verify_containment(code='sh000001'):
    """验证包含处理效果"""
    parser = parse_chanlun(code)
    r = parser.result
    print(f'{code} 包含处理验证:')
    print(f'  原始K线: {r["raw_kline_count"]}根')
    print(f'  合并后: {r["merged_kline_count"]}根')
    print(f'  节省: {r["raw_kline_count"] - r["merged_kline_count"]}根 ({((r["raw_kline_count"]-r["merged_kline_count"])/r["raw_kline_count"]*100):.1f}%)')

    # 检查token中是否还有"包含关系"
    contain_count = sum(1 for t in r['tokens'] if t['token'] == '包含关系')
    print(f'  Token中包含关系: {contain_count}个 (应为0)')
    assert contain_count == 0, f'包含处理失败：仍有 {contain_count} 个包含关系Token!'
    print('  ✅ 包含处理正确')
    return parser


def verify_bi_vs_czsc(code='sh000001'):
    """对比CZSC的笔数"""
    try:
        from czsc import CZSC
        from notebook.chanlun.adapter import df_to_bars
        from pytdx.reader import TdxDailyBarReader

        reader = TdxDailyBarReader()
        mkt = code[:2]
        fp = f'C:/zd_cjzq/vipdoc/{mkt}/lday/{code}.day'
        df = reader.get_df(fp)

        bars = df_to_bars(df, code)
        c = CZSC(bars)
        czsc_bi_count = len(c.bi_list)

        parser = parse_chanlun(code)
        our_bi_count = len(parser.result['bis'])

        print(f'  CZSC笔数: {czsc_bi_count}')
        print(f'  本解析器笔数: {our_bi_count}')
        print(f'  差异: {abs(czsc_bi_count - our_bi_count)}')

        # 输出明细对比
        print(f'  CZSC笔: {[(b.direction.value, round(b.high,2), round(b.low,2)) for b in c.bi_list[:5]]}...')
        print(f'  本解析器笔: {[(b["direction"], b["start_price"], b["end_price"]) for b in parser.result["bis"][:5]]}...')

        return czsc_bi_count, our_bi_count
    except ImportError:
        print('  ⚠️ CZSC未安装或导入失败，跳过对比')
        return None, None


# ════════════════════════════════════════════
# Training Data Export (Teacher→Student 蒸馏)
# ════════════════════════════════════════════

def extract_bar_features(bars):
    """从bars提取训练特征

    输出: [[f0, f1, ...], ...] 每个bar一组特征
    特征(12维):
      0-3: OHLC (归一化到0-1范围)
      4: volume (归一化)
      5: 当日收益率
      6: 收盘价/MA5
      7: 收盘价/MA20
      8: 收盘价/MA60
      9: 量比(volume/MA5_vol)
      10: (close - low) / (high - low + 1e-8)  # 日内位置
      11: 是否为数据边界标记
    """
    import numpy as np

    n = len(bars)
    prices = np.array([b['close'] for b in bars])
    highs = np.array([b['high'] for b in bars])
    lows = np.array([b['low'] for b in bars])
    opens = np.array([b['open'] for b in bars])
    volumes = np.array([b.get('volume', b.get('vol', 0)) for b in bars])

    # 收益率
    returns = np.zeros(n)
    returns[1:] = (prices[1:] / prices[:-1] - 1) * 100

    # MA
    def ma(arr, window):
        out = np.zeros_like(arr)
        cum = np.cumsum(arr)
        out[:window] = cum[:window] / np.arange(1, window + 1)
        out[window:] = (cum[window:] - cum[:-window]) / window
        return out

    ma5 = ma(prices, 5)
    ma20 = ma(prices, 20)
    ma60 = ma(prices, 60)

    # 归一化到[0,1]范围
    def normalize(arr):
        mn, mx = arr.min(), arr.max()
        if mx - mn < 1e-8:
            return np.zeros_like(arr)
        return (arr - mn) / (mx - mn)

    features = []
    for i in range(n):
        # 日内位置
        range_hl = highs[i] - lows[i]
        intra_pos = (prices[i] - lows[i]) / (range_hl + 1e-8)

        # 量比
        if i >= 5:
            vol_ma5 = np.mean(volumes[max(0, i-5):i])
        else:
            vol_ma5 = np.mean(volumes[:i+1]) if i > 0 else 1.0
        vol_ratio = volumes[i] / (vol_ma5 + 1e-6)

        features.append([
            opens[i], highs[i], lows[i], prices[i],
            volumes[i],
            returns[i],
            prices[i] / ma5[i] if ma5[i] > 0 else 1.0,
            prices[i] / ma20[i] if ma20[i] > 0 else 1.0,
            prices[i] / ma60[i] if ma60[i] > 0 else 1.0,
            vol_ratio,
            intra_pos,
            0.0,  # 边界标记
        ])

    return np.array(features)


def encode_structure_labels(bis, segments, tokens, n_bars):
    """将笔/段/Token结构编码为每个bar的标签

    输出: dict of numpy arrays, 每个长度=n_bars
      bi_boundary: 0=内部, 1=笔起点, 2=笔终点, 3=同时起终点
      bi_direction: 0=无/内部, 1=上涨, 2=下跌
      seg_id: -1=未分类, 0..K=所属段编号
      token_type: 0=其他, 1=方向↑, 2=方向↓, 3=顶分型, 4=底分型, 5=转折
    """
    import numpy as np

    bi_boundary = np.zeros(n_bars, dtype=np.int32)
    bi_direction = np.zeros(n_bars, dtype=np.int32)
    seg_id = np.full(n_bars, -1, dtype=np.int32)

    # 从bis标注边界
    for bi in bis:
        s = bi['_start_bar']
        e = bi['_end_bar']
        if 0 <= s < n_bars:
            bi_boundary[s] = 2 if bi_boundary[s] == 1 else 1  # start
        if 0 <= e < n_bars and e != s:
            bi_boundary[e] = 1 if bi_boundary[e] == 2 else 2  # end
        if s == e:
            bi_boundary[s] = 3  # both

        # 方向
        dir_val = 1 if bi['direction'] == '上涨' else 2
        for j in range(max(0, s), min(n_bars, e + 1)):
            bi_direction[j] = dir_val

    # 从segments标注段归属
    for sid, seg in enumerate(segments):
        seg_id[seg['_start_bar']:seg['_end_bar'] + 1] = sid

    # 从tokens标注类型
    token_type = np.zeros(n_bars, dtype=np.int32)
    token_map = {'方向↑': 1, '方向↓': 2, '顶分型': 3, '底分型': 4, '转折': 5, '其他': 0}
    for t in tokens:
        bi = t['bar_idx']
        if 0 <= bi < n_bars:
            token_type[bi] = token_map.get(t['token'], 0)

    return {
        'bi_boundary': bi_boundary,
        'bi_direction': bi_direction,
        'seg_id': seg_id,
        'token_type': token_type,
    }


def export_training_record(code, period, judgments=None, config=None):
    """导出单个标的的训练记录

    输出dict:
      meta: {code, period, name, n_bars, n_bis}
      features: [N×12] numpy array
      labels: {per-bar label arrays}
      structure: {原始bi/段列表}
    """
    parser = parse_chanlun(code, period=period, name=code, config=config)
    r = parser.result
    n_bars = r['merged_kline_count']

    # 使用judgments生成段，否则用默认
    if judgments:
        cands = segment_candidates(r['bis'], config)
        segments = apply_judgments(cands, None, r['bis'], judgments_map=judgments)
    else:
        segments = r['segments']

    bare_bars = r['bars_clean']
    features = extract_bar_features(bare_bars)
    labels = encode_structure_labels(r['bis'], segments, r['tokens'], n_bars)

    record = {
        'meta': {
            'code': code,
            'period': period,
            'name': parser.result.get('name', code),
            'n_bars': n_bars,
            'n_bis': len(r['bis']),
            'n_segments': len(segments),
            'n_raw_bars': r['raw_kline_count'],
        },
        'features': features.tolist() if hasattr(features, 'tolist') else features,
        'labels': {k: v.tolist() if hasattr(v, 'tolist') else v for k, v in labels.items()},
        'structure': {
            'bis': [
                {'direction': b['direction'], 'start_bar': b['_start_bar'],
                 'end_bar': b['_end_bar'], 'change_pct': b['change_pct']}
                for b in r['bis']
            ],
            'segments': [
                {'direction': s['direction'], 'start_bar': s['_start_bar'],
                 'end_bar': s['_end_bar']}
                for s in segments
            ],
        },
    }
    return record


def export_dataset(codes=None, output_file='training_data/chanlun_dataset.json'):
    """批量导出训练数据集"""
    if codes is None:
        codes = [
            ('sh000001', '上证指数'),
            ('sz399006', '创业板指'),
            ('sz159740', '恒生科技'),
        ]

    records = []
    for code, name in codes:
        print(f'处理: {name} ({code})...')
        try:
            rec = export_training_record(code, 'daily')
            records.append(rec)
            print(f'  → {rec["meta"]["n_bars"]} bars, {rec["meta"]["n_bis"]} bis, {rec["meta"]["n_segments"]} segments')
        except Exception as e:
            print(f'  ❌ 错误: {e}')

    output = {
        'format_version': '1.0',
        'description': '缠论结构标注数据集 (Teacher: Claude / Student target)',
        'n_records': len(records),
        'total_bars': sum(r['meta']['n_bars'] for r in records),
        'total_bis': sum(r['meta']['n_bis'] for r in records),
        'records': records,
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'\n数据集已保存: {output_file}')
    return output
# ════════════════════════════════════════════

def test_all():
    """多指数测试"""
    test_codes = [
        ('sh000001', '上证指数'),
        ('sz399006', '创业板指'),
        ('sz159740', '恒生科技'),
        ('sh880373', '半导体板块'),
        ('sh880558', '白酒板块'),
    ]

    for code, name in test_codes:
        print('\n' + '=' * 60)
        print(f'{name} ({code})')
        print('=' * 60)
        try:
            parser = parse_chanlun(code, name=name)
            parser.print_summary()
        except Exception as e:
            print(f'  ❌ 错误: {e}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='缠论层次化解析器')
    parser.add_argument('--code', default='sh000001', help='标的代码')
    parser.add_argument('--name', default='', help='标的名称')
    parser.add_argument('--period', default='daily', help='周期')
    parser.add_argument('--narrate', action='store_true', help='输出走势叙述')
    parser.add_argument('--json', action='store_true', help='输出JSON')
    parser.add_argument('--summary', action='store_true', help='输出概览')
    parser.add_argument('--verify', action='store_true', help='验证包含处理')
    parser.add_argument('--test-all', action='store_true', help='多指数测试')
    parser.add_argument('--vs-czsc', action='store_true', help='对比CZSC笔数')
    parser.add_argument('--candidates', action='store_true', help='枚举线段候选，供LLM判断')
    parser.add_argument('--judge', default='', help='应用LLM判断结果(JSON文件路径)')
    parser.add_argument('--export-dataset', default='', nargs='?', const='training_data/chanlun_dataset.json',
                        help='导出训练数据集(默认training_data/chanlun_dataset.json)')

    args = parser.parse_args()

    if args.export_dataset:
        export_dataset(output_file=args.export_dataset)
    elif args.test_all:
        test_all()
    elif args.verify:
        verify_containment(args.code)
    elif args.vs_czsc:
        verify_bi_vs_czsc(args.code)
    elif args.narrate:
        print(narrate_chanlun(args.code, args.name, args.period))
    elif args.candidates or args.judge:
        parser_obj = parse_chanlun(args.code, args.period, args.name or args.code)
        cands = segment_candidates(parser_obj.result['bis'])
        print(f'{args.code} ({args.name or args.code})')
        print(f'笔数: {len(parser_obj.result["bis"])}')
        print(f'线段候选: {len(cands)}个')
        print()
        if args.candidates:
            print(format_judge_prompt(cands, parser_obj.result['bis']))
        elif args.judge:
            segments = apply_judgments(cands, args.judge, parser_obj.result['bis'])
            parser_obj.result['segments'] = segments
            parser_obj.result['trend_type'], parser_obj.result['zhongshu'] = classify_trend_type(
                [b for seg in segments for b in seg['bi_list']])
            print(f'LLM判断后: {len(segments)}段')
            parser_obj.print_summary()
            print()
            print(parser_obj.narrate())
    else:
        p = parse_chanlun(args.code, args.period, args.name or args.code)
        if args.json:
            print(p.to_json())
        else:
            p.print_summary()
            print()
            print(p.narrate())

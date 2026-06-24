#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
3根K线原语分类 — 缠论语义视角

背景:
  小潘潘与小草的对话推导过程：
  第一步：单根K线OCHL的12种结构（一字/T字/倒T/十字/阳线4种/阴线4种）
  第二步：三根可重复无序组合 = C(12+3-1,3) = 364种
  第三步：364≠缠论语义。缠论关心的3K线分类仅7类左右。

本脚本：从缠论视角对真实行情的3-bar窗口做分类统计
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
import math
from collections import defaultdict

# ── 单根K线12种形态分类 ──

def classify_single_bar(o, h, l, c):
    """
    单根K线的12种形态分类

    特殊线4种:
      一字线  — O=H=L=C
      T字线   — O≈C≈L < H
      倒T字线  — O≈C≈H > L
      十字星   — O≈C, H > O > L
    阳线4种:
      无影    — O=L, C=H
      上影    — O=L, C<H
      下影    — O>L, C=H
      上下影  — O>L, C<H
    阴线4种(对称):
      无影    — O=H, C=L
      上影    — O=H, C>L
      下影    — O<H, C=L
      上下影  — O<H, C>L
    """
    # 容差处理 (避免浮点误差)
    eps = (h - l) * 0.01 if (h - l) > 0 else 1e-8

    is_bodyless = abs(o - c) <= eps  # 十字星/一字
    is_upper_shadow = abs(h - max(o, c)) > eps
    is_lower_shadow = abs(min(o, c) - l) > eps

    if is_bodyless:
        if is_upper_shadow and is_lower_shadow:
            return "十字星"
        elif is_upper_shadow:
            return "倒T字"
        elif is_lower_shadow:
            return "T字"
        else:
            return "一字线"

    # 有实体的：阳线或阴线
    is_bull = c > o
    if is_bull:
        if not is_upper_shadow and not is_lower_shadow:
            return "阳线无影"
        elif is_upper_shadow and not is_lower_shadow:
            return "阳线上影"
        elif not is_upper_shadow and is_lower_shadow:
            return "阳线下影"
        else:
            return "阳线上下影"
    else:
        if not is_upper_shadow and not is_lower_shadow:
            return "阴线无影"
        elif is_upper_shadow and not is_lower_shadow:
            return "阴线上影"
        elif not is_upper_shadow and is_lower_shadow:
            return "阴线下影"
        else:
            return "阴线上下影"

# ── 缠论视角3K线分类 ──

def classify_3bar_chanlun(b1, b2, b3):
    """
    缠论语义分类：3根K线只回答缠论关心的几个问题

    返回值: (大类, 子类)
    """
    h1, l1 = b1['high'], b1['low']
    h2, l2 = b2['high'], b2['low']
    h3, l3 = b3['high'], b3['low']
    c1, c2, c3 = b1['close'], b2['close'], b3['close']

    # ── 第一判断：分型 ──
    is_top_fenxing = h2 > h1 and h2 > h3
    is_bottom_fenxing = l2 < l1 and l2 < l3

    # ── 第二判断：包含关系 ──
    # B2被B1包含
    contain_b2_in_b1 = h2 <= h1 and l2 >= l1
    # B1被B2包含
    contain_b1_in_b2 = h1 <= h2 and l1 >= l2
    # B3被B2包含
    contain_b3_in_b2 = h3 <= h2 and l3 >= l2
    # B2被B3包含
    contain_b2_in_b3 = h2 <= h3 and l2 >= l3

    # ── 第三判断：方向序列 ──
    is_up_sequence = c1 < c2 < c3
    is_down_sequence = c1 > c2 > c3

    # ── 综合决策 ──
    # 标准分型（不涉及包含的情况下）
    if is_top_fenxing and not contain_b2_in_b1 and not contain_b1_in_b2:
        return ("顶分型", "标准顶分型")
    if is_bottom_fenxing and not contain_b2_in_b1 and not contain_b1_in_b2:
        return ("底分型", "标准底分型")

    # 包含关系（先于方向判断）
    if contain_b2_in_b1 or contain_b1_in_b2:
        # 向上趋势中包含 → 向上处理
        if is_up_sequence:
            return ("包含关系", "向上处理")
        # 向下趋势中 → 向下处理
        elif is_down_sequence:
            return ("包含关系", "向下处理")
        else:
            return ("包含关系", "方向不明需看前文")

    # 简单方向序列（无包含、非分型）
    if is_up_sequence:
        return ("方向序列", "简单上涨")
    if is_down_sequence:
        return ("方向序列", "简单下跌")

    # 「涨跌涨」特殊——你之前讨论的核心
    # C1<C2>C3 或 C1>C2<C3 等转折形态
    if c1 < c2 > c3:
        # B3收盘相对B1
        if c3 > c1:
            return ("转折试探", "涨跌涨_吸收")
        elif c3 < c1:
            return ("转折试探", "涨跌涨_假突破")
        else:
            return ("转折试探", "涨跌涨_平")
    if c1 > c2 < c3:
        if c3 < c1:
            return ("转折试探", "跌涨跌_诱多")
        elif c3 > c1:
            return ("转折试探", "跌涨跌_反转")
        else:
            return ("转折试探", "跌涨跌_平")

    # 其他
    return ("其他", "复杂形态")


def load_bars(code="sz159740", count=500):
    """从日线信号CSV读取最近N根bar"""
    csv_path = f"signals/tracking/{code}/daily_signals.csv"
    if not os.path.exists(csv_path):
        return None
    import pandas as pd
    df = pd.read_csv(csv_path)
    cols = [c for c in ['open','high','low','close','date'] if c in df.columns]
    if len(cols) < 4:
        return None
    return df[cols].tail(count)


def main():
    print("=" * 70)
    print("3根K线原语 — 缠论语义分类")
    print("=" * 70)

    # ── 1. 单根12种 ──
    print("\n┌─ 第一步：单根K线12种形态 ──────────────────────┐")
    print("  特殊线4种: 一字线 / T字 / 倒T字 / 十字星")
    print("  阳线4种:   无影 / 上影 / 下影 / 上下影")
    print("  阴线4种:   无影 / 上影 / 下影 / 上下影")
    print(f"  三根无序可重复组合: C(12+3-1,3) = C(14,3) = {math.comb(14, 3)}")
    print(f"  三根有序可重复组合: 12³ = {12**3}（数学上有意义，缠论语义无意义）")

    # ── 2. 真实数据分类 ──
    print("\n┌─ 第二步：缠论语义分类 × 真实行情 ──────────────┐")

    try:
        df = load_bars()
        if df is None:
            df = load_bars("sh000001")
        if df is None:
            df = load_bars("sz399006")
        if df is None:
            print("  [跳过] 找不到信号CSV\n")
            return

        print(f"\n  数据源: sz159740 | 最近{len(df)}根日线\n")
        chanlun_categories = defaultdict(int)
        chanlun_sub = defaultdict(int)
        single_type_counts = defaultdict(int)

        # 单根形态分布
        for i in range(len(df)):
            b = df.iloc[i]
            s = classify_single_bar(b['open'], b['high'], b['low'], b['close'])
            single_type_counts[s] += 1

        print(f"  【单根形态分布】({sum(single_type_counts.values())}根)")
        for t, cnt in sorted(single_type_counts.items(), key=lambda x: -x[1]):
            print(f"    {t}: {cnt}根 ({cnt/sum(single_type_counts.values())*100:.1f}%)")

        # 3K线缠论分类
        for i in range(len(df) - 2):
            b1, b2, b3 = df.iloc[i], df.iloc[i+1], df.iloc[i+2]
            cat, sub = classify_3bar_chanlun(b1, b2, b3)
            chanlun_categories[cat] += 1
            chanlun_sub[sub] += 1

        total_3bar = sum(chanlun_categories.values())
        print(f"\n  【缠论语义分类】({total_3bar}个3K线窗口)")
        for cat, cnt in sorted(chanlun_categories.items(), key=lambda x: -x[1]):
            pct = cnt / total_3bar * 100
            print(f"    {cat}: {cnt}次 ({pct:.1f}%)")

        print(f"\n  【子类明细】")
        for sub, cnt in sorted(chanlun_sub.items(), key=lambda x: -x[1]):
            pct = cnt / total_3bar * 100
            print(f"    {sub}: {cnt}次 ({pct:.1f}%)")

    except Exception as e:
        print(f"  [错误] {e}")

    # ── 3. 结论 ──
    print("\n" + "=" * 70)
    print("结论")
    print("=" * 70)
    print("""
  1. 单根K线 = 12种（含特殊4+阳4+阴4），不多不少
  2. 三根无序可重复组合 = C(14,3) = 364，纯数学答案
  3. 三根有序12³=1728，数学真理但交易语义=0
  4. 缠论语义分类 ≈ 7大类，这才是交易者关心的
  5. 下一步：跑遍多个周期/标的后，这7类分布基本稳定
""")

if __name__ == "__main__":
    main()

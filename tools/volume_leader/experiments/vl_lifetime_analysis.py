# -*- coding: utf-8 -*-
"""
VL宇宙生命周期分析 — 逐月重构 Top50 (2026-01 ~ 2026-05)

问题:
  1. 多少股票是"短暂辉煌"（进来1-2月就淘汰）?
  2. 多少是"进出反复"（出去又回来又出去）?
  3. 多少是"常青树"（持续霸榜）?
  4. 各组有何特征？

方法:
  逐月用和VL screener相同的标准重建Top50，追踪每个股票在各月的
  出现/消失/重现，再分析各组特征。

输出:
  终端表格 + JSON 保存
"""

import struct
import os
import json
import sys
from datetime import datetime
from collections import defaultdict

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)
sys.path.insert(0, os.path.join(_script_dir, '..'))
sys.path.insert(0, os.path.join(_script_dir, '../..'))
sys.path.insert(0, os.path.join(_script_dir, '../../..'))

import config

# ===== 配置 =====
TDX_VIPDOC = os.path.join(config.TDX_ROOT, 'vipdoc')
PRICE_COEF = 0.01          # A股价格系数
VOLUME_COEF = 0.01
DAY_PRICE_COEF = 1000      # TDX原始除系数（不是0.01，原始/1000）

# 月份定义: (年月, 截止日期)
MONTHS = [
    (202601, '2026-01-29'),
    (202602, '2026-02-13'),
    (202603, '2026-03-31'),
    (202604, '2026-04-30'),
    (202605, '2026-05-29'),
]
MONTH_LABELS = ['1月', '2月', '3月', '4月', '5月']

# ① 加载当前宇宙名称映射 + stock_names.csv
def _load_names():
    names = {}
    names_csv = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking', 'stock_names.csv')
    if os.path.exists(names_csv):
        import pandas as pd
        df = pd.read_csv(names_csv, encoding='utf-8', dtype=str)
        for _, r in df.iterrows():
            names[r['code']] = r.get('name', '')
    names.update(dict(config.NAME_MAP))
    return names

NAME_MAP = _load_names()

def get_name(code_label):
    if code_label in NAME_MAP:
        return NAME_MAP[code_label]
    return code_label

def _is_real_stock(exchange, code):
    """和 screener 一致的股票过滤"""
    if exchange == 'sh' and code.startswith('60'):
        return True
    elif exchange == 'sz':
        if code.startswith('00') or code.startswith('30'):
            if not code.startswith('39') and not code.startswith('15') and not code.startswith('16'):
                return True
    return False


def _read_full_day_file(filepath):
    """读取完整 .day 文件，返回所有 bar 的 list"""
    bars = []
    try:
        size = os.path.getsize(filepath)
        if size < 32:
            return bars
        with open(filepath, 'rb') as f:
            raw = f.read()
        for i in range(0, len(raw), 32):
            rec = struct.unpack_from('<IIIIIfII', raw, i)
            date_int = int(rec[0])
            bars.append({
                'date': date_int,
                'date_str': str(date_int),
                'open': rec[1] / DAY_PRICE_COEF,
                'high': rec[2] / DAY_PRICE_COEF,
                'low': rec[3] / DAY_PRICE_COEF,
                'close': rec[4] / DAY_PRICE_COEF,
                'amount': rec[5],                     # 原始成交额(元)
                'volume': int(rec[6] * VOLUME_COEF),
            })
    except Exception:
        pass
    return bars


def scan_all_stocks_full():
    """扫描所有A股，返回每个股票的全部bars"""
    all_stocks = {}
    for exchange in ['sh', 'sz']:
        lday_dir = os.path.join(TDX_VIPDOC, exchange, 'lday')
        if not os.path.isdir(lday_dir):
            continue
        for fname in os.listdir(lday_dir):
            if not fname.endswith('.day'):
                continue
            code = fname[2:8]
            if not _is_real_stock(exchange, code):
                continue
            fpath = os.path.join(lday_dir, fname)
            bars = _read_full_day_file(fpath)
            if not bars:
                continue
            label = f'{exchange}{code}'
            all_stocks[label] = bars
    return all_stocks


def get_monthly_top50(all_stocks, month_int, cutoff_date_str):
    """
    对某个月份，获取VL Top50。
    条件:
      - 当月有交易数据（截止日前后）
      - 当月最后一根bar的close距全历史最高 ≤ 20%
      - 按当月最后一根bar的amount排序，取Top50
    """
    candidates = []

    for label, bars in all_stocks.items():
        # 找到当月截止日或之前最近的 bar
        target = int(cutoff_date_str.replace('-', ''))
        month_bars = [b for b in bars if b['date'] <= target]

        if not month_bars:
            continue

        last_bar = month_bars[-1]  # 当月最后一根

        # 计算全历史最高收盘价
        all_close = [b['close'] for b in bars if b['close'] > 0]
        if not all_close:
            continue
        ath_close = max(all_close)

        # 距历史最高 ≤ 20%
        pct_from_ath = (ath_close - last_bar['close']) / ath_close * 100
        if pct_from_ath > 20:
            continue

        # 成交额必须为正
        if last_bar['amount'] <= 0:
            continue

        candidates.append({
            'code': label,
            'close': last_bar['close'],
            'amount': last_bar['amount'],
            'ath_close': ath_close,
            'pct_from_ath': round(pct_from_ath, 2),
        })

    # 按成交额降序
    candidates.sort(key=lambda x: x['amount'], reverse=True)
    return candidates[:50]


def analyze():
    print("=" * 90)
    print("VL宇宙生命周期分析：逐月重构 Top50 (2026-01 ~ 2026-05)")
    print("=" * 90)
    print()

    print("[1/3] 扫描全A股.day文件...")
    all_stocks = scan_all_stocks_full()
    print(f"  共 {len(all_stocks)} 只真实A股")
    print()

    # 逐月 Top50
    print("[2/3] 逐月重建 Top50...")
    monthly_top50 = {}   # month -> list of code_labels
    monthly_details = {} # month -> list of detail dicts

    for (month_int, cutoff_str), label in zip(MONTHS, MONTH_LABELS):
        top50 = get_monthly_top50(all_stocks, month_int, cutoff_str)
        codes = [t['code'] for t in top50]
        monthly_top50[month_int] = set(codes)
        monthly_details[month_int] = top50
        print(f"  {label} Top50: {len(top50)} 只 (截止{cutoff_str})")
        # 打印前5
        for i, t in enumerate(top50[:5]):
            print(f"    #{i+1} {t['code']} 成交额{t['amount']/1e8:.1f}亿 距ATH:{t['pct_from_ath']:.1f}%")
    print()

    # ③ 会员卡分析
    all_codes = set()
    for codes in monthly_top50.values():
        all_codes.update(codes)
    print(f"  所有曾出现在月Top50的标的: {len(all_codes)} 只")
    print()

    # 构建每个股票的出现矩阵
    membership = {}
    for code in all_codes:
        record = {}
        for month_int, label in zip(MONTHS, MONTH_LABELS):
            month_val, _ = month_int
            record[label] = code in monthly_top50[month_val]
        membership[code] = record

    # 分类

    # 常青树: 在 ≥4 个月出现
    evergreen = []
    # 稳定股: 正好 3 个月出现
    stable = []
    # 进出反复: 出现-消失-再出现 (有gap)
    oscillating = []
    # 短暂辉煌: 只出现 1-2 个月，且是连续的早期
    brief_glory = []
    # 新来者: 只在 5 月出现
    newcomer = []

    for code, record in membership.items():
        months_present = [label for label in MONTH_LABELS if record[label]]
        n = len(months_present)

        # 检测gap: 相邻出现的月份之间是否有跳过
        indices = [i for i, label in enumerate(MONTH_LABELS) if record[label]]
        has_gap = False
        if len(indices) > 1:
            for i in range(1, len(indices)):
                if indices[i] - indices[i-1] > 1:
                    has_gap = True
                    break

        # 只在5月出现
        if n == 1 and record.get('5月', False):
            newcomer.append(code)
        # 只在1-4月某个月出现(不在5月)
        elif n == 1 and not record.get('5月', False):
            brief_glory.append(code)
        elif n == 2:
            # 连续两个月早期出现
            if indices == [0, 1] or indices == [1, 2] or indices == [2, 3]:
                brief_glory.append(code)
            # 包含5月 + 前一个月
            elif indices == [3, 4] or indices == [2, 4] or indices == [1, 4]:
                if has_gap:
                    oscillating.append(code)
                else:
                    newcomer.append(code)  # 4+5月连续，算新来者
            else:
                oscillating.append(code)
        elif n >= 4:
            evergreen.append(code)
        elif n == 3:
            if has_gap:
                oscillating.append(code)
            else:
                stable.append(code)

    # 去重检查
    all_classified = set(evergreen) | set(stable) | set(oscillating) | set(brief_glory) | set(newcomer)
    unclassified = all_codes - all_classified
    if unclassified:
        # 归到最近的类别
        for code in unclassified:
            n = sum(1 for label in MONTH_LABELS if membership[code][label])
            if n <= 2:
                brief_glory.append(code)
            elif n == 3:
                stable.append(code)
            else:
                evergreen.append(code)

    # 保存JSON
    output = {
        'evergreen': evergreen,
        'stable': stable,
        'oscillating': oscillating,
        'brief_glory': brief_glory,
        'newcomer': newcomer,
        'monthly_top50': {
            label: [t['code'] for t in monthly_details[m[0]]]
            for label, m in zip(MONTH_LABELS, MONTHS)
        },
        'monthly_details': {
            label: monthly_details[m[0]]
            for label, m in zip(MONTH_LABELS, MONTHS)
        }
    }

    # ④ 输出结果
    def print_group(title, codes, detail=False):
        print(f"  {title} ({len(codes)}只):")
        for code in sorted(codes)[:20]:  # 最多显示20个
            months = [label for label in MONTH_LABELS if membership.get(code, {}).get(label, False)]
            name = get_name(code)
            n = len(months)
            label = f"出现{n}个月: {''.join(months)}"
            if name != code:
                print(f"    {code}({name}) — {label}")
            else:
                print(f"    {code} — {label}")
        if len(codes) > 20:
            print(f"    ... 还有{len(codes)-20}只")
        print()

    print("=" * 90)
    print("[3/3] 生命周期分类结果")
    print("=" * 90)
    print()

    print_group("[常青树] (>=4个月)", evergreen)
    print_group("[稳定股] (连续3个月)", stable)
    print_group("[进出反复] (有断档)", oscillating)
    print_group("[短暂辉煌] (1-2个月早期)", brief_glory)
    print_group("[新来者] (仅5月)", newcomer)

    # ⑤ 交叉特征分析
    print("=" * 90)
    print("各组特征对比")
    print("=" * 90)
    print()

    # 成交量特征：各组平均成交额、距ATH距离、行业分布
    for group_name, codes in [
        ('常青树', evergreen),
        ('稳定股', stable),
        ('进出反复', oscillating),
        ('短暂辉煌', brief_glory),
        ('新来者', newcomer),
    ]:
        if not codes:
            continue
        # 取这些股票在5月的排名数据
        may_data = {t['code']: t for t in monthly_details.get(202605, [])}
        avg_amount = 0
        avg_ath_pct = 0
        n_in_may = 0
        for code in codes:
            if code in may_data:
                avg_amount += may_data[code]['amount']
                avg_ath_pct += may_data[code]['pct_from_ath']
                n_in_may += 1
        if n_in_may > 0:
            avg_amount = avg_amount / n_in_may / 1e8
            avg_ath_pct = avg_ath_pct / n_in_may
        else:
            avg_amount = 0
            avg_ath_pct = 0

        print(f"  {group_name}:")
        print(f"    5月仍在榜: {n_in_may}/{len(codes)}")
        print(f"    平均成交额: {avg_amount:.1f}亿")
        print(f"    平均距ATH: {avg_ath_pct:.1f}%")

        # 成交额变化趋势：从1月到5月
        for m_label, m_int in zip(MONTH_LABELS, [m[0] for m in MONTHS]):
            if m_int in monthly_details:
                in_month = sum(1 for t in monthly_details[m_int] if t['code'] in codes)
                print(f"    {m_label}在Top50中: {in_month}只")
        print()

    # 行业分布概览
    print("=" * 90)
    print("行业特征（按代码前缀粗略分组）")
    print("=" * 90)
    sectors = {
        'sh60x': ('沪市主板消费/制造', lambda c: c.startswith('sh600') or c.startswith('sh601')),
        'sh68x': ('科创板', lambda c: c.startswith('sh688')),
        'sz00x': ('深市主板', lambda c: c.startswith('sz00')),
        'sz30x': ('创业板', lambda c: c.startswith('sz30')),
    }

    for group_name, codes in [
        ('常青树', evergreen),
        ('新来者', newcomer),
        ('短暂辉煌', brief_glory),
    ]:
        if not codes:
            continue
        print(f"\n  {group_name}:")
        for sect_name, (sect_label, check_fn) in sectors.items():
            count = sum(1 for c in codes if check_fn(c))
            if count > 0:
                pct = count / len(codes) * 100
                print(f"    {sect_label}: {count}/{len(codes)} ({pct:.0f}%)")

    # 保存结果
    output_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'outputs', 'vl_lifetime_analysis.json'
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    # Build membership matrix per stock
    membership_matrix = {}
    for code in all_codes:
        membership_matrix[code] = {label: code in monthly_top50[m[0]] for label, m in zip(MONTH_LABELS, MONTHS)}

    # 简化保存（去掉了完整详情以减少体积）
    save_data = {
        'evergreen': evergreen,
        'stable': stable,
        'oscillating': oscillating,
        'brief_glory': brief_glory,
        'newcomer': newcomer,
        'monthly_top50': {
            label: list(monthly_top50[m[0]])
            for label, m in zip(MONTH_LABELS, MONTHS)
        },
        'monthly_top50_counts': {
            label: len(monthly_top50[m[0]])
            for label, m in zip(MONTH_LABELS, MONTHS)
        },
        'membership': membership_matrix,
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存: {output_path}")

    return membership, monthly_details


if __name__ == '__main__':
    membership, monthly_details = analyze()

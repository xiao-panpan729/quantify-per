# -*- coding: utf-8 -*-
"""
VL宇宙生命周期分析 — 第二层：深度特征分析

读取第一层结果 vl_lifetime_analysis.json，追加：
  1. 淘汰组回本/死亡追踪
  2. 行业分布（按名称关键词）
  3. 各组在入围期间的收益率
  4. 进出反复的完整路径
"""

import struct
import os
import json
import sys
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../..'))

import config

TDX_VIPDOC = os.path.join(config.TDX_ROOT, 'vipdoc')
DAY_PRICE_COEF = 1000
PRICE_COEF = 0.01

# 行业关键词映射
SECTOR_KEYWORDS = {
    '半导体/AI': ['半导体', '芯片', '微电', '集成', '光电', '光通信', '光模块', '算力', 'AI', '智能', '信息', '软件', '互联', '电子', '科创', '中科', '华工'],
    '新能源/光伏': ['新能源', '光伏', '锂电', '电池', '能源', '硅', '风光', '电力', '特变', '天合', '隆基'],
    '资源/有色': ['黄金', '有色', '稀土', '矿业', '金属', '铜', '铝', '铅', '锌', '钨', '钼', '锂', '钴', '煤', '钢铁', '钢铁'],
    '军工/航天': ['军工', '航天', '航空', '船舶', '兵器', '电科', '中航', '航发', '卫星'],
    '医药/消费': ['医药', '医疗', '药', '生物', '食品', '饮料', '消费', '白酒', '乳业', '家电', '汽车'],
    '金融': ['银行', '证券', '保险', '金融', '信托', '中国平安', '工商', '建设', '农业', '招商'],
    '通信/5G': ['通信', '5G', '移动', '联通', '电信', '中兴', '烽火', '光纤', '亨通', '中天'],
    '机械/制造': ['机械', '装备', '制造', '工业', '自动化', '电气', '电网', '思源', '特变', '西电'],
    '石油/化工': ['石油', '化工', '石化', '化学', '化纤', '中海油', '中石油'],
    '传统周期': ['水泥', '建材', '建筑', '基建', '地产', '房产', '建工', '交建', '中铁', '铁建'],
}

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

def get_name(code):
    if code in NAME_MAP:
        return NAME_MAP[code]
    return code

def guess_sector(code):
    """根据名称关键词猜测行业"""
    name = get_name(code)
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                return sector
    # 代码前缀启发
    if code.startswith('sh60'):
        return '沪市主板(其他)'
    elif code.startswith('sz00'):
        return '深市主板(其他)'
    elif code.startswith('sz30'):
        return '创业板(其他)'
    return '其他'


def read_full_day_file(filepath):
    """读取完整 .day 文件"""
    bars = []
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        for i in range(0, len(raw), 32):
            rec = struct.unpack_from('<IIIIIfII', raw, i)
            date_int = int(rec[0])
            bars.append({
                'date': date_int,
                'date_str': str(date_int),
                'close': rec[4] / DAY_PRICE_COEF,
            })
    except Exception:
        pass
    return bars


def analyze():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
    with open(os.path.join(output_dir, 'vl_lifetime_analysis.json'), 'r', encoding='utf-8') as f:
        result = json.load(f)

    groups = {
        '常青树': result['evergreen'],
        '稳定股': result['stable'],
        '进出反复': result['oscillating'],
        '短暂辉煌': result['brief_glory'],
        '新来者': result['newcomer'],
    }

    print("=" * 90)
    print("深度分析一：行业分布")
    print("=" * 90)

    # 行业占比
    all_sectors = defaultdict(lambda: defaultdict(int))
    for group_name, codes in groups.items():
        for code in codes:
            sector = guess_sector(code)
            all_sectors[group_name][sector] += 1

    for group_name in ['常青树', '稳定股', '进出反复', '短暂辉煌', '新来者']:
        sectors = all_sectors[group_name]
        total = sum(sectors.values())
        if total == 0:
            continue
        print(f"\n  [{group_name}] ({total}只):")
        # 按数量排序
        for sector, count in sorted(sectors.items(), key=lambda x: -x[1])[:5]:
            pct = count / total * 100
            print(f"    {sector}: {count}只 ({pct:.0f}%)")

    # ===== 第二层：淘汰组回本追踪 =====
    print()
    print("=" * 90)
    print("深度分析二：淘汰组回本/死亡追踪")
    print("=" * 90)
    print()
    print("  (扫描所有被淘汰股票的全量数据，追踪后续表现)")
    print()

    # 被淘汰组：短暂辉煌 + 稳定股进不了5月的 + 常青树进不了5月的
    brief_codes = set(result['brief_glory'])
    # 哪些稳定股/常青树被淘汰了?
    stable_set = set(result['stable'])
    evergreen_set = set(result['evergreen'])
    newcomer_set = set(result['newcomer'])

    may_set = set(result['monthly_top50'].get('5月', []))

    eliminated = brief_codes | (stable_set - may_set) | (evergreen_set - may_set)

    print(f"  淘汰组: {len(eliminated)} 只")
    print(f"  其中短暂辉煌: {len(brief_codes & eliminated)} 只")
    print()

    # 读取这些股票的全部日线数据，追踪被淘汰后的表现
    # 对每个淘汰股：从它最后出现的月份之后开始，跟踪到现在的最大回撤和回本情况
    month_cutoffs = {
        '1月': 20260129,
        '2月': 20260213,
        '3月': 20260331,
        '4月': 20260430,
        '5月': 20260529,
    }

    # 确定每个淘汰股的最后出现月份
    monthly = result['monthly_top50']
    last_month_of = {}
    for code in eliminated:
        last_month = None
        for label in ['1月', '2月', '3月', '4月', '5月']:
            if label in monthly and code in monthly[label]:
                last_month = label
        if last_month:
            last_month_of[code] = last_month

    # 扫描TDX文件追踪
    recovery_data = {}
    for exchange in ['sh', 'sz']:
        lday_dir = os.path.join(TDX_VIPDOC, exchange, 'lday')
        if not os.path.isdir(lday_dir):
            continue
        for fname in os.listdir(lday_dir):
            if not fname.endswith('.day'):
                continue
            code = fname[2:8]
            label = f'{exchange}{code}'
            if label not in eliminated:
                continue

            bars = read_full_day_file(os.path.join(lday_dir, fname))
            if not bars:
                continue

            last_month = last_month_of.get(label)
            if not last_month:
                continue

            cutoff = month_cutoffs[last_month]

            # 找到淘汰时间点的价格
            before_bars = [b for b in bars if b['date'] <= cutoff]
            after_bars = [b for b in bars if b['date'] > cutoff]

            if not before_bars:
                continue

            exit_close = before_bars[-1]['close']

            if not after_bars:
                recovery_data[label] = {
                    'exit_close': exit_close,
                    'current_close': exit_close,
                    'max_drawdown': 0,
                    'recovered': False,
                    'dead': False,
                    'n_days_tracked': 0,
                    'final_return': 0,
                }
                continue

            # 追踪后续
            max_drawdown = 0
            recovered = False
            recovery_days = None

            for i, bar in enumerate(after_bars):
                ret = (bar['close'] - exit_close) / exit_close * 100
                if ret < max_drawdown:
                    max_drawdown = ret
                if bar['close'] >= exit_close and not recovered:
                    recovered = True
                    recovery_days = i + 1

            current_close = after_bars[-1]['close']
            final_return = (current_close - exit_close) / exit_close * 100
            dead = not recovered and max_drawdown < -25

            recovery_data[label] = {
                'exit_close': exit_close,
                'current_close': current_close,
                'max_drawdown': round(max_drawdown, 2),
                'recovered': recovered,
                'recovery_days': recovery_days,
                'dead': dead,
                'n_days_tracked': len(after_bars),
                'final_return': round(final_return, 2),
            }

    print(f"  成功追踪: {len(recovery_data)} 只")
    print()

    # 统计
    n_recovered = sum(1 for d in recovery_data.values() if d['recovered'])
    n_dead = sum(1 for d in recovery_data.values() if d['dead'])
    n_unrecovered = sum(1 for d in recovery_data.values() if not d['recovered'])

    recovered_list = [c for c, d in recovery_data.items() if d['recovered']]
    dead_list = [c for c, d in recovery_data.items() if d['dead']]
    unrecovered_list = [c for c, d in recovery_data.items() if not d['recovered']]

    avg_max_dd = sum(d['max_drawdown'] for d in recovery_data.values()) / len(recovery_data) if recovery_data else 0
    avg_final_ret = sum(d['final_return'] for d in recovery_data.values()) / len(recovery_data) if recovery_data else 0

    print(f"  回本率: {n_recovered}/{len(recovery_data)} ({n_recovered/len(recovery_data)*100:.1f}%)")
    print(f"  真死亡(<-25%且未回本): {n_dead}/{len(recovery_data)} ({n_dead/len(recovery_data)*100:.1f}%)")
    print(f"  未回本(含死亡): {n_unrecovered}/{len(recovery_data)} ({n_unrecovered/len(recovery_data)*100:.1f}%)")
    print(f"  平均最大回撤: {avg_max_dd:.2f}%")
    print(f"  平均最终收益: {avg_final_ret:.2f}%")
    print()

    if recovered_list:
        avg_recovery_days = sum(d['recovery_days'] for d in recovery_data.values() if d['recovered'] and d['recovery_days']) / n_recovered
        print(f"  回本平均耗时: {avg_recovery_days:.0f} 个交易日")

    print()

    # 真死亡名单
    if dead_list:
        print("  【真死亡名单】(最大回撤<-25%且未回本):")
        for code in sorted(dead_list)[:20]:
            d = recovery_data[code]
            name = get_name(code)
            sector = guess_sector(code)
            print(f"    {code}({name}) 行业:{sector} 最大回撤:{d['max_drawdown']:.1f}% 最终收益:{d['final_return']:.1f}%")
        if len(dead_list) > 20:
            print(f"    ... 还有{len(dead_list)-20}只")
    print()

    # 不同类别的淘汰表现
    print("=" * 90)
    print("深度分析三：各类别淘汰组表现对比")
    print("=" * 90)
    print()

    for group_name in ['短暂辉煌', '稳定股', '常青树']:
        codes = groups[group_name]
        group_recovery = {c: d for c, d in recovery_data.items() if c in codes}
        if not group_recovery:
            continue
        n = len(group_recovery)
        n_rec = sum(1 for d in group_recovery.values() if d['recovered'])
        n_dead = sum(1 for d in group_recovery.values() if d['dead'])
        avg_dd = sum(d['max_drawdown'] for d in group_recovery.values()) / n
        avg_ret = sum(d['final_return'] for d in group_recovery.values()) / n
        print(f"  [{group_name}] {n}只淘汰:")
        print(f"    回本率: {n_rec}/{n} ({n_rec/n*100:.1f}%)")
        print(f"    死亡率: {n_dead}/{n} ({n_dead/n*100:.1f}%)")
        print(f"    平均最大回撤: {avg_dd:.2f}%")
        print(f"    平均最终收益: {avg_ret:.2f}%")
        print()

    # ===== 第三层：进出反复的完整路径 =====
    print("=" * 90)
    print("深度分析四：进出反复股的完整路径")
    print("=" * 90)
    print()

    for code in sorted(result['oscillating']):
        name = get_name(code)
        months = []
        for label in ['1月', '2月', '3月', '4月', '5月']:
            if label in monthly and code in monthly[label]:
                months.append(label)
        sector = guess_sector(code)
        d = recovery_data.get(code, {})
        ret_str = f"最终收益:{d.get('final_return','?')}%" if d else "无追踪数据"
        print(f"  {code}({name}) [{sector}]")
        print(f"    路径: {' → '.join(months)}")
        if d:
            print(f"    最大回撤: {d.get('max_drawdown','?'):.1f}% | 回本:{'是' if d.get('recovered') else '否'} | {ret_str}")
        print()

    # ===== 第四层：五月新人深度分析 =====
    print("=" * 90)
    print("深度分析五：5月新来者 — 它们从哪里来？")
    print("=" * 90)
    print()

    for code in sorted(result['newcomer']):
        name = get_name(code)
        sector = guess_sector(code)
        d = recovery_data.get(code)
        print(f"  {code}({name}) [{sector}]")

    print()

    # 主题总结
    print("=" * 90)
    print("主题迁移分析")
    print("=" * 90)
    print()

    # 每月Top50的行业分布
    print("  每月Top50的行业构成变化:")
    print(f"  {'月份':<8} {'半导体/AI':<14} {'资源/有色':<14} {'新能源':<14} {'金融':<14} {'军工':<14}")
    for label in ['1月', '2月', '3月', '4月', '5月']:
        if label not in monthly:
            continue
        codes = monthly[label]
        sector_counts = defaultdict(int)
        for code in codes:
            s = guess_sector(code)
            sector_counts[s] += 1
        semi = sector_counts.get('半导体/AI', 0)
        res = sector_counts.get('资源/有色', 0)
        new_e = sector_counts.get('新能源/光伏', 0)
        fin = sector_counts.get('金融', 0)
        mil = sector_counts.get('军工/航天', 0)
        print(f"  {label:<8} {semi:<14} {res:<14} {new_e:<14} {fin:<14} {mil:<14}")

    # 保存深度分析结果
    deep_output = {
        'recovery_stats': {
            'total_tracked': len(recovery_data),
            'recovered': n_recovered,
            'recovery_rate': round(n_recovered/len(recovery_data)*100, 1) if recovery_data else 0,
            'dead': n_dead,
            'death_rate': round(n_dead/len(recovery_data)*100, 1) if recovery_data else 0,
            'avg_max_drawdown': round(avg_max_dd, 2),
            'avg_final_return': round(avg_final_ret, 2),
        },
        'dead_list': sorted(dead_list),
        'oscillating_paths': {
            code: {
                'name': get_name(code),
                'path': [label for label in ['1月','2月','3月','4月','5月']
                        if label in monthly and code in monthly[label]]
            }
            for code in result['oscillating']
        },
    }
    with open(os.path.join(output_dir, 'vl_lifetime_deep.json'), 'w', encoding='utf-8') as f:
        json.dump(deep_output, f, ensure_ascii=False, indent=2)
    print(f"\n  深度分析已保存: {output_dir}/vl_lifetime_deep.json")


if __name__ == '__main__':
    analyze()

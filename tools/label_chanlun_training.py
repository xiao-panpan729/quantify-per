"""标注daily_signals.csv: 缠论结构标签 + 趋势状态 + 输出Kronos训练数据"""
import sys, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
# 项目根目录 + tmp_kronos
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp_kronos'))
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np
from pathlib import Path
from notebook.chanlun.adapter import df_to_bars
from notebook.chanlun.signals import get_zs_info
from czsc import CZSC, Freq

# ── 配置 ──
TRACKED_SYMBOLS = [
    'sh000001', 'sz399006', 'sz159740', 'sh520600', 'sh513120',
    'sz159326', 'sh513310', 'sh588200', 'sz002261', 'sz300118',
    'sz000100', 'sz002129', 'sh600438', 'sh601012',
]
DATA_DIR = Path("signals/tracking")
OUTPUT_DIR = Path("training_data")
LABELED_DIR = OUTPUT_DIR / "labeled"
LABELED_DIR.mkdir(parents=True, exist_ok=True)

# 缠论分析最小数据量
MIN_BARS = 200

def analyze_chanlun(df, code):
    """对标的运行缠论分析, 返回每根bar的结构标签"""
    bars = df_to_bars(df, code, Freq.D)
    c = CZSC(bars)

    # 1. 每根bar所属笔方向
    bar_bi = {}
    for bi in c.bi_list:
        sdt_str = str(bi.sdt)[:10]
        edt_str = str(bi.edt)[:10]
        s_idx = df[df['date'] == sdt_str].index
        e_idx = df[df['date'] == edt_str].index
        if len(s_idx) and len(e_idx):
            for idx in range(s_idx[0], e_idx[0] + 1):
                bar_bi[idx] = bi.direction.value

    # 2. 中枢范围 (使用czsc内置的中枢检测)
    bar_in_zs = set()
    zhongshu_list = get_zs_info(c)
    for zs in zhongshu_list:
        sdt_str = str(zs['sdt'])[:10]
        edt_str = str(zs['edt'])[:10]
        zg, zd = zs['zg'], zs['zd']
        s_idx = df[df['date'] == sdt_str].index
        e_idx = df[df['date'] == edt_str].index
        if len(s_idx) and len(e_idx):
            for idx in range(s_idx[0], e_idx[0] + 1):
                close = df.loc[idx, 'close']
                if zd <= close <= zg:
                    bar_in_zs.add(idx)

    # 3. 分型位置 (笔的起止点)
    bi_end_set = set()
    bi_start_set = set()
    for bi in c.bi_list:
        e_idx = df[df['date'] == str(bi.edt)[:10]].index
        if len(e_idx):
            bi_end_set.add(e_idx[0])
        s_idx = df[df['date'] == str(bi.sdt)[:10]].index
        if len(s_idx):
            bi_start_set.add(s_idx[0])

    # 4. 笔的进度位置 (前/中/后段)
    # 每根bar在所属笔中的相对位置
    bar_bi_progress = {}
    for bi in c.bi_list:
        sdt_str = str(bi.sdt)[:10]
        edt_str = str(bi.edt)[:10]
        s_idx = df[df['date'] == sdt_str].index
        e_idx = df[df['date'] == edt_str].index
        if len(s_idx) and len(e_idx):
            bi_len = e_idx[0] - s_idx[0]
            if bi_len > 0:
                for i, idx in enumerate(range(s_idx[0], e_idx[0] + 1)):
                    pct = i / bi_len
                    if pct < 0.3:
                        bar_bi_progress[idx] = 'start'
                    elif pct < 0.7:
                        bar_bi_progress[idx] = 'mid'
                    else:
                        bar_bi_progress[idx] = 'end'

    return bar_bi, bar_in_zs, bi_end_set, bi_start_set, bar_bi_progress


def classify_trend_regime(row):
    """MACD-based 趋势状态分类"""
    dif = row.get('macd_dif', 0)
    hist = row.get('macd_hist', 0)
    # dif/DEA may be string or empty
    try:
        dif = float(dif) if dif != '' and not pd.isna(dif) else 0
        hist = float(hist) if hist != '' and not pd.isna(hist) else 0
    except (ValueError, TypeError):
        dif, hist = 0, 0

    if dif > 0 and hist > 0:
        return '上升趋势'
    elif dif > 0 and hist < 0:
        return '震荡偏多'
    elif dif < 0 and hist > 0:
        return '震荡偏空'
    elif dif < 0 and hist < 0:
        return '下降趋势'
    return '其他'


def normalize_prices(df):
    """将OHLCV转换为相对基准(首根close=1.0)"""
    base = df['close'].iloc[0]
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col] / base
    return df


def label_symbol(code, name):
    """标注一个标的, 返回带标签的DataFrame"""
    csv_path = DATA_DIR / code / "daily_signals.csv"
    if not csv_path.exists():
        print(f"  ⚠ 无数据: {code}")
        return None

    df = pd.read_csv(csv_path)
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    df = df.sort_values('date').reset_index(drop=True)

    if len(df) < MIN_BARS:
        print(f"  ⚠ 数据不足({len(df)}根): {code}")
        return None

    # 缠论分析
    bar_bi, bar_in_zs, bi_end_set, bi_start_set, bar_bi_progress = analyze_chanlun(df, code)
    print(f"  ✓ 缠论: {len(bar_bi)}根有笔标记, {len(bar_in_zs)}根在中枢内")

    # 逐行标注
    labels = []
    for idx in range(len(df)):
        row = df.iloc[idx]
        ld = {
            'symbol': code,
            'symbol_name': name,
            'timestamps': row['date'].strftime('%Y-%m-%d %H:%M:%S'),
            'open': row['open'],
            'high': row['high'],
            'low': row['low'],
            'close': row['close'],
            'volume': row['volume'],
            'amount': row['amount'],
            # 缠论结构
            'bi_direction': bar_bi.get(idx, '无'),
            'in_zhongshu': 1 if idx in bar_in_zs else 0,
            'is_bi_start': 1 if idx in bi_start_set else 0,
            'is_bi_end': 1 if idx in bi_end_set else 0,
            'bi_position': bar_bi_progress.get(idx, '无'),
            # 已有信号
            'star_buy': 1 if row.get('buy_signal', '') == '★买' else 0,
            'star_sell': 1 if row.get('sell_signal', '') == '★卖' else 0,
            'expma_cross': row.get('expma_cross', ''),
            # 趋势状态
            'trend_regime': classify_trend_regime(row),
            'cci': row.get('cci', 0),
        }
        labels.append(ld)

    return pd.DataFrame(labels)


def build_training_csv(all_labeled, output_path):
    """构建Kronos训练用CSV (所有标的拼接, 归一化价格)"""
    rows = []
    for code, df in all_labeled:
        df = df.copy()
        # 归一化: 以该标的第一根close为基准
        base = df['close'].iloc[0]
        for col in ['open', 'high', 'low', 'close']:
            df[col] = df[col] / base
        volume_base = df['volume'].iloc[0] or 1
        df['volume'] = df['volume'] / volume_base
        df['amount'] = df['amount'] / (base * volume_base) if volume_base > 0 else df['amount']
        # 只保留Kronos需要的列
        rows.append(df[['timestamps', 'open', 'high', 'low', 'close', 'volume', 'amount']])

    merged = pd.concat(rows, ignore_index=True)
    merged.to_csv(output_path, index=False)
    print(f"  ✓ 训练数据: {output_path} ({len(merged)}行, {len(all_labeled)}个标的)")


def build_report(all_labeled, report_path):
    """生成标注报告"""
    lines = []
    lines.append("=" * 60)
    lines.append("缠论结构标注报告")
    lines.append("=" * 60)
    lines.append(f"生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    stats = []
    for code, df in all_labeled:
        total = len(df)
        up_bi = (df['bi_direction'] == '向上').sum()
        dn_bi = (df['bi_direction'] == '向下').sum()
        no_bi = (df['bi_direction'] == '无').sum()
        in_zs = df['in_zhongshu'].sum()
        bi_end = df['is_bi_end'].sum()
        bi_start = df['is_bi_start'].sum()
        star_b = df['star_buy'].sum()
        star_s = df['star_sell'].sum()

        regime_dist = df['trend_regime'].value_counts().to_dict()
        regime_str = ' | '.join([f"{k}={v}" for k, v in sorted(regime_dist.items())])

        lines.append(f"\n{'─'*50}")
        lines.append(f"{code} {df.iloc[0]['symbol_name']}")
        lines.append(f"{'─'*50}")
        lines.append(f"  总bar数: {total}")
        lines.append(f"  笔覆盖: {up_bi}向上 / {dn_bi}向下 / {no_bi}无")
        lines.append(f"  中枢内: {in_zs}")
        lines.append(f"  分型: {bi_start}起 / {bi_end}终")
        lines.append(f"  ★买: {star_b}  ★卖: {star_s}")
        lines.append(f"  趋势: {regime_str}")

    lines.append(f"\n{'='*60}")
    lines.append("总结")
    lines.append(f"{'='*60}")
    total_bars = sum(len(df) for _, df in all_labeled)
    total_up = sum((df['bi_direction'] == '向上').sum() for _, df in all_labeled)
    total_dn = sum((df['bi_direction'] == '向下').sum() for _, df in all_labeled)
    total_zs = sum(df['in_zhongshu'].sum() for _, df in all_labeled)
    total_star_b = sum(df['star_buy'].sum() for _, df in all_labeled)
    total_star_s = sum(df['star_sell'].sum() for _, df in all_labeled)
    lines.append(f"  标的数: {len(all_labeled)}")
    lines.append(f"  总bar数: {total_bars}")
    lines.append(f"  笔覆盖率: {total_up+total_dn}/{total_bars} = {(total_up+total_dn)/total_bars:.0%}")
    lines.append(f"  中枢占比: {total_zs}/{total_bars} = {total_zs/total_bars:.1%}")
    lines.append(f"  ★买总数: {total_star_b}")
    lines.append(f"  ★卖总数: {total_star_s}")
    lines.append(f"  平均上涨笔占比: {total_up/max(total_up+total_dn,1):.0%}")
    lines.append("")

    report = "\n".join(lines)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"  ✓ 报告: {report_path}")
    return report


# ── 主流程 ──
print("=" * 60)
print("Kronos 缠论标注 & 训练数据准备")
print("=" * 60)

from config import NAME_MAP

all_labeled = []
symbols_processed = []
symbols_skipped = []

for code, name in NAME_MAP.items():
    print(f"\n▶ {code} {name}")
    df_labeled = label_symbol(code, name)
    if df_labeled is None:
        symbols_skipped.append(code)
        continue
    symbols_processed.append(code)
    all_labeled.append((code, df_labeled))

    # 保存单标标注CSV
    out_path = LABELED_DIR / f"{code}_labeled.csv"
    df_labeled.to_csv(out_path, index=False)
    print(f"  ✓ 标注保存: {out_path.name}")

# 构建训练数据
print(f"\n{'='*60}")
print("构建Kronos训练数据...")
training_csv = OUTPUT_DIR / "kronos_training_data.csv"
build_training_csv(all_labeled, training_csv)

# 标注报告
report_path = OUTPUT_DIR / "labeling_report.txt"
report = build_report(all_labeled, report_path)

print(f"\n{'='*60}")
print("完成!")
print(f"  标注标的: {len(symbols_processed)}/{len(NAME_MAP)}")
print(f"  跳过: {symbols_skipped or '无'}")
print(f"  训练数据: {training_csv}")
print(f"  标注文件: {LABELED_DIR}/")
print(f"  报告: {report_path}")
print(f"{'='*60}")

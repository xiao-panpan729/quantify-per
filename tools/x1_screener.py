# -*- coding: utf-8 -*-
"""
A股 x₁ 强度排行榜 — 全市场扫描 + A/B/C 分类 + 回测分析
====================================================

核心逻辑：
  对每只 A 股计算通达信 RSI势能2 指标 (x₁)，每日取 Top 50 并分类。
  A类(红色): x₁≥8 且走强或稳定
  B类(绿色): x₁≥8 但分数下滑
  C类(黄色+★): 新进 Top 50

数据来源: pytdx 直读通达信 .day 文件
回测窗口: 2025-01-01 → 至今

用法:
    python tools/x1_screener.py --today          # 计算今日 Top 50
    python tools/x1_screener.py --backfill       # 回填 2025-01-01 至今
    python tools/x1_screener.py --analyze        # 模式分析
    python tools/x1_screener.py --date 20250601  # 指定日期
"""

import os
import sys
import json
import struct
import time
from datetime import datetime, timedelta
from collections import defaultdict

# Windows GBK 终端编码修正
if sys.stdout.encoding and sys.stdout.encoding.lower() in ('gbk', 'cp936'):
    sys.stdout.reconfigure(encoding='utf-8')

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import config


# ===== 路径 =====
TDX_VIPDOC = os.path.join(config.TDX_ROOT, 'vipdoc')
OUTPUT_DIR = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking', '_macro', 'x1_history')
DAILY_DIR = os.path.join(OUTPUT_DIR, 'daily')
os.makedirs(DAILY_DIR, exist_ok=True)

# ===== 常量 =====
PRICE_COEF = 0.01        # A 股日线价格系数
VOLUME_COEF = 0.01       # A 股日线成交量系数
X1_MIN_BARS = 65         # 计算 x₁ 最少需要根 K 线
TOP_N = 50               # 每日取前 N 名

# 板块 x₁ 阈值（来自用户经验）
SECTOR_X1_THRESHOLDS = {
    'active': 5,      # ≥5 走强启动
    'strong': 8,      # ≥8 显著走强
    'upper': 12,      # 板块上限
}

# 个股 x₁ 阈值
STOCK_X1_THRESHOLDS = {
    'strong': 8,      # ≥8 有区分度
    'upper': 14,      # 个股极限
}


# ── 辅助函数（从 sector_momentum 引用原版，保证与通达信一致） ──

from tools.sector_momentum import _sma, _ema, _safe_div, _pct_change


def calc_x1_series(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """
    对单只股票，计算每个 bar 位置的 x₁ 值。
    返回 shape (n,) 的数组，长度与 close 相同。
    """
    n = len(close)
    if n < X1_MIN_BARS:
        return np.zeros(n)

    # ── 均线 ──
    ma3 = _sma(close, 3)
    ma5 = _sma(close, 5)
    ma13 = _sma(close, 13)
    ma20 = _sma(close, 20)
    ma60 = _sma(close, 60)
    vol_ma5 = _sma(volume, 5)

    # ── 超短强度 ──
    vol_ratio = _safe_div(volume, vol_ma5, 1.0)
    us_period = np.where(vol_ratio > 1.5, 3, 5).astype(int)
    ma_us = np.where(us_period == 3, ma3, ma5)
    us_pct = _pct_change(ma_us, 1)
    us_strength = np.degrees(np.arctan(us_pct))

    # ── 短期强度 ──
    short_pct = _pct_change(ma13, 1)
    short_atan = np.degrees(np.arctan(short_pct))
    short_ema = _ema(short_atan, 2)

    # ── 中期强度 ──
    mid_pct = _pct_change(ma20, 1)
    mid_atan = np.degrees(np.arctan(mid_pct))
    mid_ema = _ema(mid_atan, 3)

    # ── 长期强度 ──
    long_pct = _pct_change(ma60, 20)
    long_atan = np.degrees(np.arctan(long_pct))

    # ── 量能因子 ──
    vol_r1 = _safe_div(volume[1:], volume[:-1], 1.0)
    vol_r5 = _safe_div(vol_ma5[1:], vol_ma5[:-1], 1.0)
    vol_factor = np.ones(n, dtype=np.float64)
    vol_factor[1:] = np.maximum(vol_r1, vol_r5)
    vol_factor = np.nan_to_num(vol_factor, nan=1.0)
    boost = np.where(vol_factor > 1.8, 1.25, np.where(vol_factor > 1.3, 1.15, 1.0))

    # ── 势能评分（全系列） ──
    score_series = (
        us_strength * 0.45 * boost
        + short_ema * 0.3
        + mid_ema * 0.2 * 0.85
        + long_atan * 0.05
    ) * (100.0 / 60.0)
    score_series = np.nan_to_num(score_series, nan=0.0)

    # ── 连续强势天数 BARSLAST 逐 bar 计算 ──
    score_diff = np.diff(score_series)
    result = np.zeros(n)
    last_decline = -1  # 从 -1 开始，使得 (i - (-1)) = i+1
    for i in range(n):
        if i > 0 and score_diff[i-1] < 0:
            last_decline = i - 1
        result[i] = (score_series[i] + (i - last_decline) * 0.015) * 0.1

    return result


# ── 股票数据加载 ──

_STOCK_CACHE = None          # {code: {dates, close, volume, name}}
_STOCK_CACHE_DATE = None     # 缓存日期


def _scan_day_files():
    """遍历 .day 文件，筛选 A 股（排除 ETF/指数/基金）"""
    stocks = []
    for exchange in ['sh', 'sz']:
        lday_dir = os.path.join(TDX_VIPDOC, exchange, 'lday')
        if not os.path.isdir(lday_dir):
            continue
        for fname in os.listdir(lday_dir):
            if not fname.endswith('.day'):
                continue
            fpath = os.path.join(lday_dir, fname)
            code = fname[2:8]
            # sh: 60xxxx 主板, 68xxxx 科创板
            if exchange == 'sh':
                if code.startswith('60') or code.startswith('68'):
                    stocks.append((fpath, exchange, code))
            # sz: 00xxxx 主板, 30xxxx 创业板
            elif exchange == 'sz':
                if not code.startswith('39') and not code.startswith('15') \
                   and not code.startswith('16') and not code.startswith('18'):
                    if code.startswith('00') or code.startswith('30'):
                        stocks.append((fpath, exchange, code))
    return stocks


def _read_full_day(filepath):
    """读取完整的 .day 文件数据（直接 struct 解析，不依赖 pytdx reader）"""
    try:
        size = os.path.getsize(filepath)
        if size < 32:
            return None
        n_records = size // 32
        with open(filepath, 'rb') as f:
            raw = f.read()
        records = [struct.unpack_from('<IIIIIfII', raw, i * 32) for i in range(n_records)]
        dates = np.array([r[0] for r in records], dtype=np.int32)
        close = np.array([r[4] * PRICE_COEF for r in records], dtype=np.float64)
        volume = np.array([r[6] * VOLUME_COEF for r in records], dtype=np.float64)
        return dates, close, volume
    except Exception:
        return None


def _code_label(exchange, code):
    return f'{exchange}{code}'


def _load_names():
    """加载股票名称缓存，返回 {code: name} 和 ST 代码集合"""
    names_path = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking', '_funds', 'stock_names.csv')
    names = {}
    st_codes = set()
    if os.path.exists(names_path):
        try:
            import csv
            with open(names_path, 'r', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    c = row.get('code', '')
                    n = row.get('name', '')
                    names[c] = n
                    if 'ST' in n or '*ST' in n or '退市' in n:
                        st_codes.add(c)
        except Exception:
            pass
    return names, st_codes


def _compute_all_x1_series(data):
    """
    对所有股票预计算 x1 全序列（一次计算，后续逐日只需索引查找）。
    在 data dict 每个条目中添加 'x1_series' 字段。
    """
    print(f'  预计算 x₁ 全序列 ({len(data)} 只)...')
    t0 = time.time()
    for code, info in data.items():
        info['x1_series'] = calc_x1_series(info['close'], info['volume'])
    elapsed = time.time() - t0
    print(f'  完成 ({elapsed:.1f}s)')
    return data


def load_all_a_stocks(force_reload=False, precompute_x1=True):
    """
    加载全 A 股日线数据到内存，可选预计算 x1 全序列。

    precompute_x1=True (默认): 一次性算完全部股票的 x1 序列，后续按日期只需索引。
      适合 --backfill / --analyze 等批量处理。
    precompute_x1=False: 只加载原始日线，不预计算。适合单日快照。

    返回 {code: {dates, close, volume, name[, x1_series]}}
    """
    global _STOCK_CACHE, _STOCK_CACHE_DATE
    if _STOCK_CACHE is not None and not force_reload:
        return _STOCK_CACHE

    print(f'扫描 A 股 .day 文件...')
    t0 = time.time()
    files = _scan_day_files()
    print(f'  发现 {len(files)} 只 A 股')

    names, st_codes = _load_names()
    data = {}
    n_loaded = 0
    n_skipped = 0
    n_st = 0

    for fpath, exchange, code in files:
        label = _code_label(exchange, code)
        result = _read_full_day(fpath)
        if result is None:
            n_skipped += 1
            continue
        dates, close, volume = result
        if len(dates) < X1_MIN_BARS:
            n_skipped += 1
            continue
        # 过滤 ST/*ST/退市（names CSV 用带前缀的代码格式）
        if label in st_codes:
            n_st += 1
            continue
        data[label] = {
            'dates': dates,
            'close': close,
            'volume': volume,
            'name': names.get(label, ''),
        }
        n_loaded += 1

    elapsed = time.time() - t0
    print(f'  加载: {n_loaded} 只成功, {n_skipped} 只跳过, 过滤 {n_st} 只ST ({elapsed:.1f}s)')

    if precompute_x1:
        data = _compute_all_x1_series(data)

    _STOCK_CACHE = data
    _STOCK_CACHE_DATE = datetime.now().strftime('%Y-%m-%d')
    return data


def get_all_trading_dates(stock_data=None):
    """获取全市场所有交易日期（取所有股票日期的并集）"""
    if stock_data is None:
        stock_data = load_all_a_stocks()

    all_dates = set()
    for info in stock_data.values():
        for d in info['dates']:
            all_dates.add(d)
    return sorted(all_dates)


# ── 单日计算 ──

def compute_x1_for_date(stock_data, target_date_int, top_n=TOP_N):
    """
    对指定日期，计算所有股票的 x₁，返回 Top N 排名列表。

    使用预计算的 x1_series（快速索引），要求 stock_data 已包含 x1_series 字段。
    若没有（precompute_x1=False 加载），则退化为逐股重算（慢）。

    target_date_int: YYYYMMDD 格式的整数
    返回: [{code, name, x1, rank, close}, ...] 按 x₁ 降序
    """
    scores = []
    for code, info in stock_data.items():
        dates = info['dates']
        close = info['close']
        name = info['name']

        idx = np.searchsorted(dates, target_date_int, side='right') - 1
        if idx < X1_MIN_BARS - 1:     # 不够根数
            continue

        # 快速路径：预计算的 x1_series
        if 'x1_series' in info:
            x1_val = round(info['x1_series'][idx], 2)
        else:
            # 慢速路径：逐股票重算
            volume = info['volume']
            close_slice = close[:idx+1]
            volume_slice = volume[:idx+1]
            x1_val = round(calc_x1_series(close_slice, volume_slice)[-1], 2)

        if x1_val <= 0:
            continue

        scores.append({
            'code': code,
            'name': name,
            'x1': x1_val,
            'close': round(close[idx], 2),
        })

    # 排序取 Top N
    scores.sort(key=lambda x: x['x1'], reverse=True)
    top = scores[:top_n]
    for i, item in enumerate(top):
        item['rank'] = i + 1
    return top


# ── 板块映射 ──

_SECTOR_CACHE = None  # {stock_code: [sector_names]}


def load_sector_mapping():
    """
    从 sector_momentum_cache.json 加载股票→板块映射。
    返回 {code_6digit: [板块名称, ...], 'sector_scores': {板块: x1}}
    """
    global _SECTOR_CACHE
    if _SECTOR_CACHE is not None:
        return _SECTOR_CACHE

    cache_path = os.path.join(
        config.PROJECT_ROOT, 'signals', 'tracking', '_macro',
        'sector_momentum_cache.json'
    )
    if not os.path.exists(cache_path):
        print('  [警告] sector_momentum_cache.json 不存在，跳过板块标注')
        _SECTOR_CACHE = {'stock_sectors': {}, 'sector_scores': {}}
        return _SECTOR_CACHE

    with open(cache_path, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    result = {
        'stock_sectors': raw.get('stock_sectors', {}),
        'sector_scores': raw.get('sector_scores', {}),
    }
    _SECTOR_CACHE = result
    return result


def annotate_with_sectors(top50, sector_data):
    """
    给 Top 50 列表标注板块/概念。
    sector_data: {stock_sectors: {6位代码: [板块]}, sector_scores: {板块: {x1}}}
    在每项中添加 'sectors' 字段。
    """
    stock_sectors = sector_data.get('stock_sectors', {})
    sector_scores = sector_data.get('sector_scores', {})

    for item in top50:
        code_full = item['code']  # e.g. sz002636
        code_6 = code_full[2:] if len(code_full) > 6 else code_full

        sectors = stock_sectors.get(code_6, [])
        # 带上板块 x₁ 分数
        sector_x1 = {}
        for s in sectors[:5]:  # 最多标 5 个板块
            if s in sector_scores:
                sector_x1[s] = sector_scores[s].get('x1', 0)
            else:
                sector_x1[s] = None

        item['sectors'] = sectors[:5]
        item['sector_x1'] = sector_x1

    return top50


def sector_concentration_report(top50):
    """
    统计 Top 50 的板块集中度。
    返回 [(板块名, 出现次数, 平均 x₁, {code: x1}), ...]
    """
    sector_stocks = defaultdict(list)
    for item in top50:
        for s in item.get('sectors', []):
            sector_stocks[s].append({
                'code': item['code'],
                'name': item.get('name', ''),
                'x1': item['x1'],
                'rank': item['rank'],
            })

    report = []
    for sector, stocks in sector_stocks.items():
        avg_x1 = sum(s['x1'] for s in stocks) / len(stocks)
        report.append({
            'sector': sector,
            'count': len(stocks),
            'avg_x1': round(avg_x1, 2),
            'stocks': stocks,
        })

    report.sort(key=lambda x: (x['count'], x['avg_x1']), reverse=True)
    return report


# ── A/B/C 分类 ──

def classify_top50(today_top50, yesterday_top50):
    """
    A/B/C 分类。

    参数:
        today_top50: [{code, x1, name, close}, ...]
        yesterday_top50: [{code, x1, ...}, ...] 或 None (首日)

    返回:
        在原列表上添加 'class' 字段: 'A' / 'B' / 'C'
    """
    if yesterday_top50 is None:
        # 首日全部标 C
        for item in today_top50:
            item['class'] = 'C'
        return today_top50

    # 建立昨日索引
    prev_map = {item['code']: item for item in yesterday_top50}
    prev_codes = set(prev_map.keys())

    for item in today_top50:
        code = item['code']
        x1 = item['x1']

        if code not in prev_codes:
            # 新进 Top 50
            item['class'] = 'C'
        elif code in prev_codes:
            prev_x1 = prev_map[code].get('x1', 0)
            item['prev_x1'] = prev_x1
            if x1 >= 8:
                # x1 下降超过 5% 视为走弱
                if prev_x1 > 0 and x1 < prev_x1 * 0.95:
                    item['class'] = 'B'
                else:
                    item['class'] = 'A'
            else:
                # x1 < 8, 看趋势: 下降超过 5% 算 B, 否则 A
                if prev_x1 > 0 and x1 < prev_x1 * 0.95:
                    item['class'] = 'B'
                else:
                    item['class'] = 'A'

    return today_top50


# ── 历史回填 ──

def build_history(start_date="20250101", end_date=None):
    """
    回填从 start_date 到 end_date 的每日 Top 50 数据库。
    存储格式: 每天一个 JSON 文件 + 一个汇总 CSV
    """
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')

    print(f'回填 x₁ 历史数据库: {start_date} → {end_date}')
    print(f'={"=" * 60}')

    # 加载全市场数据
    stock_data = load_all_a_stocks()
    print(f'全市场: {len(stock_data)} 只股票')

    # 获取所有交易日
    all_dates = get_all_trading_dates(stock_data)
    print(f'交易日总数: {len(all_dates)}')

    # 筛选目标日期范围
    start_int = int(start_date)
    end_int = int(end_date)
    target_dates = [d for d in all_dates if start_int <= d <= end_int]
    print(f'目标日期: {len(target_dates)} 个交易日')

    # 如果已有回填数据，从最后一个日期继续
    existing = sorted([f.replace('.json', '') for f in os.listdir(DAILY_DIR) if f.endswith('.json')])
    last_done = existing[-1] if existing else None
    if last_done:
        target_dates = [d for d in target_dates if d > int(last_done)]
        print(f'从中断处继续: {last_done} → {target_dates[0] if target_dates else "全部完成"}')
        if not target_dates:
            print('所有日期已处理完毕。')
            return

    # 逐日计算
    prev_top = None
    n_done = 0
    t_start = time.time()

    for i, date_int in enumerate(target_dates):
        date_str = str(date_int)

        # 计算当日 x₁
        top = compute_x1_for_date(stock_data, date_int)
        if not top:
            continue

        # 分类
        top = classify_top50(top, prev_top)

        # 保存
        out = {
            'date': date_str,
            'total_stocks': len(stock_data),
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'ranking': top,
        }
        with open(os.path.join(DAILY_DIR, f'{date_str}.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

        prev_top = top
        n_done += 1

        if (i + 1) % 20 == 0:
            elapsed = time.time() - t_start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(f'  [{i+1}/{len(target_dates)}] {date_str} — {rate:.1f} 日/秒')

    total_time = time.time() - t_start
    print(f'完成: {n_done} 个交易日 ({(total_time/n_done) if n_done > 0 else 0:.1f}s/日)')


# ── 导出 CSV 时间序列 ──

def export_pivot_csv(output_path=None):
    """
    从 daily JSON 文件生成透视 CSV: 行=股票代码, 列=日期, 值=x₁
    """
    if output_path is None:
        output_path = os.path.join(OUTPUT_DIR, 'x1_pivot.csv')

    files = sorted([f for f in os.listdir(DAILY_DIR) if f.endswith('.json')])
    if not files:
        print('没有数据，先运行 --backfill')
        return

    # 收集所有股票 x₁ 时间序列
    rows = {}  # code → {date: x1, 'name': name}

    for fname in files:
        date_str = fname.replace('.json', '')
        with open(os.path.join(DAILY_DIR, fname), 'r', encoding='utf-8') as f:
            data = json.load(f)
        for item in data['ranking']:
            code = item['code']
            if code not in rows:
                rows[code] = {'name': item.get('name', '')}
            rows[code][date_str] = item['x1']

    # 转为 DataFrame
    df = pd.DataFrame.from_dict(rows, orient='index')
    df.index.name = 'code'
    df.to_csv(output_path, encoding='utf-8-sig')
    print(f'透视表已导出: {output_path}  ({len(rows)} 只股票 × {len(files)} 个交易日)')
    return df


# ── 今日快照 ──

def today_snapshot():
    """计算今日 x₁ Top 50 并打印表格"""
    print(f'\n今日快照 — {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'={"=" * 70}')

    stock_data = load_all_a_stocks()

    today_int = int(datetime.now().strftime('%Y%m%d'))

    # 读取昨日数据（如果有）
    yesterday_int = int((datetime.now() - timedelta(days=3)).strftime('%Y%m%d'))
    yesterday_top = None
    for d in range(yesterday_int, today_int):
        yf = os.path.join(DAILY_DIR, f'{d}.json')
        if os.path.exists(yf):
            with open(yf, 'r', encoding='utf-8') as f:
                yesterday_top = json.load(f).get('ranking', [])
            break

    # 计算今日
    top = compute_x1_for_date(stock_data, today_int)
    if not top:
        print('今日数据尚未更新（通达信可能还未收盘）')
        return

    top = classify_top50(top, yesterday_top)

    # 打印表格
    _print_table(top)

    # 保存今日快照
    out = {
        'date': str(today_int),
        'total_stocks': len(stock_data),
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ranking': top,
    }
    snapshot_path = os.path.join(DAILY_DIR, f'{today_int}.json')
    with open(snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n已保存: {snapshot_path}')

    return top


def _print_table(top, show_sectors=False):
    """打印 Top 50 表格"""
    header = f'  Rank 代码         名称           x₁    分类'
    if show_sectors:
        header += '  板块/概念'
    print(f'\n{header}')
    print(f'  {"-" * (70 if not show_sectors else 100)}')
    for item in top:
        rank = item['rank']
        code = item['code']
        name = item.get('name', '') or ''
        x1 = item['x1']
        cls = item.get('class', '')
        cls_mark = {'A': '🅰️', 'B': '🅱️', 'C': '🅲★'}.get(cls, '')
        line = f'  {rank:3d}  {code:12s} {name:<12s} {x1:>6.2f}  {cls_mark:>4s}'
        if show_sectors:
            sectors = item.get('sectors', [])
            sec_str = ' '.join(sectors[:3]) if sectors else ''
            line += f'  {sec_str:<30s}'
        print(line)
    print(f'  {"-" * (70 if not show_sectors else 100)}')


# ── 模式分析 ──

def analyze_patterns():
    """
    分析回填数据中的规律。

    核心问题:
    1. "个股 8 分走强" — x₁≥8 后 5/10/20 日收益分布
    2. A/B/C 类后续表现差异 — 多久会反转
    3. 板块 x₁≥5 走强验证
    4. x₁ 的极值区域（14-15 分见顶规律）
    """
    files = sorted([f for f in os.listdir(DAILY_DIR) if f.endswith('.json')])
    if not files:
        print('没有数据，先运行 --backfill')
        return

    print(f'\n\n{"=" * 70}')
    print(f'  x₁ 模式分析 — {len(files)} 个交易日')
    print(f'{"=" * 70}')

    # ── 1. 加载全量数据 ──
    print(f'\n[1/5] 加载每日数据...')
    daily_data = []
    for fname in files:
        with open(os.path.join(DAILY_DIR, fname), 'r', encoding='utf-8') as f:
            daily_data.append(json.load(f))

    dates = [d['date'] for d in daily_data]
    print(f'  日期范围: {dates[0]} → {dates[-1]}')

    # ── 2. x₁ 阈值分析 ──
    print(f'\n[2/5] x₁ 阈值穿透统计...')

    thresholds = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    threshold_counts = {t: 0 for t in thresholds}
    total_entries = 0

    for day in daily_data:
        for item in day['ranking']:
            total_entries += 1
            x1 = item['x1']
            for t in thresholds:
                if x1 >= t:
                    threshold_counts[t] += 1

    print(f'  总计 Top{TOP_N} 条目数: {total_entries}')
    print(f'  x₁ 各阈值穿透率:')
    for t in thresholds:
        pct = threshold_counts[t] / total_entries * 100 if total_entries > 0 else 0
        label = ''
        if t == 5:
            label = ' (板块走强启动)'
        elif t == 8:
            label = ' (个股显著走强)'
        elif t == 12:
            label = ' (板块上限)'
        elif t == 14:
            label = ' (个股极限)'
        print(f'    x₁ ≥ {t:2d}: {threshold_counts[t]:5d} 次 ({pct:.1f}%){label}')

    # ── 3. A/B/C 分类统计 ──
    print(f'\n[3/5] A/B/C 分类统计...')
    class_counts = {'A': 0, 'B': 0, 'C': 0}
    class_x1 = {'A': [], 'B': [], 'C': []}

    for day in daily_data:
        for item in day['ranking']:
            cls = item.get('class', '?')
            if cls in class_counts:
                class_counts[cls] += 1
                class_x1[cls].append(item['x1'])

    for cls in ['A', 'B', 'C']:
        count = class_counts[cls]
        avg_x1 = np.mean(class_x1[cls]) if class_x1[cls] else 0
        pct = count / total_entries * 100 if total_entries > 0 else 0
        labels = {'A': '红色 — 持续走强', 'B': '绿色 — 强度够但调整', 'C': '黄色★ — 新进'}
        print(f'    {cls}类 {labels[cls]}: {count:5d} 次 ({pct:.1f}%), 平均 x₁={avg_x1:.2f}')

    # ── 4. ★买信号相关性 ──
    print(f'\n[4/5] ★买信号相关性分析...')
    stock_data = load_all_a_stocks()
    star_buy_events = 0
    x1_gt_8_before_star = 0

    # 简化版：检查 Top 50 中那些在后续出现机会的
    # 这里需要结合 tracking 数据做更深入分析
    print(f'  （需要与 tracking 信号数据关联，待扩展）')

    # ── 5. Top N 稳定性分析 ──
    print(f'\n[5/5] A 类股连续霸榜统计...')
    a_streaks = defaultdict(int)  # code → 连续 A 类天数
    max_streaks = defaultdict(int)
    current_streak = defaultdict(int)

    for day in daily_data:
        today_a = {item['code'] for item in day['ranking'] if item.get('class') == 'A'}
        all_codes = {item['code'] for item in day['ranking']}

        # 对新入榜且为 A 的，+= 1；不在榜上的重置
        for code in all_codes:
            if code in today_a:
                current_streak[code] += 1
            else:
                if current_streak[code] > 0:
                    max_streaks[code] = max(max_streaks.get(code, 0), current_streak[code])
                current_streak[code] = 0

    # 连续 A 类 Top 20
    streaks = [(code, max(max_streaks.get(code, 0), current_streak[code]))
               for code in set(list(max_streaks.keys()) + list(current_streak.keys()))]
    streaks.sort(key=lambda x: x[1], reverse=True)

    print(f'  最长连续 A 类 Top 20:')
    print(f'  {"code":12s} {"名称":12s} {"连续天数":>8s}')
    print(f'  {"-"*40}')
    names, _ = _load_names()
    for code, days in streaks[:20]:
        name = names.get(code, stock_data.get(code, {}).get('name', '') if stock_data else '')
        print(f'  {code:12s} {name:<12s} {days:>8d}')

    print(f'\n分析完成。')


# ── CLI ──

def main():
    import argparse
    parser = argparse.ArgumentParser(description='A 股 x₁ 强度排行榜')
    parser.add_argument('--today', action='store_true', help='计算今日 Top 50')
    parser.add_argument('--backfill', action='store_true', help='回填 2025 年至今')
    parser.add_argument('--start', default='20250101', help='回填起始日期 (YYYYMMDD)')
    parser.add_argument('--end', default=None, help='回填截止日期 (YYYYMMDD)')
    parser.add_argument('--analyze', action='store_true', help='模式分析')
    parser.add_argument('--export', action='store_true', help='导出透视 CSV')
    parser.add_argument('--date', default=None, help='指定日期计算 (YYYYMMDD)')
    parser.add_argument('--report', action='store_true', help='Top 50 板块标注报告')
    parser.add_argument('--top-n', type=int, default=TOP_N, help='排名深度 (默认 50)')
    args = parser.parse_args()

    if args.report:
        stock_data = load_all_a_stocks(precompute_x1=True)
        today_int = int(datetime.now().strftime('%Y%m%d'))
        top = compute_x1_for_date(stock_data, today_int, top_n=args.top_n)
        if not top:
            print('今日数据尚未更新')
            return
        # 分类
        prev_top = None
        for d in range(today_int - 3, today_int):
            pf = os.path.join(DAILY_DIR, f'{d}.json')
            if os.path.exists(pf):
                with open(pf, 'r', encoding='utf-8') as f:
                    prev_top = json.load(f).get('ranking', [])
                break
        top = classify_top50(top, prev_top)
        # 板块标注
        sector_data = load_sector_mapping()
        top = annotate_with_sectors(top, sector_data)
        # 打印
        _print_table(top, show_sectors=True)
        # 板块集中度
        print(f'\n板块集中度 (Top {args.top_n} 中同板块≥2 只):')
        print(f'  {"板块":<18s} {"个数":>4s} {"平均 x₁":>8s}  股票')
        print(f'  {"-"*60}')
        report = sector_concentration_report(top)
        for entry in report[:15]:
            if entry['count'] < 2:
                break
            stocks_str = ' '.join(f'{s["code"]}({s["x1"]})' for s in entry['stocks'][:5])
            sector_name = entry['sector']
            print(f'  {sector_name:<18s} {entry["count"]:>4d}  {entry["avg_x1"]:>8.2f}  {stocks_str}')
        return

    if args.today:
        today_snapshot()
    elif args.backfill:
        build_history(start_date=args.start, end_date=args.end)
    elif args.analyze:
        analyze_patterns()
    elif args.export:
        export_pivot_csv()
    elif args.date:
        stock_data = load_all_a_stocks()
        date_int = int(args.date)
        top = compute_x1_for_date(stock_data, date_int)
        # 尝试读取前一日
        prev_f = os.path.join(DAILY_DIR, f'{date_int - 1}.json')
        prev_top = None
        if os.path.exists(prev_f):
            with open(prev_f, 'r', encoding='utf-8') as f:
                prev_top = json.load(f).get('ranking', [])
        top = classify_top50(top, prev_top)
        _print_table(top)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""
成交额强者选股器 — 双维度筛选机构票

条件A: 成交额全A股排名Top50（支持持续排名）
条件B: 近N日后复权创历史新高（全上市历史）

数据全部来自本地通达信 .day 文件 + gbbq 除权数据，不依赖网络。

用法:
    python tools/volume_leader_screener.py                     # Top50 + 20/90/180日三档
    python tools/volume_leader_screener.py --top 20             # 严格: Top20
    python tools/volume_leader_screener.py --update-rank       # 仅更新排名快照
    python tools/volume_leader_screener.py --top 50 --update-rank  # 更新+筛选

输出:
    1. 终端表格（成交额排名 × 新高状态）
    2. signals/tracking/volume_rank_history.csv（每日排名快照）
"""

import sys
import os
import json
import struct
import time
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import config

# ===== 路径配置 =====
TDX_VIPDOC = os.path.join(config.TDX_ROOT, 'vipdoc')
GBBQ_CSV = os.path.join(config.PROJECT_ROOT, 'gbbq', 'gbbq.csv')
RANK_HISTORY_CSV = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking', 'volume_rank_history.csv')
UNIVERSE_PATH = os.path.join(config.PROJECT_ROOT, 'signals', 'tracking', 'volume_leader_universe.json')

# ===== 常量 =====
PRICE_COEF = 0.01       # A股日线价格系数
VOLUME_COEF = 0.01      # A股日线成交量系数
GBBQ_CACHE = {}         # 除权数据内存缓存
NAME_CACHE = {}         # 股票名称缓存


def _scan_day_files():
    """遍历本地 .day 文件，返回所有A股的 (filepath, exchange, code)"""
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
            if exchange == 'sh' and code.startswith('60'):
                stocks.append((fpath, exchange, code))
            elif exchange == 'sz' and (code.startswith('00') or code.startswith('30')):
                # 过滤39xxxx（创业板指）和15/16xxxx（基金）
                if not code.startswith('39') and not code.startswith('15') and not code.startswith('16'):
                    stocks.append((fpath, exchange, code))
    return stocks


def _read_last_bar(filepath):
    """只读 .day 文件最后一条记录（32字节）"""
    try:
        size = os.path.getsize(filepath)
        if size < 32:
            return None
        with open(filepath, 'rb') as f:
            f.seek(size - 32)
            data = f.read(32)
        record = struct.unpack('<IIIIIfII', data)
        return {
            'date': int(record[0]),
            'open':  record[1] * PRICE_COEF,
            'high':  record[2] * PRICE_COEF,
            'low':   record[3] * PRICE_COEF,
            'close': record[4] * PRICE_COEF,
            'amount': record[5],                     # 成交额(元)
            'volume': int(record[6] * VOLUME_COEF),  # 成交量(手)
        }
    except Exception:
        return None


def _code_label(exchange, code):
    """sh600519 → sh600519"""
    return f'{exchange}{code}'


NAME_CACHE_FILE = os.path.join(os.path.dirname(RANK_HISTORY_CSV), 'stock_names.csv')


def _fetch_names_from_tdx():
    """从 pytdx 一次性拉取全A股名称列表 → 缓存CSV"""
    names = {}
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        servers = [
            ('180.153.18.170', 7709),
            ('119.147.212.81', 7709),
            ('112.74.214.43', 7721),
        ]
        for ip, port in servers:
            try:
                if api.connect(ip, port):
                    for mkt_label, mkt_code in [('sz', 0), ('sh', 1)]:
                        count = api.get_security_count(mkt_code)
                        for start in range(0, count, 1000):
                            batch = api.get_security_list(mkt_code, start)
                            if batch:
                                for item in batch:
                                    names[f'{mkt_label}{item["code"]}'] = item['name']
                    api.disconnect()
                    break
            except Exception:
                try:
                    api.disconnect()
                except Exception:
                    pass
    except Exception:
        pass

    if names:
        pd.DataFrame(
            [{'code': k, 'name': v} for k, v in names.items()]
        ).to_csv(NAME_CACHE_FILE, index=False, encoding='utf-8')

    return names


def _load_names():
    """加载股票名称（NAME_CACHE_FILE → config NAME_MAP → pytdx 拉取）"""
    global NAME_CACHE
    if NAME_CACHE:
        return NAME_CACHE

    NAME_CACHE = {}

    # 1. 本地名称缓存
    if os.path.exists(NAME_CACHE_FILE):
        df = pd.read_csv(NAME_CACHE_FILE, encoding='utf-8', dtype=str)
        for _, row in df.iterrows():
            NAME_CACHE[row['code']] = row['name']

    # 2. config NAME_MAP 覆盖/补充
    NAME_CACHE.update(dict(config.NAME_MAP))

    return NAME_CACHE


def scan_and_rank():
    """
    全A股扫描，返回按成交额降序排列的列表
    [(code_label, exchange, code, last_bar_dict), ...]
    """
    stocks = _scan_day_files()
    results = []

    for filepath, exchange, code in stocks:
        bar = _read_last_bar(filepath)
        if bar is None or bar['amount'] <= 0:
            continue
        label = _code_label(exchange, code)
        results.append((label, exchange, code, bar))

    # 按成交额降序
    results.sort(key=lambda x: x[3]['amount'], reverse=True)
    return results


def update_rank_history():
    """更新当日排名快照 → volume_rank_history.csv（每日盘后执行）"""
    ranked = scan_and_rank()

    today_str = datetime.now().strftime('%Y-%m-%d')
    rows = []
    for rank_idx, (label, exchange, code, bar) in enumerate(ranked, start=1):
        date_str = str(bar['date'])
        # YYYYMMDD → YYYY-MM-DD
        dt = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
        rows.append({
            'date': dt,
            'code': label,
            'amount': bar['amount'],
            'rank': rank_idx,
        })

    df_new = pd.DataFrame(rows)

    # 合并历史: 去重同一天的数据
    if os.path.exists(RANK_HISTORY_CSV):
        df_old = pd.read_csv(RANK_HISTORY_CSV, encoding='utf-8')
        # 删除已存在的同日数据
        df_old = df_old[df_old['date'] != dt]
        df_merged = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_merged = df_new

    os.makedirs(os.path.dirname(RANK_HISTORY_CSV), exist_ok=True)
    df_merged.to_csv(RANK_HISTORY_CSV, index=False, encoding='utf-8')
    print(f'[排名快照] 已更新 {len(rows)} 只A股 → {RANK_HISTORY_CSV}')
    return df_new


def load_rank_history():
    """加载历史排名数据"""
    if not os.path.exists(RANK_HISTORY_CSV):
        return None
    return pd.read_csv(RANK_HISTORY_CSV, encoding='utf-8')


def check_rank_multi(code_label, rank_history, window_days=20):
    """
    检查某股票近 window_days 个交易日中，在 Top10/20/50 各有多少天。
    返回 (days_top10, days_top20, days_top50, total_days)
    """
    if rank_history is None:
        return 0, 0, 0, 0

    stock_hist = rank_history[rank_history['code'] == code_label].copy()
    if stock_hist.empty:
        return 0, 0, 0, 0

    stock_hist['date'] = pd.to_datetime(stock_hist['date'])
    stock_hist = stock_hist.sort_values('date')

    cutoff = datetime.now() - timedelta(days=window_days + 5)
    recent = stock_hist[stock_hist['date'] >= pd.Timestamp(cutoff)]

    if recent.empty:
        return 0, 0, 0, 0

    natural_cutoff = pd.Timestamp(datetime.now() - timedelta(days=window_days))
    recent_window = recent[recent['date'] >= natural_cutoff]

    total_days = len(recent_window)
    days_top10 = int((recent_window['rank'] <= 10).sum())
    days_top20 = int((recent_window['rank'] <= 20).sum())
    days_top50 = int((recent_window['rank'] <= 50).sum())

    return days_top10, days_top20, days_top50, total_days


def calc_quality_score(rank, days_top10, days_top20, days_top50, total_days, dist_pct):
    """
    综合评分 = 价格距离(0-3) + 成交额峰值(0-3) + 持续排名(0-1.5)

    价格: ≤-10%→3, ≤-15%→2, ≤-20%→1
    峰值: Top10→3, Top20→2, Top50→1
    持续(近20日≥50%天数): Top10→1.5, Top20→1.0, Top50→0.5
    """
    score = 0.0

    # 1. 价格距离
    if dist_pct >= -10:
        score += 3
    elif dist_pct >= -15:
        score += 2
    elif dist_pct >= -20:
        score += 1

    # 2. 成交额峰值
    if rank <= 10:
        score += 3
    elif rank <= 20:
        score += 2
    elif rank <= 50:
        score += 1

    # 3. 持续性（需要≥5天数据 + ≥50%天数在对应档位）
    if total_days >= 5:
        half = total_days * 0.5
        if days_top10 >= half:
            score += 1.5
        elif days_top20 >= half:
            score += 1.0
        elif days_top50 >= half:
            score += 0.5

    return score


def load_universe():
    """读取 volume_leader_universe.json，返回 set[code_label]"""
    if not os.path.exists(UNIVERSE_PATH):
        return set()
    with open(UNIVERSE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return set(data.get('universe', []))


def sync_universe(t1, t2, t3):
    """
    将新入选标的追加到 universe JSON。
    T1/T2/T3 中不在 universe 的标的自动加入，记录 added_on 日期。
    返回 list[code_label] 新加入的标的。
    """
    existing = load_universe()
    all_screened = set(r['code'] for tier in [t1, t2, t3] for r in tier)
    new_codes = sorted(all_screened - existing)
    if not new_codes:
        return []

    os.makedirs(os.path.dirname(UNIVERSE_PATH), exist_ok=True)
    if os.path.exists(UNIVERSE_PATH):
        with open(UNIVERSE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {'universe': [], 'added_on': {}, 'last_screener_run': ''}

    today_str = datetime.now().strftime('%Y-%m-%d')
    for code in new_codes:
        data['universe'].append(code)
        data['added_on'][code] = today_str

    data['universe'].sort()
    data['last_screener_run'] = today_str
    data['total_ever'] = len(data['universe'])

    with open(UNIVERSE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f'[宇宙同步] 新增 {len(new_codes)} 只标的 → {UNIVERSE_PATH}')
    return new_codes


def load_gbbq():
    """加载全量除权数据（首次加载后缓存）"""
    global GBBQ_CACHE
    if GBBQ_CACHE:
        return GBBQ_CACHE

    if not os.path.exists(GBBQ_CSV):
        print(f'[WARN] gbbq 文件不存在: {GBBQ_CSV}')
        return {}

    df = pd.read_csv(GBBQ_CSV, encoding='utf-8-sig', dtype={'code': str})
    df['code'] = df['code'].str.zfill(6)
    for (market_code, code), group in df.groupby(['market', 'code']):
        mkt_str = 'sh' if int(market_code) == 1 else 'sz'
        key = f'{mkt_str}{code}'
        GBBQ_CACHE[key] = group.sort_values('date')
    return GBBQ_CACHE


def read_full_bars(filepath):
    """读完整日线 → DataFrame（index=date, columns=open/high/low/close/amount/volume）"""
    from pytdx.reader.daily_bar_reader import TdxDailyBarReader
    reader = TdxDailyBarReader()
    try:
        return reader.get_df_by_file(filepath)
    except Exception:
        return None


def calc_houfuquan_close(bars_df, gbbq_events):
    """
    后复权收盘价计算。

    从最新到最旧遍历除权事件，逐层累乘调整因子。
    原理: 后复权 = 当前价格保持真实，历史价格×累积复权因子向上修正。

    只处理 category=1（除权除息）。category=2/5 等是送配股上市/股本变动，
    字段含义不同（总股本非每10股比例），不参与后复权调整。

    bars_df: 日线 DataFrame，index=datetime, columns含close
    gbbq_events: 该股的除权事件 DataFrame，columns含[date, category, hongli, peigujia, songgu, peigu]
    返回: 后复权收盘价 Series（对齐bars_df index）
    """
    if gbbq_events is None or gbbq_events.empty:
        return bars_df['close'].copy()

    # 只处理除权除息事件 (category=1)，过滤掉股本变动等
    gbbq_events = gbbq_events[gbbq_events['category'] == 1].copy()
    if gbbq_events.empty:
        return bars_df['close'].copy()

    hfq_close = bars_df['close'].copy()

    # 按日期从新到旧排序
    events = gbbq_events.sort_values('date', ascending=False)

    for _, evt in events.iterrows():
        try:
            div_date = pd.Timestamp(str(int(evt['date'])))
        except (ValueError, TypeError):
            continue

        # 每股值 = 原始数据÷10（通达信存储的是每10股数据）
        hongli = float(evt.get('hongli', 0) or 0) / 10.0
        songgu = float(evt.get('songgu', 0) or 0) / 10.0
        peigujia = float(evt.get('peigujia', 0) or 0)
        peigu = float(evt.get('peigu', 0) or 0) / 10.0

        if hongli == 0 and songgu == 0 and peigu == 0:
            continue

        # 除权日之前的所有bar
        before_mask = bars_df.index < div_date
        if before_mask.sum() < 2:
            continue

        # 除权前收盘价（原始价格）
        pre_close = bars_df.loc[before_mask, 'close'].iloc[-1]
        if pre_close <= 0:
            continue

        # 除权参考价 = (前收 - 每股红利 + 配股价×配股比例) / (1 + 送股比例 + 配股比例)
        numerator = pre_close - hongli + peigujia * peigu
        denominator = 1.0 + songgu + peigu
        if denominator <= 0:
            continue
        ex_rights = numerator / denominator
        if ex_rights <= 0:
            continue

        # 调整因子 = 前收 / 除权参考价 (>1 表示除权导致价格下降)
        adj_factor = pre_close / ex_rights

        # 除权日之前所有价格 × 调整因子（包括除权日当天也可以用前收基准
        # 实际上除权日当天是除权后的价格，不需要调整
        # 只有除权日之前的需要调整
        hfq_close.loc[before_mask] = hfq_close.loc[before_mask] * adj_factor

    return hfq_close


def check_new_high(exchange, code, windows=(20, 90, 180)):
    """
    检查某股票创历史新高状态（多窗口），使用原始收盘价。

    不用后复权：后复权会放大2015-2018年大量送股时期的泡沫价格，
    导致原始价格已创历史新高的股票被误判为"距新高还有70%"。

    返回 dict:
        days_since_high:   距最近一次新高多少个交易日
        dist_pct:          当前价距历史最高 %
        all_time_high:     历史最高价
        latest_close:      最新收盘价
        last_high_date:    最近一次新高日期
        high_count_{w}:    各窗口内创新高次数
    """
    filepath = os.path.join(TDX_VIPDOC, exchange, 'lday', f'{exchange}{code}.day')
    if not os.path.exists(filepath):
        return None

    bars_df = read_full_bars(filepath)
    if bars_df is None or bars_df.empty:
        return None

    # 使用原始收盘价
    close = bars_df['close']

    # 历史最高
    all_time_high = close.max()
    if all_time_high <= 0:
        return None

    # 创历史新高的日期（容差 0.1%）
    tolerance = 0.001
    new_high_mask = close >= all_time_high * (1 - tolerance)
    new_high_dates = close.index[new_high_mask]

    if len(new_high_dates) == 0:
        return None

    latest_close = close.iloc[-1]
    n_bars = len(close)

    # 最近一次新高日
    last_high_date = new_high_dates[-1]
    last_high_pos = close.index.get_loc(last_high_date)
    days_since_high = n_bars - 1 - last_high_pos

    # 各窗口新高次数
    result = {}
    high_positions = [close.index.get_loc(d) for d in new_high_dates]
    for w in windows:
        window_start_pos = max(0, n_bars - min(w, n_bars))
        result[f'high_count_{w}'] = sum(1 for p in high_positions if p >= window_start_pos)

    dist_pct = (latest_close - all_time_high) / all_time_high * 100

    result.update({
        'days_since_high': days_since_high,
        'dist_pct': round(dist_pct, 2),
        'all_time_high': all_time_high,
        'latest_close': round(latest_close, 2),
        'last_high_date': last_high_date.strftime('%Y-%m-%d') if hasattr(last_high_date, 'strftime') else str(last_high_date),
    })
    return result


def screen(vol_top=50, save_report=False):
    """
    主筛选函数 — 四维分类输出。

    T1 (20日内): 刚突破，随时可能继续
    T2 (90日内): 突破回踩，继续上涨
    T3 (180日内): 半年内新高，充分换手甜点区
    评分 = 价格距离(0-3) + 成交额峰值(0-3) + 持续排名(0-1.5)
    """
    today_str = datetime.now().strftime('%Y-%m-%d')
    print(f'\n{"=" * 70}')
    print(f'  成交额强者 × 创历史新高 选股器')
    print(f'  条件A: 全A成交额Top{vol_top}')
    print(f'  条件B: 原始价格创历史新高 (统一≤20%) + 综合评分(价格3+排名3+持续1.5)')
    print(f'{"=" * 70}')

    # ─── Step 1: 全市场成交额扫描 ───
    print(f'\n[1/4] 扫描全A股成交额...')
    t0 = time.time()
    ranked = scan_and_rank()
    total = len(ranked)
    elapsed = time.time() - t0
    print(f'  完成: {total} 只A股 ({elapsed:.1f}s)')

    # ─── Step 2: 取Top N ───
    candidates = ranked[:vol_top]
    print(f'\n[2/4] 成交额Top{vol_top}: {len(candidates)} 只候选股')

    rank_history = load_rank_history()
    names = _load_names()

    # ─── Step 3: 逐只检查原始价格创新高 ───
    print(f'\n[3/4] 逐只检查原始价格创新高 (窗口=20/90/180日)...')
    results = []
    for rank_idx, (label, exchange, code, bar) in enumerate(candidates, start=1):
        days_top10, days_top20, days_top50, total_window_days = check_rank_multi(label, rank_history, window_days=20)
        nh = check_new_high(exchange, code)
        if nh is None:
            continue

        name = names.get(label, '')
        amount_yi = bar['amount'] / 1e8

        quality = calc_quality_score(rank_idx, days_top10, days_top20, days_top50, total_window_days, nh['dist_pct'])

        results.append({
            'rank': rank_idx,
            'code': label,
            'name': name,
            'close': bar['close'],
            'amount_yi': amount_yi,
            'days_top10': days_top10,
            'days_top20': days_top20,
            'days_top50': days_top50,
            'total_window': total_window_days,
            'score': quality,
            'days_since_high': nh['days_since_high'],
            'high_count_20': nh['high_count_20'],
            'high_count_90': nh['high_count_90'],
            'high_count_180': nh['high_count_180'],
            'dist_pct': nh['dist_pct'],
            'all_time_high': round(nh['all_time_high'], 2),
            'last_high_date': nh['last_high_date'],
        })

        if (rank_idx % 10) == 0:
            print(f'  进度: {rank_idx}/{len(candidates)}')

    # ─── Step 4: 三层梯队分类 ───
    elapsed_total = time.time() - t0

    # 三层梯队：时间 × 价格距离 双维度过滤（统一≤20%）
    t1 = [r for r in results if r['days_since_high'] <= 20 and r['dist_pct'] >= -20]
    t2 = [r for r in results if 20 < r['days_since_high'] <= 90 and r['dist_pct'] >= -20]
    t3 = [r for r in results if 90 < r['days_since_high'] <= 180 and r['dist_pct'] >= -20]
    rest = [r for r in results if r not in t1 and r not in t2 and r not in t3]

    # ─── 终端输出 ───
    print(f'\n[4/4] 结果 (总耗时 {elapsed_total:.1f}s)')

    def _print_table(stocks, title, symbol, columns):
        if not stocks:
            print(f'\n  {symbol} {title}: 0 只')
            return
        header, fmt = columns
        print(f'\n{"─" * 115}')
        print(f'{symbol} {title}: {len(stocks)} 只')
        print(f'{"─" * 115}')
        print(header)
        print(f'{"─" * 115}')
        for r in stocks:
            print(fmt.format(**r))

    # 新高梯队共用列（含综合评分）
    tier_header = f'{"排名":<5} {"代码":<12} {"名称":<10} {"现价":<8} {"成交额(亿)":<11} {"距全史高%":<10} {"评分":<5} {"20/90/180日次":<15} {"最近新高日":<12}'
    tier_fmt = '{rank:<5} {code:<12} {name:<10} {close:<8.2f} {amount_yi:<11.1f} {dist_sign}{dist_pct_abs:<9.1f}% {score:<5.1f} {hc20:<3}/{hc90:<3}/{hc180:<4}   {last_high_date:<12}'
    for r in t1 + t2 + t3:
        r['dist_sign'] = '+' if r['dist_pct'] > 0 else ('-' if r['dist_pct'] < 0 else '')
        r['dist_pct_abs'] = abs(r['dist_pct'])
        r['hc20'] = r['high_count_20']
        r['hc90'] = r['high_count_90']
        r['hc180'] = r['high_count_180']

    _print_table(t1, '★★★ T1 刚突破 — 20日内创新高，随时可能继续', '★★★', (tier_header, tier_fmt))
    _print_table(t2, '★★  T2 突破回踩 — 90日内创新高，回踩继续上涨', '★★', (tier_header, tier_fmt))
    _print_table(t3, '★   T3 季度回调 — 180日内创新高，充分换手甜点区', '★', (tier_header, tier_fmt))

    # 其他（仅列名称）
    if rest:
        names_str = '、'.join(f'{r["code"]} {r["name"]}' for r in rest)
        print(f'\n{"─" * 115}')
        print(f'    其他高成交额标的 (距新高较远): {len(rest)} 只')
        print(f'    {names_str}')

    print(f'\n{"=" * 70}')
    print(f'  全A {total}只 → Top{vol_top} → T1={len(t1)} / T2={len(t2)} / T3={len(t3)} / 其他={len(rest)}')
    print(f'  操作: T1追涨/浅回调 | T2等回调企稳 | T3等充分调整(90-180日甜点区)')
    print(f'        所有操作均需 30分钟级别回调 + 5/15分钟★买共振')
    print(f'{"=" * 70}')

    # ─── 保存报告 ───
    if save_report:
        _save_report(today_str, total, vol_top, t1, t2, t3, rest)

    return t1, t2, t3, rest


def _save_report(date_str, total, vol_top, t1, t2, t3, rest):
    """保存 Markdown 报告到 reports/volume_leader/"""
    report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              'reports', 'volume_leader')
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f'{date_str.replace("-", "")}_volume_leader.md')

    lines = []
    lines.append(f'# 成交额强者 × 创历史新高 选股报告')
    lines.append(f'')
    lines.append(f'**{date_str}** | 全市场A股: {total}只 | 筛选标准: 成交额Top{vol_top} | 原始价格=全上市历史')
    lines.append(f'')

    def _md_table(stocks, title, extra_cols=None):
        lines.append(f'## {title} ({len(stocks)}只)')
        lines.append(f'')
        if not stocks:
            lines.append('*(无符合条件标的)*')
            lines.append('')
            return
        headers = ['排名', '代码', '名称', '现价', '成交额(亿)', '距全史高%', '评分']
        aligns = ['---:', ':---', ':---', '---:', '---:', '---:', ':---:']
        if extra_cols:
            for h, a in extra_cols:
                headers.append(h)
                aligns.append(a)
        lines.append('| ' + ' | '.join(headers) + ' |')
        lines.append('|' + '|'.join(aligns) + '|')
        for r in stocks:
            dist_sign = '+' if r['dist_pct'] > 0 else ('-' if r['dist_pct'] < 0 else '')
            row = [
                str(r['rank']),
                r['code'],
                r['name'],
                f'{r["close"]:.2f}',
                f'{r["amount_yi"]:.1f}',
                f'{dist_sign}{abs(r["dist_pct"]):.1f}%',
                f'{r.get("score", 0):.1f}',
            ]
            if extra_cols:
                for h, _ in extra_cols:
                    if h == '20/90/180日次':
                        row.append(f'{r["high_count_20"]}/{r["high_count_90"]}/{r["high_count_180"]}')
                    elif h == '最近新高日':
                        row.append(r['last_high_date'])
                    elif h == '历史最高(后复权)':
                        row.append(f'{r["all_time_high"]:.2f}')
                    elif h == '历史最高日':
                        row.append(r['last_high_date'])
                    elif h == '距上次新高':
                        row.append(f'{r["days_since_high"]}天前')
            lines.append('| ' + ' | '.join(row) + ' |')
        lines.append('')

    _md_table(t1, '一、T1 刚突破 — 20日内创新高，随时可能继续',
              [('20/90/180日次', ':---:'), ('最近新高日', ':---')])
    _md_table(t2, '二、T2 突破回踩 — 90日内创新高，回踩继续上涨',
              [('20/90/180日次', ':---:'), ('最近新高日', ':---')])
    _md_table(t3, '三、T3 季度回调 — 180日内创新高，充分换手甜点区',
              [('20/90/180日次', ':---:'), ('最近新高日', ':---')])
    # 其他：仅列名称，不展开表格
    if rest:
        lines.append(f'## 四、其他高成交额标的 — 距新高较远 ({len(rest)}只)')
        lines.append('')
        lines.append(' '.join(f'`{r["code"]}`{r["name"]}' for r in rest))
        lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('**操作框架**:')
    lines.append('- **T1**: 追涨/浅回调 → 等30分钟级别回调 + 5/15分钟★买共振')
    lines.append('- **T2**: 等回调企稳 → 同上')
    lines.append('- **T3**: 等充分调整 → 同上，仓位可适当加大（90-180日充分换手甜点区）')
    lines.append('- **所有操作均需大盘环境配合**')
    lines.append('')
    lines.append(f'*报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*')

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'\n[报告] 已保存 → {report_path}')
    return report_path


# ===== CLI =====
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='成交额强者选股器 — 四维分类')
    parser.add_argument('--top', type=int, default=50, help='成交额排名阈值 (默认50)')
    parser.add_argument('--update-rank', action='store_true', help='更新排名快照后再筛选')
    parser.add_argument('--fetch-names', action='store_true', help='从 pytdx 拉取全量股票名称并缓存')
    parser.add_argument('--save', action='store_true', help='保存 Markdown 报告到 reports/volume_leader/')
    parser.add_argument('--sync-universe', action='store_true', help='新增标的自动加入 volume_leader_universe.json')
    args = parser.parse_args()

    if args.fetch_names:
        print('正在从 pytdx 拉取全A股名称...')
        names = _fetch_names_from_tdx()
        print(f'已缓存 {len(names)} 只股票名称 → {NAME_CACHE_FILE}')

    if args.update_rank:
        update_rank_history()

    t1, t2, t3, rest = screen(vol_top=args.top, save_report=args.save)

    if args.sync_universe:
        new_codes = sync_universe(t1, t2, t3)
        if new_codes:
            print(f'  新加入标的: {", ".join(new_codes)}')
        else:
            print(f'  [宇宙同步] 无新标的，universe 不变')

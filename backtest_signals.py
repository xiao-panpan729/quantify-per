# -*- coding: utf-8 -*-
"""
信号级回测引擎 v3.3 — 三种统计方式

核心:
  [低点] 低点不破合并（多个区间→合并为一个趋势段）
  [50%]  回调不超过50%合并
  [★信号] 每个原始★信号独立计算 → 向后找不破该信号低点的最大区间

v3.3 — 2026-05-06
"""

import csv, json, sqlite3, shutil
from datetime import datetime
from pathlib import Path

BASE = Path('D:/quantify-per')
SNAPSHOT_DIR = BASE / 'signals' / 'tracking'
BACKTEST_OUT = BASE / 'signals' / 'tracking' / 'backtest_report.json'
BACKTEST_DB  = BASE / 'signals' / 'tracking' / 'backtest_trades.db'
BACKTEST_ARC = BASE / 'signals' / 'backtest_archive'
CYCLE_REPORT = BASE / 'signals' / 'tracking' / 'cycle_report.json'
PERIODS = ['min1', 'min5', 'min15', 'min30', 'min60', 'daily']
PERIOD_CN = {'min1':'1分钟','min5':'5分钟','min15':'15分钟','min30':'30分钟','min60':'60分钟','daily':'日线'}
MAX_BARS = 2000


def read_csv(code, period):
    f = SNAPSHOT_DIR / code / f'{period}_signals.csv'
    if not f.exists(): return []
    rows = []
    with open(f, 'r', encoding='utf-8') as fd:
        for r in csv.DictReader(fd): rows.append(r)
    return rows


def trend_dir(code):
    try:
        data = json.load(open(CYCLE_REPORT, 'r', encoding='utf-8'))
        for item in data:
            if item['code'] == code:
                return item.get('trend', {}).get('direction', 'neutral')
    except: pass
    return 'neutral'


def find_next(rows, start_i, field, target):
    for i in range(start_i, len(rows)):
        if rows[i].get(field, '').strip() == target:
            return i, rows[i]
    return None, None


def extract_raw_closes(rows, entry_field, entry_target, exit_field, exit_target):
    """
    提取每个★信号的原始区间数据（保留全程收盘价序列）
    返回: [{'entry_i': int, 'entry_time': str, 'close': [float,...], 'entry_price': float, 'min': float, 'max': float}, ...]
    """
    cycles = []
    i = 0
    while i < len(rows):
        e = find_next(rows, i, entry_field, entry_target)
        if not e[0]: break
        ei, erow = e
        try: entry_price = float(erow.get('raw_close', 0))
        except: entry_price = 0
        if entry_price <= 0: i = ei + 1; continue
        # 过滤价格异常数据（×10000精度未还原/乱码）
        if entry_price >= 100000: i = ei + 1; continue

        x = find_next(rows, ei + 1, exit_field, exit_target)
        xi = x[0] if x[0] else min(ei + 500, len(rows) - 1)
        if xi <= ei: i = ei + 1; continue

        closes = []
        for r in rows[ei:xi+1]:
            try:
                v = float(r.get('raw_close', 0))
                if v >= 100000: continue  # 跳过异常价格
                closes.append(v)
            except: pass
        if len(closes) < 2: i = ei + 1; continue

        cycles.append({
            'entry_i': ei, 'entry_time': rows[ei].get('timestamp',''),
            'close': closes, 'entry_price': closes[0],
            'min_seq': min(closes), 'max_seq': max(closes),
        })
        i = ei + 1
    return cycles


# ========== 合并1: 低点不破合并（多个原始区间→合并段） ==========

def do_merge(segments):
    merged = []
    for seg in segments:
        if len(seg) == 1:
            s = seg[0]
            merged.append({
                'entry_time': s['entry_time'], 'exit_time': s['entry_time'],
                'entry': s['entry_price'], 'exit': s['close'][-1],
                'max': s['max_seq'], 'min': s['min_seq'],
                'max_gain': (s['max_seq']-s['entry_price'])/s['entry_price']*100 if s['entry_price'] else 0,
                'max_loss': (s['min_seq']-s['entry_price'])/s['entry_price']*100 if s['entry_price'] else 0,
                'retreat': (s['min_seq']-max(s['max_seq'],s['entry_price']))/max(s['max_seq'],s['entry_price'])*100 if max(s['max_seq'],s['entry_price']) else 0,
                'total_pct': (s['close'][-1]-s['entry_price'])/s['entry_price']*100 if s['entry_price'] else 0,
                'bars': len(s['close']), 'segments': 1,
            })
        else:
            entry = seg[0]['entry_price']; exit_ = seg[-1]['close'][-1]
            max_c = max(c['max_seq'] for c in seg); min_c = min(c['min_seq'] for c in seg)
            merged.append({
                'entry_time': seg[0]['entry_time'], 'exit_time': seg[-1]['entry_time'],
                'entry': entry, 'exit': exit_, 'max': max_c, 'min': min_c,
                'max_gain': (max_c-entry)/entry*100 if entry else 0,
                'max_loss': (min_c-entry)/entry*100 if entry else 0,
                'retreat': (min_c-max(max_c,entry))/max(max_c,entry)*100 if max(max_c,entry) else 0,
                'total_pct': (exit_-entry)/entry*100 if entry else 0,
                'bars': sum(len(c['close']) for c in seg), 'segments': len(seg),
            })
    return merged


def merge_low_higher(raw_cycles, is_buy=True):
    """低点不破合并"""
    if len(raw_cycles) < 2:
        return do_merge([raw_cycles]) if raw_cycles else raw_cycles
    segs = [[raw_cycles[0]]]
    for c in raw_cycles[1:]:
        seg_min = min(x['min_seq'] for x in segs[-1])
        mb = sum(len(x['close']) for x in segs[-1]) + len(c['close'])
        ok = c['min_seq'] >= seg_min if is_buy else c['max_seq'] <= max(x['max_seq'] for x in segs[-1])
        segs[-1].append(c) if ok and mb <= MAX_BARS else segs.append([c])
    return do_merge(segs)


def merge_retrace50(raw_cycles, is_buy=True):
    """50%回调合并"""
    if len(raw_cycles) < 2:
        return do_merge([raw_cycles]) if raw_cycles else raw_cycles
    segs = [[raw_cycles[0]]]
    for c in raw_cycles[1:]:
        last = segs[-1][-1]
        mb = sum(len(x['close']) for x in segs[-1]) + len(c['close'])
        if is_buy:
            prev_gain = last['max_seq'] - last['entry_price']
            fifty = last['max_seq'] - prev_gain * 0.5 if prev_gain > 0 else last['entry_price']
            ok = (c['min_seq'] > fifty and c['max_seq'] >= last['entry_price'])
        else:
            prev_loss = last['entry_price'] - last['min_seq']
            fifty = last['min_seq'] + prev_loss * 0.5 if prev_loss > 0 else last['entry_price']
            ok = (c['max_seq'] < fifty and c['min_seq'] <= last['entry_price'])
        segs[-1].append(c) if ok and mb <= MAX_BARS else segs.append([c])
    return do_merge(segs)


# ========== 合并3: 基于每个★信号的"低点不破区间" ==========

def per_signal_low_not_broken(raw_cycles, is_buy=True):
    """
    每个原始★信号单独计算：
    以该信号entry为起点，向前找后续所有不破它entry低点的信号区间，
    合并为一个完整区间，计算该信号的涨幅。
    即：每个信号独立向未来延伸，低点被破才终止。
    """
    n = len(raw_cycles)
    per_results = []
    for i in range(n):
        sig = raw_cycles[i]
        entry = sig['entry_price']
        low_water = sig['min_seq']  # 初始低点
        
        # 从i开始向后合并所有不破low_water的区间
        low_water = sig['min_seq']   # 买方向：追踪最低低点
        high_water = sig['max_seq']   # 卖方向：追踪最低高点
        merged_close = list(sig['close'])
        j = i + 1
        while j < n:
            next_sig = raw_cycles[j]
            if is_buy:
                # 买方向：后续信号的低点不能突破已合并段中最低的低点
                if next_sig['min_seq'] < low_water:
                    break
                low_water = min(low_water, next_sig['min_seq'])
            else:
                # 卖方向：后续信号的高点不能突破已合并段中最低的高点
                # (高点必须持续降低，反弹突破high_water则结构破坏)
                if next_sig['max_seq'] > high_water:
                    break
                high_water = min(high_water, next_sig['max_seq'])
            merged_close.extend(next_sig['close'])
            j += 1
        
        # 计算这个信号的完整区间表现
        if len(merged_close) < 2:
            continue
        exit_price = merged_close[-1]
        total_pct = (exit_price - entry) / entry * 100 if entry else 0
        max_c = max(merged_close)
        min_c = min(merged_close)
        max_gain = (max_c - entry) / entry * 100 if entry else 0
        max_loss = (min_c - entry) / entry * 100 if entry else 0
        retreat = (min_c - max(max_c, entry)) / max(max_c, entry) * 100 if max(max_c, entry) else 0
        
        per_results.append({
            'entry_time': sig['entry_time'],
            'exit_time': raw_cycles[j-1]['entry_time'] if j > i else sig['entry_time'],
            'entry': entry, 'exit': exit_price,
            'max': max_c, 'min': min_c,
            'max_gain': max_gain, 'max_loss': max_loss,
            'retreat': retreat, 'total_pct': total_pct,
            'bars': len(merged_close),
            'merged_signals': j - i,
        })
    
    return per_results


# ========== 合并4: 每信号独立不合并（不跨★卖） ==========

def per_signal_no_merge(raw_cycles, is_buy=True):
    """
    每信号独立统计 — 不跨★卖合并。
    
    每个★买/★卖信号独立计算到最近反向信号的区间利润。
    不做跨★卖合并，每个 raw_cycle 就是一笔独立交易。
    
    与 per_signal_low_not_broken 的区别：
    - per_signal: 跨★卖合并所有不破新低的区间
    - no_merge: 严格按★卖切断，每信号独立
    """
    results = []
    for c in raw_cycles:
        if len(c['close']) < 2:
            continue
        entry = c['entry_price']
        exit_price = c['close'][-1]
        if not entry:
            continue
        total_pct = (exit_price - entry) / entry * 100
        max_c = max(c['close'])
        min_c = min(c['close'])
        max_gain = (max_c - entry) / entry * 100
        max_loss = (min_c - entry) / entry * 100
        retreat = (min_c - max(max_c, entry)) / max(max_c, entry) * 100 if max(max_c, entry) else 0
        
        results.append({
            'entry_time': c['entry_time'],
            'exit_time': c['entry_time'],
            'entry': entry,
            'exit': exit_price,
            'max': max_c,
            'min': min_c,
            'max_gain': max_gain,
            'max_loss': max_loss,
            'retreat': retreat,
            'total_pct': total_pct,
            'bars': len(c['close']),
            'merged_signals': 1,
            'segments': 1,
        })
    return results


# ========== 统计 ==========

def stat(cycles, is_buy=True):
    if not cycles: return None
    pcts = [c['total_pct'] for c in cycles]
    retreats = [c['retreat'] for c in cycles]
    bars_list = [c['bars'] for c in cycles]
    segs = [c.get('segments', 1) for c in cycles]
    wins = [p for p in pcts if (is_buy and p > 0) or (not is_buy and p < 0)]
    return {
        'count': len(cycles), 'raw_cycles': sum(segs),
        'win_rate': round(len(wins)/len(cycles)*100, 1) if cycles else 0,
        'avg_pct': round(sum(pcts)/len(pcts), 2) if pcts else 0,
        'max_pct': round(max(pcts), 2) if pcts else 0,
        'min_pct': round(min(pcts), 2) if pcts else 0,
        'avg_retreat': round(sum(retreats)/len(retreats), 2) if retreats else 0,
        'max_retreat': round(min(retreats), 2) if retreats else 0,
        'avg_bars': round(sum(bars_list)/len(bars_list), 1) if bars_list else 0,
        'bars_range': '%d~%d' % (min(bars_list), max(bars_list)) if bars_list else '',
        'avg_segments': round(sum(segs)/len(segs), 1) if segs else 0,
        'avg_merged_signals': round(sum(c.get('merged_signals', 1) for c in cycles)/len(cycles), 1),
    }


# ========== 合并5: 顺趋势金叉不破新低（每信号独立） ==========

def per_signal_golden_no_new_low(code, period):
    """
    不破新低版 — 基于金叉+不破波段低点，每信号独立统计
    
    只做顺趋势方向 + 有完整★买→★卖闭环的周期。
    逆势方向暂不统计（需要次级别数据）。
    
    买方向(上涨/偏多):
    - ★买→首次金叉→记录波段低点(low_water)
    - 每次后续金叉，若其波段低点≥low_water(不破新低)=新信号
    - 所有信号共享终结: 同级别★卖
    - 每信号独立: profit = (★卖close - 金叉close) / 金叉close
    
    卖方向(下跌/偏空):
    - 死叉不破新高，反向同理
    """
    rows = read_csv(code, period)
    if len(rows) < 5:
        return None, None

    t = trend_dir(code)
    bullish = t in ('bullish', 'bullish_bias')
    bearish = t in ('bearish', 'bearish_bias')
    neutral = t == 'neutral'
    if not bullish and not bearish and not neutral:
        return None, None  # unknown方向跳过

    # 次级别映射（逆势降级用）
    SUB_PERIOD = {'min5': 'min1', 'min15': 'min5', 'min30': 'min15', 'min60': 'min30', 'daily': 'min60'}
    sub_period = SUB_PERIOD.get(period)
    sub_rows = read_csv(code, sub_period) if sub_period else []
    # 次级别时间戳→索引映射（用于快速查找）
    sub_ts_map = {}
    if sub_rows:
        for idx, sr in enumerate(sub_rows):
            sub_ts_map[int(sr.get('timestamp', 0))] = idx

    def _fv(v):
        try: return float(v)
        except: return 0.0

    buy_results = []
    sell_results = []
    i = 0

    while i < len(rows):
        r = rows[i]
        bs = r.get('buy_signal', '').strip()
        ss = r.get('sell_signal', '').strip()

        if bs and (bullish or neutral):
            entry_i = i
            gc_list = []   # {'idx':, 'close':, 'band_low':}
            low_water = None
            sell_j = None
            sell_exit = None
            exit_j = None

            j = i + 1
            while j < len(rows):
                rj = rows[j]
                cross = rj.get('expma_cross', '').strip()
                ss2 = rj.get('sell_signal', '').strip()
                close_j = _fv(rj.get('raw_close', 0))

                if '金叉' in cross:
                    prev_idx = gc_list[-1]['idx'] if gc_list else entry_i
                    band_low_vals = [_fv(rows[k].get('raw_close', 0)) for k in range(prev_idx, j + 1)]
                    band_low = min(band_low_vals) if band_low_vals else close_j

                    if low_water is None:
                        low_water = band_low
                        valid = True
                    else:
                        valid = band_low >= low_water
                        low_water = min(low_water, band_low)

                    if valid:
                        gc_list.append({'idx': j, 'close': close_j, 'band_low': band_low})

                if ss2:
                    # ★卖信号出现：记录但不立即终止，继续找下一个死叉
                    sell_j = j
                    sell_exit = close_j
                if '死叉' in cross and sell_j is not None:
                    exit_j = j
                    break

                j += 1

            # 有★卖且gc_list不为空 → 计算利润（★卖→死叉或★卖→数据末）
            if sell_j is not None and gc_list:
                if exit_j is None:
                    exit_j = len(rows) - 1
                max_close = max(_fv(rows[k].get('raw_close', 0)) for k in range(gc_list[0]['idx'], exit_j + 1))
                for gc in gc_list:
                    if gc['close'] > 0:
                        pct = (max_close - gc['close']) / gc['close'] * 100
                        buy_results.append({
                            'entry_time': rows[gc['idx']].get('timestamp', ''),
                            'entry': gc['close'],
                            'exit': sell_exit,
                            'max_gain': pct,
                            'total_pct': pct,
                            'retreat': 0,
                            'bars': exit_j - gc['idx'],
                            'merged_signals': 1,
                            'segments': 1,
                        })

            # 无★卖终结但金叉后创了新高 → 也算成功（用区间最高价）
            if j >= len(rows) and gc_list and sell_j is None:
                max_close = max(_fv(rows[k].get('raw_close', 0)) for k in range(gc_list[-1]['idx'], len(rows)))
                for gc in gc_list:
                    if gc['close'] > 0:
                        pct = (max_close - gc['close']) / gc['close'] * 100
                        buy_results.append({
                            'entry_time': rows[gc['idx']].get('timestamp', ''),
                            'entry': gc['close'],
                            'exit': max_close,
                            'max_gain': pct,
                            'total_pct': pct,
                            'retreat': 0,
                            'bars': len(rows) - gc['idx'],
                            'merged_signals': 1,
                            'segments': 1,
                        })

            i += 1  # 每个★买独立处理，不跳过同段内后续★买

        elif ss and (bearish or neutral):
            entry_i = i
            dc_list = []   # {'idx':, 'close':, 'band_high':}
            high_water = None
            buy_j = None
            exit_j = None

            j = i + 1
            while j < len(rows):
                rj = rows[j]
                cross = rj.get('expma_cross', '').strip()
                bs2 = rj.get('buy_signal', '').strip()
                close_j = _fv(rj.get('raw_close', 0))

                if '死叉' in cross:
                    prev_idx = dc_list[-1]['idx'] if dc_list else entry_i
                    band_high_vals = [_fv(rows[k].get('raw_close', 0)) for k in range(prev_idx, j + 1)]
                    band_high = max(band_high_vals) if band_high_vals else close_j

                    if high_water is None:
                        high_water = band_high
                        valid = True
                    else:
                        valid = band_high <= high_water   # 不破新高
                        high_water = max(high_water, band_high)

                    if valid:
                        dc_list.append({'idx': j, 'close': close_j, 'band_high': band_high})

                if bs2:
                    # ★买信号出现：记录但不立即终止，继续找下一个金叉
                    buy_j = j
                if '金叉' in cross and buy_j is not None:
                    exit_j = j
                    break

                j += 1

            # 有★买且dc_list不为空 → 计算利润（★买→金叉或★买→数据末）
            if buy_j is not None and dc_list:
                if exit_j is None:
                    exit_j = len(rows) - 1
                min_close = min(_fv(rows[k].get('raw_close', 0)) for k in range(dc_list[0]['idx'], exit_j + 1))
                for dc in dc_list:
                    if dc['close'] > 0:
                        pct = (min_close - dc['close']) / dc['close'] * 100
                        sell_results.append({
                            'entry_time': rows[dc['idx']].get('timestamp', ''),
                            'entry': dc['close'],
                            'exit': _fv(rows[buy_j].get('raw_close', 0)),
                            'max_gain': pct,
                            'total_pct': pct,
                            'retreat': 0,
                            'bars': exit_j - dc['idx'],
                            'merged_signals': 1,
                            'segments': 1,
                        })

            # 无★买终结但死叉后创了新低 → 也算成功（用区间最低价，跌了=赚）
            if j >= len(rows) and dc_list and buy_j is None:
                min_close = min(_fv(rows[k].get('raw_close', 0)) for k in range(dc_list[-1]['idx'], len(rows)))
                for dc in dc_list:
                    if dc['close'] > 0:
                        pct = (min_close - dc['close']) / dc['close'] * 100
                        sell_results.append({
                            'entry_time': rows[dc['idx']].get('timestamp', ''),
                            'entry': dc['close'],
                            'exit': min_close,
                            'max_gain': pct,
                            'total_pct': pct,
                            'retreat': 0,
                            'bars': len(rows) - dc['idx'],
                            'merged_signals': 1,
                            'segments': 1,
                        })

            i += 1  # 每个★卖独立处理，不跳过同段内后续★卖

        elif ss and bullish and sub_rows:
            # 逆势降级：上涨趋势中的卖信号 → 去次级别找★买做终结
            ss_ts = int(rows[i].get('timestamp', 0))
            # 在次级别中找卖信号时间之后最近的★买
            sub_exit_idx = None
            for sidx in range(len(sub_rows)):
                sts = int(sub_rows[sidx].get('timestamp', 0))
                if sts > ss_ts and sub_rows[sidx].get('buy_signal', '').strip():
                    sub_exit_idx = sidx
                    break
            if sub_exit_idx is not None:
                # 找本周期中卖信号之后的死叉（作为起点）
                j = i + 1
                while j < len(rows):
                    cross = rows[j].get('expma_cross', '').strip()
                    if '死叉' in cross:
                        entry_price = _fv(rows[j].get('raw_close', 0))
                        exit_price = _fv(sub_rows[sub_exit_idx].get('raw_close', 0))
                        if entry_price > 0:
                            pct = (exit_price - entry_price) / entry_price * 100
                            sell_results.append({
                                'entry_time': rows[j].get('timestamp', ''),
                                'entry': entry_price,
                                'exit': exit_price,
                                'total_pct': pct,
                                'retreat': 0,
                                'bars': 1,
                                'merged_signals': 1,
                                'segments': 1,
                            })
                        break
                    j += 1
            i += 1

        elif bs and bearish and sub_rows:
            # 逆势降级：下跌趋势中的买信号 → 去次级别找★卖做终结
            bs_ts = int(rows[i].get('timestamp', 0))
            sub_exit_idx = None
            for sidx in range(len(sub_rows)):
                sts = int(sub_rows[sidx].get('timestamp', 0))
                if sts > bs_ts and sub_rows[sidx].get('sell_signal', '').strip():
                    sub_exit_idx = sidx
                    break
            if sub_exit_idx is not None:
                j = i + 1
                while j < len(rows):
                    cross = rows[j].get('expma_cross', '').strip()
                    if '金叉' in cross:
                        entry_price = _fv(rows[j].get('raw_close', 0))
                        exit_price = _fv(sub_rows[sub_exit_idx].get('raw_close', 0))
                        if entry_price > 0:
                            pct = (exit_price - entry_price) / entry_price * 100
                            buy_results.append({
                                'entry_time': rows[j].get('timestamp', ''),
                                'entry': entry_price,
                                'exit': exit_price,
                                'total_pct': pct,
                                'retreat': 0,
                                'bars': 1,
                                'merged_signals': 1,
                                'segments': 1,
                            })
                        break
                    j += 1
            i += 1

        else:
            i += 1

    bs_stat = stat(buy_results, True) if buy_results else None
    ss_stat = stat(sell_results, False) if sell_results else None
    return bs_stat, ss_stat


# ========== SQLite 持久化 ==========

def _ensure_db():
    BACKTEST_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.execute('''
        CREATE TABLE IF NOT EXISTS signal_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date    TEXT NOT NULL,
            code        TEXT NOT NULL,
            period      TEXT NOT NULL,
            signal_type TEXT NOT NULL,  -- 'buy' or 'sell'
            entry_time  TEXT NOT NULL,
            exit_time   TEXT NOT NULL,
            entry_price REAL,
            exit_price  REAL,
            total_pct   REAL,
            max_gain    REAL,
            max_loss    REAL,
            retreat     REAL,
            bars        INTEGER,
            merge_count INTEGER,
            is_win      INTEGER,
            UNIQUE(code, period, signal_type, entry_time)
        )
    ''')
    conn.commit()
    return conn


def _save_per_signals(conn, run_date, code, period, results, signal_type):
    """写入★信号级别的每笔交易结果"""
    is_buy = (signal_type == 'buy')
    for r in results:
        is_win = 1 if ((is_buy and r['total_pct'] > 0) or (not is_buy and r['total_pct'] < 0)) else 0
        try:
            conn.execute('''
                INSERT OR IGNORE INTO signal_trades
                (run_date, code, period, signal_type, entry_time, exit_time,
                 entry_price, exit_price, total_pct, max_gain, max_loss,
                 retreat, bars, merge_count, is_win)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                run_date, code, period, signal_type,
                r['entry_time'], r['exit_time'],
                r['entry'], r['exit'], r['total_pct'],
                r['max_gain'], r['max_loss'], r['retreat'],
                r['bars'], r.get('merged_signals', 1), is_win
            ))
        except Exception as e:
            print(f'  [DB] 跳过 {code} {period} {signal_type} {r["entry_time"]}: {e}')


def save_backtest_to_db(code, period, buy_per, sell_per):
    """将★信号级别的回测结果写入 SQLite"""
    run_date = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn = _ensure_db()
    try:
        _save_per_signals(conn, run_date, code, period, buy_per, 'buy')
        _save_per_signals(conn, run_date, code, period, sell_per, 'sell')
        conn.commit()
    finally:
        conn.close()


def query_backtest_sql(sql, params=None):
    """查询回测交易数据库，返回列表[dict, ...]"""
    conn = _ensure_db()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params or [])
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ========== 每日快照归档 ==========

def archive_backtest_report():
    """将 backtest_report.json 归档到 backtest_archive/YYYY-MM-DD.json"""
    if not BACKTEST_OUT.exists():
        return
    today = datetime.now().strftime('%Y-%m-%d')
    BACKTEST_ARC.mkdir(parents=True, exist_ok=True)
    dst = BACKTEST_ARC / f'{today}.json'
    shutil.copy2(str(BACKTEST_OUT), str(dst))
    return dst


# ========== 累积胜率曲线 ==========

def cumulative_win_curve(code, period='min30', signal_type='buy'):
    """从 SQLite 读取某标的某周期★信号，按时间排序，计算累积胜率"""
    rows = query_backtest_sql('''
        SELECT entry_time, total_pct, is_win
        FROM signal_trades
        WHERE code=? AND period=? AND signal_type=?
        ORDER BY entry_time ASC
    ''', (code, period, signal_type))
    if not rows:
        return {'code': code, 'period': period, 'type': signal_type, 'trades': 0, 'points': []}
    
    wins = 0
    points = []
    for i, r in enumerate(rows):
        if r['is_win']: wins += 1
        cum_wr = round(wins / (i + 1) * 100, 1)
        points.append({
            'idx': i + 1,
            'entry_time': r['entry_time'],
            'pct': r['total_pct'],
            'cum_win_rate': cum_wr,
        })
    
    return {
        'code': code, 'period': period, 'type': signal_type,
        'trades': len(rows), 'total_wins': wins,
        'final_cum_wr': round(wins / len(rows) * 100, 1) if rows else 0,
        'points': points,
    }


def backtest_by_trend(code, period):
    rows = read_csv(code, period)
    if len(rows) < 5: return None
    t = trend_dir(code)

    # 提取原始闭环数据（每个★信号的完整价格序列）
    buy_raw = extract_raw_closes(rows, 'buy_signal', '★买', 'sell_signal', '★卖')
    sell_raw = extract_raw_closes(rows, 'sell_signal', '★卖', 'buy_signal', '★买')

    # 三种方式
    # 1. 低点不破合并段
    buy_low = merge_low_higher(buy_raw, True)
    sell_low = merge_low_higher(sell_raw, False)
    
    # 2. 50%回调合并段
    buy_50 = merge_retrace50(buy_raw, True)
    sell_50 = merge_retrace50(sell_raw, False)
    
    # 3. 每个★信号独立低点不破区间（跨★卖合并）
    buy_per = per_signal_low_not_broken(buy_raw, True)
    sell_per = per_signal_low_not_broken(sell_raw, False)

    # 4. 每信号独立不合并（不跨★卖，v3.4 新增）
    buy_nomerge = per_signal_no_merge(buy_raw, True)
    sell_nomerge = per_signal_no_merge(sell_raw, False)

    # 5. 顺趋势金叉不破新低（v3.5 新增）
    bs_golden, ss_golden = per_signal_golden_no_new_low(code, period)
    
    # 持久化★信号级别的每笔交易
    save_backtest_to_db(code, period, buy_per, sell_per)

    return {
        'trend': t,
        'merge_low': {
            'buy': stat(buy_low, True),
            'sell': stat(sell_low, False),
        },
        'merge_50': {
            'buy': stat(buy_50, True),
            'sell': stat(sell_50, False),
        },
        'per_signal': {
            'buy': stat(buy_per, True),
            'sell': stat(sell_per, False),
        },
        'no_merge': {
            'buy': stat(buy_nomerge, True),
            'sell': stat(sell_nomerge, False),
        },
        'golden_no_new_low': {
            'buy': bs_golden,
            'sell': ss_golden,
        },
    }


def get_all_codes():
    return [d.name for d in SNAPSHOT_DIR.iterdir() if d.is_dir() and d.name.startswith(('sh','sz'))]


def _fmt(s, is_buy=True, label=''):
    if not s or s['count'] == 0: return ''
    t = 'b' if is_buy else 's'
    return '%s %s %d段(%d次) %.0f%%均%+.1f%%(%+.1f~%+.1f%%) 均合%.1f次' % (
        label, t, s['count'], s.get('raw_cycles',0), s['win_rate'],
        s['avg_pct'], s['min_pct'], s['max_pct'],
        s.get('avg_merged_signals', 1))


def backtest_all():
    codes = get_all_codes()
    report = {}
    for code in codes:
        code_report = {}
        for period in PERIODS:
            r = backtest_by_trend(code, period)
            if r: code_report[period] = r
        if code_report: report[code] = code_report

    with open(BACKTEST_OUT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    # 归档
    arc_path = archive_backtest_report()

    print(f'回测完成: {len(report)} 只标的')
    if arc_path:
        print(f'归档: {arc_path.name}')
    print()
    
    for code in sorted(report.keys()):
        t = trend_dir(code)
        print(f'\n{code} ({t}):')
        for period in [p for p in PERIODS if p in report[code]]:
            r = report[code][period]
            print(f'  {PERIOD_CN[period]:6}:')
            for tag, loc in [('[低点]','merge_low'), ('[50%]','merge_50'), ('[★信号]','per_signal')]:
                b = _fmt(r[loc]['buy'], True, tag)
                s = _fmt(r[loc]['sell'], False, tag)
                if b: print(f'    {b}')
                if s: print(f'    {s}')
    
    # 输出累积胜率摘要
    print('\n── 累积胜率曲线（★信号法）──')
    for code in sorted(report.keys()):
        best_p = None
        for p in PERIODS:
            if p in report.get(code, {}):
                best_p = p; break
        if not best_p: continue
        curve = cumulative_win_curve(code, best_p, 'buy')
        if curve['trades'] >= 3:
            print(f'{code} {best_p} ★买: {curve["trades"]}次 累积胜率{curve["final_cum_wr"]}%')
        curve = cumulative_win_curve(code, best_p, 'sell')
        if curve['trades'] >= 3:
            print(f'{code} {best_p} ★卖: {curve["trades"]}次 累积胜率{curve["final_cum_wr"]}%')

    return report


if __name__ == '__main__':
    backtest_all()

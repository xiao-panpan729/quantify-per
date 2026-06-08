# -*- coding: utf-8 -*-
"""
信号级回测引擎 v4.0 — 基于 monitor 三级过滤 (MA级/金叉级/共振级)

核心逻辑:
  以 min5 ★买 为锚点 → 检查当时环境条件 → 分类到 MA级/金叉级/共振级/弃牌
  → 跟踪至 ★卖 出场 → 按级别统计胜率

v4.0 — 2026-05-28
"""

import csv, json, sqlite3, shutil, os, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import PROJECT_ROOT, DAY_PRICE_FACTOR, MIN_PRICE_FACTOR
from cycle_engine.utils import safe_float
from tools.volume_leader.filter_engine import (
    check_ma_chain, check_expma_golden,
    check_no_recent_death, check_no_recent_golden,
    check_close_below_ma, check_close_above_ma, check_pe_gate,
)

BASE = Path(PROJECT_ROOT)
SNAPSHOT_DIR = BASE / 'signals' / 'tracking'
BACKTEST_OUT = BASE / 'signals' / 'tracking' / '_signals' / 'backtest_report.json'
BACKTEST_DB  = BASE / 'signals' / 'tracking' / '_signals' / 'backtest_trades.db'
BACKTEST_ARC = BASE / 'signals' / 'backtest_archive'
CYCLE_REPORT = BASE / 'signals' / 'tracking' / '_signals' / 'cycle_report.json'
PERIODS = ['min5', 'min15', 'min30', 'min60', 'daily']
PERIOD_CN = {'min5':'5分钟','min15':'15分钟','min30':'30分钟','min60':'60分钟','daily':'日线'}

# ========== 数据加载 ==========

def read_csv(code, period):
    """读取信号 CSV，返回 dict list"""
    f = SNAPSHOT_DIR / code / f'{period}_signals.csv'
    if not f.exists(): return []
    rows = []
    with open(f, 'r', encoding='utf-8') as fd:
        for r in csv.DictReader(fd): rows.append(r)
    return rows


def _find_row_by_ts(rows, target_ts):
    """在 rows 中找到 timestamp <= target_ts 的最近一条"""
    target = int(target_ts)
    best = None
    for r in rows:
        ts = int(r.get('timestamp', 0) or 0)
        if ts <= target:
            best = r
        else:
            break
    return best


def _find_daily_by_date(rows, date_str):
    """在日线 rows 中找到匹配 date 的 bar"""
    for r in rows:
        if r.get('date', '').strip() == date_str:
            return r
    return None


# ========== 核心分类逻辑 ==========


def _daily_structure_uptrend(daily_rows, date_str):
    """日线结构上涨趋势检查：低点抬高+高点抬高（近20根 vs 前20根）"""
    idx = -1
    for i, r in enumerate(daily_rows):
        if r.get('date', '').strip() == date_str:
            idx = i
            break
    if idx < 40:
        return True  # 数据不足，默认通过
    fac = DAY_PRICE_FACTOR
    try:
        cur_low = min(float(daily_rows[k].get('low', 0) or 0) for k in range(idx-19, idx+1)) / fac
        prev_low = min(float(daily_rows[k].get('low', 0) or 0) for k in range(idx-39, idx-19)) / fac
        cur_high = max(float(daily_rows[k].get('high', 0) or 0) for k in range(idx-19, idx+1)) / fac
        prev_high = max(float(daily_rows[k].get('high', 0) or 0) for k in range(idx-39, idx-19)) / fac
    except (ValueError, TypeError):
        return True
    low_ok = cur_low > prev_low
    high_ok = cur_high > prev_high
    return low_ok and high_ok


def _try_ma_entry(bar, idx, min5_rows, period_data, delay_window=0):
    """
    MA级入场检查（独立版本，不升级金叉级）。
    delay_window=0 → 严格版（★买当根MA排列）
    delay_window=12 → 延期版（12根内等待MA排好）
    返回 (entry_idx, entry_price) 或 None
    """
    date_str = bar.get('date', '').strip()
    ts_str = bar.get('timestamp', '').strip()
    ts = int(ts_str) if ts_str else 0
    daily_rows = period_data.get('daily', [])
    min60_rows = period_data.get('min60', [])
    entry_idx = idx

    try:
        close_raw = float(bar.get('close', 0) or 0)
    except (ValueError, TypeError):
        return None
    if close_raw <= 0:
        return None
    entry_price = _factor_price(close_raw)

    # ── 1. MA排列检查 ──
    if not check_ma_chain(bar):
        if delay_window == 0:
            return None  # 严格版：当根不通过即弃
        # 延期版：向后扫描
        saved = False
        for offset in range(1, min(delay_window + 1, len(min5_rows) - idx)):
            nr = min5_rows[idx + offset]
            if check_ma_chain(nr):
                bar = nr
                entry_idx = idx + offset
                date_str = bar.get('date', '').strip()
                ts = int(bar.get('timestamp', '0') or '0')
                close_raw = float(bar.get('close', 0) or 0)
                entry_price = _factor_price(close_raw)
                saved = True
                break
        if not saved:
            return None

    # ── 2. 15分钟无死叉（代替原来5分钟20根检查） ──
    if _has_death_cross_15min(period_data, ts):
        return None

    # ── 3. 60分黄线上 ──
    if min60_rows:
        m60bar = _find_row_by_ts(min60_rows, ts)
        if m60bar:
            if not check_close_above_ma(m60bar, 'expma50'):
                return None
        else:
            return None
    else:
        return None

    # ── 4. PE门禁 ──
    if daily_rows:
        dbar = _find_daily_by_date(daily_rows, date_str)
        if dbar:
            if not check_pe_gate(dbar):
                return None

    # ── 5. 日线Zone ──
    if daily_rows:
        dbar = _find_daily_by_date(daily_rows, date_str)
        if dbar:
            if not check_close_above_ma(dbar, 'expma50'):
                return None
    else:
        return None

    # ── 6. 日线结构检查 ──
    if not _daily_structure_uptrend(daily_rows, date_str):
        return None

    # ── 7. 低点抬高结构检查 ──
    gc_positions = []
    for k in range(entry_idx - 1, max(entry_idx - 80, 0), -1):
        cross = (min5_rows[k].get('expma_cross', '') or '').strip()
        if cross == '金叉':
            gc_positions.append(k)
            if len(gc_positions) >= 2:
                break
    if len(gc_positions) >= 2:
        fac = MIN_PRICE_FACTOR
        cur_low = min(float(min5_rows[k].get('low', 0) or 0) for k in range(gc_positions[0], entry_idx + 1))
        prev_low = min(float(min5_rows[k].get('low', 0) or 0) for k in range(gc_positions[1], gc_positions[0] + 1))
        if (cur_low / fac) < (prev_low / fac):
            return None

    return entry_idx, entry_price


def _try_jincha_entry(bar, idx, min5_rows, period_data, scan_window=30):
    """
    金叉级独立入场：★买触发 → 扫描N根内出金叉 → 金叉bar收盘入场。
    环境过滤在★买时判断，入场价=金叉bar close。
    不依赖MA排列检查，独立于MA级。
    返回 (entry_idx, entry_price) 或 None
    """
    date_str = bar.get('date', '').strip()
    ts_str = bar.get('timestamp', '').strip()
    ts = int(ts_str) if ts_str else 0
    daily_rows = period_data.get('daily', [])
    min60_rows = period_data.get('min60', [])

    # ── 环境检查（在★买时判断） ──

    # A. 15分钟无死叉（代替原来5分钟20根检查）
    if _has_death_cross_15min(period_data, ts):
        return None

    # B. 60分黄线上
    if min60_rows:
        m60bar = _find_row_by_ts(min60_rows, ts)
        if m60bar:
            if not check_close_above_ma(m60bar, 'expma50'):
                return None
        else:
            return None
    else:
        return None

    # C. PE门禁
    if daily_rows:
        dbar = _find_daily_by_date(daily_rows, date_str)
        if dbar:
            if not check_pe_gate(dbar):
                return None

    # D. 日线Zone
    if daily_rows:
        dbar = _find_daily_by_date(daily_rows, date_str)
        if dbar:
            if not check_close_above_ma(dbar, 'expma50'):
                return None
    else:
        return None

    # E. 日线结构检查
    if not _daily_structure_uptrend(daily_rows, date_str):
        return None

    # ── 扫描金叉 ──
    for offset in range(1, min(scan_window + 1, len(min5_rows) - idx)):
        nr = min5_rows[idx + offset]
        cross = (nr.get('expma_cross', '') or '').strip()
        if cross == '金叉':
            jincha_idx = idx + offset

            # 结构检查：低点抬高（从金叉位置看）
            gc_positions = [jincha_idx]
            for k in range(jincha_idx - 1, max(jincha_idx - 80, 0), -1):
                cross2 = (min5_rows[k].get('expma_cross', '') or '').strip()
                if cross2 == '金叉':
                    gc_positions.append(k)
                    if len(gc_positions) >= 2:
                        break
            if len(gc_positions) >= 2:
                fac = MIN_PRICE_FACTOR
                cur_low = min(float(min5_rows[k].get('low', 0) or 0) for k in range(gc_positions[0], jincha_idx + 1))
                prev_low = min(float(min5_rows[k].get('low', 0) or 0) for k in range(gc_positions[1], gc_positions[0] + 1))
                if (cur_low / fac) < (prev_low / fac):
                    continue  # 低点降低，找下一个金叉

            # 入场价：金叉bar close
            gc_close_raw = float(nr.get('close', 0) or 0)
            entry_price = _factor_price(gc_close_raw)
            return jincha_idx, entry_price

    return None


def classify_buy_signal(code, min5_bar, min5_idx, min5_rows, period_data, ma_delay_window=12):
    """
    对 min5 某根 ★买 bar 执行 monitor 的三级过滤分类。
    与 monitor.py Step 4.5 一致: MA级只检查 60分位置(非日线强势)。

    ma_delay_window: ★买后多少根K线内MA排列理顺算有效入场（默认12根≈60分钟）
        实验#24验证：30分钟内排好158笔胜率63.3%，30-60分钟138笔胜率60.1%
        均高于原MA级当根通过的50.0%胜率。

    Returns:
        filter_level: 'discard' | 'ma' | 'jincha' | 'resonance'
        reason: str
        entry_price: float
        entry_idx: int (实际入场bar index，启用追赶期时可能与min5_idx不同)
    """
    bar = min5_bar
    date_str = bar.get('date', '').strip()
    ts_str = bar.get('timestamp', '').strip()
    ts = int(ts_str) if ts_str else 0
    daily_rows = period_data.get('daily', [])
    min60_rows = period_data.get('min60', [])
    min15_rows = period_data.get('min15', [])
    min30_rows = period_data.get('min30', [])
    entry_idx = min5_idx

    try:
        close_raw = float(bar.get('close', 0) or 0)
    except (ValueError, TypeError):
        return 'discard', '价格异常', 0, min5_idx
    if close_raw <= 0:
        return 'discard', '价格异常', 0, min5_idx
    close = close_raw / MIN_PRICE_FACTOR if close_raw > 100 else close_raw
    entry_price = close

    # ── 1. MA 排列检查 (MA级基本条件) ──
    try:
        ma5 = float(bar.get('ma5', 0) or 0)
        ma10 = float(bar.get('ma10', 0) or 0)
        ma20 = float(bar.get('ma20', 0) or 0)
    except (ValueError, TypeError):
        return 'discard', 'MA数据异常', entry_price, min5_idx
    if not (ma5 > ma10 > ma20):
        # ★买当根排列不通过 → MA排列追赶期: 向后扫描N根
        saved = False
        for offset in range(1, min(ma_delay_window + 1, len(min5_rows) - min5_idx)):
            nr = min5_rows[min5_idx + offset]
            try:
                nma5 = float(nr.get('ma5', 0) or 0)
                nma10 = float(nr.get('ma10', 0) or 0)
                nma20 = float(nr.get('ma20', 0) or 0)
                if nma5 > nma10 > nma20:
                    # 用MA理顺的bar替换原★买bar
                    bar = nr
                    entry_idx = min5_idx + offset
                    date_str = bar.get('date', '').strip()
                    ts_str = bar.get('timestamp', '').strip()
                    ts = int(ts_str) if ts_str else 0
                    close_raw = float(bar.get('close', 0) or 0)
                    entry_price = close_raw / MIN_PRICE_FACTOR if close_raw > 100 else close_raw
                    saved = True
                    break
            except (ValueError, TypeError):
                pass
        if not saved:
            return 'discard', f'MA排列不满足(追赶{ma_delay_window}根未排好)', entry_price, min5_idx

    # ── 2. 20根内无死叉 (用entry_idx替代min5_idx，追赶期后检查新入场位置) ──
    has_death = False
    for j in range(min(20, entry_idx)):
        pos = entry_idx - j
        if pos < 0: break
        cross = (min5_rows[pos].get('expma_cross', '') or '').strip()
        if cross == '死叉':
            has_death = True
            break
        if cross == '金叉':
            break
    if has_death:
        return 'discard', '20根内存在死叉', entry_price, entry_idx

    # ── 3. 60分钟在expma50黄线上方 (与monitor一致) ──
    min60_ok = False
    if min60_rows:
        m60bar = _find_row_by_ts(min60_rows, ts)
        if m60bar:
            try:
                c60 = float(m60bar.get('close', 0) or 0)
                e50_60 = float(m60bar.get('expma50', 0) or 0)
                min60_ok = c60 > e50_60
            except (ValueError, TypeError):
                pass
    if not min60_ok:
        return 'discard', '60分在黄线下', entry_price, entry_idx

    # ── 4. PE门禁: MA级 → 日线PE非升熵 (pe_chg_5 >= -0.02) ──
    if daily_rows:
        dbar = _find_daily_by_date(daily_rows, date_str)
        if dbar:
            try:
                pe_chg = float(dbar.get('pe_chg_5', 0) or 0)
                if pe_chg < -0.02:
                    return 'discard', f'PE门禁(pe_chg_5={pe_chg:.3f})', entry_price, entry_idx
            except (ValueError, TypeError):
                pass

    # ── 5. 日线Zone过滤: close > expma50 (仅strong/secondary zone入场) ──
    zone_ok = False
    if daily_rows:
        dbar = _find_daily_by_date(daily_rows, date_str)
        if dbar:
            try:
                c_d = float(dbar.get('close', 0) or 0)
                e50_d = float(dbar.get('expma50', 0) or 0)
                zone_ok = c_d > e50_d
            except (ValueError, TypeError):
                pass
    if not zone_ok:
        return 'discard', '日线Zone过滤', entry_price, entry_idx

    # ── 5.5 日线结构检查：低点抬高+高点抬高（确认上涨趋势大环境） ──
    if not _daily_structure_uptrend(daily_rows, date_str):
        return 'discard', '日线结构过滤(低点未抬高+高点未抬高)', entry_price, entry_idx

    # ── 6. 结构检查：低点抬高（金叉分段对比） ──
    # 找入场前最近两个金叉，比较当前段低点 >= 上一段低点
    # 找不到两个金叉则默认通过（不做结构检查）
    gc_positions = []
    for k in range(entry_idx - 1, max(entry_idx - 80, 0), -1):
        cross = (min5_rows[k].get('expma_cross', '') or '').strip()
        if cross == '金叉':
            gc_positions.append(k)
            if len(gc_positions) >= 2:
                break

    if len(gc_positions) >= 2:
        cur_low = min(float(min5_rows[k].get('low', 0) or 0) for k in range(gc_positions[0], entry_idx + 1))
        prev_low = min(float(min5_rows[k].get('low', 0) or 0) for k in range(gc_positions[1], gc_positions[0] + 1))
        fac = MIN_PRICE_FACTOR
        if (cur_low / fac) < (prev_low / fac):
            return 'discard', '结构检查(低点降低)', entry_price, entry_idx

# ── 7. 升级检查: 5分钟EXPMA金叉 → 金叉级（买） ──
    # 按层层递进: ★买→MA理顺(MA级)→5分钟EXPMA金叉(金叉级)→15分钟金叉(共振级)
    filter_level = 'ma'
    reason = 'MA级'
    expma12 = float(bar.get('expma12', 0) or 0)
    expma50 = float(bar.get('expma50', 0) or 0)

    if expma12 > expma50:
        filter_level = 'jincha'
        reason = '金叉级'
        # 金叉级入场价用金叉bar的close
        for k in range(entry_idx, max(entry_idx - 30, 0), -1):
            cross = (min5_rows[k].get('expma_cross', '') or '').strip()
            if cross == '金叉':
                gc_close = float(min5_rows[k].get('close', 0) or 0)
                entry_price = _factor_price(gc_close)
                entry_idx = k
                break


        # ── 共振检查: 15/30分K线内都有金叉/★买（近50根） ──
        m15_signal = False
        m30_signal = False
        if min15_rows:
            near_idx = -1
            for j in range(len(min15_rows)):
                cts = int(min15_rows[j].get('timestamp', 0) or 0)
                if cts <= ts:
                    near_idx = j
            if near_idx >= 0:
                for j in range(max(0, near_idx - 49), near_idx + 1):
                    cross = (min15_rows[j].get('expma_cross', '') or '').strip()
                    bs = min15_rows[j].get('buy_signal', '').strip()
                    if cross == '金叉' or bs:
                        m15_signal = True
                        break
        if min30_rows:
            near_idx = -1
            for j in range(len(min30_rows)):
                cts = int(min30_rows[j].get('timestamp', 0) or 0)
                if cts <= ts:
                    near_idx = j
            if near_idx >= 0:
                for j in range(max(0, near_idx - 49), near_idx + 1):
                    cross = (min30_rows[j].get('expma_cross', '') or '').strip()
                    bs = min30_rows[j].get('buy_signal', '').strip()
                    if cross == '金叉' or bs:
                        m30_signal = True
                        break
        if m15_signal and m30_signal:
            filter_level = 'resonance'
            reason = '共振级(15+30双信号)'

    return filter_level, reason, entry_price, entry_idx


def _factor_price(v):
    """分钟线原始值→实价，日线已除则不动"""
    return v / MIN_PRICE_FACTOR if v > 100 else v


def _calc_entry_band_low(all_rows, entry_idx, lookback=80):
    """入场时计算波段最低点：最近金叉→开仓bar之间的最低low（本上涨波段起点）"""
    best = float(all_rows[entry_idx]['low'])
    for i in range(entry_idx - 1, max(entry_idx - lookback, -1), -1):
        lo = float(all_rows[i]['low'])
        if lo < best:
            best = lo
        cross = (all_rows[i].get('expma_cross', '') or '').strip()
        if cross == '金叉':
            break
    return best


def _no_recent_golden(rows, i, n=20):
    """→ filter_engine.check_no_recent_golden"""
    return check_no_recent_golden(rows, i, n)


def _min15_below_ema50(code, ts):
    """15分钟 close < expma50（黄线下方）"""
    from tools.volume_leader.filter_engine import check_close_below_ma_generic
    rows = read_csv(code, 'min15')
    if not rows:
        return False
    bar = _find_row_by_ts(rows, ts)
    if not bar:
        return False
    return check_close_below_ma_generic(bar, 'expma50')


def _has_death_cross_15min(period_data, ts, lookback=20):
    """检查15分钟周期：★买前最近lookback根内是否有死叉（金叉在前则放行）"""
    min15_rows = period_data.get('min15', [])
    if not min15_rows:
        return True
    near_idx = -1
    for i, r in enumerate(min15_rows):
        rts = int(r.get('timestamp', 0) or 0)
        if rts <= ts:
            near_idx = i
        else:
            break
    if near_idx < 0:
        return True
    for j in range(min(lookback, near_idx + 1)):
        pos = near_idx - j
        cross = (min15_rows[pos].get('expma_cross', '') or '').strip()
        if cross == '死叉':
            return True
        if cross == '金叉':
            return False
    return False


def track_trade(min5_rows, buy_idx, filter_level='ma', code=None):
    """
    从 ★买 入场追踪交易（三层卖出逻辑，对齐 monitor Step 4.6）:
      止损层: low < band_low → 强制离场（保护性）
      减仓卖层: ★卖 + close<MA5 + 无金叉(20根) + 15分黄线下 → 减仓卖
      无条件卖层: 死叉 + 无金叉(20根) + 15分黄线下 → 强制全平（兜底）
    """
    if buy_idx >= len(min5_rows) - 1:
        return None
    entry_bar = min5_rows[buy_idx]
    try:
        entry_price_raw = float(entry_bar.get('close', 0) or 0)
    except (ValueError, TypeError):
        return None
    if entry_price_raw <= 0:
        return None
    entry_price = _factor_price(entry_price_raw)
    entry_time = entry_bar.get('timestamp', '')

    # 止损线: band_low（本上涨波段最低点）
    band_low_raw = _calc_entry_band_low(min5_rows, buy_idx)
    band_low = _factor_price(band_low_raw)

    # 逐根 bar 扫描
    exit_idx = None
    exit_price = None
    exit_reason = None
    cci_has_extreme = False  # CCI极值状态：出现>=200后启用白线防守

    for j in range(buy_idx + 1, len(min5_rows)):
        r = min5_rows[j]
        try:
            raw_low = float(r.get('low', 0) or 0)
            raw_close = float(r.get('close', 0) or 0)
        except (ValueError, TypeError):
            continue
        low = _factor_price(raw_low)
        close = _factor_price(raw_close)
        if low <= 0 or close <= 0:
            continue

        # ── 止损层: 跌破 band_low → 强制离场 ──
        if low < band_low:
            exit_idx = j
            exit_price = low
            exit_reason = '止损'
            break

        # ── ★卖止盈层: ★卖信号 | CCI极值>=350 | CCI极值后跌破白线（金叉级/共振级） ──
        if filter_level in ('jincha', 'resonance'):
            sell_signal = r.get('sell_signal', '').strip()
            try:
                cci_val = float(r.get('cci', 0) or 0)
            except (ValueError, TypeError):
                cci_val = 0

            # 跟踪CCI极值状态：出现>=200后启用白线防守
            if cci_val >= 200:
                cci_has_extreme = True

            expma12_raw = float(r.get('expma12', 0) or 0)
            expma12 = _factor_price(expma12_raw) if expma12_raw > 0 else 0
            below_white = expma12 > 0 and close < expma12

            if sell_signal == '★卖':
                exit_idx = j
                exit_price = close
                exit_reason = '★卖止盈'
                break
            if cci_val >= 350:
                exit_idx = j
                exit_price = close
                exit_reason = 'CCI极值止盈'
                break
            if cci_has_extreme and below_white:
                exit_idx = j
                exit_price = close
                exit_reason = '跌破白线'
                break

        # ── 减仓卖层: ★卖 + close<MA5 + 无金叉(20根) + 15分黄线下 ──
        sell_signal = r.get('sell_signal', '').strip()
        if sell_signal == '★卖':
            if check_close_below_ma(r):
                if code and _no_recent_golden(min5_rows, j, 20) and _min15_below_ema50(code, r.get('timestamp', '')):
                    exit_idx = j
                    exit_price = close
                    exit_reason = '减仓卖'
                    break

        # ── 无条件卖层: 死叉 + 无金叉(20根) + 15分黄线下 → 强制全平（兜底） ──
        cross = (r.get('expma_cross', '') or '').strip()
        if cross == '死叉':
            if code and _no_recent_golden(min5_rows, j, 20) and _min15_below_ema50(code, r.get('timestamp', '')):
                exit_idx = j
                exit_price = close
                exit_reason = '无条件卖'
                break

    if exit_idx is None:
        exit_idx = len(min5_rows) - 1
        exit_price = _factor_price(float(min5_rows[-1].get('close', 0) or 0))
        exit_reason = '数据尾'

    # 区间统计
    closes = []
    for k in range(buy_idx, exit_idx + 1):
        try:
            v = _factor_price(float(min5_rows[k].get('close', 0) or 0))
            if v > 0:
                closes.append(v)
        except (ValueError, TypeError):
            pass
    if len(closes) < 2:
        return None

    total_pct = (closes[-1] - entry_price) / entry_price * 100 if entry_price else 0
    max_c = max(closes)
    min_c = min(closes)
    max_gain = (max_c - entry_price) / entry_price * 100 if entry_price else 0
    max_loss = (min_c - entry_price) / entry_price * 100 if entry_price else 0
    retreat = (min_c - max(max_c, entry_price)) / max(max_c, entry_price) * 100 if max(max_c, entry_price) else 0
    exit_time = min5_rows[exit_idx].get('timestamp', '')

    return {
        'entry_time': entry_time,
        'exit_time': exit_time,
        'entry': round(entry_price, 4),
        'exit': round(exit_price, 4),
        'total_pct': round(total_pct, 2),
        'max_gain': round(max_gain, 2),
        'max_loss': round(max_loss, 2),
        'retreat': round(retreat, 2),
        'bars': exit_idx - buy_idx,
        'merged_signals': 1,
        'segments': 1,
        'exit_reason': exit_reason,
    }


# ========== 单标的回测 ==========

def backtest_one(code):
    """
    三轮对比回测：MA严格版 vs MA延期版 vs 金叉级独立。
    每根★买独立测试三种策略，互不影响。
    """
    min5_rows = read_csv(code, 'min5')
    if len(min5_rows) < 100:
        return None

    period_data = {}
    for p in PERIODS:
        period_data[p] = read_csv(code, p)

    # 三个独立交易桶
    trades_by_version = {
        'ma_strict': [],
        'ma_delayed': [],
        'jincha': [],
    }

    total_buy_signals = 0
    for idx, bar in enumerate(min5_rows):
        bs = bar.get('buy_signal', '').strip()
        if bs != '★买':
            continue
        if idx < 50:
            continue
        total_buy_signals += 1

        # 1. MA严格版: ★买当根MA5>MA10>MA20，不追赶
        result = _try_ma_entry(bar, idx, min5_rows, period_data, delay_window=0)
        if result:
            entry_idx, entry_price = result
            trade = track_trade(min5_rows, entry_idx, 'ma', code=code)
            if trade:
                trade['version'] = 'ma_strict'
                trade['code'] = code
                trades_by_version['ma_strict'].append(trade)

        # 2. MA延期版: ★买后12根内等待MA排好
        result = _try_ma_entry(bar, idx, min5_rows, period_data, delay_window=12)
        if result:
            entry_idx, entry_price = result
            trade = track_trade(min5_rows, entry_idx, 'ma', code=code)
            if trade:
                trade['version'] = 'ma_delayed'
                trade['code'] = code
                trades_by_version['ma_delayed'].append(trade)

        # 3. 金叉级独立: 扫描30根内出金叉，金叉bar收盘入场
        result = _try_jincha_entry(bar, idx, min5_rows, period_data)
        if result:
            entry_idx, entry_price = result
            trade = track_trade(min5_rows, entry_idx, 'jincha', code=code)
            if trade:
                trade['version'] = 'jincha'
                trade['code'] = code
                trades_by_version['jincha'].append(trade)

    # 统计
    result = {'min5': {}}
    for v in ['ma_strict', 'ma_delayed', 'jincha']:
        trades = trades_by_version[v]
        if trades:
            result['min5'][v] = {
                'buy': stat(trades, True),
                'trades': trades,
            }
        else:
            result['min5'][v] = {'buy': None, 'trades': []}

    result['min5']['total_buy_signals'] = total_buy_signals
    return result


# ========== 统计 ==========

def stat(cycles, is_buy=True):
    if not cycles: return None
    pcts = [c['total_pct'] for c in cycles]
    retreats = [c['retreat'] for c in cycles]
    bars_list = [c['bars'] for c in cycles]
    wins = [p for p in pcts if (is_buy and p > 0) or (not is_buy and p < 0)]
    exit_reasons = {}
    for c in cycles:
        r = c.get('exit_reason', '未知')
        exit_reasons[r] = exit_reasons.get(r, 0) + 1
    return {
        'count': len(cycles),
        'win_rate': round(len(wins)/len(cycles)*100, 1) if cycles else 0,
        'avg_pct': round(sum(pcts)/len(pcts), 2) if pcts else 0,
        'max_pct': round(max(pcts), 2) if pcts else 0,
        'min_pct': round(min(pcts), 2) if pcts else 0,
        'avg_retreat': round(sum(retreats)/len(retreats), 2) if retreats else 0,
        'max_retreat': round(min(retreats), 2) if retreats else 0,
        'avg_bars': round(sum(bars_list)/len(bars_list), 1) if bars_list else 0,
        'bars_range': '%d~%d' % (min(bars_list), max(bars_list)) if bars_list else '',
        'exit_reasons': exit_reasons,
    }


# ========== SQLite 持久化（复用 v3 结构） ==========

def _ensure_db():
    BACKTEST_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.execute('''
        CREATE TABLE IF NOT EXISTS signal_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date    TEXT NOT NULL,
            code        TEXT NOT NULL,
            period      TEXT NOT NULL,
            signal_type TEXT NOT NULL,
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
            filter_level TEXT DEFAULT '',
            UNIQUE(code, period, signal_type, entry_time)
        )
    ''')
    # 兼容旧表：可能缺 filter_level 列
    try:
        conn.execute('ALTER TABLE signal_trades ADD COLUMN filter_level TEXT DEFAULT ""')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def _save_trades(conn, run_date, code, trades, signal_type, filter_level):
    is_buy = (signal_type == 'buy')
    for r in trades:
        is_win = 1 if ((is_buy and r['total_pct'] > 0) or (not is_buy and r['total_pct'] < 0)) else 0
        try:
            conn.execute('''
                INSERT OR IGNORE INTO signal_trades
                (run_date, code, period, signal_type, entry_time, exit_time,
                 entry_price, exit_price, total_pct, max_gain, max_loss,
                 retreat, bars, merge_count, is_win, filter_level, exit_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                run_date, code, 'min5', signal_type,
                r['entry_time'], r['exit_time'],
                r['entry'], r['exit'], r['total_pct'],
                r['max_gain'], r['max_loss'], r['retreat'],
                r['bars'], r.get('merged_signals', 1), is_win,
                filter_level, r.get('exit_reason', ''),
            ))
        except Exception as e:
            print(f'  [DB] 跳过 {code} {r["entry_time"]}: {e}')


def save_to_db(code, result):
    run_date = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn = _ensure_db()
    try:
        for level in ['ma_strict', 'ma_delayed', 'jincha']:
            trades = result.get('min5', {}).get(level, {}).get('trades', [])
            if trades:
                _save_trades(conn, run_date, code, trades, 'buy', level)
        conn.commit()
    finally:
        conn.close()


# ========== 累积胜率曲线 ==========

def cumulative_win_curve(code, period='min5', signal_type='buy', filter_level=None):
    sql = '''SELECT entry_time, total_pct, is_win, filter_level
             FROM signal_trades
             WHERE code=? AND period=? AND signal_type=?'''
    params = [code, period, signal_type]
    if filter_level:
        sql += ' AND filter_level=?'
        params.append(filter_level)
    sql += ' ORDER BY entry_time ASC'

    rows = query_backtest_sql(sql, params)
    if not rows:
        return {'code': code, 'trades': 0, 'points': []}

    wins = 0
    points = []
    for i, r in enumerate(rows):
        if r['is_win']: wins += 1
        points.append({
            'idx': i + 1,
            'entry_time': r['entry_time'],
            'pct': r['total_pct'],
            'cum_win_rate': round(wins / (i + 1) * 100, 1),
            'filter_level': r.get('filter_level', ''),
        })
    return {
        'code': code, 'period': period, 'type': signal_type,
        'trades': len(rows), 'total_wins': wins,
        'final_cum_wr': round(wins / len(rows) * 100, 1) if rows else 0,
        'points': points,
    }


def query_backtest_sql(sql, params=None):
    conn = _ensure_db()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params or [])
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ========== 归档 ==========

def archive_backtest_report():
    if not BACKTEST_OUT.exists():
        return
    today = datetime.now().strftime('%Y-%m-%d')
    BACKTEST_ARC.mkdir(parents=True, exist_ok=True)
    dst = BACKTEST_ARC / f'{today}.json'
    shutil.copy2(str(BACKTEST_OUT), str(dst))
    return dst


# ========== 入口 ==========

def _load_volume_leader_universe():
    """加载量领宇宙列表，只测这些标的"""
    f = SNAPSHOT_DIR / '_funds' / 'volume_leader_universe.json'
    if not f.exists():
        return None  # 没有量领宇宙 → 回退到全量
    try:
        data = json.load(open(f, 'r', encoding='utf-8'))
        return data.get('universe', [])
    except:
        return None

def get_all_codes():
    """获取回测标的列表：优先用量领宇宙，回退到全量"""
    universe = _load_volume_leader_universe()
    if universe:
        # 只取量领宇宙中确实有信号数据的标的
        available = sorted([d.name for d in SNAPSHOT_DIR.iterdir()
                           if d.is_dir() and d.name.startswith(('sh','sz'))])
        codes = sorted([c for c in universe if c in available])
        return codes
    # 回退：全量
    return sorted([d.name for d in SNAPSHOT_DIR.iterdir()
                   if d.is_dir() and d.name.startswith(('sh','sz'))])


def trend_dir(code):
    try:
        data = json.load(open(CYCLE_REPORT, 'r', encoding='utf-8'))
        for item in data:
            if item['code'] == code:
                return item.get('trend', {}).get('direction', 'neutral')
    except:
        pass
    return 'neutral'


def _level_label(level):
    return {'ma': 'MA级(试错)', 'jincha': '金叉级(买)', 'resonance': '共振级(买完)'}.get(level, level)


def _fmt(s, label=''):
    if not s or s.get('count', 0) == 0: return ''
    reasons = s.get('exit_reasons', {})
    reason_str = ''
    if reasons:
        parts = [f'{k}={v}' for k, v in sorted(reasons.items())]
        reason_str = ' [' + '|'.join(parts) + ']'
    return '%s %d笔 %.0f%% 均%+.1f%%(%+.1f~%+.1f%%) 回撤%.1f%%%s' % (
        label, s['count'], s['win_rate'],
        s['avg_pct'], s['min_pct'], s['max_pct'],
        s['avg_retreat'], reason_str)


def _version_label(ver):
    return {'ma_strict': 'MA严格版', 'ma_delayed': 'MA延期版', 'jincha': '金叉级独立'}.get(ver, ver)


def backtest_all():
    """全量回测入口 — 三轮对比"""
    codes = get_all_codes()
    report = {}

    for code in codes:
        r = backtest_one(code)
        if r:
            report[code] = r

    # 写 JSON 摘要
    summary = {}
    for code in sorted(report.keys()):
        min5 = report[code].get('min5', {})
        t = trend_dir(code)
        summary[code] = {'trend': t, 'min5': {}}
        for v in ['ma_strict', 'ma_delayed', 'jincha']:
            s = min5.get(v, {}).get('buy')
            summary[code]['min5'][v] = s if s else None
        summary[code]['min5']['total_buy_signals'] = min5.get('total_buy_signals', 0)

    with open(BACKTEST_OUT, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    arc_path = archive_backtest_report()

    # ─── 终端输出 ───
    print(f'回测完成: {len(report)} 只标的 (min5 ★买为锚点)')
    if arc_path:
        print(f'归档: {arc_path.name}')

    total_buy = sum(report[code]['min5']['total_buy_signals'] for code in report)
    print(f'\n总★买信号: {total_buy}')

    # 三轮对比总表
    versions = ['ma_strict', 'ma_delayed', 'jincha']

    # 收集各版本数据
    ver_data = {}
    for v in versions:
        all_t = []
        stk = set()
        for code in report:
            trades = report[code].get('min5', {}).get(v, {}).get('trades', [])
            if trades:
                all_t.extend(trades)
                stk.add(code)
        ver_data[v] = (all_t, len(stk))

    print(f'\n{"=" * 76}')
    print(f'{"":>16} {"MA严格版":>18} {"MA延期版":>18} {"金叉级独立":>18}')
    print(f'{"=" * 76}')
    rows = [
        ('标的覆盖', lambda d, s: f'{s:>3}只'),
        ('总笔数',   lambda d, s: f'{len(d):>5}'),
        ('胜率',     lambda d, s: f'{stat(d)["win_rate"]:>5.1f}%' if d else '  -  '),
        ('均盈亏',   lambda d, s: f'{stat(d)["avg_pct"]:>+7.2f}%' if d else '   -   '),
        ('均回撤',   lambda d, s: f'{stat(d)["avg_retreat"]:>6.1f}%' if d else '   -  '),
        ('均持仓',   lambda d, s: f'{stat(d)["avg_bars"]:>5.0f}bar' if d else '   -  '),
    ]
    for label, fn in rows:
        vals = []
        for v in versions:
            trades, stk_cnt = ver_data[v]
            if trades:
                vals.append(fn(trades, stk_cnt))
            else:
                vals.append('   -   ')
        print(f'{label:>12}  {vals[0]:>18}  {vals[1]:>18}  {vals[2]:>18}')
    print(f'{"=" * 76}')

    # 退出原因分布
    print(f'\n=== 退出原因分布 ===')
    for v in versions:
        all_t = ver_data[v][0]
        if not all_t:
            continue
        reasons = {}
        for t in all_t:
            r = t.get('exit_reason', '未知')
            reasons[r] = reasons.get(r, 0) + 1
        total = len(all_t)
        r_str = ' | '.join(f'{k}: {v}次({v/total*100:.0f}%)' for k, v in sorted(reasons.items(), key=lambda x: -x[1]))
        print(f'  {_version_label(v):>8} ({total}笔): {r_str}')

    # 各标的明细
    print(f'\n── 各标的明细 ──')
    for code in sorted(report.keys()):
        min5 = report[code].get('min5', {})
        t = trend_dir(code)
        parts = []
        for v in versions:
            s = min5.get(v, {}).get('buy')
            if s:
                parts.append(f'{_version_label(v)} {s["count"]}笔 {s["win_rate"]}%')
        if parts:
            print(f'  {code} ({t}): {" | ".join(parts)}')
        else:
            print(f'  {code} ({t}): 无交易')

    return report


if __name__ == '__main__':
    backtest_all()

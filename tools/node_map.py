# -*- coding: utf-8 -*-
"""
节点地图 — Phase 1: 板块涨幅集群扫描 + 龙头识别
==================================================

从通达信概念板块指数日线出发:
  1. 扫描板块指数上的每一波上涨行情（涨幅超过阈值的连续区间）
  2. 在每波行情内，扫描成分股的涨幅排名，识别龙1/龙2
  3. 输出同花顺格式的节点地图 JSON

对齐同花顺格式:
  节点 = {window, gain, duration_days, leaders: [{stock, role, days, gain_pct}]}

数据来源:
  - 板块指数日线: C:/zd_cjzq/vipdoc/sh/lday/sh880xxx.day
  - 板块名称→880xxx: C:/zd_cjzq/T0002/hq_cache/tdxzs.cfg
  - 个股→板块映射: C:/zd_cjzq/T0002/hq_cache/block_gn.dat
  - 个股名称: config.py NAME_MAP + volume_leader 缓存
"""

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from pytdx.reader import TdxDailyBarReader, block_reader

# ── 路径配置 ──
VIPDOC = Path("C:/zd_cjzq/vipdoc")
HQ_CACHE = Path("C:/zd_cjzq/T0002/hq_cache")
TDXZS_CFG = str(HQ_CACHE / "tdxzs.cfg")
BLOCK_GN = str(HQ_CACHE / "block_gn.dat")
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "signals" / "tracking" / "_macro"

# ── 参数 ──
WAVE_GAIN_THRESHOLD = 0.08   # 板块指数一波行情最低总涨幅 8%
MIN_WAVE_DAYS = 3            # 最短行情持续天数
LEADER_TOP_N = 5             # 每波行情取TOP N龙头
SUB_WAVE_GAIN_THRESHOLD = 0.15  # 子波最低涨幅（比主波更严格）


# ── 板块映射加载 ──

def load_sector_list(category: int = 4) -> list[dict]:
    """从 tdxzs.cfg 加载板块列表，category=4=概念板块"""
    with open(TDXZS_CFG, "rb") as f:
        raw = f.read()
    text = raw.decode("gbk")
    sectors = []
    for line in text.strip().split("\n"):
        parts = line.split("|")
        if len(parts) < 3:
            continue
        if int(parts[2]) == category:
            sectors.append({"name": parts[0], "code_880": parts[1]})
    return sectors


def load_sector_stocks() -> dict[str, list[str]]:
    """从 block_gn.dat 加载 板块名→成分股代码列表"""
    if not os.path.exists(BLOCK_GN):
        return {}
    reader = block_reader.BlockReader()
    df = reader.get_df(BLOCK_GN, result_type=0)
    mapping = {}
    for _, row in df.iterrows():
        mapping.setdefault(row["blockname"], []).append(row["code"])
    return mapping


def load_stock_names() -> dict[str, str]:
    """加载股票代码→名称映射，多源回退，支持8位(sz000001)和6位(000001)"""
    names = {}

    # 1. volume_leader 缓存（47058条，8位代码如 sz000001）
    cache_path = PROJECT_ROOT / "signals" / "tracking" / "_funds" / "stock_names.csv"
    if cache_path.exists():
        try:
            df = pd.read_csv(cache_path, dtype=str)
            for _, row in df.iterrows():
                code = str(row.get("code", "")).strip()
                name = str(row.get("name", "")).strip()
                if not code or not name:
                    continue
                # 统一转为6位代码
                code6 = code[2:] if len(code) >= 8 and code[:2] in ("sh", "sz") else code
                if len(code6) == 6:
                    names[code6] = name
        except Exception:
            pass

    # 2. config NAME_MAP（补漏）
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from config import NAME_MAP
        for full_code, name in NAME_MAP.items():
            code6 = full_code[2:] if len(full_code) > 2 else full_code
            if code6 not in names:
                names[code6] = name
    except Exception:
        pass

    return names


# ── 行情读取 ──

class BarReader:
    """批量读取通达信日线，带缓存避免重复IO"""

    def __init__(self):
        self._reader = TdxDailyBarReader()
        self._cache = {}

    def read(self, code: str) -> pd.DataFrame | None:
        """读取标的日线，code=6位数字如 '000001' 或 '880501'"""
        if code in self._cache:
            return self._cache[code]

        # 市场检测
        if code.startswith("88"):
            mkt = "sh"           # 概念板块指数 → sh
        elif code.startswith(("60", "68")):
            mkt = "sh"           # 沪市主板+科创板
        elif code.startswith("000"):
            mkt = "sh"           # 上证指数(000001)、沪深300(000300)等 → sh
        elif code.startswith(("39", "30", "00", "002")):
            mkt = "sz"           # 深证/创业板/中小板
        else:
            mkt = "sz"

        fpath = VIPDOC / mkt / "lday" / f"{mkt}{code}.day"
        if not fpath.exists():
            self._cache[code] = None
            return None

        try:
            df = self._reader.get_df(str(fpath))
            if df is None or len(df) < 20:
                self._cache[code] = None
                return None
            self._cache[code] = df
            return df
        except Exception:
            self._cache[code] = None
            return None


# ── 波动检测 ──

def _find_local_extrema(arr: np.ndarray, order: int = 10):
    """
    用 argrelextrema 找局部极值点（不依赖 scipy）。

    返回: (valley_indices, peak_indices)
    """
    n = len(arr)
    valleys = []
    peaks = []
    # 使用比较器：对每个点，比较其与前后 order 个点
    for i in range(order, n - order):
        window_before = arr[i - order:i]
        window_after = arr[i + 1:i + order + 1]
        if np.all(arr[i] < window_before) and np.all(arr[i] <= window_after):
            valleys.append(i)
        if np.all(arr[i] > window_before) and np.all(arr[i] >= window_after):
            peaks.append(i)
    return np.array(valleys), np.array(peaks)


def detect_waves(close: np.ndarray, dates: np.ndarray,
                 threshold: float = WAVE_GAIN_THRESHOLD,
                 min_days: int = MIN_WAVE_DAYS) -> list[dict]:
    """
    用局部极值法检测上涨浪（底→顶→底 = 一波行情）。

    1. SMA5平滑价格
    2. 找局部谷和峰
    3. 每个谷→下一个峰 = 一波，计算涨幅
    4. 过滤涨幅 < threshold 的浪

    返回: [{start_idx, end_idx, start_date, end_date, gain, duration_days}, ...]
    """
    # 平滑
    smoothed = np.convolve(close, np.ones(5) / 5, mode='same')
    # 前2和后2根用原始值补齐
    smoothed[:2] = close[:2]
    smoothed[-2:] = close[-2:]

    valleys, peaks = _find_local_extrema(smoothed, order=8)

    if len(valleys) == 0 or len(peaks) == 0:
        return []

    # 配对：每个谷找下一个峰，同时找再下一个谷作为浪的终点
    results = []
    pi = 0  # 峰指针
    for vi, v_idx in enumerate(valleys):
        # 找到 v_idx 之后的第一个峰
        while pi < len(peaks) and peaks[pi] <= v_idx:
            pi += 1
        if pi >= len(peaks):
            break
        p_idx = peaks[pi]

        # 找到峰之后的第一个谷作为浪的终点
        if vi + 1 < len(valleys) and valleys[vi + 1] > p_idx:
            end_idx = valleys[vi + 1]
        else:
            end_idx = p_idx  # 没有后续谷，以峰为终点

        # 检查浪的质量
        duration = p_idx - v_idx + 1
        if duration < min_days:
            continue

        # 不直接用极值高低点，改用谷到峰的收盘价作为增益基准
        gain = float(close[p_idx] / close[v_idx] - 1.0)
        if gain < threshold:
            continue

        start_date = str(pd.Timestamp(dates[v_idx]).date())
        end_date = str(pd.Timestamp(dates[end_idx]).date())

        results.append({
            "start_idx": int(v_idx),
            "end_idx": int(end_idx),
            "start_date": start_date,
            "end_date": end_date,
            "gain": round(gain * 100, 2),
            "duration_days": int(end_idx - v_idx + 1),
        })

    # 合并重叠的浪（后浪起点 > 前浪终点则独立）
    merged = []
    for r in results:
        if not merged:
            merged.append(r)
            continue
        last = merged[-1]
        if r["start_idx"] <= last["end_idx"]:
            # 重叠：保留涨幅更大的
            if r["gain"] > last["gain"]:
                merged[-1] = r
        else:
            merged.append(r)

    return merged


def detect_sub_waves(close: np.ndarray, dates: np.ndarray,
                     wave_start: int, wave_end: int,
                     threshold: float = SUB_WAVE_GAIN_THRESHOLD) -> list[dict]:
    """在主浪范围内用局部极值法检测子浪"""
    sub_close = close[wave_start:wave_end + 1]
    sub_dates = dates[wave_start:wave_end + 1]

    if len(sub_close) < 10:
        return []

    sub_waves = detect_waves(sub_close, sub_dates, threshold=threshold, min_days=3)

    # 偏移回原始索引
    for sw in sub_waves:
        sw["start_idx"] += wave_start
        sw["end_idx"] += wave_start
    return sub_waves


# ── 龙头识别 ──

def find_leaders(stock_codes: list[str], stock_names: dict[str, str],
                 reader: BarReader, sector_dates: np.ndarray,
                 wave_start_idx: int, wave_end_idx: int,
                 top_n: int = LEADER_TOP_N) -> list[dict]:
    """
    在一波行情窗口内，对板块成分股按涨幅排序，识别龙1/龙2。

    返回: [{stock, code, role, days, gain_pct}, ...]
    """
    # 参照日期：取板块指数的日期序列，找到波浪窗口对应的日期
    wave_start_date = sector_dates[wave_start_idx]
    wave_end_date = sector_dates[wave_end_idx]

    all_returns = []  # 全部股票（含跌的）
    for code in stock_codes[:200]:
        df = reader.read(code)
        if df is None or len(df) < 20:
            continue

        try:
            start_mask = df.index >= wave_start_date
            end_mask = df.index <= wave_end_date
            window = df.loc[start_mask & end_mask]
        except Exception:
            continue

        if len(window) < 2:
            continue

        start_price = window.iloc[0]["close"]
        end_price = window.iloc[-1]["close"]

        peak_idx = window["close"].idxmax()
        peak_price = window.loc[peak_idx, "close"]
        peak_pos = window.index.get_loc(peak_idx)

        gain = float((end_price / start_price - 1.0) * 100)
        peak_gain = float((peak_price / start_price - 1.0) * 100)

        name = stock_names.get(code, code)

        all_returns.append({
            "code": code,
            "stock": name,
            "gain_pct": round(gain, 2),
            "peak_gain_pct": round(peak_gain, 2),
            "days": peak_pos + 1,
        })

    # 按峰值涨幅排序，前N只为龙头
    all_returns.sort(key=lambda x: x["peak_gain_pct"], reverse=True)
    positive_returns = [s for s in all_returns if s["gain_pct"] > 0]

    # 分配龙头角色：龙1/龙2/龙3/龙4/龙5
    role_names = ["龙1", "龙2", "龙3", "龙4", "龙5"]
    leaders = []
    for rank, sr in enumerate(positive_returns[:top_n]):
        role = role_names[rank] if rank < len(role_names) else "跟风"
        leaders.append({
            "stock": sr["stock"],
            "code": sr["code"],
            "role": role,
            "days": sr["days"],
            "gain_pct": sr["gain_pct"],
        })

    return leaders, all_returns


def analyze_wave_breadth(stock_returns: list[dict]) -> dict:
    """统计一波行情内成分股参与广度"""
    n = len(stock_returns)
    if n == 0:
        return {"total_checked": 0, "positive_count": 0, "strong_count": 0,
                "participation_rate": 0, "median_gain": 0}

    positive = [s for s in stock_returns if s["gain_pct"] > 0]
    strong = [s for s in stock_returns if s["gain_pct"] >= 10]
    gains = [s["gain_pct"] for s in stock_returns]

    return {
        "total_checked": n,
        "positive_count": len(positive),
        "strong_count": len(strong),
        "participation_rate": round(len(positive) / n * 100, 1) if n > 0 else 0,
        "median_gain": round(float(np.median(gains)), 2) if gains else 0,
        "avg_gain": round(float(np.mean(gains)), 2) if gains else 0,
    }


def score_node_quality(node: dict) -> dict:
    """
    节点质量评分 (0-12分)，分A/B/C/D四级。

    维度:
      - 参与广度 (0-3): 涨超10%的成分股占比
      - 参与深度 (0-3): 涨超10%的绝对数量
      - 龙头梯 队 (0-2): 龙1 vs 龙3 的涨幅梯度
      - 持续时间 (0-2): 浪的长度
      - 中位涨幅 (0-2): 整个板块的中位表现

    等级:
      A级(>=9): 确认真节点 — 板块效应强，梯队清晰
      B级(6-8): 大概率真节点 — 有一定板块效应
      C级(3-5): 存疑 — 可能是个股驱动或噪音
      D级(<3): 噪音 — 不具板块效应
    """
    score = 0
    reasons = []

    b = node.get("breadth", {})
    leaders = node.get("leaders", [])
    duration = node.get("duration_days", 0)

    # 1. 参与广度 (0-3)
    pr = b.get("participation_rate", 0)
    if pr >= 60:
        score += 3
        reasons.append(f"高参与度({pr}%)")
    elif pr >= 40:
        score += 2
        reasons.append(f"中等参与度({pr}%)")
    elif pr >= 25:
        score += 1
        reasons.append(f"低参与度({pr}%)")
    else:
        reasons.append(f"参与度不足({pr}%)")

    # 2. 参与深度 (0-3)
    strong = b.get("strong_count", 0)
    if strong >= 20:
        score += 3
        reasons.append(f"深度集群({strong}只涨超10%)")
    elif strong >= 10:
        score += 2
        reasons.append(f"中度集群({strong}只)")
    elif strong >= 5:
        score += 1
        reasons.append(f"浅集群({strong}只)")
    else:
        reasons.append(f"集群不足({strong}只)")

    # 3. 龙头梯队 (0-2) — 龙1应显著强于龙3，说明有真正的龙头
    if len(leaders) >= 3:
        g1 = leaders[0].get("gain_pct", 0)
        g3 = leaders[2].get("gain_pct", 0)
        if g3 > 0:
            ratio = g1 / g3
            if ratio >= 1.8:
                score += 2
                reasons.append(f"龙头清晰(龙1/龙3={ratio:.1f}x)")
            elif ratio >= 1.3:
                score += 1
                reasons.append(f"龙头尚可({ratio:.1f}x)")
            else:
                reasons.append(f"龙头分散({ratio:.1f}x)")
    elif len(leaders) >= 1:
        score += 1
        reasons.append("有龙头")

    # 4. 持续时间 (0-2)
    if duration >= 30:
        score += 2
        reasons.append(f"长波({duration}d)")
    elif duration >= 10:
        score += 1
        reasons.append(f"中波({duration}d)")
    else:
        reasons.append(f"短波({duration}d)")

    # 5. 中位涨幅 (0-2)
    median = b.get("median_gain", 0)
    if median >= 20:
        score += 2
        reasons.append(f"强中位+{median}%")
    elif median >= 10:
        score += 1
        reasons.append(f"中位+{median}%")
    else:
        reasons.append(f"弱中位+{median}%")

    if score >= 9:
        grade = "A"
    elif score >= 6:
        grade = "B"
    elif score >= 3:
        grade = "C"
    else:
        grade = "D"

    return {"score": score, "grade": grade, "reasons": reasons}


def compute_node_context(reader: BarReader,
                         sector_dates: np.ndarray,
                         sector_close: np.ndarray,
                         node_start_idx: int, node_end_idx: int) -> dict:
    """
    对一个节点窗口，计算多维度上下文标注。

    全部从已有TDX数据计算，不需要任何外部API：
      1. 大盘环境 (上证指数趋势+位置)
      2. 流动性代理 (成交量趋势)
      3. 板块位置 (相对于大盘的超额 + 是否突破)

    返回: {market_env, liquidity_proxy, sector_position, macro_label, event_label}
    """
    # ── 1. 大盘环境 ──
    sh_df = reader.read("000001")  # 上证指数
    market_env = {"sh_index_trend": "无数据", "sh_gain_pct": 0, "sh_volume_trend": "无数据"}

    if sh_df is not None:
        sh_close = sh_df["close"].to_numpy(dtype=np.float64)
        sh_volume = sh_df["volume"].to_numpy(dtype=np.float64)
        sh_dates = sh_df.index.to_numpy()

        # 对齐日期：找到上证在节点窗口内的数据
        node_start = sector_dates[node_start_idx]
        node_end = sector_dates[node_end_idx]

        try:
            sh_start_mask = sh_dates >= node_start
            sh_end_mask = sh_dates <= node_end
            sh_window = sh_close[sh_start_mask & sh_end_mask]

            if len(sh_window) >= 2:
                sh_gain = float(sh_window[-1] / sh_window[0] - 1.0) * 100
                market_env["sh_gain_pct"] = round(sh_gain, 2)

            # 上证趋势判断：用expma12/expma50
            if len(sh_close) > 50:
                ema12 = np.zeros_like(sh_close)
                ema50 = np.zeros_like(sh_close)
                alpha12 = 2.0 / 13
                alpha50 = 2.0 / 51
                ema12[0] = sh_close[0]
                ema50[0] = sh_close[0]
                for i in range(1, len(sh_close)):
                    ema12[i] = alpha12 * sh_close[i] + (1 - alpha12) * ema12[i - 1]
                    ema50[i] = alpha50 * sh_close[i] + (1 - alpha50) * ema50[i - 1]
                # 节点前一日的均线状态
                pre_idx = max(node_start_idx - 1, 50)
                if pre_idx < len(sh_close):
                    idx = np.searchsorted(sh_dates, sh_dates[min(pre_idx, len(sh_dates) - 1)])
                    idx = min(idx, len(sh_close) - 1)
                    if ema12[idx] > ema50[idx]:
                        ratio = round(float(ema12[idx] / ema50[idx] - 1) * 100, 1)
                        market_env["sh_index_trend"] = f"上涨(EMA12>EMA50 +{ratio}%)"
                    else:
                        ratio = round(float(ema50[idx] / ema12[idx] - 1) * 100, 1)
                        market_env["sh_index_trend"] = f"下跌(EMA12<EMA50 -{ratio}%)"

            # 节点窗口内的成交量趋势
            sh_vol_window = sh_volume[sh_start_mask & sh_end_mask]
            if len(sh_vol_window) >= 10:
                vol_first_half = np.mean(sh_vol_window[:len(sh_vol_window)//2])
                vol_second_half = np.mean(sh_vol_window[len(sh_vol_window)//2:])
                if vol_second_half > vol_first_half * 1.2:
                    market_env["sh_volume_trend"] = "放量"
                elif vol_second_half < vol_first_half * 0.8:
                    market_env["sh_volume_trend"] = "缩量"
                else:
                    market_env["sh_volume_trend"] = "平量"
        except Exception:
            pass

    # ── 2. 板块位置 ──
    sector_gain = round(float(sector_close[node_end_idx] / sector_close[node_start_idx] - 1.0) * 100, 2)
    relative_alpha = round(sector_gain - market_env.get("sh_gain_pct", 0), 2)

    # 判断是否突破：看节点起点前20根K线的位置
    pre_start = max(node_start_idx - 20, 0)
    pre_high = np.max(sector_close[pre_start:node_start_idx + 1])
    pre_low = np.min(sector_close[pre_start:node_start_idx + 1])
    pre_range = pre_high - pre_low
    at_high = sector_close[node_start_idx] >= pre_high * 0.95 if pre_range > 0 else False
    at_low = sector_close[node_start_idx] <= pre_low * 1.05 if pre_range > 0 else False

    if at_high and sector_gain > 10:
        position_type = "高位加速"
    elif at_low and sector_gain > 10:
        position_type = "底部反转"
    elif sector_gain > 0:
        position_type = "趋势延续"
    else:
        position_type = "其他"

    sector_position = {
        "sector_gain_pct": sector_gain,
        "relative_alpha_pct": relative_alpha,
        "position_type": position_type,
        "pre_20d_range_pct": round(float(pre_range / sector_close[node_start_idx] * 100), 1) if pre_range > 0 else 0,
    }

    # ── 3. 流动性代理 ──
    # 板块指数自身的成交量趋势
    liquidity_proxy = {"volume_trend": "无数据", "vol_ratio": 0}

    # 用上证成交额作为市场流动性代理
    if sh_df is not None:
        sh_amount = sh_df.get("amount")
        if sh_amount is None:
            sh_amount = sh_df["volume"].to_numpy(dtype=np.float64)  # 回退

        node_start_dt = sector_dates[node_start_idx]
        node_end_dt = sector_dates[node_end_idx]
        sh_dates = sh_df.index.to_numpy()

        try:
            sh_amount_start = sh_amount[sh_dates >= node_start_dt]
            sh_amount_end = sh_amount[sh_dates <= node_end_dt]
            sh_am_window = sh_amount[sh_dates >= node_start_dt]
            sh_am_window = sh_am_window[sh_dates[sh_dates >= node_start_dt] <= node_end_dt]

            # 简化：取窗口内成交额均值 vs 窗口前均值
            pre_mask = (sh_dates >= sector_dates[max(node_start_idx - 20, 0)]) & (sh_dates <= node_start_dt)
            pre_vol = np.mean(sh_amount[pre_mask]) if np.any(pre_mask) else 0

            in_mask = (sh_dates >= node_start_dt) & (sh_dates <= node_end_dt)
            in_vol = np.mean(sh_amount[in_mask]) if np.any(in_mask) else 0

            if pre_vol > 0:
                vol_ratio = float(in_vol / pre_vol)
                liquidity_proxy["vol_ratio"] = round(vol_ratio, 2)
                if vol_ratio > 1.3:
                    liquidity_proxy["volume_trend"] = f"显著放量(x{vol_ratio:.1f})"
                elif vol_ratio > 1.1:
                    liquidity_proxy["volume_trend"] = f"温和放量(x{vol_ratio:.1f})"
                elif vol_ratio < 0.8:
                    liquidity_proxy["volume_trend"] = f"缩量(x{vol_ratio:.1f})"
                else:
                    liquidity_proxy["volume_trend"] = "平量"
        except Exception:
            pass

    return {
        "market_env": market_env,
        "liquidity_proxy": liquidity_proxy,
        "sector_position": sector_position,
        "macro_label": None,
        "event_label": None,
    }


# ── 主扫描 ──

def scan_sector(sector_name: str, code_880: str,
                sector_stocks: dict[str, list[str]],
                stock_names: dict[str, str],
                reader: BarReader) -> dict:
    """
    扫描单个概念板块，生成节点地图。

    返回: {sector, code_880, node_count, nodes: [...]}
    """
    df = reader.read(code_880)
    if df is None:
        return {"sector": sector_name, "code_880": code_880, "node_count": 0, "nodes": []}

    close = df["close"].to_numpy(dtype=np.float64)
    dates = df.index.to_numpy()

    # 1. 检测主浪
    waves = detect_waves(close, dates)

    # 2. 对每个浪，识别龙头
    stock_codes = sector_stocks.get(sector_name, [])
    nodes = []

    for w in waves:
        # 子浪检测
        sub_clip_close = close[w["start_idx"]:w["end_idx"] + 1]
        sub_dates = dates[w["start_idx"]:w["end_idx"] + 1]
        sub_waves = detect_waves(sub_clip_close, sub_dates,
                                 threshold=SUB_WAVE_GAIN_THRESHOLD,
                                 min_days=3)

        # 子浪的龙头
        sub_nodes = []
        for sw in sub_waves:
            if sw["gain"] >= w["gain"] * 0.85 and sw["duration_days"] >= w["duration_days"] * 0.7:
                continue
            sw_leaders, _ = find_leaders(stock_codes, stock_names, reader,
                                          dates, sw["start_idx"], sw["end_idx"])
            sub_nodes.append({
                "window": f'{sw["start_date"]}-{sw["end_date"]}',
                "gain": f'+{sw["gain"]}%',
                "duration_days": sw["duration_days"],
                "leaders": sw_leaders[:3],
            })

        # 主浪龙头 + 参与广度
        wave_leaders, all_returns = find_leaders(stock_codes, stock_names, reader,
                                                  dates, w["start_idx"], w["end_idx"])
        breadth = analyze_wave_breadth(all_returns)

        # 上下文标注
        ctx = compute_node_context(reader, dates, close,
                                   w["start_idx"], w["end_idx"])

        node = {
            "window": f'{w["start_date"]}-{w["end_date"]}',
            "gain": f'+{w["gain"]}%',
            "duration_days": w["duration_days"],
            "leaders": wave_leaders[:5],
            "breadth": breadth,
            "context": ctx,
        }
        # 节点质量评分
        node["quality"] = score_node_quality(node)
        if sub_nodes:
            node["sub_waves"] = sub_nodes

        nodes.append(node)

    return {
        "sector": sector_name,
        "code_880": code_880,
        "node_count": len(nodes),
        "nodes": nodes,
    }


# ── 主入口 ──

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="节点地图 — 板块涨幅集群扫描")
    parser.add_argument("--sector", help="只扫指定板块（如 黄金概念）")
    parser.add_argument("--top", type=int, default=10, help="TOP N 板块输出")
    parser.add_argument("--save", action="store_true", help="保存节点地图 JSON")
    parser.add_argument("--all", action="store_true", help="全量扫描所有概念板块")
    args = parser.parse_args()

    t0 = time.time()

    print("加载板块映射...")
    all_sectors = load_sector_list(category=4)
    print(f"  概念板块: {len(all_sectors)}个")

    print("加载个股-板块映射...")
    sector_stocks = load_sector_stocks()
    print(f"  板块→成分股: {len(sector_stocks)}个板块已映射")

    print("加载股票名称缓存...")
    stock_names = load_stock_names()
    print(f"  名称缓存: {len(stock_names)}只")

    reader = BarReader()

    # 确定扫描范围
    if args.sector:
        targets = [s for s in all_sectors if args.sector in s["name"]]
        if not targets:
            print(f"\n未找到板块: {args.sector}")
            sys.exit(1)
        print(f"\n匹配到 {len(targets)} 个板块: {[t['name'] for t in targets]}")
    else:
        targets = all_sectors

    # 扫描
    all_results = []
    for idx, sec in enumerate(targets):
        code = sec["code_880"]
        name = sec["name"]
        result = scan_sector(name, code, sector_stocks, stock_names, reader)
        all_results.append(result)

        if result["node_count"] > 0:
            # 分级统计
            grades = {"A": 0, "B": 0, "C": 0, "D": 0}
            for n in result["nodes"]:
                g = n.get("quality", {}).get("grade", "D")
                grades[g] = grades.get(g, 0) + 1
            grade_summary = " | ".join(f"{g}级:{c}" for g, c in grades.items() if c > 0)

            print(f'\n{name} (sh{code}): {result["node_count"]}波 [{grade_summary}]')

            # 优先展示A/B级节点
            ab_nodes = [n for n in result["nodes"]
                        if n.get("quality", {}).get("grade") in ("A", "B")]
            show_nodes = ab_nodes[:5] or result["nodes"][:5]

            for ni, n in enumerate(show_nodes):
                q = n.get("quality", {})
                ldr_str = " | ".join(f'{l["stock"]}({l["role"]} {l["days"]}d +{l["gain_pct"]}%)' for l in n.get("leaders", [])[:3])
                b = n.get("breadth", {})
                ctx = n.get("context", {})
                sp = ctx.get("sector_position", {})
                breadth_str = f'[广度: {b.get("strong_count",0)}/总{b.get("total_checked",0)}涨超10%, 中位+{b.get("median_gain",0)}%]'
                grade_str = f'[等级: {q.get("grade","?")}级 {q.get("score",0)}分]'
                print(f'  [{ni+1}] {n["window"]}  {n["gain"]}  {grade_str}')
                print(f'      {breadth_str}')
                print(f'      龙头: {ldr_str}')
                if q.get("reasons"):
                    print(f'      评分: {", ".join(q["reasons"])}')
                print(f'      板块: {sp.get("position_type","?")} alpha={sp.get("relative_alpha_pct",0):+.1f}%')

        if not args.all and not args.sector and idx >= args.top - 1:
            break

    # 排序: 按浪数
    all_results.sort(key=lambda x: x["node_count"], reverse=True)

    if args.save:
        out_path = OUTPUT_DIR / "node_map.json"
        out_data = {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sector_count": len(all_results),
            "sectors": all_results,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2)
        print(f"\n保存: {out_path}")

    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.0f}s")

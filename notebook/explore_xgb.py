# -*- coding: utf-8 -*-
"""
笔记研究 · 实验 #1: 缠论+评分特征工程 + XGBoost 验证
=====================================================
从 77 只标的读取日线信号 + 30分钟数据，生成特征向量，
用简单规则自动打标签，跑 XGBoost 看特征重要性。
"""

import os, sys, csv, json
import warnings
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from czsc import CZSC, Freq, RawBar
from czsc.core import Direction
from datetime import datetime as dt_type

# --- 路径 ---
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
TRACKING = os.path.join(PROJECT, "signals", "tracking")

from notebook.chanlun.signals import get_structure_info, get_zs_info

warnings.filterwarnings("ignore")

# ============================================================
# 1. 数据加载
# ============================================================

def load_all_codes() -> list[str]:
    codes = []
    for d in sorted(os.listdir(TRACKING)):
        p = os.path.join(TRACKING, d, "daily_signals.csv")
        if os.path.isdir(os.path.join(TRACKING, d)) and os.path.exists(p):
            codes.append(d)
    return codes

def load_csv(code: str, period: str) -> list[dict]:
    p = os.path.join(TRACKING, code, f"{period}_signals.csv")
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            cleaned = {}
            for k, v in r.items():
                if v == "" or v is None:
                    cleaned[k] = np.nan
                else:
                    try:
                        cleaned[k] = float(v)
                    except (ValueError, TypeError):
                        cleaned[k] = v
            rows.append(cleaned)
    return rows

def to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if "date" in df.columns:
        vals = df["date"]
        if vals.dtype in (float, "float64"):
            vals = vals.fillna(0).astype(int).astype(str)
        else:
            vals = vals.astype(str).str.replace(r"\.0$", "", regex=True)
        df["date"] = vals
    return df


def rows_to_bars(rows: list[dict], symbol: str, freq: Freq) -> list:
    """直接从 CSV rows 创建 czsc RawBar，绕过 adapter 的日期解析问题"""
    from datetime import datetime as dt_type
    bars = []
    for i, r in enumerate(rows):
        date_str = str(r.get("date", "")).strip()
        # 清理: "20150902.0" → "20150902", "202606021500" → "202606021500"
        date_str = date_str.replace(".0", "") if date_str.endswith(".0") else date_str

        close = r.get("close")
        if close is None or close == "" or (isinstance(close, float) and np.isnan(close)):
            continue
        close = float(close)
        if close <= 0:
            continue

        # 判断日期格式: 8位(日线) vs 12位(分钟线)
        if len(date_str) >= 12:
            fmt = "%Y%m%d%H%M"
        else:
            fmt = "%Y%m%d"
        try:
            dt = dt_type.strptime(date_str[:12] if len(date_str) > 12 else date_str, fmt)
        except (ValueError, TypeError):
            continue

        bars.append(RawBar(
            symbol=symbol,
            id=i,
            dt=dt,
            freq=freq,
            open=float(r.get("open", close)),
            close=close,
            high=float(r.get("high", close)),
            low=float(r.get("low", close)),
            vol=float(r.get("volume", 0) or 0),
            amount=float(r.get("amount", 0) or 0),
        ))
    return bars

# ============================================================
# 2. 缠论 per-bar 状态映射
# ============================================================

def _dt_clean(dt_str):
    """统一日期格式: '2026-06-02 09:15:30' or '20260602.0' → '20260602'"""
    s = str(dt_str).strip()
    # 去掉非数字字符(保留前8位日期数字)
    digits = "".join(c for c in s[:12] if c.isdigit())
    return digits[:8] or "0"

def _build_bar_map(bi_list: list, row_dates: list):
    """将 czsc 的 bi_list 映射到 signal_csv 的每一行

    返回:
      bar_to_bi: dict[row_idx] -> {direction, change, ...}"""
    n = len(row_dates)
    bar_to_bi = {i: None for i in range(n)}

    for bi in bi_list:
        bi_s = _dt_clean(bi.get("sdt", ""))
        bi_e = _dt_clean(bi.get("edt", ""))
        if not bi_s or len(bi_s) < 8:
            continue

        bi_dir_str = str(bi.get("direction", ""))
        dir_val = 1 if "向上" in bi_dir_str else (-1 if "向下" in bi_dir_str else 0)
        change = bi.get("change", 0) or 0

        for i, rd in enumerate(row_dates):
            rd_clean = _dt_clean(rd)
            if bi_s <= rd_clean <= bi_e:
                bar_to_bi[i] = {
                    "direction": dir_val,
                    "change": change,
                }
            elif rd_clean > bi_e:
                break  # 已过此笔结束日，后续 bar 更晚

    return bar_to_bi


def _compute_macd_divergence(bi_list, row_dates, daily_rows, segments, th=0.5):
    """基于中枢+MACD面积的背驰检测（替代笔力度版本）

    从段终点往前取5/7/9笔，中间n-2笔构成中枢，
    比较进入中枢笔 vs 离开中枢笔的MACD红绿柱面积。

    签名同 _detect_divergence_from_bi
    返回: {bar_idx: divergence_score}
        >0 = 底背驰（看涨）, 越大越背驰
        <0 = 顶背驰（看跌）, 越小越背驰
        0 = 无背驰
    """
    n = len(row_dates)
    bar_div = {i: 0.0 for i in range(n)}
    if not bi_list or not daily_rows or not segments:
        return bar_div

    # 日期→macd_hist/ macd_dif 映射
    date_macd = {}
    date_dif = {}
    for r in daily_rows:
        d = _dt_clean(r.get("date", ""))
        if d:
            date_macd.setdefault(d, []).append(r.get("macd_hist", 0) or 0)
            date_dif.setdefault(d, []).append(r.get("macd_dif", 0) or 0)

    def _bi_macd_vals(bi):
        """提取笔内所有 bar 的 macd_hist"""
        sdt = _dt_clean(bi.get("sdt", ""))
        edt = _dt_clean(bi.get("edt", ""))
        vals = []
        for rd in row_dates:
            if sdt <= rd <= edt:
                vals.extend(date_macd.get(rd, []))
        return vals

    def _bi_dif_end(bi):
        """笔终点附近的 DIF 值"""
        edt = _dt_clean(bi.get("edt", ""))
        if edt in date_dif and date_dif[edt]:
            return date_dif[edt][-1]
        return None

    for s_bi, e_bi, seg_dir in segments:
        bis = bi_list[s_bi:e_bi]
        if len(bis) < 5:
            continue

        last_bi = bis[-1]

        for n_pen in (9, 7, 5):
            if len(bis) < n_pen:
                continue
            cand = bis[-n_pen:]

            # 首尾必须同向（都向下=底背驰，都向上=顶背驰）
            d_first = str(cand[0].get("direction", ""))
            d_last = str(cand[-1].get("direction", ""))
            if ("向上" in d_first) != ("向上" in d_last):
                continue

            # 中间 n-2 笔构成中枢
            zs_bis = cand[1:-1]
            up_highs = [b["high"] for b in zs_bis if "向上" in str(b.get("direction", ""))]
            down_lows = [b["low"] for b in zs_bis if "向下" in str(b.get("direction", ""))]
            if not up_highs or not down_lows:
                continue
            zg = min(up_highs)
            zd = max(down_lows)
            if zg <= zd:
                continue

            bi1, bi2 = cand[0], cand[-1]
            bi1_macd = _bi_macd_vals(bi1)
            bi2_macd = _bi_macd_vals(bi2)
            if len(bi1_macd) < 3 or len(bi2_macd) < 3:
                continue

            is_up = "向上" in d_first

            if is_up:
                bi1_area = sum(x for x in bi1_macd if x > 0)
                bi2_area = sum(x for x in bi2_macd if x > 0)
            else:
                bi1_area = abs(sum(x for x in bi1_macd if x < 0))
                bi2_area = abs(sum(x for x in bi2_macd if x < 0))

            if bi1_area < 0.001:
                continue

            area_ratio = bi2_area / bi1_area

            if area_ratio > th:
                continue

            # 价格结构条件
            max_high = max(b["high"] for b in cand)
            min_low = min(b["low"] for b in cand)

            if is_up:
                if cand[0]["low"] != min_low or cand[-1]["high"] != max_high:
                    continue
                score = -min(1.0, 1 - area_ratio)
            else:
                if cand[0]["high"] != max_high or cand[-1]["low"] != min_low:
                    continue
                score = min(1.0, 1 - area_ratio)

            # 只赋给段内最后一笔覆盖的 bar
            last_sdt = _dt_clean(last_bi.get("sdt", ""))
            last_edt = _dt_clean(last_bi.get("edt", ""))
            for i, rd in enumerate(row_dates):
                if last_sdt <= rd <= last_edt:
                    bar_div[i] = score
            break

    return bar_div


def _compute_stroke_divergence(bi_list, row_dates, segments):
    """笔力度背驰 — 基于段内首尾笔的power_price/volume/length比较

    czsc cxt_first_buy_V221126 的简化版本：
    段内首尾同向笔，三维力度（价格+量+长度）均减弱 = 背驰

    返回: {bar_idx: divergence_score}
        >0 = 底背驰, <0 = 顶背驰, 0 = 无
    """
    n = len(row_dates)
    bar_div = {i: 0.0 for i in range(n)}

    for s_bi, e_bi, direction in segments:
        bis = bi_list[s_bi:e_bi]
        if len(bis) < 3:
            continue

        first, last = bis[0], bis[-1]

        chg_f = abs(first.get("change", 0) or 0)
        chg_l = abs(last.get("change", 0) or 0)
        vol_f = first.get("power_volume", 0) or 0
        vol_l = last.get("power_volume", 0) or 0
        len_f = first.get("length", 0) or 1
        len_l = last.get("length", 0) or 1

        r_price = 1 - chg_l / chg_f if chg_f > 0.001 else 0
        r_vol = 1 - vol_l / vol_f if vol_f > 0.001 else 0
        r_len = 1 - len_l / len_f

        # czsc 条件: price + (vol or length)
        if direction == -1 and r_price > 0 and (r_vol > 0 or r_len > 0):
            score = min(1.0, r_price)
        elif direction == 1 and r_price > 0 and (r_vol > 0 or r_len > 0):
            score = max(-1.0, -r_price)
        else:
            continue

        sdt = _dt_clean(last.get("sdt", ""))
        edt = _dt_clean(last.get("edt", ""))
        for i, rd in enumerate(row_dates):
            if sdt <= rd <= edt:
                bar_div[i] = score

    return bar_div


def _compute_combined_divergence(bi_list, row_dates, daily_rows, segments, th=0.5):
    """融合两种背驰：仅当两者方向一致时出信号，分歧归零

    笔力度背驰 (_compute_stroke_divergence) +
    MACD面积背驰 (_compute_macd_divergence) 的共识
    """
    macd_div = _compute_macd_divergence(bi_list, row_dates, daily_rows, segments, th=th)
    stroke_div = _compute_stroke_divergence(bi_list, row_dates, segments)

    n = len(row_dates)
    combined = {i: 0.0 for i in range(n)}
    for i in range(n):
        m = macd_div.get(i, 0.0)
        s = stroke_div.get(i, 0.0)
        # 两者方向一致（同号）才出信号
        if m * s > 0:
            combined[i] = (m + s) / 2
    return combined


def _map_zs_trend_context(daily_zs, row_dates, daily_rows):
    """中枢在趋势中的序号 + 连续位置值

    返回:
      zs_index: {bar_idx: 中枢序号(1=第一个/2=第二个)}
      zs_position: {bar_idx: 价格到中枢中心的连续距离, -1~1为中枢内部}
    """
    n = len(row_dates)
    zs_index = {i: 0 for i in range(n)}
    zs_position = {i: 0.0 for i in range(n)}

    # 第一步: 给每个中枢标序号
    zs_sorted = sorted(daily_zs, key=lambda z: _dt_clean(z.get("sdt", "")))
    current_dir = None
    current_idx = 0
    zs_with_index = []

    for zs in zs_sorted:
        zs_dir = str(zs.get("direction", ""))
        if "向上" in zs_dir:
            trend_dir = 1
        elif "向下" in zs_dir:
            trend_dir = -1
        else:
            trend_dir = 0

        if trend_dir != current_dir:
            current_dir = trend_dir
            current_idx = 1
        else:
            current_idx += 1

        zs_with_index.append({
            "zs": zs,
            "index": current_idx,
            "edt": _dt_clean(zs.get("edt", "")),
        })

    # 第二步: 每根 bar 找最近已结束的中枢
    for i, rd in enumerate(row_dates):
        rd_clean = _dt_clean(rd)
        close_val = daily_rows[i].get("close", 0) or 0
        best = None
        for zwi in zs_with_index:
            if zwi["edt"] and zwi["edt"] <= rd_clean:
                best = zwi
        if best:
            zs = best["zs"]
            zs_index[i] = best["index"]
            zg = zs.get("zg", 0) or 0
            zd = zs.get("zd", 0) or 0
            if zg > 0 and zd > 0 and close_val > 0:
                center = (zg + zd) / 2
                half_range = (zg - zd) / 2
                if half_range > 0:
                    zs_position[i] = (close_val - center) / half_range

    return zs_index, zs_position


def map_chanlun_features(daily_rows, daily_bi_list, daily_zs, min30_zs, segments, div_th=0.5):
    """综合日线+30分钟缠论分析，生成每根 K 线的缠论特征"""
    n = len(daily_rows)
    row_dates = [_dt_clean(r.get("date", "")) for r in daily_rows]

    # --- 日线笔映射 ---
    bar_to_bi = _build_bar_map(daily_bi_list, row_dates)

    # --- 日线中枢: 序号 + 连续位置 + 离散位置 ---
    zs_index, zs_position = _map_zs_trend_context(daily_zs, row_dates, daily_rows)
    bar_zs_pos = {i: 0 for i in range(n)}
    for i, rd in enumerate(row_dates):
        pos = zs_position.get(i, 0.0)
        if pos > 1.0:
            bar_zs_pos[i] = 1    # 中枢上方
        elif pos >= -1.0:
            bar_zs_pos[i] = 0    # 中枢内部
        else:
            bar_zs_pos[i] = -1   # 中枢下方

    # --- 30分钟中枢: 序号 + 连续位置 + 离散位置 ---
    zs30_index, zs30_position = _map_zs_trend_context(min30_zs, row_dates, daily_rows)
    bar_zs30_pos = {i: 0 for i in range(n)}
    for i in range(n):
        pos = zs30_position.get(i, 0.0)
        if pos > 1.0:
            bar_zs30_pos[i] = 1
        elif pos >= -1.0:
            bar_zs30_pos[i] = 0
        else:
            bar_zs30_pos[i] = -1

    # --- 背驰: 融合笔力度+MACD面积，两者一致才出信号 ---
    bar_div = _compute_combined_divergence(daily_bi_list, row_dates, daily_rows, segments, th=div_th)

    # --- 组装特征 ---
    features = []
    for i in range(n):
        bi = bar_to_bi.get(i)
        f = {
            "date": row_dates[i],
            "trend_dir": bi["direction"] if bi else 0,
            "bi_strength": bi["change"] if bi else 0,
            "zs_daily": bar_zs_pos.get(i, 0),
            "zs_min30": bar_zs30_pos.get(i, 0),
            "zs_index": zs_index.get(i, 0),
            "zs_position": round(zs_position.get(i, 0.0), 4),
            "zs30_index": zs30_index.get(i, 0),
            "zs30_position": round(zs30_position.get(i, 0.0), 4),
            "divergence": bar_div.get(i, 0),
        }
        features.append(f)

    return features


def _detect_segments(bi_list):
    """用czsc一买/一卖逻辑检测段边界

    从尾部往前遍历，奇数笔序列(5~21)+首笔极值+末笔背驰。
    一买=下跌段完成, 一卖=上涨段完成。

    Returns: [(start_bi_idx, end_bi_idx, direction), ...]
             direction: 1=上涨段, -1=下跌段
    """
    n = len(bi_list)
    if n < 5:
        return []

    segments = []
    pos = n

    while pos >= 5:
        found = False
        for seg_len in [5, 7, 9, 11, 13, 15, 17, 19, 21]:
            start = pos - seg_len
            if start < 0:
                continue

            bis = bi_list[start:pos]
            d_first = str(bis[0].get("direction", ""))
            d_last = str(bis[-1].get("direction", ""))

            if ("向上" in d_first) != ("向上" in d_last):
                continue  # 首尾不同向

            if "向上" in d_first:
                # 一卖: 上涨段完成 — 首笔最低、末笔最高、末笔背驰
                if (min(b["low"] for b in bis) != bis[0]["low"] or
                    max(b["high"] for b in bis) != bis[-1]["high"]):
                    continue
            else:
                # 一买: 下跌段完成 — 首笔最高、末笔最低、末笔背驰
                if (max(b["high"] for b in bis) != bis[0]["high"] or
                    min(b["low"] for b in bis) != bis[-1]["low"]):
                    continue

            # 背驰: power_price < 倒数第三笔 且 (量或长度有一项背驰)
            if (bis[-1]["power_price"] < bis[-3]["power_price"] and
                (bis[-1]["power_volume"] < bis[-3]["power_volume"] or
                 bis[-1]["length"] < bis[-3]["length"])):
                direction = 1 if "向上" in d_first else -1
                segments.append((start, pos, direction))
                pos = start
                found = True
                break

        if not found:
            pos -= 1

    segments.reverse()
    return segments


def _assign_segment_to_bars(segments, bi_list, row_dates):
    """给每根bar分配所属段方向

    segments: [(start_bi, end_bi, direction), ...]
    row_dates: list[str] (YYYYMMDD)

    Returns: dict[bar_idx -> direction], direction=1/-1
    """
    if not segments:
        return {}

    # 段边界从笔索引转日期范围
    ranges = []
    for s_bi, e_bi, direction in segments:
        sdt = _dt_clean(bi_list[s_bi].get("sdt", ""))
        edt = _dt_clean(bi_list[e_bi - 1].get("edt", ""))
        ranges.append((sdt, edt, direction))

    bar_seg = {}
    for i, rd in enumerate(row_dates):
        for sdt, edt, direction in ranges:
            if sdt <= rd <= edt:
                bar_seg[i] = direction
                break
    return bar_seg


def compute_min5_buy_density(min5_rows, daily_bi_list, daily_rows):
    """对每根日线bar: 当前**段**范围内5分钟★买的累计次数

    段边界由czsc一买/一卖确定(奇数笔+首笔极值+末笔背驰)。
    段内所有5分钟★买(无论是笔级回调还是笔内部回调)都累计。
    段结束(新段开始)时清零。
    """
    n_daily = len(daily_rows)
    if not min5_rows:
        return [0] * n_daily

    # 5分钟★买按日期索引
    min5_buys = {}
    for mr in min5_rows:
        d = _dt_clean(mr.get("date", ""))
        if str(mr.get("buy_signal", "")) == "★买":
            min5_buys[d] = min5_buys.get(d, 0) + 1

    # 段边界 + 每根bar的段归属
    daily_dates = [_dt_clean(r.get("date", "")) for r in daily_rows]
    segments = _detect_segments(daily_bi_list)
    bar_seg = _assign_segment_to_bars(segments, daily_bi_list, daily_dates)

    # 按段分组: 连续相同段的bar为一组
    groups = []
    prev_dir = 0
    seg_start = 0
    for i in range(n_daily):
        cur_dir = bar_seg.get(i, 0)
        if cur_dir != prev_dir:
            if prev_dir != 0:
                groups.append((prev_dir, seg_start, i))
            prev_dir = cur_dir
            seg_start = i
    if prev_dir != 0:
        groups.append((prev_dir, seg_start, n_daily))

    # 段内累计
    density = [0] * n_daily
    for g_dir, s, e in groups:
        running = 0
        for i in range(s, e):
            running += min5_buys.get(daily_dates[i], 0)
            density[i] = running

    return density


# ============================================================
# 3. 评分特征提取
# ============================================================

def map_scoring_features(daily_rows, min5_buy_density=None):
    """从信号 CSV 直接提取评分特征"""
    n = len(daily_rows)

    features = []
    for i, r in enumerate(daily_rows):
        close = r.get("close", 0) or 0
        expma12 = r.get("expma12", 0) or 0
        expma50 = r.get("expma50", 0) or 0
        cci = r.get("cci", 0) or 0
        macd_dif = r.get("macd_dif", 0) or 0
        macd_dea = r.get("macd_dea", 0) or 0

        # EXPMA 位置
        if close > expma12 > 0:
            expma_pos = 1   # 白线上方
        elif close > expma50 > 0:
            expma_pos = 0   # 白黄之间
        else:
            expma_pos = -1  # 黄线下方

        # ABCD 级别 (简化版: MACD 状态)
        if macd_dif > 0 and macd_dea > 0:
            abcd = 3 if macd_dif > macd_dea else 2  # A=3, B=2
        elif macd_dif > 0 or macd_dea > 0:
            abcd = 1  # C=1
        else:
            abcd = 0  # D=0

        # PE 水平
        pe = r.get("pe", 0.5) or 0.5
        if pe > 0.8:
            pe_level = 2
        elif pe > 0.5:
            pe_level = 1
        else:
            pe_level = 0

        # CCI 背驰 (从 CSV 预计算字段提取)
        cci_div_str = str(r.get("cci_divergence", ""))
        if "底背驰" in cci_div_str:
            div_cci = 1.0
        elif "顶背驰" in cci_div_str:
            div_cci = -1.0
        else:
            # 极值预信号: 到-200但尚未出背驰 = 潜在底背驰酝酿
            cci_ext = str(r.get("cci_extreme", ""))
            if "CCI-200" in cci_ext:
                div_cci = 0.3
            elif "CCI+200" in cci_ext:
                div_cci = -0.3
            else:
                div_cci = 0.0

        # PE regime: 持续(低), 混沌(中), 转折(高)
        pe_raw = r.get("pe", 0.5) or 0.5
        pe_chg = r.get("pe_chg_5", 0) or 0
        if pe_raw < 0.3:
            pe_regime = 1      # 趋势持续
        elif pe_raw > 0.7:
            pe_regime = -1     # 趋势转折
        else:
            pe_regime = 0      # 混沌噪音

        # HHT
        hht_amp = r.get("hht_amp", 0) or 0
        hht_freq = r.get("hht_freq", 0.5) or 0.5

        f = {
            "sc_trend_score": _calc_trend_score(r),
            "sc_abcd": abcd,
            "sc_cci": cci,
            "sc_expma_pos": expma_pos,
            "sc_has_buy": 1 if r.get("buy_signal") == "★买" else 0,
            "sc_has_sell": 1 if r.get("sell_signal") == "★卖" else 0,
            "sc_vr60": r.get("vr60", 1) or 1,
            "sc_pe_level": pe_level,
            "sc_div_cci": div_cci,
            "sc_buy_density": min5_buy_density[i] if min5_buy_density and i < len(min5_buy_density) else 0,
            # PE+HHT regime-aware
            "sc_pe_raw": round(pe_raw, 4),
            "sc_pe_chg": round(pe_chg, 4),
            "sc_pe_regime": pe_regime,
            "sc_hht_amp": round(hht_amp, 4),
            "sc_hht_freq": round(hht_freq, 4),
        }
        features.append(f)
    return features


def _calc_trend_score(r):
    """快速估算趋势评分 (简化版 0-14)"""
    score = 0
    # MACD (0-4)
    dif = r.get("macd_dif", 0) or 0
    dea = r.get("macd_dea", 0) or 0
    hist = r.get("macd_hist", 0) or 0
    if dif > 0 and dea > 0 and dif > dea:
        score += 4
    elif dif > 0 and dea > 0:
        score += 3
    elif dif > 0 or dea > 0:
        score += 2
    elif dif < 0 and dea < 0:
        score += 1
    # MA 排列 (0-6)
    ma_keys = ["ma5", "ma10", "ma20", "ma60", "ma120", "ma250"]
    ma_vals = [(r.get(k, 0) or 0) for k in ma_keys]
    for j in range(len(ma_vals) - 1):
        if ma_vals[j] > 0 and ma_vals[j + 1] > 0 and ma_vals[j] > ma_vals[j + 1]:
            score += 1
        else:
            break
    # 闭环 (0-4)
    cross = r.get("expma_cross", "")
    buy = r.get("buy_signal", "")
    if cross == "金叉":
        score += 2
    if buy == "★买":
        score += 1
    return score


# ============================================================
# 4. 标签生成
# ============================================================

def generate_labels(daily_rows, n_days=5, threshold=0.05):
    """未来 N 天涨跌幅 → R1(好) / R5(坏) / R0(中间)

    n_days: 验证天数
    threshold: 涨幅阈值（涨超此值=R1，跌超此值=R5）
    """
    n = len(daily_rows)
    labels = [0] * n  # 默认 R0
    for i in range(n):
        close_now = daily_rows[i].get("close", 0) or 0
        if close_now <= 0:
            continue
        # 找未来第 n_days 个 bar
        future_idx = min(i + n_days, n - 1)
        close_future = daily_rows[future_idx].get("close", 0) or 0
        if close_future <= 0:
            continue
        ret = (close_future - close_now) / close_now
        if ret > threshold:
            labels[i] = 1   # R1: 好
        elif ret < -threshold:
            labels[i] = -1  # R5: 坏
        # else: 0 = R0, 不参与训练
    return labels


# ============================================================
# 5. 主流程: 特征矩阵构建
# ============================================================

def build_dataset(codes, n_days=5, threshold=0.05, max_codes=None, div_th=0.5):
    """遍历所有标的，生成特征矩阵 X 和标签 y"""
    all_X = []
    all_y = []
    stats = {"total_bars": 0, "R1": 0, "R5": 0, "errors": 0}

    if max_codes:
        codes = codes[:max_codes]

    for ci, code in enumerate(codes):
        if ci % 10 == 0:
            print(f"  [{ci+1}/{len(codes)}] {code} ...")

        try:
            # 读取数据
            daily_rows = load_csv(code, "daily")
            min30_rows = load_csv(code, "min30")
            min5_rows = load_csv(code, "min5")
            if len(daily_rows) < 100 or len(min30_rows) < 50:
                continue

            # 运行 czsc 分析 (绕过 adapter, 直接创建 RawBar)
            bars_daily = rows_to_bars(daily_rows, code, Freq.D)
            bars_min30 = rows_to_bars(min30_rows, code, Freq.F30)

            c_daily = CZSC(bars_daily, max_bi_num=500)
            c_min30 = CZSC(bars_min30, max_bi_num=500)

            # 只调用安全的结构函数，避免 SMA#34 崩溃
            daily_struct = get_structure_info(c_daily)
            daily_bi_list = daily_struct.get("bi_list", [])
            daily_zs = get_zs_info(c_daily)
            min30_zs = get_zs_info(c_min30)

            # 生成特征 (背驰由 bi_list 直接算，不依赖 SMA)
            segments = _detect_segments(daily_bi_list)
            cl_features = map_chanlun_features(
                daily_rows, daily_bi_list, daily_zs, min30_zs, segments, div_th=div_th
            )
            # ★买密度：段边界内的5分钟★买累计
            min5_density = compute_min5_buy_density(min5_rows, daily_bi_list, daily_rows)
            sc_features = map_scoring_features(daily_rows, min5_buy_density=min5_density)

            # 生成标签
            labels = generate_labels(daily_rows, n_days, threshold)

            # 合并特征矩阵
            for i in range(len(daily_rows)):
                cl = cl_features[i]
                sc = sc_features[i]
                if labels[i] == 0:
                    continue  # 跳过 R0

                row = {}
                # 缠论
                for k, v in cl.items():
                    if k != "date":
                        row[f"cl_{k}"] = v
                # 评分
                for k, v in sc.items():
                    row[k] = v
                # 交互特征
                row["ix_trend_x_zs"] = (row.get("sc_trend_score", 0) or 0) * \
                                        (row.get("cl_zs_daily", 0) or 0)
                row["ix_trend_x_bi"] = (row.get("sc_trend_score", 0) or 0) * \
                                        (row.get("cl_trend_dir", 0) or 0)
                row["ix_abcd_x_zs"] = (row.get("sc_abcd", 0) or 0) * \
                                       (row.get("cl_zs_daily", 0) or 0)
                row["ix_zs_position_x_score"] = (row.get("cl_zs_position", 0) or 0) * \
                                                 (row.get("sc_trend_score", 0) or 0)
                # 第二个中枢 + 底背驰 = 强买入信号
                is_zs2 = 1 if (row.get("cl_zs_index", 0) or 0) >= 2 else 0
                row["ix_zs2_x_div"] = is_zs2 * (row.get("cl_divergence", 0) or 0)
                # 背驰 + 信号群联立
                row["ix_div_x_density"] = (row.get("cl_divergence", 0) or 0) * \
                                          (row.get("sc_buy_density", 0) or 0)
                # 趋势方向 × 背驰
                row["ix_trend_x_div"] = (row.get("cl_trend_dir", 0) or 0) * \
                                        (row.get("cl_divergence", 0) or 0)

                # === PE+HHT regime-gated 交互 ===
                # PE × 非背驰笔: 低PE=趋势持续
                cl_div = row.get("cl_divergence", 0) or 0
                is_div = abs(cl_div) > 0.001
                pe_raw = row.get("sc_pe_raw", 0.5) or 0.5
                trend_dir = row.get("cl_trend_dir", 0) or 0
                row["ix_pe_trend"] = (0 if is_div else 1) * (1 - pe_raw) * trend_dir
                # PE × 背驰笔: 高PE=转折确认
                row["ix_pe_div"] = (1 if is_div else 0) * pe_raw * cl_div
                # HHT × 离开中枢: 价格走出中枢+HHT幅值=爆发方向
                zs_pos = row.get("cl_zs_position", 0) or 0
                leaving_zs = 1.0 if abs(zs_pos) > 0.8 else 0.0
                hht_amp = row.get("sc_hht_amp", 0) or 0
                row["ix_hht_breakout"] = leaving_zs * hht_amp * trend_dir

                # 保存日期用于时序分割
                row["_date"] = int(cl.get("date", "0"))

                all_X.append(row)
                all_y.append(1 if labels[i] == 1 else 0)  # 二分类: R1=1, R5=0

            stats["total_bars"] += len(daily_rows)
            stats["R1"] += sum(1 for lb in labels if lb == 1)
            stats["R5"] += sum(1 for lb in labels if lb == -1)

        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 5:
                print(f"    ! {code} 错误: {e}")

    print(f"\n  总计: {stats['total_bars']} 条日线, R1={stats['R1']}, R5={stats['R5']}, 错误={stats['errors']}")
    print(f"  有效样本: {len(all_X)} 条 (排除R0后)")

    return pd.DataFrame(all_X), np.array(all_y)


# ============================================================
# 6. XGBoost 训练 + 分析
# ============================================================

def run_xgboost(X, y, time_split=False):
    import xgboost as xgb
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, roc_auc_score, confusion_matrix

    # 处理缺失值
    X = X.fillna(0).replace([np.inf, -np.inf], 0)

    if time_split:
        # 时序分割: 按日期排序后切分，80%早→训练，20%晚→测试
        sort_idx = np.argsort(X["_date"].values)
        X_sorted = X.iloc[sort_idx]
        y_sorted = y[sort_idx]
        split_idx = int(len(X_sorted) * 0.8)
        X_train = X_sorted.iloc[:split_idx].drop(columns=["_date"])
        X_test = X_sorted.iloc[split_idx:].drop(columns=["_date"])
        y_train = y_sorted[:split_idx]
        y_test = y_sorted[split_idx:]
        print(f"\n时序分割: {len(X_train)} 训练 ({_dt_clean(str(X_sorted.iloc[:split_idx]['_date'].min()))} ~ {_dt_clean(str(X_sorted.iloc[:split_idx]['_date'].max()))})")
        print(f"           {len(X_test)} 测试 ({_dt_clean(str(X_sorted.iloc[split_idx:]['_date'].min()))} ~ {_dt_clean(str(X_sorted.iloc[split_idx:]['_date'].max()))})")
    else:
        # 随机分割
        X = X.drop(columns=["_date"])
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

    print(f"正样本(R1): {y_train.sum()} / {y_test.sum()} (训练/测试)")

    # 训练
    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        eval_metric='logloss',
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    # 评估
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, y_prob)
    print(f"\n{'='*60}")
    print(f"模型评估")
    print(f"{'='*60}")
    print(classification_report(y_test, y_pred, target_names=["R5(坏)", "R1(好)"]))
    print(f"AUC: {auc:.4f}")

    cm = confusion_matrix(y_test, y_pred)
    print(f"混淆矩阵: TP={cm[1][1]}, FP={cm[0][1]}, TN={cm[0][0]}, FN={cm[1][0]}")

    # 特征重要性
    feat_cols = [c for c in X.columns if c != "_date"]
    importance = pd.DataFrame({
        "feature": feat_cols,
        "importance": model.feature_importances_,
    }).sort_values("importance", ascending=False)

    print(f"\n{'='*60}")
    print(f"特征重要性 (Top 20)")
    print(f"{'='*60}")
    for _, row in importance.head(20).iterrows():
        bar = "█" * int(row["importance"] / importance["importance"].max() * 40)
        print(f"  {row['feature']:<25s} {row['importance']:.4f} {bar}")

    # 分类汇总
    print(f"\n{'='*60}")
    print(f"特征类别汇总")
    print(f"{'='*60}")
    categories = {
        "缠论骨架": [c for c in X.columns if c.startswith("cl_")],
        "评分血肉": [c for c in X.columns if c.startswith("sc_")],
        "交互特征": [c for c in X.columns if c.startswith("ix_")],
    }
    for cat, cols in categories.items():
        imp_sum = importance[importance["feature"].isin(cols)]["importance"].sum()
        print(f"  {cat:<15s} {imp_sum:.4f} ({imp_sum/importance['importance'].sum()*100:.1f}%)")

    metrics = {"auc": round(auc, 4), "n_test": len(y_test)}
    return model, importance, metrics


# ============================================================
# 7. 入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("笔记研究 · 实验 #1: 缠论+评分 × XGBoost 特征验证")
    print("=" * 60)

    # 配置
    N_DAYS = 20       # 验证周期: 20天
    THRESHOLD = 0.05  # 涨跌阈值: 5%

    print(f"\n配置: N天={N_DAYS}, 阈值={THRESHOLD*100}%")

    # 加载标的
    codes = load_all_codes()
    print(f"\n标的: {len(codes)} 只")

    # 提问: 是否限制数量测试？
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", type=int, default=0, help="快速模式: 只跑前N只标的")
    ap.add_argument("--n_days", type=int, default=N_DAYS)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--time-split", action="store_true", help="用时序分割(替代随机分割)")
    args = ap.parse_args()

    max_codes = args.quick if args.quick > 0 else None

    t0 = datetime.now()

    # 构建数据集
    print(f"\n>>> 构建特征矩阵...")
    X, y = build_dataset(codes, n_days=args.n_days, threshold=args.threshold, max_codes=max_codes)
    print(f"  耗时: {(datetime.now() - t0).total_seconds():.0f}s")
    print(f"  特征维度: {X.shape[1]} 列")

    # 训练 XGBoost
    print(f"\n>>> 训练 XGBoost{' (时序分割)' if args.time_split else ' (随机分割)'}...")
    model, importance, _ = run_xgboost(X, y, time_split=args.time_split)
    print(f"\n  总耗时: {(datetime.now() - t0).total_seconds():.0f}s")

    print(f"\n{'='*60}")
    print("实验 #1 完成。根据特征重要性调整下一轮特征设计。")
    print("=" * 60)

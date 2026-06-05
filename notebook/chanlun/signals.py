"""
完整信号提取层：调用 czsc 所有信号函数

输出覆盖:
  结构层 → 笔列表/分型列表/中枢序列/未完成笔
  买卖点 → 一买/一卖(5~21笔) + 二买/二卖(均线辅助+并列) + 三买/三卖(均线辅助+形态)
  形态层 → 三笔/五笔/七笔/九笔/十一笔 形态分类
  笔状态 → 表里关系 + 笔结束辅助(11种方法) + 笔趋势 + BI涨跌幅分层 + 止损距离
  决策层 → 分型区域决策 + 高低点决策 + 放量笔决策 + 趋势跟随买卖点
  支撑压力 → 顶底重合 + 顺畅笔支撑压力
"""
import sys
from typing import Dict, List

import numpy as np
from czsc import CZSC
from czsc.core import Direction
from czsc.utils.sig import get_zs_seq
from czsc.signals.cxt import (
    cxt_first_buy_V221126,
    cxt_first_sell_V221126,
    cxt_third_buy_V230228,
    cxt_second_bs_V230320,
    cxt_third_bs_V230319,
    cxt_second_bs_V240524,
    cxt_bi_base_V230228,
    cxt_bi_end_V230222,
    cxt_bi_end_V230224,
    cxt_bi_end_V230104,
    cxt_bi_end_V230105,
    cxt_bi_end_V230312,
    cxt_bi_end_V230320,
    cxt_bi_end_V230322,
    cxt_bi_end_V230324,
    cxt_bi_end_V230618,
    cxt_bi_end_V230815,
    cxt_ubi_end_V230816,
    cxt_bi_status_V230102,
    cxt_bi_zdf_V230601,
    cxt_three_bi_V230618,
    cxt_five_bi_V230619,
    cxt_seven_bi_V230620,
    cxt_nine_bi_V230621,
    cxt_eleven_bi_V230622,
    cxt_range_oscillation_V230620,
    cxt_double_zs_V230311,
    cxt_bi_stop_V230815,
    cxt_bi_trend_V230824,
    cxt_bi_trend_V230913,
    cxt_overlap_V240526,
    cxt_overlap_V240612,
    cxt_decision_V240526,
    cxt_decision_V240612,
    cxt_decision_V240613,
    cxt_decision_V240614,
    cxt_bs_V240526,
    cxt_bs_V240527,
)
from czsc.signals.tas import update_ma_cache, update_macd_cache  # noqa: cxt信号依赖这些缓存


# ──────────────────────────────────────────────
# 1. 结构层: 笔/分型/中枢/未完成笔
# ──────────────────────────────────────────────


def get_structure_info(c: CZSC) -> dict:
    """获取当前缠论结构信息

    Returns:
        bi_count: 笔数量
        bi_list: 每笔的力度/斜率/长度明细
        last_bi: 当前最后一笔(direction/power/change/length/slope/SNR)
        ubi_len: 未完成笔延伸K线数
        fx_count: 分型数量
    """
    info = {
        "bi_count": len(c.bi_list),
        "bi_list": [],
        "last_bi": {},
        "ubi_len": len(c.bars_ubi),
        "fx_count": len(c.fx_list),
    }
    for bi in c.bi_list:
        info["bi_list"].append({
            "direction": bi.direction.value,
            "power_price": round(bi.power_price, 4),
            "power_volume": round(bi.power_volume, 4),
            "length": bi.length,
            "change": round(bi.change, 4),
            "slope": round(bi.slope, 4),
            "snr": round(bi.SNR, 4),
            "high": round(bi.high, 4),
            "low": round(bi.low, 4),
            "sdt": str(bi.sdt),
            "edt": str(bi.edt),
        })
    if c.bi_list:
        last = c.bi_list[-1]
        info["last_bi"] = {
            "direction": last.direction.value,
            "power_price": round(last.power_price, 4),
            "power_volume": round(last.power_volume, 4),
            "length": last.length,
            "change": round(last.change, 4),
            "slope": round(last.slope, 4),
            "snr": round(last.SNR, 4),
            "high": round(last.high, 4),
            "low": round(last.low, 4),
            "sdt": str(last.sdt),
            "edt": str(last.edt),
        }
    return info


def get_zs_info(c: CZSC) -> List[dict]:
    """提取中枢序列

    Returns:
        每个中枢: sdt/edt/zg/zd/gg/dd/zz/direction/bi_count
    """
    zss = get_zs_seq(c.bi_list)
    result = []
    for zs in zss:
        result.append({
            "sdt": str(zs.sdt),
            "edt": str(zs.edt),
            "zg": round(zs.zg, 4),
            "zd": round(zs.zd, 4),
            "gg": round(zs.gg, 4),
            "dd": round(zs.dd, 4),
            "zz": round(zs.zz, 4),
            "direction": zs.sdir.value,
            "bi_count": len(zs.bis),
        })
    return result


def get_fx_info(c: CZSC) -> List[dict]:
    """提取最近分型列表(最多20个)"""
    result = []
    for fx in c.fx_list[-20:]:
        result.append({
            "mark": fx.mark.value,
            "high": round(fx.high, 4),
            "low": round(fx.low, 4),
            "power": fx.power_str,
            "dt": str(fx.dt),
        })
    return result


# ──────────────────────────────────────────────
# 2. 买卖点: 一买/一卖 + 二买/二卖 + 三买/三卖
# ──────────────────────────────────────────────


def get_bs_points(c: CZSC) -> dict:
    """完整买卖点信号提取(含全部版本)

    调用 czsc 信号函数:
      - 一买/一卖: cxt_first_buy/sell_V221126 (5~21笔奇数背驰)
      - 二买/二卖: cxt_second_bs_V230320 (均线辅助) + cxt_second_bs_V240524 (并列分型重叠)
      - 三买: cxt_third_buy_V230228 (笔三买) + cxt_third_bs_V230319 (均线辅助+形态)
      - 趋势跟随: cxt_bs_V240526 (笔内) + cxt_bs_V240527 (未完成笔)
    """
    result = {
        "buy1": [], "sell1": [],
        "buy2": [], "sell2": [],
        "buy3": [], "sell3": [],
        "trend_buy": [], "trend_sell": [],
        "last_bi_direction": "",
        "last_bi_power": 0.0,
        "ubi_len": 0,
    }

    if len(c.bi_list) < 5:
        return result

    # ── 一买/一卖 ──
    sig = {}
    sig.update(cxt_first_buy_V221126(c, di=1))
    sig.update(cxt_first_sell_V221126(c, di=1))
    for key, value in sig.items():
        sv = str(value)
        if "一买" in sv:
            bi_count = _parse_bi_count(sv)
            result["buy1"].append({"source": key, "signal": sv, "bi_count": bi_count})
        if "一卖" in sv:
            bi_count = _parse_bi_count(sv)
            result["sell1"].append({"source": key, "signal": sv, "bi_count": bi_count})

    # ── 三买(笔形态) ──
    tb = cxt_third_buy_V230228(c, di=1)
    for key, value in tb.items():
        sv = str(value)
        if "三买" in sv:
            bi_count = _parse_bi_count(sv)
            result["buy3"].append({"source": key, "signal": sv, "bi_count": bi_count, "variant": "pen_based"})

    # ── 二买/二卖(均线辅助) ──
    bs2 = cxt_second_bs_V230320(c, di=1, ma_type="SMA", timeperiod=21)
    for key, value in bs2.items():
        sv = str(value)
        if "二买" in sv:
            result["buy2"].append({"source": key, "signal": sv, "variant": "sma_assist"})
        if "二卖" in sv:
            result["sell2"].append({"source": key, "signal": sv, "variant": "sma_assist"})

    # ── 并列二买/二卖(中枢视角) ──
    bs2b = cxt_second_bs_V240524(c, di=1, w=9, t=2)
    for key, value in bs2b.items():
        sv = str(value)
        if "二买" in sv:
            result["buy2"].append({"source": key, "signal": sv, "variant": "parallel_zs"})
        if "二卖" in sv:
            result["sell2"].append({"source": key, "signal": sv, "variant": "parallel_zs"})

    # ── 三买/三卖(均线辅助+形态) ──
    bs3 = cxt_third_bs_V230319(c, di=1, ma_type="SMA", timeperiod=34)
    for key, value in bs3.items():
        sv = str(value)
        if "三买" in sv or "三卖" in sv:
            target = "buy3" if "三买" in sv else "sell3"
            ma_pattern = sv.split("_")[-1] if "均线" in sv else ""
            result[target].append({
                "source": key, "signal": sv,
                "variant": "sma_assist", "ma_pattern": ma_pattern,
            })

    # ── 双中枢辅助BS1 ──
    dzs = cxt_double_zs_V230311(c, di=1)
    for key, value in dzs.items():
        sv = str(value)
        if "看多" in sv:
            result["buy1"].append({"source": key, "signal": sv, "variant": "double_zs"})
        if "看空" in sv:
            result["sell1"].append({"source": key, "signal": sv, "variant": "double_zs"})

    # ── 趋势跟随买卖点 ──
    for sig_func, variant in [(cxt_bs_V240526, "pen"), (cxt_bs_V240527, "ubi")]:
        bs = sig_func(c)
        for key, value in bs.items():
            sv = str(value)
            if "买点" in sv:
                result["trend_buy"].append({"source": key, "signal": sv, "variant": variant})
            if "卖点" in sv:
                result["trend_sell"].append({"source": key, "signal": sv, "variant": variant})

    # ── 当前笔状态 ──
    if c.bi_list:
        last_bi = c.bi_list[-1]
        result["last_bi_direction"] = last_bi.direction.value
        result["last_bi_power"] = round(last_bi.power_price, 4)
    result["ubi_len"] = len(c.bars_ubi)

    return result


# ──────────────────────────────────────────────
# 3. 形态层: 三笔/五笔/七笔/九笔/十一笔
# ──────────────────────────────────────────────


def get_pattern_signals(c: CZSC) -> dict:
    """形态分类信号(全部N笔形态)

    返回值如:
      three_bi: "向下盘背"/"向上奔走型"/"向上收敛"/...
      five_bi: "aAb式底背驰"/"类趋势顶背驰"/"类三买"/...
      seven_bi: "aAbcd式底背驰"/"向上中枢完成"/"类三卖"/...
      nine_bi: "ABC式类一买"/"aAbBc式类一卖"/"类三买A"/"ZD三卖"/...
      eleven_bi: "A5B3C3式类一买"/"类二买"/"类三买"/...
    """
    result = {
        "three_bi": "其他",
        "five_bi": "其他",
        "seven_bi": "其他",
        "nine_bi": "其他",
        "eleven_bi": "其他",
        "range_oscillation": "其他",
    }

    # 三笔形态
    tb = cxt_three_bi_V230618(c, di=1)
    for key, value in tb.items():
        sv = str(value)
        if sv != "其他":
            result["three_bi"] = sv

    # 五笔形态
    fb = cxt_five_bi_V230619(c, di=1)
    for key, value in fb.items():
        sv = str(value)
        if sv != "其他":
            result["five_bi"] = sv

    # 七笔形态
    sb = cxt_seven_bi_V230620(c, di=1)
    for key, value in sb.items():
        sv = str(value)
        if sv != "其他":
            result["seven_bi"] = sv

    # 九笔形态
    nb = cxt_nine_bi_V230621(c, di=1)
    for key, value in nb.items():
        sv = str(value)
        if sv != "其他":
            result["nine_bi"] = sv

    # 十一笔形态
    eb = cxt_eleven_bi_V230622(c, di=1)
    for key, value in eb.items():
        sv = str(value)
        if sv != "其他":
            result["eleven_bi"] = sv

    # 区间震荡
    ro = cxt_range_oscillation_V230620(c, di=1, th=5)
    for key, value in ro.items():
        sv = str(value)
        if "震荡" in sv:
            result["range_oscillation"] = sv

    return result


# ──────────────────────────────────────────────
# 4. 笔状态辅助: 表里关系 + 笔结束 + 笔趋势 + 止损
# ──────────────────────────────────────────────


def get_pen_status(c: CZSC) -> dict:
    """笔状态辅助信号(全部)

    - 表里关系: cxt_bi_status_V230102
    - BI基础: cxt_bi_base_V230228 (中继/转折)
    - BI涨跌幅分层: cxt_bi_zdf_V230601
    - 笔结束辅助 × 11种方法
    - 笔趋势 × 2种方法
    - 止损距离
    """
    result = {
        "surface_interior": {},        # 表里关系
        "bi_base": {},                 # BI基础(中继/转折)
        "bi_zdf_layer": {},            # BI涨跌幅分层
        "bi_end_signals": [],          # 笔结束辅助(汇总)
        "bi_end_errors": [],           # 笔结束检测异常(不静默吞)
        "bi_trend": {},                # 笔趋势
        "bi_stop_distance": {},        # 止损距离
    }

    # 表里关系
    si = cxt_bi_status_V230102(c)
    for key, value in si.items():
        sv = str(value)
        if "其他" not in sv:
            parts = sv.split("_")
            result["surface_interior"] = {
                "direction": parts[2] if len(parts) > 2 else "",
                "pattern": parts[3] if len(parts) > 3 else "",
            }

    # BI基础(中继/转折)
    bb = cxt_bi_base_V230228(c, bi_init_length=9)
    for key, value in bb.items():
        sv = str(value)
        if "其他" not in sv:
            result["bi_base"] = {
                "direction": str(value).split("_")[2] if len(str(value).split("_")) > 2 else "",
                "status": str(value).split("_")[3] if len(str(value).split("_")) > 3 else "",
            }

    # BI涨跌幅分层
    bz = cxt_bi_zdf_V230601(c, di=1, n=5)
    for key, value in bz.items():
        sv = str(value)
        if "其他" not in sv:
            result["bi_zdf_layer"] = {
                "direction": str(value).split("_")[2],
                "layer": str(value).split("_")[3],
            }

    # ── 笔结束辅助(11种方法) ──
    be_functions = [
        (cxt_bi_end_V230222, {"max_overlap": 3}, "新高新低计数"),
        (cxt_bi_end_V230224, {}, "量价配合"),
        (cxt_bi_end_V230104, {"ma_type": "SMA", "timeperiod": 5, "th": 50}, "单均线"),
        (cxt_bi_end_V230105, {"ma_type": "SMA", "timeperiod": 5, "th": 50}, "K线形态+均线"),
        (cxt_bi_end_V230312, {}, "MACD辅助"),
        (cxt_bi_end_V230320, {"max_overlap": 3}, "质数窗口"),
        (cxt_bi_end_V230322, {"ma_type": "SMA", "timeperiod": 5}, "分型配合均线"),
        (cxt_bi_end_V230324, {"ma_type": "SMA", "timeperiod": 5}, "均线突破"),
        (cxt_bi_end_V230618, {"di": 1, "max_overlap": 3}, "笔内小中枢"),
        (cxt_bi_end_V230815, {}, "快速突破"),
        (cxt_ubi_end_V230816, {}, "未完成笔新高新低"),
    ]
    for func, params, method_name in be_functions:
        try:
            sig = func(c, **params)
            for key, value in sig.items():
                sv = str(value)
                if "其他" not in sv:
                    result["bi_end_signals"].append({
                        "method": method_name,
                        "signal": sv,
                    })
        except Exception as e:
            result["bi_end_errors"].append({
                "method": method_name,
                "error": f"{type(e).__name__}: {e}",
            })

    # ── 笔趋势 ──
    bt1 = cxt_bi_trend_V230824(c, di=1, n=4, th=2)
    for key, value in bt1.items():
        sv = str(value)
        if "其他" not in sv:
            result["bi_trend"]["nbi_trend"] = sv
    bt2 = cxt_bi_trend_V230913(c, di=4, n=1)
    for key, value in bt2.items():
        sv = str(value)
        if "其他" not in sv:
            result["bi_trend"]["channel_trend"] = sv

    # ── 止损距离 ──
    bs = cxt_bi_stop_V230815(c, th=50)
    for key, value in bs.items():
        sv = str(value)
        if "其他" not in sv:
            result["bi_stop_distance"] = {
                "signal": sv,
            }

    return result


# ──────────────────────────────────────────────
# 5. 决策层 + 支撑压力
# ──────────────────────────────────────────────


def get_decision_signals(c: CZSC) -> dict:
    """决策区域信号 + 支撑压力"""
    result = {
        "fractal_zone_decision": {},
        "highlow_decision": {},
        "volume_decision": {},
        "overlap_support_pressure": {},
        "snr_support_pressure": {},
    }

    # 分型区域决策
    for sig_func, _variant in [(cxt_decision_V240526, "fractal_zone")]:
        d = sig_func(c, n=9)
        for key, value in d.items():
            sv = str(value)
            if "其他" not in sv:
                result["fractal_zone_decision"] = {"signal": sv}

    # 高低点决策
    d2 = cxt_decision_V240612(c, w=10, n=5)
    for key, value in d2.items():
        sv = str(value)
        if "其他" not in sv:
            result["highlow_decision"] = {"signal": sv}

    # 放量笔决策(含两种)
    for sig_func, variant in [(cxt_decision_V240613, "no_new_hl"), (cxt_decision_V240614, "new_hl")]:
        d = sig_func(c, n=4)
        for key, value in d.items():
            sv = str(value)
            if "其他" not in sv:
                result["volume_decision"][variant] = sv

    # 顶底重合支撑压力
    o1 = cxt_overlap_V240526(c)
    for key, value in o1.items():
        sv = str(value)
        if "其他" not in sv:
            result["overlap_support_pressure"] = {"signal": sv}

    # 顺畅笔支撑压力
    o2 = cxt_overlap_V240612(c, n=7)
    for key, value in o2.items():
        sv = str(value)
        if "其他" not in sv:
            result["snr_support_pressure"] = {"signal": sv}

    return result


# ──────────────────────────────────────────────
# 6. 统一入口: 完整分析
# ──────────────────────────────────────────────


def full_analysis(c: CZSC) -> dict:
    """单级别完整缠论分析(全部输出)

    一次性输出: 结构 + 中枢 + 分型 + 买卖点 + 形态 + 笔状态 + 决策
    """
    return {
        "freq": c.freq.value,
        "structure": get_structure_info(c),
        "zhongshu": get_zs_info(c),
        "fractals": get_fx_info(c),
        "bs_points": get_bs_points(c),
        "patterns": get_pattern_signals(c),
        "pen_status": get_pen_status(c),
        "decisions": get_decision_signals(c),
    }


# ──────────────────────────────────────────────
# 7. 工具函数
# ──────────────────────────────────────────────


def _parse_bi_count(signal_str: str) -> int:
    """从信号字符串中解析笔数"""
    import re
    match = re.search(r'(\d+)笔', signal_str)
    return int(match.group(1)) if match else 0

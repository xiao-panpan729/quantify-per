"""
双级别联立定位：日线 + 30分钟 完整缠论结构分析

输出覆盖:
  - 单级别分析: 结构/中枢/分型/买卖点/形态/笔状态/决策
  - 双级别联立: 中枢共振 + 多级别定位分类
  - 中枢穿透: 大级别中枢 vs 小级别买卖点关系
"""
from czsc import CZSC, Freq
from czsc.core import Direction

from notebook.chanlun.adapter import df_to_bars
from notebook.chanlun.signals import full_analysis, get_zs_info


def get_position(df_daily, df_30min, symbol: str) -> dict:
    """日线 + 30分钟 双级别联立分析 — 笔记本系统的标准入口

    Args:
        df_daily: 日线 OHLCV DataFrame
        df_30min: 30分钟 OHLCV DataFrame
        symbol: 标的代码

    Returns:
        {
            "symbol": 标的代码,
            "position": 标准化位置描述 (如 "日线上涨笔延续（30分钟买点确认）"),
            "daily_pen_direction": 日线笔方向 "向上"/"向下"/"",
            "min30_bs_zone": 30分钟买卖点区域 "一买区域"/"三买区域"/... 或 "",
            "zhongshu_resonance": 中枢共振 "看多"/"看空"/"无",
            "consistency": 双级别方向一致性 "一致"/"不一致"/"",
            "daily": 日线完整分析,
            "min30": 30分钟完整分析,
        }
    """
    # 日线分析
    daily_bars = df_to_bars(df_daily, symbol, Freq.D)
    c_daily = CZSC(daily_bars)
    daily_result = full_analysis(c_daily)

    # 30分钟分析
    min30_bars = df_to_bars(df_30min, symbol, Freq.F30)
    c_30min = CZSC(min30_bars)
    min30_result = full_analysis(c_30min)

    # 双级别联立
    position_desc = _classify_position(daily_result, min30_result)
    resonance = _check_zhongshu_resonance(c_daily, c_30min)
    multi_level = _multi_level_linkage(daily_result, min30_result)

    # 提取关键字段
    daily_dir = daily_result.get("structure", {}).get("last_bi", {}).get("direction", "")
    min30_bs = min30_result.get("bs_points", {})
    bs_zone = ""
    if min30_bs.get("buy1"):
        bs_zone = "一买区域"
    elif min30_bs.get("buy2"):
        bs_zone = "二买区域"
    elif min30_bs.get("buy3"):
        bs_zone = "三买区域"
    elif min30_bs.get("sell1"):
        bs_zone = "一卖区域"
    elif min30_bs.get("sell2"):
        bs_zone = "二卖区域"
    elif min30_bs.get("sell3"):
        bs_zone = "三卖区域"

    return {
        "symbol": symbol,
        "position": position_desc,
        "daily_pen_direction": daily_dir,
        "min30_bs_zone": bs_zone,
        "zhongshu_resonance": resonance.get("resonance", "无"),
        "consistency": multi_level.get("consistency", ""),
        "daily": daily_result,
        "min30": min30_result,
    }


def dual_level_analysis(df_daily, df_30min, symbol: str) -> dict:
    """[兼容别名] 等同于 get_position()"""
    return get_position(df_daily, df_30min, symbol)


def _classify_position(daily: dict, min30: dict) -> str:
    """综合日线和30分钟结构，输出标准化位置描述

    位置分类:
    - "日线上涨笔延续" — 日线向上笔，30分钟无背驰
    - "日线回调笔"     — 日线向下笔
    - "日线中枢震荡"   — 日线上下笔在中枢区间内
    - "30分钟买点区域" — 30分钟出现一买/二买/三买
    - "30分钟卖点区域" — 30分钟出现一卖/二卖/三卖
    - "等待方向确认"   — 笔数不足，方向不明确
    """
    daily_bi = daily.get("structure", {}).get("last_bi", {})
    daily_dir = daily_bi.get("direction", "")
    min30_bs = min30.get("bs_points", {})

    if not daily_dir:
        return "等待方向确认"

    # 日线向上，看30分钟是否出现卖点
    if daily_dir == "向上":
        if min30_bs.get("sell1") or min30_bs.get("sell2"):
            return "30分钟卖点区域（日线上涨笔中的回调风险）"
        if min30_bs.get("buy1") or min30_bs.get("buy2") or min30_bs.get("buy3"):
            return "日线上涨笔延续（30分钟买点确认）"
        if min30_bs.get("trend_sell"):
            return "日线上涨笔延续（30分钟趋势卖点信号）"
        min30_dir = min30.get("structure", {}).get("last_bi", {}).get("direction", "")
        if min30_dir == "向下" and not min30_bs.get("buy1"):
            return "日线上涨笔延续（30分钟正常回调）"
        return "日线上涨笔延续"

    # 日线向下，看30分钟是否出现买点
    if daily_dir == "向下":
        if min30_bs.get("buy1") or min30_bs.get("buy2") or min30_bs.get("buy3"):
            return "30分钟买点区域（日线回调笔末端）"
        if min30_bs.get("trend_buy"):
            return "日线回调笔延续（30分钟趋势买点信号）"
        return "日线回调笔延续"

    return "等待方向确认"


def _check_zhongshu_resonance(c_daily: CZSC, c_30min: CZSC) -> dict:
    """中枢共振检测: 大级别中枢中轴 vs 小级别买卖点的位置关系

    简化逻辑: 小级别中枢DD > 大级别中枢中轴 → 看多
             小级别中枢GG < 大级别中枢中轴 → 看空
    """
    result = {
        "resonance": "无",
        "big_zs": {},
        "small_zs": {},
    }

    daily_zss = get_zs_info(c_daily)
    min30_zss = get_zs_info(c_30min)

    if daily_zss:
        result["big_zs"] = daily_zss[-1]
    if min30_zss:
        result["small_zs"] = min30_zss[-1]

    if daily_zss and min30_zss and c_30min.bi_list:
        big_zz = daily_zss[-1]["zz"]
        small_dd = min30_zss[-1]["dd"]
        small_gg = min30_zss[-1]["gg"]

        if small_dd > big_zz and c_30min.bi_list[-1].direction == Direction.Down:
            result["resonance"] = "看多"
        elif small_gg < big_zz and c_30min.bi_list[-1].direction == Direction.Up:
            result["resonance"] = "看空"

    return result


def _multi_level_linkage(daily: dict, min30: dict) -> dict:
    """多级别联立定位

    检查:
    - 日线中枢对30分钟当前笔的"引力/支撑"作用
    - 30分钟买卖点是否在日线中枢范围内
    - 日线最后一笔的方向与30分钟笔的一致性
    """
    result = {
        "consistency": "",
        "min30_in_daily_zs": False,
        "note": "",
    }

    daily_bi = daily.get("structure", {}).get("last_bi", {})
    min30_bi = min30.get("structure", {}).get("last_bi", {})
    daily_dir = daily_bi.get("direction", "")
    min30_dir = min30_bi.get("direction", "")

    if daily_dir and min30_dir:
        result["consistency"] = "一致" if daily_dir == min30_dir else "不一致"

    # 检查30分钟最后一笔的区间是否在日线中枢范围内
    daily_zss = daily.get("zhongshu", [])
    if daily_zss and min30_bi:
        zs = daily_zss[-1]
        zg, zd = zs["zg"], zs["zd"]
        bi_low = min30_bi.get("low")
        bi_high = min30_bi.get("high")
        if zd and zg and bi_low is not None and bi_high is not None:
            # 笔的高低点至少部分在中枢区间内
            result["min30_in_daily_zs"] = not (bi_low > zg or bi_high < zd)

    # 综合描述
    notes = []
    daily_bs = daily.get("bs_points", {})
    min30_bs = min30.get("bs_points", {})
    if daily_bs.get("buy1") or daily_bs.get("buy2") or daily_bs.get("buy3"):
        notes.append("日线级别出现买点")
    if daily_bs.get("sell1") or daily_bs.get("sell2") or daily_bs.get("sell3"):
        notes.append("日线级别出现卖点")
    if min30_bs.get("buy1"):
        notes.append("30分钟一买区域")
    if min30_bs.get("sell1"):
        notes.append("30分钟一卖区域")
    if min30_bs.get("trend_buy"):
        notes.append("30分钟趋势买点")
    if min30_bs.get("trend_sell"):
        notes.append("30分钟趋势卖点")
    result["note"] = "; ".join(notes) if notes else "无特殊信号"

    return result

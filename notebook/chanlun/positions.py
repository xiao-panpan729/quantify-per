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

    # 双级别联立（传入CZSC同量级的收盘价，与中枢边界对齐）
    daily_close = daily_bars[-1].close if daily_bars else None
    position_desc = _classify_position(daily_result, min30_result, daily_close)
    resonance = _check_zhongshu_resonance(c_daily, c_30min)
    multi_level = _multi_level_linkage(daily_result, min30_result)
    # 价格缩放因子：日线中枢价格 → 30分钟笔价格
    m30_close = min30_bars[-1].close if min30_bars else 0
    d_close = daily_bars[-1].close if daily_bars else 1
    price_scale = m30_close / d_close if d_close else 1
    divergence = _divergence_check(daily_result, min30_result, price_scale)

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
        "divergence": divergence,
        "daily": daily_result,
        "min30": min30_result,
    }


def dual_level_analysis(df_daily, df_30min, symbol: str) -> dict:
    """[兼容别名] 等同于 get_position()"""
    return get_position(df_daily, df_30min, symbol)


def _classify_position(daily: dict, min30: dict, daily_close: float = None) -> str:
    """综合日线和30分钟结构，输出标准化位置描述

    以中枢为纲，笔为目：
    - 先判定中枢线段级别方向（中枢下移→下跌趋势，中枢上移→上涨趋势，否则→震荡）
    - 再判定当前价格在中枢的什么位置（上方/下方/内部/边界）
    - 最后补充笔级细节和30分钟信号
    """
    daily_bi = daily.get("structure", {}).get("last_bi", {})
    daily_dir = daily_bi.get("direction", "")
    ubi_len = daily.get("structure", {}).get("ubi_len", 0)
    min30_bs = min30.get("bs_points", {})

    if not daily_dir:
        return "等待方向确认"

    ubi_dir = _ubi_direction(daily_bi, daily_close, ubi_len)
    seg = _zhongshu_segment(daily, daily_close)

    # ── 中枢线段级别判定 ──
    trend = seg["trend"]       # 上涨线段 / 下跌线段 / 中枢震荡 / 中枢震荡偏强 / 中枢震荡偏弱
    zs_pos = seg["position"]   # 中枢上方 / 中枢下方 / 中枢内部 / 中枢上沿 / 中枢下沿 / 跌破中枢下轨 / 突破中枢上轨
    zs_seq = seg["zs_seq"]     # 中枢序列方向（两中枢下移... 等）

    # ── 笔级细节 ──
    pen_note = ""
    if ubi_dir == "向上" and ubi_len >= 3:
        pen_note = "，下行笔已终结，反弹中"
    elif ubi_dir == "向下" and ubi_len >= 2:
        pen_note = "，从高点回落中"
    elif ubi_dir == "向上" and 1 <= ubi_len <= 2:
        pen_note = "，关注反弹能否延续"
    elif ubi_dir == "向下" and ubi_len == 1:
        pen_note = "，关注是否继续回落"

    # ── 30分钟买卖点（历史事件，用括号标注） ──
    bs_note = ""
    if min30_bs.get("buy1"):
        bs_note = "（30分钟一买信号）"
    elif min30_bs.get("buy2"):
        bs_note = "（30分钟二买信号）"
    elif min30_bs.get("buy3"):
        bs_note = "（30分钟三买信号）"
    elif min30_bs.get("sell1"):
        bs_note = "（30分钟一卖信号）"
    elif min30_bs.get("sell2"):
        bs_note = "（30分钟二卖信号）"
    elif min30_bs.get("sell3"):
        bs_note = "（30分钟三卖信号）"

    # ── 拼接 ──
    if zs_seq:
        zs_seq = f"（{zs_seq}）"

    if zs_pos in ("跌破中枢下轨", "突破中枢上轨"):
        return f"{trend}，{zs_pos}{zs_seq}{pen_note}{bs_note}"
    elif trend in ("中枢震荡", "中枢震荡偏强", "中枢震荡偏弱"):
        return f"{trend}，{zs_pos}{pen_note}{bs_note}"
    elif trend:
        return f"{trend}，{zs_pos}{zs_seq}{pen_note}{bs_note}"

    # 无有效中枢时的降级处理
    return f"{'上涨' if daily_dir == '向上' else '下跌'}方向运行{pen_note}{bs_note}"


def _ubi_direction(last_bi: dict, current_close: float, ubi_len: int) -> str:
    """推断未完成笔的方向：对比当前价与末笔端点

    末向下笔的端点是 low，末向上笔的端点是 high。
    需要 ≥2 根K线 + 0.5%以上的偏离才确认方向。
    """
    if current_close is None or ubi_len < 2:
        return ""
    direction = last_bi.get("direction", "")
    if direction == "向下":
        ref = last_bi.get("low")
        return "向上" if ref and current_close > ref * 1.005 else ""
    elif direction == "向上":
        ref = last_bi.get("high")
        return "向下" if ref and current_close < ref * 0.995 else ""
    return ""


def _zhongshu_segment(daily: dict, current_close: float) -> dict:
    """中枢+线段级别的结构判断

    先看中枢关系判定线段方向，再看当前价在中枢的什么位置。
    只使用≥3笔的有效中枢，1笔雏形忽略。

    Returns: {trend: str, position: str, zs_seq: str}
    """
    if current_close is None:
        return {"trend": "", "position": "", "zs_seq": ""}

    zss = daily.get("zhongshu", [])
    valid = [zs for zs in zss if zs.get("bi_count", 0) >= 3]

    if not valid:
        return {"trend": "", "position": "", "zs_seq": ""}

    last = valid[-1]
    zg, zd, gg, dd = last["zg"], last["zd"], last["gg"], last["dd"]
    zz = last["zz"]

    # ── 中枢序列方向（≥2个有效中枢时判断） ──
    zs_seq = ""
    if len(valid) >= 2:
        prev = valid[-2]
        if zz < prev["zz"]:
            zs_seq = f"两中枢下移下跌趋势"
        elif zz > prev["zz"]:
            zs_seq = f"两中枢上移上涨趋势"

    # ── 当前价 vs 中枢边界 ──
    if current_close <= dd:
        return {"trend": "下跌线段延续", "position": "跌破中枢下轨", "zs_seq": zs_seq}
    elif current_close >= gg:
        return {"trend": "上涨线段延续", "position": "突破中枢上轨", "zs_seq": zs_seq}
    elif current_close < zd:
        return {"trend": "中枢震荡偏弱", "position": "中枢下沿（下轨附近）", "zs_seq": zs_seq}
    elif current_close > zg:
        return {"trend": "中枢震荡偏强", "position": "中枢上沿（上轨附近）", "zs_seq": zs_seq}
    else:
        return {"trend": "中枢震荡", "position": "中枢内部", "zs_seq": zs_seq}


def _divergence_check(daily_structure: dict, min30_structure: dict, price_scale: float = 1.0) -> dict:
    """背驰检测：同时检查底背驰（向下笔力度衰减）和顶背驰（向上笔力度衰减）

    返回最强的背驰信号。底背驰优先于顶背驰（底比顶更可靠）。
    """
    # 底背驰（向下笔→买点）
    bottom = _check_level_div(min30_structure, "30分钟", "down")
    if bottom["type"] == "trend":
        bottom = _downgrade_internal_divergence(bottom, daily_structure, price_scale, "down")

    # 顶背驰（向上笔→卖点）
    top = _check_level_div(min30_structure, "30分钟", "up")
    if top["type"] == "trend":
        top = _downgrade_internal_divergence(top, daily_structure, price_scale, "up")

    # 选更强信号：底背驰 > 顶背驰 > 日线兜底
    for cand in [bottom, top]:
        if cand["type"] != "none":
            return cand

    # 日线兜底
    daily_bottom = _check_level_div(daily_structure, "日线", "down")
    if daily_bottom["type"] != "none":
        return daily_bottom
    return _check_level_div(daily_structure, "日线", "up")


def _downgrade_internal_divergence(min30_div: dict, daily_structure: dict,
                                     price_scale: float = 1.0, direction: str = "down") -> dict:
    """日线中枢穿透三档判定（底背驰/顶背驰对称）

    底背驰(direction="down")：30分钟向下段 vs 日线中枢
      A级 — 进入段高点 > 日线ZG（从上方穿过中枢）→ 趋势背驰
      B级 — 全程在日线ZG-ZD内部 → 盘整背驰
      C级 — 从日线中枢内部跌破ZD → 小级别盘整背驰

    顶背驰(direction="up")：30分钟向上段 vs 日线中枢
      A级 — 进入段低点 < 日线ZD（从下方穿过中枢）→ 趋势背驰
      B级 — 全程在日线ZG-ZD内部 → 盘整背驰
      C级 — 从日线中枢内部突破ZG → 小级别盘整背驰
    """
    daily_zss = daily_structure.get("zhongshu", [])
    valid_daily = [z for z in daily_zss if z.get("bi_count", 0) >= 3]
    if len(valid_daily) < 1:
        return min30_div

    ep = min30_div.get("entering_power", 0)
    lp = min30_div.get("leaving_power", 0)
    last_zs = valid_daily[-1]
    daily_zg = last_zs["zg"] * price_scale
    daily_zd = last_zs["zd"] * price_scale

    if direction == "down":
        entering_high = min30_div.get("entering_high", 0)
        leaving_low = min30_div.get("leaving_low", 0)
        if not entering_high or not leaving_low:
            return min30_div

        starts_outside = entering_high > daily_zg       # 从日线中枢上方进入
        stays_inside = leaving_low >= daily_zd           # 离开段还在中枢内部

        if starts_outside:
            return {
                "type": "trend", "level": min30_div["level"],
                "detail": f"趋势背驰（日线验证A级）：{min30_div['level']}下跌力度从{ep:.2f}衰减到{lp:.2f}，"
                           f"30分钟从日线中枢上方（{entering_high:.2f} > ZG{daily_zg:.2f}）穿过中枢至下方，可期待趋势反转",
                "entering_power": ep, "leaving_power": lp, "direction": "bottom",
            }
        elif stays_inside:
            return {
                "type": "consolidation", "level": min30_div["level"],
                "detail": f"盘整背驰（日线验证B级）：{min30_div['level']}下跌力度从{ep:.2f}衰减到{lp:.2f}，"
                           f"30分钟全程在日线中枢内部（{entering_high:.2f}→{leaving_low:.2f} ⊆ Z{daily_zg:.2f}-Z{daily_zd:.2f}），中枢内波动，看反弹一笔",
                "entering_power": ep, "leaving_power": lp, "direction": "bottom",
            }
        else:
            return {
                "type": "minor", "level": min30_div["level"],
                "detail": f"小级别盘整背驰（日线验证C级）：{min30_div['level']}下跌力度从{ep:.2f}衰减到{lp:.2f}，"
                           f"30分钟从中枢内部跌穿ZD（{entering_high:.2f}→{leaving_low:.2f} < ZD{daily_zd:.2f}），延续下跌趋势中的弱反弹，级别最小",
                "entering_power": ep, "leaving_power": lp, "direction": "bottom",
            }
    else:
        # 顶背驰：向上段 vs 日线中枢（对称反转）
        entering_low = min30_div.get("entering_low", 0)
        leaving_high = min30_div.get("leaving_high", 0)
        if not entering_low or not leaving_high:
            return min30_div

        starts_outside = entering_low < daily_zd        # 从日线中枢下方进入
        stays_inside = leaving_high <= daily_zg          # 离开段还在中枢内部

        if starts_outside:
            return {
                "type": "trend", "level": min30_div["level"],
                "detail": f"趋势背驰（日线验证A级）：{min30_div['level']}上涨力度从{ep:.2f}衰减到{lp:.2f}，"
                           f"30分钟从日线中枢下方（{entering_low:.2f} < ZD{daily_zd:.2f}）穿过中枢至上方，可期待趋势反转",
                "entering_power": ep, "leaving_power": lp, "direction": "top",
            }
        elif stays_inside:
            return {
                "type": "consolidation", "level": min30_div["level"],
                "detail": f"盘整背驰（日线验证B级）：{min30_div['level']}上涨力度从{ep:.2f}衰减到{lp:.2f}，"
                           f"30分钟全程在日线中枢内部（{entering_low:.2f}→{leaving_high:.2f} ⊆ Z{daily_zg:.2f}-Z{daily_zd:.2f}），中枢内波动，看回落一笔",
                "entering_power": ep, "leaving_power": lp, "direction": "top",
            }
        else:
            return {
                "type": "minor", "level": min30_div["level"],
                "detail": f"小级别盘整背驰（日线验证C级）：{min30_div['level']}上涨力度从{ep:.2f}衰减到{lp:.2f}，"
                           f"30分钟从中枢内部突破ZG（{entering_low:.2f}→{leaving_high:.2f} > ZG{daily_zg:.2f}），延续上涨趋势中的弱回落，级别最小",
                "entering_power": ep, "leaving_power": lp, "direction": "top",
            }


def _check_level_div(structure: dict, level: str, direction: str = "down") -> dict:
    """单级别背驰检测（底背驰/顶背驰对称）

    底背驰(direction="down"): 向下笔力度衰减 → 进入段=最高向下笔, 离开段=最后向下笔, 须创新低
    顶背驰(direction="up"):   向上笔力度衰减 → 进入段=最低向上笔, 离开段=最后向上笔, 须创新高

    策略：只看最近15笔内的中枢和笔，不追溯到远古数据。
    """
    all_pens = structure.get("structure", {}).get("bi_list", [])
    all_zs = structure.get("zhongshu", [])
    result_dir = "bottom" if direction == "down" else "top"

    if len(all_pens) < 5:
        return {"type": "none", "level": level, "detail": "笔数不足",
                "entering_power": 0, "leaving_power": 0,
                "entering_high": 0, "leaving_low": 0, "entering_low": 0, "leaving_high": 0,
                "direction": result_dir}

    # ── 只看最近15笔 ──
    pens = all_pens[-15:]

    if direction == "down":
        # ── 底背驰：向下笔力度衰减 ──
        target_pens = [p for p in pens if p["direction"] == "向下"]
        if len(target_pens) < 2:
            return {"type": "none", "level": level, "detail": "最近15笔内向下笔不足",
                    "entering_power": 0, "leaving_power": 0,
                    "entering_high": 0, "leaving_low": 0, "entering_low": 0, "leaving_high": 0,
                    "direction": result_dir}

        entering = max(target_pens[:-1], key=lambda p: p["high"])  # 下跌起点(最高点)
        leaving = target_pens[-1]                                   # 最后向下笔

        if leaving["low"] >= entering["low"]:
            return {"type": "none", "level": level, "detail": "",
                    "entering_power": entering["power_price"], "leaving_power": leaving["power_price"],
                    "entering_high": entering["high"], "leaving_low": leaving["low"],
                    "entering_low": entering["low"], "leaving_high": leaving["high"],
                    "direction": result_dir}

        # 有效中枢：ZG < 进入段高点 且 ZD > 离开段低点
        raw_zs = [z for z in all_zs
                  if z.get("bi_count", 0) >= 3
                  and z.get("zg", 999) < entering["high"]
                  and z.get("zd", -999) > leaving["low"]]
    else:
        # ── 顶背驰：向上笔力度衰减 ──
        target_pens = [p for p in pens if p["direction"] == "向上"]
        if len(target_pens) < 2:
            return {"type": "none", "level": level, "detail": "最近15笔内向上笔不足",
                    "entering_power": 0, "leaving_power": 0,
                    "entering_high": 0, "leaving_low": 0, "entering_low": 0, "leaving_high": 0,
                    "direction": result_dir}

        entering = min(target_pens[:-1], key=lambda p: p["low"])  # 上涨起点(最低点)
        leaving = target_pens[-1]                                  # 最后向上笔

        if leaving["high"] <= entering["high"]:
            return {"type": "none", "level": level, "detail": "",
                    "entering_power": entering["power_price"], "leaving_power": leaving["power_price"],
                    "entering_high": entering["high"], "leaving_low": entering["low"],
                    "entering_low": entering["low"], "leaving_high": leaving["high"],
                    "direction": result_dir}

        # 有效中枢：ZG < 离开段高点 且 ZD > 进入段低点（上涨中枢在下方支撑）
        raw_zs = [z for z in all_zs
                  if z.get("bi_count", 0) >= 3
                  and z.get("zg", 999) < leaving["high"]
                  and z.get("zd", -999) > entering["low"]]

    entering_pow = entering["power_price"]
    leaving_pow = leaving["power_price"]

    # ── 中枢去重叠（含 GG/DD 中枢扩张检测） ──
    # 两个中枢真正独立的条件：
    #   1. ZG(下) < ZD(上) — ZG/ZD 不重叠（中枢新生）
    #   2. GG(下) ≤ DD(上) — GG/DD 不交叉（无中枢扩张）
    # 任一条件不满足 → 合并（中枢延伸或扩张）
    raw_zs.sort(key=lambda z: z["zg"], reverse=True)
    merged = []
    for zs in raw_zs:
        if not merged:
            merged.append(dict(zs))
        elif zs["zg"] < merged[-1]["zd"]:
            # ZG 分离 → 再查 GG/DD 是否交叉
            zs_gg = zs.get("gg", zs["zg"])
            last_dd = merged[-1].get("dd", merged[-1]["zd"])
            if zs_gg <= last_dd:
                merged.append(dict(zs))  # 真正独立 → 中枢新生
            else:
                # GG/DD 交叉 → 中枢扩张 → 合并为大级别中枢
                merged[-1]["zg"] = max(merged[-1]["zg"], zs["zg"])
                merged[-1]["zd"] = min(merged[-1]["zd"], zs["zd"])
                merged[-1]["gg"] = max(merged[-1].get("gg", 0), zs_gg)
                merged[-1]["dd"] = min(last_dd, zs.get("dd", zs["zd"]))
        else:
            # ZG 重叠 → 中枢延伸 → 合并
            merged[-1]["zg"] = max(merged[-1]["zg"], zs["zg"])
            merged[-1]["zd"] = min(merged[-1]["zd"], zs["zd"])
            merged[-1]["gg"] = max(merged[-1].get("gg", merged[-1]["zg"]),
                                   zs.get("gg", zs["zg"]))
            merged[-1]["dd"] = min(merged[-1].get("dd", merged[-1]["zd"]),
                                   zs.get("dd", zs["zd"]))
    zs_count = len(merged)

    # 力度比较
    dir_label = "下跌" if direction == "down" else "上涨"
    if leaving_pow >= entering_pow:
        return {
            "type": "none", "level": level,
            "detail": f"{level}{dir_label}力度未衰减({entering_pow:.2f}→{leaving_pow:.2f})，无背驰",
            "entering_power": entering_pow, "leaving_power": leaving_pow,
            "entering_high": entering.get("high", 0), "leaving_low": leaving.get("low", 0),
            "entering_low": entering.get("low", 0), "leaving_high": leaving.get("high", 0),
            "direction": result_dir,
        }

    # 背驰成立
    if zs_count >= 2:
        return {
            "type": "trend", "level": level,
            "detail": f"趋势背驰：{level}{dir_label}力度从{entering_pow:.2f}衰减到{leaving_pow:.2f}（{zs_count}中枢），可期待趋势反转",
            "entering_power": entering_pow, "leaving_power": leaving_pow,
            "entering_high": entering.get("high", 0), "leaving_low": leaving.get("low", 0),
            "entering_low": entering.get("low", 0), "leaving_high": leaving.get("high", 0),
            "direction": result_dir,
        }
    else:
        zs_desc = f"{zs_count}中枢" if zs_count else "无完整中枢结构"
        return {
            "type": "consolidation", "level": level,
            "detail": f"盘整背驰：{level}{dir_label}力度从{entering_pow:.2f}衰减到{leaving_pow:.2f}（{zs_desc}），仅看反弹一笔或一波，非趋势反转",
            "entering_power": entering_pow, "leaving_power": leaving_pow,
            "entering_high": entering.get("high", 0), "leaving_low": leaving.get("low", 0),
            "entering_low": entering.get("low", 0), "leaving_high": leaving.get("high", 0),
            "direction": result_dir,
        }


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

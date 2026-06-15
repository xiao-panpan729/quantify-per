# -*- coding: utf-8 -*-
"""
缠论适配器 — get_position() → StandardSignal

映射:
  - S (方向): 底背驰→+1（顶背驰暂未实现，当前只检测向下笔力度衰减）
  - G (级别): 趋势背驰→A / 盘整背驰→B / 小级别盘整背驰→C
  - C (置信): A=0.80 / B=0.55 / C=0.30
  - Pow (强度): 力度衰减率 (entering - leaving) / entering，上限1.0

输入: signals/tracking/{code}/daily_signals.csv + min30_signals.csv
输出: List[StandardSignal] — 仅含有背驰信号的标的
"""

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.parent.parent
TRACKING_DIR = PROJECT_ROOT / "signals" / "tracking"
SIGNAL_ADAPTER_DIR = PROJECT_ROOT / "experts" / "signal_adapter"

for p in [str(PROJECT_ROOT), str(SIGNAL_ADAPTER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from standard_signal import StandardSignal, SignalType, EXPERT_IDS
from notebook.chanlun.positions import get_position

from config import NAME_MAP

EXPERT_ID = "ta_chanlun"

# 背驰类型 → (置信度, 操作级别)
DIVERGENCE_PROFILE = {
    "trend":         (0.80, "A"),
    "consolidation": (0.55, "B"),
    "minor":         (0.30, "C"),
}

# 买卖点 → 方向修正（有买点+底背驰→增强置信，有卖点+底背驰→降置信）
BS_BOOST = {
    "一买区域": +0.10,
    "二买区域": +0.05,
    "三买区域": +0.03,
    "一卖区域": -0.15,
    "二卖区域": -0.10,
    "三卖区域": -0.05,
}


def adapter_chanlun(codes: Optional[List[str]] = None) -> List[StandardSignal]:
    """读取所有跟踪标的的日线+30分钟数据 → 缠论分析 → StandardSignal 列表

    Args:
        codes: 要分析的标的代码列表，默认使用 config.NAME_MAP 全量
    """
    if codes is None:
        codes = list(NAME_MAP.keys())

    signals = []

    for code in codes:
        daily_path = TRACKING_DIR / code / "daily_signals.csv"
        min30_path = TRACKING_DIR / code / "min30_signals.csv"

        if not daily_path.exists() or not min30_path.exists():
            continue

        try:
            df_daily = pd.read_csv(daily_path)
            df_30min = pd.read_csv(min30_path)
        except Exception:
            continue

        if len(df_daily) < 60 or len(df_30min) < 50:
            continue

        try:
            result = get_position(df_daily, df_30min, code)
        except Exception:
            continue

        sig = _to_standard_signal(result, code)
        if sig is not None:
            signals.append(sig)

    return signals


def _to_standard_signal(result: dict, code: str) -> Optional[StandardSignal]:
    """缠论 → StandardSignal 纯净映射

    三个维度各自独立，不混合加权：
      S   ← 线段方向（仅趋势背驰可翻转）
      Pow ← 背驰衰减率 / 无背驰时线段力度对比
      C   ← 结构突破确认 + 背驰类型 + 共振加成
      G   ← 背驰级别: A(trend) / B(consolidation) / C(minor)
    """
    position = result.get("position", "")
    div = result.get("divergence", {})
    div_type = div.get("type", "none")
    div_dir = div.get("direction", "bottom")
    resonance = result.get("zhongshu_resonance", "无")
    consistency = result.get("consistency", "")
    daily_dir = result.get("daily_pen_direction", "")
    bs_zone = result.get("min30_bs_zone", "")

    # ═══════════════════════════════════════════
    # S: 纯结构方向 — 线段 / 中枢突破 / 中枢偏向
    #
    # 背驰不决定方向。背驰=力度衰减，只影响 Pow 和 C。
    # 顶背驰+上涨 ≠ 空，最多转震荡。底背驰+下跌 ≠ 多，最多转震荡。
    #
    # 例外：新高+顶背驰 / 新低+底背驰 → S=0
    # 背驰本身隐含创新高/新低（_check_level_div 强制要求），
    # 此时价格在极端位置但力度不支持延续，方向不明确。
    # ═══════════════════════════════════════════
    if "上涨线段" in position:
        S = 1
    elif "下跌线段" in position:
        S = -1
    elif "突破中枢上轨" in position:
        S = 1
    elif "跌破中枢下轨" in position:
        S = -1
    elif "中枢震荡偏强" in position:
        # 中枢内偏强 + 大级别向上 → 顺势看多
        # 中枢内偏强 + 大级别向下 → 逆势反弹，方向不明确
        S = 1 if daily_dir == "向上" else 0
    elif "中枢震荡偏弱" in position:
        # 中枢内偏弱 + 大级别向下 → 顺势看空
        # 中枢内偏弱 + 大级别向上 → 逆势回调，方向不明确
        S = -1 if daily_dir == "向下" else 0
    elif daily_dir == "向上":
        S = 1
    elif daily_dir == "向下":
        S = -1
    else:
        return None  # 无结构方向，不输出

    # 新高+顶背驰 → S=0（价格上涨到极限但力度衰减，方向不明确）
    # 新低+底背驰 → S=0（价格下跌到极限但力度衰减，方向不明确）
    # 背驰本身隐含创新高/新低（_check_level_div 强制要求）
    if div_type != "none":
        if div_dir == "top" and S == 1:
            S = 0
        elif div_dir == "bottom" and S == -1:
            S = 0

    # ═══════════════════════════════════════════
    # G: 背驰级别
    # ═══════════════════════════════════════════
    G_MAP = {"trend": "A", "consolidation": "B", "minor": "C"}
    G = G_MAP.get(div_type, "")

    # ═══════════════════════════════════════════
    # Pow: 力度 — 背驰衰减率 / 线段力度对比
    # ═══════════════════════════════════════════
    ep = div.get("entering_power", 0)
    lp = div.get("leaving_power", 0)

    if div_type != "none" and ep > 0:
        # 背驰衰减率 = (进入段-离开段) / 进入段
        Pow = round(min(1.0, (ep - lp) / ep), 3)
    else:
        # 无背驰：从 position 推断线段结构力度
        if "突破中枢" in position or "跌破中枢" in position:
            Pow = 0.70
        elif "中枢震荡偏强" in position or "中枢震荡偏弱" in position:
            Pow = 0.45
        elif "中枢震荡" in position:
            Pow = 0.30
        else:
            Pow = 0.50
    Pow = max(0.05, Pow)

    # ═══════════════════════════════════════════
    # C: 置信度 — 结构突破 + 背驰类型 + 共振
    # ═══════════════════════════════════════════
    C_BASE = {"trend": 0.80, "consolidation": 0.55, "minor": 0.30, "none": 0.40}
    C = C_BASE.get(div_type, 0.40)

    # 结构确认：走出中枢 → 方向更可信
    if "突破中枢" in position or "跌破中枢" in position:
        C = max(C, 0.65)

    # 中枢共振加成（同向共振 → +0.10）
    if (S == 1 and resonance == "看多") or (S == -1 and resonance == "看空"):
        C = min(1.0, C + 0.10)

    # 买卖点确认（方向一致的买卖点 → +0.05）
    buy_zones = {"一买区域", "二买区域", "三买区域"}
    sell_zones = {"一卖区域", "二卖区域", "三卖区域"}
    if (S == 1 and bs_zone in buy_zones) or (S == -1 and bs_zone in sell_zones):
        C = min(1.0, C + 0.05)

    C = round(C, 3)

    # ═══════════════════════════════════════════
    # 标签
    # ═══════════════════════════════════════════
    if S == 1:
        dir_label = "多"
    elif S == -1:
        dir_label = "空"
    else:
        dir_label = "中性"
    if div_type != "none":
        div_cn = "顶背驰" if div_dir == "top" else "底背驰"
    else:
        div_cn = ""
    div_label = f" {div_cn}({G}级)" if div_cn else ""
    label = f"缠论{dir_label}[{G or '-'}级]{div_label} {position}"

    ts = int(time.time())

    return StandardSignal(
        expert_id=EXPERT_ID,
        stock_code=code,
        timestamp=ts,
        signal_type=SignalType.EVENT,
        S=S,
        C=C,
        Pow=Pow,
        G=G,
        source_date=datetime.now().strftime("%Y%m%d"),
        label=label,
        raw_data={
            "position": position,
            "divergence_type": div_type,
            "divergence_direction": div_dir,
            "divergence_detail": div.get("detail", ""),
            "entering_power": ep,
            "leaving_power": lp,
            "daily_pen_direction": daily_dir,
            "min30_bs_zone": bs_zone,
            "zhongshu_resonance": resonance,
            "consistency": consistency,
        },
    )


def run():
    """终端入口"""
    print("缠论适配器 → StandardSignal")
    print("=" * 60)
    signals = adapter_chanlun()
    if not signals:
        print("无活跃背驰信号")
        return

    for sig in sorted(signals, key=lambda s: s.C, reverse=True):
        print(sig.summary())

    types = {}
    for sig in signals:
        types[sig.G] = types.get(sig.G, 0) + 1
    print(f"\n级别分布: {types}")
    print(f"共 {len(signals)} 条缠论信号（{len(NAME_MAP)} 只标的）")


if __name__ == "__main__":
    run()

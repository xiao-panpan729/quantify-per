# -*- coding: utf-8 -*-
"""
量领专家适配器 — 读取信号CSV → StandardSignal

量领专家在融合流水线中的角色：
- S（方向）：★买=1, ★卖=-1, 中性=0（事件触发，非每K线输出）
- G（级别）：A/B/C/D —— 基于MACD+EXPMA位置的ABCD分级
- C（置信）：级别越高置信越高（A=0.9, B=0.7, C=0.5, D=不输出）
- Pow（强度）：量能确认程度（百日地量/放量突破/梯度放量）

输入文件：
- signals/tracking/{code}/*_signals.csv → 47列含 buy_signal/sell_signal
- signals/tracking/_funds/volume_leader_universe.json → 量领宇宙股票列表
- signals/tracking/_funds/monitor_state.json → 已触发的告警信号记忆

输出：
- List[StandardSignal]：量领宇宙内有活跃信号的标的（仅含★买/★卖事件）
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding='utf-8')

from standard_signal import StandardSignal, SignalType, EXPERT_IDS

# --- 路径常量 ---
PROJECT_ROOT = Path(__file__).parent.parent.parent
TRACKING_DIR = PROJECT_ROOT / "signals" / "tracking"
FUNDS_DIR = TRACKING_DIR / "_funds"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXPERT_ID = "volume_leader"

# 周期 → 适配器内的简称
PERIODS = ["min5", "min15", "min30", "min60", "daily"]

# ABCD 级别 → 置信度
LEVEL_CONFIDENCE = {"A": 0.9, "B": 0.7, "C": 0.5, "D": 0.0}

# 信号窗口：只看最近 N 天内有信号的标的（防止输出过多历史信号）
SIGNAL_LOOKBACK_DAYS = 10

# 量能确认 → 强度加分
VOL_BOOST = {
    "vol_堆": 0.3,      # 地量堆 → 底部确认
    "vol_llv100": 0.2,  # 百日地量 → 抛压枯竭
    "vol_突放": 0.25,   # 放量突破 → 资金进场
    "vol_梯度升": 0.1, # 梯度放量 → 温和放量
}


def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _get_universe() -> List[str]:
    """获取量领宇宙的股票列表"""
    uni = _load_json(FUNDS_DIR / "volume_leader_universe.json")
    if uni and "universe" in uni:
        return uni["universe"]
    return []


def _get_abcd_level(close: float, expma12: float, expma50: float, macd_dif: float, macd_dea: float) -> str:
    """根据 MACD + EXPMA 状态判定 ABCD 操作级别

    A最强: EXPMA白线上方
    B次强: 白线-黄线之间
    C偏弱: 黄线下但 MACD>0
    D弱势: MACD<0（不参与）
    """
    if close > expma12:
        return "A"
    elif close > expma50:
        return "B"
    elif macd_dif > 0:
        return "C"
    else:
        return "D"


def _vol_boost(row: dict) -> float:
    """量能确认 → 强度加分"""
    boost = 0.0
    for col, val in VOL_BOOST.items():
        v = row.get(col, "")
        try:
            v = float(v) if v and str(v).strip() else 0.0
        except ValueError:
            v = 0.0
        if v > 0:
            boost = max(boost, val)  # 取最高加分，不累加
    return boost


def _parse_date(date_str: str) -> Optional[datetime]:
    """解析 YYYYMMDD 或 YYYYMMDDHHMM 日期"""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        if len(date_str) == 8:
            return datetime.strptime(date_str, "%Y%m%d")
        elif len(date_str) >= 12:
            return datetime.strptime(date_str[:12], "%Y%m%d%H%M")
    except ValueError:
        pass
    return None


def _csv_to_dicts(csv_path: Path) -> List[dict]:
    """读取信号 CSV，返回 dict 列表"""
    import csv
    if not csv_path.exists():
        return []
    rows = []
    with open(csv_path, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def adapter_volume_leader(lookback_days: int = SIGNAL_LOOKBACK_DAYS) -> List[StandardSignal]:
    """读取量领专家原生输出 → StandardSignal 列表

    策略：
    - 仅输出最近 N 天内有 ★买/★卖 信号的标的
    - 每个标的一条 StandardSignal（含方向、级别、量能确认）
    - signal_type = EVENT（量领信号是事件触发，不是逐K线滚动）
    """
    universe = _get_universe()
    if not universe:
        print("[adapter_volume_leader] 量领宇宙为空，跳过")
        return []

    cutoff = time.time() - lookback_days * 86400
    signals = []

    for code in universe:
        csv_dir = TRACKING_DIR / code
        if not csv_dir.exists():
            continue

        # 逐周期检查最近的信号
        best: Optional[StandardSignal] = None

        for period in PERIODS:
            csv_path = csv_dir / f"{period}_signals.csv"
            rows = _csv_to_dicts(csv_path)
            if not rows:
                continue

            # 从最新往前找第一条有信号的 bar
            for row in reversed(rows[-60:]):  # 最近60根K线
                buy_raw = row.get("buy_signal", "").strip()
                sell_raw = row.get("sell_signal", "").strip()

                if not buy_raw and not sell_raw:
                    continue

                # 时间过滤
                dt = _parse_date(row.get("date", ""))
                if dt and dt.timestamp() < cutoff:
                    break  # 太旧，不继续往前找

                # 数值字段
                close = float(row.get("close", 0))
                expma12 = float(row.get("expma12", 0))
                expma50 = float(row.get("expma50", 0))
                macd_dif = float(row.get("macd_dif", 0))
                macd_dea = float(row.get("macd_dea", 0))

                # ABCD 级别
                level = _get_abcd_level(close, expma12, expma50, macd_dif, macd_dea)
                if level == "D":
                    continue  # D级不交易

                # 方向
                if buy_raw:
                    S = 1
                    signal_label = "★买"
                else:
                    S = -1
                    signal_label = "★卖"

                # 置信度（级别 → 置信）
                C = LEVEL_CONFIDENCE.get(level, 0.5)

                # 强度（量能确认）
                Pow = round(0.5 + _vol_boost(row), 3)  # 基础0.5 + 量能加分

                # 时间戳
                ts = int(dt.timestamp()) if dt else int(time.time())

                # 构建信号（同标的不同周期只保留最弱的（最低置信的），最后取最有利的）
                sig = StandardSignal(
                    expert_id=EXPERT_ID,
                    stock_code=code,
                    timestamp=ts,
                    signal_type=SignalType.EVENT,
                    S=S,
                    C=C,
                    Pow=min(Pow, 1.0),
                    G=level,
                    source_date=row.get("date", "")[:8] if len(row.get("date", "")) >= 8 else row.get("date", ""),
                    label=f"量领{signal_label}({period}) G={level}",
                    raw_data={
                        "period": period,
                        "signal": signal_label,
                        "level": level,
                        "close": close,
                        "ma_chain": {
                            "expma12": expma12,
                            "expma50": expma50,
                        },
                        "vol_boost": round(_vol_boost(row), 3),
                    },
                )

                # 同标的多个周期中取最有利的：买取C最低的(保守)，卖取C最高的(积极)
                if best is None:
                    best = sig
                elif S == 1:
                    if sig.C < best.C:  # 买信号取更保守的
                        best = sig
                else:
                    if sig.C > best.C:  # 卖信号取更积极的
                        best = sig

                break  # 每个周期只取最近的一条

        if best is not None:
            signals.append(best)

    return signals


def run():
    """终端入口"""
    print("量领专家适配器 → StandardSignal")
    print("=" * 70)
    signals = adapter_volume_leader()
    if not signals:
        print("无活跃信号（无近期★买/★卖 或 全部D级）")
        return

    # 分组：买 / 卖
    buys = [s for s in signals if s.S == 1]
    sells = [s for s in signals if s.S == -1]

    print(f"\n{'='*50}")
    print(f"★买信号: {len(buys)} 条")
    print(f"{'='*50}")
    for sig in sorted(buys, key=lambda s: s.C, reverse=True)[:20]:
        print(sig.summary())

    print(f"\n{'='*50}")
    print(f"★卖信号: {len(sells)} 条")
    print(f"{'='*50}")
    for sig in sorted(sells, key=lambda s: s.C, reverse=True)[:20]:
        print(sig.summary())

    # 级别分布
    levels = {}
    for sig in signals:
        levels[sig.G] = levels.get(sig.G, 0) + 1
    print(f"\n级别分布: {levels}")
    print(f"共 {len(signals)} 条活跃信号（量领宇宙 {len(_get_universe())} 只标的）")


if __name__ == "__main__":
    run()

# -*- coding: utf-8 -*-
"""
宏观板块专家适配器 — 读取 macro_sensitivity + sector_momentum → StandardSignal

宏观板块专家在融合流水线中的角色：
- C（置信度）：环境分类（宽松/中性/收紧）→ 全局置信乘数
- Pow（强度）：板块势能（x₁ RSI势能2）→ 个股所属板块的动量强度
- S（方向）：固定 0 —— 宏观不给方向，只回答"现在该不该做"
- G（级别）：空 —— 不进级别路由

输入文件：
- signals/tracking/_macro/macro_sensitivity.json  → 宏观环境分类
- signals/tracking/_macro/sector_momentum_cache.json → 板块势能 + 个股→板块映射

输出：
- List[StandardSignal]：14只跟踪标的各一条信号，含环境C和板块Pow
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

sys.stdout.reconfigure(encoding='utf-8')

from standard_signal import StandardSignal, SignalType, EXPERT_IDS

# --- 路径常量 ---
PROJECT_ROOT = Path(__file__).parent.parent.parent
SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking" / "_macro"

# sys.path 注入项目根，以便 import config
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- 常量 ---
EXPERT_ID = "macro_sector"

# 环境分数 → 置信度 映射
# score 范围 0-3（宽松程度越高说明越适合交易）
ENV_CONFIDENCE_MAP = {
    3: 0.9,   # 极度宽松
    2: 0.7,   # 宽松
    1: 0.4,   # 中性偏紧
    0: 0.2,   # 收紧
}

# x₁ 归一化边界（从全量269板块实测得出，后续可设为自动计算）
X1_MIN = -7.55
X1_MAX = 5.64
X1_RANGE = X1_MAX - X1_MIN   # 归一化分母


def _load_json(path: Path) -> Optional[dict]:
    """加载 JSON，缺失则返回 None"""
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _normalize_x1(x1: float) -> float:
    """将原始 x₁ 值归一化到 [0, 1]，clamp 到边界内"""
    if x1 >= X1_MAX:
        return 1.0
    if x1 <= X1_MIN:
        return 0.0
    return (x1 - X1_MIN) / X1_RANGE


def _stock_code_unprefixed(stock_code: str) -> str:
    """去掉 sh/sz 前缀，得到纯6位代码用于板块查找"""
    if stock_code.startswith("sh") or stock_code.startswith("sz"):
        return stock_code[2:]
    return stock_code


def _timestamp_from_date(date_str: str, hour: int = 17) -> int:
    """将 YYYYMMDD 日期字符串转为 Unix 时间戳（默认17:00盘后）"""
    try:
        dt = datetime.strptime(date_str, "%Y%m%d").replace(hour=hour)
        return int(dt.timestamp())
    except ValueError:
        return int(time.time())


def _get_tracked_stocks() -> dict:
    """从 config.py 获取跟踪标的列表"""
    try:
        from config import NAME_MAP
        return dict(NAME_MAP)
    except ImportError:
        # 备用硬编码（和 config.py 同步）
        return {
            "sh000001": "上证指数",
            "sz399006": "创业板指",
            "sz159740": "恒生科技ETF大成",
            "sh520600": "港股通汽车ETF广发",
            "sh513120": "港股创新药ETF广发",
            "sz159326": "电网设备ETF华夏",
            "sh513310": "中韩半导体ETF",
            "sh588200": "科创芯片ETF",
            "sz002261": "拓维信息",
            "sz300118": "东方日升",
            "sz000100": "TCL科技",
            "sz002129": "TCL中环",
            "sh600438": "通威股份",
            "sh601012": "隆基绿能",
        }


def adapter_macro_sector() -> List[StandardSignal]:
    """读取宏观板块专家原生输出 → 翻译为 StandardSignal 列表。

    每只跟踪标的产出一条信号，含：
    - C: 宏观环境置信度
    - Pow: 所属板块势能强度（无板块映射则为0）
    - S: 固定0（不给方向）
    """
    # 1. 读取输入
    macro_data = _load_json(SIGNALS_DIR / "macro_sensitivity.json")
    sector_data = _load_json(SIGNALS_DIR / "sector_momentum_cache.json")

    if macro_data is None:
        print("[adapter_macro_sector] macro_sensitivity.json 缺失，跳过")
        return []

    # 2. 提取环境置信度
    env = macro_data["environment"]
    env_score = env.get("score", 1)
    env_label = env.get("environment", "未知")
    env_c = ENV_CONFIDENCE_MAP.get(env_score, 0.5)

    # 时间戳：优先用板块缓存日期，其次用宏观更新时间
    date_str = ""
    if sector_data and sector_data.get("date"):
        date_str = sector_data["date"]
    elif macro_data.get("update_time"):
        date_str = macro_data["update_time"].split(" ")[0].replace("-", "")
    ts = _timestamp_from_date(date_str, hour=17) if date_str else int(time.time())

    # 3. 板块势能数据
    sector_scores = sector_data.get("sector_scores", {}) if sector_data else {}
    stock_sectors = sector_data.get("stock_sectors", {}) if sector_data else {}

    # 4. 为每只跟踪标的生产 StandardSignal
    signals = []
    stocks = _get_tracked_stocks()

    for stock_code, stock_name in stocks.items():
        # 板块势能 Pow：查个股所属板块的 x₁ 均值
        unprefixed = _stock_code_unprefixed(stock_code)
        sector_list = stock_sectors.get(unprefixed, [])
        if sector_list:
            x1s = [sector_scores[s]["x1"] for s in sector_list if s in sector_scores]
            avg_x1 = sum(x1s) / len(x1s) if x1s else 0.0
            pow_val = round(_normalize_x1(avg_x1), 3)
            top_sectors = sorted(
                [s for s in sector_list if s in sector_scores],
                key=lambda s: sector_scores[s]["x1"], reverse=True
            )[:3]
        else:
            avg_x1 = 0.0
            pow_val = 0.0
            top_sectors = []

        signal = StandardSignal(
            expert_id=EXPERT_ID,
            stock_code=stock_code,
            timestamp=ts,
            signal_type=SignalType.CYCLE,
            S=0,
            C=env_c,
            Pow=pow_val,
            G="",
            source_date=sector_data.get("date", "") if sector_data else "",
            label=f"宏观{env_label}(score={env_score}) 板块x1={avg_x1:.2f}+",
            raw_data={
                "environment": env_label,
                "env_score": env_score,
                "env_factors": env.get("details", {}),
                "avg_sector_x1": round(avg_x1, 2),
                "top_sectors": top_sectors,
                "sector_count": len(sector_list),
            },
        )
        signals.append(signal)

    return signals


def run():
    """终端入口：跑适配器 → 打印摘要"""
    print(f"宏观板块专家适配器 → StandardSignal")
    print(f"{'='*70}")
    signals = adapter_macro_sector()
    if not signals:
        print("无信号输出")
        return

    # 按 Pow 排序展示
    signals.sort(key=lambda s: s.Pow, reverse=True)
    for sig in signals:
        print(sig.summary())

    # 统计
    with_sector = sum(1 for s in signals if s.Pow > 0)
    print(f"\n共 {len(signals)} 条信号，{with_sector} 只有板块映射")
    print(f"环境置信度: {signals[0].C:.2f} ({signals[0].raw_data['environment']})")


if __name__ == "__main__":
    run()

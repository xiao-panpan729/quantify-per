"""
缠论结构分析适配层 — 笔记本系统的结构定位层
基于 czsc (waditu/czsc) 实现分型→笔→中枢→买卖点识别。

★ 日常使用:
    from notebook.chanlun import get_position
    result = get_position(code, df_daily, df_30min)
    # → {"position": "日线上涨笔延续", "daily_pen_direction": "向上", ...}

★ 深度分析:
    from notebook.chanlun import full_analysis
    from notebook.chanlun import get_bs_points, get_pattern_signals
"""

# 主入口 — 笔记本预测卡的标准调用
from notebook.chanlun.positions import get_position, dual_level_analysis

# 数据适配
from notebook.chanlun.adapter import df_to_bars

# 6大类信号函数
from notebook.chanlun.signals import (
    # 结构层
    get_structure_info,
    get_zs_info,
    get_fx_info,
    # 买卖点
    get_bs_points,
    # 形态层
    get_pattern_signals,
    # 笔状态
    get_pen_status,
    # 决策层 + 支撑压力
    get_decision_signals,
    # 统一入口
    full_analysis,
)

__all__ = [
    # 主入口
    "get_position",
    "dual_level_analysis",
    # 数据适配
    "df_to_bars",
    # 6大类
    "get_structure_info",
    "get_zs_info",
    "get_fx_info",
    "get_bs_points",
    "get_pattern_signals",
    "get_pen_status",
    "get_decision_signals",
    "full_analysis",
]

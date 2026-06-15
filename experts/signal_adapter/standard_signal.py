# -*- coding: utf-8 -*-
"""
全局信号结构体 StandardSignal — 所有专家输出统一为此格式。

这是专家系统融合框架的"通用语言"：
- 每个专家的适配器负责将原生输出翻译为 StandardSignal
- 融合引擎读取 StandardSignal 列表，统一处理异构信号
- 不改动专家内部逻辑，只在出口处翻译
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class SignalType(str, Enum):
    """信号类型：决定生命周期管理方式"""
    CYCLE = "cycle"        # 周期滚动：每根K线刷新（signal_engine、宏观等）
    EVENT = "event"        # 事件触发：触发后有效N根K线（★买/★卖、缠论买卖点）
    REPORT = "report"      # 不定期：当日有效，跨日重置（研报、节点标注）


# 专家ID枚举 — 每个专家有唯一ID，邻接矩阵用此索引
EXPERT_IDS = {
    "ta_chart": "技术分析-量价",
    "ta_chanlun": "技术分析-缠论",
    "macro_sector": "宏观板块",
    "fundamental": "基本面",
    "info_agg": "信息聚合",
    "volume_leader": "量领",
    "potential_screener": "势能选股",
    "narrative": "叙事",
    "risk_control": "风控",
    "research_node": "研报/节点",
}


@dataclass
class StandardSignal:
    """全局统一信号结构体

    所有专家的对外输出统一为此结构体，由适配器层翻译。
    字段规则：
    - 必填：expert_id, stock_code, timestamp, signal_type, raw_data
    - 可选：S/C/Pow/G，无对应维度填 0 或空字符串
    - 时间轴统一为时间戳(int)，解决周期/事件/研报的时间对齐
    """

    expert_id: str          # 专家唯一ID（EXPERT_IDS 中的 key）
    stock_code: str         # 标的代码（如 sh600438、sz159740）
    timestamp: int          # Unix 时间戳（统一时间轴核心）
    signal_type: SignalType # cycle / event / report

    # 可选维度 — 无对应维度填 0/空
    S: int = 0              # 方向：-1(看空) / 0(中性) / 1(看多)
    C: float = 0.0          # 置信度：0.0 ~ 1.0
    Pow: float = 0.0        # 强度：0.0 ~ 1.0
    G: str = ""             # 操作级别：A/B/C/D（无级别属性留空）

    # 溯源
    raw_data: dict = field(default_factory=dict)  # 原生原始数据，用于调试/溯源

    # 元信息
    source_date: str = ""   # 数据来源日期（YYYYMMDD，用于过滤/诊断，非融合计算）
    label: str = ""         # 人类可读标签，用于报告（非计算字段）

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signal_type"] = self.signal_type.value
        return d

    def summary(self) -> str:
        """一行摘要，用于终端调试"""
        s = "+" if self.S == 1 else ("-" if self.S == -1 else "0")
        return (f"[{self.expert_id:20}] {self.stock_code:10} "
                f"S={s} C={self.C:.2f} Pow={self.Pow:.2f} G={self.G or '-':1} "
                f"({self.signal_type.value}) {self.label}")

    # --- 空值/缺失信号判定 ---
    @property
    def is_empty(self) -> bool:
        """完全空信号（S==0 且 Pow==0 且 C==0），融合时通常跳过"""
        return self.S == 0 and self.Pow == 0.0 and self.C == 0.0

    @property
    def has_direction(self) -> bool:
        """是否有方向信号（S != 0）"""
        return self.S != 0

    @property
    def has_level(self) -> bool:
        """是否有操作级别"""
        return bool(self.G)

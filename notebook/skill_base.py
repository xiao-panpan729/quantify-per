# -*- coding: utf-8 -*-
"""
统一技能契约 — BaseSkill ABC

每个技能 = 一个 Python 类，遵守统一接口：
  check(data_map) → [SkillResult]   触发条件扫描
  get_criteria()  → [dict]          验收标准模板
  verify(result, future_data) → dict  默认验证逻辑（可覆写）

参考：Vibe-Trading strategy-generate 的 SignalEngine 模式 +
      filter_engine 的纯函数风格
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SkillResult:
    """技能的单个触发结果 — check() 的返回单元"""
    skill_name: str
    code: str
    trigger_date: str              # YYYY-MM-DD
    conditions: dict               # 触发时的具体数值快照
    criteria: list[dict]           # 验收标准 [{"metric": "close_5d_return", "operator": ">", "threshold": 0}]
    confidence: float = 1.0        # 0~1，基于条件匹配度

    # 由 prediction_card.create_card() 填入
    card_id: str = ""


class BaseSkill(ABC):
    """技能基类 — 所有技能必须继承并实现 check() 和 get_criteria()

    子类需要设置的类属性:
        name: str           技能唯一标识
        description: str    一句话描述
        verify_days: int    验证周期（交易日数，默认5）
        periods_needed: list 需要的数据周期 ["daily"] / ["daily", "min30"] 等
    """

    name: str = ""
    description: str = ""
    verify_days: int = 5
    periods_needed: list = field(default_factory=lambda: ["daily"])

    @abstractmethod
    def check(self, data_map: dict) -> list[SkillResult]:
        """扫描所有标的，返回触发信号列表

        Args:
            data_map: {code: {period: [row_dict, ...]}}
                      每个 row_dict 是 CSV 行转换的 dict（按时间升序）

        Returns:
            [SkillResult, ...] 每项是一个触发信号
        """

    @abstractmethod
    def get_criteria(self) -> list[dict]:
        """返回验收标准模板

        格式: [{"metric": "close_Nd_return", "operator": ">", "threshold": 0}]

        支持的 metric:
            - close_Nd_return: N 日后收盘相对触发日收盘的涨跌幅（如 close_5d_return）
            - max_drawdown_Nd: N 日内最大回撤
            - close_above_expma50_Nd: N 日后收盘 > expma50
        """

    def verify(self, result: SkillResult, future_data: dict) -> dict:
        """默认验证逻辑：检查 future_data 中每项 criteria

        Args:
            result: 要验证的触发结果
            future_data: {period: [row_dict, ...]} 信号日之后的数据

        Returns:
            {"all_criteria_met": bool, "actual_return_Nd": float, "max_drawdown": float, ...}
        """
        daily = future_data.get("daily", [])
        if not daily:
            return {"all_criteria_met": False, "error": "无未来数据"}

        signal_close = result.conditions.get("close", 0)
        actual = {}
        all_met = True

        for c in result.criteria:
            metric = c["metric"]
            op = c.get("operator", ">")
            threshold = c.get("threshold", 0)

            if metric.startswith("close_") and metric.endswith("d_return"):
                # close_5d_return → 第5根 bar 的收盘涨跌幅
                n = int(metric.replace("close_", "").replace("d_return", ""))
                if n <= len(daily):
                    future_close = daily[n - 1].get("close", 0)
                    ret = (future_close - signal_close) / signal_close if signal_close else 0
                    actual[metric] = round(ret, 4)
                    if op == ">" and not (ret > threshold):
                        all_met = False
                    elif op == "<" and not (ret < threshold):
                        all_met = False
                else:
                    actual[metric] = None

            elif metric.startswith("max_drawdown_") and metric.endswith("d"):
                n = int(metric.replace("max_drawdown_", "").replace("d", ""))
                n = min(n, len(daily))
                lows = [r.get("close", signal_close) for r in daily[:n]]
                peak = signal_close
                max_dd = 0.0
                for c_val in lows:
                    dd = (peak - c_val) / peak if peak else 0
                    max_dd = max(max_dd, dd)
                    peak = max(peak, c_val)
                actual[metric] = round(max_dd, 4)
                if op == "<" and not (max_dd < threshold):
                    all_met = False

            elif metric.startswith("close_above_expma50_") and metric.endswith("d"):
                n = int(metric.replace("close_above_expma50_", "").replace("d", ""))
                if n <= len(daily):
                    future_close = daily[n - 1].get("close", 0)
                    future_expma50 = daily[n - 1].get("expma50", 0)
                    above = future_close > future_expma50 if future_expma50 else False
                    actual[metric] = above
                    if op == ">" and not above:
                        all_met = False

        actual["all_criteria_met"] = all_met
        return actual

    def verify_from_conditions(self, conditions: dict, criteria: list[dict],
                                future_data: dict) -> dict:
        """从条件快照直接验证 — 供 verify_engine 使用

        与 verify() 逻辑相同，但不依赖 SkillResult 对象。
        """
        sr = SkillResult(
            skill_name=self.name,
            code="",
            trigger_date="",
            conditions=conditions,
            criteria=criteria,
        )
        return self.verify(sr, future_data)

    @staticmethod
    def find_daily_close(daily_rows: list[dict], date_str: str) -> float:
        """在日线数据中查找对应日期的收盘价"""
        date_str = str(date_str).strip()
        for r in daily_rows:
            if str(r.get("date", "")).strip() == date_str:
                return r.get("close", 0) or 0
        # fallback: 最近的日线收盘
        if daily_rows:
            return daily_rows[-1].get("close", 0) or 0
        return 0


def load_skill_registry() -> dict:
    """手动维护的技能注册表 → {name: class}"""
    from notebook.skills.entry_ma import EntryMA
    from notebook.skills.entry_jincha import EntryJincha
    from notebook.skills.entry_resonance import EntryResonance
    from notebook.skills.exit_sell_t import ExitSellT
    from notebook.skills.exit_sell_reduce import ExitSellReduce
    return {
        "entry_ma": EntryMA,
        "entry_jincha": EntryJincha,
        "entry_resonance": EntryResonance,
        "exit_sell_t": ExitSellT,
        "exit_sell_reduce": ExitSellReduce,
    }

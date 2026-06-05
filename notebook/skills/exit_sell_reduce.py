# -*- coding: utf-8 -*-
"""
技能: 减仓卖出（通知级，可行动）

触发条件（与 monitor.py 精确对齐）:
  1. 5分钟 ★卖 信号
  2. ★卖 bar 的 close < MA5
  3. 近20根无金叉
  4. 15分钟 close < expma50（黄线下方）

验证标准: 5个交易日后日线收盘 < 信号日日线收盘（减仓正确 = 价格继续跌）
"""

from tools.volume_leader.filter_engine import (
    has_star_sell, check_close_below_ma, check_no_recent_golden,
)
from notebook.skill_base import BaseSkill, SkillResult


class ExitSellReduce(BaseSkill):
    name = "exit_sell_reduce"
    description = "减仓卖出: 5分★卖+close<MA5+无金叉+15分黄线下 → 5日价格下跌(卖对)"
    verify_days = 5
    periods_needed = ["min5", "min15", "daily"]

    def check(self, data_map: dict) -> list[SkillResult]:
        results = []

        for code, periods in data_map.items():
            min5 = periods.get("min5", [])
            min15 = periods.get("min15", [])
            daily = periods.get("daily", [])
            if len(min5) < 20 or not daily:
                continue

            min15_ok = False
            if min15:
                last15 = min15[-1]
                c15 = last15.get("close", 0) or 0
                e50_15 = last15.get("expma50", 0) or 0
                min15_ok = c15 < e50_15 if e50_15 > 0 else False

            for i in range(20, len(min5)):
                bar = min5[i]

                if not has_star_sell(bar):
                    continue
                if not check_close_below_ma(bar):
                    continue
                if not check_no_recent_golden(min5, i, 20):
                    continue
                if not min15_ok:
                    continue

                prev = min5[i - 1]
                if (has_star_sell(prev) and check_close_below_ma(prev)):
                    continue

                date_str = str(bar.get("date", ""))
                daily_close = self.find_daily_close(daily, date_str)

                conditions = {
                    "close": daily_close,
                    "ma5": bar.get("ma5", 0),
                    "cci": bar.get("cci", 0),
                }

                results.append(SkillResult(
                    skill_name=self.name,
                    code=code,
                    trigger_date=date_str,
                    conditions=conditions,
                    criteria=self.get_criteria(),
                    confidence=0.8,
                ))

        return results

    def get_criteria(self) -> list[dict]:
        return [
            {"metric": "close_5d_return", "operator": "<", "threshold": 0},
        ]

# -*- coding: utf-8 -*-
"""
技能: 做T卖出（日志级，非强制）

触发条件（与 monitor.py 精确对齐）:
  1. 5分钟 CCI顶背驰
  2. close > expma50（黄线上方）
  3. 窗口内该CCI顶背驰唯一（非连续/密集出现）

验证标准: 3个交易日后日线收盘 < 信号日日线收盘（卖对了 = 价格下跌）
"""

from tools.volume_leader.filter_engine import has_cci_top_divergence, check_close_above_ma
from notebook.skill_base import BaseSkill, SkillResult


class ExitSellT(BaseSkill):
    name = "exit_sell_t"
    description = "做T卖出: 5分CCI顶背驰+黄线上+窗口唯一 → 3日价格下跌(卖对)"
    verify_days = 3
    periods_needed = ["min5", "daily"]

    def check(self, data_map: dict) -> list[SkillResult]:
        results = []

        for code, periods in data_map.items():
            min5 = periods.get("min5", [])
            daily = periods.get("daily", [])
            if len(min5) < 20 or not daily:
                continue

            for i in range(20, len(min5)):
                bar = min5[i]

                if not has_cci_top_divergence(bar):
                    continue
                if not check_close_above_ma(bar, "expma50"):
                    continue

                cci_count = 1
                for j in range(i - 1, max(i - 30, 0), -1):
                    if has_cci_top_divergence(min5[j]):
                        cci_count += 1
                    else:
                        break
                if cci_count > 1:
                    continue

                prev = min5[i - 1]
                if (has_cci_top_divergence(prev)
                        and check_close_above_ma(prev, "expma50")):
                    continue

                date_str = str(bar.get("date", ""))
                daily_close = self.find_daily_close(daily, date_str)

                conditions = {
                    "close": daily_close,
                    "cci": bar.get("cci", 0),
                    "expma50": bar.get("expma50", 0),
                }

                results.append(SkillResult(
                    skill_name=self.name,
                    code=code,
                    trigger_date=date_str,
                    conditions=conditions,
                    criteria=self.get_criteria(),
                    confidence=0.7,
                ))

        return results

    def get_criteria(self) -> list[dict]:
        return [
            {"metric": "close_3d_return", "operator": "<", "threshold": 0},
        ]

# -*- coding: utf-8 -*-
"""
技能: 金叉级入场（买）

触发条件（MA级全部 + 5分钟EXPMA金叉）:
  1-6: 同 MA级全部条件
  7: expma12 > expma50（5分钟EXPMA金叉状态）

验证标准: 5个交易日后日线收盘 > 信号日日线收盘
"""

from tools.volume_leader.filter_engine import (
    has_star_buy, check_ma_chain, check_no_recent_death,
    check_expma_golden, check_pe_gate,
)
from notebook.skill_base import BaseSkill, SkillResult


class EntryJincha(BaseSkill):
    name = "entry_jincha"
    description = "金叉级入场: MA级全部 + 5分EXPMA金叉 → 5日涨跌"
    verify_days = 5
    periods_needed = ["min5", "min60", "daily"]

    def check(self, data_map: dict) -> list[SkillResult]:
        results = []

        for code, periods in data_map.items():
            min5 = periods.get("min5", [])
            min60 = periods.get("min60", [])
            daily = periods.get("daily", [])
            if len(min5) < 20 or not daily:
                continue

            min60_ok = False
            if min60:
                last60 = min60[-1]
                min60_ok = (last60.get("close", 0) or 0) > (last60.get("expma50", 0) or 0)

            last_d = daily[-1]
            daily_ok = (last_d.get("close", 0) or 0) > (last_d.get("expma50", 0) or 0)
            daily_pe_ok = check_pe_gate(last_d)

            for i in range(20, len(min5)):
                bar = min5[i]

                if not has_star_buy(bar):
                    continue
                if not check_ma_chain(bar):
                    continue
                if not check_no_recent_death(min5, i, 20):
                    continue
                if not min60_ok:
                    continue
                if not daily_pe_ok:
                    continue
                if not daily_ok:
                    continue
                if not check_expma_golden(bar):
                    continue

                prev = min5[i - 1]
                if (has_star_buy(prev) and check_ma_chain(prev)
                        and check_expma_golden(prev)):
                    continue

                date_str = str(bar.get("date", ""))
                daily_close = self.find_daily_close(daily, date_str)

                conditions = {
                    "close": daily_close,
                    "ma5": bar.get("ma5", 0),
                    "ma10": bar.get("ma10", 0),
                    "ma20": bar.get("ma20", 0),
                    "is_jincha": True,
                    "cci": bar.get("cci", 0),
                }

                results.append(SkillResult(
                    skill_name=self.name,
                    code=code,
                    trigger_date=date_str,
                    conditions=conditions,
                    criteria=self.get_criteria(),
                    confidence=0.85,
                ))

        return results

    def get_criteria(self) -> list[dict]:
        return [
            {"metric": "close_5d_return", "operator": ">", "threshold": 0},
        ]

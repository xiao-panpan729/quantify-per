# -*- coding: utf-8 -*-
"""
技能: 共振级入场（买完）

触发条件（金叉级全部 + 多周期级联共振）:
  1-7: 同 金叉级全部条件
  8: 15分钟 + 30分钟金叉共振

验证标准: 10个交易日后日线收盘 > 信号日日线收盘
"""

from tools.volume_leader.filter_engine import (
    has_star_buy, check_ma_chain, check_no_recent_death,
    check_expma_golden, check_pe_gate,
)
from notebook.skill_base import BaseSkill, SkillResult


class EntryResonance(BaseSkill):
    name = "entry_resonance"
    description = "共振级入场: 金叉级全部 + 15/30分金叉共振 → 10日涨跌"
    verify_days = 10
    periods_needed = ["min5", "min15", "min30", "min60", "daily"]

    def check(self, data_map: dict) -> list[SkillResult]:
        results = []

        for code, periods in data_map.items():
            min5 = periods.get("min5", [])
            min15 = periods.get("min15", [])
            min30 = periods.get("min30", [])
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

            resonance_ok = self._check_resonance(min15, min30)

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
                if not resonance_ok:
                    continue

                prev = min5[i - 1]
                if (has_star_buy(prev) and check_ma_chain(prev)
                        and check_expma_golden(prev)):
                    continue

                date_str = str(bar.get("date", ""))
                daily_close = self.find_daily_close(daily, date_str)

                conditions = {
                    "close": daily_close,
                    "is_jincha": True,
                    "is_resonance": True,
                    "cci": bar.get("cci", 0),
                }

                results.append(SkillResult(
                    skill_name=self.name,
                    code=code,
                    trigger_date=date_str,
                    conditions=conditions,
                    criteria=self.get_criteria(),
                    confidence=0.95,
                ))

        return results

    def _check_resonance(self, min15, min30) -> bool:
        m15_ok = False
        if min15:
            last15 = min15[-1]
            m15_ok = (last15.get("expma12", 0) or 0) > (last15.get("expma50", 0) or 0)
        m30_ok = False
        if min30:
            last30 = min30[-1]
            m30_ok = (last30.get("expma12", 0) or 0) > (last30.get("expma50", 0) or 0)
        return m15_ok and m30_ok

    def get_criteria(self) -> list[dict]:
        return [
            {"metric": "close_10d_return", "operator": ">", "threshold": 0},
        ]

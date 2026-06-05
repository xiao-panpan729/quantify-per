# -*- coding: utf-8 -*-
"""
技能: MA级入场（试错）

触发条件（与 monitor.py 精确对齐）:
  1. 5分钟 ★买 信号
  2. MA链: ma5 > ma10 > ma20
  3. 近20根无死叉
  4. 60分钟 close > expma50（黄线上方）
  5. 日线 PE非升熵: pe_chg_5 >= -0.02
  6. 日线 close > expma50（黄线上方）

验证标准: 5个交易日后日线收盘 > 信号日日线收盘
"""

from tools.volume_leader.filter_engine import (
    has_star_buy, check_ma_chain, check_no_recent_death,
    check_pe_gate,
)
from notebook.skill_base import BaseSkill, SkillResult


class EntryMA(BaseSkill):
    name = "entry_ma"
    description = "MA级入场: 5分★买+MA链+无死叉+60分/日线双黄线+PE门禁 → 5日涨跌"
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

                prev = min5[i - 1]
                if (has_star_buy(prev) and check_ma_chain(prev)):
                    continue

                date_str = str(bar.get("date", ""))
                daily_close = self.find_daily_close(daily, date_str)
                is_jincha = (bar.get("expma12", 0) or 0) > (bar.get("expma50", 0) or 0)

                conditions = {
                    "close": daily_close,
                    "ma5": bar.get("ma5", 0),
                    "ma10": bar.get("ma10", 0),
                    "ma20": bar.get("ma20", 0),
                    "is_jincha": is_jincha,
                    "cci": bar.get("cci", 0),
                }
                confidence = 0.5 + (0.3 if is_jincha else 0)

                results.append(SkillResult(
                    skill_name=self.name,
                    code=code,
                    trigger_date=date_str,
                    conditions=conditions,
                    criteria=self.get_criteria(),
                    confidence=round(confidence, 2),
                ))

        return results

    def get_criteria(self) -> list[dict]:
        return [
            {"metric": "close_5d_return", "operator": ">", "threshold": 0},
        ]

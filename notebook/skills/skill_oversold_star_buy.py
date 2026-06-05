# -*- coding: utf-8 -*-
"""
技能 #1: 超跌★买确认

触发条件: 近20根内出现过CCI<-200（极端超卖） + 当前★买信号（反弹确认）
验证标准: 5个交易日后收盘 > 信号日收盘

注意：CCI极端值和★买不在同一bar出现（CCI极值在前，★买是反弹后的确认信号）。
      因此用时间窗口（lookback=20）捕获"近期超跌 + 现在确认"的模式。
"""

from notebook.skill_base import BaseSkill, SkillResult


class SkillOversoldStarBuy(BaseSkill):
    name = "oversold_star_buy"
    description = "近20根CCI<-200 + 当前★买 → 5日后收盘>信号日收盘"
    verify_days = 5
    periods_needed = ["daily"]

    def check(self, data_map: dict) -> list[SkillResult]:
        results = []

        for code, periods in data_map.items():
            daily = periods.get("daily", [])
            if len(daily) < 20:
                continue

            for i in range(20, len(daily)):
                row = daily[i]
                buy_signal = str(row.get("buy_signal", "")).strip()
                if buy_signal != "★买":
                    continue

                # 近20根内（不含当前）出现过 CCI < -200
                lookback = daily[i - 19:i]
                cci_min = min(
                    (r.get("cci", 0) or 0 for r in lookback),
                    default=0
                )
                if cci_min >= -200:
                    continue

                # 找到最近一次CCI<-200的距离
                cci_extreme_dist = 0
                for j in range(i - 1, max(i - 20, 0), -1):
                    cci_j = daily[j].get("cci", 0) or 0
                    if isinstance(cci_j, (int, float)) and cci_j < -200:
                        cci_extreme_dist = i - j
                        break

                # 避免重复触发：如果上一根也触发了，跳过
                prev_row = daily[i - 1]
                prev_buy = str(prev_row.get("buy_signal", "")).strip()
                if prev_buy == "★买":
                    prev_lookback = daily[i - 20:i - 1]
                    prev_cci_min = min(
                        (r.get("cci", 0) or 0 for r in prev_lookback),
                        default=0
                    )
                    if prev_cci_min < -200:
                        continue

                date_str = str(row.get("date", ""))
                cci_current = row.get("cci", 0) or 0
                conditions = {
                    "cci_current": cci_current,
                    "cci_min_20bar": cci_min,
                    "cci_extreme_dist": cci_extreme_dist,
                    "close": row.get("close", 0),
                    "expma_cross": row.get("expma_cross", ""),
                    "vol_llv100": bool(row.get("vol_llv100", 0)),
                    "macd_hist": row.get("macd_hist", 0) or 0,
                }

                # 置信度：CCI极端值越低 + 距离越近 → 越确信
                confidence = min(1.0,
                    0.5 + abs(cci_min + 200) / 200 * 0.3 + (1 - cci_extreme_dist / 20) * 0.2
                )

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

# -*- coding: utf-8 -*-
"""
反馈分析 — 从案例库统计中提取改进建议

- skill_hit_rate(): 命中率趋势分析
- compare_skills(): 多技能横向对比
- suggest_threshold_adjustment(): 条件阈值优化建议
"""

from notebook.case_store import get_all_cases, get_skill_stats


def skill_hit_rate(skill_name: str) -> dict:
    """命中率趋势分析

    Returns:
        {"overall": {"total": N, "hit_rate": 0.XX},
         "by_code": {"sh600438": {"total": N, "hit_rate": 0.XX}, ...},
         "by_month": {"2026-01": {"total": N, "hit_rate": 0.XX}, ...}}
    """
    cases = get_all_cases(skill_name=skill_name)

    if not cases:
        return {"overall": {"total": 0, "hit_rate": 0}, "by_code": {}, "by_month": {}}

    # 总体统计
    total = len(cases)
    correct = sum(1 for c in cases if c.get("all_criteria_met"))
    overall = {
        "total": total,
        "correct": correct,
        "hit_rate": round(correct / total, 3) if total > 0 else 0,
    }

    # 按标的分解
    by_code = {}
    for c in cases:
        code = c["code"]
        if code not in by_code:
            by_code[code] = {"total": 0, "correct": 0}
        by_code[code]["total"] += 1
        if c.get("all_criteria_met"):
            by_code[code]["correct"] += 1
    for code, stats in by_code.items():
        stats["hit_rate"] = round(stats["correct"] / stats["total"], 3) if stats["total"] > 0 else 0

    # 按月份分解
    by_month = {}
    for c in cases:
        month = c["signal_date"][:7] if c.get("signal_date") else "unknown"
        if month not in by_month:
            by_month[month] = {"total": 0, "correct": 0}
        by_month[month]["total"] += 1
        if c.get("all_criteria_met"):
            by_month[month]["correct"] += 1
    for month, stats in by_month.items():
        stats["hit_rate"] = round(stats["correct"] / stats["total"], 3) if stats["total"] > 0 else 0

    return {
        "overall": overall,
        "by_code": by_code,
        "by_month": dict(sorted(by_month.items())),
    }


def compare_skills(skill_names: list[str]) -> dict:
    """多技能横向对比

    Returns:
        {"oversold_star_buy": {"total": 50, "hit_rate": 0.70}, ...}
    """
    result = {}
    for name in skill_names:
        stats = get_skill_stats(name)
        result[name] = stats
    return result


def suggest_threshold_adjustment(skill_name: str) -> list[dict]:
    """条件阈值优化建议 — 基于历史案例的条件分布

    当前阶段：返回各条件的成功/失败均值对比，供人工判断。

    Returns:
        [{"condition": "star_buy_count",
          "correct_avg": 2.8, "wrong_avg": 2.1,
          "suggestion": "可考虑提高阈值到3"}, ...]
    """
    cases = get_all_cases(skill_name=skill_name)
    if len(cases) < 10:
        return []

    # 分组：正确的 vs 错误的
    correct_cases = [c for c in cases if c.get("all_criteria_met")]
    wrong_cases = [c for c in cases if not c.get("all_criteria_met")]

    if not correct_cases or not wrong_cases:
        return []

    # 对每个可能的数值条件进行分析
    suggestions = []

    # 从第一个案例的 conditions_json 中提取条件键
    try:
        import json
        sample = json.loads(cases[0].get("conditions_json", "{}"))
    except (json.JSONDecodeError, TypeError):
        return []

    for key in sample:
        correct_vals = []
        wrong_vals = []

        for c in correct_cases:
            try:
                cond = json.loads(c.get("conditions_json", "{}"))
                v = cond.get(key)
                if isinstance(v, (int, float)):
                    correct_vals.append(v)
            except (json.JSONDecodeError, TypeError):
                pass

        for c in wrong_cases:
            try:
                cond = json.loads(c.get("conditions_json", "{}"))
                v = cond.get(key)
                if isinstance(v, (int, float)):
                    wrong_vals.append(v)
            except (json.JSONDecodeError, TypeError):
                pass

        if len(correct_vals) < 3 or len(wrong_vals) < 3:
            continue

        c_avg = sum(correct_vals) / len(correct_vals)
        w_avg = sum(wrong_vals) / len(wrong_vals)

        suggestion = None
        if c_avg > w_avg * 1.2:
            suggestion = f"成功案例的{key}均值({c_avg:.1f}) > 失败均值({w_avg:.1f})，可考虑提高阈值"
        elif w_avg > c_avg * 1.2:
            suggestion = f"失败案例的{key}均值({w_avg:.1f}) > 成功均值({c_avg:.1f})，可考虑降低阈值"

        if suggestion:
            suggestions.append({
                "condition": key,
                "correct_avg": round(c_avg, 2),
                "wrong_avg": round(w_avg, 2),
                "suggestion": suggestion,
            })

    return suggestions

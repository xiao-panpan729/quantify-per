# -*- coding: utf-8 -*-
"""
自动验证引擎 — 到期后读取未来数据判定预测对错

- verify_card(): 单卡验证
- verify_all_pending(): 批量验证所有待验证卡片
- batch_backtest(): 批量历史回测（对历史日期范围运行技能 + 自动验证）
"""

from datetime import datetime, timedelta

from notebook.shared import load_signal_csv
from notebook.prediction_card import PredictionCard, load_card, move_to_verified, list_pending
from notebook.case_store import insert_case


def verify_card(card: PredictionCard) -> PredictionCard:
    """验证单张预测卡 — 读信号日之后的未来数据，逐条检查 criteria

    Args:
        card: 待验证的预测卡

    Returns:
        更新后的卡（status + result 已填入）
    """
    # 加载未来数据：从信号日之后开始
    all_rows = load_signal_csv(card.code, "daily")
    if not all_rows:
        card.status = "expired"
        card.result = {"error": "无法加载日线数据"}
        return card

    # 找到信号日之后的未来数据
    future_idx = None
    for i, r in enumerate(all_rows):
        if str(r.get("date", "")) == str(card.created_date):
            future_idx = i + 1
            break

    if future_idx is None:
        # 尝试：信号日可能在非交易日，取 >= 信号日的第一个 bar
        for i, r in enumerate(all_rows):
            if str(r.get("date", "")) >= str(card.created_date):
                future_idx = i + 1
                break

    if future_idx is None or future_idx >= len(all_rows):
        card.status = "expired"
        card.result = {"error": "信号日无对应数据或无后续数据"}
        return card

    future_rows = all_rows[future_idx:]

    # 如果未来数据不够 verify_days，标记为 expired
    max_days = max(
        _extract_days(c) for c in card.criteria
    ) if card.criteria else 5
    if len(future_rows) < max_days:
        card.status = "expired"
        card.result = {"error": f"未来数据不足（需要{max_days}根，实际{len(future_rows)}根）"}
        return card

    # 调用技能验证
    from notebook.skill_base import load_skill_registry
    registry = load_skill_registry()
    skill_cls = registry.get(card.skill_name)

    if skill_cls:
        skill = skill_cls()
        # 构造未来数据 map
        future_map = {"daily": future_rows}
        result = skill.verify_from_conditions(card.conditions, card.criteria, future_map)
    else:
        result = _default_verify(card.conditions, card.criteria, future_rows)

    card.result = result
    card.verified_date = datetime.now().strftime("%Y-%m-%d")

    if result.get("all_criteria_met"):
        card.status = "verified_correct"
    else:
        card.status = "verified_wrong"

    # 入库 + 移动卡片
    move_to_verified(card)
    insert_case(card)

    return card


def verify_all_pending() -> dict:
    """批量验证所有待验证卡片

    Returns:
        {"verified": 12, "correct": 8, "wrong": 4, "expired": 0}
    """
    pending_ids = list_pending()
    stats = {"verified": 0, "correct": 0, "wrong": 0, "expired": 0}

    for cid in pending_ids:
        card = load_card(cid)
        if card is None:
            continue
        card = verify_card(card)
        stats["verified"] += 1
        if card.status == "verified_correct":
            stats["correct"] += 1
        elif card.status == "verified_wrong":
            stats["wrong"] += 1
        else:
            stats["expired"] += 1

    return stats


def batch_backtest(skill_instance, codes: list[str], date_range: tuple | None = None,
                   period: str = "daily") -> dict:
    """批量历史回测：对 date_range 内每天运行 skill.check() → 用未来数据验证

    Args:
        skill_instance: 技能实例（BaseSkill 子类）
        codes: 标的代码列表
        date_range: (start_date, end_date) 或 None（自动用全部数据）
        period: 验证用的周期标识（默认 daily，用于读取未来数据判定涨跌）

    Returns:
        {"total_signals": N, "correct": N, "hit_rate": 0.XX,
         "avg_return_5d": 0.XX, "details": [...]}
    """
    from notebook.prediction_card import create_card

    # 构建 data_map：加载技能需要的全部周期
    periods_needed = getattr(skill_instance, 'periods_needed', [period])

    data_map = {}
    for code in codes:
        per_map = {}
        for p in periods_needed:
            rows = load_signal_csv(code, p)
            if rows:
                per_map[p] = rows
        if per_map:
            data_map[code] = per_map

    if not data_map:
        return {"total_signals": 0, "correct": 0, "hit_rate": 0, "avg_return_5d": 0, "details": []}

    results = skill_instance.check(data_map)

    # 过滤日期范围
    if date_range:
        start, end = date_range
        results = [r for r in results if start <= r.trigger_date <= end]

    details = []
    correct = 0
    total_ret = 0.0

    for sr in results:
        card = create_card(sr, skill_instance.verify_days)

        # 用 period（验证周期）加载未来数据
        all_rows = load_signal_csv(sr.code, period)
        future_idx = None
        for i, r in enumerate(all_rows):
            if str(r.get("date", "")) == str(sr.trigger_date):
                future_idx = i + 1
                break
        if future_idx is None:
            for i, r in enumerate(all_rows):
                if str(r.get("date", "")) >= str(sr.trigger_date):
                    future_idx = i + 1
                    break

        future_rows = all_rows[future_idx:] if future_idx else []

        max_days = max(_extract_days(c) for c in sr.criteria) if sr.criteria else 5
        if len(future_rows) < max_days:
            continue

        future_map = {period: future_rows}
        verify_result = skill_instance.verify_from_conditions(
            sr.conditions, sr.criteria, future_map
        )

        if verify_result.get("all_criteria_met"):
            correct += 1

        ret_5d = verify_result.get("close_5d_return", 0) or 0
        total_ret += ret_5d

        details.append({
            "code": sr.code,
            "date": sr.trigger_date,
            "conditions": sr.conditions,
            "correct": verify_result.get("all_criteria_met", False),
            "return_5d": ret_5d,
        })

    total = len(details)
    return {
        "total_signals": total,
        "correct": correct,
        "hit_rate": round(correct / total, 3) if total > 0 else 0,
        "avg_return_5d": round(total_ret / total, 4) if total > 0 else 0,
        "details": details,
    }


def _extract_days(criteria: dict) -> int:
    """从 criteria 字典提取验证天数"""
    m = criteria.get("metric", "")
    for prefix in ["close_", "max_drawdown_", "close_above_expma50_"]:
        if m.startswith(prefix) and m.endswith("d_return"):
            try:
                return int(m.replace(prefix, "").replace("d_return", ""))
            except ValueError:
                pass
        elif m.startswith(prefix) and m.endswith("d"):
            try:
                return int(m.replace(prefix, "").replace("d", ""))
            except ValueError:
                pass
    return 5


def _default_verify(conditions: dict, criteria: list[dict], future_rows: list[dict]) -> dict:
    """默认验证（不依赖技能实例）"""
    signal_close = conditions.get("close", 0)
    result = {}
    all_met = True

    for c in criteria:
        metric = c["metric"]
        op = c.get("operator", ">")
        threshold = c.get("threshold", 0)

        if metric.startswith("close_") and metric.endswith("d_return"):
            n = int(metric.replace("close_", "").replace("d_return", ""))
            if n <= len(future_rows):
                future_close = future_rows[n - 1].get("close", 0)
                ret = (future_close - signal_close) / signal_close if signal_close else 0
                result[metric] = round(ret, 4)
                if op == ">" and not (ret > threshold):
                    all_met = False
                elif op == "<" and not (ret < threshold):
                    all_met = False
            else:
                result[metric] = None

        elif metric.startswith("max_drawdown_") and metric.endswith("d"):
            n = int(metric.replace("max_drawdown_", "").replace("d", ""))
            n = min(n, len(future_rows))
            lows = [r.get("close", signal_close) for r in future_rows[:n]]
            peak = signal_close
            max_dd = 0.0
            for c_val in lows:
                dd = (peak - c_val) / peak if peak else 0
                max_dd = max(max_dd, dd)
                peak = max(peak, c_val)
            result[metric] = round(max_dd, 4)
            if op == "<" and not (max_dd < threshold):
                all_met = False

    result["all_criteria_met"] = all_met
    return result

# -*- coding: utf-8 -*-
"""
笔记本系统 — 预测卡 → 验证 → 案例检索 → 反馈修正

★ 日常使用:
    from notebook import create_card, verify_all_pending, get_skill_stats
    from notebook.skills import SkillOversoldStarBuy

★ 批量回测:
    from notebook import batch_backtest, SkillOversoldStarBuy
    skill = SkillOversoldStarBuy()
    result = batch_backtest(skill, ["sh000001", "sz159740"])

★ 缠论结构定位:
    from notebook.chanlun import get_position
    result = get_position("sh000001", df_daily, df_30min)
"""

# ── 预测卡引擎 ──
from notebook.prediction_card import (
    PredictionCard,
    create_card,
    save_card,
    load_card,
    move_to_verified,
    list_pending,
)

# ── 验证引擎 ──
from notebook.verify_engine import (
    verify_card,
    verify_all_pending,
    batch_backtest,
)

# ── 案例库 ──
from notebook.case_store import (
    init_db,
    insert_case,
    search_similar,
    get_skill_stats,
    get_all_cases,
)

# ── 技能基类 ──
from notebook.skill_base import BaseSkill, SkillResult, load_skill_registry

# ── 反馈分析 ──
from notebook.feedback_loop import (
    skill_hit_rate,
    compare_skills,
    suggest_threshold_adjustment,
)

__all__ = [
    # 预测卡
    "PredictionCard",
    "create_card",
    "save_card",
    "load_card",
    "move_to_verified",
    "list_pending",
    # 验证
    "verify_card",
    "verify_all_pending",
    "batch_backtest",
    # 案例
    "init_db",
    "insert_case",
    "search_similar",
    "get_skill_stats",
    "get_all_cases",
    # 技能
    "BaseSkill",
    "SkillResult",
    "load_skill_registry",
    # 反馈
    "skill_hit_rate",
    "compare_skills",
    "suggest_threshold_adjustment",
]

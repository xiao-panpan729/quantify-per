# -*- coding: utf-8 -*-
"""
案例库 — SQLite 持久化 + 加权相似度检索

预测卡验证后自动入库。检索时按条件维度计算加权相似度，
返回 Top N 历史相似案例 + 统计汇总。
"""

import json
import sqlite3
import math
from pathlib import Path

from notebook.shared import CASE_DB, ensure_dirs

# ── SQL schema ──

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    code TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    criteria_json TEXT NOT NULL,
    verify_date TEXT,
    actual_return_5d REAL,
    actual_return_10d REAL,
    all_criteria_met INTEGER,
    status TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_skill ON cases(skill_name);
CREATE INDEX IF NOT EXISTS idx_code ON cases(code);
CREATE INDEX IF NOT EXISTS idx_status ON cases(status);
"""

# ── 默认相似度权重 ──

DEFAULT_WEIGHTS = {
    "cci": 0.25,
    "star_buy_count": 0.25,
    "vol_llv100": 0.20,
    "trend_score": 0.15,
    "close": 0.15,
}


def _get_conn() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(CASE_DB))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化案例库表结构"""
    conn = _get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def insert_case(card) -> bool:
    """验证后的预测卡入库。

    Args:
        card: PredictionCard（status 已是 verified_correct/verified_wrong）
    """
    init_db()
    conn = _get_conn()

    result = card.result or {}
    actual_5d = result.get("close_5d_return")
    actual_10d = result.get("close_10d_return")
    all_met = 1 if result.get("all_criteria_met") else 0

    conn.execute(
        """INSERT OR REPLACE INTO cases
           (id, skill_name, code, signal_date, conditions_json, criteria_json,
            verify_date, actual_return_5d, actual_return_10d, all_criteria_met, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card.id,
            card.skill_name,
            card.code,
            card.created_date,
            json.dumps(card.conditions, ensure_ascii=False),
            json.dumps(card.criteria, ensure_ascii=False),
            card.verified_date,
            actual_5d,
            actual_10d,
            all_met,
            card.status,
        ),
    )
    conn.commit()
    conn.close()
    return True


def search_similar(skill_name: str, conditions: dict, top_n: int = 10,
                   weights: dict | None = None) -> list[dict]:
    """加权相似度检索 Top N 历史案例

    计算方式：对每个 condition 键计算归一化距离，加权求和。

    Args:
        skill_name: 技能名
        conditions: 当前触发条件
        top_n: 返回数量
        weights: 自定义权重（默认用 DEFAULT_WEIGHTS）

    Returns:
        [{"id": ..., "similarity": 0.85, "status": "verified_correct", ...}, ...]
        按相似度降序排列
    """
    init_db()
    conn = _get_conn()
    w = weights or DEFAULT_WEIGHTS

    rows = conn.execute(
        "SELECT * FROM cases WHERE skill_name = ?", (skill_name,)
    ).fetchall()
    conn.close()

    if not rows:
        return []

    scored = []
    for row in rows:
        hist_cond = json.loads(row["conditions_json"])
        sim = _calc_similarity(conditions, hist_cond, w)
        scored.append({
            "id": row["id"],
            "code": row["code"],
            "signal_date": row["signal_date"],
            "status": row["status"],
            "actual_return_5d": row["actual_return_5d"],
            "actual_return_10d": row["actual_return_10d"],
            "all_criteria_met": bool(row["all_criteria_met"]),
            "similarity": round(sim, 4),
            "conditions": hist_cond,
        })

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_n]


def get_skill_stats(skill_name: str) -> dict:
    """技能命中率统计

    Returns:
        {"total": 50, "correct": 35, "hit_rate": 0.70,
         "avg_return_5d": 0.023, "avg_return_10d": 0.035}
    """
    init_db()
    conn = _get_conn()
    row = conn.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN all_criteria_met = 1 THEN 1 ELSE 0 END) as correct,
                  AVG(actual_return_5d) as avg_ret5,
                  AVG(actual_return_10d) as avg_ret10
           FROM cases WHERE skill_name = ?""",
        (skill_name,),
    ).fetchone()
    conn.close()

    total = row["total"] or 0
    correct = row["correct"] or 0
    return {
        "total": total,
        "correct": correct,
        "hit_rate": round(correct / total, 3) if total > 0 else 0,
        "avg_return_5d": round(row["avg_ret5"], 4) if row["avg_ret5"] else 0,
        "avg_return_10d": round(row["avg_ret10"], 4) if row["avg_ret10"] else 0,
    }


def get_all_cases(skill_name: str = None, code: str = None, status: str = None) -> list[dict]:
    """灵活查询案例"""
    init_db()
    conn = _get_conn()
    query = "SELECT * FROM cases WHERE 1=1"
    params = []
    if skill_name:
        query += " AND skill_name = ?"
        params.append(skill_name)
    if code:
        query += " AND code = ?"
        params.append(code)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY signal_date DESC LIMIT 200"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _calc_similarity(current: dict, historical: dict, weights: dict) -> float:
    """计算两个条件快照的加权相似度 [0, 1]

    数值键：1 - min(|diff|/max_val, 1)
    布尔键：完全匹配 = 1，不匹配 = 0
    """
    total_weight = 0.0
    score = 0.0

    for key, weight in weights.items():
        total_weight += weight
        cv = current.get(key)
        hv = historical.get(key)

        if cv is None or hv is None:
            continue

        if isinstance(cv, bool) or cv in (0, 1):
            # 布尔型
            score += weight if cv == hv else 0
        elif isinstance(cv, (int, float)) and isinstance(hv, (int, float)):
            # 数值型：归一化
            max_val = max(abs(cv), abs(hv), 1.0)
            diff = abs(cv - hv) / max_val
            score += weight * max(0, 1 - diff)
        else:
            # 字符串等：完全匹配
            score += weight if str(cv) == str(hv) else 0

    return score / total_weight if total_weight > 0 else 0.0

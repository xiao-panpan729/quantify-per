# -*- coding: utf-8 -*-
"""
预测卡数据结构 + JSON 文件存储

PredictionCard = 一次结构化的预测判断
  - 创建时状态为 pending，存入 cards/pending/
  - 验证后状态变为 verified_correct / verified_wrong，移入 cards/verified/
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

from notebook.shared import CARDS_PENDING, CARDS_VERIFIED, ensure_dirs
from notebook.skill_base import SkillResult


@dataclass
class PredictionCard:
    id: str                              # UUID
    skill_name: str                      # 使用的技能名
    code: str                            # 标的代码
    created_date: str                    # 创建日期 YYYY-MM-DD
    expiry_date: str                     # 验证到期日 YYYY-MM-DD
    conditions: dict                     # 触发条件快照
    criteria: list[dict]                 # 验收标准
    status: str = "pending"              # pending / verified_correct / verified_wrong / expired
    result: dict | None = None           # 验证后填入
    verified_date: str | None = None     # 实际验证日期


def create_card(sr: SkillResult, verify_days: int) -> PredictionCard:
    """从 SkillResult 创建预测卡

    Args:
        sr: 技能 check() 返回的触发结果
        verify_days: 验证周期（交易日数）

    Returns:
        初始化好的 PredictionCard（status=pending）
    """
    ensure_dirs()
    card_id = sr.card_id or str(uuid.uuid4())[:8]
    created = sr.trigger_date
    expiry = _add_business_days(created, verify_days)

    return PredictionCard(
        id=card_id,
        skill_name=sr.skill_name,
        code=sr.code,
        created_date=created,
        expiry_date=expiry,
        conditions=sr.conditions,
        criteria=sr.criteria,
        status="pending",
    )


def save_card(card: PredictionCard):
    """保存预测卡到 cards/pending/{id}.json"""
    ensure_dirs()
    path = CARDS_PENDING / f"{card.id}.json"
    data = asdict(card)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_card(card_id: str) -> PredictionCard | None:
    """从 cards/ 加载预测卡（先查 pending，再查 verified）"""
    for d in [CARDS_PENDING, CARDS_VERIFIED]:
        path = d / f"{card_id}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return PredictionCard(**data)
    return None


def move_to_verified(card: PredictionCard):
    """验证完成后，将卡片从 pending/ 移到 verified/"""
    pending_path = CARDS_PENDING / f"{card.id}.json"
    verified_path = CARDS_VERIFIED / f"{card.id}.json"
    ensure_dirs()

    if pending_path.exists():
        data = None
        try:
            with open(pending_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            pass

        if data:
            data["status"] = card.status
            data["result"] = card.result
            data["verified_date"] = card.verified_date
            with open(verified_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        pending_path.unlink()


def list_pending() -> list[str]:
    """列出所有待验证卡片的 ID"""
    ensure_dirs()
    return sorted([p.stem for p in CARDS_PENDING.glob("*.json")])


def _add_business_days(date_str: str, days: int) -> str:
    """简化版：自然日 = 交易日数 × 1.4（含周末）"""
    dt = _parse_date(date_str)
    calendar_days = int(days * 1.4) + 1
    expiry = dt + timedelta(days=calendar_days)
    return expiry.strftime("%Y-%m-%d")


def _parse_date(date_str: str) -> datetime:
    """兼容 YYYY-MM-DD 和 YYYYMMDD 两种格式"""
    date_str = str(date_str).strip()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return datetime.strptime(date_str, "%Y%m%d")

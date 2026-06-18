# -*- coding: utf-8 -*-
"""
变量分类器 — 查询 + 候选管理

Usage:
  from tools.variable_taxonomy import lookup_variable, add_candidates, get_candidates

  # 查询变量匹配
  results = lookup_variable(["美伊协议", "霍尔木兹"])

  # 追加候选项
  add_candidates(["量子加密", "太空采矿"], "中信建投0617")

  # 读取待审队列
  pending = get_candidates("pending")
"""

import json
import sys
from pathlib import Path
from datetime import datetime

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
MACRO_DIR = PROJECT_ROOT / "signals" / "tracking" / "_macro"
TAXONOMY_PATH = MACRO_DIR / "variable_taxonomy.json"
CANDIDATES_PATH = MACRO_DIR / "variable_candidates.json"


def _load_taxonomy() -> dict:
    """加载变量分类体系"""
    if TAXONOMY_PATH.exists():
        return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    return {"variables": [], "meta": {}}


def _load_candidates() -> dict:
    """加载候选项队列"""
    if CANDIDATES_PATH.exists():
        return json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    return {"candidates": [], "meta": {"last_checked": "", "total_pending": 0}}


def _save_candidates(data: dict):
    """保存候选项队列"""
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def lookup_variable(keywords: list[str]) -> list[dict]:
    """
    给定关键词列表，返回匹配的变量条目。
    按变量层级排序：核心变量 > 结构性变量 > 下游结果 > 情绪噪音
    """
    taxonomy = _load_taxonomy()
    variables = taxonomy.get("variables", [])
    if not keywords:
        return []

    results = []
    for var in variables:
        var_kws = [kw.lower() for kw in var.get("keywords", [])]
        for kw in keywords:
            kw_lower = kw.lower().strip()
            if not kw_lower:
                continue
            for vk in var_kws:
                if kw_lower in vk or vk in kw_lower:
                    if var not in results:
                        results.append(var)
                    break

    level_order = {"核心变量": 0, "结构性变量": 1, "下游结果": 2, "情绪噪音": 3}
    results.sort(key=lambda x: level_order.get(x.get("level", ""), 99))

    return results


def add_candidates(keywords: list[str], source_article: str = "") -> int:
    """
    将未匹配的关键词追加到候选项队列。
    返回本次新增的候选项数量。
    """
    if not keywords:
        return 0

    data = _load_candidates()
    candidates = data.get("candidates", [])
    today = datetime.now().strftime("%Y-%m-%d")
    added = 0

    for kw in keywords:
        kw_clean = kw.strip()
        if not kw_clean:
            continue
        existing = [
            c for c in candidates if c.get("keyword", "").lower() == kw_clean.lower()
        ]
        if existing:
            existing[0]["seen_count"] = existing[0].get("seen_count", 1) + 1
            existing[0]["last_seen"] = today
            if (
                source_article
                and source_article not in existing[0].get("source_articles", [])
            ):
                existing[0]["source_articles"].append(source_article)
        else:
            candidates.append(
                {
                    "keyword": kw_clean,
                    "first_seen": today,
                    "last_seen": today,
                    "seen_count": 1,
                    "source_articles": [source_article] if source_article else [],
                    "status": "pending",
                }
            )
            added += 1

    data["candidates"] = candidates
    data["meta"]["last_checked"] = today
    data["meta"]["total_pending"] = sum(
        1 for c in candidates if c.get("status") == "pending"
    )
    _save_candidates(data)

    return added


def get_candidates(status_filter: str = "pending") -> list[dict]:
    """
    读取候选项队列。
    status_filter: "pending" / "classified" / "dismissed" / "all"
    """
    data = _load_candidates()
    candidates = data.get("candidates", [])
    if status_filter == "all":
        return candidates
    return [c for c in candidates if c.get("status") == status_filter]


def classify_candidate(keyword: str, entry: dict) -> bool:
    """
    将候选项归类到 taxonomy.json。
    entry 结构与 taxonomy variables 相同。
    """
    taxonomy = _load_taxonomy()

    max_id = 0
    for var in taxonomy.get("variables", []):
        vid = var.get("id", "")
        if vid.startswith("VAR-"):
            try:
                num = int(vid.replace("VAR-", ""))
                max_id = max(max_id, num)
            except ValueError:
                pass
    new_id = f"VAR-{max_id + 1:03d}"
    entry["id"] = new_id

    taxonomy.setdefault("variables", []).append(entry)
    taxonomy["meta"]["total_variables"] = len(taxonomy["variables"])
    taxonomy["meta"]["updated"] = datetime.now().strftime("%Y-%m-%d")

    TAXONOMY_PATH.parent.mkdir(parents=True, exist_ok=True)
    TAXONOMY_PATH.write_text(
        json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _update_candidate_status(keyword, "classified")
    return True


def dismiss_candidate(keyword: str) -> bool:
    """打回候选项（标记为 dismissed）"""
    return _update_candidate_status(keyword, "dismissed")


def _update_candidate_status(keyword: str, status: str) -> bool:
    data = _load_candidates()
    for c in data.get("candidates", []):
        if c.get("keyword", "").lower() == keyword.lower():
            c["status"] = status
            data["meta"]["total_pending"] = sum(
                1 for x in data.get("candidates", []) if x.get("status") == "pending"
            )
            _save_candidates(data)
            return True
    return False


def get_stats() -> dict:
    """返回统计信息"""
    taxonomy = _load_taxonomy()
    candidates = get_candidates("all")
    pending = get_candidates("pending")
    level_counts = {}
    for var in taxonomy.get("variables", []):
        level = var.get("level", "未知")
        level_counts[level] = level_counts.get(level, 0) + 1
    return {
        "total_variables": len(taxonomy.get("variables", [])),
        "level_breakdown": level_counts,
        "total_candidates": len(candidates),
        "pending_candidates": len(pending),
        "last_updated": taxonomy.get("meta", {}).get("updated", ""),
    }


# ─── CLI ───
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        stats = get_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif len(sys.argv) > 2 and sys.argv[1] == "--lookup":
        kw = sys.argv[2:]
        results = lookup_variable(kw)
        print(f"查询: {kw}")
        print(f"匹配: {len(results)} 条")
        for r in results:
            print(
                f"  {r['id']} [{r['level']}] {r.get('chain','')} "
                f"→ {', '.join(r.get('narratives',[]))}"
            )
    elif len(sys.argv) > 1 and sys.argv[1] == "--candidates":
        pending = get_candidates("pending")
        print(f"待审候选项: {len(pending)} 个")
        for c in pending:
            print(
                f"  {c['keyword']} (出现{c['seen_count']}次, "
                f"首次{c['first_seen']})"
            )
    else:
        stats = get_stats()
        print(f"变量总数: {stats['total_variables']}")
        print(f"层级分布: {stats['level_breakdown']}")
        print(f"待审候选项: {stats['pending_candidates']}")
        print(f"最后更新: {stats['last_updated']}")

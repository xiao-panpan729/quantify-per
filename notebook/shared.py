# -*- coding: utf-8 -*-
"""
笔记本系统路径配置 + 数据读取工具

所有路径常量集中管理。CSV 读取只做 dict 转换，不做指标计算。
"""

import csv
import json
import os
from pathlib import Path

# ── 项目根目录（notebook/shared.py → 项目根） ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 只读：主系统数据 ──
TRACKING_DIR = _PROJECT_ROOT / "signals" / "tracking"
LATEST_JSON = TRACKING_DIR / "latest.json"
CYCLE_REPORT = TRACKING_DIR / "cycle_report.json"

# ── 笔记本自身路径 ──
NOTEBOOK_DIR = _PROJECT_ROOT / "notebook"
CARDS_DIR = NOTEBOOK_DIR / "cards"
CARDS_PENDING = CARDS_DIR / "pending"
CARDS_VERIFIED = CARDS_DIR / "verified"
CASE_DB = NOTEBOOK_DIR / "cases.db"
SKILLS_DIR = NOTEBOOK_DIR / "skills"


def ensure_dirs():
    """创建笔记本需要的所有目录"""
    CARDS_PENDING.mkdir(parents=True, exist_ok=True)
    CARDS_VERIFIED.mkdir(parents=True, exist_ok=True)


def load_signal_csv(code: str, period: str) -> list[dict]:
    """读取单标的单周期信号 CSV，返回 dict 列表（按时间升序）

    Args:
        code: 标的代码，如 sh000001
        period: 周期标识，如 daily / min5 / min30 / min60

    Returns:
        [{"timestamp": ..., "close": ..., ...}, ...]，全部转为 float 友好的 dict
    """
    csv_path = TRACKING_DIR / code / f"{period}_signals.csv"
    if not csv_path.exists():
        return []

    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # 数值列转为 float/int（保持 csv 原始字符串为后备）
            cleaned = {}
            for k, v in r.items():
                if v == "" or v is None:
                    cleaned[k] = ""
                else:
                    try:
                        cleaned[k] = float(v)
                        if cleaned[k] == int(cleaned[k]):
                            cleaned[k] = int(cleaned[k])
                    except (ValueError, TypeError):
                        cleaned[k] = v
            rows.append(cleaned)
    return rows


def load_latest_signals() -> dict:
    """读取 latest.json 完整内容"""
    if not LATEST_JSON.exists():
        return {}
    with open(LATEST_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_cycle_report() -> list[dict]:
    """读取 cycle_report.json"""
    if not CYCLE_REPORT.exists():
        return []
    with open(CYCLE_REPORT, "r", encoding="utf-8") as f:
        return json.load(f)


def load_name_map() -> dict:
    """延迟导入 config.py NAME_MAP，避免循环依赖"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "config", _PROJECT_ROOT / "config.py"
    )
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    return cfg.NAME_MAP

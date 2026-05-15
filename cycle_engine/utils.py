# -*- coding: utf-8 -*-
"""
cycle_engine 工具与数据IO — 配置常量 / CSV读取 / 标的扫描
"""
import os
import sys
import csv
import json
import math
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PROJECT_ROOT

BASE = Path(PROJECT_ROOT)
SNAPSHOT_DIR = BASE / 'signals' / 'tracking'
OUTPUT_PATH = BASE / 'signals' / 'tracking' / 'cycle_report.json'

PERIODS = ['min1', 'min5', 'min15', 'min30', 'min60', 'daily']
PERIOD_LABELS = {
    'min1': '1分钟', 'min5': '5分钟', 'min15': '15分钟', 'min30': '30分钟',
    'min60': '60分钟', 'daily': '日线',
}

KLINES_LOOKBACK = {
    'min1': 500, 'min5': 500, 'min15': 500, 'min30': 500,
    'min60': 500, 'daily': 0,
}

def read_csv(code, period):
    fpath = SNAPSHOT_DIR / code / f'{period}_signals.csv'
    if not fpath.exists():
        return []
    with open(fpath, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    max_k = KLINES_LOOKBACK.get(period, 200)
    if max_k > 0 and len(rows) > max_k:
        rows = rows[-max_k:]
    return rows


def get_all_codes():
    codes = []
    for d in SNAPSHOT_DIR.iterdir():
        if d.is_dir() and (d / 'daily_signals.csv').exists():
            codes.append(d.name)
    return sorted(codes)


def get_name_map():
    try:
        from config import NAME_MAP
        return NAME_MAP
    except:
        return {}


# ============================================================
# 排列熵分析 — 检测趋势线的有序/无序状态（非预期解检测）

def safe_float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


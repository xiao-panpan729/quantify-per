# -*- coding: utf-8 -*-
"""
笔记本技能库 — 每个技能一个 Python 类，遵守 BaseSkill 统一契约

买侧（三级入场，对齐 tools/volume_leader/monitor.py）:
  entry_ma        — MA级入场: 5分★买+MA链+无死叉+60分/日线双黄线+PE门禁
  entry_jincha    — 金叉级入场: MA级全部 + 5分EXPMA金叉
  entry_resonance — 共振级入场: 金叉级全部 + 15/30分金叉共振

卖侧（两层出场）:
  exit_sell_t      — 做T: 5分CCI顶背驰+黄线上+窗口唯一
  exit_sell_reduce — 减仓: 5分★卖+close<MA5+无金叉+15分黄线下
"""

from notebook.skills.entry_ma import EntryMA
from notebook.skills.entry_jincha import EntryJincha
from notebook.skills.entry_resonance import EntryResonance
from notebook.skills.exit_sell_t import ExitSellT
from notebook.skills.exit_sell_reduce import ExitSellReduce

__all__ = [
    "EntryMA",
    "EntryJincha",
    "EntryResonance",
    "ExitSellT",
    "ExitSellReduce",
]

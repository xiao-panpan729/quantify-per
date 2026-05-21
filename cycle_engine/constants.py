# -*- coding: utf-8 -*-
"""周期分析领域常量 — 杜绝魔法字符串，手滑拼错直接报 AttributeError"""


class Direction:
    """趋势方向（字符串常量，可直接 == 比较）"""
    BULLISH = 'bullish'
    BULLISH_BIAS = 'bullish_bias'
    NEUTRAL = 'neutral'
    BEARISH_BIAS = 'bearish_bias'
    BEARISH = 'bearish'

    BULLISH_DIRS = (BULLISH, BULLISH_BIAS)
    BEARISH_DIRS = (BEARISH, BEARISH_BIAS)


class RhythmVerdict:
    """节奏完整性判定（字符串常量，可直接 == 比较）"""
    INTACT = 'intact'
    TACTICAL_BROKEN = 'tactical_broken'
    STRATEGIC_BROKEN = 'strategic_broken'
    FULLY_BROKEN = 'fully_broken'

    # 常用组合
    INTACT_OR_TACTICAL = (INTACT, TACTICAL_BROKEN)
    STRATEGIC_OR_FULLY = (STRATEGIC_BROKEN, FULLY_BROKEN)
    TACTICAL_OR_STRATEGIC = (TACTICAL_BROKEN, STRATEGIC_BROKEN)

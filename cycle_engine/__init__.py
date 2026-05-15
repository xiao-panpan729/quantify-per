# -*- coding: utf-8 -*-
"""
周期循环引擎 v3.5 — Cycle Engine (包)

三层架构: 位置 → 趋势 → 循环
"""

from .utils import (
    read_csv, get_all_codes, get_name_map, safe_float,
    BASE, SNAPSHOT_DIR, OUTPUT_PATH,
    PERIODS, PERIOD_LABELS, KLINES_LOOKBACK,
)
from .indicators import (
    _permutation_entropy, analyze_trend_pe,
    judge_position, judge_trend, extract_anchors,
    price_effectiveness, signal_quality,
)
from .cycle_structure import (
    cycle_pattern, _find_local_extremes, _extract_wave_events,
    detect_dominant_cycle, analyze_volume_regime,
    judge_wave_structure, detect_exponential_readiness,
    detect_rs_density,
)
from .engine import (
    get_market_coefficient, analyze_period, analyze, analyze_all,
)
from .grading import (
    _grade_trend_signal, _grade_output, best_period_label,
    _generate_advice,
)
from .reporting import (
    _fmt_price_eff, _fmt_signal_icon, _fmt_periods_detail,
    format_report, save_results, G,
)

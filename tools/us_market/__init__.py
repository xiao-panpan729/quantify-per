# -*- coding: utf-8 -*-
"""
US Market → A-Share 跨市场映射包

三层架构:
  Layer 1: US 宏观 → A股板块敏感度 (macro_sensitivity.py)
  Layer 2: US ETF 势能 + 明星股动量 (etf_momentum.py + star_stocks.py)
  Layer 3: US → CN 跨市场相关性+领先滞后 (cross_mapping.py)
"""

from .etf_momentum import (
    US_ETF_UNIVERSE, fetch_etf_daily, calc_all_us_etf_scores,
    report_rankings, save_results,
)
from .star_stocks import (
    US_STAR_STOCKS, fetch_stock_daily, calc_all_us_stock_scores,
    report_stock_rankings as report_stock_rankings,
    save_stock_results,
)

"""
capex_analyzer.py — CFA CAPEX 周期分析 (独立工具)
=================================================

CFA 5层分析框架:
  1. Identification — CAPEX/营收阈值分类
  2. Operating Performance — 固定资产周转率
  3. Market Valuation — P/B vs ROE 二维定位 (预留)
  4. Investment Efficiency — ROIC vs WACC (推断)
  5. Competitive Positioning — (预留, 需行业数据)

Usage:
  from tools.fundamental.capex_analyzer import CapexAnalyzer
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_proj_root = Path(__file__).resolve().parent.parent.parent
if str(_proj_root) not in sys.path:
    sys.path.insert(0, str(_proj_root))


# CAPEX/营收 阈值 (CFA标准)
CAPEX_THRESHOLDS = [
    (0.40, '超高'),   # >40%
    (0.25, '高'),     # 25-40%
    (0.15, '中'),     # 15-25%
    (0.10, '低'),     # 10-15%
    (0.00, '极低'),   # <10%
]


class CapexAnalyzer:
    """CAPEX 周期分析器 — 面向单股票或全面板."""

    @staticmethod
    def classify_capex_stage(capex_ratio: float) -> str:
        """Classify CAPEX/Revenue ratio into stage."""
        for threshold, label in CAPEX_THRESHOLDS:
            if capex_ratio >= threshold:
                return label
        return '极低'

    @staticmethod
    def classify_capex_trend(capex_ratio_series: np.ndarray) -> str:
        """Classify CAPEX trend over time."""
        if len(capex_ratio_series) < 3:
            return '数据不足'
        x = np.arange(len(capex_ratio_series))
        slope = np.polyfit(x, capex_ratio_series, 1)[0]
        if slope > 0.01:
            return '扩张'
        elif slope < -0.01:
            return '收缩'
        return '稳定'

    def analyze(self, panel: pd.DataFrame, code: str = None) -> dict:
        """
        Run CAPEX analysis.
        If code is None, run for all stocks in panel.
        """
        panel = panel.sort_values('report_date').copy()
        results = {}

        stocks = [code] if code else panel['code'].unique()
        for c in stocks:
            grp = panel[panel['code'] == c].reset_index(drop=True)
            if len(grp) < 4:
                continue

            revenue = grp['营业总收入'].values
            capex = grp['购建固定资产支出(CAPEX)'].values if '购建固定资产支出(CAPEX)' in grp.columns else None
            depr = grp['固定资产折旧'].values if '固定资产折旧' in grp.columns else None
            fixed_assets = grp['固定资产'].values if '固定资产' in grp.columns else None
            ocf = grp['经营现金流量净额'].values if '经营现金流量净额' in grp.columns else None
            net_profit = grp['净利润'].values if '净利润' in grp.columns else None

            entry = {}

            # 1. CAPEX/Revenue ratio & trend
            if capex is not None:
                c_r = capex / np.maximum(revenue, 1)
                entry['capex_revenue_ratio'] = round(float(np.mean(c_r[-3:])), 4)
                entry['capex_stage'] = self.classify_capex_stage(np.mean(c_r[-3:]))
                entry['capex_trend'] = self.classify_capex_trend(c_r)
            else:
                entry['capex_revenue_ratio'] = None
                entry['capex_stage'] = None
                entry['capex_trend'] = None

            # 2. Depreciation/Revenue ratio (aging of existing assets)
            if depr is not None and revenue is not None:
                d_r = depr / np.maximum(revenue, 1)
                entry['depr_revenue_ratio'] = round(float(np.mean(d_r[-3:])), 4)
                entry['depr_trend'] = self.classify_capex_trend(d_r)
            else:
                entry['depr_revenue_ratio'] = None
                entry['depr_trend'] = None

            # 3. Fixed asset turnover (revenue / fixed assets)
            if fixed_assets is not None and len(fixed_assets) > 0:
                fa_latest = fixed_assets[-1]
                if fa_latest > 0:
                    entry['fa_turnover'] = round(float(revenue[-1] / fa_latest), 4)
                else:
                    entry['fa_turnover'] = None
            else:
                entry['fa_turnover'] = None

            # 4. FCF (operating cash flow - CAPEX)
            if ocf is not None and capex is not None:
                fcf = ocf - capex
                entry['fcf_latest'] = round(float(fcf[-1]), 2)
                entry['fcf_trend'] = '转正' if fcf[-1] > 0 and (len(fcf) < 2 or fcf[-2] < 0) else \
                    '扩大' if len(fcf) >= 2 and fcf[-1] > fcf[-2] * 1.1 else \
                    '稳定' if len(fcf) >= 2 and fcf[-1] > 0 else \
                    '负值'
            else:
                entry['fcf_latest'] = None
                entry['fcf_trend'] = None

            # 5. ROIC inference: NOPAT / invested capital
            # NOPAT ≈ net profit + (1-tax_rate)*interest ≈ net profit (simplified)
            # invested capital ≈ equity + debt ≈ shareholders equity + total liabilities
            # We use ROE as a proxy for simplicity
            if net_profit is not None and len(net_profit) > 0:
                entry['roe_latest'] = round(float(
                    grp['净资产收益率(ROE)'].iloc[-1]), 2) if '净资产收益率(ROE)' in grp.columns and pd.notna(grp['净资产收益率(ROE)'].iloc[-1]) else None
            else:
                entry['roe_latest'] = None

            results[c] = entry

        return results if code is None else results.get(code, {})

    def analyze_stock(self, panel: pd.DataFrame, code: str) -> dict:
        """Convenience: single stock CAPEX analysis."""
        return self.analyze(panel, code=code)

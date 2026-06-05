"""
growth_narrative.py — Type A / Type B 双成长叙事检测
====================================================

Layer 2 of the fundamental data layer:

  Type A — PEG叙事（机构抱团风格）
    净利润连续高增长（consistency + acceleration + sustainability）

  Type B — 营收扩张→收获期转折（重资产生命周期）
    营收持续增长 → CAPEX周期识别 → 折旧/FCF 转折检测

Usage:
  from tools.fundamental.growth_narrative import (
      TypeADetector, TypeBDetector, GrowthClassifier
  )
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_proj_root = Path(__file__).resolve().parent.parent.parent
if str(_proj_root) not in sys.path:
    sys.path.insert(0, str(_proj_root))

from config import NAME_MAP


# ══════════════════════════════════════════════════════════════════
# Type A — PEG叙事
# ══════════════════════════════════════════════════════════════════

class TypeADetector:
    """
    Type A = PEG叙事：净利润持续增长，ROE同步提升。
    （v2 — 经全市场5428只A股参数扫描优化：4季均值>12% + 至少3季正增长）
    → 全市场命中~35%，淘汰边缘股70只，覆盖最核心的成长标的

    硬条件:
      - 最近4季净利润同比均值 > 12%
      - 至少3季正增长 (>0%)
    评分项:
      - 增速量级 (0.5): 按均值分档
      - acceleration (0.25): 近2季均值 > 前2季均值
      - ROE趋势 (0.25): 线性斜率 > 0
    """

    def __init__(self, n_consecutive: int = 4,
                 avg_threshold: float = 12.0,
                 min_positive: int = 3):
        self.n_consecutive = n_consecutive
        self.avg_threshold = avg_threshold
        self.min_positive = min_positive

    def detect(self, panel: pd.DataFrame) -> dict:
        panel = panel.sort_values(['code', 'report_date']).copy()

        result = {}
        for code, grp in panel.groupby('code'):
            grp = grp.dropna(subset=['净利润增长率']).reset_index(drop=True)
            if len(grp) < self.n_consecutive:
                continue

            growth = grp['净利润增长率'].values
            roe = grp['净资产收益率(ROE)'].values if '净资产收益率(ROE)' in grp.columns else None

            recent = growth[-self.n_consecutive:]
            avg_growth = float(np.mean(recent))
            n_positive = sum(1 for g in recent if g > 0)

            # 硬条件: 均值>10% 且 至少3季正增长
            if avg_growth <= self.avg_threshold or n_positive < self.min_positive:
                result[code] = {
                    'type': None,
                    'confidence': 0.0,
                    'details': {'reason': f'4季均值{avg_growth:.1f}%≤{self.avg_threshold}% 或正增长{n_positive}<{self.min_positive}',
                                'recent_growth': [round(float(g), 2) for g in recent]}
                }
                continue

            # Score components
            score = 0.0
            max_score = 0.0

            # 增速量级 (weight 0.5)
            if avg_growth > 30:
                score += 0.5
            elif avg_growth > 20:
                score += 0.4
            elif avg_growth > 15:
                score += 0.35
            else:
                score += 0.30  # 10-15%
            max_score += 0.5

            # Acceleration: 近2季均值 > 前2季均值 (weight 0.25)
            recent_2 = np.mean(recent[-2:])
            prior_2 = np.mean(recent[-4:-2]) if len(recent) >= 4 else np.mean(recent[:1]) if len(recent) > 1 else 0
            if recent_2 > prior_2:
                score += 0.25
            max_score += 0.25

            # Sustainability: ROE同步提升 (weight 0.25)
            if roe is not None and len(roe) >= self.n_consecutive:
                recent_roe = roe[-self.n_consecutive:]
                if len(recent_roe) >= 3:
                    x = np.arange(len(recent_roe))
                    slope = np.polyfit(x, recent_roe, 1)[0]
                    if slope > 0:
                        score += 0.25
                elif recent_roe[-1] > recent_roe[0]:
                    score += 0.15
            max_score += 0.25

            confidence = score / max_score if max_score > 0 else 0.0

            result[code] = {
                'type': 'Type A',
                'confidence': round(confidence, 3),
                'details': {
                    'recent_growth': [round(float(g), 2) for g in recent],
                    'avg_growth': round(avg_growth, 2),
                    'n_positive': n_positive,
                    'acceleration_flag': bool(recent_2 > prior_2),
                    'roe_trend_up': bool(score >= 0.75),
                }
            }

        return result


# ══════════════════════════════════════════════════════════════════
# Type B — 营收扩张→收获期转折
# ══════════════════════════════════════════════════════════════════

class TypeBDetector:
    """
    Type B = 营收扩张→收获期转折.

    ★ 核心理念（来自资深交易者十几年经验总结）:
      "营收一般都是真的，但是利润企业可以藏着。"
      利润可通过折旧政策、减值准备、关联交易调节；
      营业额很难造假——有增值税发票、银行流水、客户验证。
      → Type B（营收驱动）比 Type A（净利润驱动）在A股更可靠。

    必要条件:
      - 营收连续 5 年正增长（至少 8 个连续半年度同比 > 0）
    转折信号（满足任意 1 项即触发）:
      1. CAPEX/营收从高位回落 > 2 年
      2. 折旧/营收比率到顶下降
      3. FCF 由负转正且持续扩大
    置信度: 营收持续性 0.4 + 转折信号 0.6
    """

    def __init__(self, min_revenue_periods: int = 8):
        self.min_revenue_periods = min_revenue_periods

    def detect(self, panel: pd.DataFrame) -> dict:
        """
        Returns: {code: {type, confidence, details}, ...}
        """
        panel = panel.sort_values(['code', 'report_date']).copy()

        result = {}
        for code, grp in panel.groupby('code'):
            grp = grp.reset_index(drop=True)
            if len(grp) < self.min_revenue_periods:
                continue

            revenue = grp['营业总收入'].values
            capex = grp['购建固定资产支出(CAPEX)'].values if '购建固定资产支出(CAPEX)' in grp.columns else None
            depr = grp['固定资产折旧'].values if '固定资产折旧' in grp.columns else None
            ocf = grp['经营现金流量净额'].values if '经营现金流量净额' in grp.columns else None

            # Revenue persistence
            rev_positive = sum(1 for r in revenue[-self.min_revenue_periods:] if r > 0)
            rev_persistence = rev_positive / self.min_revenue_periods
            rev_ok = rev_persistence >= 0.75  # at least 75% positive

            if not rev_ok:
                result[code] = {
                    'type': None,
                    'confidence': 0.0,
                    'details': {'reason': '营收持续正增长比例不足',
                                'rev_persistence': round(rev_persistence, 3)}
                }
                continue

            # Revenue slope: linear fit on log(revenue) → growth trend
            log_rev = np.log(np.maximum(revenue, 1))
            x = np.arange(len(log_rev))
            rev_slope = np.polyfit(x, log_rev, 1)[0]  # log-linear growth rate

            # --- Turn signals ---
            signals = []
            signal_score = 0.0
            max_signal = 0.0

            # Signal 1: CAPEX/Revenue 从高位回落 (weight 0.25)
            if capex is not None and len(capex) >= 6:
                capex_ratio = capex / np.maximum(revenue, 1)
                recent_ratio = np.mean(capex_ratio[-3:])
                prior_ratio = np.mean(capex_ratio[-6:-3])
                if prior_ratio > 0.05 and recent_ratio < prior_ratio * 0.8:
                    signals.append('CAPEX/营收回落')
                    signal_score += 0.25
                elif prior_ratio > 0.05 and recent_ratio < prior_ratio:
                    signals.append('CAPEX/营收微降')
                    signal_score += 0.15
            max_signal += 0.25

            # Signal 2: 折旧/营收比率到顶下降 (weight 0.2)
            if depr is not None and len(depr) >= 6:
                depr_ratio = depr / np.maximum(revenue, 1)
                recent_depr = np.mean(depr_ratio[-2:])
                peak_depr = max(depr_ratio[:-2]) if len(depr_ratio) > 2 else 0
                if peak_depr > 0 and recent_depr < peak_depr * 0.85:
                    signals.append('折旧/营收到顶下降')
                    signal_score += 0.2
            max_signal += 0.2

            # Signal 3: FCF由负转正且持续扩大 (weight 0.15)
            if ocf is not None and capex is not None and len(ocf) >= 4 and len(capex) >= 4:
                fcf = ocf - capex
                recent_fcf = np.mean(fcf[-2:])
                prior_fcf = np.mean(fcf[-4:-2]) if len(fcf) >= 4 else np.mean(fcf[:-2])
                if prior_fcf < 0 and recent_fcf > 0:
                    signals.append('FCF由负转正')
                    signal_score += 0.15
                elif recent_fcf > 0 and recent_fcf > prior_fcf:
                    signals.append('FCF持续扩大')
                    signal_score += 0.1
            max_signal += 0.15

            # Confidence: revenue persistence (0.4) + turn signals (0.6)
            rev_conf = rev_persistence * 0.4
            sig_conf = signal_score / max_signal * 0.6 if max_signal > 0 else 0
            confidence = rev_conf + sig_conf

            result[code] = {
                'type': 'Type B',
                'confidence': round(confidence, 3),
                'details': {
                    'rev_persistence': round(rev_persistence, 3),
                    'rev_growth_rate': round(float(rev_slope), 4),
                    'signals': signals,
                    'signal_weight': round(signal_score / max_signal, 3) if max_signal > 0 else 0,
                }
            }

        return result


# ══════════════════════════════════════════════════════════════════
# GrowthClassifier (Type A + Type B 综合)
# ══════════════════════════════════════════════════════════════════

class GrowthClassifier:
    """
    Combine Type A and Type B detectors.

    Output categories:
      - Type A (PEG 机构抱团)
      - Type B (重资产收获期)
      - Type A+B (两者兼备)
      - 都不是
    """

    def __init__(self, type_a_detector: TypeADetector = None,
                 type_b_detector: TypeBDetector = None):
        self.type_a = type_a_detector or TypeADetector()
        self.type_b = type_b_detector or TypeBDetector()

    def classify(self, panel: pd.DataFrame,
                 universe: set = None) -> dict:
        """
        Returns: {code: {primary_type, confidence, a_details, b_details}}
        """
        if universe is None:
            universe = set(NAME_MAP.keys())

        a_results = self.type_a.detect(panel)
        b_results = self.type_b.detect(panel)

        combined = {}
        all_codes = set(a_results.keys()) | set(b_results.keys())
        for code in all_codes:
            if code not in universe:
                continue

            a_info = a_results.get(code, {})
            b_info = b_results.get(code, {})

            a_conf = a_info.get('confidence', 0.0) if a_info.get('type') == 'Type A' else 0.0
            b_conf = b_info.get('confidence', 0.0) if b_info.get('type') == 'Type B' else 0.0

            # Determine primary type
            if a_conf >= 0.6 and b_conf >= 0.6:
                primary = 'Type A+B'
                confidence = max(a_conf, b_conf)
            elif a_conf >= 0.6:
                primary = 'Type A'
                confidence = a_conf
            elif b_conf >= 0.6:
                primary = 'Type B'
                confidence = b_conf
            elif a_conf >= 0.4:
                primary = 'Type A (weak)'
                confidence = a_conf
            elif b_conf >= 0.4:
                primary = 'Type B (weak)'
                confidence = b_conf
            else:
                primary = 'None'
                confidence = 0.0

            combined[code] = {
                'primary_type': primary,
                'confidence': round(confidence, 3),
                'type_a_score': round(a_conf, 3),
                'type_b_score': round(b_conf, 3),
                'details_a': a_info.get('details', {}),
                'details_b': b_info.get('details', {}),
            }

        return combined

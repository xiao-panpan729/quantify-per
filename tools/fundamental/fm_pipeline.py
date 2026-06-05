"""
fm_pipeline.py — Rolling Fama-MacBeth + Factor Momentum
======================================================

Layer 1 of the fundamental data layer:
  - GpcwLoader: load & align pytdx gpcw quarterly financial data
  - RollingFamaMacBeth: window=12q, rolling 1q at a time → factor premium time series
  - FactorMomentum: TSMOM & CSMOM signals from the premium series

Usage:
  from tools.fundamental.fm_pipeline import GpcwLoader, RollingFamaMacBeth, FactorMomentum
"""

import sys, json, warnings, time, os
from pathlib import Path

import numpy as np
import pandas as pd
from pytdx.reader import HistoryFinancialReader
from pytdx.reader import TdxDailyBarReader
from scipy.stats import mstats
from linearmodels import FamaMacBeth
import statsmodels.api as sm

# ── project root ──
_proj_root = Path(__file__).resolve().parent.parent.parent
if str(_proj_root) not in sys.path:
    sys.path.insert(0, str(_proj_root))

warnings.filterwarnings('ignore')

# Windows GBK console workaround
if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

VIPDOC = Path("C:/zd_cjzq/vipdoc")
CW_DIR = VIPDOC / "cw"
SIGNALS_DIR = _proj_root / "signals" / "tracking"

# ══════════════════════════════════════════════════════════════════
# Field mapping (pytdx gpcw col index → indicator name)
# ══════════════════════════════════════════════════════════════════

FIELD_MAP = {
    '基本每股收益': 1,
    '每股净资产': 4,
    '营业成本': 17,
    '固定资产': 27,              # col27 — fixed assets (net)
    '股东权益合计': 72,
    '营业总收入': 74,
    '净利润': 95,
    '归母净利润': 96,
    '经营现金流量净额': 107,
    '购建固定资产支出(CAPEX)': 114,  # col114 — cash paid for fixed assets
    '固定资产折旧': 136,           # col136 — depreciation
    '营收增长率': 183,
    '净利润增长率': 184,
    '期间费用率': 196,
    '销售净利率': 199,
    '毛利率': 202,
    '扣非净利润': 206,
    '资产负债率': 210,
    '净资产收益率(ROE)': 281,
}

# 6 FM factors (same as v0.1)
FACTOR_DEFS = {
    '净资产收益率(ROE)': {'col': 281, 'direction': +1, 'short': 'ROE'},
    '毛利率':            {'col': 202, 'direction': +1, 'short': '毛利率'},
    '营收增长率':         {'col': 183, 'direction': +1, 'short': '营收增长'},
    '净利润增长率':        {'col': 184, 'direction': +1, 'short': '净利增长'},
    '资产负债率':         {'col': 210, 'direction': -1, 'short': '负债率'},
    '经营现金流/营收':     {'col': None, 'direction': +1, 'short': '现金流/营收'},
}

FACTOR_KEYS = list(FACTOR_DEFS.keys())


# ══════════════════════════════════════════════════════════════════
# 1. GpcwLoader
# ══════════════════════════════════════════════════════════════════

class GpcwLoader:
    """Load and align gpcw quarterly financial data."""

    def __init__(self, cw_dir: Path = CW_DIR, min_year: int = 2021):
        self.cw_dir = Path(cw_dir)
        self.min_year = min_year
        self._panel_cache = None

    @staticmethod
    def _add_market_prefix(code: str) -> str:
        if code.startswith('6'):
            return f'sh{code}'
        elif code.startswith('0') or code.startswith('3'):
            return f'sz{code}'
        return code

    def load_one(self, filepath: str) -> pd.DataFrame:
        """Load a single gpcw zip → DataFrame with mapped fields."""
        reader = HistoryFinancialReader()
        df = reader.get_df(filepath)
        if df is None:
            return None
        df = df.reset_index()
        fname = Path(filepath).stem
        report_date = fname.replace('gpcw', '')
        df['report_date'] = report_date

        keep = {'code': df['code'].astype(str).str.strip(),
                'report_date': df['report_date']}
        for name, col_idx in FIELD_MAP.items():
            if col_idx is not None:
                keep[name] = df[f'col{col_idx}'].astype(float)

        result = pd.DataFrame(keep)
        result['code'] = result['code'].apply(self._add_market_prefix)
        return result

    def load_all(self, min_size: int = 100_000) -> pd.DataFrame:
        """Load all available gpcw quarters → stacked panel."""
        files = sorted(self.cw_dir.glob("gpcw*.zip"))
        files = [f for f in files if f.stat().st_size > min_size]
        files = [f for f in files
                 if int(f.stem.replace('gpcw', '')[:4]) >= self.min_year]

        print(f"  Loading {len(files)} quarterly periods...")
        dfs = []
        for f in files:
            try:
                df = self.load_one(str(f))
                if df is not None:
                    dfs.append(df)
            except Exception:
                continue

        panel = pd.concat(dfs, ignore_index=True)

        # Derived factor
        panel['经营现金流/营收'] = (
            panel['经营现金流量净额'] / panel['营业总收入'].replace(0, np.nan) * 100
        )

        # Keep known columns
        raw_cols = [k for k, v in FIELD_MAP.items() if v is not None]
        keep_cols = ['code', 'report_date'] + raw_cols + ['经营现金流/营收']
        panel = panel[[c for c in keep_cols if c in panel.columns]]
        panel = panel.dropna(subset=['营业总收入'], how='any')

        self._panel_cache = panel
        return panel

    @property
    def panel(self) -> pd.DataFrame:
        if self._panel_cache is None:
            raise RuntimeError("call load_all() first")
        return self._panel_cache


# ══════════════════════════════════════════════════════════════════
# 2. CrossSectionProcessor
# ══════════════════════════════════════════════════════════════════

class CrossSectionProcessor:
    """Per-quarter: MAD winsorization → z-score standardization."""

    @staticmethod
    def mad_winsorize(series: pd.Series, k: float = 5.0) -> pd.Series:
        med = series.median()
        mad = (series - med).abs().median() * 1.4826
        if mad == 0 or pd.isna(mad):
            return series
        lower = med - k * mad
        upper = med + k * mad
        return series.clip(lower, upper)

    @classmethod
    def process(cls, df: pd.DataFrame, factor_cols: list) -> pd.DataFrame:
        """Return copy with z_{col} columns added."""
        result = df.copy()
        for col in factor_cols:
            raw = result[col].astype(float)
            w = cls.mad_winsorize(raw)
            mu, sigma = w.mean(), w.std()
            result[f'z_{col}'] = (w - mu) / sigma if sigma > 0 else 0.0
        return result


# ══════════════════════════════════════════════════════════════════
# 3. Forward returns helper
# ══════════════════════════════════════════════════════════════════

def _preload_day_cache(codes: set) -> dict:
    """Pre-load .day files → {code: (dates, closes)}."""
    import contextlib
    cache = {}
    for idx, code in enumerate(codes):
        if (idx + 1) % 500 == 0:
            print(f"    day-cache: {idx+1}/{len(codes)}...", flush=True)
        mkt = 'sh' if code.startswith('sh') else 'sz'
        raw = code[2:]
        path = VIPDOC / mkt / "lday" / f"{mkt}{raw}.day"
        if not path.exists():
            continue
        try:
            reader = TdxDailyBarReader()
            with open(os.devnull, 'w') as null, contextlib.redirect_stdout(null):
                df = reader.get_df(str(path))
            cache[code] = (pd.to_datetime(df.index), df['close'].astype(float))
        except Exception:
            continue
    return cache


def _compute_forward_returns(panel: pd.DataFrame, codes: set) -> pd.DataFrame:
    """Append forward_ret (3-month) to panel. Returns new DataFrame."""
    cache = _preload_day_cache(codes)
    print(f"  day-cache loaded: {len(cache)} stocks")

    result = panel.copy()
    ret_col = []
    for rd_str, grp in result.groupby('report_date'):
        rd_dt = pd.Timestamp(rd_str)
        start_dt = rd_dt + pd.offsets.MonthEnd(1)
        end_dt = start_dt + pd.DateOffset(months=3)

        for _, row in grp.iterrows():
            code = row['code']
            if code not in cache:
                ret_col.append(np.nan)
                continue
            dates, closes = cache[code]
            mask_start = dates >= start_dt
            mask_end = dates >= end_dt
            if mask_start.any() and mask_end.any():
                p0 = closes[mask_start].iloc[0]
                p1 = closes[mask_end].iloc[0]
                ret_col.append((p1 - p0) / p0 * 100)
            else:
                ret_col.append(np.nan)
    result['forward_ret'] = ret_col
    return result.dropna(subset=['forward_ret'])


# ══════════════════════════════════════════════════════════════════
# 4. RollingFamaMacBeth
# ══════════════════════════════════════════════════════════════════

class RollingFamaMacBeth:
    """
    Rolling Fama-MacBeth regression.

    Window = 12 quarters, rolling 1 quarter at a time.
    Output: factor premium time series.
    """

    def __init__(self, window: int = 12):
        self.window = window
        self.premium_series_ = None   # DataFrame: index=window_end, cols=factors
        self.n_obs_series_ = None     # Series: n_obs per window
        self.r2_series_ = None        # Series: avg R² per window

    def fit(self, panel: pd.DataFrame, factor_keys: list = FACTOR_KEYS):
        """Run rolling FM on panel with forward_ret already computed."""
        csp = CrossSectionProcessor()
        dates = sorted(panel['report_date'].unique())
        print(f"  Rolling FM: {len(dates)} quarters, window={self.window}")

        cols_z = [f'z_{k}' for k in factor_keys]
        records = []
        nobs_list = []
        r2_list = []

        for i in range(self.window, len(dates) + 1):
            win_dates = dates[i - self.window:i]
            win_panel = panel[panel['report_date'].isin(win_dates)].copy()

            # Per-quarter winsorization+zscore
            processed = []
            for rd, grp in win_panel.groupby('report_date'):
                grp = csp.process(grp, factor_keys)
                processed.append(grp)
            win_panel = pd.concat(processed, ignore_index=True)

            # FM regression
            fm_data = win_panel[['code', 'report_date', 'forward_ret'] + cols_z].dropna()
            fm_data = fm_data.rename(columns={'forward_ret': 'ret',
                                              'report_date': 'date',
                                              'code': 'entity'})
            fm_data['date'] = pd.to_datetime(fm_data['date'])
            fm_data = fm_data.set_index(['entity', 'date'])

            if len(fm_data) < 100:
                print(f"    window ending {dates[i-1]}: only {len(fm_data)} obs, skip")
                records.append({k: np.nan for k in factor_keys})
                nobs_list.append(0)
                r2_list.append(np.nan)
                continue

            y = fm_data['ret']
            X = fm_data[cols_z]
            try:
                X_const = sm.add_constant(X)
                model = FamaMacBeth(y, X_const)
                res = model.fit(cov_type='robust')
                rec = {k: res.params.get(f'z_{k}', np.nan) for k in factor_keys}
                records.append(rec)
                nobs_list.append(res.nobs)
                r2_list.append(res.rsquared if hasattr(res, 'rsquared') else np.nan)
            except Exception as e:
                print(f"    window ending {dates[i-1]} failed: {e}")
                records.append({k: np.nan for k in factor_keys})
                nobs_list.append(0)
                r2_list.append(np.nan)

            if (i - self.window) % 4 == 0:
                print(f"    {dates[i-1]}: nobs={nobs_list[-1]}, r²={r2_list[-1]:.3f}")

        self.premium_series_ = pd.DataFrame(
            records, index=dates[self.window - 1:])
        self.premium_series_.index.name = 'window_end'
        self.n_obs_series_ = pd.Series(nobs_list, index=dates[self.window - 1:])
        self.r2_series_ = pd.Series(r2_list, index=dates[self.window - 1:])

        n_valid = self.n_obs_series_.gt(0).sum()
        print(f"  Done: {n_valid} valid windows, "
              f"avg R^2={self.r2_series_.mean():.3f}")
        return self


# ══════════════════════════════════════════════════════════════════
# 5. FactorMomentum
# ══════════════════════════════════════════════════════════════════

class FactorMomentum:
    """
    Factor momentum signals from premium time series.

    TSMOM: factor's own premium increasing for N consecutive periods.
    CSMOM: cross-sectional ranking of factor premiums.
    """

    def __init__(self, tsmom_window: int = 2):
        self.tsmom_window = tsmom_window
        self.signals_ = None  # DataFrame with TSMOM/CSMOM columns

    def fit(self, premium_series: pd.DataFrame):
        """
        premium_series: index=window_end, cols=factor_names, values=premiums
        """
        ps = premium_series.dropna(how='all')
        if len(ps) < self.tsmom_window + 1:
            raise ValueError(f"Need ≥{self.tsmom_window+1} rows, got {len(ps)}")

        result = pd.DataFrame(index=ps.index)

        # TSMOM: premium连续上升的因子数
        diff = ps.diff()
        up_flags = diff.rolling(self.tsmom_window).min() > 0  # all N periods positive
        result['tsmom_up_count'] = up_flags.sum(axis=1)
        result['tsmom_up_factors'] = up_flags.apply(
            lambda row: ','.join([k for k in ps.columns if row.get(k)]), axis=1)

        # CSMOM: rank premiums → top/bottom
        ranks = ps.rank(axis=1, ascending=False)
        result['csmom_top_factor'] = ranks.idxmin(axis=1)   # rank=1 → highest premium
        result['csmom_bottom_factor'] = ranks.idxmax(axis=1)  # lowest premium

        # Direction-weighted composite: which factors are "hot" right now
        # (positive premium + rising)
        positive = ps > 0
        rising = diff > 0
        result['hot_factors'] = (positive & rising).apply(
            lambda row: ','.join([k for k in ps.columns if row.get(k)]), axis=1)

        self.signals_ = result
        return self

    @property
    def latest(self) -> dict:
        """Return latest signal dict."""
        if self.signals_ is None or len(self.signals_) == 0:
            return {}
        last = self.signals_.iloc[-1]
        return {
            'tsmom_up_count': int(last['tsmom_up_count']),
            'tsmom_up_factors': str(last['tsmom_up_factors']),
            'csmom_top_factor': str(last['csmom_top_factor']),
            'csmom_bottom_factor': str(last['csmom_bottom_factor']),
            'hot_factors': str(last['hot_factors']),
        }


# ══════════════════════════════════════════════════════════════════
# 6. Full pipeline convenience
# ══════════════════════════════════════════════════════════════════

def run_rolling_fm(panel: pd.DataFrame = None,
                   window: int = 12,
                   factor_keys: list = None) -> dict:
    """
    Run the full Rolling FM pipeline.

    Returns dict with:
      - premium_series: list of {window_end, factor_premiums}
      - momentum: latest TSMOM/CSMOM signals
      - current_scores: latest cross-section scores for universe stocks
    """
    from config import NAME_MAP

    if factor_keys is None:
        factor_keys = FACTOR_KEYS

    # 1. Load if not provided
    if panel is None:
        loader = GpcwLoader()
        panel = loader.load_all()
    else:
        panel = panel.copy()

    print(f"\n  Panel: {len(panel)} obs, {panel['code'].nunique()} stocks, "
          f"{panel['report_date'].nunique()} periods")

    # 2. Forward returns (universe stocks for speed)
    codes = set(NAME_MAP.keys())
    panel_fm = _compute_forward_returns(panel, codes)
    print(f"  With forward_ret: {len(panel_fm)} obs")

    # 3. Rolling FM
    rfm = RollingFamaMacBeth(window=window)
    rfm.fit(panel_fm, factor_keys)

    # 4. Factor momentum
    fmom = FactorMomentum()
    fmom.fit(rfm.premium_series_)

    # 5. Current cross-section scores (latest quarter)
    latest_date = sorted(panel['report_date'].unique())[-1]
    csp = CrossSectionProcessor()
    latest = panel[panel['report_date'] == latest_date].copy()
    latest = csp.process(latest, factor_keys)

    # Direction-corrected z-scores
    for k in factor_keys:
        latest[f'd_{k}'] = latest[f'z_{k}'] * FACTOR_DEFS[k]['direction']

    # 6. Assemble result
    premium_list = []
    for idx, row in rfm.premium_series_.iterrows():
        rec = {'window_end': str(idx)}
        for k in factor_keys:
            val = row.get(k)
            rec[k] = round(float(val), 4) if pd.notna(val) else None
        premium_list.append(rec)

    # Current scores for tracked stocks
    current_scores = {}
    score_cols = ['code'] + [f'z_{k}' for k in factor_keys] + [f'd_{k}' for k in factor_keys]
    for _, row in latest[latest['code'].isin(NAME_MAP.keys())].iterrows():
        code = row['code']
        entry = {'name': NAME_MAP.get(code, '')}
        for k in factor_keys:
            entry[f'z_{k}'] = round(float(row[f'z_{k}']), 3) if pd.notna(row[f'z_{k}']) else None
            entry[f'd_{k}'] = round(float(row[f'd_{k}']), 3) if pd.notna(row[f'd_{k}']) else None
        current_scores[code] = entry

    return {
        'update_time': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'config': {
            'window': window,
            'n_periods': int(panel['report_date'].nunique()),
            'n_stocks': int(panel['code'].nunique()),
            'factors': factor_keys,
            'latest_report': latest_date,
        },
        'premium_series': premium_list,
        'momentum': fmom.latest,
        'current_scores': current_scores,
    }


if __name__ == '__main__':
    print("=" * 55)
    print("  Rolling Fama-MacBeth Pipeline")
    print("=" * 55)
    result = run_rolling_fm()

    print("\n  ─── Latest Factor Premiums ───")
    if result['premium_series']:
        last = result['premium_series'][-1]
        print(f"  Window: {last['window_end']}")
        for k in FACTOR_KEYS:
            v = last.get(k)
            print(f"    {k}: {v}")
    print(f"\n  Momentum: {result['momentum']}")
    print(f"  Latest scores: {len(result['current_scores'])} stocks")
    print("=" * 55)

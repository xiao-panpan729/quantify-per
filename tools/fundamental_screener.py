# -*- coding: utf-8 -*-
"""
Fundamental Screener v0.1 — A-share fundamental factor testing pipeline
========================================================================

Two-layer approach:
  Layer 1 — Fama-MacBeth regression across history → factor premiums
  Layer 2 — Current cross-sectional factor scores → stock-level quality

Pipeline:
  1. Load pytdx gpcw quarterly financial data (2021~2026)
  2. Construct factors: ROE, 毛利率, 营收增长率, 净利润增长率, 资产负债率
  3. Per-period: MAD winsorization → z-score standardization
  4. Fama-MacBeth: regress R_{t+1} ~ factors (cross-section, then time-series avg)
  5. Output: factor premium table + current fundamental scores

Usage:
  python tools/fundamental_screener.py                  # full pipeline
  python tools/fundamental_screener.py --factors-only    # just latest scores
  python tools/fundamental_screener.py --fm-only         # just FM regression
"""

import sys, json, warnings, time, os
from pathlib import Path
# Ensure project root is on sys.path for config import
_proj_root = Path(__file__).resolve().parent.parent
if str(_proj_root) not in sys.path:
    sys.path.insert(0, str(_proj_root))

# Windows GBK console workaround
if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from pytdx.reader import HistoryFinancialReader
from pytdx.reader import TdxDailyBarReader
from scipy.stats import mstats
from linearmodels import FamaMacBeth
import statsmodels.api as sm

from config import NAME_MAP

SIGNALS_DIR = Path(__file__).parent.parent / "signals" / "tracking"
VIPDOC = Path("C:/zd_cjzq/vipdoc")
CW_DIR = VIPDOC / "cw"

# ══════════════════════════════════════════════════════════════
# Field mapping (pytdx gpcw col index → indicator name)
# Verified by cross-referencing akshare stock_financial_abstract
# ══════════════════════════════════════════════════════════════

FIELD_MAP = {
    '基本每股收益': 1,         # col1  — per share earnings
    '每股净资产': 4,           # col4  — book value per share
    '营业成本': 17,            # col17 — cost of revenue
    '股东权益合计': 72,        # col72 — total equity
    '营业总收入': 74,          # col74 — total revenue (slight mismatch with akshare)
    '净利润': 95,              # col95 — net profit
    '归母净利润': 96,          # col96 — net profit attr. to parent
    '经营现金流量净额': 107,   # col107 — operating cash flow
    '营收增长率': 183,         # col183 — revenue growth (YoY, %)
    '净利润增长率': 184,       # col184 — profit growth (YoY, %)
    '期间费用率': 196,         # col196 — expense ratio (%)
    '销售净利率': 199,         # col199 — net profit margin (%)
    '毛利率': 202,             # col202 — gross margin (%)
    '扣非净利润': 206,         # col206 — recurring profit
    '资产负债率': 210,         # col210 — debt-to-asset ratio (%)
    '净资产收益率(ROE)': 281,  # col281 — ROE (%)
}

# Factor definitions for FM regression
FACTOR_DEFS = {
    '净资产收益率(ROE)': {'col': 281, 'label': '净资产收益率(ROE)', 'direction': +1, 'short': 'ROE'},
    '毛利率':            {'col': 202, 'label': '毛利率', 'direction': +1, 'short': '毛利率'},
    '营收增长率':         {'col': 183, 'label': '营收增长率', 'direction': +1, 'short': '营收增长'},
    '净利润增长率':        {'col': 184, 'label': '净利润增长率', 'direction': +1, 'short': '净利增长'},
    '资产负债率':         {'col': 210, 'label': '资产负债率', 'direction': -1, 'short': '负债率'},
    '经营现金流/营收':     {'col': None, 'label': '经营现金流/营收', 'direction': +1, 'short': '现金流/营收'},
}


def load_gpcw_period(filepath: str) -> pd.DataFrame:
    """Load a single gpcw zip file, return DataFrame with factor columns"""
    reader = HistoryFinancialReader()
    df = reader.get_df(filepath)
    if df is None:
        return None
    df = df.reset_index()

    # Extract report date from filename
    fname = Path(filepath).stem  # e.g. gpcw20241231
    report_date = fname.replace('gpcw', '')
    df['report_date'] = report_date

    # Keep only code, report_date + our raw field columns
    keep = {'code': df['code'], 'report_date': df['report_date']}
    for name, col_idx in FIELD_MAP.items():
        if col_idx is not None:
            keep[name] = df[f'col{col_idx}'].astype(float)

    result = pd.DataFrame(keep)
    # Ensure code is clean string
    result['code'] = result['code'].astype(str).str.strip()
    # Add market prefix to match NAME_MAP keys (sh600438, sz000100, etc.)
    result['code'] = result['code'].apply(_add_market_prefix)
    return result


def _add_market_prefix(code: str) -> str:
    """Add market prefix to stock code: 6xxxxx→sh, others→sz"""
    if code.startswith('6'):
        return f'sh{code}'
    elif code.startswith('0') or code.startswith('3'):
        return f'sz{code}'
    return code  # fallback for unknown (4xxxxx, 8xxxxx etc.)


def load_all_periods(min_size: int = 100_000) -> pd.DataFrame:
    """Load available gpcw zip files, filter meaningful ones, stack into panel DataFrame"""
    files = sorted(CW_DIR.glob("gpcw*.zip"))

    # Filter by size: skip tiny/empty files
    files = [f for f in files if f.stat().st_size > min_size]
    # Only keep last ~5 years (20 quarters)
    files = [f for f in files if int(f.stem.replace('gpcw', '')[:4]) >= 2021]

    print(f"  Loading {len(files)} quarterly periods (2021~)...")

    dfs = []
    for f in files:
        try:
            df = load_gpcw_period(str(f))
            if df is not None:
                dfs.append(df)
        except Exception:
            continue

    panel = pd.concat(dfs, ignore_index=True)

    # Add derived factors
    panel['经营现金流/营收'] = (
        panel['经营现金流量净额'] / panel['营业总收入'].replace(0, np.nan) * 100
    )

    # Keep columns needed: raw fields + derived + metadata
    factor_raw = [k for k, v in FIELD_MAP.items() if v is not None]
    keep_cols = ['code', 'report_date'] + factor_raw + ['经营现金流/营收']
    panel = panel[[c for c in keep_cols if c in panel.columns]]

    return panel


def mad_winsorize(series: pd.Series, k: float = 5.0) -> pd.Series:
    """MAD winsorization: cap at median ± k * MAD"""
    med = series.median()
    mad = (series - med).abs().median() * 1.4826  # MAD → σ scale
    if mad == 0 or pd.isna(mad):
        return series
    lower = med - k * mad
    upper = med + k * mad
    return series.clip(lower, upper)


def process_cross_section(df: pd.DataFrame, factor_cols: list) -> pd.DataFrame:
    """Per-period processing: MAD winsorize → z-score standardize"""
    result = df.copy()
    for col in factor_cols:
        raw = result[col]
        # Winsorize
        w = mad_winsorize(raw)
        # Z-score
        mu, sigma = w.mean(), w.std()
        result[f'z_{col}'] = (w - mu) / sigma if sigma > 0 else 0.0
        # Direction-corrected score
        result[f's_{col}'] = result[f'z_{col}']
    return result


def _load_stock_cache(codes: set) -> dict:
    """Pre-load all .day files into a cache: {code: (dates, closes)}"""
    import contextlib
    cache = {}
    for idx, code in enumerate(codes):
        if (idx + 1) % 500 == 0:
            print(f"    progress: {idx+1}/{len(codes)}...", flush=True)
        mkt = 'sh' if code.startswith('sh') or code.startswith('6') else 'sz'
        raw = code[2:] if code.startswith(('sh', 'sz')) else code
        path = VIPDOC / mkt / "lday" / f"{mkt}{raw}.day"
        if not path.exists():
            continue
        try:
            reader = TdxDailyBarReader()
            with open(os.devnull, 'w') as null, contextlib.redirect_stdout(null):
                df = reader.get_df(str(path))
            closes = df['close'].astype(float)
            dates = pd.to_datetime(df.index)
            cache[code] = (dates, closes)
        except Exception:
            continue
    return cache


def calculate_returns(panel: pd.DataFrame, universe_only: bool = True) -> pd.DataFrame:
    """Calculate forward 3-month return for each stock-period."""
    if universe_only:
        codes = set(NAME_MAP.keys())
        panel = panel[panel['code'].isin(codes)].copy()
    codes = set(panel['code'].unique())
    print(f"  {len(codes)} stocks, {len(panel)} obs")

    print("  Loading .day files...")
    cache = _load_stock_cache(codes)
    print(f"  Loaded {len(cache)} stocks")

    report_dates = sorted(panel['report_date'].unique())
    date_to_ret = {}

    for rd in report_dates:
        rd_dt = pd.Timestamp(rd)
        start_dt = rd_dt + pd.offsets.MonthEnd(1)
        end_dt = start_dt + pd.DateOffset(months=3)

        codes_in_period = panel[panel['report_date'] == rd]['code'].unique()
        rets = {}
        for code in codes_in_period:
            if code not in cache:
                continue
            dates, closes = cache[code]
            mask_start = dates >= start_dt
            mask_end = dates >= end_dt
            if mask_start.any() and mask_end.any():
                p0 = closes[mask_start].iloc[0]
                p1 = closes[mask_end].iloc[0]
                rets[code] = (p1 - p0) / p0 * 100
        date_to_ret[rd] = rets

    ret_col = []
    for _, row in panel.iterrows():
        rd = row['report_date']
        code = row['code']
        rets = date_to_ret.get(rd, {})
        ret_col.append(rets.get(code, np.nan))

    panel['forward_ret'] = ret_col
    return panel.dropna(subset=['forward_ret'])


def run_fama_macbeth(panel: pd.DataFrame, factor_cols: list):
    """Run Fama-MacBeth regression: R_{t+1} = α + Σ β_i * factor_i + ε"""
    print(f"\n  Fama-MacBeth regression ({len(factor_cols)} factors)...")

    # Prepare panel data for linearmodels
    fm_data = panel[['code', 'report_date', 'forward_ret'] + [f'z_{c}' for c in factor_cols]].dropna()
    fm_data = fm_data.rename(columns={'forward_ret': 'ret',
                                        'report_date': 'date',
                                        'code': 'entity'})
    fm_data['date'] = pd.to_datetime(fm_data['date'])
    fm_data = fm_data.set_index(['entity', 'date'])

    if len(fm_data) < 100:
        print(f"    [WARN] only {len(fm_data)} obs, too few for FM")
        return None, None

    y = fm_data['ret']
    X = fm_data[[f'z_{c}' for c in factor_cols]]

    try:
        X_const = sm.add_constant(X)
        model = FamaMacBeth(y, X_const)
        res = model.fit(cov_type='robust')

        print(f"\n  ─── Fama-MacBeth Results ───")
        print(f"  {'Factor':20s} {'Premium':>8s} {'t-stat':>8s} {'p-value':>8s} {'Signif':>8s}")
        print(f"  {'─'*52}")
        results = {}
        for name, stat in res.params.items():
            fname = name.replace('z_', '')
            tstat = res.tstats[name]
            pval = res.pvalues[name]
            sig = '*' if pval < 0.1 else '**' if pval < 0.05 else '***' if pval < 0.01 else ''
            print(f"  {fname:20s} {stat:>+8.4f} {tstat:>+8.3f} {pval:>8.4f} {sig:>8s}")
            results[fname] = {'premium': round(stat, 4), 'tstat': round(tstat, 3),
                              'pvalue': round(pval, 4)}

        print(f"  {'─'*52}")
        print(f"  R² (avg): {res.rsquared:.4f}")
        print(f"  Periods: {res.nobs}")

        return results, res

    except Exception as e:
        print(f"    FM regression error: {e}")
        return None, None


def score_current_period(panel: pd.DataFrame, factor_cols: list) -> pd.DataFrame:
    """Score stocks in the most recent period"""
    latest_date = sorted(panel['report_date'].unique())[-1]
    latest = panel[panel['report_date'] == latest_date].copy()

    if len(latest) == 0:
        return None

    # Process cross-section
    latest = process_cross_section(latest, factor_cols)

    # Combined score (direction-weighted average of z-scores)
    # Apply direction: multiply z-score by direction, positive=good
    dir_cols = []
    for c in factor_cols:
        dc = f'd_{c}'
        latest[dc] = latest[f'z_{c}'] * FACTOR_DEFS[c]['direction']
        dir_cols.append(dc)

    latest['fundamental_score'] = latest[dir_cols].mean(axis=1)

    # Return z-scores AND direction-corrected scores
    cols = ['code'] + [f'z_{c}' for c in factor_cols] + dir_cols + ['fundamental_score']
    return latest[cols]


def main():
    t0 = time.time()

    print("=" * 55)
    print("  Fundamental Screener v0.1 — A-share 基本面因子检验")
    print("=" * 55)

    factor_cols = list(FACTOR_DEFS.keys())
    fast_mode = '--factors-only' in sys.argv

    # 1. Load data
    print("\n[1/4] Loading pytdx gpcw financial data...")
    panel = load_all_periods()
    print(f"  Panel: {len(panel)} obs, {panel['code'].nunique()} stocks, "
          f"{panel['report_date'].nunique()} periods")

    # 2. Cross-sectional processing (per period)
    print("\n[2/4] Cross-sectional factor processing...")
    processed = []
    for rd, grp in panel.groupby('report_date'):
        grp = process_cross_section(grp, factor_cols)
        processed.append(grp)
    panel = pd.concat(processed, ignore_index=True)
    print(f"  Processed {panel['report_date'].nunique()} periods")

    # 3. Forward returns + FM regression (skip in factors-only mode)
    fm_results = None
    if not fast_mode:
        print("\n[3/4] Forward returns (full A-share)...")
        panel_fm = calculate_returns(panel, universe_only=False)
        print(f"  {len(panel_fm)} obs with forward returns ({panel_fm['code'].nunique()} stocks)")

        print("\n[4/4] Fama-MacBeth regression...")
        fm_results, _ = run_fama_macbeth(panel_fm, factor_cols)
    else:
        print("\n[3/4] Skipped (--factors-only mode)")
        print("[4/4] Skipped")

    # 5. Current period scores
    print("\n  ─── Current Period Scores (tracking universe) ───")
    scores = score_current_period(panel, factor_cols)
    if scores is not None:
        latest_date = sorted(panel['report_date'].unique())[-1]
        print(f"  Latest report: {latest_date}")
        shorts = [FACTOR_DEFS[c]['short'] for c in factor_cols]
        print(f"  {'Code':10s} {'Name':12s} {'Score':>7s}  ", end='')
        for s in shorts:
            print(f'{s:>8s}', end=' ')
        print()
        print(f"  {'─'*10} {'─'*12} {'─'*7}  ", end='')
        for _ in shorts:
            print(f"{'─'*8}", end=' ')
        print()

        for code, name in sorted(NAME_MAP.items()):
            if code in scores['code'].values:
                row = scores[scores['code'] == code].iloc[0]
                fs = f'{row["fundamental_score"]:+.3f}'
                zs = [f'{row[f"d_{c}"]:+.3f}' for c in factor_cols]
                print(f'  {code:10s} {name:12s} {fs:>7s}  {" ".join(f"{z:>8s}" for z in zs)}')
            else:
                print(f'  {code:10s} {name:12s} {"  N/A":>7s}')

    # 6. Save
    out = {
        'update_time': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'config': {'n_periods': int(panel['report_date'].nunique()),
                    'n_stocks': int(panel['code'].nunique()),
                    'factors': factor_cols,
                    'latest_report': sorted(panel['report_date'].unique())[-1]},
    }
    if fm_results:
        out['fama_macbeth'] = fm_results

    out['latest_scores'] = {}
    if scores is not None:
        for _, row in scores.iterrows():
            entry = {
                'name': NAME_MAP.get(row['code'], ''),
                'fundamental_score': round(float(row['fundamental_score']), 4),
            }
            for c in factor_cols:
                entry[c] = round(float(row[f'z_{c}']), 3)
                entry[f'd_{c}'] = round(float(row[f'd_{c}']), 3)
            out['latest_scores'][row['code']] = entry

    out_path = SIGNALS_DIR / "fundamental_scores.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n  -> saved: {out_path}")

    elapsed = time.time() - t0
    print(f"\n  Done in {elapsed:.1f}s")
    print("=" * 55)


if __name__ == '__main__':
    main()

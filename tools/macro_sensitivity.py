# -*- coding: utf-8 -*-
"""
Macro Sensitivity v0.5 — Cleaned factor set + standardized inputs
=======================================================================

Data: akshare (M2/SHIBOR/CPI/PMI — 社融 dropped, near-zero signal)
Sectors: pytdx 880xxx concept indices
Model: statsmodels RollingOLS (window=36mo, lags=2)

Key change v0.5: factors are z-score standardized before regression,
making coefficients directly comparable across factors
("sector return change per 1σ of macro factor shift").

Usage:
  python tools/macro_sensitivity.py --sectors 5     # test 5 sectors
  python tools/macro_sensitivity.py --show-beta     # show latest betas
  python tools/macro_sensitivity.py                 # all 604 sectors
"""

import sys, argparse, json, time, warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import akshare as ak
from pytdx.reader import TdxDailyBarReader
from statsmodels.regression.rolling import RollingOLS
import statsmodels.api as sm

VIPDOC = Path("C:/zd_cjzq/vipdoc")
HQ_CACHE = Path("C:/zd_cjzq/T0002/hq_cache")
SIGNALS_DIR = Path(__file__).parent.parent / "signals" / "tracking"


# ══════════════════════════════════════════════════════════════
# Macro factors
# ══════════════════════════════════════════════════════════════

def fetch_macro() -> pd.DataFrame:
    """Fetch China macro factors, return DataFrame(date, M2, SHIBOR, CPI, PMI)"""
    print("  [akshare] fetching macro data...")

    # M2 YoY (monthly, 580 rows, 1978~2026, reverse order)
    df = ak.macro_china_supply_of_money()
    m2 = df.iloc[::-1, [0, 2]].copy()
    m2.columns = ['ds', 'M2']
    m2['ds'] = pd.to_datetime(m2['ds'].astype(str)
                               .str.replace('.', '-', n=1, regex=False),
                               errors='coerce')
    m2 = m2.dropna(subset=['ds'])
    m2['M2'] = pd.to_numeric(m2['M2'], errors='coerce')

    # SHIBOR overnight (daily, 2296 rows, 2015~2026)
    df = ak.macro_china_shibor_all()
    shibor = df.iloc[:, [0, 1]].copy()
    shibor.columns = ['ds', 'SHIBOR']
    shibor['ds'] = pd.to_datetime(shibor['ds'])
    shibor['SHIBOR'] = pd.to_numeric(shibor['SHIBOR'], errors='coerce')

    # CPI YoY (monthly, 357 rows, 1996~2025)
    df = ak.macro_china_cpi_monthly()
    cpi = df.iloc[:, [1, 2]].copy()
    cpi.columns = ['ds', 'CPI']
    cpi['ds'] = pd.to_datetime(cpi['ds'])
    cpi['CPI'] = pd.to_numeric(cpi['CPI'], errors='coerce')

    # PMI manufacturing (monthly, 221 rows, 2008~2026, reverse order)
    df = ak.macro_china_pmi()
    pmi = df.iloc[::-1, [0, 1]].copy()
    pmi.columns = ['ds', 'PMI']
    pmi['ds'] = pd.to_datetime(
        pmi['ds'].astype(str)
        .str.replace('年', '-')   # year
        .str.replace('月', '')     # month
        .str.replace('份', ''),    # (suffix)
        errors='coerce')
    pmi = pmi.dropna(subset=['ds'])
    pmi['PMI'] = pd.to_numeric(pmi['PMI'], errors='coerce')

    # SHIBOR -> monthly (last value of each month)
    shibor_m = shibor.set_index('ds').resample('ME').last().reset_index()

    # Merge: align by month-end, deduplicate
    monthly = {}
    for label, src in [('M2', m2), ('CPI', cpi), ('PMI', pmi)]:
        d = src[['ds', label]].dropna(subset=[label]).copy()
        d['date'] = d['ds'] + pd.offsets.MonthEnd(0)
        monthly[label] = d.groupby('date')[label].last()

    d = shibor_m[['ds', 'SHIBOR']].copy()
    d['date'] = d['ds'] + pd.offsets.MonthEnd(0)
    monthly['SHIBOR'] = d.groupby('date')['SHIBOR'].last()

    combined = pd.DataFrame(monthly).sort_index()
    combined = combined.ffill().dropna()
    return combined


# ══════════════════════════════════════════════════════════════
# Japan macro factors (carry trade channel)
# ══════════════════════════════════════════════════════════════

def fetch_japan_macro() -> pd.DataFrame | None:
    """Fetch Japan factors via japan_macro module, return month-end aligned DataFrame"""
    try:
        from tools.japan_macro import build_japan_macro_df, compute_carry_pressure
        df = build_japan_macro_df()
        df = compute_carry_pressure(df)
        # Select key columns for regression
        cols = ['BOJ_rate', 'JP_CPI', 'carry_pressure', 'carry_regime']
        available = [c for c in cols if c in df.columns]
        return df[available].copy()
    except Exception as e:
        print(f"  [WARN] Japan macro fetch failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# P0: China bond yield + FX rate
# P1: Commodity prices (oil/copper/gold)
# ══════════════════════════════════════════════════════════════

SNAPSHOT_PATH = SIGNALS_DIR / "macro_snapshot.json"


def fetch_china_bond_fx() -> dict:
    """Fetch China 10Y govt bond yield + USD/CNY exchange rate.

    Returns dict of latest values, or empty dict on failure.
    """
    result = {}
    try:
        # 中国国债收益率曲线
        df = ak.bond_china_yield()
        if df is not None and len(df) > 0:
            # Find 10Y yield — column name varies, try common patterns
            for col in df.columns:
                col_s = str(col)
                if '10' in col_s and ('年' in col_s or 'Y' in col_s.upper()):
                    val = pd.to_numeric(df[col].iloc[-1], errors='coerce')
                    if pd.notna(val):
                        result['cn10y'] = round(val, 2)
                        break
            if 'cn10y' not in result:
                # Fallback: use last row's first numeric column
                for col in df.columns:
                    val = pd.to_numeric(df[col].iloc[-1], errors='coerce')
                    if pd.notna(val):
                        result['cn10y'] = round(val, 2)
                        break
    except Exception as e:
        print(f"  [WARN] bond yield fetch failed: {e}")

    try:
        # 美元兑人民币汇率 (中间价)
        df = ak.currency_cny_spot()
        if df is not None and len(df) > 0:
            # Find USD/CNY column
            for col in df.columns:
                col_s = str(col)
                if '美元' in col_s or 'USD' in col_s.upper():
                    val = pd.to_numeric(df[col].iloc[-1], errors='coerce')
                    if pd.notna(val):
                        result['usd_cny'] = round(val, 4)
                        break
    except Exception as e:
        print(f"  [WARN] FX fetch failed: {e}")

    return result


def fetch_commodity_prices() -> dict:
    """Fetch crude oil (WTI), copper, gold benchmark prices.

    Returns dict of latest values, or empty dict on failure.
    """
    result = {}
    try:
        # WTI crude oil futures (main contract)
        df = ak.futures_foreign_hist(symbol="NYMEX", month="spot")
        if df is not None and len(df) > 0:
            wti = df[df['商品名称'].str.contains('WTI|原油', case=False, na=False)] if '商品名称' in df.columns else df
            val = pd.to_numeric(wti['收盘'].iloc[-1], errors='coerce') if '收盘' in wti.columns else None
            if pd.notna(val):
                result['oil'] = round(val, 2)
    except Exception as e:
        print(f"  [WARN] oil fetch failed: {e}")

    try:
        # Gold futures
        df = ak.futures_foreign_hist(symbol="COMEX", month="spot")
        if df is not None and len(df) > 0:
            au = df[df['商品名称'].str.contains('黄金|GOLD|gold|GC', case=False, na=False)] if '商品名称' in df.columns else df
            val = pd.to_numeric(au['收盘'].iloc[-1], errors='coerce') if '收盘' in au.columns else None
            if pd.notna(val):
                result['gold'] = round(val, 2)
    except Exception as e:
        print(f"  [WARN] gold fetch failed: {e}")

    try:
        # Copper futures
        df = ak.futures_foreign_hist(symbol="COMEX", month="spot")
        if df is not None and len(df) > 0:
            cu = df[df['商品名称'].str.contains('铜|COPPER|copper|HG', case=False, na=False)] if '商品名称' in df.columns else df
            val = pd.to_numeric(cu['收盘'].iloc[-1], errors='coerce') if '收盘' in cu.columns else None
            if pd.notna(val):
                result['copper'] = round(val, 2)
    except Exception as e:
        print(f"  [WARN] copper fetch failed: {e}")

    # Fallback: try akshare alternative for Chinese commodity prices
    if not result:
        try:
            # 商品现货价格指数
            df = ak.spot_goods()
            if df is not None and len(df) > 0:
                for keyword, key in [('原油', 'oil'), ('铜', 'copper'), ('黄金', 'gold')]:
                    row = df[df['名称'].str.contains(keyword, na=False)] if '名称' in df.columns else None
                    if row is not None and len(row) > 0:
                        val = pd.to_numeric(row['价格'].iloc[-1], errors='coerce') if '价格' in row.columns else None
                        if pd.notna(val):
                            result[key] = round(val, 2)
        except Exception:
            pass

    return result


def merge_japan_factors(macro: pd.DataFrame, japan: pd.DataFrame) -> pd.DataFrame:
    """Merge Japan factors into macro DataFrame on month-end dates"""
    combined = macro.copy()
    for col in japan.columns:
        combined[col] = japan[col]
    # Forward-fill Japan factors (they don't change every month)
    jp_cols = [c for c in japan.columns]
    combined[jp_cols] = combined[jp_cols].ffill()
    # Drop rows where Japan data not yet available
    combined = combined.dropna(subset=[c for c in jp_cols if c in combined.columns])
    return combined


# ══════════════════════════════════════════════════════════════
# Macro environment classifier
# ══════════════════════════════════════════════════════════════

def load_sentiment_shock() -> dict | None:
    """Read B-class shock detection output"""
    path = SIGNALS_DIR / "sentiment_shock.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_liquidity() -> dict | None:
    """Read liquidity monitor output"""
    path = SIGNALS_DIR / "liquidity_monitor.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def classify_environment(macro: pd.DataFrame) -> dict:
    """Classify current macro environment for overlay filtering.

    Returns environment label (宽松/中性/收紧) + directional hints for
    sector selection.
    """
    latest = macro.iloc[-1]
    env = {}

    # M2 YoY
    m2 = latest['M2']
    if m2 > 10:
        env['M2'] = +1  # 宽松
    elif m2 > 8:
        env['M2'] = 0
    else:
        env['M2'] = -1  # 收紧

    # SHIBOR overnight (inverted: low = loose)
    shibor = latest['SHIBOR']
    if shibor < 1.5:
        env['SHIBOR'] = +1
    elif shibor < 2.5:
        env['SHIBOR'] = 0
    else:
        env['SHIBOR'] = -1

    # CPI YoY (inverted: low = loose → deflation risk → easing)
    cpi = latest['CPI']
    if cpi < 1:
        env['CPI'] = +1
    elif cpi < 3:
        env['CPI'] = 0
    else:
        env['CPI'] = -1

    # PMI
    pmi = latest['PMI']
    if pmi > 52:
        env['PMI'] = +1
    elif pmi > 48:
        env['PMI'] = 0
    else:
        env['PMI'] = -1

    # Japan carry trade pressure (if available)
    carry_pressure = latest.get('carry_pressure', 0) if 'carry_pressure' in latest.index else 0
    carry_regime = latest.get('carry_regime', 'unknown') if 'carry_regime' in latest.index else 'unknown'
    if 'BOJ_rate' in latest.index:
        boj_rate = latest['BOJ_rate']
        if boj_rate >= 0.5:
            env['BOJ'] = -1  # BOJ tightening → liquidity drain
        elif boj_rate >= 0.25:
            env['BOJ'] = 0
        else:
            env['BOJ'] = +1

    total = sum(env.values())
    # Include carry pressure in total judgment
    if carry_pressure > 0.2:
        total -= 1  # carry unwind → tighten overlay
    elif carry_pressure < -0.2:
        total += 1  # carry easing → loosen overlay

    # Sentiment shock overlay (B-class event detection)
    shock = load_sentiment_shock()
    shock_impact = shock.get("net_impact", 0) if shock else 0
    shock_label = shock.get("impact_level", "no_data") if shock else "no_data"
    if shock_impact <= -2:
        total -= 1  # negative shock → tighten overlay
    elif shock_impact >= 2:
        total += 1  # positive shock → loosen overlay

    # Liquidity pressure overlay (global: BTC/VIX/DXY/M2/Credit Impulse)
    liq = load_liquidity()
    liq_pressure = liq.get("pressure", 0) if liq else 0
    liq_regime = liq.get("regime", "unknown") if liq else "unknown"
    if liq_pressure > 0.4:
        total += 2  # strong easing
    elif liq_pressure > 0.15:
        total += 1  # liquidity easing → loosen overlay
    elif liq_pressure < -0.4:
        total -= 2  # liquidity crisis
    elif liq_pressure < -0.15:
        total -= 1  # liquidity tightening → tighten overlay

    if total >= 2:
        label = '宽松'
    elif total <= -2:
        label = '收紧'
    else:
        label = '中性'

    result = {
        'environment': label,
        'score': total,
        'details': env,
        'latest': {k: float(v) if not isinstance(v, str) else v
                    for k, v in latest.items()},
    }
    if 'BOJ_rate' in latest.index:
        result['japan'] = {
            'boj_rate': float(latest['BOJ_rate']),
            'japan_cpi': float(latest.get('JP_CPI', 0)),
            'carry_pressure': float(carry_pressure),
            'carry_regime': carry_regime,
        }
    if shock:
        result['sentiment_shock'] = {
            'net_impact': shock_impact,
            'impact_level': shock_label,
            'shocks': shock.get('shocks', []),
        }
    if liq:
        result['liquidity'] = {
            'pressure': liq_pressure,
            'regime': liq_regime,
            'regime_label': liq.get('regime_label', ''),
            'factors': liq.get('factors', {}),
        }
    return result


# ══════════════════════════════════════════════════════════════
# Sector indices
# ══════════════════════════════════════════════════════════════

def load_sectors() -> list[dict]:
    """Load 880xxx sector map from tdxzs.cfg"""
    path = HQ_CACHE / "tdxzs.cfg"
    if not path.exists():
        return []
    sectors = []
    with open(path, 'r', encoding='gbk', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('['):
                continue
            parts = line.split('|')
            if len(parts) >= 4 and parts[1].strip().startswith('880'):
                sectors.append({'code': parts[1].strip(),
                                'name': parts[0].strip()})
    return sectors


def sector_monthly_return(code: str) -> pd.Series:
    """Read sector daily data -> monthly return"""
    path = VIPDOC / "sh" / "lday" / f"sh{code}.day"
    if not path.exists():
        return None
    try:
        reader = TdxDailyBarReader()
        df = reader.get_df(str(path))
        closes = df['close'].astype(float)
        dates = pd.to_datetime(df.index)
        monthly = closes.groupby(dates.to_period('M')).last()
        monthly.index = monthly.index.to_timestamp() + pd.offsets.MonthEnd(0)
        ret = monthly.pct_change().dropna()
        return ret
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# RollingOLS
# ══════════════════════════════════════════════════════════════

def estimate_sensitivity(sector_ret: pd.Series, macro: pd.DataFrame,
                         window: int = 24, lags: int = 0) -> dict:
    """RollingOLS: sector_ret ~ macro_factors, window in months, with lags"""
    # Build feature set: contemporaneous + lags
    feat_cols = [c for c in macro.columns
                 if pd.api.types.is_numeric_dtype(macro[c])]

    # Standardize macro factors → coefficients comparable across factors
    macro_z = macro.copy()
    for c in feat_cols:
        mn, sd = macro_z[c].mean(), macro_z[c].std()
        macro_z[c] = (macro_z[c] - mn) / sd if sd > 0 else 0.0

    all_feats = list(feat_cols)  # t0

    # Create lagged columns from standardized data
    lag_data = {}
    for c in feat_cols:
        for lag in range(1, lags + 1):
            col = f'{c}_lag{lag}'
            lag_data[col] = macro_z[c].shift(lag)
            all_feats.append(col)

    lag_df = pd.DataFrame(lag_data, index=macro_z.index)
    feat_df = pd.concat([macro_z, lag_df], axis=1)

    # Align with sector returns
    aligned = pd.concat({'ret': sector_ret}, axis=1)
    for c in all_feats:
        aligned[c] = feat_df[c]
    aligned = aligned.dropna()
    if len(aligned) < window:
        return None

    y = aligned['ret'].values[-window:]
    X = sm.add_constant(aligned[all_feats].values[-window:])

    try:
        model = RollingOLS(y, X, window=min(window, len(y)))
        res = model.fit()
        latest = res.params[-1]
        r2 = res.rsquared_adj[-1]
        result = {'const': float(latest[0]), 'rsquared_adj': float(r2),
                  'n_obs': len(aligned)}

        # Per-coefficient betas (t0)
        for i, col in enumerate(feat_cols):
            result[f'beta_{col}'] = float(latest[i + 1])

        # Total sensitivity = sum(t0 + t-1 + ... + t-N) per factor
        for idx, col in enumerate(feat_cols):
            total = result[f'beta_{col}']
            for lag in range(1, lags + 1):
                lag_col = f'{col}_lag{lag}'
                if lag_col in all_feats:
                    li = all_feats.index(lag_col)
                    total += float(latest[li + 1])  # +1 for const
            result[f'total_{col}'] = total

        return result
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sectors', type=int, default=None)
    parser.add_argument('--window', type=int, default=36)
    parser.add_argument('--lags', type=int, default=2,
                        help='distributed lag months (default 2, tuned)')
    parser.add_argument('--show-beta', action='store_true')
    parser.add_argument('--classify', action='store_true',
                        help='classify current macro environment for overlay filtering')
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 50)
    print("Macro Sensitivity v0.5")
    print("=" * 50)

    # 1. Macro data
    print("\n[1/3] Macro factors...")
    macro = fetch_macro()
    print(f"  {len(macro)} obs, {macro.index[0].date()} ~ {macro.index[-1].date()}")
    print(f"  Factors: {list(macro.columns)}")

    # 1b. Japan macro
    japan = fetch_japan_macro()
    if japan is not None:
        macro = merge_japan_factors(macro, japan)
        jp_cols = [c for c in japan.columns if c in macro.columns]
        print(f"  +Japan factors: {jp_cols}  ({len(macro)} obs after merge)")

    # --classify: quick macro environment scan, no sector run needed
    if args.classify:
        env = classify_environment(macro)
        print(f"\n  Macro environment: {env['environment']} (score={env['score']:+d})")
        print(f"  Latest data: M2={env['latest']['M2']:.1f}%  SHIBOR={env['latest']['SHIBOR']:.2f}%  "
              f"CPI={env['latest']['CPI']:.1f}%  PMI={env['latest']['PMI']:.1f}")
        if 'japan' in env:
            jp = env['japan']
            print(f"  Japan: BOJ={jp['boj_rate']}%  CPI={jp['japan_cpi']}%  "
                  f"carry_pressure={jp['carry_pressure']:+.3f} ({jp['carry_regime']})")
        print(f"  Detail: {env['details']}")

        # P0+P1: bond yield, FX, commodity prices
        print("\n  [extra] China bond & FX...")
        bond_fx = fetch_china_bond_fx()
        for k, v in bond_fx.items():
            print(f"    {k} = {v}")

        print("  [extra] Commodity prices...")
        comm = fetch_commodity_prices()
        for k, v in comm.items():
            print(f"    {k} = {v}")

        # Build and save snapshot JSON
        snapshot = {
            'update_time': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
            'environment': env['environment'],
            'score': env['score'],
            'macro': {k: float(v) if not isinstance(v, str) else v
                      for k, v in env['latest'].items()},
        }
        if bond_fx:
            snapshot['bond_fx'] = bond_fx
        if comm:
            snapshot['commodity'] = comm
        if 'japan' in env:
            snapshot['japan'] = env['japan']
        if 'sentiment_shock' in env:
            snapshot['sentiment'] = {
                'net_impact': env['sentiment_shock']['net_impact'],
                'impact_level': env['sentiment_shock']['impact_level'],
            }
        if 'liquidity' in env:
            snapshot['liquidity'] = {
                'pressure': env['liquidity']['pressure'],
                'regime': env['liquidity']['regime'],
                'regime_label': env['liquidity']['regime_label'],
            }

        try:
            with open(SNAPSHOT_PATH, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            print(f"\n  -> saved: {SNAPSHOT_PATH}")
        except Exception as e:
            print(f"  [WARN] snapshot save failed: {e}")

        if env['score'] >= 2:
            print(f"  → 宽松环境：利好 PMI+ 板块 (半导体/芯片/汽车芯片)")
            print(f"    回避 PMI- 板块 (房地产/银行)")
        elif env['score'] <= -2:
            print(f"  → 收紧环境：利好 SHIBOR-/CPI- 板块 (避险属性)")
            print(f"    回避 SHIBOR+/M2+ 板块")
        else:
            print(f"  → 中性环境：宏观层不过滤，以板块评分为主")
        if 'japan' in env and env['japan']['carry_regime'] in ('unwind', 'building'):
            print(f"  [!] 套息压力 {env['japan']['carry_regime']} → 关注全球流动性收紧对科技/成长的压制")
        if 'sentiment_shock' in env:
            ss = env['sentiment_shock']
            print(f"  Sentiment shock: impact={ss['net_impact']} level={ss['impact_level']} ({len(ss['shocks'])} events)")
            for s in ss['shocks'][:3]:
                print(f"    [{s.get('impact','?')}] {s.get('event','?')}")
        if 'liquidity' in env:
            liq = env['liquidity']
            print(f"  Liquidity: pressure={liq['pressure']:+.3f} regime={liq['regime_label']}")
            for k, f in liq.get('factors', {}).items():
                bar = '+' if f['score'] > 0 else ''
                print(f"    {f['label']:6s} score={f['score']:+.3f}  raw={f.get('raw','?')}")
        return

    # 2. Sector list
    print("\n[2/3] Sector indices...")
    sectors = load_sectors()
    print(f"  {len(sectors)} concept sectors")
    if args.sectors:
        sectors = sectors[:args.sectors]
        print(f"  (test: first {args.sectors})")

    # 3. RollingOLS
    win = args.window
    print(f"\n[3/3] RollingOLS (window={win}mo)...")
    results = {}
    fail = 0

    for i, sec in enumerate(sectors):
        if (i + 1) % 20 == 0:
            print(f"  progress: {i+1}/{len(sectors)}...", flush=True)
        ret = sector_monthly_return(sec['code'])
        if ret is None or len(ret) < win + 5:
            fail += 1
            continue
        betas = estimate_sensitivity(ret, macro, window=win, lags=args.lags)
        if betas is None:
            fail += 1
            continue
        results[sec['code']] = {'name': sec['name'], **betas}

    elapsed = time.time() - t0
    print(f"\n  Done: {len(results)} ok / {fail} fail ({elapsed:.1f}s)")

    if not results:
        print("  [ERR] no results")
        return

    # Show betas (contemporaneous + total)
    bcols = sorted([c for c in list(results.values())[0] if c.startswith('beta_')])
    tcols = sorted([c for c in list(results.values())[0] if c.startswith('total_')])
    factors = [c.replace('total_', '') for c in tcols]

    if args.show_beta:
        print(f"\nLatest total sensitivity (first 20):")
        rows = []
        for code, v in list(results.items())[:20]:
            r = {'sector': v['name'], 'R2': f"{v['rsquared_adj']:.3f}"}
            for fn in factors:
                r[f'{fn}(total)'] = f"{v.get(f'total_{fn}', 0):+.3f}"
            rows.append(r)
        print(pd.DataFrame(rows).to_string(index=False))

    # Save with z-scores (using total sensitivity)
    vals = {c: [] for c in tcols}
    for v in results.values():
        for c in tcols:
            vals[c].append(v.get(c, 0))
    means = {c: float(np.mean(vals[c])) for c in tcols}
    stds = {c: float(np.std(vals[c])) for c in tcols}

    out = {
        'update_time': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'config': {'window_months': win, 'n_sectors': len(results),
                   'lags': args.lags},
        'environment': classify_environment(macro),
        'factors': {c: {} for c in macro.columns},
        'sectors': {},
    }
    for code, v in results.items():
        entry = {'name': v['name'], 'r_squared': v['rsquared_adj'],
                 'betas': {}, 'total': {}, 'z_scores': {}}
        for fn in factors:
            entry['betas'][fn] = v.get(f'beta_{fn}', 0)
            entry['total'][fn] = v.get(f'total_{fn}', 0)
            tc = f'total_{fn}'
            z = (v.get(tc, 0) - means[tc]) / stds[tc] if stds[tc] > 0 else 0.0
            entry['z_scores'][fn] = round(z, 3)
        out['sectors'][code] = entry

    out_path = SIGNALS_DIR / "macro_sensitivity.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n  -> saved: {out_path}")

    # Top/Bottom by total sensitivity
    print("\nTop/Bottom sensitive sectors (total = t0 + t-1 + t-2 + t-3):")
    for fn in factors:
        items = sorted(results.items(), key=lambda x: x[1].get(f'total_{fn}', 0))
        top = ', '.join(f'{v["name"]}({v.get(f"total_{fn}", 0):+.3f})'
                       for _, v in items[-3:][::-1])
        bot = ', '.join(f'{v["name"]}({v.get(f"total_{fn}", 0):+.3f})'
                       for _, v in items[:3])
        print(f"  [{fn}] +{top}")
        print(f"         -{bot}")
    print("=" * 50)


if __name__ == '__main__':
    main()

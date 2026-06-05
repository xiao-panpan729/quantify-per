# -*- coding: utf-8 -*-
"""
US Macro Sensitivity v1.0 — US宏观因子→A股板块敏感度
=====================================================

macro_sensitivity.py 的美股镜像。替换4个中国宏观因子为美国宏观因子：
  Fed利率 / CPI YoY / ISM PMI / 非农就业

复用 macro_sensitivity.py 的:
  load_sectors() / sector_monthly_return() / estimate_sensitivity()

参数: window=24月(US周期更短), lags=1(US传导更快)

用法:
  python tools/us_market/macro_sensitivity.py --sectors 5    # 测试5个板块
  python tools/us_market/macro_sensitivity.py --classify      # 仅US环境分类
  python tools/us_market/macro_sensitivity.py                 # 全量
"""

import argparse, json, time, warnings, sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import akshare as ak
from tools.macro_sensitivity import (
    load_sectors, sector_monthly_return, estimate_sensitivity
)

warnings.filterwarnings('ignore')

SIGNALS_DIR = Path(__file__).resolve().parent.parent.parent / "signals" / "tracking"


def fetch_us_macro() -> pd.DataFrame:
    """拉取4个US宏观因子，对齐到月频"""
    print("  [akshare] fetching US macro data...")

    # ── Fed Funds Rate (monthly release schedule) ──
    df = ak.macro_bank_usa_interest_rate()
    fed = df.iloc[:, [1, 2]].copy()
    fed.columns = ['ds', 'FEDFUNDS']
    fed['ds'] = pd.to_datetime(fed['ds'], errors='coerce')
    fed['FEDFUNDS'] = pd.to_numeric(fed['FEDFUNDS'], errors='coerce')
    fed = fed.dropna(subset=['ds'])
    # Forward-fill the rate between meetings
    fed['FEDFUNDS'] = fed['FEDFUNDS'].ffill()

    # ── CPI YoY (monthly) ──
    df = ak.macro_usa_cpi_yoy()
    cpi = df.iloc[:, [0, 2]].copy()  # col 0 = time, col 2 = value
    cpi.columns = ['ds', 'US_CPI']
    cpi['ds'] = pd.to_datetime(cpi['ds'], errors='coerce')
    cpi['US_CPI'] = pd.to_numeric(cpi['US_CPI'], errors='coerce')

    # ── ISM Manufacturing PMI (monthly) ──
    df = ak.macro_usa_ism_pmi()
    ism = df.iloc[:, [1, 2]].copy()
    ism.columns = ['ds', 'ISM_PMI']
    ism['ds'] = pd.to_datetime(ism['ds'], errors='coerce')
    ism['ISM_PMI'] = pd.to_numeric(ism['ISM_PMI'], errors='coerce')

    # ── Non-Farm Payrolls (monthly, in 10K units) ──
    df = ak.macro_usa_non_farm()
    nfp = df.iloc[:, [1, 2]].copy()
    nfp.columns = ['ds', 'NONFARM']
    nfp['ds'] = pd.to_datetime(nfp['ds'], errors='coerce')
    nfp['NONFARM'] = pd.to_numeric(nfp['NONFARM'], errors='coerce')

    # ── Align to month-end ──
    monthly = {}
    for label, src in [('FEDFUNDS', fed), ('US_CPI', cpi),
                        ('ISM_PMI', ism), ('NONFARM', nfp)]:
        d = src[['ds', label]].dropna(subset=[label]).copy()
        d['date'] = d['ds'] + pd.offsets.MonthEnd(0)
        # Take the last observation per month
        monthly[label] = d.groupby('date')[label].last()

    combined = pd.DataFrame(monthly).sort_index()
    combined = combined.ffill().dropna()
    return combined


def classify_us_environment(macro: pd.DataFrame) -> dict:
    """US宏观环境分类 — 阈值适配美国经济"""
    latest = macro.iloc[-1]
    env = {}

    # Fed Funds Rate: < 2% = loose, > 4% = tight
    fed = latest['FEDFUNDS']
    if fed < 2.0:
        env['FEDFUNDS'] = +1
    elif fed <= 4.0:
        env['FEDFUNDS'] = 0
    else:
        env['FEDFUNDS'] = -1

    # CPI YoY (inverted): < 2.5% = loose, > 4% = tight
    cpi = latest['US_CPI']
    if cpi < 2.5:
        env['US_CPI'] = +1
    elif cpi <= 4.0:
        env['US_CPI'] = 0
    else:
        env['US_CPI'] = -1

    # ISM PMI: > 52 = expansion, < 47 = contraction
    ism = latest['ISM_PMI']
    if ism > 52:
        env['ISM_PMI'] = +1
    elif ism >= 47:
        env['ISM_PMI'] = 0
    else:
        env['ISM_PMI'] = -1

    # Non-Farm Payrolls (10K units): > 20 (200K) = strong
    nfp = latest['NONFARM']
    if nfp > 20:
        env['NONFARM'] = +1
    elif nfp >= 10:
        env['NONFARM'] = 0
    else:
        env['NONFARM'] = -1

    total = sum(env.values())
    if total >= 2:
        label = '宽松'
    elif total <= -2:
        label = '收紧'
    else:
        label = '中性'

    return {
        'environment': label,
        'score': total,
        'details': env,
        'latest': {k: float(v) for k, v in latest.items()},
    }


def main():
    parser = argparse.ArgumentParser(description="US Macro → A-Share Sensitivity")
    parser.add_argument('--sectors', type=int, default=None,
                        help='limit to first N sectors (testing)')
    parser.add_argument('--window', type=int, default=24,
                        help='RollingOLS window in months (default 24)')
    parser.add_argument('--lags', type=int, default=1,
                        help='distributed lag months (default 1)')
    parser.add_argument('--classify', action='store_true',
                        help='classify US macro environment only')
    parser.add_argument('--show-beta', action='store_true',
                        help='show latest total sensitivity')
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 56)
    print("US Macro Sensitivity v1.0 — US→A-Share Sector")
    print("=" * 56)

    # 1. Macro data
    print("\n[1/3] US macro factors...")
    macro = fetch_us_macro()
    print(f"  {len(macro)} obs, {macro.index[0].date()} ~ {macro.index[-1].date()}")
    print(f"  Factors: {list(macro.columns)}")
    latest = macro.iloc[-1]
    print(f"  Latest: Fed={latest['FEDFUNDS']:.1f}%  CPI={latest['US_CPI']:.1f}%  "
          f"ISM={latest['ISM_PMI']:.1f}  NFP={latest['NONFARM']:.1f}")

    if args.classify:
        env = classify_us_environment(macro)
        print(f"\n  US Macro environment: {env['environment']} (score={env['score']:+d})")
        print(f"  Details: {env['details']}")
        if env['score'] >= 2:
            print(f"  → 宽松环境：利好 PMI+板块 (科技/半导体)")
            print(f"    回避 PMI-板块")
        elif env['score'] <= -2:
            print(f"  → 收紧环境：利好 防御性板块")
            print(f"    回避 高估值/高Beta板块")
        else:
            print(f"  → 中性环境：宏观层不过滤")
        return

    # 2. Sector list
    print("\n[2/3] A-share sector indices...")
    sectors = load_sectors()
    print(f"  {len(sectors)} concept sectors found")
    if args.sectors:
        sectors = sectors[:args.sectors]
        print(f"  (test mode: first {args.sectors})")

    # 3. RollingOLS
    win = args.window
    print(f"\n[3/3] RollingOLS (window={win}mo, lags={args.lags})...")
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

    # Show top betas
    tcols = sorted([c for c in list(results.values())[0] if c.startswith('total_')])
    factors = [c.replace('total_', '') for c in tcols]

    if args.show_beta:
        rows = []
        for code, v in list(results.items())[:20]:
            r = {'sector': v['name'], 'R2': f"{v['rsquared_adj']:.3f}"}
            for fn in factors:
                r[f'{fn}(total)'] = f"{v.get(f'total_{fn}', 0):+.3f}"
            rows.append(r)
        print(pd.DataFrame(rows).to_string(index=False))

    # Compute z-scores
    vals = {c: [] for c in tcols}
    for v in results.values():
        for c in tcols:
            vals[c].append(v.get(c, 0))
    means = {c: float(np.mean(vals[c])) for c in tcols}
    stds = {c: float(np.std(vals[c])) for c in tcols}

    out = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'config': {'window_months': win, 'n_sectors': len(results),
                    'lags': args.lags, 'market': 'US→A-share'},
        'environment': classify_us_environment(macro),
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

    out_path = SIGNALS_DIR / "us_macro_sensitivity.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n  -> saved: {out_path}")

    # Top/Bottom
    print("\nTop/Bottom sensitive sectors (total sensitivity):")
    for fn in factors:
        items = sorted(results.items(), key=lambda x: x[1].get(f'total_{fn}', 0))
        top = ', '.join(f'{v["name"]}({v.get(f"total_{fn}", 0):+.3f})'
                       for _, v in items[-3:][::-1])
        bot = ', '.join(f'{v["name"]}({v.get(f"total_{fn}", 0):+.3f})'
                       for _, v in items[:3])
        print(f"  [{fn}] +{top}")
        print(f"         -{bot}")
    print("=" * 56)


if __name__ == '__main__':
    main()

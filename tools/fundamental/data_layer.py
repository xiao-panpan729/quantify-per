"""
data_layer.py — 独立数据层输出
==============================

聚合 Rolling FM + Type A/B + CAPEX 分析 → fundamental_profile.json

约束:
  - 单向输出，不绑定任何选股模型
  - 消费方（量领/筹码/战役/日报）按需读取

Usage:
  python -m tools.fundamental.data_layer          # 全量运行+保存
  python -m tools.fundamental.data_layer --fm-only  # 只跑FM
"""

import sys, json, time
from pathlib import Path

import numpy as np
import pandas as pd

_proj_root = Path(__file__).resolve().parent.parent.parent
if str(_proj_root) not in sys.path:
    sys.path.insert(0, str(_proj_root))

from config import NAME_MAP
from tools.fundamental.fm_pipeline import (
    GpcwLoader, CrossSectionProcessor, RollingFamaMacBeth,
    FactorMomentum, FACTOR_KEYS, FACTOR_DEFS,
    _compute_forward_returns
)
from tools.fundamental.growth_narrative import (
    TypeADetector, TypeBDetector, GrowthClassifier
)
from tools.fundamental.capex_analyzer import CapexAnalyzer

SIGNALS_DIR = _proj_root / "signals" / "tracking"
OUTPUT_FILE = SIGNALS_DIR / "_funds" / "fundamental_profile.json"
CACHE_FILE = SIGNALS_DIR / ".fundamental_cache.json"
CW_DIR = Path("C:/zd_cjzq/vipdoc/cw")

# Windows GBK console workaround
if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def _gpcw_snapshot() -> dict:
    """返回 gpcw*.zip 文件 → 修改时间戳 的快照字典"""
    snap = {}
    for f in sorted(CW_DIR.glob("gpcw*.zip")):
        if f.stat().st_size > 100_000:
            snap[f.name] = f.stat().st_mtime
    return snap


def _check_updates() -> bool:
    """检查 gpcw 数据是否有更新。有更新返回 True，无更新打印提示返回 False"""
    current = _gpcw_snapshot()
    if not current:
        print("[fundamental] ⚠ gpcw 目录无数据文件，跳过")
        return False

    if not CACHE_FILE.exists():
        print(f"[fundamental] 首次运行，{len(current)} 个季报文件待处理")
        return True

    try:
        cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        cached = {}

    new_files = [k for k in current if k not in cached]
    changed_files = [k for k in current if k in cached and current[k] != cached[k]]

    if not new_files and not changed_files:
        print(f"[fundamental] 无更新 ({len(current)} 个季报文件未变化)，跳过")
        return False

    if new_files:
        print(f"[fundamental] 新季报: {new_files}")
    if changed_files:
        print(f"[fundamental] 已更新: {changed_files}")
    return True


def _save_cache():
    """保存当前 gpcw 快照到缓存"""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(_gpcw_snapshot(), indent=2), encoding="utf-8")


def build_profile(panel: pd.DataFrame = None,
                  window: int = 12,
                  save: bool = True) -> dict:
    """
    Build the complete fundamental profile.

    Pipeline:
      1. Load gpcw → panel
      2. Rolling FM → factor premium series + momentum
      3. Type A/B → growth narrative classification
      4. CAPEX analysis → CFA cycle labels
      5. Current cross-section scores → factor exposure vectors
      6. Assemble → fundamental_profile.json
    """
    t0 = time.time()
    print("=" * 60)
    print("  Fundamental Data Layer — 完整画像构建")
    print("=" * 60)

    # 1. Load data
    if panel is None:
        print("\n[1/5] Loading gpcw quarterly data...")
        loader = GpcwLoader()
        panel = loader.load_all()
    else:
        panel = panel.copy()

    print(f"  Panel: {len(panel)} obs, {panel['code'].nunique()} stocks, "
          f"{panel['report_date'].nunique()} periods")

    # 2. Rolling FM + factor momentum
    print("\n[2/5] Rolling Fama-MacBeth regression...")
    # Full cross-section for meaningful FM regression
    all_codes = set(panel['code'].unique())
    panel_fm = _compute_forward_returns(panel, all_codes)
    print(f"  With forward returns: {len(panel_fm)} obs ({panel_fm['code'].nunique()} stocks)")

    rfm = RollingFamaMacBeth(window=window)
    rfm.fit(panel_fm, FACTOR_KEYS)

    fmom = FactorMomentum()
    fmom.fit(rfm.premium_series_)

    # 3. Type A/B classification (full universe: tracking + VL + 历史VL)
    print("\n[3/5] Growth narrative classification (Type A/B)...")
    try:
        vl_data = json.load(open(SIGNALS_DIR / '_funds' / 'volume_leader_universe.json', 'r', encoding='utf-8'))
        vl_codes_classify = set(vl_data.get('universe', []))
    except Exception:
        vl_codes_classify = set()
    try:
        vl_hist = json.load(open(_proj_root / 'tools' / 'volume_leader' / 'experiments' / 'outputs' / 'vl_lifetime_analysis.json', 'r', encoding='utf-8'))
        hist_codes_classify = set(vl_hist.get('membership', {}).keys())
    except Exception:
        hist_codes_classify = set()
    classify_universe = set(NAME_MAP.keys()) | vl_codes_classify | hist_codes_classify
    classifier = GrowthClassifier()
    growth_results = classifier.classify(panel, universe=classify_universe)

    # Print summary
    type_counts = {'Type A': 0, 'Type B': 0, 'Type A+B': 0, 'None': 0}
    for v in growth_results.values():
        t = v['primary_type']
        if t in type_counts:
            type_counts[t] += 1
        elif 'A' in t:
            type_counts['Type A'] += 1
        elif 'B' in t:
            type_counts['Type B'] += 1
    print(f"  Classification: {type_counts}")

    # 4. CAPEX analysis
    print("\n[4/5] CAPEX cycle analysis...")
    ca = CapexAnalyzer()
    capex_results = ca.analyze(panel)

    # 5. Current cross-section scores
    print("\n[5/5] Current cross-section scores...")
    latest_date = sorted(panel['report_date'].unique())[-1]
    csp = CrossSectionProcessor()
    latest = panel[panel['report_date'] == latest_date].copy()
    if len(latest) == 0:
        print("  [WARN] no data for latest period")
        return {}

    latest = csp.process(latest, FACTOR_KEYS)

    # Direction-corrected z-scores
    for k in FACTOR_KEYS:
        latest[f'd_{k}'] = latest[f'z_{k}'] * FACTOR_DEFS[k]['direction']

    # Per-stock profile: tracking universe + VL universe + 历史量领宇宙(Jan~May)
    try:
        vl_data = json.load(open(SIGNALS_DIR / '_funds' / 'volume_leader_universe.json', 'r', encoding='utf-8'))
        vl_codes = set(vl_data.get('universe', []))
    except Exception:
        vl_codes = set()
    try:
        vl_hist = json.load(open(_proj_root / 'tools' / 'volume_leader' / 'experiments' / 'outputs' / 'vl_lifetime_analysis.json', 'r', encoding='utf-8'))
        hist_codes = set(vl_hist.get('membership', {}).keys())
    except Exception:
        hist_codes = set()
    profile_codes = set(NAME_MAP.keys()) | vl_codes | hist_codes

    # Load stock name cache for VL universe
    stock_names = {}
    try:
        import csv
        with open(SIGNALS_DIR / '_funds' / 'stock_names.csv', 'r', encoding='utf-8') as f:
            for row_ in csv.reader(f):
                if len(row_) >= 2:
                    stock_names[row_[0]] = row_[1]
    except Exception:
        pass

    stock_profiles = {}
    for code in sorted(profile_codes):
        profile = {'name': NAME_MAP.get(code, '') or stock_names.get(code, '')}

        # Current factor exposure
        row = latest[latest['code'] == code]
        if len(row) > 0:
            row = row.iloc[0]
            for k in FACTOR_KEYS:
                profile[f'z_{k}'] = round(float(row[f'z_{k}']), 3) if pd.notna(row[f'z_{k}']) else None
                profile[f'd_{k}'] = round(float(row[f'd_{k}']), 3) if pd.notna(row[f'd_{k}']) else None
        else:
            for k in FACTOR_KEYS:
                profile[f'z_{k}'] = None
                profile[f'd_{k}'] = None

        # Growth narrative
        g = growth_results.get(code, {})
        profile['growth_type'] = g.get('primary_type', 'None')
        profile['growth_confidence'] = g.get('confidence', 0.0)
        profile['type_a_score'] = g.get('type_a_score', 0.0)
        profile['type_b_score'] = g.get('type_b_score', 0.0)

        # CAPEX profile
        c = capex_results.get(code, {})
        profile['capex_stage'] = c.get('capex_stage', None)
        profile['capex_trend'] = c.get('capex_trend', None)
        profile['capex_revenue_ratio'] = c.get('capex_revenue_ratio', None)
        profile['fa_turnover'] = c.get('fa_turnover', None)
        profile['fcf_trend'] = c.get('fcf_trend', None)
        profile['fcf_latest'] = c.get('fcf_latest', None)

        stock_profiles[code] = profile

    # Assemble full output
    premium_list = []
    for idx, row_prem in rfm.premium_series_.iterrows():
        rec = {'window_end': str(idx)}
        for k in FACTOR_KEYS:
            val = row_prem.get(k)
            rec[k] = round(float(val), 4) if pd.notna(val) else None
        premium_list.append(rec)

    output = {
        'update_time': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'config': {
            'window': window,
            'n_periods': int(panel['report_date'].nunique()),
            'n_stocks': int(panel['code'].nunique()),
            'latest_report': latest_date,
        },
        'factor_premium_series': premium_list,
        'factor_momentum': fmom.latest,
        'stock_profiles': stock_profiles,
    }

    elapsed = time.time() - t0

    # Summary
    print(f"\n  ─── Fundamental Profile Summary ───")
    print(f"  Latest report: {latest_date}")
    print(f"  Factor momentum: {fmom.latest.get('hot_factors', 'N/A')}")
    print(f"  Stocks profiled: {len(stock_profiles)}")
    for code, p in sorted(stock_profiles.items()):
        name = p.get('name', '')
        gtype = p.get('growth_type', 'None')
        cstage = p.get('capex_stage', '-')
        print(f"  {code} {name:12s} | 成长:{gtype:12s} | CAPEX:{cstage}")
    print(f"\n  Done in {elapsed:.1f}s")

    if save:
        SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"  -> saved: {OUTPUT_FILE}")

    return output


if __name__ == '__main__':
    if not _check_updates():
        import sys as _sys; _sys.exit(0)
    build_profile()
    _save_cache()

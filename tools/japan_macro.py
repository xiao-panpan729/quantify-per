# -*- coding: utf-8 -*-
"""
日本宏观 + 套息交易压力模块 v1.0
================================

日本是全球流动性的水源地。BOJ 加息 → 日元升值 → 套息交易平仓
（借日元买美债/美股/AI概念的资金被迫回补）→ 全球流动性收缩。

三个核心因子:
  1. BOJ 政策利率 — 套息交易的开关
  2. USD/JPY (FXY ETF 代理) — 套息平仓的实时压力计
  3. 日本核心 CPI — BOJ 加息预期的领先指标

合成 "套息交易压力指数" (Carry Trade Pressure Index):
  - 正数 = 套息平仓压力大 = 全球流动性收紧 = 风险资产承压
  - 负数 = 套息环境宽松 = 全球流动性充裕 = 风险资产受益

用法:
  python tools/japan_macro.py                     # 终端打印日本宏观+压力指数
  python tools/japan_macro.py --save              # 保存 JSON
  python tools/japan_macro.py --classify          # 仅分类当前环境
  python tools/japan_macro.py --history 12        # 最近12个月压力轨迹
"""

import argparse
import json
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACKING_DIR = PROJECT_ROOT / "signals" / "tracking"


# ══════════════════════════════════════════════════════════════
# Data fetching
# ══════════════════════════════════════════════════════════════

def fetch_boj_rate() -> pd.Series:
    """BOJ 政策利率 (月频)，返回 Series 以月初为索引"""
    df = ak.macro_japan_bank_rate()
    # columns: 时间, 前值, 现值, 发布日期
    series = pd.Series(dtype=float)
    for _, row in df.iterrows():
        try:
            ts = pd.to_datetime(str(row.iloc[0]).replace('年', '-').replace('月', ''))
            val = pd.to_numeric(row.iloc[2], errors='coerce')
            if pd.notna(ts) and pd.notna(val):
                series[ts] = val
        except Exception:
            continue
    series = series.sort_index()
    # Fill: BOJ rate doesn't change every month, ffill gaps
    series = series.resample('MS').last().ffill()
    return series


def fetch_japan_cpi() -> pd.Series:
    """日本核心 CPI YoY (月频)"""
    df = ak.macro_japan_core_cpi_yearly()
    # columns: 时间, 前值, 现值, 发布日期
    series = pd.Series(dtype=float)
    for _, row in df.iterrows():
        try:
            ts = pd.to_datetime(str(row.iloc[0]).replace('年', '-').replace('月', ''))
            val = pd.to_numeric(row.iloc[2], errors='coerce')
            if pd.notna(ts) and pd.notna(val):
                series[ts] = val
        except Exception:
            continue
    return series.sort_index()


def fetch_fxy_monthly() -> pd.Series | None:
    """FXY ETF (Japanese Yen Trust) 月末收盘 → yen 强度代理

    FXY ↑ = 日元升值 = 套息平仓压力
    """
    try:
        df = ak.stock_us_daily(symbol='FXY', adjust='qfq')
        if df is None or len(df) < 60:
            return None
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        monthly = df['close'].resample('ME').last()
        return monthly
    except Exception as e:
        print(f"  [WARN] FXY 拉取失败: {e}")
        return None


def build_japan_macro_df() -> pd.DataFrame:
    """组装日本宏观 DataFrame (月频)"""
    print("  [akshare] 拉取日本宏观数据...")

    boj = fetch_boj_rate()
    cpi = fetch_japan_cpi()
    fxy = fetch_fxy_monthly()

    # Align to month-end for consistency with macro_sensitivity.py
    boj_me = boj.copy()
    boj_me.index = boj_me.index + pd.offsets.MonthEnd(0)

    cpi_me = cpi.copy()
    cpi_me.index = cpi_me.index + pd.offsets.MonthEnd(0)

    combined = pd.DataFrame({
        'BOJ_rate': boj_me,
        'JP_CPI': cpi_me,
    })

    if fxy is not None:
        combined['FXY'] = fxy
        # FXY_monthly_change: positive = yen strengthening
        combined['FXY_chg'] = combined['FXY'].pct_change()

    # BOJ_rate_change: positive = BOJ hiking
    combined['BOJ_chg'] = combined['BOJ_rate'].diff()

    # CPI_3ma: 3-month moving average for trend detection
    combined['CPI_3ma'] = combined['JP_CPI'].rolling(3).mean()
    combined['CPI_trend'] = combined['CPI_3ma'].diff()

    combined = combined.dropna(subset=['BOJ_rate'])
    return combined


# ══════════════════════════════════════════════════════════════
# Carry Trade Pressure Index
# ══════════════════════════════════════════════════════════════

def compute_carry_pressure(df: pd.DataFrame) -> pd.DataFrame:
    """合成套息交易压力指数

    三个维度的压力信号 (归一化后加总):
      1. BOJ 加息方向: 近6个月利率变化 (正=加息=压力)
      2. 日元升值方向: 近6个月 FXY 变化 (正=升值=压力)
      3. CPI 趋势: CPI 3月均线方向 (正=升温=加息预期=压力)

    输出列:
      carry_pressure_raw   — 未平滑的原始压力值
      carry_pressure       — 3个月平滑后的压力指数
      carry_regime         — 压力区间: unwind / building / stable / easing
    """
    result = df.copy()

    # Component 1: BOJ rate momentum (6-month change)
    result['p_boj'] = result['BOJ_rate'].diff(6).fillna(0)

    # Component 2: Yen strength momentum (6-month FXY change, normalized)
    if 'FXY' in result.columns:
        result['p_yen'] = result['FXY'].pct_change(6).fillna(0)
    else:
        result['p_yen'] = 0.0

    # Component 3: CPI trend momentum (3-month acceleration)
    result['p_cpi'] = result['CPI_trend'].fillna(0)

    # Normalize each component to [-1, 1] using rolling 36-month min/max
    for col in ['p_boj', 'p_yen', 'p_cpi']:
        r = result[col].rolling(36, min_periods=12)
        rmin, rmax = r.min(), r.max()
        denom = (rmax - rmin).replace(0, 1)
        result[f'{col}_norm'] = ((result[col] - rmin) / denom * 2 - 1).clip(-1, 1)

    # Composite: equal-weighted
    norm_cols = ['p_boj_norm', 'p_yen_norm', 'p_cpi_norm']
    result['carry_pressure_raw'] = result[norm_cols].mean(axis=1)

    # Smooth: 3-month EMA-ish (simple rolling average)
    result['carry_pressure'] = result['carry_pressure_raw'].rolling(3, min_periods=1).mean()

    # Regime classification
    def classify_regime(p):
        if pd.isna(p):
            return 'unknown'
        if p > 0.3:
            return 'unwind'       # 套息平仓压力显著 → 流动性收紧
        elif p > 0.0:
            return 'building'     # 压力在积累
        elif p > -0.3:
            return 'stable'       # 压力温和
        else:
            return 'easing'       # 套息环境宽松 → 流动性充裕

    result['carry_regime'] = result['carry_pressure'].apply(classify_regime)

    return result


# ══════════════════════════════════════════════════════════════
# Environment summary
# ══════════════════════════════════════════════════════════════

def classify_japan_environment(df: pd.DataFrame) -> dict:
    """当前日本宏观环境 + 套息压力"""
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest

    boj_rate = latest.get('BOJ_rate', 0)
    boj_chg = latest.get('BOJ_chg', 0)
    cpi = latest.get('JP_CPI', 0)
    pressure = latest.get('carry_pressure', 0)
    regime = latest.get('carry_regime', 'unknown')

    # BOJ direction
    if boj_chg > 0.05:
        boj_signal = 'hiking'
    elif boj_chg < -0.05:
        boj_signal = 'cutting'
    else:
        boj_signal = 'hold'

    # Yen direction
    yen_chg = latest.get('FXY_chg', 0) if 'FXY_chg' in latest.index else 0
    if pd.notna(yen_chg):
        if yen_chg > 0.01:
            yen_signal = 'strengthening'
        elif yen_chg < -0.01:
            yen_signal = 'weakening'
        else:
            yen_signal = 'stable'
    else:
        yen_signal = 'unknown'

    # Impact on A-shares
    if regime == 'unwind':
        a_share_impact = 'negative — 套息平仓→全球流动性收紧→科技/成长股承压，避险板块受益'
    elif regime == 'building':
        a_share_impact = 'caution — 压力在积累，关注 BOJ 下次会议，减仓高估值'
    elif regime == 'easing':
        a_share_impact = 'positive — 套息环境宽松→全球流动性充裕→利好 AI/半导体/成长'
    else:
        a_share_impact = 'neutral — 套息压力温和，按正常节奏交易'

    return {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'boj_rate': round(float(boj_rate), 2),
        'boj_signal': boj_signal,
        'japan_cpi': round(float(cpi), 1),
        'yen_signal': yen_signal,
        'carry_pressure': round(float(pressure), 3),
        'carry_regime': regime,
        'a_share_impact': a_share_impact,
        'components': {
            'boj_pressure': round(float(latest.get('p_boj_norm', 0)), 3),
            'yen_pressure': round(float(latest.get('p_yen_norm', 0)), 3),
            'cpi_pressure': round(float(latest.get('p_cpi_norm', 0)), 3),
        },
        'data_range': {
            'start': str(df.index[0].date()),
            'end': str(df.index[-1].date()),
            'n_months': len(df),
        },
    }


# ══════════════════════════════════════════════════════════════
# Display
# ══════════════════════════════════════════════════════════════

def print_japan_summary(env: dict):
    """终端打印日本宏观环境"""
    regime_labels = {
        'unwind': '[!!] 套息平仓',
        'building': '[~] 压力积累',
        'stable': '[=] 温和',
        'easing': '[+] 宽松',
    }
    label = regime_labels.get(env['carry_regime'], '[?]')

    print(f"\n{'='*60}")
    print(f"  日本宏观 x 套息交易压力  —  {label}")
    print(f"{'='*60}")
    print(f"  BOJ 政策利率:  {env['boj_rate']}%  ({env['boj_signal']})")
    print(f"  日本核心 CPI:  {env['japan_cpi']}%")
    print(f"  日元方向:      {env['yen_signal']}")
    print(f"  ─────────────────────────────")
    print(f"  套息压力指数:  {env['carry_pressure']:+.3f}")
    print(f"  压力区间:      {env['carry_regime']}")
    print(f"  ─────────────────────────────")
    print(f"  分项贡献:")
    print(f"    BOJ 加息压力:  {env['components']['boj_pressure']:+.3f}")
    print(f"    日元升值压力:  {env['components']['yen_pressure']:+.3f}")
    print(f"    CPI 升温压力:  {env['components']['cpi_pressure']:+.3f}")
    print(f"  ─────────────────────────────")
    print(f"  A股映射:  {env['a_share_impact']}")
    print(f"  (数据: {env['data_range']['start']} ~ {env['data_range']['end']}, {env['data_range']['n_months']}个月)")
    print(f"{'='*60}")


def print_pressure_history(df: pd.DataFrame, months: int = 12):
    """打印压力指数历史轨迹"""
    recent = df.tail(months)
    print(f"\n  最近 {months} 个月套息压力轨迹:")
    print(f"  {'月份':<12} {'BOJ%':>6} {'CPI%':>6} {'压力':>8} {'区间'}")
    print(f"  {'-'*50}")
    for idx, row in recent.iterrows():
        p = row.get('carry_pressure', 0)
        r = row.get('carry_regime', '?')
        bar = '#' * max(0, int(p * 20 + 10)) if pd.notna(p) else ''
        print(f"  {str(idx.date()):<12} {row['BOJ_rate']:>6.2f} {row['JP_CPI']:>6.1f} {p:>8.3f} {r:>10}")


# ══════════════════════════════════════════════════════════════
# Save
# ══════════════════════════════════════════════════════════════

def save_japan_data(df: pd.DataFrame, env: dict):
    """保存 Japan macro JSON"""
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)

    # Summary JSON
    out = env.copy()
    out['history'] = {}
    for idx, row in df.tail(24).iterrows():
        d = str(idx.date())
        out['history'][d] = {
            'BOJ_rate': round(float(row['BOJ_rate']), 2),
            'JP_CPI': round(float(row['JP_CPI']), 1),
            'carry_pressure': round(float(row.get('carry_pressure', 0)), 3),
            'carry_regime': row.get('carry_regime', 'unknown'),
        }

    path = TRACKING_DIR / "japan_macro.json"
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n  [JSON] {path}")


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="日本宏观 + 套息交易压力")
    parser.add_argument('--save', action='store_true', help='保存 JSON')
    parser.add_argument('--classify', action='store_true', help='仅分类当前环境')
    parser.add_argument('--history', type=int, default=None, help='显示最近 N 个月压力轨迹')
    args = parser.parse_args()

    t0 = time.time()
    print("日本宏观 × 套息交易压力 v1.0")
    print(f"开始拉取...\n")

    df = build_japan_macro_df()
    df = compute_carry_pressure(df)
    env = classify_japan_environment(df)

    elapsed = time.time() - t0
    print(f"  完成 ({elapsed:.1f}s)")

    if args.classify:
        env_short = {k: v for k, v in env.items() if k != 'history'}
        print(json.dumps(env_short, ensure_ascii=False, indent=2))
        return

    print_japan_summary(env)

    if args.history:
        print_pressure_history(df, args.history)
    else:
        print_pressure_history(df, 12)

    if args.save:
        save_japan_data(df, env)


if __name__ == '__main__':
    main()

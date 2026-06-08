"""
liquidity_monitor.py — 流动性全景监测
=====================================

货币 → 信用 → 流动性 三层传导链：

  M2（货币供给量） → 信用脉冲（新增信贷加速度） → BTC/VIX/DXY（市场实时定价）

5因子：
  BTC — 全球流动性金丝雀（领先1-2周）
  VIX — 风险偏好温度计
  DXY — 资金流向阀门（强美元=新兴市场失血）
  M2 同比 — 中国货币宽松力度
  信用脉冲 — 新增社融加速度（领先实体经济6-12月）

合成 liquidity_pressure ∈ [-1, 1]，正值=宽松利好A股。

数据源（全量中国境内可访问）：
  BTC:  ak.stock_us_daily('IBIT')  iShares Bitcoin Trust (spot ETF)
  VIX:  ak.stock_us_daily('VXX')   iPath VIX Short-Term Futures ETN
  DXY:  ak.stock_us_daily('UUP')   Invesco DB USD Index Bullish Fund
  M2:   ak.macro_china_money_supply()
  社融:  ak.macro_china_shrzgm()

Usage:
  python tools/liquidity_monitor.py              # 全量运行+打印
  python tools/liquidity_monitor.py --save        # 保存JSON
  python tools/liquidity_monitor.py --classify    # 仅输出当前环境分类
  python tools/liquidity_monitor.py --history 12  # 最近12个月压力轨迹
"""

import sys, json, time
from pathlib import Path
from datetime import datetime, date
from collections import OrderedDict

import numpy as np
import pandas as pd

_proj_root = Path(__file__).resolve().parent.parent
if str(_proj_root) not in sys.path:
    sys.path.insert(0, str(_proj_root))

SIGNALS_DIR = _proj_root / "signals" / "tracking"
OUTPUT_FILE = SIGNALS_DIR / "_macro" / "liquidity_monitor.json"

import warnings
warnings.filterwarnings('ignore')


# ══════════════════════════════════════════════════════════════════
# 数据拉取
# ══════════════════════════════════════════════════════════════════

def _get_us_daily(symbol: str) -> pd.Series:
    """拉取美股ETF日线，返回 close 序列"""
    import akshare as ak
    df = ak.stock_us_daily(symbol)
    s = df['close'].astype(float)
    s.index = pd.to_datetime(df['date'], format='%Y-%m-%d')
    return s.sort_index()


def fetch_btc() -> dict:
    """BTC → IBIT ETF (spot, BlackRock, 2024.01+)"""
    s = _get_us_daily('IBIT')
    if len(s) == 0:
        return {}
    latest = s.iloc[-1]
    # 6个月动量（正的=BTC涨=流动性宽松）
    mom6 = (s.iloc[-1] / s.iloc[max(0, len(s)-126)]) - 1 if len(s) >= 126 else 0
    mom1 = (s.iloc[-1] / s.iloc[max(0, len(s)-21)]) - 1 if len(s) >= 21 else 0
    return {
        "name": "BTC (IBIT ETF)",
        "latest": round(float(latest), 2),
        "latest_date": str(s.index[-1].date()),
        "mom_1m": round(float(mom1), 4),
        "mom_6m": round(float(mom6), 4),
        "series_6m": [round(float(x), 2) for x in s.tail(126).tolist()],
    }


def fetch_vix() -> dict:
    """VIX → VXX ETN (VIX短期期货)"""
    s = _get_us_daily('VXX')
    if len(s) == 0:
        return {}
    latest = s.iloc[-1]
    # VIX方向反转: 高VIX=恐慌=流动性收紧
    mom1 = (s.iloc[-1] / s.iloc[max(0, len(s)-21)]) - 1 if len(s) >= 21 else 0
    mom6 = (s.iloc[-1] / s.iloc[max(0, len(s)-126)]) - 1 if len(s) >= 126 else 0
    return {
        "name": "VIX (VXX ETN)",
        "latest": round(float(latest), 2),
        "latest_date": str(s.index[-1].date()),
        "mom_1m": round(float(mom1), 4),
        "mom_6m": round(float(mom6), 4),
        "series_6m": [round(float(x), 2) for x in s.tail(126).tolist()],
    }


def fetch_dxy() -> dict:
    """DXY → UUP ETF (做多美元指数)"""
    s = _get_us_daily('UUP')
    if len(s) == 0:
        return {}
    latest = s.iloc[-1]
    # DXY方向反转: 强美元=新兴市场失血=利空A股
    mom1 = (s.iloc[-1] / s.iloc[max(0, len(s)-21)]) - 1 if len(s) >= 21 else 0
    mom6 = (s.iloc[-1] / s.iloc[max(0, len(s)-126)]) - 1 if len(s) >= 126 else 0
    return {
        "name": "DXY (UUP ETF)",
        "latest": round(float(latest), 2),
        "latest_date": str(s.index[-1].date()),
        "mom_1m": round(float(mom1), 4),
        "mom_6m": round(float(mom6), 4),
        "series_6m": [round(float(x), 2) for x in s.tail(126).tolist()],
    }


def fetch_m2() -> dict:
    """中国M2货币供应量同比"""
    import akshare as ak
    df = ak.macro_china_money_supply()
    col = [c for c in df.columns if 'M2' in c and '同比' in c][0]
    s = pd.to_numeric(df[col], errors='coerce')
    # 解析月份
    dates = []
    for m in df.iloc[:, 0]:
        try:
            # 格式: "2008年01月份" 或 "200801"
            mstr = str(m).replace('年', '-').replace('月份', '').replace('月', '')
            dates.append(pd.Timestamp(mstr))
        except Exception:
            dates.append(None)
    s.index = dates
    s = s.dropna()
    s = s.sort_index()
    if len(s) == 0:
        return {}
    latest = s.iloc[-1]
    mom6 = s.iloc[-1] - s.iloc[max(0, len(s)-6)] if len(s) >= 6 else 0
    return {
        "name": "M2同比",
        "latest": round(float(latest), 2),
        "latest_date": str(s.index[-1].date()) if hasattr(s.index[-1], 'date') else str(s.index[-1]),
        "unit": "%",
        "mom_6m": round(float(mom6), 2),
        "series_12m": [round(float(x), 2) for x in s.tail(12).tolist()],
    }


def fetch_credit_impulse() -> dict:
    """信用脉冲 = 社融增量12月滚动加速度"""
    import akshare as ak
    df = ak.macro_china_shrzgm()
    col = [c for c in df.columns if '规模' in c][0]
    s = pd.to_numeric(df[col], errors='coerce')
    # 解析月份
    dates = []
    for m in df.iloc[:, 0]:
        try:
            mstr = str(m)
            if len(mstr) == 6:
                dates.append(pd.Timestamp(f"{mstr[:4]}-{mstr[4:]}-01"))
            else:
                dates.append(pd.Timestamp(mstr))
        except Exception:
            dates.append(None)
    s.index = dates
    s = s.dropna()
    s = s.sort_index()
    if len(s) == 0:
        return {}

    # 12月滚动求和 → 同比增长率
    roll12 = s.rolling(12).sum()
    # 信用脉冲 = 12月滚动的6月变化（二阶导）
    impulse = roll12.diff(6)
    latest = impulse.iloc[-1]

    return {
        "name": "信用脉冲",
        "description": "社融12月滚动→6月差分（二阶加速度）",
        "latest": round(float(latest), 0) if pd.notna(latest) else None,
        "latest_date": str(impulse.index[-1].date()) if hasattr(impulse.index[-1], 'date') else str(impulse.index[-1]),
        "unit": "亿元",
        "social_finance_latest": round(float(s.iloc[-1]), 0) if pd.notna(s.iloc[-1]) else None,
        "roll12_latest": round(float(roll12.iloc[-1]), 0) if pd.notna(roll12.iloc[-1]) else None,
        "series_12m": [round(float(x), 0) if pd.notna(x) else None for x in impulse.tail(12).tolist()],
    }


# ══════════════════════════════════════════════════════════════════
# 归一化与合成
# ══════════════════════════════════════════════════════════════════

def _norm_momentum(mom_6m: float, direction: int) -> float:
    """
    将6月动量归一化到[-1,1]。
    direction: +1=原始方向(涨=利好), -1=反转方向(涨=利空)
    """
    raw = mom_6m * direction
    return round(float(np.clip(raw * 5, -1, 1)), 3)  # 20%涨跌幅→±1


def compute_liquidity_pressure(btc, vix, dxy, m2, credit) -> dict:
    """合成流动性压力指数，正值=宽松利好"""

    factors = OrderedDict()

    # BTC: 涨=流动性宽松=利好
    if btc:
        factors["btc"] = {
            "label": "比特币",
            "raw": btc["mom_6m"],
            "direction": "+1",
            "score": _norm_momentum(btc.get("mom_6m", 0), +1),
            "latest": btc.get("latest"),
            "latest_date": btc.get("latest_date"),
        }

    # VIX: 涨=恐慌=利空 (反转)
    if vix:
        factors["vix"] = {
            "label": "VIX恐慌",
            "raw": vix["mom_6m"],
            "direction": "-1",
            "score": _norm_momentum(vix.get("mom_6m", 0), -1),
            "latest": vix.get("latest"),
            "latest_date": vix.get("latest_date"),
        }

    # DXY: 涨=强美元=利空 (反转)
    if dxy:
        factors["dxy"] = {
            "label": "美元指数",
            "raw": dxy["mom_6m"],
            "direction": "-1",
            "score": _norm_momentum(dxy.get("mom_6m", 0), -1),
            "latest": dxy.get("latest"),
            "latest_date": dxy.get("latest_date"),
        }

    # M2: 同比上升=宽松=利好
    if m2:
        # M2 同比是百分点变化，不是收益率
        m2_score = round(float(np.clip(m2.get("mom_6m", 0) * 2, -1, 1)), 3)
        factors["m2"] = {
            "label": "M2同比",
            "raw": m2.get("latest"),
            "delta_6m": m2.get("mom_6m"),
            "direction": "+1",
            "score": m2_score,
            "latest_date": m2.get("latest_date"),
        }

    # 信用脉冲: 正加速=利好
    if credit and credit.get("latest") is not None:
        # 信用脉冲量级大（千亿级），用相对变化
        roll12 = credit.get("roll12_latest", 1)
        ci_score = round(float(np.clip(credit["latest"] / max(roll12, 1) * 10, -1, 1)), 3) if roll12 else 0
        factors["credit_impulse"] = {
            "label": "信用脉冲",
            "raw": credit["latest"],
            "direction": "+1",
            "score": ci_score,
            "latest_date": credit.get("latest_date"),
        }

    scores = [f["score"] for f in factors.values()]
    pressure = round(float(np.mean(scores)), 3) if scores else 0

    # 区间判断
    if pressure > 0.15:
        regime = "easing"
        regime_label = "流动性宽松"
    elif pressure > -0.15:
        regime = "neutral"
        regime_label = "流动性中性"
    elif pressure > -0.4:
        regime = "tightening"
        regime_label = "流动性收紧"
    else:
        regime = "crisis"
        regime_label = "流动性危机"

    return {
        "pressure": pressure,
        "regime": regime,
        "regime_label": regime_label,
        "factors": {k: {kk: vv for kk, vv in v.items() if kk not in ("latest_date",)}
                    for k, v in factors.items()},
        "factor_details": {k: v for k, v in factors.items()},
    }


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def run_monitor(save: bool = False, classify_only: bool = False,
                history: int = 0) -> dict:
    """运行流动性全景监测"""
    if not classify_only:
        print("=" * 55)
        print("  流动性全景监测 — 货币 → 信用 → 流动性")
        print("=" * 55)

    print("[liquidity] 拉取 BTC (IBIT)...")
    btc = fetch_btc()
    print(f"[liquidity]   → {btc.get('latest', 'FAIL')} @ {btc.get('latest_date', '?')}")

    print("[liquidity] 拉取 VIX (VXX)...")
    vix = fetch_vix()
    print(f"[liquidity]   → {vix.get('latest', 'FAIL')} @ {vix.get('latest_date', '?')}")

    print("[liquidity] 拉取 DXY (UUP)...")
    dxy = fetch_dxy()
    print(f"[liquidity]   → {dxy.get('latest', 'FAIL')} @ {dxy.get('latest_date', '?')}")

    print("[liquidity] 拉取 M2...")
    m2 = fetch_m2()
    print(f"[liquidity]   → {m2.get('latest', 'FAIL')}% @ {m2.get('latest_date', '?')}")

    print("[liquidity] 计算 信用脉冲...")
    credit = fetch_credit_impulse()
    print(f"[liquidity]   → {credit.get('latest', 'FAIL')} @ {credit.get('latest_date', '?')}")

    result = compute_liquidity_pressure(btc, vix, dxy, m2, credit)

    if classify_only:
        print(json.dumps({
            "pressure": result["pressure"],
            "regime": result["regime"],
            "regime_label": result["regime_label"],
        }, ensure_ascii=False, indent=2))
        return result

    print(f"\n  ─── 流动性全景 ───")
    print(f"  合成压力: {result['pressure']:.3f} → {result['regime_label']}")
    print()
    for key, f in result["factor_details"].items():
        label = f["label"]
        score = f["score"]
        bar = "█" * abs(int(score * 20)) if abs(score) > 0.05 else "—"
        sign = "+" if score > 0 else ""
        print(f"  {label:8s} | {score:+6.3f} | {bar}")

    output = {
        "date": date.today().isoformat(),
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **result,
    }

    if history > 0 and btc:
        # 输出历史轨迹（简化版，只用BTC做参照）
        hist = []
        btc_series = btc.get("series_6m", [])
        for i, val in enumerate(btc_series[-min(history * 21, len(btc_series)):]):
            hist.append({"idx": i, "btc": val})
        output["history"] = hist

    if save:
        SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n  -> saved: {OUTPUT_FILE}")

    return output


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--save", action="store_true")
    p.add_argument("--classify", action="store_true")
    p.add_argument("--history", type=int, default=0)
    args = p.parse_args()
    run_monitor(save=args.save, classify_only=args.classify, history=args.history)

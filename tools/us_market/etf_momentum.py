# -*- coding: utf-8 -*-
"""
US ETF 势能评分系统 v2.0 — 通达信 RSI势能2 公式镜像
==================================================

v2.0: 扩展至 50+ ETF，覆盖宽基/GICS行业/科技子版/金融/地产/医药/能源/军工/中国/加密

用法:
  python tools/us_market/etf_momentum.py --save
  python tools/us_market/etf_momentum.py --search SMH
  python tools/us_market/etf_momentum.py --category "Tech & AI"
"""

import argparse
import json
import sys
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import akshare as ak
from tools.sector_momentum import calc_index_x1

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKING_DIR = PROJECT_ROOT / "signals" / "tracking"
REPORT_DIR = PROJECT_ROOT / "reports" / "us_market"

# ── US ETF 宇宙 v2.0 (50+ ETFs) ──
US_ETF_UNIVERSE = OrderedDict({
    "Broad Market": OrderedDict({
        "SPY": "S&P 500",
        "QQQ": "Nasdaq 100",
        "DIA": "Dow Jones",
        "IWM": "Russell 2000",
    }),
    "GICS Sectors": OrderedDict({
        "XLK": "Technology",
        "XLF": "Financials",
        "XLE": "Energy",
        "XLV": "Health Care",
        "XLI": "Industrials",
        "XLY": "Consumer Discretionary",
        "XLP": "Consumer Staples",
        "XLC": "Communication Services",
        "XLU": "Utilities",
        "XLRE": "Real Estate",
        "XLB": "Materials",
    }),
    "Tech & AI": OrderedDict({
        "SMH": "Semiconductors (VanEck)",
        "SOXX": "Semiconductors (iShares)",
        "IGV": "Software",
        "CLOU": "Cloud Computing",
        "WCLD": "Cloud SaaS",
        "CIBR": "Cybersecurity",
        "HACK": "Cybersecurity (ETFMG)",
        "BOTZ": "AI & Robotics",
        "AIQ": "AI Powered Equity",
        "ARKK": "ARK Innovation",
        "ARKW": "ARK Next Gen Internet",
    }),
    "Finance & Fintech": OrderedDict({
        "KRE": "Regional Banks",
        "KBE": "Banking",
        "ARKF": "ARK Fintech",
    }),
    "Real Estate & Infrastructure": OrderedDict({
        "XHB": "Homebuilders (SPDR)",
        "ITB": "Homebuilders (iShares)",
        "PAVE": "US Infrastructure",
        "IGF": "Global Infrastructure",
    }),
    "Healthcare & Biotech": OrderedDict({
        "IBB": "Biotech (iShares)",
        "XBI": "Biotech Equal-Weight",
        "ARKG": "ARK Genomics",
    }),
    "Energy & Materials": OrderedDict({
        "XOP": "Oil & Gas Exploration",
        "OIH": "Oil Services",
        "ICLN": "Clean Energy",
        "TAN": "Solar Energy",
        "XME": "Metals & Mining",
        "GDX": "Gold Miners",
        "SLX": "Steel Producers",
    }),
    "Defense & Industrial": OrderedDict({
        "ITA": "Aerospace & Defense",
        "XLI": "Industrials",
    }),
    "Consumer & Retail": OrderedDict({
        "XRT": "Retail (Equal-Weight)",
        "XLY": "Consumer Discretionary",
    }),
    "China & Emerging Markets": OrderedDict({
        "KWEB": "China Internet",
        "FXI": "China Large-Cap",
        "MCHI": "China Broad (iShares)",
        "EEM": "Emerging Markets",
    }),
    "Crypto & Alternatives": OrderedDict({
        "BITO": "Bitcoin Futures",
    }),
})


def fetch_etf_daily(symbol: str) -> tuple | None:
    """拉取单只 ETF 日线，返回 (close, volume) numpy 数组"""
    try:
        df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
        if df is None or len(df) < 65:
            return None
        close = df["close"].to_numpy(dtype=np.float64)
        volume = df["volume"].to_numpy(dtype=np.float64)
        return close, volume
    except Exception as e:
        print(f"  [{symbol}] 拉取失败: {e}")
        return None


def calc_all_us_etf_scores() -> list[dict]:
    """遍历所有 ETF，计算 X_1 势能评分"""
    results = []
    total = sum(len(v) for v in US_ETF_UNIVERSE.values())

    for cat, etfs in US_ETF_UNIVERSE.items():
        for symbol, name in etfs.items():
            print(f"  [{symbol}] {name} ...", end=" ", flush=True)
            arrs = fetch_etf_daily(symbol)
            if arrs is None:
                print("无数据/数据不足")
                results.append({
                    "symbol": symbol, "name": name, "category": cat,
                    "x1": None, "close": None, "n_days": 0, "error": "无数据"
                })
                continue

            close, volume = arrs
            x1 = calc_index_x1(close, volume)
            latest_close = float(close[-1])
            print(f"X_1={x1:.2f}  收盘={latest_close:.2f}")
            results.append({
                "symbol": symbol,
                "name": name,
                "category": cat,
                "x1": round(x1, 2),
                "close": latest_close,
                "n_days": len(close),
            })
            time.sleep(0.3)

    return results


def report_rankings(results: list[dict], category: str = None):
    """终端打印排名"""
    valid = [r for r in results if r.get("x1") is not None]
    if category:
        valid = [r for r in valid if r["category"] == category]
    valid.sort(key=lambda r: r["x1"], reverse=True)

    title = f"US ETF 势能排名 (RSI势能2 X_1)"
    if category:
        title += f" — {category}"
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)
    print(f"{'排名':<5} {'ETF':<8} {'名称':<30} {'类别':<24} {'X_1':>7} {'收盘':>10}")
    print("-" * 80)

    for i, r in enumerate(valid, 1):
        print(f"{i:<5} {r['symbol']:<8} {r['name']:<30} {r['category']:<24} {r['x1']:>7.2f} {r['close']:>10.2f}")

    failed = [r for r in results if r.get("x1") is None]
    if failed:
        print(f"\n  失败 ({len(failed)}): {', '.join(r['symbol'] for r in failed)}")


def save_results(results: list[dict], date_str: str = None):
    """保存 JSON + Markdown"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = TRACKING_DIR / "_macro" / "us_sector_momentum.json"
    payload = {
        "date": date_str,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_etfs": len(results),
        "etfs": results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n[JSON] {json_path}")

    # Markdown
    md_path = REPORT_DIR / f"{date_str}_us_momentum.md"
    valid = [r for r in results if r.get("x1") is not None]
    valid.sort(key=lambda r: r["x1"], reverse=True)

    lines = [
        f"# US ETF 势能日报 ({date_str})",
        "",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**公式**: 通达信 RSI势能2 (X_1)",
        f"**ETF 总数**: {len(results)} (有效: {len(valid)})",
        "",
        "## 势能排名",
        "",
        "| 排名 | ETF | 名称 | 类别 | X_1 | 收盘价 | 数据天数 |",
        "|------|-----|------|------|-----|--------|----------|",
    ]
    for i, r in enumerate(valid, 1):
        lines.append(f"| {i} | {r['symbol']} | {r['name']} | {r['category']} | {r['x1']:.2f} | {r['close']:.2f} | {r.get('n_days', '?')} |")

    # Category summaries
    lines.append("")
    lines.append("## 类别轮动概览")
    lines.append("")
    from collections import defaultdict
    cat_scores = defaultdict(list)
    for r in valid:
        cat_scores[r["category"]].append(r["x1"])
    lines.append("| 类别 | ETF数 | 平均X_1 | 最强 | 最弱 |")
    lines.append("|------|-------|---------|------|------|")
    for cat in cat_scores:
        scores = cat_scores[cat]
        best = max(valid, key=lambda r: r["x1"] if r["category"] == cat else -999)
        worst = min(valid, key=lambda r: r["x1"] if r["category"] == cat else 999)
        lines.append(f"| {cat} | {len(scores)} | {sum(scores)/len(scores):.2f} | {best['symbol']}({best['x1']:.1f}) | {worst['symbol']}({worst['x1']:.1f}) |")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[MD]   {md_path}")


def search_etf(results: list[dict], keyword: str):
    """搜索 ETF"""
    keyword = keyword.upper()
    for r in results:
        if keyword in r["symbol"] or keyword.lower() in r["name"].lower():
            status = f"X_1={r['x1']:.2f}" if r.get("x1") is not None else "无数据"
            print(f"  {r['symbol']} {r['name']} [{r['category']}] {status}  收盘={r.get('close', '?')}")


def main():
    parser = argparse.ArgumentParser(description="US ETF 势能评分 (RSI势能2) v2.0")
    parser.add_argument("--save", action="store_true", help="保存 JSON + Markdown")
    parser.add_argument("--search", type=str, help="搜索指定 ETF")
    parser.add_argument("--category", type=str, help="仅显示指定类别")
    parser.add_argument("--date", type=str, help="日期标签 (默认今天)")
    args = parser.parse_args()

    total = sum(len(v) for v in US_ETF_UNIVERSE.values())
    print(f"US ETF 势能评分 v2.0 — RSI势能2 镜像")
    print(f"ETF 总数: {total}")
    print(f"开始拉取...\n")

    results = calc_all_us_etf_scores()

    if args.search:
        search_etf(results, args.search)
    else:
        report_rankings(results, args.category)

    if args.save:
        save_results(results, args.date)


if __name__ == "__main__":
    main()

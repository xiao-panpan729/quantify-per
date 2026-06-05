# -*- coding: utf-8 -*-
"""
US 明星股动量评分 v1.0 — 通达信 RSI势能2 公式镜像
===============================================

对 ~55 只美股核心标的做动量评分，覆盖 Mag7 / 半导体链 / AI SaaS / 金融 / 医药 / 能源 / 消费 / 军工 / 加密。

用法:
  python tools/us_market/star_stocks.py --save
  python tools/us_market/star_stocks.py --search NVDA
  python tools/us_market/star_stocks.py --category "AI & Software"
"""

import argparse
import json
import sys
import time
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import akshare as ak
from tools.sector_momentum import calc_index_x1

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKING_DIR = PROJECT_ROOT / "signals" / "tracking"
REPORT_DIR = PROJECT_ROOT / "reports" / "us_market"

# ── US 明星股宇宙 (~55 stocks) ──
US_STAR_STOCKS = OrderedDict({
    "Magnificent 7": OrderedDict({
        "AAPL": "Apple",
        "MSFT": "Microsoft",
        "NVDA": "NVIDIA",
        "GOOGL": "Alphabet",
        "AMZN": "Amazon",
        "META": "Meta Platforms",
        "TSLA": "Tesla",
    }),
    "Semiconductor Chain": OrderedDict({
        "AMD": "AMD",
        "AVGO": "Broadcom",
        "QCOM": "Qualcomm",
        "MU": "Micron",
        "ASML": "ASML",
        "TSM": "TSMC",
        "INTC": "Intel",
        "AMAT": "Applied Materials",
        "LRCX": "Lam Research",
        "KLAC": "KLA Corp",
        "MRVL": "Marvell Technology",
    }),
    "AI & Software": OrderedDict({
        "PLTR": "Palantir",
        "CRWD": "CrowdStrike",
        "SNOW": "Snowflake",
        "NET": "Cloudflare",
        "DDOG": "Datadog",
        "MDB": "MongoDB",
        "NOW": "ServiceNow",
        "ADBE": "Adobe",
        "CRM": "Salesforce",
        "ORCL": "Oracle",
    }),
    "Finance": OrderedDict({
        "JPM": "JPMorgan",
        "GS": "Goldman Sachs",
        "BAC": "Bank of America",
        "MS": "Morgan Stanley",
        "V": "Visa",
        "MA": "Mastercard",
        "AXP": "AmEx",
        "BLK": "BlackRock",
    }),
    "Healthcare": OrderedDict({
        "LLY": "Eli Lilly",
        "UNH": "UnitedHealth",
        "JNJ": "J&J",
        "ABBV": "AbbVie",
        "PFE": "Pfizer",
        "MRK": "Merck",
    }),
    "Energy": OrderedDict({
        "XOM": "Exxon Mobil",
        "CVX": "Chevron",
        "COP": "ConocoPhillips",
        "SLB": "Schlumberger",
        "EOG": "EOG Resources",
    }),
    "Consumer & Retail": OrderedDict({
        "HD": "Home Depot",
        "NKE": "Nike",
        "SBUX": "Starbucks",
        "COST": "Costco",
        "WMT": "Walmart",
        "MCD": "McDonald's",
        "DIS": "Disney",
    }),
    "Industrial & Defense": OrderedDict({
        "CAT": "Caterpillar",
        "GE": "GE Aerospace",
        "BA": "Boeing",
        "RTX": "RTX Corp",
        "HON": "Honeywell",
        "DE": "Deere & Co",
    }),
    "Crypto & Alts": OrderedDict({
        "COIN": "Coinbase",
        "MSTR": "MicroStrategy",
        "MARA": "Marathon Digital",
        "RIOT": "Riot Platforms",
    }),
})


def fetch_stock_daily(symbol: str) -> tuple | None:
    """拉取单只美股日线，返回 (close, volume) numpy 数组"""
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


def calc_all_us_stock_scores() -> list[dict]:
    """遍历所有明星股，计算 X_1 势能评分"""
    results = []
    total = sum(len(v) for v in US_STAR_STOCKS.values())

    for cat, stocks in US_STAR_STOCKS.items():
        for symbol, name in stocks.items():
            print(f"  [{symbol}] {name} ...", end=" ", flush=True)
            arrs = fetch_stock_daily(symbol)
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


def report_stock_rankings(results: list[dict], category: str = None):
    """终端打印排名 — 全量 + 分类Top"""
    valid = [r for r in results if r.get("x1") is not None]
    valid.sort(key=lambda r: r["x1"], reverse=True)

    print("\n" + "=" * 90)
    print(f"  US 明星股动量排名 (RSI势能2 X_1)")
    print("=" * 90)
    print(f"{'排名':<5} {'代码':<8} {'名称':<22} {'类别':<22} {'X_1':>7} {'收盘':>10}")
    print("-" * 90)

    display = [r for r in valid if not category or r["category"] == category] if category else valid[:30]

    for i, r in enumerate(display, 1):
        print(f"{i:<5} {r['symbol']:<8} {r['name']:<22} {r['category']:<22} {r['x1']:>7.2f} {r['close']:>10.2f}")

    if not category:
        print(f"\n  ... (共 {len(valid)} 只有效，显示 Top 30)")

    # Category summary
    print(f"\n{'='*90}")
    print(f"  类别轮动快照")
    print(f"{'='*90}")
    cat_scores = defaultdict(list)
    for r in valid:
        cat_scores[r["category"]].append(r["x1"])
    print(f"  {'类别':<26} {'只数':>4} {'平均':>7} {'最强':>22} {'最弱':>22}")
    print(f"  {'-'*80}")
    for cat in cat_scores:
        scores = cat_scores[cat]
        avg = sum(scores) / len(scores)
        cat_stocks = [r for r in valid if r["category"] == cat]
        best = max(cat_stocks, key=lambda r: r["x1"])
        worst = min(cat_stocks, key=lambda r: r["x1"])
        print(f"  {cat:<26} {len(scores):>4} {avg:>7.2f} {best['symbol']}({best['x1']:.1f}){'':>14} {worst['symbol']}({worst['x1']:.1f})")

    failed = [r for r in results if r.get("x1") is None]
    if failed:
        print(f"\n  失败 ({len(failed)}): {', '.join(r['symbol'] for r in failed)}")


def save_stock_results(results: list[dict], date_str: str = None):
    """保存 JSON + Markdown + 概念链动量"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = TRACKING_DIR / "us_star_momentum.json"
    payload = {
        "date": date_str,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_stocks": len(results),
        "stocks": results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n[JSON] {json_path}")

    # ── 概念链动量 ──
    from tools.us_market.concept_chains import compute_chain_momentum, print_concept_ranking
    chain_scores = compute_chain_momentum(star_scores=results)
    if chain_scores:
        chain_json_path = TRACKING_DIR / "us_concept_momentum.json"
        chain_payload = {
            "date": date_str,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "n_concepts": len(chain_scores),
            "chains": [{
                "chain": c["chain"],
                "type": c["type"],
                "avg_x1": c["avg_x1"],
                "n_valid": c["n_valid"],
                "top3": [{"symbol": s, "x1": x} for s, x in c["top3"]],
                "bottom3": [{"symbol": s, "x1": x} for s, x in c["bottom3"]],
            } for c in chain_scores],
        }
        with open(chain_json_path, "w", encoding="utf-8") as f:
            json.dump(chain_payload, f, ensure_ascii=False, indent=2)
        print(f"[JSON] {chain_json_path}")
        print_concept_ranking(chain_scores)

    # Markdown
    md_path = REPORT_DIR / f"{date_str}_us_stars.md"
    valid = [r for r in results if r.get("x1") is not None]
    valid.sort(key=lambda r: r["x1"], reverse=True)

    lines = [
        f"# US 明星股动量日报 ({date_str})",
        "",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**公式**: 通达信 RSI势能2 (X_1)",
        f"**标的**: {len(results)} 只 (有效: {len(valid)})",
        "",
        "## 动量排名 Top 30",
        "",
        "| 排名 | 代码 | 名称 | 类别 | X_1 | 收盘价 |",
        "|------|------|------|------|-----|--------|",
    ]
    for i, r in enumerate(valid[:30], 1):
        lines.append(f"| {i} | {r['symbol']} | {r['name']} | {r['category']} | {r['x1']:.2f} | {r['close']:.2f} |")

    lines.append("")
    lines.append("## 类别轮动")
    lines.append("")
    cat_scores = defaultdict(list)
    for r in valid:
        cat_scores[r["category"]].append(r["x1"])
    lines.append("| 类别 | 只数 | 平均X_1 | 最强 | 最弱 |")
    lines.append("|------|------|---------|------|------|")
    for cat in cat_scores:
        scores = cat_scores[cat]
        cat_stocks = [r for r in valid if r["category"] == cat]
        best = max(cat_stocks, key=lambda r: r["x1"])
        worst = min(cat_stocks, key=lambda r: r["x1"])
        lines.append(f"| {cat} | {len(scores)} | {sum(scores)/len(scores):.2f} | {best['symbol']}({best['x1']:.1f}) | {worst['symbol']}({worst['x1']:.1f}) |")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[MD]   {md_path}")

    # 概念链日报
    if chain_scores:
        md_chain_path = REPORT_DIR / f"{date_str}_us_concept_rotation.md"
        chain_lines = [
            f"# US 概念链轮动日报 ({date_str})",
            "",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"**概念链**: {len(chain_scores)} 条 (基于ETF持仓 + 人工维护)",
            "",
            "## 概念链动量排名",
            "",
            "| 排名 | 概念链 | 类型 | 成分 | 有效 | 均X₁ | Top 3 领涨股 |",
            "|------|--------|------|------|------|------|------------|",
        ]
        for i, c in enumerate(chain_scores, 1):
            top_str = " / ".join(f"{s}({x:+.1f})" for s, x in c["top3"])
            chain_lines.append(f"| {i} | {c['chain']} | {c['type']} | {c['n_stocks']} | {c['n_valid']} | {c['avg_x1']:.2f} | {top_str} |")
        chain_lines.append("")
        chain_lines.append("## 概念链接入说明")
        chain_lines.append("")
        chain_lines.append("- **etf_holdings**: ETF发行商维护的持仓数据自动构建")
        chain_lines.append("- **manual**: 人工整理的产业链/主题链（不定期更新）")
        chain_lines.append("- **新增概念链**: 编辑 `tools/us_market/concept_chains.json`，在 `concepts` 下加一条即可")
        chain_lines.append("")
        with open(md_chain_path, "w", encoding="utf-8") as f:
            f.write("\n".join(chain_lines) + "\n")
        print(f"[MD]   {md_chain_path}")


def search_stock(results: list[dict], keyword: str):
    """搜索个股"""
    keyword = keyword.upper()
    for r in results:
        if keyword in r["symbol"] or keyword.lower() in r["name"].lower():
            status = f"X_1={r['x1']:.2f}" if r.get("x1") is not None else "无数据"
            print(f"  {r['symbol']} {r['name']} [{r['category']}] {status}  收盘={r.get('close', '?')}")


def main():
    parser = argparse.ArgumentParser(description="US 明星股动量评分 (RSI势能2)")
    parser.add_argument("--save", action="store_true", help="保存 JSON + Markdown")
    parser.add_argument("--search", type=str, help="搜索指定个股")
    parser.add_argument("--category", type=str, help="仅显示指定类别")
    parser.add_argument("--date", type=str, help="日期标签 (默认今天)")
    args = parser.parse_args()

    total = sum(len(v) for v in US_STAR_STOCKS.values())
    print(f"US 明星股动量评分 v1.0 — RSI势能2 镜像")
    print(f"标的: {total} 只")
    print(f"开始拉取...\n")

    results = calc_all_us_stock_scores()

    if args.search:
        search_stock(results, args.search)
    else:
        report_stock_rankings(results, args.category)

    if args.save:
        save_stock_results(results, args.date)


if __name__ == "__main__":
    main()

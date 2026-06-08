# -*- coding: utf-8 -*-
"""
US 概念链引擎 v1.0 — 对标通达信概念板块
========================================

概念链 = ETF持仓自动映射 + 人工维护产业链。
一只股票可属于多条概念链（一对多）。

数据源: concept_chains.json
  - type=etf_holdings: ETF发行商维护的持仓 → 自动概念链
  - type=manual: 人工整理的产业链/主题链

用法:
  python tools/us_market/concept_chains.py              # 终端打印概念链覆盖度
  python tools/us_market/concept_chains.py --momentum   # 概念链动量排名（需先有 star/etf 评分）
  python tools/us_market/concept_chains.py --search NVDA  # 查某股属于哪些概念链
  python tools/us_market/concept_chains.py --export     # 导出概念链→股票映射表
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKING_DIR = PROJECT_ROOT / "signals" / "tracking"
CONCEPT_PATH = Path(__file__).resolve().parent / "concept_chains.json"


def load_concepts() -> dict:
    """加载概念链定义"""
    with open(CONCEPT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_chain_stocks(chain_name: str) -> list[str]:
    """获取某条概念链的所有成分股"""
    data = load_concepts()
    chain = data["concepts"].get(chain_name, {})
    return chain.get("stocks", [])


def get_stock_chains(symbol: str) -> list[dict]:
    """获取某只股票所属的所有概念链"""
    data = load_concepts()
    chains = []
    for name, chain in data["concepts"].items():
        if symbol.upper() in [s.upper() for s in chain["stocks"]]:
            chains.append({"name": name, "type": chain["type"], "source_etfs": chain.get("source_etfs", [])})
    return chains


def get_all_chains() -> dict:
    """返回所有概念链 {name: {type, stocks, description}}"""
    return load_concepts()["concepts"]


def build_stock_to_chains() -> dict:
    """构建 {股票代码: [概念链名, ...]} 反向索引"""
    data = load_concepts()
    index = defaultdict(list)
    for name, chain in data["concepts"].items():
        for sym in chain["stocks"]:
            index[sym.upper()].append(name)
    return dict(index)


def build_chain_stock_set() -> dict:
    """构建 {概念链名: set(股票代码)} 快速查找"""
    data = load_concepts()
    return {name: set(s.upper() for s in chain["stocks"])
            for name, chain in data["concepts"].items()}


def compute_chain_momentum(star_scores: list[dict] = None,
                           etf_scores: list[dict] = None) -> list[dict]:
    """计算每条概念链的综合动量评分。

    输入: star_scores / etf_scores 来自 star_stocks.py / etf_momentum.py
    如不传，尝试从 JSON 文件加载最新评分。
    输出: [{chain_name, avg_x1, median_x1, n_stocks, n_valid, top3_stocks, bottom3_stocks}, ...]
    """
    # Load scores if not provided
    if star_scores is None:
        star_path = TRACKING_DIR / "_macro" / "us_star_momentum.json"
        if star_path.exists():
            with open(star_path, "r", encoding="utf-8") as f:
                star_scores = json.load(f).get("stocks", [])
    if etf_scores is None:
        etf_path = TRACKING_DIR / "_macro" / "us_sector_momentum.json"
        if etf_path.exists():
            with open(etf_path, "r", encoding="utf-8") as f:
                etf_scores = json.load(f).get("etfs", [])

    all_scores = {}
    for entry in (star_scores or []) + (etf_scores or []):
        sym = entry.get("symbol", "").upper()
        x1 = entry.get("x1")
        if sym and x1 is not None:
            all_scores[sym] = x1

    if not all_scores:
        print("[WARN] 无畏评分数据。请先运行 star_stocks.py --save 或 etf_momentum.py --save")
        return []

    chains = load_concepts()["concepts"]
    chain_scores = []
    for name, chain in chains.items():
        stocks = [s.upper() for s in chain["stocks"]]
        scores = [all_scores[s] for s in stocks if s in all_scores]
        if len(scores) < 3:
            continue

        scored = sorted(zip(
            [s for s in stocks if s in all_scores],
            scores
        ), key=lambda x: x[1], reverse=True)

        chain_scores.append({
            "chain": name,
            "type": chain["type"],
            "n_stocks": len(stocks),
            "n_valid": len(scores),
            "n_scored": len(scores),
            "avg_x1": round(sum(scores) / len(scores), 2),
            "median_x1": round(sorted(scores)[len(scores)//2], 2),
            "top3": [(s, round(x, 2)) for s, x in scored[:3]],
            "bottom3": [(s, round(x, 2)) for s, x in scored[-3:]],
        })

    chain_scores.sort(key=lambda c: c["avg_x1"], reverse=True)
    return chain_scores


def summarize_coverage() -> dict:
    """统计概念链覆盖度"""
    data = load_concepts()
    chains = data["concepts"]
    stock_index = build_stock_to_chains()

    return {
        "n_concepts": len(chains),
        "n_unique_stocks": len(stock_index),
        "chains": {
            name: {"n_stocks": len(chain["stocks"]), "type": chain["type"]}
            for name, chain in chains.items()
        },
        "multi_chain_stocks": [
            (sym, chains_list)
            for sym, chains_list in sorted(stock_index.items(), key=lambda x: -len(x[1]))
            if len(chains_list) >= 3
        ][:20],
    }


def print_concept_ranking(chain_scores: list[dict], top_n: int = 30):
    """终端打印概念链动量排名"""
    print(f"\n{'='*90}")
    print(f"  US 概念链轮动 (Stock + ETF 动量聚合)")
    print(f"{'='*90}")
    print(f"{'排名':<4} {'概念链':<20} {'类型':<14} {'成分':>4} {'有效':>4} {'均X₁':>7} {'Top 3 领涨股'}")
    print(f"{'-'*90}")

    for i, c in enumerate(chain_scores[:top_n], 1):
        top_str = "  ".join(f"{s}({x:+.1f})" for s, x in c["top3"])
        print(f"{i:<4} {c['chain']:<20} {c['type']:<14} {c['n_stocks']:>4} {c['n_valid']:>4} {c['avg_x1']:>7.2f} {top_str}")


def print_stock_concepts(symbol: str):
    """打印某只股票的概念链归属"""
    chains = get_stock_chains(symbol.upper())
    if not chains:
        print(f"  {symbol} 不在任何概念链中")
        return
    print(f"\n  {symbol} 归属 {len(chains)} 条概念链:")
    for c in chains:
        etf_info = f" ← {'/'.join(c['source_etfs'])}" if c["source_etfs"] else ""
        print(f"    [{c['type']}] {c['name']}{etf_info}")


def main():
    parser = argparse.ArgumentParser(description="US 概念链引擎")
    parser.add_argument("--momentum", action="store_true", help="概念链动量排名")
    parser.add_argument("--search", type=str, help="查某股概念链归属")
    parser.add_argument("--export", action="store_true", help="导出概念链→股票映射表")
    parser.add_argument("--coverage", action="store_true", help="统计覆盖度")
    args = parser.parse_args()

    if args.momentum:
        chain_scores = compute_chain_momentum()
        if chain_scores:
            print_concept_ranking(chain_scores)
            return

    if args.search:
        print_stock_concepts(args.search)
        return

    if args.export:
        chains = get_all_chains()
        export = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "n_concepts": len(chains),
            "concepts": {name: {"type": c["type"], "n_stocks": len(c["stocks"]), "stocks": c["stocks"]}
                         for name, c in chains.items()},
        }
        path = TRACKING_DIR / "_macro" / "us_concept_chains_export.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)
        print(f"  已导出: {path}")
        return

    if args.coverage:
        cov = summarize_coverage()
        print(f"\n  US 概念链覆盖度")
        print(f"  概念链: {cov['n_concepts']} 条")
        print(f"  唯一股票: {cov['n_unique_stocks']} 只")
        print(f"\n  跨链最多的股票 (≥3条):")
        for sym, chains_list in cov["multi_chain_stocks"]:
            print(f"    {sym}: {', '.join(chains_list)}")
        return

    # Default: summary
    cov = summarize_coverage()
    print(f"US 概念链引擎 v1.0")
    print(f"  概念链: {cov['n_concepts']} 条")
    print(f"  覆盖股票: {cov['n_unique_stocks']} 只")
    print(f"\n  按类型:")
    types = defaultdict(list)
    for name, info in cov["chains"].items():
        types[info["type"]].append((name, info["n_stocks"]))
    for t, items in sorted(types.items()):
        chains_str = ", ".join(f"{n}({c}只)" for n, c in items)
        print(f"    [{t}] {len(items)}条: {chains_str}")


if __name__ == "__main__":
    main()

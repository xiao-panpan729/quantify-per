# -*- coding: utf-8 -*-
"""
个股→叙事链查询工具

两步查询：
  1. 个股代码 → TDX 概念板块（从 sector_momentum_cache.json）
  2. TDX 概念板块 → 叙事链 S/A/B/C/D（从 tdx_sector_narrative_map.json）

用法:
    python tools/narrative_lookup.py 600438          # 单股查询
    python tools/narrative_lookup.py 600438 --detail # 详细模式
    python tools/narrative_lookup.py --batch 600438,002261,000100  # 批量
"""

import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() in ('gbk', 'cp936'):
    sys.stdout.reconfigure(encoding='utf-8')

# ── 路径 ──
BASE = Path(__file__).resolve().parent.parent
SECTOR_CACHE = BASE / "signals" / "tracking" / "_macro" / "sector_momentum_cache.json"
NARRATIVE_MAP = BASE / "narratives" / "tdx_sector_narrative_map.json"

# ── 运行时缓存 ──
_cache_stock_sectors = None
_cache_narrative_map = None


def load_stock_sectors():
    """加载个股→板块映射"""
    global _cache_stock_sectors
    if _cache_stock_sectors is not None:
        return _cache_stock_sectors
    if not SECTOR_CACHE.exists():
        print(f"[WARN] sector_momentum_cache.json 不存在，请先运行 sector_momentum.py")
        return {}
    with open(SECTOR_CACHE, "r", encoding="utf-8") as f:
        data = json.load(f)
    _cache_stock_sectors = data.get("stock_sectors", {})
    return _cache_stock_sectors


def _fallback_block_gn(code6: str) -> list:
    """从通达信 block_gn.dat 直接查板块（缓存中查不到时的后备）"""
    try:
        from pytdx.reader import BlockReader
        from pathlib import Path
        gn_path = Path("C:/zd_cjzq/T0002/hq_cache/block_gn.dat")
        if not gn_path.exists():
            return []
        reader = BlockReader()
        df = reader.get_df(str(gn_path), 0)
        mask = df["code"] == code6
        return df.loc[mask, "blockname"].tolist()
    except Exception:
        return []


def load_narrative_map():
    """加载板块→叙事链映射"""
    global _cache_narrative_map
    if _cache_narrative_map is not None:
        return _cache_narrative_map
    if not NARRATIVE_MAP.exists():
        print(f"[WARN] tdx_sector_narrative_map.json 不存在")
        return {}
    with open(NARRATIVE_MAP, "r", encoding="utf-8") as f:
        _cache_narrative_map = json.load(f)
    return _cache_narrative_map


def query_stock_narrative(code: str) -> dict:
    """
    查询个股的叙事链评分

    参数:
      code: 6位代码 (600438) 或 8位全码 (sh600438)

    返回:
      {
        "code": "600438",
        "tdx_sectors": ["光伏", "HJT电池", ...],
        "narratives": [
          {"chain": "光伏", "id": "#29", "grade": "B", "from_sector": "光伏"},
          ...
        ],
        "best_grade": "B",        # 最高等级 (S>A>B>C>None)
        "best_chain": "光伏",
        "all_grades": {"S": 0, "A": 0, "B": 2, "C": 0}  # 各等级计数
      }
    """
    # 统一6位代码
    code6 = code[-6:] if len(code) > 6 else code

    stock_sectors = load_stock_sectors()
    narrative_map = load_narrative_map()

    # 步骤1: 查板块（缓存 → 后备原文件）
    tdx_sectors = stock_sectors.get(code6, [])
    if not tdx_sectors:
        tdx_sectors = _fallback_block_gn(code6)

    # 步骤2: 板块→叙事链
    seen = set()
    narratives = []
    for sector in tdx_sectors:
        chain_list = narrative_map.get(sector, [])
        for c in chain_list:
            dedup_key = f"{c['id']}_{c['chain']}"
            if dedup_key not in seen:
                seen.add(dedup_key)
                narratives.append({
                    "chain": c["chain"],
                    "id": c["id"],
                    "grade": c["grade"],
                    "from_sector": sector,
                })

    # 步骤3: 取最高等级
    # U = 未覆盖（品牌/公司类板块，需研报精读定级）
    grade_order = {"S": 0, "A": 1, "B": 2, "C": 3, "U": 4}
    best = None
    for n in narratives:
        g = grade_order.get(n["grade"], 99)
        if best is None or g < best["order"]:
            best = {"order": g, "chain": n["chain"], "grade": n["grade"]}

    # 步骤4: 计数
    all_grades = {"S": 0, "A": 0, "B": 0, "C": 0, "U": 0}
    for n in narratives:
        all_grades[n["grade"]] = all_grades.get(n["grade"], 0) + 1

    return {
        "code": code6,
        "tdx_sectors": tdx_sectors,
        "narratives": narratives,
        "best_grade": best["grade"] if best else None,
        "best_chain": best["chain"] if best else None,
        "all_grades": all_grades,
    }


def print_result(result: dict, detail: bool = False):
    """格式化输出"""
    code = result["code"]
    best = result["best_grade"] or "无"
    chain = result["best_chain"] or "—"
    if best == "U":
        best = "未覆盖"
    sectors = result["tdx_sectors"]
    nars = result["narratives"]
    ag = result["all_grades"]

    print(f"\n{'='*60}")
    print(f"代码: {code}")
    print(f"最高叙事等级: {best} ({chain})")
    print(f"板块总数: {len(sectors)} | 叙事故覆盖: {len(nars)} 条")
    print(f"等级分布: S={ag['S']} A={ag['A']} B={ag['B']} C={ag['C']} U={ag.get('U', 0)}")

    if detail:
        print(f"\n【所属板块】({len(sectors)} 个):")
        for s in sectors:
            print(f"  {s}")
        print(f"\n【叙事链映射】({len(nars)} 条):")
        for n in nars:
            print(f"  {n['grade']} | {n['chain']} {n['id']} ← {n['from_sector']}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="个股→叙事链查询")
    parser.add_argument("code", nargs="?", help="6位股票代码")
    parser.add_argument("--detail", action="store_true", help="详细模式")
    parser.add_argument("--batch", help="批量查询，逗号分隔")
    args = parser.parse_args()

    if not args.code and not args.batch:
        parser.print_help()
        return

    if args.batch:
        codes = [c.strip() for c in args.batch.split(",")]
        for code in codes:
            result = query_stock_narrative(code)
            print_result(result)
        return

    result = query_stock_narrative(args.code)
    print_result(result, detail=args.detail)


if __name__ == "__main__":
    main()

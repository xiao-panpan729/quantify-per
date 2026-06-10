# -*- coding: utf-8 -*-
"""
report_pdf_extractor.py — 研报PDF全文抽取 + 事件因果结构化提取

从东方财富研报缓存中，对非例行报告下载PDF、提取前N页文本、
检测4类信息点（变量变化/传导链/未来推演/交易影响），
保存到文本缓存供节点标注使用。

用法:
  # 全量跑（过滤周报后下载+提取）
  python tools/report_pdf_extractor.py --all

  # 只看某个行业
  python tools/report_pdf_extractor.py --industry 1036

  # 强制重新下载PDF
  python tools/report_pdf_extractor.py --all --force

  # 只统计不下载
  python tools/report_pdf_extractor.py --stats
"""

import json
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from pdfminer.high_level import extract_text

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking"
CACHE_PATH = SIGNALS_DIR / "_macro" / "report_cache.json"
TEXT_CACHE_PATH = SIGNALS_DIR / "_macro" / "report_text_cache.json"

PDF_BASE = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"

# ── 例行报告标题关键词（排除） ──
EXCLUDE_TITLE_KW = [
    "周报", "月报", "日报", "双周报",
    "行业跟踪", "数据跟踪", "高频跟踪",
    "复盘", "盘前", "盘后",
]

# ── 有实质内容的标题关键词（保留） ──
INCLUDE_TITLE_KW = [
    "深度", "专题", "点评", "策略",
    "展望", "前瞻", "投资策略",
    "配置", "重磅",
    "政策", "规划", "办法", "通知", "意见",
    "开工", "投产", "量产", "商用",
    "涨价", "降价", "供需",
    "拐点", "景气", "复苏",
    "突破", "量产", "国产替代",
    "框架", "方法论",
]

# ── 4个信息点的检测关键词 ──
# 变量变化: 数据/政策/指标的变化
VAR_CHANGE_KW = [
    "增长", "下降", "提升", "回落", "上行", "下行",
    "突破", "创新高", "新低",
    "扩大", "收窄", "加速", "放缓",
    "政策", "规划", "补贴", "降息", "加息",
    "涨价", "降价", "提价",
]

# 传导链: 因果关系
CAUSAL_KW = [
    "带动", "推动", "促进", "驱动",
    "导致", "引发", "引发",
    "受益于", "受制于",
    "传导", "溢出",
    "供需", "缺口", "过剩",
]

# 未来推演
FUTURE_KW = [
    "预计", "预期", "展望", "有望",
    "未来", "将", "有望",
    "预测", "目标",
    "看好", "看空",
]

# 交易影响
TRADING_KW = [
    "建议", "推荐", "关注",
    "超配", "低配", "标配",
    "买入", "增持", "持有",
    "布局", "配置",
    "风险", "提示",
]

# ── 报告分类 ──
REPORT_CATEGORIES = {
    "深度研究": ["深度", "专题"],
    "事件点评": ["点评", "快评"],
    "策略报告": ["策略", "投资策略", "配置"],
    "政策解读": ["政策", "规划", "办法"],
    "业绩分析": ["业绩", "财报", "年报", "季报"],
    "行业跟踪": ["周报", "月报", "跟踪"],
}


def load_cache() -> dict:
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_text_cache() -> dict:
    if TEXT_CACHE_PATH.exists():
        with open(TEXT_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_text_cache(cache: dict):
    TEXT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TEXT_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def is_substantive_report(title: str) -> bool:
    """判断是否为有实质内容的研报"""
    title_lower = title.lower()
    for kw in EXCLUDE_TITLE_KW:
        if kw in title:
            return False
    # 如果包含至少一个实质关键词，保留
    for kw in INCLUDE_TITLE_KW:
        if kw in title:
            return True
    # 没有实质关键词但也不是例行报告的，保留（多数是专题/行业研究）
    return True


def classify_report(title: str) -> str:
    """给研报分类"""
    for cat, keywords in REPORT_CATEGORIES.items():
        for kw in keywords:
            if kw in title:
                return cat
    return "一般研究"


def download_and_extract(info_code: str, title: str = "",
                          max_pages: int = 5) -> dict | None:
    """下载PDF并提取前N页文本（单份，含超时）"""
    url = PDF_BASE.format(info_code=info_code)
    try:
        r = requests.get(url, timeout=(10, 30), headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        if r.status_code != 200 or r.headers.get("Content-Type", "") != "application/pdf":
            return None
        pdf_bytes = r.content
        if len(pdf_bytes) < 1000:
            return None

        # 提取前max_pages页（最长允许15s）
        pages = list(range(min(max_pages, 10)))
        text = extract_text(BytesIO(pdf_bytes), page_numbers=pages)
        return {
            "info_code": info_code,
            "title": title,
            "pages_extracted": len(pages),
            "text": text.strip(),
            "text_length": len(text.strip()),
            "pdf_size": len(pdf_bytes),
        }
    except Exception:
        return None


def extract_event_points(text: str) -> dict:
    """
    从研报正文中提取4类信息点。

    Returns:
    {
        "var_changes": [{"sentence": "...", "keywords": [...]}, ...],
        "causal_chains": [{"sentence": "...", "keywords": [...]}, ...],
        "future_outlook": [{"sentence": "...", "keywords": [...]}, ...],
        "trading_impact": [{"sentence": "...", "keywords": [...]}, ...],
        "summary": "一句话摘要（前100字）"
    }
    """
    # 按句号分割
    sentences = re.split(r"[。！？；\n]", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

    result = {
        "var_changes": [],
        "causal_chains": [],
        "future_outlook": [],
        "trading_impact": [],
    }

    for sent in sentences:
        # 变量变化
        matched_kw = [kw for kw in VAR_CHANGE_KW if kw in sent]
        if matched_kw and len(sent) < 200:
            result["var_changes"].append({
                "sentence": sent.strip(),
                "keywords": matched_kw,
            })

        # 传导链
        matched_kw = [kw for kw in CAUSAL_KW if kw in sent]
        if matched_kw and len(sent) < 300:
            result["causal_chains"].append({
                "sentence": sent.strip(),
                "keywords": matched_kw,
            })

        # 未来推演
        matched_kw = [kw for kw in FUTURE_KW if kw in sent]
        if matched_kw:
            result["future_outlook"].append({
                "sentence": sent.strip(),
                "keywords": matched_kw,
            })

        # 交易影响
        matched_kw = [kw for kw in TRADING_KW if kw in sent]
        if matched_kw:
            result["trading_impact"].append({
                "sentence": sent.strip(),
                "keywords": matched_kw,
            })

    # 摘要: 前150字
    result["summary"] = text[:150].replace("\n", " ").strip()

    return result


def scan_reports(cache: dict, industry_filter: str = None) -> list[dict]:
    """扫描缓存，返回待提取的研报清单"""
    pending = []
    for cache_key, reports in cache.items():
        if industry_filter and not cache_key.startswith(f"{industry_filter}|"):
            continue
        for r in reports:
            title = r.get("title", "")
            info_code = r.get("info_code", r.get("infoCode", ""))
            if not info_code:
                continue
            if not is_substantive_report(title):
                continue
            pending.append({
                "info_code": info_code,
                "title": title,
                "publish_date": r.get("publish_date", "")[:10],
                "industry_code": cache_key.split("|")[0],
                "cache_key": cache_key,
            })
    return pending


def batch_extract(pending: list[dict], text_cache: dict,
                   force: bool = False, max_workers: int = 4):
    """批量下载PDF + 提取文本（每50份增量保存）"""
    to_download = []
    for p in pending:
        if p["info_code"] in text_cache and not force:
            continue
        to_download.append(p)

    print(f"待下载: {len(to_download)} 份 (缓存已有: {len(pending)-len(to_download)})")

    if not to_download:
        return text_cache, 0, 0

    t0 = time.time()
    success = 0
    failed = 0

    # 多线程下载+提取
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        fut_map = {
            executor.submit(download_and_extract, p["info_code"], p["title"]): p
            for p in to_download
        }
        for i, fut in enumerate(as_completed(fut_map)):
            p = fut_map[fut]
            result = fut.result()
            if result:
                result["industry_code"] = p["industry_code"]
                result["publish_date"] = p["publish_date"]
                events = extract_event_points(result["text"])
                result["events"] = events
                result["category"] = classify_report(p["title"])
                text_cache[p["info_code"]] = result
                success += 1
            else:
                failed += 1

            # 增量保存
            if (i + 1) % 50 == 0:
                save_text_cache(text_cache)

            if (i + 1) % 20 == 0 or (i + 1) == len(to_download):
                print(f"  进度: {i+1}/{len(to_download)} 成功={success} 失败={failed} 耗时估算={time.time()-t0:.0f}s")
                sys.stdout.flush()

    return text_cache, success, failed


def print_stats(cache: dict):
    """打印统计"""
    reports = []
    for key, batch in cache.items():
        if isinstance(batch, list):
            for r in batch:
                reports.append(r)
        elif isinstance(batch, dict):
            reports.append(batch)

    # 从text_cache统计
    text_cache = load_text_cache()
    print(f"\n=== 研报文本缓存统计 ===")
    print(f"  缓存研报总数: {sum(len(v) for v in cache.values() if isinstance(v, list))}")
    print(f"  已提取PDF: {len(text_cache)}")
    if text_cache:
        cats = {}
        for v in text_cache.values():
            cat = v.get("category", "未知")
            cats[cat] = cats.get(cat, 0) + 1
        print(f"  分类:")
        for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
            print(f"    {cat}: {count}")
        total_events = sum(
            len(v.get("events", {}).get("var_changes", []))
            + len(v.get("events", {}).get("causal_chains", []))
            + len(v.get("events", {}).get("future_outlook", []))
            + len(v.get("events", {}).get("trading_impact", []))
            for v in text_cache.values()
        )
        print(f"  总事件点: {total_events}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="研报PDF全文抽取 + 事件提取")
    parser.add_argument("--all", action="store_true", help="全量跑")
    parser.add_argument("--industry", help="只跑指定行业代码")
    parser.add_argument("--force", action="store_true", help="强制重新下载")
    parser.add_argument("--stats", action="store_true", help="只统计不下载")
    parser.add_argument("--workers", type=int, default=4, help="并行下载数 (默认4)")
    args = parser.parse_args()

    if args.stats:
        cache = load_cache()
        text_cache = load_text_cache()
        print_stats(cache)

        # 统计待提取
        pending = scan_reports(cache)
        print(f"\n待提取研报 (过滤周报后): {len(pending)} 份")
        if pending:
            cats = {}
            for p in pending:
                cat = classify_report(p["title"])
                cats[cat] = cats.get(cat, 0) + 1
            for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
                print(f"  {cat}: {count}")

            # 按行业统计
            industries = {}
            for p in pending:
                ic = p["industry_code"]
                industries[ic] = industries.get(ic, 0) + 1
            print(f"\n按行业代码分布:")
            for ic, count in sorted(industries.items(), key=lambda x: -x[1])[:15]:
                print(f"  {ic}: {count} 份")
        return

    if not args.all and not args.industry:
        parser.print_help()
        return

    t0 = time.time()

    cache = load_cache()
    text_cache = load_text_cache()

    print(f"加载缓存: {sum(len(v) for v in cache.values() if isinstance(v, list))} 条研报")
    print(f"文本缓存: {len(text_cache)} 份已提取")

    pending = scan_reports(cache, industry_filter=args.industry)
    print(f"待提取 (过滤周报后): {len(pending)} 份")

    text_cache, success, failed = batch_extract(
        pending, text_cache, force=args.force, max_workers=args.workers
    )

    save_text_cache(text_cache)
    elapsed = time.time() - t0

    print(f"\n完成! 成功={success} 失败={failed} 耗时={elapsed:.0f}s")
    print_stats(cache)


if __name__ == "__main__":
    main()

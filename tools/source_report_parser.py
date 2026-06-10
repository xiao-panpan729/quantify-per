# -*- coding: utf-8 -*-
"""
聚合日报 Markdown 解析器
=========================
从 reports/sources/{date}_sources.md 中提取 10 个结构化数据块，
支持 LLM Prompt 格式化输出。

数据块清单:
  1. wechat_views   — 微信公众号最新观点 (📰)
  2. shock          — 消息面突发事件 (🔴)
  3. liquidity      — 全球流动性 5 因子压力指数 (🌍)
  4. china_macro    — 中国宏观快照 (🇨🇳)
  5. us_macro       — US 宏观环境 (🇺🇸)
  6. japan_macro    — 日本宏观 / 套息交易 (🇯🇵)
  7. us_etf         — US ETF 动量 Top/Bottom (🇺🇸 US ETF)
  8. us_stars       — US 明星股动量 Top/Bottom (⭐)
  9. concept_chains — 概念链轮动 (🔗)
  10. fundamental   — 基本面因子溢价 (📊)

用法:
    from tools.source_report_parser import read_source_report, format_blocks_for_prompt

    blocks = read_source_report("20260610")          # -> dict
    text = format_blocks_for_prompt(blocks)           # -> str
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = PROJECT_ROOT / "reports" / "sources"


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _read_file(date: str) -> Optional[str]:
    """读取源文件，不存在则返回 None"""
    path = SOURCES_DIR / f"{date}_sources.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _split_sections(text: str) -> Dict[str, str]:
    """按 ``## `` 二级标题分割 Markdown，返回 {header_title: content_text}。

    自动剔除 section 首尾的 ``---`` 分隔符及空行。
    """
    sections: Dict[str, str] = {}
    current_header: Optional[str] = None
    current_lines: List[str] = []

    for line in text.split("\n"):
        if line.startswith("## "):
            if current_header is not None:
                sections[current_header] = _clean_section(current_lines)
            current_header = line[3:].strip()
            current_lines = []
        elif current_header is not None:
            current_lines.append(line)

    if current_header is not None:
        sections[current_header] = _clean_section(current_lines)

    return sections


def _clean_section(lines: List[str]) -> str:
    """去掉 section 首尾的 ``---`` 分隔符及空行"""
    content = [l for l in lines if l.strip() != "---"]
    while content and not content[0].strip():
        content.pop(0)
    while content and not content[-1].strip():
        content.pop()
    return "\n".join(content).strip()


# ---------------------------------------------------------------------------
# 各块解析器
# ---------------------------------------------------------------------------

def _parse_wechat_views(text: str) -> List[Dict[str, Any]]:
    """解析微信公众号最新观点块

    格式:
        - **source_name** (YYYY-MM-DD): title
          └ sub_title
    """
    views: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        # 主条目: - **source** (date): title
        m = re.match(
            r'^-\s+\*\*(.+?)\*\*\s+\((\d{4}-\d{2}-\d{2})\):\s*(.*)',
            line,
        )
        if m:
            if current is not None:
                views.append(current)
            current = {
                "source": m.group(1),
                "date": m.group(2),
                "title": m.group(3).strip(),
                "sub_articles": [],
            }
            continue

        # 子条目: └ ...
        if current is not None and line.startswith("└"):
            current["sub_articles"].append(line[1:].strip())

    if current is not None:
        views.append(current)

    return views


def _parse_shock(text: str) -> Dict[str, Any]:
    """解析消息面突发事件块

    格式:
        **净冲击**: -6.4 (negative)
        **信源**: guzhang_ws=5 | wallstreetcn=352 | ...

        **子分类名称** (N条, impact=±N)
        > 新闻摘要内容...
    """
    result: Dict[str, Any] = {
        "net_impact": 0.0,
        "net_impact_label": "",
        "sources": {},
        "sub_categories": [],
    }

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # 净冲击
        m = re.match(r'\*\*净冲击\*\*:\s*([+-]?\d+(?:\.\d+)?)\s*(.*)', line)
        if m:
            result["net_impact"] = float(m.group(1))
            label_text = m.group(2).strip()
            label_m = re.search(r'\((negative|positive)\)', label_text)
            result["net_impact_label"] = label_m.group(1) if label_m else ""
            i += 1
            continue

        # 信源统计
        m = re.match(r'\*\*信源\*\*:\s*(.*)', line)
        if m:
            for part in m.group(1).split("|"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    result["sources"][k.strip()] = int(v.strip())
            i += 1
            continue

        # 子分类: **名称** (N条, impact=±N)
        m = re.match(
            r'\*\*(.+?)\*\*\s*\((\d+)条,\s*impact=([+-]?\d+)\)',
            line,
        )
        if m:
            cat: Dict[str, Any] = {
                "name": m.group(1),
                "count": int(m.group(2)),
                "impact": int(m.group(3)),
                "content": "",
            }
            content_parts: List[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith(">"):
                content_parts.append(lines[j].strip().lstrip(">").strip())
                j += 1
            cat["content"] = " ".join(content_parts)
            result["sub_categories"].append(cat)
            i = j
            continue

        i += 1

    return result


def _parse_liquidity(text: str) -> Dict[str, Any]:
    """解析全球流动性块

    格式:
        **流动性压力**: 0.115 -> neutral (流动性中性)
          - 比特币: 35.14 (score=-1.0)
          - VIX恐慌: 25.17 (score=0.951)
    """
    result: Dict[str, Any] = {
        "pressure_score": None,
        "pressure_label": "",
        "factors": [],
    }

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        m = re.match(
            r'\*\*流动性压力\*\*:\s*([+-]?\d+(?:\.\d+)?)\s*→\s*(.+)',
            line,
        )
        if m:
            result["pressure_score"] = float(m.group(1))
            result["pressure_label"] = m.group(2).strip()
            continue

        m = re.match(
            r'-\s*(.+?):\s*([+-]?\d+(?:\.\d+)?)\s*\(score=([+-]?\d+(?:\.\d+)?)\)',
            line,
        )
        if m:
            result["factors"].append({
                "name": m.group(1).strip(),
                "value": float(m.group(2)),
                "score": float(m.group(3)),
            })

    return result


def _parse_china_macro(text: str) -> str:
    """解析中国宏观快照块，原样返回文本（可能含占位符"（无数据）"）"""
    return text.strip()


def _parse_us_macro(text: str) -> Dict[str, Any]:
    """解析 US 宏观环境块

    格式:
        **分类**: 收紧 (score=-2)
        **关键数据**:
          - FEDFUNDS = 4.5
          - US_CPI = 3.8
    """
    result: Dict[str, Any] = {
        "classification": "",
        "score": None,
        "data": {},
    }

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        m = re.match(r'\*\*分类\*\*:\s*(.*?)\s*\(score=([+-]?\d+)\)', line)
        if m:
            result["classification"] = m.group(1).strip()
            result["score"] = int(m.group(2))
            continue

        m = re.match(r'-\s*(.+?)\s*=\s*([+-]?\d+(?:\.\d+)?)', line)
        if m:
            result["data"][m.group(1).strip()] = float(m.group(2))

    return result


def _parse_japan_macro(text: str) -> Dict[str, Any]:
    """解析日本宏观 / 套息交易块

    格式:
        - **BOJ利率**: 0.75 (hold)
        - **日本CPI**: 1.1
        - **套息压力**: 0.015 -> building
        - **日元信号**: strengthening
        - **A股影响**: caution ...
    """
    result: Dict[str, Any] = {}

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'-\s*\*\*(.+?)\*\*:\s*(.*)', line)
        if m:
            result[m.group(1).strip()] = m.group(2).strip()

    return result


def _parse_ranking_section(text: str) -> Dict[str, Any]:
    """解析 US ETF / US 明星股 / 概念链 通用的 Top/Bottom 排行块

    格式:
        **最弱N只**:
          - Name: x=val @price(可选)
        **最强N只**:
          - Name: val @price(可选)
    """
    result: Dict[str, Any] = {
        "top": [],
        "bottom": [],
    }
    current_section: Optional[str] = None

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        if line.startswith("**最弱"):
            current_section = "bottom"
            continue
        if line.startswith("**最强"):
            current_section = "top"
            continue

        if current_section is not None and line.startswith("- "):
            entry: Dict[str, Any] = {}
            # 带 price 格式: Name: x=val @price
            m = re.match(
                r'-\s*(.+?):\s*x[_₁]?1?=([+-]?\d+(?:\.\d+)?)\s*@([+-]?\d+(?:\.\d+)?)',
                line,
            )
            if m:
                entry["name"] = m.group(1).strip()
                entry["momentum"] = float(m.group(2))
                entry["price"] = float(m.group(3))
            else:
                # 不带 price 格式: Name: val
                m = re.match(r'-\s*(.+?):\s*([+-]?\d+(?:\.\d+)?)', line)
                if m:
                    entry["name"] = m.group(1).strip()
                    entry["momentum"] = float(m.group(2))

            if entry:
                result[current_section].append(entry)

    return result


def _parse_fundamental(text: str) -> Dict[str, Any]:
    """解析基本面因子溢价块

    格式:
        **窗口**: 20250930
          - 净资产收益率(ROE): -1.2249
          - 营收增长率: +0.3452
        **热点因子**: 营收增长率,净利润增长率
    """
    result: Dict[str, Any] = {
        "window": "",
        "factors": {},
        "hot_factors": [],
    }

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        m = re.match(r'\*\*窗口\*\*:\s*(\d+)', line)
        if m:
            result["window"] = m.group(1)
            continue

        m = re.match(r'-\s*(.+?):\s*([+-]?\d+(?:\.\d+)?)', line)
        if m:
            result["factors"][m.group(1).strip()] = float(m.group(2))
            continue

        m = re.match(r'\*\*热点因子\*\*:\s*(.*)', line)
        if m:
            result["hot_factors"] = [
                f.strip() for f in m.group(1).split(",") if f.strip()
            ]

    return result


# ---------------------------------------------------------------------------
# 块路由表  (prefix -> (output_key, parser))
# 顺序无关——每个 header 只匹配一条前缀
# ---------------------------------------------------------------------------
_BLOCK_ROUTES: List[Tuple[str, str, Any]] = [
    ("📰",                              "wechat_views",   _parse_wechat_views),
    ("🔴",                              "shock",          _parse_shock),
    ("🌍",                              "liquidity",      _parse_liquidity),
    ("🇨🇳",                              "china_macro",    _parse_china_macro),
    ("🇺🇸 US宏观",                        "us_macro",       _parse_us_macro),
    ("🇺🇸 US ETF",                       "us_etf",         _parse_ranking_section),
    ("🇯🇵",                              "japan_macro",    _parse_japan_macro),
    ("⭐",                              "us_stars",       _parse_ranking_section),
    ("🔗",                              "concept_chains", _parse_ranking_section),
    ("📊",                              "fundamental",    _parse_fundamental),
]


def _identify_block(header: str) -> Optional[str]:
    """根据 ``## `` 标题识别块类型，返回 block key；不认识则返回 None"""
    for prefix, key, _ in _BLOCK_ROUTES:
        if header.startswith(prefix):
            return key
    return None


def _get_parser(header: str):
    """根据标题获取解析函数"""
    for prefix, _, parser in _BLOCK_ROUTES:
        if header.startswith(prefix):
            return parser
    return None


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def read_source_report(date: str) -> dict:
    """读取 ``reports/sources/{date}_sources.md`` 返回结构化 dict。

    Args:
        date: 日期字符串，格式 ``YYYYMMDD``，如 ``"20260610"``。

    Returns:
        包含最多 10 个数据块的 dict，key 见模块文档字符串。
        若源文件不存在则返回空 dict（不抛异常）。
    """
    text = _read_file(date)
    if text is None:
        return {}

    sections = _split_sections(text)
    result: dict = {}

    for header, content in sections.items():
        block_key = _identify_block(header)
        if block_key is None:
            continue
        parser = _get_parser(header)
        if parser is not None:
            result[block_key] = parser(content)

    return result


def format_blocks_for_prompt(blocks: dict) -> str:
    """将结构化数据格式化为 LLM prompt 可读的文本块。

    每个块用 ``===BLOCK: block_name ===`` 包裹，
    保留关键数值，去掉冗余排版。

    只会输出 impact != 0 的突发事件子分类（高价值过滤）。

    Args:
        blocks: ``read_source_report()`` 返回的 dict。

    Returns:
        格式化后的纯文本字符串。
    """
    parts: List[str] = []

    # 固定输出顺序
    order = [
        "wechat_views",
        "shock",
        "liquidity",
        "china_macro",
        "us_macro",
        "japan_macro",
        "us_etf",
        "us_stars",
        "concept_chains",
        "fundamental",
    ]

    for key in order:
        block = blocks.get(key)
        if block is None:
            continue

        lines: List[str] = [f"===BLOCK: {key} ==="]

        if key == "wechat_views":
            for view in block:
                lines.append(
                    f"- {view['source']} ({view['date']}): {view['title']}"
                )
                for sub in view.get("sub_articles", []):
                    lines.append(f"  -> {sub}")

        elif key == "shock":
            lines.append(
                f"net_impact: {block.get('net_impact', '?')} "
                f"({block.get('net_impact_label', '')})"
            )
            srcs = block.get("sources", {})
            if srcs:
                lines.append(
                    "sources: "
                    + " | ".join(f"{k}={v}" for k, v in srcs.items())
                )
            for cat in block.get("sub_categories", []):
                if cat.get("impact", 0) == 0:
                    continue  # 只保留有实质冲击的子分类
                lines.append(
                    f"[{cat['name']}] impact={cat['impact']} "
                    f"({cat.get('count', '?')}条)"
                )
                if cat.get("content"):
                    preview = cat["content"][:200]
                    lines.append(f"  {preview}")

        elif key == "liquidity":
            lines.append(
                f"pressure: {block.get('pressure_score', '?')} "
                f"-> {block.get('pressure_label', '')}"
            )
            for f in block.get("factors", []):
                lines.append(f"  {f['name']}: {f['value']} (score={f['score']})")

        elif key == "china_macro":
            raw = block if isinstance(block, str) else str(block)
            lines.append(raw if raw else "(no data)")

        elif key == "us_macro":
            lines.append(
                f"classification: {block.get('classification', '?')} "
                f"(score={block.get('score', '?')})"
            )
            for k, v in block.get("data", {}).items():
                lines.append(f"  {k} = {v}")

        elif key == "japan_macro":
            for k, v in block.items():
                lines.append(f"  {k}: {v}")

        elif key in ("us_etf", "us_stars", "concept_chains"):
            for section_name, label in [("bottom", "WEAKEST"), ("top", "STRONGEST")]:
                items = block.get(section_name, [])
                if not items:
                    continue
                lines.append(f"  [{label}]:")
                for item in items:
                    if "price" in item:
                        lines.append(
                            f"    {item['name']}: "
                            f"x1={item['momentum']} @{item['price']}"
                        )
                    else:
                        lines.append(
                            f"    {item['name']}: {item['momentum']}"
                        )

        elif key == "fundamental":
            lines.append(f"  window: {block.get('window', '?')}")
            for name, value in block.get("factors", {}).items():
                lines.append(f"    {name}: {value:+.4f}")
            if block.get("hot_factors"):
                lines.append(
                    "  hot_factors: " + ", ".join(block["hot_factors"])
                )

        parts.append("\n".join(lines))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# CLI 入口 / 快速测试
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    test_date = sys.argv[1] if len(sys.argv) > 1 else "20260610"
    data = read_source_report(test_date)
    if data:
        print(format_blocks_for_prompt(data))
    else:
        print(f"未找到 {test_date}_sources.md")
    print("\n=== Agent 1 DONE ===")

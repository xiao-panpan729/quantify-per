"""
快讯缓存：sentiment_shock.json 的 _headlines → Obsidian Markdown
从 shock_detector 的输出 JSON 中提取所有头条，按日期+来源分组，
写入 D:\knowledge-hub\flash-news\{YYYY-MM-DD}.md

用法:
  python tools/cache_flash_news_to_hub.py                           # 缓存今天
  python tools/cache_flash_news_to_hub.py --date 2026-06-25          # 指定日期
  python tools/cache_flash_news_to_hub.py --from-json path.json      # 指定 JSON 文件

输出格式:
  D:\\knowledge-hub\\flash-news\\{YYYY-MM-DD}.md
  单文件，当日所有快讯按来源分组，每条约 - **HH:MM** 内容
"""

import os
import json
import sys
from datetime import datetime
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOCK_JSON = os.path.join(
    PROJECT_ROOT, "signals", "tracking", "_macro", "sentiment_shock.json"
)
OUTPUT_DIR = r"D:\knowledge-hub\flash-news"

SOURCE_LABELS = {
    "wallstreetcn": "华尔街见闻",
    "eastmoney_global": "东方财富全球快讯",
    "eastmoney": "东方财富",
    "cls": "财联社",
    "jin10": "金十数据",
}


def _ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def normalize_time(item):
    """统一时间格式为 HH:MM，支持多种输入格式"""
    time_val = item.get("time", "")

    if not time_val:
        return "--:--"

    # Unix 时间戳（华尔街见闻）
    if time_val.isdigit() or (time_val.startswith("-") and time_val[1:].isdigit()):
        try:
            ts = int(time_val)
            if ts > 1e9:  # 合理的时间戳范围
                return datetime.fromtimestamp(ts).strftime("%H:%M")
        except (ValueError, OSError):
            pass
        return "--:--"

    # "2026-06-26 08:06:11" 格式
    if len(time_val) >= 16:
        try:
            return datetime.strptime(time_val[:16], "%Y-%m-%d %H:%M").strftime("%H:%M")
        except ValueError:
            pass

    # 如果已经是 "HH:MM" 格式
    if len(time_val) == 5 and ":" in time_val:
        return time_val

    return time_val[:8]


def cache_flash_news_md(result_dict):
    """将 shock_detector 的结果字典缓存为 markdown。幂等：当天已有则跳过。"""
    date_str = result_dict.get("date", "")
    if not date_str:
        print("[flash-cache] 缺少 date 字段，跳过")
        return False

    # 输出路径
    out_path = os.path.join(OUTPUT_DIR, f"{date_str}.md")

    # 幂等检查
    if os.path.exists(out_path):
        print(f"[flash-cache] ⏭️  {date_str}.md 已存在，跳过")
        return False

    headlines = result_dict.get("_headlines", [])
    if not headlines:
        print(f"[flash-cache] ⏭️  {date_str} 无头条数据")
        return False

    # 按来源分组
    groups = defaultdict(list)
    for h in headlines:
        source = h.get("source", "unknown")
        label = SOURCE_LABELS.get(source, source)
        ts = normalize_time(h)
        title = h.get("title", "").strip()
        if title:
            groups[label].append((ts, title))

    # 组装 markdown
    md_lines = [
        "---",
        f"source: flash-news",
        f"date: {date_str}",
        f"total: {len(headlines)}",
        "sources:",
    ]
    for label in sorted(groups.keys()):
        md_lines.append(f"  {label}: {len(groups[label])}")
    md_lines.append("---")
    md_lines.append("")
    md_lines.append(f"# 快讯日报 — {date_str}")
    md_lines.append("")

    total_written = 0
    for label in sorted(groups.keys()):
        items = sorted(groups[label], key=lambda x: x[0])  # 按时序
        md_lines.append(f"## {label} ({len(items)}条)")
        md_lines.append("")
        for ts, title in items:
            # 转义列表中的特殊字符
            safe_title = title.replace("-", "\\-").replace("[", "\\[")
            md_lines.append(f"- **{ts}** {safe_title}")
        md_lines.append("")
        total_written += len(items)

    # 写入
    _ensure_dir(OUTPUT_DIR)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"[flash-cache] ✅ {date_str}.md 写入 ({total_written} 条)")
    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description="快讯缓存 → Obsidian Markdown")
    parser.add_argument("--date", type=str, help="指定日期 YYYY-MM-DD")
    parser.add_argument("--from-json", type=str, help="指定 JSON 文件路径")
    args = parser.parse_args()

    if args.from_json:
        fp = args.from_json
    elif args.date:
        fp = SHOCK_JSON
    else:
        fp = SHOCK_JSON

    if not os.path.exists(fp):
        print(f"[flash-cache] ❌ 文件不存在: {fp}")
        sys.exit(1)

    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)

    if args.date:
        # 检查日期是否匹配
        data_date = data.get("date", "")
        if data_date != args.date:
            print(f"[flash-cache] ⚠️  JSON 日期 {data_date} 与请求 {args.date} 不匹配")
            # 但仍然尝试处理

    cache_flash_news_md(data)


if __name__ == "__main__":
    main()

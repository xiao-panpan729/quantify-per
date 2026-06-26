"""
公众号文章 txt → Obsidian Markdown 转换
用法:
  python tools/convert_wechat_to_md.py --all         # 全量转换
  python tools/convert_wechat_to_md.py --incremental  # 增量（仅新文件）
  python tools/convert_wechat_to_md.py --account laoduo  # 单个号

输出:
  D:\\knowledge-hub\\wechat\\{account}\\{YYYY-MM-DD}\\{timestamp}_{slug}.md
"""

import os
import re
import sys
import glob
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "wechat_articles")
OUTPUT_DIR = r"D:\knowledge-hub\wechat"

# 公众号分类映射（与 _fetch_articles.py 同步）
ACCOUNT_CATEGORIES = {
    "表舅是养基大户": "宏观/观点",
    "laoduo": "宏观/观点",
    "海里的小龙龙": "宏观/观点",
    "亨特研究笔记": "宏观/观点",
    "卓哥投研笔记": "宏观/观点",
    "猫笔刀": "宏观/观点",
    "滚雪球的猫菲特闲唠嗑": "行业新闻",
    "滚雪球的猫菲特": "宏观/观点",
    "盘前": "情绪热点",
    "盘前纪要": "情绪热点",
    "一思一记": "情绪热点",
    "安静拆主线": "情绪热点",
    "灰岩金融科技": "宏观/观点",
    "中信建投证券研究": "行业新闻",
}


def parse_article(filepath):
    """解析单篇 txt 文件，返回 (title, pub_time, url, body) 或 None"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        print(f"  [跳过] 读取失败 {os.path.basename(filepath)}: {e}")
        return None

    # 解析头部
    title = ""
    pub_time = ""
    url = ""

    lines = content.split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("标题: "):
            title = line[4:].strip()
        elif line.startswith("时间: "):
            pub_time = line[4:].strip()
        elif line.startswith("链接: "):
            url = line[4:].strip()
        elif line.startswith("=" * 50):
            body_start = i + 1
            break

    if not title:
        return None  # 无法解析，跳过

    body = "\n".join(lines[body_start:]).strip()
    return title, pub_time, url, body


def safe_slug(title, max_len=40):
    """将标题转为安全的文件名片段"""
    slug = re.sub(r'[^一-鿿\w\s-]', '', title)
    slug = re.sub(r'[_\s]+', '_', slug.strip())
    return slug[:max_len]


def get_output_path(account_name, title, pub_time_str):
    """生成输出文件路径"""
    # 从发布时间提取日期
    pub_date = None
    if pub_time_str:
        try:
            dt = datetime.strptime(pub_time_str.strip()[:16], "%Y-%m-%d %H:%M")
            pub_date = dt.strftime("%Y-%m-%d")
            ts = dt.strftime("%H%M")
        except ValueError:
            pass
    if not pub_date:
        # 用今天的日期
        pub_date = datetime.now().strftime("%Y-%m-%d")
        ts = "0000"

    slug = safe_slug(title)
    out_dir = os.path.join(OUTPUT_DIR, account_name, pub_date)
    os.makedirs(out_dir, exist_ok=True)

    filename = f"{ts}_{slug}.md"
    return os.path.join(out_dir, filename)


def convert_article(filepath, account_name):
    """转换单篇文章，返回是否成功"""
    parsed = parse_article(filepath)
    if parsed is None:
        return False

    title, pub_time, url, body = parsed
    out_path = get_output_path(account_name, title, pub_time)

    # 幂等：已存在跳过
    if os.path.exists(out_path):
        return False

    category = ACCOUNT_CATEGORIES.get(account_name, "未分类")
    captured_at = datetime.now().strftime("%Y-%m-%d")

    md = [
        "---",
        f"source: 微信公众号",
        f"account: {account_name}",
        f"category: {category}",
        f"title: {title}",
        f"url: {url}",
        f"article_time: {pub_time}" if pub_time else "",
        f"captured_at: {captured_at}",
        "---",
        "",
        body,
    ]
    # 去掉空 frontmatter 字段
    md = [l for l in md if l.strip()]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    return True


def convert_account(account_name, incremental=True):
    """转换单个公众号的全部文章"""
    account_dir = os.path.join(SRC_DIR, account_name)
    if not os.path.isdir(account_dir):
        print(f"  [跳过] 目录不存在: {account_name}")
        return 0, 0

    files = sorted(glob.glob(os.path.join(account_dir, "*.txt")))
    if not files:
        print(f"  [跳过] 无 txt 文件: {account_name}")
        return 0, 0

    converted = 0
    skipped = 0
    for fp in files:
        if convert_article(fp, account_name):
            converted += 1
        else:
            skipped += 1

    return converted, skipped


def main():
    import argparse

    parser = argparse.ArgumentParser(description="公众号 txt → Obsidian Markdown")
    parser.add_argument("--all", action="store_true", help="全量转换所有公众号")
    parser.add_argument("--incremental", action="store_true", help="增量转换（仅新文件）")
    parser.add_argument("--account", type=str, help="只转换指定公众号")
    args = parser.parse_args()

    # 确定要转换的账号列表
    if args.account:
        accounts = [args.account]
    else:
        # 扫描 SRC_DIR 下的子目录（排除 _web_sources）
        accounts = sorted([
            d for d in os.listdir(SRC_DIR)
            if os.path.isdir(os.path.join(SRC_DIR, d)) and not d.startswith("_")
        ])

    total_converted = 0
    total_skipped = 0
    for acc in accounts:
        print(f"\n📰 {acc}")
        converted, skipped = convert_account(acc, incremental=args.incremental)
        print(f"   ✅ {converted} 篇转换  ⏭️  {skipped} 篇跳过")
        total_converted += converted
        total_skipped += skipped

    print(f"\n{'='*50}")
    print(f"总计: ✅ {total_converted} 篇转换  ⏭️  {total_skipped} 篇跳过")
    print(f"输出: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

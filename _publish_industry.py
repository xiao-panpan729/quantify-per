#!/usr/bin/env python3
"""发布午后产业分析到 GitHub Pages 博客。

流程:
  1. 读取 reports/industry_daily/YYYYMMDD.md
  2. 复制到 ../daily-reports/docs/industry-YYYY-MM-DD.md
  3. 更新 docs/index.md 的产业分析链接
  4. git commit + push → GitHub Actions 自动部署

用法:
  python _publish_industry.py              # 发布今日
  python _publish_industry.py --date 20260628  # 指定日期
"""
import sys, os, subprocess, re
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

SRC_DIR = os.path.join(os.path.dirname(__file__), "reports", "industry_daily")
DEST_DIR = os.path.join(os.path.dirname(__file__), "..", "daily-reports", "docs")
INDEX_PATH = os.path.join(DEST_DIR, "index.md")
MARKER = "<!-- INDUSTRY_LIST -->"


def get_today() -> str:
    return date.today().strftime("%Y%m%d")


def fmt_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def publish(yyyymmdd: str) -> bool:
    blog_date = fmt_date(yyyymmdd)
    dest_name = f"industry-{blog_date}.md"
    dest_path = os.path.join(DEST_DIR, dest_name)

    if os.path.exists(dest_path):
        print(f"  ⏭️  {dest_name} 已存在，跳过")
        return False

    src_path = os.path.join(SRC_DIR, f"{yyyymmdd}.md")
    if not os.path.exists(src_path):
        print(f"  ❌ 源文件不存在: {src_path}")
        return False

    # 复制
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  ✓ 已复制到 {dest_name}")

    # 更新 index.md
    link_line = f"- [产业分析 {blog_date}]({dest_name})"
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        idx = f.read()

    if link_line in idx:
        print(f"  → 链接已存在，跳过")
        return True

    if MARKER not in idx:
        print(f"  ❌ index.md 缺少 {MARKER} 标记")
        return False

    idx = idx.replace(MARKER, f"{link_line}\n{MARKER}")
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(idx)
    print(f"  ✓ index.md 已更新")

    # git commit + push
    os.chdir(os.path.join(os.path.dirname(__file__), "..", "daily-reports"))
    for cmd in [
        ["git", "add", f"docs/{dest_name}", "docs/index.md"],
        ["git", "commit", "-m", f"产业分析 {blog_date}"],
        ["git", "push"],
    ]:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0 and "nothing to commit" not in r.stdout:
            print(f"  ⚠ {' '.join(cmd[:2])}: {r.stderr[:100]}")
        else:
            print(f"  ✓ git {' '.join(cmd[:2])} 成功")

    return True


def main():
    target = get_today()
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        if idx + 1 < len(sys.argv):
            target = sys.argv[idx + 1]

    print(f"发布产业分析 {target} → GitHub Pages...")
    publish(target)


if __name__ == "__main__":
    main()

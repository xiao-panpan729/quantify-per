#!/usr/bin/env python3
"""发布当日信源日报到 GitHub Pages 博客。

流程:
  1. 读取 reports/sources/YYYYMMDD_sources.md
  2. 复制到 ../daily-reports/docs/YYYY-MM-DD.md（带 H1 标题）
  3. 更新 docs/index.md 的链接列表
  4. git commit + push → GitHub Actions 自动部署

用法:
  python _publish_report.py              # 发布今日日报
  python _publish_report.py --date 20260618  # 指定日期回填
"""

import sys
import os
from datetime import date
import subprocess

# 终端中文编码
sys.stdout.reconfigure(encoding="utf-8")

SRC_DIR = os.path.join(os.path.dirname(__file__), "reports", "sources")
DEST_DIR = os.path.join(os.path.dirname(__file__), "..", "daily-reports", "docs")
INDEX_PATH = os.path.join(DEST_DIR, "index.md")

MARKER = "<!-- REPORTS_LIST -->"
PLACEHOLDER_LINE = "*尚无已发布的日报。运行"


def get_today() -> str:
    """返回 YYYYMMDD 格式日期"""
    return date.today().strftime("%Y%m%d")


def format_blog_date(yyyymmdd: str) -> str:
    """20260624 → 2026-06-24"""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def find_source(yyyymmdd: str) -> str | None:
    """查找 sources 文件，支持 .md 或 .txt 后缀"""
    for ext in (".md", ".txt"):
        path = os.path.join(SRC_DIR, f"{yyyymmdd}_sources{ext}")
        if os.path.exists(path):
            return path
    return None


def publish(yyyymmdd: str) -> bool:
    """发布指定日期的日报。返回是否真的发布了（False=已存在跳过）。"""
    blog_date = format_blog_date(yyyymmdd)
    dest_path = os.path.join(DEST_DIR, f"{blog_date}.md")

    # 检查目标是否已存在
    if os.path.exists(dest_path):
        print(f"  ⏭️  {blog_date}.md 已存在，跳过")
        return False

    # 查找源文件
    src_path = find_source(yyyymmdd)
    if not src_path:
        print(f"  ❌ 未找到 {yyyymmdd}_sources.md 或 .txt")
        return False

    # 读取源文件
    with open(src_path, encoding="utf-8") as f:
        content = f.read()

    # 写入目标文件（加 H1 标题）
    title = f"# 信源日报 — {blog_date}\n\n"
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(title + content)
    print(f"  ✅ 复制到 {dest_path}")

    # 更新 index.md
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding="utf-8") as f:
            index_content = f.read()

        link_line = f"- [{blog_date} 信源日报]({blog_date}.md)"
        new_entry = f"{link_line}\n"

        if MARKER in index_content:
            after_marker = index_content.split(MARKER, 1)[1]
            lines = after_marker.split("\n")
            # 去掉占位行 + 已有同名链接（去重）
            existing_links = set()
            filtered = []
            for l in lines:
                if PLACEHOLDER_LINE in l:
                    continue
                stripped = l.strip()
                if stripped.startswith("- [") and blog_date in stripped:
                    if stripped in existing_links:
                        continue
                    existing_links.add(stripped)
                filtered.append(l)
            rest = "\n".join(filtered).lstrip("\n")
            new_index = index_content.split(MARKER, 1)[0] + MARKER + "\n" + new_entry + rest
        else:
            new_index = index_content + "\n" + new_entry

        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            f.write(new_index)
        print(f"  ✅ 更新 index.md")
    else:
        print(f"  ⚠️  index.md 不存在，跳过更新")

    return True


def git_commit_and_push() -> bool:
    """在 daily-reports 仓库执行 git commit + push。"""
    repo_dir = os.path.dirname(DEST_DIR)
    try:
        # 检查是否是 git 仓库
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_dir, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            print("  ⚠️  daily-reports 不是 git 仓库，跳过推送")
            print("  请手动初始化：cd daily-reports && git init && git remote add origin <你的仓库URL>")
            return False

        # 检查是否有文件变更
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir, capture_output=True, text=True, timeout=10
        )
        if not result.stdout.strip():
            print("  ⏭️  无变更，跳过提交")
            return True

        # git add
        subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True, timeout=10)

        # git commit
        today = date.today().strftime("%Y-%m-%d")
        subprocess.run(
            ["git", "commit", "-m", f"add daily report {today}"],
            cwd=repo_dir, check=True, timeout=10,
            capture_output=True, text=True
        )

        # git push
        result = subprocess.run(
            ["git", "push"],
            cwd=repo_dir, capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            print(f"  ✅ 推送成功")
            return True
        else:
            print(f"  ⚠️  推送失败，输出: {result.stderr.strip()}")
            print("  请检查远程仓库配置")
            return False

    except subprocess.TimeoutExpired:
        print("  ⚠️  git 操作超时")
        return False
    except subprocess.CalledProcessError as e:
        print(f"  ⚠️  git 操作失败: {e}")
        return False


def main():
    # 解析参数
    args = sys.argv[1:]
    yyyymmdd = get_today()
    if args and args[0] == "--date" and len(args) > 1:
        yyyymmdd = args[1]

    blog_date = format_blog_date(yyyymmdd)
    print(f"📤 发布日报 {blog_date}")
    print()

    # 发布
    published = publish(yyyymmdd)
    if not published:
        print()
        print("✨ 无需发布")

    # git 提交 + 推送
    print()
    git_commit_and_push()
    print()
    print(f"🌐 https://xiao-panpan729.github.io/daily-reports/")


if __name__ == "__main__":
    main()

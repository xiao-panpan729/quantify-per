"""
inventory_scanner.py — 系统模块速查表差异检测

扫描当前 .py 文件结构，对比 reference-system-inventory.md 的记录。
跑完输出差异报告，不自动修改。

用法:
  python tools/inventory_scanner.py              # 全量扫描
  python tools/inventory_scanner.py --diff        # 只显示差异
  python tools/inventory_scanner.py --save-snapshot  # 保存快照供后续diff
"""
import glob
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_DIR = os.path.join(
    os.path.expanduser("~"),
    ".claude/projects/d--quantify-per/memory",
)
INVENTORY_PATH = os.path.join(MEMORY_DIR, "reference-system-inventory.md")
SNAPSHOT_PATH = os.path.join(MEMORY_DIR, ".inventory_snapshot.json")

EXCLUDE_DIRS = {"__pycache__", ".egg", ".git", "node_modules", ".claude"}
EXCLUDE_FILES = {"__init__.py"}


def scan_files(root=None):
    """扫描所有 .py 文件，返回 {relpath: lineno}"""
    root = root or PROJECT_ROOT
    files = {}
    for py in glob.glob("**/*.py", root_dir=root, recursive=True):
        # 过滤排除目录
        parts = py.replace("\\", "/").split("/")
        if any(ex in parts for ex in EXCLUDE_DIRS):
            continue
        if os.path.basename(py) in EXCLUDE_FILES:
            continue

        full = os.path.join(root, py)
        try:
            with open(full, "r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
        except Exception:
            line_count = -1
        files[py.replace("\\", "/")] = line_count
    return files


def extract_inventory_modules() -> dict:
    """从 reference-system-inventory.md 提取已记录的模块路径"""
    if not os.path.exists(INVENTORY_PATH):
        return {}
    recorded = {}
    with open(INVENTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            # 匹配表格行: | module.py | ...
            m = re.match(r'^\|\s+([^\s|]+\.py)\s+\|', line)
            if m:
                mod = m.group(1).strip()
                recorded[mod] = True
    return recorded


def _build_basename_index(files: dict) -> dict:
    """构建 basename → [full_path] 索引，用于短名匹配"""
    idx = {}
    for p in files:
        bn = os.path.basename(p)
        idx.setdefault(bn, []).append(p)
    return idx


def report_diff(current: dict, recorded: dict):
    """输出差异报告 — 支持短名匹配"""
    # 构建 basename 索引
    bn_idx = _build_basename_index(current)

    # 匹配：优先精确匹配，其次 basename 匹配（仅当唯一）
    matched_current = set()
    matched_recorded = set()
    for rec_mod in recorded:
        if rec_mod in current:
            matched_current.add(rec_mod)
            matched_recorded.add(rec_mod)
        else:
            bn = os.path.basename(rec_mod)
            if bn in bn_idx and len(bn_idx[bn]) == 1:
                fp = bn_idx[bn][0]
                matched_current.add(fp)
                matched_recorded.add(rec_mod)

    current_set = set(current.keys())
    recorded_set = set(recorded.keys())

    new_files = current_set - matched_current
    missing_files = recorded_set - matched_recorded

    sections = []
    if new_files:
        # 按目录分组
        by_dir = {}
        for f in sorted(new_files):
            d = os.path.dirname(f) or "(root)"
            by_dir.setdefault(d, []).append(f"{f}  ({current[f]}行)")
        lines = ["**新增模块（未记录到速查表）：**"]
        for d in sorted(by_dir.keys()):
            lines.append(f"  {d}/")
            for f in by_dir[d]:
                lines.append(f"    - {f}")
        sections.append("\n".join(lines))

    if missing_files:
        lines = ["**已删除/重命名的模块（速查表有但文件不存在）：**"]
        for f in sorted(missing_files):
            lines.append(f"  - {f}")
        sections.append("\n".join(lines))

    if not sections:
        sections.append("[OK] 速查表与当前文件结构一致，无需更新。")

    return "\n\n".join(sections)


def load_snapshot():
    if os.path.exists(SNAPSHOT_PATH):
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_snapshot(files: dict):
    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)
    print(f"  快照已保存 → {SNAPSHOT_PATH}")


def line_count_diff(current: dict, snapshot: dict) -> str:
    """对比行数变化（基于快照）"""
    changes = []
    for f in sorted(current.keys()):
        if f in snapshot and f.endswith(".py"):
            old = snapshot[f]
            new = current[f]
            if old > 0 and new > 0 and abs(new - old) > 10:
                direction = "+" if new > old else ""
                changes.append(f"  {f}: {old}→{new}行  ({direction}{new-old})")
    if changes:
        return "**行数显著变化的模块（>±10行，可能逻辑有改动）：**\n" + "\n".join(changes)
    return ""


def main():
    import argparse

    parser = argparse.ArgumentParser(description="系统模块速查表差异检测")
    parser.add_argument("--diff", action="store_true", help="只显示差异")
    parser.add_argument("--save-snapshot", action="store_true", help="保存快照供后续 diff")
    args = parser.parse_args()

    print("[扫描] 扫描项目 .py 文件...")
    current = scan_files()
    print(f"  共 {len(current)} 个模块")

    if args.save_snapshot:
        save_snapshot(current)

    recorded = extract_inventory_modules()

    # 排除规则：不扫描 notebook/ / _开头 / experiments/
    EXCLUDED_PREFIXES = ("notebook/", "_", "tools/volume_leader/experiments/")

    # 收集 basename 统计：总数 vs 被排除数
    basename_total = {}
    basename_excluded = {}
    for fp in current:
        bn = os.path.basename(fp)
        basename_total[bn] = basename_total.get(bn, 0) + 1
        if any(fp.startswith(p) for p in EXCLUDED_PREFIXES):
            basename_excluded[bn] = basename_excluded.get(bn, 0) + 1

    # 仅当 basename 全部属于排除目录时才过滤（避免 shared.py 误杀）
    truly_excluded = {bn for bn in basename_total
                      if basename_total[bn] == basename_excluded.get(bn, 0)}

    # 过滤 recorded：排除完全属于被排除目录的模块
    filtered_recorded = {}
    for mod in recorded:
        if any(mod.startswith(p) for p in EXCLUDED_PREFIXES):
            continue
        if "/" not in mod and mod in truly_excluded:
            continue
        filtered_recorded[mod] = True

    # 过滤 current：只保留需要跟踪的模块
    filtered_current = {k: v for k, v in current.items()
                        if not any(k.startswith(p) for p in EXCLUDED_PREFIXES)}

    diff = report_diff(filtered_current, filtered_recorded)
    print()
    print(diff)

    # 行数变化（基于快照）
    snapshot = load_snapshot()
    if snapshot:
        lc = line_count_diff(current, snapshot)
        if lc:
            print()
            print(lc)

    return diff


if __name__ == "__main__":
    main()

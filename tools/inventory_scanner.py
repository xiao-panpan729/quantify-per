"""
inventory_scanner.py — 系统模块速查表差异检测

扫描当前 .py 文件结构，对比 reference-system-inventory.md 的记录。
跑完输出差异报告，不自动修改。

用法:
  python tools/inventory_scanner.py              # 全量扫描
  python tools/inventory_scanner.py --diff        # 只显示差异
  python tools/inventory_scanner.py --check-quality  # 五维度质量检查
  python tools/inventory_scanner.py --save-snapshot  # 保存快照供后续diff
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict

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
            m = re.match(r'^\|\s+([^\s|]+\.py)\s+\|', line)
            if m:
                mod = m.group(1).strip()
                recorded[mod] = True
    return recorded


def _parse_inventory() -> list:
    """解析参考文档，返回完整条目列表（保留重复章节）

    Returns: [
        {"path": str, "line_count": int|None, "section": str, "subsection": str},
    ]
    """
    if not os.path.exists(INVENTORY_PATH):
        return []

    entries = []
    section = "(未分类)"
    subsection = ""

    with open(INVENTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            heading_m = re.match(r'^(#{2,3})\s+(.+)$', line.strip())
            if heading_m:
                level = len(heading_m.group(1))
                title = heading_m.group(2).strip()
                if level == 2:
                    section = title
                    subsection = ""
                elif level == 3:
                    subsection = title
                continue

            m = re.match(r'^\|\s+([^\s|]+\.py)\s+\|\s*([^|]*?)\s*\|', line)
            if m:
                mod = m.group(1).strip()
                lc_str = m.group(2).strip()
                lc = None
                if lc_str and lc_str not in ('—', '--', '�', ''):
                    try:
                        lc = int(lc_str)
                    except ValueError:
                        lc = None
                entries.append({
                    "path": mod,
                    "line_count": lc,
                    "section": section,
                    "subsection": subsection,
                })
    return entries


def _build_basename_index(files: dict) -> dict:
    """构建 basename -> [full_path] 索引，用于短名匹配"""
    idx = {}
    for p in files:
        bn = os.path.basename(p)
        idx.setdefault(bn, []).append(p)
    return idx


def report_diff(current: dict, recorded: dict):
    """输出差异报告 — 支持短名匹配"""
    bn_idx = _build_basename_index(current)

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
    print(f"  快照已保存 -> {SNAPSHOT_PATH}")


def line_count_diff(current: dict, snapshot: dict) -> str:
    """对比行数变化（基于快照）"""
    changes = []
    for f in sorted(current.keys()):
        if f in snapshot and f.endswith(".py"):
            old = snapshot[f]
            new = current[f]
            if old > 0 and new > 0 and abs(new - old) > 10:
                direction = "+" if new > old else ""
                changes.append(f"  {f}: {old}->{new}行  ({direction}{new-old})")
    if changes:
        return "**行数显著变化的模块（>+-10行，可能逻辑有改动）：**\n" + "\n".join(changes)
    return ""


def quality_check_report(current: dict, entries: list, prefix_map: dict) -> str:
    """五维度质量检查报告"""
    lines = []
    sep = "━" * 50
    bn_idx = _build_basename_index(current)

    # 匹配阶段
    exact_matches = set()
    basename_matches = {}       # {ref_path: actual_path} 仅 ref 含 / 时
    matched_current = set()     # current 中被匹配的 key
    matched_recorded = set()    # prefix_map 中被匹配的 key
    actual_path_for_ref = {}    # ref_path -> actual_disk_path
    ref_lc_for_ref = {}         # ref_path -> 参考行数

    path_lc_map = {}
    for ent in entries:
        if ent["path"] not in path_lc_map:
            path_lc_map[ent["path"]] = ent["line_count"]

    for rec_mod in prefix_map:
        if rec_mod in current:
            exact_matches.add(rec_mod)
            matched_current.add(rec_mod)
            matched_recorded.add(rec_mod)
            actual_path_for_ref[rec_mod] = rec_mod
        else:
            bn = os.path.basename(rec_mod)
            if bn in bn_idx and len(bn_idx[bn]) == 1:
                fp = bn_idx[bn][0]
                if '/' in rec_mod:
                    basename_matches[rec_mod] = fp
                matched_current.add(fp)
                matched_recorded.add(rec_mod)
                actual_path_for_ref[rec_mod] = fp

        if rec_mod in path_lc_map and path_lc_map[rec_mod] is not None:
            ref_lc_for_ref[rec_mod] = path_lc_map[rec_mod]

    current_set = set(current.keys())
    recorded_set = set(prefix_map.keys())
    new_files = current_set - matched_current
    missing_files = recorded_set - matched_recorded

    # 重复检测（basename 归一化）
    basename_refs = defaultdict(list)
    for ent in entries:
        bn = os.path.basename(ent["path"])
        if ent["path"] in prefix_map:
            basename_refs[bn].append(ent)
    duplicates = {bn: group for bn, group in basename_refs.items()
                  if len(group) > 1}

    # 缺失行数统计
    missing_lc_info = defaultdict(list)
    for ent in entries:
        if ent["path"] in matched_recorded and ent["line_count"] is None:
            sec = ent["section"].split("（")[0] if "（" in ent["section"] else ent["section"]
            missing_lc_info[sec].append(ent["path"])

    # ---- 输出 ----
    lines.append("reference-system-inventory.md 质量检查")
    lines.append(sep)

    # 1. 覆盖率
    lines.append(f"[1/5 覆盖率] {len(matched_current)}/{len(current)} 模块已收录（参考共 {len(recorded_set)} 条）")
    if new_files:
        by_dir = defaultdict(list)
        for f in sorted(new_files):
            d = os.path.dirname(f) or "(root)"
            by_dir[d].append(f)
        lines.append(f"  [新增] 未收录: {len(new_files)} 处")
        for d in sorted(by_dir):
            for f in sorted(by_dir[d]):
                lines.append(f"    + {f} ({current[f]}行)")
    else:
        lines.append("  [新增] 无新增模块游离在外")
    if missing_files:
        lines.append(f"  [缺失] 参考中有已不存在的模块: {len(missing_files)} 处")
        for f in sorted(missing_files):
            lines.append(f"    - {f}")
    else:
        lines.append("  [缺失] 无已删除模块残留")
    lines.append("")

    # 2. 行数准确性
    issues_line = []
    for rec_mod, actual_path in sorted(actual_path_for_ref.items()):
        ref_lc = ref_lc_for_ref.get(rec_mod)
        actual_lc = current.get(actual_path)
        if ref_lc is not None and actual_lc and actual_lc > 0 and ref_lc > 0:
            diff = actual_lc - ref_lc
            pct = abs(diff) / ref_lc * 100
            if abs(diff) > 10 and pct > 10:
                issues_line.append((actual_path, ref_lc, actual_lc, diff))

    lines.append(f"[2/5 行数准确性] 共检查 {len(actual_path_for_ref)} 个匹配模块")
    if issues_line:
        lines.append(f"  [偏差] {len(issues_line)} 处:")
        for mod, ref, act, diff in sorted(issues_line, key=lambda x: -abs(x[3])):
            lines.append(f"    {mod}  参考{ref} -> 实际{act} ({'+' if diff > 0 else ''}{diff})")
    else:
        lines.append("  [一致] 行数与实际一致")
    lines.append("")

    # 3. 路径正确性
    lines.append("[3/5 路径正确性]")
    if basename_matches:
        lines.append(f"  [不符] {len(basename_matches)} 处:")
        for rec_mod, actual in sorted(basename_matches.items()):
            lines.append(f"    {rec_mod}  ->  实际在 {actual}")
    else:
        lines.append("  [一致] 路径一致")
    lines.append("")

    # 4. 缺失行数
    total_missing = sum(len(v) for v in missing_lc_info.values())
    lines.append("[4/5 行数完整性]")
    if missing_lc_info:
        lines.append(f"  [缺失] 无行数记录 ({total_missing} 处):")
        for sec, mods in sorted(missing_lc_info.items()):
            lines.append(f"    [{sec}]")
            for m in sorted(mods):
                lines.append(f"      - {m}")
    else:
        lines.append("  [完整] 所有模块均有行数")
    lines.append("")

    # 5. 重复条目
    lines.append("[5/5 重复条目]")
    if duplicates:
        lines.append(f"  [重复] {len(duplicates)} 处:")
        for bn, group in sorted(duplicates.items()):
            parts = []
            for g in group:
                lc_str = str(g["line_count"]) if g["line_count"] is not None else "--"
                sec_name = g["section"].split("（")[0] if "（" in g["section"] else g["section"]
                sub = g.get("subsection", "") or ""
                label = f"{sec_name}/{sub}" if sub else sec_name
                parts.append(f"{label}({lc_str}行)")
            lines.append(f"    {bn}:  {'  vs  '.join(parts)}")
    else:
        lines.append("  [无] 无重复章节条目")
    lines.append("")

    # 汇总
    total_issues = len(issues_line) + len(basename_matches) + len(duplicates) + total_missing
    if total_issues:
        lines.append(f"汇总: {total_issues} 个问题（行数{len(issues_line)} | 路径{len(basename_matches)} | 重复{len(duplicates)} | 缺失{total_missing}）")
        lines.append("提示: 确认后手工更新 reference-system-inventory.md")
    else:
        lines.append("参考文档质量良好，无需更新")

    return "\n".join(lines)


def main():
    import argparse

    sys.stdout.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(description="系统模块速查表差异检测")
    parser.add_argument("--diff", action="store_true", help="只显示差异")
    parser.add_argument("--check-quality", action="store_true", help="五维度质量检查（覆盖率/行数/路径/缺失行数/重复条目）")
    parser.add_argument("--save-snapshot", action="store_true", help="保存快照供后续 diff")
    args = parser.parse_args()

    print("[扫描] 扫描项目 .py 文件...")
    current = scan_files()
    print(f"  共 {len(current)} 个模块")

    if args.save_snapshot:
        save_snapshot(current)

    recorded = extract_inventory_modules()

    EXCLUDED_PREFIXES = ("notebook/", "_", "tools/volume_leader/experiments/")

    basename_total = {}
    basename_excluded = {}
    for fp in current:
        bn = os.path.basename(fp)
        basename_total[bn] = basename_total.get(bn, 0) + 1
        if any(fp.startswith(p) for p in EXCLUDED_PREFIXES):
            basename_excluded[bn] = basename_excluded.get(bn, 0) + 1

    truly_excluded = {bn for bn in basename_total
                      if basename_total[bn] == basename_excluded.get(bn, 0)}

    filtered_recorded = {}
    for mod in recorded:
        if any(mod.startswith(p) for p in EXCLUDED_PREFIXES):
            continue
        if "/" not in mod and mod in truly_excluded:
            continue
        filtered_recorded[mod] = True

    filtered_current = {k: v for k, v in current.items()
                        if not any(k.startswith(p) for p in EXCLUDED_PREFIXES)}

    if args.check_quality:
        entries = _parse_inventory()
        report = quality_check_report(filtered_current, entries, filtered_recorded)
        print()
        print(report)
        diff = report
    else:
        diff = report_diff(filtered_current, filtered_recorded)
        print()
        print(diff)

        snapshot = load_snapshot()
        if snapshot:
            lc = line_count_diff(current, snapshot)
            if lc:
                print()
                print(lc)

    return diff


if __name__ == "__main__":
    main()

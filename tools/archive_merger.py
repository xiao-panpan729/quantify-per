"""归档合并工具：日报->周报，删除旧文件
整合进 /neat 流程第七步使用
"""
import os, re
from datetime import datetime
from collections import defaultdict

ARCHIVES = r'C:\Users\Administrator\.claude\projects\C--Users-Administrator\archives'


def iso_week(date_str):
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    iso = dt.isocalendar()
    return f'{iso[0]}-W{iso[1]:02d}'


def current_week():
    return iso_week(datetime.now().strftime('%Y-%m-%d'))


def group_by_week():
    groups = defaultdict(list)
    for f in os.listdir(ARCHIVES):
        m = re.match(r'(\d{4}-\d{2}-\d{2})(.*)\.md$', f)
        if not m:
            continue
        groups[iso_week(m.group(1))].append(f)
    return dict(groups)


def get_weeks_to_merge():
    groups = group_by_week()
    cw = current_week()
    return {k: v for k, v in groups.items() if k < cw}


def do_merge(weeks):
    total_freed = 0
    for week in sorted(weeks):
        files = sorted(weeks[week])
        out_path = os.path.join(ARCHIVES, f'{week}.md')
        if os.path.exists(out_path):
            continue

        parts = [f'# Weekly Archive {week}']
        week_size = 0
        for fname in files:
            path = os.path.join(ARCHIVES, fname)
            size = os.path.getsize(path)
            week_size += size
            total_freed += size
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read().strip()
            day_label = re.match(r'(\d{4}-\d{2}-\d{2})', fname).group(1)
            parts.append(f'\n---\n## {day_label}\n{content}')
        parts.append(f'\n---\n*archive_merger: {len(files)} files, {week_size/1024:.1f}K*')

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(parts))
        print(f'  [OK] {os.path.basename(out_path)} ({len(files)} files)')

        for fname in files:
            os.remove(os.path.join(ARCHIVES, fname))
            print(f'  [DEL] {fname}')

    print(f'\nDone: {len(weeks)} weeks, freed ~{total_freed/1024:.0f}K')


def dry_run():
    mergeable = get_weeks_to_merge()
    total = sum(len(v) for v in mergeable.values())
    total_kb = sum(os.path.getsize(os.path.join(ARCHIVES, f))
                   for files in mergeable.values() for f in files) / 1024
    print(f'Archives: {ARCHIVES}')
    print(f'Current:  {current_week()}')
    print(f'Merge:    {len(mergeable)} weeks, {total} files, ~{total_kb:.0f}K')
    print()
    for week in sorted(mergeable):
        files = sorted(mergeable[week])
        print(f'  {week}: {len(files)} files')
        for f in files:
            sz = os.path.getsize(os.path.join(ARCHIVES, f))
            print(f'    {f} ({sz/1024:.1f}K)')
    return mergeable


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Archive merger')
    parser.add_argument('--do', action='store_true', help='Execute merge (default: dry-run)')
    args = parser.parse_args()

    mergeable = dry_run()
    if args.do:
        ans = input(f'\nProceed to merge {len(mergeable)} weeks? (y/N) ')
        if ans.lower() == 'y':
            do_merge(mergeable)

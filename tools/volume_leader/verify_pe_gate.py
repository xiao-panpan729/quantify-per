# -*- coding: utf-8 -*-
"""PE门禁误杀验证 — 事后检查被过滤信号的实际走势

用法:
  python tools/volume_leader/verify_pe_gate.py              # 检查所有未验证记录
  python tools/volume_leader/verify_pe_gate.py --days 3    # 只看最近3天的记录
  python tools/volume_leader/verify_pe_gate.py --stats     # 仅显示统计
"""

import sys, os, json, argparse, csv
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MIN_PRICE_FACTOR

PE_GATE_LOG = 'signals/tracking/pe_gate_log.jsonl'
def load_log():
    if not os.path.exists(PE_GATE_LOG):
        return []
    records = []
    with open(PE_GATE_LOG, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def check_outcome(code, signal_time_str, signal_price, days_forward=5):
    """检查信号后N天的价格走势

    Returns:
        'correct_filter': 跌了>2% → PE正确过滤
        'false_kill': 涨了>2% → PE误杀
        'pending': 涨跌幅在±2%之间 → 待继续观察
    """
    csv_path = f'signals/tracking/{code}/min5_signals.csv'
    if not os.path.exists(csv_path):
        return 'pending', 0

    with open(csv_path, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return 'pending', 0

    signal_dt = datetime.strptime(signal_time_str[:16], '%Y-%m-%d %H:%M')
    cutoff = signal_dt + timedelta(days=days_forward)

    # 找信号后的最低价和最高价
    post_low = None
    post_high = None
    found_signal = False

    for r in rows:
        ts = r.get('timestamp', '').strip()
        if not ts:
            continue
        try:
            bar_dt = datetime.strptime(ts[:16], '%Y-%m-%d %H:%M')
        except ValueError:
            continue

        if bar_dt <= signal_dt:
            continue
        if bar_dt > cutoff:
            break
        found_signal = True

        close = float(r.get('close', 0) or 0) / MIN_PRICE_FACTOR
        if post_low is None or close < post_low:
            post_low = close
        if post_high is None or close > post_high:
            post_high = close

    if not found_signal:
        return 'pending', 0

    max_drop = (post_low - signal_price) / signal_price * 100 if post_low else 0
    max_rise = (post_high - signal_price) / signal_price * 100 if post_high else 0

    if max_drop <= -2:
        return 'correct_filter', round(max_drop, 2)
    elif max_rise >= 2:
        return 'false_kill', round(max_rise, 2)
    else:
        return 'pending', round(max(max_rise, abs(max_drop)), 2)


def update_log(records):
    """将验证结果写回日志"""
    with open(PE_GATE_LOG, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description='PE门禁误杀验证')
    parser.add_argument('--days', type=int, default=3, help='只看最近N天的记录 (默认3)')
    parser.add_argument('--forward', type=int, default=5, help='向前看N天验证 (默认5)')
    parser.add_argument('--stats', action='store_true', help='仅显示统计')
    args = parser.parse_args()

    records = load_log()
    if not records:
        print('[信息] PE门禁日志为空，还没有被过滤的信号')
        return

    # 过滤最近N天
    cutoff_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
    recent = [r for r in records if r.get('time', '')[:10] >= cutoff_date]
    all_records = recent if args.days > 0 else records

    print(f'PE门禁误杀验证 — 共 {len(all_records)} 条记录')
    print(f'{"="*80}')

    # 统计
    stats = {'correct_filter': 0, 'false_kill': 0, 'pending': 0, 'total': 0}
    by_level = defaultdict(lambda: {'correct_filter': 0, 'false_kill': 0, 'pending': 0})
    by_reason = defaultdict(lambda: {'correct_filter': 0, 'false_kill': 0, 'pending': 0})

    updated = []
    for r in all_records:
        if r.get('verified'):
            # 已验证过
            result = r['verified_result']
            if result in stats:
                stats[result] += 1
            stats['total'] += 1
            by_level[r.get('would_be_level', '?')][result] += 1
            by_reason[r.get('fail_reason', '?')][result] += 1
            updated.append(r)
            continue

        outcome, pct = check_outcome(r['code'], r['time'], r['price'], args.forward)
        r['verified'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        r['verified_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        r['verified_result'] = outcome
        r['outcome_pct'] = pct
        updated.append(r)

        stats[outcome] += 1
        stats['total'] += 1
        by_level[r.get('would_be_level', '?')][outcome] += 1
        by_reason[r.get('fail_reason', '?')][outcome] += 1

    # 写回
    if any(not r.get('verified') for r in all_records):
        update_log(updated)
        print(f'[更新] {len([r for r in all_records if not r.get("verified")])} 条新验证已写回日志\n')

    # 统计输出
    if stats['total'] == 0:
        print('[信息] 没有可验证的记录')
        return

    correct_pct = stats['correct_filter'] / stats['total'] * 100
    false_pct = stats['false_kill'] / stats['total'] * 100
    pending_pct = stats['pending'] / stats['total'] * 100

    print(f'\n总览: {stats["total"]} 条')
    print(f'  正确过滤: {stats["correct_filter"]} ({correct_pct:.0f}%) — PE正确地拦住了亏损信号')
    print(f'  误杀:     {stats["false_kill"]} ({false_pct:.0f}%) — PE错误地拦住了盈利信号')
    print(f'  待观察:   {stats["pending"]} ({pending_pct:.0f}%) — 涨跌幅<2%, 继续观察')

    print(f'\n按级别:')
    for level in ['ma', 'jincha']:
        d = by_level[level]
        total = d['correct_filter'] + d['false_kill'] + d['pending']
        if total == 0:
            continue
        correct_pct = d['correct_filter'] / total * 100
        false_pct = d['false_kill'] / total * 100
        level_name = 'MA级' if level == 'ma' else '金叉级'
        print(f'  {level_name}: {total}条  正确过滤{correct_pct:.0f}%  误杀{false_pct:.0f}%')

    print(f'\n按失败原因:')
    for reason in ['daily_pe_rising', 'min5_pe_rising']:
        d = by_reason[reason]
        total = d['correct_filter'] + d['false_kill'] + d['pending']
        if total == 0:
            continue
        correct_pct = d['correct_filter'] / total * 100
        false_pct = d['false_kill'] / total * 100
        reason_name = '日线PE升熵' if reason == 'daily_pe_rising' else '5分钟PE升熵'
        print(f'  {reason_name}: {total}条  正确过滤{correct_pct:.0f}%  误杀{false_pct:.0f}%')

    if not args.stats:
        # 列出误杀和待观察
        false_kills = [r for r in updated if r.get('verified_result') == 'false_kill']
        pending = [r for r in updated if r.get('verified_result') == 'pending']

        if false_kills:
            print(f'\n⚠ 误杀信号 ({len(false_kills)}条) — 需要关注:')
            for r in false_kills:
                print(f'  {r["time"]} {r["code"]} {r["name"]} {r["would_be_level"]}级 '
                      f'过滤原因={r["fail_reason"]} PE={r["pe_val"]} '
                      f'信号价={r["price"]} 后涨={r.get("outcome_pct", "?")}%')

        if pending:
            print(f'\n? 待观察 ({len(pending)}条):')
            for r in pending[:10]:
                print(f'  {r["time"]} {r["code"]} {r["name"]} {r["would_be_level"]}级 '
                      f'过滤原因={r["fail_reason"]} PE={r["pe_val"]} '
                      f'信号价={r["price"]}')

    # 给出判断
    if correct_pct >= 70 and false_pct <= 10:
        print(f'\n[结论] PE门禁表现良好: {correct_pct:.0f}%正确过滤, 仅{false_pct:.0f}%误杀。继续使用。')
    elif false_pct >= 20:
        print(f'\n[警告] 误杀率偏高 ({false_pct:.0f}%), 考虑放宽PE阈值 (当前-0.02)')
    else:
        print(f'\n[结论] 样本不足或表现中性，继续积累数据观察。')


if __name__ == '__main__':
    main()

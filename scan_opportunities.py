# -*- coding: utf-8 -*-
"""
scan_opportunities.py — 机会扫描 + 每日判断报告生成 + AI智能分析

用法:
    python scan_opportunities.py                    # 命令行输出简要结果
    python scan_opportunities.py --report          # 生成 Markdown 报告 + CSV 日志
    python scan_opportunities.py --report --ai      # 生成报告 + 调用多 API AI分析（自动切换）
    python scan_opportunities.py --code sh513310    # 单标的详情

定位: 直接读取快照 CSV，不重新计算。可作为 update_tracking.py 的后置步骤。
"""

import csv
import os
import sys
import json
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# 项目根目录自适应（支持复制到任意位置）
BASE = Path(__file__).parent.resolve()
SNAPSHOT_DIR = BASE / 'signals' / 'tracking'
REPORT_DIR = BASE / 'reports' / 'daily'
LOG_CSV = BASE / 'reports' / 'judgement_log.csv'

CODES = [
    ('sz159740', '恒生科技ETF'),
    ('sh520600', '汽车ETF'),
    ('sh513120', '港股创新药ETF'),
    ('sz159326', '电网设备ETF'),
    ('sh513310', '中韩半导体ETF'),
    ('sz002261', '拓维信息'),
    ('sz300118', '东方日升'),
    ('sz000100', 'TCL科技'),
    ('sz002129', 'TCL中环'),
    ('sh600438', '通威股份'),
    ('sh601012', '隆基绿能'),
]

# ==== 用户定性判断（可定期更新） ====
QUALITATIVE_VIEWS = {
    'sh600438': {
        'category': '光伏',
        'view': '2025.06.26日线闭环，目前在底部，三兄弟中最强，30min已提前反弹',
        'ranking': '三兄弟: 通威 > 中环 > 隆基',
        'expectation': '日线震荡后走出EXPMA第二组闭环信号，机会非常大',
    },
    'sz002129': {
        'category': '光伏',
        'view': '日线大震荡格局。2025.06.26和2026.1.22前后各有一组闭环。目前回落到箱体低位',
        'ranking': '三兄弟: 通威 > 中环 > 隆基',
        'expectation': '30min 2026.04.07前后有一组闭环，MACD在0轴附近粘合，极可能金叉向上形成反弹买点',
    },
    'sh601012': {
        'category': '光伏',
        'view': '三兄弟中最弱',
        'ranking': '三兄弟: 通威 > 中环 > 隆基',
        'expectation': '需等待更明确的底部结构',
    },
    'sz000100': {
        'category': 'TCL系',
        'view': '类似TCL中环，日线震荡格局',
        'expectation': '等底部结构明确后观察',
    },
    'sh513120': {
        'category': '创新药',
        'view': '走势较强，可能出一个信号就起来',
        'expectation': '15min接近完整闭环，关注金叉确认',
    },
    'sz002261': {
        'category': '科技',
        'view': '走势较强，可能出一个信号就起来',
        'expectation': '30min有底背驰+金叉，需确认★买时机',
    },
}


# ============================================================
# 工具函数
# ============================================================

def read_snapshots(code, period, n=30):
    """读取某标的某周期的最后 n 行快照"""
    fname = f'{period}_signals.csv'
    fpath = SNAPSHOT_DIR / code / fname
    if not fpath.exists():
        return []
    with open(fpath, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    return rows[-n:] if rows else []


def fmt_ts(ts):
    """格式化时间戳，过滤无效时间（合成文件reserved字段可能不规范）"""
    s = str(ts)
    if len(s) == 12:
        hh = int(s[8:10])
        mm = int(s[10:12])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{s[:8]} {s[8:10]}:{s[10:12]}"
        else:
            return s[:8]  # 无效时间只返回日期
    return s


def get_daily_env(code):
    """获取日线MACD环境"""
    rows = read_snapshots(code, 'daily', 3)
    if not rows:
        return None
    last = rows[-1]
    dif = last.get('macd_dif', '')
    try:
        dif_f = float(dif)
        if abs(dif_f) <= 0.02:
            env = f'0轴附近 DIF={dif}'
            env_short = '0轴'
        elif dif_f > 0:
            env = f'多头 DIF={dif}'
            env_short = '多头'
        else:
            env = f'空头 DIF={dif}'
            env_short = '空头'
    except:
        env = f'DIF={dif}'
        env_short = '未知'
    return {
        'date': last.get('date', last.get('timestamp', '')),
        'close': last.get('close', ''),
        'env': env,
        'env_short': env_short,
        'dif': dif,
    }


def find_cci_extremes(rows):
    """从后往前找 CCI 极值和后续信号"""
    extremes = []
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        ext = r.get('cci_extreme', '').strip()
        if ext:
            extremes.append({
                'ts': r.get('timestamp', ''),
                'cci': r.get('cci', '')[:8],
                'extreme': ext,
                'close': r.get('raw_close', r.get('close', '')),
            })
            break
    return extremes


def find_recent_signals(rows, max_n=5):
    """找最近的有信号的行"""
    sigs = []
    for r in rows:
        buy = r.get('buy_signal', '').strip()
        sell = r.get('sell_signal', '').strip()
        ema = r.get('expma_cross', '').strip()
        div = r.get('cci_divergence', '').strip()
        if buy or sell or ema or div:
            sigs.append({
                'ts': r.get('timestamp', ''),
                'buy': buy,
                'sell': sell,
                'ema': ema,
                'div': div,
                'cci': r.get('cci', '')[:8],
                'close': r.get('raw_close', r.get('close', '')),
            })
    return sigs[-max_n:]


def analyze_period(code, period):
    """分析某周期，返回结构化结果"""
    rows = read_snapshots(code, period, 40)
    if not rows:
        return None

    last = rows[-1]
    ema_latest = last.get('expma_cross', '').strip()

    # CCI极值
    cci_ext = find_cci_extremes(rows)

    # 最近信号
    sigs = find_recent_signals(rows, 5)

    # 判断机会类型
    opportunity = []
    opp_level = 0  # 0=无 1=观察 2=接近 3=完整闭环

    if cci_ext:
        ext = cci_ext[0]['extreme']
        has_buy = any(s['buy'] for s in sigs)
        has_sell = any(s['sell'] for s in sigs)
        has_gold = any('金叉' in s['ema'] for s in sigs)
        has_dead = any('死叉' in s['ema'] for s in sigs)
        has_div = any(s['div'] for s in sigs)

        if '-200' in ext or '-250' in ext or '-300' in ext:
            if has_buy and has_gold:
                opportunity.append('✅完整闭环(买)')
                opp_level = 3
            elif has_buy:
                opportunity.append('⚠️部分闭环: ★买(缺金叉)')
                opp_level = 2
            else:
                opportunity.append('👀观察: CCI负极限(等★买+金叉)')
                opp_level = 1
        elif '+200' in ext or '+250' in ext:
            if has_sell and has_dead:
                opportunity.append('❌完整闭环(卖)')
                opp_level = 3
            elif has_sell:
                opportunity.append('⚠️部分闭环: ★卖(缺死叉)')
                opp_level = 2
            else:
                opportunity.append('⏸️观察: CCI正极限(等★卖+死叉)')
                opp_level = 1

    if '金叉' in ema_latest and '金叉' not in ' '.join(opportunity):
        opportunity.append(f'最新: 金叉')
        if opp_level < 2:
            opp_level = 2
    elif '死叉' in ema_latest and '死叉' not in ' '.join(opportunity):
        opportunity.append(f'最新: 死叉')

    divs = [s for s in sigs if s['div']]
    if divs and '背驰' not in ' '.join(opportunity):
        opportunity.append(f'背驰: {divs[-1]["div"]}')

    return {
        'period': period,
        'last_ts': last.get('timestamp', ''),
        'last_close': last.get('raw_close', last.get('close', '')),
        'cci_ext': cci_ext,
        'signals': sigs,
        'opportunity': ' | '.join(opportunity) if opportunity else '无明确信号',
        'opp_level': opp_level,
    }


# ============================================================
# 报告生成
# ============================================================

def generate_report(date_str=None):
    """生成每日判断报告 Markdown"""
    if date_str is None:
        date_str = datetime.now().strftime('%Y%m%d')

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f'{date_str}.md'

    # 收集所有标的分析结果
    results = []
    for code, name in CODES:
        daily = get_daily_env(code)
        periods = {}
        for p in ['min30', 'min15', 'min5']:
            ana = analyze_period(code, p)
            if ana:
                periods[p] = ana

        # 找最高机会级别
        max_level = max((periods[p]['opp_level'] for p in periods), default=0)

        results.append({
            'code': code,
            'name': name,
            'daily': daily,
            'periods': periods,
            'max_level': max_level,
        })

    # 生成 Markdown
    lines = []
    lines.append(f'# 每日判断报告 {date_str}')
    lines.append('')
    lines.append(f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M")}  ')
    lines.append('**框架**: CCI左侧信号 + 分时出击 + EXPMA金叉死叉  ')
    lines.append('**数据源**: signals/tracking/ 快照（直接读取，不重新计算）')
    lines.append('')
    lines.append('---')
    lines.append('')

    # 一、完整闭环
    lines.append('## 一、完整闭环（可操作）')
    lines.append('')
    complete = [r for r in results if r['max_level'] == 3]
    if not complete:
        lines.append('> 当前无完整闭环信号。')
    else:
        lines.append('| 标的 | 周期 | 信号组合 | 最新时间 |')
        lines.append('|------|------|---------|---------|')
        for r in complete:
            for p, ana in r['periods'].items():
                if ana['opp_level'] == 3:
                    opp_clean = ana['opportunity'].replace('|', '/')
                    lines.append(f"| {r['code']} {r['name']} | {p} | {opp_clean} | {fmt_ts(ana['last_ts'])} |")
    lines.append('')

    # 二、部分闭环（接近）
    lines.append('## 二、部分闭环（观察/接近）')
    lines.append('')
    partial = [r for r in results if r['max_level'] == 2]
    if not partial:
        lines.append('> 当前无部分闭环信号。')
    else:
        lines.append('| 标的 | 周期 | 信号状态 | 最新时间 |')
        lines.append('|------|------|---------|---------|')
        for r in partial:
            for p, ana in r['periods'].items():
                if ana['opp_level'] == 2:
                    opp_clean = ana['opportunity'].replace('|', '/')
                    lines.append(f"| {r['code']} {r['name']} | {p} | {opp_clean} | {fmt_ts(ana['last_ts'])} |")
    lines.append('')

    # 三、各标的全周期状态
    lines.append('## 三、各标的全周期状态')
    lines.append('')
    lines.append('| 标的 | 日线MACD | 30分钟 | 15分钟 | 5分钟 | 综合 |')
    lines.append('|------|---------|--------|--------|-------|------|')
    for r in results:
        daily_str = r['daily']['env_short'] if r['daily'] else 'N/A'
        p30 = r['periods'].get('min30', {})
        p15 = r['periods'].get('min15', {})
        p5 = r['periods'].get('min5', {})

        def short_opp(ana):
            opp = ana.get('opportunity', '')
            if '✅' in opp: return '✅闭环'
            if '⚠️' in opp: return '⚠️接近'
            if '👀' in opp: return '👀观察'
            if '⏸️' in opp: return '⏸️观察'
            if '金叉' in opp: return '金叉'
            if '死叉' in opp: return '死叉'
            if '背驰' in opp: return '背驰'
            return '—'

        comp = '—'
        if r['max_level'] == 3: comp = '🔴可操作'
        elif r['max_level'] == 2: comp = '🟡接近'
        elif any(p.get('opp_level', 0) == 1 for p in r['periods'].values()): comp = '⚪观察'
        elif r['daily'] and r['daily']['env_short'] == '0轴': comp = '🔶变盘'
        else: comp = '⚪平淡'

        lines.append(f"| {r['code'][:2]}...{r['code'][-3:]} {r['name']} | {daily_str} | {short_opp(p30)} | {short_opp(p15)} | {short_opp(p5)} | {comp} |")
    lines.append('')

    # 四、定性补充判断
    lines.append('## 四、定性补充判断')
    lines.append('')
    lines.append('> 以下为用户基于板块逻辑和长期结构的定性判断，与机器信号互为补充。')
    lines.append('')

    # 光伏三兄弟
    lines.append('### 光伏三兄弟')
    lines.append('')
    lines.append('| 标的 | 排序 | 判断 | 预期 |')
    lines.append('|------|------|------|------|')
    for code in ['sh600438', 'sz002129', 'sh601012']:
        if code in QUALITATIVE_VIEWS:
            v = QUALITATIVE_VIEWS[code]
            name = next(n for c, n in CODES if c == code)
            lines.append(f"| {code} {name} | {v.get('ranking','')} | {v['view']} | {v['expectation']} |")
    lines.append('')

    # 其他标的
    lines.append('### 其他标的')
    lines.append('')
    lines.append('| 标的 | 判断 | 预期 |')
    lines.append('|------|------|------|')
    for code in ['sz000100', 'sh513120', 'sz002261']:
        if code in QUALITATIVE_VIEWS:
            v = QUALITATIVE_VIEWS[code]
            name = next(n for c, n in CODES if c == code)
            lines.append(f"| {code} {name} | {v['view']} | {v['expectation']} |")
    lines.append('')

    # 五、综合机会排序
    lines.append('## 五、综合机会排序')
    lines.append('')
    lines.append('| 排序 | 标的 | 机器信号 | 定性判断 | 综合评级 |')
    lines.append('|------|------|---------|---------|---------|')
    # 机器+定性综合排序（简化版）
    sorted_results = sorted(results, key=lambda r: (
        -r['max_level'],
        -(1 if r['code'] in QUALITATIVE_VIEWS else 0),
    ))
    for i, r in enumerate(sorted_results[:6], 1):
        qv = QUALITATIVE_VIEWS.get(r['code'], {})
        qv_view = qv.get('view', '—')[:20] + '...' if len(qv.get('view', '')) > 20 else qv.get('view', '—')
        if r['max_level'] == 3:
            rating = 'A 可操作'
        elif r['max_level'] == 2:
            rating = 'B 接近'
        elif r['code'] in QUALITATIVE_VIEWS:
            rating = 'C 有潜力'
        else:
            rating = 'D 平淡'
        machine = '有信号' if r['max_level'] >= 1 else '无'
        lines.append(f"| {i} | {r['code']} {r['name']} | {machine} | {qv_view} | {rating} |")
    lines.append('')

    # 六、验证追踪
    lines.append('## 六、验证追踪（后续填写）')
    lines.append('')
    lines.append('| 日期 | 标的 | 判断 | 实际走势 | 验证结果 |')
    lines.append('|------|------|------|---------|---------|')
    lines.append('| | | | | |')
    lines.append('')

    # 七、详细信号日志
    lines.append('## 附录：详细信号日志')
    lines.append('')
    for r in results:
        if r['max_level'] >= 1 or r['code'] in QUALITATIVE_VIEWS:
            lines.append(f"### {r['code']} {r['name']}")
            lines.append('')
            for p in ['min30', 'min15', 'min5']:
                ana = r['periods'].get(p)
                if not ana:
                    continue
                lines.append(f"**{p}**: {ana['opportunity']}")
                if ana['signals']:
                    lines.append('```')
                    for s in ana['signals']:
                        ts = fmt_ts(s['ts'])
                        parts = []
                        if s['buy']: parts.append(f"★买")
                        if s['sell']: parts.append(f"★卖")
                        if s['ema']: parts.append(s['ema'])
                        if s['div']: parts.append(s['div'])
                        if s['cci']: parts.append(f"CCI={s['cci']}")
                        lines.append(f"  {ts} {' | '.join(parts)}")
                    lines.append('```')
                lines.append('')

    # 写入文件
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'[报告已生成] {report_path}')
    return report_path, results


def append_csv_log(date_str, results):
    """追加 CSV 回测日志"""
    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    headers = ['date', 'code', 'name', 'daily_macd', 'min30_opp', 'min15_opp', 'min5_opp',
               'max_opp_level', 'has_qualitative_view', 'user_view_summary', 'notes']

    file_exists = LOG_CSV.exists()
    with open(LOG_CSV, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        for r in results:
            qv = QUALITATIVE_VIEWS.get(r['code'], {})
            writer.writerow({
                'date': date_str,
                'code': r['code'],
                'name': r['name'],
                'daily_macd': r['daily']['env_short'] if r['daily'] else 'N/A',
                'min30_opp': r['periods'].get('min30', {}).get('opportunity', ''),
                'min15_opp': r['periods'].get('min15', {}).get('opportunity', ''),
                'min5_opp': r['periods'].get('min5', {}).get('opportunity', ''),
                'max_opp_level': r['max_level'],
                'has_qualitative_view': 'Y' if r['code'] in QUALITATIVE_VIEWS else 'N',
                'user_view_summary': qv.get('view', '')[:50],
                'notes': '',
            })
    print(f'[CSV日志已追加] {LOG_CSV}')


# ============================================================
# 命令行输出
# ============================================================

def print_console_summary():
    """命令行简要输出"""
    print('=' * 90)
    print('📊 机会扫描（直接读取快照，不重新计算）')
    print('=' * 90)

    for code, name in CODES:
        daily = get_daily_env(code)
        periods = {}
        for p in ['min30', 'min15', 'min5']:
            ana = analyze_period(code, p)
            if ana:
                periods[p] = ana

        if not periods:
            continue

        daily_str = daily['env_short'] if daily else 'N/A'
        print(f"\n🔹 {code} {name}  [日线: {daily_str}]")

        for p in ['min30', 'min15', 'min5']:
            ana = periods.get(p)
            if not ana:
                continue
            opp = ana['opportunity']
            # 只显示有意义的
            if ana['opp_level'] >= 1 or '金叉' in opp or '死叉' in opp or '背驰' in opp:
                print(f"  [{p}] {opp}")
                if ana['signals']:
                    for s in ana['signals'][-2:]:
                        ts = fmt_ts(s['ts'])
                        parts = []
                        if s['buy']: parts.append('★买')
                        if s['sell']: parts.append('★卖')
                        if s['ema']: parts.append(s['ema'])
                        if s['div']: parts.append(s['div'])
                        print(f"         └ {ts} {' | '.join(parts)}")

    print('\n' + '=' * 90)


def print_single_code(code, periods=['min30', 'min15', 'min5']):
    """输出单个标的的详细多周期对比"""
    name = next(n for c, n in CODES if c == code)
    print(f'\n🔍 {code} {name}')
    print('-' * 80)

    daily = get_daily_env(code)
    if daily:
        print(f'日线: close={daily["close"]} | {daily["env"]}')

    for p in periods:
        rows = read_snapshots(code, p, 20)
        if not rows:
            print(f'[{p}] 无数据')
            continue
        print(f'\n--- {p} (最近20条中有信号的) ---')
        for r in rows:
            buy = r.get('buy_signal', '').strip()
            sell = r.get('sell_signal', '').strip()
            ema = r.get('expma_cross', '').strip()
            div = r.get('cci_divergence', '').strip()
            ext = r.get('cci_extreme', '').strip()
            if buy or sell or ema or div or ext:
                ts = fmt_ts(r.get('timestamp', ''))
                cci = r.get('cci', '')[:8]
                parts = []
                if ext: parts.append(f'[{ext}]')
                if buy: parts.append('★买')
                if sell: parts.append('★卖')
                if ema: parts.append(ema)
                if div: parts.append(div)
                print(f'  {ts} CCI={cci} {" ".join(parts)}')


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='机会扫描 + AI分析报告生成')
    parser.add_argument('--report', action='store_true', help='生成 Markdown 报告 + CSV 日志')
    parser.add_argument('--ai', action='store_true', help='报告生成后调用多 API 智能分析（Cloudflare→SiliconFlow→NVIDIA 自动切换）')
    parser.add_argument('--code', type=str, help='指定标的代码 (如 sh513310)')
    parser.add_argument('--period', type=str, default='min30', help='指定周期 (默认 min30)')
    parser.add_argument('--date', type=str, help='指定日期 (YYYYMMDD格式，默认今天)')
    args = parser.parse_args()

    # 加载 .env 中的环境变量
    env_file = BASE / '.env'
    if env_file.exists():
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    if args.report:
        date_str = args.date or datetime.now().strftime('%Y%m%d')
        report_path, results = generate_report(date_str)
        append_csv_log(date_str, results)
        print_console_summary()

        # AI 分析（如果启用）
        if args.ai:
            print('\n[AI分析] 正在调用多 API 智能分析...')
            try:
                from ai_analyzer import analyze_report
                with open(report_path, 'r', encoding='utf-8') as f:
                    report_text = f.read()
                ai_result = analyze_report(report_text)
                if ai_result.get('error'):
                    # 降级：即使 analyze_report 内部已尝试所有 provider，
                    # 这里也把错误信息写入报告，避免空白
                    err_msg = ai_result['error']
                    print(f'  [AI分析失败] {err_msg}')
                    with open(report_path, 'a', encoding='utf-8') as f:
                        f.write(f'\n\n---\n\n## AI 智能分析\n\n[所有 API 均失败] {err_msg}\n')
                else:
                    provider = ai_result.get('provider', 'unknown')
                    content = ai_result.get('content', '')
                    # 追加 AI 分析到报告末尾
                    with open(report_path, 'a', encoding='utf-8') as f:
                        f.write(f'\n\n---\n\n## AI 智能分析（provider: {provider}）\n\n')
                        f.write(content)
                    print(f'[AI分析已追加] provider={provider} | {report_path}')
            except Exception as e:
                print(f'[AI分析失败] {e}')
                with open(report_path, 'a', encoding='utf-8') as f:
                    f.write(f'\n\n---\n\n## AI 智能分析\n\n[异常] {e}\n')

    elif args.code:
        print_single_code(args.code, args.period)
    else:
        print_console_summary()

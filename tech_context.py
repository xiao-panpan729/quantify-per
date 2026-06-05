# -*- coding: utf-8 -*-
"""
技术语境生成器 — 从现有数据提取重组，不添加新指标/新阈值。

用途：把散落在 cycle_report.json / latest.json / 各周期CSV 的信息
      汇总成一份结构化简报，供 ai_analyzer.py 做判断。

铁律：
  - 只读现有数据、只格式化、不计算新指标
  - 不设新阈值、不做新分类
  - 判断留给 AI

用法：
  python tech_context.py sz159740            # 单标输出
  python tech_context.py sz159740 --save     # 保存到 signals/tracking/{code}/tech_context.md
"""

import csv
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE))
from config import NAME_MAP

TRACKING = BASE / 'signals' / 'tracking'

PERIOD_ORDER = ['daily', 'min60', 'min30', 'min15', 'min5']
PERIOD_LABEL = {
    'daily': '日线', 'min60': '60分', 'min30': '30分',
    'min15': '15分', 'min5': '5分',
}

# ── helpers ──

def _safe(v, default=''):
    if v is None:
        return default
    return v

def _sf(v, default=0.0):
    """safe float"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return default

def _pct(v, ndigits=1):
    """format as percentage"""
    return f"{v:+.{ndigits}f}%"


# ── data loaders ──

def _load_cycle(code):
    path = TRACKING / 'cycle_report.json'
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for entry in data:
        if entry.get('code') == code:
            return entry
    return None


def _load_latest(code):
    path = TRACKING / 'latest.json'
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('stocks', {}).get(code, {})


def _load_csv_tail(code, period, tail=80):
    path = TRACKING / code / f'{period}_signals.csv'
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    return rows[-tail:] if len(rows) > tail else rows


def _count_signals(rows, tail=20):
    """Count ★买/★卖/金叉/死叉 in recent N bars"""
    buys, sells, goldens, deaths = 0, 0, 0, 0
    for r in rows[-tail:]:
        if r.get('buy_signal', '').strip():
            buys += 1
        if r.get('sell_signal', '').strip():
            sells += 1
        cross = r.get('expma_cross', '')
        if '金' in cross:
            goldens += 1
        elif '死' in cross:
            deaths += 1
    return buys, sells, goldens, deaths


# ── section builders ──

def _section_one(cycle, daily_rows):
    """视角一：大格局 — 现在处于什么位置？什么趋势？什么环境？"""
    trend = cycle.get('trend', {}) if cycle else {}
    pos = cycle.get('position', {}) if cycle else {}
    dd = cycle.get('dominant_direction', {}) if cycle else {}
    mc = cycle.get('market_coeff', {}) if cycle else {}
    rhythm = cycle.get('rhythm', {}) if cycle else {}
    resonance = cycle.get('resonance', {}) if cycle else {}
    advice = cycle.get('advice', {}) if cycle else {}

    lines = []

    # ── 日线趋势 ──
    macd_score = trend.get('macd_score', '?')
    ma_score = trend.get('ma_score', '?')
    cycle_score = trend.get('cycle_score', '?')
    total_score = trend.get('score', '?')

    lines.append(f"日线方向: **{trend.get('label', '?')}**  "
                 f"总分={total_score}/14 (MACD={macd_score}/4 MA排列={ma_score}/6 闭环={cycle_score}/4)")
    lines.append(f"操作区: {trend.get('zone_label', '?')} "
                 f"({trend.get('zone_advice', '?')})")
    lines.append(f"MACD: DIF={trend.get('macd_dif', '?')} DEA={trend.get('macd_dea', '?')}")

    for d in trend.get('details', []):
        lines.append(f"  > {d}")

    # ── 价格位置 ──
    lines.append(f"\n价格位置: **{pos.get('label', '?')}** — {pos.get('description', '?')}")
    lines.append(f"收盘={pos.get('close', '?')}  "
                 f"白线(EXPMA12)={pos.get('expma12', '?')}  黄线(EXPMA50)={pos.get('expma50', '?')}")
    lines.append(f"偏离白线: {pos.get('deviation_white_pct', '?')}%  偏离黄线: {pos.get('deviation_yellow_pct', '?')}%")
    lines.append(f"风险等级: {pos.get('risk_level', '?')}")

    # ── 均线排列链 ──
    if daily_rows:
        r = daily_rows[-1]
        c = _sf(r.get('close'))
        mas = [('MA5', r.get('ma5')), ('MA10', r.get('ma10')),
               ('MA20', r.get('ma20')), ('MA60', r.get('ma60')),
               ('MA120', r.get('ma120')), ('MA250', r.get('ma250'))]
        parts = [f"C({c})"]
        for name, val in mas:
            v = _sf(val)
            if v <= 0:
                continue
            op = '>' if c >= v else '<'
            parts.append(f"{op}{name}({round(v, 3) if isinstance(v, float) else v})")
        lines.append(f"均线链: {' '.join(parts)}")

        # BB
        bb_ma = _sf(r.get('bb_ma221'))
        bb_red = _sf(r.get('bb_red_line'))
        if bb_ma > 0:
            lines.append(f"BB中轨: {bb_ma}  BB红线: {bb_red}")

    # ── 跨周期聚合 ──
    if dd:
        lines.append(f"\n跨周期聚合: **{dd.get('label', '?')}**")
        chain = dd.get('chain', '')
        if chain:
            lines.append(f"方向传导链: {chain}")
        lines.append(f"收敛度: {dd.get('convergence', '?')}  变化趋势: {dd.get('change', '?')}  "
                     f"net_score={dd.get('net_score', '?')}")

    # ── 环境 ──
    lines.append(f"\n大盘系数: {mc.get('coefficient', '?')}x ({mc.get('label', '?')})")

    # ── 节奏与共振 ──
    if rhythm:
        lines.append(f"节奏: {rhythm.get('verdict', '?')} — {rhythm.get('label') or '?'}")
    if resonance:
        res_c = '✓' if resonance.get('resonance_confirmed') else '✗'
        res_side = resonance.get('resonance_side') or 'neutral'
        res_lv = resonance.get('resonance_level', '?')
        res_detail = resonance.get('resonance_detail', '')
        lines.append(f"5+15共振: {res_c} 侧={res_side} 级={res_lv} {res_detail}")

    # ── 操作建议 ──
    if advice:
        lines.append(f"\n操作建议: **{advice.get('grade_label', '?')}** "
                     f"({advice.get('grade', '?')})  置信度: {advice.get('confidence', '?')}")
        lines.append(f"动作: {advice.get('action', '?')}")
        if advice.get('summary'):
            lines.append(f"{advice['summary']}")
        if advice.get('wait_condition'):
            lines.append(f"等待条件: {advice['wait_condition']}")
        if advice.get('dominant_note'):
            lines.append(f"{advice['dominant_note']}")

    return '\n'.join(lines)


def _section_two(cycle, latest, csv_cache):
    """视角二：多周期状态 — 各周期在做什么？对齐还是打架？"""
    periods = cycle.get('periods', {}) if cycle else {}

    lines = ["| 周期 | 收盘 | EXPMA | MACD | ★买/卖 | ★买(50) | ★卖(50) | 金/死叉(20) | 信号级别 | PE状态 |",
             "|:-----|:-----|:------|:-----|:-------|:--------|:--------|:------------|:---------|:-------|"]

    for period in PERIOD_ORDER:
        lp = latest.get(period, {}) if latest else {}
        pp = periods.get(period, {}) if periods else {}
        sq = pp.get('signal_quality', {}) if pp else {}
        tp = pp.get('trend_pe', {}) if pp else {}
        rows = csv_cache.get(period, [])

        # 收盘价 — 分钟线优先从CSV取（latest.json的分钟线没有close字段）
        close_d = '-'
        if period == 'daily':
            close_v = lp.get('close', '-')
            close_d = str(close_v) if close_v != '-' else '-'
        elif rows:
            close_v = _sf(rows[-1].get('close'))
            if close_v > 100:  # 分钟线 x10000
                close_d = f"{close_v / 10000:.4f}"
            else:
                close_d = f"{close_v:.4f}"

        expma_s = lp.get('expma_status', '-')
        macd_s = lp.get('macd_status', '-')
        signal = lp.get('signal', '-')
        if signal == '无':
            signal = '-'

        buy50 = lp.get('buy_count_50', 0) or 0
        sell50 = lp.get('sell_count_50', 0) or 0

        # 从CSV统计近20根信号
        if rows:
            buys20, sells20, goldens20, deaths20 = _count_signals(rows, 20)
            cross20 = f"{goldens20}金/{deaths20}死" if (goldens20 or deaths20) else '-'
        else:
            cross20 = '-'

        # 信号级别（来自cycle_report analysis）
        if sq:
            lv = sq.get('level', 0)
            lbl = sq.get('label', '-')
            lv_str = f"{lbl}({lv:.1f})"
        else:
            lv_str = '-'

        # PE状态
        pe_val = ''
        if tp:
            pe_f = tp.get('pe_front', 0)
            pe_b = tp.get('pe_back', 0)
            pe_p = tp.get('pe_phase', '')
            pe_v = tp.get('pe_velocity', '')
            if pe_f or pe_b:
                pe_val = f"前{pe_f:.3f}/后{pe_b:.3f} {pe_p}"
        if not pe_val and rows:
            r = rows[-1]
            pe_r = r.get('pe', '')
            pe_l = r.get('pe_level', '')
            pe_c = r.get('pe_chg_5', '')
            pe_val = f"{pe_r}/{pe_l}" if pe_r else '-'
            if pe_c:
                pe_val += f" chg5={pe_c}"

        lines.append(
            f"| {PERIOD_LABEL.get(period, period)} | {close_d} | {expma_s} | {macd_s} "
            f"| {signal} | {buy50} | {sell50} | {cross20} | {lv_str} | {pe_val} |"
        )

    # ── 附属分析 ──
    ws = cycle.get('wave_structure', {}) if cycle else {}
    if ws and ws.get('label'):
        lines.append(f"\n波结构: {ws.get('label', '')}  "
                     f"方向={ws.get('direction', '')}  主导周期={ws.get('dominant_period', '')}  "
                     f"结构={ws.get('structure', '')}")

    exp_r = cycle.get('exp_readiness', {}) if cycle else {}
    if exp_r and exp_r.get('level'):
        lines.append(f"指数级行情信号: {exp_r.get('level', '')} "
                     f"({exp_r.get('score', '')}/10)  "
                     f"压缩={exp_r.get('compression', '')} 加速={exp_r.get('acceleration', '')} 锁定={exp_r.get('lockin', '')}")

    rs = cycle.get('rs_density', {}) if cycle else {}
    if rs and rs.get('label'):
        lines.append(f"缠论阻支: rs={rs.get('rs_score', '?')}  {rs.get('label', '')}  "
                     f"支撑={rs.get('support_label', '')}  阻力={rs.get('resistance_label', '')}")

    return '\n'.join(lines)


def _section_three(cycle, daily_rows):
    """视角三：关键位置 — 支撑在哪？阻力在哪？现在在什么位置？"""
    lines = []

    if not daily_rows:
        lines.append("(无日线数据)")
        return '\n'.join(lines)

    r = daily_rows[-1]
    c = _sf(r.get('close'))

    # ── 均线支撑/阻力 ──
    levels = []
    for key, label in [
        ('ma5', 'MA5'), ('ma10', 'MA10'), ('ma20', 'MA20'),
        ('ma60', 'MA60'), ('ma120', 'MA120'), ('ma250', 'MA250'),
        ('bb_ma221', 'BB中轨'), ('bb_red_line', 'BB红线'),
        ('expma12', 'EXPMA白'), ('expma50', 'EXPMA黄'),
    ]:
        v = _sf(r.get(key))
        if v <= 0:
            continue
        dist = (c - v) / v * 100 if v else 0
        levels.append((label, v, dist))

    supports = [(l, v, d) for l, v, d in levels if d >= -0.05]  # >=0 means price at or above level
    resistances = [(l, v, d) for l, v, d in levels if d < -0.05]

    if supports:
        supports.sort(key=lambda x: abs(x[2]))  # closest first
        s_strs = [f"{l}({v:.3f} 距{_pct(d)})" for l, v, d in supports[:6]]
        lines.append(f"下方支撑: {' | '.join(s_strs)}")

    if resistances:
        resistances.sort(key=lambda x: abs(x[2]))  # closest first
        r_strs = [f"{l}({v:.3f} 距{_pct(d)})" for l, v, d in resistances[:6]]
        lines.append(f"上方阻力: {' | '.join(r_strs)}")

    # ── 近期高低点 ──
    if len(daily_rows) >= 20:
        recent = daily_rows[-20:]
        highs = [_sf(rr.get('high')) for rr in recent]
        lows = [_sf(rr.get('low')) for rr in recent]
        lines.append(f"近20日: 最高={max(highs):.3f}  最低={min(lows):.3f}  " +
                     f"(距最高{_pct((c - max(highs)) / max(highs) * 100)})  " +
                     f"(距最低{_pct((c - min(lows)) / min(lows) * 100)})")

    if len(daily_rows) >= 60:
        recent60 = daily_rows[-60:]
        highs60 = [_sf(rr.get('high')) for rr in recent60]
        lines.append(f"近60日: 最高={max(highs60):.3f}  "
                     f"(距最高{_pct((c - max(highs60)) / max(highs60) * 100)})")

    # ── 红线距离 ──
    red_line = _sf(r.get('bb_red_line'))
    if red_line > 0:
        lines.append(f"BB红线距离: {_pct((c - red_line) / red_line * 100)} "
                     f"(红线={red_line:.3f}  C={c:.3f})")

    return '\n'.join(lines)


def _section_four(cycle, daily_rows):
    """视角四：量价关系 — 有量支撑吗？是真突破还是假突破？"""
    lines = []

    if not daily_rows:
        lines.append("(无日线数据)")
        return '\n'.join(lines)

    r = daily_rows[-1]

    # ── 当前量能标签 ──
    vol_flags = []
    vol_map = [
        ('vol_llv100', '百日地量'), ('vol_llv10', '十日地量'),
        ('vol_hhv100', '百日高量'), ('vol_hhv10', '十日高量'),
        ('vol_堆', '地量堆(6日≥3次十日低)'),
        ('vol_放堆', '放量堆(6日≥3次十日高)'),
        ('vol_缩50', '缩量过半(vr5<0.5)'),
        ('vol_突放', '放量突破(C>前5高+vr5>1.5)'),
        ('vol_梯度升', '连续3日放量'),
        ('vol_梯度降', '连续3日缩量'),
        ('cci_divergence', f"CCI{r.get('cci_divergence', '')}"),
    ]
    for key, label in vol_map:
        v = r.get(key, '')
        if v == '1' or (v and v != '0' and v != ''):
            vol_flags.append(label)

    vr5 = _sf(r.get('vr5'))
    vr60 = _sf(r.get('vr60'))
    cci = _sf(r.get('cci'))

    lines.append(f"成交量: {r.get('volume', '?')}  "
                 f"量比: vr5={vr5:.2f}  vr60={vr60:.2f}")
    if vol_flags:
        lines.append(f"当前特征: {', '.join(vol_flags)}")
    else:
        lines.append(f"当前特征: 无特殊量能标记")
    lines.append(f"CCI: {r.get('cci', '?')} ({r.get('cci_extreme', '')} {r.get('cci_retreat', '')})")

    # ── 量价配合（近10日） ──
    if len(daily_rows) >= 10:
        recent = daily_rows[-10:]
        up_vol = []   # 涨时量
        up_cnt = 0
        down_vol = []  # 跌时量
        down_cnt = 0
        for i in range(1, len(recent)):
            prev_c = _sf(recent[i - 1].get('close'))
            cur_c = _sf(recent[i].get('close'))
            cur_v = _sf(recent[i].get('volume'))
            if cur_c > prev_c:
                up_vol.append(cur_v)
                up_cnt += 1
            elif cur_c < prev_c:
                down_vol.append(cur_v)
                down_cnt += 1

        if up_cnt > 0 and down_cnt > 0:
            avg_up_v = sum(up_vol) / len(up_vol)
            avg_down_v = sum(down_vol) / len(down_vol)
            if avg_up_v > avg_down_v * 1.2:
                lines.append(f"量价配合: 涨放量/跌缩量 ✓ (涨均{avg_up_v:.0f} > 跌均{avg_down_v:.0f})")
            elif avg_down_v > avg_up_v * 1.2:
                lines.append(f"量价配合: 涨缩量/跌放量 ✗ (涨均{avg_up_v:.0f} < 跌均{avg_down_v:.0f})")
            else:
                lines.append(f"量价配合: 持平 (涨均{avg_up_v:.0f} ≈ 跌均{avg_down_v:.0f})")

    # ── 量价阶段（来自 cycle_report） ──
    vr = cycle.get('volume_regime', {}) if cycle else {}
    if vr:
        lines.append(f"\n量价阶段: {vr.get('label', '?')} — {vr.get('phase', '?')}")
        if vr.get('details'):
            lines.append(f"{vr['details']}")

    return '\n'.join(lines)


def _section_five(cycle, csv_cache):
    """视角五：信号质量 — 如果现在有信号，可信吗？卡在哪一维？"""
    lines = []

    periods = cycle.get('periods', {}) if cycle else {}

    for period in ['min5', 'min15', 'min30', 'daily']:
        pp = periods.get(period, {})
        if not pp:
            continue
        sq = pp.get('signal_quality', {})
        if not sq:
            continue

        buy_lv = sq.get('buy_level', 0) or 0
        sell_lv = sq.get('sell_level', 0) or 0
        label = sq.get('label', '-')
        details = sq.get('details', []) or []

        # 只取关键细节（跳过压制/增强/参考信息）
        core_details = [
            d for d in details
            if '参考' not in d
            and '压制' not in d
            and '增强' not in d
            and '共振' not in d
            and '反向' not in d
        ]

        lines.append(f"\n**{PERIOD_LABEL.get(period, period)}** — 买侧{buy_lv:.1f} 卖侧{sell_lv:.1f} ({label})")
        for d in core_details[:6]:
            lines.append(f"  · {d}")

        # PE 状态
        tp = sq.get('trend_pe', {})
        if tp:
            lines.append(f"  PE: 前{tp.get('pe_front', '?')} 后{tp.get('pe_back', '?')}  "
                         f"相位={tp.get('pe_phase', '?')}  速度={tp.get('pe_velocity', '?')}  "
                         f"trending={tp.get('trending', '?')}")

    # ── HHT ──
    daily_rows = csv_cache.get('daily', [])
    if daily_rows:
        r = daily_rows[-1]
        hht_f = r.get('hht_freq', '')
        hht_a = r.get('hht_amp', '')
        if hht_f or hht_a:
            lines.append(f"\nHHT(日线): 瞬时频率={hht_f}  瞬时振幅={hht_a}")

    # ── PE 轨迹 ──
    if daily_rows and len(daily_rows) >= 10:
        recent = daily_rows[-10:]
        pe_seq = [_sf(rr.get('pe')) for rr in recent if rr.get('pe', '') != '']
        if len(pe_seq) >= 3:
            pe_str = ' → '.join([f'{p:.3f}' for p in pe_seq])
            pe_dir = '上升(无序化)' if pe_seq[-1] > pe_seq[0] else '下降(有序化)'
            lines.append(f"\nPE轨迹(近10日): {pe_str} ({pe_dir})")
            pe_chg5 = daily_rows[-1].get('pe_chg_5', '')
            if pe_chg5:
                lines.append(f"pe_chg_5: {pe_chg5}")

    # ── CCI 闭环状态 ──
    if daily_rows:
        r = daily_rows[-1]
        cci = _sf(r.get('cci'))
        cci_ext = r.get('cci_extreme', '')
        cci_ret = r.get('cci_retreat', '')
        cci_div = r.get('cci_divergence', '')
        if cci_ext or cci_div:
            lines.append(f"\nCCI闭环(日线): CCI={cci} 极值={cci_ext} 回落={cci_ret} 背驰={cci_div}")

    return '\n'.join(lines)


# ── 主入口 ──

def build_tech_context(code, csv_tail=80):
    """
    构建技术语境 — 纯数据重组。

    Args:
        code: 股票代码，如 'sz159740'
        csv_tail: 各周期读取最近多少根 bar（默认80）

    Returns:
        str: markdown 格式的技术语境
    """
    name = NAME_MAP.get(code, code)
    cycle = _load_cycle(code)
    latest = _load_latest(code)

    # 预读 CSV（每个周期最近 N 条）
    csv_cache = {}
    for period in PERIOD_ORDER:
        csv_cache[period] = _load_csv_tail(code, period, tail=csv_tail)

    daily_rows = csv_cache.get('daily', [])

    # 日期
    date_str = ''
    if daily_rows:
        date_str = daily_rows[-1].get('date', '')
    elif latest and latest.get('daily'):
        date_str = latest['daily'].get('date', '')

    # ── 组装 ──
    out = [
        f"# {code} {name} · {date_str}",
        "",
    ]

    # 视角一：大格局
    out.append("## 一、大格局（趋势·位置·环境）")
    out.append("")
    if cycle:
        out.append(_section_one(cycle, daily_rows))
    else:
        out.append("⚠ 无 cycle_report 数据，请先运行 `python cycle_engine.py --save`")
    out.append("")

    # 视角二：多周期
    out.append("## 二、多周期状态（日线→60→30→15→5分）")
    out.append("")
    if cycle and latest:
        out.append(_section_two(cycle, latest, csv_cache))
    else:
        out.append("⚠ 无数据")
    out.append("")

    # 视角三：关键位置
    out.append("## 三、关键位置（支撑·阻力·距离）")
    out.append("")
    out.append(_section_three(cycle, daily_rows))
    out.append("")

    # 视角四：量价关系
    out.append("## 四、量价关系（成交量·量价配合·量价阶段）")
    out.append("")
    out.append(_section_four(cycle, daily_rows))
    out.append("")

    # 视角五：信号质量
    out.append("## 五、信号质量（递进维度·PE·HHT·CCI闭环）")
    out.append("")
    if cycle:
        out.append(_section_five(cycle, csv_cache))
    else:
        out.append("⚠ 无数据")
    out.append("")

    return '\n'.join(out)


# ── CLI ──

def main():
    if len(sys.argv) < 2:
        print("用法: python tech_context.py <code> [--save]")
        print("示例: python tech_context.py sz159740")
        sys.exit(1)

    code = sys.argv[1]
    do_save = '--save' in sys.argv

    ctx = build_tech_context(code)
    # Handle Windows GBK encoding
    try:
        print(ctx)
    except UnicodeEncodeError:
        print(ctx.encode('utf-8', errors='replace').decode('utf-8', errors='replace'))

    if do_save:
        out_dir = TRACKING / code
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / 'tech_context.md'
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(ctx)
        print(f"\n→ 已保存: {out_path}")


if __name__ == '__main__':
    main()

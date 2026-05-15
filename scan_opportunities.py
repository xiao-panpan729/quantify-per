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
SOLD_POSITIONS_FILE = BASE / 'tracking_notes' / 'sold_positions.json'

# ==== 已卖出持仓跟踪（需手动维护） ====
# 格式: { 'code': { 'sold_date': 'YYYYMMDD', 'sold_price': float, 'reason': str } }
def load_sold_positions():
    if SOLD_POSITIONS_FILE.exists():
        try:
            with open(SOLD_POSITIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}

# 跟踪标的列表统一从 config.NAME_MAP 读取，不再单独维护
def _get_tracking_codes():
    sys.path.insert(0, str(BASE))
    from config import NAME_MAP
    return [(code, name) for code, name in NAME_MAP.items()]

CODES = _get_tracking_codes()

# 扫描周期（分钟级别，日线单独处理）
SCAN_PERIODS = ['min30', 'min15', 'min5']

# 趋势等级常量
TREND_LABELS = {'A': 'A最强', 'B': 'B次强', 'C': 'C偏弱', 'D': 'D弱势'}

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
    """获取日线MACD环境 + EXPMA趋势强度
    
    趋势强度分级（日线）：
    A最强: 股价在EXPMA12(白线)上方 且 MACD黄白线都在0轴上方
    B次强: 股价在EXPMA12-EXPMA50(白线-黄线)间 且 MACD黄白线都在0轴上方
    C偏弱: 股价在EXPMA50(黄线)下方 且 MACD黄白线都在0轴上方
    D弱势: 股价在黄线下方 且 MACD黄白线至少一个在0轴下方
    """
    rows = read_snapshots(code, 'daily', 3)
    if not rows:
        return None
    last = rows[-1]
    dif = last.get('macd_dif', '')
    dea = last.get('macd_dea', '')
    close = last.get('close', '')
    # CSV字段名是expma12/expma50，不是expma_white/expma_yellow
    expma_white = last.get('expma12', last.get('expma_white', ''))
    expma_yellow = last.get('expma50', last.get('expma_yellow', ''))
    
    # 趋势强度判断
    trend_strength = 'D'
    try:
        c = float(close)
        w = float(expma_white) if expma_white else 0
        y = float(expma_yellow) if expma_yellow else 0
        dif_f = float(dif) if dif else -1
        dea_f = float(dea) if dea else -1
        
        # MACD黄白线都在0轴上方？
        macd_bull = dif_f > 0 and dea_f > 0
        
        if macd_bull:
            if w > 0 and c > w:
                trend_strength = 'A'  # 白线上方 + MACD黄白线>0 = 最强
            elif w > 0 and y > 0 and y < c <= w:
                trend_strength = 'B'  # 白线-黄线间 + MACD黄白线>0 = 次强
            else:
                trend_strength = 'C'  # 黄线下方 + MACD黄白线>0 = 偏弱
        else:
            # MACD至少一个在0轴下方
            if w > 0 and y > 0 and c > y:
                trend_strength = 'C'  # 还在黄线上方但MACD走弱 = 仍算偏弱
            else:
                trend_strength = 'D'  # 黄线下方 + MACD走弱 = 弱势
    except:
        pass
    
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
        'close': close,
        'env': env,
        'env_short': env_short,
        'dif': dif,
        'dea': dea,
        'trend_strength': trend_strength,
        'expma_white': expma_white,
        'expma_yellow': expma_yellow,
    }


def find_cci_extremes(rows):
    """从后往前找 CCI 极值，同时返回买入侧和卖出侧各一个最近的"""
    buy_ext = None   # -200/-250/-300
    sell_ext = None  # +200/+250
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        ext = r.get('cci_extreme', '').strip()
        if not ext:
            continue
        entry = {
            'ts': r.get('timestamp', ''),
            'cci': r.get('cci', '')[:8],
            'extreme': ext,
            'close': r.get('raw_close', r.get('close', '')),
        }
        if ('-200' in ext or '-250' in ext or '-300' in ext) and buy_ext is None:
            buy_ext = entry
        elif ('+200' in ext or '+250' in ext) and sell_ext is None:
            sell_ext = entry
        if buy_ext and sell_ext:
            break
    extremes = []
    # 按时间从早到晚排列（先买侧后卖侧是自然的时序）
    if buy_ext:
        extremes.append(buy_ext)
    if sell_ext:
        extremes.append(sell_ext)
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


def _extract_events(rows):
    """从 rows 中提取信号事件列表，统一 buy/sell/ema/div 格式"""
    events = []
    for r in rows:
        buy = r.get('buy_signal', '').strip()
        sell = r.get('sell_signal', '').strip()
        ema = r.get('expma_cross', '').strip()
        div = r.get('cci_divergence', '').strip()
        if buy or sell or ema or div:
            events.append({
                'ts': r.get('timestamp', ''),
                'buy': buy,
                'sell': sell,
                'ema': ema,
                'div': div,
                'cci': r.get('cci', '')[:8],
                'close': r.get('raw_close', r.get('close', '')),
            })
    return events


def analyze_period(code, period, _rows=None):
    """分析某周期，返回结构化结果。_rows可选，传入则跳过读CSV"""
    rows = _rows if _rows is not None else read_snapshots(code, period, 80)
    if not rows:
        return None

    last = rows[-1]
    ema_latest = last.get('expma_cross', '').strip()

    # CCI极值
    cci_ext = find_cci_extremes(rows)

    # 重新设计信号收集：从最近的CCI极值位置开始往后扫
    # 而不是只看最后5条
    sigs = find_recent_signals(rows, 5)
    if cci_ext:
        # 找到CCI极值在rows中的位置
        ext_ts = cci_ext[0].get('ts', '')
        ext_idx = None
        for i, r in enumerate(rows):
            if r.get('timestamp', '') == ext_ts:
                ext_idx = i
                break
        if ext_idx is not None:
            # 从极值位置之后开始收集信号，不限制数量
            sigs_from_ext = _extract_events(rows[ext_idx:])
            if sigs_from_ext:
                sigs = sigs_from_ext

    # 判断机会类型——遍历所有极值，分别判断买入闭环和卖出闭环
    opportunity = []
    opp_level = 0  # 0=无 1=观察 2=接近 3=完整闭环

    for cci_entry in cci_ext:
        ext = cci_entry['extreme']
        # 从该极值位置往后收集信号
        ext_ts = cci_entry.get('ts', '')
        ext_idx = None
        for i, r in enumerate(rows):
            if r.get('timestamp', '') == ext_ts:
                ext_idx = i
                break
        post_sigs = sigs
        if ext_idx is not None:
            post_sigs = _extract_events(rows[ext_idx:])

        has_buy = any(s['buy'] for s in post_sigs)
        has_sell = any(s['sell'] for s in post_sigs)
        has_gold = any('金叉' in s['ema'] for s in post_sigs)
        has_dead = any('死叉' in s['ema'] for s in post_sigs)
        has_div = any(s['div'] for s in post_sigs)

        if '-200' in ext or '-250' in ext or '-300' in ext:
            if has_buy and has_gold:
                opportunity.append('✅完整闭环(买)')
                opp_level = max(opp_level, 3)
            elif has_buy:
                opportunity.append('⚠️部分闭环: ★买(缺金叉)')
                opp_level = max(opp_level, 2)
            else:
                opportunity.append('👀观察: CCI负极限(等★买+金叉)')
                opp_level = max(opp_level, 1)
        elif '+200' in ext or '+250' in ext:
            if has_sell and has_dead:
                opportunity.append('❌完整闭环(卖)')
                opp_level = max(opp_level, 3)
            elif has_sell:
                opportunity.append('⚠️部分闭环: ★卖(缺死叉)')
                opp_level = max(opp_level, 2)
            else:
                opportunity.append('⏸️观察: CCI正极限(等★卖+死叉)')
                opp_level = max(opp_level, 1)

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
# 闭环检测引擎 v2.0
# ============================================================

def score_closing(cci_ext, cci_div, buy_or_sell, ema_cross, cci_before_signal, has_price_extreme):
    """
    闭环评分系统
    
    参数:
      cci_ext: CCI极值类型 ('-200'/'+200' 等)
      cci_div: CCI背驰类型 ('底背驰'/'顶背驰')
      buy_or_sell: 信号类型 ('★买'/'★卖')
      ema_cross: EXPMA交叉 ('金叉'/'死叉')
      cci_before_signal: CCI背驰是否在信号之前 (True=加分, False=减分)
      has_price_extreme: 价格是否创阶段新低/新高
    
    返回: (score, level_label, conditions)
    """
    score = 0.0
    conditions = []
    
    if cci_ext:
        score += 1.0
        conditions.append(f'CCI极值({cci_ext})')
    if cci_div:
        score += 1.0
        conditions.append(f'CCI背驰({cci_div})')
    if buy_or_sell:
        score += 1.0
        conditions.append(buy_or_sell)
    if ema_cross:
        score += 1.0
        conditions.append(f'EXPMA{ema_cross}')
    
    if has_price_extreme:
        score += 0.5
        conditions.append('价格创极值')
    
    if cci_before_signal:
        score += 0.5
    else:
        if cci_div:
            score -= 0.5
            conditions.append('时序瑕疵(-0.5)')
    
    level_label = '— 无效'
    if score >= 4:
        level_label = '✅✅ 大级别闭环'
    elif score >= 3:
        level_label = '✅ 完整闭环'
    elif score >= 2:
        level_label = '⚠️ 部分闭环'
    elif score >= 1:
        level_label = '👀 观测信号'
    
    return round(score, 1), level_label, conditions


def detect_closings(code, periods_data, daily_trend):
    """
    检测所有周期的闭环信号
    
    返回: dict
      'buy_closings': list[dict] 买入闭环列表
      'sell_closings': list[dict] 卖出闭环列表
      'reverse_signals': list[dict] 反向信号列表
      'resonance': dict 多级共振信息
    """
    buy_closings = []
    sell_closings = []
    
    period_labels = {'min5': '5分钟', 'min15': '15分钟', 'min30': '30分钟'}
    
    for period in ['min5', 'min15', 'min30']:
        rows = periods_data.get(period, [])
        if not rows:
            continue
        
        for i, row in enumerate(rows):
            cci_ext = row.get('cci_extreme', '').strip()
            if not cci_ext:
                continue
            
            # CCI极值出现后可能在10根bar内出现★买/金叉等信号
            # 特别在尾盘：CCI极值14:20 → ★买15:00 隔了8根bar
            look_forward = rows[i:min(i+12, len(rows))]
            
            has_buy = any(r.get('buy_signal', '').strip() for r in look_forward)
            has_sell = any(r.get('sell_signal', '').strip() for r in look_forward)
            has_gold = any('金叉' in r.get('expma_cross', '') for r in look_forward)
            has_dead = any('死叉' in r.get('expma_cross', '') for r in look_forward)
            has_neg_div = any('底背驰' in r.get('cci_divergence', '') for r in look_forward)
            has_pos_div = any('顶背驰' in r.get('cci_divergence', '') for r in look_forward)
            
            recent_bars = rows[max(0,i-20):i+1]
            if recent_bars:
                closes_vals = [float(r.get('raw_close', r.get('close', 0))) for r in recent_bars]
                current_close = float(row.get('raw_close', row.get('close', 0)))
                is_price_low = current_close <= min(closes_vals)
                is_price_high = current_close >= max(closes_vals)
            else:
                is_price_low = False
                is_price_high = False
            
            # 买入闭环检测
            if cci_ext.startswith('-') and (has_buy or has_gold):
                cci_div_idx = -1
                buy_or_cross_idx = -1
                for j, r in enumerate(look_forward):
                    if r.get('cci_divergence', '').strip() and cci_div_idx < 0:
                        cci_div_idx = j
                    if (r.get('buy_signal', '').strip() or '金叉' in r.get('expma_cross', '')) and buy_or_cross_idx < 0:
                        buy_or_cross_idx = j
                
                cci_before = cci_div_idx >= 0 and buy_or_cross_idx >= 0 and cci_div_idx <= buy_or_cross_idx
                
                score, level, conditions = score_closing(
                    cci_ext=cci_ext,
                    cci_div='底背驰' if has_neg_div else '',
                    buy_or_sell='★买' if has_buy else '',
                    ema_cross='金叉' if has_gold else '',
                    cci_before_signal=cci_before,
                    has_price_extreme=is_price_low,
                )
                
                if score >= 1.0:
                    buy_closings.append({
                        'type': 'buy_closing',
                        'level': period_labels[period],
                        'level_key': period,
                        'timestamp': str(row.get('timestamp', '')),
                        'price': float(row.get('raw_close', row.get('close', 0))),
                        'score': score,
                        'level_label': level,
                        'conditions': conditions,
                        'trend_before': daily_trend,
                        'cci_before_signal': cci_before,
                        'has_price_extreme': is_price_low,
                    })
            
            # 卖出闭环检测
            if cci_ext.startswith('+') and (has_sell or has_dead):
                cci_div_idx = -1
                sell_or_cross_idx = -1
                for j, r in enumerate(look_forward):
                    if r.get('cci_divergence', '').strip() and cci_div_idx < 0:
                        cci_div_idx = j
                    if (r.get('sell_signal', '').strip() or '死叉' in r.get('expma_cross', '')) and sell_or_cross_idx < 0:
                        sell_or_cross_idx = j
                
                cci_before = cci_div_idx >= 0 and sell_or_cross_idx >= 0 and cci_div_idx <= sell_or_cross_idx
                
                score, level, conditions = score_closing(
                    cci_ext=cci_ext,
                    cci_div='顶背驰' if has_pos_div else '',
                    buy_or_sell='★卖' if has_sell else '',
                    ema_cross='死叉' if has_dead else '',
                    cci_before_signal=cci_before,
                    has_price_extreme=is_price_high,
                )
                
                if score >= 1.0:
                    sell_closings.append({
                        'type': 'sell_closing',
                        'level': period_labels[period],
                        'level_key': period,
                        'timestamp': str(row.get('timestamp', '')),
                        'price': float(row.get('raw_close', row.get('close', 0))),
                        'score': score,
                        'level_label': level,
                        'conditions': conditions,
                        'trend_before': daily_trend,
                        'cci_before_signal': cci_before,
                        'has_price_extreme': is_price_high,
                    })
    
    reverse_signals = detect_reverse_signals(periods_data, daily_trend)
    resonance = detect_resonance(buy_closings, sell_closings)
    
    return {
        'buy_closings': buy_closings,
        'sell_closings': sell_closings,
        'reverse_signals': reverse_signals,
        'resonance': resonance,
    }


def detect_reverse_signals(periods_data, daily_trend):
    """
    反向信号检测
    
    下跌趋势(C/D): 2次死叉 + 1次★卖 → 横盘 → 第3次及以上金叉 = 上涨转折点
    上涨趋势(A/B): 2次金叉 + 1次★买 → 横盘 → 第3次及以上死叉 = 下跌转折点
    """
    reverse_signals = []
    
    period_labels = {'min5': '5分钟', 'min15': '15分钟', 'min30': '30分钟'}
    
    for period_key in ['min5', 'min15', 'min30']:
        rows = periods_data.get(period_key, [])
        if not rows:
            continue
        
        recent = rows[-60:] if len(rows) > 60 else rows
        
        cross_sequence = []
        for r in recent:
            ema = r.get('expma_cross', '').strip()
            if ema in ('金叉', '死叉'):
                cross_sequence.append({
                    'type': ema,
                    'ts': str(r.get('timestamp', '')),
                    'close': float(r.get('raw_close', r.get('close', 0))),
                })
        
        signal_sequence = []
        for r in recent:
            buy = r.get('buy_signal', '').strip()
            sell = r.get('sell_signal', '').strip()
            if buy:
                signal_sequence.append({'type': '★买', 'ts': str(r.get('timestamp', ''))})
            if sell:
                signal_sequence.append({'type': '★卖', 'ts': str(r.get('timestamp', ''))})
        
        # 下跌趋势(C/D) → 检测上涨转折
        if daily_trend in ('C', 'D') and len(cross_sequence) >= 3:
            last_crosses = cross_sequence[-5:]
            death_count = sum(1 for c in last_crosses if c['type'] == '死叉')
            gold_count = sum(1 for c in last_crosses if c['type'] == '金叉')
            has_sell = any(s['type'] == '★卖' for s in signal_sequence[-10:])
            last_cross = last_crosses[-1] if last_crosses else None
            
            if death_count >= 2 and has_sell and last_cross and last_cross['type'] == '金叉' and gold_count >= 3:
                recent_closes = [float(r.get('raw_close', r.get('close', 0))) for r in recent[-20:]]
                if recent_closes:
                    high = max(recent_closes)
                    low = min(recent_closes)
                    price_range_pct = ((high - low) / low * 100) if low > 0 else 0
                    is_sideways = price_range_pct < 2.0
                    
                    reverse_signals.append({
                        'type': 'reversal_bull',
                        'level': period_labels[period_key],
                        'level_key': period_key,
                        'trend': daily_trend,
                        'trigger': f'{death_count}次死叉+1次★卖→横盘→第{gold_count}次金叉',
                        'timestamp': last_cross['ts'],
                        'price': last_cross['close'],
                        'conditions_met': {
                            'death_count': death_count,
                            'has_sell': has_sell,
                            'gold_count': gold_count,
                            'is_sideways': is_sideways,
                            'price_range_pct': round(price_range_pct, 2),
                        },
                    })
        
        # 上涨趋势(A/B) → 检测下跌转折
        if daily_trend in ('A', 'B') and len(cross_sequence) >= 3:
            last_crosses = cross_sequence[-5:]
            gold_count = sum(1 for c in last_crosses if c['type'] == '金叉')
            death_count = sum(1 for c in last_crosses if c['type'] == '死叉')
            has_buy = any(s['type'] == '★买' for s in signal_sequence[-10:])
            last_cross = last_crosses[-1] if last_crosses else None
            
            if gold_count >= 2 and has_buy and last_cross and last_cross['type'] == '死叉' and death_count >= 3:
                recent_closes = [float(r.get('raw_close', r.get('close', 0))) for r in recent[-20:]]
                if recent_closes:
                    high = max(recent_closes)
                    low = min(recent_closes)
                    price_range_pct = ((high - low) / low * 100) if low > 0 else 0
                    is_sideways = price_range_pct < 2.0
                    
                    reverse_signals.append({
                        'type': 'reversal_bear',
                        'level': period_labels[period_key],
                        'level_key': period_key,
                        'trend': daily_trend,
                        'trigger': f'{gold_count}次金叉+1次★买→横盘→第{death_count}次死叉',
                        'timestamp': last_cross['ts'],
                        'price': last_cross['close'],
                        'conditions_met': {
                            'gold_count': gold_count,
                            'has_buy': has_buy,
                            'death_count': death_count,
                            'is_sideways': is_sideways,
                            'price_range_pct': round(price_range_pct, 2),
                        },
                    })
    
    return reverse_signals


def detect_resonance(buy_closings, sell_closings):
    """多级共振检测：同一方向多个级别同时出现闭环"""
    resonance = {'buy': [], 'sell': []}
    
    for closings, target in [(buy_closings, resonance['buy']), (sell_closings, resonance['sell'])]:
        if len(closings) >= 2:
            levels_found = set()
            for c in closings:
                if c['level_key'] in levels_found:
                    continue
                # 检查是否有相近时间（同一天内）的其他级别
                ts = c['timestamp']
                date_part = ts[:8] if len(ts) >= 8 else ''
                for c2 in closings:
                    if c2['level_key'] != c['level_key']:
                        ts2 = c2['timestamp']
                        date_part2 = ts2[:8] if len(ts2) >= 8 else ''
                        if date_part == date_part2:
                            levels_found.add(c['level_key'])
                            levels_found.add(c2['level_key'])
            
            if len(levels_found) >= 2:
                target.append({
                    'levels': sorted(list(levels_found)),
                    'count': len(levels_found),
                    'summary': f"{len(levels_found)}级共振({' + '.join(sorted(levels_found))})",
                })
    
    return resonance


def save_closings_for_backtest(code, closings_data):
    """
    保存闭环数据到JSON文件（供回测使用）
    
    格式:
    {
      "code": "sz159740",
      "last_update": "202604291840",
      "buy_closings": [...],
      "sell_closings": [...],
      "reverse_signals": [...]
    }
    """
    base_dir = SNAPSHOT_DIR / code
    base_dir.mkdir(parents=True, exist_ok=True)
    filepath = base_dir / 'closes.json'
    
    existing = {'code': code, 'buy_closings': [], 'sell_closings': [], 'reverse_signals': []}
    if filepath.exists():
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except:
            pass
    
    # 去重
    existing_keys = set()
    for ct in ['buy_closings', 'sell_closings', 'reverse_signals']:
        for item in existing.get(ct, []):
            key = f"{item.get('type','')}_{item.get('level','')}_{item.get('timestamp','')}"
            existing_keys.add(key)
    
    for ct in ['buy_closings', 'sell_closings', 'reverse_signals']:
        new_items = closings_data.get(ct, [])
        existing_list = list(existing.get(ct, []))
        for item in new_items:
            key = f"{item.get('type','')}_{item.get('level','')}_{item.get('timestamp','')}"
            if key not in existing_keys:
                existing_list.append(item)
                existing_keys.add(key)
        existing[ct] = existing_list
    
    existing['last_update'] = datetime.now().strftime('%Y%m%d%H%M')
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    
    return filepath


# ============================================================
# 多级别嵌套分析系统（层级分析法）
# 核心逻辑: 日线→60分→30分→15分→5分，逐层嵌套
# 使用前必须先运行 detect_closings() 获取闭环数据
# ============================================================

def level_label(trend):
    """趋势等级转文本"""
    return TREND_LABELS.get(trend, trend)


def focus_direction(trend):
    """根据趋势等级返回主关注方向"""
    if trend in ('A',):
        return '卖闭环 + 回调买闭环'
    elif trend in ('B', 'C'):
        return '买卖都看，注意闭环次数'
    else:
        return '主看买闭环 + 反向观测卖信号'


def get_closing_counts_by_level(closings_list, level_key):
    """按级别统计闭环次数"""
    return [c for c in closings_list if c.get('level_key', '') == level_key]


def level_analysis(code, daily_trend, buy_closings, sell_closings):
    """
    多级别嵌套分析引擎
    
    返回: dict
      'level_order': list[str] 层级顺序
      'daily': dict 日线判定
      'levels': dict[str, dict] 每个级别的分析
      'synthesis': str 综合判断
      'closing_counts': dict 闭环计数(senario: 方案, count: 次数, weight: 权重)
      'next_signals': list[str] 等待信号列表
    """
    
    # 层级定义（从大到小）
    LEVEL_ORDER = ['daily', 'min60', 'min30', 'min15', 'min5']
    LEVEL_NAMES = {
        'daily': '日线（决定15-30天走势）',
        'min60': '60分钟（决定一周走势）',
        'min30': '30分钟（辅助验证60分钟方向）',
        'min15': '15分钟（决定1-3天走势）',
        'min5':  '5分钟（日内操作基础）',
    }
    LEVEL_SHORT = {'daily':'日线', 'min60':'60分钟', 'min30':'30分钟', 'min15':'15分钟', 'min5':'5分钟'}
    
    result = {
        'code': code,
        'level_order': LEVEL_ORDER,
        'daily': {},
        'levels': {},
        'synthesis': '',
        'closing_counts': [],
        'next_signals': [],
        'current_phase': '',
        'watch_items': [],
    }
    
    # ========== 第一层判定：日线 ==========
    result['daily'] = {
        'trend': daily_trend,
        'level': level_label(daily_trend),
        'focus': focus_direction(daily_trend),
        'judgement': f"{level_label(daily_trend)}，多空方向确定" if daily_trend in ('A','D') else f"{level_label(daily_trend)}，方向不明确",
    }
    
    # ========== 第二层及以下：各周期分析 ==========
    for lvl in ['min60', 'min30', 'min15', 'min5']:
        buy_c = get_closing_counts_by_level(buy_closings, lvl)
        sell_c = get_closing_counts_by_level(sell_closings, lvl)
        
        # 从最新到最早排序
        buy_c_sorted = sorted(buy_c, key=lambda x: x.get('timestamp', ''), reverse=True)
        sell_c_sorted = sorted(sell_c, key=lambda x: x.get('timestamp', ''), reverse=True)
        
        latest_buy = buy_c_sorted[0] if buy_c_sorted else None
        latest_sell = sell_c_sorted[0] if sell_c_sorted else None
        
        level_info = {
            'name': LEVEL_SHORT[lvl],
            'full_name': LEVEL_NAMES[lvl],
            'buy_closing_count': len(buy_c),
            'sell_closing_count': len(sell_c),
            'latest_buy': latest_buy,
            'latest_sell': latest_sell,
            'has_buy': len(buy_c) > 0,
            'has_sell': len(sell_c) > 0,
            'has_complete_buy': any(b.get('score', 0) >= 3 for b in buy_c),
            'has_complete_sell': any(s.get('score', 0) >= 3 for s in sell_c),
            'latest_buy_score': latest_buy['score'] if latest_buy else 0,
            'latest_sell_score': latest_sell['score'] if latest_sell else 0,
        }
        result['levels'][lvl] = level_info
    
    # ========== 综合判断 ==========
    
    # 方法一：统计60分钟做多闭环
    min60_closes = result['levels'].get('min60', {})
    
    # 构建闭环场景分析
    scenarios = []
    
    # 统计60分钟买入闭环分组（按时间分段）
    # 如果60分钟没有直接信号，用30分钟近似
    min60_buys = get_closing_counts_by_level(buy_closings, 'min60')
    min30_buys = get_closing_counts_by_level(buy_closings, 'min30')
    min60_sells = get_closing_counts_by_level(sell_closings, 'min60')
    
    # 场景A：60分钟有直接闭环信号
    if min60_buys or min60_sells:
        # 有60分钟直接数据
        buy_count_60 = len(min60_buys)
        sell_count_60 = len(min60_sells)
    else:
        # 用30分钟近似：~2个30分钟闭环≈1个60分钟闭环
        buy_count_60 = len(min30_buys) // 2
    
    # 当前是否在60分钟卖出状态
    in_sell_state = len(min60_sells) > 0 and (not min60_buys or max(s.get('timestamp','') for s in min60_sells) > max(b.get('timestamp','') for b in min60_buys))
    
    # 当前方向
    trend = daily_trend
    
    # 构建场景
    if trend in ('D', 'C'):
        # 做多场景
        if in_sell_state:
            # 当前在卖出状态/调整中，统计之前的买入闭环
            # 按时间排序
            all_buys_60 = sorted(min60_buys, key=lambda x: x.get('timestamp', ''))
            
            # 统计做多回合
            # 第一次闭环(最早的一组) = 观望
            # 第二次闭环 = 有效
            # 有新低时，从最近的低点重启计数
            
            if len(all_buys_60) <= 1:
                # 不足2次 → 视作【场景B ：4/09是第一次观望】
                scenarios.append({
                    'label': '做多信号积累不足',
                    'detail': '60分钟买入闭环≤1次，当前处于卖出调整状态，等待新的买入闭环出现。',
                    'focus': '等待60分钟★买+金叉=完整买入闭环',
                    'readiness': '未准备好',
                    'priority': '主场景',
                })
            elif len(all_buys_60) == 2:
                # 2次 → 看价格是否创新低
                scenarios.append({
                    'label': '双闭环等待确认',
                    'detail': f'60分钟已完成{len(all_buys_60)}次买入闭环，等待第{len(all_buys_60)+1}次或小级别共振确认。',
                    'focus': '关注30-15分钟是否有第二次★买+金叉共振',
                    'readiness': '接近',
                    'priority': '主场景',
                })
            else:
                scenarios.append({
                    'label': '多闭环积累充分',
                    'detail': f'60分钟已完成{len(all_buys_60)}次买入闭环，若30-15分钟也出现买入共振，可考虑试错做多。',
                    'focus': '共振确认后试错做多',
                    'readiness': '充分',
                    'priority': '主场景',
                })
        else:
            scenarios.append({
                'label': '买入状态中',
                'detail': '60分钟当前处于买入状态，观察能否升级为更大的反弹。',
                'focus': '观察反弹力度+60分钟是否出现★卖',
                'readiness': '进行中',
                'priority': '主场景',
            })
    
    elif trend in ('A', 'B'):
        # 做空/回调关注
        if not in_sell_state:
            scenarios.append({
                'label': '多头趋势中',
                'detail': '60分钟当前为买入状态或上涨中，关注卖出闭环信号。',
                'focus': '关注60分钟CCI+极限+顶背驰+★卖组合',
                'readiness': '进行中',
                'priority': '主场景',
            })
        else:
            scenarios.append({
                'label': '回调或调整',
                'detail': '60分钟出现卖出信号后回调中，等待回调后的买入信号。',
                'focus': '关注回调深度和小级别是否出现买入闭环',
                'readiness': '等待回调到位',
                'priority': '主场景',
            })
    
    result['scenarios'] = scenarios
    
    # ========== 小级别共振检测 ==========
    
    # 趋势向下 → 检查小级别买入信号
    small_level_buy_count = sum(1 for lvl in ['min30','min15','min5'] if result['levels'].get(lvl, {}).get('has_complete_buy'))
    
    # 检查最近有没有30分钟或15分钟的★买
    min30_has_latest_buy = result['levels'].get('min30', {}).get('has_buy', False)
    min15_has_latest_buy = result['levels'].get('min15', {}).get('has_buy', False)
    
    # ========== 等待信号清单 ==========
    next_signals = []
    
    if trend in ('D', 'C') and in_sell_state:
        # 等买入
        next_signals = [
            '60分钟★买出现 = 半个买入信号',
            '60分钟EXPMA金叉 = 半个信号（+★买=完整）',
            '30分钟第2次★买+金叉 = 共振确认',
            '15分钟第2次买闭环 = 级别放大确认',
            '5分钟再出1组买闭环 = 临门一脚',
        ]
    elif trend in ('D', 'C') and not in_sell_state:
        next_signals = [
            '小级别反弹能否升级：15分钟出第2次★买',
            '60分钟★卖出现 = 反弹结束',
        ]
    elif trend in ('A', 'B'):
        next_signals = [
            '60分钟★卖+顶背驰+死叉 = 卖出信号',
            '小级别死叉+★卖突破前低 = 回调确认',
        ]
    
    result['next_signals'] = next_signals
    
    # ========== 当前阶段 ==========
    if trend in ('D', 'C'):
        if in_sell_state:
            if small_level_buy_count >= 2:
                result['current_phase'] = '在小级别(15-30分钟)已有买入信号，等60分钟确认'
            elif small_level_buy_count >= 1:
                result['current_phase'] = '少量小级别买入信号出现，但强度不够'
            else:
                result['current_phase'] = '等待60分钟或30分钟出现买入信号'
        else:
            result['current_phase'] = '反弹进行中，关注反弹力度和级别'
    else:
        result['current_phase'] = f'{level_label(trend)}趋势中'
    
    # ========== 关注清单 ==========
    watch_items = []
    if trend in ('D', 'C') and in_sell_state:
        watch_items.append('下一次60分钟★买出现时关注')
        watch_items.append('30分钟是否出现第2次★买+金叉')
        watch_items.append('15分钟是否由底背驰变为★买')
        watch_items.append('价格是否再创新低（需要重启闭环计数）')
    
    result['watch_items'] = watch_items
    
    return result


def generate_level_report_text(analysis_result):
    """生成层级分析文本（供报告和console使用）"""
    lines = []
    code = analysis_result.get('code', '')
    
    lines.append(f'### {code} 多级别嵌套分析')
    lines.append('')
    
    # 第一层：日线
    dai = analysis_result.get('daily', {})
    lines.append(f'| 层级 | 判定 | 关注方向 |')
    lines.append(f'|------|------|---------|')
    lines.append(f'| ①日线 | {dai.get("level","")} | {dai.get("focus","")} |')
    
    # 各周期
    for lvl in ['min60', 'min30', 'min15', 'min5']:
        lv = analysis_result.get('levels', {}).get(lvl, {})
        if not lv or lv.get('name') is None:
            continue
        buy_c = lv.get('buy_closing_count', 0)
        sell_c = lv.get('sell_closing_count', 0)
        latest_buy = lv.get('latest_buy', {})
        latest_sell = lv.get('latest_sell', {})
        
        parts = []
        if buy_c > 0:
            latest_buy_ts = latest_buy.get('timestamp', '')[-4:] if latest_buy.get('timestamp') else ''
            parts.append(f'买入闭环×{buy_c}')
            if lv.get('has_complete_buy'):
                parts.append('(有完整)')
        if sell_c > 0:
            parts.append(f'卖出闭环×{sell_c}')
        
        status = ' | '.join(parts) if parts else '无闭环'
        lines.append(f'| ②{lv["name"]} | {status} |')
    
    lines.append('')
    
    # 场景
    for sc in analysis_result.get('scenarios', []):
        lines.append(f'- **{sc["label"]}**（{sc["priority"]}）: {sc["detail"]}')
    
    # 当前阶段
    phase = analysis_result.get('current_phase', '')
    if phase:
        lines.append('')
        lines.append(f'**当前阶段**: {phase}')
    
    # 等待信号
    sigs = analysis_result.get('next_signals', [])
    if sigs:
        lines.append('')
        lines.append('**等待信号（按优先级）**:')
        for i, s in enumerate(sigs, 1):
            lines.append(f'  {i}. {s}')
    
    # 关注
    watch = analysis_result.get('watch_items', [])
    if watch:
        lines.append('')
        lines.append('**关注**:')
        for w in watch:
            lines.append(f'  - {w}')
    
    lines.append('')
    return lines


# ============================================================
# 报告生成
# ============================================================

def get_status_narrative(r):
    """生成标的的一句话状态叙事（模块级，供报告和命令行共用）"""
    daily = r['daily']
    periods = r['periods']
    max_level = r['max_level']

    trend = daily.get('trend_strength', 'D') if daily else 'D'

    # 找最近的关键信号（按时间倒序）
    recent_events = []
    for p in ['min5', 'min15', 'min30']:
        ana = periods.get(p)
        if not ana or not ana['signals']:
            continue
        for s in ana['signals']:
            event = []
            if s['buy']: event.append(f"{p}★买")
            if s['sell']: event.append(f"{p}★卖")
            if s['ema']: event.append(s['ema'])
            if event:
                recent_events.append((s['ts'], ' | '.join(event)))

    recent_events.sort(key=lambda x: x[0], reverse=True)
    last_event = recent_events[0][1] if recent_events else '近期无关键信号'

    # 状态分类
    if max_level == 3:
        status = '🔴 可操作'
        action = '闭环信号出现，关注入场'
    elif max_level == 2:
        status = '🟡 接近闭环'
        action = '接近信号，继续观察'
    elif trend in ('A', 'B') and any('金叉' in p.get('opportunity', '') for p in periods.values()):
        status = '🟢 强势延续'
        action = '趋势健康，持仓或等回调'
    elif trend == 'C':
        status = '🔶 偏弱震荡'
        action = '偏弱整理，等更强信号'
    elif trend == 'D':
        if any('★卖' in p.get('opportunity', '') for p in periods.values()):
            status = '⚫ 调整中'
            action = '空头趋势，回避或等底部结构'
        else:
            status = '⚫ 弱势'
            action = '弱势整理，暂无机会'
    else:
        status = '⚪ 平淡'
        action = '暂无明确方向'

    return status, action, last_event, trend


def format_timeline(r, days=5):
    """生成最近N日的信号时间线（模块级）"""
    events = []
    for p in ['min5', 'min15', 'min30']:
        ana = r['periods'].get(p)
        if not ana or not ana['signals']:
            continue
        for s in ana['signals']:
            ts = s['ts']
            parts = []
            if s['buy']: parts.append('★买')
            if s['sell']: parts.append('★卖')
            if s['ema']: parts.append(s['ema'])
            if s['div']: parts.append(s['div'])
            if parts:
                events.append((ts, p, ' | '.join(parts)))
    events.sort(key=lambda x: x[0], reverse=True)
    return events[:15]


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
        rows_dict = {}

        for p in SCAN_PERIODS:
            all_rows = read_snapshots(code, p, 300)
            rows_dict[p] = all_rows
            # analyze_period 只需要后80行，复用已读数据
            ana = analyze_period(code, p, _rows=all_rows[-80:] if len(all_rows) > 80 else all_rows)
            if ana:
                periods[p] = ana

        # 找最高机会级别
        max_level = max((periods[p]['opp_level'] for p in periods), default=0)

        # 运行闭环检测引擎
        trend_strength = daily['trend_strength'] if daily else 'D'
        closing_data = detect_closings(code, rows_dict, trend_strength)
        save_closings_for_backtest(code, closing_data)

        results.append({
            'code': code,
            'name': name,
            'daily': daily,
            'periods': periods,
            'max_level': max_level,
            'closings': closing_data,
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

    # ========== 一、标的跟踪总览（状态叙事）==========
    lines.append('## 一、标的跟踪总览')
    lines.append('')
    lines.append('> 11只标的当前状态一句话总结，按趋势强度分组')
    lines.append('')

    # 按状态分组
    groups = {'🔴 可操作': [], '🟡 接近闭环': [], '🟢 强势延续': [],
              '🔶 偏弱震荡': [], '⚫ 调整中': [], '⚫ 弱势': [], '⚪ 平淡': []}

    for r in results:
        status, action, last_event, trend = get_status_narrative(r)
        groups.setdefault(status, []).append({
            'r': r, 'status': status, 'action': action,
            'last_event': last_event, 'trend': trend
        })

    # 输出分组
    for status_name in ['🔴 可操作', '🟡 接近闭环', '🟢 强势延续',
                        '🔶 偏弱震荡', '⚫ 调整中', '⚫ 弱势', '⚪ 平淡']:
        items = groups.get(status_name, [])
        if not items:
            continue
        lines.append(f"### {status_name}")
        lines.append('')
        lines.append('| 标的 | 日线趋势 | 最近关键信号 | 操作建议 |')
        lines.append('|------|----------|-------------|----------|')
        for item in items:
            r = item['r']
            trend_label = TREND_LABELS.get(item['trend'], '—')
            lines.append(f"| {r['code']} {r['name']} | {trend_label} | {item['last_event']} | {item['action']} |")
        lines.append('')

    # ========== 二、重点标的深度分析 ==========
    lines.append('## 二、重点标的深度分析')
    lines.append('')
    lines.append('> 最近出现信号或趋势变化的标的，按时间线展示信号演进')
    lines.append('')

    # 选择重点标的：有信号的、或趋势等级A/B的
    key_results = [r for r in results if r['max_level'] >= 1
                   or (r['daily'] and r['daily'].get('trend_strength') in ('A', 'B'))]
    # 再补充已卖出的
    sold_positions = load_sold_positions()
    for code in sold_positions:
        if code not in [r['code'] for r in key_results]:
            r = next((x for x in results if x['code'] == code), None)
            if r:
                key_results.append(r)

    for r in key_results:
        status, action, last_event, trend = get_status_narrative(r)
        trend_label = TREND_LABELS.get(trend, '—')

        lines.append(f"### {r['code']} {r['name']} — {status}")
        lines.append('')
        lines.append(f"- **日线趋势**: {trend_label} ({r['daily'].get('env_short', '—') if r['daily'] else '—'})")
        lines.append(f"- **当前建议**: {action}")

        # 各周期信号摘要
        for p in SCAN_PERIODS:
            ana = r['periods'].get(p)
            if ana:
                lines.append(f"- **{p}**: {ana['opportunity']}")
        lines.append('')

        # 最近信号时间线
        timeline = format_timeline(r)
        if timeline:
            lines.append('**最近信号时间线**:')
            lines.append('```')
            for ts, p, event in timeline:
                lines.append(f"  {fmt_ts(ts)} [{p}] {event}")
            lines.append('```')
            lines.append('')

    # ========== 三、机会排序 ==========
    lines.append('## 三、机会排序')
    lines.append('')
    lines.append('| 排序 | 标的 | 状态 | 机器信号 | 定性判断 |')
    lines.append('|------|------|------|---------|---------|')
    sorted_results = sorted(results, key=lambda r: (
        -r['max_level'],
        -(1 if r['code'] in QUALITATIVE_VIEWS else 0),
    ))
    for i, r in enumerate(sorted_results[:8], 1):
        status, action, _, _ = get_status_narrative(r)
        qv = QUALITATIVE_VIEWS.get(r['code'], {})
        qv_view = qv.get('view', '—')[:20] + '...' if len(qv.get('view', '')) > 20 else qv.get('view', '—')
        machine = '有闭环' if r['max_level'] == 3 else ('接近' if r['max_level'] == 2 else '观察')
        lines.append(f"| {i} | {r['code']} {r['name']} | {status} | {machine} | {qv_view} |")
    lines.append('')

    # ========== 四、已卖出标的跟踪 ==========
    if sold_positions:
        lines.append('## 四、已卖出标的跟踪')
        lines.append('')
        lines.append('| 标的 | 卖出日 | 趋势等级 | 当前5分钟 | 当前15分钟 | 提示/确定信号 | 需确认 |')
        lines.append('|------|--------|----------|-----------|------------|---------------|--------|')
        for code, info in sold_positions.items():
            r = next((x for x in results if x['code'] == code), None)
            if not r:
                continue
            trend = r['daily'].get('trend_strength', 'D') if r['daily'] else 'D'
            trend_label = TREND_LABELS.get(trend, 'D弱势')
            p5 = r['periods'].get('min5', {})
            p15 = r['periods'].get('min15', {})
            p5_sig = p5.get('opportunity', '—') if p5 else '—'
            p15_sig = p15.get('opportunity', '—') if p15 else '—'

            alert = ''
            if trend in ('A', 'B'):
                if '★买' in p5_sig and '金叉' in p5_sig:
                    alert = '⚠️ 提示: 5分钟★买+金叉'
                p5_sigs = p5.get('signals', []) if p5 else []
                gold_count = sum(1 for s in p5_sigs if '金叉' in s.get('ema', ''))
                if '★买' in p5_sig and gold_count >= 2:
                    alert = '🔥 确定: 5分钟★买+2次金叉'
            elif trend == 'C':
                if '★买' in p15_sig and '金叉' in p15_sig:
                    alert = '⚠️ 提示: 15分钟★买+金叉'
                p15_sigs = p15.get('signals', []) if p15 else []
                gold_count = sum(1 for s in p15_sigs if '金叉' in s.get('ema', ''))
                if '★买' in p15_sig and gold_count >= 2:
                    alert = '🔥 确定: 15分钟★买+2次金叉'

            sold_date = info.get('sold_date', '')
            lines.append(f"| {code} {r['name']} | {sold_date} | {trend_label} | {p5_sig[:20]}... | {p15_sig[:20]}... | {alert} | 消息面？ |")
        lines.append('')
        lines.append('> **趋势等级**: A=白线上方 / B=白线-黄线间 / C=黄线下方但MACD>0 / D=MACD<0')
        lines.append('> **提示信号**: 可轻仓试探 | **确定信号**: 满足门槛，结合消息面可入场')
        lines.append('')

    # ========== 五、验证追踪 ==========
    lines.append('## 五、验证追踪（后续填写）')
    lines.append('')
    lines.append('| 日期 | 标的 | 判断 | 实际走势 | 验证结果 |')
    lines.append('|------|------|------|---------|---------|')
    lines.append('| | | | | |')
    lines.append('')

    # ========== 多级别嵌套分析（新增）==========
    lines.append('## 多级别嵌套分析')
    lines.append('')
    lines.append('> 日线→60分→30分→15分→5分，先拆大周期定性，再找小级别机会/风险')
    lines.append('')

    for r in results:
        closings = r.get('closings', {})
        if not closings:
            continue

        trend = r['daily'].get('trend_strength', 'D') if r['daily'] else 'D'

        analysis = level_analysis(
            code=r['code'],
            daily_trend=trend,
            buy_closings=closings.get('buy_closings', []),
            sell_closings=closings.get('sell_closings', []),
        )
        lines.extend(generate_level_report_text(analysis))

    # ========== 闭环信号检测 ==========
    lines.append('## 闭环信号检测')
    lines.append('')
    lines.append('> 基于 CCI极值 + 背驰 + ★买/★卖 + EXPMA交叉 的闭环评分系统')
    lines.append('> 反向信号：趋势中的转折点识别')
    lines.append('')

    for r in results:
        closings = r.get('closings', {})
        if not closings:
            continue

        buy_c = closings.get('buy_closings', [])
        sell_c = closings.get('sell_closings', [])
        rev = closings.get('reverse_signals', [])
        res = closings.get('resonance', {})

        if not buy_c and not sell_c and not rev:
            continue

        lines.append(f"### {r['code']} {r['name']}")
        lines.append('')

        trend = r['daily'].get('trend_strength', 'D') if r['daily'] else 'D'
        trend_label = TREND_LABELS.get(trend, '—')
        focus = '主看卖闭环 + 回调买闭环' if trend in ('A', 'B') else '主看买闭环 + 反向观测卖信号'
        lines.append(f'- 趋势: {trend_label} | 关注方向: {focus}')

        if buy_c:
            lines.append('')
            lines.append('**买入闭环:**')
            lines.append('')
            lines.append('| 级别 | 时间 | 价格 | 评分 | 等级 | 条件 |')
            lines.append('|------|------|------|------|------|------|')
            for bc in buy_c[-10:]:
                lines.append(f"| {bc['level']} | {bc['timestamp']} | {bc['price']} | {bc['score']} | {bc['level_label']} | {', '.join(bc['conditions'])} |")

        if sell_c:
            lines.append('')
            lines.append('**卖出闭环:**')
            lines.append('')
            lines.append('| 级别 | 时间 | 价格 | 评分 | 等级 | 条件 |')
            lines.append('|------|------|------|------|------|------|')
            for sc in sell_c[-10:]:
                lines.append(f"| {sc['level']} | {sc['timestamp']} | {sc['price']} | {sc['score']} | {sc['level_label']} | {', '.join(sc['conditions'])} |")

        if rev:
            lines.append('')
            lines.append('**反向信号（转折点识别）:**')
            lines.append('')
            for rs in rev:
                emoji = '🟢 上涨转折' if rs['type'] == 'reversal_bull' else '🔴 下跌转折'
                cond = rs['conditions_met']
                lines.append(f"- {emoji} [{rs['level']}] {rs['trigger']} | 横盘确认: {'✅' if cond['is_sideways'] else '❌'} | 波动: {cond['price_range_pct']}%")

        if res.get('buy') or res.get('sell'):
            lines.append('')
            lines.append('**多级共振:**')
            for rb in res.get('buy', []):
                lines.append(f"- 🟢 买入共振: {rb['summary']}")
            for rs2 in res.get('sell', []):
                lines.append(f"- 🔴 卖出共振: {rs2['summary']}")

        lines.append('')

    # ========== 附录：详细信号日志（放最下面）==========
    lines.append('## 附录：详细信号日志')
    lines.append('')
    lines.append('> 原始数据，需要时查阅')
    lines.append('')
    for r in results:
        if r['max_level'] >= 1 or r['code'] in QUALITATIVE_VIEWS:
            lines.append(f"### {r['code']} {r['name']}")
            lines.append('')
            for p in SCAN_PERIODS:
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
    report_content = '\n'.join(lines)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report_content)

    print(f'[报告已生成] {report_path}')

    # 自动打开报告
    try:
        os.startfile(str(report_path))
        print(f'[报告已打开] {report_path}')
    except Exception as e:
        print(f'[报告打开失败] {e}')

    # 同时输出精简摘要到控制台（方便直接查看）
    _print_report_summary(lines, report_path)

    return report_path, results


def _print_report_summary(lines, report_path):
    """把报告关键内容输出到控制台，避免用户还要跑到文件夹查看"""
    print('\n' + '='*70)
    print('📋 智能分析报告摘要（完整版见上方文件路径）')
    print('='*70)

    # 提取关键板块输出
    in_section = False
    section_name = ''
    section_lines = []
    key_sections = {'一、市场状态', '二、机会排序', '三、风险警示',
                    '四、已卖出标的跟踪', '五、持仓提醒'}

    for line in lines:
        if line.startswith('## '):
            # 输出上一个板块
            if section_name and section_lines:
                print(f'\n{section_name}')
                for sl in section_lines[:8]:  # 每板块最多8行
                    print(f'  {sl}')
                if len(section_lines) > 8:
                    print(f'  ... ({len(section_lines)} 行，详见报告文件)')
                section_lines = []
            section_name = line.replace('## ', '').strip()
            in_section = section_name in key_sections or '机会' in section_name or '风险' in section_name
        elif in_section and line.strip():
            section_lines.append(line)

    # 输出最后一个板块
    if section_name and section_lines:
        print(f'\n{section_name}')
        for sl in section_lines[:8]:
            print(f'  {sl}')
        if len(section_lines) > 8:
            print(f'  ... ({len(section_lines)} 行，详见报告文件)')

    print('\n' + '='*70)
    print(f'📁 完整报告: {report_path}')
    print('='*70)


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
        for p in SCAN_PERIODS:
            ana = analyze_period(code, p)
            if ana:
                periods[p] = ana

        if not periods:
            continue

        daily_str = daily['env_short'] if daily else 'N/A'
        print(f"\n🔹 {code} {name}  [日线: {daily_str}]")

        for p in SCAN_PERIODS:
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


def print_single_code(code, periods=SCAN_PERIODS):
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

def backtest_closings(code='sz159740'):
    """
    回测闭环信号：比对买入闭环出发后到卖出闭环出现之间的利润空间。
    输入：closes.json（买入/卖出闭环列表）
    数据源：对应级别的信号 CSV
    """
    tracking = str(SNAPSHOT_DIR)
    closes_path = os.path.join(tracking, code, 'closes.json')

    if not os.path.exists(closes_path):
        print(f'[错误] 未找到 {closes_path}')
        return

    with open(closes_path, 'r', encoding='utf-8') as f:
        closes = json.load(f)

    buy_list = closes.get('buy_closings', [])
    sell_list = closes.get('sell_closings', [])

    if not buy_list:
        print('[错误] 没有买入闭环数据')
        return

    # 按时间排序
    buy_list_sorted = sorted(buy_list, key=lambda x: x['timestamp'])
    sell_list_sorted = sorted(sell_list, key=lambda x: x['timestamp'])

    print(f'\n{"="*60}')
    print(f'  回测报告: {code}')
    print(f'  买入闭环: {len(buy_list_sorted)} 个')
    print(f'  卖出闭环: {len(sell_list_sorted)} 个')
    print(f'{"="*60}')
    print()

    # 对每个买入闭环，找之后最近的卖出闭环
    total_pnl = []
    matched_count = 0

    for buy in buy_list_sorted:
        ts = buy['timestamp']
        level = buy.get('level_key', buy.get('level', 'unknown'))
        buy_price = buy['price'] / 10000.0  # 价格缩放
        buy_time_str = str(ts)

        # 找这个买入闭环之后最近的卖出闭环
        matched_sell = None
        for sell in sell_list_sorted:
            if sell['timestamp'] > ts:
                matched_sell = sell
                break

        if matched_sell:
            matched_count += 1
            sell_price = matched_sell['price'] / 10000.0
            sell_time_str = str(matched_sell['timestamp'])
            pnl = (sell_price - buy_price) / buy_price * 100
            total_pnl.append(pnl)

            # 尝试读信号CSV拿中间的最高/最低价
            level_csv_map = {'5分钟': 'min5', '15分钟': 'min15', '30分钟': 'min30', '60分钟': 'min60'}
            csv_level = level_csv_map.get(level, 'min5')
            csv_path = os.path.join(tracking, code, f'{csv_level}_signals.csv')

            high_in_range = None
            if os.path.exists(csv_path):
                with open(csv_path, 'r', encoding='utf-8') as csvf:
                    reader = csv.DictReader(csvf)
                    for row in reader:
                        row_ts = int(row.get('timestamp', 0))
                        if buy_time_str <= str(row_ts) <= sell_time_str:
                            rc = float(row.get('raw_close', 0)) / 10000.0
                            if high_in_range is None or rc > high_in_range:
                                high_in_range = rc

            max_pnl = ((high_in_range - buy_price) / buy_price * 100) if high_in_range else None

            status = '✅' if pnl > 0 else '❌'
            print(f'  {status} 买入@{buy_time_str} ({buy_price:.4f})  |  卖出@{sell_time_str} ({sell_price:.4f})')
            print(f'                  利润: {pnl:+.2f}%', end='')
            if max_pnl is not None:
                print(f'  |  区间最高利润: {max_pnl:+.2f}%', end='')
            print()
        else:
            print(f'  ⏳ 买入@{buy_time_str} ({buy_price:.4f})  |  未匹配到卖出闭环 (持有中)')

    print()
    print(f'匹配到卖出闭环: {matched_count}/{len(buy_list_sorted)}')
    if total_pnl:
        win = sum(1 for p in total_pnl if p > 0)
        win_rate = win / len(total_pnl) * 100
        avg_pnl = sum(total_pnl) / len(total_pnl)
        print(f'胜率: {win}/{len(total_pnl)} = {win_rate:.1f}%')
        print(f'平均利润: {avg_pnl:+.2f}%')
        print(f'最大利润: {max(total_pnl):+.2f}%')
        print(f'最大亏损: {min(total_pnl):+.2f}%')
    print()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='机会扫描 + AI智能分析')
    parser.add_argument('--report', action='store_true', help='生成 Markdown 报告 + CSV 日志')
    parser.add_argument('--ai', action='store_true', help='生成报告后调用多 API 智能分析')
    parser.add_argument('--code', type=str, help='指定标的代码 (如 sh513310)')
    parser.add_argument('--period', type=str, default='min30', help='指定周期 (默认 min30)')
    parser.add_argument('--date', type=str, help='指定日期 (YYYYMMDD格式，默认今天)')
    parser.add_argument('--backtest', action='store_true', help='回测闭环信号：比对买入到卖出的利润空间')
    args = parser.parse_args()

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

        if args.ai:
            print('\n[AI分析] 正在调用多 API 智能分析...')
            try:
                from ai_analyzer import analyze_report
                with open(report_path, 'r', encoding='utf-8') as f:
                    report_text = f.read()
                ai_result = analyze_report(report_text)
                if ai_result.get('error'):
                    err_msg = ai_result['error']
                    print(f'  [AI分析失败] {err_msg}')
                    with open(report_path, 'a', encoding='utf-8') as f:
                        f.write(f'\n\n---\n\n## AI 智能分析\n\n[所有 API 均失败] {err_msg}\n')
                else:
                    provider = ai_result.get('provider', 'unknown')
                    content = ai_result.get('content', '')
                    with open(report_path, 'a', encoding='utf-8') as f:
                        f.write(f'\n\n---\n\n## AI 智能分析（provider: {provider}）\n\n')
                        f.write(content)
                    print(f'[AI分析已追加] provider={provider} | {report_path}')
            except Exception as e:
                print(f'[AI分析失败] {e}')
                with open(report_path, 'a', encoding='utf-8') as f:
                    f.write(f'\n\n---\n\n## AI 智能分析\n\n[异常] {e}\n')

    if args.backtest:
        backtest_closings(args.code or 'sz159740')
    elif args.code:
        print_single_code(args.code, args.period)
    else:
        print_console_summary()

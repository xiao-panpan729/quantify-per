"""战役雷达 — 数据聚合层"""
import json
import os
import pandas as pd
from datetime import datetime, timedelta

SIGNALS_DIR = os.path.join(os.path.dirname(__file__), 'signals', 'tracking')
PROJECT_ROOT = os.path.dirname(__file__)


def _load_json(*parts):
    p = os.path.join(SIGNALS_DIR, *parts)
    if not os.path.exists(p):
        return {}
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_stock_name(code):
    from config import NAME_MAP
    return NAME_MAP.get(code, code)


def is_tracked(code):
    from config import NAME_MAP
    return code in NAME_MAP


def get_cycle_status(code):
    """从 cycle_report.json 获取当前状态"""
    data = _load_json('cycle_report.json')
    if isinstance(data, list):
        for item in data:
            if item.get('code') == code:
                return item
    return {}


def get_campaigns(code):
    """从 operation_records.json 获取战役列表"""
    data = _load_json('operation_records.json')
    return [c for c in data.get('campaigns', []) if c.get('code') == code]


def get_closes(code):
    """从 closes.json 获取★信号"""
    d = _load_json(code, 'closes.json')
    if not d:
        return [], []
    buys = d.get('buy_closings', [])
    sells = d.get('sell_closings', [])
    return buys, sells


def get_signal_events(code, max_days=120):
    """从 daily 信号 CSV + closes.json 读取★信号事件"""
    events = []

    # 1. daily CSV 中的 ★买/★卖
    csv_path = os.path.join(SIGNALS_DIR, code, 'daily_signals.csv')
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path, usecols=['date', 'buy_signal', 'sell_signal'])
        except (ValueError, KeyError):
            df = pd.read_csv(csv_path)
        df = df.tail(max_days)
        for _, row in df.iterrows():
            d = str(row.get('date', ''))
            if not d:
                continue
            if str(row.get('buy_signal', '')).strip() == '★买':
                events.append({'date': d, 'type': 'buy_signal'})
            if str(row.get('sell_signal', '')).strip() == '★卖':
                events.append({'date': d, 'type': 'sell_signal'})

    # 2. closes.json 中的闭环信号（含分钟级）
    closes = _load_json(code, 'closes.json')
    if closes:
        for c in closes.get('buy_closings', []):
            ts = str(c.get('timestamp', ''))[:8]
            if ts:
                events.append({'date': ts, 'type': 'buy_signal'})
        for c in closes.get('sell_closings', []):
            ts = str(c.get('timestamp', ''))[:8]
            if ts:
                events.append({'date': ts, 'type': 'sell_signal'})

    # 去重+排序
    seen = set()
    unique = []
    for e in sorted(events, key=lambda x: x['date']):
        key = (e['date'], e['type'])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def get_score_history(code, max_days=30):
    """从 score_history.json 取评分历史"""
    data = _load_json('score_history.json')
    history = data.get('history', [])
    scores = []
    for entry in history[-max_days:]:
        s = entry.get('scores', {}).get(code, {})
        if s:
            scores.append({
                'date': entry['date'],
                'score': s.get('score', 0),
                'direction': s.get('direction', ''),
            })
    return scores


def get_timeline_range(code, default_days=120):
    """确定时间轴范围（基于信号数据中的最早/最晚日期）"""
    events = get_signal_events(code, default_days)
    if not events:
        end = datetime.now()
        start = end - timedelta(days=default_days)
        return start, end

    dates = []
    for e in events:
        try:
            dates.append(datetime.strptime(e['date'], '%Y%m%d'))
        except ValueError:
            pass
    if not dates:
        end = datetime.now()
        start = end - timedelta(days=default_days)
        return start, end

    return min(dates), max(dates)


def build_radar_data(code):
    """聚合所有数据供雷达展示"""
    name = get_stock_name(code) if is_tracked(code) else '未跟踪'
    cycle = get_cycle_status(code)

    campaigns = get_campaigns(code)
    buys, sells = get_closes(code)
    signal_events = get_signal_events(code)
    scores = get_score_history(code)
    ts_start, ts_end = get_timeline_range(code)

    trend = cycle.get('trend', {}) if cycle else {}
    advice = cycle.get('advice', {}) if cycle else {}

    return {
        'code': code,
        'name': name,
        'tracked': is_tracked(code),
        'score': trend.get('score', 0),
        'direction': trend.get('direction', ''),
        'direction_label': trend.get('label', ''),
        'zone_label': trend.get('zone_label', ''),
        'advice_label': advice.get('grade_label', ''),
        'advice_action': advice.get('action', ''),
        'dominant_level': cycle.get('best_period', ''),
        'best_signal_level': cycle.get('best_signal_level', ''),
        'position_zone': cycle.get('position', {}).get('zone', '') if isinstance(cycle.get('position'), dict) else '',
        'campaigns': campaigns,
        'signal_events': signal_events,
        'scores': scores,
        'timeline_start': ts_start,
        'timeline_end': ts_end,
        'ts_days': (ts_end - ts_start).days or 1,
    }

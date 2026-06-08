"""成交量强者模块 — 共用常量、universe 读写、路径映射"""
import json
import os
import csv
from pathlib import Path

# ─── pytdx 服务器（已验证可用） ───
PYTDX_HOST = '180.153.18.170'
PYTDX_PORT = 7709

# ─── 路径常量 ───
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKING_DIR = REPO_ROOT / 'signals' / 'tracking'
UNIVERSE_PATH = TRACKING_DIR / '_funds' / 'volume_leader_universe.json'
TRADES_LOG_PATH = TRACKING_DIR / 'realtime_trades.jsonl'
STOCK_NAMES_PATH = TRACKING_DIR / '_funds' / 'stock_names.csv'
TDX_VIPDOC = Path('C:/zd_cjzq/vipdoc')

# ─── 信号参数 ───
LOOKBACK_BARS = 500
from config import DAY_PRICE_FACTOR, MIN_PRICE_FACTOR

# ─── 等级标签 ───
BUY_LABELS = {
    (8.0, float('inf')): ('强势出击', '多周期共振+地量确认，确定性最高'),
    (6.0, 8.0):         ('出击买入', '金叉确认+多维度验证'),
    (4.0, 6.0):         ('买入做T', '金叉确立，基础强度合格'),
    (2.0, 4.0):         ('试错信号', '金叉出现但验证不足，小仓位试探'),
    (1.0, 2.0):         ('信号弱', '★买出现未闭环'),
    (0.0, 1.0):         ('无信号', ''),
}

SELL_LABELS = {
    (8.0, float('inf')): ('离场观望', '多周期共振+放量确认，确定性最高'),
    (6.0, 8.0):         ('准备离场', '死叉确认+多维度验证'),
    (4.0, 6.0):         ('调整信号', '死叉确立，减仓做T'),
    (2.0, 4.0):         ('短期回踩', '死叉出现但验证不足，观察'),
    (1.0, 2.0):         ('多头趋势', '★卖出现但未闭环，上涨中的杂音'),
    (0.0, 1.0):         ('持有看涨', '无卖出信号，维持看涨'),
}

ALERT_THRESHOLD = 4.0


def get_label(level, direction='buy'):
    """根据评分和方向返回 (等级名, 含义)"""
    labels = BUY_LABELS if direction == 'buy' else SELL_LABELS
    for (lo, hi), (name, desc) in labels.items():
        if lo <= level < hi:
            return name, desc
    return '无信号', ''


def load_universe():
    """读取 volume_leader_universe.json，返回 [{code, name, tier}, ...] 按 T1>T2>T3 排序"""
    if not UNIVERSE_PATH.exists():
        print(f'[universe] 文件不存在: {UNIVERSE_PATH}')
        return []
    with open(UNIVERSE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    codes = data.get('universe', [])
    ranks = data.get('added_on', {})
    names = load_names_dict()
    # 按 tier 排序: T1(1-10) → T2(11-30) → T3(31+), 同 tier 按收录时间
    result = []
    for i, code in enumerate(codes):
        tier = 1 if i < 10 else (2 if i < 30 else 3)
        result.append({
            'code': code,
            'name': names.get(code, code),
            'tier': tier,
            'rank': i + 1,
        })
    return result


def load_names_dict():
    """从 stock_names.csv 读全量名称缓存 → {code: name}"""
    names = {}
    if STOCK_NAMES_PATH.exists():
        with open(STOCK_NAMES_PATH, 'r', encoding='utf-8-sig') as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0] != 'code':
                    names[row[0]] = row[1]
    return names


def append_trade(record):
    """追加一行交易记录到 JSONL"""
    TRADES_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADES_LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def code_to_market(code):
    """sz159740 → (0, '159740', 'sz'), sh600176 → (1, '600176', 'sh')"""
    market = 0 if code.startswith('sz') else 1
    return market, code[2:], code[:2]

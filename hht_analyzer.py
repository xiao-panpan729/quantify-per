# -*- coding: utf-8 -*-
"""
HHT 独立分析器 (Phase 1+2: EMD 分解 + 瞬时频率稳定性检测)

用法:
    python tools/hht_analyzer.py                    # 全量14只标的
    python tools/hht_analyzer.py --code sz159740    # 单标的
    python tools/hht_analyzer.py --period daily     # 只算日线
    python tools/hht_analyzer.py --code sz159740 --period min60

消耗:
    14只 × 6周期 ≈ 60-90秒 (单次)，运算密集，不嵌入日常流水线

输入: signals/tracking/{code}/{period}_signals.csv 的 trend_line
输出: signals/tracking/hht_report.json

v1.0 — Phase 1: EMD 分解 + Phase 2: 瞬时频率稳定性 + 能量跳跃检测
"""

import csv
import json
import sys
import os
import math
from pathlib import Path

import numpy as np
from PyEMD import EMD
from scipy.signal import hilbert

# ════════════════ 配置 ════════════════

BASE = Path('D:/quantify-per')
SNAPSHOT_DIR = BASE / 'signals' / 'tracking'
OUTPUT_PATH = SNAPSHOT_DIR / 'hht_report.json'

PERIODS = ['min1', 'min5', 'min15', 'min30', 'min60', 'daily']
PERIOD_LABELS = {
    'min1': '1分钟', 'min5': '5分钟', 'min15': '15分钟', 'min30': '30分钟',
    'min60': '60分钟', 'daily': '日线',
}
# EMD 最少需要的数据点数。趋势线数据少于此值就跳过
MIN_POINTS = 120

# 稳定性检测窗口
RECENT_WINDOW = 30   # 最近 N 根 K 线算"当前状态"
HIST_WINDOW = 200    # 历史窗口

# ════════════════ 数据读取 ════════════════

def read_code_periods():
    """扫描 tracking 目录，返回所有标的的可用周期列表"""
    codes = {}
    for d in sorted(SNAPSHOT_DIR.iterdir()):
        if not d.is_dir():
            continue
        code = d.name
        available = []
        for period in PERIODS:
            fp = d / f'{period}_signals.csv'
            if fp.exists():
                available.append(period)
        if available:
            codes[code] = available
    return codes


def get_name_map():
    try:
        sys.path.insert(0, str(BASE))
        from config import NAME_MAP
        return NAME_MAP
    except:
        return {}


def read_trendline(code, period):
    """读取某标的某周期的趋势线序列"""
    fpath = SNAPSHOT_DIR / code / f'{period}_signals.csv'
    if not fpath.exists():
        return []
    with open(fpath, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    vals = []
    for r in rows:
        tv = r.get('trend_line', '')
        if tv != '':
            try:
                v = float(tv)
                if v > 0:
                    vals.append(v)
            except ValueError:
                pass
    return vals


def read_close_price(code, period):
    """读取某标的某周期的 close 价格序列（用于假突破检测）"""
    fpath = SNAPSHOT_DIR / code / f'{period}_signals.csv'
    if not fpath.exists():
        return []
    with open(fpath, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    vals = []
    for r in rows:
        cv = r.get('close', '')
        if cv != '':
            try:
                v = float(cv)
                if v > 0:
                    vals.append(v)
            except ValueError:
                pass
    return vals


def check_breakout_validity(close_vals, recent_window=30, hist_window=100):
    """检测突破是否有效（假突破检测）

    对比最近 recent_window 根 K 线和前 hist_window 根 K 线的价格区间。
    如果最近价格未创历史区间新高/新低 → 标记为假突破。

    返回: (is_false: bool|None, reason: str)
    """
    if len(close_vals) < recent_window + hist_window:
        return None, '数据不足'
    recent = close_vals[-recent_window:]
    hist = close_vals[-(recent_window + hist_window):-recent_window]
    recent_high = max(recent)
    recent_low = min(recent)
    hist_high = max(hist)
    hist_low = min(hist)

    is_up_breakout = recent_high > hist_high
    is_down_breakout = recent_low < hist_low

    if is_up_breakout:
        return False, '向上有效突破（创%d期新高）' % (recent_window + hist_window)
    elif is_down_breakout:
        return False, '向下有效突破（创%d期新低）' % (recent_window + hist_window)
    else:
        return True, '价格未脱离历史区间，疑似假突破'


# ════════════════ HHT 核心 ════════════════

def emd_decompose(values, max_imf=6):
    """
    经验模态分解
    返回: list[np.ndarray], 每个 IMF 是一个等长数组
         最后一个元素是 residue（趋势项）
    """
    if len(values) < MIN_POINTS:
        return []
    signal = np.array(values, dtype=np.float64)
    emd = EMD()
    imfs = emd.emd(signal, max_imf=max_imf)
    if imfs is None or len(imfs) == 0:
        return []
    # 转为 list of ndarray
    return [imfs[i] for i in range(imfs.shape[0])]


def hilbert_spectrum(imf):
    """
    对单个 IMF 做希尔伯特变换
    imf: np.ndarray (1D)
    返回: (inst_freq, inst_amp)
    """
    analytic = hilbert(imf)
    amp = np.abs(analytic)
    phase = np.unwrap(np.angle(analytic))
    n = len(imf)
    freq = np.zeros(n)
    for i in range(1, n):
        freq[i] = float(phase[i] - phase[i - 1]) / (2.0 * np.pi)
    freq[0] = freq[1]
    return freq.tolist(), amp.tolist()


def imf_classify(imf_idx, n_total, freq_mean, amp_mean):
    """
    给 IMF 打标签
    启发式规则:
      - imf_idx=0 (最高频) → 高频噪声 (或1分钟级波动)
      - imf_idx=n_total-2 附近+频率<0.01 → 主导循环 (不是残差项)
      - imf_idx=n_total-1 → 残差趋势项 → 低频趋势
      - 中间 → 次级别震荡
    """
    if imf_idx == 0:
        return '高频噪声'
    elif imf_idx == n_total - 1:
        return '低频趋势'
    elif freq_mean < 0.005:
        return '超低频背景'
    elif freq_mean < 0.02:
        return '主导循环'
    elif freq_mean < 0.05:
        return '次级别震荡'
    else:
        return '高频波动'


def stability_score(freq_recent, freq_hist):
    """
    频率稳定性: 当前频率标准差 / 历史频率标准差
    < 0.7 = 循环正在锁定（蓄力）
    > 1.5 = 循环被打破（非预期解）
    0.7~1.3 = 正常波动
    """
    if not freq_hist or len(freq_hist) < 10:
        return 1.0
    recent_std = float(np.std(freq_recent)) if len(freq_recent) > 2 else 0.0
    hist_std = float(np.std(freq_hist))
    if hist_std < 1e-10:
        return 1.0
    return float(recent_std / hist_std)


def energy_surge_ratio(amp_current, amp_hist):
    """能量跳跃: 当前振幅 / 历史平均振幅"""
    if not amp_hist or len(amp_hist) < 10:
        return 1.0
    hist_mean = float(np.mean(amp_hist))
    current_mean = float(np.mean(amp_current))
    if hist_mean < 1e-10:
        return 1.0
    return float(current_mean / hist_mean)


def regime_label(freq_stability, energy_ratio, dir_up=None):
    """
    循环状态标签
    dir_up: True=向上破位, False=向下破位, None=无方向
    """
    dir_word = ''
    if dir_up == True:
        dir_word = '↑'
    elif dir_up == False:
        dir_word = '↓'

    if energy_ratio > 2.0 and freq_stability < 0.7:
        return f'突破{dir_word}(能量暴增+循环锁定)'
    elif energy_ratio > 2.0:
        return f'突破{dir_word}(能量暴增)'
    elif freq_stability > 1.8:
        return f'{dir_word}循环破位'
    elif freq_stability < 0.6:
        return '循环压缩(蓄力)'
    elif freq_stability > 1.5:
        return f'频率散乱{dir_word}(方向切换)'
    elif energy_ratio > 1.5:
        return f'动能增强{dir_word}'
    elif energy_ratio < 0.5:
        return '动能枯竭'
    else:
        return '循环正常'


# ════════════════ 单标的单周期分析 ════════════════

def analyze_one(code, period):
    """
    对一个标的的一个周期做 HHT 分析
    返回: dict (含 imfs 列表 + 摘要)
    """
    trend_vals = read_trendline(code, period)
    close_vals = read_close_price(code, period)   # 用于假突破检测
    n_total = len(trend_vals)

    if n_total < MIN_POINTS:
        return {'period': period, 'label': PERIOD_LABELS.get(period, period),
                'n_points': n_total, 'error': f'数据不足(需>{MIN_POINTS})', 'imfs': [], 'summary': {}}

    imfs = emd_decompose(trend_vals)
    if not imfs:
        return {'period': period, 'label': PERIOD_LABELS.get(period, period),
                'n_points': n_total, 'error': 'EMD分解失败', 'imfs': [], 'summary': {}}

    n_imfs = len(imfs)
    imf_results = []

    # 趋势线方向：近期均值 vs 历史均值
    # ↑ 趋势线上升=走向卖压区，↓ 趋势线下降=走向买压区
    trend_recent = np.mean(trend_vals[-RECENT_WINDOW:])
    trend_hist = np.mean(trend_vals[max(0, n_total-RECENT_WINDOW-HIST_WINDOW):
                                    n_total-RECENT_WINDOW])
    trend_dir_up = trend_recent > trend_hist

    # 分界点: 最近 RECENT_WINDOW 根 K 线 vs 前 HIST_WINDOW 根
    boundary = n_total - RECENT_WINDOW
    hist_start = max(0, n_total - RECENT_WINDOW - HIST_WINDOW)

    for i, imf in enumerate(imfs):
        freq, amp = hilbert_spectrum(imf)

        freq_recent = freq[boundary:]
        freq_hist = freq[hist_start:boundary]
        amp_recent = amp[boundary:]
        amp_hist = amp[hist_start:boundary]

        freq_mean_all = float(np.mean(freq)) if freq else 0
        amp_mean_all = float(np.mean(amp)) if amp else 0

        fs = stability_score(freq_recent, freq_hist)
        es = energy_surge_ratio(amp_recent, amp_hist)
        category = imf_classify(i, n_imfs, freq_mean_all, amp_mean_all)
        regime = regime_label(fs, es, dir_up=trend_dir_up)

        imf_results.append({
            'idx': i,
            'category': category,
            'freq_mean': round(freq_mean_all, 6),
            'amp_mean': round(amp_mean_all, 4),
            'freq_stability': round(fs, 4),       # <0.7=锁, >1.5=破
            'energy_ratio': round(es, 3),          # >2=暴增, <0.5=枯竭
            'regime': regime,
        })

    # ── 摘要 ──
    # 找主导循环 IMF
    dominant_imf = None
    for imri in imf_results:
        if imri['category'] == '主导循环':
            dominant_imf = imri
            break
    if dominant_imf is None and len(imf_results) >= 2:
        # 退而求其次：取倒数第二个（非残差）中能量最大的
        mid_imfs = [imri for imri in imf_results if imri['category'] not in ('低频趋势', '超低频背景')]
        if mid_imfs:
            dominant_imf = max(mid_imfs, key=lambda x: x['amp_mean'])

    # 检查所有 IMF 是否有"突破"或"循环破位"信号
    has_breakout = any(imri['regime'].startswith('突破') or '循环破位' in imri['regime']
                       for imri in imf_results)
    has_compression = any(imri['regime'] == '循环压缩(蓄力)' for imri in imf_results)

    # 假突破检测
    false_breakout, fb_reason = check_breakout_validity(close_vals) if len(close_vals) > 0 else (None, '无价格数据')

    summary = {
        'dominant_imf_idx': dominant_imf['idx'] if dominant_imf else -1,
        'dominant_category': dominant_imf['category'] if dominant_imf else '未知',
        'freq_stability': dominant_imf['freq_stability'] if dominant_imf else 1.0,
        'energy_ratio': dominant_imf['energy_ratio'] if dominant_imf else 1.0,
        'stability_label': dominant_imf['regime'] if dominant_imf else '未知',
        'trend_dir': '↑' if trend_dir_up else '↓',
        'trend_dir_word': '向上' if trend_dir_up else '向下',
        'breakout_signal': has_breakout,
        'compression_signal': has_compression,
        'false_breakout': false_breakout,
        'false_breakout_reason': fb_reason,
    }

    return {
        'period': period,
        'label': PERIOD_LABELS.get(period, period),
        'n_points': n_total,
        'n_imfs': n_imfs,
        'imfs': imf_results,
        'summary': summary,
    }


def analyze_code(code, name, target_periods=None):
    """
    对单只标的所有周期做 HHT 分析
    返回: dict
    """
    periods = target_periods or PERIODS
    available = []
    for d in sorted(SNAPSHOT_DIR.iterdir()):
        if not d.is_dir(): continue
        # 支持带前缀和不带前缀的代码
        raw = d.name.replace('sh', '').replace('sz', '')
        if raw == code.replace('sh', '').replace('sz', ''):
            for period in PERIODS:
                if (d / f'{period}_signals.csv').exists():
                    available.append(period)
            code = d.name
            break
    if not available:
        return {'code': code, 'name': name, 'error': '无CSV数据', 'periods': {}}

    period_results = {}
    for period in periods:
        if period not in available:
            continue
        result = analyze_one(code, period)
        if result:
            period_results[period] = result

    return {
        'code': code,
        'name': name,
        'periods': period_results,
    }


# ════════════════ 格式化输出 ════════════════

def fmt_period_summary(code, name, period_data):
    """格式化单周期的 HHT 状态，一行字符串"""
    s = period_data.get('summary', {})
    if not s:
        return '无数据'

    dl = s.get('stability_label', '未知')
    fs = s.get('freq_stability', 1.0)
    er = s.get('energy_ratio', 1.0)
    td = s.get('trend_dir', '')

    # 简化标签（带方向）
    if fs < 0.6:
        tag = f'🔒锁定{td}'
    elif fs > 1.5:
        tag = f'⚠破位{td}'
    elif er > 2.0:
        tag = f'🚀暴增{td}'
    elif er > 1.5:
        tag = f'📈动能{td}'
    elif fs < 0.8:
        tag = f'➖压缩{td}'
    else:
        tag = f'✅正常{td}'

    return f'{tag} {dl} (fs={fs:.2f}, er={er:.1f})'


# ════════════════ 主入口 ════════════════

def main():
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    args = sys.argv[1:]
    target_code = None
    target_period = None

    i = 0
    while i < len(args):
        if args[i] == '--code' and i + 1 < len(args):
            target_code = args[i + 1]; i += 2
        elif args[i] == '--period' and i + 1 < len(args):
            target_period = args[i + 1]; i += 2
        else:
            i += 1

    periods = [target_period] if target_period else PERIODS
    name_map = get_name_map()

    if target_code:
        # 单标的模式
        code = target_code
        name = name_map.get(code, '')
        print(f'[HHT] 分析 {code} {name} ...')
        item = analyze_code(code, name, periods)
        results = [item]
    else:
        # 全量模式
        print(f'[HHT] 全量分析 {len(name_map)} 只标的 ...')
        results = []
        for code, name in name_map.items():
            print(f'  {code} {name} ...', end=' ')
            item = analyze_code(code, name, periods)
            results.append(item)
            n_periods = len(item.get('periods', {}))
            print(f'{n_periods}个周期完成')

    # ── 保存 JSON ──
    clean = []
    for r in results:
        clean.append({
            'code': r['code'],
            'name': r['name'],
            'error': r.get('error'),
            'periods': r.get('periods', {}),
        })

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)

    print(f'\n[HHT] 保存 -> {OUTPUT_PATH}')
    print(f'[HHT] 共 {len(clean)} 只标的')

    # ── 终端预览 ──
    print(f'\n{"="*60}')
    print('HHT 概览 (主导循环IMF的稳定性和能量)')
    print(f'{"="*60}')
    for item in clean:
        code = item['code']
        name = item['name']
        periods = item.get('periods', {})
        interesting = []
        for pname in ['min5', 'min15', 'min30', 'min60', 'daily']:
            pd = periods.get(pname, {})
            s = pd.get('summary', {})
            if not s:
                continue
            fs = s.get('freq_stability', 1.0)
            er = s.get('energy_ratio', 1.0)
            # 只列出异常的周期
            if fs < 0.7 or fs > 1.5 or er > 1.5:
                interesting.append(f'  {PERIOD_LABELS.get(pname,pname)}: {fmt_period_summary(code,name,pd)}')
        if interesting:
            print(f'{code} {name}:')
            print('\n'.join(interesting))
        elif target_code:
            # 单标的模式下显示全部周期
            for pname in ['min5','min15','min30','min60','daily']:
                pd = periods.get(pname, {})
                if pd.get('summary'):
                    print(f'  {PERIOD_LABELS.get(pname,pname)}: {fmt_period_summary(code,name,pd)}')


if __name__ == '__main__':
    main()

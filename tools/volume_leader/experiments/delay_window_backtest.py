"""基于 backtest.py 最新逻辑的延迟窗口实验 — 只改delay_window，其余全部锁定"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from tools.volume_leader.backtest import (
    _load_csv, _load_daily_zones, _has_star_buy, _no_recent_death,
    _get_min60_above, _is_sell_reduce, _entry_price, _exit_price, _calc_band_low,
    SKIP_BARS,
)
from tools.volume_leader.shared import load_universe, MIN_PRICE_FACTOR
from datetime import datetime

universe = load_universe()
codes_43 = [s['code'] for s in universe if s.get('code')]

# ── 延迟入场版回测 ──
def backtest_with_delay(code, name, delay_window=0, months=6):
    """★买出现后，[0..delay_window]根内 MA5>MA10>MA20 首次成立即入场

    delay_window=0: 同框（原始逻辑）
    其余: entry_price, band_low, exit 全部沿用 backtest.py 原始逻辑
    """
    rows = _load_csv(code, 'min5')
    if not rows or len(rows) < SKIP_BARS:
        return []

    daily_zones = _load_daily_zones(code)
    cutoff = datetime.now() - __import__('datetime').timedelta(days=months * 30)

    trades = []
    in_trade = False
    entry_idx = None
    entry_price_val = None
    band_low = None
    star_idx = None  # 记录★买发生的bar

    for i in range(SKIP_BARS, len(rows)):
        r = rows[i]
        bar_date = r.get('date', '').strip()

        try:
            d = datetime.strptime(bar_date, '%Y%m%d')
            if d < cutoff:
                continue
        except:
            pass

        zone = daily_zones.get(bar_date, 'weak')
        bar_low_price = float(r['low'])
        bar_close = float(r['close'])

        if not in_trade:
            # ── 检测★买 ──
            if _has_star_buy(r):
                # 在[当前, 当前+delay_window]内找第一个MA5>MA10>MA20
                entry_found = False
                best_j = i
                for j in range(i, min(i + delay_window + 1, len(rows))):
                    rj = rows[j]
                    try:
                        ma5 = float(rj.get('ma5', 0) or 0)
                        ma10 = float(rj.get('ma10', 0) or 0)
                        ma20 = float(rj.get('ma20', 0) or 0)
                    except:
                        continue
                    if ma5 > ma10 > ma20:
                        # 检查safe条件（在入场bar上）
                        bar_date_j = rj.get('date', '').strip()
                        zone_j = daily_zones.get(bar_date_j, 'weak')
                        if zone_j not in ('strong', 'secondary'):
                            continue
                        if not _no_recent_death(rows, j, 20):
                            continue
                        if not _get_min60_above(code, bar_date_j):
                            continue
                        best_j = j
                        entry_found = True
                        break

                if entry_found:
                    in_trade = True
                    entry_idx = best_j
                    entry_price_val = _entry_price(rows[entry_idx])
                    band_low = _calc_band_low(rows, entry_idx)
                    star_idx = i
            # 注意: 非★买的金叉等信号在本实验中忽略
        else:
            # ── 持仓中，出场: 止损 或 减仓卖(★卖+close<MA5+safe / 死叉) ──
            exit_reason = None
            exit_price_val = None

            if bar_low_price < band_low:
                exit_reason = '止损'
                exit_price_val = band_low
            elif _is_sell_reduce(rows, i, code):
                exit_reason = '减仓卖'
                exit_price_val = _exit_price(r)

            if exit_reason:
                exit_actual = exit_price_val or bar_close
                ret_pct = (exit_actual - entry_price_val) / entry_price_val * 100

                # MFE/MAE
                mfe = 0.0
                mae = 0.0
                for j in range(entry_idx + 1, i + 1):
                    h = float(rows[j]['high'])
                    l = float(rows[j]['low'])
                    mfe = max(mfe, (h - entry_price_val) / entry_price_val * 100)
                    mae = min(mae, (l - entry_price_val) / entry_price_val * 100)

                hold_bars = i - entry_idx
                trades.append({
                    'code': code, 'name': name,
                    'entry_date': rows[entry_idx].get('date', ''),
                    'exit_date': bar_date,
                    'entry_price': entry_price_val,
                    'exit_price': exit_actual,
                    'ret_pct': ret_pct,
                    'mfe': mfe, 'mae': mae,
                    'hold_bars': hold_bars,
                    'exit_reason': exit_reason,
                    'delay': entry_idx - star_idx if star_idx is not None else 0,
                    'zone': zone,
                })
                in_trade = False
                entry_idx = None
                entry_price_val = None
                band_low = None
                star_idx = None

    return trades


# ── 运行 ──
print('=== 基于 backtest.py 原始逻辑, 延迟窗口实验（43只, 6个月）===')
print()
print(f'{"窗口":>5} {"笔数":>6} {"胜率":>7} {"均收益":>9} {"均MFE":>9} {"止损率":>7}')
print(f'{"":>5} {"":>6} {"":>7} {"(return)":>9} {"(max)":>9} {"":>7}')
print('-' * 55)

for window in [0, 1, 2, 5]:
    all_trades = []
    for stock in universe:
        code = stock['code']
        name = stock.get('name', '')
        if code not in codes_43:
            continue
        try:
            trades = backtest_with_delay(code, name, delay_window=window, months=6)
            all_trades.extend(trades)
        except Exception as e:
            pass

    if not all_trades:
        print(f'{window:>5} 无数据')
        continue

    wins = [t for t in all_trades if t['ret_pct'] > 0]
    stops = [t for t in all_trades if t['exit_reason'] == '止损']
    avg_ret = sum(t['ret_pct'] for t in all_trades) / len(all_trades)
    avg_mfe = sum(t['mfe'] for t in all_trades) / len(all_trades)
    avg_delay = sum(t['delay'] for t in all_trades) / len(all_trades)

    print(f'{window:>5} {len(all_trades):>6} {100*len(wins)/len(all_trades):>6.1f}% '
          f'{avg_ret:>8.2f}% {avg_mfe:>8.2f}% {100*len(stops)/len(all_trades):>6.1f}% '
          f'(均延迟{avg_delay:.1f}根)')

print()
print('注: 出场=止损(波段低点) 或 减仓卖(★卖+close<MA5+safe/死叉), 入场价=(low+close)/2, 出场价=close')
print('    safe条件=无死叉(20根回溯)+60分黄线上方+日线强势/二级')

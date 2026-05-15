# -*- coding: utf-8 -*-
"""
信号跟踪系统 - 一键更新脚本
增量计算跟踪标的的 EXPMA/MACD/分时出击信号
输出 CSV（全量追加）+ latest.json（最新快照）
+ pytdx 抽验 + SQLite 持久化快照

用法: python update_tracking.py          # 更新所有标的
      python update_tracking.py sz159740  # 只更新指定标的
      python update_tracking.py --verify  # 更新后强制执行 pytdx 抽验
"""

import sys
import os
import json
import time

# 确保能导入 signal_engine 和 tools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
import signal_engine as se
from config import NAME_MAP


# ========== 配置 ==========

def _get_market(code):
    """从代码自动推断市场（输入格式: sh000001 或 520600）"""
    if code.startswith('sh'):
        return 'sh'
    if code.startswith('sz'):
        return 'sz'
    return 'sh' if code[0] in ('6', '5') else 'sz'

TRACKING_STOCKS = [(code, _get_market(code)) for code in NAME_MAP.keys()]

PERIODS = ['daily', 'min1', 'min5', 'min15', 'min30', 'min60']

VERIFY_PERIOD_MAP = {
    'daily': ('day', 5),
    'min1': ('1m', 50),
    'min5': ('5m', 50),
    'min15': ('15m', 50),
    'min30': ('30m', 25),
    'min60': ('60m', 16),
}

DO_VERIFY = '--verify' in sys.argv


# ========== 增量更新逻辑 ==========

def _last_id(csv_path, id_field):
    """从已有CSV中获取最后一条记录的ID"""
    rows = se.read_csv(csv_path)
    if not rows:
        return None
    return rows[-1].get(id_field)


def _compat_normalize_rows(rows):
    """兼容旧CSV格式：raw_close → close"""
    for r in rows:
        if 'close' not in r and 'raw_close' in r:
            r['close'] = r.pop('raw_close')
    return rows


def _check_format_mismatch(all_rows, last_ts):
    """检测 rebuild 后 timestamp 格式变化导致增量失效"""
    if not all_rows or not last_ts:
        return False
    try:
        last_val = int(last_ts)
        src_first_val = int(all_rows[0]['timestamp'])
        if max(last_val, src_first_val) > min(last_val, src_first_val) * 10:
            return True
    except (ValueError, TypeError):
        pass
    return False


def _update_period_daily(code, market, data_path, csv_path, force_full):
    """更新日线周期，返回 (rows, new_rows, mode, elapsed, vol_elapsed)"""
    t0 = time.time()

    if force_full or not os.path.exists(csv_path):
        mode = '全量'
        rows = se.calc_daily_all(data_path)
        if rows:
            se.write_csv(csv_path, rows, se.SIGNAL_HEADERS)
        new_rows = rows
    else:
        mode = '增量'
        last_date = _last_id(csv_path, 'date')
        all_rows = se.calc_daily_all(data_path)
        if last_date:
            last_date_int = int(last_date)
            new_rows = [r for r in all_rows if r['date'] > last_date_int]
        else:
            new_rows = all_rows
        if new_rows:
            se.append_csv(csv_path, new_rows, se.SIGNAL_HEADERS)
        # 直接用内存中的 all_rows，不重复读 CSV
        rows = all_rows

    elapsed = time.time() - t0

    # 量能指标后处理
    vol_elapsed = 0.0
    if rows:
        t_vol = time.time()
        _compat_normalize_rows(rows)
        rows = se.calc_volume_indicators(rows)
        clean_rows = [{k: r[k] for k in se.SIGNAL_HEADERS if k in r} for r in rows]
        se.write_csv(csv_path, clean_rows, se.SIGNAL_HEADERS)
        rows = clean_rows
        vol_elapsed = time.time() - t_vol

    new_count = len(new_rows) if new_rows else 0
    print(f"  [daily] {mode}计算完成: 共{len(rows)}条, "
          f"新增{new_count}条, 量能={vol_elapsed:.1f}s, 总{elapsed + vol_elapsed:.1f}s")

    return rows, new_rows, mode, elapsed, vol_elapsed


def _update_period_min(code, market, data_path, csv_path, period, force_full):
    """更新分钟线周期，返回 (rows, new_rows, mode, elapsed, vol_elapsed)"""
    t0 = time.time()

    # 选择计算函数和趋势周期
    if period == 'min1':
        calcer = lambda fp: se.calc_min1_all(fp, period=period)
    else:
        calcer = lambda fp: se.calc_min_all(fp, period=period)

    if force_full or not os.path.exists(csv_path):
        mode = '全量'
        rows = calcer(data_path)
        if rows:
            se.write_csv(csv_path, rows, se.SIGNAL_HEADERS)
        new_rows = rows
    else:
        mode = '增量'
        last_ts = _last_id(csv_path, 'timestamp')
        all_rows = calcer(data_path)

        if _check_format_mismatch(all_rows, last_ts):
            last_val = int(last_ts) if last_ts else 0
            src_first_val = int(all_rows[0]['timestamp']) if all_rows else 0
            print(f"  [{period}] ⚠️ 检测到时间戳格式不匹配! "
                  f"CSV最后={last_val}, 源文件首条={src_first_val}, 强制全量重算")
            mode = '全量(格式修复)'
            if all_rows:
                se.write_csv(csv_path, all_rows, se.SIGNAL_HEADERS)
            new_rows = all_rows
            rows = all_rows
        elif last_ts:
            last_ts_int = int(last_ts)
            new_rows = [r for r in all_rows if r['timestamp'] > last_ts_int]
            if new_rows:
                se.append_csv(csv_path, new_rows, se.SIGNAL_HEADERS)
            # 直接用内存中的 all_rows，不重复读 CSV
            rows = all_rows
        else:
            new_rows = all_rows
            if new_rows:
                se.write_csv(csv_path, new_rows, se.SIGNAL_HEADERS)
            rows = all_rows

    elapsed = time.time() - t0

    # 量能指标后处理
    vol_elapsed = 0.0
    if rows:
        t_vol = time.time()
        _compat_normalize_rows(rows)
        rows = se.calc_volume_indicators(rows)
        clean_rows = [{k: r[k] for k in se.SIGNAL_HEADERS if k in r} for r in rows]
        se.write_csv(csv_path, clean_rows, se.SIGNAL_HEADERS)
        rows = clean_rows
        vol_elapsed = time.time() - t_vol

    new_count = len(new_rows) if (new_rows and mode != '全量') else (len(rows) if mode == '全量' else 0)
    if mode.startswith('全量'):
        new_count = len(rows)

    print(f"  [{period}] {mode}计算完成: 共{len(rows)}条, "
          f"新增{new_count}条, 量能={vol_elapsed:.1f}s, 总{elapsed + vol_elapsed:.1f}s")

    return rows, new_rows, mode, elapsed, vol_elapsed


def update_stock(code, market, force_full=False):
    """增量更新单个标的的所有周期信号"""
    print(f"\n{'='*50}")
    print(f"更新 {code} ({'深圳' if market == 'sz' else '上海'})")
    print(f"{'='*50}")

    periods_data = {}

    for period in PERIODS:
        data_path = se.get_data_path(code, market, period)
        csv_path = se.get_signal_path(code, period)

        if not os.path.exists(data_path):
            print(f"  [{period}] 数据文件不存在: {data_path}")
            continue

        if period == 'daily':
            rows, _new, _mode, _elapsed, _vol = _update_period_daily(
                code, market, data_path, csv_path, force_full)
        else:
            rows, _new, _mode, _elapsed, _vol = _update_period_min(
                code, market, data_path, csv_path, period, force_full)

        periods_data[period] = rows

    return periods_data


# ========== pytdx 抽验 ==========

def verify_with_api(code, market, period, local_rows):
    """
    用 pytdx API 拉取同周期 OHLC，对比本地计算的趋势线
    返回 (result, max_diff, note) 或 None(如果API不可用)
    """
    try:
        import tdx_fetch as tf
        api_period, count = VERIFY_PERIOD_MAP.get(period, (None, 0))
        if not api_period:
            return ('SKIP', 0, '不支持该周期的抽验')

        pure_code = code.replace('sz', '').replace('sh', '')
        api_bars = tf.fetch_bars(pure_code, api_period, market, count=count)
        if not api_bars:
            return ('SKIP', 0, 'API无数据')

        if len(local_rows) < 8 or len(api_bars) < 8:
            return ('SKIP', 0, '数据不足8根，跳过抽验')

        n = min(len(local_rows), len(api_bars))
        price_factor = 1000 if period == 'daily' else 10000

        # 单次循环同时算 max_diff 和 avg_diff
        max_diff = 0.0
        sum_diff = 0.0
        compare_n = min(n, 16)  # 最多对比16根

        for i in range(1, compare_n + 1):
            li = len(local_rows) - i
            ai = len(api_bars) - i

            local_raw = float(local_rows[li].get('close') or local_rows[li].get('raw_close', 0))
            local_close = local_raw / price_factor
            api_close = api_bars[ai]['close']
            diff = abs(local_close - api_close)

            sum_diff += diff
            if diff > max_diff:
                max_diff = diff

        avg_diff = sum_diff / compare_n if compare_n > 0 else 0

        result = 'PASS' if max_diff < 0.01 else ('WARN' if max_diff < 0.05 else 'FAIL')
        return (result, round(max_diff, 6),
                f'{compare_n}根, max_diff={max_diff:.4f}, avg={avg_diff:.4f}')

    except ImportError:
        return ('SKIP', 0, 'tdx_fetch 不可用')
    except Exception as e:
        return ('WARN', 0, '验算异常: %s' % str(e))


# ========== SQLite 持久化 ==========

def save_to_db(code, periods_data):
    """将计算结果存入 SQLite"""
    try:
        from tracking_db import TrackingDB
        db = TrackingDB()

        for period, rows in periods_data.items():
            if not rows:
                continue
            processed = [r for r in rows if isinstance(r, dict)]
            if not processed:
                continue

            snap_n = min(max(1, len(processed)), 50)
            tail = processed[-snap_n:]

            trend_vals = [float(r.get('trend_line', 0) or 0) for r in tail]
            close_vals = [float(r.get('close') or r.get('raw_close', 0) or 0) for r in tail]
            bar_times = [str(r.get('date') or r.get('timestamp') or '') for r in tail]

            db.save_snapshot(code, period, trend_vals,
                             bar_times=bar_times,
                             close_prices=close_vals,
                             n_bars=snap_n)

        db.close()
        print(f'[DB] 快照已保存到 SQLite ({len(periods_data)} 个周期)')
        return True
    except Exception as e:
        import traceback
        print(f'[WARN] SQLite 存档失败: {e}')
        traceback.print_exc()
        return False


def main():
    t_start = time.time()

    target_code = None
    for arg in sys.argv[1:]:
        if arg == '--verify':
            continue
        target_code = arg
        break

    if target_code:
        stocks = [(s, m) for s, m in TRACKING_STOCKS if s == target_code]
        if not stocks:
            print(f"未找到跟踪标的: {target_code}")
            print(f"当前跟踪: {[s for s, _ in TRACKING_STOCKS]}")
            return
    else:
        stocks = TRACKING_STOCKS

    all_snapshots = {}
    _raw_periods = {}

    for code, market in stocks:
        periods_data = update_stock(code, market)
        if periods_data:
            name = NAME_MAP.get(code, '')
            all_snapshots[code] = se.build_snapshot(code, market, periods_data, name=name)
            _raw_periods[code] = periods_data

        if DO_VERIFY and periods_data:
            print(f"\n  --- pytdx 抽验 {code} ---")
            try:
                from tracking_db import TrackingDB
                db = TrackingDB()
            except Exception:
                db = None
            for period, rows in periods_data.items():
                vresult = verify_with_api(code, market, period, rows)
                if vresult:
                    res, md, note = vresult
                    marker = {'PASS': '[OK]', 'FAIL': '[NG]', 'WARN': '[!]'}.get(res, '[-]')
                    print(f"    [{period}] {marker} {res}: {note}")
                    if db:
                        db.log_verify(code, period, res, max_diff=md, note=note)
            if db:
                db.close()

    for code, periods_data in _raw_periods.items():
        save_to_db(code, periods_data)

    # 保存 latest.json（单标模式：合并旧数据，不丢其他标的快照）
    snapshot_path = os.path.join(r'D:\quantify-per\signals\tracking', 'latest.json')
    if target_code and os.path.exists(snapshot_path):
        try:
            old = json.loads(open(snapshot_path, 'r', encoding='utf-8').read())
            old_stocks = old.get('stocks', {})
            old_stocks.update(all_snapshots)
            all_snapshots = old_stocks
        except Exception:
            pass
    se.save_snapshot(snapshot_path, all_snapshots)
    print(f"\n快照已保存: {snapshot_path}")

    print(f"\n{'='*50}")
    print(f"最新信号状态")
    print(f"{'='*50}")

    for code, snapshot in all_snapshots.items():
        code_name = snapshot.get('name', '')
        display = f"【{code}】"
        if code_name:
            display += f" {code_name}"
        print(f"\n{display}")
        for period, info in snapshot.items():
            if period == 'name':
                continue
            if info is None:
                print(f"  {period}: 无数据")
                continue
            expma = info.get('expma_status', '?')
            macd = info.get('macd_status', '?')
            trend = info.get('trend_line', '?')
            sig = info.get('signal', '无')
            cross = info.get('expma_cross', '')

            line = f"  {period:6s}: EXPMA={expma} MACD={macd} 趋势线={trend} 信号={sig}"
            if cross:
                line += f" 交叉={cross}"
            print(line)

    elapsed = time.time() - t_start
    print(f"\n总耗时: {elapsed:.1f}s")


if __name__ == '__main__':
    main()

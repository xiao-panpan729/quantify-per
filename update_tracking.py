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
import time

# 确保能导入 signal_engine 和 tools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
import signal_engine as se
from config import NAME_MAP


# ========== 配置 ==========

# 跟踪列表从 config.NAME_MAP 自动生成（加/删/改名只需维护 config.py）
def _get_market(code):
    """从代码自动推断市场（输入格式: sh520600 或 520600）"""
    # 剥离市场前缀
    if code.startswith(('sh', 'sz')):
        code = code[2:]
    return 'sh' if code[0] in ('6', '5') else 'sz'

TRACKING_STOCKS = [(code, _get_market(code)) for code in NAME_MAP.keys()]

# 需要计算的周期
PERIODS = ['daily', 'min1', 'min5', 'min15', 'min30', 'min60']

# 周期 → pytdx 拉取周期映射（用于抽验）
VERIFY_PERIOD_MAP = {
    'daily': ('day', 5),
    'min1': ('1m', 50),
    'min5': ('5m', 50),
    'min15': ('15m', 50),
    'min30': ('30m', 25),
    'min60': ('60m', 16),
}

# 是否执行 pytdx 抽验
DO_VERIFY = '--verify' in sys.argv


# ========== 增量更新逻辑 ==========

def get_last_id(csv_path, id_field):
    """从已有CSV中获取最后一条记录的ID（用于增量判断）"""
    rows = se.read_csv(csv_path)
    if not rows:
        return None
    return rows[-1].get(id_field)


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

        # 判断是否全量还是增量
        if force_full or not os.path.exists(csv_path):
            mode = '全量'
        else:
            mode = '增量'

        t0 = time.time()

        if period == 'daily':
            if mode == '全量':
                rows = se.calc_daily_all(data_path)
                if rows:
                    se.write_csv(csv_path, rows, se.DAILY_HEADERS)
            else:
                # 增量: 读取已有CSV, 从最后日期后开始算
                last_date = get_last_id(csv_path, 'date')
                all_rows = se.calc_daily_all(data_path)
                if last_date:
                    last_date_int = int(last_date)
                    new_rows = [r for r in all_rows if r['date'] > last_date_int]
                else:
                    new_rows = all_rows
                if new_rows:
                    se.append_csv(csv_path, new_rows, se.DAILY_HEADERS)
                else:
                    new_rows = []
                rows = se.read_csv(csv_path)
        else:
            # 分钟线
            if period == 'min1':
                calcer = lambda fp, p=period: se.calc_min1_all(fp, period=p)
            else:
                calcer = lambda fp, p=period: se.calc_min_all(fp, period=p)
            if mode == '全量':
                rows = calcer(data_path)
                if rows:
                    se.write_csv(csv_path, rows, se.MIN_HEADERS)
            else:
                last_ts = get_last_id(csv_path, 'timestamp')
                all_rows = calcer(data_path)

                # === 格式检测：防止 rebuild 后 timestamp 格式变化导致增量失效 ===
                # 原因: rebuild 可能改变源文件的时间戳格式(如从编码数字改为YYYYMMDD),
                #       旧CSV存的是旧格式的大数字, 新源文件是新格式的小数字,
                #       导致 last_ts > src_ts, 系统误判"无新数据"
                need_format_fix = False
                if all_rows and last_ts:
                    try:
                        last_val = int(last_ts)
                        src_first_val = int(all_rows[0]['timestamp'])
                        # 如果数量级差超过10倍(或一个>1e9另一个<1e10), 判定为格式不匹配
                        if max(last_val, src_first_val) > min(last_val, src_first_val) * 10:
                            need_format_fix = True
                            print(f"  [{period}] ⚠️ 检测到时间戳格式不匹配! "
                                  f"CSV最后={last_val}, 源文件首条={src_first_val}, 强制全量重算")
                    except (ValueError, TypeError):
                        pass

                if need_format_fix:
                    mode = '全量(格式修复)'
                    if all_rows:
                        se.write_csv(csv_path, all_rows, se.MIN_HEADERS)
                        new_rows = all_rows
                    else:
                        new_rows = []
                    rows = se.read_csv(csv_path)
                elif last_ts:
                    last_ts_int = int(last_ts)
                    new_rows = [r for r in all_rows if r['timestamp'] > last_ts_int]
                    if new_rows:
                        se.append_csv(csv_path, new_rows, se.MIN_HEADERS)
                    else:
                        pass  # 无新数据，保持原样
                    rows = se.read_csv(csv_path)
                else:
                    new_rows = all_rows
                    if new_rows:
                        se.write_csv(csv_path, new_rows, se.MIN_HEADERS)
                    rows = se.read_csv(csv_path)

        elapsed = time.time() - t0
        new_count = len(new_rows) if mode == '增量' else len(rows)

        print(f"  [{period}] {mode}计算完成: 共{len(rows)}条, "
              f"新增{new_count}条, 耗时{elapsed:.1f}s")

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
        api_period, count = VERIFY_PERIOD_MAP.get(period)
        if not api_period:
            return ('SKIP', 0, '不支持该周期的抽验')

        # 拉取 API 数据（去掉市场前缀，pytdx 只认纯代码）
        pure_code = code.replace('sz', '').replace('sh', '')
        api_bars = tf.fetch_bars(pure_code, api_period, market, count=count)
        if not api_bars:
            return ('SKIP', 0, 'API无数据')

        if len(local_rows) < 8 or len(api_bars) < 8:
            return ('SKIP', 0, '数据不足8根，跳过抽验')

        # 取最后 N 根做对比
        n = min(len(local_rows), len(api_bars))
        max_diff = 0
        diff_count = 0

        # 本地价格缩放因子: 日线/1000, 分钟线/10000
        price_factor = 1000 if period == 'daily' else 10000

        for i in range(1, n + 1):  # 从最新往前
            li = len(local_rows) - i
            ai = len(api_bars) - i

            local_raw = float(local_rows[li].get('close') or local_rows[li].get('raw_close', 0))
            local_close = local_raw / price_factor
            api_close = api_bars[ai]['close']
            diff = abs(local_close - api_close)

            if diff > max_diff:
                max_diff = diff
            diff_count += 1

        avg_diff = sum(
            abs(float(local_rows[len(local_rows)-i].get('close') or local_rows[len(local_rows)-i].get('raw_close', 0)) / price_factor
                 - api_bars[len(api_bars)-i]['close'])
            for i in range(1, min(n+1, 17))
        ) / min(n, 16) if n >= 2 else 0

        result = 'PASS' if max_diff < 0.01 else ('WARN' if max_diff < 0.05 else 'FAIL')
        return (result, round(max_diff, 6),
                f'{diff_count}根, max_diff={max_diff:.4f}, avg={avg_diff:.4f}')

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
            if not rows or len(rows) == 0:
                continue
            # 提取最后 N 根趋势线值（默认50根）
            rows_list = list(rows) if hasattr(rows, '__iter__') else []
            # 确保每个元素是 dict（兼容 CSV DictReader 和其他格式）
            processed = []
            for r in rows_list:
                if isinstance(r, dict):
                    processed.append(r)
                elif hasattr(r, '__iter__') and not isinstance(r, str):
                    # 可能是 list/tuple，跳过
                    continue
                else:
                    continue
            
            snap_n = min(max(1, len(processed)), 50) if processed else 1
            tail = processed[-snap_n:]

            trend_vals = [float(r.get('trend_line', 0)) for r in tail]
            close_vals = [float(r.get('close') or r.get('raw_close', 0)) for r in tail]
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

    # 解析命令行参数（排除 --verify 等标志）
    target_code = None
    for arg in sys.argv[1:]:
        if arg == '--verify':
            continue
        target_code = arg
        break

    # 确定更新范围
    if target_code:
        stocks = [(s, m) for s, m in TRACKING_STOCKS if s == target_code]
        if not stocks:
            print(f"未找到跟踪标的: {target_code}")
            print(f"当前跟踪: {[s for s, _ in TRACKING_STOCKS]}")
            return
    else:
        stocks = TRACKING_STOCKS

    # 逐个更新
    all_snapshots = {}
    _raw_periods = {}  # 保存原始数据给SQLite用

    for code, market in stocks:
        periods_data = update_stock(code, market)
        if periods_data:
            name = NAME_MAP.get(code, '')
            all_snapshots[code] = se.build_snapshot(code, market, periods_data, name=name)
            _raw_periods[code] = periods_data  # 原始行数据，供快照用

        # pytdx 抽验（如果启用）
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
                    marker = {'PASS':'[OK]','FAIL':'[NG]','WARN':'[!]'}.get(res, '[-]')
                    print(f"    [{period}] {marker} {res}: {note}")
                    if db:
                        db.log_verify(code, period, res,
                                    max_diff=md, note=note)
            if db:
                db.close()

    # SQLite 存档（用原始数据，不是精简摘要）
    for code, periods_data in _raw_periods.items():
        save_to_db(code, periods_data)

    # 生成 latest.json
    snapshot_path = os.path.join(
        r'D:\quantify-per\signals\tracking', 'latest.json'
    )
    se.save_snapshot(snapshot_path, all_snapshots)
    print(f"\n快照已保存: {snapshot_path}")

    # 打印摘要
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

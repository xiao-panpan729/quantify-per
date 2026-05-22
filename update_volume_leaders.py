# -*- coding: utf-8 -*-
"""
成交量领导者动态跟踪 — 信号计算脚本

从 volume_leader_universe.json 读取标的列表，复用 signal_engine 全部计算函数，
对每只标的执行6周期信号计算（日线/1分钟/5分钟/15分钟/30分钟/60分钟）。

全量 vs 增量逻辑与 update_tracking.py 一致：
  - CSV 不存在 → 全量计算（从 .day/.lc5 文件第1根K线开始）
  - CSV 存在 → 增量计算（只追加新K线）
  - 新标的首次跑是全量（几千根日线），后续每天增量（几根）

用法:
    python update_volume_leaders.py              # 更新所有 universe 标的
    python update_volume_leaders.py sh603986     # 只更新指定标的
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signal_engine as se

UNIVERSE_PATH = r'D:\quantify-per\signals\tracking\volume_leader_universe.json'
PERIODS = ['daily', 'min1', 'min5', 'min15', 'min30', 'min60']


def _get_market(code):
    """从代码自动推断市场"""
    if code.startswith('sh'):
        return 'sh'
    if code.startswith('sz'):
        return 'sz'
    return 'sh' if code[0] in ('6', '5') else 'sz'


def load_universe():
    """读取 universe JSON，返回 list[code_label]"""
    if not os.path.exists(UNIVERSE_PATH):
        return []
    with open(UNIVERSE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('universe', [])


# ========== 增量更新逻辑（镜像 update_tracking.py） ==========

def _last_id(csv_path, id_field):
    rows = se.read_csv(csv_path)
    if not rows:
        return None
    return rows[-1].get(id_field)


def _compat_normalize_rows(rows):
    for r in rows:
        if 'close' not in r and 'raw_close' in r:
            r['close'] = r.pop('raw_close')
    return rows


def _check_format_mismatch(all_rows, last_ts):
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
        rows = all_rows

    elapsed = time.time() - t0

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

    return rows


def _update_period_min(code, market, data_path, csv_path, period, force_full):
    t0 = time.time()

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
            print(f"  [{period}] 检测到时间戳格式不匹配! "
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
            rows = all_rows
        else:
            new_rows = all_rows
            if new_rows:
                se.write_csv(csv_path, new_rows, se.SIGNAL_HEADERS)
            rows = all_rows

    elapsed = time.time() - t0

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
    print(f"  [{period}] {mode}计算完成: 共{len(rows)}条, "
          f"新增{new_count}条, 量能={vol_elapsed:.1f}s, 总{elapsed + vol_elapsed:.1f}s")

    return rows


def update_stock(code, market, force_full=False):
    """增量更新单个标的的所有周期信号"""
    print(f"\n{'=' * 50}")
    print(f"更新 {code} ({'深圳' if market == 'sz' else '上海'})")
    print(f"{'=' * 50}")

    periods_data = {}

    for period in PERIODS:
        data_path = se.get_data_path(code, market, period)
        csv_path = se.get_signal_path(code, period)

        if not os.path.exists(data_path):
            print(f"  [{period}] 数据文件不存在: {data_path}")
            continue

        if period == 'daily':
            rows = _update_period_daily(code, market, data_path, csv_path, force_full)
        else:
            rows = _update_period_min(code, market, data_path, csv_path, period, force_full)

        periods_data[period] = rows

    return periods_data


def main():
    t_start = time.time()

    target_code = None
    for arg in sys.argv[1:]:
        if not arg.startswith('--'):
            target_code = arg
            break

    universe_codes = load_universe()
    if not universe_codes:
        print("universe 为空，请先运行 volume_leader_screener.py --sync-universe")
        return

    if target_code:
        if target_code not in universe_codes:
            print(f"标的 {target_code} 不在 universe 中")
            print(f"当前 universe: {len(universe_codes)} 只标的")
            return
        stocks = [(target_code, _get_market(target_code))]
    else:
        stocks = [(code, _get_market(code)) for code in universe_codes]

    print(f"\n{'=' * 60}")
    print(f"  成交量领导者信号更新 — {len(stocks)} 只标的")
    print(f"{'=' * 60}")

    success = 0
    skip_no_data = 0

    for code, market in stocks:
        try:
            periods_data = update_stock(code, market)
            if periods_data:
                has_any = any(v for v in periods_data.values())
                if has_any:
                    success += 1
                else:
                    skip_no_data += 1
            else:
                skip_no_data += 1
        except Exception as e:
            print(f"  [ERROR] {code}: {e}")
            import traceback
            traceback.print_exc()

    elapsed = time.time() - t_start
    print(f"\n{'=' * 60}")
    print(f"  完成: {success} 成功, {skip_no_data} 跳过(无数据)")
    print(f"  总耗时: {elapsed:.1f}s")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""
每日数据更新脚本 v4.7
支持深圳(sz) + 上海(sh) 双市场
从通达信同步最新数据到量化库

v4.7: 移除 sync_cache 文件缓存机制，precheck 替代为唯一判断入口
      逻辑简化: 通达信日期 > 本地日期 → 全量扫描 + 增量追加
- 日线/周月线: 直接复制二进制文件
- 分钟线(lc1/lc5): 增量追加模式，历史数据持续累积(不再被滚动窗口覆盖)
- 多周期合成: lc15/30/60 从lc5源文件直接合成
- 价格精度: 分钟线 price × 10000 (0.0001元精度)
- v4.6: lc1/lc5 改为增量追加模式; 复用 source_data 消除重复读取
"""

import os
import sys
import shutil
import struct
import random
from datetime import datetime, timedelta

import pandas as pd
# 导入配置
import config

# ========== 同步缓存 ==========
# 记录每只股票的"最后同步日期"，避免重复打开文件检查
# v4.7: 缓存机制已移除，precheck 是唯一判断入口
# 保留常量占位（向后兼容，不影响主流程）

# ========== 常量 ==========

# 分钟线价格编码因子: price × 10000 → uint32
# v4.4 升级: 从 100 改为 10000, 解决低价股/ETF精度不足问题
MIN_PRICE_FACTOR = 10000

# ========== 日志工具 ==========

class Logger:
    def __init__(self, log_file):
        self.log_file = log_file
        self.logs = []
        
    def log(self, message, level='INFO'):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_line = f"[{timestamp}] [{level}] {message}"
        self.logs.append(log_line)
        if config.VERBOSE or level == 'ERROR':
            print(log_line)
    
    def save(self):
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        with open(self.log_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self.logs))
        print(f"\n日志已保存：{self.log_file}")

# ========== 数据读取工具 ==========

def read_day_file(filepath):
    """读取通达信日线文件（每条32字节：日期/开/高/低/收/量/额/保留，均为4字节整数）"""
    data = []
    try:
        with open(filepath, 'rb') as f:
            while True:
                raw = f.read(32)
                if len(raw) < 32:
                    break
                values = struct.unpack('<8I', raw)
                data.append(values)
    except Exception as e:
        print(f"DEBUG: 读取日线失败 {filepath}: {str(e)}")
        return None
    return data

def read_min_file(filepath):
    """
    读取通达信分钟线源文件（lc1/lc5）
    每条32字节，pytdx 权威格式: <HHfffffII
      [0] uint16 日期编码 (低16位)
      [1] uint16 分钟数 (从0点开始)
      [2] float  open
      [3] float  high
      [4] float  low
      [5] float  close
      [6] float  amount
      [7] uint32 volume
      [8] uint32 reserved
    """
    data = []
    try:
        with open(filepath, 'rb') as f:
            while True:
                raw = f.read(32)
                if len(raw) < 32:
                    break
                values = struct.unpack('<HHfffffII', raw)
                data.append(values)
    except Exception as e:
        print(f"DEBUG: 读取分钟线失败 {filepath}: {str(e)}")
        return None
    return data

def _clamp_uint32(v):
    """float → uint32，NaN→0，超限截断，四舍五入"""
    return max(0, min(0xFFFFFFFF, round(0 if v != v else v)))


def _make_result(stock, market, data_type):
    """统一的同步/合成结果模板"""
    return {'stock': stock, 'market': market, 'type': data_type,
            'status': 'success', 'new_bars': 0, 'message': ''}

def _convert_single_bar(bar):
    """将一条通达信原始分钟线 bar (<HHfffffII>) 转换为存储格式 (8-tuple)"""
    date_int = tdx_ts_to_date_int(bar[0])   # YYYYMMDD
    time_int = tdx_ts_to_time_int(bar[1])   # HHMM
    return (
        date_int,                                    # [0] 日期 YYYYMMDD
        _clamp_uint32(bar[2] * MIN_PRICE_FACTOR),    # [1] open × 10000
        _clamp_uint32(bar[3] * MIN_PRICE_FACTOR),    # [2] high × 10000
        _clamp_uint32(bar[4] * MIN_PRICE_FACTOR),    # [3] low × 10000
        _clamp_uint32(bar[5] * MIN_PRICE_FACTOR),    # [4] close × 10000
        _clamp_uint32(bar[6]),                       # [5] amount
        _clamp_uint32(bar[7]),                       # [6] volume
        time_int,                                    # [7] HHMM
    )

def _convert_min_bars(source_data_raw):
    """批量转换通达信原始分钟线数据为存储格式列表"""
    return [_convert_single_bar(bar) for bar in source_data_raw]

def get_last_date_from_data(data, data_type):
    """从数据中获取最后一条的日期（YYYYMMDD）"""
    if data is None or len(data) == 0:
        return None
    last = data[-1]
    if data_type in ('lc1', 'lc5'):
        # source 文件格式 <HHfffffII>: bar[0]=uint16日期编码
        return tdx_ts_to_date_int(last[0])
    # lday 或已转换的目标文件: bar[0] 已是 YYYYMMDD
    return last[0]

def get_last_date_from_file(filepath, data_type):
    """从现有（已转换）文件中获取最后日期"""
    if not os.path.exists(filepath):
        return None
    if data_type == 'lday':
        data = read_day_file(filepath)
        if data is None or len(data) == 0:
            return None
        return data[-1][0]   # lday bar[0] = YYYYMMDD
    else:
        # 已转换的 lc5/lc15/lc30/lc60 文件，格式 <8I>，bar[0]=YYYYMMDD
        bars = []
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            count = len(content) // 32
            for i in range(count):
                raw = content[i*32:(i+1)*32]
                bars.append(struct.unpack('<8I', raw))
        except Exception:
            return None
        if not bars:
            return None
        return bars[-1][0]

def _full_merge(source_data, merge_count, bar_offset=0):
    """全量合并：从 lc5 源数据（<HHfffffII> 格式）合成目标周期 K 线
    source_data 中 bar 格式（pytdx <HHfffffII>）:
      bar[0]=uint16日期编码  bar[1]=uint16分钟数
      bar[2]=float open  bar[3]=float high  bar[4]=float low
      bar[5]=float close  bar[6]=float amount  bar[7]=uint32 volume
    输出 bar 格式（<8I>，存储价格×10000整数）:
      [0]=YYYYMMDD  [1]=open×10000  [2]=high×10000  [3]=low×10000  [4]=close×10000
      [5]=amount_int  [6]=volume  [7]=HHMM
    """
    # 60分钟(merge=12): 每天4根，时间固定 930, 1030, 1300, 1400
    # 30分钟(merge=6): 每天8根，时间 930,1000,1030,1100,1300,1330,1400,1430
    # 15分钟(merge=3): 每天16根，时间 930,945,1000,1015,1030,1045,1100,1115,1300,1315,1330,1345,1400,1415,1430,1445
    # ⚠️ 不能用 range(930, 1131, 15) 生成，因为 930+15=945, 945+15=960(09:60 无效!)
    if merge_count == 12:
        slot_times = [930, 1030, 1300, 1400]  # 60分钟每天4根
        slots_per_day = 4
    elif merge_count == 6:
        slot_times = [930, 1000, 1030, 1100, 1300, 1330, 1400, 1430]  # 30分钟每天8根
        slots_per_day = 8
    elif merge_count == 3:
        # 15分钟：按分钟数计算再转HHMM，避免整数加法导致无效时间
        _morning = list(range(570, 690, 15))   # 09:30~11:15 (570~675)
        _afternoon = list(range(780, 900, 15))  # 13:00~14:45 (780~885)
        slot_times = [(m // 60) * 100 + (m % 60) for m in _morning + _afternoon]
        slots_per_day = 16
    else:
        slot_times = None
        slots_per_day = 0

    merged_data = []
    for i in range(0, len(source_data), merge_count):
        chunk = source_data[i:i+merge_count]
        if len(chunk) < merge_count:
            break
        
        first_bar = chunk[0]
        last_bar = chunk[-1]

        total_amount = _clamp_uint32(sum(0 if bar[6] != bar[6] else bar[6] for bar in chunk))
        total_volume = _clamp_uint32(sum(0 if bar[7] != bar[7] else bar[7] for bar in chunk))
        
        # 日期：从 bar[0] 日期编码解码
        date_int = tdx_ts_to_date_int(first_bar[0])
        
        # 时间：按组在总序列中的位置确定
        if slot_times:
            total_bar_index = bar_offset + len(merged_data)
            slot_index = total_bar_index % slots_per_day
            time_int = slot_times[slot_index] if slot_index < len(slot_times) else 1500
        else:
            # 兜底：从 bar[1] 分钟数解码
            time_int = tdx_ts_to_time_int(first_bar[1])
        
        merged_bar = (
            date_int,                                                    # [0] YYYYMMDD
            _clamp_uint32(first_bar[2] * MIN_PRICE_FACTOR),             # [1] open
            _clamp_uint32(max(bar[3] for bar in chunk) * MIN_PRICE_FACTOR),  # [2] high
            _clamp_uint32(min(bar[4] for bar in chunk) * MIN_PRICE_FACTOR),  # [3] low
            _clamp_uint32(last_bar[5] * MIN_PRICE_FACTOR),              # [4] close
            total_amount,                              # [5] amount
            total_volume,                              # [6] volume
            time_int,                                  # [7] HHMM
        )
        merged_data.append(merged_bar)
    return merged_data


def int_to_date(date_int):
    """将整数日期转换为字符串 YYYY-MM-DD"""
    if date_int is None:
        return 'N/A'
    date_str = str(date_int)
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    elif len(date_str) == 6:
        return f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}"
    return str(date_int)

def tdx_ts_to_date_int(date_code):
    """通达信分钟线日期编码 → YYYYMMDD 整数
    
    基于 pytdx <HHfffffII> 格式:
    bar[0] = uint16 日期编码 = (year-2004)*2048 + month*100 + day
    """
    date_val = date_code & 0xFFFF
    year = (date_val // 2048) + 2004
    month = (date_val % 2048) // 100
    day = (date_val % 2048) % 100
    return year * 10000 + month * 100 + day

def tdx_ts_to_time_int(minute_code):
    """通达信分钟线时间编码 → HHMM 整数
    
    基于 pytdx <HHfffffII> 格式:
    bar[1] = uint16 从0点开始的分钟数 (HH*60 + MM)
    """
    minute_code = int(minute_code) & 0xFFFF
    hour = minute_code // 60
    minute = minute_code % 60
    return hour * 100 + minute

def tdx_min_ts_to_str(ts):
    """通达信分钟线时间戳 → 'YYYY-MM-DD HH:MM' 字符串"""
    return f"{tdx_ts_to_date_int(ts)} {tdx_ts_to_time_int(ts):04d}"

def min_bar_to_str(bar):
    """将合成后的分钟线bar转为时间字符串（bar[0]=YYYYMMDD, bar[7]=HHMM）"""
    d = str(bar[0])
    t = f"{int(bar[7]):04d}"
    return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:]}"

# ========== 数据同步核心 ==========

def sync_stock_data(stock_code, data_type, market, logger, force_rewrite=False):
    """同步单个股票的数据（支持指定市场）
    force_rewrite: 强制覆盖写入（rebuild模式使用，忽略日期比较）
    """
    result = _make_result(stock_code, market, data_type)
    
    # 源文件路径
    source_dir = config.TDX_SOURCE[market][data_type]
    ext = config.EXTENSIONS[data_type]
    source_file = os.path.join(source_dir, f"{stock_code}{ext}")
    
    # 目标文件路径
    target_dir = config.TARGET_DIR[market][data_type]
    target_file = os.path.join(target_dir, f"{stock_code}{ext}")
    
    if not os.path.exists(source_file):
        result['status'] = 'skip'
        result['message'] = '源文件不存在'
        return result
    
    # 读取源数据
    if data_type == 'lday':
        source_data = read_day_file(source_file)
    else:
        source_data = read_min_file(source_file)
    
    if source_data is None or len(source_data) == 0:
        result['status'] = 'error'
        result['message'] = '读取源数据失败'
        logger.log(f"[{market}] {stock_code} [{data_type}]: 读取失败", level='ERROR')
        return result
    
    source_last_date = get_last_date_from_data(source_data, data_type)
    target_last_date = get_last_date_from_file(target_file, data_type)
    
    if config.VERBOSE:
        logger.log(f"[{market}] {stock_code} [{data_type}]: 目标={int_to_date(target_last_date)}, 源={int_to_date(source_last_date)}")
    
    # 已是最新则跳过（force_rewrite 模式下不跳过）
    if not force_rewrite and target_last_date and target_last_date >= source_last_date:
        result['status'] = 'skip'
        result['message'] = '已是最新'
        return result
    
    # 确保目标目录存在
    os.makedirs(target_dir, exist_ok=True)
    
    # 分钟线(lc1/lc5)需要转换为可读时间戳格式
    # v4.6: 改为增量追加模式 — 通达信分钟线是滚动窗口(固定~3个月)，
    #       每次全量覆盖会导致目标文件历史无法累积。
    #       新逻辑: 用前面已读的 source_data → 筛选新K线 → 追加写入
    if data_type in ('lc1', 'lc5'):
        try:
            if force_rewrite or not os.path.exists(target_file) or target_last_date is None:
                # 首次运行或 rebuild 模式：整文件写入（和之前行为一致）
                bars_to_write = _convert_min_bars(source_data)  # 复用前面已读的 source_data
                mode_tag = '[FORCE]' if force_rewrite else '[NEW]'
                with open(target_file, 'wb') as f:
                    for bar in bars_to_write:
                        f.write(struct.pack('<8I', *bar))
                result['new_bars'] = len(bars_to_write)
                result['message'] = f'{mode_tag} 转换写入 ({len(bars_to_write)} 条)'
            else:
                # 增量模式：只筛选目标最后日期之后的新 K 线，追加到文件尾部
                bars_to_append = []
                for bar in source_data:  # 复用前面已读的 source_data
                    bar_date = tdx_ts_to_date_int(bar[0])
                    if bar_date > target_last_date:
                        bars_to_append.append(_convert_single_bar(bar))

                if len(bars_to_append) == 0:
                    result['status'] = 'skip'
                    result['message'] = f'已是最新 (目标截至{int_to_date(target_last_date)})'
                else:
                    with open(target_file, 'ab') as f:  # ab = 追加模式
                        for bar in bars_to_append:
                            f.write(struct.pack('<8I', *bar))
                    result['new_bars'] = len(bars_to_append)
                    result['message'] = f'增量追加 ({len(bars_to_append)} 条新K线)'

            # 把已读数据挂结果上，供调用方合成多周期时复用（省一次读盘）
            result['_source_data'] = source_data

            if config.VERBOSE:
                logger.log(f"[{market}] {stock_code} [{data_type}]: OK")
        except Exception as e:
            result['status'] = 'error'
            result['message'] = f'转换写入失败：{str(e)}'
            logger.log(f"[{market}] {stock_code} [{data_type}]: {result['message']}", level='ERROR')

    else:
        # 日线：增量追加（保留原始字节，不解码/重编码，避免精度损失）
        # source_data 已经由 read_day_file 解析，只用其日期做比较；
        # 实际写入使用原始字节，保证与通达信源文件完全一致
        try:
            if target_last_date and os.path.exists(target_file):
                # 增量模式：从源文件原始字节中筛选 date > target_last_date 的 bar
                with open(source_file, 'rb') as sf:
                    raw = sf.read()
                new_bars_bytes = []
                for offset in range(0, len(raw), 32):
                    chunk = raw[offset:offset+32]
                    if len(chunk) < 32:
                        break
                    bar_date = struct.unpack('<I', chunk[:4])[0]
                    if bar_date > target_last_date:
                        new_bars_bytes.append(chunk)
                if not new_bars_bytes:
                    result['status'] = 'skip'
                    result['message'] = '已是最新'
                else:
                    with open(target_file, 'ab') as tf:
                        for chunk in new_bars_bytes:
                            tf.write(chunk)
                    result['new_bars'] = len(new_bars_bytes)
                    result['message'] = f'增量追加 ({len(new_bars_bytes)} 条新K线)'
            else:
                # 首次运行：全量复制
                os.makedirs(os.path.dirname(target_file), exist_ok=True)
                shutil.copy2(source_file, target_file)
                result['new_bars'] = len(source_data)
                result['message'] = f'全量复制 ({len(source_data)} 条)'
            if config.VERBOSE:
                logger.log(f"[{market}] {stock_code} [{data_type}]: OK")
        except Exception as e:
            result['status'] = 'error'
            result['message'] = f'日线写入失败：{str(e)}'
            logger.log(f"[{market}] {stock_code} [{data_type}]: {result['message']}", level='ERROR')
    
    return result

def sync_gbbq(logger):
    """同步股本变迁数据（共用，不分市场）"""
    result = {'type': 'gbbq', 'status': 'success', 'message': ''}
    
    source_file = config.TDX_SOURCE['gbbq']
    target_file = os.path.join(config.TARGET_DIR['gbbq'], 'gbbq')
    
    if not os.path.exists(source_file):
        result['status'] = 'error'
        result['message'] = '源文件不存在'
        logger.log("gbbq: 源文件不存在", level='ERROR')
        return result
    
    os.makedirs(config.TARGET_DIR['gbbq'], exist_ok=True)
    
    try:
        shutil.copy2(source_file, target_file)
        result['message'] = '复制成功'
        logger.log("gbbq: OK")
    except Exception as e:
        result['status'] = 'error'
        result['message'] = f'复制失败：{str(e)}'
        logger.log(f"gbbq: {result['message']}", level='ERROR')
    
    return result

def merge_min_data_from_source(stock_code, target_type, market, logger, source_data):
    """从已读取的 source_data 合成多周期数据（避免重复读取文件）"""
    result = _make_result(stock_code, market, target_type)
    
    merge_count = config.MERGE_CONFIG.get(target_type)
    if merge_count is None:
        result['status'] = 'error'
        result['message'] = f'未知合成类型：{target_type}'
        return result
    
    # 目标文件
    target_dir = config.TARGET_DIR[market][target_type]
    target_file = os.path.join(target_dir, f"{stock_code}{config.EXTENSIONS[target_type]}")
    
    if source_data is None or len(source_data) == 0:
        result['status'] = 'error'
        result['message'] = 'source_data为空'
        return result
    
    return _do_merge(stock_code, target_type, market, logger, source_data, target_file, merge_count, rebuild=False)


def merge_min_data(stock_code, target_type, market, logger, rebuild=False):
    """从 5 分钟线合成多周期数据（独立读取源文件）
    v4.4: 直接读取通达信原始 lc5 源文件 (<HHfffffII>) 进行合成
    输出格式: field[0]=YYYYMMDD, field[7]=HHMM, 价格=price×10000
    增量模式: 通过目标文件大小(条数)推算已消耗的lc5位置
    """
    result = _make_result(stock_code, market, target_type)
    
    merge_count = config.MERGE_CONFIG.get(target_type)
    if merge_count is None:
        result['status'] = 'error'
        result['message'] = f'未知合成类型：{target_type}'
        return result
    
    # 源文件：通达信原始 lc5 (<HHfffffII> 格式)
    source_dir = config.TDX_SOURCE[market]['lc5']
    source_file = os.path.join(source_dir, f"{stock_code}.lc5")
    
    # 目标文件
    target_dir = config.TARGET_DIR[market][target_type]
    target_file = os.path.join(target_dir, f"{stock_code}{config.EXTENSIONS[target_type]}")
    
    if not os.path.exists(source_file):
        result['status'] = 'skip'
        result['message'] = '5分钟源文件不存在'
        return result
    
    source_data = read_min_file(source_file)
    if source_data is None or len(source_data) == 0:
        result['status'] = 'error'
        result['message'] = '读取5分钟数据失败'
        return result
    
    return _do_merge(stock_code, target_type, market, logger, source_data, target_file, merge_count, rebuild=rebuild)


def _do_merge(stock_code, target_type, market, logger, source_data, target_file, merge_count, rebuild=False):
    result = _make_result(stock_code, market, target_type)
    target_dir = os.path.dirname(target_file)
    
    # 全量合成（rebuild=True 或 无目标文件）
    if rebuild or not os.path.exists(target_file):
        tag = '[REBUILD]' if rebuild else '[NEW]'
        merged_data = _full_merge(source_data, merge_count)
        if len(merged_data) == 0:
            result['status'] = 'skip'
            result['message'] = '无数据可合成'
            return result
        
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        try:
            with open(target_file, 'wb') as f:
                for bar in merged_data:
                    f.write(struct.pack('<8I', *bar))
            result['new_bars'] = len(merged_data)
            result['message'] = f'{tag} 全量合成 {len(merged_data)} 条'
            if config.VERBOSE:
                logger.log(f"[{market}] {stock_code} [{target_type}]: OK {result['message']}")
        except Exception as e:
            result['status'] = 'error'
            result['message'] = f'写入失败：{str(e)}'
            logger.log(f"[{market}] {stock_code} [{target_type}]: {result['message']}", level='ERROR')
        return result
    
    # ---- 增量模式：通过目标文件大小推算已消耗的 lc5 条数 ----
    existing_count = 0
    try:
        file_size = os.path.getsize(target_file)
        existing_count = file_size // 32  # 每条32字节
    except OSError:
        pass
    
    if existing_count <= 0:
        # 文件为空或读取失败，回退到全量
        merged_data = _full_merge(source_data, merge_count)
    else:
        # 已有 N 条 = 已消耗 N*merge_count 根lc5
        expected_lc5_count = existing_count * merge_count
        
        if expected_lc5_count >= len(source_data):
            result['status'] = 'skip'
            result['message'] = '已是最新'
            return result
        
        # 从下一组开始增量合成
        new_source = source_data[expected_lc5_count:]
        merged_data = _full_merge(new_source, merge_count, bar_offset=existing_count)
        
        if len(merged_data) == 0:
            result['status'] = 'skip'
            result['message'] = '无新数据'
            return result
        
        # 直接追加新合成 bar（bar_offset 保证时间槽正确）
        try:
            os.makedirs(target_dir, exist_ok=True)
            with open(target_file, 'ab') as f:
                for bar in merged_data:
                    f.write(struct.pack('<8I', *bar))
            result['new_bars'] = len(merged_data)
            result['message'] = f'增量合成 {len(merged_data)} 条（总计{existing_count + len(merged_data)}）'
            if config.VERBOSE:
                logger.log(f"[{market}] {stock_code} [{target_type}]: OK {result['message']}")
        except Exception as e:
            result['status'] = 'error'
            result['message'] = f'写入失败：{str(e)}'
            logger.log(f"[{market}] {stock_code} [{target_type}]: {result['message']}", level='ERROR')
    
    return result

def merge_daily_to_period(stock_code, target_type, market, logger):
    """从日线合成周线/月线数据（支持指定市场）"""
    result = _make_result(stock_code, market, target_type)
    
    source_dir = config.TARGET_DIR[market]['lday']
    source_file = os.path.join(source_dir, f"{stock_code}.day")
    
    target_dir = config.TARGET_DIR[market][target_type]
    target_file = os.path.join(target_dir, f"{stock_code}{config.EXTENSIONS[target_type]}")
    
    if not os.path.exists(source_file):
        result['status'] = 'skip'
        result['message'] = '日线源文件不存在'
        return result
    
    source_data = read_day_file(source_file)
    if source_data is None or len(source_data) == 0:
        result['status'] = 'error'
        result['message'] = '读取日线数据失败'
        return result
    
    target_last_date = get_last_date_from_file(target_file, target_type)
    source_last_date = get_last_date_from_data(source_data, 'lday')
    
    if config.VERBOSE:
        logger.log(f"[{market}] {stock_code} [{target_type}]: 目标={int_to_date(target_last_date)}, 源={int_to_date(source_last_date)}")
    
    if target_last_date and target_last_date >= source_last_date:
        result['status'] = 'skip'
        result['message'] = '已是最新'
        return result

    PRICE_FACTOR = 1000.0

    # 读取现有目标文件（用于后续追加）
    existing_bars = []
    if target_last_date and os.path.exists(target_file):
        existing_raw = read_day_file(target_file)
        if existing_raw:
            existing_bars = existing_raw

    # 增量：只取目标最后日期之后的日线数据
    # 注意：周/月 resample 需要包含最后一个未完结周期的日线，
    # 所以要从目标文件最后 bar 对应周期的第一天开始重算最后一段。
    # 简单安全做法：从 target_last_date 对应的周期开始往后重算（丢弃已写的最后一根重新算）
    if target_last_date:
        # 找到目标最后 bar 在日线中的对应位置，往前退到上一根已完结 bar 结束处
        # 实践上：把最后一根 existing_bars 去掉（可能是未完结周期），重算最后一段
        if len(existing_bars) > 1:
            cutoff_date = existing_bars[-1][0]  # 最后一根 bar 的日期（整数）
        else:
            cutoff_date = 0
        # 日线中只保留 cutoff_date 之后（不含）的数据做增量 resample
        new_source_data = [bar for bar in source_data if bar[0] > cutoff_date]
        # 如果无新日线，跳过
        if not new_source_data:
            result['status'] = 'skip'
            result['message'] = '已是最新'
            return result
        # 去掉 existing_bars 最后一根（未完结周期可能需要更新），用新日线重新生成它
        base_bars = existing_bars[:-1] if existing_bars else []
    else:
        new_source_data = source_data
        base_bars = []
    
    dates = []
    data_list = []
    for bar in new_source_data:
        date_int = bar[0]
        date_str = str(date_int)
        if len(date_str) == 8:
            date = pd.to_datetime(date_str)
        else:
            continue
        dates.append(date)
        data_list.append({
            'open': bar[1] / PRICE_FACTOR,
            'high': bar[2] / PRICE_FACTOR,
            'low': bar[3] / PRICE_FACTOR,
            'close': bar[4] / PRICE_FACTOR,
            'volume': bar[6],    # bar[6]=volume (pytdx <IIIIIfII)
            'amount': bar[5]     # bar[5]=amount (pytdx <IIIIIfII)
        })
    
    df = pd.DataFrame(data_list, index=dates)
    
    if target_type == 'week':
        period = 'W-FRI'
        period_label = '周线'
    elif target_type == 'month':
        period = 'ME'
        period_label = '月线'
    else:
        result['status'] = 'error'
        result['message'] = f'未知周期：{target_type}'
        return result
    
    df_resampled = df.resample(period).agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum', 'amount': 'sum'
    })
    df_resampled = df_resampled.dropna(subset=['open', 'close'])
    
    if len(df_resampled) == 0:
        result['status'] = 'skip'
        result['message'] = '无新数据'
        return result
    
    MAX_UINT32 = 0x7FFFFFFF
    new_bars_list = []
    for idx, row in df_resampled.iterrows():
        vol = int(row['volume'])
        amt = int(row['amount'])
        if vol > MAX_UINT32: vol = MAX_UINT32
        if amt > MAX_UINT32: amt = MAX_UINT32
        new_bars_list.append((
            int(idx.strftime('%Y%m%d')),
            int(row['open'] * PRICE_FACTOR),
            int(row['high'] * PRICE_FACTOR),
            int(row['low'] * PRICE_FACTOR),
            int(row['close'] * PRICE_FACTOR),
            vol, amt, 0
        ))
    
    # 最终写入 = 基础数据（去掉最后一根未完结bar）+ 重算的新bar
    all_data = list(base_bars) + new_bars_list
    
    os.makedirs(target_dir, exist_ok=True)
    
    try:
        with open(target_file, 'wb') as f:
            for bar in all_data:
                for value in bar:
                    # NaN 保护：NaN != NaN
                    if value != value:
                        v = 0
                    else:
                        v = int(value) if int(value) >= 0 else 0
                    if v > 0xFFFFFFFF: v = 0xFFFFFFFF
                    f.write(struct.pack('<I', v))
        result['new_bars'] = len(new_bars_list)
        result['message'] = f'合成{period_label} {len(new_bars_list)} 条（增量）'
        if config.VERBOSE:
            logger.log(f"[{market}] {stock_code} [{target_type}]: OK 合成{len(all_data)}条")
    except Exception as e:
        result['status'] = 'error'
        result['message'] = f'写入失败：{str(e)}'
        logger.log(f"[{market}] {stock_code} [{target_type}]: {result['message']}", level='ERROR')
    
    return result

def scan_stocks(market, data_type):
    """扫描通达信目录中指定市场的所有股票代码"""
    source_dir = config.TDX_SOURCE[market][data_type]
    ext = config.EXTENSIONS[data_type]
    
    stocks = []
    if os.path.exists(source_dir):
        for filename in os.listdir(source_dir):
            if filename.endswith(ext):
                stock_code = filename[:-len(ext)]
                stocks.append(stock_code)
    return sorted(stocks)

# ========== 下载后随机抽查 ==========

# 确保能导入 tdx_fetch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'tools'))

def _random_verify(market, stock_list, logger, sample_size=10, bar_count=10):
    """
    下载完成后随机抽查 lc5 数据正确性
    - 从当天更新的股票中随机抽 sample_size 只
    - 每只对比最近 bar_count 根 close 价格
    - 单只偏差 > 0.10 元记异常，自动重试一次
    - 大面积异常(>30%)报警但不阻塞
    """
    try:
        import tdx_fetch as tf  # type: ignore
    except ImportError:
        logger.log("  [抽查] 跳过: tdx_fetch 不可用", level='WARN')
        return
    
    # 随机抽样
    sample = random.sample(stock_list, min(sample_size, len(stock_list)))
    logger.log(f"  [抽查] 随机抽取 {len(sample)} 只进行 lc5 数据验证...")
    
    fail_count = 0
    retry_count = 0
    
    for code in sample:
        pure_code = code.replace('sz', '').replace('sh', '')
        source_file = os.path.join(config.TDX_SOURCE[market]['lc5'], f"{code}.lc5")
        
        # 读取本地 lc5 最后 bar_count 根
        local_data = read_min_file(source_file)
        if not local_data or len(local_data) < bar_count:
            logger.log(f"  [抽查] {code}: 本地数据不足{bar_count}根，跳过")
            continue
        
        local_bars = local_data[-bar_count:]
        
        # 拉取 pytdx 最近 bar_count 根 5分钟
        api_bars = tf.fetch_bars(pure_code, '5m', market, count=bar_count)
        if not api_bars or len(api_bars) < bar_count:
            logger.log(f"  [抽查] {code}: pytdx 数据不足，跳过")
            continue
        
        # 对比 close 价格
        max_diff = 0.0
        for i in range(bar_count):
            local_close = local_bars[i][5]  # index 5 = close (float)
            api_close = api_bars[i]['close']
            diff = abs(local_close - api_close)
            if diff > max_diff:
                max_diff = diff
        
        if max_diff <= 0.10:
            logger.log(f"  [抽查] {code}: PASS (max_diff={max_diff:.4f})")
            continue
        
        # 异常：自动重试一次
        logger.log(f"  [抽查] {code}: FAIL (max_diff={max_diff:.4f})，尝试重试下载...", level='WARN')
        retry_count += 1
        
        # 强制重新同步该股票 lc5
        r = sync_stock_data(code, 'lc5', market, logger, force_rewrite=True)
        if r['status'] != 'success':
            logger.log(f"  [抽查] {code}: 重试下载失败", level='ERROR')
            fail_count += 1
            continue
        
        # 重试后再次对比
        local_data = read_min_file(source_file)
        if not local_data or len(local_data) < bar_count:
            logger.log(f"  [抽查] {code}: 重试后数据仍不足", level='ERROR')
            fail_count += 1
            continue
        
        local_bars = local_data[-bar_count:]
        max_diff = 0.0
        for i in range(bar_count):
            local_close = local_bars[i][5]
            api_close = api_bars[i]['close']
            diff = abs(local_close - api_close)
            if diff > max_diff:
                max_diff = diff
        
        if max_diff <= 0.10:
            logger.log(f"  [抽查] {code}: PASS after retry (max_diff={max_diff:.4f})")
        else:
            logger.log(f"  [抽查] {code}: FAIL after retry (max_diff={max_diff:.4f})", level='ERROR')
            fail_count += 1
    
    # 汇总
    total = len(sample)
    if fail_count == 0:
        logger.log(f"  [抽查] 全部通过: {total}/{total}")
    elif fail_count / total > 0.3:
        logger.log(f"  [抽查] 警告: {fail_count}/{total} 异常，建议检查通达信数据源!", level='WARN')
    else:
        logger.log(f"  [抽查] 结果: {total - fail_count}/{total} 通过, {fail_count} 只异常, {retry_count} 只重试")


# ========== 主程序 ==========

def process_market(market, stock_list, logger, rebuild=False):
    """处理单个市场的全部数据同步和合成（v4.7: 无缓存，precheck 保证有增量）"""
    stats = {'total': 0, 'success': 0, 'skip': 0, 'error': 0, 'new_bars': 0}
    
    market_name = '深圳' if market == 'sz' else '上海'

    def _tally(r):
        stats['total'] += 1
        if r['status'] == 'success':
            stats['success'] += 1
            stats['new_bars'] += r['new_bars']
        elif r['status'] == 'skip':
            stats['skip'] += 1
        else:
            stats['error'] += 1

    # v4.7: precheck 已确认通达信有新数据，全部股票走增量
    logger.log(f"  [{market_name}] 全量扫描 {len(stock_list)} 只（增量追加模式）")

    # 1. 同步日线（全部股票走增量）
    logger.log(f"  [{market_name}] 1/6 同步日线 (lday) {len(stock_list)} 只")
    for code in stock_list:
        _tally(sync_stock_data(code, 'lday', market, logger))

    # 2. 同步1分钟（全部股票走增量）
    logger.log(f"  [{market_name}] 2/6 同步1分钟 (lc1) {len(stock_list)} 只{' [FORCE]' if rebuild else ''}")
    for code in stock_list:
        _tally(sync_stock_data(code, 'lc1', market, logger, force_rewrite=rebuild))

    # 3. 同步5分钟 + 同步合成15/30/60分钟（复用 sync 返回的 source_data，不再重读文件）
    logger.log(f"  [{market_name}] 3/6 同步5分钟并合成15/30/60分钟 {len(stock_list)} 只")
    for code in stock_list:
        r = sync_stock_data(code, 'lc5', market, logger, force_rewrite=rebuild)
        _tally(r)

        if r['status'] == 'success' and not rebuild:
            source_data = r.get('_source_data')
            if source_data:
                for target_type in ['lc15', 'lc30', 'lc60']:
                    _tally(merge_min_data_from_source(code, target_type, market, logger, source_data))
            else:
                for target_type in ['lc15', 'lc30', 'lc60']:
                    _tally(merge_min_data(code, target_type, market, logger, rebuild=rebuild))
        else:
            for target_type in ['lc15', 'lc30', 'lc60']:
                _tally(merge_min_data(code, target_type, market, logger, rebuild=rebuild))

    # 4. 合成周线
    logger.log(f"  [{market_name}] 4/6 合成周线 (week) {len(stock_list)} 只")
    for code in stock_list:
        _tally(merge_daily_to_period(code, 'week', market, logger))

    # 5. 合成月线
    logger.log(f"  [{market_name}] 5/6 合成月线 (month) {len(stock_list)} 只")
    for code in stock_list:
        _tally(merge_daily_to_period(code, 'month', market, logger))
    
    logger.log(f"  [{market_name}] 完成: 成功={stats['success']}, 跳过={stats['skip']}, 失败={stats['error']}")
    
    # 6. 下载后随机抽查
    if not rebuild and len(stock_list) > 0:
        _random_verify(market, stock_list, logger)
    
    return stats

def precheck_tdx_data(logger):
    """预检通达信源数据是否比本地项目新（日线 + 分钟线）v4.7 从严
    
    比较逻辑：取本地项目跟踪标的的最后日期，
    对比通达信抽查股票的日期。通达信日期 > 本地日期才通过。
    必须严格大于——没新数据就别跑。
    """
    from pytdx.reader import TdxDailyBarReader, TdxMinBarReader
    from datetime import date

    # 取本地项目跟踪标的的最后日期
    # 注意：本地 lc1/lc5 文件已经转换为 <8I> 格式（bar[0]=YYYYMMDD），
    #       日线文件是直接从通达信复制的源码格式（<IIIIIfII>）
    local_paths = {
        'lday': os.path.join(config.TARGET_DIR['sz']['lday'], 'sz159740.day'),
        'lc1': os.path.join(config.TARGET_DIR['sz']['lc1'], 'sz159740.lc1'),
        'lc5': os.path.join(config.TARGET_DIR['sz']['lc5'], 'sz159740.lc5'),
    }
    local_last = {}
    for dtype, path in local_paths.items():
        if os.path.exists(path):
            try:
                with open(path, 'rb') as f:
                    f.seek(-32, 2)
                    data = f.read(32)
                    if dtype == 'lday':
                        # 日线源格式 <IIIIIfII>：bar[0] = YYYYMMDD 整数
                        local_last[dtype] = struct.unpack('<I', data[:4])[0]
                    else:
                        # 已转换的分钟线 <8I> 格式：bar[0] = YYYYMMDD 整数
                        local_last[dtype] = struct.unpack('<I', data[:4])[0]
            except:
                pass

    # 随机抽查股票（从全市场扫描，不用固定股票）
    check_stocks = []
    for market in ['sz', 'sh']:
        stocks = scan_stocks(market, 'lday')
        for s in stocks:
            check_stocks.append((market, s))
    if len(check_stocks) > 5:
        check_stocks = random.sample(check_stocks, 5)
    logger.log(f"  预检抽查: {len(check_stocks)} 只 (全市场随机)")

    daily_reader = TdxDailyBarReader()
    min_reader = TdxMinBarReader()
    
    # 跟踪通达信各类型的最大日期
    tdx_max_date = {'lday': 0, 'lc5': 0}

    for market, code in check_stocks:
        # 检查日线
        day_path = os.path.join(config.TDX_SOURCE[market]['lday'], f"{code}.day")
        if os.path.exists(day_path):
            try:
                df = daily_reader.get_df(day_path)
                if len(df) > 0:
                    last_int = int(df.index[-1].date().strftime('%Y%m%d'))
                    if last_int > tdx_max_date['lday']:
                        tdx_max_date['lday'] = last_int
            except Exception as e:
                logger.log(f"日线读取失败: {code} ({e})", level='WARN')

        # 检查5分钟线
        lc5_path = os.path.join(config.TDX_SOURCE[market]['lc5'], f"{code}.lc5")
        if os.path.exists(lc5_path):
            try:
                df = min_reader.get_df(lc5_path)
                if len(df) > 0:
                    last_int = int(df.index[-1].date().strftime('%Y%m%d'))
                    if last_int > tdx_max_date['lc5']:
                        tdx_max_date['lc5'] = last_int
            except Exception as e:
                logger.log(f"分钟线读取失败: {code} ({e})", level='WARN')

    # 判断：日线或分钟线任一有更新则通过
    # v4.8: >= 而非 >，本地已有当天数据也放行(分钟线日线已下载但未合成的情况)
    lday_new = tdx_max_date['lday'] >= local_last.get('lday', 0)
    lc5_new = tdx_max_date['lc5'] >= local_last.get('lc5', 0)
    
    logger.log(f"本地日期: lday={local_last.get('lday','?')}, lc5={local_last.get('lc5','?')}")
    logger.log(f"通达信最大日期: lday={tdx_max_date['lday']}, lc5={tdx_max_date['lc5']}")

    if not lday_new and not lc5_new:
        logger.log("=" * 60, level='ERROR')
        logger.log("通达信数据预检失败: 日线和分钟线均无新数据", level='ERROR')
        logger.log(f"  日线: 通达信{tdx_max_date['lday']} <= 本地{local_last.get('lday','?')}", level='ERROR')
        logger.log(f"  分钟线: 通达信{tdx_max_date['lc5']} <= 本地{local_last.get('lc5','?')}", level='ERROR')
        logger.log("=" * 60, level='ERROR')
        logger.log("请先运行通达信盘后下载。", level='ERROR')
        return False

    parts = []
    if lday_new:
        parts.append(f"日线({tdx_max_date['lday']})")
    if lc5_new:
        parts.append(f"分钟线({tdx_max_date['lc5']})")
    logger.log(f"通达信数据预检通过: {' + '.join(parts)} 有新数据 [OK]")
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description='量化数据更新工具 v4.2')
    parser.add_argument('--rebuild', action='store_true',
                        help='全量重合成 lc15/lc30/lc60（修复数据错位时使用）')
    parser.add_argument('--skip-precheck', action='store_true',
                        help='跳过通达信数据预检（仅用于调试）')
    args = parser.parse_args()

    print("=" * 60)
    if args.rebuild:
        print("量化数据更新工具 v4.6 [REBUILD 模式 - 全量重合成]")
        print("  警告: 将从 lc5 完整重建所有多周期文件!")
    else:
        print("量化数据更新工具 v4.6 (sz+sh 双市场, lc1/lc5 增量追加, round修复)")
    print("=" * 60)
    print()

    # 创建日志
    mode_tag = "_rebuild" if args.rebuild else ""
    log_filename = f"update{mode_tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_path = os.path.join(config.LOG_DIR, log_filename)
    logger = Logger(log_path)

    logger.log("开始数据更新...")
    if args.rebuild:
        logger.log("模式: REBUILD (全量重合成)")
    logger.log(f"市场: {config.MARKETS}")
    logger.log("-" * 60)

    # 预检通达信数据（除非显式跳过）
    if not args.skip_precheck:
        if not precheck_tdx_data(logger):
            logger.save()
            return 1  # 预检失败，退出码1
    else:
        logger.log("预检已跳过 (--skip-precheck)")

    total_stats = {'total': 0, 'success': 0, 'skip': 0, 'error': 0, 'new_bars': 0}
    
    for market in config.MARKETS:
        market_name = '深圳' if market == 'sz' else '上海'
        logger.log(f">>> 开始处理 {market_name} 市场 ({market})")
        
        # 确定股票列表
        if config.AUTO_SCAN:
            stock_list = scan_stocks(market, 'lday')
            logger.log(f"  自动扫描发现 {len(stock_list)} 只股票")
        else:
            # 手动模式：筛选当前市场的股票
            stock_list = [s for s in config.STOCK_LIST if s.startswith(market)]
            logger.log(f"  手动模式: {len(stock_list)} 只股票")
        
        if len(stock_list) == 0:
            logger.log(f"  [{market_name}] 无股票需要处理，跳过")
            continue
        
        stats = process_market(market, stock_list, logger, rebuild=args.rebuild)
        
        for k in total_stats:
            total_stats[k] += stats[k]
    
    # 同步 gbbq（共用）
    logger.log("-" * 60)
    logger.log("9/9 同步股本变迁 (gbbq)")
    gbbq_result = sync_gbbq(logger)
    if gbbq_result['status'] == 'success':
        total_stats['success'] += 1
    else:
        total_stats['error'] += 1
    
    # 输出统计
    logger.log("=" * 60)
    logger.log("更新统计")
    logger.log(f"  总操作数: {total_stats['total']}")
    logger.log(f"  成功: {total_stats['success']}")
    logger.log(f"  跳过: {total_stats['skip']}")
    logger.log(f"  失败: {total_stats['error']}")
    logger.log(f"  新增K线: {total_stats['new_bars']} 条")
    logger.log("=" * 60)
    
    logger.save()
    
    print()
    print("数据更新完成!")
    print(f"日志: {log_path}")
    print()
    
    if total_stats['error'] > 0:
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())

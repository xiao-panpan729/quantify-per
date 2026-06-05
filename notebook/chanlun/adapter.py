"""
数据格式适配器：quantify-per 数据格式 ↔ czsc RawBar

价格因子:
  - 日线 (Freq.D / Freq.W / Freq.M): DAY_PRICE_FACTOR = 1000 -> 原始值/1000
  - 分钟线 (Freq.F1~F60): MIN_PRICE_FACTOR = 10000 -> 原始值/10000

CSV中分钟线价格存储为原始pytdx格式(×10000)，日线已归一化(×1000)。
"""
from datetime import datetime
from typing import List

import pandas as pd
from czsc import RawBar, Freq

# 价格因子常量(来自 quantify-per 约定)
DAY_PRICE_FACTOR = 1000.0
MIN_PRICE_FACTOR = 10000.0


def df_to_bars(df: pd.DataFrame, symbol: str, freq: Freq = Freq.D) -> List[RawBar]:
    """将 quantify-per 的 OHLCV DataFrame 转为 czsc RawBar 列表

    自动检测价格因子:
      - Freq.D/Freq.W/Freq.M -> ÷1000
      - Freq.F1~F60 -> ÷10000

    Args:
        df: 包含 open/high/low/close 列
        symbol: 标的代码，如 "sz159740"
        freq: K线周期

    Returns:
        按时间正序排列的 RawBar 列表
    """
    df = df.copy()

    # 确定时间列
    if 'timestamp' in df.columns and df['timestamp'].dtype in (int, float, 'int64', 'float64'):
        time_col = 'timestamp'
    elif 'date' in df.columns:
        time_col = 'date'
    else:
        time_col = None

    if time_col:
        df['_dt'] = pd.to_datetime(df[time_col].astype(str), format='%Y%m%d', errors='coerce')
    elif not isinstance(df.index, pd.DatetimeIndex):
        df['_dt'] = pd.to_datetime(df.index)
    else:
        df['_dt'] = df.index

    # 价格因子: 判断是否需要归一化
    # 日线/周线/月线/季线/年线 -> DAY_PRICE_FACTOR
    # 分钟线 -> MIN_PRICE_FACTOR
    if freq in (Freq.D, Freq.W, Freq.M, Freq.S, Freq.Y):
        factor = DAY_PRICE_FACTOR
    else:
        factor = MIN_PRICE_FACTOR

    # 自动检测: 如果价格已经归一化(最大值 < 100)，不再除因子
    first_close = float(df.iloc[0].get('close', df.iloc[0].get('Close', 0)))
    needs_normalize = first_close > 100

    bars = []
    for i, (_, row) in enumerate(df.iterrows()):
        dt = row['_dt']
        if pd.isna(dt) or not isinstance(dt, datetime):
            dt = pd.Timestamp(dt).to_pydatetime() if not pd.isna(dt) else datetime.now()

        _open = float(row.get('open', row.get('Open', 0)))
        _close = float(row.get('close', row.get('Close', 0)))
        _high = float(row.get('high', row.get('High', 0)))
        _low = float(row.get('low', row.get('Low', 0)))

        if needs_normalize:
            _open /= factor
            _close /= factor
            _high /= factor
            _low /= factor

        bars.append(RawBar(
            symbol=symbol,
            id=i,
            dt=dt,
            freq=freq,
            open=_open,
            close=_close,
            high=_high,
            low=_low,
            vol=float(row.get('vol', row.get('volume', row.get('Volume', 0)))),
            amount=float(row.get('amount', row.get('Amount', 0))),
        ))
    return bars

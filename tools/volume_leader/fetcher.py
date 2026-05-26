"""双通道数据获取：pytdx 主通道 + 本地 .lc5 备用"""
from datetime import datetime, date
import pandas as pd

try:
    from pytdx.hq import TdxHq_API
    from pytdx.reader import TdxMinBarReader
    PYTDX_AVAILABLE = True
except ImportError:
    PYTDX_AVAILABLE = False
    TdxHq_API = None
    TdxMinBarReader = None

from tools.volume_leader.shared import (
    PYTDX_HOST, PYTDX_PORT, TDX_VIPDOC, MIN_PRICE_FACTOR
)

TODAY = date.today()


def _is_today(ts):
    """判断时间戳是否是今天"""
    try:
        if isinstance(ts, str):
            # pytdx to_df() 返回字符串格式: '2026-05-25 14:15'
            d = ts[:10].replace('-', '')
            return d == TODAY.strftime('%Y%m%d')
        if isinstance(ts, pd.Timestamp):
            return ts.date() == TODAY
        if isinstance(ts, (int, float)):
            s = str(int(ts))
            if len(s) >= 8:
                return s[:8] == TODAY.strftime('%Y%m%d')
    except Exception:
        pass
    return False


def fetch_5min_pytdx(market, code6):
    """主通道：pytdx 拉 5 分钟线，仅返回今天的 bar"""
    if not PYTDX_AVAILABLE:
        return None
    api = TdxHq_API()
    try:
        if not api.connect(PYTDX_HOST, PYTDX_PORT):
            return None
        data = api.get_security_bars(0, market, code6, 0, 800)
        if not data:
            return None
        df = api.to_df(data)
        if df is None or len(df) == 0:
            return None
        api.disconnect()

        df = df[df['datetime'].apply(_is_today)].copy()
        if len(df) == 0:
            return None

        df['timestamp'] = df['datetime'].apply(
            lambda t: int(t.strftime('%Y%m%d%H%M')) if isinstance(t, pd.Timestamp) else int(t.replace('-', '').replace(' ', '').replace(':', '')[:12])
        )
        df['date'] = df['datetime'].apply(
            lambda t: int(t.strftime('%Y%m%d')) if isinstance(t, pd.Timestamp) else int(t.replace('-', '')[:8])
        )
        # pytdx API 返回的价格已经是元，不需要除因子
        df['open'] = df['open'].astype(float).round(4)
        df['high'] = df['high'].astype(float).round(4)
        df['low'] = df['low'].astype(float).round(4)
        df['close'] = df['close'].astype(float).round(4)
        df['volume'] = (df['vol'] if 'vol' in df.columns else df['volume']).astype(float)
        df['amount'] = df['amount'].astype(float)

        return df[['timestamp', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount']]
    except Exception as e:
        print(f'  [pytdx] {market}{code6} 失败: {e}')
        try:
            api.disconnect()
        except Exception:
            pass
        return None


def fetch_5min_lc5(market, code6):
    """备用通道：读本地通达信 .lc5 文件"""
    try:
        path = TDX_VIPDOC / ('sz' if market == 0 else 'sh') / 'fzline' / f'{"sz" if market == 0 else "sh"}{code6}.lc5'
        if not path.exists():
            return None
        r = TdxMinBarReader()
        df = r.get_df(str(path))
        if df is None or len(df) == 0:
            return None

        df = df[df.index.to_series().apply(_is_today)].copy()
        if len(df) == 0:
            return None

        df['timestamp'] = df.index.to_series().apply(
            lambda t: int(t.strftime('%Y%m%d%H%M'))
        )
        df['date'] = df.index.to_series().apply(
            lambda t: int(t.strftime('%Y%m%d'))
        )
        # LC5 价格格式可能不标准，不除以 MIN_PRICE_FACTOR
        df['open'] = df['open'].astype(float).round(4)
        df['high'] = df['high'].astype(float).round(4)
        df['low'] = df['low'].astype(float).round(4)
        df['close'] = df['close'].astype(float).round(4)
        df['volume'] = df['volume'].astype(float)
        df['amount'] = df['amount'].astype(float)

        return df[['timestamp', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount']]
    except Exception as e:
        print(f'  [lc5] {market}{code6} 失败: {e}')
        return None


def fetch_today_5min(code):
    """获取指定标的的今日5分钟数据，先 pytdx 后 lc5"""
    from tools.volume_leader.shared import code_to_market
    market, code6, _ = code_to_market(code)

    df = fetch_5min_pytdx(market, code6)
    if df is not None and len(df) > 0:
        return df

    df = fetch_5min_lc5(market, code6)
    if df is not None and len(df) > 0:
        return df

    return None

# -*- coding: utf-8 -*-
"""
pytdx API 封装工具
- 多服务器自动切换
- 统一接口拉取任意周期 K 线
- 缓存最近可用服务器，优先尝试

用法:
    from tdx_fetch import fetch_bars, fetch_last_bars, list_servers

    # 拉最近100根 60min
    bars = fetch_bars('159740', '60m', count=100)

    # 拉最近200根日线
    bars = fetch_bars('159740', 'day', count=200)

    # 拉指定日期范围
    bars = fetch_bars('159740', '15m', start_date='2026-04-01')
"""

import sys
import io

try:
    from pytdx.hq import TdxHq_API
    HAS_PYTDX = True
except ImportError:
    HAS_PYTDX = False
    print("[WARN] pytdx 未安装，tdx_fetch 将无法工作")

from datetime import datetime

# ============================================================
# 配置
# ============================================================

# 服务器列表（优先级从高到低）
SERVERS = [
    ('180.153.18.170', 7709),   # 今天验证可用
    ('119.147.212.81', 7709),
    ('112.74.214.43', 7721),
    ('59.173.18.140', 7709),
    ('101.227.73.20', 7709),
    ('101.227.77.254', 7709),
    ('14.215.128.18', 7709),
    ('47.103.48.45', 7709),
]

# 周期映射: 用户友好名称 → pytdx category
PERIOD_MAP = {
    '1min': 0, '5min': 1, '15min': 2, '30min': 3,
    '60min': 4, 'day': 5, 'week': 6, 'month': 9,
    # 简写
    '1m': 0, '5m': 1, '15m': 2, '30m': 3, '60m': 4,
}

# 市场映射
MARKET_MAP = {
    'sz': 0, 'sh': 1,
    '0': 0, '1': 1,  # 也支持数字
}


# ============================================================
# 状态
# ============================================================

_last_ok_server = None  # 上次成功的服务器


# ============================================================
# 核心函数
# ============================================================

def _connect_api():
    """
    连接 pytdx 服务器（自动切换）
    返回 (api, server_info) 或 (None, None)
    """
    global _last_ok_server

    if not HAS_PYTDX:
        return None, None

    api = TdxHq_API()

    # 1. 优先试上次成功的服务器
    if _last_ok_server:
        try:
            if api.connect(_last_ok_server[0], _last_ok_server[1]):
                return api, _last_ok_server
        except OSError:
            pass
        try:
            api.disconnect()
        except OSError:
            pass
        api = TdxHq_API()

    # 2. 遍历所有服务器
    for svr in SERVERS:
        try:
            if api.connect(svr[0], svr[1]):
                _last_ok_server = svr
                return api, svr
        except OSError:
            continue
        try:
            api.disconnect()
        except OSError:
            pass
        api = TdxHq_API()

    return None, None


def fetch_bars(code, period='day', market='sz', count=800):
    """
    拉取 K 线数据（返回从旧到新的时间正序列表）

    参数:
        code: 股票代码，如 '159740' 或 '000001'
        period: 周期名，如 '1min'/'5min'/'15min'/'30min'/'60min'/'day'
                 或简写 '1m'/'5m'/'15m'/'30m'/'60m'
        market: 市场 'sz'(深圳) / 'sh'(上海)
        count: 最多拉取根数

    返回:
        list[dict], 每个元素:
        {
            'datetime': datetime对象,
            'open': float,
            'high': float,
            'low': float,
            'close': float,
            'amount': float,
            'volume': int,
        }
        失败时返回空列表 []
    """
    cat = PERIOD_MAP.get(period)
    if cat is None:
        print('[ERROR] 不支持的周期: %s，可选: %s' % (period, ', '.join(PERIOD_MAP.keys())))
        return []

    mk = MARKET_MAP.get(str(market).lower(), MARKET_MAP.get(market))
    if mk is None:
        print('[ERROR] 不支持的市场: %s，可选: sz/sh' % market)
        return []

    api, svr = _connect_api()
    if not api:
        print('[ERROR] 无法连接任何服务器')
        return []

    try:
        page_size = min(800, count)
        pages_needed = (count + page_size - 1) // page_size
        all_pages = []

        for p in range(pages_needed):
            data = api.get_security_bars(cat, mk, str(code), p * page_size, page_size)
            if not data:
                break
            all_pages.append(data)

        api.disconnect()

        # 合并所有页：pytdx 每页内时间正序(旧→新)，page0 最老
        # 直接拼接即可得到时间正序
        result = []
        for page_data in all_pages:
            for bar in page_data:
                dt_str = str(bar['datetime'])
                try:
                    dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M')
                except ValueError:
                    try:
                        dt = datetime.strptime(dt_str, '%Y-%m-%d')
                    except ValueError:
                        continue
                result.append({
                    'datetime': dt,
                    'open': float(bar['open']),
                    'high': float(bar['high']),
                    'low': float(bar['low']),
                    'close': float(bar['close']),
                    'amount': float(bar.get('amount', 0)),
                    'volume': int(bar.get('vol', 0)),
                })

        if svr:
            print('[OK] %s:%d → %s 条数据 [%s %s]' % (svr[0], svr[1], len(result), code, period))

        return result

    except Exception as e:
        print('[ERROR] 获取数据失败: %s' % e)
        try:
            api.disconnect()
        except OSError:
            pass
        return []


def fetch_last_n(code, period='day', market='sz', n=10):
    """只取最后 N 根（最新），省流量"""
    return fetch_bars(code, period, market, count=n)


def test_connection():
    """测试所有服务器连通性"""
    results = []
    for ip, port in SERVERS:
        api = TdxHq_API()
        ok = False
        latency_ms = -1
        try:
            from time import time
            t0 = time()
            if api.connect(ip, port):
                # 试拉一根数据验证真实可用
                data = api.get_security_bars(5, 0, '159740', 0, 1)
                if data:
                    ok = True
                api.disconnect()
            latency_ms = int((time() - t0) * 1000)
        except Exception as e:
            try:
                api.disconnect()
            except OSError:
                pass
        status = '✓ OK' if ok else '✗ FAIL'
        results.append((ip, port, status, latency_ms))

    print('\npytdx 服务器状态:')
    print('%-20s %-6s %8s  %s' % ('地址', '端口', '延迟(ms)', '状态'))
    print('-' * 50)
    for ip, port, status, ms in results:
        lat = '%d' % ms if ms >= 0 else '-'
        print('%-20s %-6s %8s  %s' % (ip, port, lat, status))

    return results


def list_servers():
    """列出已配置的服务器"""
    return SERVERS


def get_last_server():
    """获取上次成功连接的服务器"""
    return _last_ok_server


# ============================================================
# CLI 入口（直接运行时测试用）
# ============================================================

if __name__ == '__main__':
    print('=' * 55)
    print('tdx_fetch.py 测试')
    print('=' * 55)

    # 测试连通性
    test_connection()

    # 测试拉取
    print('\n--- 拉取 159740 最近 5 根 60min ---')
    bars = fetch_bars('159740', '60m', count=5)
    if bars:
        for b in bars:
            print('  %s  O=%.3f H=%.3f L=%.3f C=%.3f' % (
                b['datetime'].strftime('%Y-%m-%d %H:%M'),
                b['open'], b['high'], b['low'], b['close']))

    print('\n--- 拉取 159740 最近 5 根 15min ---')
    bars15 = fetch_bars('159740', '15m', count=10)
    if bars15:
        for b in bars15:
            print('  %s  O=%.3f H=%.3f L=%.3f C=%.3f' % (
                b['datetime'].strftime('%Y-%m-%d %H:%M'),
                b['open'], b['high'], b['low'], b['close']))

    print('\n--- 拉取 520600 最近 3 根 日线 ---')
    bard = fetch_bars('520600', 'day', market='sh', count=3)
    if bard:
        for b in bard:
            print('  %s  O=%.3f H=%.3f L=%.3f C=%.3f' % (
                b['datetime'].strftime('%Y-%m-%d'),
                b['open'], b['high'], b['low'], b['close']))

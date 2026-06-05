"""
共享过滤原语 — 入场/出场过滤的原子检查函数

所有函数都是纯函数（无副作用、无CSV加载、无缓存），只做单bar或上下文判断。
CSV加载和缓存逻辑由各调用方自行管理。

使用方式:
    from tools.volume_leader.filter_engine import (
        has_star_buy, has_star_sell,
        check_ma_chain, check_expma_golden,
        check_no_recent_death, check_no_recent_golden,
        check_close_below_ma, check_pe_gate,
    )
"""

# ══════════════════════════════════════════════
# 信号检测 (单bar)
# ══════════════════════════════════════════════

def has_star_buy(bar):
    """★买信号"""
    return bool((bar.get('buy_signal', '') or '').strip())

def has_star_sell(bar):
    """★卖信号"""
    return bool((bar.get('sell_signal', '') or '').strip())

def has_golden(bar):
    """EXPMA金叉"""
    return (bar.get('expma_cross', '') or '').strip() == '金叉'

def has_death(bar):
    """EXPMA死叉"""
    return (bar.get('expma_cross', '') or '').strip() == '死叉'

def has_cci_top_divergence(bar):
    """CCI顶背驰"""
    return (bar.get('cci_divergence', '') or '').strip() == '顶背驰'


# ══════════════════════════════════════════════
# 均线/价格判断 (单bar)
# ══════════════════════════════════════════════

def check_ma_chain(bar, fast='ma5', mid='ma10', slow='ma20'):
    """MA快>中>慢 链式排列"""
    try:
        return float(bar.get(fast, 0)) > float(bar.get(mid, 0)) > float(bar.get(slow, 0))
    except (ValueError, TypeError):
        return False

def check_expma_golden(bar):
    """EXPMA12 > EXPMA50 金叉状态"""
    try:
        return float(bar.get('expma12', 0) or 0) > float(bar.get('expma50', 0) or 0)
    except (ValueError, TypeError):
        return False

def check_close_below_ma(bar, ma_col='ma5'):
    """收盘价 < 均线"""
    try:
        return float(bar['close']) < float(bar.get(ma_col, 0))
    except (ValueError, TypeError):
        return False

def check_close_above_ma(bar, ma_col='expma50'):
    """收盘价 > 均线（黄线检查用）"""
    try:
        c = float(bar.get('close', 0) or 0)
        ma = float(bar.get(ma_col, 0) or 0)
        return ma > 0 and c > ma
    except (ValueError, TypeError):
        return False

def check_close_below_ma_generic(bar, ma_col='expma50'):
    """收盘价 < 均线（卖侧黄线检查用）"""
    try:
        c = float(bar.get('close', 0) or 0)
        ma = float(bar.get(ma_col, 0) or 0)
        return ma > 0 and c < ma
    except (ValueError, TypeError):
        return False


# ══════════════════════════════════════════════
# PE门禁 (单bar)
# ══════════════════════════════════════════════

def check_pe_gate(bar):
    """PE非升熵: pe_chg_5 >= -0.02。无数据时放行"""
    try:
        pe_chg = float(bar.get('pe_chg_5', 0) or 0)
        return pe_chg >= -0.02
    except (ValueError, TypeError):
        return True


# ══════════════════════════════════════════════
# 上下文检查 (需要周围bar)
# ══════════════════════════════════════════════

def check_no_recent_death(rows, idx, window=20):
    """最近window根内，最后一个expma_cross事件不是死叉。
    从idx-1向前扫描，遇死叉→False，遇金叉→停止扫描→True。
    """
    for j in range(idx - 1, max(idx - window - 1, 0), -1):
        cross = (rows[j].get('expma_cross', '') or '').strip()
        if cross == '死叉':
            return False
        if cross == '金叉':
            return True
    return True

def check_no_recent_golden(rows, idx, window=20):
    """最近window根内，最后一个expma_cross事件不是金叉。
    从idx-1向前扫描，遇金叉→False，遇死叉→停止扫描→True。
    """
    for j in range(idx - 1, max(idx - window - 1, 0), -1):
        cross = (rows[j].get('expma_cross', '') or '').strip()
        if cross == '金叉':
            return False
        if cross == '死叉':
            return True
    return True

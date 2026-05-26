# -*- coding: utf-8 -*-
"""
机构建仓指标 v2 — 真实筹码版

基于 chip_loader 的真实筹码分布数据（data/chips/）精确计算 WINNER 函数，
替代之前的价格近似算法。

通达信原始公式:
    AA:=WINNER(CLOSE+15%)*100;
    BB:=WINNER(CLOSE)*100;
    CC:=AA-BB;                    -- 上方筹码少=锁仓
    DD:=(WINNER(C-0.1%)-WINNER(C-15%))*100;  -- 下方筹码稳定性
    EE:=CC<3 AND DD<0.5 AND OPEN<>LOW;         -- 极端平衡

    A1:=DYNAINFO(6);             -- 当日均价(VWAP)
    A2:=IF(LOW>A1,0,IF(HIGH<A1,1,(A1-LOW+0.01)/(HIGH-LOW+0.01)));  -- ✅已修正
    A3:=VOL/WINNER(LOW);          -- 量能效率(核心!)
    A4:=REF(HHV(CLOSE,120),1);   -- 120日最高收盘价  ✅已修正(原用HIGH)
    A5:=REF(LLV(CLOSE,120),1);   -- 120日最低收盘价  ✅已修正(原用LOW)
    A6:=100*(CLOSE-A5)/(A4-A5);   -- 价格位置%
    A7:=A2>0 OR A2=1;
    A8:=A7 AND A3/REF(A3,3)>=3 AND A6<80;
    A9:=REF(A8,1..4) 过去4天有A8;
    A10:=A8 AND A9;               -- 连续性确认
    A11:=FILTER(A8,3)  → 青色(进场)
    A12:=FILTER(A10,3) OR EE → 红色(加仓)

信号:
    青色(A11) = 进场信号 = 0.5组
    红色(A12) = 加仓信号 = 0.5组
    青+红 = 1组完整信号

用法:
    from jigou_jiancang import JigouJianCang
    jjc = JigouJianCang(chip_dir='D:/quantify-per/data/chips')

    # 计算单只股票多日指标
    result = jjc.calculate('sh600438', df_kline)
    # df_kline: DataFrame with columns [date, open, high, low, close, volume]
    # 返回: DataFrame 新增列 jg_entry / jg_add / signal_groups / ...

    # 信号摘要
    summary = jjc.signal_summary(result)
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# 确保 quantify-per 目录可导入
sys.path.insert(0, str(Path(__file__).parent))

from chip_loader import ChipLoader


class JigouJianCang:
    """机构建仓指标 — 基于真实筹码分布的精确计算"""

    def __init__(self, chip_dir=None):
        """
        Args:
            chip_dir: 筹码数据根目录，默认 D:/quantify-per/data/chips
        """
        self.chip_loader = ChipLoader(base_dir=chip_dir)

    # ==================== 核心: WINNER 函数 ====================

    @staticmethod
    def winner(price, distribution):
        """
        计算 WINNER(price) — 某价位处的获利盘比例

        通达信定义: 在当前价格下，所有持仓成本 <= 该价格的筹码占总筹码的比例。
        即：价格低于等于 target_price 的所有筹码百分比之和。

        Args:
            price: 目标价格 (float)
            distribution: sorted list of (cost_price, pct) — 筹码分布

        Returns:
            float: 获利盘比例 (0~100)，如果 distribution 为空返回 None
        """
        if not distribution or not isinstance(distribution, list):
            return None

        # 价格 <= 目标价的所有筹码占比之和
        profit_pct = sum(pct for cost_price, pct in distribution if cost_price <= price)
        return round(profit_pct, 2)

    # ==================== 子条件计算 ====================

    def calc_cc_dd_ee(self, row, distribution):
        """
        计算筹码平衡条件组 CC/DD/EE

        CC = WINNER(C+15%) - WINNER(C)     — 上方浮动筹码量（锁仓程度）
        DD = WINNER(C-0.1%) - WINNER(C-15%) — 下方密集筹码量（支撑厚度）
        EE = CC < 3 且 DD < 0.5 且 OPEN != LOW — 极端多空平衡

        Returns:
            dict: {cc, dd, ee}
        """
        c = row['close']

        w_c_up15   = self.winner(c * 1.15, distribution)   # WINNER(C+15%)
        w_c       = self.winner(c, distribution)           # WINNER(C)
        w_c_d01   = self.winner(c * 0.999, distribution)  # WINNER(C-0.1%)
        w_c_d15   = self.winner(c * 0.85, distribution)   # WINNER(C-15%)

        if any(v is None for v in [w_c_up15, w_c, w_c_d01, w_c_d15]):
            return {'cc': None, 'dd': None, 'ee': False}

        cc = w_c_up15 - w_c if w_c_up15 else 0       # 上方浮筹
        dd = (w_c_d01 - w_c_d15) if w_c_d01 else 0   # 下方密筹
        ee = (cc < 3) and (dd < 0.5) and (row['open'] != row['low'])

        return {
            'cc': round(cc, 2),
            'dd': round(dd, 2),
            'ee': ee,
            'w_c': w_c,
            'w_c_up15': w_c_up15,
        }

    def calc_a3(self, row, distribution):
        """
        计算 A3 = VOL / WINNER(LOW) — 量能效率

        含义: 以当前成交量去"填满"低价位获利盘需要多少倍量。
        WINNER(LOW) 越大(低位筹码越多)，同样成交量下A3越小；
        如果突然放大到前3日的3倍以上 → 量能突变。

        Returns:
            float or None
        """
        low = row['low']
        vol = row['volume']
        w_low = self.winner(low, distribution)

        if w_low is None or w_low <= 0:
            return None

        a3 = vol / w_low
        return round(a3, 2)

    def calc_a2_a6(self, row, vwap=None):
        """
        计算价格位置相关条件

        A2 (通达信原始):
            A1:=DYNAINFO(6);  -- 当日均价(VWAP)
            A2:=IF(LOW>A1,0,
                   IF(HIGH<A1,1,
                     (A1-LOW+0.01)/(HIGH-LOW+0.01)));
            含义：均价在当日振幅中的位置
              =0   → 全天在均价上方(强势)
              =1   → 全天在均价下方(弱势)
              0~1  → 均价的相对位置

        A6 = 当前价在过去120日区间的位置% (0~100)
             = 100 * (CLOSE - A5) / (A4 - A5 + 0.001)

        Args:
            row: 当日K线行
            vwap: 当日加权均价 (DYNAINFO(6) 近似 = amount/volume)

        Returns:
            dict: {a2, a6} 或 {a2, a6, a4, a5} 如有历史数据
        """
        o, h, l, c = row['open'], row['high'], row['low'], row['close']

        # A2 使用 VWAP (DYNAINFO(6))，而非 CLOSE
        if vwap is not None and not __import__('numpy').isnan(vwap):
            if l >= vwap:
                a2 = 0.0    # 最低价 ≥ 均价 → 全天强势
            elif h <= vwap:
                a2 = 1.0    # 最高价 ≤ 均价 → 全天弱势
            else:
                a2 = (vwap - l + 0.01) / (h - l + 0.01)
        else:
            # fallback: 没有 VWAP 数据时用 CLOSE 近似
            a2 = (c - l) / (h - l + 0.001) if (h - l) > 0 else 0.5

        result = {'a2': round(a2, 4)}

        # A6 需要历史窗口——这里先算基础值，完整版本需要在 calculate() 中滚动计算
        if 'a4' in row and 'a5' in row:
            a4 = row['a4']  # 120日最高
            a5 = row['a5']  # 120日最低
            a6 = 100 * (c - a5) / (a4 - a5 + 0.001)
            result['a6'] = round(a6, 2)
            result['a4'] = a4
            result['a5'] = a5

        return result

    # ==================== 主计算引擎 ====================

    def calculate(self, code, df, date_col='date'):
        """
        计算某股票全周期的机构建仓指标

        Args:
            code: 股票代码 (如 'sh600438')
            df: K线 DataFrame，需包含以下列:
                date, open, high, low, close, volume
                (volume 为手数或股数均可，只要单位一致)
            date_col: 日期列名

        Returns:
            DataFrame: 新增以下列:
                - jg_winner:      WINNER(CLOSE) 获利盘%
                - jg_cc:          上方浮筹量
                - jg_dd:          下方密筹量
                - jg_ee:          极端平衡标志
                - jg_a3:          量能效率
                - jg_a2:          当日价格位置
                - jg_a6:          120日价格位置%
                - jg_a8:          核心触发条件
                - jg_a9:          连续性条件
                - jg_a10:         连续确认
                - jg_a11:         青色=进场信号 (bool)
                - jg_a12:         红色=加仓信号 (bool)
                - jg_signal_groups: 信号组计数 (0.5/1.0/1.5...)
                - jg_signal_text:  信号文字描述
        """
        import pandas as pd
        import numpy as np

        # 确保是副本
        result = df.copy()

        # ---- 预计算 120 日高低点 (A4/A5) ----
        # ATTENTION: HHV/LLV 用的是 CLOSE (收盘价)，不是 HIGH/LOW!
        # 通达信: A4 = REF(HHV(CLOSE,120),1), A5 = REF(LLV(CLOSE,120),1)
        result['a4'] = result['close'].rolling(120, min_periods=1).max().shift(1)
        result['a5'] = result['close'].rolling(120, min_periods=1).min().shift(1)

        # ---- 初始化输出列 ----
        n = len(result)
        result['jg_winner'] = np.full(n, np.nan)
        result['jg_cc'] = np.full(n, np.nan)
        result['jg_dd'] = np.full(n, np.nan)
        result['jg_ee'] = np.full(n, False)
        result['jg_a3'] = np.full(n, np.nan)
        result['jg_a2'] = np.full(n, np.nan)
        result['jg_a6'] = np.full(n, np.nan)
        result['jg_a8'] = np.full(n, False)
        result['jg_a9'] = np.full(n, False)
        result['jg_a10'] = np.full(n, False)
        result['jg_a11'] = np.full(n, False)  # 青色=进场
        result['jg_a12'] = np.full(n, False)  # 红色=加仓
        result['jg_signal_groups'] = np.full(n, 0.0)
        result['jg_signal_text'] = ''

        print(f'[机构建仓] 开始计算 {code}, 共 {n} 个交易日...')

        # ---- 逐行计算 ----
        for i in range(n):
            row = result.iloc[i]
            date_str = str(row[date_col]).replace('-', '')[:8]

            # 获取当日筹码分布
            dist = self.chip_loader.get_distribution(code, date_str)
            if dist is None:
                continue

            # 1) WINNER(C) — 基础获利盘
            w_c = self.winner(row['close'], dist)
            if w_c is not None:
                result.at[result.index[i], 'jg_winner'] = w_c

            # 2) CC/DD/EE — 筹码平衡条件
            cond = self.calc_cc_dd_ee(row, dist)
            if cond.get('cc') is not None:
                result.at[result.index[i], 'jg_cc'] = cond['cc']
                result.at[result.index[i], 'jg_dd'] = cond['dd']
                result.at[result.index[i], 'jg_ee'] = cond['ee']

            # 3) A3 — 量能效率
            a3 = self.calc_a3(row, dist)
            if a3 is not None:
                result.at[result.index[i], 'jg_a3'] = a3

            # 4) A2/A6 — 价格位置
            # 计算 VWAP (DYNAINFO(6) 近似) = 成交额 / 成交量(手*100)
            vwap = None
            if 'amount' in result.columns:
                vol_hand = row.get('volume', 0)
                amt = row.get('amount', 0)
                if vol_hand > 0 and not np.isnan(vol_hand) and not np.isnan(amt):
                    vwap = amt / (vol_hand * 100)
            pos = self.calc_a2_a6(row, vwap=vwap)
            result.at[result.index[i], 'jg_a2'] = pos.get('a2', np.nan)
            if 'a6' in pos:
                result.at[result.index[i], 'jg_a6'] = pos['a6']

            # ---- 组合条件 A8-A12 ----
            # A7: A2 > 0 或 A2 == 1 (即不是收在最低价的极端弱势)
            a2_val = result.at[result.index[i], 'jg_a2']
            a7 = (a2_val > 0) if not np.isnan(a2_val) else False
            if abs(a2_val - 1.0) < 0.001:
                a7 = True

            # A8 = A7 AND A3/REF(A3,3) >= 3 AND A6 < 80
            a3_val = result.at[result.index[i], 'jg_a3']
            a6_val = result.at[result.index[i], 'jg_a6']

            a8 = False
            if a7 and not np.isnan(a3_val) and i >= 3:
                ref_a3 = result.at[result.index[i - 3], 'jg_a3']
                if not np.isnan(ref_a3) and ref_a3 > 0:
                    a3_ratio = a3_val / ref_a3
                    a6_ok = np.isnan(a6_val) or a6_val < 80
                    a8 = bool(a3_ratio >= 3.0 and a6_ok)

            result.at[result.index[i], 'jg_a8'] = a8

            # A9 = 过去 4 天内有任意一天出现 A8
            a9 = False
            if i >= 1:
                a9 = any(result.at[result.index[j], 'jg_a8'] for j in range(max(0, i - 4), i))
            result.at[result.index[i], 'jg_a9'] = a9

            # A10 = A8 AND A9
            a10 = bool(a8 and a9)
            result.at[result.index[i], 'jg_a10'] = a10

            # A11 = FILTER(A8, 3) — 每3天最多一次
            # 实现: 当前有A8，且过去2天没有A8
            a11 = bool(
                a8
                and (i < 1 or not result.at[result.index[i - 1], 'jg_a8'])
                and (i < 2 or not result.at[result.index[i - 2], 'jg_a8'])
            )
            result.at[result.index[i], 'jg_a11'] = a11

            # A12 = FILTER(A10, 3) OR EE
            a12_filtered = bool(
                a10
                and (i < 1 or not result.at[result.index[i - 1], 'jg_a10'])
                and (i < 2 or not result.at[result.index[i - 2], 'jg_a10'])
            )
            ee_val = result.at[result.index[i], 'jg_ee']
            a12 = bool(a12_filtered or ee_val)
            result.at[result.index[i], 'jg_a12'] = a12

            # ---- 信号组计数 & 文字 ----
            groups = 0.0
            texts = []

            if a11:
                groups += 0.5
                texts.append('青(进场)')
            if a12:
                groups += 0.5
                texts.append('红(加仓)')

            result.at[result.index[i], 'jg_signal_groups'] = groups
            result.at[result.index[i], 'jg_signal_text'] = '+'.join(texts) if texts else ''

        total_signals = result['jg_signal_groups'].sum()
        entry_count = result['jg_a11'].sum()
        add_count = result['jg_a12'].sum()

        print(f'[机构建仓] 完成! '
              f'进场信号={int(entry_count)}次, 加仓信号={int(add_count)}次, '
              f'总信号组={total_signals:.1f}组')

        return result

    # ==================== 便捷方法 ====================

    def signal_summary(self, result_df, last_n=20):
        """
        生成最近 N 天的机构建仓信号摘要

        Returns:
            str: Markdown 格式摘要
        """
        recent = result_df.tail(last_n)
        lines = [
            '## 机构建仓信号摘要',
            '',
            '| 日期 | 收盘 | WINNER | CC | DD | A3 | A6 | 信号 |',
            '|------|------|--------|----|----|-----|-----|------|',
        ]

        for _, r in recent.iterrows():
            sig = r['jg_signal_text'] or '-'
            winner_s = f"{r['jg_winner']:.1f}%" if not __import__('numpy').isnan(r['jg_winner']) else '-'
            cc_s = f"{r['jg_cc']:.1f}" if not __import__('numpy').isnan(r['jg_cc']) else '-'
            dd_s = f"{r['jg_dd']:.2f}" if not __import__('numpy').isnan(r['jg_dd']) else '-'
            a3_s = f"{r['jg_a3']:.1f}" if not __import__('numpy').isnan(r['jg_a3']) else '-'
            a6_s = f"{r['jg_a6']:.0f}" if not __import__('numpy').isnan(r['jg_a6']) else '-'

            lines.append(
                f"| {str(r.get('date', r.name))[:10]} "
                f"| {r['close']:.2f} "
                f"| {winner_s} "
                f"| {cc_s} "
                f"| {dd_s} "
                f"| {a3_s} "
                f"| {a6_s} "
                f"| {sig} |"
            )

        # 统计
        total_groups = result_df['jg_signal_groups'].sum()
        lines.extend([
            '',
            f'**总信号组**: {total_groups:.1f} 组',
            f'**进场次数**: {int(result_df["jg_a11"].sum())}',
            f'**加仓次数**: {int(result_df["jg_a12"].sum())}',
            '',
            '**信号强度判断**:',
            f'- 最近20日信号组: {recent["jg_signal_groups"].sum():.1f} 组',
        ])

        # 趋势判断辅助
        last_close = result_df.iloc[-1]['close']
        first_close = result_df.head(min(20, len(result_df)))['close'].iloc[0]
        trend_pct = (last_close - first_close) / first_close * 100
        if trend_pct > 0:
            lines.append(f'- 近期趋势: 上涨 +{trend_pct:.1f}% → 上涨回调中，信号更可靠')
        else:
            lines.append(f'- 近期趋势: 下跌 {trend_pct:.1f}% → 下跌中，需更多信号才可信')

        return '\n'.join(lines)


# ============================================================
# CLI 测试入口
# ============================================================

if __name__ == '__main__':
    import sys
    from pytdx.reader import TdxDailyBarReader
    import pandas as pd
    from config import TDX_ROOT

    # 默认参数
    code = sys.argv[1] if len(sys.argv) > 1 else 'sh600438'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 60

    # 读取日线数据（路径从 config 自动检测）
    if code.startswith('sz'):
        market = 'sz'
        tdx_code = code[2:]
    else:
        market = 'sh'
        tdx_code = code[2:]

    day_file = os.path.join(TDX_ROOT, 'vipdoc', market, 'lday', f'{market}{tdx_code}.day')
    reader = TdxDailyBarReader()
    raw_df = reader.get_df(day_file)

    if raw_df is None or len(raw_df) == 0:
        print(f'错误: 无法读取 {day_file}')
        sys.exit(1)

    # 取最近 N 天
    df = raw_df.tail(days).copy()
    df.reset_index(drop=True, inplace=True)

    # 重命名列以匹配标准接口
    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if cl in ('open', 'high', 'low', 'close', 'volume'):
            col_map[c] = cl
    df.rename(columns=col_map, inplace=True)

    # 日期列处理
    if 'datetime' in df.columns:
        df['date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')
    elif 'date' not in df.columns:
        df['date'] = range(len(df))  # fallback

    # 计算机构建仓指标
    jjc = JigouJianCang()
    result = jjc.calculate(code, df)

    # 输出摘要
    print('\n' + '=' * 70)
    print(jjjc.signal_summary(result, last_n=min(30, len(result))))

    # 输出最近信号明细
    signals = result[result_df['jg_signal_groups'] > 0] if 'result_df' in dir() else \
             result[result['jg_signal_groups'] > 0]
    if len(signals) > 0:
        print('\n【信号明细】')
        print(signals[['date', 'close', 'jg_winner', 'jg_a3', 'jg_a6',
                        'jg_signal_groups', 'jg_signal_text']].to_string(index=False))

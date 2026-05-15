# -*- coding: utf-8 -*-
"""
周期循环引擎 v3.5 — Cycle Engine (三层架构版 + 完整共振链)

核心理念:
  不是先评分再做建议，而是先定位再评分。

  三层架构:
    第一层: 价格位置 — K线在 EXPMA 白线/黄线的什么位置？高位/中位/低位？
    第二层: 趋势方向 — 上涨/震荡/下跌？
    第三层: 循环适配 — 在已知位置+方向下，信号质量如何？

  共振链 (v3.5 新增):
    min5/min15 → min30/min60 (一级) — 同评分看上层金叉/死叉
    min30/min60 → daily (二级) — 同评分看日线金叉/死叉

  三个问题按顺序回答，每个标的状态自然浮现。

设计原则 (来自用户第一性原理):
  - 位置决定风险，方向决定策略，循环决定时机
  - 科创芯片: 高位加速区 + 上涨态 + 信号散乱 = 持有/减仓，不是买入
  - 恒生科技: 低位区 + 下跌态 + 买信号密集 = 触底酝酿，等转折信号
  - 不是循环好就值得操作，而是"位置+方向+循环"三者共振

作者: 小草 (EasyClaw) + WorkBuddy (v4 Pro)
日期: 2026-05-07 (WorkBuddy 共振链改造)
v3.6: 拆包为 cycle_engine/ 目录，本文件保留为 CLI 兼容薄壳
"""

import sys
from cycle_engine import analyze, analyze_all, format_report, save_results, get_name_map

if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    if len(sys.argv) > 1 and sys.argv[1] in ('--help', '-h', '/?'):
        print(__doc__)
        sys.exit(0)

    elif len(sys.argv) > 1 and sys.argv[1] == '--save':
        results = analyze_all()
        print(format_report(results))
        save_results(results)

    elif len(sys.argv) > 1:
        code = sys.argv[1]
        nm = get_name_map()
        result = analyze(code, nm.get(code, code))
        print(format_report([result]))

    else:
        results = analyze_all()
        print(format_report(results))

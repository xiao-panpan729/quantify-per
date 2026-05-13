# CLAUDE.md — quantify-per 量化交易信号系统

## 项目定位
12只标的（6 ETF + 6 个股）的趋势评分 + 多周期循环信号 + 回测 + 每日机会报告。

## 关键文件

| 文件 | 功能 |
|------|------|
| `update_from_tdx.py` | 从通达信同步日线+分钟线增量 |
| `cycle_engine.py` | **核心**：三层架构（价格位置→趋势方向→循环适配）+ 0-16趋势评分 + 信号质量递进分析 + **主导量级检测(波峰间距法)** |
| `backtest_signals.py` | 信号级回测引擎 |
| `gen_report_md.py` | Markdown 报告生成 |
| `scan_opportunities.py` | 机会扫描 + 每日报告 |

## 数据路径

```
C:\zd_cjzq\vipdoc\          ← 通达信数据源
  sh/lday/sh600438.day      ← 日线（价格×1000）
  sz/lday/sz002261.day
  sh/minline/sh600438.lc1   ← 1分钟线（价格×10000）
  sz/fzline/sz159740.lc5    ← 5分钟线
```

数据检查统一用 pytdx 直读：
```bash
# 日线
python -c "from pytdx.reader import TdxDailyBarReader; r=TdxDailyBarReader();
for mkt,code in [('sh','000001'),('sz','000001'),('sz','159740')]:
    p=f'C:/zd_cjzq/vipdoc/{mkt}/lday/{mkt}{code}.day'; df=r.get_df(p);
    print(f'{mkt}{code} 日线:{str(df.index[-1].date())} ({len(df)}条)')"
# 分钟线
python -c "from pytdx.reader import TdxMinBarReader; r=TdxMinBarReader();
for mkt,code in [('sz','159740'),('sh','000001')]:
    for ext,d in [('lc5','fzline'),('lc1','minline')]:
        p=f'C:/zd_cjzq/vipdoc/{mkt}/{d}/{mkt}{code}.{ext}'; df=r.get_df(p);
        print(f'{mkt}{code} {ext}:{str(df.index[-1])} ({len(df)}条)')"
```

## 12只跟踪标的

| 类型 | 代码 | 名称 |
|------|------|------|
| ETF | sz159740 | 恒生科技 |
| ETF | sh513120 | 创新药 |
| ETF | sz159326 | 电网设备 |
| ETF | sh513310 | 中韩半导体 |
| ETF | sh588200 | 科创芯片 |
| ETF | sh520600 | 汽车ETF |
| 个股 | sz002261 | 拓维信息 |
| 个股 | sz300118 | 东方日升 |
| 个股 | sz000100 | TCL科技 |
| 个股 | sz002129 | TCL中环 |
| 个股 | sh600438 | 通威股份 |
| 个股 | sh601012 | 隆基绿能 |

## 0-16 趋势评分体系（judge_trend）

| 维度 | 分值 | 说明 |
|------|------|------|
| EXPMA | 0~2 | 多头/粘合/空头 |
| MACD | 0~4 | 含0轴上下+金叉死叉区分 |
| MA排列 | 0~6 | **链式递进**：5→10→20→60→120→250 逐级检查，断裂即停。链长5(全顺)→6分满分 |
| 日线闭环 | 0~4 | buy_level>=4.0→4分, >=3.5→3分, >=3.0→2分 |

方向阈值：13-16上涨 / 10-12偏多 / 7-9中性 / 4-6偏空 / 0-3下跌

## 信号质量递进分析（signal_quality，5维）

**买侧**（sell侧镜像）：
1. ★买密集度（+0.5~1.5）
2. EXPMA金叉跟随速度（+0.3~1.5）
3. 底部价格抬升（+1.0）
4. 闭环成对（+0.3~1.0）
5. **MA5/10金叉确认**：★买后→MA5/10金叉→EXPMA金叉之前=+1.2（三级递进），无EXPMA时MA5/10金叉=+1.0（电网设备模式），在EXPMA之后=+0.3

## 关键数值
- 日线价格×1000，分钟线×10000
- lc60趋势线N=40（非55），日线N=55
- 15/30/60分钟由5分钟源数据合成

## 主导量级检测（detect_dominant_cycle，v3.6新增）
波峰间距法，从5分钟开始逐级向上检查 trend_line 的波峰间距：
- 间距稳定（当前/历史 < 1.5倍）→ 该级别是主导量级
- 间距拉长（>= 1.5倍）→ 上级周期在接管，继续向上查
- 主导量级与ABCD级别取高者作为实际最低操作级别
- 低于主导量级的周期反向信号自动降级忽略

函数：`_wave_peaks(values)` → `_peak_intervals(peaks)` → `detect_dominant_cycle(code, period_results)`

## 修改规则
- 任何代码改动：改前/改后代码片段写入 `C:\Users\Administrator\.claude\projects\C--Users-Administrator\archives\`
- 通达信格式：必须查 `D:\miniconda3\Lib\site-packages\pytdx\reader\` 源码，不猜

## 最近改动（2026-05-13）
1. MA均线排列：碎片计数 → 链式递进（`judge_trend` 292-319行）
2. signal_quality 新增 MA5/10 交叉检测 + 买侧/卖侧各加第5维（535-547行 / 628-653行 / 726-751行）
3. 日线闭环新增 buy_level>=3.5 → 3分"短期确认"档（326-328行）
4. **波峰间距法新增**：`_wave_peaks` / `_peak_intervals` / `detect_dominant_cycle` 三个函数，检测主导循环量级
5. `analyze()` 集成主导量级：`actual_min_idx = max(abcd_min_idx, dominant_idx)`，替代固定ABCD
6. `gen_report_md.py` 总览表新增"主导量级"列，深度分析追加主导量级描述

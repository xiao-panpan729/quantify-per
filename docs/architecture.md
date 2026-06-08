# architecture.md — quantify-per 系统架构

**更新**: 2026-06-08
**项目**: quantify-per 量化交易信号系统
**代码**: ~20,000 行 Python
**跟踪**: 14 只标的 (2 指数 + 6 ETF + 6 个股)

---

## 一、系统定位

quantify-per 是一个**基于通达信本地数据的多周期信号计算 + 宏观/基本面/情绪 overlay + AI 报告生成系统**。核心数据来源于通达信 `.day`（日线）和 `.lc5`（5分钟线）文件，通过 pytdx 读取。上层叠加了板块势能、宏观敏感性、全球流动性、US跨市场映射、基本面因子溢价、消息面突发事件检测等多维度分析层，最后通过信源聚合管道产出 AI 日报。

---

## 二、数据流

```
通达信盘后数据 (.day / .lc5)
    │
    ▼
update_from_tdx.py          ← 数据同步（增量/全量 + 15/30/60分钟合成）
    │
    ├─ data/{lday,one,five,fifteen,thirty,sixty}/{sz,sh}/.csv  ← CSV缓存
    │
    ▼
update_tracking.py           ← 14只标的全周期信号计算
    │   signal_engine.py     ← 指标公式库（47列: 30基础+11量能+3PE+2HHT+1周期）
    │
    ├─ signals/tracking/{code}/{period}_signals.csv
    │
    ▼  分叉为四条分析线
    │
    ├─▶ run_cycle.py         ← 三层架构：位置→趋势(0-14分)→循环
    │   └─ cycle_report.json + ABCD分级 + 操作建议
    │
    ├─▶ backtest_signals.py  ← 信号回测（低点合并/50%合并/★信号独立）
    │   └─ backtest_report.json + backtest_trades.db
    │
    ├─▶ hht_analyzer.py      ← HHT独立分析（EMD+瞬时频率+非预期解检测）
    │   └─ hht_report.json
    │
    ├─▶ gen_report_md.py     ← 每日信号报告 → reports/daily/
    │
    ▼  宏观/基本面 overlay 层（与信号数据独立运行）
    │
    ├─▶ tools/sector_momentum.py       ← 269概念板块X_1势能评分
    ├─▶ tools/macro_screener.py        ← 宏观分层过滤（动量×宏观overlay）
    ├─▶ tools/macro_sensitivity.py     ← 15+因子RollingOLS宏观敏感性
    ├─▶ tools/sentiment/shock_detector.py ← 三源冗余消息面突发事件
    ├─▶ tools/liquidity_monitor.py     ← 5因子全球流动性压力指数
    ├─▶ tools/japan_macro.py           ← 日本宏观+套息交易压力
    ├─▶ tools/us_market/               ← US→A股三层映射
    │   ├─ macro_sensitivity.py        ← Layer 1: US宏观→A股敏感度
    │   ├─ etf_momentum.py             ← Layer 2a: US ETF势能评分
    │   ├─ star_stocks.py              ← Layer 2b: US明星股动量
    │   ├─ concept_chains.py           ← 概念链引擎（30条链）
    │   └─ cross_mapping.py            ← Layer 3: 跨市场领先滞后映射
    ├─▶ tools/fundamental/             ← 基本面因子（Rolling FM）
    └─▶ gen_source_summary.py          ← ★信源AI日报（8公众号→聚合→分析）
```

**关键原则**：信号引擎只算一次，后续模块读取快照CSV不复算。宏观/基本面/情绪层与信号数据独立运行，可单独刷新。

---

## 三、模块关系

| 层级 | 模块 | 文件 | 职责 |
|:---|:---|:---|:---|
| **数据层** | 数据同步 | `update_from_tdx.py` | 通达信→CSV缓存，多周期合成 |
| | 信号引擎 | `signal_engine.py` | 47列指标：30基础+11量能+3PE+2HHT+1周期 |
| | 跟踪管理 | `update_tracking.py` | 增量/全量信号计算调度 |
| **分析层** | 周期分析 | `cycle_engine/` | 趋势评分0-14/ABCD分级/主导量级/缠论结构 |
| | 信号回测 | `backtest_signals.py` | 低点合并/50%合并/★信号独立回测 |
| | HHT分析 | `hht_analyzer.py` | EMD分解+瞬时频率+非预期解检测 |
| | 价格对齐 | `price_pe_align.py` | 价格阶段×PE轨迹25组合矩阵 |
| | 战役追踪 | `operation_tracker.py` | 开仓/持仓/平仓事件链 |
| **宏观层** | 板块势能 | `tools/sector_momentum.py` | 269概念板块X_1评分，通达信RPS对标 |
| | 宏观敏感性 | `tools/macro_sensitivity.py` | 15+因子RollingOLS，环境分类 |
| | 宏观筛选 | `tools/macro_screener.py` | 板块动量×宏观overlay TopN |
| | 流动性监控 | `tools/liquidity_monitor.py` | 5因子合成（BTC/VIX/DXY/M2/信用脉冲） |
| | 日本宏观 | `tools/japan_macro.py` | 套息交易压力，BOJ/USDJPY/CPI |
| | US市场映射 | `tools/us_market/` | 三层：ETF势能+明星股动量+跨市场映射 |
| **基本面层** | 基本面筛选 | `tools/fundamental_screener.py` | Rolling FM因子溢价 |
| | 数据层 | `tools/fundamental/data_layer.py` | 季频→日频转换+因子矩阵 |
| | FM流水线 | `tools/fundamental/fm_pipeline.py` | Fama-MacBeth滚动截面回归 |
| | 增长叙事 | `tools/fundamental/growth_narrative.py` | 营收/利润趋势+生命周期 |
| | CAPEX分析 | `tools/fundamental/capex_analyzer.py` | Type A/B资本开支分类 |
| **情绪层** | 突发事件 | `tools/sentiment/shock_detector.py` | 三源关键词匹配（WSC/东财/AI股评） |
| **信源层** | AI日报 | `gen_source_summary.py` | 8公众号聚合→摘要→分析报告 |
| **选股层** | 成交额筛选 | `tools/volume_leader_screener.py` | 三层梯队+动态宇宙管理 |
| | 量领信号 | `update_volume_leaders.py` | 6周期信号计算 |
| | 量领回测 | `tools/volume_leader/backtest.py` | 对比/配对/切换分析 |
| | 量领监控 | `tools/volume_leader/monitor.py` | 三级弹窗（MA/金叉/共振/减仓） |
| | 交易台账 | `tools/volume_leader/trade_db.py` | SQLite持仓/统计/历史 |
| | 因子归因 | `tools/volume_leader/factor_attribution.py` | 逐层贡献度分析 |
| **筹码层** | 机构建仓 | `jigou_jiancang.py` | WINNER真实筹码版 |
| | 关键K选股 | `chips_selector_v2.py` | 倍量+涨幅+筹码锁定 |
| **AI层** | AI分析 | `ai_analyzer.py` | 多API自动切换+交易框架注入 |
| **缠论层** | 缠论适配 | `notebook/chanlun/` | czsc 40+信号函数/双级别联立 |

---

## 四、核心数据结构

### 4.1 信号 CSV（47列）

| 类别 | 列 | 说明 |
|:---|:---|:---|
| **基础K线** | timestamp, date, open, high, low, close | 原始K线（价格×1000日线/×10000分钟线） |
| **均线** | expma12, expma50, ma5, ma10, ma20, ma60, ma120, ma250 | EXPMA+MA双系 |
| **MACD** | macd_dif, macd_dea, macd_hist | 标准MACD |
| **布林带** | bb_ma221, bb_red_line, red_line_cross | 布林带+红线 |
| **CCI** | cci, cci_extreme, cci_retreat, cci_divergence | 完整CCI闭环 |
| **信号** | buy_signal, sell_signal, expma_cross | 核心交易信号 |
| **分时出击** | trend_line | 趋势线 |
| **量能(11列)** | vol_ma5, vol_ma60, vr5, vr60, vol_llv100, vol_llv10, vol_堆, vol_缩50, vol_突放, vol_梯度升, vol_梯度降 | 地量/放量/梯度 |
| **排列熵(3列)** | pe, pe_level, pe_chg_5 | 60窗滚动排列熵 |
| **HHT(2列,仅日线)** | hht_freq, hht_amp | 瞬时频率/振幅 |
| **周期(1列,仅min30)** | cycle_period | 峰值间距均值 |

### 4.2 核心 JSON 输出

| 文件 | 用途 | 产出模块 |
|:---|:---|:---|
| `latest.json` | 14标的6周期最新信号快照 | update_tracking.py |
| `cycle_report.json` | 评分/方向/ABCD/操作建议 | run_cycle.py |
| `backtest_report.json` | 信号回测胜率/盈亏 | backtest_signals.py |
| `hht_report.json` | HHT频率/非预期解 | hht_analyzer.py |
| `sentiment_shock.json` | 消息面突发事件 | shock_detector.py |
| `liquidity_monitor.json` | 流动性压力指数 | liquidity_monitor.py |
| `japan_macro.json` | 日本宏观+套息 | japan_macro.py |
| `us_sector_momentum.json` | US ETF势能评分 | etf_momentum.py |
| `us_star_momentum.json` | US明星股动量 | star_stocks.py |
| `us_cn_mapping.json` | 跨市场映射 | cross_mapping.py |
| `sector_momentum_cache.json` | 板块势能缓存 | sector_momentum.py |
| `fundamental_profile.json` | 基本面因子溢价 | fundamental_screener.py |
| `operation_records.json` | 战役事件链 | operation_tracker.py |

---

## 五、趋势评分系统（0-14分）

| 维度 | 分值 | 逻辑 |
|:---|:---:|:---|
| MACD | 0-4 | 0轴锚定·位置+交叉解耦，6种状态 |
| MA排列 | 0-6 | 链式递进5→10→20→60→120→250 |
| 日线闭环 | 0-4 | 波段累积扣分制，含30/60共振 |

方向映射：13-14上涨 / 10-12偏多 / 7-9中性 / 4-6偏空 / 0-3下跌

### 两轴决策框架

| 轴 | 来源 | 回答的问题 |
|:---|:---|:---|
| 纵轴（操作级别） | macd_score 0-4 | 信号在哪个周期可信？→ ABCD |
| 横轴（环境建议） | total_score 0-14 | 现在该不该做？ |

### 操作级别（ABCD）

| 等级 | 条件 | 最小操作级别 |
|:---|:---|:---|
| A最强 | EXPMA白线上方 | 5分钟一信号 |
| B次强 | 白线-黄线区域 | 5分钟★买+2次金叉 |
| C偏弱 | 黄线下但MACD>0 | 15分钟★买+2次金叉 |
| D弱势 | MACD<0或死叉 | 不参与 |

---

## 六、信号质量递进（买侧7维）

1. ★买密集度(+0.5~1.5) → 2. EXPMA金叉跟随速度(+0.3~1.5) → 3. 底部抬升(+1.0) → 4. 闭环成对(+0.3~1.0) → 5. MA5/10金叉确认(+0.3~1.2) → 6. 排列熵结构突破(+1.0~1.5) → **7. 量能确认(+0.3~1.5)**

---

## 七、关键数值

```python
DAY_PRICE_FACTOR = 1000      # 日线: 原始值/1000
MIN_PRICE_FACTOR = 10000     # 分钟线: 原始值/10000
N_trend_daily = 55           # 日线LLV/HHV周期
N_trend_min_short = 40       # 5-15分钟线LLV/HHV周期
```

当前跟踪：sh000001 上证指数 / sz399006 创业板指 / sz159740 恒生科技 / sh520600 港股通汽车 / sh513120 创新药 / sz159326 电网设备 / sh513310 中韩半导体 / sh588200 科创芯片 / sz002261 拓维信息 / sz300118 东方日升 / sz000100 TCL科技 / sz002129 TCL中环 / sh600438 通威股份 / sh601012 隆基绿能

---

## 八、关键文件路径

| 内容 | 路径 |
|:---|:---|
| 通达信源 | `C:\zd_cjzq\vipdoc\` |
| 信号CSV | `signals/tracking/{code}/{period}_signals.csv` |
| 每日信号报告 | `reports/daily/YYYYMMDD_v3.md` |
| 信源日报 | `reports/sources/YYYYMMDD_sources.md` |
| 量领报告 | `reports/volume_leader/YYYYMMDD_volume_leader*.md` |
| 实验记录 | `tools/volume_leader/experiments/filter_evolution.md` |
| 研究日志 | `notebook/research_log.md` |
| 缠论适配层 | `notebook/chanlun/` |
| 记账本 | `notebook/notebook_design.md` |
| 筹码源 | `D:\筹码峰\` |
| pytdx源码 | `D:\miniconda3\Lib\site-packages\pytdx\reader\` |

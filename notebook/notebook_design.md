# 笔记本系统 — 项目设计说明书

> 预测卡 → 自动验证 → 案例检索 → 反馈修正
> 灵感来源: Vibe-Trading (HKU, 9.2K stars) — research-goal / shadow-account / strategy-generate
> 参考代码: `d:\tmp\vibe-trading\` (隔离克隆，不污染项目)

---

## 项目定位

### 我们要解决什么问题

现有量化系统（14只标的/47列信号/6周期/三级入场过滤）做的是**信号生成**。信号出来后，AI 分析、用户判断、实际交易 — 这三个环节是脱节的。

笔记本系统要补上的是**判断→验证→反馈**闭环：

```
信号生成 (已有)  →  预测卡 (本次新做)  →  自动验证 (本次新做)  →  案例检索 (本次新做)  →  反馈修正 (本次新做)
```

### 和现有系统的关系

```
d:\quantify-per\
├── update_from_tdx.py          ← 数据层（不改）
├── signal_engine.py            ← 指标计算（不改）
├── update_tracking.py          ← 信号生成（不改）
├── tools/volume_leader/        ← 量领子系统（不改）
│   └── experiments/
│       └── filter_evolution.md ← 实验报告（对标本文档）
├── notebook/                   ← ★新模块★
│   ├── notebook_design.md      ← 本文档（设计说明书）
│   ├── chanlun/                ← ★缠论结构定位层
│   │   ├── adapter.py          ← 数据格式转换（quantify-per → czsc RawBar）
│   │   ├── signals.py          ← 6合1信号提取（结构/中枢/买卖点/形态/笔状态/决策）
│   │   └── positions.py        ← 日线+30分钟双级别联立定位 + get_position()入口
│   ├── skills/                 ← 技能定义（每个技能一个 Python 类）
│   ├── cases/                  ← 案例库（SQLite，非文件系统）
│   └── ...
```

### 三条铁律

1. **只读主系统数据**：从 signals/tracking/ 读 CSV/JSON，不重复计算指标
2. **每项技能必须可验证**：不能是"我觉得主力在洗盘"这种主观判断
3. **技能从数据出发**：先问"现有指标能回答什么问题"，不先问"我想知道什么概念"

---

## Vibe-Trading 77 技能 — 完整分类参考

> 源码位置: `d:\tmp\vibe-trading\agent\src\skills\`
> 每个技能 = 一个 SKILL.md 文件（LLM 行为指南，非可执行代码）
> 我们的技能 = Python 类，必须跑出数值结果。这是本质差异。

### 13 大分类

| # | 大类 | 数量 | 代表技能 | 对我们有用？ |
|---|------|------|---------|------------|
| 1 | 数据源接入 | 10 | tushare/akshare/yfinance/ccxt/okx/mootdx/hk-connect-flow | ❌ 我们有 pytdx |
| 2 | 技术分析 | 8 | technical-basic/candlestick/chanlun/elliott-wave/harmonic/ichimoku/smc/minute-analysis | ⚠️ 部分已有代码 |
| 3 | 基本面 | 9 | financial-statement/earnings-forecast/valuation-model/dividend/credit/corporate-events/edgar | 🔮 以后可抄 |
| 4 | **策略生成** | **7** | **strategy-generate**/event-driven/pair-trading/cross-market/ml-strategy/pine-script/ashare-pre-st | **⭐⭐⭐ 核心** |
| 5 | 因子与Alpha | 3 | alpha-zoo(101+191+158因子库)/factor-research/multi-factor | ⚠️ 已有 signal_engine |
| 6 | 宏观 | 6 | macro-analysis/global-macro/geopolitical/sector-rotation/commodity/regulatory | 🔮 以后可抄 |
| 7 | **风险与绩效** | **7** | **shadow-account**/performance-attribution/backtest-diagnose/risk-analysis/hedging/correlation/quant-statistics | **⭐⭐⭐ 核心** |
| 8 | 情绪与行为 | 3 | sentiment-analysis/behavioral-finance/social-media-intelligence | 🔮 以后可抄 |
| 9 | 资产配置 | 6 | asset-allocation/etf-analysis/fund-analysis/convertible-bond/seasonal/execution-model | 🔮 以后可抄 |
| 10 | 期权衍生品 | 4 | options-strategy/options-advanced/options-payoff/volatility | ❌ 不玩 |
| 11 | 加密专属 | 7 | crypto-derivatives/defi-yield/liquidation-heatmap/onchain/perp-funding/stablecoin/token-unlock | ❌ 不玩 |
| 12 | **工具与流程** | **6** | **research-goal**/report-generate/trade-journal/doc-reader/web-reader/vnpy-export | **⭐⭐⭐ 最核心** |
| 13 | 市场微观结构 | 1 | market-microstructure | ⚠️ A股难用 |

### 三个直接决定笔记本形态的技能（详细拆解）

#### 1. research-goal（研究目标运行时）

定义了结构化预测的完整生命周期：

```
创建目标(3-5条验收标准) → 收集证据(绑定criterion_id) → 逐条审计 → 完成/驳回/证据不足
```

关键约束：
- 每条验收标准必须可被工具或数据验证（不能是主观判断）
- 证据必须可追溯（run_id / artifact_path / source_provider / data_as_of）
- 完成 ≠ "我觉得对了"，完成 = "每条标准都有验证过的证据行"

**→ 这就是预测卡的生命周期模型。**

#### 2. shadow-account（影子账户）

定义了"AI 判断 vs 实际执行"的偏差诊断框架：

```
用户真实交易 → 提取盈利规则(3-5条) → 影子按规则模拟 → 差值拆成5项归因
```

差值 5 项归因：
| 归因项 | 含义 |
|--------|------|
| noise_trades_pnl | 不命中任何规则的真实交易 = 情绪单 |
| early_exit_pnl | 赢的单子但卖早了 = 机会成本 |
| late_exit_pnl | 亏的单子但扛太久了 = 放大损失 |
| overtrading_pnl | 超出规则频率的交易 |
| missed_signals_pnl | 残差 |

**→ 我们不比"AI 预测对了多少"，而是比"预测卡 vs 实际交易，差值从哪来"。**

#### 3. strategy-generate（策略代码契约）

定义了统一的策略接口：

```python
class SignalEngine:
    def generate(self, data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
        """
        输入: code → OHLCV DataFrame
        输出: code → signal Series [-1.0, 1.0]
        """
```

**→ 每个技能都是一个实现 `generate()` 的类。新技能只写这个类，回测/验证框架复用。**

---

## 缠论结构定位层（新增基础层）

> 2026-06-01 新增。基于 czsc (waditu/czsc v0.10.12) 实现，保持 pip 依赖，不做源码复刻。

### 为什么需要缠论

笔记本系统的核心问题是：**描述"当前价格处于什么位置"时缺少标准化的结构语言。**

你现在的系统有 47 列指标，但这些指标回答的是"当前值是多少"，而不是"当前在走势结构的哪个阶段"。缠论提供了一个长在价格上的**图形数学框架**：

```
K线 → 包含处理 → 分型 → 笔 → 中枢 → 买卖点
```

每层有严格的数学定义，输出是标准化的结构描述，不是模糊判断。

### czsc 项目评估

| 项目 | 值 |
|------|-----|
| 仓库 | waditu/czsc (原 zengbin93/czsc) |
| 版本 | 0.10.12 (2026-03-08 构建)，仍在活跃更新 |
| 架构 | Rust + Python 混合（核心算法 Rust 编译为 _rs_czsc.pyd，44MB）|
| Python 代码量 | 100 文件 / 43,112 行 |
| 核心分析 | py/analyze.py + py/objects.py 约 2,000 行 |
| 信号函数 | signals/cxt.py 约 1,500 行（一/二/三买卖点多种实现）|
| 文档 | 作者明确表示暂不写文档，依赖源码阅读 |

### 实现链路 vs 缠论原文

| 层级 | czsc 实现 | 和缠论原文的差异 |
|------|----------|----------------|
| **包含处理** | `remove_include(k1,k2,k3)` 自适应方向 | 方向由相邻K线决定，不继承前一笔方向。实战中差别不大 |
| **分型** | `check_fx(k1,k2,k3)` high+low 双条件 | ✅ 完全符合原文 |
| **成笔** | 取极值顶/底分型配对 + min_bi_len=6(新笔标准) | 笔终点选"最高的顶"而非"第一个顶"，有动态固化逻辑（被破坏则删除笔合并回未完成池），工程简化但合理 |
| **线段** | **缺失** | 作者观点："没了分型、笔、线段，缠论还是缠论吗？如果答案是'是'，这个项目就是为你准备的。" |
| **中枢** | 直接由笔构建，zg=min(bis[:3].high) | 跳过线段层，用笔中枢替代。**30分钟及以上级别可用**，小级别不稳定 |
| **买卖点** | signals/cxt.py 完整覆盖 | 一买/一卖/二买/二卖/三买/三卖 + 多种类买卖点。但是**笔形态模式匹配**，不是严格的中枢递归定义 |

### 最关键的特性：多级别联立

`CzscTrader` 原生支持同时分析日线 + 30 分钟等多周期的笔结构。这对笔记本系统来说是最有价值的部分：

```
日线笔方向  →  决定大方向（向上/向下/中枢震荡）
30分钟买卖点 →  决定操作时机（是否出现买点/卖点区域）
```

### 适配方案

不复制 czsc 源码（43k 行 + 44MB Rust 二进制），保持 pip 依赖，写适配层：

```
notebook/chanlun/
├── __init__.py      ← 模块入口
├── adapter.py       ← quantify-per DataFrame → czsc RawBar 转换
├── signals.py       ← CZSC → 一/二/三类买卖点信号提取
└── positions.py     ← 日线+30分钟 双级别联立定位 + 标准化位置分类
```

适配后，笔记本系统的技能可以直接调用 `positions.dual_level_analysis()` 获取当前标的在缠论结构中的标准位置。

### czsc 信号函数完整目录

`czsc/signals/` 共 **15,000 行**信号函数，按功能分类如下：

#### 📍 cxt 模块 — CZSC 形态信号（2,778 行，核心模块）

| 分类 | 函数名 | 版本 | 功能 | 适配层调用 |
|------|--------|------|------|-----------|
| **一买/一卖** | `cxt_first_buy_V221126` | 2022-11 | 奇数笔序背驰(5~21笔)，power_price+power_volume+length 背驰判断 | ✅ get_bs_points |
| | `cxt_first_sell_V221126` | 2022-11 | 对称一卖 | ✅ get_bs_points |
| **二买/二卖** | `cxt_second_bs_V230320` | 2023-03 | 均线(SMA21)辅助: 笔低点在均线下+5的fx_a<fx_b → 二买 | ✅ get_bs_points |
| | `cxt_second_bs_V240524` | 2024-05 | 并列二买: 当前笔底分型与前15笔中≥2个分型区间重叠 | ✅ get_bs_points |
| **三买/三卖** | `cxt_third_buy_V230228` | 2023-02 | 笔三买: 向上突破笔价格重叠 + 当前笔低点在突破笔高点之上 | ✅ get_bs_points |
| | `cxt_third_bs_V230318` | 2023-03 | 均线辅助三买/三卖(已废弃，用V230319替代) | ✅ get_bs_points |
| | `cxt_third_bs_V230319` | 2023-03 | 均线辅助+形态: 增加均线新高/底分/新低/顶分 四种形态 | ✅ get_bs_points |
| | `cxt_double_zs_V230311` | 2023-03 | 双中枢辅助BS1: 两个中枢递增/递减 + 最后笔K线长度翻倍 | ✅ get_bs_points |
| **中枢共振** | `cxt_zhong_shu_gong_zhen_V221221` | 2022-12 | 多级别中枢共振: 小级别DD>大级别中轴+向下笔底分型 → 看多 | ✅ positions |
| **笔基础** | `cxt_bi_base_V230228` | 2023-02 | 笔方向+中继/转折: ubi_len < 9 → 转折，≥9 → 中继 | ✅ get_pen_status |
| | `cxt_bi_status_V230101` | 2023-01 | 表里关系(每根K线触发): 笔方向+ubi_len+分型 | - |
| | `cxt_bi_status_V230102` | 2023-01 | 表里关系(分型触发版): 只在分型成立K线触发 | ✅ get_pen_status |
| | `cxt_bi_zdf_V230601` | 2023-06 | BI涨跌幅5层分类: 近50笔的power分位数 | ✅ get_pen_status |
| **笔结束辅助** | `cxt_bi_end_V230222` | 2023-02 | 新高新低计数: 最后笔范围内第几次新高/新低 | ✅ get_pen_status |
| | `cxt_bi_end_V230224` | 2023-02 | 量价配合: 下影/上影比+量比 | ✅ get_pen_status |
| | `cxt_bi_end_V230104` | 2023-01 | 单均线SMA5突破: 三根K线跨越阈值 | ✅ get_pen_status |
| | `cxt_bi_end_V230105` | 2023-01 | K线形态+均线: 底分型右侧阴阳组合+均线 | ✅ get_pen_status |
| | `cxt_bi_end_V230312` | 2023-03 | MACD辅助: 最后分型的MACD方向变化 | ✅ get_pen_status |
| | `cxt_bi_end_V230320` | 2023-03 | 质数窗口: ubi_len=质数+最后3根K创新高/新低 | ✅ get_pen_status |
| | `cxt_bi_end_V230322` | 2023-03 | 分型配合均线: 分型右侧K线的MA位置关系 | ✅ get_pen_status |
| | `cxt_bi_end_V230324` | 2023-03 | 均线突破: 收盘突破顶/底分型左侧K线的MA极值 | ✅ get_pen_status |
| | `cxt_bi_end_V230618` | 2023-06 | 笔内小中枢: K线重叠近似小中枢个数 | ✅ get_pen_status |
| | `cxt_bi_end_V230815` | 2023-08 | 快速突破: 1~2根K线突破反向笔极值 | ✅ get_pen_status |
| | `cxt_ubi_end_V230816` | 2023-08 | 未完成笔内新高新低次数 | ✅ get_pen_status |
| **N笔形态** | `cxt_three_bi_V230618` | 2023-06 | 三笔形态: 盘背/奔走/收敛/扩张/不重合/无背 | ✅ get_pattern_signals |
| | `cxt_five_bi_V230619` | 2023-06 | 五笔形态: aAb式背驰/类趋势背驰/类三买/颈线突破 | ✅ get_pattern_signals |
| | `cxt_seven_bi_V230620` | 2023-06 | 七笔形态: aAbcd式/abcAd式/中枢完成/类三买 | ✅ get_pattern_signals |
| | `cxt_nine_bi_V230621` | 2023-06 | 九笔形态: aAb式/aAbBc式/ABC式/类三买A/B/ZG三买/类二买/ZD三卖 | ✅ get_pattern_signals |
| | `cxt_eleven_bi_V230622` | 2023-06 | 十一笔形态: A5B3C3/A3B3C5/A3B5C3/a1Ab式/类二买/类三买/类二卖 | ✅ get_pattern_signals |
| **区间震荡** | `cxt_range_oscillation_V230620` | 2023-06 | 笔中心点振幅≤th → N笔震荡 | ✅ get_pattern_signals |
| **分型** | `cxt_fx_power_V221107` | 2022-11 | 分型强弱(强/中/弱) + 是否有中枢 | - |
| **笔趋势** | `cxt_bi_trend_V230824` | 2023-08 | N笔中心点均值趋势方向: 向上/横盘/向下 | ✅ get_pen_status |
| | `cxt_bi_trend_V230913` | 2023-09 | 通道趋势: 高低点线性回归+通道强弱 | ✅ get_pen_status |
| **止损** | `cxt_bi_stop_V230815` | 2023-08 | 当前K线距离最后笔极值的BP距离 | ✅ get_pen_status |
| **走势分类** | `cxt_intraday_V230701` | 2023-07 | 30分钟每日走势分类: 双中枢上涨/弱平衡市/强平衡市/转折平衡市 | - |
| **支撑压力** | `cxt_overlap_V240526` | 2024-05 | K线收盘价与最近9笔顶底分型重叠次数 | ✅ get_decision_signals |
| | `cxt_overlap_V240612` | 2024-06 | SNR最顺畅笔的顶底分型作为支撑/压力位 | ✅ get_decision_signals |
| **决策区域** | `cxt_decision_V240526` | 2024-05 | 分型区域N个unique price内 → 开多/开空 | ✅ get_decision_signals |
| | `cxt_decision_V240612` | 2024-06 | 最近W根K线高低点第N价位决策区域 | ✅ get_decision_signals |
| | `cxt_decision_V240613` | 2024-06 | 放量笔(未创新低) → 开多；放量笔(未创新高) → 开空 | ✅ get_decision_signals |
| | `cxt_decision_V240614` | 2024-06 | 放量笔(创新低) → 开多；放量笔(创新高) → 开空 | ✅ get_decision_signals |
| **趋势跟随** | `cxt_bs_V240526` | 2024-05 | 快速走势后减速反弹: SNR>0.7 + 力度在10%~70%间 | ✅ get_bs_points |
| | `cxt_bs_V240527` | 2024-05 | 同上，但是基于未完成笔(UBI)判断 | ✅ get_bs_points |

#### 📊 其他信号模块一览

| 模块 | 行数 | 内容 | 适配层是否使用 |
|------|------|------|--------------|
| **tas** 技术分析信号 | 3,870 | MACD/MA/KDJ/RSI/CCI/BOLL/ATR/SAR 多版本(41个函数) | 部分(cxt依赖其缓存) |
| **bar** K线形态信号 | 2,327 | 单K线、三K线组合、放量突破、假突破、TD9、通道等(45个函数) | ❌ 笔记本系统自身已有 |
| **pos** 仓位管理信号 | 1,007 | 持仓状态、止损止盈、MA追踪等(16个函数) | ❌ 笔记本系统独立管理 |
| **jcc** 日本蜡烛图 | 1,188 | 三星/十字/五云盖顶/刺透/三发/锤子线等(21个函数) | ❌ 笔记本系统自身已有 |
| **ang** 角度/指标线 | 889 | ADTM/AMV/ASI/CLV/CMO/SKDJ/BIAS/DEMA/EMV/ER/OBV 等(16个函数) | ❌ 指标层已有 |
| **zdy** 自定义信号 | 1,428 | 笔结束、震动、止损止盈、中枢、MACD背驰、支撑压力(22个函数) | ❌ 被cxt模块覆盖 |
| **vol** 成交量信号 | 363 | 均量线、地量、高低量、窗口量(7个函数) | ❌ 笔记本系统自身已有 |
| **coo** 坐标信号 | 271 | 坐标变换相关 | ❌ 不适用 |
| **xls** 其他信号 | 358 | 杂项 | ❌ 不适用 |
| **byi** 自定义指标 | 256 | 自定义指标 | ❌ 不适用 |

### 适配层代码结构（当前实现）

```
notebook/chanlun/
├── __init__.py      ← 模块入口
├── adapter.py       ← DataFrame → RawBar 转换，自动处理价格因子(日线÷1000, 分钟线÷10000)
├── signals.py       ← 完整分析引擎(277行)
│   ├── get_structure_info()      → 笔列表/明细/未完成笔
│   ├── get_zs_info()             → 中枢序列(zg/zd/gg/dd/zz)
│   ├── get_fx_info()             → 分型列表(最近20个)
│   ├── get_bs_points()           → 一买/一卖/二买/二卖/三买/三卖 + 趋势跟随
│   ├── get_pattern_signals()     → 三笔/五笔/七笔/九笔/十一笔形态 + 区间震荡
│   ├── get_pen_status()          → 表里关系/BI基础/BI涨跌幅/笔结束辅助×11种/笔趋势×2/止损距离
│   ├── get_decision_signals()    → 分型决策/高低点决策/放量笔决策/支撑压力×2
│   └── full_analysis()           → 统一入口: 一次调用输出全部结果
└── positions.py     ← 双级别联立(91行)
    ├── dual_level_analysis()      → 日线+30分钟完整分析
    ├── _classify_position()       → 标准化位置分类
    ├── _check_zhongshu_resonance()→ 中枢共振检测
    └── _multi_level_linkage()     → 多级别联立(方向一致性/中枢内外/综合描述)
```

**不使用的模块说明：**
- `bar`、`jcc`、`vol`、`ang` → 笔记本系统的 signal_engine 已有更完整的同类指标
- `pos` → 笔记本系统独立管理仓位
- `zdy` → 功能被 cxt 模块覆盖
- `tas` → 其缓存函数(update_ma_cache/update_macd_cache)被 cxt 信号依赖，间接使用

### 和你现有系统的协作关系

```
czsc 提供的：     分型→笔→中枢 + 多级别联立 + 一/二/三买卖点
                      ↓
你的系统叠加：    ★买/★卖闭环 + 7维信号质量评分 + CCI背驰验证
                      ↓
最终决策：        缠论说"日线三买" + 你系统说"信号评分够"
                      = 可以动手
```

**缠论的买卖点是区域概念，不是精确进场点。** 你的信号系统和评分正好在这个区域里判断"什么时候动手"：
- 一买区域 → CCI底背驰确认 → ★买出现了 → 信号质量够 → 开仓
- 一买区域 → 指标条件不足 → 等 → 变成二买
- 三买区域 → ★买信号没出现 → 还在蓄势 → 等确认

---

## 架构设计

### 总体架构

```
┌──────────────────────────────────────────────────────┐
│                    数据层（已有，只读）                  │
│  signals/tracking/{code}/  +  cycle_report.json       │
│  + latest.json  +  tracking_db.sqlite                 │
└────────────────────┬─────────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────────┐
│             缠论结构定位层（2026-06-01）                  │
│  notebook/chanlun/                                       │
│  适配器 → czsc CZSC分析 → 买卖点提取 → 双级别联立定位  │
│  入口: get_position() → 日线笔方向 + 买卖点区域 + 位置   │
└────────────────────┬─────────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────────┐
│                  笔记本引擎                            │
│                                                       │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │
│  │ 预测卡引擎   │  │ 验证引擎    │  │ 案例检索引擎  │  │
│  │ prediction_  │  │ verify_     │  │ case_        │  │
│  │ card.py      │  │ engine.py   │  │ retrieval.py │  │
│  └──────┬───────┘  └──────┬──────┘  └──────┬───────┘  │
│         │                 │                 │           │
│  ┌──────▼─────────────────▼─────────────────▼───────┐  │
│  │              案例库 (SQLite)                      │  │
│  │  cases: id/skill/code/date/conditions/outcome     │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │              技能库 (Python 类)                   │  │
│  │  skills/  ← 每个技能一个文件，遵守统一契约         │  │
│  └─────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### 模块规划

| 模块 | 文件 | 职责 | 状态 |
|------|------|------|:----:|
| 预测卡数据结构 | `prediction_card.py` | 卡格式定义 + 创建/读取/序列化 | ✅ |
| 验证引擎 | `verify_engine.py` | 到期自动验证(读最新数据→判定对错) | ✅ |
| 案例检索 | `case_store.py` | 相似条件匹配(Top10)+统计汇总 | ✅(SQLite案例库合入case_store) |
| 案例入库 | `case_store.py` | SQLite 读写 + 迁移 | ✅ |
| 反馈分析 | `feedback_loop.py` | 偏差归因 + 规则修正建议 | ✅ |
| 技能基类 | `skill_base.py` | 统一契约(SignalEngine风格) | ✅ |
| 路径配置 | `shared.py` | 数据路径、主项目引用（只读） | ✅ |

---

## 技能清单

### 设计原则

**从数据出发，不从概念出发。**

❌ 概念驱动：吸筹建仓 / 洗盘震荡 / 突破追涨 → 需要先理解主力意图，三层翻译
✅ 数据驱动：★买出现在什么位置最赚钱？/ 超跌+多★买反弹概率？ → 直接回测

### 候选技能（Phase 1 — 纯数据驱动）

| # | 技能名 | 触发条件（可用现有指标直接判定） | 验证标准 |
|---|--------|--------------------------------|---------|
| 1 | 超跌★买密集 | CCI < -200 + 近5根★买 ≥ 2次 | 5日后收盘>信号日收盘 |
| 2 | 地量★买确认 | vol_llv100=True + ★买=1 | 5日后收盘>信号日收盘 |
| 3 | 金叉跟随速度 | ★买→EXPMA金叉间隔天数 | 金叉后5日涨幅 |
| 4 | 趋势中最小买点 | 趋势评分≥8 + ★买=1（不要求CCI极值） | 5日后收盘>信号日收盘 |
| 5 | 放量突破★买 | vol_突放=True + ★买=1 | 5日后收盘>信号日收盘 |

### 候选技能（Phase 2 — 需要外部信息）

| # | 技能名 | 数据来源 | 可抄 VT 技能 |
|---|--------|---------|------------|
| 6 | 事件驱动评分 | 微信公众号文章 → LLM评分 | event-driven |
| 7 | A股恐贪定位 | 换手率/涨停家数/融资余额/ETF申赎 | sentiment-analysis |
| 8 | 宏观周期定位 | PMI/CPI/M2/DR007 → 美林时钟阶段 | macro-analysis |
| 9 | 板块轮动检测 | 行业ETF相对强度排名 | sector-rotation |

---

## 数据结构

### 预测卡格式

```python
@dataclass
class PredictionCard:
    id: str                    # UUID
    skill_name: str            # 使用的技能名
    code: str                  # 标的代码
    created_date: str          # 创建日期 YYYY-MM-DD
    expiry_date: str           # 验证到期日
    
    # 触发条件快照（当时的具体数值）
    conditions: dict           # {"cci": -220, "star_buy_count": 3, "vol_llv100": True, ...}
    
    # 验收标准（必须可自动验证）
    criteria: list[dict]       # [{"metric": "close_5d_return", "operator": ">", "threshold": 0}]
    
    # 状态
    status: str                # pending / verified_correct / verified_wrong / expired
    
    # 验证结果（到期后填入）
    result: dict | None        # {"actual_return": +2.3%, "all_criteria_met": True, ...}
```

### 案例记录（SQLite schema）

```sql
CREATE TABLE cases (
    id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    code TEXT NOT NULL,
    signal_date TEXT NOT NULL,       -- 信号触发日
    conditions_json TEXT NOT NULL,   -- 触发条件完整JSON
    criteria_json TEXT NOT NULL,     -- 验收标准JSON
    verify_date TEXT,                -- 验证日
    actual_return_5d REAL,           -- 5日实际收益
    actual_return_10d REAL,          -- 10日实际收益
    all_criteria_met INTEGER,        -- 是否全部达标
    status TEXT NOT NULL,            -- verified_correct / verified_wrong
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_skill ON cases(skill_name);
CREATE INDEX idx_code ON cases(code);
CREATE INDEX idx_status ON cases(status);
```

### 案例匹配（相似度计算）

不是"全部条件必须一模一样"，而是加权相似度：

```python
# 示例：超跌★买密集技能的匹配维度
weights = {
    "cci_diff": 0.30,        # CCI值差距（归一化）
    "star_count_match": 0.30, # ★买数量匹配度
    "vol_state_match": 0.20,  # 量能状态匹配（地量/放量/缩量）
    "trend_score_diff": 0.20, # 趋势评分差距（归一化）
}

# 输出：Top 10 最相似历史案例 + 统计汇总
# "历史上10个最相似场景中：7涨3跌，平均涨幅+2.8%，最大回撤-1.5%"
```

---

## 演化历史

| 日期 | 关键变更 | 说明 |
|------|---------|------|
| 06-01 | 项目立项 | Vibe-Trading 77技能分类完成；research-goal/shadow-account/strategy-generate 三项核心参考确认；技能方向从"概念驱动"修正为"数据驱动" |
| 06-01 | 缠论结构定位层新增 | 基于 czsc (v0.10.12) 建立分型→笔→中枢→买卖点框架，适配层 notebook/chanlun/ 创建。详见下方"缠论结构定位层"章节 |
| 06-01 | 预测卡引擎实现 | 8文件650行：shared(路径配置) / skill_base(BaseSkill契约) / prediction_card(卡数据结构+JSON存储) / case_store(SQLite案例+相似度) / verify_engine(验证+批量回测) / feedback_loop(命中率分析)。技能库5个(entry_ma/entry_jincha/entry_resonance/exit_sell_t/exit_sell_reduce)对齐三级入场+两层卖出框架 |

---

## 开放问题

1. **第一个技能选哪个？** 建议"超跌★买密集"——条件最简单(CCI+★买计数)，验证最直接(5日涨跌)
2. **回测 vs 实时预测卡？** 第一阶段跑历史回测批量生成案例，第二阶段才做实时预测卡
3. **案例匹配阈值？** 相似度多高才算"匹配"？需要跑出数据后再定
4. **验证周期？** 5天/10天/20天？不同技能可能需要不同周期
5. **和现有 backtest.py 的关系？** 是用现有回测引擎还是新建？新建可以更轻量（只做技能维度统计）
6. **缠论买卖点如何和现有信号系统协作？** 初步方案：缠论提供结构定位框架（日线笔方向/中枢位置/买卖点区域），现有系统提供执行确认（★买★卖/CCI/信号质量评分）。但具体信号级别的对应规则需要回测验证
7. **czsc 买点信号在多少笔参数下最优？** cxt_first_buy 尝试 5/7/9/11/13/15/17/19/21 笔多参数，需要跑数据看哪个参数组合和现有系统信号重合度最高、预测准确率最佳
8. **要不要用 czsc 的 Event+Position 交易系统？** 暂不使用。只用结构分析 + 买卖点标注，交易决策走现有系统

---

## 代码实现位置（规划）

| 逻辑 | 文件 | 状态 |
|:--|:--|:--:|
| 项目设计说明书 | `notebook/notebook_design.md` | ✅ 本文档 |
| Vibe-Trading 参考源码 | `d:\tmp\vibe-trading\` | ✅ 只读参考 |
| 缠论模块入口 | `notebook/chanlun/__init__.py` | ✅ 导出 get_position() + full_analysis() + 全部6类信号 |
| 缠论数据格式适配 | `notebook/chanlun/adapter.py` | ✅ DataFrame → czsc RawBar，自动处理价格因子 |
| 缠论完整结构分析 | `notebook/chanlun/signals.py` | ✅ 结构+中枢+买卖点+形态+笔状态+决策 6合1，异常不再静默吞 |
| 缠论双级别联立定位 | `notebook/chanlun/positions.py` | ✅ 日线+30分钟双级别联立 + 中枢共振 + 位置分类 |
| 技能基类 | `notebook/skill_base.py` | ✅ BaseSkill ABC + SkillResult + verify/verify_from_conditions |
| 预测卡数据结构 | `notebook/prediction_card.py` | ✅ PredictionCard dataclass + JSON文件存储(pending/verified) |
| 验证引擎 | `notebook/verify_engine.py` | ✅ verify_card / verify_all_pending / batch_backtest(多周期) |
| 案例入库/检索 | `notebook/case_store.py` | ✅ SQLite案例库 + 加权相似度检索 + 命中率统计 |
| 反馈分析 | `notebook/feedback_loop.py` | ✅ 命中率趋势 / 多技能对比 / 阈值优化建议 |
| 路径配置 | `notebook/shared.py` | ✅ 路径常量 + CSV/JSON读取 + ensure_dirs |
| MA级入场 | `notebook/skills/entry_ma.py` | ✅ 5分★买+MA链+无死叉+60分/日线双黄线+PE门禁 |
| 金叉级入场 | `notebook/skills/entry_jincha.py` | ✅ MA级全部 + 5分EXPMA金叉 |
| 共振级入场 | `notebook/skills/entry_resonance.py` | ✅ 金叉级全部 + 15/30分金叉共振 |
| 做T卖出 | `notebook/skills/exit_sell_t.py` | ✅ 5分CCI顶背驰+黄线上+窗口唯一 |
| 减仓卖出 | `notebook/skills/exit_sell_reduce.py` | ✅ 5分★卖+close<MA5+无金叉+15分黄线下 |
| 项目记忆 | `memory/reference-notebook-system.md` | 🔲 |

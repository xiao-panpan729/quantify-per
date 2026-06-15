# 专家系统研究报告

**定位**: 专家系统是量化系统的顶层框架。9个独立专家 → 统一输出协议 → 上层路由调度，替代原有预测卡路线。
**模式**: 单文件持续更新，做到一定程度后再考虑拆分。对标 `filter_evolution.md`（量领）和 `notebook/research_log.md`（笔记/DL）。

> 笔记项目（notebook/）是专家系统下的一个研究支撑模块，不是与专家系统平级的框架。

---

## 一、专家分类体系

按功能域分为三类专家，各有独立编号：

```
                      ┌──────────────────────────┐
                      │    路由调度层（未来待建）    │
                      │   场景识别 → 专家选择/组合  │
                      └────────────┬─────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
   ┌────▼─────────┐        ┌──────▼────────┐        ┌───────▼────────┐
   │  底层分析专家  │        │   选股专家     │        │   支撑专家      │
   │  (技术/宏观/   │        │  (量领/势能/   │        │  (叙事/研报/    │
   │   基本面/事件) │        │   红线组合)    │        │   信源)        │
   └───────────────┘        └───────────────┘        └───────────────┘
```

### 专家清单

| 分类 | # | 专家 | 现有模块 | 定位 |
|------|---|------|---------|------|
| **底层分析** | 1 | 技术分析专家 | signal_engine + 缠论 + HHT + cycle_engine（趋势评分） | 纯量价分析，输出指标和结构判断，不限单个指标或组合指标 |
| | 2 | 宏观板块专家 | macro_screener + sector_momentum + macro_sensitivity + liquidity_monitor + japan_macro | 宏观环境+板块势能 overlay |
| | 3 | 基本面专家 | fundamental_screener + FM回归 + CAPEX | 基本面因子溢价 |
| | 4 | 事件驱动专家 | shock_detector + signal_extractor + signal_deep_reader + 信源日报 | 消息面冲击+信号事件流 |
| **选股专家** | 5 | 量领专家 | volume_leader（filter_engine/monitor/backtest） | 成交额强者×价格新高选股 |
| | 6 | 势能筛选 | x1_screener + redline_breakout_screener | x₁强度排行 + 红线突破组合筛选 |
| **支撑专家** | 7 | 叙事专家 | narrative_integration + narrative_lookup | 产业链叙事等级标注，供选股专家消费 |
| | 8 | 风控专家 | ABCD级别 + 两轴决策 | 硬拦截模块，触发即否决 |
| | 9 | 研报/节点专家 | node_map + 研报流水线 | 行情区间+龙头识别+事件标注 |

### 关键关系

- **选股专家（5-6号）** 消费 **底层分析专家（1-4号）** 的指标 + **叙事专家（7号）** 的等级标签，综合输出选股清单
- **叙事专家** 是基础设施，不直接输出交易信号，为选股提供"故事质量"维度
- **选股专家之间**并列不互斥：量领侧重成交额广度，势能筛选侧重动量强度，各自跑各自的结果
- **风控专家** 硬拦截：任一路由结果触发风控 → 否决

### 执行顺序

**第一步：统一专家输出（当前阶段）** — 每个专家输出 `{direction, conviction, horizon, evidence[], risks[]}`
**第二步：再建路由层** — 场景识别器 + 门控网络 + 决策融合

### 关键约束
- 不拆现有模块，只加协议层
- 每个专家可独立迭代、独立下线
- 选股专家之间不互斥，各自出结果后由上层做综合
- 预测卡路线已冻结

---

## 二、各专家设计决策

### 2.1 技术分析专家（1号）— 纯量价分析

**核心定位**: 所有与"量价关系"相关的分析——指标计算、趋势评分、缠论结构、HHT周期。不限单个指标或组合，输出纯量价信号和结构判断。

**模块清单**:

| 模块 | 行数 | 职责 |
|------|------|------|
| signal_engine.py | 1121 | 指标计算引擎：30基础列(EXPMA/MACD/CCI/MA/牛熊红线) + 11量能指标 + 排列熵(PE) + HHT + 周期列，总计47列 |
| update_tracking.py | 410 | 信号计算调度，增量/全量模式，14标的×6周期 |
| cycle_engine/indicators.py | 1574 | 排列熵、趋势评分(0-14)、位置/方向判断、信号质量(7维含能量能) |
| cycle_engine/cycle_structure.py | 852 | 主导量级、缠论结构、量价阶段、指数级行情 |
| cycle_engine/engine.py | 768 | 大盘系数、单标分析、全量分析调度 |
| cycle_engine/grading.py | 537 | 趋势分级(ABCD)、操作建议(fragile_high/sweet_spot/neutral/fragile_low) |
| cycle_engine/reporting.py | 251 | 报告格式化、JSON保存 |
| hht_analyzer.py | 581 | EMD分解+瞬时频率稳定性+能量跳跃检测（运算密集，不嵌入日常流水线）|
| price_pe_align.py | 396 | 价格阶段检测+PE轨迹对齐+25组合矩阵 |
| notebook/chanlun/signals.py | 525 | czsc适配，40+信号函数（结构/中枢/买卖点/形态6合1）|
| notebook/chanlun/positions.py | 211 | 日线+30分钟双级别联立+中枢共振 |
| backtest_signals.py | 1059 | 信号回测引擎（低点合并/50%合并/★信号独立）|

**任务目标**:
1. 对14只跟踪标的×6周期（日/30/60/15/5/1分钟）计算标准化信号，输出47列CSV
2. 趋势评分(0-14)：量化多空强度，区分真实强势区(sweet_spot 8-10)与虚高警示(fragile_high 11+)
3. ABCD操作级别：日线MACD状态→决定最小操作周期（A=5分钟一信号，D=不参与）
4. 信号质量7维递进：★买密度→金叉跟随→底部抬升→闭环成对→MA金叉→PE突破→量能确认
5. CCI完整闭环：极值→背驰(面积)→★买/★卖→EXPMA金叉/死叉确认
6. ★买→节点映射+贝叶斯置信度修正（与#9研报专家协同）

**使用说明**:
```
# 全量信号计算（每日盘后）
python update_tracking.py                         # 14标的全量增量更新
python update_tracking.py sz159740                # 单标的更新
python update_tracking.py --verify                # pytdx抽验数据完整性

# 趋势分析
python run_cycle.py --save                        # 三层架构：位置→趋势→循环
python run_cycle.py --code sh513120               # 单标的

# 回测
python backtest_signals.py --save                  # 全量回测
python backtest_signals.py --code sh600438 --months 12  # 单标12个月

# 缠论
python -c "from notebook.chanlun.positions import get_position; print(get_position('sz159740'))"

# HHT（运算密集，按需）
python hht_analyzer.py --code sz159740             # 单标的HHT分析
```

**当前能力评估**:
- ✅ 信号CSV流水线稳定产出（47列/14标的×6周期），增量模式已对齐
- ✅ 趋势评分(0-14)框架成熟，sweet_spot(8-10)为回测验证最准区域
- ✅ ABCD级别 + 两轴决策框架可操作
- ✅ CCI完整闭环（极值→背驰→★买/★卖→金叉/死叉确认）
- ✅ 缠论40+信号函数 + 双级别联立仓位
- ✅ ★买→节点映射贝叶斯已产出（配合#9专家）
- ⚠️ HHT运算密集（60-90秒），不嵌入日常流水线，仅按需调用
- ⚠️ 信号质量第7维（量能确认）实际使用率低，需更多实盘验证
- ⚠️ 回测交易配对逻辑复杂，低点合并/50%合并规则需持续校准

**关键设计决策**:
1. **0-14趋势评分三维度**: MACD(0-4，0轴锚定·位置+交叉解耦，6种状态) + MA排列(0-6，链式递进5→10→20→60→120→250) + 日线闭环(0-4，波段累积扣分制+30/60共振) — 三个维度正交不重叠，可解释性强
2. **ABCD级别独立于总评分**: 纵轴ABCD管"哪个周期可信"，横轴zone_advice管"该不该做"，两轴独立不替代。避免"评分高=可以买"的误用
3. **CCI闭环（极值→面积背驰→信号→确认）**: CCI不看高度看面积判别背驰，防止毛刺误判。必须走完完整四步才触发操作
4. **信号质量递进（7维+权重）**: 不是简单相加，★买密集→金叉跟随→底部抬升→闭环成对→MA金叉→PE突破→量能确认，每一维在前置满足后才生效
5. **牛熊红线(MA221+3σ)**: 年线+3倍标准差的双重压制/支撑判断，红线突破作为趋势反转的强确认信号

**更新时间线**:
- 2026-06-14: 首次补充完整文档（原只有核心定位+模块清单）
- 2026-06-10: 信号质量7维递进框架定型
- 2026-06-05: CCI面积背驰逻辑实装
- 2026-06-01: 缠论适配层（czsc）首次部署
- 2026-05-25: cycle_engine三层架构定型
- 2026-05-15: 两轴决策（ABCD+zone_advice）分离设计

### 2.2 宏观板块专家（2号）

**核心定位**: 宏观环境判断 + 板块势能评分 + 宏观-板块敏感度分析。回答"当前宏观环境是什么状态？哪些板块在当前的宏观环境下最受益/最承压？"

**模块清单**:

| 模块 | 行数 | 职责 |
|------|------|------|
| sector_momentum.py | 575 | 269概念板块 X₁ 评分（直接读880xxx板块指数日线，v2.0修复了v1.0成分股平均偏高问题）|
| macro_screener.py | 189 | 板块动量 × 宏观环境 overlay（合并sector_momentum + macro_sensitivity输出，加推荐/中性/回避标签）|
| macro_sensitivity.py | 712 | 15+因子 RollingOLS（M2/SHIBOR/CPI/PMI等宏观因子→板块收益率的滚动敏感度，v0.5标准化系数可跨因子比较）|
| liquidity_monitor.py | 380 | 5因子全球流动性压力指数（BTC/VIX/DXY/M2/信用脉冲，合成压力∈[-1,1]）|
| japan_macro.py | 381 | 日本宏观+套息交易压力（BOJ政策利率/USDJPY/核心CPI → 套息交易压力指数）|

**任务目标**:
1. 判断当前宏观环境处于什么象限（宽松/紧缩/过渡）
2. 量化各概念板块对宏观因子的敏感度（哪些板块是流动性敏感型、利率敏感型）
3. 输出板块×宏观overlay推荐清单（当前环境下该买/该回避什么板块）
4. 监测全球流动性风险（BTC金丝雀+VIX恐慌+DXY资金流向）
5. 监测日本套息交易平仓风险（全球流动性的水源地）

**使用说明**:
```
# 板块势能（每日盘后）
python tools/sector_momentum.py --save          # 全量269板块X₁评分
python tools/sector_momentum.py --verify        # 与通达信RPS对比验证
python tools/sector_momentum.py --search 培育钻石  # 搜索特定板块

# 宏观敏感度（数据更新后跑）
python tools/macro_sensitivity.py                # 全量604板块OLS敏感度
python tools/macro_sensitivity.py --classify      # 仅当前环境分类
python tools/macro_sensitivity.py --sectors 5     # 测试模式

# 宏观分层过滤（依赖上面两个的输出）
python tools/macro_screener.py                    # Top 30 含overlay标签
python tools/macro_screener.py --top 50 --json    # JSON输出

# 流动性监控
python tools/liquidity_monitor.py --save          # 5因子压力指数→JSON
python tools/liquidity_monitor.py --history 12    # 最近12个月轨迹

# 日本宏观
python tools/japan_macro.py --save                # 套息压力→JSON
python tools/japan_macro.py --classify            # 仅分类
```

**当前能力评估**:
- ✅ sector_momentum 稳定可产出（269板块X₁，与通达信RPS可对标验证）
- ✅ liquidity_monitor 5因子合成有效，但数据源依赖akshare，节假日可能延迟
- ✅ japan_macro 三个核心因子完善，可作为A股科技/成长的先行指标
- ⚠️ macro_sensitivity 因子集偏少（仅M2/SHIBOR/CPI/PMI），社融等已被证明near-zero signal而剔除
- ⚠️ macro_screener overlay 逻辑偏简单（动量×敏感度的二维交叉），缺少多因子综合评分
- ⚠️ 四个模块输出各自独立，缺少一个"宏观综合快照"的统一入口

**关键设计决策**:
1. **板块指数直读（v2.0）** — v1.0对成分股逐股算分再平均导致数值偏高（培育钻石26.2 vs 通达信9.68），v2.0改为直读880xxx板块指数日线，与通达信RPS同口径可比
2. **5因子流动性模型** — BTC/VIX/DXY/M2/信用脉冲 五选，BTC作为全球流动性金丝雀（领先1-2周），信用脉冲作为实体融资需求同步指标
3. **标准化系数（v0.5）** — 回归前因子z-score标准化，使系数可跨因子比较（"宏观因子每1σ变动→板块收益率变化X%"）
4. **日本套息追踪必要性** — 日本是全球流动性水源地，BOJ加息→日元升值→套息平仓→全球流动性收缩→A股科技/成长承压，这是A股少有的可量化的外部传导链

**更新时间线**:
- 2026-06-14: 首次补充完整文档（原只有模块清单）
- 2026-06-08: sector_momentum v2.0 板块指数直读修复完成
- 2026-06-01: macro_sensitivity v0.5 标准化系数版本上线
- 2026-05-25: liquidity_monitor + japan_macro 首次部署

### 2.3 基本面专家（3号）

**核心定位**: 基本面因子溢价测算 + 增长叙事分类（TYPE A/B）。回答"当前市场奖励什么基本面特征？哪些股票有基本面支撑？"

**模块清单**:

| 模块 | 行数 | 职责 |
|------|------|------|
| fundamental_screener.py | 425 | 全流程：pytdx季频财报→因子构建(ROE/毛利率/营收增速/净利增速/负债率)→MAD去极值→z-score标准化→Fama-MacBeth截面回归→因子溢价输出 |
| fm_pipeline.py | 500 | Fama-MacBeth 滚动截面回归流水线，逐期横截面回归→时间序列平均 |
| growth_narrative.py | 329 | Type A(PEG机构抱团)/Type B(重资产收获期) 双成长叙事检测，生命周期定位 |
| capex_analyzer.py | 142 | CFA 5层CAPEX周期分析（CAPEX/营收阈值→固定资产周转率→ROIC vs WACC） |
| data_layer.py | 293 | 季频→日频转换 + 因子矩阵，pytdx gpcw财务数据（2021~2026）|

**任务目标**:
1. 计算当前市场对哪些基本面因子给溢价（市场奖励增长还是质量？惩罚什么？）
2. 对跟踪标的做TYPE A/B分类，判断处于生命周期哪个阶段
3. 识别重资产公司的CAPEX周期转折点（从投资期进入收获期）
4. 为选股专家提供基本面维度的打分（消费方：#5量领、#6势能）

**使用说明**:
```
# 全量基本面因子溢价
python tools/fundamental_screener.py                    # 默认全量
python tools/fundamental_screener.py --top 20           # Top20排序

# FM滚动回归
python tools/fundamental/fm_pipeline.py --update        # 更新FM回归数据

# 增长叙事
python tools/fundamental/growth_narrative.py --save     # 增长叙事引擎

# CAPEX分析
python -c "from tools.fundamental.capex_analyzer import CapexAnalyzer; ..."
```

**当前能力评估**:
- ✅ Fama-MacBeth 截面回归流水线正常产出因子溢价表
- ✅ Type A/B 成长叙事分类可用（PEG vs 重资产收获期）
- ✅ CAPEX 周期分析框架搭建完成
- ⚠️ 数据源限 pytdx gpcw 季频财务数据（最新窗口20250930），存在4-6个月滞后
- ⚠️ 因子集偏少（仅6因子），缺少现金流质量、研发投入等维度
- ⚠️ CAPEX 分析预留了5层框架但仅2层实装（Inverse. + Operating），尚未接入估值数据
- ⚠️ 季频→日频转换用插值填充，相邻财报期间信号可能失真

**关键设计决策**:
1. **Fama-MacBeth 而非截面回归**: 先逐期截面回归（每期得到因子载荷），再对时间序列取平均。比简单截面回归更稳健，避免单期异常值的干扰
2. **MAD去极值而非3σ**: 财务数据分布厚尾严重，3σ会剔除过多有效样本（如高增长公司的真实极端值），MAD(中位数绝对偏差)更鲁棒
3. **TYPE A/B 双分类**: A类=净利润持续高增长的PEG叙事（机构抱团风格），B类=营收扩张→CAPEX投入→折旧高峰→FCF转折的重资产生命周期。两类不是排他的，一个公司可以同时触发A和B的条件
4. **因子溢价 vs 因子暴露分离**: 溢价（factor premium）是市场给这个因子的奖励程度（时间序列平均），暴露（factor exposure）是单个股票在这个因子上的得分。两个维度正交，分开输出

**更新时间线**:
- 2026-06-14: 首次补充完整文档（原只有模块清单）
- 2026-06-10: growth_narrative + capex_analyzer 首次部署
- 2026-06-08: data_layer.py 季频→日频转换方案定型
- 2026-06-05: Fama-MacBeth 滚动回归流水线 v1 上线
- 2026-06-01: fundamental_screener v0.1 首次部署

### 2.4 事件驱动专家（4号）— 兼信源聚合

**核心**: 消息面冲击检测 + 信源聚合（公众号观点交叉+自有数据融合）。

| 模块 | 职责 |
|------|------|
| shock_detector.py | 三源消息面冲击检测（WS/东财/AI） |
| signal_extractor.py | 9源+微信→KG映射→JSON事件流 |
| signal_deep_reader.py | LLM深度精读（公众号全文→信号+CoT） |
| gen_source_summary.py | 信源AI日报生成（数据摘要层） |
| gen_daily_brief.py | 观点聚合+共振判断（公众号观点 vs 宏观数据） |
| _fetch_articles.py | 公众号批量拉取 |

**⚠️ 地基问题（2026-06-14记录）**:
信源聚合报告输出质量不达标，根源是地基没打牢：
1. 公众号精读一直被砍成"300字摘要"——违反铁律（必须先完整读）
2. LLM（NVIDIA V4 Flash）撑不起复杂结构化提取，但一直包办
3. 先改格式后补内容，输出不可控（用户感觉在黑盒里）
4. 没有反推设计：先想"要达到什么目的"再设计流程

**更正后流程（待实现）**:
```
① 消息收集
   ├─ 自有数据（宏观/ETF/概念链/基本面）
   ├─ 旧8公众号全部精读（不砍内容，完整读全文）
   └─ 突发事件检测（shock_detector）

② 按主题分组（规则匹配，非LLM分类）
   宏观 | 产业链 | 地缘政治 | 基本面 | 流动性

③ 每个主题填入 Dorian 模板
   ├─ 发生了什么事件/变量变化
   ├─ 公众号怎么解读（完整引用，非摘要）
   ├─ 自有数据印证/背离
   ├─ 对A股的影响性质（流动性/地缘/产业/基本面）
   └─ 后续跟踪重点

④ 交叉对比
   ├─ 多个博主是否同时在谈同一个主题
   ├─ 宏观数据和博主解读是否共振
   └─ 与上一期报告对比，观点是延续/加强/扭转

⑤ 渲染输出（盘前纪要风格的分类呈现）
```

**任务目标**:
1. 让用户快速知道从上一次脚本运行到现在发生了什么重要事
2. 按宏观/产业/地缘/基本面分类呈现，不是流水账
3. 各公众号观点交叉对比，不是平铺罗列
4. 标明哪些变化对A股重要（流动性冲击/地缘风险/产业拐点）
5. 指出接下来要持续跟踪什么

**使用说明**:
- 完整流水线：`update_sources.bat`（抓取→聚合→分析→报告）
- 仅报告：先 `python gen_source_summary.py --ai` → 再 `python gen_daily_brief.py`
- 情绪热点4个公众号（盘前纪要/盘前/一思一记/安静拆主线）已接入但暂未整合，后续再做

**更新清单**:
- 2026-06-14: 修正方向记录。停止所有新功能开发，先修复地基。核心问题：公众号精读不能砍、LLM不能包办、先反推设计再动手。

### 2.5 量领专家（5号）— 选股

**核心定位**: 成交额强者×原始价格新高选股。筛选全市场成交额最大的股票池（Top 50/100/200），叠加价格创新高条件，用MA链/金叉/PE门禁三级过滤。

**模块清单**:

| 模块 | 行数 | 职责 |
|------|------|------|
| volume_leader_screener.py | 801 | 成交额强者筛选 + universe管理 + 三层梯队 |
| update_volume_leaders.py | 259 | 6周期信号计算（全量+增量）|
| tools/volume_leader/filter_engine.py | 124 | 共享过滤原语（MA链/金叉/死叉/PE门禁/黄线 10个纯函数）|
| tools/volume_leader/monitor.py | 1924 | 三级弹窗监控（MA级试错/金叉级买入/共振级买完 + 减仓级）|
| tools/volume_leader/backtest.py | 1584 | 回测引擎（对比/配对/切换分析，6个月/全宇宙/MA级）|
| tools/volume_leader/trade_db.py | 238 | SQLite交易台账（持仓/统计/历史）|
| tools/volume_leader/factor_attribution.py | 432 | 因子归因（逐层贡献度分析）|
| tools/volume_leader/fetcher.py | 175 | 数据获取（日线/分钟线批量拉取）|
| tools/volume_leader/scan_resonance.py | 193 | 多周期共振检测 |
| gen_volume_leader_report.py | 371 | AI日报 |

实验记录: `tools/volume_leader/experiments/filter_evolution.md`（34实验/2300行）

**任务目标**:
1. 每日筛选成交额强者（Top 50/100/200），捕捉大资金动向
2. 三级买侧过滤：MA级（试错）→ 金叉级（买入信号）→ 共振级（多周期确认）
3. 两层卖侧过滤：减仓级（止盈）+ 严格卖（止损/破位）
4. 回测验证过滤规则的有效性，持续迭代参数
5. 提供实时弹窗监控（Windows toast），盘中有信号即时通知

**使用说明**:
```
# 每日盘后（完整流程）
python tools/volume_leader_screener.py --top 50 --update-rank --sync-universe --save
python update_volume_leaders.py
python gen_volume_leader_report.py

# 监控（盘中运行）
python tools/volume_leader/monitor.py --filter all     # 三级同时弹
python tools/volume_leader/monitor.py                  # 默认MA级(试错)
python tools/volume_leader/monitor.py --filter resonance  # 仅共振级(买完)

# 回测
python tools/volume_leader/backtest.py                  # 默认6个月/全宇宙
python tools/volume_leader/backtest.py --compare         # MA级 vs 金叉级对比
python tools/volume_leader/backtest.py --pair            # 买→卖配对交叉统计

# 交易台账
python -c "from tools.volume_leader.trade_db import get_open_entries, get_stats_by_level, get_all_trades; print('持仓:', get_open_entries())"
```

**当前能力评估**:
- ✅ 全流程稳定运行（screener→update→monitor→backtest→report）
- ✅ monitor提供Windows toast弹窗，盘中可接收信号
- ✅ trade_db记录实盘交易，支持持仓/统计/历史查询
- ✅ backtest覆盖6个月/全宇宙/MA级，支持对比+配对模式
- ✅ 34实验/2300行独立实验文献，迭代过程可追溯
- ⚠️ monitor 1924行偏大，弹窗逻辑和过滤逻辑耦合较紧
- ⚠️ 实盘交易量偏少，回测胜率与实际操作胜率之间的gap待校验
- ⚠️ 因子归因模块刚上线，实盘验证尚浅

**关键设计决策**:
1. **三级买侧过滤**: MA级(10日线上+expma12向上)→金叉级(MA级+EXPMA金叉)→共振级(金叉级+周期共振+量能确认)。逐级递增确定性，试错仓→正式仓→重仓
2. **两层卖侧**: 减仓级(涨幅达标+量缩/背离) vs 严格卖(破10日线/破成本/PE突变)。减仓是止盈不清仓，严格卖是风控止损
3. **PE排列熵门禁**: 排列熵>0.7(无序状态)时禁止买入，避免在随机波动中开仓。这是量领系统独有的过滤条件（其他选股系统没有）
4. **universe管理**: 从全市场Top50自动筛选，sync-universe将符合条件的新标的加入跟踪列表。解决"选股池动态更新"问题
5. **交易台账独立**: 用SQLite(trade_db.py)而非JSON存储交易记录，支持复杂查询（按级别统计持仓/配对交叉分析）

**更新时间线**:
- 2026-06-14: 首次补充完整文档
- 2026-06-12: factor_attribution 因子归因模块上线
- 2026-06-10: backtest配对模式(--pair)上线
- 2026-06-08: 三级过滤(MA/金叉/共振)定型
- 2026-06-05: monitor弹窗系统首次部署
- 2026-06-01: volume_leader_screener 首批上线
- 2026-05-28: 首版量领filter_engine + 实验#1

### 2.6 势能筛选（6号）— 选股

**核心定位**: x₁ 强度排行（通达信RSI势能2） + 牛熊红线突破组合筛选。全市场5000+股中选出势能最强+结构突破的标的。

**模块清单**:

| 工具 | 行数 | 定位 |
|------|------|------|
| x1_screener.py | 870 | A股全市场 x₁ 强度 Top 50，A/B/C分类，板块标注 |
| redline_breakout_screener.py | 305 | 红线突破 × x₁势能 ≥ 8 组合选股，含叙事等级列 |

**任务目标**:
1. 每日输出全市场 x₁ 势能最强 Top 50，附带板块标注和A/B/C分类
2. 识别长期被牛熊红线(MA221+3σ)压制后首次突破的标的（蓄力→点火→爆发）
3. 为跟踪清单提供势能维度的候选（消费方：跟踪清单管理）

**使用说明**:
```
# 每日盘后
python tools/x1_screener.py --today               # Top 50
python tools/x1_screener.py --top-n 30            # 指定排名深度
python tools/x1_screener.py --report              # 板块标注报告

# 红线突破
python tools/redline_breakout_screener.py          # 默认Top 30
python tools/redline_breakout_screener.py --top 20 # 更严格

# 分析模式
python tools/x1_screener.py --analyze              # 模式分析
python tools/x1_screener.py --export               # 透视CSV导出
```

**当前能力评估**:
- ✅ x1_screener 全市场扫描稳定（5000+股，全序列预计算56s→每日O(1)）
- ✅ A/B/C三级分类：A(持续走强)/B(调整)/C(新进)，可跟踪势能阶段变化
- ✅ redline_breakout 三重条件：压制→突破→爆发确认
- ✅ 输出含板块标注和叙事等级列（消费#7叙事专家输出）
- ⚠️ A/B/C分类的阈值（6/3分界线）是启发式的，未做系统回测优化
- ⚠️ 红线突破的"被压制时间"参数未做敏感性测试

**关键设计决策**:
1. **直读通达信.day文件**: 不依赖板块指数或第三方API，5000+股直接读本地数据，速度快（全量56s预计算+每日O(1)查询）
2. **复用sector_momentum._sma**: x₁计算调用的SMA函数与#2宏观板块专家的sector_momentum共享同一实现，禁止手写避免偏差
3. **全序列预计算**: 首次运行全量回算所有股票的历史x₁序列（耗时56s），之后每天只增量计算最后一天的x₁值（O(1)查询）
4. **A/B/C 三分类**: A=持续走强（x₁>6且次日>前日），B=调整（x₁>6但回落），C=新进（x₁从<2突破到>6）。分类支持跟踪"势能阶段"的变化

**更新时间线**:
- 2026-06-14: 首次补充完整文档
- 2026-06-12: 叙事等级列接入（消费#7输出）
- 2026-06-11: A/B/C分类上线，--analyze模式
- 2026-06-10: x1_screener 初版（全序列预计算+O(1)日查）
- 2026-06-08: redline_breakout_screener 初版

### 2.7 叙事专家（7号）— 支撑

**核心定位**: 产业链叙事等级标注（S/A/B/C/U五级），为选股提供"故事质量"维度。回答"这个股票的产业链故事好不好？属于什么级别的叙事？"

**模块清单**:

| 模块 | 说明 |
|------|------|
| narrative_integration | 270通达信板块 → 叙事链S/A/B/C/U分级映射 |
| narrative_lookup | 个股→叙事链查询（代码→板块→叙事等级） |
| build_narrative_mapping.py | TDX概念板块→叙事链S/A/B/C/D映射桥构建 |
| narratives/narrative_judgment_layer.md | 判定层定义（S/A/B/C/U的标准）|
| narratives/timelines/ | 53条产业链叙事时间线 |
| narratives/templates/ | 50个叙事模板（P0/P1/P2优先级） |
| narratives/foreign_views/ | 外资行观点时间线（高盛/大摩/小摩/瑞银/花旗）|
| narratives/overseas/ | 传导链工作台+海外实物变量观察站 |

**任务目标**:
1. 将通达信270个概念板块映射到S/A/B/C/U叙事等级（S=超级主线，A=强叙事，B=有故事，C=边缘，U=未分类）
2. 个股通过所属板块→叙事等级查询，为选股提供"故事质量"分
3. 维护产业链叙事时间线（53条链），记录叙事演变过程
4. 聚合外资行观点，做内资vs外资分歧监控

**使用说明**:
```
# 叙事映射构建（首次/新增板块时跑）
python tools/build_narrative_mapping.py

# 个股叙事查询
python tools/narrative_lookup.py 600438                # 单标的
python tools/narrative_lookup.py 600438 --detail       # 详细
python tools/narrative_lookup.py --batch               # 批量

# 外资观点时间线在 narratives/foreign_views/ 下直接阅读
```

**当前能力评估**:
- ✅ 关键词子串匹配覆盖220/270板块（81%），映射桥可用
- ✅ 50个叙事模板 + 53条产业链时间线已归档
- ✅ 外资行观点持续更新（高盛/大摩/小摩/瑞银/花旗）
- ✅ 传导链工作台+海外实物变量（WF6/Brent/钨等）已建立
- ⚠️ 关键词规则只有53条，部分板块的叙事等级可能不准（如华为概念1000+成分股未拆分子链）
- ⚠️ 叙事等级变化方向（升级/降级）尚未系统追踪
- ⚠️ U级（未分类）板块需手工研报精读才能定级，积压较多

**关键设计决策**:
1. **关键词子串匹配（非LLM分类）**: 用正则关键字匹配板块名称→叙事等级，速度快（毫秒级）、结果稳定可重复。避免LLM分类的不确定性
2. **品牌产业链拆分子链**: 华为概念（1000+股）不笼统定级，拆分为华为AI服务器(A)、华为昇腾(A)、华为汽车(B)等子链各给等级
3. **S/A/B/C/U五级制**: S=超级主线（AI/半导体/新能源），A=强叙事（人形机器人/商业航天），B=有故事（可控核聚变），C=边缘（禽流感），U=未分类。U级不强行塞入S/A/B/C，标记待研报精读
4. **外资观点聚合**: 单独维护 foreign_views/ 目录，每条观点带原文链接和时间戳。内外资分歧点是重要的市场拐点信号

**更新时间线**:
- 2026-06-14: 首次补充完整文档
- 2026-06-12: 传导链工作台+海外实物变量站建立
- 2026-06-11: narrative_integration 初版（53条关键词规则）
- 2026-06-10: 品牌产业链拆分子链方案定型
- 2026-06-08: 叙事判定层(narrative_judgment_layer.md)确定五级体系
- 2026-06-05: 叙事目录初建，首批模板+时间线

### 2.8 风控专家（8号）— 支撑

**核心定位**: 硬拦截模块。触发即否决——不产生信号，只否决危险信号。回答"当前能不能做？"

**模块清单**:

| 组件 | 来源 | 职责 |
|------|------|------|
| ABCD级别 | cycle_engine/grading.py | 日线MACD状态→最小操作周期筛选 |
| 两轴决策 | cycle_engine/grading.py + engine.py | 纵轴(ABCD)管周期可信度，横轴(zone_advice)管该不该做 |
| fragile_high检测 | cycle_engine/engine.py | 11+分虚高警示，评分高但结构脆弱时禁止追高 |
| D级拦截 | cycle_engine/grading.py | D级(日线MACD<0或死叉)时禁止任何做多操作 |

**任务目标**:
1. 防止在错误的时间周期入场（ABCD周期筛选）
2. 防止在虚高区域追高（fragile_high 11+分禁止买入）
3. 防止在弱势环境下频繁交易（D级+中性区不参与）
4. 两轴独立决策，不互相替代——大盘好也不能在D级重仓

**使用说明**:
```
# 风控信号自动集成在 cycle_report.json 和 synthesized_report.json 中
python run_cycle.py --save                 # 更新所有标的的风控等级
python -c "import json;d=json.load(open('signals/tracking/_signals/cycle_report.json'));[print(f'{i[\"name\"]}: {i[\"grade\"]} {i[\"zone_advice\"]}') for i in d]"
```

**当前能力评估**:
- ✅ ABCD级别 + 两轴决策框架成熟可用
- ✅ fragile_high 虚高警示成功案例（多次阻止高位追入）
- ✅ D级硬拦截逻辑清晰（MACD<0或死叉时不参与）
- ⚠️ 风控逻辑散落在grading.py和engine.py中，没有独立的风控模块入口
- ⚠️ 没有统一的"风控报告"输出——目前需要去cycle_report.json里捞
- ⚠️ 量价背离、情绪极端值等额外风控维度尚未接入

**关键设计决策**:
1. **两轴独立（不替代原则）**: 纵轴ABCD管"哪个周期可信"，横轴zone_advice管"该不该做"。大盘趋势好(横轴偏多)不能在D级(纵轴偏弱)重仓——两轴不互相替代
2. **fragile_high反转逻辑**: 趋势评分11+但评分结构脆弱（闭环薄弱）→虚高警示。这不是信号错误，而是"评分正确但结构预示反转"——是unique的卖出信号
3. **ABCD→最小操作周期**: A级=5分钟信号可入场，B级=需5分钟★买+2次金叉，C级=需15分钟★买+2次金叉，D级=不参与。越弱势越需要大级别确认

**更新时间线**:
- 2026-06-14: 首次补充完整文档（原只有核心定位片段）
- 2026-06-10: fragile_high 检测逻辑上线
- 2026-06-05: ABCD + zone_advice 两轴定型
- 2026-05-25: cycle_engine 三层架构建成分离

### 2.9 研报/节点专家（9号）— 支撑

**核心定位**: 节点地图（板块行情区间+龙头识别）+ 东财研报API + 宏观历史回溯 + ★买→节点贝叶斯映射。回答"当前行情的级别和驱动力是什么？"

**模块清单**:

| 模块 | 行数 | 职责 |
|------|------|------|
| node_map.py | 794 | 270板块×3750节点构建（波检测+龙头识别+质量评分）|
| macro_history.py | 420 | A/B节点宏观标注（3068个节点标注中国/US/日本/流动性环境）|
| star_buy_node_map.py | 330 | ★买→节点映射+贝叶斯收缩置信度修正（8分组）|
| annotate_node_events.py | 560 | 节点产业政策事件标注（43行业×5半年段缓存，1176节点/5043事件）|
| research_report.py | 376 | 东财研报API（5类研报/51行业，CLI+Python双模式）|

**任务目标**:
1. 构建板块行情节点地图——每个上涨/下跌波段的区间、龙头、质量评分
2. 给每个节点标注当时的宏观环境（中国货币/美国利率/地缘/流动性）
3. ★买信号出现时，映射到所属节点+贝叶斯分组，判断当前信号的历史胜率
4. 节点级别的产业政策事件标注（这个节点期间出了什么政策）
5. 提供研报批量读取能力

**使用说明**:
```
# 节点地图（全量扫描）
python tools/node_map.py --all --save            # 270板块→3750节点
python tools/node_map.py --sector 黄金概念         # 单板块

# 宏观标注
python tools/macro_history.py --min-grade B       # A/B节点宏观标注

# ★买→节点映射
python tools/star_buy_node_map.py --save           # ★买→节点映射
python tools/star_buy_node_map.py --stock sh600438 # 单标的

# 研报
python tools/research_report.py                    # 东财API读取
```

**当前能力评估**:
- ✅ node_map 覆盖270板块/3750节点，波检测+龙头识别稳定产出
- ✅ macro_history 标注3068个A/B节点，覆盖中国/US/日本/流动性4维度
- ✅ star_buy_node_map 贝叶斯收缩8分组，修正小样本组置信度
- ✅ annotate_node_events 1176节点/5043事件标注完成
- ✅ 研报API支持5类研报/51行业
- ⚠️ node_map扫描耗时210s，不适合每日运行，建议每周/按需运行
- ⚠️ 节点质量评分算法可能需要更多实盘验证
- ⚠️ 节点→交易决策的链路不够直接（用户需要知道"这个节点下的操作含义"，目前只有描述）

**关键设计决策**:
1. **波检测+龙头识别自动化**: 从板块指数日线自动识别涨跌波段，每个波段内识别领涨龙头股。避免手工标注的巨大工作量
2. **贝叶斯收缩修正★买置信度**: 某些分组样本极少（如某分组只有3个★买样本），直接计算胜率不可靠。贝叶斯收缩把小组的胜率向全局均值"拉回"，解决小样本偏差
3. **宏观标注四维度**: 中国货币环境 + 美国利率方向 + 地缘风险等级 + 全球流动性状态。四个维度的合成标注，覆盖影响A股的主要外部因子
4. **事件缓存(43行业×5半年段)**: 避免每次标注都调用LLM，预计算缓存产业政策事件。新增节点时只需增量补充

**更新时间线**:
- 2026-06-14: 首次补充完整文档（原只有模块清单）
- 2026-06-12: annotate_node_events 1100+节点事件标注完成
- 2026-06-11: star_buy_node_map 贝叶斯分组上线
- 2026-06-10: node_map 全量270板块扫描上线
- 2026-06-08: macro_history A/B节点标注框架确定
- 2026-06-05: node_map 初版（板块检测+龙头识别）

---

## 三、统一输出协议（v1 设计中）

### 标准 Schema

```
{
  expert:     "expert_name",
  stock:      { code, name },
  timestamp,

  verdict: {
    direction:  "bullish" | "bearish" | "neutral",
    conviction: 0.0 ~ 1.0,
    horizon:    "short" | "mid" | "long"
  },

  evidence: [
    { key: "factor_name", value: "factor_value", weight: 0.0~1.0 }
  ],

  risks: [
    "判断失效条件描述"
  ]
}
```

### 叙事专家 conviction 计算

| 因子 | 权重 | 映射 |
|------|------|------|
| 叙事等级 | 0.4 | S→0.95 / A→0.80 / B→0.55 / C→0.30 / U→0.10 |
| 链覆盖数 | 0.2 | ≥3条→↑ / 1条→↓ |
| 等级变化方向 | 0.2 | 升级→↑ / 稳定→ / 降级→↓ |
| 生命周期位置 | 0.2 | 新兴→↑ / 共识→ / 过热→↓ / 衰退→↓ |

direction: S/A→bullish, B→neutral, C/U→bearish
horizon: mid/long（叙事天然偏中长期）

### 势能专家 conviction 计算

| 因子 | 权重 | 映射 |
|------|------|------|
| x₁ 当前位置 | 0.4 | >8(过热→↓) / 3-8(强势→↑) / <2(弱势→↓) |
| x₁ 百分位 | 0.3 | >80%过热 / 20-80%正常 / <20%超卖 |
| x₁ 趋势(5日) | 0.2 | 加速→↑ / 减速→↓ / 反转→↓ |
| 量能配合 | 0.1 | 放量+高x₁→↑ / 缩量+高x₁→↓ |

direction: x₁>5→bullish, 1-5→neutral, <1→bearish
horizon: short（势能天然偏短期）

---

## 四、专家配合层（四象限决策框架）

### 核心逻辑

| 专家 | 核心问题 | 视角 |
|------|---------|------|
| narrative_integration | "这个故事好不好？" | 质量判断，中长期 |
| x1_screener | "市场在买这个故事吗？" | 动量确认，短期 |

### 四象限

| 叙事 \ 势能 | x₁ 强势 | x₁ 弱势 |
|------------|---------|---------|
| **A/S 级** | ✅ 顺势做 — 双专家共识 | ⚠️ 故事好但市场不认 |
| **B/C 级** | 🤔 资金驱动短期行情 | ❌ 避开 — 双专家否定 |

### 跟踪清单准入规则 (v1)

- **准入**: 叙事 conviction ≥ 0.5 **且** 势能 conviction ≥ 0.3
- **优先**: 双专家共振（同方向）> 单专家信号
- **观察**: 背离信号不直接排除，标记"需人工判断"

---

## 五、演化记录

### 2026-06-10 — 专家系统方向确认

- 预测卡路线冻结，转向多专家模块化+路由调度层（对标MOE）
- 9大专家清单确定，叙事专家为当前重点建设对象
- 执行顺序：先统一专家输出 → 后建路由层

### 2026-06-11 — experts/ 目录初建

- 创建 experts/ 目录，为已有的7个专家建立独立档案
- x1_screener 初版完成（全序列预计算 + A/B/C分类 + --analyze模式）
- 教训：手写递归 SMA 被 catch → 复用 sector_momentum._sma

### 2026-06-11 — narrative_integration 初版

- 关键词规则53条，覆盖220/270板块（81%）
- 新增3条品牌产业链：特斯拉机器人(B)、苹果AI终端(A)、小米汽车(A)
- 中捷精工误判修复：S级(特斯拉→新能源车) → A级(小米汽车)

### 2026-06-12 — 统一输出协议设计

- 叙事专家 + 势能筛选统一输出 schema 确定
- conviction 合成公式确定（各4因子加权）
- 四象限决策框架 + 跟踪清单准入规则
- **结构调整**: 8个独立专家文件合并为 research_log.md 单文件

### 2026-06-12 — 框架修正：三类专家体系

- 用户纠正分类：量领、x₁、红线突破、叙事不是技术分析，是**选股专家**
- 确立三类体系：底层分析（1-4号）→ 选股专家（5-6号）→ 支撑专家（7-9号）
- 选股专家消费底层分析指标 + 叙事标签，综合输出选股清单
- 技术分析专家回归纯量价分析定位

---

## 六、实验记录

专家融合实验（多专家配合、阈值测试、路由调优）统一记录在：
- **[experiments/fusion_log.md](experiments/fusion_log.md)** — 单文件累积，对标 `filter_evolution.md`

每个专家的独立迭代实验记录在各自模块目录下（如 `tools/volume_leader/experiments/filter_evolution.md`）。
跨专家协作的实验必须写入 `experiments/fusion_log.md`，不丢。

---

### 2026-06-12 — 实验日志体系建立 + neat 自动化检测

- **融合实验日志**: 建 `experts/experiments/fusion_log.md`，单文件累积，对标 filter_evolution.md
- **neat 第1.6步**: 三系统实验入库自动检测（理解→分类→路由→版本管理→确认）
- **开发规则**: CLAUDE.md + MEMORY.md 新增"专家实验必录"硬约束
- **关键转变**: 从依赖人记忆→neat 自动扫描缺失+被动确认。每次 neat 检查三个系统的实验日志是否漏记
- 6/11 势能+红线+叙事融合实验（7轮迭代，含中捷精工假阳性修复全链条）已作为 fusion_log #1 写入

---

## 七、待做

- [ ] 实现 `x1_screener.py --expert` 模式（输出标准化 JSON）
- [ ] 实现 `narrative_lookup.py --expert` 模式（输出标准化 JSON）
- [ ] 构建合成路由脚本（消费两个专家输出 → 四象限分类 + 跟踪清单准入）
- [ ] conviction 权重用回测数据校准
- [ ] 量能确认因子接入 volume_leader 数据
- [ ] 叙事生命周期位置判断标准（新兴/共识/过热/衰退）
- [ ] 等级变化方向数据接入（每次定级记录 previous_grade）
- [ ] 华为概念全集（1000+成分股）子映射拆分
- [ ] 英伟达→AI基础设施(S)的直接映射
- [ ] 品牌产业链研报精读流程 Skill 化
- [ ] 多空分组回测（x₁值→未来5/10/20天收益分组统计）
- [ ] 板块集中度时序（Top50中前3板块占比的时间序列）
- [ ] 场景识别器设计（震荡/趋势/题材/踩踏四种场景锚点）
- [ ] 路由调度层设计（门控网络 → 专家选择/权重分配）

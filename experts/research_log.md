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

**核心**: 所有与"量价关系"相关的分析，不限单个指标或组合。

涵盖模块：
- `signal_engine.py`（1121行）— 30基础列 + 11量能 + PE + HHT + 周期
- `notebook/chanlun/`（525+211行）— czsc 缠论适配（40+信号函数+双级别仓位）
- `hht_analyzer.py`（581行）— EMD + 瞬时频率
- `price_pe_align.py`（396行）— 价格阶段检测 + PE轨迹对齐
- `cycle_engine/`（768+1574+852+537行）— 趋势评分(0-14) + ABCD级别 + 两轴决策

**注意**: 牛熊红线（MA221+3σ）和 x₁ 势能（RSI势能2）的计算也在本专家完成（指标层面），但做选股筛选的工具把它们当成输入，归属在选股专家。

### 2.2 宏观板块专家（2号）

| 模块 | 职责 |
|------|------|
| sector_momentum.py | 269概念板块 X₁ 评分，个股→板块映射缓存 |
| macro_screener.py | 板块动量 × 宏观环境 overlay |
| macro_sensitivity.py | 15+因子 RollingOLS + 环境分类 |
| liquidity_monitor.py | 5因子全球流动性压力指数 |
| japan_macro.py | 日本宏观 + 套息交易压力 |

### 2.3 基本面专家（3号）

| 模块 | 职责 |
|------|------|
| fundamental_screener.py | 基本面因子溢价筛选 |
| fm_pipeline.py | Fama-MacBeth 滚动截面回归 |
| growth_narrative.py | TYPE A/B 双成长叙事 |
| capex_analyzer.py | CAPEX周期分析 |
| data_layer.py | 季频→日频转换 + 因子矩阵 |

### 2.4 事件驱动专家（4号）

| 模块 | 职责 |
|------|------|
| shock_detector.py | 三源消息面冲击检测（WS/东财/AI） |
| signal_extractor.py | 9源+微信→KG映射→JSON事件流 |
| signal_deep_reader.py | LLM深度精读（公众号全文→信号+CoT） |
| gen_source_summary.py | 信源AI日报生成 |
| _fetch_articles.py | 公众号批量拉取 |

### 2.5 量领专家（5号）— 选股

**核心**: 成交额强者×价格新高选股，有独立完整的管线。

| 模块 | 行数 | 职责 |
|------|------|------|
| volume_leader_screener.py | 801 | 成交额强者筛选 + universe管理 |
| filter_engine.py | 124 | 共享过滤原语（MA链/金叉/死叉/PE门禁） |
| monitor.py | 1924 | 三级弹窗监控 |
| backtest.py | 1584 | 回测引擎 |
| trade_db.py | 238 | SQLite交易台账 |
| factor_attribution.py | 432 | 因子归因 |
| scan_resonance.py | 193 | 多周期共振 |
| update_volume_leaders.py | 259 | 6周期信号计算 |
| gen_volume_leader_report.py | 371 | AI日报 |

实验记录: `tools/volume_leader/experiments/filter_evolution.md`（34实验/2300行）

### 2.6 势能筛选（6号）— 选股

**核心**: x₁ 强度排行 + 牛熊红线突破组合筛选。

| 工具 | 行数 | 定位 |
|------|------|------|
| `x1_screener.py` | 870 | A股全市场 x₁ 强度 Top 50，A/B/C分类 |
| `redline_breakout_screener.py` | 305 | 红线突破 × x₁势能 ≥ 8 组合选股，含叙事等级列 |

**x1_screener 关键决策**:
1. 直读通达信 .day 文件，不依赖板块指数
2. x₁ 计算复用 `sector_momentum._sma`，禁止手写
3. ST 过滤 252 只
4. 全序列预计算（56s）→ 每日 O(1) 查询
5. A(持续走强) / B(调整) / C(新进) 三级分类

**redline_breakout_screener 三条件**:
1. 长期被牛熊红线压制（蓄力）
2. 股价突破红线（点火）
3. x₁ ≥ 8（爆发确认）
+ 叙事等级列（消费叙事专家输出）

### 2.7 叙事专家（7号）— 支撑

**核心**: 产业链叙事等级标注，为选股提供"故事质量"维度。

涵盖：
- `narrative_integration` — 270通达信板块 → S/A/B/C/U叙事链映射
- `narrative_lookup` — 个股→叙事链查询
- `narratives/narrative_judgment_layer.md` — 判定层定义
- `narratives/timelines/` — 53条链时间线
- `narratives/templates/` — 50个叙事模板
- `narratives/foreign_views/` — 外资行观点
- 研报精读流程：并行agent → 时间线 → 模板 → 判定层 → 映射桥

关键决策：
1. 关键词子串匹配，覆盖 220/270=81% 板块
2. 品牌产业链拆分子链，不笼统定级
3. U级标记需研报精读，不强行塞入S/A/B/C

### 2.8 风控专家（8号）— 支撑

**核心**: 硬拦截模块，触发即否决。

涵盖 cycle_engine/grading.py + 两轴决策框架：
- ABCD操作级别（周期筛选）：A最强=5分钟一信号，D弱势=不参与
- 环境建议（zone_advice）：fragile_high(11+) / sweet_spot(8-10) / neutral(3-7) / fragile_low(0-2)
- 硬拦截：D级 + fragile_high → 否决买入

### 2.9 研报/节点专家（9号）— 支撑

| 模块 | 职责 |
|------|------|
| node_map.py | 270板块 × 3750节点（波检测+龙头识别） |
| macro_history.py | A/B节点宏观标注 |
| star_buy_node_map.py | ★买→节点映射+贝叶斯分组 |
| research_report.py | 东财研报API |
| annotate_node_events.py | 节点产业政策事件标注 |

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

# quantify-per — 量化交易信号系统

基于通达信数据的多周期量化信号计算 + AI 信源日报系统。
自动跟踪 **14 只标的**（指数/ETF/个股），约 **20,000 行 Python**。

---

## 系统全景

```
数据底座       专家系统（9个专家）          研究层            AI层
通达信数据 ─→  技术分析 / 量领 / 基本面     笔记/缠论/DL ─→  AI 日报
公众号/快讯 ─→  板块势能 / 宏观 / US映射     预测卡/验证      信源聚合
知识星球 ─→    叙事 / 风控 / 研报标注        实验日志          GitHub Pages
```

## 当前功能

| 模块 | 说明 |
|------|------|
| **数据同步** | 通达信 pytdx → 日线/分钟线增量同步，多周期合成 |
| **信号引擎** | 14 趋势评分 + 两轴决策(ABCD+操作级别) + CCI闭环 |
| **量领系统** | 成交额强者筛选 → 三级买侧/两层卖侧 → 回测引擎 |
| **板块势能** | 269 概念板块 X₁ 强度评分，个股→板块映射 |
| **宏观分层** | 中国/US/日本宏观环境 + 全球流动性 5 因子压力 |
| **US 映射** | 52 ETF + 74 明星股动量 + 概念链 + 跨市场映射 |
| **缠论结构** | czsc 适配层，40+ 信号函数，双级别联立 |
| **信源日报** | 8 公众号拉取 → 聚合摘要 → 话题分析 → GitHub Pages |
| **知识图谱** | 12 公众号→事件流 JSON→SQL 实体-事件索引 |
| **知识库捕获** | mitmproxy 拦截知识星球/IMA → Obsidian 统一知识库 |

## 跟踪标的（14 只）

**指数**: 上证指数 · 创业板指
**ETF**: 恒生科技 · 港股通汽车 · 创新药 · 电网设备 · 中韩半导体 · 科创芯片
**个股**: 拓维信息 · 东方日升 · TCL科技 · TCL中环 · 通威股份 · 隆基绿能

## 快速开始

```bash
# 盘后全量流水线（14步）
run_daily.bat

# 信源日报
update_sources.bat

# 单步执行
python update_from_tdx.py        # 数据同步
python update_tracking.py         # 信号计算
python run_cycle.py --save        # 周期分析
python gen_report_md.py           # 报告生成
python gen_daily_brief.py         # 信源观点聚合
python _publish_report.py         # 发布到 GitHub Pages
```

## 关键路径

| 文件 | 说明 |
|------|------|
| `config.py` | 14 只跟踪标的配置 |
| `signal_engine.py` | 评分引擎核心 |
| `cycle_engine/` | 三层周期循环分析 |
| `tools/volume_leader/` | 量领系统（screener/backtest/monitor） |
| `tools/sector_momentum.py` | 板块势能评分 |
| `tools/node_map.py` | 板块节点地图 |
| `experts/` | 9 大专家系统 |
| `reports/sources/` | 信源日报输出 |

## 技术栈

- Python 3.10+
- pytdx（通达信数据解码 — 唯一权威）
- MyTT（技术指标）
- Claude / DeepSeek（AI 报告）
- mitmproxy（HTTPS 拦截捕获）
- Obsidian（统一知识库）
- Gitee + GitHub Pages（托管/发布）

## 项目演化

- **v0.1** (3月) 概念验证：通达信公式理解
- **v0.4** (4月) 信号校准 + 闭环引擎
- **v1.2** (5月) 量领系统 + 板块势能 + 缠论适配
- **v2.0** (6月) 9专家架构 + 信源日报 + 知识图谱 + 知识库统一

# CLAUDE.md — quantify-per 量化交易信号系统

**代码行数统计**: ~14,600 行 Python  (2026-05-22)
**当前跟踪**: 14 只标的 (2 指数 + 12 只 ETF/个股)
**文档目录**: [docs/](docs/) 含 architecture.md / integration-guide.md / operator-runbook.md / evolution_timeline.md

## 一、常用命令

```bash
# ─── ★新会话启动：先读系统状态，再查原始数据★ ───
python operation_tracker.py --status   # 战役状态
python -c "import json;d=json.load(open('signals/tracking/latest.json','r',encoding='utf-8'));[print(f'{k} {v[\"name\"]}: dailyCCI={v[\"daily\"][\"indicators\"].get(\"cci\",\"?\")} ★买={sum(1 for s in v[\"daily\"][\"signals\"].values() if s.get(\"buy_signal\"))}') for k,v in d['stocks'].items()]"
python -c "import json;d=json.load(open('signals/tracking/cycle_report.json','r',encoding='utf-8'));[print(f'{i[\"code\"]} {i[\"name\"]}: {i[\"trend\"][\"score\"]}分 {i[\"trend\"][\"direction\"]} → {i[\"advice\"]}') for i in d]"
# 最新报告: dir reports/daily/*_v3.md | 最近一个
# 记忆: 读 ~/.claude/projects/d--quantify-per/memory/case-*.md（近期案例）

# ─── 完整盘后流水线（按需选择） ───
run_daily.bat               # 全量10步: 数据同步 → 量领筛选+宇宙 → 信号(固+量) → 分析 → 回测 → 报告(固+量)
run_daily_timed.bat         # 快速: 数据同步 → 信号计算 → 机会扫描+报告（含耗时统计）
run_update.bat              # 仅数据: 数据同步 → 信号计算（不含分析/报告）

# ─── 分步执行（单向依赖） ───
python update_from_tdx.py              # 步骤1: 通达信→量化库（日线增量+分钟线+合成周期）
python update_tracking.py               # 步骤2: 信号计算 → CSV + latest.json + SQLite
python cycle_engine.py --save           # 步骤3: 三层架构周期循环分析 → cycle_report.json
python backtest_signals.py --save       # 步骤4: 信号回测 → backtest_report.json
python gen_report_md.py                 # 步骤5: 可读 Markdown 报告
python operation_tracker.py --status    # 战役级操作追踪

# ─── 信源管理（微信公众号） ───
python _fetch_articles.py               # 批量拉取7个信源的最新文章全文 → wechat_articles/
python _search_accounts.py              # 搜索公众号 fakeid（新增信源时用）

# ─── 单标的 / 单周期 ───
python update_tracking.py sz159740      # 只更新恒生科技
python cycle_engine.py --code sh513120  # 只分析创新药

# ─── 数据验证 ───
python update_tracking.py --verify      # pytdx 抽验全量

# ─── 胜率与回测 ───
python qa_tool.py                       # 终端胜率对比
python qa_tool.py sz159740 min5         # 单标信号流水

# ─── 筹码与机构建仓分析 ───
python chip_extractor.py                # 解压筹码峰7z到 data/chips/
python chips_selector_v2.py             # 全市场关键K选股(倍量+涨幅+筹码锁定)
python chips_selector_v2.py --code sh513120  # 单标的筹码选股
python jigou_jiancang.py                # 机构建仓指标(WINNER真实筹码版)

# ─── 战役级操作追踪 ───
python operation_tracker.py --status    # 显示活跃战役
python operation_tracker.py --scan      # 扫描新战役建议
python operation_tracker.py --suggest   # 检测平仓信号

# ─── 全市场筛选 ───
python tools/volume_leader_screener.py                    # ★成交额强者×原始价格新高 三层梯队
python tools/volume_leader_screener.py --top 20           # 更严格: Top20
python tools/volume_leader_screener.py --update-rank      # 更新排名快照+筛选（每日盘后必做）
python tools/volume_leader_screener.py --sync-universe    # 新增标的自动加入 universe（每日盘后）
python tools/volume_leader_screener.py --fetch-names      # 首次运行：拉取全量名称缓存
python tools/volume_leader_screener.py --top 50 --update-rank --sync-universe --save  # 完整每日流程
python update_volume_leaders.py           # ★成交量领导者信号计算（6周期，全量+增量）
python update_volume_leaders.py sh603986  # 只更新指定标的
python gen_volume_leader_report.py       # ★成交量领导者 AI 日报 → reports/volume_leader/
python tools/a_stock_screener.py        # 全A股强势+波动率排序
python tools/strong_vol_screener.py     # ETF+主流板块强势筛选

# ─── 综合数据检查（pytdx 直读，不猜格式） ───
python -c "from pytdx.reader import TdxDailyBarReader; r=TdxDailyBarReader();
for mkt,code in [('sh','000001'),('sz','000001'),('sz','159740')]:
    p=f'C:/zd_cjzq/vipdoc/{mkt}/lday/{mkt}{code}.day'; df=r.get_df(p);
    print(f'{mkt}{code} 日线:{str(df.index[-1].date())} ({len(df)}条)')"
python -c "from pytdx.reader import TdxMinBarReader; r=TdxMinBarReader();
for mkt,code in [('sz','159740'),('sh','000001')]:
    for ext,d in [('lc5','fzline'),('lc1','minline')]:
        p=f'C:/zd_cjzq/vipdoc/{mkt}/{d}/{mkt}{code}.{ext}'; df=r.get_df(p);
        print(f'{mkt}{code} {ext}:{str(df.index[-1])} ({len(df)}条)')"
```

## 二、环境依赖

```bash
# Python 3.10+，无 requirements.txt，直接 pip 安装
pip install pytdx mytt numpy pandas pandas-ta
```

关键依赖版本参考：pytdx 1.72、MyTT 2.9.3、pandas 2.3.3、numpy 2.2.6。

## 三、架构总览

### 处理流水线（单向依赖）

```
通达信 .day/.lc1/.lc5          ← 源数据 (C:\zd_cjzq\vipdoc) + 筹码峰 (D:\筹码峰\)
    │
    ├─▶ update_from_tdx.py      ← 数据同步
    │       │
    │       ▼
    │   data/{lday,one,five,fifteen,thirty,sixty}/{sz,sh}/  ← CSV缓存
    │
    ├─▶ chip_extractor.py       ← 筹码峰解压 → data/chips/
    │
    ▼
update_tracking.py              ← 14只标的全周期信号计算
    ├─ CSV → signals/tracking/{code}/{period}_signals.csv  (47列/6周期)
    ├─ JSON → signals/tracking/latest.json
    └─ SQLite → signals/tracking/tracking_db.sqlite
    │
    ├─▶ cycle_engine.py         ← 三层架构：位置→趋势→循环
    │   └─ signals/tracking/cycle_report.json
    │
    ├─▶ hht_analyzer.py         ← HHT独立分析 (EMD+瞬时频率)
    │   └─ signals/tracking/hht_report.json
    │
    ├─▶ backtest_signals.py     ← 信号回测
    │   └─ signals/tracking/backtest_report.json  +  backtest_trades.db
    │
    ├─▶ gen_report_md.py        ← 报告 → reports/daily/{date}_v3.md
    │
    ├─▶ operation_tracker.py    ← 战役级操作追踪
    │   └─ signals/tracking/operation_records.json
    │
    └─▶ jigou_jiancang.py / chips_selector_v2.py  ← 机构建仓/选股
```

### 关键模块

| 模块 | 行数 | 职责 |
|------|------|------|
| [config.py](config.py) | 168 | 路径自适应、`NAME_MAP` 统一管理跟踪列表、合成周期配置 |
| [signal_engine.py](signal_engine.py) | 1060 | 指标公式库(30基础列) + PE/HHT/cycle_period + 量能后处理(11列) → 总计47列 |
| [update_from_tdx.py](update_from_tdx.py) | 1097 | 通达信二进制读写、增量同步、15/30/60分钟合成 |
| [update_tracking.py](update_tracking.py) | 408 | 信号计算调度，增量/全量模式，量能后处理 |
| [cycle_engine.py](cycle_engine.py) | 54 | CLI 薄壳（实际逻辑在 cycle_engine/ 包） |
| [cycle_engine/utils.py](cycle_engine/utils.py) | 66 | 常量、路径、CSV读取、NAME_MAP |
| [cycle_engine/indicators.py](cycle_engine/indicators.py) | 1007 | 排列熵、趋势评分、位置/方向判断、信号质量(7维含量能) |
| [cycle_engine/cycle_structure.py](cycle_engine/cycle_structure.py) | 730 | 主导量级、缠论结构、量价阶段、指数级行情 |
| [cycle_engine/engine.py](cycle_engine/engine.py) | 294 | 大盘系数、单标分析、全量分析调度 |
| [cycle_engine/grading.py](cycle_engine/grading.py) | 506 | 趋势分级、ABCD级别、操作建议生成 |
| [cycle_engine/reporting.py](cycle_engine/reporting.py) | 238 | 报告格式化、JSON保存 |
| [backtest_signals.py](backtest_signals.py) | 817 | 信号回测引擎（低点合并/50%合并/★信号独立） |
| [hht_analyzer.py](hht_analyzer.py) | 580 | HHT 独立分析（EMD分解+瞬时频率+非预期解检测+量能修正层） |
| [gen_report_md.py](gen_report_md.py) | 1026 | Markdown 报告生成（含A+/A-/A假等级 + 回撤可视化） |
| [synthesize_report.py](synthesize_report.py) | 454 | 三层聚合引擎（ABCD等级 + A+/A-/A假细分 + 操作动作） |
| [price_pe_align.py](price_pe_align.py) | 240 | 价格-结构对齐分析（价格阶段检测 + PE轨迹对齐 + 25组合矩阵） |
| [operation_tracker.py](operation_tracker.py) | 661 | 战役级操作追踪（开仓/持仓/平仓事件链） |
| [jigou_jiancang.py](jigou_jiancang.py) | 495 | 机构建仓指标（基于真实筹码 WINNER 函数） |
| [chip_loader.py](chip_loader.py) | 415 | 筹码分布数据加载器 |
| [chip_extractor.py](chip_extractor.py) | 188 | 筹码峰 7z 批量解压 |
| [chips_selector_v2.py](chips_selector_v2.py) | 373 | 关键K选股（倍量+涨幅+短上影+筹码锁定） |
| [ai_analyzer.py](ai_analyzer.py) | 174 | AI 分析引擎（多API自动切换 + 交易框架注入） |
| [qa_tool.py](qa_tool.py) | 144 | 终端盘后分析/胜率统计 |
| [tools/tdx_fetch.py](tools/tdx_fetch.py) | 291 | pytdx API 封装（多服务器自动切换） |
| [tools/tracking_db.py](tools/tracking_db.py) | 427 | SQLite 持久化 |
| [tools/a_stock_screener.py](tools/a_stock_screener.py) | ~200 | 全A股强势+波动率排序器 |
| [tools/strong_vol_screener.py](tools/strong_vol_screener.py) | ~200 | ETF+主流板块强势筛选 |
| [tools/volume_leader_screener.py](tools/volume_leader_screener.py) | ~440 | ★成交额强者×原始价格新高 选股器 + universe 管理 |
| [update_volume_leaders.py](update_volume_leaders.py) | ~190 | ★成交量领导者6周期信号计算（全量+增量） |
| [gen_volume_leader_report.py](gen_volume_leader_report.py) | ~200 | ★成交量领导者 AI 日报生成 |

### 数据目录

```
D:\quantify-per\
├── lday/sz|sh/          ← 日线原始副本
├── one/sz|sh/           ← 1分钟线（lc1）
├── five/sz|sh/          ← 5分钟线（lc5）
├── fifteen|thirty|sixty/ ← 从5分钟合成
├── data/
│   └── chips/           ← 筹码分布数据 (yearly/ + daily/)
├── signals/tracking/
│   ├── {code}/          ← 每标的的6个周期CSV
│   │   └── {period}_signals.csv   (41列含11量能指标)
│   ├── cycle_report.json          (14条, 含评分/主导量级/建议)
│   ├── backtest_report.json
│   ├── backtest_trades.db
│   ├── score_history.json         (每日评分快照)
│   ├── hht_report.json
│   ├── analysis_history.json      (每日分析历史快照)
│   ├── operation_records.json     (战役级操作追踪)
│   ├── volume_leader_universe.json (★成交量领导者动态宇宙)
│   ├── volume_rank_history.csv    (每日成交额排名快照)
│   ├── stock_names.csv            (全量股票名称缓存)
│   └── tracking_db.sqlite
├── reports/daily/       ← 每日报告 (YYYYMMDD_v3.md + YYYYMMDD_v3_nl.md)
├── reports/volume_leader/ ← ★成交额强者选股报告 (YYYYMMDD_volume_leader.md) + AI日报 (YYYYMMDD_volume_leader_report.md)
├── chips_picks/        ← 关键K选股结果
├── gbbq/               ← 除权数据（pytdx TdxDailyBarReader 自动读取）
├── prompts/
│   ├── trading_persona.md  ← AI风格模板（扫街/分时出击风格）
│   ├── trading_analysis_framework.md  ← 战役级分析框架（ai_analyzer.py 使用）
│   └── operation_tracker_prompt.md  ← 操作追踪提示词
└── docs/
    ├── architecture.md
    ├── integration-guide.md
    ├── operator-runbook.md
    └── evolution_timeline.md
```

## 四、关键数值与命名约定

```python
DAY_PRICE_FACTOR = 1000    # 日线: 原始值/1000
MIN_PRICE_FACTOR = 10000   # 分钟线: 原始值/10000
N_trend_daily = 55         # 日线LLV/HHV周期
N_trend_min_short = 40     # 5-15分钟线LLV/HHV周期 (lc60用40，非55!)
```

- 代码格式：`{市场前缀}{6位代码}`，如 `sz159740`、`sh000001`，直接作为 config.py `NAME_MAP` 的 key
- 周期标识：`daily`、`min1`、`min5`、`min15`、`min30`、`min60`
- 数据文件命名：`{market}{code}.day` / `.lc1` / `.lc5` / `.lc15` / `.lc30` / `.lc60`
- 信号CSV路径：`signals/tracking/{code}/{period}_signals.csv`

## 五、数据格式

### 信号 CSV（47 列）

- **基础列（30列）**：`timestamp, date, open, high, low, close, expma12, expma50, macd_dif, macd_dea, macd_hist, trend_line, bb_ma221, bb_red_line, red_line_cross, buy_signal, sell_signal, expma_cross, cci, cci_extreme, cci_retreat, cci_divergence, ma5, ma10, ma20, ma60, ma120, ma250, volume, amount`
- **量能指标列（11列）**：`vol_ma5, vol_ma60, vr5, vr60, vol_llv100, vol_llv10, vol_堆, vol_缩50, vol_突放, vol_梯度升, vol_梯度降`
- **PE 列（3列）**：`pe`(60窗滚动排列熵), `pe_level`(high/mid/low), `pe_chg_5`(5根PE变化) — 全周期
- **HHT 列（2列）**：`hht_freq`(瞬时频率), `hht_amp`(瞬时振幅) — 仅日线
- **周期列（1列）**：`cycle_period`(峰值间距均值) — 仅 min30

- `vr5/vr60`：短期/中期量比（当前VOL / 5或60周期均量）
- `vol_llv100`：百日地量（近5根内出现100期最低量）
- `vol_llv10`：十日地量
- `vol_堆`：地量堆（近5根中十日地量>=3次）
- `vol_突放`：放量突破（C>前5根最高 + vr5>1.5）
- `vol_梯度升/降`：成交量连续3根递增/递减

关键字段：
- `buy_signal`：`★买` 或空；`sell_signal`：`★卖` 或空；`expma_cross`：`金叉`/`死叉`/空
- `cci_extreme`：`CCI+200`(正极限) / `CCI-200`(负极限)；`cci_divergence`：`顶背驰`/`底背驰`
- `red_line_cross`：`突破红线`/`跌破红线`

### latest.json 结构

```json
{
  "update_time": "2026-05-14 15:30:00",
  "stocks": {
    "sz159740": {
      "name": "恒生科技ETF大成",
      "daily": { "signals": {...}, "indicators": {...} },
      "min5": { ... },
      "min15": { ... },
      "min30": { ... },
      "min60": { ... }
    }
  }
}
```

每个周期条目含最新 bar 的完整指标 + 近5/50根 K 线信号摘要。

### cycle_report.json 结构

```json
[
  {
    "code": "sz159740",
    "name": "恒生科技ETF大成",
    "position": "高位区间/低位区间",
    "trend": { "score": 12, "direction": "上涨", "aspects": {...} },
    "periods": { "daily": {...}, "min5": {...}, ... },
    "best_period": "min60",
    "best_signal_level": "B",
    "advice": "持有/观望",
    "signal_quality": { "buy": 0.5, "sell": 1.2 },
    "dominant_level": "日线",
    "structure": { "type": "双底", "target": "..." }
  }
]
```

## 六、核心领域规则（高频使用）

### 0-16 趋势评分

| 维度 | 分值 | 逻辑 |
|------|------|------|
| EXPMA | 0~2 | e12>e50=2，粘合=1，空头=0 |
| MACD | 0~4 | 0轴+金叉死叉，强势>0.01% |
| MA排列 | 0~6 | 链式递进5→10→20→60→120→250，断链即停 |
| 日线闭环 | 0~4 | buy_level>=4→4分, >=3.5→3分 |

方向：13-16上涨 / 10-12偏多 / 7-9中性 / 4-6偏空 / 0-3下跌

### ABCD 级别匹配

| 等级 | 条件 | 最小操作级别 |
|------|------|-------------|
| A最强 | EXPMA白线上方 | 5分钟一信号 |
| B次强 | 白线-黄线区域 | 5分钟★买+2次金叉 |
| C偏弱 | 黄线下但MACD>0 | 15分钟★买+2次金叉 |
| D弱势 | MACD<0或死叉 | 不参与，等大级别底部 |

### 信号质量递进（买侧7维）

1. ★买密集度(+0.5~1.5) → 2. EXPMA金叉跟随速度(+0.3~1.5) → 3. 底部抬升(+1.0) → 4. 闭环成对(+0.3~1.0) → 5. MA5/10金叉确认(+0.3~1.2) → 6. 排列熵结构突破(+1.0~1.5) → **7. 量能确认(+0.3~1.5)**

第7维量能确认：
- 地量堆+★买 = +1.5（最强底部确认）
- 百日地量+★买 = +1.0
- 放量突破 = +1.0
- 上涨回调缩量50%+ = +0.5
- 梯度放量 = +0.3

### CCI 完整闭环流程

```
CCI极值(≤-200/≥+200) → 背驰(看面积非高度) → ★买/★卖 → EXPMA金叉/死叉确认
```

### 当前跟踪标的（14只，统一从 config.py NAME_MAP 生成）

指数：sh000001 上证指数、sz399006 创业板指
ETF：sz159740 恒生科技、sh520600 港股通汽车、sh513120 创新药、sz159326 电网设备、sh513310 中韩半导体、sh588200 科创芯片
个股：sz002261 拓维信息、sz300118 东方日升、sz000100 TCL科技、sz002129 TCL中环、sh600438 通威股份、sh601012 隆基绿能

加/删/改名只需维护 [config.py:153-168](config.py#L153-L168) 的 `NAME_MAP`。

## 七、开发规则

### 开发流程硬约束（Superpowers）

以下规则优先于其他所有开发规则，每次编码前强制执行：

| 条件 | 必须执行 | 含义 |
|------|---------|------|
| **新建算法/功能/模块** | `Skill("brainstorming")` | 先探索→问清楚→出设计→用户点头→再写代码 |
| **修改算法逻辑（>30行）** | `Skill("brainstorming")` | 同上，算法改动不能直接上手 |
| **多文件修改任务** | `Skill("writing-plans")` | 先出实现计划，再动手 |
| **遇到 bug / 报错 / 异常** | `Skill("systematic-debugging")` | 不准猜原因直接改，走系统调试流程 |
| **有实现计划后** | `Skill("subagent-driven-development")` | 可并行的子任务用子代理执行 |

以下情况可以跳过：
- 单行修复（typo/语法错误/变量名修正）
- 纯数据查询（只读 CSV/SQLite，不改代码）
- 用户明确说"直接改"或"不用走流程"

### 其他开发规则

- **修改必写日志**：改前/改后代码片段写入 `C:\Users\Administrator\.claude\projects\C--Users-Administrator\archives\`
- **通达信格式不猜**：必须查 `D:\miniconda3\Lib\site-packages\pytdx\reader\` 源码
- **数据只读快照**：信号查询走 CSV/SQLite，禁止从源文件重算
- **日志输出**：各模块统一 `print()` 输出到 stdout，无 logging 配置
- **🔥新增指标必是 per-bar 滚动计算**：任何新指标（除非算法天然全局不可拆，如 EMD）都必须在 signal_engine 中按每根 bar 独立计算、存入 CSV、参与每日增量更新。禁止只做一次性快照再临时分析。目的：每根 bar 有值 → 可以做轨迹图 → 持续正向反馈

## 八、相关路径

- 通达信源：`C:\zd_cjzq\vipdoc\`
- 记忆/归档：`C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\` + `archives\`
- pytdx 源码：`D:\miniconda3\Lib\site-packages\pytdx\reader\`
- 筹码源数据：`D:\筹码峰\`

## 九、常见问题排查

| 现象 | 原因 | 解决 |
|------|------|------|
| 信号漏算/某标的无数据 | 该标的不在 `NAME_MAP` | 在 config.py NAME_MAP 添加代码+中文名 |
| 日线最后日期不更新 | 通达信未下载当日数据 | 打开通达信 → 盘后数据下载 → 确认日线成功 |
| 分钟线缺失 | 只下了日线没下分钟线 | 通达信下载时勾选"5分钟线" |
| pytdx 连接超时 | 通达信行情服务器断开 | 等待几秒重试，或重启通达信 |
| 合成周期报错 | 5分钟源数据不完整 | 检查 five/sz|sh/ 下对应 lc5 文件是否完整 |
| cycle_engine.py 索引越界 | CSV 文件为空或格式异常 | 检查 signals/tracking/{code}/{period}_signals.csv |
| 价格数值异常大 | 因子未除（日线/1000，分钟线/10000） | 确认使用 DAY_PRICE_FACTOR / MIN_PRICE_FACTOR |
| GEN_REPORT 报告乱码 | 中文字符编码问题 | gen_report_md.py 已统一 utf-8，检查控制台编码 |
| `cycle_engine.py` 运行极慢 | 全量扫描14只标的6周期 | 指定单标的: `--code sz159740`，或只跑 `--quick` |

# CLAUDE.md — quantify-per 量化交易信号系统

## 一、常用命令

```bash
# ─── 完整盘后流水线 ───
run_daily.bat

# ─── 分步执行（单向依赖） ───
python update_from_tdx.py              # 步骤1: 通达信→量化库（日线增量+分钟线+合成周期）
python update_tracking.py               # 步骤2: 信号计算 → CSV + latest.json + SQLite
python cycle_engine.py --save           # 步骤3: 三层架构周期循环分析 → cycle_report.json
python backtest_signals.py --save       # 步骤4: 信号回测 → backtest_report.json
python gen_report_md.py                 # 步骤5: 可读 Markdown 报告
python scan_opportunities.py --report   # 替代: 机会扫描报告

# ─── 单标的 / 单周期 ───
python update_tracking.py sz159740      # 只更新恒生科技
python cycle_engine.py --code sh513120  # 只分析创新药
python scan_opportunities.py --code sh513310

# ─── 数据验证 ───
python update_tracking.py --verify      # pytdx 抽验全量

# ─── 工具 ───
python qa_tool.py                       # 终端胜率对比
python qa_tool.py sz159740 min5         # 单标信号流水

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
通达信 .day/.lc1/.lc5          ← 源数据 (C:\zd_cjzq\vipdoc)
    │
    ▼
update_from_tdx.py              ← 数据同步
    │
    ▼
update_tracking.py              ← 14只标的全周期信号计算
    ├─ CSV → signals/tracking/{code}/{period}_signals.csv
    ├─ JSON → signals/tracking/latest.json
    └─ SQLite → signals/tracking/tracking_db.sqlite
    │
    ▼
cycle_engine.py                 ← 三层架构：位置→趋势→循环
    └─ 输出: signals/tracking/cycle_report.json
    │
    ▼
backtest_signals.py             ← 信号回测
    └─ 输出: signals/tracking/backtest_report.json
    │
    ▼
gen_report_md.py                ← 报告 → reports/daily/{date}_v3.md
```

### 关键模块

| 模块 | 行数 | 职责 |
|------|------|------|
| [config.py](config.py) | 169 | 路径自适应、`NAME_MAP` 统一管理跟踪列表、合成周期配置 |
| [signal_engine.py](signal_engine.py) | 856 | 指标公式库：EXPMA/MACD/CCI/分时出击/★买★卖/牛熊红线 |
| [update_from_tdx.py](update_from_tdx.py) | ~900 | 通达信二进制读写、增量同步、15/30/60分钟合成 |
| [update_tracking.py](update_tracking.py) | ~350 | 信号计算调度，增量/全量模式 |
| [cycle_engine.py](cycle_engine.py) | ~1200 | 三层架构 + 0-16评分 + 信号质量 + 主导量级 + 结构分析 |
| [backtest_signals.py](backtest_signals.py) | ~550 | 信号回测引擎（低点合并/50%合并/★信号独立） |
| [hht_analyzer.py](hht_analyzer.py) | ~450 | HHT 独立分析（EMD分解+瞬时频率+非预期解检测） |
| [gen_report_md.py](gen_report_md.py) | ~900 | Markdown 报告生成 |
| [scan_opportunities.py](scan_opportunities.py) | ~700 | 机会扫描（AI分析接口 + 定性判断） |
| [qa_tool.py](qa_tool.py) | ~200 | 终端盘后分析/胜率统计 |
| [tools/tdx_fetch.py](tools/tdx_fetch.py) | ~200 | pytdx API 封装（多服务器自动切换） |
| [tools/tracking_db.py](tools/tracking_db.py) | ~200 | SQLite 持久化 |

### 数据目录

```
D:\quantify-per\
├── lday/sz|sh/          ← 日线原始副本
├── one/sz|sh/           ← 1分钟线（lc1）
├── five/sz|sh/          ← 5分钟线（lc5）
├── fifteen|thirty|sixty/ ← 从5分钟合成
├── signals/tracking/
│   ├── {code}/          ← 每标的的6个周期CSV
│   │   └── {period}_signals.csv   (30列全量指标)
│   ├── cycle_report.json          (14条, 含评分/主导量级/建议)
│   ├── backtest_report.json
│   ├── score_history.json         (每日评分快照)
│   ├── hht_report.json
│   └── tracking_db.sqlite
├── reports/daily/       ← 每日报告
├── gbbq/                ← 除权数据
└── prompts/trading_persona.md  ← AI风格模板
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

### 信号 CSV（30 列）

每列含义按 [signal_engine.py:490-521](signal_engine.py#L490-L521) 生成：
`timestamp, date, open, high, low, close, expma12, expma50, macd_dif, macd_dea, macd_hist, trend_line, bb_ma221, bb_red_line, red_line_cross, buy_signal, sell_signal, expma_cross, cci, cci_extreme, cci_retreat, cci_divergence, ma5, ma10, ma20, ma60, ma120, ma250, volume, amount`

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

### 信号质量递进（买侧5维）

1. ★买密集度(+0.5~1.5) → 2. EXPMA金叉跟随速度(+0.3~1.5) → 3. 底部抬升(+1.0) → 4. 闭环成对(+0.3~1.0) → 5. MA5/10金叉确认(+0.3~1.2)

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

- **修改必写日志**：改前/改后代码片段写入 `C:\Users\Administrator\.claude\projects\C--Users-Administrator\archives\`
- **通达信格式不猜**：必须查 `D:\miniconda3\Lib\site-packages\pytdx\reader\` 源码
- **数据只读快照**：信号查询走 CSV/SQLite，禁止从源文件重算
- **日志输出**：各模块统一 `print()` 输出到 stdout，无 logging 配置

## 八、相关路径

- 通达信源：`C:\zd_cjzq\vipdoc\`
- 记忆/归档：`C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\` + `archives\`
- pytdx 源码：`D:\miniconda3\Lib\site-packages\pytdx\reader\`

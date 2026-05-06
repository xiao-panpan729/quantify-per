# architecture.md — quantify-per 系统架构

**更新**: 2026-04-29
**项目**: quantify-per 量化交易信号系统

---

## 一、系统定位

quantify-per 是一个**基于通达信本地数据的多周期信号计算与 AI 报告生成系统**。不联网，所有数据来源于通达信 `.day`（日线）和 `.lc5`（分钟线）文件。

---

## 二、数据流

```
通达信 ↓（盘后下载）
  ↓
.day / .lc5 文件
  ↓（pytdx 读取）
CSV 缓存 / SQLite
  ↓（signal_engine.py 计算）
多周期信号快照 CSV
  ↓（update_tracking.py 管理）
signals/tracking/{code}/{period}_signals.csv
  ↓（scan_opportunities.py 分析）
  ├─ 闭环检测 → closes.json
  ├─ 多级别嵌套分析
  └─ 每日报告 → reports/daily/YYYYMMDD.md
```

---

## 三、模块关系

| 模块 | 文件 | 职责 | 数据输入 | 数据输出 |
|:---|:---|:---|:---|:---|
| 数据同步 | `update_from_tdx.py` | 通达信→CSV缓存 | `.day`/`.lc5` | `data/*.csv` |
| 信号引擎 | `signal_engine.py` | 计算指标+信号 | `data/*.csv` | `signals/tracking/*.csv` |
| 跟踪管理 | `update_tracking.py` | 管理11只标的的快照 | `signals/tracking/*.csv` | 同上（增量更新） |
| 机会扫描 | `scan_opportunities.py` | 报告+闭环+嵌套分析 | `signals/tracking/*.csv` | 报告/JSON |
| 筹码选股 | `chips_selector.py` | 筹码集中度筛选 | data/ | 候选列表 |
| AI分析 | `ai_analyzer.py` | API调用生成智能分析 | 报告.md | 分析.md |

**关键原则**：信号引擎**只算一次**，后续模块**读取快照CSV**，不复算。

---

## 四、核心数据结构

### 4.1 信号快照 CSV（`{period}_signals.csv`）

| 列 | 说明 | 来源 |
|:---|:---|:---|
| `timestamp` | YYYYMMDDHHMM | 原始K线 |
| `raw_close` | 真实价格（/10000 已处理） | 原始K线 |
| `trend_line` | 分时出击趋势线 | signal_engine |
| `fenshi_signal` | 分时出击买/卖信号 | signal_engine |
| `expma_cross` | EXPMA 金叉/死叉 | signal_engine |
| `macd_dif/macd_dea` | MACD 值 | signal_engine |
| `cci` | CCI 值 | signal_engine |
| `cci_extreme` | CCI 极值标签（±200/±250/±300） | signal_engine |
| `cci_retreat` | CCI 回撤 | signal_engine |
| `cci_divergence` | CCI 背驰（底背驰/顶背驰） | signal_engine |
| `buy_signal` | ★买信号（星级） | signal_engine |
| `sell_signal` | ★卖信号 | signal_engine |

### 4.2 闭环 JSON（`closes.json`）

```json
{
  "code": "sz159740",
  "last_update": "202604291840",
  "buy_closings": [
    {
      "type": "buy_closing",
      "level": "5分钟",
      "level_key": "min5",
      "timestamp": "202604281420",
      "price": 6110,
      "score": 4.0,
      "level_label": "✅✅ 大级别闭环",
      "conditions": ["CCI极值(-200)", "CCI背驰(底背驰)", "★买", "价格创极值"],
      "trend_before": "D",
      "cci_before_signal": true,
      "has_price_extreme": true
    }
  ],
  "sell_closings": [],
  "reverse_signals": []
}
```

---

## 五、闭环评分系统

| 条件 | 分值 | 说明 |
|:---|:---:|:---|
| CCI 极值 | 1 | -200/+200 及以上 |
| CCI 背驰 | 1 | 底背驰/顶背驰 |
| ★买/★卖 | 1 | 核心信号 |
| EXPMA 交叉 | 1 | 金叉/死叉 |
| 价格创极值 | 0.5 | 加分项 |
| 背驰在信号之前 | 0.5 | 标准流程加分 |
| 背驰在信号之后 | -0.5 | 时序瑕疵扣分 |

| 总分 | 等级 |
|:---:|:---|
| ≥4 | ✅✅ 大级别闭环 |
| 3-3.9 | ✅ 完整闭环 |
| 2-2.9 | ⚠️ 部分闭环 |
| 1-1.9 | 👀 观测信号 |

---

## 六、趋势分级

| 级别 | 条件 | 最小可操作周期 |
|:---|:---|:---|
| A 最强 | EXPMA白线上方 + MACD>0 | 5分钟 |
| B 次强 | 白线-黄线间 + MACD>0 | 5分钟 |
| C 偏弱 | 黄线下方 + MACD>0 | 15分钟 |
| D 弱势 | MACD<0 | 30分钟+ |

---

## 七、多级别嵌套分析

**核心逻辑**：日线→60分→30分→15分→5分，逐层嵌套。

```
日线（15-30天方向）
  ↓ 决定
60分钟（一周走势）
  ↓ 辅助验证
30分钟
  ↓ 决定1-3天
15分钟
  ↓ 日内操作
5分钟
```

**权重规则**（D/C做多时）：
- 第一次60分钟买入闭环 = 观望（权重30%）
- 第二次60分钟买入闭环 = 可准备试错（权重70%）
- 有30/15分钟共振 → 加速确认

---

## 八、关键文件路径

| 内容 | 路径 |
|:---|:---|
| 信号快照 | `D:\quantify-per\signals\tracking\{code}\{period}_signals.csv` |
| 闭环数据 | `D:\quantify-per\signals\tracking\{code}\closes.json` |
| 每日报告 | `D:\quantify-per\reports\daily\YYYYMMDD.md` |
| 判断日志 | `D:\quantify-per\reports\judgement_log.csv` |
| 上下文约束 | `D:\quantify-per\prompts\trading_persona.md` |
| 日线缓存 | `D:\quantify-per\data\day\{code}.csv` |
| 分钟线缓存 | `D:\quantify-per\data\min5\{code}.csv` |

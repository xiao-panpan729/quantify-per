# integration-guide.md — 集成指南

**更新**: 2026-04-29
**受众**: 第一次接触 quantify-per 的 AI Agent

---

## 一、快速安装

**前置条件**:
- Python 3.10+
- 通达信客户端（金长江版，`C:\zd_cjzq\`）
- 已下载日线和分钟线数据

**不需要 pip install**：所有依赖在 `D:\quantify-per\tools\` 中打包。

---

## 二、核心流程

### 2.1 每日盘后运行

```bash
# 一键完成：同步 + 信号计算 + 报告
D:\quantify-per\run_daily.bat
```

自动执行：
1. `update_from_tdx.py` — 同步通达信数据到 CSV
2. `update_tracking.py` — 计算信号 → 快照
3. `scan_opportunities.py --report` — 生成 Markdown 报告

### 2.2 单独运行各模块

```bash
# 只同步数据
python D:\quantify-per\update_from_tdx.py

# 只更新某只标的的跟踪快照
python D:\quantify-per\update_tracking.py --code sz159740

# 只生成报告（依赖快照已存在）
python D:\quantify-per\scan_opportunities.py --report

# 单标的多周期详情
python D:\quantify-per\scan_opportunities.py --code sh513310

# AI 智能分析（生成 AI 版本的报告）
python D:\quantify-per\scan_opportunities.py --report --ai

# 指定日期报告
python D:\quantify-per\scan_opportunities.py --report --date 20260429

# 筹码选股
python D:\quantify-per\chips_selector.py
```

---

## 三、实时查询（AI Agent 标准协议）

当用户问"今天的信号"时，**不重新计算**，按以下顺序读取：

### 步骤 1：读报告（最快）

```python
# 今日报告
report_path = r'D:\quantify-per\reports\daily\20260429.md'

# 标题包含了机会排序、标的分析、闭环检测
```

### 步骤 2：读信号快照（可信）

```python
import csv

with open(r'D:\quantify-per\signals\tracking\sz159740\min5_signals.csv', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

# rows[-1] = 最后一根 K 线
```

### 步骤 3：读闭环数据

```python
import json
with open(r'D:\quantify-per\signals\tracking\sz159740\closes.json', encoding='utf-8') as f:
    data = json.load(f)

# data['buy_closings'] — 买入闭环列表
# data['sell_closings'] — 卖出闭环列表
```

### 标准输出模板（详见 signal-analyzer SKILL.md）

```
=== {code} 信号分析 ===

【核心信号】
★买: 时间 (星级) 

【辅助信号】
CCI: 值（极值类型）

【闭环状态】
买入闭环: 第N次（最近: 时间 评分）

【结论】
一句话结论
```

---

## 四、12 只跟踪标的

跟踪列表从 `config.py` 的 `NAME_MAP` 自动生成，加/删/改名只需维护 `config.py`，`update_tracking.py` 自动适配。

| 代码 | 名称 | 类型 |
|:---|:---|:---|
| `sz159740` | 恒生科技ETF大成 | ETF |
| `sh520600` | 港股通汽车ETF广发 | ETF |
| `sh513120` | 港股创新药ETF广发 | ETF |
| `sz159326` | 电网设备ETF华夏 | ETF |
| `sh513310` | 中韩半导体ETF | ETF |
| `sh588200` | 科创芯片ETF | ETF（5/6 新增） |
| `sz002261` | 拓维信息 | 个股（华为云） |
| `sz300118` | 东方日升 | 个股（太空光伏） |
| `sz000100` | TCL科技 | 个股 |
| `sz002129` | TCL中环 | 个股 |
| `sh600438` | 通威股份 | 个股 |
| `sh601012` | 隆基绿能 | 个股 |

**注意**: 跟踪标的已从纯 ETF 扩展为 ETF + 个股混合，`latest.json` 输出包含 `name` 字段。

---

## 五、数据来源规则（铁律）

1. **pytdx 是通达信格式的唯一权威** — 不用 Python 内置 struct 或 wencai
2. **信号只读快照，不复算** — `signals/tracking/*.csv` 是只读的
3. **日线 /1000，分钟线 /10000** — 价格缩放因子必须除
4. **时间戳精度** — `get_file_date()` 返回 float，同一天多次修改可区分
5. **闭环数据用 JSON** — `closes.json` 可以增量合并

---

## 六、常见操作

### 用户问：今天有没有信号？

```python
# 1. 确认今天报告是否存在
# 2. 如果 no → 运行 --report 先
# 3. 如果 yes → 读报告 + 读 closes.json
# 4. 输出结论
```

### 用户问：怎么看 XXX？

```python
# 1. 读 daily_signals.csv 看日线级别
# 2. 读 min60_signals.csv 看周趋势
# 3. 读 min30/min15/min5 逐层往下
# 4. 读 closes.json 看闭环
# 5. 按多级别嵌套分析输出
```

### 用户问：有没有闭环？

```python
# 直接读 closes.json
# 看 buy_closings 和 sell_closings 的长度
# 按倒序输出最近3条
```

---

## 七、错误排查

| 症状 | 可能原因 | 处理 |
|:---|:---|:---|
| 报告说"无数据" | 通达信还没下载盘后数据 | 先打开通达信下载 |
| CCI 值巨大 | 价格缩放因子未除 | `/10000` |
| 信号漏了 ★买 | 时序窗口太短 | 检查 `look_forward` 参数 |
| 闭环数据不对 | 没运行过 `--report` | 先运行 scan_opportunities.py |
| 时间戳全是同一天 | `get_file_date()` 返回 YYYYMMDD 字符串 | 改为返回 float |

# 实时 ★买/★卖 监控脚本 — 设计文档

## 一、目的

盘中实时扫描 volume_leader_universe，发现 ★买/★卖 闭环信号 → Windows 弹窗通知 + 记录交易样本供回测。

## 二、架构总览

```
盘后（已有）                          盘中（新增）
volume_leader_screener ─┐
  ├─ T1/T2/T3 排名       │         pytdx (180.153.18.170:7709)
  ├─ universe 入库       │              │
  └─ 信号全量计算 ✓      │         ★ fallback: 本地 .lc5
                         │              │
signal CSV (只读源)  ←───┘    ┌─────────┘
                         │    │
                    ┌────┴────┴────┐
                    │  实时监控脚本  │
                    │  monitor.py   │
                    └────┬────┬────┘
                         │    │
                    弹窗通知  交易记录
                    (Windows)  (JSONL)
                           │
                    回测模块消费 ←── 未来
```

## 三、数据流

### 3.1 候选池

- 来源：`signals/tracking/volume_leader_universe.json` → `universe` 字段
- 排序：按 T1 > T2 > T3 梯队顺序扫描，同梯队内按成交额排名
- 不设自动淘汰，靠盘后 screener 自然流动
- 首批约 37 只，预估上限 100-200 只

### 3.2 数据获取（主通道）

```python
# pytdx get_security_bars
# 5分钟:  category=0, market=0(sz)/1(sh)
# 1分钟:  category=7 (仅用于时间戳对齐)
# 服务器: 180.153.18.170:7709
```

每轮只拉**今天的增量** bar（通过时间过滤，避免重复拉全量）。

### 3.3 数据获取（备用通道）

当 pytdx 连接失败时，降级读取本地 `.lc5` 文件：
- 路径：`C:/zd_cjzq/vipdoc/{mkt}/fzline/{mkt}{code}.lc5`
- 读取：`pytdx.reader.TdxMinBarReader().get_df()`
- 前提：用户已在通达信中下载当天 5 分钟数据

### 3.4 信号计算（零污染）

```
历史 CSV (读 500 根) ─┐
                       ├→ 内存 DataFrame → 全量重算信号
pytdx 今天增量 bar  ──┘
                       │
                       ✗ 不写回 CSV
                       ✗ 不写回 SQLite
                       ✗ 不修改任何历史文件
```

- 读 500 根历史 bar 确保指标窗口充足（CCI 14、EXPMA 50、MACD 各参数）
- 当天新 bar 追加后全量重算（numpy 向量化，毫秒级）
- 所有计算结果仅存内存，脚本退出即释放

### 3.5 周期合成

- 5 分钟 → 15 分钟：每 3 根 5min bar 合成 1 根 15min bar
- 5 分钟 → 30 分钟：每 6 根 5min bar 合成 1 根 30min bar
- 合成逻辑复用 `update_from_tdx.py` 现有代码

## 四、信号判定

### 4.1 准入条件（满足其一即可进入评分）

```
★买 + EXPMA金叉   ← 买侧最低门槛
★卖 + EXPMA死叉   ← 卖侧最低门槛
```

单有 ★ 信号没有金叉/死叉确认 → 只记录不评分。

### 4.2 评分函数

**直接复用** `cycle_engine/indicators.py:signal_quality()`，不另起炉灶。

输入：`anchors`（`extract_anchors(rows)`） + `raw_rows`（历史500根+今天增量） + `position` + `trend` + `trend_pe`
输出：`{level, label, buy_level, sell_level, net_score, details}`

### 4.3 八维评分体系（买侧）

| # | 维度 | 分值 |
|---|------|------|
| 1 | ★买密集度（波内计数） | +0.5~1.5 |
| 2 | 金叉跟随速度（★买→金叉间隔） | +0.3~1.5 |
| 3 | 底部抬升（★买低点方向） | +1.0 |
| 4 | 闭环成对（★买+金叉配对次数） | +0.3~1.0 |
| 5 | MA5/10金叉确认（短期趋势验证） | +0.3~1.2 |
| 6 | PE结构突破（排列熵方向确认） | +1.0~1.5 |
| 7 | 量能确认（超卖三件套或非超卖三选一） | +0.3~1.5 |
| 8 | 趋势线上穿0（极端超卖反转） | +0.8 |
| **满分** | | **~10.0** |

### 4.4 八维评分体系（卖侧，镜像对称）

| # | 维度 | 分值 |
|---|------|------|
| 1 | ★卖密集度（波内计数） | +0.5~1.5 |
| 2 | 死叉跟随速度（★卖→死叉间隔） | +0.3~1.5 |
| 3 | 顶部下移（★卖高点方向） | +1.0 |
| 4 | 闭环成对（★卖+死叉配对次数） | +0.3~1.0 |
| 5 | MA5/10死叉确认（短期趋势验证） | +0.3~1.2 |
| 6 | PE结构突破（排列熵方向确认） | +1.0~1.5 |
| 7 | 量能确认（超买三件套或非超买放量阴线） | +0.3~1.5 |
| 8 | 趋势线下穿100（极端超买反转） | +0.8 |
| **满分** | | **~10.0** |

### 4.5 等级标签

买侧和卖侧使用不同的标签体系，面向操作决策：

**买侧：**

| 评分 | 等级 | 含义 |
|------|------|------|
| ≥ 8.0 | **强势出击** | 多周期共振+地量确认，确定性最高 |
| ≥ 6.0 | **出击买入** | 金叉确认+多维度验证 |
| ≥ 4.0 | **买入做T** | 金叉确立，基础强度合格 |
| ≥ 2.0 | **试错信号** | 金叉出现但验证不足，小仓位试探 |
| ≥ 1.0 | **信号弱** | ★买出现未闭环 |
| < 1.0 | **无信号** | — |

**卖侧：**

| 评分 | 等级 | 含义 |
|------|------|------|
| ≥ 8.0 | **离场观望** | 多周期共振+放量确认，确定性最高 |
| ≥ 6.0 | **准备离场** | 死叉确认+多维度验证 |
| ≥ 4.0 | **调整信号** | 死叉确立，减仓做T |
| ≥ 2.0 | **短期回踩** | 死叉出现但验证不足，观察 |
| ≥ 1.0 | **多头趋势** | ★卖出现但未闭环，上涨中的杂音 |
| < 1.0 | **持有看涨** | 无卖出信号，维持看涨 |

**弹窗阈值：≥ 4.0**，买卖两侧通用。

### 4.6 扫描周期

- 5 分钟 K 线：每 5 分钟（≈ 新 bar 生成时）扫描一次
- 15/30 分钟 K 线：每次扫描附带检查合成周期

## 五、弹窗通知

### 5.1 弹窗格式

```
═══ 强势出击 ★买 ══════
sz159740 恒生科技ETF
评分：8.5  |  价格：0.607
5分钟级别  |  14:35
★★买密集(3次/波) + 金叉跟随快 + PE结构上破
+ 趋势线上穿0
═══════════════════════

═══ 调整信号 ★卖 ══════
sz000021 深科技
评分：4.5  |  价格：15.32
15分钟级别  |  10:15
★卖连续 + 死叉跟随正常 + 顶部下移
═══════════════════════
```

### 5.2 实现方式

Windows `win10toast` 或 `plyer` 库的系统通知。备选：`MessageBox` 弹窗。

### 5.3 去重

同一标的同一方向同一周期，5 分钟内不重复弹窗。

## 六、交易记录

### 6.1 格式

JSONL 追加写入，不覆盖历史。买卖共用同一格式：

```json
{"time":"2026-05-26 14:35:00","code":"sz159740","name":"恒生科技ETF","direction":"buy","period":"min5","price":0.607,"score":8.5,"label":"强势出击","buy_level":8.5,"sell_level":0,"net_score":8.5,"details":["★买密集(3次/波)","金叉跟随快(gap=2)","底部抬升(100%)","闭环2对","★结构上破(熵=0.62)","超卖量能百日地量+地量堆(1.0)","趋势线上穿0(极端反转)"]}
{"time":"2026-05-26 10:15:00","code":"sz000021","name":"深科技","direction":"sell","period":"min15","price":15.32,"score":4.5,"label":"调整信号","buy_level":0,"sell_level":4.5,"net_score":-4.5,"details":["★卖连续(2次/波)","死叉跟随正常(gap=8)","顶部下移(100%)","闭环2对"]}
```

### 6.2 路径

`signals/tracking/realtime_trades.jsonl`

## 七、内存安全

- 单只标的：500 bar × ~30 列 × 2 周期 ≈ 30KB（DataFrame）
- 37 只标的：约 1.1MB
- 200 只标的：约 6MB
- 全量重算耗时：37 只 × 2 周期 × 向量化 numpy ≈ **< 2 秒**

结论：不会爆内存，不会卡顿。

## 八、容错

| 故障 | 处理 |
|------|------|
| pytdx 连接失败 | 降级读本地 .lc5 |
| 本地 .lc5 不存在 | 跳过该标的，不弹窗不崩溃 |
| 历史 CSV 不存在 | 跳过该标的（universe 里有但盘后没算过的） |
| 网络波动 | 连续 3 次失败 → 标记服务器不可用 → 切换到 .lc5 |
| 程序异常退出 | JSONL 追加写不丢已有记录 |

## 九、文件组织

收归 `tools/volume_leader/` 子包，前后端集中管理：

```
tools/volume_leader/
├── __init__.py          ← 包入口
├── shared.py            ← 共用常量、universe 读写接口、路径映射
├── screener.py          ← 盘后筛选（从 tools/volume_leader_screener.py 迁移）
├── signals.py           ← 信号计算（从 update_volume_leaders.py 迁移）
├── report.py            ← 报告生成（从 gen_volume_leader_report.py 迁移）
├── monitor.py           ← ★新建：盘中实时监控
└── fetcher.py           ← ★新建：pytdx + .lc5 双通道数据获取
```

根目录旧文件改为薄壳 import，保持向后兼容：
- `update_volume_leaders.py` → `from tools.volume_leader.signals import main`
- `gen_volume_leader_report.py` → `from tools.volume_leader.report import main`
- `tools/volume_leader_screener.py` → 移动后原路径留一个 redirect import

## 十、实现拆解

| 文件 | 职责 | 行数 |
|------|------|------|
| `tools/volume_leader/shared.py` | 共用常量、universe 读写、路径 | ~60 |
| `tools/volume_leader/monitor.py` | 主循环、调度、弹窗、日志 | ~200 |
| `tools/volume_leader/fetcher.py` | pytdx + .lc5 双通道数据获取 | ~100 |
| 迁移（screener/signals/report） | 无逻辑改动，只移位置 | ~0 |

## 十一、启动方式

```bash
# 盘中启动，Ctrl+C 退出
python -m tools.volume_leader.monitor

# 可选参数
python -m tools.volume_leader.monitor --interval 300     # 轮询间隔(秒)，默认 300
python -m tools.volume_leader.monitor --threshold 3.0    # 弹窗评分阈值，默认 3.0
python -m tools.volume_leader.monitor --no-toast         # 不用系统通知，用控制台打印
```

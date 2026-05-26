# 三层机制详解：节奏 · 共振 · 主导量级

> 本文档用 sh520600 2026/3~5 的一波完整行情为线索，拆解 cycle_engine 里三个核心机制（节奏完整性检查、跨周期共振扫描、主导量级检测）各自在行情不同阶段起了什么作用。
>
> **本文档可修改**——随着框架迭代，里面的判断标准和案例都可以更新。

---

## 一、三个机制的代码定位

```
analyze() — 主函数 (engine.py)
  │
  ├── judge_position()        → 位置 (高位/低位/超跌)
  ├── judge_trend()           → 方向 (0-14评分)
  │
  ├── analyze_period() × 6    → 各周期信号质量
  │     │
  │     ├── signal_quality()  → 5维递进评分
  │     └── analyze_trend_pe()→ 排列熵结构分析
  │
  ├── detect_dominant_cycle() → ① 主导量级 (波峰间距法)
  │
  ├── check_rhythm_integrity()→ ② 节奏完整性 (30分战术/日线战略)
  ├── scan_resonance()        → ③ 共振扫描 (5+15分钟)
  │
  ├── [跨周期增强/压制]       → 节奏+共振 → 调整各周期 buy/sell_level
  ├── [5+15共振增强]          → 共振confirmed → ×1.2
  │
  └── _grade_trend_signal()   → 进入ABCD阶梯 (用节奏裁决选分支)
```

三个机制各自输出什么、流向哪里：

```
check_rhythm_integrity()
  → 输出: verdict (intact/tactical_broken/strategic_broken/fully_broken)
  → 流向1: engine.py 的跨周期增强/压制 (买/卖强度±30%~50%)
  → 流向2: scan_resonance() 决定"检查同向还是反向"
  → 流向3: grading.py 的ABCD阶梯选分支 (intact→A, broken→降级)

scan_resonance()
  → 输出: {resonance_confirmed, side(buy/sell/buy_reversal/sell_reversal)}
  → 流向1: engine.py 5+15共振增强 (×1.2)
  → 流向2: grading.py 的resonance_score参数

detect_dominant_cycle()
  → 输出: {dominant_cycle, stretched_periods[]}
  → 流向1: engine.py: actual_min_idx = max(ABCD级别, 主导量级)
  → 流向2: grading.py: wait_condition + 小级反向暂不采信
```

---

## 二、sh520600 六阶段全景

先给整波行情的阶段划分，作为三个机制分析的共同坐标系：

```
价格
1.34 ┤   ┌─── P2 筑顶
     │  ╱ │        ┌─── P4 反弹诱惑
1.30 ┤╱   │  P3   ╱
     │    │ 下跌 ╱
1.26 ┤    │ 确认╱      ┌─── P5 二次下跌
     │    │  ┌─        │
1.22 ┤  P1 │ ╱         │        ┌─── P6 超跌
     │ 上升│╱          │        │
     └────┴──┬─────────┴────┬───┴──→
       ~4/10 4/13~4/22 4/23~4/28 5/7~5/12 5/13~5/19 5/19+
```

| 阶段 | 价格区间 | 方向 | 关键事件 |
|------|---------|------|---------|
| P1 上升期 | 1.22→1.34 | bullish | 日线金叉，MACD持续放大，30/60全金叉 |
| P2 筑顶期 | 1.32~1.34 | bullish→neutral | MACD连续缩脚，CCI从138降到9，4/22 30分死叉 |
| P3 反转确认 | 1.34→1.24 | bearish | MACD转负，60分死叉(4/23)，4天跌6% |
| P4 反弹诱惑 | 1.24→1.29 | bearish | 30分5/7金叉，价格反弹，60分仍死叉 |
| P5 二次下跌 | 1.29→1.18 | bearish | 30分5/13死叉，日线5/18死叉 |
| P6 超跌区 | 1.18附近 | bearish | CCI=-289/-252，全周期死叉共振 |

---

## 三、节奏 (Rhythm) — 趋势完整性的总开关

### 3.1 判定规则

节奏在**两个固定级别**上检查交叉方向是否和当前趋势方向一致：

```
战术节奏 = 30分钟 (比日线敏感，先出信号)
战略节奏 = 日线 (慢，但一旦变了就是大级别的)

上涨趋势: 最近交叉是金叉=节奏完整，死叉=节奏破坏
下跌趋势: 最近交叉是死叉=节奏完整，金叉=节奏破坏

四级裁决:
  intact              = 战术完好 + 战略完好
  tactical_broken     = 战术破坏 + 战略完好
  strategic_broken    = 战略破坏 + 战术完好
  fully_broken        = 战术破坏 + 战略破坏
```

节奏线和旋律线（EXPMA12/50的价格位置关系）当前默认返回 True，尚未启用精确比较。

### 3.2 在 sh520600 各阶段的表现

#### P1 上升期 — 节奏不干活

```
30分钟: 4/1金叉 → tactical intact ✓
日线:   金叉完好 → strategic intact ✓
裁决: intact
```

上涨趋势+节奏intact=所有信号正常通过。节奏在P1就是"不干扰"。

**流向 grading.py**: 上涨阶梯→非极端→intact→A-级"顺势做多"
**流向 跨周期增强**: 买强度+buy共振→买强度提升+20~30%

#### P2 筑顶期 — 节奏第一次干活

```
4/22: 30分钟死叉
方向=bullish, 最近交叉=死叉 → cross_ok=False
战术节奏: broken ❌
战略节奏(日线): still金叉 ✓
裁决: tactical_broken
```

节奏让框架从"顺势做多"降级为"持有/减仓"——这是旧系统完全没有的输出。

**流向 grading.py**: 上涨阶梯→非极端→tactical_broken→**B级"持有/减仓"**
**流向 跨周期增强**: 节奏破坏→买强度被大级别卖信号压制(降30~50%)

#### P3 反转确认 — 节奏翻转判断标准

```
4/23: 方向从bullish→bearish

节奏的判断逻辑跟着翻转:
  对bearish: 死叉=完整(趋势稳定下跌)，金叉=破坏(趋势松动)
  
  30分钟: 最近是死叉(4/22) → tactical intact(对下跌而言是好的) ✓
  日线:   还在金叉(死叉没到5/18) → 对下跌来说金叉=交叉方向不对
          last_golden > last_dead → cross_ok=False
          战略节奏: broken ❌
  裁决: strategic_broken
```

**关键理解**: 方向一翻转，节奏的"好/坏"定义跟着翻转。日线金叉在上涨时是"完整"的标志，在下跌时是"趋势还没完全进入空头节奏"的标志。

#### P4 反弹诱惑 — 节奏的核心贡献

```
5/7: 30分钟金叉
方向=bearish, 最近交叉=金叉

30分钟: bearish+金叉 → cross_ok=False → tactical broken ❌
日线:   still金叉(5/18才死叉) → bearish+金叉 → strategic broken ❌
裁决: fully_broken
```

5/7出30分钟金叉时，节奏系统对它的评价是：**"反向扰动，不是反转"**。

因为没有节奏检查，旧系统在这里输出了"有机会做反弹"。有了节奏检查，30分钟金叉在bearish方向下被视为"节奏破坏"而不是"做多信号"。

**流向 grading.py**:
```
下跌阶梯→非极端→fully_broken
  → 检查has_30_60_golden? 30分金叉但60分死叉 → NO
  → 输出: C级"关注"(不等同于可操作)
```

### 3.3 节奏的作用边界

节奏不产生价格信号，它只决定**信号怎么被解读**：

```
intact:          所有信号×1.0 (正常通过)
tactical_broken: 小级别同向信号轻微压制，大级别不受影响
strategic_broken: 封顶C级，等日线修复才能回到B级+
fully_broken:    封顶C级，需要30-60金叉共振才能回到B级
```

节奏本身不会说"做多"或"做空"，它只会告诉grading走哪个分支。

---

## 四、共振 (Resonance) — 信号的二次验证

### 4.1 判定规则

```
检查 5分钟 + 15分钟 是否有同向闭环 (buy_level≥2.0 / sell_level≥2.0)

节奏intact或tactical_broken时:
  - 方向=bullish: 检查5+15买信号 → buy共振
  - 方向=bearish: 检查5+15卖信号 → sell共振

节奏strategic_broken或fully_broken时:
  - 方向=bullish: 检查5+15卖信号 → sell_reversal(反转预警)
  - 方向=bearish: 检查5+15买信号 → buy_reversal(反转预警)
```

### 4.2 在 sh520600 各阶段的表现

| 阶段 | 方向 | 节奏 | 5+15状态 | 共振输出 | 实际影响 |
|------|------|------|----------|---------|---------|
| P1 | bullish | intact | 5★买+15★买 | **buy confirmed** | 买强度×1.2，进A级条件 |
| P2 | bullish | tactical_broken | 5★买+15★卖 | 未确认 | 无影响 |
| P3 | bearish | strategic_broken | 5有买15弱 | 未确认 | 无影响 |
| P4 | bearish | fully_broken | 5★买+15★买 | **buy_reversal** | 标记反转预警，但60死叉压住 |
| P5 | bearish | fully_broken | 5★卖+15★卖 | **sell confirmed** | 卖强度增强，进D-条件 |
| P6 | bearish | strategic_broken | 5★卖+15★卖 | **sell confirmed** | 强化D-结论 |

**P4是共振最有信息量的阶段**——它检测到了5+15买共振，但节奏是fully_broken，所以输出buy_reversal而不是buy。grading.py里这个buy_reversal要经过has_30_60_golden的检查才能生效，而60分死叉中→不通过→仍然是C级。

**共振不独立决策**——它只提供信息，由grading决定用不用。

### 4.3 共振的实际效果计算

在 engine.py 中，共振确认后有两类操作：

**5+15共振增强** (L321-342):
```python
# 只有当 resonance_confirmed=True 时才触发
if res_side == 'buy':
    m5_buy_level *= 1.20   # 5分钟买强度+20%
    m15_buy_level *= 1.20  # 15分钟买强度+20%
```

**跨周期共振辅助** (在 grading.py 的 _detect_resonance 中):
```python
# 用于grading的resonance_score计算
if short_golden and mid_golden:
    resonance_score = 0.8  # 多周期金叉共振
```

---

## 五、主导量级 (Dominant Cycle) — 最小操作级别的硬底线

### 5.1 判定规则

```
波峰间距法:
  从最小级别(5分钟)开始，逐级向上检查
  每个周期取 trend_line 的波峰，量间距

  间距稳定(当前间距/历史均值 < 1.5倍) → 该级别是主导量级
  间距拉长(>= 1.5倍) → 上级周期在接管，继续向上查

  一旦找到稳定级别就停，不再往上查
```

### 5.2 在 sh520600 各阶段的表现

| 阶段 | 行情特征 | 波峰间距变化 | 主导量级 | 实际影响 |
|------|---------|-------------|---------|---------|
| P1 | 趋势明确，波动规律 | 5分钟间距稳定(13/14) | 5分钟 | ABCD也是5分钟，无影响 |
| P2 | 高位震荡，波动收窄 | 仍然稳定在5~15分钟 | 5分钟或15分钟 | ABCD级别可能升高 |
| P3 | 急跌，节奏打乱 | 间距可能拉长 | 可能升到30分钟 | **开始过滤5分钟信号** |
| P4 | 弱势反弹，结构不稳 | 波峰不清晰 | 可能继续30分钟 | 小级别买入信号不参与选最佳 |
| P5 | 续跌，趋势明确 | 可能稳定在15~30分钟 | 30分钟 | 维持过滤 |
| P6 | 超跌区，全周期死叉 | 全部死叉中 | 5分钟(最新) | D-回避 |

### 5.3 主导量级的两个影响路径

**路径1: 实际最低操作级别** (engine.py L371)

```python
actual_min_idx = max(abcd_min_idx, dominant_idx)
```

假设 ABCD 给了 D 级(要求 min30+)，dominant 是 5 分钟级：
- actual_min = max(3, 1) = 3 → 仍然要 30 分钟级
- 5分钟信号不进入"最佳周期"候选

假设 ABCD 给了 A 级(允许 5 分钟级)，dominant 是 30 分钟级：
- actual_min = max(1, 3) = 3 → 强制提高到 30 分钟
- 即使用户想做 5 分钟短线，系统也会挡回来

**路径2: 小级反向暂不采信** (grading.py 的 dominant_note)

```python
stretched = dominant_info.get('stretched_periods', [])
if stretched:
    # 有被判定为"拉长"的级别
    if bearish:
        dominant_note = '5分钟主导(小级买信号暂不采信)'
```

这个在报告里显示为一条提示，比如"5分钟主导(小级买信号暂不采信)"。

---

## 六、三机制协同全景图

用 sh520600 整波行情，标注每个阶段三机制的状态和输出：

```
         P1上升    P2筑顶    P3反转    P4反弹     P5续跌    P6超跌
         ~4/10    4/13-22   4/23-28   5/7-12    5/13-19   5/19+
         ─────    ─────    ─────    ─────     ─────    ─────
方向     bullish  →neutral  bearish   bearish   bearish   bearish

节奏     intact   tactical  strategic fully      fully     strategic
                  _broken   _broken   _broken    _broken   _broken

共振     buy      —         —         buy_       sell      sell
         confirmed           (检测中)  reversal   confirmed confirmed

主导     5分钟    5-15分    15-30分   30分级     30分级    5分钟
量级                        级(提升)  (维持)     (维持)   (最新)
         ─────    ─────    ─────    ─────     ─────    ─────
grading  A-       B         D-       C         D-       D-
输出     顺势做多  持有/减仓  回避     关注(不动)  回避     回避
```

### 关键观察

1. **P2 筑顶期 — 节奏先于价格给出信号**
   30分钟死叉 → tactical_broken → B级"减仓"。价格还在1.32，没有跌，但节奏已经说"有问题"。

2. **P4 反弹诱惑 — 三机制联合过滤**
   - 节奏说 fully_broken（不给操作许可）
   - 共振检测到 buy_reversal 但过不了60分钟死叉关
   - 主导量级升到30分钟级（小级别信号不参与选最佳）
   - 最终输出 C级"关注"——三个机制从不同角度同向发力，阻止了错误入场

3. **P6 超跌区 — D假的可能性**
   如果未来30分钟金叉先于日线出现，且60分钟仍死叉：
   - 节奏: 30分金叉(bearish→tactical broken), 日线死叉(strategic intact)
   - 裁决: tactical_broken (而不是当前的strategic/fully broken)
   - 共振: 如果5+15同时有买信号且节奏是tactical_broken→同向买共振
   - grading: 下跌阶梯→超跌区→tactical_broken→走check_30_60_golden/resonance
   - **如果60分钟仍然死叉→还是C级等待**
   - 只有30分钟和60分钟都修复了，才可能回到B级以上

### 三机制的角色一句话总结

| 机制 | 一句话 | 类比 |
|------|--------|------|
| 节奏 | **趋势完整性总开关**——坏了就降级，不商量 | 路况：路好开快，路烂开慢 |
| 共振 | **信号的二次验证**——两个小级别同向才确认 | 复检：一个人说不够，两个人都说才信 |
| 主导量级 | **操作级别的硬底线**——不能小于这个级 | 最小排量：赛车不能低于1.6L |

---

## 七、需要记住的限制

1. **节奏线和旋律线的价格位置检查未启用**。当前`rhythm_line_ok = melody_line_ok = True`只靠交叉判断。未来如果启用精确比较（比如价格是否有效跌破EXPMA50），节奏裁决会更敏感。

2. **共振只在5+15两个级别上扫描**。30分钟以上的共振通过grading.py的_detect_resonance独立计算，和scan_resonance是两套逻辑。

3. **主导量级依赖trend_line的波峰质量**。在震荡行情或trend_line走平时，波峰检测可能不准确（波峰不足3个就跳到上一级）。

4. **这三个机制都是"减法"不是"加法"**——它们只能压制或降级，不能创造信号。一个标的如果本身没有★买信号，节奏intact也没用。

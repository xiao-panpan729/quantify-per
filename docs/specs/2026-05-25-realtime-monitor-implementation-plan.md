# 实时监控脚本 — 实现计划

## Phase 1: 包结构 + shared.py

### 创建 `tools/volume_leader/__init__.py`
空文件，标记为 Python 包。

### 创建 `tools/volume_leader/shared.py`
共用模块，包含：

```python
# 常量
PYTDX_HOST = '180.153.18.170'
PYTDX_PORT = 7709
UNIVERSE_PATH = 'signals/tracking/volume_leader_universe.json'
TRADES_PATH = 'signals/tracking/realtime_trades.jsonl'
LOOKBACK_BARS = 500

# 函数
def load_universe() -> list  # 读 universe JSON，返回 [{code, name, tier}, ...]，按 tier 排序
def append_trade(record: dict)  # 追加一行到 JSONL
def dedup_key(code, direction, period) -> str  # 生成去重键
```

**依赖**：标准库 json + pathlib，不依赖 pytdx/signal_engine。

---

## Phase 2: 数据获取 — fetcher.py

### 创建 `tools/volume_leader/fetcher.py`

```python
class RealtimeFetcher:
    def __init__(self):
        self.api = None
        self.lc5_reader = TdxMinBarReader()
    
    def connect(self) -> bool  # 连接 pytdx
    
    def fetch_today_5min(code: str) -> pd.DataFrame | None
        """主通道：pytdx get_security_bars(category=0)"""
    
    def fetch_today_5min_lc5(code: str) -> pd.DataFrame | None
        """备用通道：读本地 .lc5 文件"""
    
    def get_today_bars(code: str) -> pd.DataFrame | None
        """先试 pytdx，失败降级 .lc5"""
    
    def close(self)
```

**输入**：`code`（如 `sz159740`）
**输出**：DataFrame 含 `timestamp/open/high/low/close/volume/amount`，仅今日 bar。无数据返回 None。
**依赖**：pytdx.hq.TdxHq_API、pytdx.reader.TdxMinBarReader、shared 的常量

**关键细节**：
- pytdx 需要市场前缀分解：`sz→market=0, sh→market=1`，代码取后6位
- `.lc5` 路径：`C:/zd_cjzq/vipdoc/{mkt}/fzline/{mkt}{code6}.lc5`
- 价格因子：分钟线 `/10000`

---

## Phase 3: 主循环 — monitor.py

### 创建 `tools/volume_leader/monitor.py`

```python
class Monitor:
    def __init__(self, interval=300, threshold=4.0, use_toast=True)
    
    def load_historical_csv(code: str) -> pd.DataFrame | None
        """读 signals/tracking/{code}/min5_signals.csv，取最后 LOOKBACK_BARS 根"""
    
    def compute_signals(rows: pd.DataFrame) -> pd.DataFrame
        """调用 signal_engine 的核心函数对合并后的数据重算信号列"""
    
    def scan_one(code: str)
        """单只标的完整扫描流程：
        1. fetcher.get_today_bars(code) → 今天的新 bar
        2. 拼接 historical[-500:] + today_bars
        3. compute_signals() 全量重算
        4. extract_anchors() + signal_quality()
        5. 检查 buy_level >= threshold 或 sell_level >= threshold
        6. 去重检查 → 弹窗 + 记录
        """
    
    def notify(record: dict)
        """Windows 系统通知"""
    
    def run(self)
        """主循环：load_universe → while True: for code in universe: scan_one(code) → sleep(interval)"""
```

**依赖**：
- `shared`: load_universe, append_trade, dedup_key
- `fetcher`: RealtimeFetcher
- `cycle_engine.indicators`: extract_anchors, signal_quality, analyze_trend_pe, judge_position, judge_trend
- `signal_engine`（或直接内联所需指标函数）

**关键问题需要实现时解决**：
1. `signal_quality()` 需要 `position`（日线位置）和 `trend`（方向）——监控用分钟线，需要用已有日线数据或做近似。**方案：从日线 CSV 读取最近一根 bar 判断位置；方向用 5 分钟 EXPMA 排列近似**
2. `trend_pe` 需要排列熵分析 → 调 `analyze_trend_pe()` 即可
3. 15/30 分钟合成：复用 `update_from_tdx.py` 的合成逻辑，或直接 numpy 重采样

### 去重逻辑

```python
_recent_alerts: dict  # key=(code, direction, period), value=timestamp
# 弹窗前检查：同一 key 的 last_alert 距现在 > 300s
```

### 弹窗实现

优先用 `win10toast`，备选 `plyer`：
```python
from win10toast import ToastNotifier
toaster = ToastNotifier()
toaster.show_toast(title, body, duration=10)
```

---

## Phase 4: 现有文件迁移

### 4a. 移动文件

| 原路径 | 新路径 |
|--------|--------|
| `tools/volume_leader_screener.py` | `tools/volume_leader/screener.py` |
| `update_volume_leaders.py` | `tools/volume_leader/signals.py` |
| `gen_volume_leader_report.py` | `tools/volume_leader/report.py` |

### 4b. 旧路径留 redirect

```python
# update_volume_leaders.py（根目录）
import sys
sys.path.insert(0, os.path.dirname(__file__))
from tools.volume_leader.signals import *
if __name__ == '__main__':
    main()
```

其他两个同理。

### 4c. 调整内部 import

迁移后 `shared.py` 是 universe 读写的唯一入口。screener.py / signals.py 的 universe 读写路径改为从 shared import。

---

## Phase 5: 测试

```bash
# 1. 导入测试
python -c "from tools.volume_leader.shared import load_universe; print(len(load_universe()))"

# 2. 数据获取测试
python -c "from tools.volume_leader.fetcher import RealtimeFetcher; f=RealtimeFetcher(); df=f.get_today_bars('sz159740'); print(df.tail(3))"

# 3. 单只扫描测试（不弹窗）
python -c "from tools.volume_leader.monitor import Monitor; m=Monitor(use_toast=False); m.scan_one('sz159740')"

# 4. 完整运行（Ctrl+C 停止）
python -m tools.volume_leader.monitor --interval 300
```

---

## 文件清单（新建+修改）

| 文件 | 动作 | 估算行数 |
|------|------|---------|
| `tools/volume_leader/__init__.py` | 新建 | 1 |
| `tools/volume_leader/shared.py` | 新建 | ~60 |
| `tools/volume_leader/fetcher.py` | 新建 | ~100 |
| `tools/volume_leader/monitor.py` | 新建 | ~250 |
| `tools/volume_leader/screener.py` | 迁移 | 0 行新增 |
| `tools/volume_leader/signals.py` | 迁移 | 0 行新增 |
| `tools/volume_leader/report.py` | 迁移 | 0 行新增 |
| `update_volume_leaders.py` | 改为 redirect | ~5 |
| `gen_volume_leader_report.py` | 改为 redirect | ~5 |
| `tools/volume_leader_screener.py` | 改为 redirect | ~5 |

**总新增代码：~400 行**

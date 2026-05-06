# operator-runbook.md — 运维手册

**更新**: 2026-04-29

---

## 一、每日操作流程

### 盘后（15:00 之后）

1. **打开通达信** → 盘后数据下载（确保日线和 5 分钟线下载成功）
2. **运行报告**：
   ```bash
   cd D:\quantify-per
   python run_daily.bat
   ```
3. 查看报告：`reports/daily/YYYYMMDD.md`（自动打开）

### 检查信号

```bash
# 查看单标的详情
python scan_opportunities.py --code sz159740

# 检查闭环数据
# 打开 signals/tracking/sz159740/closes.json
```

---

## 二、环境变量/关键配置

| 配置 | 值 |
|:---|:---|
| 通达信路径 | `C:\zd_cjzq\` |
| 项目根 | `D:\quantify-per\` |
| Python | 系统默认（3.10+） |
| 跟踪标的 | 12 只（从 `config.NAME_MAP` 自动生成，见 `config.py`） |
| 分钟线编码 | ×10000 浮点型 |
| 日线编码 | ×1000 浮点型 |

---

## 三、冒烟命令（快速验证）

```bash
# 验证语法
python -m py_compile D:\quantify-per\scan_opportunities.py

# 快速测试单标的信号读取
python -c "import csv; r=csv.DictReader(open(r'D:\quantify-per\signals\tracking\sz159740\min5_signals.csv')); print(sum(1 for _ in r), 'rows')"

# 检查闭环数据完整性
python -c "import json; d=json.load(open(r'D:\quantify-per\signals\tracking\sz159740\closes.json')); print(f'买入:{len(d[\"buy_closings\"])} 卖出:{len(d[\"sell_closings\"])} 反向信号:{len(d[\"reverse_signals\"])}')"
```

---

## 四、故障排查

### 4.1 报告不生成或出错

```bash
# 检查数据源
ls D:\quantify-per\signals\tracking\sz159740\min5_signals.csv

# 如果有文件但报告报错，尝试单标的
python D:\quantify-per\scan_opportunities.py --code sz159740
```

### 4.2 信号异常（比如 CCI 巨大）

```
CCI 从 -200 变成 -20000 → 价格缩放因子异常
修复: 确认 signal_engine.py 中价格因子为 10000（分钟线）
```

### 4.3 信号漏报

```
问题：★买出现但闭环检测没抓到
排查：
1. 看 closes.json 有没有该时间点
2. 看 CSV 中 buy_signal 列确实有值
3. 检查 look_forward 参数（默认 12，够不够）
```

### 4.4 今天报告用了昨天数据

```
问题：报告数据截止日期是昨天
原因：get_file_date() 可能返回 YYYYMMDD 字符串，同一天多次修改无法区分
修复：确认 get_file_date() 返回的是 float 时间戳
```

---

## 五、备份恢复

### 手动备份

```bash
# 备份快照和报告
xcopy D:\quantify-per\signals\tracking D:\backup\quantify-per\signals\tracking /E /I /Y
xcopy D:\quantify-per\reports D:\backup\quantify-per\reports /E /I /Y
xcopy D:\quantify-per\docs D:\backup\quantify-per\docs /E /I /Y
```

### Gitee 同步

```bash
cd D:\quantify-per
git add .
git commit -m "每日更新 2026-04-29"
git push
```

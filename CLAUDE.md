# CLAUDE.md — quantify-per 量化交易信号系统

**代码行数统计**: ~20,000 行 Python  (2026-06-08)
**当前跟踪**: 14 只标的 (2 指数 + 12 只 ETF/个股)
**文档目录**: [docs/](docs/) 含 architecture.md / integration-guide.md / operator-runbook.md / evolution_timeline.md
**实验记录**: [tools/volume_leader/experiments/filter_evolution.md](tools/volume_leader/experiments/filter_evolution.md) — 34实验/2300+行，入场过滤系统完整研究文献（每次改 filter/monitor/backtest 代码必须同步更新）
**笔记本系统**: [notebook/notebook_design.md](notebook/notebook_design.md) — 预测卡→验证→案例检索→反馈修正 项目设计说明书（对标实验报告）。**研究日志**: [notebook/research_log.md](notebook/research_log.md) — 简称"笔记研究"，每次DL框架讨论/实验/迭代必须同步更新
**信源日报**: [reports/sources/](reports/sources/) — 8公众号聚合→突发事件→流动性→宏观→US映射→基本面→AI分析。**提示词**: [prompts/source_analysis_prompt.md](prompts/source_analysis_prompt.md) + [prompts/macro_analysis_template.md](prompts/macro_analysis_template.md)
**缠论结构层**: [notebook/chanlun/](notebook/chanlun/) — 基于 czsc 的完整缠论适配层（2026-06-01新增）。[signals.py](notebook/chanlun/signals.py) 覆盖 cxt 全部 40+ 信号函数（结构/中枢/买卖点/形态/笔状态/决策 6合1），[positions.py](notebook/chanlun/positions.py) 日线+30分钟双级别联立+中枢共振，入口: `get_position()`

## 一、常用命令

```bash
# ─── ★新会话启动：按 S→A 级顺序★ ───
# S级必读：研究日志最新实验(notebook/research_log.md) + 实验报告最新(tools/volume_leader/experiments/filter_evolution.md) + 最新日报
# ★回顾窗口：读上个窗口最后几轮纯问答 → ls -t ~/.claude/projects/d--quantify-per/*.jsonl 排除当前session取最新的 → python解析type=user/assistant的message.content.text，跳过thinking/tool_use噪音
# S级必查：
python operation_tracker.py --status   # 战役状态
python -c "import json;d=json.load(open('signals/tracking/_signals/latest.json','r',encoding='utf-8'));[print(f'{k} {v[\"name\"]}: dailyCCI={v[\"daily\"][\"indicators\"].get(\"cci\",\"?\")} ★买={sum(1 for s in v[\"daily\"][\"signals\"].values() if s.get(\"buy_signal\"))}') for k,v in d['stocks'].items()]"
python -c "import json;d=json.load(open('signals/tracking/_signals/cycle_report.json','r',encoding='utf-8'));[print(f'{i[\"code\"]} {i[\"name\"]}: {i[\"trend\"][\"score\"]}分 {i[\"trend\"][\"direction\"]} → {i[\"advice\"]}') for i in d]"
# 最新报告: dir reports/daily/*_v3.md          | 最近一个
# 记忆: 读 ~/.claude/projects/d--quantify-per/memory/case-*.md（近期案例）
# 文档分类: 见 CLAUDE.md §七 文档分类与读取规则

# ─── 完整盘后流水线（按需选择） ───
run_daily.bat               # 全量14步: 数据同步 → 量领筛选+宇宙 → 信号(固+量) → 分析 → 回测 → 报告(固+量) → US市场 → 日本宏观
run_daily_timed.bat         # 快速: 数据同步 → 信号计算 → 机会扫描+报告（含耗时统计）
run_update.bat              # 仅数据: 数据同步 → 信号计算（不含分析/报告）

# ─── 分步执行（单向依赖） ───
python update_from_tdx.py              # 步骤1: 通达信→量化库（日线增量+分钟线+合成周期）
python update_tracking.py               # 步骤2: 信号计算 → CSV + latest.json + SQLite
python run_cycle.py --save              # 步骤3: 三层架构周期循环分析 → cycle_report.json
python backtest_signals.py --save       # 步骤4: 信号回测 → backtest_report.json
python gen_report_md.py                 # 步骤5: 可读 Markdown 报告
python operation_tracker.py --status    # 战役级操作追踪

# ─── 信源管理（微信公众号） ───
python _fetch_articles.py               # 批量拉取7个信源的最新文章全文 → wechat_articles/
python _search_accounts.py              # 搜索公众号 fakeid（新增信源时用）
python gen_source_summary.py            # ★信源AI日报生成（纯数据摘要，无AI）
python gen_source_summary.py --ai       # ★数据聚合完成后提示用户触发AI分析
python update_sources.bat               # ★全流程：抓取8信源→聚合一键生成

# ─── 单标的 / 单周期 ───
python update_tracking.py sz159740      # 只更新恒生科技
python run_cycle.py --code sh513120     # 只分析创新药

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
python tools/volume_leader/monitor.py --filter all   # 三级同时弹: MA级(试错)/金叉级(买)/共振级(买完)
python tools/volume_leader/monitor.py               # 默认 MA级(试错)
python tools/volume_leader/monitor.py --filter resonance  # 仅共振级(买完)
python tools/volume_leader/monitor.py --once --no-toast  # 单轮扫描（测试用）
python tools/volume_leader/monitor.py --sell-filter sell_reduce  # 仅减仓级弹窗
python tools/volume_leader/backtest.py                # ★量领回测引擎（默认6个月/全宇宙/MA级）
python tools/volume_leader/backtest.py --compare      # MA级 vs 金叉级 对比回测
python tools/volume_leader/backtest.py --code sh600176 --months 12  # 单标12个月回测
python tools/volume_leader/backtest.py --pair         # 买→卖配对交叉统计
python tools/volume_leader/backtest.py --save --detail # 保存JSON+交易明细
python gen_campaign_html.py                           # 生成战役HTML跟踪报告（ECharts K线图）
python gen_campaign_html.py --code sz159740 --days 60 # 指定标的+最近60天
python -c "from tools.volume_leader.trade_db import get_open_entries, get_stats_by_level, get_all_trades; print('持仓:', get_open_entries()); print('统计:', get_stats_by_level()); print('最近交易:', get_all_trades(10))"  # ★交易台账查询

# ─── 板块势能评分（每日盘后，量领宇宙主题增强） ───
python tools/sector_momentum.py --save                  # ★全量计算269概念板块X_1 + 构建个股板块映射缓存
python tools/sector_momentum.py --verify                # 与通达信RPS对比验证
python tools/sector_momentum.py --cache-only            # 只重建缓存（已有评分时）
python tools/sector_momentum.py --search 培育钻石        # 搜索特定板块
python -c "from tools.sector_momentum import query_stock_sector_momentum as q; print(q('600438'))"  # 查某只股的板块势能
# 缓存: signals/tracking/_macro/sector_momentum_cache.json → volume_leader_screener 自动读取作为主题强度加分

# ─── ★节点地图（板块行情区间+龙头识别+宏观逆推） ───
python tools/node_map.py --all --save            # 全量扫描270概念板块→节点地图(3750节点/210s)
python tools/node_map.py --sector 黄金概念        # 只扫指定板块
python tools/macro_history.py --min-grade B      # A/B节点宏观标注(3068节点/25s)
python tools/macro_history.py --sector 黄金概念 --dry-run  # 预览特定板块
python tools/macro_history.py --fetch-events     # 含联网事件抓取(慢)
python tools/star_buy_node_map.py --save         # ★买→节点映射+贝叶斯分组
python tools/star_buy_node_map.py --stock sh600438  # 单标的最近★买置信度
# 输出: signals/tracking/_macro/node_map.json / star_buy_node_bayes.json

# ─── ★CLAUDE.md 覆盖率检查（每次新会话/每周首次运行） ───
python -c "import os,glob; py={f for f in glob.glob('**/*.py',recursive=True) if '__pycache__' not in f}; md=open('CLAUDE.md','r',encoding='utf-8').read(); [print(f'MISS: {f}') for f in sorted(py) if f.replace(chr(92),'/') not in md and not f.startswith('_')]"

# ─── ★实验研究文献（1324行/25实验，每次改代码必须同步更新） ───
# tools/volume_leader/experiments/filter_evolution.md
#   ├─ 当前生效规格（三级买侧 + 两层卖侧 + 关键参数速查 + 代码位置）
#   ├─ 实验 #1~#25 完整记录: 假设→回测数据→结论→实盘追踪
#   └─ 最新: #25 ★买→MA追赶→站上黄线→金叉 组合信号
python tools/a_stock_screener.py        # 全A股强势+波动率排序
python tools/strong_vol_screener.py     # ETF+主流板块强势筛选

# ─── ★宏观分层过滤（板块动量 × 宏观环境 overlay） ───
python tools/macro_screener.py                  # 默认Top 30，含推荐/中性/回避标签
python tools/macro_screener.py --top 50         # Top 50
python tools/macro_screener.py --all            # 全量
python tools/macro_screener.py --top 30 --json  # JSON输出（自动化用）
python tools/macro_sensitivity.py --classify    # 仅查看宏观环境分类（不跑板块）
# 依赖: sector_momentum_cache.json + macro_sensitivity.json 均需最新

# ─── ★消息面突发事件检测（B类冲击：关税/制裁/地缘/黑天鹅） ───
python tools/sentiment/shock_detector.py         # 华尔街见闻+东财 关键词匹配 → sentiment_shock.json
# 编辑: tools/sentiment/shock_keywords.json  →  新增/修改关键词分类

# ─── ★日本宏观 × 套息交易压力（全球流动性水源地） ───
python tools/japan_macro.py                     # 日本宏观环境 + 套息压力 + 12个月轨迹
python tools/japan_macro.py --classify          # 仅分类当前环境（JSON输出）
python tools/japan_macro.py --history 24        # 最近24个月压力轨迹
python tools/japan_macro.py --save              # 保存 JSON → signals/tracking/_macro/japan_macro.json
# 三个核心因子: BOJ政策利率 / USDJPY(FXY ETF代理) / 日本核心CPI
# 合成套息交易压力指数 → 全球流动性收紧/宽松信号 → A股科技/成长板块的先行指标

# ─── ★全球流动性监控（5因子合成压力） ───
python tools/liquidity_monitor.py               # 5因子压力指数（BTC/VIX/DXY/M2/信用脉冲）
python tools/liquidity_monitor.py --history 24  # 最近24个月压力轨迹
python tools/liquidity_monitor.py --save        # 保存 JSON → signals/tracking/_macro/liquidity_monitor.json
# 因子: BTC价格 / VIX恐慌 / 美元指数 / M2同比 / 信用脉冲

# ─── ★基本面因子筛选（Rolling FM） ───
python tools/fundamental_screener.py                    # 默认全量基本面因子溢价
python tools/fundamental_screener.py --top 20           # Top20排序
python tools/fundamental/fm_pipeline.py --update        # 更新FM滚动回归数据
python tools/fundamental/growth_narrative.py --save     # 增长叙事引擎
# 依赖: fundamental_profile.json + .fundamental_cache.json 均需最新

# ─── ★US 市场→A股 跨市场映射（三层） ───
python tools/us_market/etf_momentum.py --save       # Layer 2a: US ETF 势能评分 (52只)
python tools/us_market/etf_momentum.py --search SMH # 单ETF查询
python tools/us_market/etf_momentum.py --category "Tech & AI"  # 按类别过滤
python tools/us_market/star_stocks.py --save        # Layer 2b: US 明星股动量评分 (64只)
python tools/us_market/star_stocks.py --search NVDA # 单股查询
python tools/us_market/star_stocks.py --category "Semiconductor Chain"  # 按类别过滤
python tools/us_market/concept_chains.py            # ★概念链引擎 (30条, 对标通达信概念板块)
python tools/us_market/concept_chains.py --momentum # 概念链轮动排名
python tools/us_market/concept_chains.py --search NVDA  # 查某股归属哪些概念链
python tools/us_market/concept_chains.py --coverage    # 概念链覆盖度统计
python tools/us_market/concept_chains.py --export      # 导出概念链→股票映射表
# 编辑: tools/us_market/concept_chains.json  →  新增/修改概念链（股票只需填代码）
python tools/us_market/macro_sensitivity.py --classify  # Layer 1: US宏观环境分类
python tools/us_market/macro_sensitivity.py             # 全量RollingOLS敏感度
python tools/us_market/macro_sensitivity.py --sectors 5 # 测试模式
python tools/us_market/cross_mapping.py --top 15    # Layer 3: US→A股 跨市场映射 Top15
python tools/us_market/cross_mapping.py --etf SMH   # 单ETF映射钻取
python tools/us_market/cross_mapping.py --sector 芯片 # 单板块映射钻取
python tools/us_market/cross_mapping.py --etf GDX --min-corr 0.2  # 低阈值全扫
# 输出: signals/tracking/_macro/us_sector_momentum.json / us_star_momentum.json / us_macro_sensitivity.json / us_cn_mapping.json
# 日报: reports/us_market/<date>_us_momentum.md / <date>_us_stars.md

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
    ├─ JSON → signals/tracking/_signals/latest.json
    └─ SQLite → data/tracking.db
    │
    ├─▶ run_cycle.py         ← 三层架构：位置→趋势→循环
    │   └─ signals/tracking/_signals/cycle_report.json
    │
    ├─▶ hht_analyzer.py         ← HHT独立分析 (EMD+瞬时频率)
    │   └─ signals/tracking/_signals/hht_report.json
    │
    ├─▶ backtest_signals.py     ← 信号回测
    │   └─ signals/tracking/_signals/backtest_report.json  +  backtest_trades.db
    │
    ├─▶ gen_report_md.py        ← 报告 → reports/daily/{date}_v3.md
    │
    ├─▶ operation_tracker.py    ← 战役级操作追踪
    │   └─ signals/tracking/_funds/operation_records.json
    │
    │
    ├─▶ tools/sector_momentum.py    ← ★概念板块势能 (269板块X_1评分)
    │
    ├─▶ tools/macro_screener.py     ← ★宏观分层过滤 (板块动量×宏观overlay)
    │
    ├─▶ tools/sentiment/            ← ★消息面突发事件检测 (B类: 关税/制裁/地缘 三源冗余)
    │
    ├─▶ tools/liquidity_monitor.py  ← ★全球流动性监控 (5因子合成压力)
    │
    ├─▶ tools/japan_macro.py        ← ★日本宏观+套息交易压力 (BOJ/USDJPY/CPI)
    │
    ├─▶ tools/us_market/            ← ★US市场→A股三层映射
    │   ├─ macro_sensitivity.py   ← Layer 1: US宏观→A股敏感度
    │   ├─ etf_momentum.py        ← Layer 2a: US ETF势能 (52只)
    │   ├─ star_stocks.py         ← Layer 2b: US明星股动量 (64只)
    │   ├─ concept_chains.py      ← ★概念链引擎 (30条链, ETF持仓+手动)
    │   └─ cross_mapping.py       ← Layer 3: 跨市场领先滞后映射
    │
    ├─▶ tools/fundamental/          ← ★基本面因子 (Rolling FM + 增长叙事)
    │
    ├─▶ gen_source_summary.py       ← ★信源AI日报 (8公众号聚合→分析→报告)
    │
    ├─▶ jigou_jiancang.py / chips_selector_v2.py  ← 机构建仓/选股
    │
    ├─▶ notebook/chanlun/           ← 缠论结构适配层 (czsc)
    │
    └─▶ notebook/                   ← DL研究: XGBoost/预测卡/反馈循环
```

### 关键模块

| 模块 | 行数 | 职责 |
|------|------|------|
| [config.py](config.py) | 272 | 路径自适应、`NAME_MAP` 统一管理跟踪列表、合成周期配置 |
| [signal_engine.py](signal_engine.py) | 1121 | 指标公式库(30基础列) + PE/HHT/cycle_period + 量能后处理(11列) → 总计47列 |
| [update_from_tdx.py](update_from_tdx.py) | 1141 | 通达信二进制读写、增量同步、15/30/60分钟合成 |
| [update_tracking.py](update_tracking.py) | 410 | 信号计算调度，增量/全量模式，量能后处理 |
| [run_cycle.py](run_cycle.py) | 54 | CLI 薄壳（实际逻辑在 cycle_engine/ 包） |
| [cycle_engine/utils.py](cycle_engine/utils.py) | 66 | 常量、路径、CSV读取、NAME_MAP |
| [cycle_engine/indicators.py](cycle_engine/indicators.py) | 1574 | 排列熵、趋势评分、位置/方向判断、信号质量(7维含量能) |
| [cycle_engine/cycle_structure.py](cycle_engine/cycle_structure.py) | 852 | 主导量级、缠论结构、量价阶段、指数级行情 |
| [cycle_engine/engine.py](cycle_engine/engine.py) | 768 | 大盘系数、单标分析、全量分析调度 |
| [cycle_engine/grading.py](cycle_engine/grading.py) | 537 | 趋势分级、ABCD级别、操作建议生成 |
| [cycle_engine/reporting.py](cycle_engine/reporting.py) | 251 | 报告格式化、JSON保存 |
| [backtest_signals.py](backtest_signals.py) | 1059 | 信号回测引擎（低点合并/50%合并/★信号独立） |
| [hht_analyzer.py](hht_analyzer.py) | 581 | HHT 独立分析（EMD分解+瞬时频率+非预期解检测+量能修正层） |
| [gen_report_md.py](gen_report_md.py) | 1136 | Markdown 报告生成（含A+/A-/A假等级 + 回撤可视化） |
| [synthesize_report.py](synthesize_report.py) | 482 | 三层聚合引擎（ABCD等级 + A+/A-/A假细分 + 操作动作） |
| [price_pe_align.py](price_pe_align.py) | 396 | 价格-结构对齐分析（价格阶段检测 + PE轨迹对齐 + 25组合矩阵） |
| [operation_tracker.py](operation_tracker.py) | 922 | 战役级操作追踪（开仓/持仓/平仓事件链） |
| [jigou_jiancang.py](jigou_jiancang.py) | 495 | 机构建仓指标（基于真实筹码 WINNER 函数） |
| [chip_loader.py](chip_loader.py) | 416 | 筹码分布数据加载器 |
| [chip_extractor.py](chip_extractor.py) | 189 | 筹码峰 7z 批量解压 |
| [chips_selector_v2.py](chips_selector_v2.py) | 373 | 关键K选股（倍量+涨幅+短上影+筹码锁定） |
| [ai_analyzer.py](ai_analyzer.py) | 408 | AI 分析引擎（多API自动切换 + 交易框架注入） |
| [qa_tool.py](qa_tool.py) | 136 | 终端盘后分析/胜率统计 |
| [tools/tdx_fetch.py](tools/tdx_fetch.py) | 291 | pytdx API 封装（多服务器自动切换） |
| [tools/tracking_db.py](tools/tracking_db.py) | 430 | SQLite 持久化 |
| [tools/a_stock_screener.py](tools/a_stock_screener.py) | 174 | 全A股强势+波动率排序器 |
| [tools/strong_vol_screener.py](tools/strong_vol_screener.py) | 133 | ETF+主流板块强势筛选 |
| [tools/sector_momentum.py](tools/sector_momentum.py) | 575 | ★板块势能评分（269概念板块X_1，通达信RPS对标） |
| [tools/macro_screener.py](tools/macro_screener.py) | 189 | ★宏观分层过滤（板块动量 × 宏观环境 overlay） |
| [tools/macro_sensitivity.py](tools/macro_sensitivity.py) | 712 | ★宏观敏感性 + 环境分类（15+因子 RollingOLS） |
| [tools/liquidity_monitor.py](tools/liquidity_monitor.py) | 380 | ★全球流动性监控（5因子合成压力指数） |
| [gen_source_summary.py](gen_source_summary.py) | 342 | ★信源AI日报生成器（8公众号聚合→分析→报告） |
| [tools/sentiment/shock_detector.py](tools/sentiment/shock_detector.py) | 650 | ★消息面突发事件检测 (三源冗余: WSC/东财/AI股评) |
| [tools/japan_macro.py](tools/japan_macro.py) | 381 | ★日本宏观+套息交易压力 (BOJ/FXY/CPI → carry pressure) |
| [tools/fundamental_screener.py](tools/fundamental_screener.py) | 425 | ★基本面因子溢价筛选（Rolling FM + ROE/营收增长率） |
| [tools/fundamental/data_layer.py](tools/fundamental/data_layer.py) | 293 | ★基本面数据层（季频→日频转换 + 因子矩阵） |
| [tools/fundamental/fm_pipeline.py](tools/fundamental/fm_pipeline.py) | 500 | ★Fama-MacBeth 滚动截面回归流水线 |
| [tools/fundamental/capex_analyzer.py](tools/fundamental/capex_analyzer.py) | 142 | ★CAPEX 周期分析 + Type A/B 资本开支分类 |
| [tools/fundamental/growth_narrative.py](tools/fundamental/growth_narrative.py) | 329 | ★增长叙事引擎（营收/利润趋势 + 生命周期定位） |
| [tools/volume_leader_screener.py](tools/volume_leader_screener.py) | 801 | ★成交额强者×原始价格新高 选股器 + universe 管理 |
| [update_volume_leaders.py](update_volume_leaders.py) | 259 | ★成交量领导者6周期信号计算（全量+增量） |
| [tools/volume_leader/filter_engine.py](tools/volume_leader/filter_engine.py) | 124 | ★共享过滤原语（MA链/金叉/死叉/PE门禁/黄线 10个纯函数） |
| [tools/volume_leader/backtest.py](tools/volume_leader/backtest.py) | 1584 | ★量领回测引擎（对比/配对/切换分析） |
| [tools/volume_leader/monitor.py](tools/volume_leader/monitor.py) | 1924 | ★量领三级弹窗监控（MA级/金叉级/共振级/减仓级） |
| [tools/volume_leader/trade_db.py](tools/volume_leader/trade_db.py) | 238 | ★量领交易台账（SQLite 持仓/统计/历史） |
| [tools/volume_leader/factor_attribution.py](tools/volume_leader/factor_attribution.py) | 432 | ★量领因子归因（逐层贡献度分析） |
| [tools/volume_leader/fetcher.py](tools/volume_leader/fetcher.py) | 175 | ★量领数据获取（日线/分钟线批量拉取） |
| [tools/volume_leader/scan_resonance.py](tools/volume_leader/scan_resonance.py) | 193 | ★量领共振扫描（多周期共振检测） |
| [tools/volume_leader/verify_pe_gate.py](tools/volume_leader/verify_pe_gate.py) | 220 | ★量领PE门禁验证（排列熵过滤有效性） |
| [gen_volume_leader_report.py](gen_volume_leader_report.py) | 371 | ★成交量领导者 AI 日报生成 |
| [tools/us_market/etf_momentum.py](tools/us_market/etf_momentum.py) | 279 | ★US ETF 势能评分 (52只, RSI势能2) |
| [tools/us_market/star_stocks.py](tools/us_market/star_stocks.py) | 353 | ★US 明星股动量评分 (64只, RSI势能2) |
| [tools/us_market/macro_sensitivity.py](tools/us_market/macro_sensitivity.py) | 280 | ★US宏观→A股板块 RollingOLS敏感度 |
| [tools/us_market/cross_mapping.py](tools/us_market/cross_mapping.py) | 397 | ★US ETF→A股板块 相关性+领先滞后映射 |
| [tools/us_market/concept_chains.py](tools/us_market/concept_chains.py) | 240 | ★US 概念链引擎 (30条链, ETF持仓+手动产业链) |
| [tools/us_market/concept_chains.json](tools/us_market/concept_chains.json) | — | ★概念链定义文件 (新增链只需加JSON条目) |
| [tools/node_map.py](tools/node_map.py) | 794 | ★节点地图 (270板块×3750节点, 波检测+龙头识别+质量评分) |
| [tools/macro_history.py](tools/macro_history.py) | 420 | ★宏观历史回溯 (3068个A/B节点标注中国/US/日本/流动性) |
| [tools/star_buy_node_map.py](tools/star_buy_node_map.py) | 330 | ★★买→节点映射→贝叶斯收缩 (8分组置信度修正) |
| [notebook/chanlun/signals.py](notebook/chanlun/signals.py) | 525 | 缠论信号函数（40+信号，结构/中枢/买卖点/形态6合1） |
| [notebook/chanlun/positions.py](notebook/chanlun/positions.py) | 211 | 缠论仓位（日线+30分钟双级别联立+中枢共振） |
| [notebook/chanlun/adapter.py](notebook/chanlun/adapter.py) | 94 | 缠论适配器（czsc 数据格式转换） |

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
│   ├── {code}/          ← 每标的的6个周期CSV (82个代码目录)
│   │   └── {period}_signals.csv   (47列含11量能指标)
│   ├── _signals/        ← ★信号管线产出
│   │   ├── latest.json / cycle_report.json / hht_report.json
│   │   ├── synthesized_report.json / score_history.json
│   │   ├── analysis_history.json / backtest_report.json
│   │   ├── backtest_trades.db / score_validation.csv
│   ├── _macro/          ← ★宏观/情绪/流动性/US/节点产出
│   │   ├── sentiment_shock.json / liquidity_monitor.json
│   │   ├── japan_macro.json / macro_sensitivity.json
│   │   ├── macro_scenarios.json / sector_momentum_cache.json
│   │   ├── us_sector_momentum.json / us_star_momentum.json
│   │   ├── us_macro_sensitivity.json / us_cn_mapping.json
│   │   ├── us_concept_momentum.json / us_concept_chains_export.json
│   │   ├── node_map.json / star_buy_node_bayes.json
│   └── _funds/          ← ★基本面/量领/操作产出
│       ├── fundamental_profile.json / fundamental_scores.json
│       ├── volume_leader_universe.json / volume_rank_history.csv
│       ├── stock_names.csv / factor_attribution.json
│       ├── monitor_state.json / realtime_trades.db
│       └── operation_records.json
├── data/
│   ├── chips/           ← 筹码分布数据 (yearly/ + daily/)
│   ├── tracking.db      ← SQLite 追踪数据库
│   └── .fundamental_cache.json  (★基本面因子缓存)
├── reports/daily/       ← 每日信号报告 (YYYYMMDD_v3.md + YYYYMMDD_v3_nl.md)
├── reports/volume_leader/ ← ★成交额强者选股报告 + AI日报 (YYYYMMDD_volume_leader*.md)
├── reports/sources/     ← ★信源聚合日报 (YYYYMMDD_sources.md)
├── chips_picks/        ← 关键K选股结果
├── gbbq/               ← 除权数据（pytdx TdxDailyBarReader 自动读取）
├── prompts/
│   ├── trading_persona.md  ← AI风格模板（扫街/分时出击风格）
│   ├── trading_analysis_framework.md  ← 战役级分析框架（ai_analyzer.py 使用）
│   ├── operation_tracker_prompt.md  ← 操作追踪提示词
│   ├── macro_analysis_template.md  ← ★Dorian六步宏观分析框架（含失效条件/交易员检验）
│   └── source_analysis_prompt.md  ← ★信源日报提示词（含第-2步通读卡口）
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

### latest.json / cycle_report.json 结构

详见 [params_reference.md](memory/params_reference.md)。

## 六、核心领域规则（高频使用）

### 0-14 趋势评分

| 维度 | 分值 | 逻辑 |
|------|------|------|
| MACD | 0~4 | 0轴锚定·位置+交叉解耦，6种状态(0轴上金叉/死叉、过渡态、0轴下回踩/深水) |
| MA排列 | 0~6 | 链式递进5→10→20→60→120→250，断链即停 |
| 日线闭环 | 0~4 | 波段累积扣分制·来时路，含30/60共振(金叉+1/死叉-1) |

方向：13-14上涨 / 10-12偏多 / 7-9中性 / 4-6偏空 / 0-3下跌

**操作建议区（基于回测验证）**：
- fragile_high (11+分): 虚高警示。MACD+MA完美但闭环薄弱易反转。不追高
- sweet_spot (8-10分): 真实强势区，顺势做多窗口。评分最准区域
- neutral (3-7分): 中性等待
- fragile_low (0-2分): 筑底观察，不盲目抄底

### 两轴决策框架

| 轴 | 名称 | 来源 | 回答的问题 |
|:--|:--|:--|:--|
| 纵轴 | 操作级别 (ABCD) | macd_score(0-4) | 信号在哪个周期可信？ |
| 横轴 | 环境建议 (zone_advice) | total_score(0-14) | 现在该不该做？ |

两轴独立使用，不互相替代。

### 操作级别（ABCD — 周期筛选）

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

## 七、文档与规则导航

全部规则/细则/模板的触发式导航已移至 **MEMORY.md**（每次会话自动加载）。
需要翻细则时，按触发条件匹配定位到具体 memory 文件。

S级研究进度（新会话追一下）：
```
notebook/research_log.md             → grep "^## 实验" 跳到最后一个，看结论
tools/volume_leader/experiments/filter_evolution.md  → grep最新实验号，看结论
```
注意作息：用户 ~17:00 跑 `run_daily.bat`，在此之前没有当日新报告。

---

## 八、开发规则

### 开发流程硬约束（Superpowers）

以下规则优先于其他所有开发规则，每次编码前强制执行：

| 条件 | 必须执行 | 含义 |
|------|---------|------|
| **新建算法/功能/模块** | `Skill("brainstorming")` | 先探索→问清楚→出设计→用户点头→再写代码 |
| **修改算法逻辑（>30行）** | `Skill("brainstorming")` | 同上，算法改动不能直接上手 |
| **多文件修改任务** | `Skill("writing-plans")` | 先出实现计划，再动手 |
| **遇到 bug / 报错 / 异常** | `Skill("systematic-debugging")` | 不准猜原因直接改，走系统调试流程 |
| **有实现计划后** | `Skill("subagent-driven-development")` | 可并行的子任务用子代理执行 |
| **①新会话启动/每周首次** | 自动扫描 CLAUDE.md 覆盖率 | 运行下方 `--check` 命令，比对项目 .py 文件与 CLAUDE.md 收录情况 |
| **用户说"回顾/跟进度/开新窗口"** | 读上个窗口 JSONL 纯问答 | `ls -t ~/.claude/projects/$(basename $PWD)/*.jsonl` 排除当前session取最新的，python解析type=user/assistant的text字段，跳过thinking/tool_use |

以下情况可以跳过：
- 单行修复（typo/语法错误/变量名修正）
- 纯数据查询（只读 CSV/SQLite，不改代码）
- 用户明确说"直接改"或"不用走流程"

### 其他开发规则

详见 **MEMORY.md 开发规则简表**（每次会话自动加载，9条规则覆盖：增量优先 / per-bar滚动 / 修改写日志 / 改完同步文档 / 搜索优先 / 不偏离用户计划 / pytdx权威 / 老代码不动 / 重要流程Skill化）。

## 八、相关路径

- 通达信源：`C:\zd_cjzq\vipdoc\`
- 记忆/归档：`C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\` + `archives\`
- pytdx 源码：`D:\miniconda3\Lib\site-packages\pytdx\reader\`
- 筹码源数据：`D:\筹码峰\`

## 九、常见问题

排查FAQ已移至 [reference-startup-checklist.md](memory/reference-startup-checklist.md)。

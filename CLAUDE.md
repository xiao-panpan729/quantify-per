# CLAUDE.md — quantify-per 量化交易信号系统

**代码行数统计**: ~20,000 行 Python  (2026-06-08)
**当前跟踪**: 14 只标的 (2 指数 + 12 只 ETF/个股)
**文档目录**: [docs/](docs/) 含 architecture.md / integration-guide.md / operator-runbook.md / evolution_timeline.md
**实验记录**: [tools/volume_leader/experiments/filter_evolution.md](tools/volume_leader/experiments/filter_evolution.md) — 34实验/2300+行，入场过滤系统完整研究文献（每次改 filter/monitor/backtest 代码必须同步更新）
**笔记本系统**: [notebook/notebook_design.md](notebook/notebook_design.md) — 预测卡→验证→案例检索→反馈修正 项目设计说明书（对标实验报告）。**研究日志**: [notebook/research_log.md](notebook/research_log.md) — 简称"笔记研究"，每次DL框架讨论/实验/迭代必须同步更新。**专家系统**: [experts/research_log.md](experts/research_log.md) — 9大专家统一研究报告（设计决策+统一输出协议+演化记录），单文件持续更新。
**信源日报**: [reports/sources/](reports/sources/) — 8公众号聚合→突发事件→流动性→宏观→US映射→基本面→AI分析。**提示词**: [prompts/source_analysis_prompt.md](prompts/source_analysis_prompt.md) + [prompts/macro_analysis_template.md](prompts/macro_analysis_template.md)
**缠论结构层**: [notebook/chanlun/](notebook/chanlun/) — 基于 czsc 的完整缠论适配层（2026-06-01新增）。[signals.py](notebook/chanlun/signals.py) 覆盖 cxt 全部 40+ 信号函数（结构/中枢/买卖点/形态/笔状态/决策 6合1），[positions.py](notebook/chanlun/positions.py) 日线+30分钟双级别联立+中枢共振，入口: `get_position()`

## 一、常用命令

```bash
# ─── ★新会话启动：按 S→A 级顺序★ ───
# S级必读：研究日志最新实验(notebook/research_log.md) + 实验报告最新(tools/volume_leader/experiments/filter_evolution.md) + 最新日报
# ★回顾窗口：读上个窗口最后几轮纯问答
#   路径: ~/.claude/projects/{当前项目目录名}/*.jsonl
#   取最新一个（新窗口场景下最新JSONL就是上个窗口）
#   解析注意:
#     - Python路径用 C:/Users/... 不是 /c/Users/...
#     - obj["type"] → "user" / "assistant"
#     - obj["message"]["content"] 是数组 [{type, text}, ...]，取 type=text 拼接
#     - 跳过 text 以 <ide_ 开头的行（IDE上下文噪音）
#     - 只看最后 5-8 轮
# S级必查：
python operation_tracker.py --status   # 战役状态
python -c "import json;d=json.load(open('signals/tracking/_signals/latest.json','r',encoding='utf-8'));[print(f'{k} {v[\"name\"]}: dailyCCI={v[\"daily\"][\"indicators\"].get(\"cci\",\"?\")} ★买={sum(1 for s in v[\"daily\"][\"signals\"].values() if s.get(\"buy_signal\"))}') for k,v in d['stocks'].items()]"
python -c "import json;d=json.load(open('signals/tracking/_signals/cycle_report.json','r',encoding='utf-8'));[print(f'{i[\"code\"]} {i[\"name\"]}: {i[\"trend\"][\"score\"]}分 {i[\"trend\"][\"direction\"]} → {i[\"advice\"]}') for i in d]"
# 最新报告: dir reports/daily/*_v3.md          | 最近一个
# 记忆: 读 ~/.claude/projects/d--quantify-per/memory/case-*.md（近期案例）
# 文档分类与规则导航: 见 §七 → MEMORY.md 触发式导航

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
python ai_report_rewrite.py             # 步骤6: AI 自然语言日报 → reports/daily/YYYYMMDD_v3_nl.md
python operation_tracker.py --status    # 战役级操作追踪

# ─── 信源管理（微信公众号） ───
python _fetch_articles.py               # 批量拉取8个信源的最新文章全文 → wechat_articles/
python _search_accounts.py              # 搜索公众号 fakeid（新增信源时用）
python _fetch_full_history.py --all     # ★全量历史拉取（默认最近6个月，13个号全量）
python _fetch_full_history.py --accounts 猫菲特 盘前     # 指定号拉取
python _fetch_full_history.py --all --dry-run  # 仅统计不下载
python gen_source_summary.py            # ★信源AI日报生成（纯数据摘要，无AI）。同日幂等：已有报告则跳过
python gen_source_summary.py --ai       # ★数据聚合完成后提示用户触发AI分析
python gen_source_summary.py --force    # 强制重写当日报告（跳过幂等守卫）
python gen_daily_brief.py               # ★观点聚合+宏观共振+海外映射检测→追加（首次=完整区，再跑=午后增量）
# 午后更新：单独跑 gen_daily_brief.py，不要重跑 update_sources.bat 全流程
python _publish_report.py               # ★发布当日日报到 GitHub Pages（复制→更新index→git push）
python _publish_report.py --date 20260618 # 指定日期回填
python tools/signal_extractor.py        # ★信号事件流提取（从数据源→KG映射→JSON事件流）
python tools/signal_extractor.py --date 20260608  # 指定日期回填
python tools/signal_extractor.py --no-cache       # 跳过去重缓存
python tools/signal_extractor.py --full-history   # 回填所有历史日期
python tools/kg_extract.py --save                 # ★知识图谱变量/传导/交易指向抽取（12公众号→事件流JSON，V3版）
python tools/kg_extract.py --dry-run              # 预览统计
python tools/kg_extract.py --accounts 猫菲特 --save  # 单号抽取
python update_sources.bat               # ★全流程：抓取8信源→聚合→信号提取
python -c "import json;d=json.load(open('signals/tracking/_signals/daily_signals/20260610_signals.json'));[print(f\"{s['source_label']:20} {s['direction']:8} -> {','.join(s['kg_chains'][:3])}\") for s in d['signals'][:10]]"  # ★信号事件流查询

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
python tools/x1_screener.py --today               # ★x₁强度 Top 50（通达信RSI势能2，5000+股）
python tools/x1_screener.py --backfill            # 回填历史(2025→至今)
python tools/x1_screener.py --analyze             # 模式分析(阈值穿透/A/B/C统计/★买相关性)
python tools/x1_screener.py --report              # Top 50 板块标注报告
python tools/x1_screener.py --export              # 导出透视 CSV (3000+股×300+日)
python tools/x1_screener.py --top-n 30           # 指定排名深度(默认50)
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
python tools/annotate_node_events.py                 # ★研报→节点产业政策事件标注 (1176节点/5043事件)
python tools/annotate_node_events.py --sector 半导体  # 只标注指定板块
python tools/annotate_node_events.py --dry-run        # 预览不写文件
python tools/build_narrative_mapping.py                # ★构建TDX板块→叙事链S/A/B/C/D映射桥
python tools/narrative_lookup.py 600438                # ★个股→叙事链查询（支持--detail/--batch）
python tools/redline_breakout_screener.py               # ★牛熊红线突破×量化势能组合选股（MA221+3σ压制→突破→x₁≥8）
python tools/redline_breakout_screener.py --top 20     # Top 20
python tools/redline_breakout_screener.py --x1-threshold 9  # 自定义势能门槛
python tools/redline_breakout_screener.py --suppression 0.7 # 自定义压制比例
# 输出: signals/tracking/_macro/node_map.json / star_buy_node_bayes.json
# 输出: narratives/tdx_sector_narrative_map.json（build_narrative_mapping.py）

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

# ─── ★缠论Token研究实验（3K线原语→语料库→模型） ───
python tools/three_bar_primitives.py               # 3-bar原语分类验证（理论版）
python tools/build_token_corpus.py                 # 初版语料库(6指数日线)
python tools/build_token_corpus_full.py            # 全量语料库(655板块指数+30分钟)
python tools/train_token_model.py                  # Token预测LSTM训练(21K参数)
python tools/token_bi_segmenter.py                 # Token→笔分词器+走势叙述
python tools/label_chanlun_training.py            # 缠论训练数据标注工具
python tools/train_chanlun_student.py             # 缠论学生模型训练(Teacher→Student知识蒸馏)
python tools/chanlun_parser.py                    # 缠论层次化解析器(包含处理→笔→线段→走势类型)
python tools/chanlun_parser.py --candidates       # 枚举线段候选断点(供Teacher判断)
python tools/chanlun_parser.py --export-dataset   # 导出训练数据(Teacher标注→JSON)
```

## 二、环境依赖

```bash
# Python 3.10+，无 requirements.txt，直接 pip 安装
pip install pytdx mytt numpy pandas pandas-ta
```

关键依赖版本参考：pytdx 1.72、MyTT 2.9.3、pandas 2.3.3、numpy 2.2.6。

## 三、架构总览

完整架构总览（流水线依赖图/模块清单/数据目录）见 [memory/reference-system-inventory.md](memory/reference-system-inventory.md)。

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

信号 CSV 47列字段说明 + latest.json/cycle_report.json 结构详见 [memory/params_reference.md](memory/params_reference.md)。

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
| **专家融合/设计迭代/阈值调优** | 记录到 `experts/experiments/fusion_log.md` | 跨专家协作的文件、参数、结论必须写。用户可能忘记提，我有责任主动提醒 |
| **①新会话启动/每周首次** | 自动扫描 CLAUDE.md 覆盖率 | 运行下方 `--check` 命令，比对项目 .py 文件与 CLAUDE.md 收录情况 |
| **用户说"回顾/跟进度/开新窗口"** | 读上个窗口 JSONL 对话记录 | 见上方「★回顾窗口」详解。关键注意：Python路径用 C:/ 开头；message.content 是数组需逐 type=text 拼接；跳过 <ide_ 噪音消息
| **用户说"拉取知识星球/知识星球拉取"** | 执行 `python tools/convert_zsxq_to_md.py --auto --group=28888114545551` | 自动找 ima_captures 最新日期目录，将 知识星球 JSON 转为 Obsidian markdown 到 D:\knowledge-hub\zsxq\

以下情况可以跳过：
- 单行修复（typo/语法错误/变量名修正）
- 纯数据查询（只读 CSV/SQLite，不改代码）
- 用户明确说"直接改"或"不用走流程"

### 其他开发规则

详见 **MEMORY.md 开发规则简表**（每次会话自动加载，9条规则覆盖：增量优先 / per-bar滚动 / 修改写日志 / 改完同步文档 / 搜索优先 / 不偏离用户计划 / pytdx权威 / 老代码不动 / 重要流程Skill化）。

## 九、相关路径

- 通达信源：`C:\zd_cjzq\vipdoc\`
- 记忆/归档：`C:\Users\Administrator\.claude\projects\C--Users-Administrator\memory\` + `archives\`
- pytdx 源码：`D:\miniconda3\Lib\site-packages\pytdx\reader\`
- 筹码源数据：`D:\筹码峰\`

## 十、常见问题

排查FAQ已移至 [reference-startup-checklist.md](memory/reference-startup-checklist.md)。

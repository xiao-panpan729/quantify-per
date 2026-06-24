# US→A股话题桥 + 外资观点系统重构 (2026-06-24)

## 背景

日报系统存在系统性盲区：Stream A（8公众号）驱动话题生成，Stream B（US ETF动量/板块势能等量化数据）只做表格展示不参与话题生成。当 US biotech 势能强（x₁=5.14）但公众号无人讨论时，信号躺在表格里不触发话题。用户希望建立"US→A股话题桥"并复活外资观点系统（改为板块级轮动记录）。

## 修改的文件

### gen_daily_brief.py
- 新增 `US_SECTOR_TO_A_SHARE` 映射表（~15条 US ETF类别 → A股板块关键词）
- 新增 `US_SECTOR_X1_THRESHOLD = 3.0` 检测阈值
- 新增 `detect_uncoupled_signals()` 函数 — 检测US强势但公众号未覆盖的板块
- 新增 `render_overseas_mapping()` 函数 — 生成 `## 🪝 海外映射` 区块
- 修改 `MACRO_PATHS` — 添加 `sector_momentum` 路径
- 修改 `build_llm_prompt()` — 接受 `uncoupled_signals` 参数，加入prompt上下文
- 修改 `generate_full_section()` — 接受海外映射参数，写入报告
- 修改 `main()` — 在分组后检测 uncoupled signals 并传递到下游

### prompts/source_analysis_prompt.md
- 触发条件表新增 #5：海外映射板块
- 新增第0.3步：海外映射验证（Claude Code联网搜索外资观点）

### narratives/foreign_views/_index.md
- 从战略大方向（沪深300目标价）重构为板块级轮动观点索引
- 按板块分类表格（医药/消费/半导体/新能源/金融/地产）

### CLAUDE.md
- gen_daily_brief.py 描述和模块表条目添加"海外映射检测"
- foreign_views 目录描述更新

### update_sources.bat
- Claude Code调用添加第0.3步指令

## 架构变化

```
之前: 公众号 → 话题生成（Stream A 单流）
之后: 公众号 → 话题生成 + US动量检测 → 海外映射区块（Stream A+B 双流）
        ↓
     Claude Code AI验证 → anysearch搜外资观点 → 填充到海外映射区块
```

## 验证结果

用今日数据测试：
- Healthcare & Biotech 被正确检测为 uncoupled signal
  - avg_x₁ = +3.2 (3只ETF)
  - Top: ARKG (x₁=+5.1, 月涨幅+17.9%)
  - A股映射: 医药/创新药/CXO
- 生成 `## 🪝 海外映射` 区块格式正确

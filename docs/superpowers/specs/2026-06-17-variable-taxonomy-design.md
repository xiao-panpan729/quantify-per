# 变量分类器 Phase 1 — 设计规格书

**日期**: 2026-06-17
**状态**: 待实施
**关联**: `experts/research_log.md §八`（五维打分框架）

## 一、问题

`gen_daily_brief.py` 每次生成日报时，LLM 对话题重要性的判断没有结构化底层支撑。相同素材每次重跑结果不一致。需要一个可查询的变量分类器，让日报引擎知道"这个事件在传导链上是什么位置、重不重要"。

## 二、设计

### 2.1 数据层

**variable_taxonomy.json**（`signals/tracking/_macro/`）— 约25-30条初始变量：

```json
{
  "variables": [
    {
      "id": "VAR-001",
      "keywords": ["美伊协议", "美伊谈判", "美伊停火", "美伊冲突", "伊朗核谈"],
      "level": "核心变量",
      "chain": "Chain#001",
      "narratives": ["#47", "#3", "HALO"],
      "pricing": "边际变化",
      "validation": ["Brent油价变动幅度", "霍尔木兹通航状态", "美方/伊方官方表态"]
    }
  ],
  "meta": {
    "updated": "2026-06-17",
    "total_variables": 25
  }
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一标识 |
| `keywords` | string[] | 匹配关键词（中文） |
| `level` | enum | 核心变量 / 结构性变量 / 下游结果 / 情绪噪音 |
| `chain` | string | 对应传导链编号（指针 → transmission_chains.md） |
| `narratives` | string[] | 关联叙事链编号（对应 narrative_judgment_layer.md） |
| `pricing` | enum | 边际变化（新增量）/ 延续（已被定价） |
| `validation` | string[] | 验证条件要点（日报可用） |

**四个变量层级**：

| 层级 | 定义 | 示例 | 是否点亮节点 |
|------|------|------|:---:|
| 核心变量 | 定价锚级别，跨行业影响 | 美伊协议、Fed加息、Brent油价 | ✅ |
| 结构性变量 | 产业逻辑变化，影响特定链 | CPO→NPO切换、WF6涨价、铜关税 | ✅ |
| 下游结果 | 已被定价的末端结果 | "板块涨了"、"大盘跌了" | ❌ |
| 情绪噪音 | 无结构性影响 | 段子、小作文、一日游 | ❌ |

**variable_candidates.json** — 未分类候选项队列：

```json
{
  "candidates": [
    {
      "keyword": "量子加密",
      "first_seen": "2026-06-17",
      "seen_count": 3,
      "source_articles": ["中信建投0617", "一思一记0617"],
      "status": "pending"
    }
  ]
}
```

### 2.2 代码层

**新文件**: `tools/variable_taxonomy.py` — 查询 + 候选管理

```python
# 核心函数
lookup_variable(keywords: list[str]) -> list[dict]   # 关键词 → 变量条目匹配
add_candidates(keywords: list[str], source: str)      # 未匹配 → 写入候选项
get_candidates() -> list[dict]                        # 读取待审队列
classify_candidate(keyword: str, entry: dict)         # 归类候选项 → taxonomy
dismiss_candidate(keyword: str)                       # 打回候选项
```

**改动文件**: `gen_daily_brief.py`
- LLM 话题识别后插入 `lookup_variable()` 匹配
- 未匹配话题 → 写 `add_candidates()`

**改动文件**: `update_sources.bat`
- `gen_daily_brief.py` 执行后检查 candidates.json
- 有新增 → 终端打印 `⚠️ N new topics → /taxonomy-review`

### 2.3 运行层

```
── 日常自动化（无人值守）──
update_sources.bat
  → gen_daily_brief.py
    → 查 taxonomy.json 匹配话题
    → 未匹配 → 追加 candidates.json
    → 终端提示 /taxonomy-review

── 手工审查（聊天框）──
/taxonomy-review
  → 读 candidates.json
  → 逐个展示候选项
  → 用户确认 → 更新 taxonomy.json
```

### 2.4 Skill 层

`/taxonomy-review` Skill：
- 触发条件：用户敲 `/taxonomy-review`
- 输入：`variable_candidates.json`
- 输出：更新后的 `variable_taxonomy.json`
- 位置：注册到 `.claude/settings.json` 或项目 CLAUDE.md

## 三、初始变量来源

| 来源 | 条数 | 说明 |
|------|:---:|------|
| 6条传导链的起点 + 分支节点 | ~18 | Chain#001~006 的触发事件和关键中间变量 |
| 8个S级叙事的关键催化 | ~8 | 英伟达业绩/光模块出口/CPO量产等 |
| S/A/B/C/D 跨级别交叉节点 | ~4 | 如"铜关税"跨有色金属+半导体材料 |

**Phase 1 目标**: 25-30条，覆盖最活跃的传导链和叙事。

## 四、不做什么

- **不做** 自动打变量层级（规则匹配足够，LLM 匹配回到不一致问题）
- **不做** 重型版（完整传导路径存 JSON）—— transmission_chains.md 是权威源，JSON 只存指针
- **不做** Phase 2 节点亮度计算——本次只做变量分类器
- **不做** gen_daily_brief 重要性打分引擎对接——Phase 1 只产出 taxonomy.json 和查询接口

## 五、验收标准

1. `variable_taxonomy.json` 存在，含 25+ 条变量定义
2. `tools/variable_taxonomy.py` 可执行：`lookup_variable(["美伊协议"])` 返回匹配条目
3. `gen_daily_brief.py` 中能自动匹配话题 + 写入候选项
4. `/taxonomy-review` Skill 可触发、可交互审查
5. `update_sources.bat` 末尾有候选项提示

# 外资观点采集系统 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有量化信源体系上，建立外资行对A股观点的专项采集、分类、承接体系，实现零VPN采集国内财经媒体编译的外资观点。

**Architecture:** 三层结构：采集层（扩展 `_fetch_articles.py` + `shock_detector` 关键词）→ 承接层（`narratives/foreign_views/` 各行时间线 + `_index.md` 叙事映射）→ 输出层（信源日报 prompt 增加外资观点提取指令）。保持轻量级，不建数据库，纯 md + JSON。

**Tech Stack:** Python (urllib/requests), MPTEXT API (微信), markdown, 现有 shock_keywords.json 机制

---

## 文件结构

```
narratives/foreign_views/
├── _index.md                   ← 新建：索引总览，各行最新观点摘要 + 映射到产业链编号
├── _template.md                ← 新建：单行时间线模板
├── gs_goldman_sachs.md         ← 新建：高盛
├── ms_morgan_stanley.md        ← 新建：摩根士丹利
├── jpm_jp_morgan.md            ← 新建：摩根大通
├── ubs.md                      ← 新建：瑞银
└── citi.md                     ← 新建：花旗

修改文件：
- _fetch_articles.py            ← 新增 2-3 个信源（Reuters 中文网 / 财联社等）
- tools/sentiment/shock_keywords.json  ← 新增 "外资行观点" 分类
- prompts/source_analysis_prompt.md    ← 增加从已抓取文章提取外资观点的指令
- CLAUDE.md                     ← 更新文档
```

---

### Task 1: 创建外资观点承接目录 + 模板

**Files:**
- Create: `narratives/foreign_views/_template.md`
- Create: `narratives/foreign_views/_index.md`
- Create: `narratives/foreign_views/gs_goldman_sachs.md`
- Create: `narratives/foreign_views/ms_morgan_stanley.md`
- Create: `narratives/foreign_views/jpm_jp_morgan.md`
- Create: `narratives/foreign_views/ubs.md`
- Create: `narratives/foreign_views/citi.md`

- [ ] **Step 1: 创建目录**

```bash
mkdir -p narratives/foreign_views
```

- [ ] **Step 2: 创建 `_template.md`**

```markdown
# [银行名称] — 对A股观点时间线

> 更新时间：YYYY-MM-DD
> 数据来源：国内财经媒体编译 / 公开报告摘要

---

## 当前核心观点

[1-3句当前最新观点摘要]

## 年度基调

[看多/看空/结构性看多 A 股，核心逻辑]

---

## 观点时间线

### YYYY-MM-DD
**标题/事件**: [观点标题]
**方向**: [看多/看空/中性] | **涉及链**: [链#编号]
**内容**: [观点详细内容]
**来源**: [编译来源，如财联社/Reuters中文网]

---

## 历史判断回顾

| 日期 | 观点 | 方向 | 后续验证 |
|------|------|:----:|---------|
| YYYY-MM-DD | 摘要 | ↑/↓/→ | 后续市场走势如何 |

## 映射产业链

本行观点涉及的产业链编号：
- 链#[编号] — [链名]
```

- [ ] **Step 3: 创建 `_index.md`**

```markdown
# 外资机构观点索引

> 生成日期：2026-06-10
> 跟踪范围：高盛 / 大摩 / 小摩 / 瑞银 / 花旗
> 数据来源：国内财经媒体编译（Reuters中文网 / 财联社 / 华尔街见闻等，零VPN）

---

## 一览表

| 机构 | 最新观点 | 方向 | 涉及产业链 | 更新 |
|------|---------|:----:|-----------|:----:|
| 高盛 | [待采集] | → | — | — |
| 摩根士丹利 | [待采集] | → | — | — |
| 摩根大通 | [待采集] | → | — | — |
| 瑞银 | [待采集] | → | — | — |
| 花旗 | [待采集] | → | — | — |

---

## 内外资分歧监控

| 产业链 | 国内主流观点 | 外资主流观点 | 分歧度 | 跟踪 |
|-------|------------|------------|:-----:|:----:|
| — | — | — | — | — |

> **分歧度说明**：
> - 🔴 严重分歧 — 内外资方向相反，有一方将大幅犯错
> - 🟡 轻度分歧 — 方向一致但节奏/幅度判断不同
> - 🟢 方向一致 — 内外资形成共识

---

## 今日观点详表

### YYYY-MM-DD

| 机构 | 观点摘要 | 方向 | 映射链 | 来源 |
|------|---------|:----:|-------|------|
| 高盛 | [观点] | ↑/↓/→ | #[编号] | [链接] |

> **映射说明**：每条观点末尾标注涉及哪条产业链编号（50条链），用于叙事看板交叉引用。格式：`→ 链#[编号]`
```

- [ ] **Step 4: 创建五家银行初始文件**

用模板创建以下文件，填充基本的机构信息和空的时间线结构：
- `narratives/foreign_views/gs_goldman_sachs.md`
- `narratives/foreign_views/ms_morgan_stanley.md`
- `narratives/foreign_views/jpm_jp_morgan.md`
- `narratives/foreign_views/ubs.md`
- `narratives/foreign_views/citi.md`

每个文件包含：
- 机构简介（中文名+英文名）
- 空的时间线结构
- 映射产业链占位符

---

### Task 2: 扩展 _fetch_articles.py 新增信源

**Files:**
- Modify: `_fetch_articles.py` (新增 2-3 个信源)

关键决策：`_fetch_articles.py` 当前只支持 MPTEXT API（微信公众号）。新增信源有两类：

1. **微信公众号类** — 如果有对应的公众号，直接用 fakeid 方式接入
2. **网页抓取类** — 如 Reuters 中文网或财联社网站，需新增 HTTP 抓取函数

先查一下可用的公众号 fakeid 和网页源：

**候选信源 A：财联社「外资风向」栏目**
- 类型：网站/公众号
- 内容：每日外资观点编译
- 接入方式：如果有公众号直接用 fakeid；否则用 HTTP 抓取

**候选信源 B：Reuters 中文网**
- 类型：网站
- 内容：外资行观点编译
- URL 模式：`https://cn.reuters.com/`（墙内可访问）
- 接入方式：新增 HTTP 抓取函数

**候选信源 C：华尔街见闻「机构观点」栏目**
- 类型：网站/公众号
- 内容：各外资行 A 股策略摘要
- 接入方式：公众号或网站

- [ ] **Step 1: 在 `_fetch_articles.py` 中新增 web 抓取函数**

```python
# ─── 新增：网页类信源抓取（非微信公众号） ───
import re
from html.parser import HTMLParser

def fetch_reuters_cn_foreign_views():
    """抓取 Reuters 中文网外资观点相关文章（零VPN）"""
    url = 'https://cn.reuters.com/'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp = urllib.request.urlopen(req, timeout=30)
        html = resp.read().decode('utf-8', errors='replace')
        # 提取文章标题和链接
        articles = []
        # 匹配常见的外资行关键词和文章链接
        foreign_bank_kw = ['高盛', '摩根士丹利', '摩根大通', '瑞银', '花旗', '大摩', '小摩']
        # 简单提取所有链接
        for match in re.finditer(r'<a[^>]*href="(https?://cn\.reuters\.com[^"]*)"[^>]*>([^<]+)</a>', html):
            link, title = match.group(1), match.group(2).strip()
            if any(kw in title for kw in foreign_bank_kw):
                articles.append({'title': title, 'link': link, 'source': 'reuters_cn'})
        # 限制最多5条
        return articles[:5]
    except Exception as e:
        print(f'[Reuters CN] 抓取失败: {e}')
        return []


OUTPUT_DIR_WEB = os.path.join(PROJECT_ROOT, 'wechat_articles', '_web_sources')
os.makedirs(OUTPUT_DIR_WEB, exist_ok=True)
```

注意：上述函数实现一个爬取框架，具体的选择器需根据 Reuters/财联社实际页面结构调整。

- [ ] **Step 2: 在主流程中并行调用网页信源**

在 `_fetch_articles.py` 主流程末尾增加网页信源的抓取调用，与公众号并行：

```python
# 网页信源
web_sources = [
    ('Reuters中文网', fetch_reuters_cn_foreign_views),
    # 后续可添加更多
]

for name, fetch_fn in web_sources:
    print(f'\n===== {name} =====')
    articles = fetch_fn()
    if not articles:
        print(f'  无外资观点文章')
        continue
    for art in articles:
        filepath = os.path.join(OUTPUT_DIR_WEB, f"{datetime.now().strftime('%Y%m%d_%H%M')}_{name}_{art['title'][:30]}.txt")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"标题: {art['title']}\n")
            f.write(f"来源: {art.get('source', name)}\n")
            f.write(f"链接: {art.get('link', '')}\n")
            f.write("="*50 + "\n")
            f.write(f"(摘要待完善，请手动访问链接)\n")
        print(f'  OK {art["title"][:40]}')
```

注意：初始版本可能只保存标题和链接，内容抓取需要额外的页面解析逻辑，可以后续迭代完善。

---

### Task 3: 新增 shock_keywords 外资行观点分类

**Files:**
- Modify: `tools/sentiment/shock_keywords.json`

- [ ] **Step 1: 在 `categories` 末尾新增 `institutional_views` 分类**

```json
    "institutional_views": {
      "label": "外资行观点",
      "level": "market",
      "impact_sign": 0,
      "impact_magnitude": 1,
      "keywords": [
        "高盛", "Goldman Sachs", "GS",
        "摩根士丹利", "大摩", "Morgan Stanley",
        "摩根大通", "小摩", "JPMorgan", "J.P. Morgan",
        "瑞银", "UBS",
        "花旗", "Citi", "Citigroup",
        "美银", "Bank of America", "BofA",
        "外资行", "外资机构", "华尔街",
        "看多A股", "看空A股", "超配A股", "低配A股",
        "上调中国", "下调中国", "上调A股", "下调A股",
        "上调评级", "下调评级",
        "China strategy", "超配中国", "低配中国"
      ]
    }
```

关键设计：
- `level: "market"` — 归类到市场层面冲击
- `impact_sign: 0` — 中性标记，因为外资行观点本身可多可空，不预设方向
- `impact_magnitude: 1` — 中等影响（相比关税/macro 的 2 级低一些）

---

### Task 4: 更新 source_analysis_prompt.md

**Files:**
- Modify: `prompts/source_analysis_prompt.md`

- [ ] **Step 1: 在数据源优先顺序中增加"外资观点"**

在 `## 4. 数据源的融合顺序` 部分的公众号观点之后，增加：

```
6. 外资行观点：高盛/大摩/小摩/瑞银/花旗对A股的最新策略观点 → 寻找内外资分歧
```

- [ ] **Step 2: 在审视角度中增加第9条**

在 `## 每次必须覆盖的审视角度` 末尾新增：

```
### 9. 内外资分歧（新增）

**内外资机构对同一板块/标的的观点是否存在方向性分歧？** 这种分歧往往是重大行情的先兆（如中金看多锂矿 vs 高盛看空 → 最终下跌）。

必须思考的问题链：
- 外资行最近有没有集中调整对中国/ A 股的评级？（如 GS 上调/下调 China strategy）
- 内外资对具体产业链（锂矿/半导体/新能源）有没有方向性分歧？
- 历史上类似分歧出现后，市场最终朝哪边走了？
- 分歧的来源是什么？是数据解读不同还是框架本身不同？
```

- [ ] **Step 3: 在检查清单中增加确认项**

在 `## 检查清单（写完后自检）` 末尾增加：

```
- [ ] 审视角度9（内外资分歧）：内外资有没有方向性分歧？
- [ ] 如有分歧，是否分析了历史先例和可能的走向？
```

---

### Task 5: 更新 CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 在 narratives 目录说明中新增 foreign_views**

在文档「数据目录」部分的 narratives 章节增加：

```
├── narratives/
│   ├── narrative_judgment_layer.md
│   ├── foreign_views/        ← ★外资行观点时间线（高盛/大摩/小摩/瑞银/花旗）
│   │   ├── _index.md         ← 索引总览 + 内外资分歧监控
│   │   ├── gs_goldman_sachs.md
│   │   ├── ms_morgan_stanley.md
│   │   └── ...
│   ├── timelines/            ← 53条产业链叙事时间线
│   └── templates/            ← 叙事模板
```

- [ ] **Step 2: 在模块表中新增 `tools/research_report.py` 说明（如果缺失）**

检查 CLAUDE.md 中是否已有 `tools/research_report.py` 条目，确保外资观点系统的相关文件在模块表中。

---

### Task 6: 初版数据填充 — 手动采集近期外资观点

**Files:**
- Modify: `narratives/foreign_views/_index.md`
- Modify: `narratives/foreign_views/gs_goldman_sachs.md`
- Modify: `narratives/foreign_views/ms_morgan_stanley.md`
- Modify: `narratives/foreign_views/jpm_jp_morgan.md`
- Modify: `narratives/foreign_views/ubs.md`
- Modify: `narratives/foreign_views/citi.md`

- [ ] **Step 1: 搜索近期外资行对A股观点**

使用 WebSearch 搜索以下内容：
- "高盛 A股 2026" "Goldman Sachs China strategy 2026"
- "摩根士丹利 A股 2026" "Morgan Stanley China 2026"
- "摩根大通 A股 2026" "JP Morgan China 2026"
- "瑞银 A股 2026" "UBS China 2026"
- "花旗 A股 2026" "Citi China 2026"

- [ ] **Step 2: 填充各行时间线初始数据**

将搜索到的观点填入对应文件，每条包含：日期、观点摘要、方向、涉及产业链编号、来源链接。

- [ ] **Step 3: 更新 `_index.md` 一览表**

将各行最新观点填入索引表，如有内外资分歧填入分歧监控表。

---

## 后续迭代方向（本期不实现）

1. **自动观点提取** — 用 AI 从已抓取的公众号文章中自动提取外资行观点，写入对应时间线文件
2. **分歧预警** — 当内外资对同一产业链出现方向性分歧时，在操作追踪中标记
3. **观点验证追踪** — 记录每条观点的后续市场走势，统计各行准确率
4. **gen_source_summary.py 展示** — 在信源摘要报告中增加"外资观点"区块

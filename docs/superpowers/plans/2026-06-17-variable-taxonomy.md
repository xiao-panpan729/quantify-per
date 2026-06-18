# 变量分类器 Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a queryable variable taxonomy JSON + lookup engine + candidate queue, and integrate it into the daily brief pipeline so LLM-generated topics can be matched against structured transmission chains and narrative levels.

**Architecture:** New module `tools/variable_taxonomy.py` provides pure-function lookup + candidate management. `gen_daily_brief.py` calls lookup after LLM topic parsing, appending unmatched topics to a candidate queue. A new `/taxonomy-review` skill lets the user interactively review and classify candidates.

**Tech Stack:** Python 3.10+, JSON, existing `ai_analyzer.call_llm()`, Claude Code skill system

---

### Task 1: Create initial `variable_taxonomy.json`

**Files:**
- Create: `signals/tracking/_macro/variable_taxonomy.json`
- Create: `signals/tracking/_macro/variable_candidates.json`

- [ ] **Step 1: Write the initial taxonomy JSON (25 variables)**

Extract variables from 6 transmission chains + 8 S-level narratives:

```json
{
  "variables": [
    {
      "id": "VAR-001",
      "keywords": ["美伊协议", "美伊谈判", "美伊停火", "美伊冲突", "伊朗核谈", "美伊和谈"],
      "level": "核心变量",
      "chain": "Chain#001",
      "narratives": ["#47", "#3", "#44", "HALO"],
      "pricing": "边际变化",
      "validation": ["Brent油价变动幅度", "霍尔木兹通航状态", "美方/伊方官方表态"]
    },
    {
      "id": "VAR-002",
      "keywords": ["霍尔木兹", "海峡关闭", "霍尔木兹海峡"],
      "level": "核心变量",
      "chain": "Chain#001",
      "narratives": ["#47", "#3", "#44"],
      "pricing": "延续",
      "validation": ["通航恢复状态", "Brent油价中枢$95+"]
    },
    {
      "id": "VAR-003",
      "keywords": ["Brent", "布伦特", "原油价格", "油价"],
      "level": "核心变量",
      "chain": "Chain#001",
      "narratives": ["#47", "#3"],
      "pricing": "边际变化",
      "validation": ["Brent运行区间$95-100", "美国原油库存变化", "中东供给扰动"]
    },
    {
      "id": "VAR-004",
      "keywords": ["日本化工", "光刻胶", "JSR", "TOK", "信越化学", "住友化学"],
      "level": "结构性变量",
      "chain": "Chain#005",
      "narratives": ["#3"],
      "pricing": "边际变化",
      "validation": ["日本光刻胶企业涨价/减产公告", "中国晶圆厂验证加速", "彤程新材/南大光电新订单"]
    },
    {
      "id": "VAR-005",
      "keywords": ["六氟化钨", "WF6", "钨原料", "特种气体", "关东电化"],
      "level": "结构性变量",
      "chain": "Chain#004",
      "narratives": ["#3", "#47"],
      "pricing": "边际变化",
      "validation": ["WF6现货价格是否执行70-90%涨幅", "日本供应商成本二次冲击", "国产替代（华特/金宏）"]
    },
    {
      "id": "VAR-006",
      "keywords": ["液化天然气", "LNG", "欧洲天然气", "TTF", "卡塔尔", "RasLaffan"],
      "level": "结构性变量",
      "chain": "Chain#002",
      "narratives": ["#47"],
      "pricing": "边际变化",
      "validation": ["TTF价格中枢", "欧洲储气率(当前38%)", "新疆煤化工项目进度"]
    },
    {
      "id": "VAR-007",
      "keywords": ["新疆煤化工", "煤制油", "煤制气", "煤化工"],
      "level": "结构性变量",
      "chain": "Chain#002",
      "narratives": ["#47"],
      "pricing": "边际变化",
      "validation": ["煤化工项目批复/开工进度", "甲醇/乙二醇/醋酸价差"]
    },
    {
      "id": "VAR-008",
      "keywords": ["HALO", "重资产", "低淘汰率", "AI免疫"],
      "level": "结构性变量",
      "chain": "Chain#003",
      "narratives": ["HALO", "#16", "#8", "#47", "#43"],
      "pricing": "延续",
      "validation": ["更多券商提出HALO概念（叙事扩散）", "重资产vs科技股估值溢价走阔", "AI资本开支持续外溢"]
    },
    {
      "id": "VAR-009",
      "keywords": ["AI颠覆", "AI替代", "白领替代", "AI焦虑"],
      "level": "结构性变量",
      "chain": "Chain#003",
      "narratives": ["HALO", "#16", "#8"],
      "pricing": "延续",
      "validation": ["AI替代白领的媒体报道密度", "软件/互联网vs制造业估值剪刀差"]
    },
    {
      "id": "VAR-010",
      "keywords": ["中东铝厂", "铝产能关停", "LME铝库存", "日本铝升水", "电解铝"],
      "level": "结构性变量",
      "chain": "Chain#006",
      "narratives": ["#44", "HALO", "#43"],
      "pricing": "边际变化",
      "validation": ["LME铝库存<35万吨是否持续", "日本铝升水维持>400美元", "国内铝利润8440元/吨"]
    },
    {
      "id": "VAR-011",
      "keywords": ["铜关税", "铜供需缺口", "铜矿增量"],
      "level": "结构性变量",
      "chain": "Chain#006",
      "narratives": ["#44", "#43"],
      "pricing": "边际变化",
      "validation": ["6.30铜关税落地后进口成本变化", "铜供需缺口是否扩大"]
    },
    {
      "id": "VAR-012",
      "keywords": ["Fed", "美联储", "加息", "降息", "利率决议", "FOMC", "联邦基金利率"],
      "level": "核心变量",
      "chain": "Chain#001",
      "narratives": ["#11", "#10", "#44", "#38"],
      "pricing": "边际变化",
      "validation": ["Fed利率预期（FedWatch概率）", "通胀数据(CPI/PCE)", "非农就业"]
    },
    {
      "id": "VAR-013",
      "keywords": ["英伟达", "NVDA", "NVIDIA", "GPU", "Blackwell"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#11", "#12", "#10"],
      "pricing": "边际变化",
      "validation": ["NVDA季度财报(CAPEX/指引)", "CSP资本开支", "GPU出货量/价格"]
    },
    {
      "id": "VAR-014",
      "keywords": ["光模块", "光通信", "CPO", "NPO", "LPO", "MPO", "硅光"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#12", "#11"],
      "pricing": "边际变化",
      "validation": ["光纤出口数据(量/价)", "CPO/NPO交换机量产进度", "光模块公司订单排期"]
    },
    {
      "id": "VAR-015",
      "keywords": ["光纤", "光纤出口", "光纤涨价", "光纤价格"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#12"],
      "pricing": "边际变化",
      "validation": ["光纤出口量同比+63.6%是否持续", "运营商集采价从25→80元/芯公里"]
    },
    {
      "id": "VAR-016",
      "keywords": ["人形机器人", "机器人", "Optimus", "Figure", "宇树科技"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#22", "#23"],
      "pricing": "边际变化",
      "validation": ["Optimus V3量产(7-8月 Sched)", "Figure03商业化验证", "供应链送样确认"]
    },
    {
      "id": "VAR-017",
      "keywords": ["商业航天", "SpaceX", "千帆", "卫星互联网", "可复用火箭"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#27"],
      "pricing": "边际变化",
      "validation": ["SpaceX IPO进度(1.77万亿估值/6.12上市)", "千帆200颗卫星", "一周三发"]
    },
    {
      "id": "VAR-018",
      "keywords": ["出海", "全球化", "贸易顺差", "出口"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["meta-8"],
      "pricing": "延续",
      "validation": ["贸易顺差创新高(1.189万亿美元)", "各行业出海订单", "关税/制裁变化"]
    },
    {
      "id": "VAR-019",
      "keywords": ["功率半导体", "IGBT", "SiC", "碳化硅", "GaN", "氮化镓", "BB Ratio"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#6", "#3a", "HALO"],
      "pricing": "边际变化",
      "validation": ["BB Ratio重回1.0+是否持续", "AIDC电源BOM占比30-40%", "SiC衬底价格/产能"]
    },
    {
      "id": "VAR-020",
      "keywords": ["制冷剂", "R22", "R32", "配额制", "氟化工", "F-Gas"],
      "level": "结构性变量",
      "chain": "Chain#001",
      "narratives": ["#47"],
      "pricing": "边际变化",
      "validation": ["英国F-Gas法案推迟削减→高盈利久期延长", "制冷剂配额制落地", "现货价格"]
    },
    {
      "id": "VAR-021",
      "keywords": ["铜箔", "算力铜箔", "MLCC", "PCB", "ABF", "载板"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#8", "#11"],
      "pricing": "边际变化",
      "validation": ["铜箔供需缺口", "MLCC排产至2027", "ABF载板供不应求"]
    },
    {
      "id": "VAR-022",
      "keywords": ["新能源车", "电动车", "渗透率", "城市NOA", "智能驾驶", "FSD"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#18", "#17", "#53"],
      "pricing": "延续",
      "validation": ["新能源车渗透率>50%", "出海400万辆/+60%", "城市NOA渗透率5%→30%"]
    },
    {
      "id": "VAR-023",
      "keywords": ["半导体设备", "光刻机", "国产替代", "去美化", "去日化"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#2", "#3"],
      "pricing": "延续",
      "validation": ["国产化率4.91%→18.02%跟踪", "长存/长鑫扩产进度", "MATCH法案替代"]
    },
    {
      "id": "VAR-024",
      "keywords": ["AI应用", "ChatGPT", "OpenAI", "Anthropic", "AI Agent", "AI智能体"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#10", "#11"],
      "pricing": "边际变化",
      "validation": ["OpenAI ARR超200亿", "AI Agent落地场景", "企业AI采纳率"]
    },
    {
      "id": "VAR-025",
      "keywords": ["日本加息", "BOJ", "日本央行", "植田和男", "内田", "日元", "套息交易", "carry trade"],
      "level": "核心变量",
      "chain": "",
      "narratives": ["#33", "#44", "#38"],
      "pricing": "边际变化",
      "validation": ["BOJ政策利率", "USDJPY汇率", "日本核心CPI"]
    },
    {
      "id": "VAR-026",
      "keywords": ["苹果", "Apple", "iPhone", "AI终端", "折叠屏", "Apple Intelligence"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#52", "#14"],
      "pricing": "边际变化",
      "validation": ["iPhone备货9500万部(+11.7%)", "换机意愿52%创十年新高", "Apple Intelligence国行获批"]
    },
    {
      "id": "VAR-027",
      "keywords": ["电网", "电力设备", "特高压", "AIDC电力", "十五五"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#33", "HALO"],
      "pricing": "延续",
      "validation": ["北美AI电力需求19→71GW", "燃机排产至2030", "十五五国网4万亿"]
    },
    {
      "id": "VAR-028",
      "keywords": ["战略金属", "稀土", "小金属", "钨", "钽", "铼", "镓", "锗", "锑"],
      "level": "结构性变量",
      "chain": "",
      "narratives": ["#43"],
      "pricing": "边际变化",
      "validation": ["刚果供应中断", "AI需求爆发拉动", "地缘安全溢价"]
    }
  ],
  "meta": {
    "updated": "2026-06-17",
    "total_variables": 28,
    "chains_covered": ["Chain#001", "Chain#002", "Chain#003", "Chain#004", "Chain#005", "Chain#006"],
    "narratives_covered": [
      "#2", "#3", "#6", "#8", "#10", "#11", "#12", "#14", "#16", "#17",
      "#18", "#22", "#23", "#27", "#33", "#38", "#43", "#44", "#47",
      "#52", "#53", "HALO", "meta-8", "#3a"
    ]
  }
}
```

Save to: `signals/tracking/_macro/variable_taxonomy.json`

- [ ] **Step 2: Create empty candidates JSON**

```json
{
  "candidates": [],
  "meta": {
    "last_checked": "",
    "total_pending": 0
  }
}
```

Save to: `signals/tracking/_macro/variable_candidates.json`

- [ ] **Step 3: Commit**

```bash
git add signals/tracking/_macro/variable_taxonomy.json signals/tracking/_macro/variable_candidates.json
git commit -m "feat: add initial variable taxonomy (28 variables) and candidates queue"
```

---

### Task 2: Create `tools/variable_taxonomy.py` — core functions

**Files:**
- Create: `tools/variable_taxonomy.py`

- [ ] **Step 1: Write the module**

```python
# -*- coding: utf-8 -*-
"""
变量分类器 — 查询 + 候选管理

Usage:
  from tools.variable_taxonomy import lookup_variable, add_candidates, get_candidates

  # 查询变量匹配
  results = lookup_variable(["美伊协议", "霍尔木兹"])

  # 追加候选项
  add_candidates(["量子加密", "太空采矿"], "中信建投0617")

  # 读取待审队列
  pending = get_candidates()
"""

import json
import sys
from pathlib import Path
from datetime import datetime

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
MACRO_DIR = PROJECT_ROOT / "signals" / "tracking" / "_macro"
TAXONOMY_PATH = MACRO_DIR / "variable_taxonomy.json"
CANDIDATES_PATH = MACRO_DIR / "variable_candidates.json"


def _load_taxonomy() -> dict:
    """加载变量分类体系"""
    if TAXONOMY_PATH.exists():
        return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    return {"variables": [], "meta": {}}


def _load_candidates() -> dict:
    """加载候选项队列"""
    if CANDIDATES_PATH.exists():
        return json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    return {"candidates": [], "meta": {"last_checked": "", "total_pending": 0}}


def _save_candidates(data: dict):
    """保存候选项队列"""
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def lookup_variable(keywords: list[str]) -> list[dict]:
    """
    给定关键词列表，返回匹配的变量条目。

    Args:
      keywords: 关键词列表，如 ["美伊协议", "油价"]

    Returns:
      匹配的变量条目列表（按变量层级排序：核心变量在前）
    """
    taxonomy = _load_taxonomy()
    variables = taxonomy.get("variables", [])
    if not keywords:
        return []

    results = []
    for var in variables:
        var_kws = [kw.lower() for kw in var.get("keywords", [])]
        for kw in keywords:
            kw_lower = kw.lower()
            for vk in var_kws:
                if kw_lower in vk or vk in kw_lower:
                    if var not in results:
                        results.append(var)
                    break

    # 按变量层级排序：核心变量 > 结构性变量 > 下游结果 > 情绪噪音
    level_order = {"核心变量": 0, "结构性变量": 1, "下游结果": 2, "情绪噪音": 3}
    results.sort(key=lambda x: level_order.get(x.get("level", ""), 99))

    return results


def add_candidates(keywords: list[str], source_article: str = "") -> int:
    """
    将未匹配的关键词追加到候选项队列。

    Args:
      keywords: 未匹配的关键词列表
      source_article: 来源文章标识（如 "中信建投0617"）

    Returns:
      本次新增的候选项数量
    """
    if not keywords:
        return 0

    data = _load_candidates()
    candidates = data.get("candidates", [])
    today = datetime.now().strftime("%Y-%m-%d")
    added = 0

    for kw in keywords:
        kw_clean = kw.strip()
        if not kw_clean:
            continue
        # 检查是否已存在
        existing = [c for c in candidates if c.get("keyword", "").lower() == kw_clean.lower()]
        if existing:
            existing[0]["seen_count"] = existing[0].get("seen_count", 1) + 1
            existing[0]["last_seen"] = today
            if source_article and source_article not in existing[0].get("source_articles", []):
                existing[0]["source_articles"].append(source_article)
        else:
            candidates.append({
                "keyword": kw_clean,
                "first_seen": today,
                "last_seen": today,
                "seen_count": 1,
                "source_articles": [source_article] if source_article else [],
                "status": "pending"
            })
            added += 1

    data["candidates"] = candidates
    data["meta"]["last_checked"] = today
    data["meta"]["total_pending"] = sum(
        1 for c in candidates if c.get("status") == "pending"
    )
    _save_candidates(data)

    return added


def get_candidates(status_filter: str = "pending") -> list[dict]:
    """
    读取候选项队列。

    Args:
      status_filter: "pending" / "classified" / "dismissed" / "all"

    Returns:
      候选项列表
    """
    data = _load_candidates()
    candidates = data.get("candidates", [])
    if status_filter == "all":
        return candidates
    return [c for c in candidates if c.get("status") == status_filter]


def classify_candidate(keyword: str, entry: dict) -> bool:
    """
    将候选项归类到 taxonomy.json。

    Args:
      keyword: 候选项关键词
      entry: 变量条目 dict（与 taxonomy variables 相同结构）

    Returns:
      是否成功
    """
    # 加载 taxonomy
    taxonomy = _load_taxonomy()

    # 生成新 ID
    max_id = 0
    for var in taxonomy.get("variables", []):
        vid = var.get("id", "")
        if vid.startswith("VAR-"):
            try:
                num = int(vid.replace("VAR-", ""))
                max_id = max(max_id, num)
            except ValueError:
                pass
    new_id = f"VAR-{max_id + 1:03d}"
    entry["id"] = new_id

    # 追加到 taxonomy
    taxonomy.setdefault("variables", []).append(entry)
    taxonomy["meta"]["total_variables"] = len(taxonomy["variables"])
    taxonomy["meta"]["updated"] = datetime.now().strftime("%Y-%m-%d")

    TAXONOMY_PATH.parent.mkdir(parents=True, exist_ok=True)
    TAXONOMY_PATH.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")

    # 标记候选项为已分类
    _update_candidate_status(keyword, "classified")

    return True


def dismiss_candidate(keyword: str) -> bool:
    """打回候选项（标记为 dismissed）"""
    return _update_candidate_status(keyword, "dismissed")


def _update_candidate_status(keyword: str, status: str) -> bool:
    """更新候选项状态"""
    data = _load_candidates()
    for c in data.get("candidates", []):
        if c.get("keyword", "").lower() == keyword.lower():
            c["status"] = status
            data["meta"]["total_pending"] = sum(
                1 for x in data.get("candidates", []) if x.get("status") == "pending"
            )
            _save_candidates(data)
            return True
    return False


def get_stats() -> dict:
    """返回统计信息"""
    taxonomy = _load_taxonomy()
    candidates = get_candidates("all")
    pending = get_candidates("pending")
    level_counts = {}
    for var in taxonomy.get("variables", []):
        level = var.get("level", "未知")
        level_counts[level] = level_counts.get(level, 0) + 1
    return {
        "total_variables": len(taxonomy.get("variables", [])),
        "level_breakdown": level_counts,
        "total_candidates": len(candidates),
        "pending_candidates": len(pending),
        "last_updated": taxonomy.get("meta", {}).get("updated", "")
    }


# ─── CLI ───
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        stats = get_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif len(sys.argv) > 2 and sys.argv[1] == "--lookup":
        kw = sys.argv[2:]
        results = lookup_variable(kw)
        print(f"查询: {kw}")
        print(f"匹配: {len(results)} 条")
        for r in results:
            print(f"  {r['id']} [{r['level']}] {r.get('chain','')} → {', '.join(r.get('narratives',[]))}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--candidates":
        pending = get_candidates("pending")
        print(f"待审候选项: {len(pending)} 个")
        for c in pending:
            print(f"  {c['keyword']} (出现{c['seen_count']}次, 首次{c['first_seen']})")
    else:
        print("用法:")
        print("  python tools/variable_taxonomy.py --stats          # 统计信息")
        print("  python tools/variable_taxonomy.py --lookup KW ...  # 关键词查询")
        print("  python tools/variable_taxonomy.py --candidates     # 待审候选项")
```

- [ ] **Step 2: Verify the module loads**

```bash
python -c "from tools.variable_taxonomy import lookup_variable, add_candidates, get_candidates; print('OK')"
```

- [ ] **Step 3: Test lookup function**

```bash
python tools/variable_taxonomy.py --lookup 美伊协议 油价
```

Expected output: matches VAR-001 and VAR-003 (2 results)

- [ ] **Step 4: Commit**

```bash
git add tools/variable_taxonomy.py
git commit -m "feat: add variable_taxonomy.py — lookup + candidate management"
```

---

### Task 3: Modify `gen_daily_brief.py` — integrate taxonomy lookup

**Files:**
- Modify: `gen_daily_brief.py` (import + after LLM topic parsing in `main()`)

- [ ] **Step 1: Add import at top of gen_daily_brief.py**

After line 19 (`from tools.topic_classifier import classify_articles, format_groups_summary`), add:

```python
from tools.variable_taxonomy import lookup_variable, add_candidates
```

- [ ] **Step 2: Add taxonomy lookup after LLM call in main()**

After line 736 (`llm_text = ""`) and the LLM call block, add taxonomy matching logic. Insert new code at line 737 (after the `except` block):

```python
    # ── 5b. 变量分类器匹配 ──
    candidate_count = 0
    if llm_text:
        topics = parse_llm_topics(llm_text)
        unmatched = []
        for topic in topics:
            topic_name = topic.get("topic", "")
            if not topic_name:
                continue
            # 用话题名 + author_details 中的关键信息做关键词匹配
            match_kws = [topic_name]
            for ad in topic.get("author_details", []):
                key_point = ad.get("key_point", "")
                if key_point:
                    match_kws.append(key_point[:30])
            results = lookup_variable(match_kws)
            if results:
                # 匹配到了 — 把 taxonomy 信息灌入 topic dict
                best = results[0]
                topic["_taxonomy"] = {
                    "var_id": best["id"],
                    "level": best["level"],
                    "chain": best.get("chain", ""),
                    "narratives": best.get("narratives", []),
                    "pricing": best.get("pricing", ""),
                    "validation": best.get("validation", [])
                }
            else:
                unmatched.append(topic_name)

        if unmatched:
            candidate_count = add_candidates(unmatched, f"gen_daily_brief_{date}")
            print(f"  变量分类器: {len(topics)-len(unmatched)}/{len(topics)} 话题已匹配")

    if candidate_count > 0:
        print(f"  ⚠ {candidate_count} 个新话题（待分类） → /taxonomy-review")
```

This goes at line ~737, right after the LLM call block and before the section 6 markdown generation.

- [ ] **Step 3: Verify the integration compiles**

```bash
python -c "import py_compile; py_compile.compile('gen_daily_brief.py', doraise=True); print('Syntax OK')"
```

- [ ] **Step 4: Commit**

```bash
git add gen_daily_brief.py
git commit -m "feat: integrate variable taxonomy lookup into gen_daily_brief pipeline"
```

---

### Task 4: Modify `update_sources.bat` — terminal hint

**Files:**
- Modify: `update_sources.bat` (add candidate check after gen_daily_brief)

- [ ] **Step 1: Add candidate check step**

Replace line 149 (`echo.` after `python gen_daily_brief.py`) with:

```batch
echo.

REM 检查变量分类器候选项
python -c "from tools.variable_taxonomy import get_candidates; pending=get_candidates('pending'); print(f'[TAXONOMY] pending={len(pending)}')" 2>nul
for /f "tokens=2 delims==" %%a in ('python -c "from tools.variable_taxonomy import get_candidates; pending=get_candidates('pending'); print(len(pending))" 2^>nul') do set "CAND_CNT=%%a"
if defined CAND_CNT (
    if %CAND_CNT% gtr 0 (
        echo ==========================================
        echo   ⚠ %CAND_CNT% 个新话题未分类 → /taxonomy-review
        echo ==========================================
    )
)
echo.
```

- [ ] **Step 2: Commit**

```bash
git add update_sources.bat
git commit -m "feat: add candidate count check to update_sources.bat"
```

---

### Task 5: Create `/taxonomy-review` Skill

**Files:**
- Create: `C:/Users/Administrator/.claude/skills/taxonomy-review/skill.md`

- [ ] **Step 1: Write the skill markdown**

```markdown
---
name: taxonomy-review
description: 变量分类器审查 — 审查并分类未匹配的话题候选项，更新 variable_taxonomy.json。触发: /taxonomy-review
---

# /taxonomy-review — 变量分类器审查

你是一个**变量分类器维护者**。审查 `variable_candidates.json` 中的待审队列，帮助用户将未分类的话题匹配到变量层级和传导链。

## 执行流程

### 第 0 步：读取待审队列

```bash
python tools/variable_taxonomy.py --candidates
```

### 第 1 步：逐个审查

对每个 pending 候选项：

1. 展示候选项信息：关键词 / 出现次数 / 首次出现日期 / 来源文章
2. 在 `variable_taxonomy.json` 中搜索是否有已存在的变量可以覆盖它
3. 问用户：
   - **归入已有变量**：选一个已有变量，把新关键词追加进去
   - **新建变量**：确定变量层级（核心变量/结构性变量/下游结果/情绪噪音）+ 关联传导链 + 关联叙事链
   - **打回**（dismiss）：这次不处理，标记为 dismissed
   - **跳过**：保持 pending，下次再审

### 第 2 步：执行更新

用户确认后，调用 `tools/variable_taxonomy.py` 中的函数：

```python
from tools.variable_taxonomy import classify_candidate, dismiss_candidate, _load_taxonomy

# 归入已有变量 — 手动更新 taxonomy.json
# 新建变量 — 调用 classify_candidate(keyword, entry)
# 打回 — 调用 dismiss_candidate(keyword)
```

### 第 3 步：打印结果

```
/taxonomy-review 完成
  审查: N 个候选项
  新建变量: X 个
  归入已有: Y 个
  打回: Z 个
  剩余待审: R 个
```

## 新建变量的字段模板

```json
{
  "keywords": ["新关键词1", "新关键词2"],
  "level": "核心变量|结构性变量|下游结果|情绪噪音",
  "chain": "Chain#00X 或 空字符串",
  "narratives": ["#编号"],
  "pricing": "边际变化|延续",
  "validation": ["验证条件1"]
}
```

## 注意事项

- 不要新建和已有变量高度重复的条目
- `seen_count < 3` 的候选项，提示用户"证据不足，建议再观察"
- 变量层级宁缺毋滥：只有真正跨行业影响定价锚的才标"核心变量"
```

- [ ] **Step 2: Register the skill**

The skill frontmatter (`name: taxonomy-review`, `description: ...`) is auto-detected by Claude Code from the skills directory. No settings.json change needed — the skill directory name and the `name` field in frontmatter handle registration.

- [ ] **Step 3: Test skill detection**

In the chat: type `/taxonomy-review` (after restarting VS Code or cc)

- [ ] **Step 4: Commit**

```bash
git add C:/Users/Administrator/.claude/skills/taxonomy-review/skill.md
# Note: this is outside the project repo; track separately or commit user config
```

---

## Summary

| Task | File | Action |
|------|------|--------|
| 1 | `signals/tracking/_macro/variable_taxonomy.json` | Create (28 initial variables) |
| 1 | `signals/tracking/_macro/variable_candidates.json` | Create (empty queue) |
| 2 | `tools/variable_taxonomy.py` | Create (lookup + candidates + CLI) |
| 3 | `gen_daily_brief.py` | Modify (lines 19, ~737) |
| 4 | `update_sources.bat` | Modify (line ~149) |
| 5 | `...skills/taxonomy-review/skill.md` | Create (interactive review skill) |

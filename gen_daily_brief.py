# -*- coding: utf-8 -*-
"""
信源日报聚合层 — 追加"公众号观点聚合"+ "宏观共振判断"到 sources.md。

读取现有 sources.md、今日公众号文章（deep_signals.json 结构化信号优先）、
宏观/流动性/情绪快照，调用 LLM 生成作者观点 vs 宏观数据的共振/分歧分析。

Usage:
  python gen_daily_brief.py                    # 默认今日，追加聚合区块
  python gen_daily_brief.py --date 20260611    # 指定日期

依赖: gen_source_summary.py --ai 先生成 sources.md, signal_deep_reader.py 生成 deep_signals.json
"""
import sys, json, os, re
from pathlib import Path
from datetime import datetime, timedelta
# 规则主题分类器（代替LLM分类）
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from tools.topic_classifier import classify_articles, format_groups_summary
from tools.variable_taxonomy import lookup_variable, add_candidates

# ─── 只处理"宏观/观点"类信源，排除"情绪热点"类（如盘前纪要/一思一记等） ───
# 同步于 _fetch_articles.py 的 ACCOUNT_CATEGORIES
MACRO_ACCOUNTS = {
    '表舅是养基大户', 'laoduo', '海里的小龙龙', '亨特研究笔记',
    '卓哥投研笔记', '灰岩金融科技', '猫笔刀', '中信建投证券研究',
}

PROJECT_ROOT = Path(__file__).parent.resolve()
SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking"
WECHAT_DIR = PROJECT_ROOT / "wechat_articles"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "sources"
DEEP_SIGNALS_DIR = SIGNALS_DIR / "_signals" / "daily_signals"

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─── 宏观 JSON 路径 ───
MACRO_PATHS = {
    "sentiment": SIGNALS_DIR / "_macro" / "sentiment_shock.json",
    "liquidity": SIGNALS_DIR / "_macro" / "liquidity_monitor.json",
    "japan": SIGNALS_DIR / "_macro" / "japan_macro.json",
    "us_macro": SIGNALS_DIR / "_macro" / "us_macro_sensitivity.json",
}


def load_json(path):
    """安全读取 JSON，失败返回空 dict"""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def get_unread_articles(today_str: str) -> list:
    """
    取 wechat_articles/ 下所有文章，过滤掉已在历史 sources.md 中被覆盖的。
    不做日期过滤、不做每号上限——只要没被报告过，就应该纳入分析。
    """
    if not WECHAT_DIR.exists():
        return []

    # ── 1. 收集已读文章：扫描今天+过去7天 sources.md 的 dedup-file 标记 ──
    covered_files = set()
    covered_titles = set()
    today_date = datetime.strptime(today_str, "%Y%m%d")
    for day_offset in range(0, 8):  # 从今天(0)开始，到7天前
        d = today_date - timedelta(days=day_offset)
        d = today_date - timedelta(days=day_offset)
        hist_path = OUTPUT_DIR / f"{d.strftime('%Y%m%d')}_sources.md"
        if not hist_path.exists():
            continue
        text = hist_path.read_text(encoding="utf-8")
        # 方法A: dedup-file 标记（文件名精确匹配，最可靠）
        for m in re.finditer(r'<!--dedup-file: (.+?)-->', text):
            covered_files.add(m.group(1).strip())
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            # 方法B: gen_source_summary 标题格式（兼容旧数据）
            if line.startswith("- **") and "**: " in line:
                title_part = line.split("**: ", 1)[-1].strip()
                if title_part:
                    covered_titles.add(title_part)
            elif line.startswith("└ ") or line.startswith("  └ "):
                title_part = line.replace("└ ", "").strip()
                if title_part:
                    covered_titles.add(title_part)
            elif "**:**" in line:
                pass  # 聚合区摘要无法精确匹配

    # ── 2. 收集所有文章，用文件名（无扩展名）和标题两种方式匹配已读 ──
    all_files = []
    for author_dir in sorted(WECHAT_DIR.iterdir()):
        if not author_dir.is_dir() or author_dir.name.startswith("_"):
            continue
        # ★ 只处理"宏观/观点"类信源，情绪热点号跳过
        if author_dir.name not in MACRO_ACCOUNTS:
            continue
        for f in author_dir.glob("*.txt"):
            all_files.append((f, author_dir.name))

    # 按文件名倒序 = 按时间从新到旧
    all_files.sort(key=lambda x: x[0].name, reverse=True)

    result = []
    for f, author in all_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        title = ""
        for line in content.split("\n")[:3]:
            if line.startswith("标题:"):
                title = line.replace("标题:", "").strip()
                break
        if not title:
            title = f.stem

        # 只看最近5天的文章（文件名前8位 >= 5天前）
        fname = f.name
        if len(fname) >= 8:
            try:
                f_date = datetime.strptime(fname[:8], "%Y%m%d")
                if f_date < today_date - timedelta(days=5):
                    continue
            except ValueError:
                pass

        # 去重：文件路径在 covered_files 中 → 跳过（文件名精确匹配）
        rel_path = str(f.relative_to(PROJECT_ROOT))
        if rel_path in covered_files:
            continue
        # 兼容旧数据：标题匹配（没有 dedup-file 标记的旧版 sources.md）
        if title in covered_titles:
            continue
        stem_parts = f.stem.split("_", 1)
        fn_title = stem_parts[1].replace("_", " ") if len(stem_parts) > 1 else f.stem
        if fn_title in covered_titles:
            continue

        result.append({
            "author": author,
            "title": title,
            "file": rel_path,
            "content": content,  # 全文保留
        })

    return result


def match_deep_signals(articles: list, deep_data: dict) -> list:
    """将 deep_signals 的结构化信号匹配到文章列表"""
    if not deep_data or "articles" not in deep_data:
        return articles

    signal_map = {}
    for da in deep_data["articles"]:
        author = da.get("article_source", "")
        title = da.get("article_title", "")
        key = f"{author}|{title}"
        signal_map[key] = da.get("signals", [])

    for art in articles:
        key = f"{art['author']}|{art['title']}"
        if key in signal_map:
            art["deep_signals"] = signal_map[key]
        else:
            # 尝试模糊匹配（取标题前15字）
            for k, v in signal_map.items():
                if art["author"] in k and art["title"][:15] in k:
                    art["deep_signals"] = v
                    break
    return articles


def build_macro_snapshot() -> str:
    """汇总宏观快照文本"""
    parts = []
    sent = load_json(MACRO_PATHS["sentiment"])
    if sent:
        level = sent.get("impact_level", "?")
        net = sent.get("net_impact", 0)
        parts.append(f"消息面冲击: {level} (净影响 {net})")

    liq = load_json(MACRO_PATHS["liquidity"])
    if liq:
        pressure = liq.get("pressure", "?")
        regime = liq.get("regime", "?")
        parts.append(f"全球流动性: {regime} (压力 {pressure})")

    jp = load_json(MACRO_PATHS["japan"])
    if jp:
        cp = jp.get("carry_pressure", "?")
        cr = jp.get("carry_regime", "?")
        yen = jp.get("yen_signal", "?")
        parts.append(f"日本套息: {cr} (压力 {cp}, 日元 {yen})")

    us = load_json(MACRO_PATHS["us_macro"])
    if us:
        env = us.get("environment", {})
        env_name = env.get("environment", "?") if isinstance(env, dict) else "?"
        parts.append(f"US宏观: {env_name}")

    return "\n".join(parts) if parts else "宏观数据暂缺"


def extract_prev_views(date_str: str) -> str:
    """从昨日 sources.md 提取作者观点作为历史对比"""
    try:
        prev_date = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=1)
    except ValueError:
        return ""
    # 尝试昨天，如果昨天没有再往前推
    for offset in range(1, 4):
        d = prev_date - timedelta(days=offset - 1)
        prev_path = OUTPUT_DIR / f"{d.strftime('%Y%m%d')}_sources.md"
        if not prev_path.exists():
            continue
        text = prev_path.read_text(encoding="utf-8")
        # 提取旧格式：## 📰 公众号观点聚合 区块
        section_m = re.search(r"## 📰 公众号观点聚合(.*?)(?=## |$)", text, re.DOTALL)
        views = []
        if section_m:
            section = section_m.group(1)
            for line in section.split("\n"):
                m = re.match(r'-\s[🔴🟢⚪]\s\*\*(.+?)\*\*:\s(.+)', line)
                if m:
                    views.append(f"  {m.group(1)}: {m.group(2).strip()}")
        else:
            # 提取新格式：## 📰 热点事件分类 区块
            # 使用明确的后继section名作为边界，避免 ### 子标题误匹配
            section_m = re.search(
                r"## 📰 热点事件分类(.*?)(?=## 🆕 今日增量信号|## 🔗 Dorian|## 🆚 宏观|$)",
                text, re.DOTALL
            )
            if section_m:
                section = section_m.group(1)
                # 从作者观点表中提取：| 🟢 作者 | ... 格式
                for line in section.split("\n"):
                    m = re.match(r'\|\s[🔴🟢⚪]\s(.+?)\s\|\s(偏多|偏空|中性)\s\|', line)
                    if m:
                        author = m.group(1).strip()
                        stance = m.group(2).strip()
                        # 找"核心观点"列
                        parts = line.split("|")
                        for i, p in enumerate(parts):
                            if p.strip() in ('偏多', '偏空', '中性'):
                                if i + 1 < len(parts):
                                    detail = parts[i + 1].strip()
                                    views.append(f"  {author} ({stance}): {detail}")
                                break
        if views:
            prev_date_str = d.strftime("%Y-%m-%d")
            return f"昨日（{prev_date_str}）作者观点回顾：\n" + "\n".join(views)
    return ""


def build_llm_prompt(articles: list, macro_text: str, is_incremental: bool,
                     prev_article_titles: set = None, prev_views_text: str = "",
                     date_str: str = "") -> str:
    """
    构建 LLM prompt — 规则分组 + 轻量LLM。
    先用 topic_classifier 按主题分组，再把每组文章标题+关键段落喂给 LLM，
    让 LLM 只做"提取观点+识别标的"的轻量工作，不做分类。
    """
    # ── 1. 规则主题分类 ──
    groups = classify_articles(articles)
    if not groups:
        return "（今日无文章可分析）"

    # ── 2. 按重要性+作者覆盖数排序 ──
    rank = {"high": 0, "medium": 1, "low": 2}
    sorted_topics = sorted(
        groups.items(),
        key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
    )

    # ── 3. 构建 prompt：每组一段，含全文关键内容 ──
    blocks = [f"今日日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    blocks.append("")

    for topic, info in sorted_topics:
        authors_str = "、".join(sorted(info["authors"]))
        blocks.append(f"===== 【{topic}】— {len(info['articles'])}篇 — {len(info['authors'])}位作者: {authors_str} =====")
        for a in info["articles"]:
            # 取文章开头关键部分（~1500字）+ 结尾（~500字）
            full = a["content_full"] if "content_full" in a and a["content_full"] else a.get("content", "")
            # 跳过元数据行
            body = full
            for prefix in ["标题:", "时间:", "链接:", "=================================="]:
                if body.startswith(prefix):
                    nl = body.find("\n", 0)
                    if nl > 0:
                        body = body[nl+1:]
            # 压缩：去空行，保留前2000字+尾500字
            body_clean = re.sub(r"\n{3,}", "\n\n", body.strip())
            if len(body_clean) > 2500:
                snippet = body_clean[:2000] + "\n...（中略）...\n" + body_clean[-500:]
            else:
                snippet = body_clean
            blocks.append(f"\n【{a['author']}】《{a['title']}》")
            blocks.append(snippet)
        blocks.append("")

    blocks.append("\n===== 宏观/流动性快照 =====")
    blocks.append(macro_text if macro_text else "（数据暂缺）")

    if prev_views_text:
        blocks.append(f"\n===== 历史观点对比 =====")
        blocks.append(prev_views_text)

    # ── 4. 给 LLM 的指令（消息面精炼 + 话题深度分析JSON，Dorian技巧嵌入到JSON字段中） ──
    blocks.append("""
===== 分析指令 =====
输出分两部分，严格按顺序：

=== 第一部分：消息面精炼 ===
用 ```msg ``` 包裹，格式为 5 条以内的 markdown 要点：

**① 🇮🇷 主题名 → 一句话驱动**
- 核心事实（具体数字/金额/百分比）
- 公众号交叉引用：作者名"原话"+作者名"原话"
- ⏰ 时间节点/关键日期

**② ...**

要求：
- 每条 3 行以内
- 必须有 emoji 国旗/分类标识
- 必须引用公众号作者原话（交叉验证）
- 不能只是转载新闻标题，要提炼"所以呢"

=== 第二部分：话题深度分析 JSON ===
输出一个 JSON 数组（用 ```json ``` 包裹），每个元素代表一个主题：
{
  "topic": "主题名",
  "importance": "high/medium/low",
  "importance_reason": "N位信源同时指向，当前市场绝对主线（说明理由，不要重复前面importance字段的"高/中/低"前缀）",
  "variable_type": "核心变量/结构性变量/null（真正的核心变量才标「核心变量」，次要但仍重要的标「结构性变量」）",
  "consensus_detail": "一句话共识/分歧判断（如：高共识，AI上游景气度最强）",
  "is_variable": true/false,
  "author_details": [
    {
      "author": "作者名（短名，如laoduo/猫笔刀/卓哥/表舅/中信建投，不要带emoji）",
      "key_point": "一句话核心观点（嵌入Dorian式的判断：是核心矛盾还是传导链？是对标历史还是情景概率？）",
      "narrative_change": "叙事变化（🆕 新叙事/⬆️ 加强/⬇️ 减弱/➡️ 维持/🟡 情绪过热信号/🔄 转向）",
      "stocks": ["股票或ETF代码"]
    }
  ]
}

要求：
- 作者名不要带emoji，不要带"偏多/偏空/中性"
- 叙事变化列体现Dorian技巧——是变盘信号？是历史对标？还是情景概率改变？
- variable_type 区分"核心变量"和"结构性变量"，宁缺毋滥
- 没有把握的字段留null
- 宁少勿滥，不重要的话题不写
""")

    return "\n".join(blocks)


def parse_llm_topics(llm_text: str) -> list:
    """从 LLM 输出中提取结构化主题列表（JSON 数组）"""
    if not llm_text:
        return []
    # 找 ```json [...] ``` 代码块
    m = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', llm_text)
    if m:
        try:
            topics = json.loads(m.group(1))
            if isinstance(topics, list):
                return topics
        except json.JSONDecodeError:
            pass
    # 找裸 [...]（无代码块包裹）
    m = re.search(r'(\[[\s\S]*?\])', llm_text)
    if m:
        try:
            topics = json.loads(m.group(1))
            if isinstance(topics, list):
                return topics
        except json.JSONDecodeError:
            pass
    return []


def parse_llm_json(raw: str) -> dict:
    """从 LLM 输出中提取 JSON"""
    # 尝试直接解析
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 尝试找 {...}
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {}


def _render_topic(topic: dict) -> list:
    """渲染单个话题 —— v2 demo 格式：覆盖→重要性→三列表(作者|核心观点|叙事变化)→共识判断→是否为变量"""
    lines = []
    imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}
    imp_label = {"high": "高", "medium": "中", "low": "低"}

    variable_type = topic.get("variable_type", "")
    topic_name = topic.get("topic", "未命名主题")
    title = f"{imp_icon.get(topic.get('importance', 'medium'), '⚪')} {topic_name}"
    if variable_type:
        title += f" — {variable_type}"
    lines.append(f"### {title}")
    lines.append("")

    # 覆盖：作者数 + 具体名单
    authors = topic.get("author_details", [])
    author_names = "、".join(a.get("author", "") for a in authors) if authors else "?"
    lines.append(f"**覆盖**: {len(authors)}位作者 ({author_names})")

    # 重要性判断（去重LLM可能在前缀重复的"高 —"）
    imp = topic.get("importance", "medium")
    reason = topic.get("importance_reason", "")
    if reason:
        reason = re.sub(r'^(高|中|低)\s*[—\-]\s*', '', reason).strip()
        lines.append(f"**重要性判断**: {imp_label.get(imp, '')} — {reason}")

    # 作者观点表（三列：作者 | 核心观点 | 叙事变化）
    if authors:
        lines.append("")
        lines.append("| 作者 | 核心观点 | 叙事变化 |")
        lines.append("|------|---------|---------|")
        for a in authors:
            name = a.get("author", "")
            key_point = a.get("key_point", "")
            narrative_change = a.get("narrative_change", "")
            lines.append(f"| {name} | {key_point} | {narrative_change} |")

    # 共识判断
    consensus = topic.get("consensus_detail", "")
    if consensus:
        lines.append("")
        lines.append(f"**共识判断**: {consensus}")

    # 是否为变量（variable_type 存在即视为变量）
    vt = topic.get("variable_type", "")
    is_var = topic.get("is_variable", False) or bool(vt)
    if is_var and vt:
        lines.append(f"**是否为变量**: **是，{vt}**")

    lines.append("")
    return lines



def generate_topic_analysis(articles: list, llm_text: str) -> str:
    """生成话题深度分析（不含Dorian独立章节——Dorian技巧已嵌入到话题块的叙事变化列中）"""
    lines = []

    topics = parse_llm_topics(llm_text)
    if topics:
        lines.extend(["\n---\n", "## 🧠 话题深度分析\n"])
        for t in topics:
            lines.extend(_render_topic(t))
    else:
        groups = classify_articles(articles)
        if groups:
            lines.extend(["\n---\n", "## 🧠 话题深度分析\n"])
            rank = {"high": 0, "medium": 1, "low": 2}
            sorted_topics = sorted(
                groups.items(),
                key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
            )
            for topic, info in sorted_topics:
                imp = info["importance"]
                imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(imp, "⚪")
                imp_label = {"high": "高重要性", "medium": "一般", "low": "低"}.get(imp, "")
                author_count = len(info["authors"])
                author_names = "、".join(sorted(info["authors"]))
                lines.append(f"### {imp_icon} {topic}")
                lines.append(f"> {imp_label} · {author_count}位作者提及: {author_names}")
                lines.append("")
                for a in info["articles"]:
                    lines.append(f"- **{a['author']}** 《{a['title'][:50]}》")
                if info["importance"] == "high" and author_count >= 3:
                    lines.append(f"> ⚡ **重点信号**: {author_count}位作者同题，为当前市场核心矛盾")
                lines.append("")
        else:
            lines.append("（暂无分类文章）\n")

    return "\n".join(lines) + "\n"


def format_article_table(articles: list) -> str:
    """生成简洁文章清单表格（放末尾），日期格式 MMDD，列名为 作者|日期|内容"""
    # 作者名缩写映射
    AUTHOR_SHORT = {
        "中信建投证券研究": "中信建投",
        "亨特研究笔记": "亨特",
        "卓哥投研笔记": "卓哥",
        "表舅是养基大户": "表舅",
    }
    lines = ["\n---\n", "## 📋 本期覆盖文章\n"]
    lines.append("| 作者 | 日期 | 内容 |")
    lines.append("|------|------|------|")
    for art in articles:
        # 从文件名提取日期格式化为 MMDD
        fname = art["file"].split("\\")[-1] if "\\" in art["file"] else art["file"].split("/")[-1]
        if len(fname) >= 8 and fname[:8].isdigit():
            date_str = fname[4:8]  # YYYYMMDD → MMDD
        else:
            date_str = "??"
        # 缩写作者名
        author = AUTHOR_SHORT.get(art["author"], art["author"])
        # 精简标题
        title = art["title"]
        # 去掉"首席经济学家XXX："前缀
        title = re.sub(r'^首席经济学家\S+[：:]?\s*', '', title)
        # 去掉"中信建投"前缀（包括"中信建投：""中信建投 |"等变体）
        title = re.sub(r'^中信建投\s*[：|]?\s*', '', title)
        if len(title) > 36:
            title = title[:35] + "…"
        if not title.strip():
            title = "（标题暂缺）"
        lines.append(f"| {author} | {date_str} | {title} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def _extract_msg_block(llm_text: str) -> str:
    """从 LLM 输出中提取 ```msg``` 块内容"""
    m = re.search(r'```msg\s*(.*?)\s*```', llm_text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _strip_msg_block(llm_text: str) -> str:
    """从 LLM 文本中移除 ```msg``` 块"""
    return re.sub(r'```msg\s*.*?\s*```\s*', '', llm_text, flags=re.DOTALL).strip()


def generate_full_section(parsed: dict, articles: list, llm_text: str,
                          existing_content: str = "") -> str:
    """生成完整报告 — 消息面要点(LLM精炼) → 话题深度分析 → 其余数据 → 文章清单"""
    msg_refined = _extract_msg_block(llm_text)
    cleaned_llm_text = _strip_msg_block(llm_text)
    topic_section = generate_topic_analysis(articles, cleaned_llm_text)
    article_table = format_article_table(articles)

    if existing_content:
        msg_match = re.search(
            r'(## 🔴 消息面要点.*?)(?=\n## )',
            existing_content, re.DOTALL
        )
        if msg_match and msg_refined:
            refined_section = "## 🔴 消息面要点\n\n" + msg_refined + "\n"
            rest = existing_content[msg_match.end():]
            # 去掉旧 LLM 生成的区块（话题分析/Dorian/文章清单），避免重复
            for header in ['## 🧠 话题深度分析', '## 🔗 Dorian 六步拆解', '## 🔗 Dorian', '## 📋 本期覆盖文章']:
                rest = re.sub(
                    rf'\n{re.escape(header)}.*?(?=\n## +|\Z)',
                    '', rest, flags=re.DOTALL
                )
            rest = re.sub(r'\n<!--dedup-file:.*?-->', '', rest)
            result = refined_section + "\n\n" + topic_section.strip() + "\n\n" + rest.strip()
        elif msg_match:
            msg_part = msg_match.group(1)
            rest = existing_content[msg_match.end():]
            for header in ['## 🧠 话题深度分析', '## 🔗 Dorian 六步拆解', '## 🔗 Dorian', '## 📋 本期覆盖文章']:
                rest = re.sub(
                    rf'\n{re.escape(header)}.*?(?=\n## +|\Z)',
                    '', rest, flags=re.DOTALL
                )
            rest = re.sub(r'\n<!--dedup-file:.*?-->', '', rest)
            result = msg_part + "\n\n" + topic_section.strip() + "\n\n" + rest.strip()
        else:
            result = existing_content + "\n" + topic_section
        result += article_table
        result = re.sub(r'\n(---\n){2,}', '\n---\n', result)
        return result
    else:
        result = topic_section
        result += article_table
        return result


def generate_incremental_section(parsed: dict, old_titles: set,
                                 new_articles: list, now_str: str) -> str:
    """生成增量更新区块"""
    lines = ["\n---\n", f"## 🔄 午后更新 （{now_str}）\n"]

    new_files = [a["file"] for a in new_articles]
    truly_new = [f for f in new_files if f not in old_titles]

    if truly_new:
        lines.append("**新增文章**:")
        for t in truly_new:
            lines.append(f"- {t}")
    else:
        lines.append("（无新增文章）")

    # 如果有新文章，重新分组展示
    groups = classify_articles(new_articles)
    if groups:
        lines.extend(["", "**新增热点**:", ""])
        rank = {"high": 0, "medium": 1, "low": 2}
        sorted_topics = sorted(
            groups.items(),
            key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
        )
        for topic, info in sorted_topics:
            imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(info["importance"], "⚪")
            author_names = "、".join(sorted(info["authors"]))
            lines.append(f"{imp_icon} **{topic}** — {len(info['authors'])}位作者: {author_names}")
            for a in info["articles"]:
                lines.append(f"  - {a['author']}: {a['title'][:40]}")
            lines.append("")

    # LLM 增量分析
    llm_text = parsed.get("_llm_text", "")
    if llm_text and isinstance(parsed, dict):
        lines.extend(["---", "**AI增量分析**:", ""])
        lines.append(llm_text.strip())

    return "\n".join(lines) + "\n"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="信源日报聚合层 — 规则分类+轻量LLM追加到 sources.md")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--dry-run", action="store_true", help="只打印分类结果，不调LLM")
    args = parser.parse_args()

    date = args.date
    now_str = datetime.now().strftime("%H:%M")
    sources_md = OUTPUT_DIR / f"{date}_sources.md"
    deep_signals_path = DEEP_SIGNALS_DIR / f"{date}_deep_signals.json"

    if not sources_md.exists():
        print(f"[gen_daily_brief] ❌ {sources_md.name} 不存在，请先运行 gen_source_summary.py --ai")
        return

    # ── 1. 读取数据 ──
    print(f"[gen_daily_brief] 读取 sources.md + 今日文章 + 宏观数据...")
    report_content = sources_md.read_text(encoding="utf-8")
    articles = get_unread_articles(date)
    deep_data = load_json(deep_signals_path)
    articles = match_deep_signals(articles, deep_data)
    macro_text = build_macro_snapshot()
    print(f"  公众号文章: {len(articles)} 篇")
    print(f"  Deep Signals: {'有' if deep_data else '无'}")

    # ── 2. 检测模式 ──
    has_new_section = "## 📰 热点事件分类" in report_content or "## 📰 公众号观点聚合" in report_content
    if has_new_section:
        prev_files = set()
        for m in re.finditer(r'<!--dedup-file: (.+?)-->', report_content):
            prev_files.add(m.group(1).strip())
        mode = "incremental"
        print(f"  模式: 增量更新（已有 {len(prev_files)} 篇，今日 {len(articles)} 篇）")
    else:
        mode = "full"
        prev_files = set()
        print(f"  模式: 完整追加（首次运行）")

    if not articles:
        print("[gen_daily_brief] ⚠ 今日无文章，跳过 LLM 分析")
        if not has_new_section:
            fallback = ("\n---\n## 📋 本期覆盖文章\n"
                        "（今日暂无公众号文章）\n")
            sources_md.write_text(report_content + fallback, encoding="utf-8")
            print(f"[gen_daily_brief] ✅ 已追加空聚合区到 {sources_md.name}")
        return

    # ── 3. 规则分类展示（不依赖LLM） ──
    groups = classify_articles(articles)
    rank = {"high": 0, "medium": 1, "low": 2}
    sorted_topics = sorted(
        groups.items(),
        key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
    )
    print(f"\n  规则分类结果:")
    for topic, info in sorted_topics:
        authors_str = "、".join(sorted(info["authors"]))
        print(f"    {topic}: {len(info['articles'])}篇, {len(info['authors'])}位作者({authors_str})")

    # ── 4. 提取昨日观点（用于LLM上下文） ──
    prev_views_text = extract_prev_views(date)

    # ── 5. 轻量 LLM 调用 ──
    if args.dry_run:
        print("\n[dry-run] 跳过LLM调用，只输出规则分类结果")
        # 只输出文章清单+规则分类
        fallback_lines = ["\n---\n", "## 📋 本期覆盖文章\n"]
        fallback_lines.append("| 作者 | 日期 | 文章 |")
        fallback_lines.append("|------|------|------|")
        for art in articles:
            date_str = art["file"][-20:-12] if len(art["file"]) > 12 else "?"
            fallback_lines.append(f"| {art['author']} | {date_str} | {art['title'][:40]} |")
        fallback_lines.append("")
        for topic, info in sorted_topics:
            imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(info["importance"], "⚪")
            author_names = "、".join(sorted(info["authors"]))
            fallback_lines.append(f"### {imp_icon} {topic} — {len(info['authors'])}位作者: {author_names}")
            for a in info["articles"]:
                fallback_lines.append(f"- **{a['author']}** 《{a['title'][:50]}》")
            fallback_lines.append("")
        for art in articles:
            fallback_lines.append(f"<!--dedup-file: {art['file']}-->")
        section = "\n".join(fallback_lines) + "\n"
        sources_md.write_text(report_content + section, encoding="utf-8")
        print(f"[gen_daily_brief] ✅ dry-run 分类已追加到 {sources_md.name}")
        return

    prompt = build_llm_prompt(articles, macro_text,
                              is_incremental=(mode == "incremental"),
                              prev_article_titles=prev_files,
                              prev_views_text=prev_views_text,
                              date_str=date)

    print(f"\n  调用 LLM 进行观点摘要（轻量）...")
    try:
        from ai_analyzer import call_llm
        system_prompt = "你是一个交易员视角的市场分析助手。把公众号文章按主题分组，提取各作者核心观点和股票代码，输出Dorian六步分析。用中文，交易员的语言，短句。"
        llm_text, provider = call_llm(system_prompt, prompt, max_tokens=4096)
        print(f"  LLM: {provider} ({len(llm_text)} chars)")
    except Exception as e:
        print(f"  ⚠ LLM 调用失败: {e}")
        llm_text = ""

    # ── 5b. 变量分类器匹配 ──
    candidate_count = 0
    if llm_text:
        topics = parse_llm_topics(llm_text)
        unmatched = []
        for topic in topics:
            topic_name = topic.get("topic", "")
            if not topic_name:
                continue
            match_kws = [topic_name]
            for ad in topic.get("author_details", []):
                key_point = ad.get("key_point", "")
                if key_point:
                    match_kws.append(key_point[:30])
            results = lookup_variable(match_kws)
            if results:
                best = results[0]
                topic["_taxonomy"] = {
                    "var_id": best["id"],
                    "level": best["level"],
                    "chain": best.get("chain", ""),
                    "narratives": best.get("narratives", []),
                    "pricing": best.get("pricing", ""),
                    "validation": best.get("validation", []),
                }
            else:
                unmatched.append(topic_name)

        if unmatched:
            candidate_count = add_candidates(unmatched, f"gen_daily_brief_{date}")
            matched = len(topics) - len(unmatched)
            print(f"  变量分类器: {matched}/{len(topics)} 话题已匹配")

    if candidate_count > 0:
        print(f"  ⚠ {candidate_count} 个新话题（待分类） → /taxonomy-review")

    # ── 6. 生成 Markdown（话题深度分析插在消息面要点后，文章清单放末尾） ──
    if mode == "full":
        parsed = {"_llm_text": llm_text} if llm_text else {}
        section = generate_full_section(parsed, articles, llm_text, existing_content=report_content)
        sources_md.write_text(section, encoding="utf-8")
    else:
        parsed = {"_llm_text": llm_text} if llm_text else {}
        section = generate_incremental_section(parsed, prev_files, articles, now_str)
        sources_md.write_text(report_content + section, encoding="utf-8")
    print(f"[gen_daily_brief] ✅ 聚合层已追加到 {sources_md.name}")

    # ── 7. 打印摘要 ──
    print(f"\n  📊 本期覆盖: {len(articles)} 篇文章, {len(groups)} 个主题")
    for topic, info in sorted_topics[:5]:
        imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(info["importance"], "⚪")
        print(f"    {imp_icon} {topic} ({len(info['authors'])}位作者)")
    if llm_text:
        print(f"  🤖 AI观点摘要: {len(llm_text)} chars")


if __name__ == "__main__":
    main()

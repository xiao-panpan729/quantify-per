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

    # ── 4. 给 LLM 的指令（极简） ──
    blocks.append("""
===== 分析指令 =====
你对以上每个【主题】输出一段分析（纯文本，不要JSON），包含：
1. 该主题的核心事件/驱动是什么（一句话）
2. 各作者的核心观点和方向（谁说了什么）
3. 有没有共识或分歧：多人说一样的事=重要
4. 提到的关键股票代码

然后单独输出一段 Dorian 六步分析（针对全局）：
① 核心变量 ② 传导路径 ③ 历史对标 ④ 情景分析 ⑤ 交易含义 ⑥ 失效条件

不要分块报告，不要列"公众号部分/量化部分"，不要平铺数据。
用交易员的语言，短句，一段不超过5行。
""")

    return "\n".join(blocks)


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
    """渲染单个热点条目"""
    lines = []
    importance = topic.get("importance", "medium")
    imp_map = {"high": "🔴", "medium": "🟡", "low": "⚪"}
    imp_label = {"high": "高重要性", "medium": "一般", "low": "低"}

    heat = topic.get("heat_change", "")
    heat_str = {"升温": "🔥 热度上升", "降温": "❄️ 热度下降", "维持": "➖ 热度维持"}.get(heat, "")

    info_type = topic.get("info_type", "")
    type_tag = {"新增": "🆕 新增", "延续": "➡️ 延续", "消退": "⬇️ 消退"}.get(info_type, info_type)

    consensus = topic.get("consensus", "")
    cons_map = {"一致看多": "🟢", "一致看空": "🔴", "分歧": "🟡", "无明显共识": "⚪"}

    # 主题行
    topic_name = topic.get("topic", "未命名主题")
    lines.append(f"### {imp_map[importance]} {topic_name}")
    meta_parts = [f"{imp_label[importance]}"]
    if type_tag:
        meta_parts.append(type_tag)
    if heat_str:
        meta_parts.append(heat_str)
    if consensus:
        meta_parts.append(f"{cons_map.get(consensus, '')} {consensus}")
    lines.append(f"> {' · '.join(meta_parts)}")

    # 事件驱动
    driver = topic.get("event_driver", "")
    if driver:
        lines.append(f"> **事件**: {driver}")

    # 作者观点表
    authors = topic.get("author_details", [])
    if authors:
        lines.append("")
        lines.append("| 作者 | 方向 | 变化 | 核心观点 | 标的 |")
        lines.append("|------|------|------|----------|------|")
        stance_emoji = {"偏多": "🟢", "偏空": "🔴", "中性": "⚪"}
        for a in authors:
            emoji = stance_emoji.get(a.get("stance", ""), "⚪")
            change = a.get("change_vs_yesterday", "首次")
            change_icon = {"加强": "⬆️", "维持": "➡️", "减弱": "⬇️", "扭转": "🔄", "首次": "🆕"}
            ci = change_icon.get(change, "")
            stocks = ", ".join(a.get("stocks", [])[:4]) if a.get("stocks") else "—"
            lines.append(f"| {emoji} {a.get('author','')} | {a.get('stance','')} | {ci}{change} | {a.get('key_point','')} | {stocks} |")

    # 关键标的汇总
    stocks = topic.get("key_stocks", [])
    if stocks:
        lines.append("")
        lines.append(f"**关键标的**: {'、'.join(stocks[:8])}")

    sectors = topic.get("related_sectors", [])
    if sectors:
        lines.append(f"**关联板块**: {'、'.join(sectors[:5])}")

    lines.append("")
    return lines


def generate_dorian_section(dorian: dict) -> list:
    """生成 Dorian 六步分析区块"""
    if not dorian:
        return []

    lines = [
        "",
        "---",
        "## 🔗 Dorian 六步拆解",
        "",
    ]

    steps = [
        ("① 核心变量", dorian.get("core_variable", "")),
        ("② 传导路径", dorian.get("transmission_path", "")),
        ("③ 历史对标", dorian.get("historical_analog", "")),
    ]
    for label, content in steps:
        if content:
            lines.append(f"**{label}**: {content}")
            lines.append("")

    # 情景分析表
    scenarios = dorian.get("scenarios", [])
    if scenarios:
        lines.append("**④ 情景分析**:")
        lines.append("")
        lines.append("| 情景 | 概率 | 影响 |")
        lines.append("|------|------|------|")
        for s in scenarios:
            lines.append(f"| {s.get('condition','')} | {s.get('probability','')} | {s.get('impact','')} |")
        lines.append("")

    ti = dorian.get("trading_implication", "")
    if ti:
        lines.append(f"**⑤ 交易含义**: {ti}")
        lines.append("")

    fs = dorian.get("failure_signals", "")
    if fs:
        lines.append(f"**⑥ 失效条件**: {fs}")
        lines.append("")

    return lines


def generate_full_section(parsed: dict, articles: list, llm_text: str) -> str:
    """生成完整聚合区块 — 盘前纪要风格（规则分组 + 轻量 LLM 摘要）"""
    lines = ["\n---\n", "## 📋 本期覆盖文章\n"]

    # ── 1. 文章清单（透明） ──
    lines.append("| 作者 | 日期 | 文章 |")
    lines.append("|------|------|------|")
    for art in articles:
        date_str = art["file"][-20:-12] if len(art["file"]) > 12 else "?"
        title = art["title"][:40]
        lines.append(f"| {art['author']} | {date_str} | {title} |")
    lines.append("")

    # ── 2. 规则分类展示 ──
    groups = classify_articles(articles)
    if groups:
        lines.extend(["\n---\n", "## 📰 热点事件分类\n"])
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

    # ── 3. AI 摘要区块 ──
    if llm_text:
        lines.extend(["\n---\n", "## 🤖 AI 观点分析\n"])
        # 分离 Dorian 部分（如果在 llm_text 中）
        if "① 核心变量" in llm_text or "Dorian" in llm_text:
            # 把 Dorian 部分单独提取出来
            dorian_parts = []
            ai_parts = []
            in_dorian = False
            for line in llm_text.split("\n"):
                if "① 核心变量" in line or "Dorian" in line:
                    in_dorian = True
                if in_dorian:
                    dorian_parts.append(line)
                else:
                    ai_parts.append(line)
            if ai_parts:
                lines.extend(ai_parts)
                lines.append("")
            if dorian_parts:
                lines.extend(["\n---\n", "## 🔗 Dorian 六步拆解\n"])
                lines.extend(dorian_parts)
                lines.append("")
        else:
            lines.append(llm_text.strip())
            lines.append("")
    else:
        lines.extend(["\n---\n", "## 🤖 AI 观点分析\n（AI 分析暂不可用）\n"])

    # ── 4. dedup 文件标记 ──
    for art in articles:
        lines.append(f"<!--dedup-file: {art['file']}-->")

    return "\n".join(lines) + "\n"


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

    # ── 6. 生成 Markdown（规则分组展示 + LLM摘要） ──
    if mode == "full":
        parsed = {"_llm_text": llm_text} if llm_text else {}
        section = generate_full_section(parsed, articles, llm_text)
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

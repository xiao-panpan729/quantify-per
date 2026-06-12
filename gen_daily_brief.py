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

    # ── 1. 收集已读文章：扫描过去7天 sources.md 的 dedup-file 标记 ──
    covered_files = set()
    covered_titles = set()
    today_date = datetime.strptime(today_str, "%Y%m%d")
    for day_offset in range(1, 8):
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
            "content": content[:1500],
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


def build_llm_prompt(articles: list, macro_text: str, is_incremental: bool,
                     prev_article_titles: set = None) -> str:
    """构建 LLM prompt，要求返回 JSON"""
    articles_block = ""
    for art in articles:
        signals_block = ""
        if art.get("deep_signals"):
            for s in art["deep_signals"][:3]:
                signals_block += f"    - [{s['direction']}] {s['signal_type']}: {s['text'][:100]}\n"
        articles_block += (
            f"\n【{art['author']}】{art['title']}\n"
            f"  内容概要: {art['content'][:300]}\n"
            f"{signals_block}"
        )

    prompt = f"""今日日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## 各公众号文章
{articles_block if articles_block else "（今日暂无公众号文章）"}

## 宏观/流动性快照
{macro_text if macro_text else "（数据暂缺）"}
"""
    if is_incremental and prev_article_titles:
        prompt += f"""
## 上次运行已有的文章（请在 delta 中对比新增）
{chr(10).join(f'- {t}' for t in prev_article_titles)}
"""

    prompt += """
请分析以上信源，输出 JSON（不要 markdown 包裹，纯 JSON）：
{
  "author_views": [
    {
      "name": "作者名",
      "stance": "偏多/偏空/中性",
      "key_point": "核心观点一句话",
      "chains": ["关联产业链", ...]
    }
  ],
  "consensus": "作者间共识方向（偏多/偏空/分歧）",
  "consensus_detail": "共识/分歧的具体说明",
  "macro_vs_views": [
    {
      "dimension": "维度名（如流动性/消息面/US宏观）",
      "signal": "当前信号描述",
      "alignment": "共振/背离/中性"
    }
  ],
  "summary": "综合判断（宏观与观点是否共振，市场含义）"
}"""
    return prompt


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


def generate_full_section(parsed: dict, articles: list) -> str:
    """生成完整聚合区块 Markdown"""
    lines = ["\n---\n", "## 📰 公众号观点聚合\n"]
    views = parsed.get("author_views", [])
    if views:
        for v in views:
            stance_emoji = {"偏多": "🟢", "偏空": "🔴", "中性": "⚪"}
            emoji = stance_emoji.get(v.get("stance", ""), "⚪")
            chains = v.get("chains", [])
            chain_str = f"（{', '.join(chains[:3])}）" if chains else ""
            lines.append(f"- {emoji} **{v['name']}**: {v['key_point']} {chain_str}")
    else:
        lines.append(f"（今日 {len(articles)} 篇文章，暂无结构化观点）")

    consensus = parsed.get("consensus", "—")
    consensus_detail = parsed.get("consensus_detail", "")
    lines.extend([
        "",
        f"**作者共识**: {consensus}",
        f"{'  ' + consensus_detail if consensus_detail else ''}",
    ])

    lines.extend(["", "## 🔗 宏观 vs 观点 共振判断\n"])
    mv = parsed.get("macro_vs_views", [])
    if mv:
        lines.append("| 维度 | 信号 | 与观点共识 |")
        lines.append("|------|------|-----------|")
        for m in mv:
            align_emoji = {"共振": "✅", "背离": "⚠️", "中性": "➖"}
            ae = align_emoji.get(m.get("alignment", ""), "➖")
            lines.append(f"| {m['dimension']} | {m['signal']} | {ae} {m['alignment']} |")
    else:
        lines.append("（暂无宏观对比数据）")

    summary = parsed.get("summary", "")
    if summary:
        lines.extend(["", f"**综合判断**: {summary}"])

    # 埋已覆盖文章文件名，供后续去重用（文件名唯一、可靠，不依赖标题匹配）
    lines.append("")
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

    mv = parsed.get("macro_vs_views", [])
    if mv:
        lines.extend(["", "**宏观共振更新**:", ""])
        for m in mv:
            align_emoji = {"共振": "✅", "背离": "⚠️", "中性": "➖"}
            ae = align_emoji.get(m.get("alignment", ""), "➖")
            lines.append(f"- {m['dimension']}: {ae} {m['alignment']} — {m['signal']}")

    summary = parsed.get("summary", "")
    if summary:
        lines.extend(["", f"**更新判断**: {summary}"])

    return "\n".join(lines) + "\n"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="信源日报聚合层 — 追加观点聚合+共振判断到 sources.md")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
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
    if deep_data:
        print(f"    其中 {deep_data.get('statistics', {}).get('success', 0)} 篇精读成功")

    # ── 2. 检测模式 ──
    has_full_section = "## 📰 公众号观点聚合" in report_content
    if has_full_section:
        # 从聚合区标记中提取已覆盖的文章文件路径
        prev_files = set()
        for m in re.finditer(r'<!--dedup-file: (.+?)-->', report_content):
            prev_files.add(m.group(1).strip())
        mode = "incremental"
        print(f"  模式: 增量更新（已有 {len(prev_files)} 篇，今日 {len(articles)} 篇）")
    else:
        mode = "full"
        prev_files = set()
        print(f"  模式: 完整追加（首次运行）")

    # ── 3. 调用 LLM ──
    if not articles:
        print("[gen_daily_brief] ⚠ 今日无文章，跳过 LLM 分析")
        if not has_full_section:
            fallback = ("\n---\n## 📰 公众号观点聚合\n"
                        "（今日暂无公众号文章）\n")
            sources_md.write_text(report_content + fallback, encoding="utf-8")
            print(f"[gen_daily_brief] ✅ 已追加空聚合区到 {sources_md.name}")
        return

    prompt = build_llm_prompt(articles, macro_text,
                              is_incremental=(mode == "incremental"),
                              prev_article_titles=prev_files)

    print(f"  调用 LLM 进行聚合分析...")
    try:
        from ai_analyzer import call_llm
        system_prompt = "你是信源聚合分析师。分析公众号作者观点与宏观数据的共振或背离关系，输出严格 JSON。"
        raw_output, provider = call_llm(system_prompt, prompt, max_tokens=2048)
        print(f"  LLM: {provider}")
    except Exception as e:
        print(f"  ⚠ LLM 调用失败: {e}")
        raw_output = ""

    parsed = parse_llm_json(raw_output) if raw_output else {}
    if not parsed:
        print("  ⚠ LLM 输出解析失败，使用降级文本")
        # 降级：直接用文章标题拼接
        fallback_lines = ["\n---\n", "## 📰 公众号观点聚合（降级）\n"]
        for art in articles:
            fallback_lines.append(f"- **{art['author']}**: {art['title']}")
        fallback_lines.extend([
            "",
            "## 🔗 宏观 vs 观点 共振判断",
            "（LLM 分析暂不可用，宏观数据如下）",
            macro_text,
        ])
        section = "\n".join(fallback_lines) + "\n"
        sources_md.write_text(report_content + section, encoding="utf-8")
        print(f"[gen_daily_brief] ✅ 降级文本已追加到 {sources_md.name}")
        return

    # ── 4. 生成 Markdown 并追加 ──
    if mode == "full":
        section = generate_full_section(parsed, articles)
    else:
        section = generate_incremental_section(parsed, prev_files, articles, now_str)

    sources_md.write_text(report_content + section, encoding="utf-8")
    print(f"[gen_daily_brief] ✅ 聚合层已追加到 {sources_md.name}")

    # ── 展示追加的核心内容 ──
    views = parsed.get("author_views", [])
    mv = parsed.get("macro_vs_views", [])
    summary = parsed.get("summary", "")
    consensus = parsed.get("consensus", "")

    long_count = sum(1 for v in views if v.get("stance") == "偏多")
    short_count = sum(1 for v in views if v.get("stance") == "偏空")
    neutral_count = sum(1 for v in views if v.get("stance") == "中性")
    print(f"  作者观点: {len(views)} 条（🟢偏多 {long_count} / 🔴偏空 {short_count} / ⚪中性 {neutral_count}）")

    if consensus:
        print(f"  作者共识: {consensus}")

    if mv:
        resonate = sum(1 for m in mv if m.get("alignment") == "共振")
        diverge = sum(1 for m in mv if m.get("alignment") == "背离")
        print(f"  共振维度: {len(mv)} 项（✅共振 {resonate} / ⚠️背离 {diverge}）")
        for m in mv[:3]:
            dim = m.get("dimension", "?")
            sig = m.get("signal", "?")
            align = m.get("alignment", "?")
            print(f"    - {dim}: {sig} [{align}]")

    if summary:
        print(f"  综合判断: {summary[:120]}")


if __name__ == "__main__":
    main()

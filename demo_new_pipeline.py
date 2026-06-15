# -*- coding: utf-8 -*-
"""
新流水线演示 — 规则分类 + 全量文章 + 轻量LLM
生成独立报告，不修改现有 sources.md
"""
import sys, json, os, re
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from tools.topic_classifier import classify_articles

# ─── 配置 ────────────────────────────────────────────────────
DATE = "20260614"
OLD_ACCOUNTS = ['laoduo', '中信建投证券研究', '亨特研究笔记', '卓哥投研笔记',
                '海里的小龙龙', '灰岩金融科技', '猫笔刀', '表舅是养基大户']
WECHAT_DIR = PROJECT_ROOT / "wechat_articles"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "sources"
OUTPUT_FILE = OUTPUT_DIR / f"{DATE}_sources_v2_demo.md"


def load_articles():
    """从8个旧公众号加载最近的文章（跳过已读的，保留全文）"""
    today = datetime.strptime(DATE, "%Y%m%d")
    articles = []

    for author_dir in sorted(WECHAT_DIR.iterdir()):
        if author_dir.name not in OLD_ACCOUNTS:
            continue
        files = sorted(author_dir.glob("*.txt"), reverse=True)
        for f in files:
            # 5天窗口
            fname = f.name
            if len(fname) >= 8:
                try:
                    fdate = datetime.strptime(fname[:8], "%Y%m%d")
                    if fdate < today - timedelta(days=5):
                        continue
                except ValueError:
                    pass

            content = f.read_text(encoding="utf-8", errors="replace")
            title = ""
            url = ""
            for line in content.split("\n")[:5]:
                if line.startswith("标题:"):
                    title = line.replace("标题:", "").strip()
                elif line.startswith("链接:"):
                    url = line.replace("链接:", "").strip()
            if not title:
                title = f.stem[9:].replace("_", " ") if len(f.stem) > 9 else f.stem

            articles.append({
                "author": author_dir.name,
                "title": title,
                "url": url,
                "file": str(f.relative_to(PROJECT_ROOT)),
                "content": content,  # 全文
                "date": fname[:8] if len(fname) >= 8 else "?",
            })

    # 按日期排序（最新的在前），但每个作者最多取3篇
    seen = {}
    result = []
    for art in reversed(articles):
        seen.setdefault(art["author"], 0)
        if seen[art["author"]] < 3:
            result.append(art)
            seen[art["author"]] += 1

    return result


def clean_body(text):
    """清理公众号正文：去掉元数据、阅读器广告"""
    # 去掉前几行的元数据
    lines = text.split("\n")
    clean = []
    skip_header = True
    for line in lines:
        if skip_header:
            if line.startswith("==="):
                skip_header = False
            continue
        # 跳过阅读器广告行
        stripped = line.strip()
        if stripped in ["在小说阅读器读本章", "去阅读", "在小说阅读器中沉浸阅读", "原创"]:
            continue
        # 跳过作者名重复行（原创后面的作者行）
        if stripped and all(c == stripped[0] for c in stripped):
            continue
        clean.append(line)
    text = "\n".join(clean)
    # 合并多余空行
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def build_report(articles, llm_analysis="", groups=None):
    """构建报告文本（紧凑版：综述 + LLM深度观点对比）"""
    lines = []
    date_str = f"{DATE[:4]}-{DATE[4:6]}-{DATE[6:8]}"
    now = datetime.now().strftime("%H:%M")

    # ─── 标题 ───
    lines.append(f"# 信源聚合报告 {date_str} {now}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ─── 1. 文章清单（透明） ───
    lines.append("## 📋 本期覆盖文章\n")
    lines.append("| 作者 | 日期 | 文章 | 链接 |")
    lines.append("|------|------|------|------|")
    for art in articles:
        title_short = art["title"][:40] + "..." if len(art["title"]) > 40 else art["title"]
        url_md = f"[原文]({art['url']})" if art.get("url") else ""
        lines.append(f"| {art['author']} | {art['date'][-8:]} | {title_short} | {url_md} |")
    lines.append("")

    # ─── 2. 热点扫描（规则匹配，1行概览，不重复引文） ───
    lines.append("---")
    lines.append("## 📊 热点扫描（规则匹配）\n")

    if groups:
        rank = {"high": 0, "medium": 1, "low": 2}
        sorted_topics = sorted(
            groups.items(),
            key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
        )

        current_imp = None
        for topic, info in sorted_topics:
            imp = info["importance"]
            if imp != current_imp:
                current_imp = imp
                imp_label = {"high": "🔴 高重要性", "medium": "🟡 一般", "low": "⚪ 低"}.get(imp, "")
                lines.append(f"**{imp_label}**")
                lines.append("")

            author_count = len(info["authors"])
            author_names = "、".join(sorted(info["authors"]))
            signal = " ⚠️多人共议" if author_count >= 3 else ""
            lines.append(f"- **{topic}**{signal} — {author_count}位: {author_names}")

        lines.append("")
        lines.append("> 以上为关键词规则匹配，仅作快速扫描。LLM深度分析见下文。")
        lines.append("")

    # ─── 3. LLM 话题深度分析 ───
    if llm_analysis:
        lines.append("---")
        lines.append("## 🧠 话题深度分析（LLM）\n")
        lines.append(llm_analysis)
        lines.append("")

    # ─── 4. Dorian 分析基调 ───
    lines.append("---")
    lines.append("## 🔗 覆盖概览\n")
    all_authors = set()
    if groups:
        for info in groups.values():
            all_authors.update(info["authors"])
    lines.append(f"- **覆盖**: {len(articles)}篇文章, {len(all_authors)}/{len(OLD_ACCOUNTS)}个信源有更新")
    high_topics = [t for t, i in groups.items() if i["importance"] == "high"] if groups else []
    if high_topics:
        lines.append(f"- **高重要性方向({len(high_topics)}个)**: {'、'.join(high_topics[:5])}")
    lines.append("")

    # ─── dedup 标记 ───
    for art in articles:
        lines.append(f"<!--dedup-file: {art['file']}-->")

    return "\n".join(lines)


def main():
    print(f"[demo] 加载文章...")
    articles = load_articles()
    print(f"  加载 {len(articles)} 篇（8个旧公众号，5天窗口，每号最多3篇）")

    # 统计信源覆盖
    authors_present = set(a["author"] for a in articles)
    missing = [a for a in OLD_ACCOUNTS if a not in authors_present]
    print(f"  信源覆盖: {len(authors_present)}/{len(OLD_ACCOUNTS)}")
    if missing:
        print(f"  缺失: {', '.join(missing)}")

    # 规则分类（仅用于快速扫描概览）
    groups = classify_articles(articles)
    print(f"  规则分类主题: {len(groups)} 个")

    # 调用 LLM（话题分类+观点提炼）
    print(f"\n[demo] 调用 LLM 进行话题分类+观点提炼...")
    try:
        from ai_analyzer import call_llm

        # 构建 prompt — 发完整文章，但要 LLM 做精细处理
        prompt_parts = [f"今日日期: 2026-06-14\n"]
        prompt_parts.append("以下是今日8个投资方向公众号的最新文章（全文已清理广告）。请完成：\n")

        for art in articles:
            body = clean_body(art["content"])[:2000]
            prompt_parts.append(f"--- {art['author']}《{art['title']}》---\n{body}\n")

        prompt_parts.append("""
请输出以下结构（Markdown格式）：

## 话题分组与观点对比

对每篇文章判断其所属话题（可以多个，但每篇只归入最重要的1-2个话题），按话题分组输出。

对于每个话题（只列真正有内容关联的话题，不要强行归类）：

### 🔴 [话题名称]
**覆盖**: N位作者
**重要性判断**: 高/中/低 — 理由是？

| 作者 | 核心观点（1-2句话） | 叙事变化 |
|------|---------------------|---------|
| 作者名 | 这篇文章关于此话题的核心结论是什么 | 新观点/延续/弱化/反转 |

**共识判断**: 各作者在这个话题上是否有共识？分歧在哪里？

**是否为变量**: 这个议题是影响了市场的变量吗？产生了什么实际影响？

---

然后：

## 核心矛盾
一句话说清当前市场最核心的矛盾

## 关键标的
文章里提到的具体股票/ETF代码或名称

## Dorian六步（精简版）
①核心变量 ②传导路径 ③情景矩阵（基准/风险各一） ④交易含义 ⑤失效条件

---
要求：交易员语言，短句。每作者观点控制在2句话以内。只提炼观点，不要原文照搬。""")

        system_prompt = "你是一个交易员视角的市场分析助手。读公众号文章，做话题分类+观点提炼+多作者对比。"
        llm_text, provider = call_llm(system_prompt, "\n".join(prompt_parts), max_tokens=4096)
        print(f"  LLM: {provider} ({len(llm_text)} chars)")
    except Exception as e:
        print(f"  LLM调用失败: {e}")
        llm_text = ""

    # 构建报告
    report = build_report(articles, llm_text, groups)

    # 保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(report, encoding="utf-8")
    print(f"\n[demo] ✅ 报告已生成: {OUTPUT_FILE}")
    print(f"  大小: {len(report)} 字")


if __name__ == "__main__":
    main()

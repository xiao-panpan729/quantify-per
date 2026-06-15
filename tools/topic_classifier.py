# -*- coding: utf-8 -*-
"""
规则主题分类器 — 非LLM，关键词匹配。
把文章按主题分组，输出结构化结果供 gen_daily_brief.py 消费。

Usage:
  from tools.topic_classifier import classify_articles, TOPIC_DEFS
  groups = classify_articles(articles_list)
  # groups = { "topic_name": {"importance": "high", "articles": [...], "authors": set(), "stocks": [...]} }
"""
import re

# ─── 主题定义 ────────────────────────────────────────────────────
# 每个主题：keyword列表（标题/内容匹配）+ importance + 别名映射
TOPIC_DEFS = [
    {
        "name": "地缘政治/美伊冲突",
        "importance": "high",
        "keywords": [
            "美伊", "中东", "伊朗", "以色列", "地缘", "霍尔木兹",
            "军事冲突", "导弹", "军舰", "特朗普.*伊朗", "伊朗.*谈判",
            "真主党", "哈马斯", "胡塞", "红海", "原油.*地缘",
            "油价.*地缘", "石油.*制裁",
        ],
    },
    {
        "name": "AI算力/半导体设备",
        "importance": "high",
        "keywords": [
            "AI算力", "算力", "半导体设备", "芯片", "半导体周期",
            "英伟达", "NVDA", "HBM", "GPU", "昇腾", "华为.*芯片",
            "光模块", "CPO", "HPE", "甲骨文.*AI", "AI.*基建",
            "数据中心.*AI", "大模型.*算力", "半导体.*涨价",
            "设备.*涨价", "晶圆", "先进制程", "CoWoS",
        ],
    },
    {
        "name": "上游涨价链/材料紧缺",
        "importance": "high",
        "keywords": [
            "涨价", "MLCC", "PCB", "钼", "钨", "稀土", "磷化铟",
            "大硅片", "化学法球硅", "紧缺", "供需.*缺口", "供不应求",
            "涨价潮", "库存.*去化", "产能.*紧张", "氧化镝",
            "有色金属.*涨价", "化工.*涨价", "材料.*涨价",
            "前驱体", "村田", "存储.*涨价",
        ],
    },
    {
        "name": "商业航天/SpaceX",
        "importance": "medium",
        "keywords": [
            "商业航天", "SpaceX", "SPCX", "星链", "卫星互联网",
            "火箭", "低轨卫星", "千帆星座", "马斯克", "太空",
            "发射.*成功", "组网.*卫星", "航天.*板块",
        ],
    },
    {
        "name": "宏观/流动性/利率",
        "importance": "high",
        "keywords": [
            "美联储", "加息", "降息", "通胀", "CPI", "PPI",
            "流动性", "M2", "VIX", "美债", "利率", "货币政策",
            "央行", "资金面", "回购利率", "OMO", "逆回购",
            "信用脉冲", "美元指数", "DXY", "缩表",
        ],
    },
    {
        "name": "关税/贸易政策/制裁",
        "importance": "high",
        "keywords": [
            "关税", "制裁", "贸易", "出口管制", "CMC清单",
            "1260H", "实体清单", "脱钩", "供应链.*安全",
            "反倾销", "双反", "关税.*欧盟", "关税.*美国",
            "贸易战", "科技战",
        ],
    },
    {
        "name": "市场情绪/资金面",
        "importance": "medium",
        "keywords": [
            "慢熊", "退潮", "情绪", "资金面", "增量资金",
            "基金发行", "中位数跌幅", "亏钱效应", "市场底",
            "成交量.*萎缩", "缩量", "恐慌", "抄底",
            "反弹.*出局", "高低切换",
        ],
    },
    {
        "name": "AI应用/大模型/国产算力",
        "importance": "medium",
        "keywords": [
            "大模型", "AI应用", "豆包", "智谱", "腾讯混元",
            "Agent", "GPT", "文心一言", "通义千问", "Kimi",
            "AI.*应用", "AI.*软件", "AI.*电商", "多模态",
            "华为.*AI", "国产算力", "昇腾.*AI",
            "AI.*编程", "AI.*办公",
        ],
    },
    {
        "name": "产业趋势/国产替代/出海",
        "importance": "medium",
        "keywords": [
            "国产替代", "国产设备", "出海", "自主可控",
            "产业链.*国产", "进口替代", "国产化率",
            "智能制造", "工业母机", "机器人.*产业",
            "新能源.*出海", "储能.*出海",
        ],
    },
    {
        "name": "港美股映射/中概",
        "importance": "medium",
        "keywords": [
            "中概", "港股", "恒生", "纳斯达克", "美股",
            "科技股.*暴跌", "中概互联", "恒生科技",
            "ADR", "中国概念股", "港股通",
            "FANG", "科技巨头.*美股",
        ],
    },
    {
        "name": "新能源/锂电/光伏",
        "importance": "medium",
        "keywords": [
            "锂电", "光伏", "新能源", "储能", "碳酸锂",
            "电动车", "新能源车", "锂电池", "光伏.*组件",
            "风电", "氢能", "固态电池", "充电桩",
            "宁德", "比亚迪",
        ],
    },
    {
        "name": "基本面/业绩/财报",
        "importance": "medium",
        "keywords": [
            "财报", "业绩", "盈利", "ROE", "营收", "净利润",
            "季报", "年报", "预增", "业绩预告",
            "分红", "回购", "增持", "减持",
            "估值", "市盈率", "市净率", "PEG",
        ],
    },
    {
        "name": "消费/医药/其他板块",
        "importance": "low",
        "keywords": [
            "消费", "医药", "创新药", "CXO", "药明",
            "白酒", "食品", "旅游", "航空",
            "房地产", "基建", "银行", "券商",
            "煤炭", "钢铁", "电力",
        ],
    },
    {
        "name": "日本宏观/套息交易",
        "importance": "medium",
        "keywords": [
            "日本", "BOJ", "日元", "套息", "carry trade",
            "日本.*加息", "日本.*利率", "日本.*CPI",
            "USDJPY", "日经",
        ],
    },
    {
        "name": "IPO/再融资/抽血",
        "importance": "medium",
        "keywords": [
            "IPO", "再融资", "增发", "募资", "上市",
            "抽血", "股权融资", "配股", "解禁",
            "过会", "提交.*上市", "科创板.*上市",
        ],
    },
    {
        "name": "超级IPO/流动性抽血",
        "importance": "high",
        "keywords": [
            "超级IPO", "SpaceX.*上市", "谷歌.*融资",
            "800亿.*融资", "750亿.*IPO", "天量.*融资",
            "巨无霸.*上市", "IPO.*抽血",
        ],
    },
]

# 编译正则
_COMPILED = []
for td in TOPIC_DEFS:
    patterns = []
    for kw in td["keywords"]:
        try:
            patterns.append(re.compile(kw, re.IGNORECASE))
        except re.error:
            patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
    _COMPILED.append((td, patterns))


def classify_article(title: str, content: str) -> list:
    """
    对一篇文章分类，返回匹配的主题名列表。
    按顺序匹配，返回所有命中的主题（一篇文章可能属于多个主题）。
    """
    text = f"{title}\n{content[:3000]}"  # 标题+内容前3000字
    hits = []
    for td, patterns in _COMPILED:
        for p in patterns:
            if p.search(text):
                hits.append(td["name"])
                break
    return hits


def classify_articles(articles: list) -> dict:
    """
    对文章列表进行全部分类。

    articles: list of dict, 每个含 author, title, content (全文)

    returns: dict of { topic_name: {
        "importance": "high/medium/low",
        "articles": [ {author, title, content_snippet, full_content} ],
        "authors": set() of author names,
        "stock_mentions": [],  (占位，后续LLM填充)
    }}
    """
    groups = {}
    for art in articles:
        title = art.get("title", "")
        content = art.get("content", "")
        topics = classify_article(title, content)
        if not topics:
            topics = ["其他"]
        for topic in topics:
            if topic not in groups:
                # 找 importance
                imp = "low"
                for td in TOPIC_DEFS:
                    if td["name"] == topic:
                        imp = td["importance"]
                        break
                groups[topic] = {
                    "importance": imp,
                    "articles": [],
                    "authors": set(),
                }
            groups[topic]["articles"].append({
                "author": art["author"],
                "title": title,
                # 存全文前1000字 + 结尾500字作为摘要，全文还在
                "content_snippet": content[:1000] + ("\n...\n" + content[-500:] if len(content) > 1500 else ""),
                "content_full": content,  # 全文保留
            })
            groups[topic]["authors"].add(art["author"])

    return groups


def format_groups_summary(groups: dict) -> str:
    """输出可读的分组摘要（用于调试/展示）"""
    lines = []
    # 按重要性+作者数排序
    rank = {"high": 0, "medium": 1, "low": 2}
    sorted_topics = sorted(
        groups.items(),
        key=lambda x: (rank.get(x[1]["importance"], 3), -len(x[1]["authors"])),
    )
    for topic, info in sorted_topics:
        imp = info["importance"]
        imp_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(imp, "⚪")
        authors = ", ".join(sorted(info["authors"]))
        art_count = len(info["articles"])
        lines.append(f"{imp_icon} [{imp}] {topic} — {art_count}篇, 作者: {authors}")
        for a in info["articles"][:3]:
            # 只显示前30字
            snip = a["content_snippet"][:60].replace("\n", " ").strip()
            lines.append(f"    ├ {a['author']}: {a['title'][:40]}")
            lines.append(f"    │  {snip[:80]}...")
        if len(info["articles"]) > 3:
            lines.append(f"    └ ...还有{len(info['articles'])-3}篇")
        lines.append("")
    return "\n".join(lines)


# ─── 直接运行查看分类效果 ───
if __name__ == "__main__":
    import json, sys, os
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    WECHAT_DIR = r"d:\quantify-per\wechat_articles"
    # 测试用：读几篇最新文章
    test_accounts = ["猫笔刀", "表舅是养基大户", "卓哥投研笔记", "亨特研究笔记"]
    test_articles = []
    for acc in test_accounts:
        import glob, os
        dir_path = os.path.join(WECHAT_DIR, acc)
        if not os.path.isdir(dir_path):
            continue
        files = sorted(os.listdir(dir_path), reverse=True)[:3]
        for f in files:
            fp = os.path.join(dir_path, f)
            content = open(fp, "r", encoding="utf-8", errors="replace").read()
            title = ""
            for line in content.split("\n")[:3]:
                if line.startswith("标题:"):
                    title = line.replace("标题:", "").strip()
                    break
            test_articles.append({
                "author": acc,
                "title": title or f,
                "content": content,
            })

    groups = classify_articles(test_articles)
    print(format_groups_summary(groups))

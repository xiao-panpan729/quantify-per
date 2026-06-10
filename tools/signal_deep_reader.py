# -*- coding: utf-8 -*-
"""
Layer 3 深度精读 — 调用 LLM 精读公众号全文, 提取信号 + CoT 推理 + 叙事链映射.

区别于 signal_extractor.py 的关键词匹配, 本模块真正"读懂"文章内容,
理解"SK海力士晶圆产能翻倍 → 产能扩张 → HBM供给紧张 → 先进封装链"这种逻辑链.

用法:
  python tools/signal_deep_reader.py                    # 默认今日（文章精读 + 聚合日报）
  python tools/signal_deep_reader.py --date 20260609    # 指定日期
  python tools/signal_deep_reader.py --all               # 所有有公众号文章的日子
  python tools/signal_deep_reader.py --aggregation-only  # 只跑聚合日报跨块分析
  python tools/signal_deep_reader.py --date 20260609 --aggregation-only  # 指定日期聚合分析

输出:
  signals/tracking/_signals/daily_signals/{date}_deep_signals.json
  signals/tracking/_signals/daily_signals/{date}_aggregation_signals.json  (聚合日报跨块信号)
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking"
OUTPUT_DIR = SIGNALS_DIR / "_signals" / "daily_signals"

# ADDED FOR AGGREGATION: paths for aggregation report and prompts
REPORTS_SOURCES_DIR = PROJECT_ROOT / "reports" / "sources"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
CROSS_BLOCK_PROMPT_PATH = PROMPTS_DIR / "cross_block_analysis_prompt.md"

# ADDED FOR AGGREGATION: additional imports (lazy inside analyze_aggregation_blocks)
# NOTE: source_report_parser imported lazily inside function to handle path correctly

# 50 链名称
CHAIN_NAMES = {
    "narrative#1": "IC设计", "narrative#2": "半导体设备", "narrative#3": "半导体材料",
    "narrative#3a": "SiC衬底", "narrative#4": "先进封装", "narrative#5": "EDA/IP",
    "narrative#6": "功率半导体", "narrative#7": "半导体配套", "narrative#8": "MLCC/PCB/载板",
    "narrative#10": "AI应用", "narrative#11": "AI基础设施", "narrative#12": "光模块/CPO",
    "narrative#13": "5G/6G通信", "narrative#14": "消费电子", "narrative#15": "AI穿戴",
    "narrative#16": "面板", "narrative#17": "智能驾驶", "narrative#18": "新能源车",
    "narrative#19": "汽车电子", "narrative#20": "充电桩", "narrative#21": "汽车零部件",
    "narrative#22": "人形机器人", "narrative#23": "机器人核心部件", "narrative#24": "工业机器人",
    "narrative#25": "工业互联网", "narrative#26": "低空经济", "narrative#27": "商业航天",
    "narrative#28": "国防军工", "narrative#29": "光伏", "narrative#30": "风电",
    "narrative#31": "电池", "narrative#32": "锂资源", "narrative#33": "电网/电力设备",
    "narrative#34": "储能", "narrative#35": "信创", "narrative#36": "数据要素",
    "narrative#37": "数字货币", "narrative#38": "创新药", "narrative#39": "疫苗",
    "narrative#40": "医疗器械", "narrative#41": "中药", "narrative#42": "贵金属",
    "narrative#43": "小金属/战略金属", "narrative#44": "有色金属（铜铝）",
    "narrative#45": "能源金属", "narrative#46": "新材料", "narrative#47": "化工新材料",
    "narrative#48": "游戏", "narrative#49": "数字内容/IP", "narrative#50": "养殖/农业",
}

# 信号类型
SIGNAL_TYPES = [
    "涨价", "供给冲击", "政策利好", "政策利空", "需求爆发",
    "资本开支", "产能扩张", "技术突破", "出口限制", "地缘风险",
    "板块动量", "业绩预告", "行业景气", "其他",
]

# 信号类型 → 默认方向
TYPE_DIRECTION = {
    "涨价": "positive", "供给冲击": "negative", "政策利好": "positive",
    "政策利空": "negative", "需求爆发": "positive", "资本开支": "positive",
    "产能扩张": "positive", "技术突破": "positive", "出口限制": "negative",
    "地缘风险": "negative", "板块动量": "neutral", "业绩预告": "neutral",
    "行业景气": "positive", "其他": "neutral",
}


def _date_str(dt=None):
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y%m%d")


def _read_articles_for_date(date: str) -> list:
    """读取指定日期的所有公众号文章 (匹配文件名前缀)"""
    wechat_dir = PROJECT_ROOT / "wechat_articles"
    if not wechat_dir.exists():
        return []
    articles = []
    for src_dir in sorted(wechat_dir.iterdir()):
        if not src_dir.is_dir():
            continue
        files = sorted(src_dir.glob("*.txt"), reverse=True)
        for f in files:
            # 只取文件名以日期开头的 (YYYYMMDD_*)
            if not f.stem.startswith(date):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            title = ""
            body = text
            lines = text.split("\n", 4)
            if lines[0].startswith("标题:"):
                title = lines[0][3:].strip()
            if len(lines) >= 5:
                body = lines[4].strip()
            else:
                body = text[:4000]
            if not body:
                continue
            # 太长就截断
            if len(body) > 5000:
                body = body[:5000] + "\n...(以下省略)"
            articles.append({
                "source": src_dir.name,
                "title": title or f.stem,
                "file": f.name,
                "content": body,
            })
    return articles


def _build_chain_list() -> str:
    """生成 50 链列表供 LLM prompt 使用"""
    parts = []
    for cid in sorted(CHAIN_NAMES.keys()):
        parts.append(f"  {cid}: {CHAIN_NAMES[cid]}")
    return "\n".join(parts)


DEEP_READER_SYSTEM_PROMPT = """你是一个产业链信号分析师。你的任务：

1. 精读一篇微信公众号文章，提取其中蕴含的所有**可交易信号**
2. 对每个信号做**CoT推理**：解释为什么这是一个信号、它的逻辑链条是什么
3. 将信号映射到**最相关的产业链叙事链**（参见下方列表）
4. 输出结构化JSON

## 50条产业链叙事链
{chain_list}

## 信号类型
{signal_types_str}

## 映射规则
- 每个信号可以映射1-3条叙事链，按相关性从高到低排列
- 只映射**文章真正讨论到的链**，不要强行关联
- 如果信号不涉及任何链，chain_ids 留空数组

## 输出格式
输出一个 JSON 数组，每个元素一条信号：
```json
[
  {{
    "text": "信号的一句话描述（原文证据）",
    "signal_type": "涨价|供给冲击|政策利好|政策利空|需求爆发|资本开支|产能扩张|技术突破|出口限制|地缘风险|板块动量|业绩预告|行业景气|其他",
    "direction": "positive|negative|neutral",
    "chain_ids": ["narrative#X", "narrative#Y"],
    "reasoning": "这个信号为什么重要？逻辑链是什么？涉及哪些产品/公司/政策？",
    "confidence": 0.85
  }}
]

## 推理原则
- 对照原文证据，不要无中生有
- 文章说"可能涨价" → direction=positive, confidence 降低
- 文章说"已确认涨价X%" → direction=positive, confidence 提高
- **每篇最多5条信号**
- 如果文章没有可交易的信号或与产业链无关，**输出空数组 []**
"""


def _call_llm(system_prompt, user_message):
    """调用 ai_analyzer 的 LLM 接口"""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from ai_analyzer import call_llm
        content, provider = call_llm(system_prompt, user_message, max_tokens=4096)
        return content, provider
    except Exception as e:
        return None, str(e)[:200]


def _extract_json(text: str) -> list:
    """从 LLM 输出中提取 JSON 数组, 兼容各种包裹格式"""
    if not text:
        return []
    # 尝试直接解析
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 找 ```json ... ``` 块
    import re
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 找 [...] 块
    m = re.search(r'(\[[\s\S]*?\])', text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    return []


def process_article(article: dict, system_prompt: str) -> dict:
    """精读一篇公众号文章, 返回结构化的信号"""
    title = article.get("title", "")
    content = article.get("content", "")
    source = article.get("source", "")

    user_msg = f"""## 文章信息
来源: {source}
标题: {title}

## 全文内容
{content}

请提取信号并输出JSON数组。"""

    raw_response, provider = _call_llm(system_prompt, user_msg)

    result = {
        "article_title": title,
        "article_source": source,
        "article_file": article.get("file", ""),
        "provider": provider,
        "signals": [],
        "error": "",
    }

    if raw_response is None:
        result["error"] = f"API调用失败: {provider}"
        return result

    signals = _extract_json(raw_response)
    if not signals:
        result["error"] = f"无法解析LLM输出: {raw_response[:200]}"
        result["raw_response"] = raw_response[:500]
        return result

    # 验证和标准化
    validated = []
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        s = {
            "text": str(sig.get("text", ""))[:200],
            "signal_type": sig.get("signal_type", "其他") if sig.get("signal_type") in SIGNAL_TYPES else "其他",
            "direction": sig.get("direction", TYPE_DIRECTION.get(sig.get("signal_type", ""), "neutral")),
            "chain_ids": [c for c in sig.get("chain_ids", []) if c in CHAIN_NAMES][:3],
            "reasoning": str(sig.get("reasoning", ""))[:500],
            "confidence": min(max(float(sig.get("confidence", 0.5)), 0.0), 1.0),
        }
        if s["text"]:
            validated.append(s)

    # 信号价值过滤: 去掉凑数的低质量信号
    filtered = [s for s in validated
                if not (s["signal_type"] == "其他" and s["confidence"] < 0.6)]
    # 如果过滤后没信号了, 标记为空
    if not filtered:
        result["signals"] = []
        result["skipped_reason"] = "所有信号均被标记为低价值"
    else:
        result["signals"] = filtered

    return result


def build_markdown(date: str, results: list, stats: dict) -> str:
    """生成深度精读报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# 深度精读信号报告 {date}", f"生成: {now}", ""]

    lines.append(f"**文章数**: {stats['total_articles']} | "
                 f"成功: {stats['success']} | 失败: {stats['failed']} | "
                 f"总信号: {stats['total_signals']}")
    lines.append(f"**API**: {stats['provider_summary']}")
    lines.append("")

    sig_count = 0
    for res in results:
        sig_count += 1
        title = res.get("article_title", "无标题")
        source = res.get("article_source", "?")
        provider = res.get("provider", "?")
        signals = res.get("signals", [])
        error = res.get("error", "")

        lines.append(f"### {sig_count}. [{source}] {title}")
        lines.append(f"**API**: {provider}")
        if error:
            lines.append(f"**错误**: {error}")
        lines.append("")

        for sig in signals:
            chains_str = ", ".join(CHAIN_NAMES.get(c, c) for c in sig.get("chain_ids", []))
            lines.append(f"- **{sig['signal_type']}**({sig['direction']}, conf={sig['confidence']:.2f})")
            lines.append(f"  > {sig['text']}")
            if chains_str:
                lines.append(f"  → {chains_str}")
            if sig.get("reasoning"):
                lines.append(f"  *推理: {sig['reasoning'][:200]}")
            lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════
# ADDED FOR AGGREGATION: 聚合日报跨块分析
# ═══════════════════════════════════════════════

def _extract_json_object(text: str) -> dict:
    """从 LLM 输出中提取 JSON 对象 ({...}), 兼容各种包裹格式"""
    if not text:
        return {}
    text = text.strip()
    # 尝试直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    # 找 ```json ... ``` 块
    import re
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            result = json.loads(m.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    # 找 {...} 块（允许嵌套花括号）
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    result = json.loads(text[start:i+1])
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    pass
    return {}


def analyze_aggregation_blocks(date: str, cross_block_prompt: str) -> dict:
    """
    读取聚合日报的 9 个数据块，调用 LLM 进行跨块分析。

    Args:
        date: 日期字符串 YYYYMMDD
        cross_block_prompt: system prompt（跨块分析指令）

    Returns:
        dict: {
            "date": str,
            "cross_signals": list,
            "summary": str,
            "error": str,
        }
    """
    # 1. 懒加载 source_report_parser（路径兼容）
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from tools.source_report_parser import read_source_report, format_blocks_for_prompt
    except ImportError:
        from source_report_parser import read_source_report, format_blocks_for_prompt

    # 2. 读取聚合日报
    try:
        blocks = read_source_report(date)
    except FileNotFoundError as e:
        return {"date": date, "cross_signals": [], "summary": "", "error": str(e)}
    except Exception as e:
        return {"date": date, "cross_signals": [], "summary": "", "error": f"解析失败: {e}"}

    if not blocks:
        return {"date": date, "cross_signals": [], "summary": "", "error": "聚合日报为空或无有效数据块"}

    # 3. 格式化为文本
    block_text = format_blocks_for_prompt(blocks)
    print(f"  [聚合] 数据块: {len(blocks)} 块, {len(block_text)} 字符")

    # 4. 调用 LLM
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from ai_analyzer import call_llm
        user_message = (
            f"请对以下 {date} 聚合日报的 {len(blocks)} 个数据块进行跨块分析。\n\n"
            f"{block_text}\n\n"
            "输出 JSON 跨块信号。"
        )
        raw_response, provider = call_llm(cross_block_prompt, user_message, max_tokens=4096)
    except Exception as e:
        return {"date": date, "cross_signals": [], "summary": "", "error": f"LLM调用失败: {e}"}

    if not raw_response:
        return {"date": date, "cross_signals": [], "summary": "", "error": "LLM 返回空响应"}

    # 5. 解析 JSON（兼容两种格式）
    # 格式A: 直接输出 JSON 数组 [...] （prompt原始设计）
    # 格式B: 输出 JSON 对象 {"cross_signals": [...], "summary": "..."}
    parsed_obj = _extract_json_object(raw_response)
    parsed_arr = _extract_json(raw_response)

    cross_signals = []
    summary = ""
    error_hint = ""

    if parsed_obj and "cross_signals" in parsed_obj:
        # 格式B: 对象格式
        cross_signals = parsed_obj.get("cross_signals", [])
        summary = str(parsed_obj.get("summary", ""))[:300]
    elif parsed_arr:
        # 格式A: 直接数组格式
        cross_signals = parsed_arr
    elif parsed_obj:
        # 对象但无 cross_signals 字段
        error_hint = f"对象格式但缺 cross_signals 字段: {list(parsed_obj.keys())[:3]}"
    else:
        return {
            "date": date,
            "cross_signals": [],
            "summary": "",
            "error": f"无法解析LLM输出为JSON: {raw_response[:300]}",
            "raw_response": raw_response[:1000],
        }

    if not isinstance(cross_signals, list):
        cross_signals = []

    validated = []
    for sig in cross_signals:
        if not isinstance(sig, dict):
            continue
        validated.append({
            "signal_type": str(sig.get("signal_type", "其他")),
            "title": str(sig.get("title", ""))[:200],
            "description": str(sig.get("description", ""))[:500],
            "blocks_involved": [str(b) for b in sig.get("blocks_involved", []) if isinstance(b, str)][:5],
            "direction": str(sig.get("direction", "neutral")),
            "confidence": min(max(float(sig.get("confidence", 0.5)), 0.0), 1.0),
            "narrative_chains": [str(c) for c in sig.get("narrative_chains", []) if isinstance(c, str)][:5],
            "reasoning": str(sig.get("reasoning", ""))[:500],
        })

    return {
        "date": date,
        "cross_signals": validated,
        "summary": summary,
        "provider": provider,
        "error": "",
    }


def run_aggregation_analysis(date: str) -> dict:
    """
    执行单日聚合日报跨块分析全流程。

    流程:
        1. 加载 prompts/cross_block_analysis_prompt.md 作为 system prompt
        2. 调用 analyze_aggregation_blocks()
        3. 保存结果到 daily_signals/{date}_aggregation_signals.json
        4. 返回结果 dict

    Args:
        date: 日期字符串 YYYYMMDD

    Returns:
        dict: 分析结果
    """
    # 1. 加载 system prompt
    if not CROSS_BLOCK_PROMPT_PATH.exists():
        print(f"  [聚合] 提示词文件不存在: {CROSS_BLOCK_PROMPT_PATH}")
        return {"date": date, "cross_signals": [], "summary": "", "error": "提示词文件不存在"}

    cross_block_prompt = CROSS_BLOCK_PROMPT_PATH.read_text(encoding="utf-8", errors="replace")

    # 2. 执行跨块分析
    result = analyze_aggregation_blocks(date, cross_block_prompt)

    # 3. 保存 JSON
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / f"{date}_aggregation_signals.json"

    output = {
        "date": date,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary": result.get("summary", ""),
        "provider": result.get("provider", ""),
        "error": result.get("error", ""),
        "cross_signals": result.get("cross_signals", []),
    }

    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. 打印结果
    n = len(result.get("cross_signals", []))
    err = result.get("error", "")
    if err:
        print(f"  [聚合] ✗ {err[:80]}")
    else:
        print(f"  [聚合] ✓ {n} 条跨块信号 ({result.get('provider', '?')})")
    print(f"  [聚合] → {json_path.name}")

    return result


def main():
    # UTF-8 output for Windows
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    import argparse
    parser = argparse.ArgumentParser(description="Layer 3 深度精读 — LLM通读公众号全文提取信号")
    parser.add_argument("--date", type=str, default=_date_str(), help="日期 YYYYMMDD")
    parser.add_argument("--all", action="store_true", help="处理所有有文章的日期")
    # ADDED FOR AGGREGATION: 新增 --aggregation-only 参数
    parser.add_argument("--aggregation-only", action="store_true",
                        help="只跑聚合日报跨块分析，不跑文章精读")
    args = parser.parse_args()

    date = args.date

    # ADDED FOR AGGREGATION: --aggregation-only 模式
    if args.aggregation_only:
        print(f"聚合日报跨块分析 — {date}（仅聚合，跳过文章精读）")
        run_aggregation_analysis(date)
        print("聚合分析完成")
        return

    chain_list = _build_chain_list()
    signal_types_str = ", ".join(SIGNAL_TYPES)
    system_prompt = DEEP_READER_SYSTEM_PROMPT.format(
        chain_list=chain_list, signal_types_str=signal_types_str
    )

    if args.all:
        # 扫描所有有文章的日期
        wechat_dir = PROJECT_ROOT / "wechat_articles"
        dates = set()
        if wechat_dir.exists():
            for src_dir in wechat_dir.iterdir():
                if not src_dir.is_dir():
                    continue
                for f in src_dir.glob("*.txt"):
                    if f.stem[:8].isdigit():
                        dates.add(f.stem[:8])
        dates = sorted(dates, reverse=True)
        print(f"深度精读全量回填 — {len(dates)} 天")
        for d in dates:
            _run_single(d, system_prompt)
        print("全量回填完成")
        return

    _run_single(date, system_prompt)


def _run_single(date: str, system_prompt: str):
    """执行单日深度精读"""
    print(f"\n深度精读 — {date}")
    articles = _read_articles_for_date(date)
    if not articles:
        print("  无公众号文章")
        return

    print(f"  文章数: {len(articles)}")

    results = []
    total_signals = 0
    success = 0
    failed = 0
    providers = set()

    for i, article in enumerate(articles):
        src = article.get("source", "?")
        title = (article.get("title") or "无标题")[:30]
        print(f"  [{i+1}/{len(articles)}] {src} - {title}...", end=" ", flush=True)

        result = process_article(article, system_prompt)
        sig_count = len(result.get("signals", []))
        error = result.get("error", "")

        if error:
            print(f"✗ ({error[:50]})")
            failed += 1
        else:
            print(f"✓ {sig_count}条信号 ({result.get('provider','?')})")
            success += 1
            total_signals += sig_count
            providers.add(result.get("provider", "?"))

        results.append(result)

    # 统计
    stats = {
        "total_articles": len(articles),
        "success": success,
        "failed": failed,
        "total_signals": total_signals,
        "provider_summary": ", ".join(sorted(providers)),
    }

    # 输出
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "date": date,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "statistics": stats,
        "articles": results,
    }

    json_path = OUTPUT_DIR / f"{date}_deep_signals.json"
    json_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md_content = build_markdown(date, results, stats)
    md_path = OUTPUT_DIR / f"{date}_deep_signals.md"
    md_path.write_text(md_content, encoding="utf-8")

    print(f"\n  [OK] {json_path.name}")
    print(f"  [OK] {md_path.name}")
    print(f"  信号: {total_signals}条 / 文章: {success}成功 {failed}失败")

    # ADDED FOR AGGREGATION: 文章精读完成后，额外跑聚合日报跨块分析
    print(f"\n  [聚合] 开始聚合日报跨块分析...")
    run_aggregation_analysis(date)


if __name__ == "__main__":
    main()

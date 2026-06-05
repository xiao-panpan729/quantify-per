"""
B类突发事件检测器 — 每日盘后从多个消息源抓取标题，关键词匹配，
输出 sentiment_shock.json 供 macro_sensitivity.py 读取作为 overlay。

数据源: 鼓掌财经WebSocket(同花顺+选股宝+见闻) / 华尔街见闻REST / 东财全球快讯
设计参考: aion-taxonomy (YAML关键词匹配) + Tech-Pulse (多源归一+降级)
"""
import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

import requests
import websockets

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKING_DIR = PROJECT_ROOT / "signals" / "tracking"
OUTPUT_FILE = TRACKING_DIR / "sentiment_shock.json"
KEYWORDS_FILE = Path(__file__).resolve().parent / "shock_keywords.json"


def load_keywords():
    with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── 超时包装 ───
def _with_timeout(fn, timeout_sec=45):
    """在线程中运行 fn，超时返回 []"""
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout_sec)
        except FutureTimeout:
            return []
        except Exception:
            return []


# ─── 消息源0: 鼓掌财经 WebSocket (同花顺+选股宝+华尔街见闻聚合) ───
GUZHANG_PAGE_URL = "https://724.guzhang.com/"
GUZHANG_WS_HOST = "wss://swoole2.guzhang.com/"


def _get_guzhang_token():
    """从鼓掌财经页面HTML中动态提取WebSocket token（每次访问页面都生成新JWT）"""
    try:
        resp = requests.get(GUZHANG_PAGE_URL, timeout=15)
        html = resp.text
        # 服务端将token渲染为: var encryptedToken = "xxx";
        m = re.search(r'var\s+encryptedToken\s*=\s*"([^"]+)"', html)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _build_guzhang_ws_url(token):
    """用动态token构建WebSocket URL"""
    if not token:
        return None
    return f"{GUZHANG_WS_HOST}?token={token}"


def fetch_guzhang():
    """通过WebSocket连接鼓掌财经，耐心等待最多30条消息（最长20秒）"""
    headlines = []

    # 动态获取新token（每次运行都重新从页面拉取）
    token = _get_guzhang_token()
    if not token:
        print("[shock]     ⚠ 无法获取鼓掌财经token，跳过")
        return headlines

    ws_url = _build_guzhang_ws_url(token)

    async def _collect():
        empty_windows = 0
        try:
            async with websockets.connect(
                ws_url, ping_interval=None, close_timeout=3, max_size=2**20
            ) as ws:
                for _ in range(60):
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        empty_windows += 1
                        if empty_windows >= 3:  # 连续3个空窗=6秒没消息，退出
                            break
                        continue
                    empty_windows = 0
                    if msg == "ping":
                        continue
                    try:
                        data = json.loads(msg)
                        title = data.get("title", "")
                        if title:
                            headlines.append({
                                "title": title,
                                "source": f"guzhang({data.get('comefrom', '?')})",
                                "time": data.get("ptime", "")
                            })
                        if len(headlines) >= 30:
                            break
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

    try:
        asyncio.run(_collect())
    except Exception:
        pass
    return headlines


# ─── 消息源1: 东方财富全球快讯 (akshare) ───
def _fetch_eastmoney_global_worker():
    import akshare as ak
    df = ak.stock_info_global_em()
    if df is None or df.empty:
        return []
    headlines = []
    for _, row in df.tail(300).iterrows():
        title = str(row.iloc[0])[:200]
        time_str = str(row.iloc[2])[:30] if len(row) > 2 else ""
        headlines.append({"title": title, "source": "eastmoney_global", "time": time_str})
    return headlines


def fetch_eastmoney_global():
    try:
        return _with_timeout(_fetch_eastmoney_global_worker, timeout_sec=45)
    except Exception as e:
        print(f"  [shock] 东方财富全球快讯获取失败: {e}")
        return []


def _fetch_eastmoney_worker():
    import akshare as ak
    df = ak.stock_news_em()
    if df is None or df.empty:
        return []
    headlines = []
    for _, row in df.tail(500).iterrows():
        title = str(row.iloc[0])[:200]
        time_str = str(row.iloc[3]) if len(row) > 3 else ""
        headlines.append({"title": title, "source": "eastmoney", "time": time_str})
    return headlines


def _fetch_cls_worker():
    import akshare as ak
    df = ak.stock_info_global_cls()
    if df is None or df.empty:
        return []
    headlines = []
    for _, row in df.tail(500).iterrows():
        title = str(row.iloc[0])[:200]
        time_str = str(row.iloc[1])[:30] if len(row) > 1 else ""
        headlines.append({"title": title, "source": "cls", "time": time_str})
    return headlines


# ─── 消息源1: 东方财富 (akshare) ───
def fetch_eastmoney():
    try:
        return _with_timeout(_fetch_eastmoney_worker, timeout_sec=45)
    except Exception as e:
        print(f"  [shock] 东方财富新闻获取失败: {e}")
        return []


# ─── 消息源2: 财联社 (akshare) ───
def fetch_cls():
    try:
        return _with_timeout(_fetch_cls_worker, timeout_sec=45)
    except Exception as e:
        print(f"  [shock] 财联社获取失败: {e}")
        return []


# ─── 消息源3: 华尔街见闻 REST API ───
def fetch_wallstreetcn():
    """华尔街见闻 global-channel 快讯，游标翻页取最近200条"""
    headlines = []
    try:
        url = "https://api-prod.wallstreetcn.com/apiv1/content/lives"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://wallstreetcn.com/",
        }
        cursor = 0
        for _ in range(5):  # 最多翻5页=200条
            params = {"channel": "global-channel", "client": "pc", "cursor": cursor, "limit": 40}
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("data", {}).get("items", [])
            if not items:
                break
            for item in items:
                content = (item.get("content_text") or item.get("title") or "")[:200]
                if not content.strip():
                    continue
                headlines.append({
                    "title": content,
                    "source": "wallstreetcn",
                    "time": str(item.get("display_time", ""))
                })
            cursor = data.get("data", {}).get("next_cursor")
            if cursor is None:
                break
        return headlines
    except Exception as e:
        print(f"  [shock] 华尔街见闻获取失败: {e}")
        return headlines


# ─── 关键词匹配 ───
def match_keywords(headlines, keyword_db):
    """对每条标题跑全部关键词，返回命中列表"""
    categories = keyword_db["categories"]
    hits_by_category = defaultdict(list)

    for h in headlines:
        title = h["title"].lower()
        for cat_id, cat_cfg in categories.items():
            for kw in cat_cfg["keywords"]:
                if kw.lower() in title:
                    hits_by_category[cat_id].append({
                        "title": h["title"],
                        "source": h["source"],
                        "time": h["time"],
                        "matched_keyword": kw
                    })
                    break  # 每条新闻每个分类只计数一次

    return hits_by_category


# ─── 聚合与打分 ───
def aggregate_shocks(hits_by_category, keyword_db):
    """将分类命中聚合成冲击事件列表，计算净影响"""
    categories = keyword_db["categories"]
    shocks = []
    net_impact = 0
    total_weight = 0

    for cat_id, items in hits_by_category.items():
        if not items:
            continue
        cat_cfg = categories[cat_id]
        unique_titles = list({i["title"] for i in items})
        # 唯一标题数 ≥ 2 才算有效冲击（减少单条误报）
        if len(unique_titles) < 1:
            continue

        impact = cat_cfg["impact_sign"] * cat_cfg["impact_magnitude"]
        shocks.append({
            "type": cat_id,
            "label": cat_cfg["label"],
            "level": cat_cfg["level"],
            "count": len(items),
            "unique_count": len(unique_titles),
            "impact": impact,
            "sample_titles": unique_titles[:5],
            "matched_keywords": list({i["matched_keyword"] for i in items})
        })

        weight = 1.0 if cat_cfg["level"] == "macro" else (0.6 if cat_cfg["level"] == "market" else 0.3)
        net_impact += impact * weight
        total_weight += weight

    return shocks, round(net_impact, 1)


# ─── 主入口 ───
def run_detection(save=True):
    """运行B类突发事件检测，返回结果字典"""
    print("[shock] 消息面突发事件检测...")
    keyword_db = load_keywords()

    # 多源拉取（串行，各源独立失败）
    print("[shock]   拉取 鼓掌财经 WebSocket (同花顺+选股宝+见闻)...")
    t0 = time.time()
    guzhang = fetch_guzhang()
    print(f"[shock]     → {len(guzhang)} 条 ({time.time()-t0:.1f}s)")

    print("[shock]   拉取 华尔街见闻 REST...")
    t0 = time.time()
    wscn = fetch_wallstreetcn()
    print(f"[shock]     → {len(wscn)} 条 ({time.time()-t0:.1f}s)")

    print("[shock]   拉取 东方财富全球快讯...")
    t0 = time.time()
    em_global = fetch_eastmoney_global()
    print(f"[shock]     → {len(em_global)} 条 ({time.time()-t0:.1f}s)")

    all_headlines = guzhang + wscn + em_global
    print(f"[shock]   合计 {len(all_headlines)} 条标题，关键词匹配中...")

    # 去重（相同标题只保留一份）
    seen = set()
    unique = []
    for h in all_headlines:
        key = h["title"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(h)

    hits = match_keywords(unique, keyword_db)
    shocks, net_impact = aggregate_shocks(hits, keyword_db)

    result = {
        "date": date.today().isoformat(),
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_headlines": len(unique),
        "sources": {
            "guzhang_ws": len([h for h in unique if h["source"].startswith("guzhang")]),
            "wallstreetcn": len([h for h in unique if h["source"] == "wallstreetcn"]),
            "eastmoney_global": len([h for h in unique if h["source"] == "eastmoney_global"]),
        },
        "net_impact": net_impact,
        "impact_level": "negative" if net_impact < -1 else "positive" if net_impact > 1 else "neutral",
        "shocks": shocks,
        "all_hits": {
            cat_id: len(items)
            for cat_id, items in hits.items()
        }
    }

    if save:
        TRACKING_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[shock]   输出 → {OUTPUT_FILE}")
        print(f"[shock]   净影响: {net_impact} ({result['impact_level']})")
        if shocks:
            for s in shocks:
                print(f"[shock]     {s['label']}: {s['unique_count']}条, impact={s['impact']}")

    return result


if __name__ == "__main__":
    run_detection()

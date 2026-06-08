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
OUTPUT_FILE = TRACKING_DIR / "_macro" / "sentiment_shock.json"
STATE_FILE = TRACKING_DIR / "_macro" / ".sentiment_state.json"  # 记录上次运行时间
KEYWORDS_FILE = Path(__file__).resolve().parent / "shock_keywords.json"


def load_keywords():
    with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── 运行状态管理（时间戳断点回补） ───
def _load_last_run() -> float:
    """加载上次运行的时间戳（Unix seconds），用于断点回补"""
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return float(state.get("last_run_ts", 0))
        except Exception:
            pass
    return 0.0


def _save_last_run(ts: float = None):
    """保存本次运行时间戳"""
    if ts is None:
        ts = time.time()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"last_run_ts": ts, "last_run": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")}, indent=2), encoding="utf-8")


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
    from datetime import datetime as _dt
    df = ak.stock_info_global_em()
    if df is None or df.empty:
        return []
    headlines = []
    for _, row in df.tail(500).iterrows():
        title = str(row.iloc[0])[:200]
        summary = str(row.iloc[1])[:300] if len(row) > 1 else ""
        time_str = str(row.iloc[2])[:30] if len(row) > 2 else ""
        # 解析时间戳用于回补过滤
        ts = None
        try:
            ts = _dt.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        headlines.append({
            "title": title, "summary": summary,
            "source": "eastmoney_global", "time": time_str,
            "_ts": ts  # 内部用于 since 过滤
        })
    return headlines


def fetch_eastmoney_global(since: float = 0):
    try:
        results = _with_timeout(_fetch_eastmoney_global_worker, timeout_sec=45)
        if since > 0:
            from datetime import datetime as _dt
            cutoff = _dt.fromtimestamp(since)
            results = [h for h in results if h.get("_ts") and h["_ts"] >= cutoff]
        # 清理内部字段
        for h in results:
            h.pop("_ts", None)
        return results
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
def fetch_wallstreetcn(since: float = 0, max_pages: int = 20):
    """华尔街见闻 global-channel 快讯，游标翻页，回补到 since 时间戳为止"""
    headlines = []
    try:
        url = "https://api-prod.wallstreetcn.com/apiv1/content/lives"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://wallstreetcn.com/",
        }
        cursor = 0
        for page in range(max_pages):
            params = {"channel": "global-channel", "client": "pc", "cursor": cursor, "limit": 40}
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("data", {}).get("items", [])
            if not items:
                break

            oldest_ts = None
            for item in items:
                ts = item.get("display_time", 0)
                if isinstance(ts, str):
                    ts = int(ts)
                if oldest_ts is None or ts < oldest_ts:
                    oldest_ts = ts

                # 时间戳回补边界：遇到早于 since 的消息就停止
                if since > 0 and ts < since:
                    continue  # 这条太旧，跳过

                content = (item.get("content_text") or item.get("title") or "")[:300]
                if not content.strip():
                    continue
                headlines.append({
                    "title": content,
                    "content_text": content,
                    "source": "wallstreetcn",
                    "time": str(item.get("display_time", ""))
                })

            # 如果本页最旧的消息已经早于 since，说明回补到位了
            if since > 0 and oldest_ts and oldest_ts < since:
                break

            cursor = data.get("data", {}).get("next_cursor")
            if cursor is None:
                break
        return headlines
    except Exception as e:
        print(f"  [shock] 华尔街见闻获取失败: {e}")
        return headlines


# ─── 消息源4: 爱股票快讯 (纯JSON, 零认证) ───
AIGUPIAO_API = "https://apis.aigupiao.com/Express/express_list/"
AIGUPIAO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://news.aigupiao.com/",
}


def fetch_aigupiao(pages=3):
    """爱股票快讯，按页拉取（每页~10条，最多3页）"""
    headlines = []
    try:
        for page in range(1, pages + 1):
            params = {"page": page, "source": "pc", "web_data": "yes", "is_with_stock_link": "1"}
            resp = requests.get(AIGUPIAO_API, headers=AIGUPIAO_HEADERS, params=params, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            if data.get("rslt") != "succ":
                break
            for date_key, date_group in data.get("data", {}).items():
                for item in date_group.get("data", []):
                    content = (item.get("content") or "")[:200]
                    if not content.strip():
                        continue
                    # 去除HTML标签
                    content_clean = re.sub(r"<[^>]+>", "", content)
                    headlines.append({
                        "title": content_clean,
                        "source": "aigupiao",
                        "time": item.get("update_time", str(item.get("rec_time", "")))
                    })
            # 判断是否还有下一页（返回<10条说明到底了）
            items_in_page = sum(len(v.get("data", [])) for v in data.get("data", {}).values())
            if items_in_page < 10:
                break
        return headlines
    except Exception as e:
        print(f"  [shock] 爱股票获取失败: {e}")
        return headlines


# ─── 实体提取: 板块名 / 个股名 / 方向词 ───

# 板块名缓存（首次调用时从 tdxzs.cfg 加载）
_sector_names_cache = None
_stock_names_cache = None


def _load_sector_names() -> list[str]:
    """从 tdxzs.cfg 加载概念板块+行业板块名称列表（约300个）"""
    global _sector_names_cache
    if _sector_names_cache is not None:
        return _sector_names_cache

    cfg_path = Path("C:/zd_cjzq/T0002/hq_cache/tdxzs.cfg")
    if not cfg_path.exists():
        _sector_names_cache = []
        return []

    with open(cfg_path, "rb") as f:
        text = f.read().decode("gbk")
    names = []
    for line in text.strip().split("\n"):
        parts = line.split("|")
        if len(parts) < 3:
            continue
        category = int(parts[2])
        # 概念板块(4) + 行业板块(3)
        if category in (4, 3):
            name = parts[0]
            if len(name) >= 3 and name not in names:  # 至少3字，排除2字短名噪声
                names.append(name)
    # 按长度降序排列，优先匹配长名称（如"铜缆高速连接"优先于"铜缆"）
    names.sort(key=len, reverse=True)
    _sector_names_cache = names
    return names


def _load_stock_names() -> dict[str, str]:
    """从 stock_names.csv 加载个股名称→代码映射（仅真实A股，排除ETF/指数）"""
    global _stock_names_cache
    if _stock_names_cache is not None:
        return _stock_names_cache

    csv_path = PROJECT_ROOT / "signals" / "tracking" / "_funds" / "stock_names.csv"
    mapping = {}
    if csv_path.exists():
        import csv
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 2:
                    name = row[1].strip()
                    code = row[0].strip()
                    if not name or not code or len(name) < 3:
                        continue
                    # 只保留真实A股: sh6xxxxx / sz0xxxxx(00xxxx) / sz3xxxxx(30xxxx)
                    prefix2 = code[2:4] if len(code) >= 4 else ""
                    valid = False
                    if code.startswith("sh") and code[2:].startswith("6"):
                        valid = True
                    elif code.startswith("sz"):
                        if prefix2 in ("00", "30"):  # 主板00xxxx + 创业板30xxxx，排除39xxxx(指数)
                            valid = True
                    if not valid:
                        continue
                    # 排除ST/退市前缀
                    mapping[name] = code
    _stock_names_cache = mapping
    return mapping


# 方向词：负面 / 正面
_DIRECTION_NEG = ["利空", "承压", "冲击", "拖累", "受损", "暴跌", "大跌",
                  "重挫", "恐慌", "抛售", "制裁", "限制", "管制", "加征关税",
                  "退市", "违约", "暴雷", "爆雷", "调查", "立案", "处罚"]
_DIRECTION_POS = ["利好", "受益", "提振", "推动", "大涨", "飙升", "反弹",
                  "突破", "创新高", "放宽", "松绑", "取消限制", "减税",
                  "补贴", "扶持", "注入", "增持", "回购", "超预期"]


def _extract_entities(text: str) -> dict:
    """从正文中提取板块名、个股名、方向词"""
    result = {"sectors": [], "stocks": [], "direction": 0}

    if not text:
        return result

    # 提取板块名（按长度降序匹配，避免短名误匹配）
    for name in _load_sector_names():
        if name in text:
            result["sectors"].append(name)

    # 提取个股名
    stock_map = _load_stock_names()
    for name, code in stock_map.items():
        if name in text:
            result["stocks"].append({"name": name, "code": code})

    # 提取方向
    neg_count = sum(1 for w in _DIRECTION_NEG if w in text)
    pos_count = sum(1 for w in _DIRECTION_POS if w in text)
    if neg_count > pos_count:
        result["direction"] = -1
    elif pos_count > neg_count:
        result["direction"] = 1
    # 相等或都为0时保持0

    return result


# ─── 关键词匹配 ───
def match_keywords(headlines, keyword_db):
    """对每条标题跑全部关键词，返回命中列表（含实体提取）"""
    categories = keyword_db["categories"]
    hits_by_category = defaultdict(list)

    for h in headlines:
        title = h["title"].lower()
        # 合并所有可用正文: content_text + summary
        body_text = " ".join([
            h.get("content_text", ""),
            h.get("summary", "")
        ])
        for cat_id, cat_cfg in categories.items():
            for kw in cat_cfg["keywords"]:
                if kw.lower() in title:
                    entities = _extract_entities(title + " " + body_text)
                    hits_by_category[cat_id].append({
                        "title": h["title"],
                        "source": h["source"],
                        "time": h["time"],
                        "matched_keyword": kw,
                        "sectors": entities["sectors"],
                        "stocks": entities["stocks"],
                        "text_direction": entities["direction"],
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

        # 聚合实体信息
        all_sectors = []
        all_stocks = []
        text_dirs = []
        for it in items:
            all_sectors.extend(it.get("sectors", []))
            all_stocks.extend(it.get("stocks", []))
            text_dirs.append(it.get("text_direction", 0))

        # 去重
        unique_sectors = list({s: s for s in all_sectors}.values())
        seen_stocks = set()
        unique_stocks = []
        for s in all_stocks:
            if s["code"] not in seen_stocks:
                seen_stocks.add(s["code"])
                unique_stocks.append(s)

        # 正文方向与预定义方向比较
        auto_dir = round(sum(text_dirs) / len(text_dirs), 1) if text_dirs else 0
        # 同向确认（正文方向×影响方向>0）→ 强度++
        impact = cat_cfg["impact_sign"] * cat_cfg["impact_magnitude"]
        if auto_dir != 0 and (auto_dir * impact) > 0:
            confirmed = True
        else:
            confirmed = False

        unique_titles = list({i["title"] for i in items})
        shocks.append({
            "type": cat_id,
            "label": cat_cfg["label"],
            "level": cat_cfg["level"],
            "count": len(items),
            "unique_count": len(unique_titles),
            "impact": impact,
            "sample_titles": unique_titles[:5],
            "matched_keywords": list({i["matched_keyword"] for i in items}),
            "affected_sectors": unique_sectors[:10],   # 最多10个板块
            "affected_stocks": unique_stocks[:15],     # 最多15只个股
            "text_direction": auto_dir,
            "direction_confirmed": confirmed,
        })

        weight = 1.0 if cat_cfg["level"] == "macro" else (0.6 if cat_cfg["level"] == "market" else 0.3)
        net_impact += impact * weight
        total_weight += weight

    return shocks, round(net_impact, 1)


# ─── 累计标题管理 ───
def _load_existing_headlines(today_str: str) -> tuple:
    """加载已保存的当天累计标题。返回 (headlines_list, seen_keys_set)"""
    if not OUTPUT_FILE.exists():
        return [], set()
    try:
        prev = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        if prev.get("date") != today_str:
            return [], set()
        prev_headlines = prev.get("_headlines", [])
        seen = {h["title"][:80] for h in prev_headlines}
        return prev_headlines, seen
    except Exception:
        return [], set()


# ─── 主入口 ───
def run_detection(save=True):
    """运行B类突发事件检测。按时间戳回补到上次断点，同一天内增量追加。"""
    print("[shock] 消息面突发事件检测...")
    today_str = date.today().isoformat()
    last_run = _load_last_run()
    keyword_db = load_keywords()

    if last_run > 0:
        gap_hours = (time.time() - last_run) / 3600
        print(f"[shock]   上次运行: {datetime.fromtimestamp(last_run).strftime('%m-%d %H:%M')} ({gap_hours:.0f}h前)，回补中...")
    else:
        print("[shock]   首次运行，拉取最新快讯...")

    # 加载当天已有累计（增量模式）
    existing_headlines, seen_keys = _load_existing_headlines(today_str)
    if existing_headlines:
        print(f"[shock]   当天已有 {len(existing_headlines)} 条累计标题，增量追加...")

    # 多源拉取（串行，各源独立失败，传入 since 时间戳回补）
    print("[shock]   拉取 鼓掌财经 WebSocket (同花顺+选股宝+见闻)...")
    t0 = time.time()
    guzhang = fetch_guzhang()  # WebSocket 不支持回查，只能拿当前实时
    print(f"[shock]     → {len(guzhang)} 条 ({time.time()-t0:.1f}s)")

    print("[shock]   拉取 华尔街见闻 REST (回补中)...")
    t0 = time.time()
    wscn = fetch_wallstreetcn(since=last_run)
    print(f"[shock]     → {len(wscn)} 条 ({time.time()-t0:.1f}s)")

    print("[shock]   拉取 东方财富全球快讯 (回补中)...")
    t0 = time.time()
    em_global = fetch_eastmoney_global(since=last_run)
    print(f"[shock]     → {len(em_global)} 条 ({time.time()-t0:.1f}s)")

    print("[shock]   拉取 爱股票快讯...")
    t0 = time.time()
    aigupiao = fetch_aigupiao()  # 页数浅，可能丢历史但不影响主流程
    print(f"[shock]     → {len(aigupiao)} 条 ({time.time()-t0:.1f}s)")

    all_headlines = guzhang + wscn + em_global + aigupiao

    # 与已有累计合并（去重新增）
    new_count = 0
    for h in all_headlines:
        key = h["title"][:80]
        if key not in seen_keys:
            seen_keys.add(key)
            existing_headlines.append(h)
            new_count += 1

    print(f"[shock]   新增 {new_count} 条，累计 {len(existing_headlines)} 条标题，关键词匹配中...")

    if new_count == 0 and existing_headlines:
        print("[shock]   无新增标题，跳过重新匹配（使用上次结果）")
        prev = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        return prev

    hits = match_keywords(existing_headlines, keyword_db)
    shocks, net_impact = aggregate_shocks(hits, keyword_db)

    result = {
        "date": today_str,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_headlines": len(existing_headlines),
        "sources": {
            "guzhang_ws": len([h for h in existing_headlines if h["source"].startswith("guzhang")]),
            "wallstreetcn": len([h for h in existing_headlines if h["source"] == "wallstreetcn"]),
            "eastmoney_global": len([h for h in existing_headlines if h["source"] == "eastmoney_global"]),
            "aigupiao": len([h for h in existing_headlines if h["source"] == "aigupiao"]),
        },
        "net_impact": net_impact,
        "impact_level": "negative" if net_impact < -1 else "positive" if net_impact > 1 else "neutral",
        "shocks": shocks,
        "all_hits": {
            cat_id: len(items)
            for cat_id, items in hits.items()
        },
        "_headlines": existing_headlines,  # 累计标题缓存（供同日增量使用）
    }

    if save:
        TRACKING_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        _save_last_run()  # 保存本次运行时间戳，供下次回补
        print(f"[shock]   输出 → {OUTPUT_FILE}")
        print(f"[shock]   净影响: {net_impact} ({result['impact_level']})")
        if shocks:
            for s in shocks:
                extra = ""
                if s["affected_sectors"]:
                    extra += f" → 板块: {', '.join(s['affected_sectors'][:5])}"
                if s["affected_stocks"]:
                    stocks_str = ", ".join([x["name"] for x in s["affected_stocks"][:5]])
                    extra += f" | 个股: {stocks_str}"
                confirm_mark = " [OK]" if s["direction_confirmed"] else ""
                print(f"[shock]     {s['label']}: {s['unique_count']}条, impact={s['impact']}{confirm_mark}{extra}")

    return result


if __name__ == "__main__":
    run_detection()

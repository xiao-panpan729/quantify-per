# -*- coding: utf-8 -*-
"""
US 市场异动提取器 — 从 shock_detector 累积快讯中提取美股个股/板块/ETF异动事件

输入: signals/tracking/_macro/sentiment_shock.json (shock_detector 产出)
输出: signals/tracking/_macro/us_movers.json + 结构化事件文本

用法:
  python tools/us_market/us_movers_extract.py              # 默认今日
  python tools/us_market/us_movers_extract.py --date 20260612  # 指定日期
  python tools/us_market/us_movers_extract.py --today           # 只看当日增量
"""
import json, re, sys, argparse
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config import PROJECT_ROOT

TRACKING_DIR = Path(PROJECT_ROOT, "signals", "tracking")
MACRO_DIR = TRACKING_DIR / "_macro"
MACRO_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─── 美股个股中英文名映射（用于识别快讯中的公司名） ───
US_STOCK_NAMES = {
    # 半导体链
    "英伟达": "NVDA", "AMD": "AMD", "超威半导体": "AMD", "博通": "AVGO",
    "高通": "QCOM", "美光": "MU", "迈威尔": "MRVL", "美满电子": "MRVL",
    "阿斯麦": "ASML", "台积电": "TSM", "英特尔": "INTC",
    "应用材料": "AMAT", "拉姆研究": "LRCX", "科磊": "KLAC", "科天半导体": "KLAC",
    "中际旭创": "300308.SZ",  # A股映射
    # 存储链
    "闪迪": "WDC", "西部数据": "WDC", "希捷": "STX", "希捷科技": "STX",
    # AI/软件
    "Palantir": "PLTR", "CrowdStrike": "CRWD", "Snowflake": "SNOW",
    "Cloudflare": "NET", "Datadog": "DDOG", "MongoDB": "MDB",
    "ServiceNow": "NOW", "Adobe": "ADBE", "Salesforce": "CRM", "Oracle": "ORCL",
    # Mag7
    "苹果": "AAPL", "Apple": "AAPL", "微软": "MSFT", "Microsoft": "MSFT",
    "谷歌": "GOOGL", "Alphabet": "GOOGL", "亚马逊": "AMZN", "Amazon": "AMZN",
    "Meta": "META", "特斯拉": "TSLA", "Tesla": "TSLA",
    # 太空/卫星
    "Rocket Lab": "RKLB", "Redwire": "RDW",
    "AST SpaceMobile": "ASTS", "AST": "ASTS",
    "Virgin Galactic": "SPCE",
    "Firefly Aerospace": None,  # 未上市
    "Intuitive Machines": "LUNR",
    # 金融
    "摩根大通": "JPM", "高盛": "GS", "美国银行": "BAC", "摩根士丹利": "MS",
    "Visa": "V", "Mastercard": "MA", "BlackRock": "BLK",
    # 加密
    "Coinbase": "COIN", "MicroStrategy": "MSTR",
    # 其他
    "Robinhood": "HOOD", "英特尔": "INTC", "华纳兄弟": "WBD", "高意": "COHR",
    "Coherent": "COHR",
}

# ─── 板块/指数关键词 → 归类 ───
SECTOR_KEYWORDS = {
    "存储芯片": ["存储", "闪存", "SSD", "NAND", "DRAM"],
    "半导体": ["半导体", "芯片", "晶圆", "光刻", "费城半导体"],
    "AI/人工智能": ["人工智能", "AI赢家", "AI软件", "AI先驱"],
    "太空/卫星": ["太空", "卫星", "星链", "SpaceX", "火箭", "航天"],
    "科技/信息": ["信息科技", "科技股"],
    "金融": ["银行", "金融", "区域银行"],
    "新能源车": ["电动汽车", "EV"],
    "航空": ["航空", "全球航空"],
    "能源": ["能源", "原油", "油气"],
    "加密/数字资产": ["数字资产", "加密", "区块链", "比特币"],
}

# ─── 正则模式 ───
RE_PCT = re.compile(
    r'([一-鿿\w\s·&()-]{1,30}?)\s*[-−]?\s*'
    r'((?:收?[涨跌]约?|上涨|下跌|大涨|暴跌|飙升|下[跌挫]|上[涨扬])\s*[0-9]+\.?[0-9]*\s*%?)'
)
RE_STOCK_MOVE = re.compile(
    r'([一-鿿\w·]{1,20}?(?:公司|科技|控股)?)'
    r'\s*(收?[涨跌]约?|股价上[涨扬]|股价下[跌挫]|大涨|暴跌|飙升|重挫|下[跌挫]|上[涨扬]|刷新|创)'
    r'([^，。,.\n]*?[0-9]+\.?[0-9]*\s*%?)'
)
RE_INDEX_PCT = re.compile(
    r'(美股\s*)?([一-鿿\w/·&\-]{1,25}?(?:指数|ETF|板块))'
    r'\s*([涨跌])\s*([0-9]+\.?[0-9]*)\s*%'
)
RE_ETC_PCT = re.compile(
    r'(半导体ETF|银行ETF|航空ETF|金融ETF|科技ETF|能源ETF|'
    r'全球航空业ETF|区域银行ETF|公用事业ETF|房地产ETF|网络股ETF|'
    r'生物科技ETF|医疗ETF|消费ETF|罗素\d+ETF|纳指\d+ETF|标普\d+ETF|'
    r'道指ETF|新兴市场ETF|黄金ETF|大豆基金)'
    r'[一-鿿]*[涨跌][一-鿿]*([0-9]+\.?[0-9]*)\s*%'
)
RE_NEW_HIGH_LOW = re.compile(
    r'((?:[一-鿿\w·&]+\s?){1,4}(?:公司|科技|股份|集团)?)'
    r'[一-鿿·]*'
    r'(刷新|创下?|创)[一-鿿·]*'
    r'(盘中历史最高|历史最高|历史新高|盘中新高|历史最低|历史新低|最低位)'
    r'(?:位?至\s*[0-9.,]+\s*(?:美元)?)?'
)
RE_COMPONENT = re.compile(
    r'([一-鿿\w·&]+(?:公司|科技|控股)?)'
    r'\s*([涨跌]约?)\s*([0-9]+\.?[0-9]*)\s*%'
)


def load_shock_data(date_str=None):
    """读取 shock_detector 产出"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    path = MACRO_DIR / "sentiment_shock.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def extract_us_movers(data: dict, date_str: str = None):
    """从 shock_detector 数据中提取美股异动事件"""
    headlines = data.get("_headlines", [])
    events = {
        "stocks": [],       # 个股异动
        "sectors": [],      # 板块/指数异动
        "etfs": [],         # ETF异动
        "broad_index": [],  # 大盘指数
        "new_high_low": [], # 创历史新高/新低
        "ipo_spacex": [],   # SpaceX/IPO
    }

    for h in headlines:
        title = h.get("title", "").strip()
        source = h.get("source", "")
        ts = h.get("time", "")

        # 跳过非美股内容
        if any(x in title for x in [
            'A股', '上证', '创业板', '科创', '北证', '恒生', '港股',
            '央行', '财政部', '统计局', '发改委', '证监会', '国铁',
            '人民币', '暴雨', '铁路', '统计局', '巴基斯坦', '约旦',
            '以色列', '伊朗', '拉美', 'MSCI发展', '新兴市场',
            '韩元', '日元', 'ICE美元', 'CFTC', '彭博谷物',
            '富时A50',
        ]):
            continue

        # ── 匹配模式 ──
        event = None

        # 1. 大盘指数（标普/纳指/道指）— 精确定位开头
        m_idx = re.search(r'(标普500指数|纳斯达克(?:综合|100|)指数|纳指|道琼斯工业平均指数|道指)\s*(?:收?[涨跌])\s*[0-9]+\.?[0-9]*\s*(?:点|，)', title)
        if m_idx:
            # 只处理以该指数开头的快讯
            idx_name = m_idx.group(1)
            if "标普" in idx_name:
                idx_name = "标普500"
            elif "纳" in idx_name:
                idx_name = "纳指"
            elif "道" in idx_name:
                idx_name = "道指"
            m_pct = re.search(r'(?:涨幅|收?[涨跌])\s*([0-9]+\.?[0-9]*)\s*%', title)
            m_pt = re.search(r'收?[涨跌]\s*([0-9]+\.?[0-9]*)\s*点', title)
            if m_pct:
                direction = "up" if re.search(r'(?:涨|涨幅)', title) else "down"
                events["broad_index"].append({
                    "name": idx_name,
                    "move": m_pct.group(1),
                    "unit": "%",
                    "direction": direction,
                    "source": source,
                    "ts": ts,
                })
            elif m_pt:
                direction = "up" if "涨" in title else "down"
                events["broad_index"].append({
                    "name": idx_name,
                    "move": m_pt.group(1),
                    "unit": "点",
                    "direction": direction,
                    "source": source,
                    "ts": ts,
                })

        # 2. 行业指数/板块
        m = RE_INDEX_PCT.search(title)
        if m:
            idx_name = m.group(2).strip()
            # 排除大盘宽基、ETF名、非美国指数
            if any(x in idx_name for x in ['标普500', '道琼斯', '纳斯达克', 'MSCI发展', '北欧', '欧洲', '德国', '法国']):
                continue
            if idx_name.endswith('ETF'):
                continue  # ETF 在单独 section 处理
            direction = m.group(3)
            pct = float(m.group(4))
            # 去重
            if not any(e["name"] == idx_name and abs(float(e["pct"]) - pct) < 0.01 for e in events["sectors"]):
                sec = classify_sector(idx_name)
                # 恢复被截断的名称
                if idx_name == "赢家指数" and "AI" in title:
                    idx_name = "AI赢家指数"
                events["sectors"].append({
                    "name": idx_name,
                    "direction": "up" if direction == "涨" else "down",
                    "pct": pct,
                    "category": sec,
                    "title": title[:150],
                    "source": source,
                    "ts": ts,
                })

        # 3. ETF 异动由 extract_etf_events() 独立提取

        # 4. 创历史新高/新低（只收录美股可识别个股）
        m = RE_NEW_HIGH_LOW.search(title)
        if m:
            stock_name = m.group(1).strip()
            hl_type = m.group(3)
            is_high = "高" in hl_type
            symbol = US_STOCK_NAMES.get(stock_name, "")
            # 排除欧洲指数、亚洲、非个股
            if (len(stock_name) >= 2 and
                stock_name not in ["美国应急", "标普", "纳指", "欧洲", "德国", "法国", "意大利",
                                   "突破", "逼近", "暂报", "本周", "上周"] and
                not stock_name.startswith(("突破", "逼近", "暂报", "至"))):
                events["new_high_low"].append({
                    "name": stock_name,
                    "symbol": symbol,
                    "type": "high" if is_high else "low",
                    "title": title[:120],
                    "source": source,
                    "ts": ts,
                })

        # 5. SpaceX / IPO
        if "SpaceX" in title or "IPO" in title:
            events["ipo_spacex"].append({
                "title": title[:150],
                "source": source,
                "ts": ts,
            })

    # 6. 提取个股异动（从全部标题中二次扫描）
    stock_moves = extract_stock_moves(headlines)
    events["stocks"] = dedup_stock_moves(stock_moves)
    # ★ ETF 异动独立提取（与主循环分离）
    events["etfs"] = extract_etf_events(headlines)

    return events


def extract_etf_events(headlines: list) -> list:
    """独立提取ETF异动（与主循环分离，避免边界case）"""
    seen = set()
    result = []
    skip_words = {'A股', '上证', '创业板', '港股', '恒生', '新兴市场', 'MSCI发展',
                  '韩元', '日元', 'ICE美元', 'CFTC', '富时A50'}
    for h in headlines:
        title = h.get("title", "").strip()
        if any(x in title for x in skip_words):
            continue
        for m in RE_ETC_PCT.finditer(title):
            name = m.group(1).strip()
            pct = float(m.group(2))
            if (name, pct) in seen:
                continue
            seen.add((name, pct))
            mt = title[m.start():m.end()]
            direction = "up" if "涨" in mt else "down"
            result.append({
                "name": name,
                "direction": direction,
                "pct": pct,
                "source": h.get("source", ""),
                "ts": h.get("time", ""),
            })
    result.sort(key=lambda x: abs(x["pct"]), reverse=True)
    return result


def classify_sector(name: str) -> str:
    """按关键词归入大类"""
    for cat, kws in SECTOR_KEYWORDS.items():
        for kw in kws:
            if kw in name:
                return cat
    return "其他"


def extract_stock_moves(headlines: list) -> list:
    """从标题中提取个股涨跌 — 要求股票名在涨跌幅±30字符内"""
    results = []
    for h in headlines:
        title = h.get("title", "").strip()
        source = h.get("source", "")
        ts = h.get("time", "")

        if any(x in title for x in ['A股', '上证', '创业板', '港股', '恒生']):
            continue

        # 找到所有已知美股公司名
        for cname, ticker in US_STOCK_NAMES.items():
            if cname not in title or not ticker:
                continue

            # 对每个出现都尝试提取
            search_start = 0
            while cname in title[search_start:]:
                idx = title.index(cname, search_start)
                search_start = idx + 1

                local = title[max(0, idx-5):idx+len(cname)+35]
                m = re.search(r'(收?[涨跌]约?|[涨跌]幅|股价上涨|股价下跌|上[涨扬]|下[跌挫]|大涨|暴跌|飙升|重挫|下滑|跌幅扩大)\s*([0-9]+\.?[0-9]*)\s*%', local)
                if not m:
                    continue
                pct_match_pos = local.find(m.group(0))
                if pct_match_pos > len(cname) + 25:
                    continue

                pct = float(m.group(2))
                tag = m.group(1)
                ctx = title[max(0, idx-20):idx+len(cname)+20]
                if any(s in ctx for s in ['签署', '协议', '合作', '授予', '跳槽', '加盟', '挖角', 'CFO', '劲敌', '对手']):
                    continue

                direction = "up" if any(d in tag for d in ['涨', '上', '升', '飙升', '大涨']) else "down"
                results.append({
                    "name": cname,
                    "symbol": ticker,
                    "direction": direction,
                    "pct": pct,
                    "title": title[:120],
                    "source": source,
                    "ts": ts,
                })
                break  # 同股票名在同一条快讯里只取一次

    return results


def dedup_stock_moves(moves: list) -> list:
    """去重：同个股取最新pct"""
    by_symbol = {}
    for m in moves:
        key = m["symbol"]
        if key not in by_symbol:
            by_symbol[key] = m
        else:
            if abs(m["pct"]) > abs(by_symbol[key]["pct"]):
                by_symbol[key] = m
    return sorted(by_symbol.values(), key=lambda x: abs(x["pct"]), reverse=True)


def format_movers_markdown(events: dict) -> str:
    """输出结构化 Markdown"""
    lines = []
    lines.append("## 📊 美股异动")
    lines.append("")

    # ── 大盘指数 ──
    if events["broad_index"]:
        lines.append("### 大盘指数")
        for e in events["broad_index"]:
            arrow = "📈" if e["direction"] == "up" else "📉"
            sign = "+" if e["direction"] == "up" else "-"
            lines.append(f"- {arrow} {e['name']} {sign}{e['move']}{e['unit']}")
        lines.append("")

    # ── 行业板块 ──
    if events["sectors"]:
        lines.append("### 行业板块/指数")
        # 按类别排序
        sectors_by_cat = defaultdict(list)
        for e in events["sectors"]:
            sectors_by_cat[e["category"]].append(e)
        for cat, slist in sectors_by_cat.items():
            lines.append(f"**{cat}**")
            for e in sorted(slist, key=lambda x: abs(x["pct"]), reverse=True):
                arrow = "🔴" if e["direction"] == "up" else "🟢"
                lines.append(f"  - {arrow} {e['name']} {'+' if e['direction']=='up' else '-'}{e['pct']}%")
        lines.append("")

    # ── 个股异动 Top 20 ──
    if events["stocks"]:
        lines.append("### 个股异动")
        for e in events["stocks"][:20]:
            arrow = "🔴" if e["direction"] == "up" else "🟢"
            sign = "+" if e["direction"] == "up" else "-"
            symbol = e.get("symbol", "")
            lines.append(f"  - {arrow} {e['name']} ({symbol}) {sign}{e['pct']}%")
            if lines[-1] and len(e['title']) > 60:
                pass
        lines.append("")

    # ── ETF ──
    if events["etfs"]:
        lines.append("### ETF")
        etf_strs = []
        for e in sorted(events["etfs"], key=lambda x: abs(x["pct"]), reverse=True)[:10]:
            arrow = "🔴" if e["direction"] == "up" else "🟢"
            sign = "+" if e["direction"] == "up" else "-"
            etf_strs.append(f"{arrow} {e['name']} {sign}{e['pct']}%")
        lines.append("  " + " | ".join(etf_strs))
        lines.append("")

    # ── 创历史新高/新低 ──
    if events["new_high_low"]:
        lines.append("### 创历史新高/新低")
        for e in events["new_high_low"]:
            tag = "🆕 新高" if e["type"] == "high" else "⚠️ 新低"
            sym = f"({e.get('symbol','')})" if e.get("symbol") else ""
            lines.append(f"  - {tag} {e['name']} {sym}")
        lines.append("")

    return "\n".join(lines)


def save_movers(events: dict, date_str: str = None):
    """保存 JSON"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    json_path = MACRO_DIR / "us_movers.json"
    payload = {
        "date": date_str,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "events": events,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[JSON] {json_path}")
    return payload


# ─── CLI ───
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="日期 YYYYMMDD")
    parser.add_argument("--save", action="store_true", help="保存JSON")
    parser.add_argument("--markdown", action="store_true", help="输出MD格式")
    args = parser.parse_args()

    data = load_shock_data(args.date)
    if not data:
        print("无 shock_detector 数据，请先运行 shock_detector.py")
        return

    events = extract_us_movers(data)
    total = (len(events["stocks"]) + len(events["sectors"]) +
             len(events["etfs"]) + len(events["broad_index"]) +
             len(events["new_high_low"]) + len(events["ipo_spacex"]))
    print(f"提取美股异动: {total} 条事件")
    print(f"  个股: {len(events['stocks'])} | 板块: {len(events['sectors'])} | "
          f"ETF: {len(events['etfs'])} | 大盘: {len(events['broad_index'])} | "
          f"新高/新低: {len(events['new_high_low'])} | SpaceX/IPO: {len(events['ipo_spacex'])}")

    if args.markdown:
        print(format_movers_markdown(events))
    if args.save:
        save_movers(events, args.date)


if __name__ == "__main__":
    main()

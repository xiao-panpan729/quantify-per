# -*- coding: utf-8 -*-
"""
US 明星股动量评分 v1.0 — 通达信 RSI势能2 公式镜像
===============================================

对 ~74 只美股核心标的做动量评分，覆盖 Mag7 / 半导体链 / AI SaaS / 金融 / 医药 / 能源 / 消费 / 军工 / 加密。

用法:
  python tools/us_market/star_stocks.py --save
  python tools/us_market/star_stocks.py --search NVDA
  python tools/us_market/star_stocks.py --category "AI & Software"
"""

import argparse
import json
import os
import sys
import time
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import akshare as ak
from tools.sector_momentum import calc_index_x1

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKING_DIR = PROJECT_ROOT / "signals" / "tracking"
REPORT_DIR = PROJECT_ROOT / "reports" / "us_market"

# ── US 明星股宇宙 (~55 stocks) ──
US_STAR_STOCKS = OrderedDict({
    "Magnificent 7": OrderedDict({
        "AAPL": "Apple",
        "MSFT": "Microsoft",
        "NVDA": "NVIDIA",
        "GOOGL": "Alphabet",
        "AMZN": "Amazon",
        "META": "Meta Platforms",
        "TSLA": "Tesla",
        "NFLX": "Netflix",
    }),
    "Semiconductor Chain": OrderedDict({
        "AMD": "AMD",
        "AVGO": "Broadcom",
        "QCOM": "Qualcomm",
        "MU": "Micron",
        "ASML": "ASML",
        "TSM": "TSMC",
        "INTC": "Intel",
        "AMAT": "Applied Materials",
        "LRCX": "Lam Research",
        "KLAC": "KLA Corp",
        "MRVL": "Marvell Technology",
        "TXN": "Texas Instruments",
        "ADI": "Analog Devices",
        "NXPI": "NXP Semiconductors",
        "MCHP": "Microchip Technology",
        "ON": "ON Semiconductor",
        "APH": "Amphenol",
        "WDC": "Western Digital",
        "STX": "Seagate",
    }),
    "AI & Software": OrderedDict({
        "PLTR": "Palantir",
        "CRWD": "CrowdStrike",
        "SNOW": "Snowflake",
        "NET": "Cloudflare",
        "DDOG": "Datadog",
        "MDB": "MongoDB",
        "NOW": "ServiceNow",
        "ADBE": "Adobe",
        "CRM": "Salesforce",
        "ORCL": "Oracle",
    }),
    "Finance": OrderedDict({
        "JPM": "JPMorgan",
        "GS": "Goldman Sachs",
        "BAC": "Bank of America",
        "MS": "Morgan Stanley",
        "V": "Visa",
        "MA": "Mastercard",
        "AXP": "AmEx",
        "BLK": "BlackRock",
    }),
    "Healthcare": OrderedDict({
        "LLY": "Eli Lilly",
        "UNH": "UnitedHealth",
        "JNJ": "J&J",
        "ABBV": "AbbVie",
        "PFE": "Pfizer",
        "MRK": "Merck",
    }),
    "Energy": OrderedDict({
        "XOM": "Exxon Mobil",
        "CVX": "Chevron",
        "COP": "ConocoPhillips",
        "SLB": "Schlumberger",
        "EOG": "EOG Resources",
        "NRG": "NRG Energy",
    }),
    "Consumer & Retail": OrderedDict({
        "HD": "Home Depot",
        "NKE": "Nike",
        "SBUX": "Starbucks",
        "COST": "Costco",
        "WMT": "Walmart",
        "MCD": "McDonald's",
        "DIS": "Disney",
    }),
    "Industrial & Defense": OrderedDict({
        "CAT": "Caterpillar",
        "GE": "GE Aerospace",
        "BA": "Boeing",
        "RTX": "RTX Corp",
        "HON": "Honeywell",
        "DE": "Deere & Co",
    }),
    "Crypto & Alts": OrderedDict({
        "COIN": "Coinbase",
        "MSTR": "MicroStrategy",
        "MARA": "Marathon Digital",
        "RIOT": "Riot Platforms",
    }),
})

# ── US x₁ 阈值（基于 320 obs 分布，2026-06 校准） ──
US_X1_THRESHOLDS = {
    "mild": 3.0,      # P75: 走强启动
    "elevated": 6.0,  # ~P90: 显著走强
    "strong": 8.0,    # P95: 强势
}

# ── 子行业排名（全球前三龙头的细分归属） ──
US_SUB_SECTORS = OrderedDict({
    "模拟芯片": OrderedDict([("TXN", 1), ("ADI", 2)]),
    "存储芯片": OrderedDict([("MU", 1), ("WDC", 2), ("STX", 3)]),
    "AI芯片": OrderedDict([("NVDA", 1)]),
    "通信芯片": OrderedDict([("QCOM", 1)]),
    "CPU/GPU": OrderedDict([("INTC", 1), ("AMD", 2)]),
    "网络/AI芯片": OrderedDict([("AVGO", 1)]),
    "功率半导体": OrderedDict([("ON", 1)]),
    "MCU": OrderedDict([("MCHP", 1)]),
    "汽车芯片": OrderedDict([("NXPI", 1)]),
    "数据中心芯片": OrderedDict([("MRVL", 1)]),
    "半导体设备": OrderedDict([("AMAT", 1), ("LRCX", 2)]),
    "光刻设备": OrderedDict([("ASML", 1)]),
    "晶圆代工": OrderedDict([("TSM", 1)]),
    "半导体检测": OrderedDict([("KLAC", 1)]),
    "连接器": OrderedDict([("APH", 1)]),
    "智能手机": OrderedDict([("AAPL", 1)]),
    "云计算平台": OrderedDict([("MSFT", 1)]),
    "搜索引擎": OrderedDict([("GOOGL", 1)]),
    "云计算+电商": OrderedDict([("AMZN", 1)]),
    "社交平台": OrderedDict([("META", 1)]),
    "电动车": OrderedDict([("TSLA", 1)]),
    "流媒体": OrderedDict([("NFLX", 1)]),
    "网络安全": OrderedDict([("CRWD", 1)]),
    "CDN/边缘计算": OrderedDict([("NET", 1)]),
    "创意软件": OrderedDict([("ADBE", 1)]),
    "数据库": OrderedDict([("ORCL", 1), ("MDB", 2)]),
    "云数据平台": OrderedDict([("SNOW", 1)]),
    "云监控": OrderedDict([("DDOG", 1)]),
    "企业管理SaaS": OrderedDict([("NOW", 1)]),
    "客户管理SaaS": OrderedDict([("CRM", 1)]),
    "数据分析": OrderedDict([("PLTR", 1)]),
    "支付网络": OrderedDict([("V", 1), ("MA", 2)]),
    "资产管理": OrderedDict([("BLK", 1)]),
    "信用卡": OrderedDict([("AXP", 1)]),
    "投资银行": OrderedDict([("GS", 1), ("MS", 2)]),
    "全能银行": OrderedDict([("JPM", 1), ("BAC", 2)]),
    "创新药": OrderedDict([("LLY", 1), ("ABBV", 2), ("MRK", 3)]),
    "综合医疗": OrderedDict([("JNJ", 1)]),
    "医保服务": OrderedDict([("UNH", 1)]),
    "综合制药": OrderedDict([("PFE", 1)]),
    "综合油气": OrderedDict([("XOM", 1), ("CVX", 2)]),
    "油气勘探生产": OrderedDict([("COP", 1)]),
    "油服": OrderedDict([("SLB", 1)]),
    "页岩油": OrderedDict([("EOG", 1)]),
    "独立发电": OrderedDict([("NRG", 1)]),
    "运动鞋服": OrderedDict([("NKE", 1)]),
    "快餐连锁": OrderedDict([("MCD", 1)]),
    "咖啡连锁": OrderedDict([("SBUX", 1)]),
    "仓储会员店": OrderedDict([("COST", 1)]),
    "综合零售": OrderedDict([("WMT", 1)]),
    "综合娱乐": OrderedDict([("DIS", 1)]),
    "家居建材零售": OrderedDict([("HD", 1)]),
    "工程机械": OrderedDict([("CAT", 1)]),
    "航空发动机": OrderedDict([("GE", 1)]),
    "飞机制造": OrderedDict([("BA", 1)]),
    "防务航空": OrderedDict([("RTX", 1)]),
    "多元化工业": OrderedDict([("HON", 1)]),
    "农用机械": OrderedDict([("DE", 1)]),
    "加密交易所": OrderedDict([("COIN", 1)]),
    "比特币持仓": OrderedDict([("MSTR", 1)]),
    "比特币挖矿": OrderedDict([("MARA", 1), ("RIOT", 2)]),
})


def fetch_stock_daily(symbol: str) -> tuple | None:
    """拉取单只美股日线，返回 (close, volume) numpy 数组"""
    try:
        df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
        if df is None or len(df) < 65:
            return None
        close = df["close"].to_numpy(dtype=np.float64)
        volume = df["volume"].to_numpy(dtype=np.float64)
        return close, volume
    except Exception as e:
        print(f"  [{symbol}] 拉取失败: {e}")
        return None


def load_us_stock_names() -> dict:
    """从 us_stock_names.json 加载完整的股票信息（含描述/行业/子行业排名）"""
    path = Path(__file__).resolve().parent / "us_stock_names.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("stocks", {})
    except Exception:
        return {}


def _enrich_with_names(results: list[dict]) -> list[dict]:
    """将名称数据库中的描述信息合并到结果中"""
    names_db = load_us_stock_names()
    for r in results:
        sym = r["symbol"]
        info = names_db.get(sym, {})
        r["cn_name"] = info.get("cn", "")
        r["description"] = info.get("description", "")
        r["industry"] = info.get("industry", "")
        r["sub_sector"] = info.get("sub_sector", "")
        r["sub_rank"] = info.get("sub_rank", 0)
    return results


# ── x₁ 历史回填 ──
US_X1_HISTORY_DIR = TRACKING_DIR / "_macro" / "us_x1_history"
US_X1_DAILY_DIR = US_X1_HISTORY_DIR / "daily"
US_X1_MIN_BARS = 65

_STOCK_CACHE = {}  # 全局缓存：{symbol: {dates, close, volume}}


def fetch_all_us_stock_data() -> dict:
    """批量获取所有 US 明星股完整日线数据（带缓存）"""
    if _STOCK_CACHE:
        return _STOCK_CACHE

    total = sum(len(v) for v in US_STAR_STOCKS.values())
    print(f"  批量获取 {total} 只个股日线...")
    data = {}
    for cat, stocks in US_STAR_STOCKS.items():
        for symbol, name in stocks.items():
            print(f"    [{symbol}] {name} ...", end=" ", flush=True)
            try:
                df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
                if df is None or len(df) < US_X1_MIN_BARS:
                    print("数据不足")
                    continue
                dates = df["date"].to_numpy()
                close = df["close"].to_numpy(dtype=np.float64)
                volume = df["volume"].to_numpy(dtype=np.float64)
                names_db = load_us_stock_names()
                info = names_db.get(symbol, {})
                data[symbol] = {
                    "dates": dates,
                    "close": close,
                    "volume": volume,
                    "name": name,
                    "cn_name": info.get("cn", ""),
                    "category": cat,
                    "description": info.get("description", ""),
                    "industry": info.get("industry", ""),
                    "sub_sector": info.get("sub_sector", ""),
                    "sub_rank": info.get("sub_rank", 0),
                }
                print(f"OK ({len(close)} bars)")
            except Exception as e:
                print(f"失败: {e}")
            time.sleep(0.3)

    _STOCK_CACHE.update(data)
    print(f"  批量获取完成: {len(data)}/{total} 只有效")
    return data


def get_us_trading_dates(stock_data: dict) -> list:
    """合并所有股票日期列表，50% 覆盖度过滤"""
    from collections import Counter
    date_counter = Counter()
    for symbol, info in stock_data.items():
        for d in info["dates"]:
            date_counter[d] += 1

    threshold = max(1, len(stock_data) * 0.5)
    trading_dates = sorted(d for d, cnt in date_counter.items() if cnt >= threshold)
    return trading_dates


def compute_us_x1_for_date(stock_data: dict, target_date_int: int,
                           x1_cache: dict = None) -> list[dict]:
    """对单日计算全部标的 x₁，含子行业排名"""
    scores = []
    for symbol, info in stock_data.items():
        dates = info["dates"]
        close = info["close"]

        # 找到 <= target_date_int 的最后一个索引
        idx = np.searchsorted(dates, target_date_int, side="right") - 1
        if idx < US_X1_MIN_BARS - 1:
            continue

        if x1_cache and symbol in x1_cache:
            x1_series = x1_cache[symbol]
            x1_val = x1_series[idx]
        else:
            volume = info["volume"]
            close_slice = close[:idx + 1]
            volume_slice = volume[:idx + 1]
            x1_val = calc_index_x1(close_slice, volume_slice)

        scores.append({
            "symbol": symbol,
            "name": info["name"],
            "cn_name": info.get("cn_name", ""),
            "category": info.get("category", ""),
            "x1": round(float(x1_val), 2),
            "close": round(float(close[idx]), 2),
            "description": info.get("description", ""),
            "industry": info.get("industry", ""),
            "sub_sector": info.get("sub_sector", ""),
            "sub_rank": info.get("sub_rank", 0),
        })

    scores.sort(key=lambda x: x["x1"], reverse=True)
    for i, item in enumerate(scores):
        item["rank"] = i + 1
    return scores


def build_us_x1_history(start_date: str = None, end_date: str = None):
    """回填 US 明星股 x₁ 历史（增量 resume）"""
    if start_date is None:
        from datetime import timedelta
        start_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    US_X1_DAILY_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: 批量获取数据
    stock_data = fetch_all_us_stock_data()

    # Step 2: 预计算 x₁ 序列
    print(f"\n  预计算 x₁ 序列 ({len(stock_data)} 只)...")
    x1_cache = {}
    for symbol, info in stock_data.items():
        x1_cache[symbol] = calc_index_x1(info["close"], info["volume"])
    print("  预计算完成")

    # Step 3: 获取交易日并过滤范围
    all_dates = get_us_trading_dates(stock_data)
    start_int, end_int = int(start_date), int(end_date)
    target_dates = [d for d in all_dates if start_int <= d <= end_int]
    print(f"  回填范围: {start_date} ~ {end_date} ({len(target_dates)} 个交易日)")

    # Step 4: 增量 resume — 跳过已有文件
    existing = sorted([f.replace(".json", "") for f in os.listdir(US_X1_DAILY_DIR)
                       if f.endswith(".json")])
    if existing:
        last_done = int(existing[-1])
        before = len(target_dates)
        target_dates = [d for d in target_dates if d > last_done]
        print(f"  已有 {len(existing)} 天，跳过 {before - len(target_dates)} 天")

    # Step 5: 逐日计算
    for i, date_int in enumerate(target_dates):
        date_str = str(date_int)
        top = compute_us_x1_for_date(stock_data, date_int, x1_cache)

        output = {
            "date": date_str,
            "total_stocks": len(stock_data),
            "n_valid": len(top),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stocks": top,
        }
        with open(US_X1_DAILY_DIR / f"{date_str}.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        if (i + 1) % 10 == 0 or i == len(target_dates) - 1:
            print(f"  进度: {i + 1}/{len(target_dates)} 天")

    print(f"  ✅ 回填完成: {len(target_dates)} 天 → {US_X1_DAILY_DIR}")


def export_us_x1_pivot(output_path: str = None):
    """从 daily JSON 导出透视 CSV（列=日期，行=标的）"""
    import pandas as pd
    if output_path is None:
        output_path = US_X1_HISTORY_DIR / "us_x1_pivot.csv"

    files = sorted([f for f in os.listdir(US_X1_DAILY_DIR) if f.endswith(".json")])
    if not files:
        print("⚠  没有历史数据，先运行 --backfill")
        return

    rows = {}  # symbol → {date: x1, ...}
    for fname in files:
        date_str = fname.replace(".json", "")
        with open(US_X1_DAILY_DIR / fname, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data.get("stocks", []):
            sym = item["symbol"]
            if sym not in rows:
                rows[sym] = {
                    "name": item.get("name", ""),
                    "cn_name": item.get("cn_name", ""),
                    "category": item.get("category", ""),
                }
            rows[sym][date_str] = item.get("x1")

    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "symbol"
    df.to_csv(output_path, encoding="utf-8-sig")
    print(f"  ✅ 透视 CSV: {output_path}  ({len(rows)} stocks × {len(files)} days)")


def compute_us_x1_thresholds():
    """基于历史数据计算 x₁ 分布阈值"""
    files = sorted([f for f in os.listdir(US_X1_DAILY_DIR) if f.endswith(".json")])
    if not files:
        print("⚠  没有历史数据，先运行 --backfill")
        return

    all_x1 = []
    for fname in files:
        with open(US_X1_DAILY_DIR / fname, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data.get("stocks", []):
            x1 = item.get("x1")
            if x1 is not None:
                all_x1.append(x1)

    arr = np.array(all_x1)
    print(f"  US x₁ 分布统计 ({len(arr)} obs, {len(files)} days):")
    print(f"    均值={np.mean(arr):.2f}  标准差={np.std(arr):.2f}")
    for p in [25, 50, 75, 85, 90, 95, 99]:
        print(f"    P{p}={np.percentile(arr, p):.2f}")

    # Proposed thresholds
    print(f"\n  建议阈值:")
    print(f"    mild (P75):   {np.percentile(arr, 75):.1f}")
    print(f"    elevated (P90): {np.percentile(arr, 90):.1f}")
    print(f"    strong (P95):  {np.percentile(arr, 95):.1f}")


# ── 信号检测 ──
def _load_stock_x1_history(symbol: str, n_days: int = 30) -> list[float]:
    """从 daily JSON 读取某个标的历史 x₁ 序列"""
    files = sorted([f for f in os.listdir(US_X1_DAILY_DIR) if f.endswith(".json")])
    values = []
    for fname in files[-n_days:]:
        with open(US_X1_DAILY_DIR / fname, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data.get("stocks", []):
            if item["symbol"] == symbol:
                values.append(item.get("x1"))
                break
    return [v for v in values if v is not None]


def detect_stock_signals(current_x1: float, history: list[float],
                         prev_x1: float = None,
                         thresholds: dict = None) -> dict:
    """检测个股信号：快速变化、连续走强、阈值穿越、强弱标签"""
    if thresholds is None:
        thresholds = US_X1_THRESHOLDS
    if current_x1 is None:
        return {"x1_label": "无数据"}

    signals = {}
    history = [h for h in history if h is not None]

    # x1 日变化
    if prev_x1 is not None:
        delta = current_x1 - prev_x1
        signals["x1_delta"] = round(delta, 2)
        # 快速变化
        if len(history) >= 5:
            std = np.std(history[-5:]) if len(history) >= 5 else 1.0
            if std > 0 and abs(delta) > 2 * std:
                signals["fast_change"] = "up" if delta > 0 else "down"
    else:
        signals["x1_delta"] = 0

    # 连续走强
    if len(history) >= 3:
        recent = list(history[-3:]) + [current_x1]
        if all(recent[i] < recent[i + 1] for i in range(3)):
            signals["consecutive_rise"] = 3
    if len(history) >= 4 and signals.get("consecutive_rise") != 3:
        recent = list(history[-4:]) + [current_x1]
        if all(recent[i] < recent[i + 1] for i in range(4)):
            signals["consecutive_rise"] = 4

    # 阈值穿越
    if len(history) >= 1:
        prev = history[-1]
        for level_name, level_val in sorted(thresholds.items(), key=lambda x: x[1]):
            if prev < level_val <= current_x1:
                signals["threshold_cross"] = {"level": level_name, "direction": "up", "value": level_val}
                break
            elif prev >= level_val > current_x1:
                signals["threshold_cross"] = {"level": level_name, "direction": "down", "value": level_val}
                break

    # 强弱标签
    if current_x1 >= thresholds["strong"]:
        signals["x1_label"] = "强势"
    elif current_x1 >= thresholds["elevated"]:
        signals["x1_label"] = "显著走强"
    elif current_x1 >= thresholds["mild"]:
        signals["x1_label"] = "走强启动"
    else:
        signals["x1_label"] = "弱势/中性"

    return signals


def _compute_sub_sector_ranking(results: list[dict]) -> list[dict]:
    """按子行业分组，x₁ 降序，返回 Top 3 龙头排名"""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in results:
        sub = r.get("sub_sector", "")
        if sub:
            groups[sub].append(r)

    ranking = []
    for sub_sector, stocks in sorted(groups.items()):
        stocks.sort(key=lambda s: s.get("x1", -999), reverse=True)
        top3 = []
        for i, s in enumerate(stocks[:3]):
            top3.append({
                "symbol": s["symbol"],
                "name": s["name"],
                "cn_name": s.get("cn_name", ""),
                "x1": s.get("x1"),
                "rank": i + 1,
                "sub_rank": s.get("sub_rank", 0),
            })
        valid_x1 = [s["x1"] for s in top3 if s["x1"] is not None]
        avg_x1 = round(sum(valid_x1) / len(valid_x1), 2) if valid_x1 else 0
        ranking.append({
            "sub_sector": sub_sector,
            "industry": stocks[0].get("industry", ""),
            "top3": top3,
            "avg_x1": avg_x1,
        })
    return ranking


def _collect_signals(results: list[dict]) -> dict:
    """聚合全局信号摘要"""
    fast_movers = []
    consecutive = []
    crossings = []

    for r in results:
        sig = r.get("signals", {})
        if sig.get("fast_change"):
            fast_movers.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "cn_name": r.get("cn_name", ""),
                "direction": sig["fast_change"],
                "delta": sig.get("x1_delta", 0),
            })
        if sig.get("consecutive_rise"):
            consecutive.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "cn_name": r.get("cn_name", ""),
                "days": sig["consecutive_rise"],
            })
        if sig.get("threshold_cross"):
            crossings.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "cn_name": r.get("cn_name", ""),
                "cross": sig["threshold_cross"],
            })

    return {
        "fast_movers": fast_movers,
        "consecutive_strength": consecutive,
        "threshold_crossings": crossings,
    }




def calc_all_us_stock_scores() -> list[dict]:
    """遍历所有明星股，计算 X_1 势能评分"""
    results = []
    total = sum(len(v) for v in US_STAR_STOCKS.values())

    for cat, stocks in US_STAR_STOCKS.items():
        for symbol, name in stocks.items():
            print(f"  [{symbol}] {name} ...", end=" ", flush=True)
            arrs = fetch_stock_daily(symbol)
            if arrs is None:
                print("无数据/数据不足")
                results.append({
                    "symbol": symbol, "name": name, "category": cat,
                    "x1": None, "close": None, "n_days": 0, "error": "无数据"
                })
                continue

            close, volume = arrs
            x1 = calc_index_x1(close, volume)
            latest_close = float(close[-1])
            # 每日涨跌幅
            daily_chg = round((close[-1] / close[-2] - 1) * 100, 2) if len(close) >= 2 else 0
            # 周涨跌幅（5个交易日）
            week_chg = round((close[-1] / close[-6] - 1) * 100, 2) if len(close) >= 6 else 0
            # 月涨跌幅（21个交易日）
            month_chg = round((close[-1] / close[-22] - 1) * 100, 2) if len(close) >= 22 else week_chg
            # 季涨跌幅（63个交易日）
            quarter_chg = round((close[-1] / close[-64] - 1) * 100, 2) if len(close) >= 64 else month_chg
            vol_ratio = 0
            if len(volume) >= 21:
                avg_vol = float(np.mean(volume[-21:-1]))
                vol_ratio = round(volume[-1] / avg_vol, 2) if avg_vol > 0 else 0
            print(f"X_1={x1:.2f}  日涨跌={daily_chg:+.2f}%  周={week_chg:+.2f}%  月={month_chg:+.2f}%  收盘={latest_close:.2f}")
            results.append({
                "symbol": symbol,
                "name": name,
                "category": cat,
                "x1": round(x1, 2),
                "close": latest_close,
                "daily_chg": daily_chg,
                "week_chg": week_chg,
                "month_chg": month_chg,
                "quarter_chg": quarter_chg,
                "vol_ratio": vol_ratio,
                "n_days": len(close),
            })
            time.sleep(0.3)

    return results


def report_stock_rankings(results: list[dict], category: str = None, show_all: bool = False):
    """终端打印排名 — 全量 + 分类Top"""
    valid = [r for r in results if r.get("x1") is not None]
    valid.sort(key=lambda r: r["x1"], reverse=True)

    print("\n" + "=" * 90)
    print(f"  US 明星股动量排名 (RSI势能2 X_1)")
    print("=" * 90)
    print(f"{'排名':<5} {'代码':<8} {'名称':<22} {'类别':<22} {'X_1':>7} {'收盘':>10}")
    print("-" * 90)

    display = [r for r in valid if not category or r["category"] == category]
    if not category and not show_all:
        display = display[:30]

    for i, r in enumerate(display, 1):
        print(f"{i:<5} {r['symbol']:<8} {r['name']:<22} {r['category']:<22} {r['x1']:>7.2f} {r['close']:>10.2f}")

    if not category and not show_all:
        print(f"\n  ... (共 {len(valid)} 只有效，显示 Top 30)")

    # Category summary
    print(f"\n{'='*90}")
    print(f"  类别轮动快照")
    print(f"{'='*90}")
    cat_scores = defaultdict(list)
    for r in valid:
        cat_scores[r["category"]].append(r["x1"])
    print(f"  {'类别':<26} {'只数':>4} {'平均':>7} {'最强':>22} {'最弱':>22}")
    print(f"  {'-'*80}")
    for cat in cat_scores:
        scores = cat_scores[cat]
        avg = sum(scores) / len(scores)
        cat_stocks = [r for r in valid if r["category"] == cat]
        best = max(cat_stocks, key=lambda r: r["x1"])
        worst = min(cat_stocks, key=lambda r: r["x1"])
        print(f"  {cat:<26} {len(scores):>4} {avg:>7.2f} {best['symbol']}({best['x1']:.1f}){'':>14} {worst['symbol']}({worst['x1']:.1f})")

    failed = [r for r in results if r.get("x1") is None]
    if failed:
        print(f"\n  失败 ({len(failed)}): {', '.join(r['symbol'] for r in failed)}")


def report_top_movers(results: list[dict], n: int = 10):
    """打印每日涨跌榜 Top N"""
    valid = [r for r in results if r.get("x1") is not None and r.get("daily_chg") is not None]
    gainers = sorted(valid, key=lambda r: r.get("daily_chg", 0), reverse=True)[:n]
    losers = sorted(valid, key=lambda r: r.get("daily_chg", 0))[:n]

    print("\n" + "=" * 70)
    print("  US 明星股每日涨幅 Top %d" % n)
    print("=" * 70)
    print(f"  {'代码':<8} {'名称':<15} {'类别':<22} {'涨幅':>7} {'X_1':>6} {'量比':>6}")
    for r in gainers:
        print(f"  {r['symbol']:<8} {r['name']:<15} {r['category']:<22} {r['daily_chg']:>+7.2f}% {r['x1']:>6.1f} {r.get('vol_ratio',0):>6.2f}")

    print("\n" + "=" * 70)
    print("  US 明星股每日跌幅 Top %d" % n)
    print("=" * 70)
    print(f"  {'代码':<8} {'名称':<15} {'类别':<22} {'跌幅':>7} {'X_1':>6} {'量比':>6}")
    for r in losers:
        print(f"  {r['symbol']:<8} {r['name']:<15} {r['category']:<22} {r['daily_chg']:>+7.2f}% {r['x1']:>6.1f} {r.get('vol_ratio',0):>6.2f}")


def _load_prev_stock_x1() -> dict:
    """加载前一天个股 x1 值用于趋势对比"""
    path = TRACKING_DIR / "_macro" / "us_star_momentum.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {e["symbol"]: e.get("x1") for e in data.get("stocks", []) if e.get("x1") is not None}
    except Exception:
        return {}


def save_stock_results(results: list[dict], date_str: str = None):
    """保存增强版 JSON (v2 + signals + sub_sector_ranking) + Markdown + 概念链"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    US_X1_DAILY_DIR.mkdir(parents=True, exist_ok=True)

    # ── 补充中文名/描述 ──
    results = _enrich_with_names(results)
    valid = [r for r in results if r.get("x1") is not None]

    # ── x1 趋势对比 + 信号检测 ──
    prev_x1 = _load_prev_stock_x1()
    x1_history_dir = US_X1_DAILY_DIR
    if x1_history_dir.exists():
        for r in valid:
            symbol = r["symbol"]
            prev = prev_x1.get(symbol)
            if prev is not None and r["x1"] is not None:
                r["x1_delta"] = round(r["x1"] - prev, 2)
            else:
                r["x1_delta"] = 0

            # 从历史文件加载 x1 序列
            hist = _load_stock_x1_history(symbol, n_days=20) if x1_history_dir.exists() else []
            sig = detect_stock_signals(r["x1"], hist, prev)
            r["signals"] = sig
            r["x1_label"] = sig.get("x1_label", "")
            if "x1_delta" not in r and sig.get("x1_delta") is not None:
                r["x1_delta"] = sig["x1_delta"]
    else:
        # 无历史数据，只做简单趋势
        for r in valid:
            symbol = r["symbol"]
            prev = prev_x1.get(symbol)
            if prev is not None and r["x1"] is not None:
                diff = r["x1"] - prev
                r["x1_trend"] = "up" if diff > 0.5 else ("down" if diff < -0.5 else "flat")
                r["x1_delta"] = round(diff, 2)
            else:
                r["x1_trend"] = "new"
                r["x1_delta"] = 0
            r["signals"] = {}
            r["x1_label"] = ""

    # ── 子行业排名 ──
    sub_ranking = _compute_sub_sector_ranking(valid)

    # ── 聚合信号 ──
    agg_signals = _collect_signals(valid)

    # ── 分布统计 ──
    x1_values = [r["x1"] for r in valid if r["x1"] is not None]
    dist_summary = {}
    if x1_values:
        arr = np.array(x1_values)
        dist_summary = {
            "mean": round(float(np.mean(arr)), 2),
            "std": round(float(np.std(arr)), 2),
            "p75": round(float(np.percentile(arr, 75)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "p95": round(float(np.percentile(arr, 95)), 2),
        }

    # ═══ JSON v2 ═══
    json_path = TRACKING_DIR / "_macro" / "us_star_momentum.json"
    payload = {
        "date": date_str,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": 2,
        "total_stocks": len(results),
        "n_valid": len(valid),
        "thresholds": US_X1_THRESHOLDS,
        "distribution_summary": dist_summary,
        "sub_sector_ranking": sub_ranking,
        "signals": agg_signals,
        "stocks": results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n[JSON v2] {json_path}")

    # ── 今日快照 → 历史目录 ──
    if valid:
        snapshot = {
            "date": date_str,
            "total_stocks": len(results),
            "n_valid": len(valid),
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stocks": [{
                "symbol": r["symbol"],
                "name": r["name"],
                "cn_name": r.get("cn_name", ""),
                "category": r["category"],
                "x1": r["x1"],
                "x1_label": r.get("x1_label", ""),
                "x1_delta": r.get("x1_delta", 0),
                "description": r.get("description", ""),
                "industry": r.get("industry", ""),
                "sub_sector": r.get("sub_sector", ""),
                "sub_rank": r.get("sub_rank", 0),
                "close": r.get("close"),
                "daily_chg": r.get("daily_chg"),
                "signals": {k: v for k, v in r.get("signals", {}).items()
                            if k in ("fast_change", "consecutive_rise", "threshold_cross")},
            } for r in valid],
        }
        snapshot_path = US_X1_DAILY_DIR / f"{date_str}.json"
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        print(f"[SNAP] {snapshot_path}")

    # ── 概念链动量 ──
    from tools.us_market.concept_chains import compute_chain_momentum, print_concept_ranking
    chain_scores = compute_chain_momentum(star_scores=results)
    if chain_scores:
        chain_json_path = TRACKING_DIR / "_macro" / "us_concept_momentum.json"
        chain_payload = {
            "date": date_str,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "n_concepts": len(chain_scores),
            "thresholds": US_X1_THRESHOLDS,
            "chains": [{
                "chain": c["chain"],
                "type": c["type"],
                "avg_x1": c["avg_x1"],
                "n_valid": c["n_valid"],
                "top3": [{"symbol": s, "x1": x} for s, x in c["top3"]],
                "bottom3": [{"symbol": s, "x1": x} for s, x in c["bottom3"]],
            } for c in chain_scores],
        }
        with open(chain_json_path, "w", encoding="utf-8") as f:
            json.dump(chain_payload, f, ensure_ascii=False, indent=2)
        print(f"[JSON] {chain_json_path}")
        print_concept_ranking(chain_scores)

    # ── Markdown 报告 ──
    md_path = REPORT_DIR / f"{date_str}_us_stars.md"
    trend_arrow = {"up": "↑", "down": "↓", "flat": "→", "new": "☆"}

    lines = [
        f"# US 明星股日报 ({date_str})",
        "",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**标的**: {len(results)} 只 (有效: {len(valid)})",
        "",
    ]

    # 信号摘要
    if agg_signals.get("fast_movers") or agg_signals.get("consecutive_strength") or agg_signals.get("threshold_crossings"):
        lines.append("## ⚡ 信号摘要\n")
        if agg_signals["fast_movers"]:
            fm = agg_signals["fast_movers"][:5]
            lines.append("**快速变化**: " + " / ".join(f"{s['symbol']}({s['direction']})" for s in fm))
        if agg_signals["consecutive_strength"]:
            cs = agg_signals["consecutive_strength"][:5]
            lines.append("  \n**连续走强**: " + " / ".join(f"{s['symbol']}({s['days']}天)" for s in cs))
        if agg_signals["threshold_crossings"]:
            tc = agg_signals["threshold_crossings"][:5]
            lines.append("  \n**阈值穿越**: " + " / ".join(
                f"{s['symbol']}({s['cross']['level']}/{s['cross']['direction']})" for s in tc))
        lines.append("")

    # 子行业龙头
    if sub_ranking:
        lines.append("## 🏆 子行业龙头排名\n")
        lines.append("| 子行业 | 龙一 | X₁ | 龙二 | X₁ | 龙三 | X₁ |")
        lines.append("|--------|------|-----|------|-----|------|-----|")
        for sr in sub_ranking[:15]:  # Top 15 sub-sectors
            t = sr["top3"]
            r1 = f"{t[0]['symbol']}({t[0]['cn_name']})" if len(t) > 0 else "-"
            r1x = f"{t[0]['x1']:.1f}" if len(t) > 0 and t[0]['x1'] is not None else "-"
            r2 = f"{t[1]['symbol']}({t[1]['cn_name']})" if len(t) > 1 else "-"
            r2x = f"{t[1]['x1']:.1f}" if len(t) > 1 and t[1]['x1'] is not None else "-"
            r3 = f"{t[2]['symbol']}({t[2]['cn_name']})" if len(t) > 2 else "-"
            r3x = f"{t[2]['x1']:.1f}" if len(t) > 2 and t[2]['x1'] is not None else "-"
            lines.append(f"| {sr['sub_sector']} | {r1} | {r1x} | {r2} | {r2x} | {r3} | {r3x} |")
        lines.append("")

    # ① 当日异动 Top 3
    movers = sorted(valid, key=lambda r: r.get("daily_chg", 0) or 0, reverse=True)[:3]
    lines.append("## 🚀 当日异动 Top 3\n")
    lines.append("| 排名 | 代码 | 名称 | 类别 | 日涨跌 | 周涨跌 | 月涨跌 | 中文名 | 说明 |")
    lines.append("|------|------|------|------|--------|--------|--------|--------|------|")
    for i, r in enumerate(movers, 1):
        dc = f"{r.get('daily_chg', 0):+.2f}%" if r.get("daily_chg") is not None else "?"
        wc = f"{r.get('week_chg', 0):+.2f}%" if r.get("week_chg") is not None else "?"
        mc = f"{r.get('month_chg', 0):+.2f}%" if r.get("month_chg") is not None else "?"
        cn = r.get("cn_name", "")
        desc = r.get("description", "")
        lines.append(f"| {i} | {r['symbol']} | {r['name']} | {r['category']} | {dc} | {wc} | {mc} | {cn} | {desc} |")
    lines.append("")

    # ② x1 势能强度 Top 5
    x1_top = sorted(valid, key=lambda r: r["x1"], reverse=True)[:5]
    lines.append("## 📊 x₁ 势能强度 Top 5\n")
    lines.append("| 排名 | 代码 | 中文名 | 类别 | x₁ | 标签 | Δx₁ | 说明 |")
    lines.append("|------|------|--------|------|-----|------|-----|------|")
    for i, r in enumerate(x1_top, 1):
        label = r.get("x1_label", r.get("x1_trend", ""))
        delta = f"{r.get('x1_delta', 0):+.1f}"
        desc = r.get("description", "")
        lines.append(f"| {i} | {r['symbol']} | {r.get('cn_name', '')} | {r['category']} | {r['x1']:.1f} | {label} | {delta} | {desc} |")
    lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[MD]   {md_path}")

    # 概念链日报
    if chain_scores:
        md_chain_path = REPORT_DIR / f"{date_str}_us_concept_rotation.md"
        chain_lines = [
            f"# US 概念链轮动日报 ({date_str})",
            "",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"**概念链**: {len(chain_scores)} 条 (基于ETF持仓 + 人工维护)",
            "",
            "## 概念链动量排名",
            "",
            "| 排名 | 概念链 | 类型 | 成分 | 有效 | 均X₁ | Top 3 领涨股 |",
            "|------|--------|------|------|------|------|------------|",
        ]
        for i, c in enumerate(chain_scores, 1):
            top_str = " / ".join(f"{s}({x:+.1f})" for s, x in c["top3"])
            chain_lines.append(f"| {i} | {c['chain']} | {c['type']} | {c['n_stocks']} | {c['n_valid']} | {c['avg_x1']:.2f} | {top_str} |")
        chain_lines.extend([
            "",
            "## 概念链接入说明",
            "",
            "- **etf_holdings**: ETF发行商维护的持仓数据自动构建",
            "- **manual**: 人工整理的产业链/主题链（不定期更新）",
            "- **新增概念链**: 编辑 `tools/us_market/concept_chains.json`，在 `concepts` 下加一条即可",
            "",
        ])
        with open(md_chain_path, "w", encoding="utf-8") as f:
            f.write("\n".join(chain_lines) + "\n")
        print(f"[MD]   {md_chain_path}")

    # 终端信号摘要
    print(f"\n  信号摘要:")
    if agg_signals.get("fast_movers"):
        print(f"    ⚡ 快速变化: {len(agg_signals['fast_movers'])} 只")
    if agg_signals.get("consecutive_strength"):
        print(f"    📈 连续走强: {len(agg_signals['consecutive_strength'])} 只")
    if agg_signals.get("threshold_crossings"):
        print(f"    🚩 阈值穿越: {len(agg_signals['threshold_crossings'])} 只")


def search_stock(results: list[dict], keyword: str):
    """搜索个股（含中文名）"""
    keyword = keyword.upper()
    for r in results:
        if (keyword in r["symbol"]
                or keyword.lower() in r["name"].lower()
                or keyword.lower() in r.get("cn_name", "").lower()):
            status = f"X_1={r['x1']:.2f}" if r.get("x1") is not None else "无数据"
            cn = r.get("cn_name", "")
            desc = r.get("description", "")
            label = r.get("x1_label", "")
            print(f"  {r['symbol']} {r['name']} ({cn}) [{r['category']}] {status}  {label}  收盘={r.get('close', '?')}")
            if desc:
                print(f"    → {desc}")


def main():
    parser = argparse.ArgumentParser(description="US 明星股动量评分 (RSI势能2)")
    parser.add_argument("--save", action="store_true", help="保存 JSON v2 + Markdown")
    parser.add_argument("--search", type=str, help="搜索指定个股")
    parser.add_argument("--category", type=str, help="仅显示指定类别")
    parser.add_argument("--date", type=str, help="日期标签 (默认今天)")
    parser.add_argument("--movers", action="store_true", help="显示每日涨跌榜")
    parser.add_argument("--movers-top", type=int, default=10, help="涨跌榜显示数 (默认10)")
    parser.add_argument("--backfill", action="store_true", help="回填 US x₁ 历史数据")
    parser.add_argument("--backfill-start", type=str, default=None, help="回填起始日期 (YYYYMMDD)")
    parser.add_argument("--backfill-end", type=str, default=None, help="回填截止日期 (YYYYMMDD)")
    parser.add_argument("--export-pivot", action="store_true", help="导出 x₁ 透视 CSV")
    parser.add_argument("--thresholds", action="store_true", help="计算 x₁ 阈值分布")
    parser.add_argument("--list", action="store_true", help="打印完整排名（不截断）")
    args = parser.parse_args()

    # 路由: 回填
    if args.backfill:
        build_us_x1_history(start_date=args.backfill_start, end_date=args.backfill_end)
        return

    # 路由: 透视
    if args.export_pivot:
        export_us_x1_pivot()
        return

    # 路由: 阈值分布
    if args.thresholds:
        compute_us_x1_thresholds()
        return

    # 日常模式
    total = sum(len(v) for v in US_STAR_STOCKS.values())
    print(f"US 明星股动量评分 v2.0 — RSI势能2 镜像")
    print(f"标的: {total} 只")
    print(f"开始拉取...\n")

    results = calc_all_us_stock_scores()

    if args.search:
        search_stock(results, args.search)
    elif args.movers:
        report_top_movers(results, args.movers_top)
    elif args.list:
        report_stock_rankings(results, args.category, show_all=True)
    else:
        report_stock_rankings(results, args.category)

    if args.save:
        save_stock_results(results, args.date)


if __name__ == "__main__":
    main()

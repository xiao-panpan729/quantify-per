# -*- coding: utf-8 -*-
"""
事件标注映射器 — 将按月搜索到的历史事件映射回节点窗口
====================================================

用法:
  from tools.event_mapper import EventMapper
  em = EventMapper()
  em.apply_month_events("2024-02", ["LPR降息: 5年期LPR从4.20%降至3.95%",
                                     "证监会限制卖出: 限制机构早盘/尾盘净卖出"])
  em.save()
"""

import json
import time
import copy
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
NODE_MAP_PATH = PROJECT_ROOT / "signals" / "tracking" / "_macro" / "node_map.json"
BACKUP_DIR = PROJECT_ROOT / "signals" / "tracking" / "_macro" / "backups"

# 9 类事件关键词映射
EVENT_CATEGORIES = {
    "地缘冲突": ["地缘", "冲突", "战争", "军事", "制裁", "台海", "南海", "俄乌", "中东", "巴以",
                 "领土", "争端", "核试验", "导弹", "出兵", "入侵", "武装", "炸"],
    "贸易摩擦": ["关税", "贸易战", "反倾销", "301", "实体清单", "出口管制", "科技封锁",
                 "脱钩", "供应链", "断供", "禁运"],
    "金融制裁": ["金融制裁", "冻结资产", "SWIFT", "剔除", "黑名单", "OFAC"],
    "货币利率冲击": ["加息", "降息", "LPR", "MLF", "SLF", "逆回购", "RRR", "降准", "利率",
                   "存款准备金", "美联储", "加息周期", "量化宽松", "缩表"],
    "监管风暴": ["监管", "整顿", "罚款", "反垄断", "审查", "数据安全", "网络安全",
                "双减", "教培", "房地产调控", "三条红线", "资管新规", "去杠杆",
                "IPO收紧", "减持", "限制卖出", "量化交易限制"],
    "流动性危机": ["流动性", "挤兑", "爆仓", "质押", "违约", "评级下调", "债务危机",
                  "雪球", "敲入", "下跌", "赎回", "钱荒"],
    "产业政策利好": ["产业政策", "补贴", "规划", "数字经济", "人工智能", "芯片", "新能源",
                   "光伏", "电动车", "碳中和", "双碳", "储能", "特高压", "信创",
                   "东数西算", "新质生产力", "设备更新", "以旧换新", "低空经济"],
    "突发公共事件": ["疫情", "新冠", "封控", "封锁", "地震", "洪水", "灾害", "事故",
                   "传染", "病毒"],
    "大宗商品冲击": ["油价", "原油", "大宗商品", "期货", "暴涨", "暴跌", "供应",
                   "能源危机", "粮食", "黄金", "铜", "锂", "硅料"],
}


class EventMapper:
    """事件映射器 — 按月接受搜索到的历史事件，匹配到节点窗口"""

    def __init__(self, node_map_path=None):
        self.path = Path(node_map_path) if node_map_path else NODE_MAP_PATH
        with open(self.path, "r", encoding="utf-8") as f:
            self.node_map = json.load(f)
        self.dirty = False
        self._stats = {"total_nodes": 0, "labeled_nodes": set()}
        self._fix_null_event_labels()
        self._precompute_window_lookup()

    def _fix_null_event_labels(self):
        """将 JSON 中 event_label: null 全部初始化为 []"""
        fixed = 0
        for sector in self.node_map.get("sectors", []):
            for node in sector.get("nodes", []):
                ctx = node.get("context")
                if ctx and ctx.get("event_label") is None:
                    ctx["event_label"] = []
                    fixed += 1
        if fixed:
            self.dirty = True

    def _precompute_window_lookup(self):
        """预计算所有节点的窗口日期范围，加速匹配"""
        self._node_index = []  # [(sector_idx, node_idx, start_dt, end_dt, grade), ...]
        for si, sector in enumerate(self.node_map.get("sectors", [])):
            for ni, node in enumerate(sector.get("nodes", [])):
                grade = node.get("quality", {}).get("grade", "D")
                if grade not in ("A", "B"):
                    continue
                w = node.get("window", "")
                if not w or "-" not in w:
                    continue
                parts = w.split("-")
                if len(parts) < 6:
                    continue
                start = "-".join(parts[:3])
                end = "-".join(parts[3:6])
                self._node_index.append((si, ni, start, end, grade))
        self._stats["total_nodes"] = len(self._node_index)

    def _classify_event(self, event_text: str) -> list[str]:
        """根据事件文本分类到1-2个类别"""
        matched = []
        for category, keywords in EVENT_CATEGORIES.items():
            if any(kw in event_text for kw in keywords):
                matched.append(category)
        return matched if matched else ["其他"]

    def _month_overlaps(self, event_month: str, node_start: str, node_end: str) -> bool:
        """判断 event_month (YYYY-MM) 是否与节点窗口 [start, end] 重叠"""
        return event_month >= node_start[:7] and event_month <= node_end[:7]

    def apply_month_events(self, year_month: str, event_texts: list[str],
                           source_urls: list[str] | None = None) -> int:
        """
        应用一个月的事件到所有重叠节点窗口。

        Args:
            year_month: "YYYY-MM" 格式
            event_texts: 事件描述列表
            source_urls: 来源URL列表 (可选，与event_texts对应)

        Returns:
            匹配到的节点数
        """
        if not event_texts:
            return 0

        # 解析事件
        events = []
        for i, text in enumerate(event_texts):
            categories = self._classify_event(text)
            events.append({
                "date": year_month,
                "event": text.strip(),
                "categories": categories,
                "source": source_urls[i] if source_urls and i < len(source_urls) else "",
            })

        # 匹配节点
        matched = 0
        for si, ni, n_start, n_end, grade in self._node_index:
            if not self._month_overlaps(year_month, n_start, n_end):
                continue

            node = self.node_map["sectors"][si]["nodes"][ni]
            ctx = node.setdefault("context", {})
            if ctx.get("event_label") is None:
                ctx["event_label"] = []
            existing = ctx["event_label"]

            # 合并新事件 (去重)
            existing_texts = {e["event"] for e in existing}
            new_count = 0
            for ev in events:
                if ev["event"] not in existing_texts:
                    existing.append(ev)
                    existing_texts.add(ev["event"])
                    new_count += 1

            if new_count > 0:
                self.dirty = True
                matched += 1
                self._stats["labeled_nodes"].add((si, ni))

        return matched

    def get_stats(self) -> dict:
        """获取当前统计"""
        total = self._stats["total_nodes"]
        labeled = len(self._stats["labeled_nodes"])
        # 计算事件总条数
        total_events = 0
        for si, ni in self._stats["labeled_nodes"]:
            node = self.node_map["sectors"][si]["nodes"][ni]
            total_events += len(node.get("context", {}).get("event_label", []))
        return {
            "total_ab_nodes": total,
            "labeled_nodes": labeled,
            "coverage_pct": round(labeled / total * 100, 1) if total else 0,
            "total_events": total_events,
        }

    def save(self, backup=True):
        """保存更新后的 node_map.json"""
        if not self.dirty:
            print("  [无变更，跳过保存]")
            return

        # 备份
        if backup:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            backup_path = BACKUP_DIR / f"node_map_{ts}.json"
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(self.node_map, f, ensure_ascii=False, indent=None)
            print(f"  备份: {backup_path.name}")

        # 保存
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.node_map, f, ensure_ascii=False, indent=None)
        file_mb = self.path.stat().st_size / 1024 / 1024
        print(f"  保存: {self.path.name} ({file_mb:.1f}MB)")
        self.dirty = False
        print(f"  统计: {self.get_stats()}")

    def print_progress(self, month: str, matched: int):
        stats = self.get_stats()
        print(f"  [{month}] → {matched}个节点匹配 | "
              f"累计: {stats['labeled_nodes']}/{stats['total_ab_nodes']}节点 "
              f"({stats['coverage_pct']}%) | {stats['total_events']}条事件")

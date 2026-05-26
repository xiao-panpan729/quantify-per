# -*- coding: utf-8 -*-
"""
筹码分布数据读取与分析器 v1.0

数据源: D:/筹码峰/ (通达信WINNER筹码峰导出)
格式: CSV — 股票代码,日期,成本价格,价格占比
      每个交易日约151行（0.4元~60元+，每档0.4元）

核心指标:
  - 获利盘比例 (WINNER): 当前价以下所有筹码占比之和
  - 成本集中度 (COST): 聚集90%筹码的价格区间宽度
  - 主峰价格/占比: 筹码最密集的价位
  - 筹码峰偏移: 主峰 vs 当前价的偏离方向与幅度
  - 上方套牢盘: 当前价以上筹码占比（压力）
  - 下方支撑盘: 当前价以下筹码占比（支撑）

用法:
  from chip_loader import ChipLoader
  cl = ChipLoader('D:/quantify-per/data/chips')
  
  # 单日分析
  info = cl.analyze('sh600438', '2026-05-06', current_price=22.50)
  print(info.summary())
  
  # 多日对比（看筹码移动趋势）
  trend = cl.trend('sh600438', ['2026-05-04','2026-05-05','2026-05-06'])
"""

import os
import csv
from datetime import datetime
from collections import defaultdict
from config import PROJECT_ROOT

# ============================================================
# 配置
# ============================================================

CHIP_DIR = os.path.join(PROJECT_ROOT, 'data', 'chips')

# 代码 → 文件名前缀映射 (筹码数据文件命名规则)
# v2: code 参数直接就是前缀(如 sh600438)，不再需要映射表
# 保留旧映射作为 fallback
CODE_PREFIX_MAP = {
    'sh600438': 'sh600438',   # 通威股份
    'sh601012': 'sh601012',   # 隆基绿能
    'sz000100': 'sz000100',   # TCL科技
    'sz002129': 'sz002129',   # TCL中环
    'sz002261': 'sz002261',   # 拓维信息
    'sz300118': 'sz300118',   # 东方日升
}


class ChipLoader:
    """筹码分布加载器 + 分析器"""
    
    def __init__(self, base_dir=None):
        self.base_dir = base_dir or CHIP_DIR
        # 缓存: {code: {date_str: [(price, pct), ...]}}
        self._cache = {}
    
    # ---------- 数据读取 ----------
    
    def _find_file(self, code):
        """查找某股票的筹码CSV文件，返回路径列表(可能跨年)
        v2: code 参数直接就是前缀(如 sh600438/sz000001)，无需映射
        """
        # v2: 直接用 code 作为前缀（文件名 = sh600438.csv）
        prefix = CODE_PREFIX_MAP.get(code, code)
        
        files = []
        
        # 1) 年度归档: chips/yearly/YYYY/YYYY/prefix.csv
        yearly_dir = os.path.join(self.base_dir, 'yearly')
        if os.path.isdir(yearly_dir):
            for year_name in sorted(os.listdir(yearly_dir)):
                year_path = os.path.join(yearly_dir, year_name)
                # 支持两种结构: yearly/YYYY/prefix.csv 或 yearly/YYYY/YYYY/prefix.csv
                for candidate in [
                    os.path.join(year_path, f'{prefix}.csv'),
                    os.path.join(year_path, year_name, f'{prefix}.csv'),
                ]:
                    if os.path.isfile(candidate):
                        files.append(candidate)
                        break
        
        # 2) 每日数据: chips/daily/YYYYMMDD/prefix.csv (优先级最高，覆盖年度)
        daily_dir = os.path.join(self.base_dir, 'daily')
        if os.path.isdir(daily_dir):
            for day_name in sorted(os.listdir(daily_dir), reverse=True):  # 最近的放后面，覆盖前面的
                day_path = os.path.join(daily_dir, day_name)
                # 支持两种结构: daily/YYYYMMDD/prefix.csv 或 daily/YYYYMMDD/YYYYMMDD/prefix.csv
                for candidate in [
                    os.path.join(day_path, f'{prefix}.csv'),
                    os.path.join(day_path, day_name, f'{prefix}.csv'),
                ]:
                    if os.path.isfile(candidate):
                        files.append(candidate)
                        break
        
        # 排序：年度在前，每日在后（后面的会覆盖前面同日期数据）
        return files
    
    def _load_csv(self, filepath):
        """加载单个CSV文件，返回 {date_str: [(price, pct), ...]}"""
        data = defaultdict(list)
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)  # skip header
            for row in reader:
                if len(row) >= 4:
                    try:
                        date_str = row[1].strip()  # YYYY-MM-DD
                        price = float(row[2])
                        pct = float(row[3])
                        data[date_str].append((price, pct))
                    except (ValueError, IndexError):
                        continue
        return dict(data)
    
    def _ensure_loaded(self, code):
        """确保某股票数据已加载到缓存"""
        if code in self._cache:
            return
        
        files = self._find_file(code)
        all_data = {}
        for fp in files:
            year_data = self._load_csv(fp)
            all_data.update(year_data)  # 后面的覆盖前面的(日常更新优先)
        
        self._cache[code] = all_data
    
    def get_distribution(self, code, date_str):
        """
        获取某日某股的完整筹码分布
        返回: sorted list of (price, pct) 或 None
        """
        self._ensure_loaded(code)
        data = self._cache.get(code, {})
        
        # 支持多种日期格式
        for fmt in [date_str, date_str.replace('-', ''), 
                     date_str.replace('-', '/')]:
            if fmt in data:
                return sorted(data[fmt], key=lambda x: x[0])
        
        # 找最近的交易日
        dates = sorted(data.keys())
        if not dates:
            return None
        target = date_str.replace('-', '')
        # 取 <= target 的最近一天
        best = None
        for d in dates:
            dd = d.replace('-', '')
            if dd <= target:
                best = d
            else:
                break
        if best:
            return sorted(data[best], key=lambda x: x[0])
        return None
    
    def get_available_dates(self, code):
        """获取某股票有数据的所有交易日期"""
        self._ensure_loaded(code)
        return sorted(self._cache.get(code, {}).keys())
    
    # ---------- 核心指标计算 ----------
    
    @staticmethod
    def calc_metrics(distribution, current_price=None):
        """
        从筹码分布计算核心指标
        
        Args:
            distribution: sorted list of (price, pct)
            current_price: 当前价格(可选，用于计算获利盘等)
        
        Returns:
            dict with keys:
              - profit_ratio:     获利盘比例 % (当前价以下筹码占比)
              - loss_ratio:       套牢盘比例 % (当前价以上筹码占比)
              - concentration:    集中度 — 90%筹码聚集的相对区间宽 %
              - concentration_abs:集中度的绝对价格区间(元)
              - peak_price:       主峰价格(筹码最密集价位)
              - peak_pct:         主峰占比%
              - peak_deviation:   主峰偏离度% (正=主峰在当前价上方=被套)
              - support_zone:     支撑区 [低价, 高价]
              - resistance_zone:  压力区 [低价, 高价]
              - total_levels:     价位档数
              - avg_cost:         加权平均成本
        """
        if not distribution or not isinstance(distribution, list):
            return None
        
        prices = [p for p, _ in distribution]
        pcts = [pct for _, pct in distribution]
        total_pct = sum(pcts)
        
        result = {
            'total_levels': len(distribution),
            'total_pct': round(total_pct, 2),
            'min_price': min(prices),
            'max_price': max(prices),
        }
        
        # 加权平均成本
        if total_pct > 0:
            weighted_sum = sum(p * pct for p, pct in distribution)
            result['avg_cost'] = round(weighted_sum / total_pct, 2)
        
        # 主峰
        max_idx = pcts.index(max(pcts))
        result['peak_price'] = prices[max_idx]
        result['peak_pct'] = round(pcts[max_idx], 2)
        
        # 如果给了当前价格，算更多指标
        if current_price is not None and current_price > 0:
            # 获利盘：当前价以下的筹码占比之和
            profit = sum(pct for p, pct in distribution if p < current_price)
            # 套牢盘：当前价以上的筹码占比
            loss = sum(pct for p, pct in distribution if p > current_price)
            
            result['profit_ratio'] = round(profit, 2)
            result['loss_ratio'] = round(loss, 2)
            
            # 主峰偏离度 = (主峰价格 - 当前价) / 当前价 × 100
            dev = (result['peak_price'] - current_price) / current_price * 100
            result['peak_deviation'] = round(dev, 2)
            
            # 成本集中度：90%筹码聚集的最小区间宽度(相对%)
            cumsum = 0
            target = total_pct * 0.9
            start_i = 0
            end_i = len(distribution) - 1
            
            # 找到累计达到5%和95%的区间
            low_5, high_95 = None, None
            cum = 0
            for i, (_, pct) in enumerate(distribution):
                cum += pct
                if low_5 is None and cum >= total_pct * 0.05:
                    low_5 = i
                if high_95 is None and cum >= total_pct * 0.95:
                    high_95 = i
                    break
            
            if low_5 is not None and high_95 is not None:
                conc_abs = prices[high_95] - prices[low_5]
                conc_rel = conc_abs / current_price * 100 if current_price > 0 else 0
                result['concentration_abs'] = round(conc_abs, 2)
                result['concentration'] = round(conc_rel, 2)
                
                result['support_zone'] = (round(prices[low_5], 1), round(result['peak_price'], 1))
                result['resistance_zone'] = (round(prices[high_95], 1), round(prices[-1], 1))
            
            # 状态判断
            if dev > 10:
                result['status'] = '主力被套'  # 主峰在上方，多数人亏
            elif dev < -10:
                result['status'] = '主力获利'  # 主峰在下方，多数人赚
            else:
                result['status'] = '成本均衡'
        
        return result
    
    # ---------- 便捷方法 ----------
    
    def analyze(self, code, date_str, current_price=None):
        """
        完整的单日分析
        Returns: ChipAnalysis object
        """
        dist = self.get_distribution(code, date_str)
        if not dist:
            return None
        metrics = self.calc_metrics(dist, current_price)
        return ChipAnalysis(code, date_str, dist, metrics)
    
    def trend(self, code, date_list, price_map=None):
        """
        多日趋势对比
        date_list: 日期字符串列表 ['2026-05-01', ...]
        price_map: {date_str: current_price} 可选
        """
        results = []
        for d in date_list:
            price = price_map.get(d) if price_map else None
            info = self.analyze(code, d, price)
            if info:
                results.append(info)
        return results
    
    def summary_text(self, code, date_str, current_price=None):
        """快速获取文字摘要"""
        info = self.analyze(code, date_str, current_price)
        if not info:
            return f"无 {code} 在 {date_str} 的筹码数据"
        return info.summary()


class ChipAnalysis:
    """单日筹码分析结果"""
    
    def __init__(self, code, date_str, distribution, metrics):
        self.code = code
        self.date = date_str
        self.distribution = distribution
        self.metrics = metrics or {}
    
    @property
    def profit_ratio(self):
        return self.metrics.get('profit_ratio')
    
    @property
    def loss_ratio(self):
        return self.metrics.get('loss_ratio')
    
    @property
    def peak_price(self):
        return self.metrics.get('peak_price')
    
    @property
    def peak_pct(self):
        return self.metrics.get('peak_pct')
    
    @property
    def concentration(self):
        return self.metrics.get('concentration')
    
    @property
    def status(self):
        return self.metrics.get('status', '未知')
    
    def summary(self):
        """生成可读摘要"""
        m = self.metrics
        lines = [
            f"【{self.code} {self.date} 筹码分析】",
            f"  主峰: {m.get('peak_price', '?')}元 ({m.get('peak_pct', '?')}%)",
        ]
        if 'profit_ratio' in m:
            lines.append(f"  获利盘: {m['profit_ratio']}% | 套牢盘: {m['loss_ratio']}%")
        if 'concentration' in m:
            lines.append(f"  集中度: ±{m.get('concentration_abs', '?')}元 ({m['concentration']}%)")
        if 'avg_cost' in m:
            lines.append(f"  平均成本: {m['avg_cost']}元")
        if 'peak_deviation' in m:
            lines.append(f"  主峰偏移: {m['peak_deviation']}% → {m.get('status', '?')}")
        lines.append(f"  状态判定: {self.status}")
        return '\n'.join(lines)
    
    def to_dict(self):
        return {
            'code': self.code,
            'date': self.date,
            **self.metrics,
        }
    
    def bar_chart(self, width=40, highlight_price=None):
        """
        生成文本柱状图（适合终端/.md展示）
        返回多行字符串
        """
        if not self.distribution:
            return ""
        
        # 只画有意义的部分（占比>0.3%）
        meaningful = [(p, pct) for p, pct in self.distribution if pct >= 0.3]
        if not meaningful:
            return "(无显著筹码)"
        
        max_pct = max(pct for _, pct in meaningful)
        lines = []
        for price, pct in meaningful:
            bar_len = int(pct / max_pct * width)
            block = '█' * bar_len + '░' * (width - bar_len)
            
            marker = ''
            if highlight_price and abs(price - highlight_price) < 0.3:
                marker = ' ◀─现价'
            
            lines.append(f"  {price:6.1f}│{block} {pct:5.2f}%{marker}")
        
        header = f"{'价格':>6s}│{'█'*width} 占比"
        return f"{header}\n" + '\n'.join(reversed(lines))  # 价格从高到低更直观


# ============================================================
# CLI 测试入口
# ============================================================

if __name__ == '__main__':
    import sys
    
    loader = ChipLoader()
    
    # 默认测试: 通威股份 600438 最近一个交易日
    code = sys.argv[1] if len(sys.argv) > 1 else 'sh600438'
    date = sys.argv[2] if len(sys.argv) > 2 else '2026-05-06'
    price = float(sys.argv[3]) if len(sys.argv) > 3 else None
    
    print(f"=== 筹码分析 v1.0 ===\n")
    
    # 分析
    info = loader.analyze(code, date, price)
    if info:
        print(info.summary())
        print()
        print("【筹码分布图】")
        print(info.bar_chart(highlight_price=price))
    else:
        print(f"未找到 {code} 在 {date} 的筹码数据")
        print(f"可用日期: {loader.get_available_dates(code)[-5:]}" if loader.get_available_dates(code) else "无任何数据")

# -*- coding: utf-8 -*-
"""
数据更新配置文件 v3
支持深圳(sz) + 上海(sh) 双市场
路径自适应：自动检测通达信和Python位置
"""
import os
import sys
from pathlib import Path

# ========== 路径自适应 ==========

def find_tdx_path():
    """自动检测通达信安装路径"""
    candidates = [
        r'C:\zd_cjzq',
        r'C:\new_zdzq',
        r'C:\new_dxzq',
        r'C:\new_hxzq',
        r'D:\zd_cjzq',
        r'D:\new_zdzq',
    ]
    for p in candidates:
        if os.path.exists(os.path.join(p, 'vipdoc')):
            return p
    # 如果没找到，返回默认值（后续会报错提示）
    return r'C:\zd_cjzq'

def find_python():
    """自动检测Python解释器"""
    # 1. 优先用当前运行的Python
    return sys.executable

def get_project_root():
    """获取项目根目录（config.py所在目录）"""
    return os.path.dirname(os.path.abspath(__file__))

# 自动检测
TDX_ROOT = find_tdx_path()
PYTHON_PATH = find_python()
PROJECT_ROOT = get_project_root()

# ========== 路径配置 ==========

# 通达信源数据目录（深圳 + 上海）
TDX_SOURCE = {
    'sz': {
        'lday': os.path.join(TDX_ROOT, 'vipdoc', 'sz', 'lday'),
        'lc1': os.path.join(TDX_ROOT, 'vipdoc', 'sz', 'minline'),
        'lc5': os.path.join(TDX_ROOT, 'vipdoc', 'sz', 'fzline'),
    },
    'sh': {
        'lday': os.path.join(TDX_ROOT, 'vipdoc', 'sh', 'lday'),
        'lc1': os.path.join(TDX_ROOT, 'vipdoc', 'sh', 'minline'),
        'lc5': os.path.join(TDX_ROOT, 'vipdoc', 'sh', 'fzline'),
    },
    'gbbq': os.path.join(TDX_ROOT, 'T0002', 'hq_cache', 'gbbq'),
}

# 目标数据目录（量化库，按市场分文件夹）
TARGET_DIR = {
    'sz': {
        'lday': os.path.join(PROJECT_ROOT, 'lday', 'sz'),
        'lc1': os.path.join(PROJECT_ROOT, 'one', 'sz'),
        'lc5': os.path.join(PROJECT_ROOT, 'five', 'sz'),
        'lc15': os.path.join(PROJECT_ROOT, 'fifteen', 'sz'),
        'lc30': os.path.join(PROJECT_ROOT, 'thirty', 'sz'),
        'lc60': os.path.join(PROJECT_ROOT, 'sixty', 'sz'),
        'week': os.path.join(PROJECT_ROOT, 'week', 'sz'),
        'month': os.path.join(PROJECT_ROOT, 'month', 'sz'),
    },
    'sh': {
        'lday': os.path.join(PROJECT_ROOT, 'lday', 'sh'),
        'lc1': os.path.join(PROJECT_ROOT, 'one', 'sh'),
        'lc5': os.path.join(PROJECT_ROOT, 'five', 'sh'),
        'lc15': os.path.join(PROJECT_ROOT, 'fifteen', 'sh'),
        'lc30': os.path.join(PROJECT_ROOT, 'thirty', 'sh'),
        'lc60': os.path.join(PROJECT_ROOT, 'sixty', 'sh'),
        'week': os.path.join(PROJECT_ROOT, 'week', 'sh'),
        'month': os.path.join(PROJECT_ROOT, 'month', 'sh'),
    },
    'gbbq': os.path.join(PROJECT_ROOT, 'gbbq'),
}

# 日志目录
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')

# ========== 更新策略 ==========

# 是否自动扫描所有股票（True=扫描通达信所有股票，False=只更新 STOCK_LIST 中的股票）
# 用户需要每天下载全市场4222只数据，所以保持 True
AUTO_SCAN = True

# 要处理的市场列表
MARKETS = ['sz', 'sh']

# 手动配置的股票代码列表（当 AUTO_SCAN=False 时使用）
# 格式：'sz000001', 'sh520600' 等，需要带市场前缀
STOCK_LIST = [
    # === 市场指数 (2只) ===
    'sh000001',   # 上证指数
    'sz399006',   # 创业板指
    # === 跟踪标的 (12只) ===
    # ETF 类
    'sz159740',   # 恒生科技ETF大成
    'sh520600',   # 港股通汽车ETF广发
    'sh513120',   # 港股创新药ETF广发 (上海51开头ETF)
    'sz159326',   # 电网设备ETF华夏
    'sh513310',   # 中韩半导体ETF
    'sh588200',   # 科创芯片ETF
    # 个股类（华为云/光伏/TCL）
    'sz002261',   # 拓维信息 华为云概念
    'sz300118',   # 东方日升 太空光伏
    'sz000100',   # TCL科技
    'sz002129',   # TCL中环
    'sh600438',   # 通威股份
    'sh601012',   # 隆基绿能
]

# ========== 高级选项 ==========

# 是否跳过周末（True=跳过周六日，False=每天都检查）
SKIP_WEEKEND = True

# 是否显示详细日志（True=详细输出，False=只输出摘要）
VERBOSE = True

# 数据文件扩展名映射
EXTENSIONS = {
    'lday': '.day',
    'lc1': '.lc1',
    'lc5': '.lc5',
    'gbbq': '',         # gbbq 无扩展名
    'lc15': '.lc15',
    'lc30': '.lc30',
    'lc60': '.lc60',
    'week': '.week',
    'month': '.month',
}

# 合成周期配置（从 5 分钟线合成）
# 每个周期需要多少根 5 分钟 K 线
MERGE_CONFIG = {
    'lc15': 3,   # 3 根 5 分钟 → 1 根 15 分钟
    'lc30': 6,   # 6 根 5 分钟 → 1 根 30 分钟
    'lc60': 12,  # 12 根 5 分钟 → 1 根 60 分钟
}

# ========== 跟踪标的中文名称映射 ==========

# 用于 update_tracking.py 写入 latest.json 时附带中文名
# 新增或改名的标的，只需在这里维护即可
NAME_MAP = {
    'sh000001': '上证指数',
    'sz399006': '创业板指',
    'sz159740': '恒生科技ETF大成',
    'sh520600': '港股通汽车ETF广发',
    'sh513120': '港股创新药ETF广发',
    'sz159326': '电网设备ETF华夏',
    'sh513310': '中韩半导体ETF',
    'sh588200': '科创芯片ETF',
    'sz002261': '拓维信息',
    'sz300118': '东方日升',
    'sz000100': 'TCL科技',
    'sz002129': 'TCL中环',
    'sh600438': '通威股份',
    'sh601012': '隆基绿能',
}

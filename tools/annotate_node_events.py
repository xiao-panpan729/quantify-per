# -*- coding: utf-8 -*-
"""
annotate_node_events.py — 节点产业政策事件标注

为 node_map.json 中 2024+ 的 A/B 级节点，标注对应的板块产业政策事件。
基于 tools.research_report (东方财富研报 API) 的行业研报数据。

流程:
  1. 加载 node_map.json, 过滤 2024+ 节点
  2. 通达信概念板块 → 东财行业代码 映射
  3. 按行业代码批量拉取研报 (本地缓存,避免重复请求)
  4. 对每个节点: 匹配行业 → 筛选时间窗口 → 政策关键词过滤 → 标注
  5. 写回 node_map.json

用法:
  python tools/annotate_node_events.py                    # 标注全部2024+ A/B节点
  python tools/annotate_node_events.py --sector 半导体    # 只标注指定板块
  python tools/annotate_node_events.py --min-grade B      # 只标注B级以上
  python tools/annotate_node_events.py --dry-run          # 预览不写文件
  python tools/annotate_node_events.py --force-refetch    # 强制重新拉取缓存
"""

import json
import sys
import time
import re
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SIGNALS_DIR = PROJECT_ROOT / "signals" / "tracking"
NODE_MAP_PATH = SIGNALS_DIR / "_macro" / "node_map.json"
CACHE_PATH = SIGNALS_DIR / "_macro" / "report_cache.json"

# ── 通达信概念板块 → 东财行业代码 映射 ──
# 手工维护,覆盖主要概念板块。
# 值可以是多个行业代码（按相关性排序），查询时逐个尝试
SECTOR_TO_INDUSTRY = {
    # ── 半导体/芯片 ──
    "半导体": ["1036"],
    "芯片": ["1036"],
    "MCU芯片": ["1036"],
    "汽车芯片": ["1036", "1027"],
    "存储芯片": ["1036"],
    "先进封装": ["1036"],
    "第三代半导体": ["1036"],
    "光刻机": ["1036"],
    "PCB概念": ["1036"],
    "玻璃基板": ["1036"],
    "MLCC概念": ["1036", "1017"],
    # ── 消费电子 ──
    "消费电子概念": ["1037"],
    "苹果概念": ["1037", "1017"],
    "小米概念": ["1037", "1017"],
    "无线耳机": ["1037"],
    "智能穿戴": ["1037"],
    "折叠屏": ["1037"],
    "AI眼镜": ["1037"],
    "电子纸": ["1017", "1037"],
    # ── 汽车 ──
    "汽车电子": ["1027", "1037"],
    "新能源车": ["1027"],
    "小米汽车概念": ["1027"],
    "华为汽车": ["1027"],
    "一体压铸": ["1027", "1025"],
    "汽车热管理": ["1027", "1025"],
    "飞行汽车": ["1027", "1026"],
    "无人驾驶": ["1027", "1020"],
    "车联网": ["1027", "1020"],
    "特斯拉概念": ["1027"],
    "换电概念": ["1027", "1024"],
    "胎压监测": ["1027"],
    "减速器": ["1025"],
    "汽车零部件": ["481"],
    "汽车拆解": ["481"],
    # ── 电池 ──
    "锂电池概念": ["1033"],
    "固态电池": ["1033"],
    "钠电池": ["1033"],
    "钒电池": ["1033"],
    "BC电池": ["1033"],
    "HJT电池": ["1033"],
    "钙钛矿电池": ["1033"],
    "TOPCon电池": ["1033"],
    "动力电池回收": ["1033"],
    "燃料电池": ["1033", "1027"],
    "超级电容": ["1033", "1017"],
    "储能": ["1024", "1033"],
    "锂矿": ["1034"],
    "盐湖提锂": ["1034"],
    "钴金属": ["1012", "1034"],
    "镍金属": ["1012", "1034"],
    "PVDF概念": ["1013"],
    "POE胶膜": ["1013"],
    "复合铜箔": ["1013", "1037"],
    # ── 光伏 ──
    "光伏": ["1031"],
    "BIPV概念": ["1031"],
    # ── 风电 ──
    "风电": ["1032"],
    "风电设备": ["1032"],
    # ── 电力设备 ──
    "智能电网": ["1024"],
    "特高压": ["1024"],
    "虚拟电厂": ["1024"],
    "超临界发电": ["1024"],
    "核电核能": ["1024"],
    "充电桩": ["1024"],
    "高压快充": ["1024"],
    "光热发电": ["1024"],
    "绿色电力": ["1006", "1024"],
    # ── 国防军工 ──
    "国防军工": ["1026"],
    "军工信息化": ["1026"],
    "军民融合": ["1026"],
    "大飞机": ["1026"],
    "商业航天": ["1026"],
    "低空经济": ["1026"],
    "无人机": ["1026"],
    "卫星导航": ["1026"],
    # ── 医药 ──
    "创新药": ["1043", "1044"],
    "减肥药": ["1043"],
    "仿制药": ["1043"],
    "CXO概念": ["1014"],
    "AI医疗概念": ["1035", "1014"],
    "生物疫苗": ["1044"],
    "基因概念": ["1044"],
    "免疫治疗": ["1044"],
    "中药": ["1041"],
    "家庭医生": ["1035"],
    "DRG-DIP": ["1035"],
    "血氧仪": ["1035"],
    "幽门螺杆菌": ["1043"],
    "肝炎概念": ["1043"],
    "合成生物": ["1044"],
    # ── 计算机/AI ──
    "人工智能": ["1018"],
    "AIGC概念": ["1018", "1019"],
    "ChatGPT概念": ["1018"],
    "多模态AI": ["1018"],
    "AI智能体": ["1018"],
    "AI营销": ["1018", "1019"],
    "DeepSeek概念": ["1018"],
    "智谱AI": ["1018"],
    "AI手机PC": ["1018", "1037"],
    "国产软件": ["1038"],
    "操作系统": ["1038"],
    "工业软件": ["1038"],
    "信创": ["1038", "1039"],
    "财税数字化": ["1038"],
    "大数据": ["1038"],
    "云计算": ["1038"],
    "边缘计算": ["1038"],
    "东数西算": ["1038", "1039"],
    "算力租赁": ["1038"],
    "华为算力": ["1038"],
    "时空大数据": ["1038"],
    "数据要素": ["1038"],
    "数据确权": ["1038"],
    "数字水印": ["1038"],
    "数字孪生": ["1038"],
    "智慧城市": ["1038", "1039"],
    "智慧政务": ["1038"],
    "国资云": ["1038"],
    # ── 通信 ──
    "5G概念": ["1020"],
    "6G概念": ["1020"],
    "光通信": ["1020"],
    "CPO概念": ["1020"],
    "星闪概念": ["1020"],
    "毫米波雷达": ["1020", "1027"],
    "物联网": ["1020"],
    "数据中心": ["1039", "1020"],
    "液冷服务器": ["1039"],
    # ── 传媒/游戏/内容 ──
    "网络游戏": ["1040"],
    "云游戏": ["1040"],
    "短剧游戏": ["1040", "1019"],
    "元宇宙概念": ["1019"],
    "超清视频": ["1019", "1017"],
    "知识产权": ["1019"],
    "知识付费": ["1019"],
    "职业教育": ["1019"],
    "体育概念": ["1019"],
    "网红经济": ["1019"],
    "抖音概念": ["1019"],
    "小红书概念": ["1019"],
    "IP经济": ["1019"],
    "NFT概念": ["1019"],
    "Web3概念": ["1019"],
    "区块链": ["1018", "1021"],
    "数字货币": ["1021"],
    "跨境支付CIPS": ["1021"],
    "电子身份证": ["1038"],
    # ── 电子 ──
    "OLED概念": ["1017"],
    "MiniLED": ["1017"],
    "MicroLED": ["1017"],
    "华为海思": ["1017"],
    "英伟达概念": ["1017"],
    # ── 机器人/高端装备 ──
    "机器人概念": ["1025"],
    "人形机器人": ["1025"],
    "外骨骼机器人": ["1025"],
    "工业母机": ["1025"],
    "高端装备": ["1025"],
    "新型工业化": ["1025"],
    "机器视觉": ["1025"],
    "3D打印": ["1025"],
    # ── 家电 ──
    "智能家居": ["1028"],
    "热泵概念": ["1028"],
    # ── 有色/化工/材料 ──
    "有色金属": ["1012"],
    "稀土永磁": ["1012"],
    "黄金概念": ["1012"],
    "钛金属": ["1012"],
    "新材料": ["1013"],
    "石墨烯": ["1013"],
    "碳纤维": ["1013"],
    "有机硅概念": ["1013"],
    "氟概念": ["1013"],
    "磷概念": ["1013"],
    "分散染料": ["1013"],
    "降解塑料": ["1013"],
    "PEEK材料": ["1013"],
    "化肥概念": ["1013"],
    "草甘膦": ["1013"],
    "维生素": ["1013"],
    "工业气体": ["1013"],
    "培育钻石": ["1012"],
    "煤炭": ["1030"],
    "钢铁": ["1011"],
    "能源金属": ["1034"],
    # ── 食品饮料/农业 ──
    "白酒概念": ["1001"],
    "食品安全": ["1001"],
    "预制菜": ["1001"],
    "代糖概念": ["1001"],
    "猪肉": ["1002"],
    "鸡肉": ["1002"],
    "种业": ["1002"],
    "粮食概念": ["1002"],
    "水产品": ["1002"],
    "人造肉": ["1001"],
    "宠物经济": ["1001"],
    "新零售": ["1004"],
    "免税概念": ["1004"],
    "跨境电商": ["1004"],
    "旅游概念": ["1015"],
    "纺织服饰": ["1005"],
    "医美概念": ["1016"],
    # ── 地产/基建/环保 ──
    "房地产": ["1008"],
    "物业管理概念": ["1008"],
    "租购同权": ["1008"],
    "水利建设": ["1009"],
    "地下管网": ["1009"],
    "新型城镇": ["1009"],
    "装配式建筑": ["1010"],
    "绿色建筑": ["1010"],
    "垃圾分类": ["1029"],
    "环保": ["1029"],
    "医废处理": ["1029"],
    "碳中和": ["1029", "1024"],
    "节能环保": ["1029"],
    "生物质能": ["1006"],
    "天然气": ["1006"],
    # ── 区域/政策 ──
    "雄安新区": ["1009"],
    "上海自贸": ["1004"],
    "海南自贸": ["1015"],
    "粤港澳": ["1023"],
    "一带一路": ["1009", "1025"],
    "乡村振兴": ["1002"],
    "土地流转": ["1002"],
    "中特估": ["1022"],
    "供销社": ["1004"],
    "海南自贸": ["1015"],
    "中俄贸易": ["1004", "1007"],
    # ── 交运/基建 ──
    "高铁": ["1025", "1007"],
    "航运概念": ["1007"],
    "交通运输": ["1007"],
    "冷链物流": ["1007"],
    # ── 金融 ──
    "银行": ["1021"],
    "互联金融": ["1021", "1022"],
    "非银金融": ["1022"],
    "保险": ["1022"],
    "券商": ["1022"],
    # ── 无对应 ──
    "次新股": [],
    "ST板块": [],
    "含可转债": [],
    "含H股": [],
    "含B股": [],
    "含GDR": [],
    "通达信88": [],
    "建筑装饰": ["1010"],
    "建筑材料": ["1009"],
    "纺织服饰": ["1005"],
    "国资云": ["1038"],
    "家用电器": ["1028"],
    # ── 剩余未覆盖的概念板块 ──
    "安防服务": ["1039", "1017"],
    "风沙治理": ["1029"],
    "稀缺资源": ["1034", "1012"],
    "聚氨酯": ["1013"],
    "量子科技": ["1020", "1018"],
    "超导概念": ["1017", "1024"],
    "地热能": ["1006"],
    "博彩概念": ["1019"],
    "阿里概念": ["1018"],
    "虚拟现实": ["1019", "1037"],
    "OLED概念": ["1017"],
    "黄金概念": ["1012"],
    "基因概念": ["1044"],
    "免疫治疗": ["1044"],
    "军民融合": ["1026"],
    "碳纤维": ["1013"],
    "智能医疗": ["1035"],
    "婴童概念": ["1001"],
    "PPP概念": ["1009"],
    "养老概念": ["1015"],
    "粤港澳": ["1023"],
    "通达信88": [],
    "操作系统": ["1038"],
    "跨境电商": ["1004"],
    "腾讯概念": ["1018", "1040"],
    "创投概念": ["1022"],
    "上海自贸": ["1004"],
    "租购同权": ["1008"],
    "工业互联": ["1038"],
    "高铁": ["1025", "1007"],
    "ETC概念": ["1020"],
    "数据中心": ["1039"],
    "乡村振兴": ["1002"],
    "远程办公": ["1038"],
    "仿制药": ["1043"],
    "工业大麻": ["1013"],
    "氢能源": ["1024", "1013"],
    "口罩防护": ["1005", "1014"],
    "虫害防治": ["1002"],
    "C2M概念": ["1018"],
    "华为鸿蒙": ["1038"],
    "MiniLED": ["1017"],
    "CXO概念": ["1014"],
    "锂电池概念": ["1033"],
    "固态电池": ["1033"],
    "钠电池": ["1033"],
    "钒电池": ["1033"],
    "动力电池回收": ["1033"],
    "燃料电池": ["1033"],
    "汽车拆解": ["1029", "481"],
    "降解塑料": ["1013"],
    "地摊经济": ["1004"],
    "新材料": ["1013"],
    "工业母机": ["1025"],
    "国资云": ["1038"],
    "先进封装": ["1036"],
    "航运概念": ["1007"],
    "储能": ["1024"],
    "元宇宙概念": ["1019"],
    "换电概念": ["1024", "1027"],
    "锂矿": ["1034"],
    "PVDF概念": ["1013"],
    "装配式建筑": ["1010"],
    "东数西算": ["1038", "1020"],
    "家庭医生": ["1035"],
    "一体压铸": ["1027", "1025"],
    "时空大数据": ["1038"],
    "可控核聚变": ["1024"],
    "盐湖提锂": ["1013", "1034"],
    "人脑工程": ["1025", "1035"],
    "工业气体": ["1013"],
    "数字孪生": ["1038"],
    "新型烟草": ["1001"],
    "天然气": ["1006"],
    "绿色电力": ["1006"],
    "鸡肉": ["1002"],
    "医废处理": ["1029"],
    "免税概念": ["1004"],
    "辅助生殖": ["1014"],
    "核污染防治": ["1029"],
    "粮食概念": ["1002"],
    "热泵概念": ["1028"],
    "EDA概念": ["1036"],
    "DRG-DIP": ["1035"],
    "AIGC概念": ["1018"],
    "数据确权": ["1038"],
    "血氧仪": ["1035"],
    "数字水印": ["1038"],
    "6G概念": ["1020"],
    "机器视觉": ["1025"],
    "算力租赁": ["1038"],
    "物业管理概念": ["1008"],
    "NMN概念": ["1014"],
    "冷链物流": ["1007"],
    "预制菜": ["1001"],
    "幽门螺杆菌": ["1043"],
    "镍金属": ["1012", "1034"],
    "绿色建筑": ["1010"],
    "化肥概念": ["1013"],
    "新型城镇": ["1009"],
    "超临界发电": ["1024"],
    "钒电池": ["1033"],
    "含GDR": [],
    "供销社": ["1004"],
    "Web3概念": ["1018"],
    "复合铜箔": ["1013", "1037"],
    "旅游概念": ["1015"],
    "创新药": ["1043"],
    "CPO概念": ["1020"],
    "高压快充": ["1024"],
    "工业软件": ["1038"],
    "存储芯片": ["1036"],
    "混合现实": ["1019"],
    "减肥药": ["1043"],
    "星闪概念": ["1020"],
    "新型工业化": ["1025"],
    "代糖概念": ["1001"],
    "网红经济": ["1019"],
    "医美概念": ["1016"],
    "低空经济": ["1026"],
    "中俄贸易": ["1004", "1007"],
    "肝炎概念": ["1043"],
    "虚拟电厂": ["1024"],
    "TOPCon电池": ["1033"],
    "光热发电": ["1024"],
    "ChatGPT概念": ["1018"],
    "钙钛矿电池": ["1033"],
    "毫米波雷达": ["1020", "1027"],
    "知识付费": ["1019"],
    "光通信": ["1020"],
    "英伟达概念": ["1017"],
    "减速器": ["1025"],
    "华为海思": ["1017", "1036"],
    "BC电池": ["1033"],
    "液冷服务器": ["1038", "1039"],
    "华为算力": ["1038"],
    "多模态AI": ["1018"],
    "培育钻石": ["1012"],
    "军工信息化": ["1026"],
    "AI手机PC": ["1018"],
    "车联网": ["1027", "1020"],
    "POE胶膜": ["1013"],
    "短剧游戏": ["1040", "1019"],
    "PEEK材料": ["1013"],
    "小米汽车概念": ["1027"],
    "飞行汽车": ["1026", "1027"],
    "人形机器人": ["1025"],
    "铜缆高速连接": ["1020", "1017"],
    "商业航天": ["1026"],
    "PCB概念": ["1036", "1017"],
    "财税数字化": ["1038"],
    "折叠屏": ["1037"],
    "AI眼镜": ["1037"],
    "智谱AI": ["1018"],
    "华为汽车": ["1027"],
    "合成生物": ["1044"],
    "玻璃基板": ["1036"],
    "中特估": ["1022"],
    "AI医疗概念": ["1035"],
    "外骨骼机器人": ["1025"],
    "IP经济": ["1019"],
    "AI智能体": ["1018"],
    "海洋经济": ["1024", "1006"],
    "军贸概念": ["1026"],
    "DeepSeek概念": ["1018"],
    "宠物经济": ["1001"],
    "小红书概念": ["1019"],
    "AI营销": ["1018", "1019"],
    "雅江水电概念": ["1024", "1006"],
    "MLCC概念": ["1017"],
}

# ── 产业/政策事件关键词 ──
# 研报标题命中以下关键词 → 标注为政策/产业事件
POLICY_KEYWORDS = [
    # 政策文件
    "政策", "规划", "纲要", "意见", "通知", "办法", "方案",
    "产业政策", "产业规划", "专项规划",
    "补贴", "减税", "降费", "专项资金", "产业基金",
    "标准", "规范", "行业标准", "准入条件", "目录",
    "十四五", "十五五", "中央", "国务院", "发改委", "工信部",
    "科技部", "证监会", "中央经济",
    # 产业事件
    "产业链", "供应链", "国产替代", "自主可控",
    "重大突破", "核心技术", "技术突破", "研发",
    "创新", "科技攻关", "重大专项",
    # 产业趋势
    "数字化", "智能化", "绿色转型", "低碳", "双碳",
    "新质生产力", "新型工业化", "产业升级",
    "出海", "全球化", "产能", "供需",
    # 事件驱动
    "集采", "医保谈判", "带量采购",
    "反倾销", "关税", "制裁", "出口管制",
    "涨价", "涨价潮", "价格",
    "重组", "整合", "合并",
    "开工", "投产", "量产", "商用",
    # 产业景气
    "景气", "周期", "拐点", "复苏",
    "产能过剩", "去库存", "出清",
]


def load_node_map() -> dict:
    """加载节点地图"""
    if not NODE_MAP_PATH.exists():
        print(f"未找到节点地图: {NODE_MAP_PATH}")
        print("请先运行: python tools/node_map.py --all --save")
        sys.exit(1)

    with open(NODE_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_node_map(node_map: dict):
    """保存更新后的节点地图"""
    with open(NODE_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(node_map, f, ensure_ascii=False, indent=2)
    print(f"保存: {NODE_MAP_PATH}")


def get_industry_codes(sector_name: str) -> list[str]:
    """获取板块对应的东财行业代码列表"""
    return SECTOR_TO_INDUSTRY.get(sector_name, [])


def load_report_cache() -> dict:
    """加载研报缓存"""
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_report_cache(cache: dict):
    """保存研报缓存"""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"缓存保存: {CACHE_PATH} ({sum(len(v) for v in cache.values())} 条研报)")


# ═══════════════════════════════════════════════════════════════
# 研报拉取与缓存
# ═══════════════════════════════════════════════════════════════

CACHE_PERIODS = [
    ("2024-01-01", "2024-06-30"),
    ("2024-07-01", "2024-12-31"),
    ("2025-01-01", "2025-06-30"),
    ("2025-07-01", "2025-12-31"),
    ("2026-01-01", "2026-06-08"),
]


def fetch_and_cache_reports(cache: dict, force_refetch: bool = False) -> dict:
    """
    按行业代码 × 半年度时间段，批量拉取研报并缓存。

    cache: { "industry_code|period_label": [report, ...] }
    """
    from tools.research_report import fetch_industry_reports

    total_fetched = 0
    total_skipped = 0
    total_failed = 0

    # 收集需要拉取的行业代码
    industry_codes = set()
    for codes in SECTOR_TO_INDUSTRY.values():
        for c in codes:
            if c:  # 排除空字符串
                industry_codes.add(c)
    industry_codes = sorted(industry_codes)
    print(f"待拉取行业: {len(industry_codes)} 个")

    for ic in industry_codes:
        for begin, end in CACHE_PERIODS:
            cache_key = f"{ic}|{begin[:7]}"

            # 检查缓存
            if not force_refetch and cache.get(cache_key):
                total_skipped += 1
                continue

            # 拉取研报（分页，最多5页 = 100条）
            all_reports = []
            for page in range(1, 6):
                reports = fetch_industry_reports(ic, begin, end,
                                                  page_no=page, page_size=20)
                if reports is None:
                    total_failed += 1
                    break
                if len(reports) == 0:
                    break
                all_reports.extend(reports)
                if len(reports) < 20:
                    break  # 最后一页
                time.sleep(0.5)  # API限速

            if all_reports:
                cache[cache_key] = all_reports
                total_fetched += 1
                print(f"  [{ic}] {begin[:7]}: {len(all_reports)} 条")
            else:
                cache[cache_key] = []
                total_skipped += 1

            time.sleep(0.3)

    print(f"拉取完成: 新增={total_fetched}, 跳过={total_skipped}, 失败={total_failed}")
    return cache


# ═══════════════════════════════════════════════════════════════
# 政策事件检测
# ═══════════════════════════════════════════════════════════════

def detect_policy_events(reports: list[dict],
                          keywords: list[str] = None) -> list[dict]:
    """
    从研报列表中检测政策/产业事件。

    Returns: [{title, org, date, matched_keywords, url}, ...]
    """
    if keywords is None:
        keywords = POLICY_KEYWORDS

    events = []
    for r in reports:
        title = r.get("title", "")
        matched = [kw for kw in keywords if kw in title]
        if matched:
            events.append({
                "title": title,
                "org_name": r.get("org_name", ""),
                "publish_date": r.get("publish_date", ""),
                "matched_keywords": matched,
                "url": r.get("url", ""),
            })
    return events


# ═══════════════════════════════════════════════════════════════
# 节点标注主逻辑
# ═══════════════════════════════════════════════════════════════

def annotate_node_events(node_map: dict, cache: dict,
                          min_grade: str = "B") -> tuple[dict, int, int]:
    """
    对所有2024+ A/B节点标注产业政策事件。

    标注写入 node.context.event_annotations:
    {
        "annotated_at": "2026-06-08",
        "industry_codes": ["1036"],
        "events": [...],
        "event_count": int,
        "policy_keyword_matches": int,
    }
    """
    grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
    min_rank = grade_rank.get(min_grade, 3)

    total_annotated = 0
    total_nodes = 0
    total_events = 0

    for sector in node_map.get("sectors", []):
        sector_name = sector.get("sector", "")
        industry_codes = get_industry_codes(sector_name)

        for node in sector.get("nodes", []):
            total_nodes += 1
            grade = node.get("quality", {}).get("grade", "D")
            if grade_rank.get(grade, 0) < min_rank:
                continue

            window = node.get("window", "")
            if not window or "-" not in window:
                continue

            # 只标注2024年及以后的节点
            if not window.startswith("2024") and not window.startswith("2025") and not window.startswith("2026"):
                continue

            parts = window.split("-")
            node_start = f"{parts[0]}-{parts[1]}-{parts[2]}"
            node_end = f"{parts[3]}-{parts[4]}-{parts[5]}" if len(parts) >= 6 else node_start

            ctx = node.setdefault("context", {})

            # 如果没有对应的行业代码，标记为空
            if not industry_codes:
                ctx["event_annotations"] = {
                    "annotated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "industry_codes": [],
                    "event_count": 0,
                    "note": "无对应东财行业分类",
                }
                continue

            # 从缓存中查找匹配的研报事件
            all_events = []
            for ic in industry_codes:
                for begin, end in CACHE_PERIODS:
                    cache_key = f"{ic}|{begin[:7]}"
                    reports = cache.get(cache_key, [])

                    # 筛选时间窗口内的研报
                    window_reports = [
                        r for r in reports
                        if r.get("publish_date", "") >= node_start[:7]  # 按月份粗略筛选
                        and r.get("publish_date", "") <= node_end[:7]    # 避免遗漏跨期报告
                    ]
                    # 如果时间跨度跨越半年，补充查询相邻期的报告
                    if node_start[:7] != node_end[:7]:
                        for begin2, end2 in CACHE_PERIODS:
                            k2 = f"{ic}|{begin2[:7]}"
                            if k2 != cache_key:
                                extra = cache.get(k2, [])
                                window_reports.extend([
                                    r for r in extra
                                    if r.get("publish_date", "") >= node_start[:7]
                                    and r.get("publish_date", "") <= node_end[:7]
                                ])

                    # 去重
                    seen_titles = set()
                    unique_reports = []
                    for r in window_reports:
                        t = r.get("title", "")
                        if t and t not in seen_titles:
                            seen_titles.add(t)
                            unique_reports.append(r)

                    # 检测政策/产业事件
                    events = detect_policy_events(unique_reports)
                    all_events.extend(events)

            # 去重（不同行业代码可能查出相同研报）
            seen = set()
            unique_events = []
            for ev in all_events:
                key = ev["title"] + ev.get("publish_date", "")
                if key not in seen:
                    seen.add(key)
                    unique_events.append(ev)

            # 按日期排序
            unique_events.sort(key=lambda x: x.get("publish_date", ""), reverse=True)

            ctx["event_annotations"] = {
                "annotated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "industry_codes": industry_codes,
                "event_count": len(unique_events),
                "events": unique_events[:20],  # 最多保存20条
            }
            total_annotated += 1
            total_events += len(unique_events)

    node_map["event_annotated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    node_map["event_annotated_count"] = total_annotated
    node_map["total_events_found"] = total_events
    return node_map, total_annotated, total_events


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="节点产业政策事件标注")
    parser.add_argument("--sector", help="只标注指定板块")
    parser.add_argument("--min-grade", default="B", choices=["A", "B", "C"],
                        help="最低节点等级 (default: B)")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览不写文件")
    parser.add_argument("--force-refetch", action="store_true",
                        help="强制重新拉取研报缓存")
    args = parser.parse_args()

    t0 = time.time()

    # 1. 加载节点地图
    print("加载节点地图...")
    node_map = load_node_map()
    if args.sector:
        node_map["sectors"] = [s for s in node_map["sectors"]
                               if args.sector in s.get("sector", "")]
        if not node_map["sectors"]:
            print(f"未找到板块: {args.sector}")
            sys.exit(1)
        print(f"  过滤板块: {node_map['sectors'][0]['sector']}")

    # 统计2024+ A/B节点
    grade_rank_map = {"A": 4, "B": 3, "C": 2, "D": 1}
    mr = grade_rank_map.get(args.min_grade, 3)
    pending = sum(
        1 for s in node_map.get("sectors", [])
        for n in s.get("nodes", [])
        if grade_rank_map.get(n.get("quality", {}).get("grade", "D"), 0) >= mr
        and (n.get("window", "").startswith("2024")
             or n.get("window", "").startswith("2025")
             or n.get("window", "").startswith("2026"))
    )
    print(f"  待标注节点 (2024+/≥{args.min_grade}级): {pending}")

    # 2. 加载研报缓存
    print("\n加载研报缓存...")
    cache = load_report_cache()
    cached_keys = sum(1 for v in cache.values() if v)
    print(f"  缓存中 {cached_keys} 个行业-时间段有数据")

    # 3. 拉取/更新研报缓存
    print("\n拉取研报 (按行业代码 × 半年度)...")
    cache = fetch_and_cache_reports(cache, force_refetch=args.force_refetch)
    save_report_cache(cache)

    # 4. 标注节点
    print("\n标注节点...")
    node_map, annotated, total_events = annotate_node_events(
        node_map, cache, min_grade=args.min_grade
    )

    elapsed = time.time() - t0
    print(f"\n标注完成: {annotated}/{pending} 节点, 共 {total_events} 条政策/产业事件")
    print(f"耗时: {elapsed:.1f}s")

    # 5. 预览/保存
    if args.dry_run:
        # 预览有事件的节点
        shown = 0
        for sector in node_map.get("sectors", []):
            for node in sector.get("nodes", []):
                ea = node.get("context", {}).get("event_annotations", {})
                if ea.get("event_count", 0) > 0 and shown < 10:
                    print(f"\n  [{sector['sector']}] {node['window']} "
                          f"({node.get('quality',{}).get('grade','?')}级)")
                    print(f"    行业代码: {ea.get('industry_codes',[])}")
                    print(f"    事件数: {ea['event_count']}")
                    for ev in ea.get("events", [])[:3]:
                        print(f"    · [{ev.get('publish_date','')}] {ev['title'][:50]}")
                    shown += 1
        if shown == 0:
            print("  (无标注节点)")
    else:
        save_node_map(node_map)


if __name__ == "__main__":
    main()

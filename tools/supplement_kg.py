# -*- coding: utf-8 -*-
"""
知识图谱产品补全工具 — 填补 ChainKnowledgeGraph 缺失的关键产品
用法: python tools/supplement_kg.py
效果: 在 industry_kg.json 中追加补充产品→链映射

补充来源: 研报关键词命中 + 产业知识 + 概念股搜索
"""
import json
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KG_PATH = PROJECT_ROOT / "signals" / "tracking" / "_macro" / "industry_kg.json"

# ═══════════════════════════════════════════════════════════════
# 补充产品映射表 — 产品名 → [叙事链, ...]
# 这些产品在 ChainKnowledgeGraph 中缺失（财报段位数据不覆盖）
# 但频繁出现在研报/新闻/概念股讨论中
# ═══════════════════════════════════════════════════════════════

SUPPLEMENT_PRODUCTS = {
    # ── 功率半导体（narrative#6）──
    "碳化硅(SiC)": {"chains": ["narrative#3a", "narrative#6"], "tags": ["第三代半导体", "宽禁带"]},
    "SiC衬底": {"chains": ["narrative#3a", "narrative#6"], "tags": ["碳化硅", "衬底"]},
    "SiC外延片": {"chains": ["narrative#3a", "narrative#6"], "tags": ["碳化硅", "外延"]},
    "SiC器件": {"chains": ["narrative#6"], "tags": ["碳化硅", "功率器件"]},
    "SiC MOSFET": {"chains": ["narrative#6"], "tags": ["碳化硅", "MOSFET"]},
    "SiC二极管": {"chains": ["narrative#6"], "tags": ["碳化硅", "二极管"]},
    "氮化镓(GaN)": {"chains": ["narrative#6", "narrative#3a"], "tags": ["第三代半导体", "宽禁带"]},
    "GaN射频器件": {"chains": ["narrative#6"], "tags": ["氮化镓", "射频"]},
    "GaN充电器": {"chains": ["narrative#6"], "tags": ["氮化镓", "快充"]},
    "IGBT": {"chains": ["narrative#6"], "tags": ["功率半导体", "逆变器"]},
    "IGBT模块": {"chains": ["narrative#6", "narrative#31"], "tags": ["IGBT", "新能源"]},
    "SiC逆变器": {"chains": ["narrative#6", "narrative#18"], "tags": ["碳化硅", "新能源车"]},
    "MOSFET": {"chains": ["narrative#6"], "tags": ["功率器件"]},
    "超级结MOSFET": {"chains": ["narrative#6"], "tags": ["功率器件"]},

    # ── 半导体设备（narrative#2）──
    "光刻机": {"chains": ["narrative#2"], "tags": ["光刻", "前道设备"]},
    "浸没式光刻机": {"chains": ["narrative#2"], "tags": ["光刻", "先进制程"]},
    "刻蚀设备": {"chains": ["narrative#2"], "tags": ["刻蚀", "前道设备"]},
    "硅刻蚀设备": {"chains": ["narrative#2"], "tags": ["刻蚀", "前道设备"]},
    "介质刻蚀设备": {"chains": ["narrative#2"], "tags": ["刻蚀", "前道设备"]},
    "薄膜沉积设备": {"chains": ["narrative#2"], "tags": ["沉积", "CVD", "PVD"]},
    "CVD设备": {"chains": ["narrative#2"], "tags": ["化学气相沉积"]},
    "PVD设备": {"chains": ["narrative#2"], "tags": ["物理气相沉积"]},
    "ALD设备": {"chains": ["narrative#2"], "tags": ["原子层沉积"]},
    "CMP设备": {"chains": ["narrative#2"], "tags": ["平坦化", "抛光"]},
    "清洗设备": {"chains": ["narrative#2"], "tags": ["清洗", "前道设备"]},
    "离子注入机": {"chains": ["narrative#2"], "tags": ["掺杂", "注入"]},
    "涂胶显影设备": {"chains": ["narrative#2"], "tags": ["光刻配套"]},
    "检测设备": {"chains": ["narrative#2"], "tags": ["量测", "缺陷检测"]},
    "半导体设备零部件": {"chains": ["narrative#7", "narrative#2"], "tags": ["零部件", "耗材"]},

    # ── IC设计（narrative#1）──
    "MCU": {"chains": ["narrative#1", "narrative#19"], "tags": ["微控制器", "汽车电子"]},
    "车规级MCU": {"chains": ["narrative#1", "narrative#19"], "tags": ["汽车电子", "微控制器"]},
    "GPU": {"chains": ["narrative#1", "narrative#11"], "tags": ["图形处理器", "AI"]},
    "AI加速卡": {"chains": ["narrative#1", "narrative#11"], "tags": ["AI芯片", "推理"]},
    "FPGA": {"chains": ["narrative#1"], "tags": ["可编程逻辑"]},
    "DSP": {"chains": ["narrative#1"], "tags": ["数字信号处理"]},
    "SoC芯片": {"chains": ["narrative#1", "narrative#19"], "tags": ["系统级芯片"]},
    "AI SoC": {"chains": ["narrative#1", "narrative#11"], "tags": ["AI芯片", "SoC"]},
    "RISC-V处理器": {"chains": ["narrative#1", "narrative#5"], "tags": ["RISC-V", "CPU架构"]},

    # ── 半导体材料（narrative#3）──
    "大硅片": {"chains": ["narrative#3"], "tags": ["硅片", "300mm"]},
    "300mm硅片": {"chains": ["narrative#3"], "tags": ["大硅片"]},
    "光刻胶": {"chains": ["narrative#3"], "tags": ["光刻", "材料"]},
    "ArF光刻胶": {"chains": ["narrative#3"], "tags": ["深紫外", "光刻胶"]},
    "KrF光刻胶": {"chains": ["narrative#3"], "tags": ["光刻胶"]},
    "CMP抛光液": {"chains": ["narrative#3"], "tags": ["平坦化", "抛光"]},
    "CMP抛光垫": {"chains": ["narrative#3"], "tags": ["平坦化", "耗材"]},
    "电子特气": {"chains": ["narrative#3", "narrative#7"], "tags": ["特种气体"]},
    "高纯电子气体": {"chains": ["narrative#3", "narrative#7"], "tags": ["特种气体"]},
    "溅射靶材": {"chains": ["narrative#3"], "tags": ["靶材", "PVD"]},
    "高纯金属靶材": {"chains": ["narrative#3"], "tags": ["靶材"]},
    "前驱体材料": {"chains": ["narrative#3"], "tags": ["ALD", "CVD"]},
    "掩模版": {"chains": ["narrative#3"], "tags": ["光刻", "掩膜"]},
    "湿电子化学品": {"chains": ["narrative#3", "narrative#7"], "tags": ["湿法", "清洗"]},

    # ── 先进封装（narrative#4）──
    "Chiplet": {"chains": ["narrative#4"], "tags": ["芯粒", "异构集成"]},
    "HBM": {"chains": ["narrative#4", "narrative#8"], "tags": ["高带宽存储", "AI"]},
    "HBM2E": {"chains": ["narrative#4", "narrative#8"], "tags": ["高带宽存储"]},
    "HBM3": {"chains": ["narrative#4", "narrative#8"], "tags": ["高带宽存储"]},
    "CoWoS封装": {"chains": ["narrative#4"], "tags": ["台积电", "2.5D封装"]},
    "2.5D封装": {"chains": ["narrative#4"], "tags": ["中介层", "硅通孔"]},
    "3D封装": {"chains": ["narrative#4"], "tags": ["堆叠", "TSV"]},
    "Fan-Out封装": {"chains": ["narrative#4"], "tags": ["扇出型封装"]},
    "SiP封装": {"chains": ["narrative#4"], "tags": ["系统级封装"]},
    "TSV": {"chains": ["narrative#4"], "tags": ["硅通孔", "3D封装"]},
    "探针卡": {"chains": ["narrative#4", "narrative#2"], "tags": ["测试", "晶圆测试"]},
    "先进封装设备": {"chains": ["narrative#4", "narrative#2"], "tags": ["封装设备"]},

    # ── 半导体配套（narrative#7）──
    "洁净室工程": {"chains": ["narrative#7", "narrative#11"], "tags": ["洁净室", "无尘室", "半导体厂房"]},
    "洁净室系统": {"chains": ["narrative#7", "narrative#11"], "tags": ["洁净室", "厂房建设"]},
    "高纯管路系统": {"chains": ["narrative#7"], "tags": ["气体输送", "化学品输送"]},
    "半导体厂房设计": {"chains": ["narrative#7"], "tags": ["工程服务"]},
    "厂务监控系统": {"chains": ["narrative#7"], "tags": ["FMCS", "厂务"]},

    # ── PCB/载板（narrative#8）──
    "IC载板": {"chains": ["narrative#8"], "tags": ["封装基板"]},
    "ABF载板": {"chains": ["narrative#8"], "tags": ["载板", "CPU基板"]},
    "BT载板": {"chains": ["narrative#8"], "tags": ["载板", "存储基板"]},
    "HDI板": {"chains": ["narrative#8"], "tags": ["高密度互连", "PCB"]},
    "柔性电路板(FPC)": {"chains": ["narrative#8", "narrative#15"], "tags": ["FPC", "柔性PCB"]},

    # ── MLCC/被动元件（同属narrative#8）──
    "MLCC": {"chains": ["narrative#8"], "tags": ["多层陶瓷电容", "被动元件"]},
    "片式电阻": {"chains": ["narrative#8"], "tags": ["被动元件"]},
    "电感": {"chains": ["narrative#8"], "tags": ["被动元件"]},

    # ── 汽车电子（narrative#19）──
    "智能座舱芯片": {"chains": ["narrative#19", "narrative#1"], "tags": ["汽车芯片"]},
    "自动驾驶芯片": {"chains": ["narrative#19", "narrative#17"], "tags": ["智驾芯片"]},
    "激光雷达": {"chains": ["narrative#19", "narrative#17"], "tags": ["LiDAR"]},
    "毫米波雷达": {"chains": ["narrative#19", "narrative#17"], "tags": ["雷达"]},

    # ── AI基础设施（narrative#11）──
    "AI服务器": {"chains": ["narrative#11"], "tags": ["服务器", "算力"]},
    "高速交换机": {"chains": ["narrative#11", "narrative#12"], "tags": ["网络", "数据中心"]},
    "液冷散热": {"chains": ["narrative#11"], "tags": ["散热", "数据中心"]},
    "光模块": {"chains": ["narrative#12"], "tags": ["数通", "光通信"]},
    "800G光模块": {"chains": ["narrative#12"], "tags": ["高速光模块"]},
    "硅光芯片": {"chains": ["narrative#12", "narrative#1"], "tags": ["硅光子", "光通信"]},
    "光纤": {"chains": ["narrative#12", "narrative#13"], "tags": ["光通信", "光传输"]},
    "光纤光缆": {"chains": ["narrative#12", "narrative#13"], "tags": ["光传输"]},

    # ── 光伏（narrative#29）──
    "钙钛矿电池": {"chains": ["narrative#29"], "tags": ["叠层", "光伏新技术"]},
    "异质结(HJT)": {"chains": ["narrative#29"], "tags": ["HJT", "高效电池"]},
    "TOPCon电池": {"chains": ["narrative#29"], "tags": ["钝化接触", "N型"]},
    "BC电池": {"chains": ["narrative#29"], "tags": ["背接触", "IBC"]},

    # ── 电池/储能（narrative#31/34）──
    "固态电池": {"chains": ["narrative#31"], "tags": ["下一代电池"]},
    "钠离子电池": {"chains": ["narrative#31"], "tags": ["钠电"]},
    "磷酸铁锂": {"chains": ["narrative#31", "narrative#32"], "tags": ["LFP"]},
    "高压快充": {"chains": ["narrative#20", "narrative#31"], "tags": ["快充", "充电"]},

    # ── 低空经济（narrative#26）──
    "eVTOL": {"chains": ["narrative#26"], "tags": ["飞行汽车", "电动垂直起降"]},
    "无人机": {"chains": ["narrative#26"], "tags": ["UAV", "工业无人机"]},

    # ── 创新药/医疗器械（narrative#38/40）──
    "GLP-1": {"chains": ["narrative#38"], "tags": ["减肥药", "糖尿病"]},
    "ADC药物": {"chains": ["narrative#38"], "tags": ["抗体偶联"]},
    "CAR-T": {"chains": ["narrative#38", "narrative#39"], "tags": ["细胞治疗"]},
    "基因编辑": {"chains": ["narrative#38"], "tags": ["CRISPR"]},
    "内窥镜": {"chains": ["narrative#40"], "tags": ["医疗设备"]},
    "CT": {"chains": ["narrative#40"], "tags": ["影像设备"]},
    "MRI": {"chains": ["narrative#40"], "tags": ["影像设备"]},

    # ── 新材料（narrative#46）──
    "高温合金": {"chains": ["narrative#46", "narrative#28"], "tags": ["特种金属"]},
    "碳纤维": {"chains": ["narrative#46"], "tags": ["复合材料"]},
    "芳纶": {"chains": ["narrative#46"], "tags": ["特种纤维"]},
    "超导材料": {"chains": ["narrative#46"], "tags": ["超导"]},

    # ── 补充缺口（demo反馈）──
    "光纤": {"chains": ["narrative#12", "narrative#13"], "tags": ["光通信", "光传输"]},
    "光纤光缆": {"chains": ["narrative#12", "narrative#13"], "tags": ["光传输"]},
    "存储芯片": {"chains": ["narrative#1", "narrative#8"], "tags": ["存储", "DRAM", "NAND"]},
    "DRAM芯片": {"chains": ["narrative#1", "narrative#8"], "tags": ["存储", "内存"]},
    "NAND闪存": {"chains": ["narrative#1", "narrative#8"], "tags": ["存储", "闪存"]},
    "数据中心": {"chains": ["narrative#11", "narrative#12"], "tags": ["算力", "IDC"]},
    "CPO": {"chains": ["narrative#12"], "tags": ["共封装光学", "硅光"]},
    "共封装光学(CPO)": {"chains": ["narrative#12"], "tags": ["CPO", "硅光"]},
    "光通信": {"chains": ["narrative#12", "narrative#13"], "tags": ["光传输"]},
    "铜连接": {"chains": ["narrative#12", "narrative#11"], "tags": ["DAC", "铜缆"]},
    "算力": {"chains": ["narrative#11", "narrative#10"], "tags": ["计算", "AI"]},
    "算力网": {"chains": ["narrative#11"], "tags": ["算力基础设施"]},
    "消费电子": {"chains": ["narrative#14"], "tags": ["手机", "PC"]},
    "硅光子技术": {"chains": ["narrative#12", "narrative#1"], "tags": ["硅光", "CPO"]},
}


def supplement_kg():
    """加载现有KG并追加补充产品"""
    if not KG_PATH.exists():
        print(f"KG文件不存在: {KG_PATH}")
        return

    kg = json.loads(KG_PATH.read_text(encoding="utf-8"))
    pg = kg["product_graph"]
    existing = set(pg.keys())

    added = 0
    skipped = 0
    for pname, pinfo in SUPPLEMENT_PRODUCTS.items():
        if pname in existing:
            # 产品已存在，补充链信息（不覆盖）
            existing_chains = set(pg[pname].get("chains", []))
            new_chains = set(pinfo["chains"])
            extra = new_chains - existing_chains
            if extra:
                pg[pname]["chains"] = list(existing_chains | new_chains)
                pg[pname].setdefault("tags", pinfo.get("tags", []))
                # 标注补充来源
                if "_supplement" not in pg[pname]:
                    pg[pname]["_supplement"] = True
                added += 1
                print(f"  [补充] {pname}: +{extra}")
        else:
            # 全新产品
            pg[pname] = {
                "chains": pinfo["chains"],
                "tags": pinfo.get("tags", []),
                "_supplement": True,
            }
            added += 1

    print(f"\n补充产品: {added} 个 (新增+补充)")
    print(f"跳过(已存在): {skipped} 个")

    # 重新统计链覆盖度
    chain_coverage = defaultdict(list)
    for pname, entry in pg.items():
        for c in entry.get("chains", []):
            chain_coverage[c].append(pname)

    kg["chain_coverage"] = {
        chain: {
            "product_count": len(products),
            "sample_products": products[:10]
        }
        for chain, products in sorted(chain_coverage.items(), key=lambda x: -len(x[1]))
    }

    # 更新统计
    total_products = len(pg)
    total_supplement = sum(1 for v in pg.values() if v.get("_supplement"))
    kg["statistics"]["products"] = total_products
    kg["statistics"]["supplementary_products"] = total_supplement
    kg["source"] += " + 研报/产业知识补充"
    kg["version"] = "1.1"

    # 保存
    KG_PATH.write_text(json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8")
    size_mb = KG_PATH.stat().st_size / 1024 / 1024
    print(f"\n知识图谱已更新: {KG_PATH}")
    print(f"   文件大小: {size_mb:.1f} MB")
    print(f"   总产品节点: {total_products} (其中CKG: {total_products - total_supplement}, 补充: {total_supplement})")
    print(f"   链覆盖: {len(chain_coverage)} 条叙事链")


if __name__ == "__main__":
    supplement_kg()

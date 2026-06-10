# -*- coding: utf-8 -*-
"""
产业链知识图谱构建器 — 基于 ChainKnowledgeGraph + 50条叙事链映射
用法: python tools/build_knowledge_graph.py
输出: signals/tracking/_macro/industry_kg.json

三步:
1. 50条叙事链 → 申万行业代码映射
2. 从 ChainKnowledgeGraph 过滤相关产品 + 上下游 + 公司
3. 输出结构化知识图谱
"""
import json
import os
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CKGRAPH = Path("C:/Users/Administrator/ChainKnowledgeGraph/data")
OUTPUT_DIR = PROJECT_ROOT / "signals" / "tracking" / "_macro"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════
# 第一步：50条叙事链 → 申万行业映射
# ═══════════════════════════════════════════

CHAIN_INDUSTRY_MAP = {
    # === S级 ===
    "功率半导体": {"chains": ["narrative#6"], "shenwan_codes": ["270103","270104","270106"]},
    "化工新材料": {"chains": ["narrative#47"], "shenwan_codes": ["220000","220100","220700","220500"]},
    "人形机器人": {"chains": ["narrative#22"], "shenwan_codes": ["640100","640500","270100"]},
    "新能源车": {"chains": ["narrative#18"], "shenwan_codes": ["280000","280300","280400"]},
    "商业航天": {"chains": ["narrative#27"], "shenwan_codes": ["650200"]},
    "低空经济": {"chains": ["narrative#26"], "shenwan_codes": ["650200","650300","730700"]},

    # === A级 ===
    "AI基础设施": {"chains": ["narrative#11"], "shenwan_codes": ["710000","710300"]},
    "AI应用": {"chains": ["narrative#10"], "shenwan_codes": ["710500","710501","710502"]},
    "智能驾驶": {"chains": ["narrative#17"], "shenwan_codes": ["280700","710500"]},
    "半导体设备": {"chains": ["narrative#2"], "shenwan_codes": ["270108"]},
    "先进封装": {"chains": ["narrative#4"], "shenwan_codes": ["270107"]},
    "光模块/CPO": {"chains": ["narrative#12"], "shenwan_codes": ["730700","730702"]},
    "国防军工": {"chains": ["narrative#28"], "shenwan_codes": ["650000","650100","650200","650300","650400","650500","650600"]},
    "游戏": {"chains": ["narrative#48"], "shenwan_codes": ["720200","720201"]},
    "数字内容/IP": {"chains": ["narrative#49"], "shenwan_codes": ["720200","720800","720900","710500"]},

    # === B级（核心） ===
    "消费电子": {"chains": ["narrative#14"], "shenwan_codes": ["270200","270201","270202"]},
    "AI穿戴": {"chains": ["narrative#15"], "shenwan_codes": ["270200","330000"]},
    "MLCC/PCB/载板": {"chains": ["narrative#8"], "shenwan_codes": ["270203","270204"]},
    "半导体材料": {"chains": ["narrative#3"], "shenwan_codes": ["270104"]},
    "IC设计": {"chains": ["narrative#1"], "shenwan_codes": ["270105","270106"]},
    "半导体配套": {"chains": ["narrative#7"], "shenwan_codes": ["270104","220000"]},
    "EDA/IP": {"chains": ["narrative#5"], "shenwan_codes": ["710500","270100"]},
    "SiC衬底": {"chains": ["narrative#3a"], "shenwan_codes": ["270104","270103"]},
    "面板": {"chains": ["narrative#16"], "shenwan_codes": ["270200"]},
    "光伏": {"chains": ["narrative#29"], "shenwan_codes": ["630600","630601","630602","630603","630604"]},
    "风电": {"chains": ["narrative#30"], "shenwan_codes": ["630700"]},
    "电池": {"chains": ["narrative#31"], "shenwan_codes": ["630800","240200"]},
    "储能": {"chains": ["narrative#34"], "shenwan_codes": ["630800","630500"]},
    "电网/电力设备": {"chains": ["narrative#33"], "shenwan_codes": ["630000","630300","630400"]},
    "汽车电子": {"chains": ["narrative#19"], "shenwan_codes": ["280700","270100"]},
    "充电桩": {"chains": ["narrative#20"], "shenwan_codes": ["630500"]},
    "汽车零部件": {"chains": ["narrative#21"], "shenwan_codes": ["280300","280301","280302","280303","280304","280305"]},
    "锂资源": {"chains": ["narrative#32"], "shenwan_codes": ["240600"]},
    "贵金属": {"chains": ["narrative#42"], "shenwan_codes": ["240400","240500"]},
    "有色金属（铜铝）": {"chains": ["narrative#44"], "shenwan_codes": ["240300","240301","240302"]},
    "小金属/战略金属": {"chains": ["narrative#43"], "shenwan_codes": ["240600","240200"]},
    "能源金属": {"chains": ["narrative#45"], "shenwan_codes": ["240600"]},
    "创新药": {"chains": ["narrative#38"], "shenwan_codes": ["370200","370201","370202"]},
    "疫苗": {"chains": ["narrative#39"], "shenwan_codes": ["370400","370401"]},
    "医疗器械": {"chains": ["narrative#40"], "shenwan_codes": ["370600","370601"]},
    "中药": {"chains": ["narrative#41"], "shenwan_codes": ["370300","370301"]},
    "新材料": {"chains": ["narrative#46"], "shenwan_codes": ["240200","220000"]},
    "机器人核心部件": {"chains": ["narrative#23"], "shenwan_codes": ["640100","640500"]},
    "工业机器人": {"chains": ["narrative#24"], "shenwan_codes": ["640500","640300"]},
    "工业互联网": {"chains": ["narrative#25"], "shenwan_codes": ["710500","710000"]},
    "信创": {"chains": ["narrative#35"], "shenwan_codes": ["710500","710000"]},
    "数据要素": {"chains": ["narrative#36"], "shenwan_codes": ["710500"]},
    "数字货币": {"chains": ["narrative#37"], "shenwan_codes": ["710500","490000"]},
    "养殖/农业": {"chains": ["narrative#50"], "shenwan_codes": ["110000","110100","110200","110300","110400"]},

    # === C级 ===
    "5G/6G通信": {"chains": ["narrative#13"], "shenwan_codes": ["730000","730700","730701","730702","730703"]},
    "医疗器械（C）": {"chains": ["narrative#40"], "shenwan_codes": ["370600"]},
    "疫苗/免疫（C）": {"chains": ["narrative#39"], "shenwan_codes": ["370400"]},
}

# 反向映射：申万代码 → 叙事链
CODE_TO_CHAINS = defaultdict(list)
for entry in CHAIN_INDUSTRY_MAP.values():
    for code in entry["shenwan_codes"]:
        CODE_TO_CHAINS[code].extend(entry["chains"])


# ═══════════════════════════════════════════
# 第二步：加载 ChainKnowledgeGraph 数据
# ═══════════════════════════════════════════

def load_jsonl(fp):
    """加载 JSONL 文件"""
    data = []
    with open(fp, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data

def load_json(fp):
    """加载普通 JSON 文件"""
    with open(fp, 'r', encoding='utf-8') as f:
        return json.load(f)

print("[1/6] 加载 ChainKnowledgeGraph 数据...")
industries = load_jsonl(str(CKGRAPH / "industry.json"))  # 511 行业
products = load_jsonl(str(CKGRAPH / "product.json"))  # 9.5万产品
product_product = load_jsonl(str(CKGRAPH / "product_product.json"))  # 11.2万上下游关系
company_product = load_jsonl(str(CKGRAPH / "company_product.json"))  # 5.3万公司-产品

# 公司数据（按代码索引）
companies = {}
try:
    for c in load_jsonl(str(CKGRAPH / "company.json")):
        companies[c["code"]] = c
except: pass

# 公司-行业映射
company_industry = {}
try:
    for ci in load_jsonl(str(CKGRAPH / "company_industry.json")):
        company_industry[ci["company_code"]] = {
            "industry_code": ci["industry_code"],
            "industry_name": ci.get("industry_name",""),
            "company_name": ci.get("company_name","")
        }
except: pass

# 构建 company_industry 所有行业代码集合（用于最佳前缀匹配）
all_industry_codes = set(ci["industry_code"] for ci in company_industry.values())

def resolve_code_best(code):
    """解析单个申万代码的最佳匹配粒度。
    优先级：6位精确 > 4位前缀 > 2位前缀 > 无匹配。
    返回 (prefix, specificity_level)，level越高越精确。"""
    if code in all_industry_codes:
        return (code, 6)
    prefix4 = code[:4]
    if any(c.startswith(prefix4) for c in all_industry_codes):
        return (prefix4, 4)
    prefix2 = code[:2]
    if any(c.startswith(prefix2) for c in all_industry_codes):
        return (prefix2, 2)
    return (None, 0)

def resolve_chain_filters(shenwan_codes):
    """解析叙事链的全局过滤前缀（4位为主，2位回退）。
    用于公司过滤阶段——需要尽量宽松以覆盖所有可能相关公司。"""
    prefixes = set()
    for sc in shenwan_codes:
        prefix4 = sc[:4]
        if any(c.startswith(prefix4) for c in all_industry_codes):
            prefixes.add(prefix4)
        else:
            prefix2 = sc[:2]
            if any(c.startswith(prefix2) for c in all_industry_codes):
                prefixes.add(prefix2)
    return prefixes

def resolve_chain_assign(shenwan_codes):
    """解析叙事链的精确赋值前缀（6位精确优先，无匹配则降级）。
    用于产品→链映射阶段——需要尽量精确以避免跨链串扰。
    降级策略：如果链内任何代码有6位匹配，全链用6位精确；
    否则全部按最佳可用粒度。"""
    # 先检查是否有任何代码能6位匹配
    all_six = True
    for sc in shenwan_codes:
        prefix, level = resolve_code_best(sc)
        if level < 6:
            all_six = False
            break
    if all_six:
        # 全链6位精确匹配
        return {sc for sc in shenwan_codes if sc in all_industry_codes}
    # 有代码无法6位匹配，按各自最佳粒度
    prefixes = set()
    for sc in shenwan_codes:
        p, level = resolve_code_best(sc)
        if p:
            prefixes.add(p)
    return prefixes

# 为每条叙事链计算：过滤前缀（宽）+ 赋值前缀（精确）
chain_filter_map = {}   # entry_name -> 宽前缀集（公司过滤用）
chain_assign_map = {}   # entry_name -> 精确前缀集（链赋值用）
for entry_name, entry in CHAIN_INDUSTRY_MAP.items():
    chain_filter_map[entry_name] = resolve_chain_filters(entry["shenwan_codes"])
    chain_assign_map[entry_name] = resolve_chain_assign(entry["shenwan_codes"])

# 全局过滤前缀集合（用于公司过滤）
global_prefixes = set().union(*chain_filter_map.values())

def code_matches_prefixes(industry_code, prefixes):
    """检查行业代码是否匹配前缀集合中的任意一个"""
    for p in prefixes:
        if industry_code == p or industry_code.startswith(p):
            return True
    return False

# ═══════════════════════════════════════════
# 第三步：构建行业→产品索引
# ═══════════════════════════════════════════

print("[2/6] 构建行业代码集合...")
# 收集所有相关的申万行业代码（含上级行业展开）
all_codes = set()
for entry in CHAIN_INDUSTRY_MAP.values():
    all_codes.update(entry["shenwan_codes"])

# 构建行业名称↔代码映射
ind_name_to_code = {ind["name"]: ind["code"] for ind in industries}
ind_code_to_name = {ind["code"]: ind["name"] for ind in industries}

# 找出这些代码对应的行业名称
relevant_industry_names = set()
for code in all_codes:
    if code in ind_code_to_name:
        relevant_industry_names.add(ind_code_to_name[code])
    # 通配：如果code是6位前几位匹配
    prefix = code[:2]
    for ind in industries:
        if ind["code"].startswith(prefix):
            relevant_industry_names.add(ind["name"])

print(f"   相关行业代码: {len(all_codes)} 个")
print(f"   相关行业名称: {len(relevant_industry_names)} 个")
print(f"   全局匹配前缀: {sorted(global_prefixes)}")

# ═══════════════════════════════════════════
# 第四步：过滤相关产品
# ═══════════════════════════════════════════

print("[3/6] 过滤相关产品（按公司-行业归属）...")

# 方法: 通过 company_product 找到属于相关行业的公司→产品
# 先找相关行业的公司（多粒度前缀匹配）
relevant_company_codes = set()
for cc, ci in company_industry.items():
    if code_matches_prefixes(ci["industry_code"], global_prefixes):
        relevant_company_codes.add(cc)

print(f"   相关行业公司: {len(relevant_company_codes)} 家")

# 再找这些公司的产品
company_products_map = defaultdict(list)
product_companies_map = defaultdict(list)
for cp in company_product:
    if cp["company_code"] in relevant_company_codes:
        product_name = cp["product_name"]
        company_products_map[cp["company_code"]].append(cp)
        product_companies_map[product_name].append(cp)

relevant_products = set(product_companies_map.keys())
print(f"   相关产品: {len(relevant_products)} 个")

# ═══════════════════════════════════════════
# 第五步：上下游关系过滤
# ═══════════════════════════════════════════

print("[4/6] 过滤上下游关系...")

# 所有相关产品 + 它们的上下游产品
all_related_products = set(relevant_products)
upstream_map = defaultdict(list)   # product → [上游]
downstream_map = defaultdict(list)  # product → [下游]

for rel in product_product:
    from_e = rel["from_entity"]
    to_e = rel["to_entity"]
    rtype = rel["rel"]

    # 如果关系两端的任一产品在相关集合中，记录
    if from_e in relevant_products or to_e in relevant_products:
        all_related_products.add(from_e)
        all_related_products.add(to_e)

        if "上游" in rtype or "原材料" in rtype:
            upstream_map[to_e].append({"product": from_e, "rel": rtype})
            downstream_map[from_e].append({"product": to_e, "rel": rtype})
        elif "下游" in rtype:
            upstream_map[to_e].append({"product": from_e, "rel": rtype})
            downstream_map[from_e].append({"product": to_e, "rel": rtype})
        else:
            # 其他关系类型（如"小类"）
            upstream_map[to_e].append({"product": from_e, "rel": rtype})
            downstream_map[from_e].append({"product": to_e, "rel": rtype})

# 再扩展一轮：新加入的产品的上下游也可能相关
expanded = set(all_related_products)
for rel in product_product:
    from_e = rel["from_entity"]
    to_e = rel["to_entity"]
    if from_e in all_related_products or to_e in all_related_products:
        expanded.add(from_e)
        expanded.add(to_e)

print(f"   展开后产品总数: {len(expanded)} 个")
print(f"   上游关系: {sum(len(v) for v in upstream_map.values())} 条")
print(f"   下游关系: {sum(len(v) for v in downstream_map.values())} 条")

# ═══════════════════════════════════════════
# 第六步：构建产品 → 叙事链映射
# ═══════════════════════════════════════════

print("[5/6] 构建产品→叙事链映射...")

# 通过公司所属行业反向推断产品所属叙事链
product_to_chains = defaultdict(set)
for pname in expanded:
    # 找生产该产品的公司的所属行业
    comps = product_companies_map.get(pname, [])
    for cp in comps:
        cc = cp["company_code"]
        if cc in company_industry:
            ind_code = company_industry[cc]["industry_code"]
            # 检查这个行业代码属于哪些叙事链（用精确前缀映射）
            for entry_name, entry in CHAIN_INDUSTRY_MAP.items():
                prefixes = chain_assign_map.get(entry_name, set())
                if code_matches_prefixes(ind_code, prefixes):
                    for c in entry["chains"]:
                        product_to_chains[pname].add(c)

# ═══════════════════════════════════════════
# 第七步：输出
# ═══════════════════════════════════════════

print("[6/6] 构建知识图谱 JSON...")

# 构建完整图谱
kg = {
    "version": "1.0",
    "description": "产业链知识图谱 - 基于ChainKnowledgeGraph + 50条叙事链映射",
    "source": "liuhuanyong/ChainKnowledgeGraph (申万行业分类)",
    "generated": __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M"),
    "statistics": {
        "shenwan_industries": len(all_codes),
        "products": len(expanded),
        "upstream_relations": sum(len(v) for v in upstream_map.values()),
        "downstream_relations": sum(len(v) for v in downstream_map.values()),
        "companies": len(relevant_company_codes),
    },
    "product_graph": {},
}

for pname in sorted(expanded)[:5000]:  # 限制输出规模
    entry = {}
    # 叙事链归属
    chains = list(product_to_chains.get(pname, set()))
    if chains:
        entry["chains"] = chains
    # 上下游
    ups = upstream_map.get(pname, [])
    downs = downstream_map.get(pname, [])
    if ups:
        entry["upstream"] = [{"product": u["product"], "type": u["rel"]} for u in ups[:10]]
    if downs:
        entry["downstream"] = [{"product": d["product"], "type": d["rel"]} for d in downs[:10]]
    # 生产公司
    comps = product_companies_map.get(pname, [])
    if comps:
        entry["companies"] = [
            {"code": c["company_code"], "name": c.get("company_name",""), "weight": c.get("rel_weight",0)}
            for c in comps[:5]
        ]
    if entry:
        kg["product_graph"][pname] = entry

# 统计链覆盖度
chain_coverage = defaultdict(list)
for pname, entry in kg["product_graph"].items():
    for c in entry.get("chains", []):
        chain_coverage[c].append(pname)

kg["chain_coverage"] = {
    chain: {
        "product_count": len(products),
        "sample_products": products[:10]
    }
    for chain, products in sorted(chain_coverage.items(), key=lambda x: -len(x[1]))
}

# 保存
output_path = OUTPUT_DIR / "industry_kg.json"
output_path.write_text(json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8")
size_mb = output_path.stat().st_size / 1024 / 1024
print(f"\n知识图谱已保存: {output_path}")
print(f"   文件大小: {size_mb:.1f} MB")
print(f"   产品节点: {kg['statistics']['products']}")
print(f"   公司节点: {kg['statistics']['companies']}")
print(f"   链覆盖: {len(chain_coverage)} 条叙事链")

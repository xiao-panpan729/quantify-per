#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teacher标签自动标注管道

用LLM (DeepSeek V4) 替代3%硬编码规则，真正当Teacher:
  1. 读取标的K线 → 笔序列 → 候选线段断点
  2. 调用LLM判断每个候选是否构成线段破坏
  3. 保存LLM判断为JSON
  4. 导出LLM标注的训练数据
  5. 训练Student模型

Usage:
  python tools/teacher_label.py                           # 默认3只指数
  python tools/teacher_label.py --codes sh000001 sz399006  # 指定标的
  python tools/teacher_label.py --all                      # 跟踪列表全部14只
  python tools/teacher_label.py --expand                   # 扩展20+只
  python tools/teacher_label.py --judge-only               # 只标注(不训练)
  python tools/teacher_label.py --train-only               # 只训练(已有标注)
"""
import sys, os, json, time, urllib.request, urllib.error
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np

# ── 配置 ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import NAME_MAP

# 尝试从.env加载API key
_env_loaded = False
for _env_path in [os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')]:
    if os.path.exists(_env_path):
        try:
            with open(_env_path, encoding='utf-8') as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith('#') and '=' in _line:
                        _k, _v = _line.split('=', 1)
                        os.environ.setdefault(_k.strip(), _v.strip().strip('"\''))
            _env_loaded = True
        except Exception:
            pass
    if _env_loaded:
        break

DEEPSEEK_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
API_URL = 'https://api.deepseek.com/chat/completions'
MODEL = 'deepseek-chat'

JUDGMENTS_DIR = 'training_data/teacher_judgments'
DATASET_OUT = 'training_data/chanlun_dataset_v2_teacher.json'
STUDENT_SCRIPT = 'tools/train_chanlun_student.py'

# 默认标的（先跑3只验证）
DEFAULT_CODES = ['sh000001', 'sz399006', 'sz159740']

# ── 扩展标的（缠论解析器支持的标的） ──
EXPAND_CODES = [
    'sh000001', 'sz399006', 'sz159740',  # 3指数
    'sh520600', 'sh513120', 'sz159326', 'sh513310', 'sh588200',  # ETF
    'sz002261', 'sz300118', 'sz000100', 'sz002129', 'sh600438', 'sh601012',  # 个股
    # 额外的指数/板块 (不含无TDX数据: sz399005/sh880479/sz399395)
    'sh000016',  # 上证50
    'sh000300',  # 沪深300
    'sh000905',  # 中证500
    'sh000688',  # 科创50
    'sz399001',  # 深证成指
    'sh880529',  # 半导体板块
    'sh880472',  # 白酒板块
]


def load_chanlun_data(code, period='daily'):
    """加载缠论解析结果"""
    from tools.chanlun_parser import parse_chanlun, segment_candidates, format_judge_prompt

    parser = parse_chanlun(code, period=period)
    result = parser.result
    bis = result.get('bis', [])
    candidates = segment_candidates(bis)
    prompt = format_judge_prompt(candidates, bis)

    return {
        'code': code,
        'name': NAME_MAP.get(code, code),
        'n_bars': result.get('raw_kline_count', 0),
        'n_bis': len(bis),
        'n_candidates': len(candidates),
        'parser': parser,
        'bis': bis,
        'all_bis': bis,  # full bi list for post-candidate context
        'candidates': candidates,
        'prompt': prompt,
    }, bis, candidates


def build_teacher_system_prompt():
    return """你是缠论专家，任务是判断候选笔是否构成有效的线段破坏。

你必须按以下顺序做完整推理，不能只做简化判断:

### 推理流程

**第一层:笔破坏（初步预警）**
- 上涨段中出现下跌笔 → 看下跌笔最低价是否跌破前一笔最低价
- 下跌段中出现上涨笔 → 看上涨笔最高价是否突破前一笔最高价
- 笔破坏 = 反向笔突破前笔极值点 → 这是线段破坏的预警信号，不是确认

**第二层:特征序列分析（核心判断）**
- 上涨段的特征序列 = 段内所有下跌笔
- 下跌段的特征序列 = 段内所有上涨笔
- 特征序列做包含处理（方向与K线相反）:
  - 上涨段特征序列 → 向下合并（取低低）
  - 下跌段特征序列 → 向上合并（取高高）
- 检查包含处理后的特征序列是否形成分型:
  - 上涨段特征序列 → 底分型 = 线段破坏确认
  - 下跌段特征序列 → 顶分型 = 线段破坏确认

**第三层:后续走势确认**
- 笔破坏后，后续笔是否延续新方向？
- 如果后续笔延续新方向 → 新段确认
- 如果后续笔回到原方向 → 可能是假突破

**第四层:力度辅助判断**
- 反向笔幅度<2% → 大概率只是回调
- 反向笔幅度>5% → 大概率是反转
- 2-5%之间 → 需要特征序列分型确认

### 输出规则
- 只有三层的推理都支持时，才判 is_break=true
- 笔破坏未触及+特征序列无分型+后续未延续 → is_break=false
- 笔破坏触及 + 特征序列有分型 + 后续延续 → is_break=true (high confidence)
- 笔破坏触及但特征序列无分型 → 需要看后续力度 (medium/low confidence)

输出必须是JSON数组，每个元素:
```json
{"id": 0, "is_break": true, "confidence": "high", "reason": "笔破坏+特征序列底分型+后续延续"}
```
"""


def extract_feature_sequence(seg_bis, seg_direction):
    """提取特征序列: 上涨段取所有下跌笔, 下跌段取所有上涨笔"""
    target_dir = '下跌' if seg_direction == '上涨' else '上涨'
    return [bi for bi in seg_bis if bi['direction'] == target_dir]


def feature_element_range(bi, seg_direction):
    """特征序列元素的价格范围"""
    if seg_direction == '上涨':  # 特征序列是下跌笔
        high = max(bi['start_price'], bi['end_price'])
        low = min(bi['start_price'], bi['end_price'])
    else:  # 特征序列是上涨笔
        low = min(bi['start_price'], bi['end_price'])
        high = max(bi['start_price'], bi['end_price'])
    return low, high


def process_feature_containment(feature_seq, seg_direction):
    """特征序列包含处理（方向与K线相反）

    上涨段特征序列(下跌笔) → 向下合并(取低低)
    下跌段特征序列(上涨笔) → 向上合并(取高高)
    """
    if not feature_seq:
        return [], []

    processed_full = [feature_seq[0]]
    for bi in feature_seq[1:]:
        prev = processed_full[-1]
        p_low, p_high = feature_element_range(prev, seg_direction)
        c_low, c_high = feature_element_range(bi, seg_direction)

        # 检查包含关系
        contained = (c_low >= p_low and c_high <= p_high) or (p_low >= c_low and p_high <= c_high)

        if contained:
            if seg_direction == '上涨':  # 向下合并
                merged = prev.copy()
                merged['_merged_high'] = min(p_high, c_high)
                merged['_merged_low'] = min(p_low, c_low)
                merged['_merge_note'] = f'向下合并: 含{len(processed_full)}-{len(processed_full)+1}'
            else:  # 下跌段，向上合并
                merged = prev.copy()
                merged['_merged_high'] = max(p_high, c_high)
                merged['_merged_low'] = max(p_low, c_low)
                merged['_merge_note'] = f'向上合并: 含{len(processed_full)}-{len(processed_full)+1}'

            processed_full[-1] = merged
        else:
            # 不包含，新元素
            bi['_merge_note'] = ''
            processed_full.append(bi)

    return feature_seq, processed_full


def check_feature_fractal(processed_seq, seg_direction):
    """检查特征序列是否形成分型（需要至少3个元素）"""
    if len(processed_seq) < 3:
        return False, '不足3元素，无法形成分型'

    # 取最后3个元素
    e1, e2, e3 = processed_seq[-3:]
    l1, h1 = feature_element_range(e1, seg_direction)
    l2, h2 = feature_element_range(e2, seg_direction)
    l3, h3 = feature_element_range(e3, seg_direction)

    if seg_direction == '上涨':
        # 上涨段特征序列(下跌笔) → 看底分型
        is_fractal = (l2 <= l1 and l2 <= l3) and (h2 <= h1 and h2 <= h3)
        return is_fractal, f'底分型: e1[{l1:.0f}~{h1:.0f}] e2[{l2:.0f}~{h2:.0f}] e3[{l3:.0f}~{h3:.0f}]'
    else:
        # 下跌段特征序列(上涨笔) → 看顶分型
        is_fractal = (h2 >= h1 and h2 >= h3) and (l2 >= l1 and l2 >= l3)
        return is_fractal, f'顶分型: e1[{l1:.0f}~{h1:.0f}] e2[{l2:.0f}~{h2:.0f}] e3[{l3:.0f}~{h3:.0f}]'


def build_teacher_prompt(data, all_bis):
    """构建Teacher标注prompt — 完整缠论特征序列分析"""
    lines = [f'# {data["code"]} {data["name"]} — 线段终结候选判断（完整特征序列分析）']
    lines.append('')
    lines.append(f'日线: {data["n_bars"]}根K线 | 笔: {data["n_bis"]}笔')
    lines.append('')

    for c in data['candidates']:
        lines.append('---')
        lines.append(f'## 候选 #{c["id"]}')
        lines.append('')

        # ── 段概览 ──
        seg_dir = c['seg_direction']
        break_bi = c['break_bi']
        pre_bi = c['pre_bi']
        lines.append(f'**段方向**: {seg_dir} | **笔数**: {len(c["seg_bis"])} | **净值**: {c["seg_net_change"]:+.1f}%')
        lines.append('')

        # ── 笔序列（标注特征序列元素） ──
        lines.append('### 笔序列')
        lines.append('')
        target_dir = '下跌' if seg_dir == '上涨' else '上涨'
        lines.append(f'上涨段→特征序列=下跌笔 | 下跌段→特征序列=上涨笔')
        lines.append(f'特征序列方向: {target_dir}')
        lines.append('')

        for bi in c['seg_bis']:
            marker = ' ← 候选' if bi is break_bi else ''
            is_fe = ' [特征序列元素]' if bi['direction'] == target_dir else ''
            lines.append(
                f'  {bi["direction"]} {bi["start_date"]}~{bi["end_date"]} '
                f'{bi["change_pct"]:+.1f}% ({bi["start_price"]:.0f}→{bi["end_price"]:.0f})'
                f'{is_fe}{marker}'
            )
        lines.append('')

        # ── 特征序列 ──
        feat_seq, processed = extract_feature_sequence(c['seg_bis'], seg_dir), []
        if feat_seq:
            _, processed = process_feature_containment(feat_seq, seg_dir)

            lines.append('### 特征序列分析')
            lines.append('')
            lines.append(f'原始特征序列（{len(feat_seq)}个元素）:')
            for i, fe in enumerate(feat_seq):
                low, high = feature_element_range(fe, seg_dir)
                lines.append(f'  元素#{i}: {fe["direction"]} {fe["start_date"]}~{fe["end_date"]} 范围[{low:.0f}~{high:.0f}]')

            if len(feat_seq) != len(processed):
                lines.append(f'')
                lines.append(f'包含处理后（{len(processed)}个元素）:')
                for i, p in enumerate(processed):
                    merge_note = p.get('_merge_note', '')
                    mn = f' | {merge_note}' if merge_note else ''
                    if '_merged_low' in p:
                        lines.append(f'  元素#{i}: 合并范围[{p["_merged_low"]:.0f}~{p["_merged_high"]:.0f}]{mn}')
                    else:
                        low, high = feature_element_range(p, seg_dir)
                        lines.append(f'  元素#{i}: 范围[{low:.0f}~{high:.0f}]{mn}')

            lines.append('')
            is_fractal, fractal_note = check_feature_fractal(processed, seg_dir)
            lines.append(f'特征序列分型检查: {fractal_note}')
            lines.append(f'分型形成: {"✅ 是 → 线段破坏确认" if is_fractal else "❌ 否 → 待确认"}')
            lines.append('')
        else:
            lines.append('### 特征序列分析')
            lines.append('')
            lines.append('特征序列为空（段内无反向笔）→ 无法形成分型')
            lines.append('')

        # ── 笔破坏检查 ──
        lines.append('### 笔破坏检查')
        lines.append('')
        if seg_dir == '上涨' and break_bi['direction'] == '下跌':
            break_low = min(break_bi['start_price'], break_bi['end_price'])
            pre_low = min(pre_bi['start_price'], pre_bi['end_price'])
            broke = break_low < pre_low
            strength = abs(break_bi['change_pct'])
            lines.append(f'  候选笔(下跌): {break_bi["start_price"]:.0f}→{break_bi["end_price"]:.0f}, {break_bi["change_pct"]:+.1f}%')
            lines.append(f'  前笔(下跌)低点: {pre_low:.0f}')
            lines.append(f'  候选笔低点: {break_low:.0f}')
            lines.append(f'  笔破坏: {"✅ 有效 (跌破前低)" if broke else "❌ 未触及 (未跌破前低)"}')
            if strength < 2:
                lines.append(f'  力度: 弱 (<2%), 大概率只是回调')
            elif strength > 5:
                lines.append(f'  力度: 强 (>5%), 大概率是反转')
            else:
                lines.append(f'  力度: 中等 (2-5%), 需后续确认')
        elif seg_dir == '下跌' and break_bi['direction'] == '上涨':
            break_high = max(break_bi['start_price'], break_bi['end_price'])
            pre_high = max(pre_bi['start_price'], pre_bi['end_price'])
            broke = break_high > pre_high
            strength = abs(break_bi['change_pct'])
            lines.append(f'  候选笔(上涨): {break_bi["start_price"]:.0f}→{break_bi["end_price"]:.0f}, {break_bi["change_pct"]:+.1f}%')
            lines.append(f'  前笔(上涨)高点: {pre_high:.0f}')
            lines.append(f'  候选笔高点: {break_high:.0f}')
            lines.append(f'  笔破坏: {"✅ 有效 (突破前高)" if broke else "❌ 未触及 (未突破前高)"}')
            if strength < 2:
                lines.append(f'  力度: 弱 (<2%), 大概率只是回调')
            elif strength > 5:
                lines.append(f'  力度: 强 (>5%), 大概率是反转')
            else:
                lines.append(f'  力度: 中等 (2-5%), 需后续确认')
        lines.append('')

        # ── 后续确认（候选笔之后的3笔） ──
        lines.append('### 后续走势确认')
        lines.append('')
        cand_idx_in_bis = c['break_bi_idx']
        next_bis = all_bis[cand_idx_in_bis + 1:cand_idx_in_bis + 4]

        if next_bis:
            lines.append(f'候选后的{len(next_bis)}笔:')
            for nb in next_bis:
                lines.append(f'  {nb["direction"]} {nb["start_date"]}~{nb["end_date"]} {nb["change_pct"]:+.1f}%')
            # 判断是否延续新段方向
            new_dir = break_bi['direction']
            continued = sum(1 for nb in next_bis if nb['direction'] == new_dir)
            lines.append(f'  新段方向: {new_dir}')
            lines.append(f'  后续{continued}/{len(next_bis)}笔同向延续 → {"✅ 新段确认" if continued >= len(next_bis)//2+1 else "⚠️ 待进一步确认"}')
        else:
            lines.append('(无后续数据 — 这是最后一段)')
        lines.append('')

        # ── 综合判断 ──
        lines.append('### 综合判断')
        lines.append('')
        lines.append('请基于以下三层逻辑推理:')
        lines.append('')
        lines.append('**第一层 — 笔破坏**: 反向笔是否突破前笔极值?')
        lines.append('  → 突破=预警, 不突破=不构成线段破坏')
        lines.append('')
        lines.append('**第二层 — 特征序列分型**: 特征序列(含包含处理)是否形成分型?')
        lines.append('  → 形成分型=线段破坏确认, 不形成=待确认或非破坏')
        lines.append('')
        lines.append('**第三层 — 后续确认**: 笔破坏后后续笔是否延续新方向?')
        lines.append('  → 延续=新段确认, 反转回原方向=假突破')
        lines.append('')
        lines.append('输出JSON:')
        lines.append('```json')
        lines.append('{"id": N, "is_break": true/false, "confidence": "high/medium/low", "reason": "简述三层推理结论"}')
        lines.append('```')
        lines.append('')

    lines.append('\n请逐个判断以上候选。输出JSON数组。')
    return '\n'.join(lines)


def call_llm(prompt, max_retries=3):
    """调用LLM API进行判断"""
    system_prompt = build_teacher_system_prompt()

    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 2000,
        "temperature": 0.01,
        "response_format": {"type": "json_object"},
    }).encode()

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                API_URL, data=body,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST"
            )
            resp = urllib.request.urlopen(req, timeout=120)
            result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"].strip()
            return parse_judgments(content)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 10 * (attempt + 1)
                print(f'  ⏳ 限流, 等待{wait}s...')
                time.sleep(wait)
                continue
            print(f'  ❌ HTTP {e.code}: {e.read().decode()[:200]}')
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 3 * (attempt + 1)
                print(f'  ⚠️ 重试({attempt+1}/{max_retries}): {str(e)[:80]}, 等待{wait}s')
                time.sleep(wait)
                continue
            print(f'  ❌ 失败: {str(e)[:120]}')
            return None
    return None


def parse_judgments(content):
    """解析LLM返回的JSON判断结果"""
    # 尝试提取JSON
    try:
        # 如果整个response就是JSON
        data = json.loads(content)
        if isinstance(data, list):
            return data
        # 如果有"judgments"键
        if isinstance(data, dict):
            for key in ['judgments', 'candidates', 'results']:
                if key in data and isinstance(data[key], list):
                    return data[key]
            # 如果是 {0: {...}, 1: {...}} 格式
            judgments = []
            for k, v in data.items():
                try:
                    jid = int(k)
                    judgments.append({"id": jid, **v})
                except (ValueError, TypeError):
                    pass
            if judgments:
                return judgments
            return [data]
    except json.JSONDecodeError:
        pass

    # 尝试从```json块提取
    import re
    m = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    print(f'  ⚠️ JSON解析失败, 原文前200字: {content[:200]}')
    return None


def judgments_to_map(judgments_list):
    """将LLM返回的list转为 {id: {break, reason}} dict"""
    result = {}
    for j in judgments_list:
        jid = str(j.get('id', 0))
        is_break = j.get('is_break', False)
        if isinstance(is_break, str):
            is_break = is_break.lower() in ('true', 'yes', '是', '1')
        result[jid] = {
            'break': is_break,
            'reason': j.get('reason', ''),
            'confidence': j.get('confidence', 'medium'),
        }
    return result


def process_stock(code, period='daily', force=False):
    """处理一只标的:加载数据 → LLM判断 → 保存JSON"""
    os.makedirs(JUDGMENTS_DIR, exist_ok=True)
    out_path = f'{JUDGMENTS_DIR}/{code}_judgments.json'

    if os.path.exists(out_path) and not force:
        with open(out_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f'  📋 已有标注, 跳过 ({len(data)}个候选)')
        return data

    print(f'  🔄 加载缠论数据...')
    data, bis, candidates = load_chanlun_data(code, period=period)
    if data is None or not candidates:
        print(f'  ⏭️ 无候选段, 跳过')
        return None

    print(f'  🧠 调用LLM判断 {len(candidates)}个候选...')
    prompt = build_teacher_prompt(data, bis)
    judgments_list = call_llm(prompt)

    if judgments_list is None:
        print(f'  ❌ LLM调用失败')
        return None

    judgments_map = judgments_to_map(judgments_list)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(judgments_map, f, ensure_ascii=False, indent=2)
    print(f'  ✅ 保存 -> {out_path} ({len(judgments_map)}个判断)')

    # 统计
    n_break = sum(1 for v in judgments_map.values() if v['break'])
    print(f'     终结: {n_break}/{len(judgments_map)}')

    return judgments_map


def export_training_dataset(codes, output_file=DATASET_OUT):
    """导出LLM标注的训练数据"""
    sys.path.insert(0, '.')
    from tools.chanlun_parser import export_training_record

    records = []
    for code in codes:
        judgments_path = f'{JUDGMENTS_DIR}/{code}_judgments.json'
        if not os.path.exists(judgments_path):
            print(f'  ⏭️ {code}: 无LLM标注, 跳过')
            continue

        with open(judgments_path, 'r', encoding='utf-8') as f:
            judgments = json.load(f)

        print(f'  📦 {code}: 导出训练数据...')
        record = export_training_record(code, period='daily', judgments=judgments)
        if record:
            records.append(record)
            print(f'     {len(record.get("features", []))}根bar')
        else:
            print(f'     ⚠️ 导出失败')

    dataset = {
        'meta': {
            'generated_by': 'teacher_label.py',
            'teacher': 'DeepSeek V4 (LLM segment judgment)',
            'n_stocks': len(records),
            'n_candidates': sum(len(r.get('structure', {}).get('segments', [])) for r in records),
        },
        'records': records,
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, ensure_ascii=False)
    print(f'\n✅ 数据集: {output_file}')
    print(f'   标的: {len(records)}只')
    print(f'   标注段: {dataset["meta"]["n_candidates"]}个')
    return output_file


def train_student():
    """调用训练脚本"""
    print(f'\n🎯 训练Student模型...')
    ret = os.system(f'python {STUDENT_SCRIPT} --augment --dataset {DATASET_OUT}')
    if ret == 0:
        print('✅ 训练完成')
    else:
        print(f'❌ 训练失败 (code={ret})')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Teacher→Student 缠论标注管道')
    parser.add_argument('--codes', nargs='+', default=None, help='标的列表, 默认3只指数')
    parser.add_argument('--all', action='store_true', help='跟踪列表全部14只')
    parser.add_argument('--expand', action='store_true', help='扩展20+只')
    parser.add_argument('--force', action='store_true', help='重新标注(覆盖已有)')
    parser.add_argument('--judge-only', action='store_true', help='只标注, 不训练')
    parser.add_argument('--train-only', action='store_true', help='只训练, 不标注')
    args = parser.parse_args()

    if not DEEPSEEK_KEY:
        print('❌ DEEPSEEK_API_KEY 未设置')
        sys.exit(1)

    if args.codes:
        codes = args.codes
    elif args.all:
        codes = [c for c in NAME_MAP if c in EXPAND_CODES]
    elif args.expand:
        codes = EXPAND_CODES
    else:
        codes = DEFAULT_CODES

    print(f'🔬 Teacher→Student 标注管道')
    print(f'   标的: {len(codes)}只')
    print(f'   模型: {MODEL}')
    print()

    # 阶段1: LLM标注
    if not args.train_only:
        os.makedirs(JUDGMENTS_DIR, exist_ok=True)
        for i, code in enumerate(codes):
            name = NAME_MAP.get(code, code)
            print(f'[{i+1}/{len(codes)}] {code} {name}')
            process_stock(code, force=args.force)
            time.sleep(0.5)  # 避免限流

        # 导出数据集
        dataset_path = export_training_dataset(codes)

    # 阶段2: 训练
    if not args.judge_only:
        train_student()


if __name__ == '__main__':
    main()

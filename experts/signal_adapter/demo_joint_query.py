# -*- coding: utf-8 -*-
"""两个适配器的联合查询演示：同一标的 → 两个专家输出什么 → 融合预览"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from adapter_macro_sector import adapter_macro_sector
from adapter_volume_leader import adapter_volume_leader

macro_signals = {s.stock_code: s for s in adapter_macro_sector()}
vl_signals = {s.stock_code: s for s in adapter_volume_leader()}

common = [c for c in macro_signals if c in vl_signals]
macro_only = [c for c in macro_signals if c not in vl_signals]
vl_only = [c for c in vl_signals if c not in macro_signals]

print(f"重叠标的: {len(common)} 只 | 宏观独有: {len(macro_only)} 只 | 量领独有: {len(vl_only)} 只")
print(f"\n宏观独有 (ETF/指数/跟踪但非量领): {macro_only}")
print()

# 选 5 只做详细展示：3买 + 2卖
sample = []
for c in common:
    if vl_signals[c].S == 1 and sum(1 for x in sample if vl_signals[x].S == 1) < 3:
        sample.append(c)
for c in common:
    if vl_signals[c].S == -1 and sum(1 for x in sample if vl_signals[x].S == -1) < 2:
        sample.append(c)
    if len(sample) >= 5:
        break

for code in sample:
    m = macro_signals[code]
    v = vl_signals[code]

    top_sector = m.raw_data.get("top_sectors", ["无名板块"])[0] if m.raw_data.get("top_sectors") else "无名板块"

    print("=" * 65)
    print(f"  {code}  [{top_sector}]")
    print("=" * 65)
    print(f"  宏观板块  →  C={m.C:.2f}  Pow={m.Pow:.2f}  S={m.S}  G={m.G or '无'}")
    print(f"    ({m.label})")
    print(f"  量    领  →  C={v.C:.2f}  Pow={v.Pow:.2f}  S={v.S:+d}  G={v.G}")
    print(f"    ({v.label})")

    # 简易融合
    fused_c = round(v.C * m.C, 2)
    fused_pow = round(v.Pow * 0.7 + m.Pow * 0.3, 2)
    if v.S == 1:
        verdict = "看多" if fused_c > 0.35 else "中性偏多"
    else:
        verdict = "看空" if fused_c > 0.35 else "谨慎减仓"

    print(f"  ── 融合 → 方向={v.S:+d}  置信={fused_c}({v.C}×{m.C})  强度={fused_pow}  {verdict}")
    print()

# 汇总
print("=" * 65)
print("汇总")
print("=" * 65)
buys = [(c, vl_signals[c]) for c in common if vl_signals[c].S == 1]
sells = [(c, vl_signals[c]) for c in common if vl_signals[c].S == -1]
print(f"看多标的 ({len(buys)}只):")
for c, v in sorted(buys, key=lambda x: x[1].C, reverse=True)[:8]:
    m = macro_signals[c]
    top_s = m.raw_data.get("top_sectors", [""])[0] if m.raw_data.get("top_sectors") else ""
    fused_c = round(v.C * m.C, 2)
    print(f"  {c} G={v.G} C={fused_c} Pow={round(v.Pow*.7+m.Pow*.3,2)} [{top_s}]")

print(f"\n看空标的 ({len(sells)}只):")
for c, v in sorted(sells, key=lambda x: x[1].C, reverse=True)[:5]:
    m = macro_signals[c]
    top_s = m.raw_data.get("top_sectors", [""])[0] if m.raw_data.get("top_sectors") else ""
    fused_c = round(v.C * m.C, 2)
    print(f"  {c} G={v.G} C={fused_c} Pow={round(v.Pow*.7+m.Pow*.3,2)} [{top_s}]")

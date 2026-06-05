# -*- coding: utf-8 -*-
"""
批量测试背驰 th 参数：遍历不同阈值，记录 divergence 贡献和 AUC
"""
import os, sys, warnings, time
from datetime import datetime
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 为每次运行清空 import 缓存，确保参数生效
from notebook.explore_xgb import load_all_codes, build_dataset, run_xgboost

codes = load_all_codes()
print(f"标的: {len(codes)} 只")

th_values = [0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
MAX_CODES = 20  # 快速模式
N_DAYS = 20
THRESHOLD = 0.05

results = []
for th in th_values:
    t0 = datetime.now()
    print(f"\n{'='*60}")
    print(f"th={th:.2f}  (离开段面积 <= 进入段面积 × {th:.0%})")
    print(f"{'='*60}")

    X, y = build_dataset(codes, n_days=N_DAYS, threshold=THRESHOLD,
                         max_codes=MAX_CODES, div_th=th)

    # 统计 divergence 分布
    non_zero = (X["cl_divergence"].abs() > 0.001).sum()
    total = len(X)
    print(f"  divergence 非零: {non_zero}/{total} ({non_zero/total*100:.2f}%)")

    model, importance, metrics = run_xgboost(X, y, time_split=False)

    # 提取 divergence 相关特征的重要性
    div_imps = importance[importance["feature"].isin(
        ["cl_divergence", "ix_trend_x_div", "ix_zs2_x_div"]
    )]
    div_total = div_imps["importance"].sum() if len(div_imps) > 0 else 0
    cl_div_imp = div_imps.loc[div_imps["feature"] == "cl_divergence", "importance"].values
    cl_div_imp = cl_div_imp[0] if len(cl_div_imp) > 0 else 0

    row = {
        "th": th,
        "auc": metrics["auc"],
        "n_test": metrics["n_test"],
        "non_zero_pct": round(non_zero / total * 100, 2),
        "cl_divergence_imp": round(cl_div_imp, 4),
        "div_family_imp": round(div_total, 4),
        "elapsed_s": int((datetime.now() - t0).total_seconds()),
    }
    results.append(row)

    print(f"\n  >>> AUC={row['auc']:.4f} | div_imp={row['cl_divergence_imp']:.4f} | "
          f"div_family={row['div_family_imp']:.4f} | {row['elapsed_s']}s")

# 汇总
print(f"\n\n{'='*60}")
print(f"背驰 th 参数批量测试汇总 (MAX_CODES={MAX_CODES}, N_DAYS={N_DAYS})")
print(f"{'='*60}")
print(f"{'th':>6} | {'AUC':>6} | {'非零%':>8} | {'div_imp':>8} | {'div家族':>8} | {'耗时':>6}")
print("-" * 60)
df = pd.DataFrame(results)
for _, r in df.iterrows():
    print(f"{r['th']:>6.2f} | {r['auc']:>6.4f} | {r['non_zero_pct']:>7.2f}% | "
          f"{r['cl_divergence_imp']:>8.4f} | {r['div_family_imp']:>8.4f} | {r['elapsed_s']:>5}s")
print("=" * 60)

# 保存
out_path = os.path.join(os.path.dirname(__file__), "batch_divth_results.csv")
df.to_csv(out_path, index=False)
print(f"\n结果保存至: {out_path}")

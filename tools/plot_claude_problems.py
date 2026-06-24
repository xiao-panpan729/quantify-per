import sys
sys.path.insert(0, 'D:/miniconda3/Lib/site-packages')
from pytdx.reader import TdxDailyBarReader
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

# Read SH000001 daily data
reader = TdxDailyBarReader()
df = reader.get_df('C:/zd_cjzq/vipdoc/sh/lday/sh000001.day')
for col in ['open', 'high', 'low', 'close']:
    df[col] = df[col] / 1000

# ========== Plot 1: Candidate #2 ==========
# 2016-01-06 ~ 2016-02-22
# bi0: 下跌  2016-01-06~2016-02-01  3362.97->2638.30
# bi1: 上涨  2016-02-15~2016-02-22  2682.09->2933.96  <- 候选笔
# Claude判断: invalid (置信度: low)
# 理由: 上涨笔未突破前笔高点

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
fig.suptitle('Claude标注问题 #2: n_bis=2 只有2笔就判invalid', fontsize=14, fontweight='bold')

# Plot 1: Full context (2015-10 to 2016-04)
mask1 = (df.index >= '2015-10-01') & (df.index <= '2016-04-30')
ctx1 = df[mask1].copy()
ctx1['idx'] = range(len(ctx1))

ax1.set_title('大背景：2015年底~2016年初（熔断行情）', fontsize=11)
for i in range(len(ctx1)):
    color = 'red' if ctx1.iloc[i]['close'] >= ctx1.iloc[i]['open'] else 'green'
    ax1.plot([ctx1.iloc[i]['idx'], ctx1.iloc[i]['idx']], 
              [ctx1.iloc[i]['low'], ctx1.iloc[i]['high']], 
              color=color, linewidth=0.8, alpha=0.7)
    ax1.plot([ctx1.iloc[i]['idx']-0.3, ctx1.iloc[i]['idx']+0.3],
              [ctx1.iloc[i]['open'], ctx1.iloc[i]['close']],
              color=color, linewidth=2.5, alpha=0.8)

# Mark key points
idx_3684 = ctx1.index.get_indexer(['2015-12-23'], method='nearest')[0]
ax1.annotate('前笔高点\n3684.57\n(2015-12-23)', 
             xy=(ctx1.iloc[idx_3684]['idx'], 3684.57),
             xytext=(ctx1.iloc[idx_3684]['idx']-15, 3750),
             arrowprops=dict(arrowstyle='->', color='red', lw=1.5),
             fontsize=9, color='red', fontweight='bold')

idx_2638 = ctx1.index.get_indexer(['2016-02-01'], method='nearest')[0]
ax1.annotate('前笔低点\n2638.30\n(2016-02-01)', 
             xy=(ctx1.iloc[idx_2638]['idx'], 2638.30),
             xytext=(ctx1.iloc[idx_2638]['idx']+5, 2500),
             arrowprops=dict(arrowstyle='->', color='green', lw=1.5),
             fontsize=9, color='green', fontweight='bold')

ax1.set_ylabel('Price')
ax1.grid(True, alpha=0.3)
ax1.set_ylim(2400, 3900)

# Plot 2: Candidate #2 detail
mask2 = (df.index >= '2016-01-06') & (df.index <= '2016-02-22')
seg2 = df[mask2].copy()
seg2['idx'] = range(len(seg2))

ax2.set_title('Candidate #2 细节：2016-01-06 ~ 2016-02-22', fontsize=11)
for i in range(len(seg2)):
    color = 'red' if seg2.iloc[i]['close'] >= seg2.iloc[i]['open'] else 'green'
    ax2.plot([seg2.iloc[i]['idx'], seg2.iloc[i]['idx']], 
              [seg2.iloc[i]['low'], seg2.iloc[i]['high']], 
              color=color, linewidth=1.2, alpha=0.8)
    ax2.plot([seg2.iloc[i]['idx']-0.3, seg2.iloc[i]['idx']+0.3],
              [seg2.iloc[i]['open'], seg2.iloc[i]['close']],
              color=color, linewidth=3, alpha=0.9)

# Mark bi0 (下跌笔)
idx_b0s = seg2.index.get_indexer(['2016-01-06'], method='nearest')[0]
idx_b0e = seg2.index.get_indexer(['2016-02-01'], method='nearest')[0]
ax2.plot([seg2.iloc[idx_b0s]['idx'], seg2.iloc[idx_b0e]['idx']], 
          [3362.97, 2638.30], color='green', linewidth=2.5, linestyle='-', alpha=0.8)
ax2.annotate('bi0 下跌笔\n3362.97 -> 2638.30', 
             xy=(seg2.iloc[idx_b0e]['idx'], 2638.30),
             xytext=(seg2.iloc[idx_b0e]['idx']+1, 2500),
             fontsize=9, color='green', fontweight='bold')

# Mark bi1 (上涨笔，候选)
idx_b1s = seg2.index.get_indexer(['2016-02-15'], method='nearest')[0]
idx_b1e = seg2.index.get_indexer(['2016-02-22'], method='nearest')[0]
ax2.plot([seg2.iloc[idx_b1s]['idx'], seg2.iloc[idx_b1e]['idx']], 
          [2682.09, 2933.96], color='red', linewidth=2.5, linestyle='-', alpha=0.8)
ax2.annotate('bi1 上涨笔(候选)\n2682.09 -> 2933.96\n未突破3684.57', 
             xy=(seg2.iloc[idx_b1e]['idx'], 2933.96),
             xytext=(seg2.iloc[idx_b1e]['idx']-3, 3100),
             fontsize=9, color='red', fontweight='bold')

# Mark the problem
ax2.axhline(y=3684.57, color='red', linewidth=1, linestyle='--', alpha=0.6, label='前笔高点 3684')
ax2.text(len(seg2)-1, 3700, 'Claude的invalid理由:\n"上涨笔未突破前笔高点3684"\n\n问题: 只有2笔,\n不足以确认线段结束', 
         fontsize=10, color='red', fontweight='bold',
         bbox=dict(boxstyle='round,pad=0.5', facecolor='yellow', alpha=0.7))

ax2.set_ylabel('Price')
ax2.grid(True, alpha=0.3)
ax2.set_ylim(2500, 3800)
ax2.legend()

plt.tight_layout()
plt.savefig('D:/quantify-per/training_data/teacher_judgments/problem_2.png', dpi=100, bbox_inches='tight')
print('Plot #2 saved')
plt.close()

# ========== Plot 2: Candidate #14 ==========
# 2022-07-08 ~ 2022-12-07
# bi0: 下跌  2022-07-08~2022-10-31  3386.31->2885.09
# bi1-2: 下跌（小笔）
# bi3: 上涨  2022-12-02~2022-12-07  3149.84->3226.08  <- 候选笔
# Claude判断: valid_break (置信度: medium)
# 理由: 上涨笔突破前笔高点，幅度2.4%

fig2, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
fig2.suptitle('Claude标注问题 #14: 幅度2.4%就判valid_break', fontsize=14, fontweight='bold')

# Plot 1: Full context (2022-01 to 2023-01)
mask1 = (df.index >= '2022-01-01') & (df.index <= '2023-01-31')
ctx1 = df[mask1].copy()
ctx1['idx'] = range(len(ctx1))

ax1.set_title('大背景：2022年（全年下跌）', fontsize=11)
for i in range(len(ctx1)):
    color = 'red' if ctx1.iloc[i]['close'] >= ctx1.iloc[i]['open'] else 'green'
    ax1.plot([ctx1.iloc[i]['idx'], ctx1.iloc[i]['idx']], 
              [ctx1.iloc[i]['low'], ctx1.iloc[i]['high']], 
              color=color, linewidth=0.8, alpha=0.7)
    ax1.plot([ctx1.iloc[i]['idx']-0.3, ctx1.iloc[i]['idx']+0.3],
              [ctx1.iloc[i]['open'], ctx1.iloc[i]['close']],
              color=color, linewidth=2.5, alpha=0.8)

# Mark key points
idx_3386 = ctx1.index.get_indexer(['2022-07-08'], method='nearest')[0]
ax1.annotate('前笔起点\n3386.31\n(2022-07-08)', 
             xy=(ctx1.iloc[idx_3386]['idx'], 3386.31),
             xytext=(ctx1.iloc[idx_3386]['idx'], 3550),
             arrowprops=dict(arrowstyle='->', color='blue', lw=1.5),
             fontsize=9, color='blue', fontweight='bold')

idx_2885 = ctx1.index.get_indexer(['2022-10-31'], method='nearest')[0]
ax1.annotate('前笔终点(大跌)\n2885.09\n(2022-10-31)', 
             xy=(ctx1.iloc[idx_2885]['idx'], 2885.09),
             xytext=(ctx1.iloc[idx_2885]['idx']+5, 2700),
             arrowprops=dict(arrowstyle='->', color='green', lw=1.5),
             fontsize=9, color='green', fontweight='bold')

ax1.set_ylabel('Price')
ax1.grid(True, alpha=0.3)
ax1.set_ylim(2800, 3600)

# Plot 2: Candidate #14 detail
mask2 = (df.index >= '2022-07-08') & (df.index <= '2022-12-07')
seg14 = df[mask2].copy()
seg14['idx'] = range(len(seg14))

ax2.set_title('Candidate #14 细节：2022-07-08 ~ 2022-12-07', fontsize=11)
for i in range(len(seg14)):
    color = 'red' if seg14.iloc[i]['close'] >= seg14.iloc[i]['open'] else 'green'
    ax2.plot([seg14.iloc[i]['idx'], seg14.iloc[i]['idx']], 
              [seg14.iloc[i]['low'], seg14.iloc[i]['high']], 
              color=color, linewidth=1.2, alpha=0.8)
    ax2.plot([seg14.iloc[i]['idx']-0.3, seg14.iloc[i]['idx']+0.3],
              [seg14.iloc[i]['open'], seg14.iloc[i]['close']],
              color=color, linewidth=3, alpha=0.9)

# Mark the big drop (bi0)
idx_b0s = seg14.index.get_indexer(['2022-07-08'], method='nearest')[0]
idx_b0e = seg14.index.get_indexer(['2022-10-31'], method='nearest')[0]
ax2.plot([seg14.iloc[idx_b0s]['idx'], seg14.iloc[idx_b0e]['idx']], 
          [3386.31, 2885.09], color='green', linewidth=2.5, linestyle='-', alpha=0.8)
ax2.annotate('bi0 大跌笔\n3386.31 -> 2885.09\n幅度 -14.8%', 
             xy=(seg14.iloc[idx_b0e]['idx'], 2885.09),
             xytext=(seg14.iloc[idx_b0e]['idx']+1, 2700),
             fontsize=9, color='green', fontweight='bold')

# Mark the small rebound (bi3, candidate)
idx_b3s = seg14.index.get_indexer(['2022-12-02'], method='nearest')[0]
idx_b3e = seg14.index.get_indexer(['2022-12-07'], method='nearest')[0]
ax2.plot([seg14.iloc[idx_b3s]['idx'], seg14.iloc[idx_b3e]['idx']], 
          [3149.84, 3226.08], color='red', linewidth=2.5, linestyle='-', alpha=0.8)
ax2.annotate('bi3 小反弹(候选)\n3149.84 -> 3226.08\n幅度 +2.4%', 
             xy=(seg14.iloc[idx_b3e]['idx'], 3226.08),
             xytext=(seg14.iloc[idx_b3e]['idx']-5, 3350),
             fontsize=9, color='red', fontweight='bold')

# Mark the problem
ax2.axhline(y=3386.31, color='blue', linewidth=1, linestyle='--', alpha=0.6, label='前笔高点 3386')
ax2.text(len(seg14)-1, 3450, 'Claude的valid_break理由:\n"上涨笔突破前笔高点2.4%"\n\n问题: 前一笔大跌14.8%,\n一根小阳线就反转？\n且突破幅度极小(2.4%)', 
         fontsize=10, color='red', fontweight='bold',
         bbox=dict(boxstyle='round,pad=0.5', facecolor='orange', alpha=0.7))

ax2.set_ylabel('Price')
ax2.grid(True, alpha=0.3)
ax2.set_ylim(2800, 3500)
ax2.legend()

plt.tight_layout()
plt.savefig('D:/quantify-per/training_data/teacher_judgments/problem_14.png', dpi=100, bbox_inches='tight')
print('Plot #14 saved')
plt.close()

print('Done')

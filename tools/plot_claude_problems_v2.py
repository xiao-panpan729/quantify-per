import sys
sys.path.insert(0, 'D:/miniconda3/Lib/site-packages')
from pytdx.reader import TdxDailyBarReader
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# Read SH000001 daily data
reader = TdxDailyBarReader()
df = reader.get_df('C:/zd_cjzq/vipdoc/sh/lday/sh000001.day')
for col in ['open', 'high', 'low', 'close']:
    df[col] = df[col] / 1000

# Fix: use UTF-8 compatible approach - draw English labels but add Chinese in a separate text file
# Actually, let's just use English for reliability

# ========== Plot: Candidate #2 Problem ==========
fig, ax = plt.subplots(1, 1, figsize=(16, 6))

# Data range: 2015-10 to 2016-04
mask = (df.index >= '2015-10-01') & (df.index <= '2016-04-30')
data = df[mask].copy()
data['i'] = range(len(data))

# Draw candles
for i in range(len(data)):
    color = 'red' if data.iloc[i]['close'] >= data.iloc[i]['open'] else 'green'
    # wick
    ax.plot([data.iloc[i]['i'], data.iloc[i]['i']],
             [data.iloc[i]['low'], data.iloc[i]['high']],
             color=color, linewidth=0.7, alpha=0.6)
    # body
    ax.plot([data.iloc[i]['i']-0.3, data.iloc[i]['i']+0.3],
             [data.iloc[i]['open'], data.iloc[i]['close']],
             color=color, linewidth=2.5, alpha=0.8)

# Key prices
price_3684 = 3684.57  # 2015-12-23 high
price_2638 = 2638.30  # 2016-02-01 low
price_2682 = 2682.09  # 2016-02-15 open (bottom)
price_2933 = 2933.96  # 2016-02-22 close (candidate bi end)

# Find x positions
idx_3684 = data.index.get_indexer(['2015-12-23'], method='nearest')[0]
idx_2638 = data.index.get_indexer(['2016-02-01'], method='nearest')[0]
idx_2682 = data.index.get_indexer(['2016-02-15'], method='nearest')[0]
idx_2933 = data.index.get_indexer(['2016-02-22'], method='nearest')[0]

# Draw bi0 (drop)
ax.plot([data.iloc[idx_3684]['i'], data.iloc[idx_2638]['i']],
         [price_3684, price_2638], color='green', linewidth=3, alpha=0.9)
# Arrow for bi0
ax.annotate('', xy=(data.iloc[idx_2638]['i'], price_2638),
             xytext=(data.iloc[idx_3684]['i'], price_3684),
             arrowprops=dict(arrowstyle='->', color='green', lw=2.5))

# Draw bi1 (rebound, candidate)
ax.plot([data.iloc[idx_2682]['i'], data.iloc[idx_2933]['i']],
         [price_2682, price_2933], color='red', linewidth=3, alpha=0.9)
ax.annotate('', xy=(data.iloc[idx_2933]['i'], price_2933),
             xytext=(data.iloc[idx_2682]['i'], price_2682),
             arrowprops=dict(arrowstyle='->', color='red', lw=2.5))

# Mark previous high (the "not broken" level)
ax.axhline(y=price_3684, color='red', linewidth=1.5, linestyle='--', alpha=0.7)
ax.text(data.iloc[idx_3684]['i']-20, price_3684+30,
        'Prev high = 3684 (NOT broken)',
        fontsize=9, color='red', fontweight='bold')

# Problem annotation
prob_text = (
    'PROBLEM #2\n'
    'Claude: invalid (only 2 bi)\n'
    'Issue: With only 2 bi, cannot confirm segment end.\n'
    'The rebound (bi1) not breaking 3684 does NOT prove\n'
    'the uptrend is over.'
)
ax.text(len(data)*0.65, 3700, prob_text,
         fontsize=10, color='red', fontweight='bold',
         bbox=dict(boxstyle='round,pad=0.6', facecolor='yellow', alpha=0.8))

# Mark 2-bi problem
ax.text(data.iloc[idx_2933]['i']+0.5, price_2933+80,
        'Only 2 bi!\nCannot judge!',
        fontsize=9, color='red', fontweight='bold',
        bbox=dict(boxstyle='round,pad=0.4', facecolor='orange', alpha=0.7))

ax.set_title('Problem #2: Candidate #2 (2016-01 to 2016-02)', fontsize=13, fontweight='bold')
ax.set_ylabel('Price')
ax.set_xlabel('Bar Index')
ax.grid(True, alpha=0.3)
ax.set_ylim(2400, 4000)

plt.tight_layout()
plt.savefig('D:/quantify-per/training_data/teacher_judgments/problem_2_clean.png', dpi=120, bbox_inches='tight')
print('Plot #2 saved')
plt.close()

# ========== Plot: Candidate #14 Problem ==========
fig, ax = plt.subplots(1, 1, figsize=(16, 6))

# Data range: 2022-07 to 2022-12
mask = (df.index >= '2022-07-01') & (df.index <= '2022-12-31')
data = df[mask].copy()
data['i'] = range(len(data))

# Draw candles
for i in range(len(data)):
    color = 'red' if data.iloc[i]['close'] >= data.iloc[i]['open'] else 'green'
    ax.plot([data.iloc[i]['i'], data.iloc[i]['i']],
             [data.iloc[i]['low'], data.iloc[i]['high']],
             color=color, linewidth=0.7, alpha=0.6)
    ax.plot([data.iloc[i]['i']-0.3, data.iloc[i]['i']+0.3],
             [data.iloc[i]['open'], data.iloc[i]['close']],
             color=color, linewidth=2.5, alpha=0.8)

# Key prices
price_3386 = 3386.31  # 2022-07-08 high (bi0 start)
price_2885 = 2885.09  # 2022-10-31 low (big drop end)
price_3149 = 3149.84  # 2022-12-02 open (rebound start)
price_3226 = 3226.08  # 2022-12-07 close (candidate bi end)

# Find x positions
idx_3386 = data.index.get_indexer(['2022-07-08'], method='nearest')[0]
idx_2885 = data.index.get_indexer(['2022-10-31'], method='nearest')[0]
idx_3149 = data.index.get_indexer(['2022-12-02'], method='nearest')[0]
idx_3226 = data.index.get_indexer(['2022-12-07'], method='nearest')[0]

# Draw bi0 (big drop)
ax.plot([data.iloc[idx_3386]['i'], data.iloc[idx_2885]['i']],
         [price_3386, price_2885], color='green', linewidth=3, alpha=0.9)
ax.annotate('', xy=(data.iloc[idx_2885]['i'], price_2885),
             xytext=(data.iloc[idx_3386]['i'], price_3386),
             arrowprops=dict(arrowstyle='->', color='green', lw=2.5))

# Draw bi3 (small rebound, candidate)
ax.plot([data.iloc[idx_3149]['i'], data.iloc[idx_3226]['i']],
         [price_3149, price_3226], color='red', linewidth=3, alpha=0.9)
ax.annotate('', xy=(data.iloc[idx_3226]['i'], price_3226),
             xytext=(data.iloc[idx_3149]['i'], price_3149),
             arrowprops=dict(arrowstyle='->', color='red', lw=2.5))

# Mark the previous high
ax.axhline(y=price_3386, color='blue', linewidth=1.5, linestyle='--', alpha=0.7)
ax.text(data.iloc[idx_3386]['i']-5, price_3386+30,
        'Prev high = 3386 (broken by +2.4%)',
        fontsize=9, color='blue', fontweight='bold')

# Show the drop magnitude
drop_pct = (price_2885 - price_3386) / price_3386 * 100
rebound_pct = (price_3226 - price_3149) / price_3149 * 100
ax.text(len(data)*0.6, 3450,
        f'Big drop: {drop_pct:.1f}%\n(bi0: 3386 -> 2885)\n\nSmall rebound: +{rebound_pct:.1f}%\n(bi3: 3149 -> 3226)\n\nClaude: valid_break (+2.4% enough?)',
        fontsize=10, color='red', fontweight='bold',
        bbox=dict(boxstyle='round,pad=0.6', facecolor='orange', alpha=0.8))

# Problem annotation
prob_text = (
    'PROBLEM #14\n'
    'Claude: valid_break (confidence: medium)\n'
    'Issue: After a -14.8% drop, a +2.4% rebound\n'
    'barely breaking the previous high.\n'
    'Is this really a trend reversal?'
)
ax.text(len(data)*0.02, 3400, prob_text,
         fontsize=10, color='red', fontweight='bold',
         bbox=dict(boxstyle='round,pad=0.6', facecolor='yellow', alpha=0.8))

ax.set_title('Problem #14: Candidate #14 (2022-07 to 2022-12)', fontsize=13, fontweight='bold')
ax.set_ylabel('Price')
ax.set_xlabel('Bar Index')
ax.grid(True, alpha=0.3)
ax.set_ylim(2750, 3550)

plt.tight_layout()
plt.savefig('D:/quantify-per/training_data/teacher_judgments/problem_14_clean.png', dpi=120, bbox_inches='tight')
print('Plot #14 saved')
plt.close()

print('All plots saved.')

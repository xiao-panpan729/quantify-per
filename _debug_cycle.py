import sys
sys.path.insert(0, 'd:/quantify-per')
from cycle_engine.utils import read_csv

daily = read_csv('sh000001', 'daily')
min30 = read_csv('sh000001', 'min30')
min60 = read_csv('sh000001', 'min60')

last = daily[-1]
print(f"daily: close={float(last['close']):.4f} e12={float(last['expma12']):.4f} e50={float(last['expma50']):.4f}")
print(f"e12>e50: {float(last['expma12']) > float(last['expma50'])}")
print(f"close<e12: {float(last['close']) < float(last['expma12'])}")
print(f"close<e50: {float(last['close']) < float(last['expma50'])}")

# Find last sell index
last_sell_idx = None
for i in range(len(daily) - 1, -1, -1):
    if str(daily[i].get('sell_signal', '')).strip():
        last_sell_idx = i
        break
print(f"\nlast_sell_idx={last_sell_idx}")
print(f"sell bar: {daily[last_sell_idx].get('date','')[:10]} close={float(daily[last_sell_idx]['close']):.4f}")

# Check ref_row (before sell)
if last_sell_idx and last_sell_idx > 0:
    ref = daily[last_sell_idx - 1]
    print(f"ref bar (before sell): {ref.get('date','')[:10]} close={float(ref['close']):.4f} e12={float(ref['expma12']):.4f} e50={float(ref['expma50']):.4f}")

# Count sell since last_sell_idx
sell_count = sum(1 for r in daily[last_sell_idx:] if str(r.get('sell_signal', '')).strip())
print(f"sell_count from idx={sell_count}")

# Check 30/60 min expma
e30_12 = float(min30[-1].get('expma12', 0))
e30_50 = float(min30[-1].get('expma50', 0))
e60_12 = float(min60[-1].get('expma12', 0))
e60_50 = float(min60[-1].get('expma50', 0))
print(f"\n30min: e12={e30_12:.4f} e50={e30_50:.4f} golden={e30_12>e30_50}")
print(f"60min: e12={e60_12:.4f} e50={e60_50:.4f} golden={e60_12>e60_50}")

# 60min buy count
sell_date = str(daily[last_sell_idx].get('date', ''))[:10]
end_idx = len(min60)
for j in range(len(min60) - 1, -1, -1):
    if str(min60[j].get('date', ''))[:10] <= sell_date:
        end_idx = j
        break
start_idx = max(0, end_idx - 240)
buy60 = [r for r in min60[start_idx:end_idx] if str(r.get('buy_signal', '')).strip()]
print(f"\n60min★买 before sell: {len(buy60)}")
for b in buy60:
    print(f"  {b.get('date','')[:16]} buy={b.get('buy_signal','')}")

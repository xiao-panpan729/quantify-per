import sys
sys.path.insert(0, 'd:/quantify-per')
from cycle_engine import analyze

r = analyze('sh000001', '上证指数')
t = r['trend']
print(f"score={t['score']} dir={t['direction']}")
print(f"MACD={t['macd_score']} MA={t['ma_score']} cycle={t['cycle_score']}")
for d in t['details']:
    print(f"  {d}")

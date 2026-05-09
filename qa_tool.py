#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号快速查验工具 v2

用法:
  python qa_tool.py sz159740 min5         # 4/09起完整流水
  python qa_tool.py sz159740 min5 20260424 # 从指定日期起
  python qa_tool.py                # 全市场5分钟胜率对比(终端)
  python qa_tool.py sz159740 min5 --fix   # 修正版(★卖→下一个死叉)
"""
import sys, csv
sys.path.insert(0, 'D:/quantify-per')
from backtest_signals import read_csv

NAME_MAP = {
    'sz159740': '恒生科技ETF', 'sh520600': '港股汽车ETF',
    'sh513120': '创新药ETF',    'sz159326': '电网设备ETF',
    'sh513310': '中韩半导体',   'sh588200': '科创芯片',
    'sz002261': '拓维信息',     'sz300118': '东方日升',
    'sz000100': 'TCL科技',      'sz002129': 'TCL中环',
    'sh600438': '通威股份',     'sh601012': '隆基绿能',
}

def _fv(v):
    try: return float(v)
    except: return 0.0

def scan_all():
    """全市场5分钟胜率对比 — 纯终端输出"""
    codes = list(NAME_MAP.keys())
    print(f'\n{"代码":>12} {"名称":<8} {"笔数":>4} {"胜":>4} {"负":>4} {"胜率":>6} {"均利润":>8}')
    print('-' * 54)

    for code in codes:
        rows = read_csv(code, 'min5')
        bt=bw=bl=0; sp=0
        i=0
        while i < len(rows):
            if rows[i].get('timestamp','') < '20260409': i+=1; continue
            if rows[i].get('buy_signal','').strip():
                gc=[]; lw=None; f=False; si=None
                j=i+1
                while j<len(rows):
                    rj=rows[j]; cross=rj.get('expma_cross','').strip(); cj=_fv(rj.get('raw_close',0))
                    if '金叉' in cross:
                        p=gc[-1]['idx'] if gc else i
                        bl=min(_fv(rows[k].get('raw_close',0)) for k in range(p,j+1))
                        if lw is None: lw=bl; ok=True
                        elif bl>=lw: ok=True; lw=min(lw,bl)
                        else: ok=False
                        if ok: gc.append({'idx':j,'c':cj})
                    if rj.get('sell_signal','').strip(): si=j; f=True; break
                    j+=1
                if not f: j=len(rows)-1
                if gc:
                    mx=max(_fv(rows[k].get('raw_close',0)) for k in range(gc[0]['idx'],j+1))
                    for g in gc:
                        pkt=(mx-g['c'])/g['c']*100; bt+=1; sp+=pkt
                        if pkt>0: bw+=1
                        else: bl+=1
            i+=1

        avg_pct = sp/bt if bt else 0
        wr = bw/bt*100 if bt else 0
        m = ' ★' if avg_pct >= 2.0 else ''
        print(f'{code:>12} {NAME_MAP.get(code,""):<8} {bt:>4} {bw:>4} {bl:>4} {wr:>5.1f}% {avg_pct:>+7.2f}%{m}')

    print('-' * 54)

def scan_one(code, period, start='20260409', fix=False):
    """单标的单周期逐笔"""
    rows = read_csv(code, period)
    rs = [r for r in rows if r.get('timestamp','') >= start]
    if not rs: print('无数据'); return
    print(f'=== {code} {period} ({len(rs)}根, {start}起) ===\n')

    sigs = [(r['timestamp'], r.get('buy_signal','').strip() or r.get('sell_signal','').strip(),
             r.get('expma_cross','').strip(), int(r['raw_close'])) for r in rs
            if r.get('buy_signal','').strip() or r.get('sell_signal','').strip() or r.get('expma_cross','').strip()]
    print(f'信号: {len([s for s in sigs if s[1]])}个')
    for ts, sig, cross, close in sigs:
        print(f'  {ts} close={close}({close/10000:.4f})  {sig:>4}  {cross}', end='')
        if '金叉' in cross: print(f' ✅', end='')
        elif '死叉' in cross: print(f' ❌', end='')
        print()

    bt=0
    i=0
    while i < len(rows):
        ts=rows[i].get('timestamp','')
        if ts < start: i+=1; continue
        bs=rows[i].get('buy_signal','').strip()
        if not bs: i+=1; continue

        gc=[]; lw=None; sell_j=None; dead_j=None; data_end=len(rows)-1
        j=i+1
        while j<len(rows):
            rj=rows[j]; cross=rj.get('expma_cross','').strip(); cj=_fv(rj.get('raw_close',0))
            if '金叉' in cross:
                p=gc[-1]['idx'] if gc else i
                bl=min(_fv(rows[k].get('raw_close',0)) for k in range(p,j+1))
                if lw is None: lw=bl; ok=True
                elif bl>=lw: ok=True; lw=min(lw,bl)
                else: ok=False
                if ok: gc.append({'idx':j,'ts':rj.get('timestamp',''),'c':cj,'bl':bl})
            if rj.get('sell_signal','').strip() and sell_j is None:
                sell_j=j; sell_r=rj
            if fix and sell_j is not None and '死叉' in cross:
                dead_j=j; break
            if not fix and rj.get('sell_signal','').strip():
                break
            j+=1
        if not fix and j>=len(rows)-1: j=len(rows)-1

        if sell_j is not None or j>=len(rows)-1:
            if fix:
                exit_j = max(sell_j or 0, dead_j or (len(rows)-1))
            else:
                exit_j = j
            if gc:
                bt+=1
                mx=max(_fv(rows[k].get('raw_close',0)) for k in range(gc[0]['idx'], exit_j+1))
                exit_sig = ('★卖' + rows[exit_j]['timestamp']) if not fix else (
                    '★卖→死叉' + rows[exit_j]['timestamp'])
                print(f'\n  买段{bt} ★买:{ts} → {exit_sig}')
                for gi,g in enumerate(gc):
                    pct=(mx-g['c'])/g['c']*100
                ec = g['c']; ebl = g['bl']
                print('    金叉%d: %s e=%d(%.4f) low=%d(%.4f) mx=%d(%.4f) +%.2f%%' %
                      (gi+1, g['ts'], ec, ec/10000, ebl, ebl/10000, mx, mx/10000, pct))
        i+=1
    if bt:
        print(f'\n总计: {bt}笔买信号')

if __name__ == '__main__':
    if len(sys.argv) >= 3:
        code = sys.argv[1]
        period = sys.argv[2]
        start = sys.argv[3] if len(sys.argv) > 3 else '20260409'
        fix = '--fix' in sys.argv
        scan_one(code, period, start, fix)
    else:
        scan_all()

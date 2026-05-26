# -*- coding: utf-8 -*-
"""
筹码峰数据全量解压器 v1.0
从 D:/筹码峰/ 批量解压 A股筹码分布数据到 D:/quantify-per/data/chips/

解压内容:
  - 2025/2025.7z → chips/yearly/2025/   (全部 sh/sz 个股)
  - 2026/每日数据/*.7z → chips/daily/YYYYMMDD/ (每天全部 sh/sz 个股)
  
排除: bj (北交所) 前缀文件
"""

import py7zr
import os
import sys
import time
from pathlib import Path
from config import PROJECT_ROOT

# 筹码峰源数据目录（外部数据，不在项目内）
SOURCE_DIR = r'D:\筹码峰'
OUTPUT_BASE = os.path.join(PROJECT_ROOT, 'data', 'chips')

# 只解压 A 股（sh + sz 前缀），跳过 bj 北交所
VALID_PREFIXES = ('sh', 'sz')


def extract_yearly(year_str):
    """解压某年的年度归档"""
    arc_path = os.path.join(SOURCE_DIR, f'{year_str}/{year_str}.7z')
    out_dir = os.path.join(OUTPUT_BASE, 'yearly', year_str)
    
    if not os.path.exists(arc_path):
        print(f'  [!] 年度归档不存在: {arc_path}')
        return 0
    
    os.makedirs(out_dir, exist_ok=True)
    
    # 列出所有文件名，筛选 A 股
    with py7zr.SevenZipFile(arc_path, 'r') as z:
        all_names = z.getnames()
        a_stock_files = [n for n in all_names 
                        if n.count('/') == 1 and n.split('/')[1].startswith(VALID_PREFIXES)]
        non_a_stock = len(all_names) - len(a_stock_files)
        
        print(f'  归档内总计 {len(all_names)} 文件, A股 {len(a_stock_files)}, 跳过北交所 {non_a_stock}')
        
        if a_stock_files == 0:
            print('  [!] 没有找到A股文件')
            return 0
        
        # 分批提取 (避免内存爆炸)
        BATCH_SIZE = 200
        extracted = 0
        start_t = time.time()
        
        for i in range(0, len(a_stock_files), BATCH_SIZE):
            batch = a_stock_files[i:i+BATCH_SIZE]
            try:
                with py7zr.SevenZipFile(arc_path, 'r') as bz:
                    bz.extract(targets=batch, path=out_dir)
                extracted += len(batch)
                
                # 进度显示
                pct = extracted / len(a_stock_files) * 100
                elapsed = time.time() - start_t
                speed = extracted / elapsed if elapsed > 0 else 0
                eta = (len(a_stock_files) - extracted) / speed if speed > 0 else 0
                
                sys.stdout.write(f'\r    进度: {extracted}/{len(a_stock_files)} ({pct:.1f}%) '
                               f'速度: {speed:.0f}文件/s ETA: {eta:.0f}s')
                sys.stdout.flush()
            except Exception as e:
                print(f'\n    [!] 批次 {i//BATCH_SIZE} 出错: {e}')
        
        print(f'\n  ✓ 完成! 解压 {extracted} 文件到 {out_dir}')
        
        # 验证实际文件数
        actual = sum(1 for _ in Path(out_dir).rglob('*.csv'))
        print(f'  实际文件数: {actual}')
    
    return extracted


def extract_daily():
    """解压2026年所有每日数据"""
    daily_dir = os.path.join(SOURCE_DIR, '2026', '每日数据')
    if not os.path.exists(daily_dir):
        print(f'  [!] 每日数据目录不存在: {daily_dir}')
        return 0
    
    daily_7zs = sorted([f for f in os.listdir(daily_dir) if f.endswith('.7z')])
    print(f'  找到 {len(daily_7zs)} 天的每日数据')
    
    total_extracted = 0
    success_days = 0
    fail_days = 0
    
    for idx, fname in enumerate(daily_7zs):
        date_str = fname.replace('.7z', '')  # YYYYMMDD
        arc_path = os.path.join(daily_dir, fname)
        out_dir = os.path.join(OUTPUT_BASE, 'daily', date_str)
        os.makedirs(out_dir, exist_ok=True)
        
        try:
            with py7zr.SevenZipFile(arc_path, 'r') as z:
                all_names = z.getnames()
                # 筛选A股 (日期子目录/sh*.csv 或 sz*.csv 或 直接sh/sz*.csv)
                inner_dir = date_str  # 20260506
                a_stock_files = [
                    n for n in all_names 
                    if ('/' in n and n.split('/')[1].startswith(VALID_PREFIXES))
                    or n.startswith(VALID_PREFIXES)
                ]
            
            if not a_stock_files:
                fail_days += 1
                continue
            
            # 提取时去掉日期前缀目录层
            with py7zr.SevenZipFile(arc_path, 'r') as z:
                z.extract(targets=a_stock_files[:5000], path=out_dir)
            
            total_extracted += len(a_stock_files)
            success_days += 1
            
            if (idx + 1) % 10 == 0 or idx == len(daily_7zs) - 1:
                print(f'  日进度: {idx+1}/{len(daily_7zs)} 天, 已解压 ~{total_extracted} 文件')
                
        except Exception as e:
            print(f'  [!] {date_str} 解压失败: {e}')
            fail_days += 1
    
    print(f'  ✓ 每日数据完成! 成功 {success_days}天, 失败 {fail_days}天, 共 ~{total_extracted} 文件')
    return total_extracted


def main():
    print('=' * 50)
    print('筹码峰数据 全量解压器 v1.0')
    print(f'输出目录: {OUTPUT_BASE}')
    print('=' * 50)
    os.makedirs(OUTPUT_BASE, exist_ok=True)
    
    t0 = time.time()
    
    # Step 1: 2025年度归档
    print('\n[Step 1/2] 解压 2025年度归档...')
    y25_count = extract_yearly('2025')
    
    # Step 2: 2024年度归档（如果存在）
    arc_2024 = os.path.join(SOURCE_DIR, '2024', '2024.7z')
    if os.path.exists(arc_2024):
        print('\n[Step 1b] 解压 2024年度归档...')
        extract_yearly('2024')
    
    # Step 3: 2026每日数据
    print('\n[Step 2/2] 解压 2026每日数据...')
    d_count = extract_daily()
    
    elapsed = time.time() - t0
    
    # 最终统计
    print('\n' + '=' * 50)
    print('=== 解压完成 ===')
    print(f'总耗时: {elapsed:.1f}s ({elapsed/60:.1f}分钟)')
    
    # 统计输出目录
    total_size = 0
    total_files = 0
    for root, dirs, files in os.walk(OUTPUT_BASE):
        for f in files:
            fp = os.path.join(root, f)
            total_size += os.path.getsize(fp)
            total_files += 1
    print(f'总文件数: {total_files}')
    print(f'总大小: {total_size / 1024 / 1024:.1f} MB ({total_size / 1024 / 1024 / 1024:.2f} GB)')
    
    # 目录结构预览
    print('\n目录结构:')
    for d in ['yearly', 'daily']:
        p = os.path.join(OUTPUT_BASE, d)
        if os.path.exists(p):
            subdirs = os.listdir(p)
            print(f'  {d}/: {subdirs}')


if __name__ == '__main__':
    main()

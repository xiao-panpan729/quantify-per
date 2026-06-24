#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论学生模型 v3 — 笔边界二分类 (Teacher labels)

关键修复 vs v2:
  1. BCEWithLogitsLoss + pos_weight (正确处理5:95不平衡)
  2. 数据集使用Teacher标注(Claude判断)
  3. 边界检测: 二分类 → 然后找连通域提取边界

Usage:
  python tools/train_chanlun_student.py
  python tools/train_chanlun_student.py --augment  # 数据增强
"""
import sys, json, math
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

WINDOW = 128
STRIDE = 32
BATCH_SIZE = 32
HIDDEN = 64
N_LAYERS = 2
DROPOUT = 0.2
LR = 0.001
EPOCHS = 50
N_FEATURES = 12


class ChanlunDataset(Dataset):
    def __init__(self, json_path, window=WINDOW, stride=STRIDE,
                 train=True, split=0.8, augment=False):
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.samples = []
        self.augment = augment and train
        np.random.seed(42)

        for rec in data['records']:
            features = np.array(rec['features'], dtype=np.float32)
            bis = rec['structure']['bis']
            n = len(features)

            # 构建per-bar二分类标签: 1=笔边界, 0=非边界
            boundary = np.zeros(n, dtype=np.float32)
            for b in bis:
                s, e = b['start_bar'], b['end_bar']
                if 0 <= s < n:
                    boundary[s] = 1.0
                if 0 <= e < n and e != s:
                    boundary[e] = 1.0

            # 方向标签: 0=无, 1=上涨, 2=下跌 (只在边界处有用)
            direction = np.zeros(n, dtype=np.int64)
            for b in bis:
                s = b['start_bar']
                if 0 <= s < n:
                    direction[s] = 1 if b['direction'] == '上涨' else 2

            # z-score归一化 (沿时间轴)
            features = self._normalize(features)

            # 滑窗
            indices = list(range(0, n - window + 1, stride))
            if train:
                np.random.shuffle(indices)
            split_idx = int(len(indices) * split)
            sel = indices[:split_idx] if train else indices[split_idx:]

            for start in sel:
                end = start + window
                feat = features[start:end].copy()
                bnd = boundary[start:end].copy()

                # 数据增强: 标注偏移±1bar
                if self.augment:
                    shift = np.random.choice([-1, 0, 1])
                    if shift != 0:
                        bnd = np.roll(bnd, shift)
                        if shift > 0:
                            bnd[:shift] = 0
                        else:
                            bnd[shift:] = 0

                self.samples.append({
                    'features': feat,
                    'boundary': bnd,
                    'direction': direction[start:end].copy(),
                })

        print(f'  {"训练" if train else "验证"}: {len(self.samples)}窗'
              + f' {"(增强)" if self.augment else ""}')

    def _normalize(self, features):
        mean = features.mean(axis=0, keepdims=True)
        std = features.std(axis=0, keepdims=True) + 1e-8
        return (features - mean) / std

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s = self.samples[idx]
        return {
            'features': torch.from_numpy(s['features']),
            'boundary': torch.from_numpy(s['boundary']),
            'direction': torch.from_numpy(s['direction']),
        }


class ChanlunStudent(nn.Module):
    """BiLSTM → 笔边界二分类 + 方向"""
    def __init__(self, n_features=N_FEATURES, hidden=HIDDEN, n_layers=N_LAYERS):
        super().__init__()
        self.lstm = nn.LSTM(n_features, hidden, n_layers,
                            batch_first=True, dropout=DROPOUT, bidirectional=True)
        self.dropout = nn.Dropout(DROPOUT)
        self.boundary_head = nn.Linear(hidden * 2, 1)   # 笔边界: sigmoid
        self.direction_head = nn.Linear(hidden * 2, 3)  # 方向: softmax

    def forward(self, x):
        out, _ = self.lstm(x)
        out = self.dropout(out)
        return {
            'boundary': self.boundary_head(out).squeeze(-1),  # (B, T)
            'direction': self.direction_head(out),             # (B, T, 3)
        }


def train(json_path=None):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'设备: {device}')

    # 使用Teacher标注版数据集
    if json_path is None:
        json_path = 'training_data/chanlun_dataset_v1_teacher.json'
    print('加载Teacher标注数据集...')
    train_ds = ChanlunDataset(json_path, train=True, augment=True)
    val_ds = ChanlunDataset(json_path, train=False)

    # 计算正负样本比例
    n_pos = sum((s['boundary'] > 0).sum() for s in train_ds.samples[:200])
    n_neg = sum((s['boundary'] == 0).sum() for s in train_ds.samples[:200])
    pos_weight_val = n_neg / max(n_pos, 1)
    print(f'  正样本: {n_pos}, 负样本: {n_neg}, pos_weight: {pos_weight_val:.1f}')

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, drop_last=True)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

    model = ChanlunStudent().to(device)
    total_params = sum(p.numel() for p in model.parameters())
    print(f'\n模型参数: {total_params:,}')

    # BCEWithLogitsLoss + pos_weight (处理不平衡)
    pos_weight = torch.tensor([pos_weight_val])
    bce_loss = nn.BCEWithLogitsLoss(pos_weight=pos_weight.to(device))
    ce_loss = nn.CrossEntropyLoss()

    optimizer = optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)

    print('\n训练...')
    best_f1 = 0

    for epoch in range(EPOCHS):
        model.train()
        train_loss = 0

        for batch in train_loader:
            feats = batch['features'].to(device)
            bnd = batch['boundary'].to(device)
            drc = batch['direction'].to(device)

            optimizer.zero_grad()
            preds = model(feats)

            loss_bnd = bce_loss(preds['boundary'], bnd)

            # 方向损失: 只在边界位置计算
            mask = (bnd > 0)
            if mask.sum() > 0:
                loss_dir = ce_loss(preds['direction'][mask], drc[mask])
            else:
                loss_dir = torch.tensor(0.0, device=device)

            loss = loss_bnd + 0.5 * loss_dir
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += loss.item()

        scheduler.step()

        # 验证: 用0.5阈值提取边界
        model.eval()
        total_tp, total_fp, total_fn = 0, 0, 0
        dir_correct, dir_total = 0, 0

        with torch.no_grad():
            for batch in val_loader:
                feats = batch['features'].to(device)
                true_b = batch['boundary'].numpy()
                true_d = batch['direction'].numpy()

                preds = model(feats)
                prob_b = torch.sigmoid(preds['boundary']).cpu().numpy()
                pred_d = preds['direction'].argmax(-1).cpu().numpy()

                # 最优阈值搜索
                for thresh in [0.3, 0.5, 0.7]:
                    pred_b = (prob_b > thresh).astype(np.float32)
                    tp = ((pred_b == 1) & (true_b == 1)).sum()
                    fp = ((pred_b == 1) & (true_b == 0)).sum()
                    fn = ((pred_b == 0) & (true_b == 1)).sum()
                    if thresh == 0.5:
                        total_tp, total_fp, total_fn = tp, fp, fn

                # 方向精度 (边界处)
                bnd_mask = (true_b > 0)
                dir_correct += (pred_d[bnd_mask] == true_d[bnd_mask]).sum()
                dir_total += bnd_mask.sum()

        precision = total_tp / (total_tp + total_fp) * 100 if (total_tp + total_fp) > 0 else 0
        recall = total_tp / (total_tp + total_fn) * 100 if (total_tp + total_fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        dir_acc = dir_correct / dir_total * 100 if dir_total > 0 else 0

        print(f'  Epoch {epoch+1:2d}: loss={train_loss/len(train_loader):.3f} '
              f'P={precision:.1f}% R={recall:.1f}% F1={f1:.1f}% | dir={dir_acc:.1f}%')

        if f1 > best_f1:
            best_f1 = f1
            torch.save(model.state_dict(), 'training_data/chanlun_student_v3.pt')

    print(f'\n最佳F1: {best_f1:.1f}%')
    print('模型: training_data/chanlun_student_v3.pt')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--augment', action='store_true', help='数据增强')
    parser.add_argument('--dataset', type=str, default=None, help='数据集JSON路径')
    args = parser.parse_args()
    train(json_path=args.dataset)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token序列 → 下一Token预测模型（极小版，今天跑完）

数据: 1.24M Token from index_token_corpus_full.csv
模型: Embedding + LSTM(64) → 7分类
"""
import sys, os, json
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

# ── 1. 加载数据 ──
print('加载语料库...')
corpus = pd.read_csv('training_data/index_token_corpus_full.csv')
print(f'  Token总数: {len(corpus):,}')

# 编码 Token → 数字
TOKEN7_LIST = ['方向', '包含关系', '转折', '顶分型', '底分型', '其他']
token2id = {t:i for i,t in enumerate(TOKEN7_LIST)}
id2token = {i:t for t,i in token2id.items()}

corpus = corpus[corpus['token_7'].isin(TOKEN7_LIST)].copy()
ids = corpus['token_7'].map(token2id).values
print(f'  有效Token: {len(ids):,}')
print(f'  词表: {TOKEN7_LIST}')
dist = corpus['token_7'].value_counts()
for t in TOKEN7_LIST:
    print(f'    {t}: {dist.get(t,0)/len(corpus)*100:.1f}%')

# ── 2. 构建序列样本 ──
SEQ_LEN = 10  # 用前10个Token预测下一个
X, y = [], []
for i in range(len(ids) - SEQ_LEN):
    X.append(ids[i:i+SEQ_LEN])
    y.append(ids[i+SEQ_LEN])
X = np.array(X)
y = np.array(y)
print(f'\n样本数: {len(X):,}')
print(f'输入形状: {X.shape}')

# ── 3. 分训练/验证集 ──
split = int(len(X) * 0.85)
perm = np.random.permutation(len(X))
X, y = X[perm], y[perm]
X_train, y_train = X[:split], y[:split]
X_val, y_val = X[split:], y[split:]
print(f'训练集: {len(X_train):,}  验证集: {len(X_val):,}')

BATCH_SIZE = 512
train_ds = TensorDataset(torch.from_numpy(X_train).long(),
                         torch.from_numpy(y_train).long())
val_ds = TensorDataset(torch.from_numpy(X_val).long(),
                       torch.from_numpy(y_val).long())
train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE)

# ── 4. 定义极小模型 ──
class TokenPredictor(nn.Module):
    def __init__(self, vocab_size=5, embed_dim=16, hidden=64, seq_len=10):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, embed_dim)
        self.lstm = nn.LSTM(embed_dim, hidden, batch_first=True, dropout=0.2)
        self.classifier = nn.Linear(hidden, vocab_size)

    def forward(self, x):
        emb = self.embed(x)          # (B, S, E)
        lstm_out, _ = self.lstm(emb) # (B, S, H)
        last = lstm_out[:, -1, :]    # (B, H)
        return self.classifier(last) # (B, V)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
model = TokenPredictor(vocab_size=len(TOKEN7_LIST)).to(device)
total_params = sum(p.numel() for p in model.parameters())
print(f'\n模型参数: {total_params:,}')
print(f'设备: {device}')

# ── 5. 训练 ──
criterion = nn.CrossEntropyLoss()
optimizer = optim.Adam(model.parameters(), lr=0.001)
EPOCHS = 8

print('\n开始训练...')
for epoch in range(EPOCHS):
    model.train()
    total_loss = 0
    correct, total = 0, 0
    for bx, by in train_loader:
        bx, by = bx.to(device), by.to(device)
        optimizer.zero_grad()
        out = model(bx)
        loss = criterion(out, by)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        correct += (out.argmax(1) == by).sum().item()
        total += by.size(0)
    train_acc = correct / total * 100

    # 验证
    model.eval()
    val_correct, val_total = 0, 0
    val_loss = 0
    with torch.no_grad():
        for bx, by in val_loader:
            bx, by = bx.to(device), by.to(device)
            out = model(bx)
            val_loss += criterion(out, by).item()
            val_correct += (out.argmax(1) == by).sum().item()
            val_total += by.size(0)
    val_acc = val_correct / val_total * 100
    print(f'  Epoch {epoch+1}: train_loss={total_loss:.3f} train_acc={train_acc:.2f}%  val_acc={val_acc:.2f}%')

print('\n训练完成。')

# ── 6. 评估混淆矩阵 ──
print(f'\n混淆矩阵（验证集）:')
header = f'{"预测/实际":>12}'
for t in TOKEN7_LIST: header += f'{t:>8}'
print(header)

confusion = np.zeros((6,6), dtype=int)
model.eval()
with torch.no_grad():
    for bx, by in val_loader:
        bx, by = bx.to(device), by.to(device)
        out = model(bx)
        preds = out.argmax(1)
        for p, t in zip(preds.cpu(), by.cpu()):
            confusion[p, t] += 1

for i, t_pred in enumerate(TOKEN7_LIST):
    print(f'{t_pred:>12}', end='')
    for j in range(6):
        total_col = confusion[:, j].sum()
        pct = confusion[i, j] / max(confusion[:, j].sum(), 1) * 100
        print(f'{pct:>7.1f}%', end='')
    print()

# 重点：方向延续性
up_idx = TOKEN7_LIST.index('方向')
up_up = confusion[up_idx, up_idx] / max(confusion[up_idx, :].sum(), 1) * 100
print(f'\n方向→方向准确率: {up_up:.1f}%')

# ── 7. 和随机基线对比 ──
rand_baseline = 100 / len(TOKEN7_LIST)
final_val_acc = val_correct / val_total * 100
print(f'\n随机基线: {rand_baseline:.1f}%')
print(f'model准确率: {final_val_acc:.2f}%')
print(f'提升: {final_val_acc - rand_baseline:.2f}%')
print(f'效果: {"有用 ✅" if final_val_acc > rand_baseline * 1.5 else "勉强 ⚠️" if final_val_acc > rand_baseline else "无效 ❌"}')

# ── 8. 保存模型 ──
torch.save(model.state_dict(), 'training_data/token_predictor.pt')
with open('training_data/token_model_meta.json', 'w') as f:
    json.dump({'vocab': TOKEN7_LIST, 'seq_len': SEQ_LEN, 'params': total_params,
               'val_acc': final_val_acc, 'random_baseline': rand_baseline}, f)
print(f'\n模型已保存: training_data/token_predictor.pt')

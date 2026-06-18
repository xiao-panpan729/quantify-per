# 家庭PC 微调 Kronos-mini 操作指南

## 前置条件

- 你有 RTX 4060 (8GB VRAM) 的 Windows 电脑
- 已安装 Python 3.10+
- CUDA 已配置好

## 步骤

### 1. 先在本机 git push

`models/` 目录只有 32MB（kronos-mini 16MB + tokenizer 16MB），可以直接上传。

```bash
git add training_data/ tools/label_chanlun_training.py
git add models/
git commit -m "缠论标注训练数据 + Kronos 微调配置"
git push
```

> ⚠️ 记得确认 `models/` 确实被加进来了——`git status` 看一下

### 2. 家庭PC Git Pull

在你的家庭PC上 clone/pull 仓库，模型权重已经包含在内，**不需要额外下载**:

```bash
git pull origin main
```

确认以下文件和目录存在:
- `training_data/kronos_training_data.csv`  (24936行训练数据)
- `training_data/kronos_config.yaml`         (微调配置)
- `training_data/labeled/*.csv`              (14个标的缠论标注)
- `models/kronos-mini/`                       (模型权重, 16MB)
- `models/kronos-tokenizer/`                  (tokenizer权重, 16MB)
- `tmp_kronos/finetune_csv/train_sequential.py` (Kronos训练脚本)

### 3. 安装依赖

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
pip install pandas numpy pyyaml
```

### 3. 下载 Kronos 权重

从 HuggingFace 镜像下载 Kronos-mini 权重:

```bash
# 设置镜像
set HF_ENDPOINT=https://hf-mirror.com

# 下载 tokenizer
huggingface-cli download --resume-download \
    jingyaogong/Kronos-Tokenizer-base \
    --local-dir ./models/kronos-tokenizer

# 下载 mini 模型 (2.7GB, 4.1M参数)
huggingface-cli download --resume-download \
    jingyaogong/Kronos-mini \
    --local-dir ./models/kronos-mini
```

> 如果不想装 `huggingface-cli`, 也可以直接从浏览器下载:
> - https://hf-mirror.com/jingyaogong/Kronos-Tokenizer-base
> - https://hf-mirror.com/jingyaogong/Kronos-mini
>
> 下载后放到 `./models/kronos-tokenizer/` 和 `./models/kronos-mini/`

**注意:** `Kronos-mini` 是 4.1M 参数版 (已验证)。如果想试更大的, 也可以下 `Kronos-base` (36M参数) — 但训练时间会长 5-10 倍。

### 4. 运行微调

**在项目根目录下运行:**

```bash
python tmp_kronos/finetune_csv/train_sequential.py \
    --config training_data/kronos_config.yaml
```

**预估时间** (RTX 4060, 4.1M参数):
- tokenizer 阶段: ~5-15 分钟
- basemodel 阶段: ~15-30 分钟
- 合计: **~20-40 分钟**

**如果 OOM (显存不足):**
- 打开 `training_data/kronos_config.yaml`
- 把 `batch_size: 16` 改成 `batch_size: 8`
- 或者把 `lookback_window: 256` 改成 `lookback_window: 128`

### 5. 验证训练结果

训练完成后, 微调后的权重保存在:
```
finetuned/kronos_chanlun_ft_v1/
  tokenizer/best_model/
  basemodel/best_model/
```

用之前写过的测试脚本验证效果:
```bash
python test_kronos_bi_consistency.py
python test_kronos_batch.py
```

## 常见问题

**Q: 报错 `ModuleNotFoundError: No module named 'model'`**
A: 需要先 `cd tmp_kronos/finetune_csv` 再运行, 或者在项目根目录运行并确认 `sys.path` 包含 `tmp_kronos`

**Q: 跑一半断了怎么办?**
A: 没事, `skip_existing: false` 会从头跑。如果改为 `true`, 会跳过已完成的阶段。建议一开始用 `false` 确保完整训练。

**Q: 能不能关掉 tokenizer 训练只练 basemodel?**
A: 可以。config 里 `train_tokenizer: true` → 改为 `false`。但 Kronos 官方建议两阶段都做。

**Q: 训练数据是归一化的, 会不会有问题?**
A: 归一化到每只标的的首根 close=1.0, 14只标的拼接成一个长序列。Kronos 的 VQ-VAE 处理的是相对变化, 所以跨标的拼接是安全的。

## 后续

跑完告诉我一声, 我帮你分析:
- 微调后笔方向一致性有变化吗? (跑 test_kronos_bi_consistency.py)
- 方向正确率突破 50±2% 了吗? (跑 test_kronos_batch.py)
- 结构违反率降了多少? (跑 test_kronos_structure.py)

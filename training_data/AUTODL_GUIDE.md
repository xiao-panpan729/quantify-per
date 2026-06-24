# AutoDL 租 GPU 跑 Kronos 微调指南

比买 3090 划算，跑一次 0.5 元，跑废了也不心疼。

---

## 一、注册充钱

1. 打开 [autodl.com](https://www.autodl.com) → 手机号注册 → 实名认证
2. 充值 **50 元**够用很久了

## 二、创建实例

1. 点"创建实例"
2. **GPU 选 RTX 3090（24GB）**，大概 1.5 元/小时
3. **镜像选 PyTorch 2.x**（选最新的，带 CUDA 12.x 的）
4. 数据盘勾 **20GB** 够了
5. 创建，等 1-2 分钟启动

## 三、上传代码和数据

实例启动后，点"JupyterLab"进入网页端终端。

**在终端里执行：**

```bash
# 1. 拉代码（如果你的项目在 Gitee）
git clone https://gitee.com/xiao-panpan729/quantify-per.git
cd quantify-per

# 如果压缩包上传更快：点 JupyterLab 左侧上传 → 拖入 quantify.zip
# unzip quantify.zip
# cd quantify-per

# 2. 确认文件都在
ls training_data/
ls models/kronos-mini/
```

## 四、运行微调

```bash
# 安装依赖
pip install pandas numpy pyyaml

# 跑微调（20-30分钟）
python tmp_kronos/finetune_csv/train_sequential.py \
    --config training_data/kronos_config.yaml
```

## 五、下载结果

训练完微调后的权重在 `finetuned/` 目录，压缩下载：

```bash
zip -r finetuned.zip finetuned/
```

然后在 JupyterLab 里右键下载 `finetuned.zip`。

## 六、记得关机！

**用完一定关机**，不然一直扣钱。AutoDL 可以设置**自动关机**——创建实例时勾选"无操作后自动关机"，设 1 小时。

## 费用参考

| 任务 | 时长 | 费用 |
|-----|------|------|
| Kronos-mini 微调 | ~20 分钟 | ~0.5 元 |
| Kronos-base (36M) 微调 | ~2-3 小时 | ~3-4.5 元 |
| Qwen3-4B 微调 | ~3-4 小时 | ~5-6 元 |
| 7B 模型推理测试 | 随便玩 | ~1-2 元 |

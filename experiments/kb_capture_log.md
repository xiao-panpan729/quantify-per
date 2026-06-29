# 知识库捕获管道实验日志

> 记录 IMA 知识库截获、知识星球转换、抖音 ASR 等知识源接入管道的设计迭代。

---

## 新源接入：抖音视频 → ASR 文字 (2026-06-28)

**新增脚本**: `tools/douyin_asr_pipeline.py`（278行）
**输出目录**: `D:/knowledge-hub/douyin_asr/{YYYY-MM-DD}/@{author}_{title}.md`

### 背景
用户希望将关注的抖音财经博主（合伙人Mike等）的视频内容转录为文字，纳入 Obsidian 知识库统一管理。

### 技术方案

| 环节 | 方案 | 选择理由 |
|------|------|---------|
| 视频解析 | dy-cli（DouyinAPIClient Python API） | 无需 cookie，支持无水印直链 |
| 音频提取 | ffmpeg MP3 32kbps 16kHz mono | 体积小（10min视频压到~2.4MB），可用 base64 直传 |
| 语音识别 | 腾讯云录音文件识别（CreateRecTask） | 10h/月免费，超额 3.24元/h |

### 关键设计决策

1. **直接上传 vs URL**: SourceType=1（base64 直传），省去文件托管环节
2. **音频加速省额度**: `--speed 1.5~2.0`，用 ffmpeg atempo 滤镜压缩时长（1.5x 省33%，2.0x 省50%）
3. **日期分目录**: `{date}/@{author}_{title}.md`，与现有 ima_ocr 结构一致

### 测试结果
- 实测视频（10分32秒 @合伙人Mike → 高盛稀宇科技研报拆解）
- 1.5x 加速后约 7 分钟，ASR 返回 4235 字符，专业术语识别准确（DFM/OpenRouter/Hailuo 3/EBIT 等）
- 单次耗时约 7 秒完成识别（2 次轮询）

### 已知限制
- 批量获取用户视频列表需抖音登录态（`dy login --browser` 因 Windows DPAPI 加密失败）
- 付费视频不可下载
- 建议用户手动提供 URL 单条处理

---

## OCR 引擎迁移：EasyOCR/PaddleOCR → 腾讯云通用文字识别 (2026-06-27)

**涉及**: `tools/ocr_ima_kb.py`

### 迁移原因
- EasyOCR 中文乱码率 ~80%，不可用
- PaddleOCR 因 PaddlePaddle 3.3.1 与 PaddleX 模型格式不兼容（`NotImplementedError: ConvertPirAttribute2RuntimeAttribute`）无法运行
- 腾讯云 `GeneralAccurateOCR` 高精度版准确率约 99%

### 技术变化
- 删除 EasyOCR/PaddleOCR/PaddlePaddle/torch 释放 ~650MB
- `.env` 添加 `TENCENT_OCR_SECRET_ID` / `TENCENT_OCR_SECRET_KEY`
- SSL 证书绕过：`HttpProfile(certification=False)` 解决 Windows 证书验证失败
- 实现内容 MD5 去重，增量扫描

### 费用
- 1000次/月 免费额度
- 14.9元/千次 资源包（已购防身）

---

## 午后产业分析管道输出格式改版 (2026-06-29)

**涉及**: `tools/afternoon_pipeline.py`（562行）

### 变更内容

1. **个股标注格式改版**：从简单列表 `**相关个股**: 002407 多氟多, 688549 中巨芯` 改为每只股票独立标注——`多氟多 (002407)：国内半导体级氢氟酸龙头，现有产能4万吨且原材料完全自供...`
   - 第一段：产业链角色/细分定位
   - 第二段：该事件/趋势为什么利好它

2. **去除来源归属**：输出中不再标注"（来源：一思一记/猫菲特闲唠嗑）"，信息正常陈述

3. **去除观点者名**：输出中不再写"猫菲特认为"、"一思一记说"，避免第三人称转述

4. **日期动态注入**：prompt 模板从硬编码"今日"改为 `{target_date}` 占位符，运行时由 `build_prompt()` 注入具体日期（如"2026年06月29日"）

5. **输出约束加固**：prompt 末尾追加"直接输出分析正文，不要输出任何其他内容"指令，防止 Claude 输出元总结

### 触发原因
用户反馈原格式"不够全面"，缺少个股的产业链角色和受益逻辑推导；同时要求去掉来源标注和转述句式。

### 代码改动
- `INDUSTRY_SYSTEM_PROMPT` 重写：输出格式模板 + 新增"个股标注要求"章节 + 示例
- `build_prompt()` 新增 `target_date` 参数和日期格式化逻辑
- 修复 `.format()` 花括号转义问题（`{{...}}`）
- 终端中文编码配置 `sys.stdout.reconfigure(encoding='utf-8')`

"""
知识星球 JSON → Obsidian Markdown 转换脚本
用法:
  python tools/convert_zsxq_to_md.py --auto                  # 自动最新日期
  python tools/convert_zsxq_to_md.py --date=20260625         # 指定日期
  python tools/convert_zsxq_to_md.py --auto --group=28888114545551  # 最新日期+指定群组

输出:
  D:\knowledge-hub\zsxq\{group_id}\
    _images\              ← 话题引用图片（软链/复制）
    YYYY-MM-DD\
      topic_{topic_id}.md  ← 单条话题
"""

import os
import re
import sys
import json
import glob
import shutil
from datetime import datetime
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding='utf-8')

# 配置
CAPTURE_DIR = r"D:\ima_captures"
OUTPUT_DIR = r"D:\knowledge-hub\zsxq"

def _ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def _parse_date_arg():
    """解析日期参数"""
    for arg in sys.argv[1:]:
        if arg.startswith("--date="):
            return arg.split("=")[1]
        if arg.startswith("--group="):
            return None  # 不限制日期
    if "--auto" in sys.argv:
        # 自动找最新日期目录
        dirs = [d for d in os.listdir(CAPTURE_DIR)
                if os.path.isdir(os.path.join(CAPTURE_DIR, d)) and d.isdigit() and len(d) == 8]
        if dirs:
            return sorted(dirs)[-1]
        print(f"[ERROR] {CAPTURE_DIR} 下无有效日期目录")
        sys.exit(1)
    # 默认今天
    return datetime.now().strftime("%Y%m%d")

def _parse_group_arg():
    for arg in sys.argv[1:]:
        if arg.startswith("--group="):
            return arg.split("=")[1]
    return None  # 所有群组

def extract_img_id_from_url(url):
    """从 images.zsxq.com URL 提取图片 ID"""
    parsed = urlparse(url)
    path_part = os.path.splitext(parsed.path)[0].lstrip('/')
    if not path_part or (len(path_part) < 5 and path_part.isdigit()):
        return None
    return path_part[:20]

def clean_hashtag(text):
    """把 <e type=\"hashtag\" ... title=\"%23xxx%23\"> 转为 #xxx """
    def replace_tag(m):
        title = m.group(1)
        # URL decode %23 → #, %XX 解码
        try:
            from urllib.parse import unquote
            decoded = unquote(title)
            decoded = decoded.strip('#')
            return f"#{decoded} "
        except:
            return f"#{title} "
    text = re.sub(r'<e\s+type="hashtag"[^>]*title="([^"]+)"\s*/>', replace_tag, text)
    # 清理其他 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    return text

def build_image_map(capture_date, group_id):
    """构建 img_id → 本地文件路径 映射"""
    img_map = {}
    # 扫描 _images 目录
    img_dir = os.path.join(CAPTURE_DIR, capture_date, "zsxq", group_id, "_images")
    if os.path.exists(img_dir):
        for fn in os.listdir(img_dir):
            if fn.endswith(('.jpg', '.png', '.webp', '.gif')):
                # 文件名格式: {counter:04d}_{img_id}{ext}
                parts = fn.split('_', 1)
                if len(parts) == 2:
                    img_id = os.path.splitext(parts[1])[0]
                    img_map[img_id] = os.path.join(img_dir, fn)

    # 也扫 unknown 目录（旧版抓的可能在 unknown 下）
    unknown_img_dir = os.path.join(CAPTURE_DIR, capture_date, "zsxq", "unknown", "_images")
    if os.path.exists(unknown_img_dir):
        for fn in os.listdir(unknown_img_dir):
            if fn.endswith(('.jpg', '.png', '.webp', '.gif')):
                parts = fn.split('_', 1)
                if len(parts) == 2:
                    img_id = os.path.splitext(parts[1])[0]
                    if img_id not in img_map:
                        img_map[img_id] = os.path.join(unknown_img_dir, fn)

    return img_map

def convert_topics(capture_date, filter_group=None):
    """转换指定日期的所有话题为 markdown"""
    base = os.path.join(CAPTURE_DIR, capture_date, "zsxq")

    if not os.path.exists(base):
        print(f"[ERROR] {base} 不存在")
        return

    # 遍历群组
    for group_id in os.listdir(base):
        if filter_group and group_id != filter_group:
            continue
        if group_id == "unknown":
            continue  # unknown 群组没有明确归属，跳过

        topics_dir = os.path.join(base, group_id, "_topics")
        if not os.path.isdir(topics_dir):
            continue

        print(f"\n{'='*60}")
        print(f"群组: {group_id}")
        print(f"{'='*60}")

        # 构建图片映射
        img_map = build_image_map(capture_date, group_id)
        print(f"图片映射: {len(img_map)} 张")

        # 输出目录
        out_group = os.path.join(OUTPUT_DIR, group_id)
        out_images = os.path.join(out_group, "_images")
        _ensure_dir(out_images)

        # 复制图片到输出目录
        copied = 0
        for img_id, src_path in img_map.items():
            dst = os.path.join(out_images, os.path.basename(src_path))
            if not os.path.exists(dst):
                shutil.copy2(src_path, dst)
                copied += 1
        print(f"复制图片: {copied} 张 (已存在则跳过)")

        # 遍历所有 topics JSON
        total_md = 0
        for fn in sorted(os.listdir(topics_dir)):
            fp = os.path.join(topics_dir, fn)
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)

            topics = data.get("resp_data", {}).get("topics", [])
            for t in topics:
                talk = t.get("talk", {})
                text = talk.get("text", "")
                owner = talk.get("owner", {})
                author = owner.get("name", "未知") if isinstance(owner, dict) else str(owner)
                topic_id = t.get("topic_id", "unknown")
                create_time = t.get("create_time", "") or ""

                if not text.strip():
                    continue

                # 清理文本
                clean_text = clean_hashtag(text)

                # 处理图片引用
                images = talk.get("images", [])
                img_refs = []
                for img in images:
                    # 优先用 large 或 original
                    for key in ["large", "original", "thumbnail"]:
                        if key in img and isinstance(img[key], dict):
                            url = img[key].get("url", "")
                            if url:
                                img_id = extract_img_id_from_url(url)
                                if img_id and img_id in img_map:
                                    local_path = os.path.basename(img_map[img_id])
                                    img_refs.append(f"![]({os.path.join('..', '_images', local_path).replace(chr(92), '/')})")
                                    break

                # 生成 markdown
                md_lines = ["---"]
                md_lines.append(f"source: 知识星球")
                md_lines.append(f"group_id: {group_id}")
                md_lines.append(f"author: {author}")
                md_lines.append(f"topic_id: {topic_id}")
                if create_time:
                    md_lines.append(f"create_time: {create_time}")
                md_lines.append(f"captured_at: {capture_date[:4]}-{capture_date[4:6]}-{capture_date[6:8]}")
                md_lines.append("---")
                md_lines.append("")
                md_lines.append(clean_text)
                md_lines.append("")
                md_lines.extend(img_refs)

                # 按日期分目录（没有 create_time 就用抓取日期）
                topic_date = capture_date
                if create_time and len(create_time) >= 10:
                    topic_date = create_time[:10].replace("-", "")
                date_dir = f"{topic_date[:4]}-{topic_date[4:6]}-{topic_date[6:8]}"
                out_date_dir = os.path.join(out_group, date_dir)
                _ensure_dir(out_date_dir)

                # 写入文件
                md_filename = f"topic_{topic_id}.md"
                md_path = os.path.join(out_date_dir, md_filename)

                with open(md_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(md_lines))

                total_md += 1

                if total_md % 1000 == 0:
                    print(f"  已生成 {total_md} 篇...")

        print(f"\n✅ 完成: {total_md} 篇 markdown")

if __name__ == "__main__":
    date = _parse_date_arg()
    group = _parse_group_arg()

    if date:
        print(f"转换日期: {date}")
        convert_topics(date, group)
    else:
        # 扫描所有日期
        for d in sorted(os.listdir(CAPTURE_DIR), reverse=True):
            if d.isdigit() and len(d) == 8:
                convert_topics(d, group)

    print(f"\n输出目录: {OUTPUT_DIR}")

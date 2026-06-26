"""
IMA 知识库 / 知识星球 图片+文字内容捕获脚本 — mitmproxy addon
用法: mitmdump -s tools/ima_capture.py -p 8888

捕获范围:
  1. IMA 知识库图片 → D:\ima_captures\{YYYYMMDD}\kb_{知识库ID}\
  2. 知识星球话题内容(JSON) → D:\ima_captures\{YYYYMMDD}\zsxq\{群组ID}\_topics\
  3. 知识星球图片 → D:\ima_captures\{YYYYMMDD}\zsxq\{群组ID}\_images\
  4. 其他图片 → D:\ima_captures\{YYYYMMDD}\other\

注意事项:
  - 首次运行需要安装 mitmproxy CA 证书（自动弹出安装提示）
  - Electron 应用不信任系统证书时，需手动安装证书到"受信任的根证书颁发机构"
  - 首次安装证书位置: ~/.mitmproxy/mitmproxy-ca-cert.cer
"""

import os
import re
import json
import hashlib
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from mitmproxy import http

# 保存目录
SAVE_DIR = r"D:\ima_captures"
STATE_FILE = os.path.join(SAVE_DIR, "state.json")

# 去重缓存
_seen_urls = set()
_seen_api = set()
_seen_topic_ids = set()  # 知识星球 topic_id 去重
_counters = {}
_metadata = {}

# 当前群组上下文（图片通过 Referer 归属群组失败时的 fallback）
_current_group = "unknown"

# 保存的 API 认证信息（用于后续批量拉取）
_zsxq_cookies = None

# ─── 状态持久化 ───

def _load_state():
    global _seen_topic_ids, _counters
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        _seen_topic_ids = set(state.get("seen_topic_ids", []))
        _counters = state.get("counters", {})
        print(f"[STATE] loaded: {len(_seen_topic_ids)} topic_ids, {len(_counters)} counters")
    except Exception as e:
        print(f"[STATE] load failed: {e}")

def _save_state():
    try:
        state = {
            "seen_topic_ids": list(_seen_topic_ids),
            "counters": _counters,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _ensure_dir(SAVE_DIR)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[STATE] save failed: {e}")

# 加载持久状态
_load_state()


def _ensure_dir(path: str):
    if not os.path.exists(path):
        os.makedirs(path)


def _today_dir() -> str:
    return datetime.now().strftime("%Y%m%d")


def _guess_ext(content_type: str) -> str:
    ct = content_type.lower().strip()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
    }
    for k, v in mapping.items():
        if ct.startswith(k):
            return v
    return ".bin"


def _counter(key: str) -> int:
    if key not in _counters:
        _counters[key] = 0
    _counters[key] += 1
    return _counters[key]


def _save_file(data: bytes, dir_path: str, filename: str):
    _ensure_dir(dir_path)
    path = os.path.join(dir_path, filename)
    with open(path, "wb") as f:
        f.write(data)
    return path


# ─── 知识星球处理 ───

def _handle_zsxq(flow: http.HTTPFlow):
    """处理知识星球 API（文字内容）和图片"""
    global _current_group
    url = flow.request.pretty_url

    # API 响应: topics 列表
    if "api.zsxq.com" in url and "/topics" in url:
        ct = flow.response.headers.get("Content-Type", "")
        if "json" not in ct:
            return
        content = flow.response.content
        if not content or len(content) < 100:
            return

        # 从 URL 提取群组 ID，同时更新当前群组上下文
        m = re.search(r"/groups/(\d+)/topics", url)
        gid = m.group(1) if m else "unknown"
        _current_group = gid

        # 保存 auth cookie（首次捕获）
        global _zsxq_cookies
        if _zsxq_cookies is None:
            raw_cookie = flow.request.headers.get("Cookie", "")
            if raw_cookie:
                _zsxq_cookies = raw_cookie
                # 也保存到文件
                _ensure_dir(os.path.join(SAVE_DIR, "_auth"))
                auth_path = os.path.join(SAVE_DIR, "_auth", "zsxq_cookies.txt")
                with open(auth_path, "w", encoding="utf-8") as f:
                    f.write(raw_cookie)
                print(f"[ZSXQ-AUTH] cookies saved ({len(raw_cookie)} chars)")

        # 记录实际请求 URL（含 query 参数）以了解翻页机制
        print(f"[ZSXQ-API-REQ] {url[:300]}")

        # 记录完整请求头（用于后续批量拉取认证）
        req_headers = dict(flow.request.headers)
        sensitive_keys = ["cookie", "x-request-id", "x-signature", "x-timestamp",
                          "x-version", "authorization", "access-token", "user-agent",
                          "referer", "origin", "x-csrf-token", "x-access-token"]
        auth_info = {k: req_headers[k] for k in req_headers if k.lower() in sensitive_keys}
        if auth_info:
            _ensure_dir(os.path.join(SAVE_DIR, "_auth"))
            auth_path = os.path.join(SAVE_DIR, "_auth", "zsxq_headers.json")
            with open(auth_path, "w", encoding="utf-8") as f:
                json.dump(auth_info, f, ensure_ascii=False, indent=2)
            print(f"[ZSXQ-AUTH] headers saved: {list(auth_info.keys())}")

        # 解析 JSON，按 topic_id 去重
        try:
            data = json.loads(content)
            topics = data.get("resp_data", {}).get("topics", [])
            if not topics:
                return True

            before = len(_seen_topic_ids)
            new_topics = [t for t in topics if t.get("topic_id") not in _seen_topic_ids]
            for t in topics:
                tid = t.get("topic_id")
                if tid:
                    _seen_topic_ids.add(tid)

            if not new_topics:
                print(f"[ZSXQ-API] skip (all {len(topics)} topics already captured)")
                return True

            after = len(_seen_topic_ids)
            print(f"[ZSXQ-API] {len(new_topics)} new + {len(topics) - len(new_topics)} dup (total unique: {after})")

            # 只保存新话题的 JSON
            data["resp_data"]["topics"] = new_topics
            content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        except (json.JSONDecodeError, KeyError):
            pass

        # 从 query 提取参数
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        cursor = qs.get("cursor", ["0"])[0][:8]

        base_dir = os.path.join(SAVE_DIR, _today_dir(), "zsxq", gid, "_topics")
        ts = datetime.now().strftime("%H%M%S")
        filename = f"topics_{ts}_cursor-{cursor}.json"

        path = _save_file(content, base_dir, filename)
        print(f"[ZSXQ-API] saved: {path} ({len(content)} bytes) <- cursor={cursor}")
        # 每批保存后写状态（防崩溃丢数据）
        _save_state()
        return True

    # 知识星球图片
    if "images.zsxq.com" in url:
        parsed = urlparse(url)
        path_part = os.path.splitext(parsed.path)[0].lstrip('/')

        # 跳过 emoji/图标/贴纸（path 为单个数字或很短）
        if not path_part or (len(path_part) < 5 and path_part.isdigit()):
            return True

        img_id = path_part[:20]
        ct = flow.response.headers.get("Content-Type", "image/jpeg")
        ext = _guess_ext(ct)

        # 从 Referer 推测群组，fallback 到 API 上下文
        referer = flow.request.headers.get("Referer", "")
        gid = _current_group
        m = re.search(r"/(?:group|groups)/(\d+)", referer)
        if m:
            gid = m.group(1)
            _current_group = gid

        base_dir = os.path.join(SAVE_DIR, _today_dir(), "zsxq", gid, "_images")
        c = _counter(f"zsxq_img_{gid}")
        filename = f"{c:04d}_{img_id}{ext}"

        path = _save_file(flow.response.content, base_dir, filename)
        print(f"[ZSXQ-IMG] saved: {path} ({len(flow.response.content)} bytes)")
        return True

    return False


# ─── IMA 知识库处理 ───

def _extract_kb_id(url: str) -> str:
    m = re.search(r"image\.myqcloud\.com/\d+/([a-zA-Z0-9_-]+)/", url)
    if m:
        return f"kb_{m.group(1)[:12]}"
    return "other"


def _handle_ima(flow: http.HTTPFlow):
    """处理 IMA 知识库图片"""
    url = flow.request.pretty_url
    content_type = flow.response.headers.get("Content-Type", "")
    content = flow.response.content

    if not content or len(content) < 100:
        return False

    is_img_ct = content_type.startswith("image/")
    is_img_ext = os.path.splitext(url.split("?")[0])[1].lower() in [
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]
    if not (is_img_ct or is_img_ext):
        return False

    skip_domains = ["galileotelemetry.tencent.com", "insight.tencent.com"]
    if any(d in url for d in skip_domains):
        return False

    kb_id = _extract_kb_id(url)
    base_dir = os.path.join(SAVE_DIR, _today_dir(), kb_id)
    ext = _guess_ext(content_type)
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    c = _counter(f"img_{kb_id}")
    filename = f"{c:04d}_{url_hash}{ext}"

    path = _save_file(content, base_dir, filename)
    print(f"[IMA] [{kb_id}] saved: {path} ({len(content)} bytes)")

    # 记录 metadata
    meta_key = kb_id
    if meta_key not in _metadata:
        _metadata[meta_key] = []
    _metadata[meta_key].append({
        "file": filename,
        "url": url,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "size_bytes": len(content),
    })
    if len(_metadata[meta_key]) >= 20:
        _save_metadata(base_dir, meta_key)

    return True


def _save_metadata(dir_path: str, meta_key: str):
    if meta_key in _metadata and _metadata[meta_key]:
        meta_path = os.path.join(dir_path, "metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(_metadata[meta_key], f, ensure_ascii=False, indent=2)


def response(flow: http.HTTPFlow):
    """拦截 HTTP 响应"""
    url = flow.request.pretty_url
    status_code = flow.response.status_code
    content = flow.response.content

    if status_code != 200 or not content:
        return

    # 1. 优先处理知识星球
    if "zsxq.com" in url:
        # API 内容不 URL 去重（每次 cursor 不同）
        if "api.zsxq.com" in url and "/topics" in url:
            if url in _seen_api:
                return
            _seen_api.add(url)
        elif url in _seen_urls:
            return
        _seen_urls.add(url)
        _handle_zsxq(flow)
        return

    # 2. 处理 IMA 知识库
    if url in _seen_urls:
        return
    _seen_urls.add(url)
    _handle_ima(flow)


def done():
    """mitmproxy 退出时，metadata 全部写盘"""
    today = _today_dir()
    for meta_key in list(_metadata.keys()):
        for d in os.listdir(os.path.join(SAVE_DIR, today)):
            if d.startswith(meta_key) or d == meta_key:
                _save_metadata(os.path.join(SAVE_DIR, today, d), meta_key)
    _save_state()
    print(f"[IMA] Done. Total unique topic_ids: {len(_seen_topic_ids)}, images: {len(_seen_urls)}")

"""
读取上个会话的聊天记录 — 开新窗口回顾专用

用法:
  python tools/read_previous_session.py
  python tools/read_previous_session.py --rounds 10    # 显示更多轮
  python tools/read_previous_session.py --list         # 列出所有历史会话
  python tools/read_previous_session.py --no-session-log  # 跳过 session_log

工作原理:
  1. 扫描 ~/.claude/projects/<当前项目>/ 下所有 .jsonl 文件
  2. 按 mtime 排序，跳过最新的（当前会话），取上一个
  3. 解析 user/assistant 消息的 text content block
  4. 输出最后 N 轮对话摘要
  5. 读取 session_log/ 下最新的摘要文件（按文件名排序，最新的日期=最近的会话）

注意事项:
  - Assistant 消息有 type=thinking / text / tool_use 三种 block，只取 text
  - 跳过 <ide_ 开头的行（IDE 环境噪音）
  - session_log 文件名是 YYYY-MM-DD.md，按字符串排序即按日期排序
"""

import json
import os
import sys
from datetime import datetime
from glob import glob

sys.stdout.reconfigure(encoding="utf-8")

# 兼容 "quantify-per" vs "d--quantify-per" 两种目录名
_cwd_base = os.path.basename(os.getcwd())
_projects_root = os.path.join(os.path.expanduser("~"), ".claude/projects")
PROJECT_DIR = _cwd_base
# 尝试匹配实际的 projects 子目录
if os.path.isdir(os.path.join(_projects_root, _cwd_base)):
    JSONL_DIR = os.path.join(_projects_root, _cwd_base)
else:
    # 扫描所有 projects 目录，找名字包含 cwd_base 的
    candidates = [d for d in os.listdir(_projects_root)
                  if os.path.isdir(os.path.join(_projects_root, d))
                  and _cwd_base in d]
    if candidates:
        PROJECT_DIR = candidates[0]
    JSONL_DIR = os.path.join(_projects_root, PROJECT_DIR)


def find_sessions():
    """返回按 mtime 排序的 (path, mtime) 列表，最新的在最后"""
    files = glob(os.path.join(JSONL_DIR, "*.jsonl"))
    if not files:
        return []
    sessions = []
    for fp in files:
        mtime = os.path.getmtime(fp)
        size = os.path.getsize(fp)
        sessions.append((fp, mtime, size))
    sessions.sort(key=lambda x: x[1])  # oldest first
    return sessions


def extract_messages(filepath):
    """从 JSONL 提取 (role, text) 对，role='user' 或 'assistant'"""
    msgs = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = obj.get("type")
            if role not in ("user", "assistant"):
                continue

            content = obj.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue

            texts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "text":
                    continue
                t = block.get("text", "")
                # 跳过 IDE 环境噪音
                if t.startswith("<ide_"):
                    continue
                if t.strip():
                    texts.append(t.strip())

            if texts:
                full_text = "\n".join(texts)
                msgs.append((role, full_text))

    return msgs


def summarize_session(msgs, rounds=6):
    """取最后 rounds 轮对话，返回格式化字符串"""
    if not msgs:
        return "（会话为空，没有找到对话内容）"

    # 取最后 rounds*2 条消息（因为 user+assistant 成对）
    recent = msgs[-(rounds * 2):]

    lines = []
    for role, text in recent:
        prefix = "👤" if role == "user" else "🤖"
        # 截断超长文本（3000字足够覆盖完整的带走清单/总结）
        if len(text) > 3000:
            text = text[:3000] + f"\n   ...（共 {len(text)} 字符，已截断）"
        # 缩进 assistant 的多行文本
        text_display = text.replace("\n", "\n  ")
        lines.append(f"{prefix} {text_display}")
        lines.append("")  # blank line between turns

    return "\n".join(lines)


def summarize_key_topics(msgs):
    """提取关键主题：只看用户消息的开头几行"""
    if not msgs:
        return ""

    user_msgs = [text for role, text in msgs if role == "user"]
    if not user_msgs:
        return ""

    # 用户消息前 3 条的关键词
    topics = []
    keywords = set()
    for msg in user_msgs[:5]:
        # 取第一行作为主题
        first_line = msg.split("\n")[0].strip()
        if len(first_line) > 100:
            first_line = first_line[:100] + "…"
        topics.append(first_line)

    return "\n".join(f"  {t}" for t in topics)


def read_latest_session_log():
    """读取 session_log/ 下最新的摘要文件

    按文件名（YYYY-MM-DD.md）降序排列，最新的日期即最近一次的会话摘要。
    返回 (filename, content) 或 (None, None) 如果目录不存在或没有文件。
    """
    log_dir = os.path.join(os.getcwd(), "session_log")
    if not os.path.isdir(log_dir):
        return None, None

    files = sorted(glob(os.path.join(log_dir, "*.md")), reverse=True)
    if not files:
        return None, None

    latest = files[0]
    with open(latest, "r", encoding="utf-8") as f:
        content = f.read()

    return os.path.basename(latest), content


def print_session_info(fp, mtime, size, index, current=False):
    """打印单个会话文件信息"""
    label = "← 当前会话" if current else ""
    dt = datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
    size_str = f"{size / 1024:.0f}KB" if size > 1024 else f"{size}B"

    # 快速统计消息数
    msg_count = 0
    user_count = 0
    with open(fp, "r", encoding="utf-8") as f:
        for line in f:
            if '"type":"user"' in line:
                user_count += 1
                msg_count += 1
            elif '"type":"assistant"' in line:
                msg_count += 1

    print(f"  [{index}] {dt}  {size_str:>6}  {msg_count}条({user_count}用户)  {os.path.basename(fp)}  {label}")


def main():
    args = set(sys.argv[1:])

    sessions = find_sessions()
    if not sessions:
        print(f"❌ 未找到历史会话文件")
        print(f"   路径: {JSONL_DIR}")
        print(f"   当前目录: {os.getcwd()}")
        sys.exit(1)

    # --list: 只列会话不读取
    if "--list" in args:
        print(f"历史会话列表 ({len(sessions)} 个):")
        for i, (fp, mtime, size) in enumerate(sessions):
            is_current = (i == len(sessions) - 1)
            print_session_info(fp, mtime, size, i, is_current)

        # 也显示 session_log 摘要文件
        log_dir = os.path.join(os.getcwd(), "session_log")
        if os.path.isdir(log_dir):
            log_files = sorted(glob(os.path.join(log_dir, "*.md")))
            if log_files:
                print(f"\n📋 session_log 摘要文件 ({len(log_files)} 个):")
                for lf in reversed(log_files[-5:]):  # 最近5个
                    fname = os.path.basename(lf)
                    size = os.path.getsize(lf)
                    print(f"  {fname} ({size} 字节)")
        return

    if len(sessions) < 2:
        print(f"⚠️  只有当前会话，没有历史会话可读取")
        print_session_info(sessions[0][0], sessions[0][1], sessions[0][2], 0, True)
        if "--no-session-log" not in args:
            _print_session_log()
        return

    # 取上一个会话（排除最新的当前会话）
    prev_fp, prev_mtime, prev_size = sessions[-2]

    # 解析消息
    msgs = extract_messages(prev_fp)

    # 取轮数
    rounds = 6
    for arg in args:
        if arg.startswith("--rounds="):
            try:
                rounds = int(arg.split("=")[1])
            except ValueError:
                pass

    # 输出
    dt = datetime.fromtimestamp(prev_mtime).strftime("%Y-%m-%d %H:%M")
    user_count = sum(1 for r, _ in msgs if r == "user")

    print(f"━" * 50)
    print(f"📋 上个会话回顾")
    print(f"  时间: {dt}")
    print(f"  文件: {os.path.basename(prev_fp)}")
    print(f"  总轮数: {len(msgs)} 条消息（{user_count} 条用户消息）")
    print()

    # 关键主题
    if msgs:
        print(f"📌 会话主题（前5条消息）:")
        print(summarize_key_topics(msgs))
        print()
        print(f"💬 最后 {rounds} 轮对话:")
        print(summarize_session(msgs, rounds))

    print(f"\n{'━' * 50}")

    # 读取 session_log 补充
    if "--no-session-log" not in args:
        _print_session_log()


def _print_session_log():
    """输出最新的 session_log 摘要"""
    log_name, log_content = read_latest_session_log()
    if log_content:
        print(f"\n📋 会话摘要日志 ({log_name}):")
        # 只显示前 2000 字，足够覆盖 5-10行摘要
        if len(log_content) > 2000:
            print(log_content[:2000] + f"\n...（共 {len(log_content)} 字符）")
        else:
            print(log_content)
        print(f"{'━' * 50}")
    else:
        # session_log 不存在是正常情况（第一次用之前没有），不报错
        pass


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
ai_analyzer.py — 多 API 自动切换智能分析
支持 Cloudflare → SiliconFlow → NVIDIA 循环降级

用法:
    from ai_analyzer import analyze_report
    result = analyze_report(report_text)

环境变量（.env 文件）:
    CF_API_KEY      — Cloudflare API Key
    CF_ACCOUNT_ID   — Cloudflare Account ID
    SF_API_KEY      — SiliconFlow API Key
    NVIDIA_API_KEY  — 英伟达 API Key
"""

import os
import sys
import json
import ssl
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

BASE = Path(__file__).parent.resolve()
PERSONA_PATH = BASE / 'prompts' / 'trading_persona.md'
ENV_FILE = BASE / '.env'

# ── 加载 .env ──
def _load_env():
    if not ENV_FILE.exists():
        return
    with open(ENV_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            # 强制覆盖，确保 .env 里的值生效
            os.environ[k] = v.strip().strip('"').strip("'")

_load_env()


# ============================================================
# Provider 配置（按优先级排序）
# ============================================================

PROVIDERS = [
    {
        'name': 'nvidia-v4',
        'type': 'openai',
        'base_url': 'https://integrate.api.nvidia.com/v1',
        'api_key_env': 'NVIDIA_API_KEY',
        'model': 'deepseek-ai/deepseek-v4-pro',
        'timeout': 180,
        'max_retries': 1,
    },
    {
        'name': 'nvidia-v3',
        'type': 'openai',
        'base_url': 'https://integrate.api.nvidia.com/v1',
        'api_key_env': 'NVIDIA_API_KEY',
        'model': 'deepseek-ai/deepseek-v3.1-terminus',
        'timeout': 180,
        'max_retries': 1,
    },
    {
        'name': 'cloudflare',
        'type': 'cloudflare',
        'api_key_env': 'CF_API_KEY',
        'account_id_env': 'CF_ACCOUNT_ID',
        'model': '@cf/deepseek-ai/deepseek-r1-distill-qwen-32b',
        'timeout': 120,
        'max_retries': 2,
    },
    {
        'name': 'siliconflow',
        'type': 'openai',
        'base_url': 'https://api.siliconflow.cn/v1',
        'api_key_env': 'SF_API_KEY',
        'model': 'deepseek-ai/DeepSeek-V3',
        'timeout': 120,
        'max_retries': 2,
    },
]

DEFAULT_MAX_TOKENS = 4096


# ============================================================
# 底层调用
# ============================================================

def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _call_cloudflare(provider: dict, messages: list, max_tokens: int) -> str:
    """调用 Cloudflare Workers AI"""
    api_key = os.environ.get(provider['api_key_env'], '')
    account_id = os.environ.get(provider['account_id_env'], '')
    if not api_key or not account_id:
        raise RuntimeError(f"缺少 {provider['api_key_env']} 或 {provider['account_id_env']}")

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/run/{provider['model']}"
    )
    payload = json.dumps({
        "messages": messages,
        "max_tokens": max_tokens,
    }).encode('utf-8')

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    )

    resp = urllib.request.urlopen(req, context=_ssl_ctx(), timeout=provider['timeout'])
    data = json.loads(resp.read().decode('utf-8'))
    if data.get("success"):
        return data["result"]["response"]
    raise RuntimeError(f"Cloudflare error: {data.get('errors', 'unknown')}")


def _call_openai(provider: dict, messages: list, max_tokens: int) -> str:
    """调用 OpenAI 兼容 API（SiliconFlow / NVIDIA）"""
    api_key = os.environ.get(provider['api_key_env'], '')
    if not api_key:
        raise RuntimeError(f"缺少 {provider['api_key_env']}")

    payload = {
        "model": provider['model'],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "top_p": 1,
    }
    # NVIDIA 特殊参数
    if provider['name'] == 'nvidia':
        payload["extra_body"] = {
            "chat_template_kwargs": {
                "enable_thinking": True,
                "clear_thinking": False
            }
        }

    req = urllib.request.Request(
        f"{provider['base_url'].rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
    )

    resp = urllib.request.urlopen(req, context=_ssl_ctx(), timeout=provider['timeout'])
    data = json.loads(resp.read().decode('utf-8'))
    if "choices" in data and len(data["choices"]) > 0:
        return data["choices"][0]["message"]["content"]
    if "error" in data:
        raise RuntimeError(f"API error: {data['error'].get('message', str(data))}")
    raise RuntimeError(f"Unexpected response: {data}")


# ============================================================
# 路由 + 降级
# ============================================================

def call_llm(messages: list, max_tokens: int = DEFAULT_MAX_TOKENS) -> tuple:
    """
    循环尝试所有 provider，返回 (content, provider_name)
    全部失败则抛出 RuntimeError
    """
    last_error = None
    call_log = []

    for provider in PROVIDERS:
        api_key = os.environ.get(provider['api_key_env'], '')
        if not api_key:
            msg = f"跳过 {provider['name']}: 未配置 {provider['api_key_env']}"
            call_log.append({"provider": provider['name'], "status": "SKIP", "reason": msg})
            print(f"  [API] {msg}")
            continue

        for attempt in range(provider['max_retries']):
            try:
                print(f"  [API] 尝试 {provider['name']} (attempt {attempt + 1}/{provider['max_retries']})...")
                t0 = time.time()
                if provider['type'] == 'cloudflare':
                    result = _call_cloudflare(provider, messages, max_tokens)
                else:
                    result = _call_openai(provider, messages, max_tokens)
                elapsed = time.time() - t0
                print(f"  [API] {provider['name']} 成功 ({elapsed:.1f}s)")
                return result, provider['name']
            except Exception as e:
                last_error = str(e)
                call_log.append({
                    "provider": provider['name'],
                    "status": "FAIL",
                    "error": last_error,
                    "attempt": attempt + 1,
                })
                print(f"  [API] {provider['name']} 失败: {last_error}")
                if attempt < provider['max_retries'] - 1:
                    time.sleep(1)

    # 全部失败
    raise RuntimeError(
        f"所有 API Provider 均调用失败。最后一个错误: {last_error}\n"
        f"尝试记录: {json.dumps(call_log, ensure_ascii=False, indent=2)}"
    )


# ============================================================
# 业务层
# ============================================================

def load_persona():
    """加载交易人格系统提示词"""
    if not PERSONA_PATH.exists():
        return ""
    with open(PERSONA_PATH, 'r', encoding='utf-8') as f:
        return f.read()


def _preprocess_report(report_text: str) -> str:
    """
    预处理报告文本，强制标注信号类型。
    让 AI 没机会把卖信号当成机会。
    """
    import re
    lines = report_text.split('\n')
    processed = []
    for line in lines:
        # 在包含 ★买 的行前加 [机会]
        if '★买' in line and '[机会]' not in line:
            line = line.replace('★买', '[机会] ★买')
        # 在包含 ★卖 的行前加 [风险]
        if '★卖' in line and '[风险]' not in line:
            line = line.replace('★卖', '[风险] ★卖')
        # 在包含 完整闭环(卖) 的行加警告
        if '完整闭环(卖)' in line:
            line = line.replace('完整闭环(卖)', '完整闭环(卖) [风险警示: A股不能做空，禁止买入]')
        # 在包含 完整闭环(买) 的行加标注
        if '完整闭环(买)' in line:
            line = line.replace('完整闭环(买)', '完整闭环(买) [机会确认]')
        # 在机会列表里出现卖信号的行，强制修正
        if '机会' in line and ('★卖' in line or '完整闭环(卖)' in line):
            line = line.replace('机会', '【错误标注-应为风险】')
        processed.append(line)
    return '\n'.join(processed)


def analyze_report(report_text: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
    """
    分析报告文本，返回AI智能解读

    Returns:
        dict: {
            "content": str,      # AI 分析结果（Markdown）
            "provider": str,     # 实际调用的 provider 名称
            "error": str,        # 如果有错误
        }
    """
    persona = load_persona()
    if not persona:
        return {"content": "", "provider": "", "error": "无法加载 trading_persona.md"}

    # 预处理：强制标注信号类型
    report_processed = _preprocess_report(report_text)

    messages = [
        {"role": "system", "content": persona},
        {"role": "user", "content": f"""请基于以下每日量化报告，按照你的交易人格进行分析。

重要提醒：
- 标注 [机会] 的信号 = 可以买入赚钱
- 标注 [风险] 的信号 = 需要减仓回避，A股不能做空
- 绝对不要把 [风险] 信号排到"机会"列表里

报告内容：
{report_processed}"""}
    ]

    try:
        content, provider_name = call_llm(messages, max_tokens)
        return {"content": content, "provider": provider_name, "error": ""}
    except Exception as e:
        return {"content": "", "provider": "", "error": str(e)}


def main():
    """命令行测试"""
    import argparse
    parser = argparse.ArgumentParser(description='AI分析报告（多API自动切换）')
    parser.add_argument('report_file', help='报告文件路径')
    parser.add_argument('--output', '-o', help='输出文件路径')
    parser.add_argument('--max-tokens', type=int, default=DEFAULT_MAX_TOKENS)
    args = parser.parse_args()

    if not os.path.exists(args.report_file):
        print(f"[错误] 文件不存在: {args.report_file}")
        sys.exit(1)

    with open(args.report_file, 'r', encoding='utf-8') as f:
        report = f.read()

    print("正在调用 API 分析（多 provider 自动切换）...")
    result = analyze_report(report, args.max_tokens)

    if result['error']:
        print(f"\n[错误] {result['error']}")
        sys.exit(1)

    print(f"\n[成功] 使用 provider: {result['provider']}")
    print("=" * 60)
    print(result['content'])
    print("=" * 60)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(result['content'])
        print(f"结果已保存: {args.output}")


if __name__ == '__main__':
    main()

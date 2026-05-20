# -*- coding: utf-8 -*-
"""
AI 分析引擎 v3 — 多 API 自动切换 + 交易框架注入

加载 prompts/trading_persona.md（人格）+ prompts/trading_analysis_framework.md（分析框架）
合并为系统提示词，指导 AI 按战役级框架输出分析。

API 优先级: NVIDIA V4 Flash(免费) → NVIDIA V4 Pro → 硅基流动V4 Flash(已充值) → DeepSeek官方V4(自有Key)
Cloudflare 暂跳过。
"""

import os
import json
import urllib.request
import urllib.error
import ssl
from pathlib import Path

BASE = Path(__file__).parent.resolve()
ENV_PATH = BASE / '.env'
PERSONA_PATH = BASE / 'prompts' / 'trading_persona.md'
FRAMEWORK_PATH = BASE / 'prompts' / 'trading_analysis_framework.md'

# ─────────────────────────────────────────
# 环境变量加载
# ─────────────────────────────────────────

def _load_env():
    """加载 .env 文件到 os.environ"""
    if not ENV_PATH.exists():
        return
    with open(ENV_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip()
            if key not in os.environ:
                os.environ[key] = val

_load_env()

# ─────────────────────────────────────────
# SSL 上下文
# ─────────────────────────────────────────

def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

# ─────────────────────────────────────────
# API Provider 配置
# ─────────────────────────────────────────

PROVIDERS = [
    # ─── 1. NVIDIA V4 Flash（免费层，薅羊毛首选） ───
    {
        'name': 'nvidia (v4 flash)',
        'type': 'openai',
        'api_key_env': 'NVIDIA_API_KEY',
        'model': 'deepseek-ai/deepseek-v4-flash',
        'base_url': 'https://integrate.api.nvidia.com/v1',
    },
    # ─── 2. NVIDIA V4 Pro（免费额度用完前的兜底） ───
    {
        'name': 'nvidia (v4 pro)',
        'type': 'openai',
        'api_key_env': 'NVIDIA_API_KEY',
        'model': 'deepseek-ai/deepseek-v4-pro',
        'base_url': 'https://integrate.api.nvidia.com/v1',
    },
    # ─── 3. 硅基流动 V4 Flash（已充值，先用完余额） ───
    {
        'name': '硅基流动 (v4 flash)',
        'type': 'openai',
        'api_key_env': 'SF_API_KEY',
        'model': 'deepseek-ai/DeepSeek-V4-Flash',
        'base_url': 'https://api.siliconflow.cn/v1',
    },
    # ─── 4. DeepSeek 官方 V4 Flash（自有 Key，终极兜底） ───
    {
        'name': 'deepseek (v4 flash)',
        'type': 'openai',
        'api_key_env': 'DEEPSEEK_API_KEY',
        'model': 'deepseek-v4-flash',
        'base_url': 'https://api.deepseek.com',
    },
    # ─── 5. DeepSeek 官方 V4 Pro（如果需要更强推理） ───
    {
        'name': 'deepseek (v4 pro)',
        'type': 'openai',
        'api_key_env': 'DEEPSEEK_API_KEY',
        'model': 'deepseek-v4-pro',
        'base_url': 'https://api.deepseek.com',
    },
    # ─── Cloudflare（暂跳过，以后再用） ───
    # {
    #     'name': 'cloudflare',
    #     'type': 'cloudflare',
    #     'api_key_env': 'CF_API_KEY',
    #     'account_id_env': 'CF_ACCOUNT_ID',
    # },
]

# ─────────────────────────────────────────
# API 调用函数
# ─────────────────────────────────────────

def _call_openai(provider, messages, max_tokens=4096):
    """调用 OpenAI 兼容 API（SiliconFlow / NVIDIA）"""
    api_key = os.environ.get(provider['api_key_env'], '')
    if not api_key:
        raise RuntimeError(f'缺少API密钥: {provider["api_key_env"]}')

    model = provider['model']
    base_url = provider['base_url'].rstrip('/')
    url = f'{base_url}/chat/completions'

    body = json.dumps({
        'model': model,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': 0.7,
    }).encode('utf-8')

    req = urllib.request.Request(
        url, data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
    )
    try:
        resp = urllib.request.urlopen(req, context=_ssl_ctx(), timeout=120)
        result = json.loads(resp.read().decode('utf-8'))
        return result['choices'][0]['message']['content']
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'API error ({provider["name"]}): {err_body[:200]}')
    except Exception as e:
        raise RuntimeError(f'API error ({provider["name"]}): {str(e)[:200]}')


def _call_cloudflare(provider, messages, max_tokens=4096):
    """调用 Cloudflare Workers AI"""
    api_key = os.environ.get(provider['api_key_env'], '')
    account_id = os.environ.get(provider['account_id_env'], '')
    if not api_key or not account_id:
        raise RuntimeError(f'缺少Cloudflare配置: {provider["api_key_env"]} / {provider["account_id_env"]}')

    # Cloudflare 用 text-generation 模型，从消息中提取提示
    prompt_parts = []
    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        if role == 'system':
            prompt_parts.append(f'System: {content}')
        else:
            prompt_parts.append(f'User: {content}')
    full_prompt = '\n\n'.join(prompt_parts)

    url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/meta/llama-3.1-8b-instruct'
    body = json.dumps({
        'prompt': full_prompt,
        'max_tokens': max_tokens,
    }).encode('utf-8')

    req = urllib.request.Request(
        url, data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
    )
    try:
        resp = urllib.request.urlopen(req, context=_ssl_ctx(), timeout=120)
        result = json.loads(resp.read().decode('utf-8'))
        return result['result']['response']
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Cloudflare API error: {err_body[:200]}')
    except Exception as e:
        raise RuntimeError(f'Cloudflare API error: {str(e)[:200]}')


def call_llm(system_prompt, user_message, max_tokens=4096):
    """
    循环调用 providers，全部失败则抛 RuntimeError。
    Returns: (content: str, provider_name: str)
    """
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_message},
    ]

    errors = []
    for provider in PROVIDERS:
        try:
            if provider['type'] == 'cloudflare':
                content = _call_cloudflare(provider, messages, max_tokens)
            else:
                content = _call_openai(provider, messages, max_tokens)
            return content, provider['name']
        except Exception as e:
            errors.append(f'{provider["name"]}: {str(e)[:100]}')
            continue

    raise RuntimeError(f'所有 API Provider 均失败。最后一轮: {"; ".join(errors)}')

# ─────────────────────────────────────────
# 加载文件
# ─────────────────────────────────────────

def load_persona():
    """加载交易人格模板"""
    if not PERSONA_PATH.exists():
        return ''
    with open(PERSONA_PATH, 'r', encoding='utf-8') as f:
        return f.read()


def load_framework():
    """加载交易分析框架"""
    if not FRAMEWORK_PATH.exists():
        return ''
    with open(FRAMEWORK_PATH, 'r', encoding='utf-8') as f:
        return f.read()

# ─────────────────────────────────────────
# 报告预处理
# ─────────────────────────────────────────

def _preprocess_report(text):
    """
    预处理报告文本，强制标注信号类型。
    让 AI 没有误解信号的机会。
    """
    # 标记买入信号
    for kw in ['★买', '★买入', 'buy_signal']:
        text = text.replace(kw, '[买入] ' + kw)
    # 标记卖出信号
    for kw in ['★卖', '★卖出', 'sell_signal']:
        text = text.replace(kw, '[卖出] ' + kw)
    return text

# ─────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────

def analyze_report(report_text, max_tokens=4096):
    """
    传入报告文本，返回 AI 智能分析结果。

    Returns:
        dict: {
            "content": str,      # AI 分析结果（Markdown）
            "provider": str,     # 实际用到的 provider 名称
            "error": str,        # 如有错误
        }
    """
    try:
        persona = load_persona()
        framework = load_framework()

        # 合并人格 + 框架为系统提示词
        parts = []
        if persona:
            parts.append(persona)
        if framework:
            parts.append('\n\n---\n\n## 分析框架（必须遵守）\n\n')
            parts.append(framework)
        parts.append('\n\n---\n\n### 输出要求\n\n')
        parts.append('严格按照 trading_analysis_framework.md 第六节的模板格式输出。')
        parts.append('分析2-3句、判断1句、结论1句、跟踪1句。总字数不超过600字。')

        system_prompt = ''.join(parts)

        processed = _preprocess_report(report_text)
        user_message = f'请对以下每日量化报告进行分析，按框架模板输出。\n\n{processed}'

        content, provider = call_llm(system_prompt, user_message, max_tokens)
        return {'content': content, 'provider': provider, 'error': ''}

    except Exception as e:
        return {'content': '', 'provider': '', 'error': str(e)}


# ─────────────────────────────────────────
# CLI
# ─────────────────────────────────────────

def main():
    """命令行入口：测试用"""
    import argparse
    parser = argparse.ArgumentParser(description='AI分析引擎（多API自动切换）')
    parser.add_argument('report_file', help='报告文件路径')
    parser.add_argument('--output', help='输出文件路径（可选）')
    args = parser.parse_args()

    with open(args.report_file, 'r', encoding='utf-8') as f:
        report_text = f.read()

    result = analyze_report(report_text)
    if result.get('error'):
        print(f'[错误] {result["error"]}')
    else:
        print(f'[provider] {result["provider"]}')
        print(result['content'])
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(result['content'])


if __name__ == '__main__':
    main()

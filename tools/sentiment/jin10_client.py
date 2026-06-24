# -*- coding: utf-8 -*-
"""
金十数据 MCP 客户端 — 获取快讯/搜索快讯
========================================
用法:
  python tools/sentiment/jin10_client.py --flash           # 最新快讯
  python tools/sentiment/jin10_client.py --search 美联储    # 搜索快讯
  python tools/sentiment/jin10_client.py --flash --save    # 保存 JSON
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error
import ssl
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
JIN10_MCP_URL = "https://mcp.jin10.com/mcp"
JIN10_MCP_TOKEN = "sk-w49lR1ClzA9txVBAoWAO7qcTVAC3yrULGTzB780dCQU"

sys.stdout.reconfigure(encoding="utf-8")


class Jin10MCPClient:
    """金十 MCP 客户端"""

    def __init__(self, token: str = JIN10_MCP_TOKEN, url: str = JIN10_MCP_URL):
        self.url = url
        self.token = token
        self.session_id = None

    def _request(self, method: str, params: dict = None) -> dict:
        req_id = int(time.time() * 1000) % 100000
        body = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params:
            body["params"] = params

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            raw = resp.read().decode("utf-8")
            if self.session_id is None:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
        except Exception as e:
            return {"error": str(e)}

        for line in raw.split("\n"):
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
        return {"error": f"no data: {raw[:200]}"}

    def initialize(self) -> bool:
        resp = self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "quantify-per", "version": "1.0"},
        })
        return "result" in resp

    def _parse_text_response(self, resp: dict) -> dict:
        """解析 MCP text content → 实际 JSON data"""
        if "error" in resp:
            return {"error": resp["error"]}
        content = resp.get("result", {}).get("content", [])
        for c in content:
            if c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except (json.JSONDecodeError, KeyError):
                    continue
        return {"error": "no parseable content"}

    def list_flash(self, cursor: str = None) -> dict:
        """获取快讯分页，返回 {"items": [...], "has_more": bool, "next_cursor": str}"""
        args = {}
        if cursor:
            args["cursor"] = cursor
        resp = self._request("tools/call", {"name": "list_flash", "arguments": args})
        parsed = self._parse_text_response(resp)
        data = parsed.get("data", {})
        return {
            "items": data.get("items", []),
            "has_more": data.get("has_more", False),
            "next_cursor": data.get("next_cursor"),
        }

    def get_all_flash(self, max_pages: int = 3) -> list:
        """获取多页快讯"""
        all_items = []
        cursor = None
        for _ in range(max_pages):
            result = self.list_flash(cursor)
            items = result.get("items", [])
            if not items:
                break
            all_items.extend(items)
            if not result.get("has_more"):
                break
            cursor = result.get("next_cursor")
            time.sleep(0.3)
        return all_items

    def search_flash(self, keyword: str) -> list:
        """搜索快讯，最多150条"""
        resp = self._request("tools/call", {
            "name": "search_flash",
            "arguments": {"keyword": keyword},
        })
        parsed = self._parse_text_response(resp)
        return parsed.get("data", {}).get("items", [])

    def list_news(self, cursor: str = None) -> dict:
        """获取文章分页"""
        args = {}
        if cursor:
            args["cursor"] = cursor
        resp = self._request("tools/call", {"name": "list_news", "arguments": args})
        parsed = self._parse_text_response(resp)
        data = parsed.get("data", {})
        return {
            "items": data.get("items", []),
            "has_more": data.get("has_more", False),
            "next_cursor": data.get("next_cursor"),
        }

    def search_news(self, keyword: str, cursor: str = None) -> dict:
        """搜索文章"""
        args = {"keyword": keyword}
        if cursor:
            args["cursor"] = cursor
        resp = self._request("tools/call", {"name": "search_news", "arguments": args})
        parsed = self._parse_text_response(resp)
        data = parsed.get("data", {})
        return {
            "items": data.get("items", []),
            "has_more": data.get("has_more", False),
            "next_cursor": data.get("next_cursor"),
        }

    def get_quote(self, symbol: str) -> dict:
        """获取实时行情"""
        resp = self._request("tools/call", {
            "name": "get_quote",
            "arguments": {"symbol": symbol},
        })
        parsed = self._parse_text_response(resp)
        return parsed.get("data", {})

    def list_calendar(self) -> list:
        """获取本周财经日历"""
        resp = self._request("tools/call", {"name": "list_calendar", "arguments": {}})
        parsed = self._parse_text_response(resp)
        return parsed.get("data", {}).get("items", [])


def fetch_jin10_flash(max_pages: int = 3) -> list[dict]:
    """快捷函数: 获取金十快讯列表"""
    client = Jin10MCPClient()
    if not client.initialize():
        return []
    return client.get_all_flash(max_pages)


def main():
    parser = argparse.ArgumentParser(description="金十数据 MCP 快讯")
    parser.add_argument("--flash", action="store_true", help="获取最新快讯")
    parser.add_argument("--search", type=str, help="搜索快讯关键词")
    parser.add_argument("--pages", type=int, default=3, help="快讯翻页数(默认3)")
    parser.add_argument("--save", action="store_true", help="保存到 JSON")
    parser.add_argument("--calendar", action="store_true", help="本周财经日历")
    args = parser.parse_args()

    client = Jin10MCPClient()
    if not client.initialize():
        print("[jin10] 初始化失败", file=sys.stderr)
        sys.exit(1)

    all_items = []

    if args.flash:
        items = client.get_all_flash(max_pages=args.pages)
        print(f"--- Jin10 最新快讯 ({len(items)} 条) ---")
        for i, item in enumerate(items[:20], 1):
            print(f"[{i}] {item['time']} {item['content'][:200]}")
        if len(items) > 20:
            print(f"... 以及 {len(items) - 20} 条更多")
        all_items = items

    if args.search:
        items = client.search_flash(args.search)
        print(f"\n--- Jin10 搜索: \"{args.search}\" ({len(items)} 条) ---")
        for i, item in enumerate(items[:20], 1):
            print(f"[{i}] {item['time']} {item['content'][:200]}")
        all_items = items

    if args.calendar:
        items = client.list_calendar()
        print(f"\n--- 本周财经日历 ({len(items)} 条) ---")
        for item in items[:20]:
            print(f"  {item}")

    if args.save and all_items:
        out_dir = PROJECT_ROOT / "signals" / "tracking" / "_macro"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"jin10_flash_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        out_path.write_text(
            json.dumps({
                "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(all_items),
                "items": all_items,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\n[保存] {out_path}")


if __name__ == "__main__":
    main()

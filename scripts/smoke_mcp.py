"""End-to-end check: drive the MCP server over stdio like a real client would.

Run only when the debug-port browser is up and U+ is signed in:

    python _smoke_mcp.py

It exercises an async tool (the exact path that used to fail with
"Playwright Sync API inside the asyncio loop") and one browse tool.
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "uplus_mcp.server"],
        env=None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"tools exposed: {len(names)}")
            print("uplus tools:", [n for n in names if n.startswith("uplus_")])
            print("browse tools:", [n for n in names if n.startswith("browse_")])

            print("\n--- uplus_check_login ---")
            res = await session.call_tool("uplus_check_login", {})
            print(json.dumps(res.structuredContent or res.content, ensure_ascii=False, default=str)[:800])

            print("\n--- browse_open ---")
            res = await session.call_tool(
                "browse_open", {"url": "https://www.eduplus.net/workbench/index/school"}
            )
            print(json.dumps(res.structuredContent or res.content, ensure_ascii=False, default=str)[:600])

            print("\n--- browse_snapshot ---")
            res = await session.call_tool("browse_snapshot", {"max_nodes": 8})
            import json as _json
            payload = res.structuredContent or {}
            if isinstance(payload, dict) and "result" in payload:
                payload = payload["result"]
            if isinstance(payload, str):
                payload = _json.loads(payload)
            print("url:", payload.get("url"))
            print("title:", payload.get("title"))
            print("nodes:", len(payload.get("nodes") or []))
            print("bodyText head:", (payload.get("bodyText") or "")[:160].replace("\n", " | "))

            print("\n--- uplus_list_courses ---")
            res = await session.call_tool("uplus_list_courses", {})
            import json as _json
            raw = res.structuredContent or {}
            courses = raw.get("result") if isinstance(raw, dict) else raw
            if isinstance(courses, str):
                courses = _json.loads(courses)
            print("course count:", len(courses or []))
            for c in (courses or [])[:5]:
                print("  -", c.get("name"), "|", c.get("teacher"))
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

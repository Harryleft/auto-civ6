#!/usr/bin/env python3
"""Ad-hoc MCP tool caller for pi. Usage:
    uv run python scripts/_mcp_call.py <tool_name> '<json args>' [--raw]
Calls the civ6 MCP server over stdio and prints the tool result as JSON.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


async def main() -> int:
    tool = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
    raw = "--raw" in sys.argv

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "--directory", str(REPO), "civ-mcp"],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            if result.isError:
                print(f"ERROR: {result.content}", file=sys.stderr)
                return 1
            for item in result.content:
                if item.type == "text":
                    print(item.text)
                else:
                    print(json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

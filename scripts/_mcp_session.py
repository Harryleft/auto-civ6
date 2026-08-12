#!/usr/bin/env python3
"""Persistent MCP session for pi. Reads JSON commands from stdin:
    {"tool": "get_game_overview", "args": {}}
Prints JSON result per line. One line per command, one line per result.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


OUT_FILE = Path("/tmp/civ6_mcp_out")


def emit(payload: dict, out: Path) -> None:
    """Append result JSON to the caller-specified output file (one line per result)."""
    with out.open("a") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        f.flush()


async def main() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "--directory", str(REPO), "civ-mcp"],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            loop = asyncio.get_event_loop()
            # Read commands from stdin in a thread
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    cmd = json.loads(line)
                except json.JSONDecodeError:
                    emit({"ok": False, "error": "bad json", "result": None}, Path("/tmp/civ6_badjson_out"))
                    continue
                out_path = Path(cmd.get("out", "/tmp/civ6_mcp_out"))
                tool = cmd.get("tool")
                args = cmd.get("args") or {}
                try:
                    if tool == "__list__":
                        tools = await session.list_tools()
                        names = [t.name for t in tools.tools]
                        emit({"ok": True, "error": None, "result": "\n".join(names)}, out_path)
                        continue
                    result = await session.call_tool(tool, args)
                    text = "\n".join(
                        i.text for i in result.content if i.type == "text"
                    )
                    emit(
                        {
                            "ok": not result.isError,
                            "error": None,
                            "result": text,
                        },
                        out_path,
                    )
                except Exception as e:  # noqa: BLE001
                    emit({"ok": False, "error": str(e), "result": None}, out_path)


if __name__ == "__main__":
    asyncio.run(main())

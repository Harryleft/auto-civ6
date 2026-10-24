#!/usr/bin/env python3
"""Run explicit Codex-selected calls through the fixed-seat stdio MCP entry."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import shutil

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(args):
    root = Path(__file__).resolve().parents[1]
    commands = json.loads(args.calls.read_text())
    params = StdioServerParameters(command=shutil.which("uv"), args=["run", "--locked", "--directory", str(root),
        "python", str(root / "scripts/hotseat_agent.py"), "--seat", str(args.seat), "--run", str(args.run.resolve())])
    results = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for command in commands:
                result = await session.call_tool(command["tool"], command.get("arguments", {}))
                rows = [c.text for c in result.content if c.type == "text"]
                record = {**command, "content": rows, "is_error": result.isError}
                results.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
                args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
                if result.isError or any(any(tag in row for tag in ("ERR:", "WAIT|", "BLOCKED|", "Error:")) for row in rows):
                    break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seat", type=int, choices=[0, 1], required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))

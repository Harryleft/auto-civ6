"""Read-only live smoke through the formal ``civ-mcp`` MCP entry point.

Run this file directly, never through pytest.  It starts the installed
``civ-mcp`` console command in a child process, initialises its MCP session,
lists the served Runtime tools, then calls ``get_runtime_context`` exactly
once.  It never sends a game mutation, retries an unknown read, loads a save,
or makes a decision.

Example:

    uv run python tests/manual/test_runtime_entry_smoke.py \\
      --branch save-0001 --store /tmp/civ6-runtime-entry.sqlite3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any, NoReturn

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_RUNTIME_TOOLS = frozenset({"get_runtime_context", "end_turn"})
LEGACY_TOOL_NAMES = frozenset(
    {
        "get_game_overview",
        "get_game_state",
        "get_belief_summary",
        "route_belief_decision",
        "run_lua",
    }
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a read-only live smoke through the formal civ-mcp entry."
    )
    parser.add_argument(
        "--branch",
        required=True,
        help="host-selected stable branch token for the currently loaded save",
    )
    parser.add_argument(
        "--store",
        required=True,
        type=Path,
        help="explicit SQLite operation-store path; it is retained after the smoke",
    )
    return parser.parse_args()


def _context_from_result(result: Any) -> dict[str, object]:
    """Read exactly one JSON text response from the Runtime context tool."""
    if result.isError:
        raise RuntimeError(f"get_runtime_context returned an MCP error: {result.content!r}")
    texts = [item.text for item in result.content if item.type == "text"]
    if len(texts) != 1:
        raise RuntimeError(f"expected one text response, received: {result.content!r}")
    payload = json.loads(texts[0])
    if not isinstance(payload, dict):
        raise RuntimeError("get_runtime_context did not return an object payload")
    return payload


def _turn_from_context(context: dict[str, object]) -> int:
    try:
        facts = context["facts"]
        overview = facts["overview"]  # type: ignore[index]
        value = overview["value"]  # type: ignore[index]
        turn = value["turn"]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Runtime context has no readable overview turn") from exc
    if not isinstance(turn, int) or turn < 0:
        raise RuntimeError(f"Runtime context returned an invalid turn: {turn!r}")
    return turn


async def _run(args: argparse.Namespace) -> int:
    environment = os.environ.copy()
    environment.update(
        {
            "CIV_MCP_RUNTIME_BRANCH": args.branch,
            "CIV_MCP_RUNTIME_STORE": str(args.store),
        }
    )
    server = StdioServerParameters(
        command="uv",
        args=["run", "--directory", str(REPOSITORY_ROOT), "civ-mcp"],
        env=environment,
        cwd=str(REPOSITORY_ROOT),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}
            missing = sorted(REQUIRED_RUNTIME_TOOLS - tool_names)
            legacy_present = sorted(LEGACY_TOOL_NAMES & tool_names)
            if missing or legacy_present:
                raise RuntimeError(
                    f"formal entry tool boundary failed: missing={missing}, "
                    f"legacy_present={legacy_present}"
                )
            context = _context_from_result(
                await session.call_tool("get_runtime_context", {})
            )

    unknown = context.get("unknown")
    if not isinstance(unknown, list):
        raise RuntimeError("Runtime context has no readable unknown collection")
    result = {
        "phase": "FORMAL_ENTRY_READ",
        "branch": args.branch,
        "tool_count": len(tool_names),
        "required_tools": sorted(REQUIRED_RUNTIME_TOOLS),
        "legacy_tools_present": legacy_present,
        "turn": _turn_from_context(context),
        "unknown": unknown,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if unknown:
        print("formal entry read is incomplete; no mutation was sent.", file=sys.stderr)
        return 2
    return 0


def main() -> NoReturn:
    raise SystemExit(asyncio.run(_run(_arguments())))


if __name__ == "__main__":
    main()

"""Run a bounded, deterministic gameplay check through the actual stdio MCP server.

This script starts/loads and operates the real game only when explicitly run.
The fixed blocker policy belongs to this test driver, never to the runtime.
Unknown results, hangs and unsupported blockers abort without replay or recovery.

Usage: uv run python scripts/integration_turns.py --turns 10
Always starts from benchmark_turn_1. Exit 0 means all turns confirmed.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from pathlib import Path
import re
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class DriverError(RuntimeError):
    pass


async def call(session: ClientSession, tool: str, arguments: dict | None = None,
               *, allow_error: bool = False) -> str:
    """Call once; the server owns execution, verification and lifecycle state."""
    print(f"[call] {tool} {arguments or {}}", flush=True)
    try:
        result = await session.call_tool(tool, arguments or {})
    except Exception as exc:
        raise DriverError(f"{tool}: transport failed; outcome may be unknown: {exc}") from exc
    text = "\n".join(c.text for c in result.content if c.type == "text").strip()
    print(f"[result] {tool}: {text}", flush=True)
    if not text:
        raise DriverError(f"{tool}: empty result")
    uncertain = ("HANG:", "OUTCOME_UNKNOWN", "result unknown", "outcome unknown")
    if any(marker.lower() in text.lower() for marker in uncertain):
        raise DriverError(f"{tool}: uncertain result; inspect the game explicitly before another action")
    if not allow_error and (result.isError or text.startswith(("Error", "ERR", "FAILED", "WARNING"))):
        raise DriverError(f"{tool}: {text}")
    return text


def overview_turn(text: str) -> int | None:
    match = re.search(r"^Turn (\d+)(?:[/ |])", text, re.MULTILINE)
    return int(match[1]) if match else None


async def prepare(session: ClientSession, save: str | None) -> int:
    overview = await call(session, "get_game_overview", allow_error=True)
    turn = overview_turn(overview)
    if save:
        if turn is not None:
            # --save explicitly authorizes replacing the loaded session.
            loaded = await call(session, "restart_and_load", {"save_name": save})
        else:
            await call(session, "launch_game")
            loaded = await call(session, "load_game_save", {"save_name": save})
        if not loaded.startswith("Loaded "):
            raise DriverError(f"Save load was not confirmed: {loaded}")
        turn = overview_turn(await call(session, "get_game_overview"))
    if turn is None:
        raise DriverError("No loaded game confirmed; supply --save or load a game manually")
    return turn


async def resolve_blockers(session: ClientSession, report: str) -> None:
    """Fixed test policy: skip units, choose first available research/production.

    Unrecognized policy, diplomacy, religion or Congress decisions abort for
    manual handling. A new end_turn is allowed only after a reported blocker
    was handled; unknown outcomes never reach this function.
    """
    blockers = re.findall(r"^\s*- ([^:]+):", report, re.MULTILINE)
    if not blockers or any(kind not in {"Units", "Research", "Civic", "Production"} for kind in blockers):
        raise DriverError(f"Unsupported blocker; handle explicitly through MCP:\n{report}")
    for kind in dict.fromkeys(blockers):
        if kind == "Units":
            await call(session, "skip_remaining_units")
        elif kind in {"Research", "Civic"}:
            text = await call(session, "get_tech_civics")
            section = "Available techs:" if kind == "Research" else "Available civics:"
            available = text.partition(section)[2].split("\n\n", 1)[0]
            match = re.search(r"\((TECH_\w+|CIVIC_\w+)\)", available)
            if not match:
                raise DriverError(f"No available {kind.lower()} confirmed")
            await call(session, "set_research", {
                "tech_or_civic": match[1], "category": "tech" if kind == "Research" else "civic",
            })
        else:
            cities = await call(session, "get_cities")
            ids = [int(m[1]) for line in cities.splitlines()
                   if "Building: nothing" in line or "Building: CORRUPTED_QUEUE" in line
                   if (m := re.search(r"\[id:(\d+)\]", line))]
            if not ids:
                raise DriverError("Production blocker without a confirmed idle city")
            for city_id in ids:
                options = await call(session, "get_city_production", {"city_id": city_id})
                choice = None
                category = None
                for line in options.splitlines():
                    if line in {"Units:", "Buildings:", "Projects:"}:
                        category = {"Units:": "UNIT", "Buildings:": "BUILDING", "Projects:": "PROJECT"}[line]
                    elif not line.startswith("  "):
                        category = None
                    elif category and (match := re.match(r"\s+(.+?) \(cost ", line)):
                        choice = (category, match[1])
                        break
                if not choice:
                    raise DriverError(f"No production option without placement coordinates for city {city_id}")
                await call(session, "set_city_production", {
                    "city_id": city_id, "item_type": choice[0], "item_name": choice[1],
                })


async def run(session: ClientSession, save: str | None, turns: int,
              max_blocker_rounds: int = 8) -> int:
    completed = 0
    try:
        current = await prepare(session, save)
        blocker_rounds = 0
        while completed < turns:
            result = await call(session, "end_turn")
            if result.startswith("Cannot end turn"):
                blocker_rounds += 1
                if blocker_rounds > max_blocker_rounds:
                    raise DriverError(f"Exceeded {max_blocker_rounds} blocker rounds without advancement")
                await resolve_blockers(session, result)
                continue
            advance = re.match(r"Turn (\d+) -> (\d+)(?:\n|$)", result)
            if not advance or int(advance[2]) <= int(advance[1]) or int(advance[1]) != current:
                raise DriverError(f"Turn advance not confirmed from {current}: {result}")
            current = int(advance[2])
            completed += 1
            blocker_rounds = 0
            print(f"[confirmed] {completed}/{turns}; current turn {current}", flush=True)
            if "GAME OVER" in result and completed < turns:
                raise DriverError("Game ended before requested turn count")
    except DriverError as exc:
        print(f"[ABORT] {exc}\n[summary] {completed}/{turns} turns confirmed", flush=True)
        return 1
    print(f"[summary] {completed}/{turns} turns confirmed", flush=True)
    return 0


async def play(save: str | None, turns: int, timeout: float = 900) -> int:
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "civ_mcp"],
        cwd=Path(__file__).resolve().parent.parent,
    )
    try:
        async with asyncio.timeout(timeout):
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=300)) as session:
                    await session.initialize()
                    return await run(session, save, turns)
    except TimeoutError:
        print(f"[ABORT] Total time limit {timeout:g}s exceeded; inspect game state before retrying", flush=True)
    except Exception as exc:
        print(f"[ABORT] MCP session failed: {exc}", flush=True)
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", default="benchmark_turn_1", choices=["benchmark_turn_1"],
                        help="唯一原始基准；运行会替换当前已加载对局")
    parser.add_argument("--turns", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=900, help="Total runtime limit in seconds")
    args = parser.parse_args()
    if args.turns < 1 or args.timeout <= 0:
        parser.error("--turns and --timeout must be positive")
    try:
        code = asyncio.run(play(args.save, args.turns, args.timeout))
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()

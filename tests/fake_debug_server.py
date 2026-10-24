"""Offline stdio fixture: real server/GameState with a scripted engine boundary."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from civ_mcp import game_state, server
from civ_mcp.game_state import GameState
from mcp.server.fastmcp import Context
from test_action_confirmation import Engine, no_dismiss


parser = argparse.ArgumentParser()
parser.add_argument("--receipt", type=Path, required=True)
parser.add_argument("--snapshot", choices=("ready", "pending", "changed-turn", "read-error"))
args = parser.parse_args()
game_state.ACTION_CONFIRM_TIMEOUT = .05
game_state.ACTION_CONFIRM_INTERVAL = .001
engine = Engine(writes=[["OK:MOVING_TO|22,9|from:22,11"]],
                reads=[ConnectionError("lost response"), ["POS|22|9|moves:1|busy:false"]])
engine.is_connected = False
game = GameState(engine)
game.dismiss_popup = no_dismiss
observed = []


async def units():
    observed.append("get_units")
    return []


game.get_units = units


async def auxiliary_query():
    return None


game.get_threat_scan = auxiliary_query
game.get_trade_routes = auxiliary_query


if args.snapshot:
    from civ_mcp.lua import GameOverview
    overview_reads = 0

    async def identity():
        return ("rome", 4242)

    async def overview(**kwargs):
        global overview_reads
        overview_reads += 1
        if args.snapshot == "read-error":
            raise ConnectionError("scripted transport unavailable")
        return GameOverview(turn=13 if args.snapshot == "changed-turn" and overview_reads >= 3 else 12,
            player_id=0, civ_name="Rome", leader_name="Trajan", gold=100, gold_per_turn=5,
            science_yield=10, culture_yield=8, faith=3, current_research="TECH_POTTERY",
            current_civic="CIVIC_CODE_OF_LAWS", num_cities=0, num_units=0,
            civilization_type="CIVILIZATION_ROME", leader_type="LEADER_TRAJAN")

    async def cities():
        return [], []

    game.get_game_identity = identity
    game.get_game_overview = overview
    game.get_cities = cities
    game.check_game_over = auxiliary_query
    game._revealed_tiles = set()
    if args.snapshot == "pending":
        game._pending_save = True


class Logger:
    async def log_tool_call(self, *arguments):
        pass


async def disconnect():
    args.receipt.write_text(json.dumps({"process_id": os.getpid(), "phase": "closed",
        "submissions": len(engine.submissions), "remaining_reads": len(engine.reads),
        "observed": observed, "has_pending": game.pending_action_summary is not None}))


# Keep the production lifespan and MCP dispatcher; replace only their external
# engine boundary and logger. No test path can construct a real connection.
engine.disconnect = disconnect
server.GameConnection = lambda: engine
server.GameState = lambda connection: game
server.GameLogger = Logger
args.receipt.write_text(json.dumps({"process_id": os.getpid(), "phase": "started"}))


@server.mcp.tool()
async def test_slow():
    await game.move_unit(0, 22, 9)
    await asyncio.sleep(10)
    return "returned"


@server.mcp.tool()
async def test_change_session(ctx: Context):
    ctx.request_context.lifespan_context.session_id = uuid4().hex
    return "changed"


@server.mcp.tool(annotations={"readOnlyHint": True})
async def test_slow_read():
    await asyncio.sleep(10)
    return "read"


@server.mcp.tool(annotations={"readOnlyHint": True})
async def test_exit():
    async def die():
        await asyncio.sleep(.1)
        os._exit(7)
    asyncio.create_task(die())
    return "will exit"


@server.mcp.tool(annotations={"readOnlyHint": True})
async def test_legacy_error():
    return "Error: ERR:NO_TILES|No valid placement tiles found"


@server.mcp.tool(annotations={"readOnlyHint": True})
async def test_unknown_error():
    return "ERR:OUTCOME_UNKNOWN|Preserve original request"


server.main()

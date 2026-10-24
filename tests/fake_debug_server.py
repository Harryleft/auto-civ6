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


server.main()

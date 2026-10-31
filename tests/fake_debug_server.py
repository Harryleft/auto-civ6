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
parser.add_argument("--snapshot", choices=("ready", "pending", "changed-turn", "read-error", "popup-unknown", "decision"))
args = parser.parse_args()
game_state.ACTION_CONFIRM_TIMEOUT = .05
game_state.ACTION_CONFIRM_INTERVAL = .001
engine = Engine(writes=[["OK:MOVING_TO|22,9|from:22,11"]],
                reads=[ConnectionError("lost response"), ["POS|22|9|moves:1|busy:false"]])
engine.is_connected = False
game = GameState(engine)
game.dismiss_popup = no_dismiss
observed = []
popup_writes = 0


async def units(*, on_confirmation=None):
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
    if args.snapshot == "popup-unknown":
        # 只替换引擎边界，保留真实弹窗入口、门禁和 CLI 错误分类。
        async def lost_popup_reply(source, timeout=5):
            global popup_writes
            popup_writes += 1
            raise TimeoutError("scripted popup write reply lost")

        engine.execute_write = lost_popup_reply
        game.dismiss_popup = GameState.dismiss_popup.__get__(game, GameState)


if args.snapshot == "decision":
    from test_decision_context import response
    original_write = engine.execute_write

    async def decision_write(code, **kwargs):
        if "DECISION_BEGIN|" in code:
            observed.append("get_decision_context")
            return response()
        return await original_write(code, **kwargs)

    engine.execute_write = decision_write


class Logger:
    async def log_tool_call(self, *arguments):
        persist("running")


def persist(phase):
    path = args.receipt.with_suffix('.tmp')
    path.write_text(json.dumps({"process_id": os.getpid(), "phase": phase,
        "submissions": len(engine.submissions), "remaining_reads": len(engine.reads),
        "observed": observed, "has_pending": game.pending_action_summary is not None,
        "popup_writes": popup_writes}), encoding='utf-8')
    path.replace(args.receipt)


async def disconnect():
    persist("closed")


# Keep the production lifespan and MCP dispatcher; replace only their external
# engine boundary and logger. No test path can construct a real connection.
engine.disconnect = disconnect
server.GameConnection = lambda: engine
server.GameState = lambda connection: game
server.GameLogger = Logger
persist("started")


@server.mcp.tool()
async def test_slow():
    await game.move_unit(0, 22, 9)
    # Windows SDK shutdown terminates the process tree without running finally.
    # Persist the real submitted/observed facts before sleeping, not at shutdown.
    persist("waiting")
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

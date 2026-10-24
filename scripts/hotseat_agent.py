#!/usr/bin/env python3
"""Experimental fixed-seat MCP entrypoint for the native-hotseat live test.

Uses the existing server/GameState/action lifecycle. A shared file lock covers
each complete tool, and each invocation releases its FireTuner connection.
Only native PlayerChange/ENDTURN performs handoff; no local-player switching.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
from pathlib import Path
import time
import uuid

from mcp.server.fastmcp import Context
from civ_mcp import server

ALLOWED = {"get_game_overview", "get_units", "get_cities", "get_city_production",
           "get_tech_civics", "get_map_area", "get_settle_advisor", "get_pathing_estimate",
           "unit_action", "skip_remaining_units", "set_research", "set_city_production", "search_knowledge", "get_knowledge"}
original_logged = server._logged
seat = 0
run = Path()
session_id = uuid.uuid4().hex
serial = asyncio.Lock()


def phase_code() -> str:
    return '''
print("HS|"..tostring(GameConfiguration.IsHotseat()).."|"..tostring(Game.GetLocalPlayer()).."|"..
 tostring(Game.GetCurrentGameTurn()).."|"..tostring(GameConfiguration.IsPaused()).."|"..
 tostring(Players[0]:IsHuman()).."|"..tostring(Players[1]:IsHuman()).."|"..
 tostring(Players[Game.GetLocalPlayer()]:IsTurnActive()).."|"..
 tostring(GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")).."|"..
 PlayerConfigurations[0]:GetCivilizationTypeName().."|"..PlayerConfigurations[1]:GetCivilizationTypeName())
'''


async def phase(conn) -> list[str]:
    # The native single-client listener rearms asynchronously after disconnect.
    # Only this read-only preflight/confirmation may wait and reconnect.
    deadline = time.monotonic() + 8
    while True:
        try:
            rows = await conn.execute_write(phase_code())
            break
        except (OSError, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(.2)
    facts = [r.split("|") for r in rows if r.startswith("HS|")]
    if len(facts) != 1 or len(facts[0]) != 11:
        raise ValueError("Native hotseat identity is incomplete")
    f = facts[0]
    if (f[1] != "true" or f[5:7] != ["true", "true"] or f[8] != "-1896272000"
            or f[9:] != ["CIVILIZATION_SUMERIA", "CIVILIZATION_SWEDEN"]):
        raise ValueError("Expected original four-leaf game with two human seats")
    return f


def pending_path() -> Path:
    return run / "evidence" / "shared-pending.json"


def write_pending(record: dict) -> None:
    path = pending_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


async def guarded_logged(ctx: Context, tool_name, params, fn):
    app = ctx.request_context.lifespan_context
    if tool_name == "unit_action" and params.get("action", "").lower() not in {
        "move", "cancel_move", "fortify", "skip", "found_city"
    }:
        return "REFUSED|This hotseat experiment exposes only move/cancel/fortify/skip/found"
    read_only = tool_name.startswith(("get_", "search_"))
    async with serial:
        with (run / "evidence" / "control.lock").open("a") as lock:
            deadline = time.monotonic() + 15
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        return "WAIT|Another seat owns the control channel"
                    await asyncio.sleep(.1)
            record = {"session": session_id, "seat": seat, "tool": tool_name,
                      "params": params, "time": time.time()}
            admitted = False
            try:
                if tool_name not in {"search_knowledge", "get_knowledge"}:
                    f = await phase(app.game.conn)
                    record["before"] = f
                    if int(f[2]) != seat:
                        return f"WAIT|Seat {seat}; native current seat {f[2]}, turn {f[3]}"
                    if not read_only and tool_name != "begin_hotseat_turn" and (f[4] != "false" or f[7] != "true"):
                        return "WAIT|Native handoff is paused or seat is inactive"
                if not read_only:
                    if pending_path().exists():
                        old = json.loads(pending_path().read_text())
                        if any(old.get(k) != record[k] for k in ("session", "seat", "tool", "params")):
                            return "ERR:OUTCOME_UNKNOWN|An earlier shared request remains unresolved; no action sent"
                    else:
                        write_pending(record | {"phase": "registered"})
                    admitted = True
                result = await original_logged(ctx, tool_name, params, fn)
                if tool_name in {"begin_hotseat_turn", "end_hotseat_turn"} and not str(result).startswith(("HS_READY|", "HS_HANDOFF|", "BLOCKED|")):
                    result = "ERR:OUTCOME_UNKNOWN|" + str(result)
                record["result"] = str(result)
                pending_domain = (app.game.pending_action_summary is not None
                                  or app.game.save_pending or app.game.replacement_pending
                                  or app.game.turn_pending)
                if not read_only and not pending_domain and "OUTCOME_UNKNOWN" not in str(result):
                    pending_path().unlink(missing_ok=True)
                return result
            except Exception as exc:
                record["error"] = str(exc)
                if read_only:
                    return "ERR:READ_FAILED|" + str(exc)
                return ("ERR:OUTCOME_UNKNOWN|" if admitted else "ERR:NOT_SUBMITTED|") + str(exc)
            finally:
                await app.game.conn.disconnect()
                with (run / "evidence" / "agent-tools.jsonl").open("a") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")


async def begin_hotseat_turn(ctx: Context) -> str:
    """Confirm this bound human's native PlayerChange dialog once, then read readiness."""
    async def action():
        conn = server._get_game(ctx).conn
        pending = json.loads(pending_path().read_text())
        if pending["phase"] == "registered":
            pending["phase"] = "submitted"
            write_pending(pending)
            await conn.execute_in_named_state("PlayerChange", f'''
assert(GameConfiguration.IsHotseat() and Players[0]:IsHuman() and Players[1]:IsHuman())
assert(Game.GetLocalPlayer()=={seat})
if not ContextPtr:IsHidden() then assert(PlayerConfigurations[{seat}]:GetHotseatPassword()=="");OnOk();print("HS_BEGIN_SENT")
else print("HS_BEGIN_ALREADY_HIDDEN") end
''')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            f = await phase(conn)
            if int(f[2]) == seat and f[4] == "false" and f[7] == "true":
                return f"HS_READY|seat:{seat}|turn:{f[3]}|both_human:true"
            await asyncio.sleep(.1)
        return "ERR:OUTCOME_UNKNOWN|Begin requested once; readiness unconfirmed"
    return await guarded_logged(ctx, "begin_hotseat_turn", {}, action)


async def end_hotseat_turn(ctx: Context) -> str:
    """End only this bound seat once; confirm native handoff, not a global turn increment."""
    async def action():
        conn = server._get_game(ctx).conn
        pending = json.loads(pending_path().read_text())
        if pending["phase"] == "registered":
            rows = await conn.execute_write('print("CAN_END|"..tostring(UI.CanEndTurn()))')
            if rows != ["CAN_END|true"]:
                return "BLOCKED|Native UI cannot end this seat; resolve its pending choices"
            pending["phase"] = "submitted"
            write_pending(pending)
            await conn.execute_write(f'''
assert(GameConfiguration.IsHotseat() and Players[0]:IsHuman() and Players[1]:IsHuman())
assert(Game.GetLocalPlayer()=={seat} and Players[{seat}]:IsTurnActive() and not GameConfiguration.IsPaused())
assert(UI.CanEndTurn())
UI.RequestAction(ActionTypes.ACTION_ENDTURN)
print("HS_END_SENT")
''')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            f = await phase(conn)
            if int(f[2]) != seat and f[7] == "true":
                return f"HS_HANDOFF|from:{seat}|to:{f[2]}|turn:{f[3]}|both_human:true"
            await asyncio.sleep(.2)
        return "ERR:OUTCOME_UNKNOWN|End requested once; native handoff unconfirmed"
    return await guarded_logged(ctx, "end_hotseat_turn", {}, action)


def main():
    global seat, run
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seat", required=True, type=int, choices=[0, 1])
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()
    seat, run = args.seat, args.run.resolve()
    if not (run / "evidence" / "patch-manifest.json").exists():
        parser.error("Use a run with its verified experimental patch manifest")
    server._logged = guarded_logged
    server.mcp.tool()(begin_hotseat_turn)
    server.mcp.tool()(end_hotseat_turn)
    allowed = ALLOWED | {"begin_hotseat_turn", "end_hotseat_turn"}
    for tool in server.mcp._tool_manager.list_tools():
        if tool.name not in allowed:
            server.mcp._tool_manager.remove_tool(tool.name)
    server.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

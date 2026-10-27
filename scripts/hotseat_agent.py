#!/usr/bin/env python3
"""Experimental fixed-seat MCP entrypoint for the native-hotseat live test.

Uses the existing server/GameState/action lifecycle. A shared file lock covers
each complete tool, and each invocation releases its FireTuner connection.
Only native PlayerChange/ENDTURN performs handoff; no local-player switching.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
import uuid

from mcp.server.fastmcp import Context
from civ_mcp import server
from civ_mcp.file_lock import acquire
from civ_mcp.end_turn import _check_blockers

EXCLUDED = {"run_lua", "end_turn", "save_game", "list_saves", "load_save",
            "load_game_save", "reload_game_save", "kill_game", "launch_game",
            "load_save_from_menu", "restart_and_load"}
ALLOWED = {tool.name for tool in server.mcp._tool_manager.list_tools() if tool.name not in EXCLUDED}
original_logged = server._logged
seat = 0
run = Path()
session_id = uuid.uuid4().hex
serial = asyncio.Lock()


async def wait_for_congress_handoff(game, *, timeout=8.0, interval=0.1):
    """Bounded original-token confirmation while native handoff settles."""
    deadline = time.monotonic() + timeout
    while True:
        result = await game.confirm_hotseat_congress_handoff()
        if result.startswith("HS_HANDOFF|") or not game.congress_pending:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return result
        await asyncio.sleep(min(interval, remaining))


async def match_status(conn) -> dict:
    """Read official global victory separately from a local player's defeat.

    GameCore survives end screens. Do not infer global victory from scores,
    the configured turn limit, or one human's elimination.
    """
    code = '''
local ok,team,selector=pcall(Game.GetWinningTeam)
-- Stock WorldInput.IsGameOver uses GetWinningTeam() ~= nil. The native
-- active-game result is nil, not an absent/failed query or a winning team.
assert(ok and (team==nil or (type(team)=="number" and team==math.floor(team))))
if team==nil then team=-1 end
local victory="Unknown"
if team>=0 and selector~=nil then
 local row=GameInfo.Victories[selector]
 if row then victory=row.VictoryType end
end
-- IsHotseat is UI-only; active operation guards independently verify it via
-- phase(). Core remains readable when an end screen removes that UI context.
print("MATCH|"..Game.GetCurrentGameTurn().."|"..Game.GetLocalPlayer().."|"..
 GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED").."|"..
 PlayerConfigurations[0]:GetCivilizationTypeName().."|"..PlayerConfigurations[1]:GetCivilizationTypeName().."|"..
 tostring(Players[0]:IsHuman()).."|"..tostring(Players[1]:IsHuman()).."|"..
 tostring(Players[0]:IsAlive()).."|"..tostring(Players[1]:IsAlive()).."|"..team.."|"..victory)
if team>=0 then
 for id=0,62 do
  local p=Players[id]
  if p and p:IsMajor() and p:GetTeam()==team then
   print("WINNER|"..id.."|"..PlayerConfigurations[id]:GetCivilizationTypeName())
  end
 end
end
'''
    deadline = time.monotonic() + 8
    while True:
        try:
            rows = await conn.execute_in_named_state("GameCore_Tuner", code)
            break
        except (OSError, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(.2)
    facts = [row.split("|") for row in rows if row.startswith("MATCH|")]
    if len(facts) != 1 or len(facts[0]) != 12:
        raise ValueError("Native match outcome is incomplete")
    f = facts[0]
    if (f[3:8] != ["-1896272000", "CIVILIZATION_SUMERIA", "CIVILIZATION_SWEDEN", "true", "true"]
            or any(value not in {"true", "false"} for value in f[8:10])):
        raise ValueError("Unexpected hotseat world or human seats")
    winners = [{"id": int(v[1]), "civilization": v[2]} for row in rows
               if row.startswith("WINNER|") for v in [row.split("|")]]
    return {"turn": int(f[1]), "local": int(f[2]), "alive": [v == "true" for v in f[8:10]],
            "winner_team": int(f[10]), "victory_type": f[11], "terminal": int(f[10]) >= 0,
            "winners": winners}


def terminal_receipt(status: dict) -> str:
    return (f"HS_GAME_OVER|turn:{status['turn']}|winner_team:{status['winner_team']}|"
            f"victory:{status['victory_type']}|winners:" + json.dumps(status['winners'], ensure_ascii=False))


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
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def pending_domains(game) -> list[str]:
    """Consume public owner facts; the broker does not retire domain requests."""
    domains = ["action"] if game.pending_action_summary is not None else []
    for name in ("save", "replacement", "turn", "diplomacy", "congress"):
        if getattr(game, name + "_pending", False) is True:
            domains.append(name)
    return domains


def permits_followup(old: dict, tool: str, game) -> bool:
    domains = pending_domains(game)
    if tool == "dismiss_popup" and domains:
        return True
    if old.get("outcome_unknown", True):
        return False
    if "diplomacy" in domains and old["tool"] in {
        "propose_trade", "propose_peace", "test_trade", "negotiate_incoming_trade",
        "respond_to_trade", "respond_to_diplomacy", "send_diplomatic_action",
    }:
        return tool in {"respond_to_diplomacy", "respond_to_trade", "negotiate_incoming_trade"}
    return "congress" in domains and old["tool"] == "queue_wc_votes" and tool == "open_world_congress"


async def guarded_logged(ctx: Context, tool_name, params, fn):
    app = ctx.request_context.lifespan_context
    if tool_name == "unit_action" and params.get("action", "").lower() in {"teleport", "automate"}:
        return "REFUSED|The long test keeps unit decisions with the model; no teleport/automation"
    read_only = tool_name.startswith(("get_", "search_"))
    async with serial:
        with (run / "evidence" / "control.lock").open("a+b") as lock:
            deadline = time.monotonic() + 15
            while True:
                try:
                    acquire(lock)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        return "WAIT|Another seat owns the control channel"
                    await asyncio.sleep(.1)
            record = {"session": session_id, "seat": seat, "tool": tool_name,
                      "params": params, "time": time.time()}
            timeline = run / "evidence" / "current-timeline.json"
            record["timeline"] = json.loads(timeline.read_text(encoding="utf-8"))["timeline"] if timeline.exists() else 0
            admitted = False
            try:
                if not read_only:
                    outcome = await match_status(app.game.conn)
                    if outcome["terminal"]:
                        record["result"] = "GAME_OVER|No new action submitted|" + terminal_receipt(outcome)
                        return record["result"]
                if tool_name not in {"search_knowledge", "get_knowledge"}:
                    f = await phase(app.game.conn)
                    record["before"] = f
                    if int(f[2]) != seat:
                        return f"WAIT|Seat {seat}; native current seat {f[2]}, turn {f[3]}"
                    if not read_only and tool_name != "begin_hotseat_turn" and (f[4] != "false" or f[7] != "true"):
                        return "WAIT|Native handoff is paused or seat is inactive"
                if not read_only:
                    if pending_path().exists():
                        old = json.loads(pending_path().read_text(encoding="utf-8"))
                        if any(old.get(k) != record[k] for k in ("session", "seat")):
                            return "ERR:OUTCOME_UNKNOWN|An earlier shared request remains unresolved; no action sent"
                        if any(old.get(k) != record[k] for k in ("tool", "params")):
                            if permits_followup(old, tool_name, app.game):
                                # A response/cleanup is an explicit child intent.
                                # Preserve the parent until its domain confirms it;
                                # an unknown child can only resume its own intent.
                                write_pending(record | {"phase": "registered", "parent": old})
                            else:
                                prefix = "ERR:OUTCOME_UNKNOWN|" if old.get("outcome_unknown", True) else "BLOCKED|"
                                return (prefix + "Original request still owns the native flow. This new action was not submitted. "
                                        + "Confirm " + old["tool"] + " with the exact original parameters: "
                                        + json.dumps(old["params"], ensure_ascii=False))
                    else:
                        write_pending(record | {"phase": "registered"})
                    admitted = True
                result = await original_logged(ctx, tool_name, params, fn)
                if tool_name == "queue_wc_votes" and app.game.congress_pending:
                    # A vote continuation can natively transfer the hotseat in
                    # this very call. Confirm its exact request before dispatch.
                    handoff = await wait_for_congress_handoff(app.game)
                    if handoff.startswith("HS_HANDOFF|"):
                        result = handoff
                if tool_name in {"begin_hotseat_turn", "end_hotseat_turn"} and not str(result).startswith(("HS_READY|", "HS_HANDOFF|", "HS_GAME_OVER|", "BLOCKED|")):
                    result = "ERR:OUTCOME_UNKNOWN|" + str(result)
                record["result"] = str(result)
                domains = pending_domains(app.game)
                if not read_only:
                    unknown = "OUTCOME_UNKNOWN" in str(result)
                    if not domains and not unknown:
                        pending_path().unlink(missing_ok=True)
                    else:
                        active = json.loads(pending_path().read_text(encoding="utf-8"))
                        if not unknown and "parent" in active:
                            active = active["parent"] | {"last_child_receipt": str(result)}
                        else:
                            active["outcome_unknown"] = unknown
                        write_pending(active | {"domains": domains})
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
    """Confirm native PlayerChange once; return readiness plus fresh units/cities/research."""
    async def action():
        conn = server._get_game(ctx).conn
        pending = json.loads(pending_path().read_text(encoding="utf-8"))
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
                return (f"HS_READY|seat:{seat}|turn:{f[3]}|both_human:true\n"
                        "For efficiency, use hotseat_actions for explicit independent queries/actions "
                        "with already-known IDs and prerequisites. Inspect receipts before dependent decisions.")
            await asyncio.sleep(.1)
        return "ERR:OUTCOME_UNKNOWN|Begin requested once; readiness unconfirmed"
    result = await guarded_logged(ctx, "begin_hotseat_turn", {}, action)
    if result.startswith("HS_READY|"):
        # These are ordinary guarded reads after the begin intent has completed.
        # A read failure must not turn an already-confirmed handoff into unknown.
        for name in ("get_units", "get_cities", "get_tech_civics"):
            try:
                facts = await server.mcp._tool_manager.call_tool(name, {}, context=ctx)
                result += f"\n=== Fresh {name} ===\n{facts}"
            except Exception as exc:
                result += f"\n{name}: read failed: {exc}"
        result += "\nThese facts are current; repeat a query only after a relevant change or missing detail."
    return result


async def end_hotseat_turn(ctx: Context) -> str:
    """End only this bound seat once; confirm native handoff, not a global turn increment."""
    async def action():
        conn = server._get_game(ctx).conn
        pending = json.loads(pending_path().read_text(encoding="utf-8"))
        if pending["phase"] == "registered":
            f = await phase(conn)
            blockers = await _check_blockers(server._get_game(ctx), int(f[3]))
            if blockers:
                return "BLOCKED|" + blockers
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
            outcome = await match_status(conn)
            if outcome["terminal"]:
                return terminal_receipt(outcome)
            f = await phase(conn)
            if f[7] == "true" and (int(f[2]) != seat or (
                    int(f[3]) > int(pending["before"][3]) and not outcome["alive"][1-seat])):
                return f"HS_HANDOFF|from:{seat}|to:{f[2]}|turn:{f[3]}|both_human:true"
            await asyncio.sleep(.2)
        return "ERR:OUTCOME_UNKNOWN|End requested once; native handoff unconfirmed"
    return await guarded_logged(ctx, "end_hotseat_turn", {}, action)


async def hotseat_actions(actions_json: str, ctx: Context) -> str:
    """Execute an explicit JSON list of independent model-selected tool calls.

    Format: [{"tool":"unit_action","arguments":{...}}, ...], at most 20.
    Each call uses its normal seat/lifecycle guards and returns its own receipt.
    Stops at an error or unknown outcome. No begin/end, loads, Lua, or recursion.
    Only group actions whose IDs/coordinates and prerequisites are already known;
    inspect results before planning dependent actions.
    """
    commands = json.loads(actions_json)
    if not isinstance(commands, list) or not 1 <= len(commands) <= 20:
        return "REFUSED|Expected 1..20 explicit tool calls"
    if any(not isinstance(c, dict) or c.get("tool") not in ALLOWED
           or not isinstance(c.get("arguments", {}), dict) for c in commands):
        return "REFUSED|Batch contains an unavailable tool or invalid arguments"
    results = []
    for command in commands:
        name = command["tool"]
        try:
            result = await server.mcp._tool_manager.call_tool(name, command.get("arguments", {}), context=ctx)
        except Exception as exc:
            results.append({"tool": name, "error": str(exc)})
            break
        results.append({"tool": name, "result": result})
        if any(tag in str(result) for tag in ("ERR:", "WAIT|", "BLOCKED|", "REFUSED|", "Error:", "FAILED|", "INVALID|")):
            break
    return json.dumps(results, ensure_ascii=False)


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
    server.mcp.tool()(hotseat_actions)
    allowed = ALLOWED | {"begin_hotseat_turn", "end_hotseat_turn", "hotseat_actions"}
    for tool in server.mcp._tool_manager.list_tools():
        if tool.name not in allowed:
            server.mcp._tool_manager.remove_tool(tool.name)
    server.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

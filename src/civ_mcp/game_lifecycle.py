"""Game lifecycle — popup dismissal, save/load, raw Lua execution."""

from __future__ import annotations

import asyncio
import json
import uuid
import logging
from pathlib import Path

from civ_mcp import lua as lq
from civ_mcp.connection import GameConnection, LuaError
from civ_mcp.lua._helpers import SENTINEL

log = logging.getLogger(__name__)
SAVE_CONFIRM_TIMEOUT = 10.0
SAVE_POLL_INTERVAL = 0.2


async def dismiss_popup(conn: GameConnection) -> str:
    """Dismiss any blocking popup or UI overlay in the game.

    Three-phase approach:
    1. Single batched InGame call that acknowledges Congress results and
       closes informational overlays. Strategy choices remain pending.
    2. Only if Phase 1 found nothing: scan individual Lua states for
       ExclusivePopupManager popups (disaster, wonder, era screens) that
       need Close() in their own state to release the engine event lock.
    3. Safety net: always fire ExclusivePopupManager Close LuaEvents to
       ensure BulkHide counters are decremented even if Phase 1 caught
       the popup by name (SetHide) without proper cleanup.
    """
    dismissed = []

    # Phase 1: Single batched InGame call — handles most cases in one roundtrip.
    # Covers: diplomacy screens, generic popups, world congress, boosts, etc.
    # NOTE: ExclusivePopupManager popups (NaturalDisaster, NaturalWonder,
    # WonderBuilt, EraComplete, RockBand, ProjectBuilt) are handled ONLY in
    # Phase 2 via Close() in their own Lua state.  Phase 1's SetHide() breaks
    # Phase 2's IsHidden check without releasing the PopupManager lock.
    popup_names = [
        "InGamePopup",
        "GenericPopup",
        "PopupDialog",
        "BoostUnlockedPopup",
        "GreatWorkShowcase",
    ]
    # Results acknowledgment replaces the old automatic end-turn unlock.
    # Match notifications before requesting the operation, and report only
    # entries confirmed gone/dismissed afterward. Sessions still need votes.
    checks = ["""
do
    local me = Game.GetLocalPlayer()
    local reviewType = EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK
    local review = {}
    local sessionPending = false
    local list = NotificationManager.GetList(me)
    for _, nid in ipairs(list or {}) do
        local entry = NotificationManager.Find(me, nid)
        if entry and not entry:IsDismissed() then
            local blocking = entry:GetEndTurnBlocking()
            if reviewType and blocking == reviewType then
                table.insert(review, nid)
            elseif blocking == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION
                or blocking == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION then
                sessionPending = true
            end
        end
    end
    if #review > 0 then
        local requested = UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_LOOKED_AT_AVAILABLE, {})
        if requested ~= false then
            local dismissed = 0
            for _, nid in ipairs(review) do
                local entry = NotificationManager.Find(me, nid)
                if entry and not entry:IsDismissed() then NotificationManager.Dismiss(me, nid) end
                entry = NotificationManager.Find(me, nid)
                if not entry or entry:IsDismissed() then dismissed = dismissed + 1 end
            end
            if dismissed > 0 then
                print("DISMISSED|WorldCongressResults")
                if not sessionPending then
                    for _, name in ipairs({"WorldCongressIntro", "WorldCongressPopup"}) do
                        local popup = ContextPtr:LookUpControl("/InGame/" .. name)
                        if popup then popup:SetHide(true) end
                    end
                end
            end
        end
    end
end
"""]
    for name in popup_names:
        checks.append(
            f'do local c = ContextPtr:LookUpControl("/InGame/{name}") '
            f"if c and not c:IsHidden() then "
            f"  pcall(function() UIManager:DequeuePopup(c) end) "
            f"  pcall(function() Input.PopContext() end) "
            f"  c:SetHide(true) "
            f'  print("DISMISSED|{name}") '
            f"end end"
        )
    # LeaderScene 3D model: SetHide does NOT clear the C++ 3D viewport.
    # Must fire Events.HideLeaderScreen() to unload the 3D leader model.
    checks.append(
        'do local ls = ContextPtr:LookUpControl("/InGame/LeaderScene") '
        "if ls and not ls:IsHidden() then "
        "  pcall(function() Events.HideLeaderScreen() end) "
        "  ls:SetHide(true) "
        '  print("DISMISSED|LeaderScene") '
        "end end"
    )
    # Diplomacy screens: report only, do NOT close sessions.
    # Force-closing sessions via DiplomacyManager.CloseSession() bypasses
    # the C++ engine's session lifecycle callbacks, leaving the AI diplomacy
    # subsystem in an inconsistent state that causes turn processing hangs
    # (confirmed across Games 1-5).  Use respond_to_diplomacy() instead.
    checks.append(
        'do local dv = ContextPtr:LookUpControl("/InGame/DiplomacyActionView") '
        "if dv and not dv:IsHidden() then "
        '  print("PENDING|DiplomacyActionView") '
        "end end"
    )
    # NOTE: DiplomacyDealView is NOT dismissed here — it represents an
    # incoming trade deal offer that the agent must accept/reject via
    # get_pending_trades + respond_to_trade.  Dismissing it silently kills
    # the offer (e.g. incoming delegations from other civs).
    checks.append(
        'do local ddv = ContextPtr:LookUpControl("/InGame/DiplomacyDealView") '
        "if ddv and not ddv:IsHidden() then "
        '  print("PENDING|DiplomacyDealView") '
        "end end"
    )
    # Camera reset for cinematic mode
    checks.append(
        "local mode = UI.GetInterfaceMode() "
        "if mode == InterfaceModeTypes.CINEMATIC then "
        '  pcall(function() UI.ClearTemporaryPlotVisibility("NaturalDisaster") end) '
        '  pcall(function() UI.ClearTemporaryPlotVisibility("NaturalWonder") end) '
        "  pcall(function() Events.StopAllCameraAnimations() end) "
        "  pcall(function() UILens.RestoreActiveLens() end) "
        "  UI.SetInterfaceMode(InterfaceModeTypes.SELECTION) "
        '  print("DISMISSED|cinematic_camera") '
        "end"
    )
    pending_deal = False
    pending_diplomacy = False
    try:
        lua = " ".join(checks) + f' print("{lq.SENTINEL}")'
        lines = await conn.execute_write(lua)
        for line in lines:
            if line.startswith("DISMISSED|"):
                dismissed.append(line.split("|", 1)[1])
            elif line.startswith("PENDING|"):
                if "DiplomacyDealView" in line:
                    pending_deal = True
                elif "DiplomacyActionView" in line:
                    pending_diplomacy = True
    except ConnectionError:
        raise  # dead transport is not "no popups" — surface it
    except Exception as e:
        log.debug("Phase 1 dismiss failed: %s", e)

    # Pre-check: single InGame call to detect visible ExclusivePopupManager
    # popups.  Phase 2 scans ~30 Lua states individually (~450ms each = ~13.5s)
    # to find these.  This pre-check costs one round-trip (~500ms) and skips
    # Phase 2+3 entirely when no ExclusivePopups are active (>99% of calls).
    exclusive_popup_names = [
        "TechCivicCompletedPopup",
        "NaturalWonderPopup",
        "NaturalDisasterPopup",
        "WonderBuiltPopup",
        "EraCompletePopup",
        "HistoricMoments",
        "MomentPopup",
        "ProjectBuiltPopup",
        "RockBandPopup",
        "RockBandMoviePopup",
    ]
    any_exclusive_visible = False
    try:
        precheck_lua = (
            " ".join(
                f'do local c = ContextPtr:LookUpControl("/InGame/{n}") '
                f'if c and not c:IsHidden() then print("EXCL_VISIBLE") end end'
                for n in exclusive_popup_names
            )
            + f' print("{lq.SENTINEL}")'
        )
        precheck_lines = await conn.execute_write(precheck_lua)
        any_exclusive_visible = any("EXCL_VISIBLE" in l for l in precheck_lines)
    except Exception as e:
        log.debug("ExclusivePopup pre-check failed (will run Phase 2): %s", e)
        any_exclusive_visible = True  # fail-open: scan if pre-check errors

    if any_exclusive_visible:
        log.info("ExclusivePopup visible — running Phase 2 state scan")

        # Phase 2: Close ExclusivePopupManager popups in their own Lua states.
        # These need Close() in their OWN state to release the engine lock —
        # Phase 1's SetHide() does NOT release this lock.
        popup_keywords = ("Popup", "Wonder", "Moment", "Era", "Disaster")
        popup_states = {
            idx: n
            for idx, n in conn.lua_states.items()
            if any(kw in n for kw in popup_keywords)
        }
        log.debug("Phase 2 popup states: %s", popup_states)
        for state_idx, name in popup_states.items():
            # Loop to drain the ExclusivePopupManager's engine queue —
            # each Close() pops the next event, so we keep closing until
            # the popup stays hidden (max 20 to avoid infinite loops).
            for _drain in range(20):
                try:
                    lines = await conn.execute_in_state(
                        state_idx,
                        "pcall(function() if m_kQueuedPopups then m_kQueuedPopups = {} end end); "
                        "if not ContextPtr:IsHidden() then "
                        "  local ok = pcall(Close); "
                        "  if not ok then pcall(OnClose) end; "
                        '  print("DISMISSED") '
                        "end; "
                        'print("---END---")',
                    )
                    if any("DISMISSED" in l for l in lines):
                        if name not in dismissed:
                            dismissed.append(name)
                    else:
                        break  # popup stayed hidden, queue drained
                except Exception as e:
                    log.debug(
                        "Popup check failed for %s (state %d): %s",
                        name,
                        state_idx,
                        e,
                    )
                    break

        # Phase 3: Fallback — if InGame still sees visible ExclusivePopups,
        # probe state indexes to find and close them.  Handles cases where
        # lua_states from the handshake is incomplete (truncated LSQ).
        try:
            check_lua = (
                " ".join(
                    f'do local c = ContextPtr:LookUpControl("/InGame/{n}") '
                    f'if c and not c:IsHidden() then print("STILL_VISIBLE|{n}") end end'
                    for n in exclusive_popup_names
                )
                + f' print("{lq.SENTINEL}")'
            )
            still_visible = await conn.execute_write(check_lua)
            remaining = [
                l.split("|", 1)[1]
                for l in still_visible
                if l.startswith("STILL_VISIBLE|")
            ]
            if remaining:
                log.info(
                    "Phase 3: ExclusivePopups still visible after Phase 2: %s "
                    "(probing state indexes...)",
                    remaining,
                )
                for probe_idx in range(50, 200):
                    if probe_idx in popup_states:
                        continue
                    if not remaining:
                        break
                    try:
                        probe_lines = await conn.execute_in_state(
                            probe_idx,
                            'print(ContextPtr:GetID()); print("---END---")',
                            timeout=1.0,
                        )
                        state_name = probe_lines[0] if probe_lines else ""
                        if state_name not in remaining:
                            continue
                        close_lines = await conn.execute_in_state(
                            probe_idx,
                            "pcall(function() if m_kQueuedPopups then m_kQueuedPopups = {} end end); "
                            "local ok = pcall(Close); "
                            "if not ok then pcall(OnClose) end; "
                            "ContextPtr:SetHide(true); "
                            'print("DISMISSED"); '
                            'print("---END---")',
                            timeout=2.0,
                        )
                        if any("DISMISSED" in l for l in close_lines):
                            dismissed.append(f"{state_name} (probed state {probe_idx})")
                            remaining.remove(state_name)
                            conn.lua_states[probe_idx] = state_name
                            log.info(
                                "Phase 3: Dismissed %s at state %d",
                                state_name,
                                probe_idx,
                            )
                    except Exception:
                        pass
        except Exception as e:
            log.debug("Phase 3 probe failed: %s", e)

    # Final phase: dismiss Windows-level crash dialogs (Firaxis Crash
    # Reporter, Unhandled Exception).  These are Win32 dialogs that appear
    # on top of the game after EXCEPTION_ACCESS_VIOLATION crashes — the
    # game keeps running but Lua calls return degraded data until dismissed.
    from . import game_launcher

    crash_dismissed = await game_launcher.dismiss_crash_dialogs()
    dismissed.extend(crash_dismissed)

    if dismissed:
        msg = f"Dismissed: {', '.join(dismissed)}"
        if pending_diplomacy:
            msg += ". Also: diplomacy session active — use respond_to_diplomacy."
        if pending_deal:
            msg += " (incoming trade deal pending — use get_pending_trades)"
        return msg
    if pending_diplomacy:
        return "Diplomacy session active — use respond_to_diplomacy to handle it."
    if pending_deal:
        return "No popups to dismiss (incoming trade deal pending — use get_pending_trades)."
    return "No popups to dismiss."


# ------------------------------------------------------------------
# Save / Load
# ------------------------------------------------------------------


def _lua_escape(value: str) -> str:
    """Escape a string for interpolation into a double-quoted Lua string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


# Names from the most recently displayed listing, from either filesystem or
# native enumeration. An index always refers to that exact displayed snapshot.
_filesystem_save_names: list[str] = []


async def save_game(conn: GameConnection, name: str) -> str:
    """Submit once; require native SaveComplete and the exact fresh file.

    Network.SaveGame only acknowledges submission. Never replay a lost
    request: its file may still finish writing after FireTuner disconnects.
    """
    from civ_mcp.game_launcher import SINGLE_SAVE_DIR
    from civ_mcp import checkpoints

    error = _validate_save_name(name)
    if error:
        return error
    name = name.removesuffix(".Civ6Save")
    if name.rstrip(" .").casefold() == "benchmark_turn_1":
        return "Error: The original benchmark_turn_1 baseline cannot be overwritten."
    checkpoints.forget_completion(name)
    path = Path(SINGLE_SAVE_DIR) / f"{name}.Civ6Save"

    def fingerprint():
        try:
            stat = path.stat()
            return (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        except FileNotFoundError:
            return None

    before = fingerprint()
    # The installed native automation tests explicitly wait for SaveComplete.
    # It has no filename argument, so arm before our single request, bind the
    # listener to this token, and also require this exact file to become fresh.
    # No end-turn/save request may overlap this serialized active-turn operation.
    token = uuid.uuid4().hex
    code = f"""
local prior = __civmcpSaveCompletion;
if prior and not prior.done then print("MCP_SAVE_PENDING"); print("{lq.SENTINEL}"); return; end;
if prior and prior.handler then pcall(function() Events.SaveComplete.Remove(prior.handler); end); end;
local q = {{token="{token}", done=false}};
__civmcpSaveCompletion = q;
q.handler = function()
  if __civmcpSaveCompletion == q then q.done = true; end;
  Events.SaveComplete.Remove(q.handler);
end;
local armed = pcall(function() Events.SaveComplete.Add(q.handler); end);
if not armed then q.done = true; print("MCP_SAVE_UNAVAILABLE"); print("{lq.SENTINEL}"); return; end;
local gf = {{}};
gf.Name = "{_lua_escape(name)}";
gf.Location = SaveLocations.LOCAL_STORAGE;
gf.Type = SaveTypes.SINGLE_PLAYER;
gf.IsAutosave = false;
gf.IsQuicksave = false;
local ok, result = pcall(function() return Network.SaveGame(gf); end);
if not ok or result == false then q.done = true; end;
print("MCP_SAVE|" .. tostring(ok and result ~= false));
if not ok then print("MCP_SAVE_ERROR|" .. tostring(result)); end;
print("{lq.SENTINEL}");
"""
    status = f"""
-- MCP_SAVE_COMPLETION_STATUS
local q = __civmcpSaveCompletion;
print("MCP_SAVE_COMPLETE|" .. tostring(q and q.token == "{token}" and q.done == true));
print("{lq.SENTINEL}");
"""
    deadline = asyncio.get_running_loop().time() + SAVE_CONFIRM_TIMEOUT
    # Reserve a little of the same total budget for listener cleanup. A dead
    # transport cannot guarantee cleanup; the unresolved latch still refuses
    # another save until explicit recovery instead of accepting a late event.
    cleanup_budget = min(0.5, SAVE_CONFIRM_TIMEOUT / 4)
    try:
        async with asyncio.timeout(SAVE_CONFIRM_TIMEOUT - cleanup_budget):
            try:
                lines = await conn.execute_write(code)
            except (OSError, TimeoutError, LuaError):
                lines = []
            if "MCP_SAVE_PENDING" in lines:
                return "ERR:OUTCOME_UNKNOWN|Earlier native save completion is unresolved; no new save submitted. Use explicit recovery."
            if "MCP_SAVE_UNAVAILABLE" in lines:
                return "Error: Native save completion event unavailable; no save submitted."
            if "MCP_SAVE|false" in lines:
                return f"Error: Civ VI rejected save {name!r}: {lines!r}"
            while True:
                current = fingerprint()
                if current and current != before and current[1] > 0:
                    try:
                        observed = await conn.execute_write(status)
                    except (OSError, TimeoutError, LuaError):
                        observed = []
                    if "MCP_SAVE_COMPLETE|true" in observed and fingerprint() == current:
                        checkpoints.mark_complete(name)
                        return f"Saved: {name}; native completion and file confirmed ({current[1]} bytes)."
                await asyncio.sleep(SAVE_POLL_INTERVAL)
    except (TimeoutError, OSError):
        return (
            f"ERR:OUTCOME_UNKNOWN|Save {name!r} was attempted once; a fresh "
            "nonempty file and native completion were not both confirmed. "
            "Inspect the save; do not use it for recovery or resend blindly."
        )
    finally:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining > 0:
            cleanup = f"""
-- MCP_SAVE_COMPLETION_CLEANUP
local q = __civmcpSaveCompletion;
if q and q.token == "{token}" and q.handler then
  pcall(function() Events.SaveComplete.Remove(q.handler); end);
  q.handler = nil;
end;
print("{lq.SENTINEL}");
"""
            try:
                async with asyncio.timeout(remaining):
                    await conn.execute_write(cleanup, timeout=remaining)
            except (OSError, TimeoutError, LuaError):
                log.warning("Save completion listener cleanup could not be confirmed")




async def list_saves(conn: GameConnection) -> str:
    """List available saves (normal + autosave).

    Uses filesystem scan (reliable — finds all save types including
    autosaves and quicksaves). Falls back to Lua query if filesystem
    scan finds nothing.
    """
    _filesystem_save_names.clear()
    result = _list_saves_filesystem()
    if "No saves found" not in result:
        return result

    # Fallback: Lua-based query (may miss autosaves/quicksaves)
    lua_result = await _list_saves_lua(conn)
    if lua_result is not None:
        return lua_result
    return result


async def _list_saves_lua(conn: GameConnection) -> str | None:
    """Try Lua-based save enumeration. Returns None on failure."""
    try:
        await conn.execute_write(
            f"if not ExposedMembers then ExposedMembers = {{}} end; "
            f"ExposedMembers.MCPSaveList = nil; "
            f"ExposedMembers.MCPSaveQueryDone = false; "
            f"local function OnResults(fileList, qid) "
            f"  ExposedMembers.MCPSaveList = fileList; "
            f"  ExposedMembers.MCPSaveQueryDone = true; "
            f"  UI.CloseFileListQuery(qid); "
            f"  LuaEvents.FileListQueryResults.Remove(OnResults); "
            f"end; "
            f"LuaEvents.FileListQueryResults.Add(OnResults); "
            f"local opts = SaveLocationOptions.NORMAL + SaveLocationOptions.AUTOSAVE + SaveLocationOptions.QUICKSAVE + SaveLocationOptions.LOAD_METADATA; "
            f"UI.QuerySaveGameList(SaveLocations.LOCAL_STORAGE, SaveTypes.SINGLE_PLAYER, opts); "
            f'print("QUERY_SENT"); '
            f'print("{lq.SENTINEL}")'
        )

        import asyncio

        for _ in range(20):
            await asyncio.sleep(0.25)
            check_lines = await conn.execute_write(
                f"if ExposedMembers.MCPSaveQueryDone then "
                f"  local fl = ExposedMembers.MCPSaveList; "
                f"  if fl and #fl > 0 then "
                f'    print("COUNT|" .. #fl); '
                f"    for i, s in ipairs(fl) do "
                f'      if i <= 20 then print("SAVE|" .. i .. "|" .. tostring(s.Name)) end '
                f"    end "
                f'  else print("EMPTY") end '
                f'else print("PENDING") end; '
                f'print("{lq.SENTINEL}")'
            )
            if any(l.startswith("COUNT|") or l == "EMPTY" for l in check_lines):
                results = [l for l in check_lines if l.startswith("SAVE|")]
                if not results:
                    return None  # empty — fall through to filesystem
                lines_out = ["Available saves (use load_save with the index number):"]
                names = []
                for index, r in enumerate(results, 1):
                    parts = r.split("|", 2)
                    if len(parts) != 3 or int(parts[1]) != index or _validate_save_name(parts[2]):
                        return None
                    name = parts[2].removesuffix(".Civ6Save")
                    names.append(name)
                    lines_out.append(f"  {index}. {name}")
                _filesystem_save_names[:] = names
                return "\n".join(lines_out)
    except ConnectionError:
        raise  # dead transport is not "no saves" — surface it
    except Exception:
        pass
    return None  # timed out or error — fall through to filesystem


def _list_saves_filesystem() -> str:
    """Scan the save directory on disk (always works)."""
    import glob
    import os

    from .game_launcher import SAVE_DIR

    save_base = os.path.dirname(SAVE_DIR)  # .../Saves/Single
    all_saves: list[tuple[float, str]] = []

    # Autosaves
    for f in glob.glob(os.path.join(SAVE_DIR, "*.Civ6Save")):
        all_saves.append((os.path.getmtime(f), os.path.basename(f)))

    # Normal saves (parent directory)
    for f in glob.glob(os.path.join(save_base, "*.Civ6Save")):
        all_saves.append((os.path.getmtime(f), os.path.basename(f)))

    all_saves.sort(reverse=True)  # newest first
    _filesystem_save_names.clear()
    if not all_saves:
        return "No saves found on filesystem."

    lines = ["Available saves (filesystem scan, sorted by date):"]
    for i, (_mtime, name) in enumerate(all_saves[:25], 1):
        stripped = name.replace(".Civ6Save", "")
        _filesystem_save_names.append(stripped)
        lines.append(f"  {i}. {stripped}")
    return "\n".join(lines)


async def load_save(conn: GameConnection, save_index: int) -> str:
    """Load a save by index from the most recent list_saves() query.

    The game will reload — the FireTuner connection stays alive but
    all Lua state is wiped. Wait a few seconds after calling this.
    """
    lines = await conn.execute_write(
        f"if not ExposedMembers or not ExposedMembers.MCPSaveList then "
        f'  print("ERR:NO_SAVE_LIST"); print("{lq.SENTINEL}"); return '
        f"end; "
        f"local fl = ExposedMembers.MCPSaveList; "
        f"local idx = {save_index}; "
        f"if idx < 1 or idx > #fl then "
        f'  print("ERR:INDEX_OUT_OF_RANGE|" .. #fl); print("{lq.SENTINEL}"); return '
        f"end; "
        f"local save = fl[idx]; "
        f'print("LOADING|" .. tostring(save.Name)); '
        f'print("{lq.SENTINEL}"); '
        f"Network.LeaveGame(); "
        f"Network.LoadGame(save, ServerType.SERVER_TYPE_NONE)"
    )
    for line in lines:
        if line.startswith("ERR:NO_SAVE_LIST"):
            if _filesystem_save_names:
                if not 1 <= save_index <= len(_filesystem_save_names):
                    count = len(_filesystem_save_names)
                    return (
                        f"Error: Index {save_index} out of range (1-{count}). "
                        "Call list_saves() to see available saves."
                    )
                name = _filesystem_save_names[save_index - 1]
                log.info(
                    "No Lua save list — resolving filesystem entry %d ('%s')",
                    save_index,
                    name,
                )
                return await load_game_save(conn, name)
            return "Error: No save list cached. Call list_saves() first."
        if line.startswith("ERR:INDEX_OUT_OF_RANGE"):
            count = line.split("|")[1] if "|" in line else "?"
            return f"Error: Index {save_index} out of range (1-{count}). Call list_saves() to see available saves."
        if line.startswith("LOADING|"):
            name = line.split("|", 1)[1]
            return f"Loading save: {name}. Game will reload — wait ~10 seconds then call get_game_overview to verify."
    return "Load command sent. Wait for game to reload."


def _validate_save_name(save_name: str) -> str | None:
    """Reject path-like or control-character input before sending it to Lua."""

    if not isinstance(save_name, str) or not save_name.strip():
        return "Error: save name must be a non-empty filename."
    if save_name.removesuffix(".Civ6Save") in ("", ".", ".."):
        return "Error: save name must include a filename stem."
    if len(save_name) > 255 or any(char in "/\\" for char in save_name):
        return "Error: save name contains an unsupported path or control character."
    if any(ord(char) < 32 or ord(char) == 127 for char in save_name):
        return "Error: save name contains an unsupported control character."
    return None


async def load_save_from_frontend(conn: GameConnection, save_name: str) -> str:
    """Load a save from Civ VI's MainMenu state without GUI automation.

    ``Automation.SetAutoStartEnabled`` asks Civ VI to complete the same
    post-load Continue action used by the normal menu flow.  The load command
    is sent exactly once because Civ VI may close FireTuner while accepting it.
    """

    validation_error = _validate_save_name(save_name)
    if validation_error:
        return validation_error

    main_menu_index = next(
        (idx for idx, name in conn.lua_states.items() if name == "MainMenu"),
        None,
    )
    if main_menu_index is None:
        return "Error: MainMenu Lua state is unavailable; refusing UI automation fallback."

    # Match the native menu: load the complete queried entry, preserving its
    # Path, FileType and mod metadata. A hand-built descriptor failed the live
    # checkpoint round trip even though the native requirements check passed.
    save_filename = save_name.removesuffix(".Civ6Save") + ".Civ6Save"
    query = f"""
-- MCP_SAVE_QUERY_START
local wanted = {json.dumps(save_filename, ensure_ascii=False)};
local q = {{name=wanted, done=false, count=0}};
__civmcpSaveQuery = q;
local function onResults(files, queryID)
  if q.id ~= queryID then return; end;
  for _, entry in ipairs(files or {{}}) do
    if entry.Name == wanted and not entry.IsDirectory then
      q.count = q.count + 1; q.save = entry;
    end;
  end;
  q.done = true;
  UI.CloseFileListQuery(queryID);
  LuaEvents.FileListQueryResults.Remove(onResults);
end;
LuaEvents.FileListQueryResults.Add(onResults);
q.id = UI.QuerySaveGameList(SaveLocations.LOCAL_STORAGE, SaveTypes.SINGLE_PLAYER,
  SaveLocationOptions.NORMAL + SaveLocationOptions.AUTOSAVE + SaveLocationOptions.QUICKSAVE + SaveLocationOptions.LOAD_METADATA,
  SaveFileTypes.GAME_STATE);
print("MCP_SAVE_QUERY_STARTED");
print("{lq.SENTINEL}");
"""
    status = f"""
-- MCP_SAVE_QUERY_STATUS
local q = __civmcpSaveQuery;
if not q or not q.done then print("MCP_SAVE_QUERY|PENDING");
elseif q.count == 0 then print("MCP_SAVE_QUERY|MISSING");
elseif q.count ~= 1 then print("MCP_SAVE_QUERY|AMBIGUOUS");
else print("MCP_SAVE_QUERY|READY"); end;
print("{lq.SENTINEL}");
"""
    try:
        async with asyncio.timeout(10):
            await conn.execute_in_state(main_menu_index, query)
            while True:
                lines = await conn.execute_in_state(main_menu_index, status)
                if "MCP_SAVE_QUERY|READY" in lines:
                    break
                if "MCP_SAVE_QUERY|MISSING" in lines:
                    return f"Error: Save {save_filename!r} not found in the native list; no load sent."
                if "MCP_SAVE_QUERY|AMBIGUOUS" in lines:
                    return f"Error: Save {save_filename!r} is ambiguous; no load sent."
                await asyncio.sleep(0.2)
    except (OSError, TimeoutError, LuaError) as exc:
        return f"Error: Native save metadata unavailable; no load sent: {exc}"

    code = f"""
local q = __civmcpSaveQuery;
if not q or not q.done or q.count ~= 1 or q.name ~= {json.dumps(save_filename, ensure_ascii=False)} then
  print("MCP_FRONTEND_LOAD|false"); print("{lq.SENTINEL}"); return;
end;
local loadGame = q.save;
__civmcpSaveQuery = nil;
local automationOK = pcall(function() Automation.SetAutoStartEnabled(true); end);
local loadOK, loadResult = pcall(function()
  return Network.LoadGame(loadGame, ServerType.SERVER_TYPE_NONE);
end);
print("MCP_FRONTEND_AUTOSTART|" .. tostring(automationOK));
print("MCP_FRONTEND_LOAD|" .. tostring(loadOK and loadResult ~= false));
if not loadOK then print("MCP_FRONTEND_ERROR|" .. tostring(loadResult)); end;
print("{lq.SENTINEL}");
"""
    try:
        lines = await conn.execute_in_state(
            main_menu_index,
            code,
            timeout=5.0,
        )
    except (ConnectionError, OSError, TimeoutError) as exc:
        return (
            f"ERR:OUTCOME_UNKNOWN|FrontEnd load for {save_name!r} may have been "
            f"submitted; FireTuner disconnected: {exc}"
        )

    if "MCP_FRONTEND_LOAD|false" in lines:
        return f"Error: Civ VI rejected save {save_name!r}: {lines!r}"
    if "MCP_FRONTEND_LOAD|true" not in lines:
        return f"ERR:OUTCOME_UNKNOWN|FrontEnd load returned no confirmation: {lines!r}"
    if "MCP_FRONTEND_AUTOSTART|true" not in lines:
        return (
            "ERR:OUTCOME_UNKNOWN|save loading began but Civ VI auto-start was unavailable; "
            "refusing UI automation fallback."
        )
    return (
        f"Loading save {save_name}; Civ VI will complete Continue Game through "
        "its native FrontEnd automation."
    )


async def load_game_save(conn: GameConnection, save_name: str) -> str:
    """Load a save by name through Civ VI's native FrontEnd API.

    This operation is intentionally limited to the main menu.  An in-game
    load would invalidate the current FireTuner world and needs a separate,
    explicitly verified recovery workflow; silently restarting or navigating
    the GUI makes the result impossible to prove from scripts.
    """

    validation_error = _validate_save_name(save_name)
    if validation_error:
        return validation_error
    if conn.gamecore_index is not None:
        return (
            "Error: a game is already loaded; return to the main menu before "
            "calling load_game_save. No GUI fallback was attempted."
        )
    return await load_save_from_frontend(conn, save_name)


async def execute_lua(
    conn: GameConnection, code: str, context: str = "gamecore"
) -> str:
    """Escape hatch: run arbitrary Lua code."""
    # End every chunk with the sentinel, like the builders do — without it the
    # connection layer cannot tell "ran fine, printed nothing" from "never
    # answered" (silent sockets raise TimeoutError there now). Function-wrapped
    # so a trailing `return` in user code stays valid Lua.
    wrapped = f'(function() {code} end)(); print("{SENTINEL}")'
    if context == "ingame":
        lines = await conn.execute_write(wrapped)
    elif context.isdigit():
        lines = await conn.execute_in_state(int(context), wrapped)
    else:
        lines = await conn.execute_read(wrapped)
    return "\n".join(lines) if lines else "(no output)"

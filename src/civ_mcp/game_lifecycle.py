"""Game lifecycle — popup dismissal, save/load, raw Lua execution."""

from __future__ import annotations

import json
import logging
import re
import uuid

from civ_mcp import lua as lq
from civ_mcp.connection import CommandNotSentError, GameConnection

log = logging.getLogger(__name__)


_RECOVERY_SAVE_NAME = re.compile(r"^(?:0_MCP_\d+|AutoSave_\d+)$")
_SAVE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _validate_save_name(save_name: str) -> str | None:
    """Reject path/injection-like names before interpolating into Lua."""

    if not isinstance(save_name, str) or not _SAVE_NAME.fullmatch(save_name):
        return (
            "Error: unsupported save name; use the Civ VI save filename "
            "without extension (letters, digits, '.', '_' or '-')."
        )
    return None


async def _prepare_load_probe(conn: GameConnection) -> None:
    """Install an acknowledged UI-session marker before attempting a load.

    Reconnecting alone does not prove a reload. This marker survives a tuner
    reconnect but disappears when Civ VI replaces the InGame Lua world, so a
    same-seed, same-turn save can still be verified without resending the load.
    """
    token = uuid.uuid4().hex
    lines = await conn.execute_mutation(
        'if not ExposedMembers then ExposedMembers = {} end; '
        f'ExposedMembers.MCPReloadProbe = "{token}"; '
        f'print("RELOAD_PROBE|{token}"); print("{lq.SENTINEL}")',
        turn_action="load",
    )
    if f"RELOAD_PROBE|{token}" not in lines:
        raise ValueError("无法确认读档前的局面标记，未发送加载请求")
    conn._load_probe_token = token
    conn._load_probe_from_menu = False


async def verify_loaded_world(conn: GameConnection) -> bool:
    """Read-only proof that an attempted load reached a different Lua world."""
    token = getattr(conn, "_load_probe_token", None)
    from_menu = getattr(conn, "_load_probe_from_menu", False) is True
    revision = getattr(conn, "load_revision", None)
    if not isinstance(token, str) and not from_menu:
        return False
    try:
        await conn.reconnect()
        if conn.gamecore_index is None or conn.ingame_index is None:
            return False
        lines = await conn.execute_write(
            'local me = Game.GetLocalPlayer(); '
            'if me ~= nil and me >= 0 and Players[me] ~= nil then '
            'print("RELOAD_WORLD|" .. tostring(Game.GetCurrentGameTurn()) '
            '.. "|" .. tostring(ExposedMembers and ExposedMembers.MCPReloadProbe)); '
            f'end; print("{lq.SENTINEL}")',
            turn_action="load",
        )
        for line in lines:
            if line.startswith("RELOAD_WORLD|"):
                parts = line.split("|")
                if len(parts) == 3 and int(parts[1]) >= 0:
                    return (
                        (from_menu or parts[2] == "nil")
                        and getattr(conn, "load_revision", None) == revision
                        and getattr(conn, "_load_probe_token", None) == token
                        and (getattr(conn, "_load_probe_from_menu", False) is True) == from_menu
                    )
    except Exception:
        log.debug("读档后的新局面尚未确认", exc_info=True)
    return False


async def load_save_from_frontend(
    conn: GameConnection, save_name: str
) -> str:
    """Load a save from MainMenu and let Civ VI finish Continue internally.

    ``Automation.SetAutoStartEnabled`` is the same native post-load handler
    used by Civ VI's Continue Game flow.  This is the shared API-first path
    for DSH startup recovery and direct MCP lifecycle tools; it never clicks
    a window or uses OCR.
    """

    validation_error = _validate_save_name(save_name)
    if validation_error:
        return validation_error

    main_menu_index = next(
        (idx for idx, name in conn.lua_states.items() if name == "MainMenu"),
        None,
    )
    if main_menu_index is None:
        return "Error: MainMenu Lua state is unavailable; refusing unsafe UI fallback."

    code = f"""
local loadGame = {{}};
loadGame.Location = SaveLocations.LOCAL_STORAGE;
loadGame.Type = SaveTypes.SINGLE_PLAYER;
loadGame.IsAutosave = false;
loadGame.IsQuicksave = false;
loadGame.Directory = SaveDirectories.DEFAULT;
loadGame.Name = {json.dumps(save_name)};
local automationOK = pcall(function() Automation.SetAutoStartEnabled(true); end);
local loadOK = Network.LoadGame(loadGame, ServerType.SERVER_TYPE_NONE);
print("MCP_FRONTEND_AUTOSTART|" .. tostring(automationOK));
print("MCP_FRONTEND_LOAD|" .. tostring(loadOK));
print("{lq.SENTINEL}");
"""
    was_pending = getattr(conn, "reload_pending", False) is True
    conn._load_probe_token = None
    conn._load_probe_from_menu = True
    conn.reload_pending = True
    conn.load_revision = getattr(conn, "load_revision", 0) + 1
    try:
        lines = await conn.execute_in_state(
            main_menu_index,
            code,
            timeout=5.0,
            mutation=True,
            require_sentinel=False,
            turn_action="load",
        )
    except CommandNotSentError as exc:
        conn.reload_pending = was_pending
        return f"Error: {exc} LOAD_NOT_SUBMITTED"
    except Exception as exc:
        return f"Error: FrontEnd API failed for {save_name}: {exc}"
    if "MCP_FRONTEND_LOAD|false" in lines:
        conn.reload_pending = was_pending
        return f"Error: FrontEnd refused save {save_name}: {lines!r} LOAD_NOT_SUBMITTED"
    if "MCP_FRONTEND_LOAD|true" not in lines:
        return f"ERR:OUTCOME_UNKNOWN|无法确认 {save_name} 的加载结果：{lines!r}"
    if "MCP_FRONTEND_AUTOSTART|true" not in lines:
        return (
            "Error: save loading began but Civ VI automation auto-start was "
            "unavailable; refusing visual fallback."
        )
    return (
        f"Loading save {save_name} via FrontEnd API; Civ VI will confirm "
        "Continue Game through its built-in automation API."
    )


async def load_recovery_save_from_frontend(
    conn: GameConnection, save_name: str
) -> str:
    """Load a recovery save without OCR, including the final Continue action.

    The call runs in Civ VI's ``MainMenu`` Lua state.  Its native
    ``Automation.SetAutoStartEnabled`` switch tells the game's own
    ``LoadScreen`` to call the same handler as the visible Continue Game
    button once loading is complete.  It therefore needs no mouse, keyboard,
    focused window, or visual recognition.
    """

    if not _RECOVERY_SAVE_NAME.fullmatch(save_name):
        return f"Error: unsupported recovery save name: {save_name!r}"
    result = await load_save_from_frontend(conn, save_name)
    # Preserve the public recovery-tool wording while sharing the actual
    # FrontEnd implementation with arbitrary named saves.
    if result.startswith("Loading save "):
        return result.replace("Loading save ", "Loading recovery save ", 1)
    return result


async def dismiss_popup(conn: GameConnection) -> str:
    """Dismiss any blocking popup or UI overlay in the game.

    Three-phase approach:
    1. Single batched InGame call that checks all known popup/overlay names
       and closes diplomacy screens (fast — one TCP roundtrip).
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
        "WorldCongressPopup",
        "WorldCongressIntro",
    ]
    checks = []
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
        lines = await conn.execute_mutation(lua)
        for line in lines:
            if line.startswith("DISMISSED|"):
                dismissed.append(line.split("|", 1)[1])
            elif line.startswith("PENDING|"):
                if "DiplomacyDealView" in line:
                    pending_deal = True
                elif "DiplomacyActionView" in line:
                    pending_diplomacy = True
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
                        mutation=True,  # Close() mutates popup/engine state
                    )
                    if any("DISMISSED" in l for l in lines):
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
                            mutation=True,  # Close()/SetHide mutate popup state
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


async def save_game(conn: GameConnection, name: str) -> str:
    """Create a named save. Used for MCP per-turn autosaves."""
    lines = await conn.execute_mutation(
        f"local gf = {{}}; "
        f'gf.Name = "{name}"; '
        f"gf.Location = SaveLocations.LOCAL_STORAGE; "
        f"gf.Type = SaveTypes.SINGLE_PLAYER; "
        f"gf.IsAutosave = false; "
        f"gf.IsQuicksave = false; "
        f"Network.SaveGame(gf); "
        f'print("OK|{name}"); '
        f'print("{lq.SENTINEL}")'
    )
    if any("OK|" in l for l in lines):
        return f"Saved: {name}"
    return f"Save may have failed: {' '.join(lines)}"


def cleanup_old_autosaves(keep: int = 5) -> None:
    """Delete MCP autosaves older than the most recent `keep` saves."""
    import glob
    import os

    from .game_launcher import SINGLE_SAVE_DIR

    pattern = os.path.join(SINGLE_SAVE_DIR, "0_MCP_*.Civ6Save")
    saves = glob.glob(pattern)
    if len(saves) <= keep:
        return
    saves.sort(key=os.path.getmtime, reverse=True)
    for old in saves[keep:]:
        try:
            os.remove(old)
            log.debug("Deleted old MCP autosave: %s", old)
        except OSError as e:
            log.debug("Failed to delete %s: %s", old, e)


async def list_saves(conn: GameConnection) -> str:
    """List available saves (normal + autosave).

    Uses filesystem scan (reliable — finds all save types including
    autosaves and quicksaves). Falls back to Lua query if filesystem
    scan finds nothing.
    """
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
                for r in results:
                    parts = r.split("|", 2)
                    idx = parts[1]
                    name = parts[2] if len(parts) > 2 else "?"
                    lines_out.append(f"  {idx}. {name}")
                return "\n".join(lines_out)
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
    if not all_saves:
        return "No saves found on filesystem."

    lines = ["Available saves (filesystem scan, sorted by date):"]
    for i, (_mtime, name) in enumerate(all_saves[:25], 1):
        lines.append(f"  {i}. {name.replace('.Civ6Save', '')}")
    return "\n".join(lines)


async def load_save(conn: GameConnection, save_index: int) -> str:
    """Load a save by index from the most recent list_saves() query.

    The game will reload — the FireTuner connection stays alive but
    all Lua state is wiped. Wait a few seconds after calling this.
    """
    await _prepare_load_probe(conn)
    was_pending = getattr(conn, "reload_pending", False) is True
    conn.reload_pending = True
    conn.load_revision = getattr(conn, "load_revision", 0) + 1
    try:
        lines = await conn.execute_mutation(
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
            f"Network.LoadGame(save, ServerType.SERVER_TYPE_NONE)",
            turn_action="load",
        )
    except CommandNotSentError as exc:
        conn.reload_pending = was_pending
        return f"Error: {exc} LOAD_NOT_SUBMITTED"
    for line in lines:
        if line.startswith("ERR:NO_SAVE_LIST"):
            conn.reload_pending = was_pending
            return "Error: No save list cached. Call list_saves() first. LOAD_NOT_SUBMITTED"
        if line.startswith("ERR:INDEX_OUT_OF_RANGE"):
            count = line.split("|")[1] if "|" in line else "?"
            conn.reload_pending = was_pending
            return f"Error: Index {save_index} out of range (1-{count}). Call list_saves() to see available saves. LOAD_NOT_SUBMITTED"
        if line.startswith("LOADING|"):
            name = line.split("|", 1)[1]
            return f"Loading save: {name}. Game will reload — wait ~10 seconds then call get_game_overview to verify."
    return "Load command sent. Wait for game to reload."


async def load_game_save(conn: GameConnection, save_name: str) -> str:
    """Load a save by name — no list_saves() prerequisite.

    Use native FrontEnd loading at the menu, or one asynchronous in-game
    query-and-load. An unconfirmed request is never followed by a restart or
    another load; only read-back or the explicit recovery tool can resolve it.
    """
    import asyncio
    import sys

    validation_error = _validate_save_name(save_name)
    if validation_error:
        return validation_error

    from . import game_launcher

    # Refresh a stale/disconnected shared connection before inspecting Lua
    # states. This prevents a crashed session's old ``gamecore_index`` from
    # bypassing the MainMenu FrontEnd API path.
    try:
        await conn.ensure_connected()
    except ConnectionError as exc:
        return (
            "Error: FireTuner connection unavailable; refusing OCR recovery "
            f"to avoid taking over another client: {exc}"
        )

    # MainMenu is a distinct FrontEnd Lua state. Use the native load path so
    # Civ VI itself performs the post-load Continue action; never click the
    # menu while the shared tuner connection is unavailable.
    if conn.gamecore_index is None:
        frontend_result = await load_save_from_frontend(conn, save_name)
        if not frontend_result.startswith("Error:"):
            return frontend_result
        if "LOAD_NOT_SUBMITTED" not in frontend_result:
            return frontend_result
        if not game_launcher.ocr_recovery_enabled():
            return (
                f"{frontend_result} OCR fallback is disabled by default; set "
                f"{game_launcher.OCR_RECOVERY_ENV}=1 to opt in."
            )
        return await game_launcher.load_save_from_menu(save_name)

    # The Linux port cannot use in-game loading. Report the explicit recovery
    # path below instead of hiding a process restart inside this tool.
    if sys.platform != "linux":
        # Tier 1: Lua query-match-load (Windows/macOS only)
        await _prepare_load_probe(conn)
        was_pending = getattr(conn, "reload_pending", False) is True
        conn.reload_pending = True
        conn.load_revision = getattr(conn, "load_revision", 0) + 1
        load_sent = False
        try:
            await conn.execute_mutation(
                f"if not ExposedMembers then ExposedMembers = {{}} end; "
                f"ExposedMembers.MCPLoadResult = nil; "
                f"ExposedMembers.MCPLoadDone = false; "
                f"local function OnResults(fileList, qid) "
                f"  UI.CloseFileListQuery(qid); "
                f"  LuaEvents.FileListQueryResults.Remove(OnResults); "
                f"  for i, s in ipairs(fileList) do "
                f'    if s.Name == "{save_name}" then '
                f'      ExposedMembers.MCPLoadResult = "FOUND"; '
                f"      ExposedMembers.MCPLoadDone = true; "
                f"      Network.LeaveGame(); "
                f"      Network.LoadGame(s, ServerType.SERVER_TYPE_NONE); "
                f"      return "
                f"    end "
                f"  end; "
                f'  ExposedMembers.MCPLoadResult = "NOT_FOUND"; '
                f"  ExposedMembers.MCPLoadDone = true; "
                f"end; "
                f"LuaEvents.FileListQueryResults.Add(OnResults); "
                f"local opts = SaveLocationOptions.NORMAL + SaveLocationOptions.AUTOSAVE "
                f"  + SaveLocationOptions.QUICKSAVE + SaveLocationOptions.LOAD_METADATA; "
                f"UI.QuerySaveGameList(SaveLocations.LOCAL_STORAGE, SaveTypes.SINGLE_PLAYER, opts); "
                f'print("QUERY_SENT"); '
                f'print("{lq.SENTINEL}")',
                turn_action="load",
            )
            load_sent = True

            for _ in range(20):
                await asyncio.sleep(0.25)
                check = await conn.execute_write(
                    f"if ExposedMembers.MCPLoadDone then "
                    f'  print("RESULT|" .. tostring(ExposedMembers.MCPLoadResult)) '
                    f'else print("PENDING") end; '
                    f'print("{lq.SENTINEL}")',
                    turn_action="load",
                )
                for line in check:
                    if line == "RESULT|FOUND":
                        return (
                            f"Loading save: {save_name}. Game will reload — "
                            f"wait ~10 seconds then call get_game_overview to verify."
                        )
                    if line == "RESULT|NOT_FOUND":
                        conn.reload_pending = was_pending
                        return f"Error: Save '{save_name}' not found. LOAD_NOT_SUBMITTED"
                else:
                    continue
                break  # NOT_FOUND — try filesystem

            return (
                "ERR:OUTCOME_UNKNOWN|加载查询已提交，尚未确认结果；"
                "只读核验 get_game_overview，不自动重发或重启。"
            )
        except CommandNotSentError as exc:
            if not load_sent:
                conn.reload_pending = was_pending
                return f"Error: {exc} LOAD_NOT_SUBMITTED"
            return f"ERR:OUTCOME_UNKNOWN|加载已提交，但核验查询未发送：{exc}"
        except Exception as exc:
            log.debug("Lua load_game_save outcome unknown", exc_info=True)
            return (
                f"ERR:OUTCOME_UNKNOWN|加载可能已执行：{exc}。"
                "只读核验 get_game_overview，不自动重发或重启。"
            )
    else:
        log.info("Linux: skipping Lua load (Aspyr port bug) for '%s'", save_name)

    # Unsupported in-game platform: check the name before advising recovery.
    import os

    from .game_launcher import SAVE_DIR, SINGLE_SAVE_DIR

    auto_path = os.path.join(SAVE_DIR, f"{save_name}.Civ6Save")
    single_path = os.path.join(SINGLE_SAVE_DIR, f"{save_name}.Civ6Save")

    if not os.path.exists(auto_path) and not os.path.exists(single_path):
        return (
            f"Error: Save '{save_name}' not found in Lua query or on filesystem. "
            f"Check the name and try list_saves() to see available saves."
        )

    # Restarting remains a separate explicit lifecycle operation.
    return (
        "Error: 当前平台的对局内加载路径不可用；需要由操作者显式调用 "
        f"restart_and_load('{save_name}')。LOAD_NOT_SUBMITTED"
    )


async def execute_lua(
    conn: GameConnection, code: str, context: str = "gamecore"
) -> str:
    """Escape hatch: run arbitrary Lua code."""
    if context == "ingame":
        lines = await conn.execute_write(code, require_sentinel=False)
    elif context.isdigit():
        lines = await conn.execute_in_state(int(context), code)
    else:
        lines = await conn.execute_read(code, require_sentinel=False)
    return "\n".join(lines) if lines else "(no output)"

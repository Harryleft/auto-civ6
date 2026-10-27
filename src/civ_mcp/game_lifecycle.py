"""Game lifecycle — popup dismissal, save/load, raw Lua execution."""

from __future__ import annotations

import asyncio
import json
import uuid
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Literal

from civ_mcp import lua as lq
from civ_mcp.connection import GameConnection, LuaError, LuaStateUnavailable
from civ_mcp.lua._helpers import SENTINEL

log = logging.getLogger(__name__)
SAVE_CONFIRM_TIMEOUT = 10.0
SAVE_POLL_INTERVAL = 0.2


@dataclass(frozen=True)
class NativeTransition:
    """Native facts for sequencing; message is presentation only."""

    status: Literal["confirmed", "not_submitted", "submitted", "unknown"]
    message: str


class DiplomacyViewCleanup:
    """One explicit native Close with a callback receipt; never replay uncertainty."""

    def __init__(self) -> None:
        self.token: str | None = None
        self._scope_invalid = False

    def invalidate_scope(self) -> None:
        if self.pending:
            self._scope_invalid = True

    @property
    def pending(self) -> bool:
        return self.token is not None

    async def close(self, conn: GameConnection) -> NativeTransition:
        if self._scope_invalid:
            return NativeTransition("unknown", "Diplomacy UI cleanup unknown: world changed; only explicit protected replacement can retire this attempt.")
        confirming = self.pending
        token = self.token or uuid.uuid4().hex
        # The receipt belongs to this context and attempt. It proves the native
        # callback returned; hidden alone cannot prove UninitializeView ran.
        source = f"""
-- DIPLOMACY_NATIVE_CLOSE: no diplomatic responses or direct event release
local token = {json.dumps(token)}
local confirming = {str(confirming).lower()}
local function report(status, detail)
    print("DIPLO_CLEANUP|" .. status .. "|" .. detail)
end
local okID, id = pcall(function() return ContextPtr:GetID() end)
if not okID or id ~= "DiplomacyActionView" then report("NOT_SUBMITTED", "CONTEXT_UNKNOWN"); return end
local function noPending()
    local me = Game.GetLocalPlayer()
    if type(me) ~= "number" or me < 0 or not Players[me] then return false, "IDENTITY_UNKNOWN" end
    local queued = DiplomacyManager.HasQueuedSession(me)
    if type(queued) ~= "boolean" then return false, "QUEUED_UNKNOWN" end
    if queued then return false, "QUEUED_SESSION" end
    for other = 0, 63 do
        if other ~= me and Players[other] then
            local sid = DiplomacyManager.FindOpenSessionID(me, other)
            if sid ~= nil and type(sid) ~= "number" then return false, "SESSION_UNKNOWN" end
            if sid and sid >= 0 then return false, "OPEN_SESSION" end
            local pending = DealManager.HasPendingDeal(me, other)
            if type(pending) ~= "boolean" then return false, "DEAL_UNKNOWN" end
            if pending then return false, "PENDING_DEAL" end
        end
    end
    return true, "NONE"
end
local ok, safe, reason = pcall(noPending)
if not ok then
    report(confirming and "UNKNOWN" or "NOT_SUBMITTED", "SESSION_READ_UNKNOWN"); return
end
if not safe then
    report(confirming and "UNKNOWN" or "NOT_SUBMITTED", reason); return
end
local okScope, scope = pcall(function()
    local me = Game.GetLocalPlayer()
    local civilization = PlayerConfigurations[me]:GetCivilizationTypeName()
    local seed = GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
    if type(civilization) ~= "string" or type(seed) ~= "number" then return nil end
    return tostring(me) .. "|" .. civilization .. "|" .. tostring(seed)
end)
if not okScope or not scope then report(confirming and "UNKNOWN" or "NOT_SUBMITTED", "WORLD_UNKNOWN"); return end
if confirming then
    if not __civmcp_diplo_close or __civmcp_diplo_close.token ~= token or __civmcp_diplo_close.scope ~= scope or __civmcp_diplo_close.done ~= true then
        report("UNKNOWN", "NATIVE_CALLBACK_NOT_CONFIRMED"); return
    end
else
    if type(Close) ~= "function" then report("NOT_SUBMITTED", "NATIVE_CLOSE_UNAVAILABLE"); return end
    __civmcp_diplo_close = {{token=token, scope=scope, done=false}}
    local called, err = pcall(Close)
    if not called then report("UNKNOWN", "NATIVE_CLOSE_FAILED:" .. tostring(err)); return end
    __civmcp_diplo_close.done = true
end
local okAfter, safeAfter, afterReason = pcall(noPending)
local okHidden, hidden = pcall(function() return ContextPtr:IsHidden() end)
local okProcessing, processing = pcall(UI.IsProcessingMessages)
if okAfter and safeAfter and okHidden and hidden == true and okProcessing and processing == false then
    report("CONFIRMED", "NATIVE_CLOSE_RETURNED;hidden=true;ui_processing=false")
else
    report("UNKNOWN", "POST_CLOSE_NOT_CONFIRMED")
end
print("{SENTINEL}")
"""
        # Store before the invocation. A missing response might follow Close.
        self.token = token
        try:
            lines = await conn.execute_in_named_state("DiplomacyActionView", source)
        except LuaStateUnavailable as exc:
            if not confirming:
                self.token = None
            return NativeTransition("unknown" if confirming else "not_submitted", f"Diplomacy UI cleanup: {exc}")
        except (OSError, TimeoutError, LuaError) as exc:
            return NativeTransition("unknown", f"Diplomacy UI cleanup unknown: {exc}; no callback was replayed.")
        rows = [line.split("|", 2) for line in lines if line.startswith("DIPLO_CLEANUP|")]
        if len(rows) != 1 or len(rows[0]) != 3:
            return NativeTransition("unknown", "Diplomacy UI cleanup unknown: no valid native receipt; no callback was replayed.")
        _, status, detail = rows[0]
        if status == "CONFIRMED":
            self.token = None
            return NativeTransition("confirmed", "Diplomacy UI cleanup confirmed: " + detail)
        if status == "NOT_SUBMITTED" and not confirming:
            self.token = None
            return NativeTransition("not_submitted", "Diplomacy UI cleanup not submitted: " + detail + "; inspect pending diplomacy/trades.")
        return NativeTransition("unknown", "Diplomacy UI cleanup unknown: " + detail + "; use dismiss_popup only to confirm this callback, or explicit recovery.")


def clear_save_listing() -> None:
    """Retire displayed indexes when the world is replaced."""
    _filesystem_save_names.clear()


def listed_save_name(index: int) -> str | None:
    if 1 <= index <= len(_filesystem_save_names):
        return _filesystem_save_names[index - 1]
    return None


INFORMATION_POPUPS = (
    "TechCivicCompletedPopup", "NaturalWonderPopup", "NaturalDisasterPopup",
    "WonderBuiltPopup", "EraCompletePopup", "HistoricMoments", "MomentPopup",
    "ProjectBuiltPopup", "RockBandPopup", "RockBandMoviePopup",
)


async def visible_information_popups(conn: GameConnection) -> list[str] | None:
    """Read known information overlays; incomplete visibility remains unknown."""
    names = ", ".join(json.dumps(name) for name in INFORMATION_POPUPS)
    lines = await conn.execute_write(f"""
-- INFORMATION_POPUPS: read-only, independent of engine/UI turn readiness
local known = true
for _, name in ipairs({{{names}}}) do
    local ok, visible = pcall(function()
        local c = ContextPtr:LookUpControl("/InGame/" .. name)
        return c ~= nil and not c:IsHidden()
    end)
    if not ok then known = false
    elseif visible then print("INFORMATION_POPUPS|VISIBLE|" .. name) end
end
print(known and "INFORMATION_POPUPS|DONE" or "INFORMATION_POPUPS|UNKNOWN")
print("{lq.SENTINEL}")
""")
    visible = []
    for line in lines:
        if line.startswith("INFORMATION_POPUPS|VISIBLE|"):
            name = line.split("|", 2)[2]
            if name not in INFORMATION_POPUPS or name in visible:
                return None
            visible.append(name)
    if "INFORMATION_POPUPS|DONE" not in lines or "INFORMATION_POPUPS|UNKNOWN" in lines:
        return None
    return visible


def _native_popup_close(name: str) -> str:
    # Tech/civic OnClose is the button callback (TryClose), whereas its Close
    # clears the whole private queue. Never mutate that queue from MCP.
    callback = "OnClose" if name == "TechCivicCompletedPopup" else "Close"
    fallback = "" if name == "TechCivicCompletedPopup" else 'if type(callback) ~= "function" then callback = OnClose end'
    return f"""
local expected = {json.dumps(name)}
local okID, id = pcall(function() return ContextPtr:GetID() end)
if not okID or id ~= expected then
    print("POPUP_UNKNOWN|" .. expected .. "|context mismatch")
    return
end
local okHidden, hidden = pcall(function() return ContextPtr:IsHidden() end)
if not okHidden or type(hidden) ~= "boolean" then
    print("POPUP_UNKNOWN|" .. expected .. "|visibility unavailable")
    return
end
if hidden then print("POPUP_HIDDEN|" .. expected); return end
local callback = {callback}
{fallback}
if type(callback) ~= "function" then
    print("POPUP_UNKNOWN|" .. expected .. "|native callback unavailable")
    return
end
local ok, err = pcall(callback)
if not ok then
    print("POPUP_UNKNOWN|" .. expected .. "|native callback failed: " .. tostring(err))
    return
end
local verified, nowHidden = pcall(function() return ContextPtr:IsHidden() end)
if verified and nowHidden == true then print("POPUP_CLOSED|" .. expected)
elseif verified and nowHidden == false then print("POPUP_VISIBLE|" .. expected)
else print("POPUP_UNKNOWN|" .. expected .. "|visibility unavailable") end
print("{lq.SENTINEL}")
"""


async def dismiss_popup_result(conn: GameConnection, diplomacy_cleanup: DiplomacyViewCleanup | None = None) -> NativeTransition:
    """Explicitly close information overlays through their native callbacks.

    State discovery is by exact name, never guessed indexes. A successful
    callback is not a closed-popup fact until that context is proven hidden.
    """
    dismissed = []
    cleanup = diplomacy_cleanup or DiplomacyViewCleanup()
    cleanup_result = None

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
    from civ_mcp.lua.unit_transforms import build_stale_promotion_cleanup
    checks.append(build_stale_promotion_cleanup())
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
    # Detect the view even when hidden: SetHide in older clients could leave
    # InitializeView's event reference owned. Native Close below is guarded by
    # fresh session/deal facts; it never supplies a diplomatic response.
    checks.append(
        'do local dv = ContextPtr:LookUpControl("/InGame/DiplomacyActionView") '
        'if dv then print("DIPLOMACY_VIEW_PRESENT") end '
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
    pending_promotion = False
    pending_diplomacy = False
    diplomacy_view_present = False
    try:
        lua = " ".join(checks) + f' print("{lq.SENTINEL}")'
        lines = await conn.execute_write(lua)
        for line in lines:
            if line.startswith("DISMISSED|"):
                dismissed.append(line.split("|", 1)[1])
            elif line == "DIPLOMACY_VIEW_PRESENT":
                diplomacy_view_present = True
            elif line.startswith("PENDING|"):
                if "DiplomacyDealView" in line:
                    pending_deal = True
                elif "DiplomacyActionView" in line:
                    pending_diplomacy = True
                elif "UnitPromotion" in line:
                    pending_promotion = True
    except ConnectionError:
        raise  # dead transport is not "no popups" — surface it
    except Exception as e:
        log.debug("Phase 1 dismiss failed: %s", e)
        return NativeTransition("unknown", f"Popup dismissal batch incomplete: {e}; outcome unknown.")

    if diplomacy_view_present or pending_diplomacy or cleanup.pending:
        cleanup_result = await cleanup.close(conn)
        if cleanup_result.status == "unknown":
            # 外交关闭已有专属回执所有者，未确认时不再进入其他弹窗写入阶段。
            notice = ". Also: diplomacy session active — use respond_to_diplomacy." if pending_diplomacy else ""
            return NativeTransition("unknown", cleanup_result.message + notice)

    unknown = None
    try:
        # One budget covers discovery and every queued native callback. This is
        # explicit dismissal, never a background probe or a replay of end-turn.
        async with asyncio.timeout(15.0):
            visible = await visible_information_popups(conn)
            if visible is None:
                unknown = "Information popup visibility unavailable; outcome unknown."
            elif visible:
                if any(name not in conn.lua_states.values() for name in visible):
                    await conn.rediscover_states(timeout=5.0)
                for name in visible:
                    indexes = [idx for idx, found in conn.lua_states.items() if found == name]
                    if not indexes:
                        unknown = f"Popup still visible: {name}; native state unavailable; outcome unknown."
                        break
                    index = indexes[0]
                    for _ in range(20):
                        lines = await conn.execute_in_state(
                            index, _native_popup_close(name), timeout=3.0,
                        )
                        if f"POPUP_CLOSED|{name}" in lines:
                            dismissed.append(name)
                            break
                        if f"POPUP_HIDDEN|{name}" in lines:
                            break
                        if f"POPUP_VISIBLE|{name}" not in lines:
                            unknown = f"Popup {name}: native close not confirmed; outcome unknown."
                            break
                    else:
                        unknown = f"Popup still visible: {name}; native information queue remains; outcome unknown."
                    if unknown:
                        break
    except (ConnectionError, TimeoutError, LuaError) as exc:
        unknown = f"Information popup close incomplete: {exc}; outcome unknown."

    # 展示文案还可能包含明确未提交的外交关闭或待选晋升，不据文案判定未知。
    uncertain = unknown is not None
    if cleanup_result is not None and cleanup_result.status == "confirmed":
        dismissed.append("DiplomacyActionView (native Close)")
        pending_diplomacy = False
    elif cleanup_result is not None:
        uncertain = uncertain or cleanup_result.status == "unknown"
        unknown = (unknown + " " if unknown else "") + cleanup_result.message

    if pending_promotion:
        unknown = (unknown + " " if unknown else "") + "Promotion notification retained: a choice remains or eligibility is unavailable; use get_unit_promotions."

    # Final phase: dismiss Windows-level crash dialogs (Firaxis Crash
    # Reporter, Unhandled Exception).  These are Win32 dialogs that appear
    # on top of the game after EXCEPTION_ACCESS_VIOLATION crashes — the
    # game keeps running but Lua calls return degraded data until dismissed.
    from . import game_launcher

    crash_dismissed = await game_launcher.dismiss_crash_dialogs()
    dismissed.extend(crash_dismissed)

    if dismissed:
        msg = f"Dismissed: {', '.join(dismissed)}"
        if unknown:
            msg += ". " + unknown
        if pending_diplomacy:
            msg += ". Also: diplomacy session active — use respond_to_diplomacy."
        if pending_deal:
            msg += " (incoming trade deal pending — use get_pending_trades)"
        return NativeTransition("unknown" if uncertain else "confirmed", msg)
    if unknown:
        if pending_diplomacy:
            unknown += ". Also: diplomacy session active — use respond_to_diplomacy."
        if pending_deal:
            unknown += ". Incoming trade deal pending — use get_pending_trades."
        return NativeTransition("unknown" if uncertain else "not_submitted", unknown)
    if pending_diplomacy:
        return NativeTransition("not_submitted", "Diplomacy session active — use respond_to_diplomacy to handle it.")
    if pending_deal:
        return NativeTransition("not_submitted", "No popups to dismiss (incoming trade deal pending — use get_pending_trades).")
    return NativeTransition("confirmed", "No popups to dismiss.")


async def dismiss_popup(conn: GameConnection, diplomacy_cleanup: DiplomacyViewCleanup | None = None) -> str:
    """保留原有底层字符串接口；运行层消费结构化状态。"""
    return (await dismiss_popup_result(conn, diplomacy_cleanup)).message


# ------------------------------------------------------------------
# Save / Load
# ------------------------------------------------------------------


def _lua_escape(value: str) -> str:
    """Escape a string for interpolation into a double-quoted Lua string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


# Names from the most recently displayed listing, from either filesystem or
# native enumeration. An index always refers to that exact displayed snapshot.
_filesystem_save_names: list[str] = []


async def save_game_result(conn: GameConnection, name: str) -> NativeTransition:
    """Submit once; require native SaveComplete and the exact fresh file.

    Network.SaveGame only acknowledges submission. Never replay a lost
    request: its file may still finish writing after FireTuner disconnects.
    """
    from civ_mcp.game_launcher import SINGLE_SAVE_DIR
    from civ_mcp import checkpoints

    error = _validate_save_name(name)
    if error:
        return NativeTransition("not_submitted", error)
    name = name.removesuffix(".Civ6Save")
    if checkpoints.is_original_baseline_name(name):
        return NativeTransition("not_submitted", "Error: An original baseline cannot be overwritten.")
    path = Path(SINGLE_SAVE_DIR) / f"{name}.Civ6Save"

    def fingerprint():
        try:
            stat = path.stat()
            return (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        except FileNotFoundError:
            return None

    try:
        before = fingerprint()
    except OSError as exc:
        return NativeTransition("not_submitted", f"Error: Save path unavailable; no save submitted: {exc}")
    # The installed native automation tests explicitly wait for SaveComplete.
    # It has no filename argument, so arm before our single request, bind the
    # listener to this token, and also require this exact file to become fresh.
    # No end-turn/save request may overlap this serialized active-turn operation.
    token = uuid.uuid4().hex
    popup_names = ", ".join(json.dumps(name) for name in INFORMATION_POPUPS)
    code = f"""
local prior = __civmcpSaveCompletion;
if prior and not prior.done then print("MCP_SAVE_PENDING"); print("{lq.SENTINEL}"); return; end;
-- This tool saves confirmed active turns. Do not release the end-game pause
-- or enter One More Turn to make a terminal save possible.
local stateOK, terminal = pcall(function()
  local winner = Game.GetWinningTeam();
  assert(winner == nil or type(winner) == "number" and winner == winner
    and math.abs(winner) < math.huge and winner == math.floor(winner));
  if winner ~= nil and winner >= 0 then return true; end;
  local alive = Players[Game.GetLocalPlayer()]:IsAlive();
  assert(type(alive) == "boolean");
  return not alive;
end);
if not stateOK then print("MCP_SAVE_STATE_UNAVAILABLE"); print("{lq.SENTINEL}"); return; end;
if terminal then print("MCP_SAVE_TERMINAL"); print("{lq.SENTINEL}"); return; end;
-- 在同一原生调用中检查动画与弹窗，避免检查后到保存前出现界面竞态。
local uiKnown, blockingPopup = pcall(function()
  for _, name in ipairs({{{popup_names}}}) do
    local c = ContextPtr:LookUpControl("/InGame/" .. name);
    if c ~= nil then
      local hidden = c:IsHidden(); assert(type(hidden) == "boolean");
      if not hidden then return name; end;
    end;
  end;
  local mode = UI.GetInterfaceMode();
  assert(type(mode) == "number" and type(InterfaceModeTypes.CINEMATIC) == "number");
  if mode == InterfaceModeTypes.CINEMATIC then return "cinematic_camera"; end;
end);
if not uiKnown then print("MCP_SAVE_UI_UNAVAILABLE"); print("{lq.SENTINEL}"); return; end;
if blockingPopup then
  print("MCP_SAVE_POPUP_BLOCKED"); print("MCP_SAVE_POPUP|" .. blockingPopup);
  print("{lq.SENTINEL}"); return;
end;
local available = pcall(function()
  assert(type(Network.SaveGame) == "function");
  assert(SaveLocations.LOCAL_STORAGE ~= nil and SaveTypes.SINGLE_PLAYER ~= nil);
  assert(type(Events.SaveComplete.Add) == "function" and type(Events.SaveComplete.Remove) == "function");
end);
if not available then print("MCP_SAVE_UNAVAILABLE"); print("{lq.SENTINEL}"); return; end;
if prior and prior.handler then pcall(function() Events.SaveComplete.Remove(prior.handler); end); end;
local q = {{token="{token}", done=false}};
__civmcpSaveCompletion = q;
q.handler = function()
  if __civmcpSaveCompletion == q then q.done = true; end;
  Events.SaveComplete.Remove(q.handler);
end;
local armed = pcall(function() Events.SaveComplete.Add(q.handler); end);
if not armed then
  pcall(function() Events.SaveComplete.Remove(q.handler); end);
  __civmcpSaveCompletion = nil;
  print("MCP_SAVE_UNAVAILABLE"); print("{lq.SENTINEL}"); return;
end;
local gf = {{}};
gf.Name = "{_lua_escape(name)}";
gf.Location = SaveLocations.LOCAL_STORAGE;
gf.Type = SaveTypes.SINGLE_PLAYER;
gf.IsAutosave = false;
gf.IsQuicksave = false;
local ok, result = pcall(function() return Network.SaveGame(gf); end);
-- Entering SaveGame may start a write even if it throws or returns false.
-- Only the SaveComplete handler supplies completion evidence.
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
            lines = []
            try:
                lines = await conn.execute_write(code)
            except (OSError, TimeoutError, LuaError):
                pass
            finally:
                # Serialized saves cannot overlap recovery. Invalidate once a
                # submission may have occurred, including cancellation/lost
                # replies; a proven zero-submit refusal preserves old records.
                unsubmitted = {"MCP_SAVE_PENDING", "MCP_SAVE_UNAVAILABLE",
                                "MCP_SAVE_TERMINAL", "MCP_SAVE_STATE_UNAVAILABLE",
                                "MCP_SAVE_POPUP_BLOCKED", "MCP_SAVE_UI_UNAVAILABLE"}
                if not unsubmitted.intersection(lines):
                    checkpoints.forget_completion(name)
            if "MCP_SAVE_PENDING" in lines:
                return NativeTransition("unknown", "ERR:OUTCOME_UNKNOWN|Earlier native save completion is unresolved; no new save submitted. Use explicit recovery.")
            if "MCP_SAVE_UNAVAILABLE" in lines:
                return NativeTransition("not_submitted", "Error: Native save operation or completion event unavailable; no save submitted.")
            if "MCP_SAVE_TERMINAL" in lines:
                return NativeTransition("not_submitted", "Error: GAME_OVER|Saving is unavailable at the confirmed terminal boundary; no save submitted. Keep the last confirmed checkpoint and terminal evidence.")
            if "MCP_SAVE_STATE_UNAVAILABLE" in lines:
                return NativeTransition("not_submitted", "Error: SAVE_STATE_UNAVAILABLE|Native terminal state unavailable; no save submitted.")
            if "MCP_SAVE_POPUP_BLOCKED" in lines:
                popup = next((row.split("|", 1)[1] for row in lines if row.startswith("MCP_SAVE_POPUP|")), "information popup")
                return NativeTransition("not_submitted", f"Error: SAVE_POPUP_BLOCKED|{popup}; dismiss_popup before saving; no save submitted.")
            if "MCP_SAVE_UI_UNAVAILABLE" in lines:
                return NativeTransition("not_submitted", "Error: SAVE_UI_UNAVAILABLE|Native popup visibility unavailable; no save submitted.")
            while True:
                current = fingerprint()
                if current and current != before and current[1] > 0:
                    try:
                        observed = await conn.execute_write(status)
                    except (OSError, TimeoutError, LuaError):
                        observed = []
                    if "MCP_SAVE_COMPLETE|true" in observed and fingerprint() == current:
                        checkpoints.mark_complete(name)
                        return NativeTransition("confirmed", f"Saved: {name}; native completion and file confirmed ({current[1]} bytes).")
                await asyncio.sleep(SAVE_POLL_INTERVAL)
    except (TimeoutError, OSError):
        return NativeTransition("unknown",
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


async def save_game(conn: GameConnection, name: str) -> str:
    """Display wrapper; sequencing uses the native transition facts."""
    return (await save_game_result(conn, name)).message



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


async def is_in_frontend(conn: GameConnection, *, timeout: float = 5.0) -> bool | None:
    """Read the native frontend fact from a freshly discovered named context.

    World indexes can remain in the directory after returning to the menu.
    Neither their presence nor MainMenu's presence proves the current mode.
    """
    try:
        async with asyncio.timeout(timeout):
            while True:
                try:
                    await conn.rediscover_states(timeout=timeout)
                    index = next((idx for idx, name in conn.lua_states.items() if name == "MainMenu"), None)
                    if index is None:
                        index = next((idx for idx, name in conn.lua_states.items() if name == "InGame"), None)
                    if index is None:
                        return None
                    lines = await conn.execute_in_state(index, f"""
-- MCP_FRONTEND_STATUS: read-only native frontend fact
local ok, value = pcall(function() return UI.IsInFrontEnd(); end);
if not ok or type(value) ~= "boolean" then print("MCP_FRONTEND|UNKNOWN");
else print("MCP_FRONTEND|" .. tostring(value)); end;
print("{lq.SENTINEL}");
""", timeout=timeout)
                    break
                except OSError:
                    # FireTuner can briefly refuse a replacement client while
                    # retiring the old one. Retry only discovery/native reads,
                    # within the same deadline; exit/load are never replayed.
                    await asyncio.sleep(0.2)
    except (TimeoutError, LuaError):
        return None
    facts = [line for line in lines if line.startswith("MCP_FRONTEND|")]
    if facts == ["MCP_FRONTEND|true"]:
        return True
    if facts == ["MCP_FRONTEND|false"]:
        return False
    return None


async def return_to_main_menu_result(conn: GameConnection, *, timeout: float = 30.0) -> NativeTransition:
    """Submit at most one native exit, then confirm only through native facts.

    The caller must serialize session replacement and retain its gate after an
    unknown outcome. This helper never kills, launches, loads or resets state.
    Its complete discovery/submit/confirmation budget is capped at 30 seconds.
    """
    attempted = False
    try:
        async with asyncio.timeout(min(timeout, 30.0)):
            frontend = await is_in_frontend(conn)
            if frontend is True:
                return NativeTransition("confirmed", "Main menu confirmed.")
            if frontend is None:
                return NativeTransition("not_submitted", "Error: Native frontend state unavailable; no exit sent.")
            index = next((idx for idx, name in conn.lua_states.items() if name == "InGame"), None)
            if index is None:
                return NativeTransition("not_submitted", "Error: InGame Lua state unavailable; no exit sent.")
            code = f"""
-- MCP_MENU_EXIT: submit once; never replay after a lost response
local ok, frontend = pcall(function() return UI.IsInFrontEnd(); end);
if not ok or type(frontend) ~= "boolean" then
  print("MCP_MENU_EXIT|UNAVAILABLE"); print("{lq.SENTINEL}"); return;
end;
if frontend then print("MCP_MENU_EXIT|ALREADY"); print("{lq.SENTINEL}"); return; end;
if not Events or not Events.ExitToMainMenu then
  print("MCP_MENU_EXIT|UNAVAILABLE"); print("{lq.SENTINEL}"); return;
end;
local submitted, err = pcall(function() Events.ExitToMainMenu(); end);
print(submitted and "MCP_MENU_EXIT|SUBMITTED" or "MCP_MENU_EXIT|UNKNOWN");
print("{lq.SENTINEL}");
"""
            attempted = True
            try:
                lines = await conn.execute_in_state(index, code, timeout=5.0)
            except (OSError, TimeoutError, LuaError):
                lines = []
            if lines == ["MCP_MENU_EXIT|UNAVAILABLE"]:
                return NativeTransition("not_submitted", "Error: Native exit unavailable; no exit sent.")
            while True:
                if await is_in_frontend(conn) is True:
                    return NativeTransition("confirmed", "Main menu confirmed.")
                await asyncio.sleep(0.2)
    except TimeoutError:
        if not attempted:
            return NativeTransition("not_submitted", "Error: Native frontend discovery timed out; no exit sent.")
    return NativeTransition("unknown",
        "ERR:OUTCOME_UNKNOWN|Return to main menu was attempted once but not confirmed; "
        "do not resend. Use explicit restart_and_load for fault recovery."
    )


async def return_to_main_menu(conn: GameConnection, *, timeout: float = 30.0) -> str:
    """Compatibility display wrapper for the typed native transition."""
    return (await return_to_main_menu_result(conn, timeout=timeout)).message


async def load_save_from_frontend_result(conn: GameConnection, save_name: str) -> NativeTransition:
    """Load a save from Civ VI's MainMenu state without GUI automation.

    ``Automation.SetAutoStartEnabled`` asks Civ VI to complete the same
    post-load Continue action used by the normal menu flow.  The load command
    is sent exactly once because Civ VI may close FireTuner while accepting it.
    """

    validation_error = _validate_save_name(save_name)
    if validation_error:
        return NativeTransition("not_submitted", validation_error)

    main_menu_index = next(
        (idx for idx, name in conn.lua_states.items() if name == "MainMenu"),
        None,
    )
    if main_menu_index is None:
        return NativeTransition("not_submitted", "Error: MainMenu Lua state is unavailable; refusing UI automation fallback.")

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
                    return NativeTransition("not_submitted", f"Error: Save {save_filename!r} not found in the native list; no load sent.")
                if "MCP_SAVE_QUERY|AMBIGUOUS" in lines:
                    return NativeTransition("not_submitted", f"Error: Save {save_filename!r} is ambiguous; no load sent.")
                await asyncio.sleep(0.2)
    except (OSError, TimeoutError, LuaError) as exc:
        return NativeTransition("not_submitted", f"Error: Native save metadata unavailable; no load sent: {exc}")

    code = f"""
local frontendOK, frontend = pcall(function() return UI.IsInFrontEnd(); end);
if not frontendOK or type(frontend) ~= "boolean" or frontend ~= true then
  print("MCP_FRONTEND_NOT_READY|" .. (frontendOK and tostring(frontend) or "UNKNOWN"));
  print("{lq.SENTINEL}"); return;
end;
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
    except (ConnectionError, OSError, TimeoutError, LuaError) as exc:
        return NativeTransition("unknown",
            f"ERR:OUTCOME_UNKNOWN|FrontEnd load for {save_name!r} may have been "
            f"submitted; FireTuner disconnected: {exc}"
        )

    if any(line.startswith("MCP_FRONTEND_NOT_READY|") for line in lines):
        return NativeTransition("not_submitted", "Error: Native frontend is not confirmed in the load context; no load sent.")
    # pcall failing says nothing about whether Network.LoadGame performed its
    # side effect before raising. Only a normal false result proves rejection.
    if any(line.startswith("MCP_FRONTEND_ERROR|") for line in lines):
        return NativeTransition("unknown",
            f"ERR:OUTCOME_UNKNOWN|Native load for {save_name!r} raised after invocation; "
            f"it may have been submitted. Do not replay: {lines!r}")
    if "MCP_FRONTEND_LOAD|false" in lines:
        return NativeTransition("not_submitted", f"Error: Civ VI rejected save {save_name!r}: {lines!r}")
    if "MCP_FRONTEND_LOAD|true" not in lines:
        return NativeTransition("unknown", f"ERR:OUTCOME_UNKNOWN|FrontEnd load returned no confirmation: {lines!r}")
    if "MCP_FRONTEND_AUTOSTART|true" not in lines:
        return NativeTransition("unknown",
            "ERR:OUTCOME_UNKNOWN|save loading began but Civ VI auto-start was unavailable; "
            "refusing UI automation fallback."
        )
    return NativeTransition("submitted",
        f"Loading save {save_name}; Civ VI will complete Continue Game through "
        "its native FrontEnd automation."
    )


async def load_save_from_frontend(conn: GameConnection, save_name: str) -> str:
    """Compatibility display wrapper; replacement is owned by GameState."""
    return (await load_save_from_frontend_result(conn, save_name)).message


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
    frontend = await is_in_frontend(conn)
    if frontend is False:
        return (
            "Error: a game is already loaded; return to the main menu before "
            "calling load_game_save. No GUI fallback was attempted."
        )
    if frontend is None:
        return "Error: Native frontend state unavailable; no load sent."
    return await load_save_from_frontend(conn, save_name)


async def execute_lua(
    conn: GameConnection, code: str, context: str = "gamecore"
) -> str:
    """Escape hatch: run arbitrary Lua code."""
    if context == "ingame":
        lines = await conn.execute_write(code)
    elif context.isdigit():
        lines = await conn.execute_in_state(int(context), code)
    else:
        lines = await conn.execute_read(code)
    return "\n".join(lines) if lines else "(no output)"

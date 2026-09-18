"""Assembly: lifespan wiring, the FastMCP app object, and the entry point.

Split out of the former server.py monolith (move-only); see server/__init__.py.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, AsyncIterator

import uvicorn
from mcp.server.fastmcp import FastMCP

from civ6_belief_engine.belief_engine import BeliefEngine
from civ6_belief_engine.belief_mode import BELIEF_MODE_ENV, BeliefMode
from civ_mcp import game_launcher, heartbeat
from civ_mcp.game_lifecycle import load_recovery_save_from_frontend
from civ_mcp.connection import GameConnection
from civ_mcp.game_over_watchdog import GameOverWatchdog
from civ_mcp.game_state import GameState
from civ_mcp.logger import GameLogger
from civ_mcp.map_capture import MapCapture
from civ_mcp.spatial import SpatialTracker
from civ_mcp.spectator import CameraController, PopupWatcher
from civ_mcp.telemetry import (
    AlertSink,
    CloudSink,
    LocalSink,
    TelemetryEmitter,
)
from civ_mcp.web_api import create_app

log = logging.getLogger(__name__)

PLAY_PROFILE_ENV = "CIV_MCP_PLAY_PROFILE"

# The belief/governance control plane, by tool name. The list is explicit rather
# than derived at runtime because the execution guard in ``pipeline`` must
# recognise these names *after* they have been removed from the served surface.
#
# Drift is covered from the other side: tests/test_lean_profile.py asserts this
# set equals the tools actually declared by the modules below, so adding a new
# control-plane tool there fails the build instead of quietly joining the lean
# profile.
LEAN_HIDDEN_CONTROL_TOOLS = frozenset(
    {
        "assess_route_combat_risk",
        "cancel_routed_action",
        "delete_belief_entity",
        "get_belief_metrics",
        "get_belief_state",
        "get_belief_trace",
        "get_calibration_report",
        "get_governance_brief",
        "get_turn_brief",
        "rebalance_hypotheses_bayesian",
        "rebalance_hypothesis_pool",
        "recompute_failure_attribution",
        "record_action_verification",
        "record_observation",
        "resolve_governance_council",
        "resolve_prediction",
        "review_belief_engine",
        "review_governance_proposal",
        "route_belief_decision",
        "run_trend_forecast",
        "set_plan_status",
        "submit_governance_proposal",
        "update_belief_entity",
        "upsert_belief",
        "upsert_dynamic_plan",
        "upsert_failure_attribution",
        "upsert_hypothesis",
        "upsert_prediction",
        "upsert_strategic_goal",
    }
)

# Modules that own those tools; the drift test compares them to the list above.
_CONTROL_PLANE_MODULES = (
    "civ_mcp.server.tools.belief_tools",
    "civ_mcp.server.tools.world_model",
)


class PlayProfileConflictError(RuntimeError):
    """Raised when an explicit play profile contradicts an explicit belief mode."""


class PlayProfile(StrEnum):
    """Which play loop this process serves.

    ``legacy`` keeps the historical behavior exactly: the Belief Engine modes,
    the governance gates, and the full 112-tool surface. ``lean`` is the
    experimental minimal player: governance-``off``, no control-plane tools, and
    a role prompt that does not ask for proposals or probabilities.

    The two profiles are a *deployment* choice, not a set of composable feature
    flags — combining them can produce states that were never tested, so the
    contradiction between an explicit ``lean`` and an explicit ``observe`` /
    ``enforce`` is rejected instead of resolved silently.
    """

    LEGACY = "legacy"
    LEAN = "lean"

    @classmethod
    def parse(cls, value: str) -> "PlayProfile":
        if not isinstance(value, str):
            raise ValueError(
                f"{PLAY_PROFILE_ENV} must be one of legacy, lean; got {value!r}"
            )
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            raise ValueError(
                f"{PLAY_PROFILE_ENV} must be one of legacy, lean; got {value!r}"
            ) from exc

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "PlayProfile":
        source = os.environ if environ is None else environ
        return cls.parse(source.get(PLAY_PROFILE_ENV, cls.LEGACY.value))

    @property
    def hides_control_plane(self) -> bool:
        """Whether belief/governance tools must leave the model's tool list."""

        return self is PlayProfile.LEAN

    @property
    def requires_reflections(self) -> bool:
        """Whether the five end_turn reflection fields must be non-empty."""

        return self is PlayProfile.LEGACY

    def effective_belief_mode(self, environ: dict[str, str] | None = None) -> BeliefMode:
        """Return the belief mode this profile runs, or raise on a conflict.

        ``lean`` pins the engine off. An *explicit* ``observe``/``enforce`` in
        the environment would otherwise be silently overridden, which is exactly
        the kind of configuration lie this refactor must not introduce.
        """

        source = os.environ if environ is None else environ
        explicit = str(source.get(BELIEF_MODE_ENV, "")).strip()
        if self is PlayProfile.LEGACY:
            return BeliefMode.from_env(source)
        if explicit:
            mode = BeliefMode.parse(explicit)
            if mode is not BeliefMode.OFF:
                raise PlayProfileConflictError(
                    f"{PLAY_PROFILE_ENV}=lean 要求 {BELIEF_MODE_ENV}=off，"
                    f"但环境显式设置为 {mode.value!r}。"
                    "精简游玩与治理 enforce/observe 不能同时生效；"
                    "请显式选择其一："
                    f"{PLAY_PROFILE_ENV}=legacy 保留治理，或取消 {BELIEF_MODE_ENV} 设置。"
                )
        return BeliefMode.OFF


def resolve_play_profile(environ: dict[str, str] | None = None) -> PlayProfile:
    """Resolve and validate the process play profile from the environment."""

    profile = PlayProfile.from_env(environ)
    # Validates the lean/observe/enforce contradiction at startup rather than at
    # the first end_turn.
    profile.effective_belief_mode(environ)
    return profile


def registered_control_plane_tool_names() -> frozenset[str]:
    """Return the control-plane tools currently registered in this process.

    Registry-derived on purpose: the drift test compares this against
    ``LEAN_HIDDEN_CONTROL_TOOLS`` so the two can never disagree.
    """

    return frozenset(
        name
        for name, tool in mcp._tool_manager._tools.items()
        if getattr(getattr(tool, "fn", None), "__module__", "") in _CONTROL_PLANE_MODULES
    )


def hidden_tool_names(profile: PlayProfile) -> frozenset[str]:
    """Return the tool names ``profile`` withholds from the model."""

    return LEAN_HIDDEN_CONTROL_TOOLS if profile.hides_control_plane else frozenset()


@dataclass(frozen=True)
class ToolSurfaceChange:
    """The tools one profile application removed, plus how to put them back."""

    profile: PlayProfile
    removed: tuple[str, ...]

    def restore(self) -> None:
        """Re-register the removed tools (used by tests and profile switching)."""

        for name in self.removed:
            mcp._tool_manager._tools.setdefault(name, _REMOVED_TOOLS[name])


_REMOVED_TOOLS: dict[str, Any] = {}


def apply_play_profile(profile: PlayProfile) -> ToolSurfaceChange:
    """Remove the control-plane tools from the served surface when lean.

    DSH has no MCP tool allowlist (its client registers every tool a server
    advertises), so the only place a tool can be withheld from the model is
    here, before ``tools/list`` is answered. Prompt-only instructions were
    explicitly rejected by the refactor plan: "不要仅在提示词中说'不使用'".
    """

    if not profile.hides_control_plane:
        return ToolSurfaceChange(profile=profile, removed=())
    removed: list[str] = []
    for name in sorted(hidden_tool_names(profile)):
        tool = mcp._tool_manager._tools.get(name)
        if tool is None:
            # Already removed (or never registered in this process); nothing to
            # do. The execution guard still refuses the name.
            continue
        _REMOVED_TOOLS[name] = tool
        mcp._tool_manager.remove_tool(name)
        removed.append(name)
    # Debug, not info: importing the MCP SDK installs a root handler at INFO,
    # so an info line here would print on every `--dry-run` and `check` before
    # their own output. `lifespan` logs the active profile during a real run.
    log.debug(
        "Play profile %s: removed %d control-plane tools from the served surface",
        profile.value,
        len(removed),
    )
    return ToolSurfaceChange(profile=profile, removed=tuple(removed))


def play_profile_summary() -> str:
    """Return a one-line effective-config summary for the launch preview.

    Deliberately short: the preview must be readable at a glance, while
    ``play_profile_report`` stays the complete machine-readable form used by
    ``deepseek_harness check`` and the tests.
    """

    profile = resolve_play_profile()
    mode = profile.effective_belief_mode()
    return (
        f"play_profile={profile.value} belief_mode={mode.value} "
        f"tools={len(mcp._tool_manager._tools)} "
        f"hidden_control_plane={len(hidden_tool_names(profile))} "
        f"reflections_required={'yes' if profile.requires_reflections else 'no'}"
    )


def play_profile_report() -> str:
    """Return the effective play configuration as one JSON object.

    The launch preview (``scripts/civ6_agent --dry-run``), the environment check
    (``scripts/deepseek_harness check``), the MCP child and the ``RUNTIME POLICY``
    block all derive from this one resolution path, so they cannot disagree
    about which profile, belief mode, or tool surface actually applies.
    """

    profile = resolve_play_profile()
    mode = profile.effective_belief_mode()
    served = sorted(mcp._tool_manager._tools)
    hidden = sorted(hidden_tool_names(profile))
    return json.dumps(
        {
            "play_profile": profile.value,
            "belief_mode": mode.value,
            "runtime_policy": mode.runtime_policy(),
            "tool_count": len(served),
            "hidden_tool_count": len(hidden),
            "hidden_tools": hidden,
            "hidden_tools_still_served": sorted(set(hidden) & set(served)),
            "requires_reflections": profile.requires_reflections,
            "control_plane_registered": sorted(registered_control_plane_tool_names()),
        },
        ensure_ascii=False,
    )


@dataclass
class AppContext:
    game: GameState
    logger: GameLogger
    camera: CameraController
    popup_watcher: PopupWatcher
    spatial: SpatialTracker
    map_capture: MapCapture
    watchdog: GameOverWatchdog
    beliefs: BeliefEngine
    belief_mode: BeliefMode = BeliefMode.ENFORCE
    play_profile: PlayProfile = PlayProfile.LEGACY
    auto_resume_ready: asyncio.Event | None = None
    # Serializes the one-time journal replay in pipeline._bind_belief_engine.
    belief_bind_lock: asyncio.Lock | None = None


DSH_AUTO_RESUME_ENV = "CIV_MCP_DSH_AUTO_RESUME"

# 从 FireTuner 可达（启动初期）到主菜单 Lua state 就绪的最长等待秒数。
# 游戏冷启动时 state 列表逐步增长，载档必须等 MainMenu state 真正出现。
_AUTO_RESUME_MAIN_MENU_WAIT_SECONDS = 120


def _dsh_auto_resume_enabled() -> bool:
    """Return whether the DSH-only startup recovery path is explicitly enabled."""

    return os.environ.get(DSH_AUTO_RESUME_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def _wait_for_main_menu_state(conn: GameConnection) -> bool:
    """Wait (bounded) until Civ VI exposes its ``MainMenu`` Lua state.

    The FireTuner state list grows while the game boots; a handshake during
    boot reports GameCore/InGame absent before the menu is actually usable.
    Loading through the FrontEnd API requires the ``MainMenu`` state to
    exist, so waiting here prevents racing a fresh game launch and keeps
    the refusal of unsafe UI fallbacks instead of retrying a doomed load.
    """

    for attempt in range(_AUTO_RESUME_MAIN_MENU_WAIT_SECONDS):
        try:
            if conn.is_connected:
                await conn.reconnect()
            else:
                await conn.connect()
            if "MainMenu" in conn.lua_states.values():
                return True
        except ConnectionError:
            pass
        if attempt % 15 == 0:
            log.info("DSH auto-resume: waiting for main menu (%ds)", attempt)
        await asyncio.sleep(1)
    return False


async def _auto_resume(conn: GameConnection) -> None:
    """Optionally launch Civ 6 and load the latest known recovery save.

    This path is deliberately separate from ``_auto_boot``.  The latter is
    an eval-only flow keyed by ``CIV_MCP_SAVE_FILE`` and clears ``0_MCP_*``
    files before loading its scenario.  DSH recovery must preserve those
    files, use the Civ VI FrontEnd API, and remain disabled by default.

    A successful FireTuner handshake with both ``GameCore_Tuner`` and
    ``InGame`` means the game is already in a playable session, so no action
    is attempted.  A handshake that reaches the main menu (waited for, since
    the state list still grows while the game boots) uses the same native
    FrontEnd/LoadScreen calls as the normal UI, without OCR clicks.
    """

    save_name = game_launcher.get_latest_recovery_save()
    if save_name is None:
        log.warning(
            "DSH auto-resume enabled but no 0_MCP_* or AutoSave_* recovery save "
            "was found; leaving the game untouched"
        )
        return

    # First determine whether the running game is already in a session.  A
    # refused connection is expected when Civ 6 is not running; a reachable
    # tuner with a failed handshake is not safe to take over because another
    # FireTuner client may own the single-client slot.
    try:
        await conn.connect()
    except ConnectionError as exc:
        if game_launcher._is_tuner_port_open():
            log.error(
                "DSH auto-resume cannot establish FireTuner handshake while "
                "port 4318 is reachable; refusing recovery takeover (possible "
                "second client): %s",
                exc,
            )
            return
        log.info("DSH auto-resume: FireTuner unavailable; starting Civ VI")
        try:
            await asyncio.to_thread(game_launcher._launch_game_sync)
            await conn.connect()
        except Exception:
            log.exception("DSH auto-resume: Civ VI launch/front-end connection failed")
            return
    except Exception:
        log.exception("DSH auto-resume: FireTuner probe failed; refusing recovery")
        return
    else:
        if conn.gamecore_index is not None and conn.ingame_index is not None:
            log.info(
                "DSH auto-resume: game already in progress (GameCore=%s, InGame=%s); "
                "skipping GUI recovery",
                conn.gamecore_index,
                conn.ingame_index,
            )
            return
        # GameCore/InGame absent does not prove the menu is usable: the
        # state list still grows while the game boots.  Wait for the
        # MainMenu state before attempting the FrontEnd load.
        if not await _wait_for_main_menu_state(conn):
            log.error(
                "DSH auto-resume: MainMenu Lua state never appeared within %ds; "
                "leaving the game untouched",
                _AUTO_RESUME_MAIN_MENU_WAIT_SECONDS,
            )
            return
        log.info("DSH auto-resume: FireTuner reached the main menu; loading %s", save_name)

    heartbeat.write("loading")
    try:
        # Civ VI's official FrontEnd API starts the save load and enables its
        # own post-load Continue Game handler.  Do not fall back to OCR: an
        # unrelated desktop window can contain the same localized text.
        result = await load_recovery_save_from_frontend(conn, save_name)
    except Exception:
        log.exception("DSH auto-resume: FrontEnd load failed for %s", save_name)
        heartbeat.write("error")
        return
    log.info("DSH auto-resume: FrontEnd load result: %s", result)
    load_failed = result.startswith(("FAILED", "Error:", "No autosaves")) or (
        "not found" in result.lower()
    )
    if load_failed:
        log.error("DSH auto-resume: FrontEnd load did not start: %s", result)
        heartbeat.write("error")
        return

    # Loading a save tears down the old Lua states.  Reconnect until both
    # states are visible; this is the same readiness boundary used by the
    # normal connection path and avoids claiming success from a port alone.
    for attempt in range(90):
        try:
            if conn.is_connected:
                await conn.reconnect()
            else:
                await conn.connect()
            if conn.gamecore_index is not None and conn.ingame_index is not None:
                log.info(
                    "DSH auto-resume: game ready after %ds (GameCore=%s, InGame=%s)",
                    attempt,
                    conn.gamecore_index,
                    conn.ingame_index,
                )
                heartbeat.write("playing")
                return
        except ConnectionError:
            if attempt % 15 == 0:
                log.info("DSH auto-resume: waiting for loaded game (%ds)", attempt)
        await asyncio.sleep(1)

    log.error("DSH auto-resume: loaded save but GameCore/InGame never appeared")
    heartbeat.write("error")
    try:
        await conn.disconnect()
    except Exception:
        log.debug("DSH auto-resume: cleanup disconnect failed", exc_info=True)


async def _auto_resume_then_start_services(
    conn: GameConnection,
    camera: CameraController,
    popup_watcher: PopupWatcher,
    watchdog: GameOverWatchdog,
    ready: asyncio.Event | None = None,
) -> None:
    """Run startup recovery before starting background FireTuner users.

    Camera tracking and popup/game-over watchers share the same connection
    with recovery.  They must remain stopped while OCR navigation and the
    post-load reconnect are in progress.  This coroutine is scheduled after
    the lifespan yields, so MCP ``tools/list`` can complete while the GUI is
    still loading the game.
    """

    try:
        try:
            await _auto_resume(conn)
        except asyncio.CancelledError:
            # Do not start any watcher after a shutdown cancellation.
            raise
        except Exception:
            log.exception("DSH auto-resume task failed")
        camera.start()
        popup_watcher.start()
        watchdog.start()
    finally:
        if ready is not None:
            ready.set()


async def _auto_boot(conn: GameConnection, save_name: str) -> None:
    """Launch game and load a save before MCP tools become available.

    Called during lifespan when CIV_MCP_SAVE_FILE is set (eval mode).
    Blocks until the game is loaded and ready for play.
    """
    import glob

    from civ_mcp.game_lifecycle import load_game_save

    # 0. Clear stale MCP autosaves. These are the saves that the main
    # menu's "Continue Game" button would load. If a previous run
    # crashed at T197, "Continue Game" resumes T197 instead of loading
    # the scenario save. Clearing them makes "Continue Game" harmless
    # (it would load the scenario save or nothing).
    # This does NOT break --resume-save which loads by name via Lua.
    stale = glob.glob(os.path.join(game_launcher.SINGLE_SAVE_DIR, "0_MCP_*.Civ6Save"))
    if stale:
        for f in stale:
            try:
                os.remove(f)
            except OSError:
                pass
        log.info("Auto-boot: cleared %d stale MCP autosave(s)", len(stale))

    # 1. Launch game (or reuse if already running).
    # The eval runner's ensure_game_ready() typically launches the game
    # before the MCP server starts. _launch_game_sync() detects an
    # already-running game and returns immediately, avoiding a wasteful
    # kill + relaunch cycle through the Aspyr launcher.
    # The step-5 verification below catches wrong-save scenarios as a
    # safety net (the Lua load path fails when mid-session, not from
    # main menu).
    heartbeat.write("launching")
    log.info("Auto-boot: launching game...")
    result = await asyncio.to_thread(game_launcher._launch_game_sync)
    log.info("Auto-boot: launch result: %s", result)

    # 2. Connect to FireTuner (retry — game takes time to start)
    for attempt in range(90):
        try:
            await conn.connect()
            log.info("Auto-boot: connected to FireTuner")
            heartbeat.write("connecting")
            break
        except ConnectionError:
            if attempt % 10 == 0:
                log.info("Auto-boot: waiting for FireTuner... (%ds)", attempt)
            await asyncio.sleep(1)
    else:
        log.error("Auto-boot: could not connect to FireTuner after 90s")
        heartbeat.write("error")
        return

    # 2b. Verify Lua states exist (port can open before game initialises).
    # A hung splash screen ("Loading, Please Wait...") has port open but
    # GameCore never appears. Skip this check — the main menu legitimately
    # has no GameCore on any platform; it only appears after a save is
    # loaded (step 3). The splash hang detection was causing false kills
    # when autosaves were cleaned (game stays at main menu, no GameCore).
    if conn.gamecore_index is None and False:  # disabled — see comment above
        log.warning(
            "Auto-boot: FireTuner connected but GameCore not found "
            "— game may be hung at splash screen"
        )
        for retry in range(30):
            await asyncio.sleep(2)
            try:
                await conn.reconnect()
                if conn.gamecore_index is not None:
                    log.info("Auto-boot: GameCore found after %ds", (retry + 1) * 2)
                    break
            except ConnectionError:
                pass
        else:
            log.error("Auto-boot: GameCore never appeared — killing hung game")
            heartbeat.write("error")
            await asyncio.to_thread(game_launcher._kill_game_sync)
            await asyncio.sleep(5)
            result = await asyncio.to_thread(game_launcher._launch_game_sync)
            log.info("Auto-boot: relaunched after hung splash: %s", result)
            for attempt in range(90):
                try:
                    await conn.connect()
                    if conn.gamecore_index is not None:
                        log.info("Auto-boot: GameCore found on relaunch")
                        break
                except ConnectionError:
                    pass
                await asyncio.sleep(1)
            if conn.gamecore_index is None:
                log.error("Auto-boot: relaunch also failed — giving up")
                heartbeat.write("error")
                return

    # 3. Load save (Lua on Windows/macOS, OCR menu nav on Linux)
    log.info("Auto-boot: loading save '%s'...", save_name)
    result = await load_game_save(conn, save_name)
    log.info("Auto-boot: load result: %s", result)
    heartbeat.write("loading")

    # 4. Wait for save to load, click through leader intro, then reconnect.
    # The CONTINUE GAME button on the leader screen has low-contrast
    # teal-on-teal text that OCR often misses — fall back to positional
    # click grid if OCR fails. Verify the click actually worked by
    # checking for Lua states (only available once in-game, not on leader
    # screen).
    log.info("Auto-boot: waiting 15s for save to load...")
    await asyncio.sleep(15)
    clicked = await asyncio.to_thread(
        lambda: game_launcher._click_text("CONTINUE", timeout=105, post_delay=1),
    )
    if clicked:
        log.info("Auto-boot: clicked CONTINUE GAME via OCR")
    else:
        log.warning("Auto-boot: OCR missed CONTINUE — using positional click grid")
        await asyncio.to_thread(game_launcher._click_continue_positional)

    # Verify the click worked — Lua states only appear once past the
    # leader screen into gameplay. Retry positional click if needed.
    await asyncio.sleep(3)
    game_ready = False
    for attempt in range(45):
        try:
            await conn.reconnect()
            if conn.gamecore_index is not None:
                log.info("Auto-boot: game ready (GameCore=%s)", conn.gamecore_index)
                heartbeat.write("playing")  # turn unknown until first end_turn
                game_ready = True
                break
        except ConnectionError:
            pass
        # Retry positional click every 10s in case the first click missed
        if attempt > 0 and attempt % 10 == 0:
            heartbeat.write("loading")  # keep heartbeat fresh during retry
            if not clicked:
                log.info("Auto-boot: retrying positional click (attempt %d)", attempt)
                await asyncio.to_thread(game_launcher._click_continue_positional)
        await asyncio.sleep(1)
    if not game_ready:
        log.warning("Auto-boot: save may not have loaded — GameCore not found")
        heartbeat.write("error")
        return

    # 5. Verify correct save loaded. If the wrong save loaded (e.g.
    # main-menu "Continue Game" loaded a stale autosave instead of the
    # scenario save), reload the correct one via Lua — no OCR needed.
    try:
        verify = await conn.execute_read(
            "local t = Game.GetCurrentGameTurn(); "
            'print("VERIFY|" .. t); '
            'print("---END---")'
        )
        for line in verify:
            if line.startswith("VERIFY|"):
                turn = int(line.split("|")[1])
                if turn > 5:
                    log.error(
                        "Auto-boot: loaded T%d but expected T1 — wrong save! "
                        "Reloading '%s' via Lua",
                        turn,
                        save_name,
                    )
                    # Retry via Lua (Network.LoadGame) — bypasses OCR entirely
                    result = await load_game_save(conn, save_name)
                    log.info("Auto-boot: Lua reload result: %s", result)
                    await asyncio.sleep(15)
                    # Click CONTINUE again for the leader screen
                    await asyncio.to_thread(game_launcher._click_continue_positional)
                    await asyncio.sleep(5)
                    for retry in range(30):
                        try:
                            await conn.reconnect()
                            if conn.gamecore_index is not None:
                                break
                        except ConnectionError:
                            pass
                        await asyncio.sleep(1)
                    # Verify again
                    try:
                        verify2 = await conn.execute_read(
                            "local t = Game.GetCurrentGameTurn(); "
                            'print("VERIFY|" .. t); '
                            'print("---END---")'
                        )
                        for line2 in verify2:
                            if line2.startswith("VERIFY|"):
                                t2 = int(line2.split("|")[1])
                                if t2 > 5:
                                    log.error(
                                        "Auto-boot: Lua reload also loaded T%d "
                                        "— falling back to kill + OCR",
                                        t2,
                                    )
                                    await game_launcher.kill_game()
                                    r = await asyncio.to_thread(
                                        game_launcher._launch_game_sync
                                    )
                                    log.info("Auto-boot: relaunch: %s", r)
                                    r = await asyncio.to_thread(
                                        game_launcher._navigate_to_save_sync,
                                        save_name,
                                        None,
                                    )
                                    log.info("Auto-boot: OCR nav: %s", r)
                                    for a in range(30):
                                        try:
                                            await conn.reconnect()
                                            if conn.gamecore_index is not None:
                                                return
                                        except ConnectionError:
                                            pass
                                        await asyncio.sleep(1)
                                    log.warning("Auto-boot: all fallbacks failed")
                                    return
                                log.info("Auto-boot: Lua reload verified at T%d", t2)
                                heartbeat.write("playing", turn=t2)
                    except Exception:
                        log.debug("Auto-boot: post-reload verify failed", exc_info=True)
                    return
                log.info("Auto-boot: verified save at T%d", turn)
                heartbeat.write("playing", turn=turn)
    except Exception:
        log.debug("Auto-boot: save verification failed", exc_info=True)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    conn = GameConnection()

    # Telemetry emitter — routes events to local JSONL + optional cloud sink
    emitter = TelemetryEmitter()
    emitter.add_sink(LocalSink())
    cloud_bucket = os.environ.get("CIV_MCP_TELEMETRY_BUCKET")
    if cloud_bucket:
        emitter.add_sink(CloudSink(cloud_bucket))
    alert_webhook = os.environ.get("CIV_MCP_ALERT_WEBHOOK")
    if alert_webhook:
        emitter.add_sink(AlertSink(alert_webhook))
    emitter.start()
    heartbeat.init(emitter.run_id)
    # Bind eval identity so the orchestrator can match running games to jobs
    eval_model = os.environ.get("CIV_MCP_AGENT_MODEL", "")
    eval_metadata = os.environ.get("CIV_MCP_METADATA", "")
    eval_scenario = ""
    if eval_metadata:
        try:
            eval_scenario = json.loads(eval_metadata).get("scenario_id", "")
        except Exception:
            pass
    heartbeat.bind_eval(eval_model, eval_scenario)
    heartbeat.write("starting")

    logger = GameLogger(emitter)
    spatial = SpatialTracker(emitter)
    map_capture = MapCapture(emitter)
    gs = GameState(conn)
    beliefs = BeliefEngine(run_id=emitter.run_id)
    play_profile = resolve_play_profile()
    belief_mode = play_profile.effective_belief_mode()
    log.info("Game logger session: %s", logger.session_id)
    log.info("Play profile: %s", play_profile.value)
    log.info("Belief Engine mode: %s", belief_mode.value)

    camera = CameraController(conn)
    popup_watcher = PopupWatcher(conn)
    watchdog = GameOverWatchdog(gs, logger)
    auto_resume_task: asyncio.Task[None] | None = None
    auto_resume_ready = asyncio.Event()

    # Auto-boot: launch game + load save when running as eval
    save_file = os.environ.get("CIV_MCP_SAVE_FILE")
    if save_file:
        # Eval mode loads the scenario save inline, so by the time _auto_boot
        # returns no background recovery owns the connection. Release the tool
        # gate that pipeline._logged awaits first: leaving it unset makes every
        # gate-passing tool wait forever on an event nobody will ever set.
        await _auto_boot(conn, save_file)
        auto_resume_ready.set()
    elif _dsh_auto_resume_enabled():
        # DSH recovery is an explicit opt-in and intentionally does not reuse
        # _auto_boot: that eval-only path deletes 0_MCP_* saves before loading.
        # Schedule it after the first lifespan yield below.  GUI OCR can take
        # minutes, while DSH must finish MCP tools/list within its startup
        # timeout.  Watchers start only after recovery finishes.
        auto_resume_task = asyncio.create_task(
            _auto_resume_then_start_services(
                conn,
                camera,
                popup_watcher,
                watchdog,
                auto_resume_ready,
            )
        )
    else:
        # Spectator-mode background services (camera tracking + popup
        # auto-dismiss) are safe to start immediately when no recovery owns
        # the connection.
        camera.start()
        popup_watcher.start()
        watchdog.start()
        auto_resume_ready.set()

    # Start the web dashboard API as a background task (port 8000)
    web_app = create_app(gs)
    uvi_config = uvicorn.Config(web_app, host="0.0.0.0", port=8000, log_level="info")
    uvi_server = uvicorn.Server(uvi_config)
    api_task = asyncio.create_task(uvi_server.serve())
    log.info("Web API starting on http://0.0.0.0:8000")

    try:
        yield AppContext(
            game=gs,
            logger=logger,
            camera=camera,
            popup_watcher=popup_watcher,
            spatial=spatial,
            map_capture=map_capture,
            watchdog=watchdog,
            beliefs=beliefs,
            belief_mode=belief_mode,
            play_profile=play_profile,
            auto_resume_ready=auto_resume_ready,
            belief_bind_lock=asyncio.Lock(),
        )
    finally:
        if auto_resume_task is not None:
            auto_resume_task.cancel()
            try:
                await auto_resume_task
            except asyncio.CancelledError:
                log.info("DSH auto-resume task cancelled during shutdown")
            auto_resume_ready.set()
        await watchdog.stop()
        await camera.stop()
        await popup_watcher.stop()
        await emitter.close()
        uvi_server.should_exit = True
        try:
            await api_task
        except asyncio.CancelledError:
            # Host shutdown cancels background tasks before lifespan cleanup.
            # Continue so the FireTuner socket is closed deliberately below.
            pass
        await conn.disconnect()


mcp = FastMCP(
    "Civilization VI",
    instructions=(
        "Read game state and issue commands to a running Civ 6 game. Start every "
        "turn with get_game_overview and follow its machine-readable RUNTIME POLICY. "
        "The server's reported policy, action gates, game rules, and end-turn "
        "blockers are authoritative."
    ),
    lifespan=lifespan,
)

def main():
    """Entry point for the MCP server."""
    import signal

    logging.basicConfig(level=logging.INFO)

    # Remap SIGTERM → SIGINT so asyncio's existing SIGINT handler triggers a
    # graceful shutdown (cancels all tasks → lifespan finally block runs →
    # conn.disconnect() closes the FireTuner TCP connection cleanly).
    # Without this, SIGTERM kills the process immediately, leaving the game
    # with an abrupt TCP RST which can cause it to crash.
    # SIGTERM is not available on Windows, so skip the remap there.
    if hasattr(signal, "SIGTERM"):
        signal.signal(
            signal.SIGTERM, lambda sig, frame: os.kill(os.getpid(), signal.SIGINT)
        )

    if os.environ.get("CIV_MCP_DISABLE_LUA"):
        mcp._tool_manager.remove_tool("run_lua")

    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        # SIGTERM is intentionally remapped to SIGINT above so FastMCP runs
        # lifespan cleanup. Treat the resulting interrupt as a normal stop.
        pass

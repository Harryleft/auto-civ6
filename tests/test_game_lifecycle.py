"""Unit tests for `civ_mcp.game_lifecycle` and the process-lifecycle
orchestrators it delegates to in `civ_mcp.game_launcher`.

The connection is a `FakeConn` that records every Lua chunk and replays
scripted response lines, so the tests can assert on the generated Lua itself
(save names, indexes, popup names) without a live game. Process and
filesystem seams (`game_launcher` module attributes, `asyncio.sleep`) are
monkeypatched — no game is launched and no real save directory is touched.

"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from civ_mcp import checkpoints
from civ_mcp import game_launcher
from civ_mcp import game_lifecycle as gl

run = asyncio.run


@pytest.fixture(autouse=True)
def isolated_lifecycle(monkeypatch, tmp_path):
    """Prevent process/transport access and keep every scan in a temp tree."""
    def external_call(*args, **kwargs):
        raise AssertionError("Test attempted an external process or socket call")

    monkeypatch.setattr(subprocess, "run", external_call)
    monkeypatch.setattr(subprocess, "Popen", external_call)
    real_socket = socket.socket

    def local_socket(family=socket.AF_INET, *args, **kwargs):
        # asyncio uses an AF_UNIX socketpair for its internal wake-up pipe.
        if family == socket.AF_UNIX:
            return real_socket(family, *args, **kwargs)
        return external_call(family, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", local_socket)
    auto = tmp_path / "Single" / "auto"
    auto.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(game_launcher, "SAVE_DIR", str(auto))
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(auto.parent))
    monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "records")
    assert checkpoints.begin_baseline_run() is None
    no_crash_dialogs(monkeypatch)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


async def _no_sleep(_seconds):
    return None


class FakeConn:
    """Stands in for GameConnection.

    `on_write(lua) -> list[str]` and `on_state(idx, lua) -> list[str]` are
    handlers that script the game's answers; raising from them simulates a
    dead socket / Lua error. Every chunk is recorded for assertion.
    """

    def __init__(
        self,
        *,
        gamecore_index=0,
        ingame_index=1,
        lua_states=None,
        on_write=None,
        on_state=None,
        on_read=None,
    ):
        self.gamecore_index = gamecore_index
        self.ingame_index = ingame_index
        self.lua_states = dict(lua_states or {})
        self.on_write = on_write
        self.on_state = on_state
        self.on_read = on_read
        self.native_completion = True
        self.writes: list[str] = []
        self.state_calls: list[tuple[int, str]] = []
        self.reads: list[str] = []
        self.discoveries = 0
        self.discovered_states = None

    @property
    def load_calls(self):
        return [(idx, code) for idx, code in self.state_calls if "Network.LoadGame" in code]

    async def rediscover_states(self, timeout=5.0):
        self.discoveries += 1
        if self.discovered_states is not None:
            self.lua_states = self.discovered_states

    async def ensure_connected(self):
        """No-op — the fake is always 'connected'; tests set indexes directly."""

    async def execute_write(self, lua, timeout=5.0):
        self.writes.append(lua)
        if "MCP_SAVE_COMPLETION_CLEANUP" in lua:
            return []
        if "MCP_SAVE_COMPLETION_STATUS" in lua:
            done = self.native_completion() if callable(self.native_completion) else self.native_completion
            return ["MCP_SAVE_COMPLETE|" + str(done).lower()]
        if self.on_write is None:
            return []
        return self.on_write(lua)

    async def execute_in_state(self, state_index, lua, timeout=5.0):
        self.state_calls.append((state_index, lua))
        if "MCP_SAVE_QUERY_START" in lua:
            return ["MCP_SAVE_QUERY_STARTED"]
        if "MCP_SAVE_QUERY_STATUS" in lua:
            return ["MCP_SAVE_QUERY|" + getattr(self, "query_status", "READY")]
        if self.on_state is None:
            return []
        return self.on_state(state_index, lua)

    async def execute_read(self, lua, timeout=5.0):
        self.reads.append(lua)
        if self.on_read is None:
            return []
        return self.on_read(lua)


def dismiss_router(*, phase1=(), precheck=(), phase3=()):
    """Route overlay and complete information visibility responses."""

    def on_write(lua):
        if "INFORMATION_POPUPS" in lua:
            return list(precheck) + ["INFORMATION_POPUPS|DONE"]
        return list(phase1)

    return on_write


def no_crash_dialogs(monkeypatch):
    async def fake():
        return []

    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", fake)


# ---------------------------------------------------------------------------
# save_game
# ---------------------------------------------------------------------------


class TestSaveGame:
    @pytest.fixture(autouse=True)
    def fast_confirmation(self, monkeypatch):
        monkeypatch.setattr(gl, "SAVE_CONFIRM_TIMEOUT", 0.03)
        monkeypatch.setattr(gl, "SAVE_POLL_INTERVAL", 0.001)

    def test_partial_file_is_not_confirmed_until_native_save_complete(self):
        async def go():
            path = Path(game_launcher.SINGLE_SAVE_DIR) / "checkpoint.Civ6Save"
            complete = False
            async def finish():
                nonlocal complete
                await asyncio.sleep(0.012)
                path.write_bytes(b"completed save")
                complete = True
            def respond(lua):
                if "Network.SaveGame" in lua:
                    path.write_bytes(b"part")
                    asyncio.create_task(finish())
                    return ["MCP_SAVE|true"]
                return ["MCP_SAVE_COMPLETE|" + str(complete).lower()]
            conn = FakeConn(on_write=respond)
            conn.native_completion = lambda: complete
            result = await gl.save_game(conn, "checkpoint")
            return result, complete
        result, complete = run(go())
        assert complete
        assert "(14 bytes)" in result

    def test_success_requires_a_fresh_nonempty_file(self):
        def write(lua):
            path = Path(game_launcher.SINGLE_SAVE_DIR) / "checkpoint.Civ6Save"
            path.write_bytes(b"new save")
            return ["MCP_SAVE|true"]

        async def go():
            conn = FakeConn(on_write=write)
            return conn, await gl.save_game(conn, "checkpoint.Civ6Save")

        conn, result = run(go())
        assert result == "Saved: checkpoint; native completion and file confirmed (8 bytes)."
        assert sum("Network.SaveGame" in code for code in conn.writes) == 1
        assert 'gf.Name = "checkpoint"' in conn.writes[0]
        assert "SaveLocations.LOCAL_STORAGE" in conn.writes[0]
        assert "SaveTypes.SINGLE_PLAYER" in conn.writes[0]
        assert "IsAutosave = false" in conn.writes[0]

    def test_acknowledgment_does_not_confirm_an_old_file(self):
        path = Path(game_launcher.SINGLE_SAVE_DIR) / "checkpoint.Civ6Save"
        path.write_bytes(b"old save")
        conn = FakeConn(on_write=lambda lua: ["MCP_SAVE|true"])
        result = run(gl.save_game(conn, "checkpoint"))
        assert result.startswith("ERR:OUTCOME_UNKNOWN")
        assert sum("Network.SaveGame" in code for code in conn.writes) == 1
        assert path.read_bytes() == b"old save"

    def test_lost_response_can_be_confirmed_without_resending(self):
        def write(lua):
            (Path(game_launcher.SINGLE_SAVE_DIR) / "checkpoint.Civ6Save").write_bytes(b"save")
            raise ConnectionError("response lost")
        conn = FakeConn(on_write=write)
        assert run(gl.save_game(conn, "checkpoint")).startswith("Saved:")
        assert sum("Network.SaveGame" in code for code in conn.writes) == 1

    def test_waits_for_nonempty_file_after_submission(self):
        async def go():
            path = Path(game_launcher.SINGLE_SAVE_DIR) / "checkpoint.Civ6Save"
            async def finish():
                await asyncio.sleep(0.005)
                path.write_bytes(b"finished")
            def write(lua):
                path.touch()
                asyncio.create_task(finish())
                return ["MCP_SAVE|true"]
            return await gl.save_game(FakeConn(on_write=write), "checkpoint")
        assert "(8 bytes)" in run(go())

    def test_fresh_file_without_native_completion_remains_unknown(self):
        def write(lua):
            (Path(game_launcher.SINGLE_SAVE_DIR) / "checkpoint.Civ6Save").write_bytes(b"part")
            return ["MCP_SAVE|true"]
        conn = FakeConn(on_write=write)
        conn.native_completion = False
        assert run(gl.save_game(conn, "checkpoint")).startswith("ERR:OUTCOME_UNKNOWN")
        assert sum("Network.SaveGame" in code for code in conn.writes) == 1

    def test_checkpoint_record_survives_session_and_rejects_changed_file(self):
        path = Path(game_launcher.SINGLE_SAVE_DIR) / "checkpoint.Civ6Save"
        def write(lua):
            path.write_bytes(b"native completed")
            return ["MCP_SAVE|true"]
        assert run(gl.save_game(FakeConn(on_write=write), "checkpoint")).startswith("Saved:")
        assert checkpoints.record_checkpoint("checkpoint", ("sumeria", 719665280), 21) is None
        assert checkpoints.expected_checkpoint("checkpoint") == ("sumeria", 719665280, 21)
        path.write_bytes(b"different later file")
        assert checkpoints.expected_checkpoint("checkpoint") is None

    def test_unknown_overwrite_invalidates_prior_recovery_record(self):
        path = Path(game_launcher.SINGLE_SAVE_DIR) / "checkpoint.Civ6Save"
        def write(lua):
            path.write_bytes(b"native completed")
            return ["MCP_SAVE|true"]
        run(gl.save_game(FakeConn(on_write=write), "checkpoint"))
        assert checkpoints.record_checkpoint("checkpoint", ("sumeria", 719665280), 21) is None
        result = run(gl.save_game(FakeConn(on_write=lambda code: ["MCP_SAVE|true"]), "checkpoint"))
        assert result.startswith("ERR:OUTCOME_UNKNOWN")
        assert checkpoints.expected_checkpoint("checkpoint") is None

    def test_only_exact_archived_baseline_has_trusted_original_identity(self):
        archived = Path(__file__).resolve().parents[1] / "fixtures/saves/benchmark_turn_1.Civ6Save"
        target = Path(game_launcher.SINGLE_SAVE_DIR) / archived.name
        target.write_bytes(archived.read_bytes())
        assert checkpoints.expected_checkpoint("benchmark_turn_1") == ("sumeria", 719665280, 1)
        target.write_bytes(b"different map using the same name")
        assert checkpoints.expected_checkpoint("benchmark_turn_1") is None

    @pytest.mark.parametrize("name", ["benchmark_turn_1", "benchmark_turn_1.Civ6Save", "BENCHMARK_TURN_1", "benchmark_turn_1 "])
    def test_original_baseline_cannot_be_overwritten(self, name):
        conn = FakeConn()
        assert run(gl.save_game(conn, name)).startswith("Error:")
        assert conn.writes == []

    def test_explicit_rejection_is_not_reported_as_saved(self):
        conn = FakeConn(on_write=lambda lua: ["MCP_SAVE|false"])
        assert run(gl.save_game(conn, "checkpoint")).startswith("Error: Civ VI rejected")
        assert sum("Network.SaveGame" in code for code in conn.writes) == 1

    @pytest.mark.parametrize("name", ["", "a/b", "a\\b", "bad\x01name", ".", "..", ".Civ6Save"])
    def test_invalid_name_never_reaches_game(self, name):
        conn = FakeConn()
        assert run(gl.save_game(conn, name)).startswith("Error:")
        assert conn.writes == []

    def test_save_name_cannot_inject_lua(self):
        conn = FakeConn(on_write=lambda lua: ["MCP_SAVE|false"])
        run(gl.save_game(conn, 'evil"; print("pwned"); --'))
        assert 'gf.Name = "evil\\"; print(\\"pwned\\"); --"' in conn.writes[0]
        assert 'gf.Name = "evil"; print("pwned"); --"' not in conn.writes[0]


# ---------------------------------------------------------------------------
# load_save (index-based)
# ---------------------------------------------------------------------------


class TestLoadSave:
    def test_reports_missing_cache_before_any_list_query(self):
        async def go():
            conn = FakeConn(on_write=lambda lua: ["ERR:NO_SAVE_LIST"])
            return await gl.load_save(conn, 1)

        assert "No save list cached" in run(go())

    def test_reports_index_out_of_range_with_the_list_size(self):
        async def go():
            conn = FakeConn(on_write=lambda lua: ["ERR:INDEX_OUT_OF_RANGE|3"])
            return await gl.load_save(conn, 9)

        assert "out of range (1-3)" in run(go())

    def test_confirms_loading_and_interpolates_the_index(self):
        async def go():
            conn = FakeConn(on_write=lambda lua: ["LOADING|AutoSave_0221"])
            result = await gl.load_save(conn, 7)
            return conn, result

        conn, result = run(go())
        assert result.startswith("Loading save: AutoSave_0221")
        assert "local idx = 7;" in conn.writes[0]
        assert "Network.LeaveGame()" in conn.writes[0]

    def test_unrecognised_response_falls_back_to_a_generic_message(self):
        async def go():
            conn = FakeConn(on_write=lambda lua: ["garbage"])
            return await gl.load_save(conn, 1)

        assert run(go()) == "Load command sent. Wait for game to reload."


# ---------------------------------------------------------------------------
# list_saves — filesystem scan first, Lua query as fallback
# ---------------------------------------------------------------------------


@pytest.fixture
def save_dirs(tmp_path, monkeypatch):
    """Point both save-dir constants at an empty tmp tree.

    SAVE_DIR is the autosave subdir; its parent holds regular saves.
    """
    base = tmp_path / "Single"
    auto = base / "auto"
    auto.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(game_launcher, "SAVE_DIR", str(auto))
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(base))
    return base, auto


@pytest.fixture(autouse=True)
def _reset_filesystem_save_cache():
    """The filesystem listing caches names module-level for load_save(index);
    keep tests isolated from each other."""
    gl._filesystem_save_names.clear()
    yield
    gl._filesystem_save_names.clear()


def _touch(path, mtime):
    path.write_text("x")
    os.utime(path, (mtime, mtime))


class TestListSaves:
    def test_filesystem_scan_wins_and_sorts_newest_first(self, save_dirs):
        base, auto = save_dirs
        _touch(auto / "AutoSave_0001.Civ6Save", 1000)
        _touch(auto / "AutoSave_0002.Civ6Save", 3000)
        _touch(base / "MyGame.Civ6Save", 2000)

        async def go():
            # Must not be consulted — the filesystem already found saves.
            def on_write(lua):
                raise AssertionError("Lua fallback should not run")

            return await gl.list_saves(FakeConn(on_write=on_write))

        out = run(go())
        assert out.startswith("Available saves (filesystem scan, sorted by date):")
        lines = out.splitlines()[1:]
        assert lines == [
            "  1. AutoSave_0002",
            "  2. MyGame",
            "  3. AutoSave_0001",
        ]

    def test_filesystem_listing_is_capped_at_25_entries(self, save_dirs):
        base, auto = save_dirs
        for i in range(30):
            _touch(auto / f"AutoSave_{i:04d}.Civ6Save", 1000 + i)

        out = run(gl.list_saves(FakeConn()))
        assert len(out.splitlines()) == 1 + 25

    def test_falls_back_to_lua_query_when_disk_is_empty(
        self, save_dirs, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        def on_write(lua):
            if "MCPSaveQueryDone = false" in lua:
                return ["QUERY_SENT"]
            if "MCPSaveQueryDone then" in lua:
                return ["COUNT|2", "SAVE|1|FirstSave", "SAVE|2|SecondSave"]
            raise AssertionError(f"unexpected lua: {lua}")

        out = run(gl.list_saves(FakeConn(on_write=on_write)))
        assert out.splitlines() == [
            "Available saves (use load_save with the index number):",
            "  1. FirstSave",
            "  2. SecondSave",
        ]

    def test_empty_lua_result_returns_the_filesystem_message(
        self, save_dirs, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        def on_write(lua):
            if "MCPSaveQueryDone = false" in lua:
                return ["QUERY_SENT"]
            return ["EMPTY"]

        out = run(gl.list_saves(FakeConn(on_write=on_write)))
        assert out == "No saves found on filesystem."

    def test_lua_query_that_never_completes_times_out_to_filesystem_message(
        self, save_dirs, monkeypatch
    ):
        """Pins the 20-poll timeout: PENDING forever is treated as no result."""
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        def on_write(lua):
            if "MCPSaveQueryDone = false" in lua:
                return ["QUERY_SENT"]
            return ["PENDING"]

        out = run(gl.list_saves(FakeConn(on_write=on_write)))
        assert out == "No saves found on filesystem."

    def test_connection_error_propagates_instead_of_masquerading_as_empty(
        self, save_dirs
    ):
        """A dead connection is a transport failure, not an empty save dir —
        it must propagate instead of becoming 'No saves found'."""

        def on_write(lua):
            raise ConnectionError("socket dead")

        with pytest.raises(ConnectionError):
            run(gl.list_saves(FakeConn(on_write=on_write)))

    def test_other_lua_errors_still_fall_back_to_the_filesystem_message(
        self, save_dirs
    ):
        """Non-transport errors keep the old behaviour: swallow and report
        the filesystem result."""

        def on_write(lua):
            raise RuntimeError("lua blew up")

        out = run(gl.list_saves(FakeConn(on_write=on_write)))
        assert out == "No saves found on filesystem."


class TestLoadSaveFilesystemFallback:
    """A filesystem listing is cached so load_save(index) works even when
    the Lua MCPSaveList cache was never populated."""

    def test_filesystem_listing_is_cached_for_load_save(
        self, save_dirs, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        monkeypatch.setattr(sys, "platform", "darwin")
        base, auto = save_dirs
        _touch(auto / "AutoSave_0001.Civ6Save", 1000)
        _touch(base / "MyGame.Civ6Save", 2000)

        def on_write(lua):
            return ["ERR:NO_SAVE_LIST"]  # load_save's Lua cache check

        async def go():
            conn = menu_conn(on_write=on_write, on_state=frontend_ok)
            listing = await gl.list_saves(conn)
            result = await gl.load_save(conn, 1)
            return conn, listing, result

        conn, listing, result = run(go())
        assert listing.startswith("Available saves (filesystem scan")
        # Index 1 = newest = MyGame; loaded by name via the FrontEnd loader.
        assert result.startswith("Loading save MyGame")
        ((_, lua),) = conn.load_calls
        assert "local loadGame = q.save" in lua
        assert 'local wanted = "MyGame.Civ6Save";' in conn.state_calls[0][1]

    def test_out_of_range_index_against_the_filesystem_cache(self, save_dirs):
        base, _auto = save_dirs
        _touch(base / "Only.Civ6Save", 1000)

        async def go():
            conn = FakeConn(on_write=lambda lua: ["ERR:NO_SAVE_LIST"])
            await gl.list_saves(conn)
            return await gl.load_save(conn, 5)

        assert "out of range (1-1)" in run(go())


# ---------------------------------------------------------------------------
# load_game_save — native FrontEnd API from the MainMenu state (no OCR)
# ---------------------------------------------------------------------------


def menu_conn(**kwargs):
    """A FakeConn sitting at the main menu: no GameCore/InGame, MainMenu at 24."""
    kwargs.setdefault("gamecore_index", None)
    kwargs.setdefault("ingame_index", None)
    kwargs.setdefault("lua_states", {24: "MainMenu"})
    return FakeConn(**kwargs)


def frontend_ok(idx, lua):
    return ["MCP_FRONTEND_AUTOSTART|true", "MCP_FRONTEND_LOAD|true"]


class TestLoadGameSave:
    """load_game_save is intentionally main-menu-only: it drives Civ VI's
    native FrontEnd API (Network.LoadGame + Automation.SetAutoStartEnabled)
    from the MainMenu Lua state — never OCR, never an in-game reload."""

    def test_in_game_load_is_refused_without_touching_the_game(self):
        """An in-game load would invalidate the FireTuner world; refuse it
        explicitly instead of silently restarting or navigating the GUI."""

        async def go():
            conn = FakeConn()  # gamecore_index=0 — a game is loaded
            return conn, await gl.load_game_save(conn, "0_MCP_0042")

        conn, result = run(go())
        assert "a game is already loaded" in result
        assert conn.state_calls == []
        assert conn.writes == []

    def test_invalid_save_names_are_rejected_before_any_lua(self):
        async def go():
            conn = menu_conn()
            results = [
                await gl.load_game_save(conn, ""),
                await gl.load_game_save(conn, "a/b"),
                await gl.load_game_save(conn, "bad\x01name"),
            ]
            return conn, results

        conn, results = run(go())
        assert all(r.startswith("Error:") for r in results)
        assert conn.state_calls == []

    def test_missing_mainmenu_state_is_an_error_not_an_ocr_fallback(self):
        async def go():
            conn = menu_conn(lua_states={})
            return await gl.load_game_save(conn, "0_MCP_0042")

        assert "MainMenu Lua state is unavailable" in run(go())

    def test_frontend_load_succeeds_via_mainmenu_state(self):
        """The happy path: load + auto-start accepted in the MainMenu state —
        sent exactly once, including when the response is lost."""

        async def go():
            conn = menu_conn(on_state=frontend_ok)
            return conn, await gl.load_game_save(conn, "0_MCP_0042")

        conn, result = run(go())
        assert result.startswith("Loading save 0_MCP_0042")
        ((idx, lua),) = conn.load_calls
        assert idx == 24
        assert "Network.LoadGame(loadGame, ServerType.SERVER_TYPE_NONE)" in lua
        assert "Automation.SetAutoStartEnabled(true)" in lua
        assert len(conn.load_calls) == 1  # never resent mid-load

    @pytest.mark.parametrize("status", ["MISSING", "AMBIGUOUS"])
    def test_unresolved_native_entry_never_loads_another_save(self, status):
        async def go():
            conn = menu_conn(on_state=frontend_ok)
            conn.query_status = status
            result = await gl.load_game_save(conn, "benchmark_turn_1")
            return conn, result
        conn, result = run(go())
        assert result.startswith("Error:")
        assert "no load sent" in result
        assert conn.load_calls == []

    def test_civ6_rejecting_the_save_is_an_error(self):
        async def go():
            conn = menu_conn(
                on_state=lambda idx, lua: [
                    "MCP_FRONTEND_AUTOSTART|true",
                    "MCP_FRONTEND_LOAD|false",
                ]
            )
            return await gl.load_game_save(conn, "ghost")

        assert "Civ VI rejected save" in run(go())

    def test_no_confirmation_means_outcome_unknown(self):
        async def go():
            conn = menu_conn(on_state=lambda idx, lua: ["garbage"])
            return await gl.load_game_save(conn, "0_MCP_0042")

        assert run(go()).startswith("ERR:OUTCOME_UNKNOWN")

    def test_autostart_unavailable_after_load_starts_is_unknown(self):
        async def go():
            conn = menu_conn(
                on_state=lambda idx, lua: [
                    "MCP_FRONTEND_AUTOSTART|false",
                    "MCP_FRONTEND_LOAD|true",
                ]
            )
            return await gl.load_game_save(conn, "0_MCP_0042")

        result = run(go())
        assert result.startswith("ERR:OUTCOME_UNKNOWN")
        assert "auto-start was unavailable" in result

    @pytest.mark.parametrize("failure", [ConnectionError, OSError, TimeoutError])
    def test_connection_loss_mid_load_is_outcome_unknown_not_an_exception(self, failure):
        """Civ VI may close FireTuner while accepting a load — that is an
        unknown outcome to report, not a crash, and never a resend."""

        async def go():
            def on_state(idx, lua):
                raise failure("socket closed")

            conn = menu_conn(on_state=on_state)
            return conn, await gl.load_game_save(conn, "0_MCP_0042")

        conn, result = run(go())
        assert result.startswith("ERR:OUTCOME_UNKNOWN")
        assert len(conn.load_calls) == 1

    def test_save_name_is_lua_quoted_by_json(self):
        """Names are quoted with json.dumps — quotes or backslashes in the
        name cannot break out of the Lua string."""

        async def go():
            conn = menu_conn(on_state=frontend_ok)
            await gl.load_game_save(conn, 'evil"; print("pwned"); --')
            return conn.state_calls[0][1]

        lua = run(go())
        assert 'local wanted = "evil\\"; print(\\"pwned\\"); --.Civ6Save";' in lua

    @pytest.mark.parametrize("name", ["0_MCP_0042", "0_MCP_0042.Civ6Save"])
    def test_native_filename_keeps_exactly_one_suffix(self, name):
        async def go():
            conn = menu_conn(on_state=frontend_ok)
            await gl.load_game_save(conn, name)
            return conn.state_calls[0][1]

        assert 'local wanted = "0_MCP_0042.Civ6Save";' in run(go())



# ---------------------------------------------------------------------------
# dismiss_popup
# ---------------------------------------------------------------------------


class TestDismissPopup:
    def test_congress_results_dismissal_is_reported_from_confirmed_output(self):
        async def go():
            conn = FakeConn(on_write=dismiss_router(
                phase1=["DISMISSED|WorldCongressResults"],
            ))
            return conn, await gl.dismiss_popup(conn)

        conn, result = run(go())
        assert result == "Dismissed: WorldCongressResults"
        assert len(conn.writes) == 2
        # The acknowledgment applies to captured LOOK notifications only,
        # not to every Congress notification or its live voting session.
        lua = conn.writes[0]
        assert "local reviewType = EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK" in lua
        assert "if reviewType and blocking == reviewType then" in lua
        assert "if #review > 0 then" in lua
        assert "WORLD_CONGRESS_LOOKED_AT_AVAILABLE" in lua
        assert "for _, nid in ipairs(review) do" in lua
        assert "if not entry or entry:IsDismissed() then dismissed = dismissed + 1 end" in lua
        assert "if dismissed > 0 then" in lua
        assert "if not sessionPending then" in lua

    def test_congress_acknowledgment_without_dismissed_fact_is_not_success(self):
        async def go():
            conn = FakeConn(on_write=dismiss_router(
                phase1=["ACKNOWLEDGED|WorldCongressResults"],
            ))
            return await gl.dismiss_popup(conn)

        assert run(go()) == "No popups to dismiss."

    def test_congress_voting_screens_are_not_unconditionally_hidden(self):
        async def go():
            conn = FakeConn(on_write=dismiss_router())
            return conn, await gl.dismiss_popup(conn)

        conn, result = run(go())
        assert result == "No popups to dismiss."
        lua = conn.writes[0]
        assert 'LookUpControl("/InGame/WorldCongressPopup")' not in lua
        assert 'LookUpControl("/InGame/WorldCongressIntro")' not in lua
        assert "blocking == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION" in lua
        assert "blocking == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION" in lua
        assert 'k:find("WORLD_CONGRESS")' not in lua

    def test_phase1_dismissal_is_reported_without_a_state_scan(
        self, monkeypatch
    ):
        """No exclusive popup visible => Phase 2/3 never touch other states."""

        async def go():
            conn = FakeConn(
                on_write=dismiss_router(phase1=["DISMISSED|InGamePopup"])
            )
            result = await gl.dismiss_popup(conn)
            return conn, result

        conn, result = run(go())
        assert result == "Dismissed: InGamePopup"
        assert len(conn.writes) == 2  # phase 1 batch + exclusive pre-check
        assert conn.state_calls == []

    def test_pending_diplomacy_is_reported_not_dismissed(self, monkeypatch):

        async def go():
            conn = FakeConn(
                on_write=dismiss_router(phase1=["PENDING|DiplomacyActionView"])
            )
            return await gl.dismiss_popup(conn)

        assert (
            run(go())
            == "Diplomacy session active — use respond_to_diplomacy to handle it."
        )

    def test_pending_trade_deal_is_reported_not_dismissed(self, monkeypatch):
        """DiplomacyDealView is an incoming offer; dismissing it kills the deal."""

        async def go():
            conn = FakeConn(
                on_write=dismiss_router(phase1=["PENDING|DiplomacyDealView"])
            )
            return await gl.dismiss_popup(conn)

        assert run(go()) == (
            "No popups to dismiss (incoming trade deal pending — "
            "use get_pending_trades)."
        )

    def test_dismissal_plus_pending_deal_combines_the_message(self, monkeypatch):

        async def go():
            conn = FakeConn(
                on_write=dismiss_router(
                    phase1=["DISMISSED|GenericPopup", "PENDING|DiplomacyDealView"]
                )
            )
            return await gl.dismiss_popup(conn)

        assert run(go()) == (
            "Dismissed: GenericPopup (incoming trade deal pending — "
            "use get_pending_trades)"
        )

    def test_nothing_visible_means_nothing_to_dismiss(self, monkeypatch):

        async def go():
            return await gl.dismiss_popup(FakeConn(on_write=dismiss_router()))

        assert run(go()) == "No popups to dismiss."

    def test_phase1_connection_error_propagates(self, monkeypatch):
        """A dead socket during Phase 1 is a transport failure — it
        propagates instead of being reported as 'No popups to dismiss.'"""

        def on_write(lua):
            if "EXCL_VISIBLE" in lua or "STILL_VISIBLE" in lua:
                return []
            raise ConnectionError("socket dead")

        with pytest.raises(ConnectionError):
            run(gl.dismiss_popup(FakeConn(on_write=on_write)))

    def test_phase1_other_errors_are_still_swallowed(self, monkeypatch):
        """Non-transport errors keep the old behaviour: logged at debug and
        the sweep continues as if nothing was dismissed."""

        def on_write(lua):
            if "EXCL_VISIBLE" in lua or "STILL_VISIBLE" in lua:
                return []
            raise RuntimeError("lua blew up")

        async def go():
            return await gl.dismiss_popup(FakeConn(on_write=on_write))

        assert "outcome unknown" in run(go())

    def test_information_popup_closes_only_its_exact_discovered_state(self):
        conn = FakeConn(
            lua_states={5: "NaturalWonderPopup", 7: "FrontEnd", 8: "UnrelatedPopup"},
            on_write=dismiss_router(precheck=["INFORMATION_POPUPS|VISIBLE|NaturalWonderPopup"]),
            on_state=lambda idx, lua: ["POPUP_CLOSED|NaturalWonderPopup"],
        )
        assert run(gl.dismiss_popup(conn)) == "Dismissed: NaturalWonderPopup"
        assert [idx for idx, _ in conn.state_calls] == [5]
        lua = conn.state_calls[0][1]
        assert 'id ~= expected' in lua
        assert 'local expected = "NaturalWonderPopup"' in lua
        assert 'pcall(callback)' in lua
        assert 'nowHidden == true' in lua
        assert 'm_kQueuedPopups' not in lua and 'SetHide' not in lua

    def test_native_information_queue_is_drained_without_clearing_private_state(self):
        responses = iter([["POPUP_VISIBLE|TechCivicCompletedPopup"], ["POPUP_CLOSED|TechCivicCompletedPopup"]])
        conn = FakeConn(
            lua_states={5: "TechCivicCompletedPopup"},
            on_write=dismiss_router(precheck=["INFORMATION_POPUPS|VISIBLE|TechCivicCompletedPopup"]),
            on_state=lambda idx, lua: next(responses),
        )
        assert run(gl.dismiss_popup(conn)) == "Dismissed: TechCivicCompletedPopup"
        assert len(conn.state_calls) == 2
        for _, lua in conn.state_calls:
            assert 'local callback = OnClose' in lua
            assert 'callback = Close' not in lua
            assert 'm_kPopupData' not in lua and 'm_kQueuedPopups' not in lua

    def test_missing_state_is_discovered_once_and_then_closed_by_name(self):
        conn = FakeConn(
            on_write=dismiss_router(precheck=["INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup"]),
            on_state=lambda idx, lua: ["POPUP_CLOSED|NaturalDisasterPopup"],
        )
        conn.discovered_states = {211: "NaturalDisasterPopup"}
        assert run(gl.dismiss_popup(conn)) == "Dismissed: NaturalDisasterPopup"
        assert conn.discoveries == 1
        assert [idx for idx, _ in conn.state_calls] == [211]

    @pytest.mark.parametrize("native_response", [
        [], ["DISMISSED"], ["POPUP_UNKNOWN|NaturalDisasterPopup|context mismatch"],
        ["POPUP_UNKNOWN|NaturalDisasterPopup|native callback failed"],
        ["POPUP_CLOSED|OtherPopup"],
    ])
    def test_unconfirmed_native_close_never_reports_dismissed(self, native_response):
        conn = FakeConn(
            lua_states={5: "NaturalDisasterPopup"},
            on_write=dismiss_router(precheck=["INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup"]),
            on_state=lambda idx, lua: native_response,
        )
        result = run(gl.dismiss_popup(conn))
        assert "outcome unknown" in result
        assert "Dismissed:" not in result
        assert len(conn.state_calls) == 1 and conn.discoveries == 0

    def test_visible_native_queue_has_a_limit_without_false_success(self):
        conn = FakeConn(
            lua_states={5: "NaturalDisasterPopup"},
            on_write=dismiss_router(precheck=["INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup"]),
            on_state=lambda idx, lua: ["POPUP_VISIBLE|NaturalDisasterPopup"],
        )
        result = run(gl.dismiss_popup(conn))
        assert "queue remains; outcome unknown" in result
        assert "Dismissed:" not in result
        assert len(conn.state_calls) == 20

    def test_native_callback_transport_failure_stops_without_rediscovery(self):
        def failed(idx, lua):
            raise ConnectionError("reply lost")
        conn = FakeConn(
            lua_states={5: "NaturalDisasterPopup", 6: "HistoricMoments"},
            on_write=dismiss_router(precheck=[
                "INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup",
                "INFORMATION_POPUPS|VISIBLE|HistoricMoments",
            ]),
            on_state=failed,
        )
        result = run(gl.dismiss_popup(conn))
        assert "reply lost; outcome unknown" in result
        assert len(conn.state_calls) == 1 and conn.discoveries == 0

    @pytest.mark.parametrize("visibility", [[], ["INFORMATION_POPUPS|UNKNOWN"],
        ["INFORMATION_POPUPS|VISIBLE|UnknownPopup", "INFORMATION_POPUPS|DONE"]])
    def test_unknown_visibility_does_not_scan_or_report_none(self, visibility):
        conn = FakeConn(on_write=lambda lua: visibility if "INFORMATION_POPUPS" in lua else [])
        assert "visibility unavailable; outcome unknown" in run(gl.dismiss_popup(conn))
        assert conn.state_calls == [] and conn.discoveries == 0

    def test_crash_dialog_dismissals_are_appended(self, monkeypatch):
        async def fake():
            return ["Firaxis Crash Reporter (clicked 'Close')"]

        monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", fake)

        async def go():
            return await gl.dismiss_popup(FakeConn(on_write=dismiss_router()))

        assert run(go()) == "Dismissed: Firaxis Crash Reporter (clicked 'Close')"


# ---------------------------------------------------------------------------
# execute_lua — the escape hatch
# ---------------------------------------------------------------------------


class TestExecuteLua:
    def test_ingame_context_routes_to_the_ingame_state(self):
        async def go():
            conn = FakeConn(on_write=lambda lua: ["1", "2"])
            result = await gl.execute_lua(conn, "print(1)", context="ingame")
            return conn, result

        conn, result = run(go())
        assert result == "1\n2"
        assert conn.writes == ['print(1)']

    def test_numeric_context_routes_to_that_state_index(self):
        async def go():
            conn = FakeConn(on_state=lambda idx, lua: [f"state{idx}"])
            result = await gl.execute_lua(conn, "print(1)", context="5")
            return conn, result

        conn, result = run(go())
        assert result == "state5"
        assert conn.state_calls == [(5, 'print(1)')]

    def test_any_other_context_routes_to_gamecore_read(self):
        async def go():
            conn = FakeConn(on_read=lambda lua: ["ok"])
            result = await gl.execute_lua(conn, "print(1)")
            return conn, result

        conn, result = run(go())
        assert result == "ok"
        assert conn.reads == ['print(1)']

    def test_user_code_passes_unchanged_to_the_connection_boundary(self):
        """Only the connection owns response framing, including trailing returns."""

        async def go():
            conn = FakeConn(on_read=lambda lua: [])
            await gl.execute_lua(conn, "return 1")
            return conn.reads

        assert run(go()) == ['return 1']

    def test_empty_output_becomes_a_placeholder(self):
        async def go():
            return await gl.execute_lua(FakeConn(), "x = 1")

        assert run(go()) == "(no output)"


def test_missing_popup_state_refreshes_once_without_guessing_indexes():
    conn = FakeConn(on_write=lambda lua: [
        "EXCL_VISIBLE", "STILL_VISIBLE|NaturalDisasterPopup",
        "INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup", "INFORMATION_POPUPS|DONE",
    ] if "EXCL_VISIBLE" in lua or "STILL_VISIBLE" in lua or "INFORMATION_POPUPS" in lua else [])
    result = run(gl.dismiss_popup(conn))
    assert "outcome unknown" in result
    assert "NaturalDisasterPopup" in result
    assert conn.discoveries == 1
    assert conn.state_calls == []

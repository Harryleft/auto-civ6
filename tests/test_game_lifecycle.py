"""Unit tests for `civ_mcp.game_lifecycle` and the process-lifecycle
orchestrators it delegates to in `civ_mcp.game_launcher`.

The connection is a `FakeConn` that records every Lua chunk and replays
scripted response lines, so the tests can assert on the generated Lua itself
(save names, indexes, popup names) without a live game. Process and
filesystem seams (`game_launcher` module attributes, `asyncio.sleep`) are
monkeypatched — no game is launched and no real save directory is touched.

`cleanup_old_autosaves` is deliberately not covered here; it is tested in
tests/test_hang_recovery.py.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from civ_mcp import game_launcher
from civ_mcp import game_lifecycle as gl

run = asyncio.run


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
        lua_states=None,
        on_write=None,
        on_state=None,
        on_read=None,
    ):
        self.gamecore_index = gamecore_index
        self.lua_states = dict(lua_states or {})
        self.on_write = on_write
        self.on_state = on_state
        self.on_read = on_read
        self.writes: list[str] = []
        self.state_calls: list[tuple[int, str]] = []
        self.reads: list[str] = []

    async def execute_write(self, lua, timeout=5.0):
        self.writes.append(lua)
        if self.on_write is None:
            return []
        return self.on_write(lua)

    async def execute_in_state(self, state_index, lua, timeout=5.0):
        self.state_calls.append((state_index, lua))
        if self.on_state is None:
            return []
        return self.on_state(state_index, lua)

    async def execute_read(self, lua, timeout=5.0):
        self.reads.append(lua)
        if self.on_read is None:
            return []
        return self.on_read(lua)


def dismiss_router(*, phase1=(), precheck=(), phase3=()):
    """Route dismiss_popup's three InGame calls by their marker strings."""

    def on_write(lua):
        if "EXCL_VISIBLE" in lua:
            return list(precheck)
        if "STILL_VISIBLE" in lua:
            return list(phase3)
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
    def test_emits_a_named_single_player_local_save(self):
        """The Lua must name the save and pin it to local single-player storage."""

        async def go():
            conn = FakeConn(on_write=lambda lua: ["OK|0_MCP_0001"])
            result = await gl.save_game(conn, "0_MCP_0001")
            return conn, result

        conn, result = run(go())
        assert result == "Saved: 0_MCP_0001"
        (lua,) = conn.writes
        assert 'gf.Name = "0_MCP_0001"' in lua
        assert "SaveLocations.LOCAL_STORAGE" in lua
        assert "SaveTypes.SINGLE_PLAYER" in lua
        assert "IsAutosave = false" in lua
        assert "Network.SaveGame(gf)" in lua

    def test_reports_failure_when_the_game_does_not_echo_ok(self):
        async def go():
            conn = FakeConn(on_write=lambda lua: ["ERR:something broke"])
            return await gl.save_game(conn, "x")

        assert run(go()).startswith("Save may have failed:")

    def test_save_name_is_escaped_against_lua_injection(self):
        """The name is escaped before landing inside the Lua string, so a
        name containing a double quote cannot break out and inject Lua."""

        async def go():
            conn = FakeConn(on_write=lambda lua: [])
            await gl.save_game(conn, 'evil"; print("pwned"); --')
            return conn.writes[0]

        lua = run(go())
        assert 'gf.Name = "evil\\"; print(\\"pwned\\"); --"' in lua
        assert 'gf.Name = "evil"; print("pwned"); --"' not in lua


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
    auto.mkdir(parents=True)
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
            if "MCPSaveList" in lua and "MCPLoadResult" not in lua:
                return ["ERR:NO_SAVE_LIST"]  # load_save's Lua cache check
            if "MCPLoadResult = nil" in lua:
                return ["QUERY_SENT"]
            if "MCPLoadDone then" in lua:
                return ["RESULT|FOUND"]
            raise AssertionError(f"unexpected lua: {lua}")

        async def go():
            conn = FakeConn(on_write=on_write)
            listing = await gl.list_saves(conn)
            result = await gl.load_save(conn, 1)
            return conn, listing, result

        conn, listing, result = run(go())
        assert listing.startswith("Available saves (filesystem scan")
        # Index 1 = newest = MyGame; loaded by name via load_game_save.
        assert result.startswith("Loading save: MyGame")
        assert any('if s.Name == "MyGame" then' in w for w in conn.writes)

    def test_out_of_range_index_against_the_filesystem_cache(self, save_dirs):
        base, _auto = save_dirs
        _touch(base / "Only.Civ6Save", 1000)

        async def go():
            conn = FakeConn(on_write=lambda lua: ["ERR:NO_SAVE_LIST"])
            await gl.list_saves(conn)
            return await gl.load_save(conn, 5)

        assert "out of range (1-1)" in run(go())


# ---------------------------------------------------------------------------
# load_game_save — Lua tier, then filesystem verify + launcher fallback
# ---------------------------------------------------------------------------


def load_router(poll_lines):
    """Script Tier 1: query accepted, poll answers with the given lines."""

    def on_write(lua):
        if "MCPLoadResult = nil" in lua:
            return ["QUERY_SENT"]
        if "MCPLoadDone then" in lua:
            return list(poll_lines)
        raise AssertionError(f"unexpected lua: {lua}")

    return on_write


class TestLoadGameSave:
    def test_found_via_lua_returns_loading_message(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        monkeypatch.setattr(sys, "platform", "darwin")

        async def go():
            conn = FakeConn(on_write=load_router(["RESULT|FOUND"]))
            result = await gl.load_game_save(conn, "0_MCP_0042")
            return conn, result

        conn, result = run(go())
        assert result.startswith("Loading save: 0_MCP_0042")
        assert 'if s.Name == "0_MCP_0042" then' in conn.writes[0]
        assert "Network.LoadGame(s, ServerType.SERVER_TYPE_NONE)" in conn.writes[0]

    def test_not_found_anywhere_returns_an_actionable_error(
        self, save_dirs, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        monkeypatch.setattr(sys, "platform", "darwin")

        async def go():
            conn = FakeConn(on_write=load_router(["RESULT|NOT_FOUND"]))
            return await gl.load_game_save(conn, "ghost")

        out = run(go())
        assert "Error: Save 'ghost' not found" in out
        assert "list_saves()" in out

    def test_on_disk_at_main_menu_uses_menu_navigation(
        self, save_dirs, monkeypatch
    ):
        """gamecore_index=None means main menu: navigate, don't restart."""
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        monkeypatch.setattr(sys, "platform", "darwin")
        base, _auto = save_dirs
        _touch(base / "OldSave.Civ6Save", 1000)

        calls = []

        async def fake_menu_nav(name):
            calls.append(("menu", name))
            return "menu nav done"

        async def fake_restart(name):
            calls.append(("restart", name))
            return "restarted"

        monkeypatch.setattr(game_launcher, "load_save_from_menu", fake_menu_nav)
        monkeypatch.setattr(game_launcher, "restart_and_load", fake_restart)

        async def go():
            conn = FakeConn(
                gamecore_index=None, on_write=load_router(["RESULT|NOT_FOUND"])
            )
            return await gl.load_game_save(conn, "OldSave")

        assert run(go()) == "menu nav done"
        assert calls == [("menu", "OldSave")]

    def test_on_disk_while_in_game_triggers_restart_and_load(
        self, save_dirs, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        monkeypatch.setattr(sys, "platform", "darwin")
        _base, auto = save_dirs
        _touch(auto / "AutoSave_0221.Civ6Save", 1000)

        calls = []

        async def fake_restart(name):
            calls.append(name)
            return "restarted"

        monkeypatch.setattr(game_launcher, "restart_and_load", fake_restart)

        async def go():
            conn = FakeConn(
                gamecore_index=0, on_write=load_router(["RESULT|NOT_FOUND"])
            )
            return await gl.load_game_save(conn, "AutoSave_0221")

        assert run(go()) == "restarted"
        assert calls == ["AutoSave_0221"]

    def test_linux_skips_the_lua_tier_entirely(self, save_dirs, monkeypatch):
        """The Aspyr Linux port silently ignores Network.LoadGame, so on
        linux the function must not send a single Lua chunk."""
        monkeypatch.setattr(sys, "platform", "linux")
        base, _auto = save_dirs
        _touch(base / "TuxSave.Civ6Save", 1000)

        async def fake_menu_nav(name):
            return f"menu:{name}"

        monkeypatch.setattr(game_launcher, "load_save_from_menu", fake_menu_nav)

        async def go():
            def on_write(lua):
                raise AssertionError("Lua tier must be skipped on linux")

            conn = FakeConn(gamecore_index=None, on_write=on_write)
            result = await gl.load_game_save(conn, "TuxSave")
            return conn, result

        conn, result = run(go())
        assert result == "menu:TuxSave"
        assert conn.writes == []

    def test_save_name_is_escaped_in_the_lua_match(self, save_dirs, monkeypatch):
        """The name-matching Lua must escape quotes, same as save_game."""
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        monkeypatch.setattr(sys, "platform", "darwin")

        async def go():
            conn = FakeConn(on_write=load_router(["RESULT|NOT_FOUND"]))
            await gl.load_game_save(conn, 'evil"; print("pwned"); --')
            return conn.writes[0]

        lua = run(go())
        assert 'if s.Name == "evil\\"; print(\\"pwned\\"); --" then' in lua

    def test_lua_tier_connection_error_propagates(self, save_dirs, monkeypatch):
        """A dead connection during the Lua query is a transport failure —
        it propagates instead of falling through to the launcher."""
        monkeypatch.setattr(sys, "platform", "darwin")
        base, _auto = save_dirs
        _touch(base / "Fallback.Civ6Save", 1000)

        async def go():
            def on_write(lua):
                raise ConnectionError("socket dead")

            conn = FakeConn(gamecore_index=None, on_write=on_write)
            await gl.load_game_save(conn, "Fallback")

        with pytest.raises(ConnectionError):
            run(go())

    def test_other_lua_tier_errors_fall_through_to_filesystem(
        self, save_dirs, monkeypatch
    ):
        """Non-transport errors keep the old behaviour: swallow (log.debug)
        and continue loading via the launcher."""
        monkeypatch.setattr(sys, "platform", "darwin")
        base, _auto = save_dirs
        _touch(base / "Fallback.Civ6Save", 1000)

        async def fake_menu_nav(name):
            return f"menu:{name}"

        monkeypatch.setattr(game_launcher, "load_save_from_menu", fake_menu_nav)

        async def go():
            def on_write(lua):
                raise RuntimeError("lua blew up")

            conn = FakeConn(gamecore_index=None, on_write=on_write)
            return await gl.load_game_save(conn, "Fallback")

        assert run(go()) == "menu:Fallback"


# ---------------------------------------------------------------------------
# dismiss_popup
# ---------------------------------------------------------------------------


class TestDismissPopup:
    def test_phase1_dismissal_is_reported_without_a_state_scan(
        self, monkeypatch
    ):
        """No exclusive popup visible => Phase 2/3 never touch other states."""
        no_crash_dialogs(monkeypatch)

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
        no_crash_dialogs(monkeypatch)

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
        no_crash_dialogs(monkeypatch)

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
        no_crash_dialogs(monkeypatch)

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
        no_crash_dialogs(monkeypatch)

        async def go():
            return await gl.dismiss_popup(FakeConn(on_write=dismiss_router()))

        assert run(go()) == "No popups to dismiss."

    def test_phase1_connection_error_propagates(self, monkeypatch):
        """A dead socket during Phase 1 is a transport failure — it
        propagates instead of being reported as 'No popups to dismiss.'"""
        no_crash_dialogs(monkeypatch)

        def on_write(lua):
            if "EXCL_VISIBLE" in lua or "STILL_VISIBLE" in lua:
                return []
            raise ConnectionError("socket dead")

        with pytest.raises(ConnectionError):
            run(gl.dismiss_popup(FakeConn(on_write=on_write)))

    def test_phase1_other_errors_are_still_swallowed(self, monkeypatch):
        """Non-transport errors keep the old behaviour: logged at debug and
        the sweep continues as if nothing was dismissed."""
        no_crash_dialogs(monkeypatch)

        def on_write(lua):
            if "EXCL_VISIBLE" in lua or "STILL_VISIBLE" in lua:
                return []
            raise RuntimeError("lua blew up")

        async def go():
            return await gl.dismiss_popup(FakeConn(on_write=on_write))

        assert run(go()) == "No popups to dismiss."

    def test_exclusive_popup_is_closed_in_its_own_lua_state(self, monkeypatch):
        """Phase 2: only states whose names carry popup keywords are scanned."""
        no_crash_dialogs(monkeypatch)

        closes = []

        def on_state(idx, lua):
            assert "pcall(Close)" in lua
            closes.append(idx)
            # First Close() dismisses; the drain loop's next check finds it hidden.
            return ["DISMISSED"] if len(closes) == 1 and idx == 5 else []

        async def go():
            conn = FakeConn(
                lua_states={5: "NaturalWonderPopup", 7: "FrontEnd"},
                on_write=dismiss_router(precheck=["EXCL_VISIBLE"]),
                on_state=on_state,
            )
            result = await gl.dismiss_popup(conn)
            return conn, result

        conn, result = run(go())
        assert result == "Dismissed: NaturalWonderPopup"
        scanned = {idx for idx, _ in conn.state_calls}
        assert 5 in scanned and 7 not in scanned

    def test_phase2_drains_until_the_popup_stays_hidden(self, monkeypatch):
        """The drain loop closes every queued popup, but the state name is
        reported only once no matter how many queue entries drained."""
        no_crash_dialogs(monkeypatch)
        closes = []

        def on_state(idx, lua):
            closes.append(idx)
            # Two queued popups drain, the third check finds it hidden.
            return ["DISMISSED"] if len(closes) <= 2 else []

        async def go():
            conn = FakeConn(
                lua_states={5: "WonderBuiltPopup"},
                on_write=dismiss_router(precheck=["EXCL_VISIBLE"]),
                on_state=on_state,
            )
            return await gl.dismiss_popup(conn)

        assert run(go()) == "Dismissed: WonderBuiltPopup"

    def test_phase3_probes_unknown_state_indexes(self, monkeypatch):
        """When the handshake's state list is incomplete, Phase 3 probes
        indexes 50+ for the still-visible popup and learns the new state."""
        no_crash_dialogs(monkeypatch)

        def on_state(idx, lua):
            if "ContextPtr:GetID()" in lua:
                return ["WonderBuiltPopup"] if idx == 50 else ["Other"]
            if "pcall(Close)" in lua:
                return ["DISMISSED"]
            return []

        async def go():
            conn = FakeConn(
                lua_states={},  # truncated handshake — no popup states known
                on_write=dismiss_router(
                    precheck=["EXCL_VISIBLE"],
                    phase3=["STILL_VISIBLE|WonderBuiltPopup"],
                ),
                on_state=on_state,
            )
            result = await gl.dismiss_popup(conn)
            return conn, result

        conn, result = run(go())
        assert result == "Dismissed: WonderBuiltPopup (probed state 50)"
        assert conn.lua_states[50] == "WonderBuiltPopup"

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
        assert conn.writes == ['(function() print(1) end)(); print("---END---")']

    def test_numeric_context_routes_to_that_state_index(self):
        async def go():
            conn = FakeConn(on_state=lambda idx, lua: [f"state{idx}"])
            result = await gl.execute_lua(conn, "print(1)", context="5")
            return conn, result

        conn, result = run(go())
        assert result == "state5"
        assert conn.state_calls == [(5, '(function() print(1) end)(); print("---END---")')]

    def test_any_other_context_routes_to_gamecore_read(self):
        async def go():
            conn = FakeConn(on_read=lambda lua: ["ok"])
            result = await gl.execute_lua(conn, "print(1)")
            return conn, result

        conn, result = run(go())
        assert result == "ok"
        assert conn.reads == ['(function() print(1) end)(); print("---END---")']

    def test_user_code_is_function_wrapped_and_sentinel_terminated(self):
        """The connection layer tells "no output" from "no answer" by the
        sentinel, so run_lua chunks must end with one. Function-wrapped so a
        trailing `return` in user code stays valid Lua."""

        async def go():
            conn = FakeConn(on_read=lambda lua: [])
            await gl.execute_lua(conn, "return 1")
            return conn.reads

        assert run(go()) == ['(function() return 1 end)(); print("---END---")']

    def test_empty_output_becomes_a_placeholder(self):
        async def go():
            return await gl.execute_lua(FakeConn(), "x = 1")

        assert run(go()) == "(no output)"


# ---------------------------------------------------------------------------
# Process lifecycle orchestrators in game_launcher
# ---------------------------------------------------------------------------


class TestProcessLifecycle:
    def test_kill_game_delegates_to_the_sync_killer(self, monkeypatch):
        monkeypatch.setattr(game_launcher, "_kill_game_sync", lambda: "killed")
        assert run(game_launcher.kill_game()) == "killed"

    def test_launch_game_delegates_to_the_sync_launcher(self, monkeypatch):
        monkeypatch.setattr(
            game_launcher, "_launch_game_sync", lambda: "launched"
        )
        assert run(game_launcher.launch_game()) == "launched"

    def test_restart_and_load_runs_kill_launch_load_in_order(self, monkeypatch):
        calls = []

        async def fake_dismiss():
            calls.append("dismiss")
            return []

        async def fake_load(name):
            calls.append(("load", name))
            return "Loaded save"

        monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", fake_dismiss)
        monkeypatch.setattr(
            game_launcher, "_kill_game_sync", lambda: calls.append("kill") or "Killed"
        )
        monkeypatch.setattr(
            game_launcher,
            "_launch_game_sync",
            lambda: calls.append("launch") or "Game process detected",
        )
        monkeypatch.setattr(game_launcher, "load_save_from_menu", fake_load)

        out = run(game_launcher.restart_and_load("0_MCP_0042"))
        assert out == "Kill: Killed | Launch: Game process detected | Load: Loaded save"
        assert calls == [
            "dismiss",  # pre-kill: crash dialogs block process exit
            "kill",
            "launch",
            "dismiss",  # post-launch: leftover crash dialog from last session
            ("load", "0_MCP_0042"),
        ]

    def test_restart_and_load_aborts_when_the_game_never_launches(
        self, monkeypatch
    ):
        """'not detected' in the launch result short-circuits before any load."""

        async def fake_dismiss():
            return []

        async def fake_load(name):
            raise AssertionError("load must not run after a failed launch")

        monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", fake_dismiss)
        monkeypatch.setattr(game_launcher, "_kill_game_sync", lambda: "Killed")
        monkeypatch.setattr(
            game_launcher,
            "_launch_game_sync",
            lambda: "Game process not detected after launch",
        )
        monkeypatch.setattr(game_launcher, "load_save_from_menu", fake_load)

        out = run(game_launcher.restart_and_load("0_MCP_0042"))
        assert out.endswith("ABORTED: Game failed to launch.")

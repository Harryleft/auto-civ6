"""Background pollers must stay off the InGame context while a turn runs.

The wait loop deliberately uses GameCore-only queries while the AI civs process
their turn, because InGame queries force context switches that stall the AI's
diplomacy job and wedge the turn — the documented cause of the Games 1-5 hangs.
The popup watcher, camera and game-over watchdog were all sending InGame queries
through that same window anyway (the popup watcher at 2 Hz), which is roughly 200
context switches in a 100-second wait.

These tests pin the rule from both sides: the pollers consult the shared
in-flight flag, and the flag is kept in step with the request — including across
a save load, where forgetting it makes the next ``end_turn`` skip sending
ACTION_ENDTURN and poll a turn that can never advance.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp import spectator
from civ_mcp.game_over_watchdog import GameOverWatchdog
from civ_mcp.game_state import GameState


class _Conn:
    """Counts queries and carries the shared in-flight flag."""

    def __init__(self) -> None:
        self.turn_in_progress = False
        self.reads = 0
        self.writes = 0

    async def execute_read(self, _code, **_kwargs):
        self.reads += 1
        return ["NO_THREATS"]

    async def execute_write(self, _code, **_kwargs):
        self.writes += 1
        return ["CLEAR"]


def _bare_state() -> GameState:
    state = GameState.__new__(GameState)
    state.conn = _Conn()
    state._cache_epoch = 0
    state._pending_end_turn = True
    state._pending_end_turn_from = 12
    state._end_turn_blocked = True
    state._pending_end_turn_wait = 321.0
    state._wc_driven = True
    state._wc_drives = 7
    state._wc_dismissals = 2
    return state


# ---------------------------------------------------------------------------
# The flag is reset whenever the world branch is abandoned
# ---------------------------------------------------------------------------


def test_a_reload_forgets_the_in_flight_request() -> None:
    """Otherwise the next end_turn skips ACTION_ENDTURN and waits forever."""

    state = _bare_state()
    state.conn.turn_in_progress = True

    state.invalidate_cached_state()

    assert state._pending_end_turn is False
    assert state._pending_end_turn_from is None
    assert state._end_turn_blocked is False
    assert state._pending_end_turn_wait == 0.0
    assert state._wc_driven is False
    assert state._wc_drives == 0
    assert state._wc_dismissals == 0
    assert state.conn.turn_in_progress is False


def test_a_stub_connection_without_the_flag_does_not_break_the_reset() -> None:
    state = _bare_state()
    state.conn = SimpleNamespace()  # no turn_in_progress attribute at all

    state.invalidate_cached_state()

    assert state._pending_end_turn is False


def test_the_next_end_turn_sends_action_endturn_again_after_a_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression this guards: a self-inflicted wedge after every reload."""

    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "budget_fake",
        pathlib.Path(__file__).resolve().parent / "test_end_turn_budget.py",
    )
    assert spec and spec.loader
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)

    from civ_mcp import end_turn as et

    clock = fake._VirtualClock(monkeypatch)
    gs = fake._FakeGameState(wc_turn=False)

    asyncio.run(et.execute_end_turn(gs))
    assert gs.conn.sends == 1
    assert gs.conn.turn_in_progress is True, "在途期间必须让后台轮询器让开"

    # A load abandons the branch, and the real reload path is
    # invalidate_cached_state() -> _reset_pending_end_turn(). That pair is
    # covered on a real GameState above; here we exercise what the flow depends
    # on, using the same function the reload path calls.
    GameState._reset_pending_end_turn(gs)
    assert gs.conn.turn_in_progress is False

    asyncio.run(et.execute_end_turn(gs))
    assert gs.conn.sends == 2, "读档后必须重新发送结束回合请求，否则永远等不到推进"
    assert clock.elapsed > 0


def test_the_flag_clears_when_the_turn_advances(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "budget_fake2",
        pathlib.Path(__file__).resolve().parent / "test_end_turn_budget.py",
    )
    assert spec and spec.loader
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)

    from civ_mcp import end_turn as et

    fake._VirtualClock(monkeypatch)
    gs = fake._FakeGameState(wc_turn=False)

    # Reading the turn twice gives the "before" value; from the first poll on it
    # has advanced.
    readings = {"n": 0}

    async def advancing(_gs):
        readings["n"] += 1
        return 12 if readings["n"] <= 2 else 13

    original = et._get_turn_number
    et._get_turn_number = advancing
    try:
        result = asyncio.run(et.execute_end_turn(gs))
    finally:
        et._get_turn_number = original

    assert gs.conn.turn_in_progress is False, "回合推进后必须让后台轮询器恢复"
    assert gs._pending_end_turn is False
    assert gs._pending_end_turn_wait == 0.0
    assert "->" in result


# ---------------------------------------------------------------------------
# The pollers stay off the InGame context
# ---------------------------------------------------------------------------


def test_popup_watcher_does_not_poll_while_a_turn_is_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _Conn()
    conn.turn_in_progress = True
    watcher = spectator.PopupWatcher(conn)
    monkeypatch.setattr(spectator, "POPUP_POLL_INTERVAL", 0.01)
    polls: list[float] = []

    async def counting_poll() -> str:
        polls.append(1.0)
        return "CLEAR"

    monkeypatch.setattr(watcher, "_poll", counting_poll)

    async def scenario() -> None:
        watcher.start()
        await asyncio.sleep(0.08)
        assert polls == [], "AI 处理期间 2Hz 的 InGame 轮询必须停掉"
        conn.turn_in_progress = False
        await asyncio.sleep(0.08)
        assert polls, "回合结束后应恢复轮询"
        await watcher.stop()

    asyncio.run(scenario())


def test_camera_skips_its_ingame_lookup_while_a_turn_is_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _Conn()
    conn.turn_in_progress = True
    camera = spectator.CameraController(conn)
    looked: list[tuple[int, int]] = []

    async def fake_look(x: int, y: int) -> None:
        looked.append((x, y))

    monkeypatch.setattr(camera, "_look_at", fake_look)
    camera.push(3, 4, "unit")

    async def scenario() -> None:
        camera.start()
        await asyncio.sleep(0.05)
        assert looked == [], "AI 处理期间不得发 InGame 相机查询"
        assert camera._queue.empty(), "事件应留给回合结束后处理"
        await camera.stop()

    asyncio.run(scenario())


def test_watchdog_skips_its_ingame_game_over_check_during_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import civ_mcp.game_over_watchdog as gw

    conn = _Conn()
    conn.turn_in_progress = True
    gs = SimpleNamespace(conn=conn)
    checks: list[float] = []

    async def counting_check():
        checks.append(1.0)
        return None

    gs.check_game_over = counting_check
    watchdog = GameOverWatchdog(gs, SimpleNamespace())
    monkeypatch.setattr(gw, "INTERVAL", 0.01)

    async def scenario() -> None:
        watchdog.start()
        watchdog.arm()
        await asyncio.sleep(0.08)
        assert checks == [], "等待循环已经负责回合内的结束检测，看门狗应让开"
        conn.turn_in_progress = False
        await asyncio.sleep(0.08)
        assert checks, "回合结束后应恢复看门狗"
        await watchdog.stop()

    asyncio.run(scenario())

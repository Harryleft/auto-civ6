"""弹窗写入丢回应后，原请求必须阻止重发和依赖变更。"""

import asyncio
import json

import pytest

from civ_mcp import game_launcher, server
from civ_mcp.actions import _PendingAction
from civ_mcp.game_state import GameState
from civ_mcp.snapshot import pending_facts
from test_pending_dispatch import context
from test_action_confirmation import Engine


@pytest.mark.parametrize("method,args,writes,reads", [
    ("move_unit", (0, 22, 9), [["OK:MOVING_TO|22,9|from:22,11"]],
     [["POS|22|9|moves:1|busy:false"]]),
    ("found_city", (0,), [["OK:FOUNDED|19,17"]],
     [["CITY_EXISTS|owner:0|city:65536"]]),
    ("attack_unit", (2, 12, 20), [[],
     ["OK:RANGE_ATTACK|pre_hp:80/100|target_owner:63|target_id:123|attacks_before:1"],
     ["UNIT|UNIT_SCOUT|55/100|owner:63|id:123", "CORE_BUSY|false"]], []),
])
def test_action_stops_when_its_own_popup_cleanup_loses_reply(method, args, writes, reads):
    class CleanupLoss(Engine):
        popup_calls = 0

        async def execute_write(self, source, timeout=5):
            if not self.popup_calls:
                self.popup_calls += 1
                raise TimeoutError("popup write reply lost during action preparation")
            return await super().execute_write(source)

    async def scenario():
        connection = CleanupLoss(writes=writes, reads=reads)
        state = GameState(connection)
        result = await getattr(state, method)(*args)
        assert "OUTCOME_UNKNOWN" in result
        assert state.popup_pending
        assert connection.popup_calls == 1
        assert connection.submissions == []
        assert state.pending_action_summary is None

    asyncio.run(scenario())


class LostPopupReply:
    is_connected = True

    def __init__(self):
        self.writes = []

    async def execute_write(self, source, timeout=5):
        self.writes.append(source)
        raise TimeoutError("Incomplete response from Civ 6 within 5.0s (Lua state 119); command outcome is unknown.")


def test_popup_write_timeout_is_an_explicit_unknown_with_public_pending():
    async def scenario():
        state = GameState(LostPopupReply())
        result = await state.dismiss_popup()
        assert result.startswith("ERR:OUTCOME_UNKNOWN|"), result
        assert pending_facts(state).get("popup") is True
    asyncio.run(scenario())


def test_repeated_popup_command_never_replays_the_unknown_write():
    async def scenario():
        connection = LostPopupReply()
        state = GameState(connection)
        await state.dismiss_popup()
        state._clear_world_caches()
        repeated = await state.dismiss_popup()
        assert len(connection.writes) == 1
        assert "OUTCOME_UNKNOWN" in repeated
        assert pending_facts(state).get("popup") is True
    asyncio.run(scenario())


def test_unknown_popup_blocks_direct_gameplay_before_game_io():
    async def scenario():
        connection = LostPopupReply()
        state = GameState(connection)
        await state.dismiss_popup()
        for operation in (
            lambda: state.set_research("TECH_MINING"),
            lambda: state.end_turn(),
            lambda: state.save_game("must_not_write"),
            lambda: state.return_to_main_menu(),
            lambda: state.launch_game(),
        ):
            result = await operation()
            assert "OUTCOME_UNKNOWN" in result
        assert len(connection.writes) == 1
    asyncio.run(scenario())


def test_unknown_popup_is_public_and_blocks_mcp_dispatch():
    async def scenario():
        connection = LostPopupReply()
        state = GameState(connection)
        ctx = context(state)
        await server.dismiss_popup(ctx)
        status = json.loads(await server.get_runtime_status(ctx))
        assert status["pending"]["popup"] is True
        assert status["has_pending"] is True
        assert "OUTCOME_UNKNOWN" in await server.set_research(ctx, "TECH_MINING")
        assert "OUTCOME_UNKNOWN" in await server.end_turn(ctx)
        assert len(connection.writes) == 1
    asyncio.run(scenario())


def test_cancelled_popup_keeps_the_gate():
    class Interrupted(LostPopupReply):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()

        async def execute_write(self, source, timeout=5):
            self.writes.append(source)
            self.entered.set()
            await asyncio.Event().wait()

    async def scenario():
        connection = Interrupted()
        state = GameState(connection)
        task = asyncio.create_task(state.dismiss_popup())
        await connection.entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert state.popup_pending
        assert "OUTCOME_UNKNOWN" in await state.dismiss_popup()
        assert len(connection.writes) == 1
    asyncio.run(scenario())


def test_lost_native_close_preserves_both_popup_and_original_action(monkeypatch):
    class LostNativeClose:
        lua_states = {8: "TechCivicCompletedPopup"}

        def __init__(self):
            self.writes = []
            self.closes = 0

        async def execute_write(self, source, timeout=5):
            self.writes.append(source)
            if "INFORMATION_POPUPS" in source:
                return ["INFORMATION_POPUPS|VISIBLE|TechCivicCompletedPopup", "INFORMATION_POPUPS|DONE"]
            return []

        async def execute_in_state(self, index, source, timeout=5):
            assert index == 8
            self.closes += 1
            raise TimeoutError("native Close reply lost")

    async def no_os_dialogs():
        return []

    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", no_os_dialogs)

    async def scenario():
        connection = LostNativeClose()
        state = GameState(connection)
        original = _PendingAction("move", ("unit", 7), "original observation")
        state._pending_action = original
        result = await state.dismiss_popup()
        assert "OUTCOME_UNKNOWN" in result
        assert state._pending_action is original
        assert pending_facts(state)["action"] and pending_facts(state)["popup"]
        assert "OUTCOME_UNKNOWN" in await state.dismiss_popup()
        assert connection.closes == 1 and len(connection.writes) == 2
    asyncio.run(scenario())


def test_confirmed_empty_popup_does_not_leave_a_gate(monkeypatch):
    class NoPopup:
        async def execute_write(self, source, timeout=5):
            return ["INFORMATION_POPUPS|DONE"] if "INFORMATION_POPUPS" in source else []

    async def no_os_dialogs():
        return []

    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", no_os_dialogs)

    async def scenario():
        state = GameState(NoPopup())
        assert await state.dismiss_popup() == "No popups to dismiss."
        assert not state.popup_pending
    asyncio.run(scenario())

"""Notification capability arbitration — the dedication dead-lock fix.

Born from the hidden-jet-steppe-78 run (T56+): the engine emits
NOTIFICATION_COMMEMORATION_AVAILABLE under Standard Rules while the ruleset
has no CommemorationTypes rows, producing an unsatisfiable, undismissable,
uncircumventable "选择着力点" notice that directed the agent into an erroring
tool call for 40+ turns. A notification is evidence of an event, not proof
of a satisfiable obligation; the capability surface arbitrates.
"""

from __future__ import annotations

import asyncio

import pytest

from civ6_belief_engine.governance.capabilities import capabilities_for_ruleset
from civ_mcp.connection import LuaError
from civ_mcp.game_state import GameState
from civ_mcp.lua import notifications as lq
from civ_mcp.lua.models import GameNotification


def _notification(type_name: str, **overrides) -> GameNotification:
    payload = {
        "type_name": type_name,
        "message": "选择着力点",
        "turn": 56,
        "x": -1,
        "y": -1,
        "is_action_required": True,
        "resolution_hint": "get_dedications() then choose_dedication(dedication_index=...)",
    }
    payload.update(overrides)
    return GameNotification(**payload)


class TestDowngradeUnsatisfiableNotifications:
    def test_standard_ruleset_downgrades_all_expansion_only_notices(self):
        caps = capabilities_for_ruleset("RULESET_STANDARD")
        arbitrated = lq.downgrade_unsatisfiable_notifications(
            [
                _notification("NOTIFICATION_COMMEMORATION_AVAILABLE"),
                _notification("NOTIFICATION_WORLD_CONGRESS_RESULTS"),
                _notification("NOTIFICATION_GOVERNOR_APPOINTMENT_AVAILABLE"),
            ],
            caps,
        )
        for item in arbitrated:
            assert item.is_action_required is False
            assert item.resolution_hint is None
            assert "引擎残留" in item.message
            assert "忽略即可" in item.message
        assert "当前规则集无时代着力点机制" in arbitrated[0].message
        assert "当前规则集无世界议会机制" in arbitrated[1].message

    def test_expansion_ruleset_keeps_notices_actionable(self):
        caps = capabilities_for_ruleset("RULESET_EXPANSION_2")
        original = _notification("NOTIFICATION_COMMEMORATION_AVAILABLE")
        arbitrated = lq.downgrade_unsatisfiable_notifications([original], caps)
        assert arbitrated[0] is original

    def test_unknown_capability_object_disables_arbitration(self):
        arbitrated = lq.downgrade_unsatisfiable_notifications(
            [_notification("NOTIFICATION_COMMEMORATION_AVAILABLE")],
            object(),  # no dedications attribute
        )
        assert arbitrated[0].is_action_required is True

    def test_plain_notifications_pass_through_any_ruleset(self):
        caps = capabilities_for_ruleset("RULESET_STANDARD")
        original = _notification("NOTIFICATION_CHOOSE_TECH")
        arbitrated = lq.downgrade_unsatisfiable_notifications([original], caps)
        assert arbitrated[0] is original


class _FakeConn:
    """Routes Lua responses by query content; unexpected queries fail loud."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[str] = []

    async def execute_write(self, lua: str, **_kwargs):
        self.calls.append(lua)
        if "NotificationManager" in lua:
            return [
                "NOTIF|NOTIFICATION_COMMEMORATION_AVAILABLE|选择着力点|56|-1,-1",
                "TOTAL|1|1",
                "{SENTINEL}",
            ]
        if "ERR:" in self._responses[0] and "CommemorationTypes" in lua:
            return [self._responses[0]]
        if "VENABLED" in lua:  # the overview query prints the RULESET line
            game_row = (
                "1|0|法国|埃莉诺|100.0|5.0|10.0|8.0|4.0|None|None|1|1|50|0|0|3|5.0|2.0"
            )
            return [game_row, self._responses[0], "{SENTINEL}"]
        # Snapshot bootstrap inside get_game_overview parses and discards;
        # an empty answer is swallowed by its try/except.
        return ["{SENTINEL}"]

    async def execute_read(self, lua: str, **_kwargs):
        self.calls.append(lua)
        return ["1"]

    async def execute_mutation(self, lua: str, **_kwargs):
        self.calls.append(lua)
        raise AssertionError(f"mutation reached the connection: {lua[:80]}")


def _overview_lines(ruleset: str) -> list[str]:
    return [f"RULESET|{ruleset}", "{SENTINEL}"]


class TestGameStateArbitration:
    def test_get_notifications_applies_capability_arbitration(self):
        asyncio.run(self._arbitration_flow())

    async def _arbitration_flow(self):
        conn = _FakeConn(_overview_lines("RULESET_STANDARD"))
        gs = GameState(conn)
        result = await gs.get_notifications()
        assert len(result) == 1
        assert result[0].is_action_required is False
        assert result[0].resolution_hint is None
        assert "引擎残留" in result[0].message
        # The warm-up overview query happened exactly once — "VENABLED" is
        # only printed by the overview query — and the second read uses the
        # cache without another roundtrip.
        first = await gs.get_notifications()
        assert first[0].message == result[0].message
        assert len([c for c in conn.calls if "VENABLED" in c]) == 1

    def test_get_dedications_short_circuits_without_lua_probe(self):
        asyncio.run(self._short_circuit_flow())

    async def _short_circuit_flow(self):
        conn = _FakeConn(_overview_lines("RULESET_STANDARD"))
        gs = GameState(conn)
        await gs.get_game_overview()  # warm the cache
        calls_before = len(conn.calls)
        with pytest.raises(ValueError, match="NO_DEDICATIONS_IN_RULESET"):
            await gs.get_dedications()
        with pytest.raises(ValueError, match="引擎残留"):
            await gs.choose_dedication(0)
        # No Lua roundtrip for either call — the immutable capability was
        # already known.
        assert len(conn.calls) == calls_before

    def test_cold_cache_falls_through_to_lua_guard(self):
        asyncio.run(self._cold_cache_flow())

    async def _cold_cache_flow(self):
        conn = _FakeConn(["ERR:NO_DEDICATIONS_IN_RULESET|guard=MONUMENTALITY_ROW"])
        gs = GameState(conn)
        with pytest.raises(LuaError, match="NO_DEDICATIONS_IN_RULESET"):
            await gs.get_dedications()
        assert any("CommemorationTypes" in c or "gotEras" in c for c in conn.calls)

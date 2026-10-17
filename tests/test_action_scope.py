"""Submission scope is fresh native evidence, independent of report snapshots."""
import asyncio
import re

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.lua import TurnSnapshot


class ScopeEngine:
    def __init__(self):
        self.scope = ("CIVILIZATION_SUMERIA", 719665280, 68, 0)
        self.before_dispatch = None
        self.scope_rows = None
        self.ack_error = None
        self.ack_rows = None
        self.confirm_error = None
        self.operations = []
        self.probes = 0
        self.wires = []

    async def execute_write(self, code):
        self.wires.append(code)
        if 'print("ACTION_SCOPE|' in code:
            self.probes += 1
            if isinstance(self.scope_rows, BaseException):
                raise self.scope_rows
            return self.scope_rows if self.scope_rows is not None else ["ACTION_SCOPE|" + "|".join(map(str, self.scope))]
        if "ACTION_IDENTITY|" in code:
            return await self.execute_read(code)
        if "RequestOperation" not in code and "RequestCommand" not in code:
            return []  # Native combat preview, if requested.
        if self.before_dispatch:
            self.scope = self.before_dispatch
        # Model the native guard contract, rather than reusing Python internals.
        guard = re.search(r'-- ACTION_SUBMIT_SCOPE\|([^\n]+)', code)
        if guard and guard[1] != "|".join(map(str, self.scope)):
            return ["ERR:ACTION_SCOPE_NO_SUBMIT|Native action scope changed or unavailable"]
        self.operations.append(code)
        if self.ack_error:
            raise self.ack_error
        if self.ack_rows is not None:
            return self.ack_rows
        if "CityManager.RequestOperation" in code and "UnitOperationTypes" not in code:
            return ["OK:PRODUCING|UNIT_TRADER"]
        if "UnitOperationTypes.FOUND_CITY" in code:
            return ["OK:FOUNDED|19,17"]
        if "UnitOperationTypes.MOVE_TO" in code:
            return ["OK:MOVING_TO|18,18|from:19,17"]
        attack = "CITY_RANGE_ATTACK" if "CityManager.RequestCommand" in code else "RANGE_ATTACK"
        return [f"OK:{attack}|pre_hp:80/100|target_owner:63|target_id:123|attacks_before:1"]

    async def execute_read(self, code):
        if self.confirm_error:
            raise self.confirm_error
        identity = "ACTION_IDENTITY|" + "|".join(map(str, self.scope))
        return [identity, "CONFIRMED|queue verified", f"CITY_EXISTS|owner:{self.scope[3]}|city:65536",
                "POS|18|18|moves:1|busy:false", "UNIT|UNIT_SCOUT|55/100|owner:63|id:123", "CORE_BUSY|false"]


def current_state(engine):
    state = GameState(engine)
    state._game_identity = ("sumeria", 719665280)
    state._last_snapshot = TurnSnapshot(67, {}, {}, "", "")
    async def no_dismiss():
        return ""
    state.dismiss_popup = no_dismiss
    return state


def test_production_after_congress_uses_actual_turn_without_replacing_report_baseline():
    engine = ScopeEngine()
    state = current_state(engine)
    result = asyncio.run(state.set_city_production(65536, "UNIT", "UNIT_TRADER"))
    assert result == "PRODUCING|UNIT_TRADER"
    assert state.pending_action_summary is None
    assert state._last_snapshot.turn == 67
    assert len(engine.operations) == 1


def invoke(state, action):
    return {"production": lambda: state.set_city_production(65536, "UNIT", "UNIT_TRADER"),
            "move": lambda: state.move_unit(6, 18, 18),
            "found": lambda: state.found_city(6),
            "unit_attack": lambda: state.attack_unit(6, 18, 18),
            "city_attack": lambda: state.city_attack(65536, 18, 18)}[action]()


@pytest.mark.parametrize("action", ["production", "move", "found", "unit_attack", "city_attack"])
def test_every_tracked_action_uses_native_turn_and_keeps_report_baseline(action):
    engine = ScopeEngine()
    state = current_state(engine)
    result = asyncio.run(invoke(state, action))
    assert "OUTCOME_UNKNOWN" not in result
    assert "Error:" not in result
    assert state.pending_action_summary is None
    assert state._last_snapshot.turn == 67
    assert engine.probes == 1
    assert len(engine.operations) == 1
    code = engine.operations[0]
    request = "RequestCommand" if action == "city_attack" else "RequestOperation"
    assert code.index("ACTION_SCOPE_NO_SUBMIT") < code.index(request)
    for comparison in ('scope[1] ~= "CIVILIZATION_SUMERIA"', 'scope[2] ~= 719665280',
                       'scope[3] ~= 68', 'scope[4] ~= 0'):
        assert comparison in code[:code.index(request)]
    if action == "move":
        assert code.index("ACTION_SCOPE_NO_SUBMIT") < code.index("Events.UnitOperationSegmentComplete.Add")


@pytest.mark.parametrize("action", ["production", "move", "found", "unit_attack", "city_attack"])
@pytest.mark.parametrize("race", [
    ("CIVILIZATION_SUMERIA", 719665280, 69, 0),
    ("CIVILIZATION_ROME", 719665280, 68, 0),
    ("CIVILIZATION_SUMERIA", 11, 68, 0),
    ("CIVILIZATION_SUMERIA", 719665280, 68, 1),
])
def test_scope_change_between_probe_and_write_does_not_submit(action, race):
    engine = ScopeEngine()
    engine.before_dispatch = race
    state = current_state(engine)
    result = asyncio.run(invoke(state, action))
    assert "ACTION_SCOPE_NO_SUBMIT" in result
    assert not engine.operations
    assert state.pending_action_summary is None
    assert state._last_snapshot.turn == 67


@pytest.mark.parametrize("rows", [[], ["ACTION_SCOPE|truncated"],
    ["ACTION_SCOPE|CIVILIZATION_SUMERIA|719665280|68|0"] * 2,
    ["ACTION_SCOPE|CIVILIZATION_SUMERIA|719665280|nan|0"],
    ["ACTION_SCOPE|CIVILIZATION_SUMERIA|719665280|68|-1"],
    ["ACTION_SCOPE|CIVILIZATION_ROME|11|68|0"], ConnectionError("scope lost")])
def test_unknown_or_changed_submission_source_does_not_execute(rows):
    engine = ScopeEngine()
    engine.scope_rows = rows
    state = current_state(engine)
    assert "ACTION_SCOPE_UNAVAILABLE" in asyncio.run(invoke(state, "production"))
    assert not engine.operations
    assert state.pending_action_summary is None


@pytest.mark.parametrize("initialized", [False, True])
def test_native_submission_scope_is_required_without_a_report_snapshot(initialized):
    engine = ScopeEngine()
    state = current_state(engine)
    state._last_snapshot = None
    if not initialized:
        state._game_identity = None
    assert asyncio.run(invoke(state, "production")) == "PRODUCING|UNIT_TRADER"
    assert engine.probes == 1
    assert state._last_snapshot is None


def test_founding_uses_fresh_local_owner_instead_of_cached_report_owner():
    engine = ScopeEngine()
    engine.scope = ("CIVILIZATION_SUMERIA", 719665280, 68, 1)
    state = current_state(engine)
    assert asyncio.run(invoke(state, "found")) == "FOUNDED|19,17"
    assert state.pending_action_summary is None
    assert len(engine.operations) == 1


@pytest.mark.parametrize("action", ["production", "move", "found", "unit_attack", "city_attack"])
def test_lost_acknowledgement_retains_original_scope_without_replay(action):
    engine = ScopeEngine()
    engine.ack_error = ConnectionError("lost acknowledgement")
    state = current_state(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(invoke(state, action))
    engine.scope = ("CIVILIZATION_SUMERIA", 719665280, 69, 0)
    engine.confirm_error = ConnectionError("confirmation unavailable")
    assert "OUTCOME_UNKNOWN" in asyncio.run(invoke(state, action))
    engine.confirm_error = None
    # Different turn does not relabel the pending action from 68 to 69.
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.confirm_pending_action(wait=False))
    assert state.pending_action_summary
    assert len(engine.operations) == 1 and engine.probes == 1


def test_cancelled_dispatch_is_not_a_known_no_submission():
    engine = ScopeEngine()
    engine.ack_error = asyncio.CancelledError()
    state = current_state(engine)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(invoke(state, "production"))
    assert state.pending_action_summary
    assert asyncio.run(invoke(state, "production")) == "PRODUCING|UNIT_TRADER"
    assert len(engine.operations) == 1 and engine.probes == 1


def test_changed_local_owner_cannot_confirm_matching_queue():
    engine = ScopeEngine()
    engine.confirm_error = ConnectionError("lost read")
    state = current_state(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(invoke(state, "production"))
    engine.confirm_error = None
    engine.scope = ("CIVILIZATION_SUMERIA", 719665280, 68, 1)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.confirm_pending_action(wait=False))
    assert state.pending_action_summary
    assert len(engine.operations) == 1


@pytest.mark.parametrize("rows", [
    ["ERR:ACTION_SCOPE_NO_SUBMIT"],
    ["ERR:ACTION_SCOPE_NO_SUBMIT|Native action scope changed or unavailable", "OK:PRODUCING|UNIT_TRADER"],
    [],
])
def test_incomplete_guard_or_missing_ack_is_not_a_safe_no_submission(rows):
    engine = ScopeEngine()
    engine.ack_rows = rows
    engine.confirm_error = ConnectionError("fresh confirmation unavailable")
    state = current_state(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(invoke(state, "production"))
    assert state.pending_action_summary
    assert "OUTCOME_UNKNOWN" in asyncio.run(invoke(state, "production"))
    assert len(engine.operations) == 1 and engine.probes == 1


@pytest.mark.parametrize("row", [
    "ACTION_IDENTITY|CIVILIZATION_SUMERIA|719665280|68",  # owner absent
    "ACTION_IDENTITY|CIVILIZATION_SUMERIA|719665280|oops|0",
    "ACTION_IDENTITY|CIVILIZATION_SUMERIA|719665280|68|0|extra",
])
def test_incomplete_native_confirmation_keeps_original_intent(row):
    class IncompleteEngine(ScopeEngine):
        async def execute_read(self, code):
            return [row, "CONFIRMED|queue verified"]
    engine = IncompleteEngine()
    engine.confirm_error = ConnectionError("lost first read")
    state = current_state(engine)
    # Keep this check bounded without weakening the production confirmation.
    engine.ack_error = ConnectionError("lost acknowledgement")
    assert "OUTCOME_UNKNOWN" in asyncio.run(invoke(state, "production"))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.confirm_pending_action(wait=False))
    assert state.pending_action_summary
    assert len(engine.operations) == 1

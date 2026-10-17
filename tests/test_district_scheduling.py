"""District scheduling at the approved GameState / FireTuner boundary.

The fake records the outgoing Lua contract; native result meaning comes from
installed UI sources documented in the scheduling-repair native-facts note.
These checks do not claim to execute Civ6 Lua or validate native availability.
"""

import asyncio

from civ_mcp.connection import LuaError
from civ_mcp.game_state import GameState
from action_scope_support import scope_rows, with_action_identity


class DistrictConnection:
    def __init__(self, writes=(), reads=()):
        self.writes = list(writes)
        self.reads = list(reads)
        self.calls = []

    async def execute_write(self, lua):
        if 'print("ACTION_SCOPE|' in lua:
            return scope_rows()
        self.calls.append(("write", lua))
        # execute_write selects InGame, including read-only district queries.
        responses = self.reads if 'print("PRODUCTION|"' in lua else self.writes
        value = responses.pop(0) if responses else ConnectionError("no observation")
        if isinstance(value, Exception):
            raise value
        return with_action_identity(lua, value)

    async def execute_read(self, lua):
        self.calls.append(("read", lua))
        if "pCity:GetDistricts():Members()" in lua:
            raise LuaError("function expected instead of nil (GameCore district entities)")
        value = self.reads.pop(0) if self.reads else ConnectionError("no observation")
        if isinstance(value, Exception):
            raise value
        return with_action_identity(lua, value)


def test_exact_district_confirmation_uses_native_ingame_facts_without_resubmission():
    conn = DistrictConnection(
        writes=[["OK:PRODUCING|DISTRICT_CAMPUS|5 turns"]],
        reads=[["PRODUCTION|DISTRICT_CAMPUS", "DISTRICT|DISTRICT_CAMPUS|19|16"]],
    )
    state = GameState(conn)
    result = asyncio.run(state.set_city_production(65536, "DISTRICT", "DISTRICT_CAMPUS", 19, 16))
    assert result == "PRODUCING|DISTRICT_CAMPUS|5 turns"
    assert state.pending_action_summary is None
    assert [kind for kind, _ in conn.calls] == ["write", "write"]
    assert sum("CityManager.RequestOperation" in lua for _, lua in conn.calls) == 1
    confirmation = conn.calls[-1][1]
    assert "GetCurrentProductionTypeHash()" in confirmation
    assert "GameInfo.Districts[" in confirmation
    assert "CurrentlyBuilding()" not in confirmation
    assert "RequestOperation" not in confirmation and "RequestCommand" not in confirmation


def test_advisor_requests_only_native_available_plot_indices():
    conn = DistrictConnection(writes=[["DPLOT|19,16|2|0|0|0|0|2|Rainforest"]])
    placements = asyncio.run(GameState(conn).get_district_advisor(65536, "DISTRICT_CAMPUS"))
    assert [(p.x, p.y) for p in placements] == [(19, 16)]
    lua = conn.calls[0][1]
    # Other native result tables can contain plausible map indices too.
    # Restrict the outgoing contract, so BLOCKED_PLOTS cannot enter the score loop.
    assert "targets[CityOperationResults.PLOTS]" in lua
    assert "pairs(targets)" not in lua


def test_rejected_exact_plot_is_not_requested_or_left_pending():
    rejection = (
        "ERR:CANNOT_START|DISTRICT_CAMPUS|target:19,16|not submitted"
        "|native:LOC_CANNOT_REMOVE_FEATURE|feature:FEATURE_JUNGLE|owner:0"
    )
    conn = DistrictConnection(
        writes=[[rejection], ["OK:PRODUCING|UNIT_WARRIOR|2 turns"]],
        reads=[["CONFIRMED|production queue verified"]],
    )
    state = GameState(conn)
    result = asyncio.run(state.set_city_production(65536, "DISTRICT", "DISTRICT_CAMPUS", 19, 16))
    assert "native:LOC_CANNOT_REMOVE_FEATURE" in result
    assert "target:19,16" in result
    assert state.pending_action_summary is None
    lua = conn.calls[0][1]
    # Both native checks and submission must use the same precise parameters.
    assert "CityOperationTypes.PARAM_X] = 19" in lua
    assert "CityOperationTypes.PARAM_Y] = 16" in lua
    assert "CanStartOperation(pCity, CityOperationTypes.BUILD, tParams, true)" in lua
    assert lua.index('"ERR:CANNOT_START|') < lua.index("CityManager.RequestOperation")
    assert "if canStart == false then" in lua
    assert "CityOperationResults.FAILURE_REASONS" in lua
    assert "TECH_BRONZE_WORKING" not in lua
    assert len(conn.calls) == 1  # A definite preflight rejection needs no readback.
    assert asyncio.run(state.set_city_production(65536, "UNIT", "UNIT_WARRIOR")) == "PRODUCING|UNIT_WARRIOR|2 turns"


def test_district_requires_queue_and_exact_placement_after_delayed_readback():
    conn = DistrictConnection(
        writes=[["OK:PRODUCING|DISTRICT_CAMPUS|5 turns"]],
        reads=[
            ["CONFIRMED|production queue verified", "PRODUCTION|DISTRICT_CAMPUS", "DISTRICT|DISTRICT_CAMPUS|18|16"],
            ConnectionError("not yet placed"),
            ["PRODUCTION|DISTRICT_CAMPUS", "DISTRICT|DISTRICT_CAMPUS|19|16"],
        ],
    )
    state = GameState(conn)
    first = asyncio.run(state.set_city_production(65536, "DISTRICT", "DISTRICT_CAMPUS", 19, 16))
    assert "OUTCOME_UNKNOWN" in first
    assert "(19, 16)" in state.pending_action_summary
    # Repeating the intent performs confirmation only, never another request.
    assert asyncio.run(state.set_city_production(65536, "DISTRICT", "DISTRICT_CAMPUS", 19, 16)) == "PRODUCING|DISTRICT_CAMPUS|5 turns"
    assert state.pending_action_summary is None
    assert sum("CityManager.RequestOperation" in lua for _, lua in conn.calls) == 1


def test_lost_submission_acknowledgement_confirms_exact_plot_without_resending():
    conn = DistrictConnection(
        writes=[ConnectionError("acknowledgement lost")],
        reads=[["PRODUCTION|DISTRICT_CAMPUS", "DISTRICT|DISTRICT_CAMPUS|19.0|16.0"]],
    )
    state = GameState(conn)
    first = asyncio.run(state.set_city_production(65536, "DISTRICT", "DISTRICT_CAMPUS", 19, 16))
    assert "OUTCOME_UNKNOWN" in first
    assert "(19, 16)" in state.pending_action_summary
    assert asyncio.run(state.confirm_pending_action()) == "PRODUCING|DISTRICT_CAMPUS"
    assert sum("CityManager.RequestOperation" in lua for _, lua in conn.calls) == 1


def test_missing_queue_evidence_does_not_accept_an_existing_district():
    conn = DistrictConnection(
        writes=[["OK:PRODUCING|DISTRICT_CAMPUS"]],
        reads=[
            ["PRODUCTION|UNIT_WARRIOR", "DISTRICT|DISTRICT_CAMPUS|19|16"],
            ConnectionError("queue unavailable"),
            ["PRODUCTION|DISTRICT_CAMPUS", "DISTRICT|DISTRICT_CAMPUS|bad|16"],
            ConnectionError("placement unavailable"),
        ],
    )
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.set_city_production(65536, "DISTRICT", "DISTRICT_CAMPUS", 19, 16))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.purchase_item(65536, "UNIT", "UNIT_WARRIOR"))
    assert state.pending_action_summary is not None
    assert sum("CityManager.RequestOperation" in lua for _, lua in conn.calls) == 1
    query = next(lua for _, lua in conn.calls if 'print("PRODUCTION|"' in lua)
    assert "pCity:GetDistricts():Members()" in query
    assert 'print("PRODUCTION|"' in query
    assert "RequestOperation" not in query and "RequestCommand" not in query


def test_legacy_false_after_possible_submission_does_not_become_safe_to_retry():
    conn = DistrictConnection(
        writes=[["MAYBE:PRODUCING|DISTRICT_CAMPUS|canStart=false"]],
        reads=[ConnectionError("unknown placement"), ConnectionError("still unknown")],
    )
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.set_city_production(65536, "DISTRICT", "DISTRICT_CAMPUS", 19, 16))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.set_city_production(65536, "DISTRICT", "DISTRICT_CAMPUS", 18, 16))
    assert "(19, 16)" in state.pending_action_summary
    assert sum("CityManager.RequestOperation" in lua for _, lua in conn.calls) == 1


def test_unknown_preflight_explicitly_reports_no_submission_without_pending():
    conn = DistrictConnection(writes=[[
        "ERR:PREFLIGHT_UNAVAILABLE|DISTRICT_CAMPUS|target:19,16|not submitted|native eligibility unknown"
    ]])
    state = GameState(conn)
    result = asyncio.run(state.set_city_production(65536, "DISTRICT", "DISTRICT_CAMPUS", 19, 16))
    assert "PREFLIGHT_UNAVAILABLE" in result and "not submitted" in result
    assert state.pending_action_summary is None
    lua = conn.calls[0][1]
    guard = lua[lua.index('if not checkOk or type(canStart) ~= "boolean" then'):lua.index("if canStart == false then")]
    assert "PREFLIGHT_UNAVAILABLE" in guard and "return" in guard
    assert "RequestOperation" not in guard

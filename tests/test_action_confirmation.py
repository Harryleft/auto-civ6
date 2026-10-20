"""Delayed engine observations at the GameState / FireTuner boundary."""
import asyncio

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.lua import TurnSnapshot
from action_scope_support import DEFAULT_SCOPE, scope_rows, with_action_identity


pytestmark = pytest.mark.usefixtures('fast_action_confirmation')


class Engine:
    def __init__(self, writes=(), reads=()):
        self.writes = list(writes)
        self.reads = list(reads)
        self.submissions = []
        self.scope = DEFAULT_SCOPE
        self.fill_identity = True

    async def execute_write(self, code):
        if 'print("ACTION_SCOPE|' in code:
            return scope_rows(self.scope)
        if 'print("POS|' in code:
            return await self.execute_read(code)
        self.submissions.append(code)
        value = self.writes.pop(0)
        if isinstance(value, Exception):
            raise value
        return with_action_identity(code, value, self.scope, fill_missing=self.fill_identity)

    async def execute_read(self, code):
        value = self.reads.pop(0)
        if isinstance(value, Exception):
            raise value
        return with_action_identity(code, value, self.scope, fill_missing=self.fill_identity)


def test_production_waits_for_target_queue_without_resubmitting():
    engine = Engine(
        writes=[["OK:PRODUCING|UNIT_SCOUT"]],
        reads=[["NOT_SET|current=NONE"], ["NOT_SET|current=NONE"], ["CONFIRMED|queue verified"]],
    )
    state = GameState(engine)
    result = asyncio.run(state.set_city_production(65536, "UNIT", "UNIT_SCOUT"))
    assert result == "PRODUCING|UNIT_SCOUT"
    assert len(engine.submissions) == 1
    assert state.pending_action_summary is None


def test_lost_production_read_remains_unknown_and_can_be_confirmed_without_replay():
    engine = Engine(writes=[["OK:PRODUCING|UNIT_SCOUT"]], reads=[ConnectionError("lost"), ["CONFIRMED|queue verified"]])
    state = GameState(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.set_city_production(65536, "UNIT", "UNIT_SCOUT"))
    assert state.pending_action_summary
    assert asyncio.run(state.confirm_pending_action()) == "PRODUCING|UNIT_SCOUT"
    assert state.pending_action_summary is None
    assert len(engine.submissions) == 1


def test_unconfirmed_production_blocks_dependent_purchase():
    engine = Engine(writes=[["OK:PRODUCING|UNIT_SCOUT"]], reads=[ConnectionError("lost"), ConnectionError("still lost")])
    state = GameState(engine)
    asyncio.run(state.set_city_production(65536, "UNIT", "UNIT_SCOUT"))
    result = asyncio.run(state.purchase_item(65536, "UNIT", "UNIT_SCOUT"))
    assert "OUTCOME_UNKNOWN" in result
    assert len(engine.submissions) == 1


async def no_dismiss():
    return ""


def test_found_city_waits_for_owned_city_without_resending():
    engine = Engine(writes=[["OK:FOUNDED|19,17"]], reads=[["OK:NO_CITY"], ["CITY_EXISTS|owner:0|city:65536"]])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.found_city(0))
    assert result == "FOUNDED|19,17"
    assert len(engine.submissions) == 1


def test_movement_waits_through_old_and_intermediate_positions():
    engine = Engine(
        writes=[["OK:MOVING_TO|22,9|from:22,11"]],
        reads=[["POS|22|11|moves:3|busy:true"], ["POS|22|10|moves:1|busy:true"], ["POS|22|9|moves:1|busy:false"]],
    )
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.move_unit(0, 22, 9))
    assert "now_at:22,9" in result
    assert "BLOCKED" not in result and "exhausted" not in result
    assert len(engine.submissions) == 1


def test_attack_waits_for_real_damage_and_never_uses_estimate_as_actual():
    engine = Engine(
        writes=[["NATIVE_ESTIMATE|UNIT_SLINGER|UNIT_SCOUT|15|10|1|100|80|0|35"],
                ["OK:RANGE_ATTACK|pre_hp:80/100|target_owner:63|target_id:123|attacks_before:1"],
                ["UNIT|UNIT_SCOUT|80/100|owner:63|id:123"],
                ["UNIT|UNIT_SCOUT|55/100|owner:63|id:123", "CORE_BUSY|false"]],
    )
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.attack_unit(2, 12, 20))
    assert "damage dealt:25" in result
    assert "est damage dealt" not in result
    assert state.pending_action_summary is None
    assert sum("RequestOperation" in code for code in engine.submissions) == 1


def test_empty_attack_target_never_becomes_a_kill():
    engine = Engine(writes=[[], ["OK:RANGE_ATTACK|pre_hp:80/100|target_owner:63|target_id:123"], ["EMPTY"], ConnectionError("lost")])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.attack_unit(2, 12, 20))
    assert "OUTCOME_UNKNOWN" in result
    assert "killed" not in result.lower() and "eliminated" not in result.lower()
    assert state.pending_action_summary


def test_repeat_unknown_move_only_confirms_original_intent():
    engine = Engine(writes=[["OK:MOVING_TO|22,9|from:22,11"]], reads=[ConnectionError("lost"), ["POS|22|9|moves:1|busy:false"]])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(0, 22, 9))
    assert "now_at:22,9" in asyncio.run(state.move_unit(0, 22, 9))
    assert len(engine.submissions) == 1


def test_save_cannot_overtake_unconfirmed_move():
    engine = Engine(writes=[["OK:MOVING_TO|22,9|from:22,11"]], reads=[ConnectionError("lost"), ConnectionError("still lost")])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    asyncio.run(state.move_unit(0, 22, 9))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.save_game("new_checkpoint"))
    assert len(engine.submissions) == 1


def test_intermediate_position_without_move_count_stays_unknown():
    engine = Engine(writes=[["OK:MOVING_TO|22,9|from:22,11"]], reads=[["POS|22|10|busy:false"], ConnectionError("lost")])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.move_unit(0, 22, 9))
    assert "OUTCOME_UNKNOWN" in result
    assert "moves exhausted" not in result


def test_attack_consumption_confirms_zero_damage_without_claiming_missing_target_killed():
    engine = Engine(writes=[[], ["OK:RANGE_ATTACK|pre_hp:80/100|target_owner:63|target_id:123|attacks_before:1"], ["EMPTY", "ATTACKER|hp:100|moves:1|attacks:0|busy:false"]])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.attack_unit(2, 12, 20))
    assert "RANGE_ATTACK" in result and "OUTCOME_UNKNOWN" not in result
    assert "Target state unknown" in result
    assert "killed" not in result.lower()
    assert state.pending_action_summary is None


def test_matching_hp_of_different_enemy_does_not_confirm_attack():
    engine = Engine(writes=[[], ["OK:RANGE_ATTACK|pre_hp:80/100|target_owner:63|target_id:123"], ["UNIT|UNIT_SCOUT|30/100|owner:63|id:456"], ConnectionError("lost")])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.attack_unit(2, 12, 20))
    assert state.pending_action_summary


@pytest.mark.parametrize("change", [None, "military", "same_origin", "no_movement", "wrong_tile", "busy", "missing_position", "ranged"])
def test_civilian_entry_requires_original_ack_and_actual_completed_movement(change):
    ack = "OK:MELEE_ATTACK|pre_hp:100/100|target_owner:2|target_id:123|attacks_before:1|civilian_entry:1|origin_x:11|origin_y:20|moves_before:4"
    observed = "ATTACKER|hp:100|moves:3|attacks:1|busy:false|x:12|y:20"
    if change == "military":
        ack = ack.replace("civilian_entry:1", "civilian_entry:0")
    elif change == "same_origin":
        ack = ack.replace("origin_x:11", "origin_x:12")
    elif change == "no_movement":
        observed = observed.replace("moves:3", "moves:4")
    elif change == "wrong_tile":
        observed = observed.replace("x:12", "x:11")
    elif change == "busy":
        observed = observed.replace("busy:false", "busy:true")
    elif change == "missing_position":
        observed = observed.replace("|x:12|y:20", "")
    elif change == "ranged":
        ack = ack.replace("MELEE_ATTACK", "RANGE_ATTACK")
    engine = Engine(writes=[[], [ack], [observed, "CORE_BUSY|false"], ConnectionError("lost")])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.attack_unit(2, 12, 20))
    if change is None:
        assert "CIVILIAN_TILE_ENTERED" in result
        assert "Target fate unknown" in result
        assert state.pending_action_summary is None
    else:
        assert "OUTCOME_UNKNOWN" in result
        assert state.pending_action_summary
    assert sum("RequestOperation" in source for source in engine.submissions) == 1


def test_confirmation_in_another_world_does_not_clear_unknown():
    engine = Engine(writes=[["OK:PRODUCING|UNIT_SCOUT"]], reads=[["ACTION_IDENTITY|CIVILIZATION_ROME|11|1", "CONFIRMED|queue verified"], ConnectionError("lost")])
    state = GameState(engine)
    state._game_identity = ("sumeria", 719665280)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.set_city_production(65536, "UNIT", "UNIT_SCOUT"))
    assert state.pending_action_summary


def test_confirmation_budget_counts_a_query_that_never_returns(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, "ACTION_CONFIRM_TIMEOUT", 0.01)

    class DelayedEngine(Engine):
        async def execute_read(self, code):
            await asyncio.Event().wait()

    engine = DelayedEngine(writes=[["OK:PRODUCING|UNIT_SCOUT"]])
    state = GameState(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.set_city_production(65536, "UNIT", "UNIT_SCOUT"))
    assert len(engine.submissions) == 1
    assert state.pending_action_summary


def test_other_unit_action_cannot_bypass_pending_same_unit_move():
    engine = Engine(writes=[["OK:MOVING_TO|22,9|from:22,11"]], reads=[ConnectionError("lost"), ConnectionError("still lost")])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    asyncio.run(state.move_unit(0, 22, 9))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.fortify_unit(0))
    assert len(engine.submissions) == 1


def test_identity_refresh_does_not_clear_unknown_lifecycle_or_action():
    engine = Engine(writes=[["OK:PRODUCING|UNIT_SCOUT"], ["GAMESEED|CIVILIZATION_ROME|11"]], reads=[ConnectionError("lost")])
    state = GameState(engine)
    state._game_identity = ("sumeria", 719665280)
    asyncio.run(state.set_city_production(65536, "UNIT", "UNIT_SCOUT"))
    state._pending_load = True
    state._pending_save = True
    assert asyncio.run(state.get_game_identity()) == ("rome", 11)
    assert state.pending_action_summary
    assert state._pending_load and state._pending_save


def test_wrong_owner_city_does_not_confirm_founding():
    engine = Engine(writes=[["OK:FOUNDED|19,17"]], reads=[["CITY_EXISTS|owner:1"], ConnectionError("lost")])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.found_city(0))
    assert state.pending_action_summary


def test_changed_combat_hp_does_not_bypass_busy_engine():
    engine = Engine(writes=[[], ["OK:RANGE_ATTACK|pre_hp:80/100|target_owner:63|target_id:123|attacks_before:1"], ["UNIT|UNIT_SCOUT|55/100|owner:63|id:123", "CORE_BUSY|true"], ["UNIT|UNIT_SCOUT|55/100|owner:63|id:123", "CORE_BUSY|false"]])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    assert "damage dealt:25" in asyncio.run(state.attack_unit(2, 12, 20))
    assert engine.writes == []


def test_changed_city_attack_hp_does_not_bypass_busy_engine():
    engine = Engine(writes=[["OK:CITY_RANGE_ATTACK|pre_hp:80/100|target_owner:63|target_id:123"], ["UNIT|UNIT_SCOUT|55/100|owner:63|id:123", "CORE_BUSY|true"], ["UNIT|UNIT_SCOUT|55/100|owner:63|id:123", "CORE_BUSY|false"]])
    state = GameState(engine)
    assert "damage dealt:25" in asyncio.run(state.city_attack(65536, 12, 20))
    assert engine.writes == []


def test_later_turn_path_label_alone_does_not_confirm_a_partial_move():
    engine = Engine(writes=[["OK:MOVING_TO|14,13|from:14,15"]], reads=[
        ["POS|14|14|moves:1|busy:true", "PATH_STEP|14|13|turn:2"],
        ["POS|14|14|moves:1|busy:false", "PATH_STEP|14|13|turn:2"],
        ConnectionError("no reliable native stop signal"),
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.move_unit(1, 14, 13))
    assert "OUTCOME_UNKNOWN" in result
    assert state.pending_action_summary
    assert "STOPPED_MID_PATH" not in result
    assert "moves exhausted" not in result
    assert len(engine.submissions) == 1
    assert engine.reads == []


@pytest.mark.parametrize("busy", ["unknown", "true"])
def test_later_turn_path_does_not_confirm_move_while_engine_idle_is_unknown(busy):
    engine = Engine(writes=[["OK:MOVING_TO|14,13|from:14,15"]], reads=[
        [f"POS|14|14|moves:1|busy:{busy}", "PATH_STEP|14|13|turn:2"], ConnectionError("lost"),
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(1, 14, 13))
    assert state.pending_action_summary


def test_idle_partial_move_with_native_step_still_reachable_stays_unknown():
    engine = Engine(writes=[["OK:MOVING_TO|14,13|from:14,15"]], reads=[
        ["POS|14|14|moves:1|busy:false", "PATH_STEP|14|13|turn:1"], ConnectionError("lost"),
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(1, 14, 13))
    assert state.pending_action_summary


def test_turn_22_partial_move_confirms_native_movement_exhaustion_with_real_moves():
    engine = Engine(writes=[["OK:MOVING_TO|17,15|from:15,12"]], reads=[
        ["POS|15|12|moves:2|movement_moves:2|zoc:false|busy:true"],
        ["POS|15|13|moves:1|movement_moves:0|zoc:false|busy:true", "PATH_STEP|15|14|turn:1"],
        ["POS|15|13|moves:1|movement_moves:0|zoc:false|busy:false", "PATH_STEP|15|14|turn:1"],
        ConnectionError("no further observation"),
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.move_unit(1, 17, 15))
    assert "OUTCOME_UNKNOWN" not in result
    assert "now_at:15,13" in result
    assert "moves_remaining:1" in result
    assert "STOPPED_MID_PATH (native movement exhausted)" in result
    assert "now_at:17,15" not in result
    assert len(engine.submissions) == 1


def test_native_zoc_confirms_partial_stop_without_claiming_moves_exhausted():
    engine = Engine(writes=[["OK:MOVING_TO|17,15|from:15,12"]], reads=[
        ["POS|15|13|moves:1|movement_moves:1|zoc:true|busy:false", "PATH_STEP|15|14|turn:1"],
        ConnectionError("no further observation"),
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.move_unit(1, 17, 15))
    assert "OUTCOME_UNKNOWN" not in result
    assert "now_at:15,13" in result and "moves_remaining:1" in result
    assert "STOPPED_MID_PATH (entered zone of control)" in result
    assert "exhausted" not in result
    assert len(engine.submissions) == 1


@pytest.mark.parametrize("activity", ["", "|busy:unknown", "|busy:true"])
def test_arrival_without_confirmed_idle_keeps_movement_pending(activity):
    engine = Engine(writes=[["OK:MOVING_TO|17,15|from:15,12"]], reads=[
        [f"POS|17|15|moves:1{activity}"], ConnectionError("idle state unavailable"),
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(1, 17, 15))
    assert state.pending_action_summary
    assert len(engine.submissions) == 1


@pytest.mark.parametrize("native", ["", "|movement_moves:1|zoc:false", "|movement_moves:unknown|zoc:unknown"])
def test_repeated_idle_partial_position_without_native_stop_remains_unknown(native):
    observation = [f"POS|15|13|moves:1{native}|busy:false", "PATH_STEP|15|14|turn:1"]
    engine = Engine(writes=[["OK:MOVING_TO|17,15|from:15,12"]], reads=[
        observation, observation, ConnectionError("no reliable stop evidence"),
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    result = asyncio.run(state.move_unit(1, 17, 15))
    assert "OUTCOME_UNKNOWN" in result
    assert "STOPPED_MID_PATH" not in result
    assert state.pending_action_summary
    assert len(engine.submissions) == 1


@pytest.mark.parametrize("native", ["movement_moves:0", "zoc:true"])
@pytest.mark.parametrize("position,busy", [("15|12", "false"), ("15|13", "true"), ("15|13", "unknown")])
def test_native_stop_signal_without_observed_progress_and_idle_is_insufficient(native, position, busy):
    engine = Engine(writes=[["OK:MOVING_TO|17,15|from:15,12"]], reads=[
        [f"POS|{position}|moves:1|{native}|busy:{busy}"], ConnectionError("incomplete stop evidence"),
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(1, 17, 15))
    assert state.pending_action_summary
    assert len(engine.submissions) == 1


def test_read_only_unit_query_confirms_lost_move_without_replaying():
    engine = Engine(writes=[["OK:MOVING_TO|17,15|from:15,12"], []], reads=[
        ConnectionError("lost"), ["POS|15|13|moves:1|movement_moves:0|busy:false"],
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(1, 17, 15))
    assert asyncio.run(state.get_units()) == []
    assert state.pending_action_summary is None
    assert sum("RequestOperation" in code for code in engine.submissions) == 1


@pytest.mark.parametrize("operation", ["end_turn", "save", "fortify"])
def test_ambiguous_turn_22_movement_blocks_later_mutations_after_identity_refresh(operation):
    engine = Engine(writes=[
        ["OK:MOVING_TO|17,15|from:15,12"], ["GAMESEED|CIVILIZATION_SUMERIA|719665280"],
    ], reads=[
        ["ACTION_IDENTITY|CIVILIZATION_SUMERIA|719665280|22", "POS|15|13|moves:1|busy:false", "PATH_STEP|15|14|turn:1"],
        ConnectionError("stop evidence unavailable"), ConnectionError("still unknown"),
    ])
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    state._game_identity = ("sumeria", 719665280)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(1, 17, 15))
    assert asyncio.run(state.get_game_identity()) == ("sumeria", 719665280)
    if operation == "end_turn":
        result = asyncio.run(state.end_turn())
    elif operation == "save":
        result = asyncio.run(state.save_game("isolated_checkpoint"))
    else:
        result = asyncio.run(state.fortify_unit(1))
    assert "OUTCOME_UNKNOWN" in result
    assert state.pending_action_summary
    assert len(engine.submissions) == 2  # MOVE_TO and identity read only.


@pytest.mark.parametrize("identity", [None, "CIVILIZATION_ROME|11|22", "CIVILIZATION_SUMERIA|719665280|23"])
def test_native_movement_stop_from_missing_or_changed_identity_stays_unknown(identity):
    observation = ["POS|15|13|moves:1|movement_moves:0|zoc:true|busy:false"]
    if identity:
        observation.insert(0, f"ACTION_IDENTITY|{identity}")
    engine = Engine(writes=[["OK:MOVING_TO|17,15|from:15,12"]], reads=[
        observation, ConnectionError("same-world evidence unavailable"),
    ])
    state = GameState(engine)
    engine.fill_identity = False
    state.dismiss_popup = no_dismiss
    state._game_identity = ("sumeria", 719665280)
    state._last_snapshot = TurnSnapshot(turn=22, units={}, cities={}, current_research="", current_civic="")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(1, 17, 15))
    assert state.pending_action_summary
    assert len(engine.submissions) == 1


class SegmentEngine(Engine):
    """Native request record returned by the FireTuner boundary."""
    def __init__(self, observations=None):
        super().__init__(writes=[["OK:MOVING_TO|18,18|from:19,17"]])
        self.observations = observations or [{}]
        self.read_codes = []
        self.scope = ("CIVILIZATION_SUMERIA", 719665280, 54, 0)

    async def execute_read(self, code):
        import re
        self.read_codes.append(code)
        if not self.observations:
            raise ConnectionError("no further native observation")
        observation = self.observations.pop(0)
        if isinstance(observation, BaseException):
            raise observation
        token = re.search(r'token="([a-f0-9]+)"', self.submissions[0])
        token = token[1] if token else "missing-current-token"
        values = dict(token=token, owner="0", unit="851974", civ="CIVILIZATION_SUMERIA",
                      seed="719665280", turn="54", origin="19|17", target="18|18",
                      done="true", pos="19|18", busy="false", queued="18|18", step="2",
                      pos_id="851974", moves="1")
        values.update(observation)
        return [
            "ACTION_IDENTITY|CIVILIZATION_SUMERIA|719665280|54|0",
            f"POS|{values['pos']}|moves:{values['moves']}|movement_moves:1|zoc:false|busy:{values['busy']}|owner:0|id:{values['pos_id']}",
            f"MOVE_WITNESS|{values['token']}|{values['owner']}|{values['unit']}|{values['civ']}|{values['seed']}|{values['turn']}|{values['origin']}|{values['target']}|{values['done']}|rawCommand:177|rawData:9",
            f"QUEUED_TARGET|{values['queued']}",
            f"PATH_STEP|18|18|turn:{values['step']}",
        ]


def segment_state(engine):
    state = GameState(engine)
    state.dismiss_popup = no_dismiss
    state._game_identity = ("sumeria", 719665280)
    state._last_snapshot = TurnSnapshot(turn=54, units={}, cities={}, current_research="", current_civic="")
    return state


def test_turn_54_native_segment_witness_confirms_partial_move_with_real_remaining_moves():
    engine = SegmentEngine()
    state = segment_state(engine)
    result = asyncio.run(state.move_unit(6, 18, 18))
    assert "STOPPED_MID_PATH (native segment complete)" in result
    assert "now_at:19,18" in result and "moves_remaining:1" in result
    assert state.pending_action_summary is None
    assert sum("RequestOperation" in code for code in engine.submissions) == 1


@pytest.mark.parametrize("change", [
    {"token": "old-token"}, {"owner": "1"}, {"unit": "917510"},
    {"pos_id": "917510"}, {"civ": "CIVILIZATION_ROME"}, {"seed": "11"},
    {"turn": "55"}, {"origin": "19|16"}, {"target": "17|18"},
    {"done": "false"}, {"busy": "true"}, {"busy": "unknown"},
    {"pos": "19|17"}, {"queued": "17|18"},
])
def test_segment_event_without_all_current_request_facts_keeps_movement_unknown(change):
    engine = SegmentEngine([change])
    state = segment_state(engine)
    result = asyncio.run(state.move_unit(6, 18, 18))
    assert "OUTCOME_UNKNOWN" in result
    assert "STOPPED_MID_PATH" not in result
    assert state.pending_action_summary
    assert sum("RequestOperation" in code for code in engine.submissions) == 1


def test_late_segment_witness_confirms_after_read_loss_without_move_replay():
    engine = SegmentEngine([ConnectionError("lost observation"), {}])
    state = segment_state(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(6, 18, 18))
    assert "native segment complete" in asyncio.run(state.confirm_pending_action())
    assert sum("RequestOperation" in code for code in engine.submissions) == 1


def test_cancelled_segment_read_keeps_witness_for_later_confirmation():
    engine = SegmentEngine([asyncio.CancelledError(), {}])
    state = segment_state(engine)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.move_unit(6, 18, 18))
    assert state.pending_action_summary
    assert "native segment complete" in asyncio.run(state.confirm_pending_action())
    assert sum("RequestOperation" in code for code in engine.submissions) == 1


def test_lost_move_acknowledgement_can_use_its_prearmed_native_record_without_replay():
    engine = SegmentEngine()
    engine.writes = [ConnectionError("lost acknowledgement")]
    state = segment_state(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(6, 18, 18))
    assert "native segment complete" in asyncio.run(state.confirm_pending_action())
    assert sum("RequestOperation" in code for code in engine.submissions) == 1


def test_partial_segment_confirmation_preserves_untyped_native_payload_for_live_diagnosis():
    result = asyncio.run(segment_state(SegmentEngine()).move_unit(6, 18, 18))
    assert "rawCommand:177|rawData:9" in result


def test_truncated_position_response_with_segment_witness_stays_unknown():
    class TruncatedEngine(SegmentEngine):
        async def execute_read(self, code):
            rows = await super().execute_read(code)
            return ["POS|19" if row.startswith("POS|") else row for row in rows]

    engine = TruncatedEngine()
    state = segment_state(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(6, 18, 18))
    assert state.pending_action_summary


@pytest.mark.parametrize("bad_row", [
    "MOVE_WITNESS|truncated", "QUEUED_TARGET|18", "MOVE_WITNESS|missing",
])
def test_incomplete_native_segment_response_never_clears_unknown(bad_row):
    class IncompleteEngine(SegmentEngine):
        async def execute_read(self, code):
            rows = await super().execute_read(code)
            prefix = bad_row.split("|", 1)[0] + "|"
            return [bad_row if row.startswith(prefix) else row for row in rows]

    state = segment_state(IncompleteEngine())
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(6, 18, 18))
    assert state.pending_action_summary


@pytest.mark.parametrize("missing", ["MOVE_WITNESS|", "QUEUED_TARGET|"])
def test_missing_native_segment_fact_never_clears_unknown(missing):
    class MissingEngine(SegmentEngine):
        async def execute_read(self, code):
            return [row for row in await super().execute_read(code) if not row.startswith(missing)]

    state = segment_state(MissingEngine())
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(6, 18, 18))
    assert state.pending_action_summary


@pytest.mark.parametrize("lose_ack", [False, True])
def test_concurrent_same_production_intent_submits_once(lose_ack):
    class ConcurrentEngine(Engine):
        def __init__(self):
            super().__init__()
            self.scope_reads = 0
            self.scoped = asyncio.Event()

        async def execute_write(self, code):
            if 'print("ACTION_SCOPE|' in code:
                self.scope_reads += 1
                if self.scope_reads == 2:
                    self.scoped.set()
                await self.scoped.wait()
                return scope_rows(self.scope)
            self.submissions.append(code)
            await asyncio.sleep(0)
            if lose_ack:
                raise ConnectionError("lost after independent confirmation")
            return ["OK:PRODUCING|UNIT_SCOUT"]

        async def execute_read(self, code):
            return with_action_identity(code, ["CONFIRMED|queue verified"], self.scope)

    async def scenario():
        engine = ConcurrentEngine()
        state = GameState(engine)
        results = await asyncio.gather(
            state.set_city_production(65536, "UNIT", "UNIT_SCOUT"),
            state.set_city_production(65536, "UNIT", "UNIT_SCOUT"),
        )
        assert len(engine.submissions) == 1
        assert results == ["PRODUCING|UNIT_SCOUT", "PRODUCING|UNIT_SCOUT"]
        assert state.pending_action_summary is None

    asyncio.run(scenario())

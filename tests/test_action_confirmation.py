"""Delayed engine observations at the GameState / FireTuner boundary."""
import asyncio

from civ_mcp.game_state import GameState


class Engine:
    def __init__(self, writes=(), reads=()):
        self.writes = list(writes)
        self.reads = list(reads)
        self.submissions = []

    async def execute_write(self, code):
        if 'print("POS|' in code:
            return await self.execute_read(code)
        self.submissions.append(code)
        value = self.writes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def execute_read(self, code):
        value = self.reads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


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
        writes=[["ESTIMATE|UNIT_SLINGER|UNIT_SCOUT|15|10|1||100|80"],
                ["OK:RANGE_ATTACK|pre_hp:80/100|target_owner:63|target_id:123|attacks_before:1"],
                ["UNIT|UNIT_SCOUT|80/100|owner:63|id:123"],
                ["UNIT|UNIT_SCOUT|55/100|owner:63|id:123"]],
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

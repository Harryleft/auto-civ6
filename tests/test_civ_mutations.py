"""G2: action construction never promotes estimates or missing reads to success."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp.civ.mutations import CivMutationFactory
from civ_mcp.runtime.contracts import Evidence, OperationId
from civ_mcp.runtime.session import MutationPreconditionError


def test_move_uses_unit_position_readback_as_confirmation() -> None:
    class Adapter:
        calls = 0

        async def read_units(self, *, observed_turn):
            self.calls += 1
            unit = (
                SimpleNamespace(unit_index=3, x=4, y=6)
                if self.calls == 1
                else SimpleNamespace(unit_index=3, x=5, y=7)
            )
            return SimpleNamespace(value=[unit], observed_turn=observed_turn)

    adapter = Adapter()
    execution = CivMutationFactory(adapter).move_unit(
        operation_id=OperationId("move-3"), unit_index=3, target_x=5, target_y=7, observed_turn=12
    )
    asyncio.run(execution.precheck())
    evidence = asyncio.run(execution.verify())
    assert evidence.source == "read_units"
    assert adapter.calls == 2
    assert execution.intent.tool == execution.request.tool == "move_unit"


def test_attack_without_factual_readback_remains_unconfirmed() -> None:
    class Adapter:
        pass

    async def missing_readback():
        return None

    execution = CivMutationFactory(Adapter()).attack_unit(
        operation_id=OperationId("attack-3"), unit_index=3, target_x=5, target_y=7, readback=missing_readback
    )
    assert asyncio.run(execution.verify()) is None


def test_attack_accepts_explicit_domain_evidence_only() -> None:
    class Adapter:
        pass

    async def confirmed_readback():
        return Evidence("read_units", 12, "target has a confirmed state transition")

    execution = CivMutationFactory(Adapter()).attack_unit(
        operation_id=OperationId("attack-4"), unit_index=4, target_x=5, target_y=7, readback=confirmed_readback
    )
    assert asyncio.run(execution.verify()).source == "read_units"


def test_city_attack_requires_explicit_domain_readback() -> None:
    class Adapter:
        pass

    async def readback() -> Evidence:
        return Evidence("read_units", 12, "target has an observed post-combat state")

    execution = CivMutationFactory(Adapter()).attack_city(
        operation_id=OperationId("city-attack-4"),
        city_id=4,
        target_x=5,
        target_y=7,
        readback=readback,
    )
    assert execution.intent.tool == execution.request.tool == "city_attack"
    assert "CityCommandTypes.RANGE_ATTACK" in execution.request.lua_code
    assert asyncio.run(execution.verify()).source == "read_units"


def test_production_requires_city_queue_readback() -> None:
    class Adapter:
        calls = 0

        async def read_cities(self, *, observed_turn):
            self.calls += 1
            building = "UNIT_WARRIOR" if self.calls == 1 else "UNIT_ARCHER"
            city = SimpleNamespace(city_id=4, currently_building=building)
            return SimpleNamespace(value=[city], observed_turn=observed_turn)

    adapter = Adapter()
    execution = CivMutationFactory(adapter).set_production(
        operation_id=OperationId("production-4"), city_id=4, item_type="UNIT", item_name="UNIT_ARCHER", observed_turn=12
    )
    asyncio.run(execution.precheck())
    assert asyncio.run(execution.verify()).source == "read_cities"
    assert adapter.calls == 2


def test_founding_requires_a_new_city_at_the_observed_settler_tile() -> None:
    class Adapter:
        city_calls = 0

        async def read_cities(self, *, observed_turn):
            self.city_calls += 1
            old_city = SimpleNamespace(city_id=4, x=1, y=1)
            cities = [old_city]
            if self.city_calls > 1:
                cities.append(SimpleNamespace(city_id=8, x=5, y=7))
            return SimpleNamespace(value=cities, observed_turn=observed_turn)

        async def read_units(self, *, observed_turn):
            settler = SimpleNamespace(unit_index=2, x=5, y=7)
            return SimpleNamespace(value=[settler], observed_turn=observed_turn)

    adapter = Adapter()
    execution = CivMutationFactory(adapter).found_city(
        operation_id=OperationId("found-city-1"),
        unit_index=2,
        target_x=5,
        target_y=7,
        observed_turn=12,
    )
    asyncio.run(execution.precheck())
    assert execution.intent.tool == execution.request.tool == "found_city"
    assert asyncio.run(execution.verify()).source == "read_cities"
    assert adapter.city_calls == 2


def test_founding_rejects_a_tile_that_already_has_a_city() -> None:
    class Adapter:
        async def read_cities(self, *, observed_turn):
            city = SimpleNamespace(city_id=4, x=5, y=7)
            return SimpleNamespace(value=[city], observed_turn=observed_turn)

    execution = CivMutationFactory(Adapter()).found_city(
        operation_id=OperationId("found-city-known"),
        unit_index=2,
        target_x=5,
        target_y=7,
        observed_turn=12,
    )
    with pytest.raises(MutationPreconditionError, match="已有城市"):
        asyncio.run(execution.precheck())


def test_city_capture_requires_a_current_choice_and_post_decision_ownership() -> None:
    pending = SimpleNamespace(
        city_id=9,
        x=4,
        y=5,
        owner_id=0,
        original_owner_id=2,
        previous_owner_id=3,
        allowed_choices=("KEEP", "RAZE"),
    )

    class Adapter:
        calls = 0

        async def read_pending_city_capture(self, *, observed_turn):
            self.calls += 1
            return SimpleNamespace(
                value=pending if self.calls == 1 else None,
                observed_turn=observed_turn,
            )

        async def read_city_capture_state(self, *, x, y, observed_turn):
            assert (x, y) == (4, 5)
            return SimpleNamespace(
                value=SimpleNamespace(city_id=9, owner_id=0),
                observed_turn=observed_turn,
            )

    execution = CivMutationFactory(Adapter()).resolve_city_capture(
        operation_id=OperationId("capture-9"), city_id=9, choice="KEEP", observed_turn=12
    )

    asyncio.run(execution.precheck())
    evidence = asyncio.run(execution.verify())

    assert execution.intent.tool == execution.request.tool == "resolve_city_capture"
    assert "CityDestroyDirectives.KEEP" in execution.request.lua_code
    assert evidence.source == "read_pending_city_capture+read_city_capture_state"


def test_city_capture_rejects_choices_not_allowed_by_current_game_state() -> None:
    class Adapter:
        async def read_pending_city_capture(self, *, observed_turn):
            return SimpleNamespace(
                value=SimpleNamespace(city_id=9, allowed_choices=("KEEP",)),
                observed_turn=observed_turn,
            )

    execution = CivMutationFactory(Adapter()).resolve_city_capture(
        operation_id=OperationId("capture-invalid"),
        city_id=9,
        choice="RAZE",
        observed_turn=12,
    )

    with pytest.raises(MutationPreconditionError, match="当前允许"):
        asyncio.run(execution.precheck())


def test_purchase_requires_gold_change_and_new_unit() -> None:
    class Adapter:
        async def read_overview(self):
            return SimpleNamespace(value=SimpleNamespace(gold=50), observed_turn=12)

        async def read_units(self, *, observed_turn):
            unit = SimpleNamespace(unit_id=9, unit_type="UNIT_ARCHER")
            return SimpleNamespace(value=[unit], observed_turn=observed_turn)

    execution = CivMutationFactory(Adapter()).purchase_item(
        operation_id=OperationId("purchase-4"), city_id=4, item_type="UNIT", item_name="UNIT_ARCHER", yield_type="YIELD_GOLD", currency_before=100, observed_turn=12, known_unit_ids=frozenset({1})
    )
    assert asyncio.run(execution.verify()).source == "read_overview+read_units"


def test_builder_mutations_are_hash_bound_and_need_tile_readback() -> None:
    class Adapter:
        pass

    async def readback() -> Evidence:
        return Evidence("read_map", 12, "tile state transition observed")

    factory = CivMutationFactory(Adapter())
    improvement = factory.improve_tile(
        operation_id=OperationId("improvement-1"),
        unit_index=2,
        improvement_name="IMPROVEMENT_FARM",
        readback=readback,
    )
    repair = factory.repair_improvement(
        operation_id=OperationId("repair-1"), unit_index=2, readback=readback
    )
    demolition = factory.remove_improvement(
        operation_id=OperationId("demolish-1"), unit_index=2, readback=readback
    )
    harvest = factory.remove_feature(
        operation_id=OperationId("harvest-1"), unit_index=2, readback=readback
    )
    route = factory.build_route(
        operation_id=OperationId("route-1"), unit_index=2, readback=readback
    )
    assert [
        improvement.intent.tool,
        repair.intent.tool,
        demolition.intent.tool,
        harvest.intent.tool,
        route.intent.tool,
    ] == [
        "improve_tile",
        "repair_improvement",
        "remove_improvement",
        "remove_feature",
        "build_route",
    ]
    assert "BUILD_IMPROVEMENT" in improvement.request.lua_code
    assert "REPAIR" in repair.request.lua_code
    assert "REMOVE_IMPROVEMENT" in demolition.request.lua_code
    assert "REMOVE_FEATURE" in harvest.request.lua_code
    assert "BUILD_ROUTE" in route.request.lua_code
    assert asyncio.run(route.verify()).source == "read_map"


def test_city_controls_require_their_own_domain_readback() -> None:
    class Adapter:
        pass

    async def readback() -> Evidence:
        return Evidence("read_map", 12, "city ownership/focus state observed")

    factory = CivMutationFactory(Adapter())
    tile = factory.purchase_tile(
        operation_id=OperationId("tile-1"), city_id=4, x=5, y=7, readback=readback
    )
    focus = factory.set_city_focus(
        operation_id=OperationId("focus-1"), city_id=4, focus="production", readback=readback
    )
    assert tile.intent.tool == "purchase_tile"
    assert focus.intent.tool == "set_city_focus"
    assert "PURCHASE" in tile.request.lua_code
    assert "SET_FOCUS" in focus.request.lua_code
    assert asyncio.run(focus.verify()).source == "read_map"


def test_trade_proposal_preserves_the_exact_hash_bound_terms() -> None:
    class Adapter:
        pass

    async def no_acceptance_evidence():
        return None

    execution = CivMutationFactory(Adapter()).propose_trade(
        operation_id=OperationId("trade-4"), other_player_id=2,
        offer_items=[{"type": "GOLD", "amount": 50}], request_items=[], readback=no_acceptance_evidence,
    )
    assert "DealProposalAction.ACCEPTED" not in execution.request.lua_code
    assert asyncio.run(execution.verify()) is None


def test_diplomacy_response_requires_a_fresh_session_transition() -> None:
    initial = SimpleNamespace(
        session_id=9,
        other_player_id=2,
        dialogue_text="Greetings",
        reason_text="First meeting",
        buttons="Accept;Reject",
        deal_summary="",
    )
    advanced = SimpleNamespace(
        session_id=9,
        other_player_id=2,
        dialogue_text="A new response",
        reason_text="First meeting",
        buttons="Accept;Reject",
        deal_summary="",
    )

    class Adapter:
        calls = 0

        async def read_diplomacy_sessions(self, *, observed_turn):
            self.calls += 1
            session = initial if self.calls == 1 else advanced
            return SimpleNamespace(value=[session], observed_turn=observed_turn)

    adapter = Adapter()
    execution = CivMutationFactory(adapter).respond_to_diplomacy(
        operation_id=OperationId("diplomacy-9"),
        other_player_id=2,
        response="positive",
        observed_turn=12,
    )
    asyncio.run(execution.precheck())
    evidence = asyncio.run(execution.verify())
    assert evidence.source == "read_diplomacy_sessions"
    assert "POSITIVE" in execution.request.lua_code
    assert adapter.calls == 2


def test_diplomacy_response_does_not_confirm_an_unchanged_session() -> None:
    session = SimpleNamespace(
        session_id=9,
        other_player_id=2,
        dialogue_text="Greetings",
        reason_text="First meeting",
        buttons="Accept;Reject",
        deal_summary="",
    )

    class Adapter:
        async def read_diplomacy_sessions(self, *, observed_turn):
            return SimpleNamespace(value=[session], observed_turn=observed_turn)

    execution = CivMutationFactory(Adapter()).respond_to_diplomacy(
        operation_id=OperationId("diplomacy-stale"),
        other_player_id=2,
        response="NEGATIVE",
        observed_turn=12,
    )
    asyncio.run(execution.precheck())
    assert asyncio.run(execution.verify()) is None


def test_research_and_civic_require_fresh_typed_selection_evidence() -> None:
    before = SimpleNamespace(
        current_research_type="TECH_MINING",
        current_civic_type="CIVIC_CODE_OF_LAWS",
        available_techs=[SimpleNamespace(tech_type="TECH_WRITING")],
        available_civics=[SimpleNamespace(civic_type="CIVIC_CRAFTSMANSHIP")],
    )
    after_research = SimpleNamespace(
        current_research_type="TECH_WRITING",
        current_civic_type="CIVIC_CODE_OF_LAWS",
        available_techs=[SimpleNamespace(tech_type="TECH_WRITING")],
        available_civics=[SimpleNamespace(civic_type="CIVIC_CRAFTSMANSHIP")],
    )
    after_civic = SimpleNamespace(
        current_research_type="TECH_WRITING",
        current_civic_type="CIVIC_CRAFTSMANSHIP",
        available_techs=[SimpleNamespace(tech_type="TECH_WRITING")],
        available_civics=[SimpleNamespace(civic_type="CIVIC_CRAFTSMANSHIP")],
    )

    class Adapter:
        calls = 0

        async def read_tech_civics(self, *, observed_turn):
            self.calls += 1
            value = (before, after_research, after_research, after_civic)[self.calls - 1]
            return SimpleNamespace(value=value, observed_turn=observed_turn)

    adapter = Adapter()
    factory = CivMutationFactory(adapter)
    research = factory.set_research(
        operation_id=OperationId("tech-1"), tech_name="TECH_WRITING", observed_turn=12
    )
    civic = factory.set_civic(
        operation_id=OperationId("civic-1"), civic_name="CIVIC_CRAFTSMANSHIP", observed_turn=12
    )
    asyncio.run(research.precheck())
    assert asyncio.run(research.verify()).source == "read_tech_civics"
    asyncio.run(civic.precheck())
    assert asyncio.run(civic.verify()).source == "read_tech_civics"
    assert research.intent.tool == "set_research"
    assert civic.intent.tool == "set_civic"


def test_research_baseline_rejects_an_already_selected_or_illegal_target() -> None:
    status = SimpleNamespace(
        current_research_type="TECH_WRITING",
        current_civic_type="CIVIC_CODE_OF_LAWS",
        available_techs=[],
        available_civics=[],
    )

    class Adapter:
        async def read_tech_civics(self, *, observed_turn):
            return SimpleNamespace(value=status, observed_turn=observed_turn)

    selected = CivMutationFactory(Adapter()).set_research(
        operation_id=OperationId("same-tech"), tech_name="TECH_WRITING", observed_turn=12
    )
    illegal = CivMutationFactory(Adapter()).set_civic(
        operation_id=OperationId("illegal-civic"), civic_name="CIVIC_CRAFTSMANSHIP", observed_turn=12
    )
    with pytest.raises(MutationPreconditionError, match="当前已在研究"):
        asyncio.run(selected.precheck())
    with pytest.raises(MutationPreconditionError, match="不是当前可推进候选"):
        asyncio.run(illegal.precheck())


def test_end_turn_is_a_single_hash_bound_mutation() -> None:
    class Adapter:
        pass

    async def turn_advanced():
        return Evidence("read_overview", 13, "turn 12 -> 13")

    execution = CivMutationFactory(Adapter()).end_turn(
        operation_id=OperationId("end-turn-12"), readback=turn_advanced
    )
    assert execution.intent.tool == execution.request.tool == "end_turn"
    assert "ACTION_ENDTURN" in execution.request.lua_code
    assert asyncio.run(execution.verify()).observed_turn == 13


def test_governance_mutations_are_hash_bound_and_need_readback() -> None:
    class Adapter:
        pass

    async def evidence():
        return Evidence("read_governors", 13, "governance state observed")

    factory = CivMutationFactory(Adapter())
    governor = factory.assign_governor(operation_id=OperationId("gov-1"), governor_type="GOVERNOR_MAGNUS", city_id=4, readback=evidence)
    promotion = factory.promote_unit(operation_id=OperationId("unit-promo-1"), unit_index=2, promotion_type="PROMOTION_BATTLECRY", readback=evidence)
    assert governor.intent.tool == "assign_governor"
    assert promotion.request.context == "gamecore"
    assert asyncio.run(governor.verify()).source == "read_governors"


def test_unit_upgrade_is_hash_bound_and_requires_domain_readback() -> None:
    class Adapter:
        pass

    async def readback() -> Evidence:
        return Evidence("read_units", 13, "unit type changed from UNIT_SLINGER")

    execution = CivMutationFactory(Adapter()).upgrade_unit(
        operation_id=OperationId("upgrade-1"), unit_index=2, readback=readback
    )
    assert execution.intent.tool == execution.request.tool == "upgrade_unit"
    assert "UnitCommandTypes.UPGRADE" in execution.request.lua_code
    assert asyncio.run(execution.verify()).source == "read_units"


def test_remaining_domain_mutations_use_explicit_readback_contracts() -> None:
    class Adapter:
        pass

    async def evidence():
        return Evidence("domain_read", 13, "verified")

    factory = CivMutationFactory(Adapter())
    pantheon = factory.choose_pantheon(operation_id=OperationId("pantheon-1"), belief_type="BELIEF_DIVINE_SPARK", readback=evidence)
    person = factory.recruit_great_person(operation_id=OperationId("gp-1"), individual_id=5, readback=evidence)
    spy = factory.spy_travel(operation_id=OperationId("spy-1"), unit_index=2, target_x=4, target_y=5, readback=evidence)
    vote = factory.congress_vote(operation_id=OperationId("vote-1"), resolution_hash=1, option=0, target_index=2, num_votes=3, readback=evidence)
    assert [pantheon.intent.tool, person.intent.tool, spy.intent.tool, vote.intent.tool] == ["choose_pantheon", "recruit_great_person", "spy_travel", "congress_vote"]
    assert asyncio.run(vote.verify()).source == "domain_read"

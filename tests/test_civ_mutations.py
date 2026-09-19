"""G2: action construction never promotes estimates or missing reads to success."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from civ_mcp.civ.mutations import CivMutationFactory
from civ_mcp.runtime.contracts import Evidence, OperationId


def test_move_uses_unit_position_readback_as_confirmation() -> None:
    class Adapter:
        async def read_units(self, *, observed_turn):
            unit = SimpleNamespace(unit_index=3, x=5, y=7)
            return SimpleNamespace(value=[unit], observed_turn=observed_turn)

    execution = CivMutationFactory(Adapter()).move_unit(
        operation_id=OperationId("move-3"), unit_index=3, target_x=5, target_y=7, observed_turn=12
    )
    evidence = asyncio.run(execution.verify())
    assert evidence.source == "read_units"
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


def test_production_requires_city_queue_readback() -> None:
    class Adapter:
        async def read_cities(self, *, observed_turn):
            city = SimpleNamespace(city_id=4, currently_building="UNIT_ARCHER")
            return SimpleNamespace(value=[city], observed_turn=observed_turn)

    execution = CivMutationFactory(Adapter()).set_production(
        operation_id=OperationId("production-4"), city_id=4, item_type="UNIT", item_name="UNIT_ARCHER", observed_turn=12
    )
    assert asyncio.run(execution.verify()).source == "read_cities"


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


def test_research_and_civic_require_fresh_domain_evidence() -> None:
    class Adapter:
        pass

    async def evidence():
        return Evidence("read_tech_civics", 12, "research/civic selection observed")

    factory = CivMutationFactory(Adapter())
    research = factory.set_research(operation_id=OperationId("tech-1"), tech_name="TECH_WRITING", readback=evidence)
    civic = factory.set_civic(operation_id=OperationId("civic-1"), civic_name="CIVIC_CODE_OF_LAWS", readback=evidence)
    assert research.intent.tool == "set_research"
    assert civic.intent.tool == "set_civic"
    assert asyncio.run(research.verify()).source == "read_tech_civics"


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

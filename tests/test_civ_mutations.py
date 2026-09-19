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

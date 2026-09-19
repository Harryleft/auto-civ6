"""H2: strategic handoff uses the bound session without touching operations."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from civ_mcp.runtime.mcp_surface import RuntimeMcpSurface


def test_surface_saves_only_the_four_handoff_fields_through_session() -> None:
    expected = SimpleNamespace(branch_id="game-a:save-1", strategic_focus="secure frontier")

    class Session:
        saved: dict[str, str] | None = None

        def save_handoff(self, **kwargs: str):
            self.saved = kwargs
            return expected

    session = Session()
    surface = RuntimeMcpSurface(
        context=SimpleNamespace(), session=session, turn_loop=SimpleNamespace()
    )

    note = surface.save_handoff(
        strategic_focus="secure frontier",
        existing_arrangements="warrior at the pass",
        rationale="barbarian camp nearby",
        change_conditions="camp removed",
    )

    assert note is expected
    assert session.saved == {
        "strategic_focus": "secure frontier",
        "existing_arrangements": "warrior at the pass",
        "rationale": "barbarian camp nearby",
        "change_conditions": "camp removed",
    }


def test_surface_reads_city_states_only_through_context() -> None:
    expected = SimpleNamespace(tokens_available=2)

    class Context:
        async def read_city_states(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_city_states()) is expected


def test_surface_reads_trade_negotiation_only_through_context() -> None:
    expected = SimpleNamespace(state="PROPOSED")

    class Context:
        async def read_trade_negotiation(self, other_player_id: int):
            assert other_player_id == 2
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_trade_negotiation(2)) is expected


def test_surface_reads_attack_target_only_through_context() -> None:
    expected = SimpleNamespace(value="RANGE")

    class Context:
        async def read_attack_target(self, unit_index: int, target_x: int, target_y: int):
            assert (unit_index, target_x, target_y) == (4, 5, 7)
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )
    assert asyncio.run(surface.get_unit_attack_target(4, 5, 7)) is expected


def test_surface_reads_city_attack_target_only_through_context() -> None:
    expected = SimpleNamespace(value="CITY_RANGE")

    class Context:
        async def read_city_attack_target(self, city_id: int, target_x: int, target_y: int):
            assert (city_id, target_x, target_y) == (4, 5, 7)
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )
    assert asyncio.run(surface.get_city_attack_target(4, 5, 7)) is expected


def test_surface_reads_district_placements_only_through_context() -> None:
    expected = [SimpleNamespace(district_type="DISTRICT_HARBOR", x=5, y=7)]

    class Context:
        async def read_district_placements(self, city_id: int, district_type: str):
            assert (city_id, district_type) == (4, "DISTRICT_HARBOR")
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )
    assert asyncio.run(surface.get_district_placements(4, "DISTRICT_HARBOR")) is expected


def test_surface_reads_builder_improvements_only_through_context() -> None:
    expected = [SimpleNamespace(improvement_type="IMPROVEMENT_FARM")]

    class Context:
        async def read_builder_improvement_candidates(self, unit_index: int):
            assert unit_index == 4
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )
    assert asyncio.run(surface.get_builder_improvement_candidates(4)) is expected


def test_surface_reads_pending_deals_only_through_context() -> None:
    expected = [SimpleNamespace(other_player_id=2)]

    class Context:
        async def read_pending_deals(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )
    assert asyncio.run(surface.get_pending_deals()) is expected


def test_surface_reads_governors_only_through_context() -> None:
    expected = SimpleNamespace(points_available=1)

    class Context:
        async def read_governors(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_governors()) is expected


def test_surface_reads_governments_only_through_context() -> None:
    expected = [SimpleNamespace(government_type="GOVERNMENT_CHIEFDOM")]

    class Context:
        async def read_governments(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_governments()) is expected


def test_surface_reads_policies_only_through_context() -> None:
    expected = SimpleNamespace(slots=[])

    class Context:
        async def read_policies(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_policies()) is expected


def test_surface_reads_city_purchases_only_through_context() -> None:
    expected = [SimpleNamespace(item_name="UNIT_ARCHER", cost=60)]

    class Context:
        async def read_city_purchases(self, city_id: int, yield_type: str):
            assert (city_id, yield_type) == (4, "YIELD_GOLD")
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_city_purchases(4, "YIELD_GOLD")) is expected


def test_surface_reads_city_production_only_through_context() -> None:
    expected = [SimpleNamespace(item_name="UNIT_ARCHER")]

    class Context:
        async def read_city_production(self, city_id: int):
            assert city_id == 4
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_city_production(4)) is expected


def test_surface_reads_trade_facts_only_through_context() -> None:
    destinations = [SimpleNamespace(city_name="Berlin")]
    routes = SimpleNamespace(active_count=1)

    class Context:
        async def read_trade_destinations(self, unit_index: int):
            assert unit_index == 7
            return destinations

        async def read_trade_routes(self):
            return routes

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_trade_destinations(7)) is destinations
    assert asyncio.run(surface.get_trade_routes()) is routes


def test_surface_reads_world_congress_only_through_context() -> None:
    expected = SimpleNamespace(is_in_session=True, favor=25)

    class Context:
        async def read_world_congress(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_world_congress()) is expected


def test_surface_reads_climate_only_through_context() -> None:
    expected = SimpleNamespace(phase=4, co2_total=410.0)

    class Context:
        async def read_climate_overview(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_climate_overview()) is expected


def test_surface_reads_spies_only_through_context() -> None:
    expected = [SimpleNamespace(unit_index=7, available_ops=["TRAVEL"])]

    class Context:
        async def read_spies(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_spies()) is expected


def test_surface_reads_religion_overview_only_through_context() -> None:
    expected = SimpleNamespace(religions_founded=2, faith_balance=123.0)

    class Context:
        async def read_religion_overview(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_religion_overview()) is expected


def test_surface_reads_barbarian_overview_only_through_context() -> None:
    expected = SimpleNamespace(camps=[], units=[])

    class Context:
        async def read_barbarian_overview(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_barbarian_overview()) is expected

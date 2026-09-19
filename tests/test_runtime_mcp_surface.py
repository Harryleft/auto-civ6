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


def test_surface_reads_governors_only_through_context() -> None:
    expected = SimpleNamespace(points_available=1)

    class Context:
        async def read_governors(self):
            return expected

    surface = RuntimeMcpSurface(
        context=Context(), session=SimpleNamespace(), turn_loop=SimpleNamespace()
    )

    assert asyncio.run(surface.get_governors()) is expected

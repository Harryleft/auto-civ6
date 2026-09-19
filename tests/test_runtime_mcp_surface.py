"""H2: strategic handoff uses the bound session without touching operations."""

from __future__ import annotations

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

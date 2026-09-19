"""H1: ContextBuilder is a fresh, read-only composition boundary."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp.runtime.context import ContextBuilder


def test_context_keeps_failed_domain_reads_explicitly_unknown() -> None:
    class Adapter:
        async def read_overview(self):
            return SimpleNamespace(observed_turn=42)

        async def read_cities(self, **_kwargs):
            return SimpleNamespace(value=[])

        async def read_pending_city_capture(self, **_kwargs):
            return SimpleNamespace(value=None)

        async def read_units(self, **_kwargs):
            raise TimeoutError("no response")

        async def read_diplomacy(self, **_kwargs):
            return SimpleNamespace(value=[])

        async def read_diplomacy_sessions(self, **_kwargs):
            return SimpleNamespace(value=[])

        async def read_tech_civics(self, **_kwargs):
            return SimpleNamespace(value=[])

        async def read_pantheon_status(self, **_kwargs):
            return SimpleNamespace(value=[])

        async def read_dedications(self, **_kwargs):
            return SimpleNamespace(value=[])

        async def read_great_people(self, **_kwargs):
            return SimpleNamespace(value=[])

        async def read_victory_progress(self, **_kwargs):
            return SimpleNamespace(value=[])

    class Session:
        async def current_binding(self):
            return "game-a:save-1"

        async def assert_binding_current(self, binding):
            assert binding == "game-a:save-1"

        def current_operations(self):
            return []

        def handoff_note(self):
            return None

    context = asyncio.run(ContextBuilder(Adapter(), Session()).build())
    assert "overview" in context.facts
    assert context.unknown == ("units: TimeoutError",)
    assert context.pending_decisions == ()
    assert context.unfinished_intents == ()
    assert context.further_queries == (
        "get_unit_promotions",
        "get_unit_attack_target",
        "get_city_attack_target",
        "get_district_placements",
        "get_builder_improvement_candidates",
        "get_pending_deals",
        "get_trade_negotiation",
        "get_city_states",
        "get_governors",
        "get_governments",
        "get_policies",
        "get_city_purchases",
        "get_city_production",
        "get_trade_destinations",
        "get_trade_routes",
        "get_world_congress",
    )


def test_context_exposes_only_a_read_only_pending_decision_snapshot() -> None:
    class Adapter:
        async def read_overview(self):
            return SimpleNamespace(observed_turn=42)

        async def read_cities(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_pending_city_capture(self, **_kwargs): return SimpleNamespace(value=None)
        async def read_units(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_diplomacy(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_diplomacy_sessions(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_tech_civics(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_pantheon_status(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_dedications(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_great_people(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_victory_progress(self, **_kwargs): return SimpleNamespace(value=[])

    class Session:
        async def current_binding(self):
            return "game-a:save-1"

        async def assert_binding_current(self, binding):
            assert binding == "game-a:save-1"

        def current_operations(self): return []
        def handoff_note(self): return None

    decision = SimpleNamespace(decision_type="DIPLOMACY", allowed_choices=("EXIT",))
    result = asyncio.run(
        ContextBuilder(Adapter(), Session(), pending_decisions=lambda: (decision,)).build()
    )

    assert result.pending_decisions == (decision,)


def test_context_rejects_facts_if_the_session_binding_changed_during_reads() -> None:
    class Adapter:
        async def read_overview(self):
            return SimpleNamespace(observed_turn=42)

        async def read_cities(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_pending_city_capture(self, **_kwargs): return SimpleNamespace(value=None)
        async def read_units(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_diplomacy(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_diplomacy_sessions(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_tech_civics(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_pantheon_status(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_dedications(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_great_people(self, **_kwargs): return SimpleNamespace(value=[])
        async def read_victory_progress(self, **_kwargs): return SimpleNamespace(value=[])

    class Session:
        async def current_binding(self):
            return "game-a:save-1"

        async def assert_binding_current(self, _binding):
            raise RuntimeError("game/branch changed")

        def current_operations(self): return []
        def handoff_note(self): return None

    with pytest.raises(RuntimeError, match="game/branch changed"):
        asyncio.run(ContextBuilder(Adapter(), Session()).build())

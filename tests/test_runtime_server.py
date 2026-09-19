"""Registration and configuration boundaries for the experimental Runtime MCP."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest

from civ_mcp.runtime.contracts import BranchIdentity, Evidence, GameIdentity, OperationId, OperationIntent, OperationRecord
from civ_mcp.runtime.server import (
    RUNTIME_BRANCH_ENV,
    RuntimeServerConfigurationError,
    _json_value,
    lifespan,
    mcp,
)


def test_experimental_runtime_surface_exposes_only_new_core_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}

    assert names == {
        "get_runtime_context",
        "get_unit_promotions",
        "get_unit_attack_target",
        "get_city_attack_target",
        "get_city_states",
        "get_governors",
        "get_governments",
        "get_policies",
        "get_city_purchases",
        "get_city_production",
        "get_trade_destinations",
        "get_trade_routes",
        "save_handoff",
        "move_unit",
        "attack_unit",
        "attack_city",
        "upgrade_unit",
        "promote_unit",
        "send_envoy",
        "appoint_governor",
        "assign_governor",
        "promote_governor",
        "change_government",
        "set_policies",
        "set_city_production",
        "purchase_item",
        "make_trade_route",
        "set_research",
        "set_civic",
        "choose_pantheon",
        "choose_dedication",
        "recruit_great_person",
        "found_city",
        "end_turn",
        "resume_turn_decision",
    }


def test_runtime_server_requires_explicit_branch_before_opening_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUNTIME_BRANCH_ENV, raising=False)

    async def run() -> None:
        with pytest.raises(RuntimeServerConfigurationError, match=RUNTIME_BRANCH_ENV):
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(lifespan(mcp))

    asyncio.run(run())


def test_runtime_payload_serializes_operation_facts_without_reclassifying_them() -> None:
    game = GameIdentity("game-a")
    operation = OperationRecord.create(
        game_id=game,
        branch_id=BranchIdentity(game, "main"),
        decision_turn=10,
        operation_id=OperationId("move-1"),
        intent=OperationIntent.create("move_unit", {"unit": 1}),
    ).prechecked().sending().maybe_sent().confirmed(
        Evidence("read_units", 10, "unit moved")
    )

    payload = _json_value(operation)

    assert payload["outcome_state"] == "CONFIRMED"
    assert payload["evidence"][0]["detail"] == "unit moved"

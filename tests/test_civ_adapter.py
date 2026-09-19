"""Task D1: the Civ adapter has no strategy, recovery, or legacy dependency."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from civ_mcp.civ import adapter
from civ_mcp.runtime.contracts import SendState
from civ_mcp.runtime.transport import Frame, TransportReceipt


def test_adapter_does_not_depend_on_legacy_runtime_or_control_layers() -> None:
    source = Path(adapter.__file__).read_text()
    imports = [
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "civ_mcp.runtime.transport" in imports
    assert not {"civ6_belief_engine", "civ_mcp.game_state", "civ_mcp.game_launcher"} & set(imports)


def test_adapter_command_binds_lua_to_the_selected_discovered_state() -> None:
    assert adapter.CivAdapter._command(8, "return 1") == "CMD:8:return 1"


class _Transport:
    def __init__(self, receipt: TransportReceipt):
        self.receipt = receipt
        self.commands: list[str] = []

    async def execute_read(self, command: str, *, is_complete):
        self.commands.append(command)
        return self.receipt


def _complete(*lines: str) -> TransportReceipt:
    return TransportReceipt(
        send_state=SendState.MAYBE_SENT,
        complete=True,
        frames=tuple(
            Frame(tag=2, payload=f"O\x00GameCore: {line}")
            for line in (*lines, "---END---")
        ),
        connection_usable=True,
    )


def test_overview_is_a_typed_game_fact_with_an_observed_turn() -> None:
    transport = _Transport(
        _complete("42|0|France|Catherine|100|10|5|3|1|Mining|Code of Laws|1|2|100")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_overview())

    assert result.value.turn == 42
    assert result.observed_turn == 42
    assert result.coverage == "CURRENT_GAME:COMPLETE"
    assert transport.commands[0].startswith("CMD:8:")


def test_cities_read_is_bound_to_the_ingame_domain_context() -> None:
    transport = _Transport(_complete())
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_cities(observed_turn=42))

    assert result.value == []
    assert result.observed_turn == 42
    assert transport.commands[0].startswith("CMD:153:")


def test_adapter_can_resolve_lua_state_when_a_new_runtime_connection_refreshes_it() -> None:
    transport = _Transport(_complete())
    indexes = {"gamecore": 8, "ingame": 153}
    civ = adapter.CivAdapter(transport, state_resolver=indexes.__getitem__)

    asyncio.run(civ.read_cities(observed_turn=42))
    indexes["ingame"] = 154
    asyncio.run(civ.read_cities(observed_turn=43))

    assert len(transport.commands) == 2
    assert transport.commands[0].startswith("CMD:153:")
    assert transport.commands[1].startswith("CMD:154:")


def test_incomplete_query_is_not_converted_to_an_empty_domain_result() -> None:
    transport = _Transport(
        TransportReceipt(
            send_state=SendState.MAYBE_SENT,
            complete=False,
            frames=(),
            connection_usable=False,
            error=TimeoutError("receipt missing"),
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    with pytest.raises(adapter.CivReadError):
        asyncio.run(civ.read_cities(observed_turn=42))

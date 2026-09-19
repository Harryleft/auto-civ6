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


def test_game_identity_is_a_typed_runtime_identity_not_a_legacy_game_state_value() -> None:
    transport = _Transport(_complete("GAMESEED|CIVILIZATION_FRANCE|425675776"))
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_game_identity())

    assert result.value.value == "civilization_france_425675776"
    assert result.observed_turn is None
    assert transport.commands[0].startswith("CMD:8:")


def test_cities_read_is_bound_to_the_ingame_domain_context() -> None:
    transport = _Transport(_complete())
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_cities(observed_turn=42))

    assert result.value == []
    assert result.observed_turn == 42
    assert transport.commands[0].startswith("CMD:153:")


def test_diplomacy_sessions_are_typed_read_only_facts() -> None:
    transport = _Transport(
        _complete("SESSION|9|2|Germany|Frederick|Greetings|First meeting|Accept;Reject|0")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_diplomacy_sessions(observed_turn=42))

    assert result.value[0].session_id == 9
    assert result.value[0].other_player_id == 2
    assert result.value[0].dialogue_text == "Greetings"
    assert result.coverage == "OPEN_DIPLOMACY_SESSIONS:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_pending_city_capture_and_its_coordinate_state_are_typed_facts() -> None:
    pending_transport = _Transport(
        _complete("PENDING_CITY_CAPTURE|captured|9|Berlin|4|5|7|0|2|3|KEEP;RAZE")
    )
    civ = adapter.CivAdapter(pending_transport, gamecore_state=8, ingame_state=153)

    pending = asyncio.run(civ.read_pending_city_capture(observed_turn=42))

    assert pending.value is not None
    assert pending.value.city_id == 9
    assert pending.value.allowed_choices == ("KEEP", "RAZE")
    assert pending.coverage == "PENDING_CITY_CAPTURE:COMPLETE"
    assert pending_transport.commands[0].startswith("CMD:153:")

    state_transport = _Transport(_complete("CITY_CAPTURE_STATE|9|0"))
    state_civ = adapter.CivAdapter(state_transport, gamecore_state=8, ingame_state=153)

    state = asyncio.run(
        state_civ.read_city_capture_state(x=4, y=5, observed_turn=42)
    )

    assert state.value.city_id == 9
    assert state.value.owner_id == 0
    assert state.coverage == "CITY_CAPTURE_COORDINATE:COMPLETE"
    assert state_transport.commands[0].startswith("CMD:153:")


def test_tech_civics_read_exposes_stable_current_selection_ids() -> None:
    transport = _Transport(
        _complete("CURRENT|Writing|3|Code of Laws|2|TECH_WRITING|CIVIC_CODE_OF_LAWS")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_tech_civics(observed_turn=42))

    assert result.value.current_research_type == "TECH_WRITING"
    assert result.value.current_civic_type == "CIVIC_CODE_OF_LAWS"
    assert result.coverage == "RESEARCH_AND_CIVICS:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_pantheon_read_exposes_current_and_legal_stable_belief_ids() -> None:
    transport = _Transport(
        _complete(
            "STATUS|0|None|None|30.0|25",
            "BELIEF|BELIEF_DIVINE_SPARK|Divine Spark|Great people points",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_pantheon_status(observed_turn=42))

    assert result.value.has_pantheon is False
    assert result.value.pantheon_cost == 25
    assert result.value.available_beliefs[0].belief_type == "BELIEF_DIVINE_SPARK"
    assert result.coverage == "PANTHEON:COMPLETE"
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


def test_adapter_state_resolver_also_applies_to_direct_identity_reads() -> None:
    transport = _Transport(_complete("GAMESEED|CIVILIZATION_FRANCE|42"))
    indexes = {"gamecore": 8, "ingame": 153}
    civ = adapter.CivAdapter(transport, state_resolver=indexes.__getitem__)

    asyncio.run(civ.read_game_identity())
    indexes["gamecore"] = 9
    asyncio.run(civ.read_game_identity())

    assert transport.commands[0].startswith("CMD:8:")
    assert transport.commands[1].startswith("CMD:9:")


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

"""The Runtime bootstrap binds an explicit branch without legacy services."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp.runtime.bootstrap import assemble_runtime
from civ_mcp.runtime.contracts import GameIdentity, OperationId, OutcomeState, SendState
from civ_mcp.runtime.session import SessionIdentityMismatchError
from civ_mcp.runtime.store import OperationStore
from civ_mcp.runtime.transport import TransportReceipt
from civ_mcp.runtime.turn import TurnOutcome


class _Adapter:
    def __init__(self, identities: list[GameIdentity]) -> None:
        self._identities = iter(identities)

    async def read_game_identity(self):
        return SimpleNamespace(value=next(self._identities))

    async def read_overview(self):
        return SimpleNamespace(value=SimpleNamespace(turn=42))


def test_bootstrap_binds_adapter_to_an_explicit_game_scoped_branch(tmp_path) -> None:
    async def run() -> None:
        game = GameIdentity("civilization_france_42")
        assembly = await assemble_runtime(
            _Adapter([game, game]),
            OperationStore(tmp_path / "operations.sqlite3"),
            branch_token="save-0001",
        )

        assert assembly.binding.game_id == game
        assert assembly.binding.branch_id.value == "civilization_france_42:save-0001"
        assert assembly.session.binding == assembly.binding
        assert assembly.mutations._adapter is assembly.adapter

    asyncio.run(run())


def test_bootstrap_refuses_identity_change_between_discovery_and_bind(tmp_path) -> None:
    async def run() -> None:
        with pytest.raises(SessionIdentityMismatchError):
            await assemble_runtime(
                _Adapter([GameIdentity("game-a"), GameIdentity("game-b")]),
                OperationStore(tmp_path / "operations.sqlite3"),
                branch_token="save-0001",
            )

    asyncio.run(run())


def test_bootstrap_requires_a_host_provided_branch_token(tmp_path) -> None:
    async def run() -> None:
        with pytest.raises(ValueError, match="branch_token"):
            await assemble_runtime(
                _Adapter([GameIdentity("game-a")]),
                OperationStore(tmp_path / "operations.sqlite3"),
                branch_token=" ",
            )

    asyncio.run(run())


def test_bootstrap_wires_end_turn_waiting_to_a_fresh_turn_observation(tmp_path) -> None:
    class Adapter:
        def __init__(self) -> None:
            self.game = GameIdentity("civilization_france_42")
            self.overview_calls = 0
            self.submit_calls = 0

        async def read_game_identity(self):
            return SimpleNamespace(value=self.game)

        async def read_overview(self):
            self.overview_calls += 1
            turn = 10 if self.overview_calls <= 2 else 11
            return SimpleNamespace(value=SimpleNamespace(turn=turn))

        async def submit(self, _request):
            self.submit_calls += 1
            return TransportReceipt(SendState.MAYBE_SENT, True, (), True)

    async def run() -> None:
        adapter = Adapter()
        assembly = await assemble_runtime(
            adapter,
            OperationStore(tmp_path / "operations.sqlite3"),
            branch_token="save-0001",
        )

        async def no_immediate_evidence():
            return None

        result = await assembly.surface.end_turn(
            assembly.mutations.end_turn(
                operation_id=OperationId("end-turn-10"),
                readback=no_immediate_evidence,
            ),
            decision_turn=10,
        )

        assert result.outcome is TurnOutcome.ADVANCED
        assert result.operation.outcome_state is OutcomeState.CONFIRMED
        assert adapter.submit_calls == 1

    asyncio.run(run())

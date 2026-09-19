"""The Runtime bootstrap binds an explicit branch without legacy services."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp.runtime.bootstrap import assemble_runtime
from civ_mcp.runtime.contracts import GameIdentity
from civ_mcp.runtime.session import SessionIdentityMismatchError
from civ_mcp.runtime.store import OperationStore


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

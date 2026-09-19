"""Read-only game/branch ownership for the Runtime Core."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from civ_mcp.civ.adapter import CivAdapter, CivReadRequest, CivReadResult
from civ_mcp.runtime.contracts import BranchIdentity, GameIdentity


T = TypeVar("T")
IdentityProbe = Callable[[], Awaitable[GameIdentity]]


class SessionIdentityMismatchError(RuntimeError):
    """The requested binding does not match the game observed from Civ6."""


class StaleSessionRequestError(RuntimeError):
    """A delayed read returned after its game/branch binding changed."""


@dataclass(frozen=True, slots=True)
class SessionBinding:
    game_id: GameIdentity
    branch_id: BranchIdentity
    generation: int


class SessionKernel:
    """Single in-process owner of the current game and branch, read-only for E1."""

    def __init__(self, adapter: CivAdapter, *, identity_probe: IdentityProbe) -> None:
        self._adapter = adapter
        self._identity_probe = identity_probe
        self._binding: SessionBinding | None = None
        self._lock = asyncio.Lock()

    @property
    def binding(self) -> SessionBinding | None:
        return self._binding

    async def bind(self, game_id: GameIdentity, branch_id: BranchIdentity) -> SessionBinding:
        if branch_id.game_id != game_id:
            raise SessionIdentityMismatchError("branch_id 不属于待绑定的 game_id。")
        observed = await self._identity_probe()
        if observed != game_id:
            raise SessionIdentityMismatchError("Civ6 当前对局与请求绑定的 game_id 不一致。")
        async with self._lock:
            generation = 1 if self._binding is None else self._binding.generation + 1
            self._binding = SessionBinding(game_id, branch_id, generation)
            return self._binding

    async def read(
        self, request: CivReadRequest[T], *, observed_turn: int
    ) -> CivReadResult[T]:
        binding = self._require_binding()
        result = await self._adapter.read(request, observed_turn=observed_turn)
        if self._binding != binding:
            raise StaleSessionRequestError("读取结果属于已切换的 game/branch，已拒绝使用。")
        return result

    def _require_binding(self) -> SessionBinding:
        if self._binding is None:
            raise SessionIdentityMismatchError("SessionKernel 尚未绑定当前 Civ6 对局。")
        return self._binding

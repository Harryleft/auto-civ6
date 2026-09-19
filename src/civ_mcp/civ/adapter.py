"""A narrow Civ6 adapter: typed reads and one-shot mutation submission only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from civ_mcp.runtime.transport import FireTunerTransport, Frame, TransportReceipt


SENTINEL = "---END---"
T = TypeVar("T")


class CivReadError(RuntimeError):
    """A Civ6 read failed or was incomplete; it is never an empty result."""


@dataclass(frozen=True, slots=True)
class CivReadRequest(Generic[T]):
    """A domain-owned Lua query and its typed decoder."""

    tool: str
    lua_code: str
    decode: Callable[[tuple[str, ...]], T]
    coverage: str


@dataclass(frozen=True, slots=True)
class CivReadResult(Generic[T]):
    """A typed game fact with its direct source and observation boundary."""

    value: T
    source: str
    observed_turn: int | None
    coverage: str


@dataclass(frozen=True, slots=True)
class CivMutationRequest:
    """A requested player action, without strategy or recovery behavior."""

    tool: str
    lua_code: str
    context: str = "ingame"


class CivAdapter:
    """Translate Civ6 Lua calls to typed reads and transport receipts.

    It owns neither operation state nor game-process recovery.  SessionKernel
    will own the former; RecoverySupervisor will own the latter.
    """

    def __init__(
        self, transport: FireTunerTransport, *, gamecore_state: int, ingame_state: int
    ) -> None:
        self._transport = transport
        self._gamecore_state = gamecore_state
        self._ingame_state = ingame_state

    async def read(
        self, request: CivReadRequest[T], *, observed_turn: int | None = None
    ) -> CivReadResult[T]:
        receipt = await self._transport.execute_read(
            self._command(self._gamecore_state, request.lua_code),
            is_complete=_is_sentinel,
        )
        if not receipt.complete:
            raise CivReadError(f"{request.tool} 未得到完整游戏响应：{receipt.error!r}")
        lines = tuple(
            value for frame in receipt.frames
            if (value := _output_value(frame)) is not None and value != SENTINEL
        )
        return CivReadResult(
            value=request.decode(lines),
            source="civ6:FireTuner",
            observed_turn=observed_turn,
            coverage=request.coverage,
        )

    async def submit(self, request: CivMutationRequest) -> TransportReceipt:
        state = self._ingame_state if request.context == "ingame" else self._gamecore_state
        return await self._transport.execute_mutation(
            self._command(state, request.lua_code), is_complete=_is_sentinel
        )

    @staticmethod
    def _command(state: int, lua_code: str) -> str:
        return f"CMD:{state}:{lua_code}"


def _is_sentinel(frame: Frame) -> bool:
    return _output_value(frame) == SENTINEL


def _output_value(frame: Frame) -> str | None:
    if not frame.payload.startswith("O"):
        return None
    separator = frame.payload.find(": ", 2)
    return frame.payload[separator + 2 :] if separator >= 0 else frame.payload.lstrip("O\x00").strip()

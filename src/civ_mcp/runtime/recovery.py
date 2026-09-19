"""Host-owned recovery boundary for the Runtime Core."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from civ_mcp.runtime.contracts import BranchIdentity, GameIdentity, OperationRecord


class RecoveryOutcome(StrEnum):
    RECOVERED = "RECOVERED"
    NEEDS_OPERATOR = "NEEDS_OPERATOR"
    FAILED_SAFE = "FAILED_SAFE"


@dataclass(frozen=True, slots=True)
class RecoveryInput:
    game_id: GameIdentity
    current_branch: BranchIdentity
    operation: OperationRecord | None
    process_running: bool
    connection_available: bool
    checkpoints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    outcome: RecoveryOutcome
    reason: str
    new_branch: BranchIdentity | None = None


RecoveryDriver = Callable[[RecoveryInput], Awaitable[str | None]]


class RecoverySupervisor:
    """Runs explicit host recovery; it has no SessionKernel or store authority."""

    def __init__(self, driver: RecoveryDriver) -> None:
        self._driver = driver

    async def recover(self, request: RecoveryInput) -> RecoveryResult:
        # This class has no store or SessionKernel dependency, so it cannot
        # reclassify UNKNOWN or retry a mutation during recovery.
        if not request.checkpoints:
            return RecoveryResult(RecoveryOutcome.NEEDS_OPERATOR, "没有可验证的 checkpoint。")
        try:
            branch_suffix = await self._driver(request)
        except Exception as exc:
            return RecoveryResult(RecoveryOutcome.FAILED_SAFE, f"恢复驱动失败：{type(exc).__name__}")
        if not branch_suffix:
            return RecoveryResult(RecoveryOutcome.NEEDS_OPERATOR, "恢复驱动未确认稳定 game identity。")
        return RecoveryResult(
            RecoveryOutcome.RECOVERED,
            "恢复完成；必须以新 branch 重新绑定 SessionKernel。",
            BranchIdentity(request.game_id, branch_suffix),
        )

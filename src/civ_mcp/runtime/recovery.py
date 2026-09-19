"""Host-owned recovery boundary for the Runtime Core."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from civ_mcp.runtime.contracts import BranchIdentity, ContractViolation, GameIdentity, OperationRecord


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


@dataclass(frozen=True, slots=True)
class RecoveredBinding:
    game_id: GameIdentity
    branch_token: str


RecoveryDriver = Callable[[RecoveryInput], Awaitable[RecoveredBinding | None]]


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
            recovered = await self._driver(request)
        except Exception as exc:
            return RecoveryResult(RecoveryOutcome.FAILED_SAFE, f"恢复驱动失败：{type(exc).__name__}")
        if recovered is None:
            return RecoveryResult(RecoveryOutcome.NEEDS_OPERATOR, "恢复驱动未确认稳定 game identity。")
        if recovered.game_id != request.game_id:
            return RecoveryResult(RecoveryOutcome.FAILED_SAFE, "恢复后的 game identity 与请求不一致。")
        try:
            new_branch = BranchIdentity.from_token(recovered.game_id, recovered.branch_token)
        except ContractViolation as exc:
            return RecoveryResult(RecoveryOutcome.FAILED_SAFE, f"恢复分支 token 非法：{exc}")
        if new_branch == request.current_branch:
            return RecoveryResult(
                RecoveryOutcome.FAILED_SAFE,
                "恢复不得复用原 branch token；必须创建新的时间线。",
            )
        return RecoveryResult(
            RecoveryOutcome.RECOVERED,
            "恢复完成；必须以新 branch 重新绑定 SessionKernel。",
            new_branch,
        )

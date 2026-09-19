"""Host-owned recovery boundary for the Runtime Core."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from civ_mcp.civ.adapter import CivAdapter
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
    checkpoints: tuple[RecoveryCheckpoint, ...]

    def __post_init__(self) -> None:
        if self.current_branch.game_id != self.game_id:
            raise ContractViolation("recovery current_branch 必须属于 game_id。")
        if self.operation is not None and (
            self.operation.game_id != self.game_id
            or self.operation.branch_id != self.current_branch
        ):
            raise ContractViolation(
                "recovery operation 必须属于请求中的 game_id 和 current_branch。"
            )
        if any(checkpoint.game_id != self.game_id for checkpoint in self.checkpoints):
            raise ContractViolation("recovery checkpoint 必须属于请求中的 game_id。")
        checkpoint_ids = tuple(checkpoint.checkpoint_id for checkpoint in self.checkpoints)
        if len(set(checkpoint_ids)) != len(checkpoint_ids):
            raise ContractViolation("recovery checkpoint inventory 不能有重复 ID。")


@dataclass(frozen=True, slots=True)
class RecoveryCheckpoint:
    """One host-discovered checkpoint with its expected game observation."""

    checkpoint_id: str
    game_id: GameIdentity
    expected_turn: int

    def __post_init__(self) -> None:
        if not self.checkpoint_id.strip():
            raise ContractViolation("recovery checkpoint_id 不得为空。")
        if self.expected_turn < 0:
            raise ContractViolation("recovery checkpoint expected_turn 不得为负数。")


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    outcome: RecoveryOutcome
    reason: str
    new_branch: BranchIdentity | None = None
    checkpoint: RecoveryCheckpoint | None = None


@dataclass(frozen=True, slots=True)
class RecoveredBinding:
    """Host evidence for one completed load/reconnect attempt.

    The host reports the exact checkpoint it used, rather than merely claiming
    that some checkpoint existed when recovery started.  The Runtime verifies
    it against the inventory captured in ``RecoveryInput`` before allowing a
    new branch to be bound.
    """

    game_id: GameIdentity
    checkpoint_id: str
    observed_turn: int
    branch_token: str


RecoveryDriver = Callable[[RecoveryInput], Awaitable[RecoveredBinding | None]]


class VerifiedRecoveryDriver:
    """Read-only post-recovery probe for a checkpoint the host has loaded.

    The host owns process control and save loading.  This driver only confirms
    that the newly connected game remains on one identity and turn across two
    reads.  A transition during either probe is deliberately inconclusive.
    """

    def __init__(
        self,
        adapter: CivAdapter,
        *,
        checkpoint_id: str,
        branch_token: str,
    ) -> None:
        self._adapter = adapter
        self._checkpoint_id = checkpoint_id
        self._branch_token = branch_token

    async def __call__(self, _request: RecoveryInput) -> RecoveredBinding | None:
        first_identity = (await self._adapter.read_game_identity()).value
        first_overview = (await self._adapter.read_overview()).value
        second_identity = (await self._adapter.read_game_identity()).value
        second_overview = (await self._adapter.read_overview()).value
        if first_identity != second_identity or first_overview.turn != second_overview.turn:
            return None
        return RecoveredBinding(
            game_id=first_identity,
            checkpoint_id=self._checkpoint_id,
            observed_turn=first_overview.turn,
            branch_token=self._branch_token,
        )


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
        checkpoint = next(
            (
                candidate
                for candidate in request.checkpoints
                if candidate.checkpoint_id == recovered.checkpoint_id
            ),
            None,
        )
        if checkpoint is None:
            return RecoveryResult(
                RecoveryOutcome.FAILED_SAFE,
                "恢复驱动确认的 checkpoint 不在请求的 checkpoint inventory 中。",
            )
        if recovered.observed_turn != checkpoint.expected_turn:
            return RecoveryResult(
                RecoveryOutcome.FAILED_SAFE,
                "恢复后的 turn 与 checkpoint 的预期 turn 不一致。",
            )
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
            outcome=RecoveryOutcome.RECOVERED,
            reason="恢复完成；必须以新 branch 重新绑定 SessionKernel。",
            new_branch=new_branch,
            checkpoint=checkpoint,
        )

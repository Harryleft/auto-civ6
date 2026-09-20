"""把 DeepSeek 选定的行动提交给 Runtime（M12）。

不变量（方案 §M12：``duplicate mutation = 0`` / ``false confirmed = 0``）：

1. **恰好发送一次**。每个候选行动拥有一个在此进程内不再改变的 ``operation_id``；
   同一个行动重复提交时复用同一个 id，而不是生成新的——换 id 重发正是"重复 mutation"
   的定义。
2. **UNKNOWN 不是成功**。``UNKNOWN`` / ``OBSERVING`` 一律记为未确认，保留
   ``operation_id`` 供核对，绝不重发，也不写入 CONFIRMED。
3. **``end_turn`` 是唯一推进回合的动作**。它经 ``TurnLoop`` 返回
   ``ADVANCED`` / ``NEEDS_DECISION`` / ``RECOVERY_REQUIRED``；后两者必须交给模型
   决策，不能自动恢复或补发 end-turn。
4. **阻断即停**。遇到需要决策的阻塞时，本轮不再提交其他动作，把
   ``decision_type``、允许的选项与 ``continuation_operation_id`` 记录下来。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from civ_agent.mcp_client import RuntimeClient
from civ_agent.state import CandidateAction, ExecutionResult, ExecutionStatus

#: 推进回合的动作名；只有它经 TurnLoop。
END_TURN_TOOL = "end_turn"

#: 续跑被阻塞回合的动作名。
RESUME_DECISION_TOOL = "resume_turn_decision"

#: Runtime 的 mutation 工具清单（与 ``civ_mcp/runtime/server.py`` 一致）。
#: ``end_turn`` / ``resume_turn_decision`` 走路径不同，但同属可提交动作。
MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "save_handoff",
        "move_unit",
        "attack_unit",
        "attack_city",
        "build_improvement",
        "propose_trade",
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
        END_TURN_TOOL,
        RESUME_DECISION_TOOL,
    }
)

#: Runtime 的 outcome_state → 本层执行状态。
#: ``OBSERVING`` 表示已发送但尚未回读确认——它与 ``UNKNOWN`` 不同（后者是结果不明），
#: 但两者都**不是** CONFIRMED。
_OUTCOME_TO_STATUS: dict[str, ExecutionStatus] = {
    "CONFIRMED": ExecutionStatus.CONFIRMED,
    "OBSERVING": ExecutionStatus.OBSERVING,
    "UNKNOWN": ExecutionStatus.UNKNOWN,
    "NOT_STARTED": ExecutionStatus.UNKNOWN,
    "REJECTED": ExecutionStatus.UNKNOWN,
    "STALE_INTENT": ExecutionStatus.UNKNOWN,
}


class MutationError(RuntimeError):
    """提交 mutation 失败（含非法动作、工具报错）；消息说明动作与原因。"""


@dataclass(frozen=True, slots=True)
class PendingDecision:
    """一个需要模型选择才能继续的 Runtime 阻塞。"""

    decision_type: str
    allowed_choices: tuple[str, ...]
    continuation_operation_id: str
    facts: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_type": self.decision_type,
            "allowed_choices": list(self.allowed_choices),
            "continuation_operation_id": self.continuation_operation_id,
            "facts": dict(self.facts),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class MutationOutcome:
    """一次 action 提交的结果，同时覆盖普通 mutation 与 end_turn。"""

    execution: ExecutionResult
    pending_decision: PendingDecision | None = None
    turn_advanced: bool = False


def generate_operation_id() -> str:
    """不透明的 operation id；由 Runtime 语义要求唯一。"""

    from uuid import uuid4

    return str(uuid4())


def _extract_operation_id(payload: Any) -> str | None:
    """从 ``OperationRecord`` / ``TurnResult`` 的 JSON 里取出 operation_id。"""

    if not isinstance(payload, Mapping):
        return None
    operation = payload.get("operation")
    holder = operation if isinstance(operation, Mapping) else payload
    identifier = holder.get("operation_id")
    if isinstance(identifier, Mapping):
        value = identifier.get("value")
        return str(value) if value else None
    if isinstance(identifier, str) and identifier:
        return identifier
    return None


def _extract_outcome_state(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    operation = payload.get("operation")
    holder = operation if isinstance(operation, Mapping) else payload
    state = holder.get("outcome_state")
    if isinstance(state, str):
        return state
    if isinstance(state, Mapping):
        value = state.get("value")
        return str(value) if value else None
    return None


def _extract_evidence(payload: Any) -> str:
    """把 Evidence 压成一行可读文本；没有证据就说明没有。"""

    if not isinstance(payload, Mapping):
        return ""
    operation = payload.get("operation")
    holder = operation if isinstance(operation, Mapping) else payload
    evidence = holder.get("evidence")
    if not isinstance(evidence, Sequence) or isinstance(evidence, str | bytes):
        return ""
    parts: list[str] = []
    for item in evidence:
        if isinstance(item, Mapping):
            summary = item.get("summary") or item.get("kind") or item.get("source")
            if summary:
                parts.append(str(summary))
    return "; ".join(parts)


def _extract_turn_outcome(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    outcome = payload.get("outcome")
    if isinstance(outcome, str):
        return outcome
    if isinstance(outcome, Mapping):
        value = outcome.get("value")
        return str(value) if value else None
    return None


def _extract_pending_decision(payload: Any) -> PendingDecision | None:
    """只在 ``NEEDS_DECISION`` 时才构造；其余情况返回 ``None``。"""

    if not isinstance(payload, Mapping):
        return None
    decision = payload.get("decision")
    if not isinstance(decision, Mapping):
        return None
    choices = decision.get("allowed_choices")
    allowed = tuple(str(choice) for choice in choices) if isinstance(choices, Sequence) else ()
    continuation = decision.get("continuation_operation_id")
    if isinstance(continuation, Mapping):
        continuation = continuation.get("value")
    facts = decision.get("facts")
    return PendingDecision(
        decision_type=str(decision.get("decision_type") or "UNKNOWN"),
        allowed_choices=allowed,
        continuation_operation_id=str(continuation or ""),
        facts=dict(facts) if isinstance(facts, Mapping) else {},
        reason=str(payload.get("reason") or ""),
    )


def _status_from_record(payload: Any, tool: str) -> ExecutionResult:
    outcome = _extract_outcome_state(payload)
    status = _OUTCOME_TO_STATUS.get(outcome or "", ExecutionStatus.UNKNOWN)
    reason = ""
    if status is ExecutionStatus.OBSERVING:
        reason = (
            f"已发送，Runtime 状态 {outcome}；尚未回读确认，"
            "保留 operation_id，不重发。"
        )
    elif status is ExecutionStatus.UNKNOWN:
        reason = (
            f"Runtime 返回 {outcome or '未知状态'}；保留 operation_id 等待核对，"
            "不重发、不视为成功。"
        )
    elif status is ExecutionStatus.CONFIRMED:
        reason = "已由 Runtime 确认。"
    return ExecutionResult(
        status=status,
        tool=tool,
        operation_id=_extract_operation_id(payload),
        reason=reason,
        evidence=_extract_evidence(payload),
    )


class MutationExecutor:
    """按"恰好一次"语义把 action 提交给 Runtime。

    ``operation_ids`` 让同一个 action 在重复提交时复用同一个 id。跨进程恢复时
    应把已用 id 传进来，否则重启后可能对同一意图生成新 id。
    """

    def __init__(
        self,
        client: RuntimeClient,
        *,
        operation_ids: Mapping[str, str] | None = None,
        new_operation_id: Callable[[], str] = generate_operation_id,
    ) -> None:
        self._client = client
        self._ids: dict[str, str] = dict(operation_ids or {})
        self._new_operation_id = new_operation_id

    def operation_id_for(self, action: CandidateAction) -> str:
        """取得（必要时分配）该 action 的 operation id。"""

        key = action_key(action)
        if key not in self._ids:
            self._ids[key] = self._new_operation_id()
        return self._ids[key]

    def known_operation_ids(self) -> dict[str, str]:
        return dict(self._ids)

    async def submit(
        self, action: CandidateAction, *, decision_turn: int
    ) -> MutationOutcome:
        """提交一个 action；``end_turn`` 走 TurnLoop，其余走普通 mutation。"""

        if not isinstance(decision_turn, int) or decision_turn < 0:
            raise ValueError("decision_turn 必须是非负整数。")
        if action.tool not in MUTATION_TOOLS:
            raise MutationError(
                f"{action.tool!r} 不是 Runtime mutation；拒绝提交。"
                "候选必须来自 Runtime 的工具清单。"
            )

        operation_id = self.operation_id_for(action)
        if action.tool == END_TURN_TOOL:
            return await self._submit_end_turn(operation_id, decision_turn)
        if action.tool == RESUME_DECISION_TOOL:
            return await self._submit_resume(action, operation_id)
        return await self._submit_generic(action, operation_id, decision_turn)

    async def _submit_generic(
        self, action: CandidateAction, operation_id: str, decision_turn: int
    ) -> MutationOutcome:
        arguments = dict(action.arguments)
        # Runtime 自己需要这两个字段；不允许模型覆盖，否则可能伪造 intent 绑定。
        arguments["operation_id"] = operation_id
        arguments["decision_turn"] = decision_turn
        payload = await self._call(action.tool, arguments)
        return MutationOutcome(execution=_status_from_record(payload, action.tool))

    async def _submit_end_turn(
        self, operation_id: str, decision_turn: int
    ) -> MutationOutcome:
        payload = await self._call(
            END_TURN_TOOL,
            {"operation_id": operation_id, "decision_turn": decision_turn},
        )
        outcome = _extract_turn_outcome(payload)
        execution = _status_from_record(payload, END_TURN_TOOL)
        pending = _extract_pending_decision(payload) if outcome == "NEEDS_DECISION" else None

        if outcome == "ADVANCED":
            # 只有 TurnLoop 明确说 ADVANCED 才算推进；operation 的 CONFIRMED 另算。
            return MutationOutcome(
                execution=execution, turn_advanced=True, pending_decision=None
            )
        if outcome == "NEEDS_DECISION":
            return MutationOutcome(
                execution=ExecutionResult(
                    status=ExecutionStatus.NEEDS_DECISION,
                    tool=END_TURN_TOOL,
                    operation_id=_extract_operation_id(payload) or operation_id,
                    reason=str(payload.get("reason") or "回合被阻塞，需要模型决策。")
                    if isinstance(payload, Mapping)
                    else "回合被阻塞，需要模型决策。",
                    evidence=execution.evidence,
                ),
                pending_decision=pending,
            )
        if outcome == "RECOVERY_REQUIRED":
            return MutationOutcome(
                execution=ExecutionResult(
                    status=ExecutionStatus.UNKNOWN,
                    tool=END_TURN_TOOL,
                    operation_id=_extract_operation_id(payload) or operation_id,
                    reason="Runtime 要求恢复（RECOVERY_REQUIRED）；本层不自动恢复。",
                    evidence=execution.evidence,
                )
            )
        # 无法识别的 outcome：不得猜成成功。
        return MutationOutcome(
            execution=ExecutionResult(
                status=ExecutionStatus.UNKNOWN,
                tool=END_TURN_TOOL,
                operation_id=_extract_operation_id(payload) or operation_id,
                reason=f"无法识别的 TurnLoop outcome：{outcome!r}；不视为成功。",
                evidence=execution.evidence,
            )
        )

    async def _submit_resume(
        self, action: CandidateAction, operation_id: str
    ) -> MutationOutcome:
        choice = action.arguments.get("choice")
        if not isinstance(choice, str) or not choice.strip():
            raise MutationError("resume_turn_decision 需要非空的 choice。")
        target = action.arguments.get("operation_id")
        payload = await self._call(
            RESUME_DECISION_TOOL,
            {
                "operation_id": str(target) if target else operation_id,
                "choice": choice,
            },
        )
        outcome = _extract_turn_outcome(payload)
        execution = _status_from_record(payload, RESUME_DECISION_TOOL)
        pending = _extract_pending_decision(payload) if outcome == "NEEDS_DECISION" else None
        return MutationOutcome(
            execution=execution,
            turn_advanced=outcome == "ADVANCED",
            pending_decision=pending,
        )

    async def _call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        try:
            return await self._client.call(tool, dict(arguments))
        except Exception as exc:  # noqa: BLE001 - 统一包装，保留动作上下文
            raise MutationError(f"提交 {tool} 失败：{type(exc).__name__}: {exc}") from exc


def action_key(action: CandidateAction) -> str:
    """稳定的 action 身份：同工具同参数视为同一意图。"""

    import json

    try:
        arguments = json.dumps(action.arguments, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        arguments = repr(action.arguments)
    return f"{action.tool}:{arguments}"


def make_execute_fn(
    executor: MutationExecutor,
) -> Callable[..., Any]:
    """构造图用的 ``execute_fn``，把执行器的完整结果交给图。"""

    async def execute_fn(action: CandidateAction, deps: Any) -> MutationOutcome:
        turn = int(getattr(deps, "decision_turn", 0) or 0)
        return await executor.submit(action, decision_turn=turn)

    return execute_fn

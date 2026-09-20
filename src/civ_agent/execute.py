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

    **身份属于决定，不属于参数内容**（审查 R04）：

    - 每个新的决定由宿主分配一个 ``decision_id`` 与一个 ``operation_id``；
      该决定的传输重试复用同一 ``operation_id``；
    - 参数内容只用于**核对**同一个 ``operation_id`` 没有被偷换参数，
      绝不用于认定"所有同参数动作都是同一次决定"。T1 与 T2 的
      ``end_turn`` 参数相同，但它们是两次决定，因此必须是两个 operation。

    ``max_submissions`` 限制一次决定能提交的动作数。超限**报错而不是丢弃**，
    因为静默丢弃会让模型以为动作已经发出（审查 R04 的"新行动被吞掉"）。
    """

    def __init__(
        self,
        client: RuntimeClient,
        *,
        operation_ids: Mapping[str, str] | None = None,
        new_operation_id: Callable[[], str] = generate_operation_id,
        max_submissions: int = 1,
    ) -> None:
        if max_submissions < 1:
            raise ValueError("max_submissions 必须是正整数。")
        self._client = client
        #: decision_id → operation_id；重试同一决定时复用。
        self._ids: dict[str, str] = dict(operation_ids or {})
        #: decision_id → 已提交动作的规范化内容，用于禁止同一 ID 偷换参数。
        self._contents: dict[str, str] = {}
        self._new_operation_id = new_operation_id
        self._max_submissions = max_submissions
        self._submissions: dict[str, int] = {}

    def operation_id_for(self, decision_id: str) -> str:
        """取得（必要时分配）该**决定**的 operation id。"""

        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError("decision_id 必须是非空字符串：身份以决定为单位。")
        if decision_id not in self._ids:
            self._ids[decision_id] = self._new_operation_id()
        return self._ids[decision_id]

    def known_operation_ids(self) -> dict[str, str]:
        return dict(self._ids)

    async def submit(
        self,
        action: CandidateAction,
        *,
        decision_id: str,
        decision_turn: int,
    ) -> MutationOutcome:
        """提交一个 action；``end_turn`` 走 TurnLoop，其余走普通 mutation。"""

        if not isinstance(decision_turn, int) or decision_turn < 0:
            raise ValueError("decision_turn 必须是非负整数。")
        if action.tool not in MUTATION_TOOLS:
            raise MutationError(
                f"{action.tool!r} 不是 Runtime mutation；拒绝提交。"
                "候选必须来自 Runtime 的工具清单。"
            )

        operation_id = self.operation_id_for(decision_id)
        content = action_key(action)

        # 先判次数上限，再判内容一致性：第二次提交一个**不同**动作时，更准确的
        # 诊断是"一次决定只能提交一个动作"，而不是"偷换意图"。
        attempts = self._submissions.get(decision_id, 0)
        if attempts >= self._max_submissions:
            raise MutationError(
                f"决定 {decision_id} 已提交 {attempts} 次，超过 max_submissions="
                f"{self._max_submissions}；拒绝静默丢弃 {content}。"
                "同一回合需要多个动作时，请把它建模为多个决定（各自新 operation_id）。"
            )

        recorded = self._contents.get(decision_id)
        if recorded is not None and recorded != content:
            raise MutationError(
                f"决定 {decision_id} 已用 operation_id {operation_id} 提交过 {recorded}，"
                f"现在却要提交 {content}；拒绝在同一 operation_id 上偷换意图。"
            )

        self._contents[decision_id] = content
        self._submissions[decision_id] = attempts + 1

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
    """一个动作的**规范化内容**，只用于核对同一 operation 没被偷换意图。

    它**不**决定操作身份（审查 R04）：身份来自 decision_id。模型提供的
    ``operation_id`` / ``decision_turn`` 是宿主字段，先剔除再规范化，否则模型多带
    一个最后会被覆盖的字段就能改变这个核对值。
    """

    import json

    arguments = {
        key: value
        for key, value in action.arguments.items()
        if key not in _HOST_OWNED_ARGUMENTS
    }
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        encoded = repr(sorted(arguments.items(), key=lambda item: str(item[0])))
    return f"{action.tool}:{encoded}"


#: 由宿主（执行器/Runtime）注入的字段；模型给的值一律先剔除。
_HOST_OWNED_ARGUMENTS = frozenset({"operation_id", "decision_turn"})


def make_execute_fn(
    executor: MutationExecutor,
) -> Callable[..., Any]:
    """构造图用的 ``execute_fn``，把执行器的完整结果交给图。"""

    async def execute_fn(action: CandidateAction, deps: Any) -> MutationOutcome:
        decision_id = str(getattr(deps, "decision_id", "") or "")
        turn = int(getattr(deps, "decision_turn", 0) or 0)
        return await executor.submit(action, decision_id=decision_id, decision_turn=turn)

    return execute_fn

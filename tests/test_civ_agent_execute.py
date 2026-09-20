"""M12：把选定行动提交给 Runtime 的"恰好一次"语义。

核心不变量（方案 §M12）：

- ``duplicate mutation = 0``：同一意图绝不生成新的 operation_id 重发；
- ``false confirmed = 0``：UNKNOWN / OBSERVING / STALE_INTENT 一律不算成功；
- ``end_turn`` 只能经 TurnLoop，NEEDS_DECISION / RECOVERY_REQUIRED 不得自动处理。

用假 RuntimeClient 精确控制返回 JSON，因此可以逐个覆盖 Runtime 的各种结局。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from civ_agent.execute import (
    END_TURN_TOOL,
    MUTATION_TOOLS,
    RESUME_DECISION_TOOL,
    MutationError,
    MutationExecutor,
    action_key,
    make_execute_fn,
)
from civ_agent.graph import GraphDeps, GraphError, make_execute
from civ_agent.state import CandidateAction, ExecutionStatus


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 假 RuntimeClient
# ---------------------------------------------------------------------------


class FakeRuntimeClient:
    """按工具名返回预设 payload；记录每一次调用。"""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = responses or {}
        self.raise_for: dict[str, Exception] = {}

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, dict(arguments or {})))
        if name in self.raise_for:
            raise self.raise_for[name]
        response = self.responses.get(name, _record("CONFIRMED"))
        return response

    async def list_tools(self) -> tuple[Any, ...]:
        return ()


def _record(outcome: str, *, operation_id: str = "op-1", evidence: bool = True) -> dict[str, Any]:
    """模拟 ``OperationRecord`` 经 ``_json_value`` 的 JSON 形状。"""

    return {
        "operation_id": {"value": operation_id},
        "decision_turn": 7,
        "send_state": "PRECHECKED",
        "outcome_state": outcome,
        "evidence": [{"summary": "unit at (4,5)"}] if evidence else [],
    }


def _turn(outcome: str, *, decision: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "outcome": outcome,
        "operation": _record("CONFIRMED" if outcome == "ADVANCED" else "OBSERVING"),
        "reason": reason,
    }
    if decision is not None:
        payload["decision"] = decision
    return payload


def _action(tool: str = "move_unit", **arguments: Any) -> CandidateAction:
    return CandidateAction(tool=tool, arguments=arguments or {"unit_index": 1, "target_x": 2, "target_y": 3})


# ---------------------------------------------------------------------------
# 恰好一次
# ---------------------------------------------------------------------------


def test_operation_id_is_stable_for_the_same_action() -> None:
    executor = MutationExecutor(FakeRuntimeClient(), new_operation_id=lambda: "op-fixed")

    first = executor.operation_id_for(_action())
    second = executor.operation_id_for(_action())

    assert first == second == "op-fixed"


def test_different_actions_get_different_operation_ids() -> None:
    counter = iter(["op-1", "op-2"])
    executor = MutationExecutor(FakeRuntimeClient(), new_operation_id=lambda: next(counter))

    first = executor.operation_id_for(_action(unit_index=1))
    second = executor.operation_id_for(_action(unit_index=2))

    assert first != second


def test_resubmitting_the_same_action_reuses_the_operation_id() -> None:
    """重复提交同一意图必须复用 id，否则就是 duplicate mutation。"""

    client = FakeRuntimeClient()
    executor = MutationExecutor(client, new_operation_id=lambda: "op-fixed")
    action = _action()

    _run(executor.submit(action, decision_turn=7))
    _run(executor.submit(action, decision_turn=7))

    ids = [call[1]["operation_id"] for call in client.calls]
    assert ids == ["op-fixed", "op-fixed"]


def test_known_operation_ids_can_be_restored_across_processes() -> None:
    """跨进程恢复：传入已用 id 后不得再生成新的 id。"""

    action = _action()
    key = action_key(action)
    executor = MutationExecutor(
        FakeRuntimeClient(),
        operation_ids={key: "op-from-store"},
        new_operation_id=lambda: "op-brand-new",
    )

    assert executor.operation_id_for(action) == "op-from-store"


def test_action_key_is_order_insensitive() -> None:
    assert action_key(_action(a=1, b=2)) == action_key(_action(b=2, a=1))


def test_action_key_separates_different_tools() -> None:
    assert action_key(_action("move_unit")) != action_key(_action("attack_unit"))


# ---------------------------------------------------------------------------
# 不允许假 confirmed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome",
    ["UNKNOWN", "NOT_STARTED", "REJECTED", "STALE_INTENT", "SOMETHING_NEW"],
)
def test_non_confirmed_outcomes_never_become_confirmed(outcome: str) -> None:
    client = FakeRuntimeClient({"move_unit": _record(outcome)})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(_action(), decision_turn=7))

    assert result.execution.status is ExecutionStatus.UNKNOWN
    assert result.execution.status is not ExecutionStatus.CONFIRMED


def test_observing_is_distinguished_from_unknown_but_still_not_confirmed() -> None:
    """已发送未回读 ≠ 结果不明；但两者都不是成功。"""

    client = FakeRuntimeClient({"move_unit": _record("OBSERVING")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(_action(), decision_turn=7))

    assert result.execution.status is ExecutionStatus.OBSERVING
    assert result.execution.status is not ExecutionStatus.CONFIRMED
    assert "不重发" in result.execution.reason


def test_confirmed_outcome_is_reported_as_confirmed() -> None:
    client = FakeRuntimeClient({"move_unit": _record("CONFIRMED")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(_action(), decision_turn=7))

    assert result.execution.status is ExecutionStatus.CONFIRMED
    assert result.execution.operation_id == "op-1"
    assert "unit at (4,5)" in result.execution.evidence


def test_unknown_result_keeps_the_operation_id_for_reconciliation() -> None:
    client = FakeRuntimeClient({"move_unit": _record("UNKNOWN", operation_id="op-unknown")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(_action(), decision_turn=7))

    assert result.execution.operation_id == "op-unknown"
    assert "不重发" in result.execution.reason


def test_unknown_result_is_not_resent() -> None:
    """UNKNOWN 之后不得自动重发原操作。"""

    client = FakeRuntimeClient({"move_unit": _record("UNKNOWN")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    _run(executor.submit(_action(), decision_turn=7))

    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# 参数与白名单
# ---------------------------------------------------------------------------


def test_runtime_owned_arguments_are_injected() -> None:
    client = FakeRuntimeClient()
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    _run(executor.submit(_action(unit_index=9, target_x=1, target_y=2), decision_turn=42))

    name, arguments = client.calls[0]
    assert name == "move_unit"
    assert arguments["operation_id"] == "op-1"
    assert arguments["decision_turn"] == 42
    assert arguments["unit_index"] == 9


def test_model_cannot_forge_the_operation_id() -> None:
    """模型给的 operation_id / decision_turn 必须被 Runtime 侧的值覆盖。"""

    client = FakeRuntimeClient()
    executor = MutationExecutor(client, new_operation_id=lambda: "op-real")
    action = CandidateAction(
        tool="move_unit",
        arguments={"unit_index": 1, "operation_id": "forged", "decision_turn": 999},
    )

    _run(executor.submit(action, decision_turn=7))

    arguments = client.calls[0][1]
    assert arguments["operation_id"] == "op-real"
    assert arguments["decision_turn"] == 7


def test_unknown_tools_are_rejected() -> None:
    executor = MutationExecutor(FakeRuntimeClient())

    with pytest.raises(MutationError, match="不是 Runtime mutation"):
        _run(executor.submit(CandidateAction(tool="launch_nuke", arguments={}), decision_turn=1))


def test_all_runtime_mutation_tools_are_whitelisted() -> None:
    """清单必须与 civ_mcp/runtime/server.py 的 25 个 mutation 一致。"""

    expected = {
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
        "end_turn",
        "resume_turn_decision",
    }

    assert MUTATION_TOOLS == expected
    assert len(MUTATION_TOOLS) == 25


def test_invalid_decision_turn_is_rejected() -> None:
    executor = MutationExecutor(FakeRuntimeClient())

    with pytest.raises(ValueError, match="decision_turn"):
        _run(executor.submit(_action(), decision_turn=-1))


def test_tool_failures_are_wrapped_with_context() -> None:
    client = FakeRuntimeClient()
    client.raise_for["move_unit"] = TimeoutError("tuner 断了")
    executor = MutationExecutor(client)

    with pytest.raises(MutationError, match="move_unit"):
        _run(executor.submit(_action(), decision_turn=7))


# ---------------------------------------------------------------------------
# end_turn 与阻塞
# ---------------------------------------------------------------------------


def test_end_turn_advanced_marks_the_turn_advanced() -> None:
    client = FakeRuntimeClient({END_TURN_TOOL: _turn("ADVANCED")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(CandidateAction(tool=END_TURN_TOOL, arguments={}), decision_turn=7))

    assert result.turn_advanced is True
    assert result.pending_decision is None


def test_end_turn_needs_decision_records_the_blocker() -> None:
    client = FakeRuntimeClient(
        {
            END_TURN_TOOL: _turn(
                "NEEDS_DECISION",
                reason="外交会话待答",
                decision={
                    "decision_type": "DIPLOMACY",
                    "allowed_choices": ["POSITIVE", "NEGATIVE", "EXIT"],
                    "continuation_operation_id": {"value": "op-endturn"},
                    "facts": {"other_civ_name": "埃及"},
                },
            )
        }
    )
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(CandidateAction(tool=END_TURN_TOOL, arguments={}), decision_turn=7))

    assert result.execution.status is ExecutionStatus.NEEDS_DECISION
    assert result.turn_advanced is False
    pending = result.pending_decision
    assert pending is not None
    assert pending.decision_type == "DIPLOMACY"
    assert pending.allowed_choices == ("POSITIVE", "NEGATIVE", "EXIT")
    assert pending.continuation_operation_id == "op-endturn"
    assert pending.facts["other_civ_name"] == "埃及"


def test_end_turn_recovery_required_is_not_treated_as_success() -> None:
    client = FakeRuntimeClient({END_TURN_TOOL: _turn("RECOVERY_REQUIRED")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(CandidateAction(tool=END_TURN_TOOL, arguments={}), decision_turn=7))

    assert result.execution.status is ExecutionStatus.UNKNOWN
    assert result.turn_advanced is False
    assert "恢复" in result.execution.reason


def test_unknown_turn_outcome_is_not_treated_as_success() -> None:
    client = FakeRuntimeClient({END_TURN_TOOL: _turn("SOMETHING_ELSE")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(CandidateAction(tool=END_TURN_TOOL, arguments={}), decision_turn=7))

    assert result.execution.status is ExecutionStatus.UNKNOWN
    assert result.turn_advanced is False


def test_needs_decision_without_a_decision_payload_is_still_reported() -> None:
    client = FakeRuntimeClient({END_TURN_TOOL: _turn("NEEDS_DECISION")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(CandidateAction(tool=END_TURN_TOOL, arguments={}), decision_turn=7))

    assert result.execution.status is ExecutionStatus.NEEDS_DECISION
    assert result.pending_decision is None


def test_pending_decision_is_serializable() -> None:
    client = FakeRuntimeClient(
        {
            END_TURN_TOOL: _turn(
                "NEEDS_DECISION",
                decision={
                    "decision_type": "ENVOY",
                    "allowed_choices": [1, 2],
                    "continuation_operation_id": "op-x",
                },
            )
        }
    )
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(executor.submit(CandidateAction(tool=END_TURN_TOOL, arguments={}), decision_turn=7))

    payload = json.loads(json.dumps(result.pending_decision.as_dict(), ensure_ascii=False))
    assert payload["decision_type"] == "ENVOY"
    assert payload["allowed_choices"] == ["1", "2"]


def test_resume_decision_requires_a_choice() -> None:
    executor = MutationExecutor(FakeRuntimeClient())

    with pytest.raises(MutationError, match="choice"):
        _run(
            executor.submit(
                CandidateAction(tool=RESUME_DECISION_TOOL, arguments={}), decision_turn=7
            )
        )


def test_resume_decision_targets_the_original_end_turn() -> None:
    client = FakeRuntimeClient({RESUME_DECISION_TOOL: _turn("ADVANCED")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")

    result = _run(
        executor.submit(
            CandidateAction(
                tool=RESUME_DECISION_TOOL,
                arguments={"choice": "POSITIVE", "operation_id": "op-endturn"},
            ),
            decision_turn=7,
        )
    )

    name, arguments = client.calls[0]
    assert name == RESUME_DECISION_TOOL
    assert arguments["operation_id"] == "op-endturn"
    assert arguments["choice"] == "POSITIVE"
    assert result.turn_advanced is True


def test_resume_decision_without_target_falls_back_to_its_own_id() -> None:
    client = FakeRuntimeClient({RESUME_DECISION_TOOL: _turn("ADVANCED")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-own")

    _run(
        executor.submit(
            CandidateAction(tool=RESUME_DECISION_TOOL, arguments={"choice": "EXIT"}),
            decision_turn=7,
        )
    )

    assert client.calls[0][1]["operation_id"] == "op-own"


# ---------------------------------------------------------------------------
# 图接线
# ---------------------------------------------------------------------------


def test_execute_node_reports_pending_decision_and_turn_flag() -> None:
    client = FakeRuntimeClient(
        {
            END_TURN_TOOL: _turn(
                "NEEDS_DECISION",
                decision={
                    "decision_type": "CITY_CAPTURE",
                    "allowed_choices": ["KEEP", "RAZE", "REJECT"],
                    "continuation_operation_id": "op-endturn",
                },
            )
        }
    )
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")
    node = make_execute(
        GraphDeps(client=client, allow_mutation=True, executor=executor, decision_turn=7)
    )

    result = _run(node({"final_action": CandidateAction(tool=END_TURN_TOOL, arguments={})}))

    assert result["execution"].status is ExecutionStatus.NEEDS_DECISION
    assert result["pending_decision"]["decision_type"] == "CITY_CAPTURE"
    assert result["turn_advanced"] is False


def test_execute_node_requires_an_executor() -> None:
    node = make_execute(GraphDeps(client=FakeRuntimeClient(), allow_mutation=True))

    with pytest.raises(GraphError, match="executor"):
        _run(node({"final_action": _action()}))


def test_execute_node_uses_deps_decision_turn() -> None:
    client = FakeRuntimeClient()
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")
    node = make_execute(
        GraphDeps(client=client, allow_mutation=True, executor=executor, decision_turn=33)
    )

    _run(node({"final_action": _action()}))

    assert client.calls[0][1]["decision_turn"] == 33


def test_make_execute_fn_delegates_to_the_executor() -> None:
    client = FakeRuntimeClient()
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")
    fn = make_execute_fn(executor)

    outcome = _run(fn(_action(), GraphDeps(client=client, decision_turn=5)))

    assert outcome.execution.status is ExecutionStatus.CONFIRMED
    assert client.calls[0][1]["decision_turn"] == 5

"""M10：LangGraph 只读链路的端到端验证。

事实走真实的内存 MCP 协议（假 Runtime server），模型与 Jev 判断器是假对象，因此
整条链路离线可跑：不连 FireTuner、不需要 API key、不操作真实游戏。

只读阶段的核心断言是：**Jev 必须经过两次，且 execute 不提交任何 mutation**。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from civ_agent.graph import GraphDeps, GraphError, GraphResources, build_graph
from civ_agent.mcp_client import RuntimeClient
from civ_agent.observation import Observation
from civ_agent.state import ExecutionStatus, Seed, new_state
from mcp_fixtures import build_fake_server


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed() -> Seed:
    return Seed(
        benchmark_save="benchmark_start.Civ6Save",
        branch_token="bench-test",
        game_id="game_20260920_083500",
        civilization="巴比伦",
        leader="汉谟拉比",
    )


# ---------------------------------------------------------------------------
# 假模型与假判断器（形状与真实节点一致）
# ---------------------------------------------------------------------------


class FakeMessage:
    def __init__(self, text: str = "", tool_calls: list[dict[str, Any]] | None = None) -> None:
        self.text = text
        self.content = text
        self.tool_calls = tool_calls or []


class FakeModel:
    """deepseek_decide 返回"无工具调用"；deepseek_finalize 返回 0（不提交行动）。"""

    def __init__(self, *, decisions: list[FakeMessage] | None = None, final: str = "0") -> None:
        self.decisions = list(decisions or [FakeMessage(text="只读阶段：本回合不提交行动。")])
        self.final = final
        self.final_calls = 0

    def bind_tools(self, tools: list[Any]) -> "FakeModel":
        return self

    async def ainvoke(self, messages: Any) -> FakeMessage:
        # deepseek_finalize 的 prompt 里会带"候选"字样；decide 不带。
        joined = " ".join(str(getattr(m, "content", "")) for m in messages)
        if "候选" in joined and "最终" in joined:
            self.final_calls += 1
            return FakeMessage(text=self.final)
        return self.decisions.pop(0) if self.decisions else FakeMessage(text="（无更多响应）")


class FakeAnswer:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return dict(self._payload)


class FakeClassifier:
    """按问题 id 集合区分 assess 与 review，返回对应答案。"""

    def __init__(self) -> None:
        self.calls: list[set[str]] = []

    async def ainvoke(self, state: Any) -> Any:
        questions: dict[str, Any] = getattr(self, "questions", {})
        ids = set(questions)
        self.calls.append(ids)
        answers: dict[str, Any] = {}
        for question_id in ids:
            answers[question_id] = self._answer_for(question_id)
        return type("Response", (), {"answers": answers})()

    @staticmethod
    def _answer_for(question_id: str) -> FakeAnswer:
        if question_id == "military_pressure":
            return FakeAnswer({"type": "score", "score": 0.4, "confidence": 0.6})
        if question_id == "strategic_direction":
            return FakeAnswer(
                {"type": "choice", "choice": "develop", "confidence": 0.7}
            )
        if question_id == "reversibility":
            return FakeAnswer({"type": "score", "score": 0.5, "confidence": 0.5})
        if question_id == "plan_conflicts_with_assessment":
            return FakeAnswer({"type": "noul", "noul": 0.1})
        if question_id == "needs_confirmation":
            return FakeAnswer({"type": "noul", "noul": 0.2})
        return FakeAnswer({"type": "noul", "noul": 0.8})


def _resources() -> tuple[GraphResources, FakeClassifier, FakeModel]:
    classifier = FakeClassifier()

    def factory(questions: dict[str, Any]) -> FakeClassifier:
        classifier.questions = questions  # type: ignore[attr-defined]
        return classifier

    model = FakeModel()
    return GraphResources(classifier_factory=factory, model=model), classifier, model


# ---------------------------------------------------------------------------
# 端到端只读运行
# ---------------------------------------------------------------------------


def _run_chain(**deps_overrides: Any) -> tuple[dict[str, Any], FakeClassifier, FakeModel]:
    server = build_fake_server()
    resources, classifier, model = _resources()

    async def scenario() -> dict[str, Any]:
        async with create_connected_server_and_client_session(server) as session:
            deps = GraphDeps(client=RuntimeClient(session), **deps_overrides)
            graph = build_graph(deps, resources)
            return await graph.ainvoke(new_state(seed=_seed(), turn=1))

    return _run(scenario()), classifier, model


def test_read_only_chain_completes_and_produces_an_observation() -> None:
    final, _classifier, _model = _run_chain()

    observation = final["observation"]
    assert isinstance(observation, Observation)
    assert observation.turn == 7
    assert observation.our_state.civilization == "巴比伦"


def test_jev_runs_both_assess_and_review() -> None:
    """方案硬约束：正式路径必须经过 Jev 两次。"""

    final, classifier, _model = _run_chain()

    assert len(classifier.calls) == 2
    assess_ids, review_ids = classifier.calls
    assert "strategic_direction" in assess_ids
    assert "plan_conflicts_with_assessment" in review_ids
    assert final["jev_assess"] is not None
    assert final["jev_review"] is not None


def test_read_only_chain_does_not_execute_any_mutation() -> None:
    final, _classifier, _model = _run_chain()

    execution = final["execution"]
    assert execution is not None
    assert execution.status is ExecutionStatus.NOT_ATTEMPTED
    assert "未选定任何行动" in execution.reason


def test_read_only_chain_does_not_write_game_memory() -> None:
    final, _classifier, _model = _run_chain()

    assert final.get("memory_file") is None


def test_chain_preserves_unknown_facts_from_runtime() -> None:
    final, _classifier, _model = _run_chain()

    assert final["unknown"] == ("units: TimeoutError",)


def test_chain_records_the_rule_and_memory_channels_as_empty_not_missing() -> None:
    final, _classifier, _model = _run_chain()

    assert final["rule_queries"] == ()
    assert final["memory_hits"] == ()

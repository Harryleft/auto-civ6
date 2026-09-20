"""M10 图的拓扑与路由不变量。

方案最硬的一条约束是**正式路径不允许 ``observe → DeepSeek → execute``**，所以这里
直接对编译后的图做结构断言，而不是只靠"跑一遍没报错"。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from civ_agent.graph import (
    MAX_SEARCH_BACKEDGES,
    GraphDeps,
    GraphError,
    GraphResources,
    build_graph,
    make_observe,
    route_after_backedge,
    route_after_decide,
)
from civ_agent.nodes.jev import JevError
from civ_agent.observation import OurState, Observation
from civ_agent.state import CandidateAction, ExecutionResult, ExecutionStatus, Seed, new_state


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed() -> Seed:
    return Seed(
        benchmark_save="benchmark_start.Civ6Save",
        branch_token="bench-test",
        game_id="game_20260901_120000",
        civilization="巴比伦",
        leader="汉谟拉比",
    )


class _StubClient:
    """只提供事实，不调用工具；用于不跑端到端的拓扑测试。"""

    def __init__(self, context: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._context = context if context is not None else _context_json()

    async def read_context(self) -> dict[str, Any]:
        return self._context

    async def list_tools(self) -> tuple[Any, ...]:
        return ()

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, dict(arguments or {})))
        return {"tool": name, "arguments": dict(arguments or {})}


def _context_json() -> dict[str, Any]:
    return {
        "facts": {
            "overview": {
                "value": {
                    "turn": 5,
                    "player_id": 0,
                    "civ_name": "巴比伦",
                    "leader_name": "汉谟拉比",
                    "num_cities": 1,
                    "total_population": 1,
                    "gold": 10.0,
                    "gold_per_turn": 1.0,
                    "science_yield": 2.0,
                    "culture_yield": 1.0,
                    "faith": 0.0,
                    "score": 5,
                },
                "coverage": "CURRENT_GAME:COMPLETE",
            }
        },
        "unknown": [],
    }


def _edges(graph: Any) -> set[tuple[str, str]]:
    return {
        (edge.source, edge.target)
        for edge in graph.get_graph().edges
    }


# ---------------------------------------------------------------------------
# 拓扑不变量
# ---------------------------------------------------------------------------


def test_graph_compiles_with_the_full_node_set() -> None:
    graph = build_graph(GraphDeps(client=_StubClient()))

    nodes = set(graph.get_graph().nodes)
    for expected in (
        "observe",
        "retrieve_memory",
        "jev_assess",
        "deepseek_decide",
        "search_rules",
        "read_game_info",
        "jev_review",
        "deepseek_finalize",
        "execute",
        "verify",
        "append_game_memory",
    ):
        assert expected in nodes, expected


def test_main_path_goes_through_jev_twice() -> None:
    """方案 §12：Jev 必须经过两次，正式路径不得直连 DeepSeek→execute。"""

    edges = _edges(build_graph(GraphDeps(client=_StubClient())))

    assert ("retrieve_memory", "jev_assess") in edges
    assert ("jev_assess", "deepseek_decide") in edges
    assert ("deepseek_decide", "jev_review") in edges
    assert ("jev_review", "deepseek_finalize") in edges
    assert ("deepseek_finalize", "execute") in edges


def test_no_direct_edge_from_decision_to_execute() -> None:
    """禁止 ``observe → DeepSeek → execute``：execute 只能由 finalize 进入。"""

    graph = build_graph(GraphDeps(client=_StubClient()))
    incoming = {target: set() for _s, target in _edges(graph)}
    for source, target in _edges(graph):
        incoming.setdefault(target, set()).add(source)

    assert incoming["execute"] == {"deepseek_finalize"}
    assert "observe" not in incoming["execute"]
    assert "deepseek_decide" not in incoming["execute"]


def test_verify_and_memory_close_the_turn() -> None:
    edges = _edges(build_graph(GraphDeps(client=_StubClient())))

    assert ("execute", "verify") in edges
    assert ("verify", "append_game_memory") in edges


def test_backedges_end_at_the_expected_nodes() -> None:
    edges = _edges(build_graph(GraphDeps(client=_StubClient())))

    assert ("deepseek_decide", "search_rules") in edges
    assert ("deepseek_decide", "read_game_info") in edges
    assert ("count_rules_backedge", "deepseek_decide") in edges
    assert ("count_info_backedge", "observe") in edges
    assert ("count_info_backedge", "deepseek_decide") in edges


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


def test_route_after_decide_defaults_to_review_without_a_request() -> None:
    assert route_after_decide({"tool_request": None}) == "jev_review"
    assert route_after_decide({}) == "jev_review"
    assert route_after_decide({"tool_request": "   "}) == "jev_review"


def test_route_after_decide_honours_explicit_requests() -> None:
    assert route_after_decide({"tool_request": "search_rules"}) == "search_rules"
    assert route_after_decide({"tool_request": "read_game_info"}) == "read_game_info"


def test_route_after_decide_stops_backedges_at_the_cap() -> None:
    """结构保护：即使模型一直请求，回边也必须收敛到 Review。"""

    assert (
        route_after_decide(
            {"tool_request": "search_rules", "backedge_count": MAX_SEARCH_BACKEDGES}
        )
        == "jev_review"
    )


def test_route_after_decide_ignores_unknown_requests() -> None:
    assert route_after_decide({"tool_request": "launch_nuke"}) == "jev_review"


def test_route_after_backedge_sends_read_info_back_to_observe() -> None:
    """方案 §5：需要更多游戏事实时重新进入 Jev Assess。"""

    assert route_after_backedge({"last_backedge": "read_game_info"}) == "observe"
    assert route_after_backedge({"last_backedge": "search_rules"}) == "deepseek_decide"
    assert route_after_backedge({}) == "deepseek_decide"


# ---------------------------------------------------------------------------
# 只读保护
# ---------------------------------------------------------------------------


def test_execute_refuses_to_run_before_m12() -> None:
    """M10 只读阶段：execute 被真正调用时必须失败，而不是操作游戏。"""

    from civ_agent.graph import make_execute

    node = make_execute(GraphDeps(client=_StubClient()))

    async def scenario() -> Any:
        return await node(
            {"final_action": CandidateAction(tool="move_unit", arguments={"unit_index": 0})}
        )

    with pytest.raises(GraphError, match="allow_mutation=False"):
        _run(scenario())


def test_execute_without_a_final_action_is_not_attempted() -> None:
    from civ_agent.graph import make_execute

    node = make_execute(GraphDeps(client=_StubClient()))

    result = _run(node({"final_action": None}))

    assert result["execution"].status is ExecutionStatus.NOT_ATTEMPTED


def test_execute_requires_an_executor_when_mutation_is_allowed() -> None:
    from civ_agent.graph import make_execute

    node = make_execute(GraphDeps(client=_StubClient(), allow_mutation=True))

    async def scenario() -> Any:
        return await node(
            {"final_action": CandidateAction(tool="move_unit", arguments={"unit_index": 0})}
        )

    with pytest.raises(GraphError, match="execute_fn"):
        _run(scenario())


def test_verify_keeps_unknown_as_unknown() -> None:
    """UNKNOWN 不得被当成成功。"""

    from civ_agent.graph import make_verify

    node = make_verify(GraphDeps(client=_StubClient()))

    result = _run(
        node(
            {
                "execution": ExecutionResult(
                    status=ExecutionStatus.UNKNOWN,
                    tool="move_unit",
                    operation_id="op-1",
                )
            }
        )
    )

    assert result["execution"].status is ExecutionStatus.UNKNOWN
    assert "operation_id" in result["execution"].reason or "未知" in result["execution"].reason


# ---------------------------------------------------------------------------
# 回边链路（M11 的形状）
# ---------------------------------------------------------------------------


class _ScriptedModel:
    """按脚本返回响应；用于驱动 search_rules / read_game_info 回边。"""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.bound: list[str] = []

    def bind_tools(self, tools: list[Any]) -> "_ScriptedModel":
        self.bound = [getattr(tool, "name", "?") for tool in tools]
        return self

    async def ainvoke(self, messages: Any) -> Any:
        return self.script.pop(0) if self.script else _Msg(text="（脚本结束）")


class _Msg:
    def __init__(self, text: str = "", tool_calls: list[dict[str, Any]] | None = None) -> None:
        self.text = text
        self.content = text
        self.tool_calls = tool_calls or []


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"{name}-1"}


def test_search_rules_backedge_populates_rule_queries() -> None:
    """模型通过元工具请求查规则，图应回边并写入 rule_queries。"""

    client = _StubClient()
    model = _ScriptedModel(
        [
            _Msg(tool_calls=[_call("search_rules", {"query": "忠诚度 机制"})]),
            _Msg(text="查完了，本回合不提交动作。"),
        ]
    )
    deps = GraphDeps(
        client=client,
        rule_search=lambda query: (
            type("Hit", (), {"doc": "cities.md", "section": "忠诚度机制"})(),
        ),
    )
    graph = build_graph(
        deps, GraphResources(classifier_factory=_fake_classifier_factory(), model=model)
    )

    async def scenario() -> dict[str, Any]:
        return await graph.ainvoke(new_state(seed=_seed(), turn=1))

    final = _run(scenario())

    assert final["backedge_count"] == 1
    assert final["rule_queries"]
    assert "cities.md" in final["rule_queries"][0]["result"]
    assert final["rule_queries"][0]["query"] == "忠诚度 机制"


def test_read_game_info_backedge_calls_the_requested_tool() -> None:
    """模型请求补读事实时，图应真的调用那个只读工具。"""

    client = _StubClient()
    model = _ScriptedModel(
        [
            _Msg(
                tool_calls=[
                    _call(
                        "read_game_info",
                        {"tool": "get_policies", "arguments": {"city_id": 1}},
                    )
                ]
            ),
            _Msg(text="读完了。"),
        ]
    )
    deps = GraphDeps(client=client)
    graph = build_graph(
        deps, GraphResources(classifier_factory=_fake_classifier_factory(), model=model)
    )

    async def scenario() -> dict[str, Any]:
        return await graph.ainvoke(new_state(seed=_seed(), turn=1))

    final = _run(scenario())

    assert ("get_policies", {"city_id": 1}) in client.calls
    assert "get_policies" in final["extra_facts"]
    assert final["backedge_count"] == 1


def test_meta_requests_are_not_recorded_as_action_candidates() -> None:
    """查规则不是游戏动作：不能进 candidates，否则会被当成可执行操作。

    回边被消费后 ``tool_request`` 会被清空（否则会在下一轮重复触发），因此这里
    用 ``rule_queries`` 证明请求被执行、用 ``candidates`` 证明它没被当成动作。
    """

    client = _StubClient()
    model = _ScriptedModel(
        [
            _Msg(tool_calls=[_call("search_rules", {"query": "区域成本"})]),
            _Msg(text="done"),
        ]
    )
    graph = build_graph(
        GraphDeps(
            client=client,
            rule_search=lambda query: (
                type("Hit", (), {"doc": "cities.md", "section": "区域（District）"})(),
            ),
        ),
        GraphResources(classifier_factory=_fake_classifier_factory(), model=model),
    )

    final = _run(graph.ainvoke(new_state(seed=_seed(), turn=1)))

    assert final["candidates"] == ()
    assert final["rule_queries"][0]["query"] == "区域成本"
    assert final["tool_request"] is None


def test_backedge_cap_forces_convergence_to_review() -> None:
    """模型一直请求查规则时，回边次数必须被结构上限截断。"""

    client = _StubClient()
    model = _ScriptedModel(
        [_Msg(tool_calls=[_call("search_rules", {"query": "x"})]) for _ in range(30)]
    )
    graph = build_graph(
        GraphDeps(client=client, rule_search=lambda query: ()),
        GraphResources(classifier_factory=_fake_classifier_factory(), model=model),
    )

    final = _run(graph.ainvoke(new_state(seed=_seed(), turn=1)))

    assert final["backedge_count"] == MAX_SEARCH_BACKEDGES
    # 截断后仍然走完 Jev Review 与 finalize，而不是卡死。
    assert final["jev_review"] is not None


def test_meta_tools_are_offered_to_the_model() -> None:
    """模型必须看得到这两个元工具，否则无法表达"我要查规则"。"""

    from civ_agent.nodes.deepseek import build_meta_tools

    names = {tool.name for tool in build_meta_tools()}

    assert names == {"search_rules", "read_game_info"}


def _fake_classifier_factory() -> Any:
    class Classifier:
        def __init__(self, questions: dict[str, Any]) -> None:
            self.questions = questions

        async def ainvoke(self, state: Any) -> Any:
            answers = {
                question_id: type(
                    "Answer", (), {"model_dump": lambda self, q=question_id: _payload(q)}
                )()
                for question_id in self.questions
            }
            return type("Response", (), {"answers": answers})()

    return lambda questions: Classifier(questions)


def _payload(question_id: str) -> dict[str, Any]:
    if question_id == "strategic_direction":
        return {"type": "choice", "choice": "develop", "confidence": 0.6}
    if question_id in {"military_pressure", "reversibility"}:
        return {"type": "score", "score": 0.5, "confidence": 0.5}
    return {"type": "noul", "noul": 0.9}

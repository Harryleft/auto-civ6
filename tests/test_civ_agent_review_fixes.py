"""审查修复的回归测试：一条发现一个测试。

对应《Civ6 架构审查_第一性原理_9082880》的 R01–R06、R08 与附加问题。审查明确
要求"不能用一个替换整个 Jev/DeepSeek 节点的假函数绕过失败"，因此这里尽量装配
**真实的图、真实的 classifier 工厂、真实的执行器**，只替换：
模型 HTTP 边界、TypeSafe 的 classifier 替身、游戏/MCP 响应。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from civ_agent.execute import END_TURN_TOOL, MutationExecutor
from civ_agent.graph import GraphDeps, GraphResources, build_graph
from civ_agent.mcp_client import RuntimeClient
from civ_agent.nodes.jev import _build_questions, ASSESS_QUESTIONS, REVIEW_QUESTIONS, make_classifier_factory
from civ_agent.state import CandidateAction, Seed, new_state
from mcp_fixtures import fake_runtime_context


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed() -> Seed:
    return Seed(
        benchmark_save="benchmark_start.Civ6Save",
        branch_token="review-fixes",
        game_id="game_review",
        civilization="巴比伦",
        leader="汉谟拉比",
    )


# ---------------------------------------------------------------------------
# 假 Runtime server：带真实 MCP 只读注解
# ---------------------------------------------------------------------------


def build_review_server(read_log: list[str], write_log: list[str]) -> FastMCP:
    server = FastMCP("Review Fake Runtime")

    @server.tool(annotations={"readOnlyHint": True})
    async def get_runtime_context() -> dict[str, Any]:
        """Return fresh facts."""
        return fake_runtime_context()

    @server.tool(annotations={"readOnlyHint": True})
    async def get_city_production(city_id: int) -> dict[str, Any]:
        """Read-only production candidates with a unique marker."""
        read_log.append(f"get_city_production:{city_id}")
        return {
            "value": [{"item": "MARKER-PRODUCTION-SLOT", "city_id": city_id}],
            "coverage": "OK",
        }

    @server.tool()
    async def move_unit(unit_index: int, target_x: int, target_y: int) -> dict[str, Any]:
        """A write tool: must never run through the read-only path."""
        write_log.append(f"move_unit:{unit_index}")
        return {"outcome_state": "CONFIRMED", "operation_id": {"value": "op-write"}}

    return server


# ---------------------------------------------------------------------------
# 假模型：记录真正发出的消息
# ---------------------------------------------------------------------------


class _Msg:
    def __init__(self, text: str = "", tool_calls: list[dict[str, Any]] | None = None) -> None:
        self.text = text
        self.content = text
        self.tool_calls = tool_calls or []


class SpyModel:
    """按脚本返回响应，同时留下**实际发给模型的**消息。"""

    def __init__(self, script: list[_Msg], *, final: str = "0") -> None:
        self.script = list(script)
        self.final = final
        self.system_prompts: list[str] = []
        self.tool_messages: list[str] = []
        self.final_prompts: list[str] = []

    def bind_tools(self, tools: list[Any]) -> "SpyModel":
        self.bound = [getattr(tool, "name", "?") for tool in tools]
        return self

    async def ainvoke(self, messages: Any) -> _Msg:
        joined = " ".join(str(getattr(m, "content", "")) for m in messages)
        if "最终决策者" in joined:
            self.final_prompts.append(joined)
            return _Msg(text=self.final)
        # 记录系统提示与工具回执，用来证明证据真的到达模型。
        for message in messages:
            if type(message).__name__ == "SystemMessage":
                self.system_prompts.append(str(message.content))
            elif type(message).__name__ == "ToolMessage":
                self.tool_messages.append(str(message.content))
        return self.script.pop(0) if self.script else _Msg(text="（脚本结束）")


class _Answer:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return dict(self._payload)


class RecordingClassifier:
    """记录每次收到的 state 与问题集合。"""

    def __init__(self, questions: dict[str, Any], log: list[dict[str, Any]]) -> None:
        self.questions = questions
        self._log = log

    async def ainvoke(self, state: Any) -> Any:
        self._log.append({"questions": set(self.questions), "state": state})
        answers = {
            question_id: _Answer(_answer_for(question_id)) for question_id in self.questions
        }
        return type("Response", (), {"answers": answers})()


def _answer_for(question_id: str) -> dict[str, Any]:
    if question_id in {"immediate_risk", "action_cost"}:
        return {"type": "score", "score": 0.3, "confidence": 0.6}
    if question_id in {"assumptions_supported", "cost_understood", "information_sufficient"}:
        return {"type": "noul", "noul": 0.9}
    return {"type": "noul", "noul": 0.2}


def _recording_factory(log: list[dict[str, Any]]) -> Any:
    return lambda questions: RecordingClassifier(questions, log)


# ---------------------------------------------------------------------------
# R01：同一个工厂先 Assess 再 Review
# ---------------------------------------------------------------------------


def test_r01_one_factory_serves_two_question_sets() -> None:
    """用**真实**的 make_classifier_factory，而不是自造工厂。"""

    factory = make_classifier_factory(api_key="test-key")

    assess = factory(_build_questions(ASSESS_QUESTIONS))
    review = factory(_build_questions(REVIEW_QUESTIONS))

    assert assess is not review, "Assess 与 Review 必须是两个实例，否则问题会串"
    assert set(assess.questions) == {spec["id"] for spec in ASSESS_QUESTIONS}
    assert set(review.questions) == {spec["id"] for spec in REVIEW_QUESTIONS}


def test_r01_review_receives_its_own_questions_through_the_graph(
    tmp_path: Path,
) -> None:
    """端到端：图的两次 Jev 调用必须各自拿到正确问题集合。"""

    read_log: list[str] = []
    write_log: list[str] = []
    jlog: list[dict[str, Any]] = []
    model = SpyModel([_Msg(text="不提交动作。")])

    async def scenario() -> dict[str, Any]:
        async with create_connected_server_and_client_session(
            build_review_server(read_log, write_log)
        ) as session:
            deps = GraphDeps(client=RuntimeClient(session), memory_search=lambda *a, **k: ())
            resources = GraphResources(
                classifier_factory=_recording_factory(jlog), model=model
            )
            return await build_graph(deps, resources).ainvoke(
                new_state(seed=_seed(), turn=1, decision_id="d-review-questions")
            )

    _run(scenario())

    assert len(jlog) == 2
    assert jlog[0]["questions"] == {spec["id"] for spec in ASSESS_QUESTIONS}
    assert jlog[1]["questions"] == {spec["id"] for spec in REVIEW_QUESTIONS}


# ---------------------------------------------------------------------------
# R02：证据必须真的进入模型消息
# ---------------------------------------------------------------------------


def test_r02_rule_and_memory_evidence_reach_the_model_messages(
    tmp_path: Path,
) -> None:
    """在检索正文里放唯一标记，断言它出现在真正发往模型的消息里。"""

    marker = "MARKER-RULE-BODY-3f9a"
    read_log: list[str] = []
    write_log: list[str] = []
    jlog: list[dict[str, Any]] = []
    model = SpyModel(
        [
            _Msg(tool_calls=[{"name": "search_rules", "args": {"query": "区域成本"}, "id": "c1"}]),
            _Msg(text="查完了，暂不提交。"),
        ]
    )

    async def scenario() -> dict[str, Any]:
        async with create_connected_server_and_client_session(
            build_review_server(read_log, write_log)
        ) as session:
            deps = GraphDeps(
                client=RuntimeClient(session),
                memory_search=lambda *a, **k: (),
                rule_search=lambda query: (
                    {
                        "doc": "cities.md",
                        "section": "区域成本",
                        "level": 2,
                        "excerpt": marker,
                    },
                ),
            )
            resources = GraphResources(
                classifier_factory=_recording_factory(jlog), model=model
            )
            return await build_graph(deps, resources).ainvoke(
                new_state(seed=_seed(), turn=1, decision_id="d-evidence")
            )

    final = _run(scenario())

    assert marker in final["rule_queries"][0]["hits"][0]["excerpt"]
    joined = "\n".join(model.system_prompts)
    assert marker in joined, "规则正文必须进入模型消息，而不只是停在 state 里"


def test_r02_jev_receives_the_decision_material() -> None:
    """Jev Assess 的 state 必须包含材料，而不只是 Observation 摘要。"""

    from civ_agent.decision import build_decision_context

    context = build_decision_context(
        {
            "turn": 5,
            "observation": None,
            "rule_queries": (
                {"query": "x", "hits": ({"doc": "cities.md", "section": "s", "excerpt": "正文标记"},)},
            ),
            "memory_hits": (),
            "extra_facts": {"get_policies": {"value": "标记-补读"}},
            "long_term_goal": "目标标记",
            "runtime_facts": {"units": {"value": [{"unit_index": 1, "x": 3, "y": 4}]}},
        }
    )

    payload = context.as_dict()
    assert payload["rule_evidence"][0]["excerpt"] == "正文标记"
    assert payload["extra_facts"]["get_policies"]["value"] == "标记-补读"
    assert payload["goal"] == "目标标记"
    # 审查 R07：可操作实体也要在材料里，而不只是日志摘要
    assert payload["facts"]["runtime"]["units"]["value"][0]["unit_index"] == 1


# ---------------------------------------------------------------------------
# R03：只读通道不能发出修改
# ---------------------------------------------------------------------------


def test_r03_read_only_client_refuses_a_write_tool() -> None:
    from civ_agent.mcp_client import RuntimeClient, ToolSpec, tool_result_value

    class Session:
        async def list_tools(self, cursor: str | None = None) -> Any:
            spec = ToolSpec(name="move_unit", description="写", read_only=False)

            class R:
                tools = [
                    type(
                        "T",
                        (),
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "inputSchema": {"type": "object"},
                            "annotations": None,
                        },
                    )()
                ]
                nextCursor = None

            return R()

        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
            raise AssertionError("只读通道不应真的调用写工具")

    client = RuntimeClient(Session())

    with pytest.raises(PermissionError, match="只读"):
        _run(client.call_read_only("move_unit", {}))


def test_r03_read_game_info_refuses_a_write_tool_through_the_graph() -> None:
    """审查 P4 的反例：read_game_info(tool="move_unit") 必须被拒绝且写调用数为 0。"""

    read_log: list[str] = []
    write_log: list[str] = []
    jlog: list[dict[str, Any]] = []
    model = SpyModel(
        [
            _Msg(
                tool_calls=[
                    {
                        "name": "read_game_info",
                        "args": {"tool": "move_unit", "arguments": {"unit_index": 1, "target_x": 2, "target_y": 3}},
                        "id": "c1",
                    }
                ]
            ),
            _Msg(text="被拒绝了，停止。"),
        ]
    )

    async def scenario() -> dict[str, Any]:
        async with create_connected_server_and_client_session(
            build_review_server(read_log, write_log)
        ) as session:
            deps = GraphDeps(client=RuntimeClient(session), memory_search=lambda *a, **k: ())
            resources = GraphResources(
                classifier_factory=_recording_factory(jlog), model=model
            )
            return await build_graph(deps, resources).ainvoke(
                new_state(seed=_seed(), turn=1, decision_id="d-readonly")
            )

    final = _run(scenario())

    assert write_log == [], "补读通道绝不能发出写操作"
    recorded = final["extra_facts"]["move_unit"]
    assert recorded["refused"] is True
    assert "只读" in recorded["reason"]


# ---------------------------------------------------------------------------
# R04 / R05：决定身份与提交回合
# ---------------------------------------------------------------------------


def test_r04_two_decisions_of_end_turn_get_two_operations() -> None:
    """审查 P2 的反例：不再用参数内容当身份。"""

    from tests.test_civ_agent_execute import FakeRuntimeClient, _turn

    counter = iter(["op-t1", "op-t2"])
    client = FakeRuntimeClient({END_TURN_TOOL: _turn("ADVANCED")})
    executor = MutationExecutor(client, new_operation_id=lambda: next(counter))
    action = CandidateAction(tool=END_TURN_TOOL, arguments={})

    _run(executor.submit(action, decision_id="T1", decision_turn=1))
    _run(executor.submit(action, decision_id="T2", decision_turn=2))

    ids = [call[1]["operation_id"] for call in client.calls]
    assert ids == ["op-t1", "op-t2"]


def test_r05_submitted_turn_comes_from_the_observation() -> None:
    """审查 P6 的反例：state.turn=2 时不得提交静态 0/1。"""

    from civ_agent.graph import make_execute
    from tests.test_civ_agent_execute import FakeRuntimeClient, _record

    client = FakeRuntimeClient({"move_unit": _record("CONFIRMED")})
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")
    node = make_execute(
        GraphDeps(client=client, allow_mutation=True, executor=executor, decision_id="d1")
    )

    _run(node({"final_action": CandidateAction(tool="move_unit", arguments={"unit_index": 1}), "turn": 2}))

    assert client.calls[0][1]["decision_turn"] == 2


# ---------------------------------------------------------------------------
# R06：查询真的执行；写工具才是候选
# ---------------------------------------------------------------------------


def test_r06_read_only_tool_call_executes_and_returns_body_to_the_model() -> None:
    """审查 R06：get_city_production 必须真的查到数据，且不作为 mutation 候选。"""

    read_log: list[str] = []
    write_log: list[str] = []
    jlog: list[dict[str, Any]] = []
    model = SpyModel(
        [
            _Msg(tool_calls=[{"name": "get_city_production", "args": {"city_id": 1}, "id": "c1"}]),
            _Msg(text="看到了生产候选，本回合不提交动作。"),
        ]
    )

    async def scenario() -> dict[str, Any]:
        async with create_connected_server_and_client_session(
            build_review_server(read_log, write_log)
        ) as session:
            deps = GraphDeps(client=RuntimeClient(session), memory_search=lambda *a, **k: ())
            resources = GraphResources(
                classifier_factory=_recording_factory(jlog), model=model
            )
            return await build_graph(deps, resources).ainvoke(
                new_state(seed=_seed(), turn=1, decision_id="d-readonly-exec")
            )

    final = _run(scenario())

    assert read_log == ["get_city_production:1"], "只读查询必须真的执行"
    assert final["candidates"] == (), "只读查询不得成为 mutation 候选"
    assert any("MARKER-PRODUCTION-SLOT" in message for message in model.tool_messages)


def test_r06_meta_tools_are_available_on_the_default_path() -> None:
    """审查 R06：默认 tools=None 时也必须给模型补读出口。"""

    from civ_agent.nodes.deepseek import build_agent_tools
    from civ_agent.mcp_client import ToolSpec

    class Client:
        async def call(self, name: str, arguments: Any = None) -> Any:  # pragma: no cover
            return {}

    tools = build_agent_tools(
        (ToolSpec(name="get_policies", description="只读", read_only=True),), Client()
    )
    names = {tool.name for tool in tools}

    assert {"search_rules", "read_game_info", "get_policies"} <= names


# ---------------------------------------------------------------------------
# R08：Memory 是连续经历
# ---------------------------------------------------------------------------


def test_r08_two_decisions_in_one_turn_are_both_recorded(tmp_path: Path) -> None:
    from civ_agent.memory import DecisionRecord, GameMemoryWriter, GameStart, TurnRecord
    from civ_agent.observation import OurState

    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))
    our = OurState(civilization="巴比伦", score=12)

    writer.append_turn_state(path, TurnRecord(turn=57, our_state=our))
    writer.append_decision(
        path, 57, DecisionRecord(decision_id="57-1", our_state=our, final_action=("set_research",))
    )
    writer.append_decision(
        path, 57, DecisionRecord(decision_id="57-2", our_state=our, final_action=("end_turn",))
    )
    writer.close_turn(path, 57, advanced_to=58)

    text = path.read_text(encoding="utf-8")
    assert "<!-- turn-state 57 -->" in text
    # 只应有一个回合状态段（收尾小节的 "### Turn 57 收尾" 不算独立回合段）。
    assert [line for line in text.splitlines() if line == "## Turn 57"] == ["## Turn 57"]
    assert "Decision 57-57-1" in text
    assert "Decision 57-57-2" in text
    assert "已进入 Turn 58" in text


def test_r08_repeated_writes_are_idempotent(tmp_path: Path) -> None:
    """恢复后重跑同一节点不得重复写（审查 R08）。"""

    from civ_agent.memory import DecisionRecord, GameMemoryWriter, GameStart, TurnRecord
    from civ_agent.observation import OurState

    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))
    our = OurState(civilization="巴比伦", score=12)
    record = TurnRecord(turn=57, our_state=our)
    decision = DecisionRecord(decision_id="57-1", our_state=our)

    for _ in range(3):
        writer.append_turn_state(path, record)
        writer.append_decision(path, 57, decision)
        writer.close_turn(path, 57, advanced_to=58)

    text = path.read_text(encoding="utf-8")
    assert text.count("<!-- turn-state 57 -->") == 1
    assert text.count("<!-- decision 57-1 -->") == 1
    assert text.count("<!-- turn-close 57 -->") == 1


def test_r08_consequences_cannot_be_backfilled_onto_a_missing_decision(tmp_path: Path) -> None:
    from civ_agent.memory import GameMemoryWriter, GameStart

    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))

    with pytest.raises(ValueError, match="尚未写入"):
        writer.record_consequence(path, "57-99", ("不应该出现",))


def test_r08_close_turn_states_a_non_advance_explicitly(tmp_path: Path) -> None:
    from civ_agent.memory import GameMemoryWriter, GameStart

    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))

    writer.close_turn(path, 57, advanced_to=None, note="被外交阻塞")

    text = path.read_text(encoding="utf-8")
    assert "本回合尚未推进" in text
    assert "被外交阻塞" in text


# ---------------------------------------------------------------------------
# 附加问题：最终编号解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1", 1),
        ("2", 2),
        ("0", 0),
        ("编号：2", 2),
        ("（2）", 2),
        ("99 then 2", None),
        ("-1", None),
        ("4", None),
        ("none", None),
        ("", None),
    ],
)
def test_extra_parse_choice_is_strict(text: str, expected: int | None) -> None:
    from civ_agent.nodes.deepseek import _parse_choice

    assert _parse_choice(text, 3) == expected


# ---------------------------------------------------------------------------
# 真实验收发现：阈值校准 + 否决反馈
# ---------------------------------------------------------------------------


def test_blocking_threshold_matches_the_measured_distribution() -> None:
    """真实验收实测合理动作只得 0.10–0.26；门槛必须落在分布内。"""

    from civ_agent.nodes.jev import BLOCKING_THRESHOLD

    assert 0.15 <= BLOCKING_THRESHOLD <= 0.35, (
        "门槛高于实测分布会让任何动作都过不了复核（真实验收教训）"
    )


def test_review_provides_readable_blocking_reasons() -> None:
    """被拦时必须给出可读理由，否则无法反馈给模型。"""

    from civ_agent.nodes.jev import _payload_from_verdicts, _verdicts

    class Answer:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def model_dump(self) -> dict[str, Any]:
            return dict(self._payload)

    from civ_agent.nodes.jev import REVIEW_QUESTIONS

    answers = {
        spec["id"]: Answer(
            {"type": "score", "score": 0.0}
            if spec["kind"] == "score"
            else {"type": "noul", "noul": 0.02}
        )
        for spec in REVIEW_QUESTIONS
    }
    response = type("Response", (), {"answers": answers})()

    payload = _payload_from_verdicts(_verdicts(REVIEW_QUESTIONS, response))

    assert payload["blocking"], "极低概率应当触发阻断"
    assert len(payload["blocking_reasons"]) == len(payload["blocking"])
    assert all("=" in reason for reason in payload["blocking_reasons"])


def test_veto_feedback_reaches_the_next_decision_material() -> None:
    """被拦后的理由必须出现在下一轮决策材料里。"""

    from civ_agent.decision import build_decision_context

    context = build_decision_context(
        {
            "turn": 1,
            "observation": None,
            "review_feedback": "上一轮候选被复核拦下：assumptions_supported=否",
        }
    )

    assert "assumptions_supported" in context.as_dict()["review_feedback"]

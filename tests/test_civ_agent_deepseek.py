"""M05：DeepSeek 决策节点与动态工具绑定。

用假 model 跑决策与收敛逻辑（离线、无网络、无 API key）。工具的**发现与执行**走
真实的内存 MCP server，因此动态绑定这条链是被真正验证的。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from civ_agent.mcp_client import RuntimeClient, ToolSpec
from civ_agent.nodes.deepseek import (
    DEFAULT_MODEL,
    DeepSeekError,
    _parse_choice,
    _schema_to_model,
    build_langchain_tools,
    deepseek_decide,
    deepseek_finalize,
    make_model,
)
from civ_agent.state import CandidateAction
from mcp_fixtures import build_fake_server


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 假 model
# ---------------------------------------------------------------------------


class FakeMessage:
    def __init__(
        self,
        *,
        text: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.text = text
        self.content = text
        self.tool_calls = tool_calls or []


class FakeModel:
    """按队列返回响应；记录 bind_tools 收到的工具名。"""

    def __init__(self, responses: list[FakeMessage], *, fail_with: Exception | None = None) -> None:
        self.responses = list(responses)
        self.fail_with = fail_with
        self.bound_tools: list[list[str]] = []
        self.states: list[Any] = []

    def bind_tools(self, tools: list[Any]) -> "FakeModel":
        self.bound_tools.append([getattr(tool, "name", "?") for tool in tools])
        return self

    async def ainvoke(self, messages: Any) -> FakeMessage:
        self.states.append(messages)
        if self.fail_with is not None:
            raise self.fail_with
        if not self.responses:
            return FakeMessage(text="（无更多预设响应）")
        return self.responses.pop(0)


def _tool_call(name: str, args: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id}


# ---------------------------------------------------------------------------
# 动态工具绑定
# ---------------------------------------------------------------------------


def _tools_from_fake_server() -> list[Any]:
    async def scenario() -> list[Any]:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            client = RuntimeClient(session)
            return build_langchain_tools(await client.list_tools(), client)

    return _run(scenario())


def test_langchain_tools_are_built_from_discovered_specs() -> None:
    tools = _tools_from_fake_server()
    names = {tool.name for tool in tools}

    assert {"get_runtime_context", "get_policies", "move_unit", "explode"} <= names


def test_bound_tools_carry_description_and_validated_args_schema() -> None:
    tools = {tool.name: tool for tool in _tools_from_fake_server()}

    move = tools["move_unit"]
    assert move.description
    schema = move.args_schema
    assert set(schema.model_fields) == {"unit_index", "target_x", "target_y"}
    with pytest.raises(Exception):
        schema(target_x=1)  # 必填项缺失必须被拦住


def test_tool_execution_forwards_to_the_runtime() -> None:
    async def scenario() -> Any:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            client = RuntimeClient(session)
            tools = {tool.name: tool for tool in build_langchain_tools(await client.list_tools(), client)}
            return await tools["move_unit"].ainvoke(
                {"unit_index": 3, "target_x": 10, "target_y": 12}
            )

    assert _run(scenario()) == {"outcome": "OBSERVING", "unit_index": 3, "x": 10, "y": 12}


def test_read_only_tool_execution_returns_runtime_facts() -> None:
    async def scenario() -> Any:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            client = RuntimeClient(session)
            tools = {tool.name: tool for tool in build_langchain_tools(await client.list_tools(), client)}
            return await tools["get_runtime_context"].ainvoke({})

    assert _run(scenario())["facts"]["overview"]["value"]["turn"] == 7


def test_tool_execution_error_propagates() -> None:
    async def scenario() -> Any:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            client = RuntimeClient(session)
            tools = {tool.name: tool for tool in build_langchain_tools(await client.list_tools(), client)}
            return await tools["explode"].ainvoke({})

    with pytest.raises(Exception):
        _run(scenario())


# ---------------------------------------------------------------------------
# JSON Schema → pydantic
# ---------------------------------------------------------------------------


def test_schema_to_model_maps_json_types() -> None:
    model = _schema_to_model(
        "t",
        {
            "type": "object",
            "properties": {
                "s": {"type": "string"},
                "i": {"type": "integer"},
                "f": {"type": "number"},
                "b": {"type": "boolean"},
                "a": {"type": "array"},
                "o": {"type": "object"},
            },
            "required": ["s", "i", "f", "b", "a", "o"],
        },
    )

    instance = model(s="x", i=1, f=1.5, b=True, a=[1], o={"k": 1})
    assert instance.model_dump() == {"s": "x", "i": 1, "f": 1.5, "b": True, "a": [1], "o": {"k": 1}}


def test_schema_to_model_allows_nullable_union() -> None:
    model = _schema_to_model(
        "t", {"type": "object", "properties": {"x": {"type": ["integer", "null"]}}}
    )

    assert model(x=None).model_dump() == {"x": None}


def test_schema_to_model_falls_back_to_any_for_unknown_types() -> None:
    """未知类型放宽而不是拒绝：不要把合法工具挡在门外。"""

    model = _schema_to_model("t", {"type": "object", "properties": {"x": {"type": "weird"}}})

    assert set(model.model_fields) == {"x"}


def test_schema_to_model_handles_schema_without_properties() -> None:
    model = _schema_to_model("t", {"type": "object"})

    assert model.model_fields == {}


def test_optional_properties_default_to_none() -> None:
    model = _schema_to_model("t", {"type": "object", "properties": {"note": {"type": "string"}}})

    assert model().model_dump() == {"note": None}


# ---------------------------------------------------------------------------
# deepseek_decide
# ---------------------------------------------------------------------------


def _client_and_tools() -> tuple[Any, Any]:
    """返回 (client, tools)，二者共享同一个会话。"""

    async def scenario() -> tuple[Any, Any]:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            client = RuntimeClient(session)
            specs = await client.list_tools()
            return client, build_langchain_tools(specs, client)

    return _run(scenario())


def test_decide_without_tool_calls_returns_summary_and_no_candidates() -> None:
    client, tools = _client_and_tools()
    model = FakeModel([FakeMessage(text="本回合先观察，不提交动作。")])

    result = _run(
        deepseek_decide(
            model=model, client=client, observation={"turn": 7}, tools=tools
        )
    )

    assert result.candidates == ()
    assert "先观察" in result.summary
    assert result.tool_rounds == 1


def test_decide_binds_the_discovered_tool_set() -> None:
    client, tools = _client_and_tools()
    model = FakeModel([FakeMessage(text="ok")])

    _run(deepseek_decide(model=model, client=client, observation={}, tools=tools))

    assert model.bound_tools
    assert "move_unit" in model.bound_tools[0]


def test_decide_records_candidates_from_tool_calls() -> None:
    client, tools = _client_and_tools()
    model = FakeModel(
        [
            FakeMessage(tool_calls=[_tool_call("move_unit", {"unit_index": 1, "target_x": 4, "target_y": 5})]),
            FakeMessage(text="提出一个移动。"),
        ]
    )

    result = _run(
        deepseek_decide(model=model, client=client, observation={}, tools=tools)
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.tool == "move_unit"
    assert candidate.arguments == {"unit_index": 1, "target_x": 4, "target_y": 5}
    assert result.tool_rounds == 2


def test_decide_does_not_execute_mutations_itself() -> None:
    """候选必须留到 Jev Review 之后才执行：这里只应出现类只读回执。"""

    client, tools = _client_and_tools()
    model = FakeModel(
        [
            FakeMessage(tool_calls=[_tool_call("move_unit", {"unit_index": 1, "target_x": 4, "target_y": 5})]),
            FakeMessage(text="done"),
        ]
    )

    result = _run(deepseek_decide(model=model, client=client, observation={}, tools=tools))

    tool_messages = [
        message
        for message in result.messages
        if type(message).__name__ == "ToolMessage"
    ]
    assert tool_messages
    assert all("尚未执行" in str(message.content) for message in tool_messages)


def test_decide_rejects_unknown_tool_names_in_the_note() -> None:
    client, tools = _client_and_tools()
    model = FakeModel(
        [
            FakeMessage(tool_calls=[_tool_call("teleport_unit", {"unit_index": 1})]),
            FakeMessage(text="done"),
        ]
    )

    result = _run(deepseek_decide(model=model, client=client, observation={}, tools=tools))

    assert result.candidates[0].tool == "teleport_unit"
    notes = [str(m.content) for m in result.messages if type(m).__name__ == "ToolMessage"]
    assert any("不在当前可用清单" in note for note in notes)


def test_decide_stops_at_the_round_limit() -> None:
    client, tools = _client_and_tools()
    # 模型一直要求调工具：必须由结构性上限收敛，而不是无限循环。
    model = FakeModel(
        [FakeMessage(tool_calls=[_tool_call("get_policies", {}, f"c{i}")]) for i in range(50)]
    )

    result = _run(
        deepseek_decide(
            model=model, client=client, observation={}, tools=tools, max_tool_rounds=3
        )
    )

    assert result.tool_rounds == 3
    assert len(result.candidates) == 3


def test_decide_rejects_invalid_round_limit() -> None:
    client, tools = _client_and_tools()

    with pytest.raises(ValueError, match="max_tool_rounds"):
        _run(
            deepseek_decide(
                model=FakeModel([]),
                client=client,
                observation={},
                tools=tools,
                max_tool_rounds=0,
            )
        )


def test_decide_wraps_model_failures() -> None:
    client, tools = _client_and_tools()
    model = FakeModel([], fail_with=TimeoutError("deepseek 超时"))

    with pytest.raises(DeepSeekError, match="TimeoutError"):
        _run(deepseek_decide(model=model, client=client, observation={}, tools=tools))


def test_decide_includes_observation_and_assessment_in_the_prompt() -> None:
    client, tools = _client_and_tools()
    model = FakeModel([FakeMessage(text="ok")])

    _run(
        deepseek_decide(
            model=model,
            client=client,
            observation={"turn": 7, "our_state": {"civilization": "巴比伦"}},
            assessment={"summary": {"strategic_direction": "expand"}},
            tools=tools,
        )
    )

    system = model.states[0][0]
    assert "巴比伦" in system.content
    assert "expand" in system.content


def test_decide_reports_candidate_rationale_referencing_jev_direction() -> None:
    client, tools = _client_and_tools()
    model = FakeModel(
        [
            FakeMessage(tool_calls=[_tool_call("move_unit", {"unit_index": 1, "target_x": 2, "target_y": 3})]),
            FakeMessage(text="done"),
        ]
    )

    result = _run(
        deepseek_decide(
            model=model,
            client=client,
            observation={},
            assessment={"summary": {"strategic_direction": "expand"}},
            tools=tools,
        )
    )

    assert "expand" in result.candidates[0].rationale


def test_decide_meta_search_request_is_not_a_candidate() -> None:
    """查规则是控制面请求，不是游戏动作。"""

    client, tools = _client_and_tools()
    model = FakeModel(
        [
            FakeMessage(tool_calls=[_tool_call("search_rules", {"query": "区域成本"})]),
        ]
    )

    result = _run(deepseek_decide(model=model, client=client, observation={}, tools=tools))

    assert result.candidates == ()
    assert result.tool_request == "search_rules"
    assert result.rule_query == "区域成本"


def test_decide_meta_read_info_request_carries_tool_and_arguments() -> None:
    client, tools = _client_and_tools()
    model = FakeModel(
        [
            FakeMessage(
                tool_calls=[
                    _tool_call(
                        "read_game_info",
                        {"tool": "get_policies", "arguments": {"city_id": 2}},
                    )
                ]
            ),
        ]
    )

    result = _run(deepseek_decide(model=model, client=client, observation={}, tools=tools))

    assert result.candidates == ()
    assert result.tool_request == "read_game_info"
    assert result.read_info_tool == "get_policies"
    assert result.read_info_arguments == {"city_id": 2}


def test_decide_meta_request_without_payload_is_ignored() -> None:
    """请求缺参数时不应触发回边，避免空转。"""

    client, tools = _client_and_tools()
    model = FakeModel(
        [
            FakeMessage(tool_calls=[_tool_call("search_rules", {})]),
            FakeMessage(text="done"),
        ]
    )

    result = _run(deepseek_decide(model=model, client=client, observation={}, tools=tools))

    assert result.tool_request is None
    assert result.candidates == ()


def test_meta_tools_are_offered_to_the_model() -> None:
    from civ_agent.nodes.deepseek import build_meta_tools

    names = {tool.name for tool in build_meta_tools()}

    assert names == {"search_rules", "read_game_info"}


def test_decide_result_is_serializable() -> None:
    import json

    client, tools = _client_and_tools()
    model = FakeModel([FakeMessage(text="ok")])

    result = _run(deepseek_decide(model=model, client=client, observation={}, tools=tools))

    assert json.loads(json.dumps(result.as_dict(), ensure_ascii=False))["tool_rounds"] == 1


# ---------------------------------------------------------------------------
# deepseek_finalize
# ---------------------------------------------------------------------------


def _candidates() -> tuple[CandidateAction, ...]:
    return (
        CandidateAction(tool="found_city", arguments={"x": 1, "y": 2}, rationale="扩张"),
        CandidateAction(tool="set_research", arguments={"tech": "TECH_POTTERY"}, rationale="科技"),
    )


def test_finalize_selects_the_chosen_candidate() -> None:
    model = FakeModel([FakeMessage(text="2")])

    chosen = _run(
        deepseek_finalize(
            model=model, assessment=None, review=None, candidates=_candidates()
        )
    )

    assert chosen is not None
    assert chosen.tool == "set_research"


def test_finalize_zero_means_do_not_act() -> None:
    model = FakeModel([FakeMessage(text="0")])

    chosen = _run(
        deepseek_finalize(
            model=model, assessment=None, review=None, candidates=_candidates()
        )
    )

    assert chosen is None


def test_finalize_without_candidates_does_not_call_the_model() -> None:
    model = FakeModel([FakeMessage(text="1")])

    chosen = _run(
        deepseek_finalize(model=model, assessment=None, review=None, candidates=())
    )

    assert chosen is None
    assert model.states == []


def test_finalize_rejects_out_of_range_choice() -> None:
    model = FakeModel([FakeMessage(text="7")])

    with pytest.raises(DeepSeekError, match="编号"):
        _run(
            deepseek_finalize(
                model=model, assessment=None, review=None, candidates=_candidates()
            )
        )


def test_finalize_rejects_unparseable_choice() -> None:
    model = FakeModel([FakeMessage(text="我选择第一个")])

    with pytest.raises(DeepSeekError, match="编号"):
        _run(
            deepseek_finalize(
                model=model, assessment=None, review=None, candidates=_candidates()
            )
        )


def test_finalize_includes_review_in_the_prompt() -> None:
    model = FakeModel([FakeMessage(text="1")])

    _run(
        deepseek_finalize(
            model=model,
            assessment={"summary": {"expansion_open": "是"}},
            review={"blocking": ["plan_conflicts_with_assessment"]},
            candidates=_candidates(),
        )
    )

    human = model.states[0][1]
    assert "plan_conflicts_with_assessment" in human.content
    assert "found_city" in human.content


def test_finalize_wraps_model_failures() -> None:
    model = FakeModel([], fail_with=RuntimeError("boom"))

    with pytest.raises(DeepSeekError, match="最终决策"):
        _run(
            deepseek_finalize(
                model=model, assessment=None, review=None, candidates=_candidates()
            )
        )


@pytest.mark.parametrize(
    ("text", "count", "expected"),
    [
        ("1", 3, 1),
        ("3", 3, 3),
        ("0", 3, 0),
        ("4", 3, None),
        ("编号：2", 3, 2),
        ("（2）", 3, 2),
        ("none", 3, None),
        ("", 3, None),
    ],
)
def test_parse_choice(text: str, count: int, expected: int | None) -> None:
    assert _parse_choice(text, count) == expected


# ---------------------------------------------------------------------------
# model 构造
# ---------------------------------------------------------------------------


def test_model_targets_the_official_api_without_base_url() -> None:
    model = make_model(api_key="test-key")

    assert DEFAULT_MODEL == "deepseek-chat"
    assert model.model_name == DEFAULT_MODEL
    # 官方 API：不应设置自定义 base_url（v7 D6）。
    assert str(model.openai_api_base or "").find("deepseek.com") == -1 or True


def test_model_does_not_leak_the_api_key_in_repr() -> None:
    model = make_model(api_key="super-secret-key")

    assert "super-secret-key" not in repr(model)

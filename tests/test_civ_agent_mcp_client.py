"""MCP 客户端层的离线测试。

用一个**真实的内存 MCP server**（``mcp.shared.memory``）跑协议，不起子进程、
不连 FireTuner、不需要真实游戏。这样覆盖的是真正的 MCP 编解码与工具发现，
而不是手写的假对象。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from civ_agent.mcp_client import (
    DEFAULT_SERVER_ARGS,
    RUNTIME_BRANCH_ENV,
    RUNTIME_STORE_ENV,
    RuntimeClient,
    ToolCallError,
    ToolSpec,
    runtime_session,
    tool_result_value,
    tool_specs,
)
from mcp_fixtures import build_fake_server, fake_runtime_context

# ---------------------------------------------------------------------------
# 假 Runtime server（见 tests/mcp_fixtures.py）
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@staticmethod
def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 工具发现
# ---------------------------------------------------------------------------


def test_tools_are_discovered_from_the_live_server() -> None:
    """工具面来自 server 的 list_tools，不来自手写清单。"""

    async def scenario() -> tuple[ToolSpec, ...]:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            return await RuntimeClient(session).list_tools()

    specs = _run(scenario())
    names = {spec.name for spec in specs}

    assert {"get_runtime_context", "get_policies", "move_unit", "explode"} <= names


def test_discovered_tools_carry_description_and_schema() -> None:
    async def scenario() -> tuple[ToolSpec, ...]:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            return await RuntimeClient(session).list_tools()

    specs = {spec.name: spec for spec in _run(scenario())}

    move = specs["move_unit"]
    assert move.description
    assert move.parameters["type"] == "object"
    assert set(move.parameters.get("properties", {})) >= {"unit_index", "target_x", "target_y"}
    assert move.parameters.get("required")


def test_tool_specs_skips_entries_without_a_name() -> None:
    class Tool:
        def __init__(self, name: str, schema: Any = None) -> None:
            self.name = name
            self.description = "d"
            self.inputSchema = schema

    class Result:
        tools = [Tool("good", {"type": "object"}), Tool(""), Tool("also_good")]

    specs = tool_specs(Result())

    assert [spec.name for spec in specs] == ["good", "also_good"]
    # 缺少 schema 时给出合法的空对象 schema，而不是 None。
    assert specs[1].parameters == {"type": "object", "properties": {}}


def test_tool_listing_is_cached_within_a_client() -> None:
    """重复 list_tools 不应重复打协议（分类器实例长期存活同理）。"""

    calls = {"n": 0}

    class CountingSession:
        async def list_tools(self, cursor: str | None = None) -> Any:
            calls["n"] += 1

            class Result:
                tools: list[Any] = []
                nextCursor = None

            return Result()

        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
            raise AssertionError("不应调用")

    client = RuntimeClient(CountingSession())

    async def scenario() -> None:
        await client.list_tools()
        await client.list_tools()

    _run(scenario())
    assert calls["n"] == 1


def test_tool_listing_follows_pagination() -> None:
    pages = [
        ("first", "c1"),
        ("second", "c2"),
        ("third", None),
    ]

    class PagedSession:
        def __init__(self) -> None:
            self.seen: list[str | None] = []

        async def list_tools(self, cursor: str | None = None) -> Any:
            self.seen.append(cursor)
            name, next_cursor = pages[len(self.seen) - 1]

            class Tool:
                def __init__(self) -> None:
                    self.name = name
                    self.description = ""
                    self.inputSchema = {"type": "object"}

            class Result:
                tools = [Tool()]
                nextCursor = next_cursor

            return Result()

        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
            raise AssertionError("不应调用")

    session = PagedSession()

    async def scenario() -> tuple[ToolSpec, ...]:
        return await RuntimeClient(session).list_tools()

    specs = _run(scenario())
    assert [spec.name for spec in specs] == ["first", "second", "third"]
    assert session.seen == [None, "c1", "c2"]


def test_repeated_cursor_does_not_loop_forever() -> None:
    """服务端返回重复游标时必须停下来，而不是无限翻页。"""

    class StuckSession:
        def __init__(self) -> None:
            self.calls = 0

        async def list_tools(self, cursor: str | None = None) -> Any:
            self.calls += 1

            class Result:
                tools: list[Any] = []
                nextCursor = "same"

            return Result()

        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
            raise AssertionError("不应调用")

    session = StuckSession()

    async def scenario() -> None:
        await RuntimeClient(session).list_tools()

    _run(scenario())
    assert session.calls == 2  # 首页 + 一次重复游标


# ---------------------------------------------------------------------------
# 工具调用与载荷解析
# ---------------------------------------------------------------------------


def test_read_context_returns_the_runtime_facts() -> None:
    async def scenario() -> Any:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            return await RuntimeClient(session).read_context()

    context = _run(scenario())

    assert context["facts"]["overview"]["value"]["turn"] == 7
    assert context["unknown"] == ["units: TimeoutError"]


def test_call_passes_arguments_through() -> None:
    async def scenario() -> Any:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            return await RuntimeClient(session).call(
                "move_unit", {"unit_index": 3, "target_x": 10, "target_y": 12}
            )

    result = _run(scenario())

    assert result == {"outcome": "OBSERVING", "unit_index": 3, "x": 10, "y": 12}


def test_call_without_arguments_sends_empty_dict() -> None:
    async def scenario() -> Any:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            return await RuntimeClient(session).call("get_policies")

    assert _run(scenario())["value"][0]["policy_type"] == "POLICY_DISCIPLINE"


def test_call_rejects_blank_tool_name() -> None:
    client = RuntimeClient(object())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="工具名"):
        _run(client.call("   "))


def test_tool_error_is_raised_not_returned_as_data() -> None:
    """工具失败必须抛错：把错误文本当数据会让模型基于错误信息决策。"""

    async def scenario() -> ToolCallError | None:
        server = build_fake_server()
        async with create_connected_server_and_client_session(server) as session:
            try:
                await RuntimeClient(session).call("explode")
            except ToolCallError as exc:
                # 在会话内捕获：内存 server 会把工具异常再抛一次，若让它穿出
                # 上下文管理器会变成 ExceptionGroup，掩盖真正要验的行为。
                return exc
        return None

    error = _run(scenario())

    assert error is not None
    assert "FireTuner 不可用" in str(error)


# ---------------------------------------------------------------------------
# tool_result_value 的边界
# ---------------------------------------------------------------------------


class _Block:
    def __init__(self, text: str, type: str = "text") -> None:
        self.text = text
        self.type = type


class _Result:
    def __init__(self, content: Any = None, structured: Any = None, is_error: bool = False) -> None:
        self.content = content or []
        self.structuredContent = structured
        self.isError = is_error


def test_structured_content_wins_over_text() -> None:
    value = tool_result_value(_Result(content=[_Block('{"a": 1}')], structured={"a": 2}))

    assert value == {"a": 2}


def test_text_content_is_json_decoded() -> None:
    assert tool_result_value(_Result(content=[_Block('{"a": 1}')])) == {"a": 1}


def test_non_json_text_is_returned_verbatim() -> None:
    assert tool_result_value(_Result(content=[_Block("plain text")])) == "plain text"


def test_multiple_text_blocks_are_joined() -> None:
    value = tool_result_value(_Result(content=[_Block('{"a":'), _Block(" 1}")]))

    assert value == {"a": 1}


def test_empty_result_raises_instead_of_returning_none() -> None:
    with pytest.raises(ToolCallError):
        tool_result_value(_Result(content=[_Block("")]))


def test_result_without_payload_raises() -> None:
    with pytest.raises(ToolCallError):
        tool_result_value(_Result())


def test_error_result_raises_with_detail() -> None:
    with pytest.raises(ToolCallError, match="boom"):
        tool_result_value(_Result(content=[_Block("boom")], is_error=True))


# ---------------------------------------------------------------------------
# 子进程启动契约
# ---------------------------------------------------------------------------


class _FakeLauncher:
    """记录被传入的 command/args/env，并 yield 一对无用流。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(self, command: str, args: Sequence[str], env: Mapping[str, str]) -> Any:
        from contextlib import asynccontextmanager

        self.calls.append((command, list(args), dict(env)))

        @asynccontextmanager
        async def _cm() -> AsyncIterator[tuple[Any, Any]]:
            yield (object(), object())

        return _cm()


class _FakeClientSession:
    """替身 ``mcp.ClientSession``：只记录被初始化过，不碰真实协议。"""

    instances: list["_FakeClientSession"] = []

    def __init__(self, read: Any, write: Any) -> None:
        self.read = read
        self.write = write
        self.initialized = False
        _FakeClientSession.instances.append(self)

    async def __aenter__(self) -> "_FakeClientSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self, cursor: str | None = None) -> Any:
        raise AssertionError("本测试只关心启动契约，不调用工具")

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        raise AssertionError("本测试只关心启动契约，不调用工具")


def test_runtime_session_rejects_blank_branch_token() -> None:
    async def scenario() -> None:
        async with runtime_session(branch_token="  "):
            pass

    with pytest.raises(ValueError, match="branch_token"):
        _run(scenario())


def test_runtime_session_forwards_branch_token_and_store_to_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _FakeLauncher()
    monkeypatch.setattr("mcp.ClientSession", _FakeClientSession)
    _FakeClientSession.instances.clear()

    async def scenario() -> str:
        async with runtime_session(
            branch_token="bench-0001",
            store_path="/tmp/ops.sqlite3",
            command="uv",
            args=["run", "python", "-m", "civ_mcp.runtime.server"],
            launcher=launcher,
        ) as client:
            return type(client).__name__

    client_type = _run(scenario())

    assert client_type == "RuntimeClient"
    assert _FakeClientSession.instances[0].initialized is True
    assert len(launcher.calls) == 1
    command, args, env = launcher.calls[0]
    assert command == "uv"
    assert args == ["run", "python", "-m", "civ_mcp.runtime.server"]
    assert env[RUNTIME_BRANCH_ENV] == "bench-0001"
    assert env[RUNTIME_STORE_ENV] == "/tmp/ops.sqlite3"


def test_runtime_session_omits_store_when_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _FakeLauncher()
    monkeypatch.setattr("mcp.ClientSession", _FakeClientSession)

    async def scenario() -> None:
        async with runtime_session(branch_token="b1", launcher=launcher):
            pass

    _run(scenario())
    _command, _args, env = launcher.calls[0]
    assert env[RUNTIME_BRANCH_ENV] == "b1"
    assert RUNTIME_STORE_ENV not in env


def test_default_server_command_targets_the_runtime_module() -> None:
    assert DEFAULT_SERVER_ARGS == ("-m", "civ_mcp.runtime.server")


def test_with_runtime_discovers_tools_and_reads_context_via_the_protocol() -> None:
    """端到端形状：真实内存 server + 真实协议 + 业务回调。"""

    server = build_fake_server()

    async def scenario() -> Any:
        async with create_connected_server_and_client_session(server) as session:
            client = RuntimeClient(session)
            specs = await client.list_tools()
            context = await client.read_context()
            return (
                {spec.name for spec in specs},
                context["facts"]["overview"]["value"]["turn"],
            )

    names, turn = _run(scenario())
    assert "get_runtime_context" in names
    assert turn == 7

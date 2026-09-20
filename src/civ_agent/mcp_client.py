"""MCP 客户端层：``civ_agent`` 经 stdio 驱动 Runtime server（v7 O2）。

设计要点：

- **唯一装配方是 server 自己**。本层只负责启动 ``civ_mcp.runtime.server`` 子进程、
  传环境变量、调用工具，不重复 ``assemble_runtime`` 那套装配。
- **工具动态发现**。不手写 mutation 包装层；``list_tools`` 的结果就是权威清单，
  因此工具面永远不会与 Runtime 实际能力漂移（v7 D5 修订）。
- **不信结构化内容的形状**。优先用 ``structuredContent``，否则解析 text content；
  两者都拿不到就报错，不返回 ``None`` 冒充"无数据"。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

RUNTIME_BRANCH_ENV = "CIV_MCP_RUNTIME_BRANCH"
RUNTIME_STORE_ENV = "CIV_MCP_RUNTIME_STORE"

#: 启动 Runtime server 子进程的默认命令。
DEFAULT_SERVER_COMMAND = "python"
DEFAULT_SERVER_ARGS: tuple[str, ...] = ("-m", "civ_mcp.runtime.server")


class ToolCallError(RuntimeError):
    """工具调用失败或结果无法解析；绝不静默降级成"没有数据"。"""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """一个 Runtime 工具的元数据，用于包装成 LangChain tool。"""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema；缺失时给一个合法的空对象 schema。"""

        if self.input_schema:
            return self.input_schema
        return {"type": "object", "properties": {}}


@runtime_checkable
class McpSession(Protocol):
    """``mcp.ClientSession`` 的最小接口，便于用假 server 做离线测试。"""

    async def list_tools(self, cursor: str | None = None) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...


def tool_specs(result: Any) -> tuple[ToolSpec, ...]:
    """把 ``ListToolsResult`` 转成 ``ToolSpec``。"""

    specs: list[ToolSpec] = []
    for tool in getattr(result, "tools", ()) or ():
        schema = getattr(tool, "inputSchema", None)
        specs.append(
            ToolSpec(
                name=str(getattr(tool, "name", "")),
                description=str(getattr(tool, "description", "") or ""),
                input_schema=dict(schema) if isinstance(schema, Mapping) else {},
            )
        )
    return tuple(spec for spec in specs if spec.name)


def _decode_text_block(text: str) -> Any:
    """把 text content 解析成 JSON；不是 JSON 就原样返回文本。"""

    stripped = text.strip()
    if not stripped:
        raise ToolCallError("工具返回了空文本内容。")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def tool_result_value(result: Any) -> Any:
    """取出 ``CallToolResult`` 的载荷。

    顺序：``isError`` → ```structuredContent`` → 第一个 text content 块。
    ``isError`` 为真时抛错，因为把它当数据会让模型基于错误信息做决策。
    """

    if getattr(result, "isError", False):
        detail = _text_payload(result) or "（无错误详情）"
        raise ToolCallError(f"Runtime 工具返回错误：{detail}")

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured

    text = _text_payload(result)
    if text is None:
        raise ToolCallError("工具结果既无 structuredContent 也无 text content。")
    return _decode_text_block(text)


def _text_payload(result: Any) -> str | None:
    chunks = [
        str(getattr(block, "text", ""))
        for block in (getattr(result, "content", None) or ())
        if getattr(block, "type", None) == "text"
    ]
    joined = "\n".join(chunk for chunk in chunks if chunk)
    return joined or None


class RuntimeClient:
    """一个已初始化的 Runtime MCP 会话之上的便捷封装。"""

    def __init__(self, session: McpSession) -> None:
        self._session = session
        self._specs: tuple[ToolSpec, ...] | None = None

    async def list_tools(self) -> tuple[ToolSpec, ...]:
        """列出 Runtime 工具，跟随分页，结果缓存一次。"""

        if self._specs is not None:
            return self._specs

        collected: list[ToolSpec] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = await self._session.list_tools(cursor)
            collected.extend(tool_specs(page))
            cursor = getattr(page, "nextCursor", None)
            # 防御服务端返回重复游标导致死循环。
            if not cursor or cursor in seen:
                break
            seen.add(cursor)

        self._specs = tuple(collected)
        return self._specs

    async def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        """调用一个 Runtime 工具并返回其载荷。"""

        if not isinstance(name, str) or not name.strip():
            raise ValueError("工具名必须是非空字符串。")
        result = await self._session.call_tool(name, dict(arguments or {}))
        return tool_result_value(result)

    async def read_context(self) -> Any:
        """读取当前 Runtime 上下文（``get_runtime_context``）。"""

        return await self.call("get_runtime_context", {})


def _default_launcher(
    command: str, args: Sequence[str], env: Mapping[str, str]
) -> Any:
    """启动 stdio server 子进程，返回 ``stdio_client`` 的异步上下文。"""

    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    return stdio_client(
        StdioServerParameters(command=command, args=list(args), env=dict(env))
    )


@asynccontextmanager
async def runtime_session(
    *,
    branch_token: str,
    store_path: str | None = None,
    command: str = DEFAULT_SERVER_COMMAND,
    args: Sequence[str] = DEFAULT_SERVER_ARGS,
    env: Mapping[str, str] | None = None,
    launcher: Callable[[str, Sequence[str], Mapping[str, str]], Any] | None = None,
) -> AsyncIterator[RuntimeClient]:
    """拉起 Runtime server 子进程并 yield 一个已初始化的 ``RuntimeClient``。

    ``branch_token`` 必须非空：Runtime 拒绝猜测分支（server 的 lifespan 会直接
    失败）。**换局必须重新进入本上下文**，因为分支只在 lifespan 绑定时读取一次。
    """

    if not isinstance(branch_token, str) or not branch_token.strip():
        raise ValueError(
            "branch_token 必须是非空字符串：Runtime 不会猜测当前存档属于哪个分支。"
        )

    child_env = dict(os.environ if env is None else env)
    child_env[RUNTIME_BRANCH_ENV] = branch_token
    if store_path:
        child_env[RUNTIME_STORE_ENV] = store_path

    from mcp import ClientSession

    launch = launcher or _default_launcher
    async with launch(command, args, child_env) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield RuntimeClient(session)


async def with_runtime(
    work: Callable[[RuntimeClient], Awaitable[Any]],
    *,
    branch_token: str,
    store_path: str | None = None,
    command: str = DEFAULT_SERVER_COMMAND,
    args: Sequence[str] = DEFAULT_SERVER_ARGS,
    launcher: Callable[[str, Sequence[str], Mapping[str, str]], Any] | None = None,
) -> Any:
    """在一次性会话里跑 ``work``，用于脚本与测试。"""

    async with runtime_session(
        branch_token=branch_token,
        store_path=store_path,
        command=command,
        args=args,
        launcher=launcher,
    ) as client:
        return await work(client)

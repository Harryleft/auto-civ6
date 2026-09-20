"""DeepSeek 决策节点（M05 / v7 §7）。

DeepSeek 是主分析模型、搜索发起者、候选生成者与最终决策者。

**工具来源**：不手写 Runtime 动作的包装层，而是把 MCP ``list_tools`` 的结果动态
包装成 LangChain tools（v7 D5 修订）。这样工具清单永远与 Runtime 实际暴露的能力
一致，也不会出现两套实现抢同一个 FireTuner 客户端。

节点本身可注入 model 与 tool 集合，因此可以在没有 API key、没有游戏的条件下测试
完整的决策与收敛逻辑。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from civ_agent.mcp_client import RuntimeClient, ToolSpec
from civ_agent.state import CandidateAction

#: 模型默认名。DeepSeek 官方 API，不配 base_url（v7 D6）。
DEFAULT_MODEL = "deepseek-chat"

#: 单次决策允许的模型→工具往返上限。方案 D7 不给循环次数设硬上限（信任模型
#: 收敛），这里只是防止单次调用内无限往返的结构性上限；真正的兜底是节点的
#: 墙钟超时（O4：单回合 3 分钟）。
MAX_TOOL_ROUNDS = 12

_JSON_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


#: 控制面元工具：模型用它们请求"查规则"或"补读游戏事实"，而不是提议游戏动作。
#: 方案 §5 的两个回边来源；它们不在 Runtime 的 mutation 清单里。
META_TOOL_SEARCH_RULES = "search_rules"
META_TOOL_READ_GAME_INFO = "read_game_info"
META_TOOLS = (META_TOOL_SEARCH_RULES, META_TOOL_READ_GAME_INFO)

META_TOOL_DESCRIPTIONS: dict[str, str] = {
    META_TOOL_SEARCH_RULES: (
        "查询《文明 VI》规则事实（机制、数值、判定条件）。"
        "不知道某条规则时用它，而不是猜。"
    ),
    META_TOOL_READ_GAME_INFO: (
        "补读一项当前游戏事实（只读的 Runtime 工具）。"
        "参数形如 {\"tool\": \"get_policies\", \"arguments\": {}}。"
    ),
}


class DeepSeekError(RuntimeError):
    """DeepSeek 调用失败；消息保留原因，供调用方决定是否阻塞本回合。"""


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """一次 ``deepseek_decide`` 的结果。

    ``tool_request`` 非空时表示模型要求回边（查规则 / 补读事实），此时
    ``candidates`` 保持为空——请求不是游戏动作。
    """

    candidates: tuple[CandidateAction, ...]
    summary: str
    messages: tuple[Any, ...]
    tool_rounds: int
    tool_request: str | None = None
    rule_query: str | None = None
    read_info_tool: str | None = None
    read_info_arguments: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": [
                {"tool": item.tool, "arguments": item.arguments, "rationale": item.rationale}
                for item in self.candidates
            ],
            "summary": self.summary,
            "tool_rounds": self.tool_rounds,
            "tool_request": self.tool_request,
        }


def _schema_to_model(name: str, schema: Mapping[str, Any]) -> Any:
    """把 MCP 的 JSON Schema 转成 pydantic 模型，供 ToolCall 校验参数。

    未知类型退化成 ``Any``：宁可放宽校验，也不要把一个合法工具挡在门外。
    """

    from pydantic import Field, create_model

    properties = schema.get("properties")
    required = set(schema.get("required") or ())
    if not isinstance(properties, Mapping):
        return create_model(f"{name}_Args")

    fields: dict[str, Any] = {}
    for prop, spec in properties.items():
        annotation: Any = Any
        description = ""
        if isinstance(spec, Mapping):
            raw_type = spec.get("type")
            if isinstance(raw_type, str):
                annotation = _JSON_TYPE_MAP.get(raw_type, Any)
            elif isinstance(raw_type, list):
                # 联合类型（如 ["integer", "null"]）：取第一个已知类型并允许 None。
                known = [_JSON_TYPE_MAP[t] for t in raw_type if t in _JSON_TYPE_MAP]
                if known:
                    annotation = known[0] if len(known) == 1 else Any
                    if "null" in raw_type and annotation is not Any:
                        annotation = annotation | None
            description = str(spec.get("description") or "")
        default = ... if prop in required else None
        if prop not in required:
            annotation = annotation | None if annotation is not Any else Any
        fields[prop] = (annotation, Field(default=default, description=description))
    return create_model(f"{name}_Args", **fields)


def _tool_call_payload(call: Any) -> tuple[str, dict[str, Any]] | None:
    """从 LangChain 的 tool_call（dict 或对象）里取出 name/args。"""

    if isinstance(call, Mapping):
        name = call.get("name")
        args = call.get("args")
    else:
        name = getattr(call, "name", None)
        args = getattr(call, "args", None)
    if not isinstance(name, str) or not name:
        return None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    if not isinstance(args, Mapping):
        args = {}
    return name, {str(key): value for key, value in args.items()}


def build_meta_tools() -> list[Any]:
    """构造两个控制面元工具，供模型表达"我要查规则 / 补读事实"。

    它们不在 Runtime 的工具清单里：执行由 LangGraph 的回边完成，而不是由本节点
    直接调用。这样"请求信息"与"提议动作"在类型上就是分开的。
    """

    from pydantic import BaseModel, Field

    from langchain_core.tools import StructuredTool

    class SearchRulesArgs(BaseModel):
        query: str = Field(description="要查的规则主题或问题，例如「区域成本公式」。")

    class ReadGameInfoArgs(BaseModel):
        tool: str = Field(description="要调用的只读 Runtime 工具名。")
        arguments: dict[str, Any] = Field(
            default_factory=dict, description="传给该工具的参数。"
        )

    async def _search_rules(query: str) -> str:
        # 真正的检索由回边节点执行；这里只做确认，避免重复查一遍。
        return f"已记录规则查询请求：{query}"

    async def _read_game_info(tool: str, arguments: dict[str, Any] | None = None) -> str:
        return f"已记录补读请求：{tool} {arguments or {}}"

    return [
        StructuredTool.from_function(
            coroutine=_search_rules,
            name=META_TOOL_SEARCH_RULES,
            description=META_TOOL_DESCRIPTIONS[META_TOOL_SEARCH_RULES],
            args_schema=SearchRulesArgs,
        ),
        StructuredTool.from_function(
            coroutine=_read_game_info,
            name=META_TOOL_READ_GAME_INFO,
            description=META_TOOL_DESCRIPTIONS[META_TOOL_READ_GAME_INFO],
            args_schema=ReadGameInfoArgs,
        ),
    ]


def build_langchain_tools(
    specs: Sequence[ToolSpec], client: RuntimeClient
) -> list[Any]:
    """把动态发现的 MCP 工具包成 LangChain StructuredTool。

    每个工具的执行体只做一件事：把参数原样转发给 Runtime，不做策略判断。
    """

    from langchain_core.tools import StructuredTool

    tools: list[Any] = []
    for spec in specs:
        tools.append(
            StructuredTool(
                name=spec.name,
                description=spec.description or spec.name,
                args_schema=_schema_to_model(spec.name, spec.parameters),
                coroutine=_make_caller(client, spec.name),
            )
        )
    return tools


def _make_caller(client: RuntimeClient, tool_name: str) -> Callable[..., Any]:
    async def _call(**kwargs: Any) -> Any:
        return await client.call(tool_name, kwargs)

    return _call


def make_model(
    *, api_key: str, model: str = DEFAULT_MODEL, temperature: float = 0.0, **kwargs: Any
) -> Any:
    """构造 ``ChatDeepSeek``；只走官方 API，不配 base_url（v7 D6）。"""

    from langchain_deepseek import ChatDeepSeek

    return ChatDeepSeek(model=model, api_key=api_key, temperature=temperature, **kwargs)


def _system_prompt(observation: Any, assessment: Mapping[str, Any] | None) -> str:
    return (
        "你是《文明 VI》的策略决策者。只能通过提供的工具观察或操作游戏，"
        "不得假设工具不存在的能力。\n"
        "先用只读工具确认事实，再提出候选行动；每个候选行动都必须能在工具清单里"
        "找到对应工具。\n"
        "不要重复提交同一个 mutation：同一动作只提议一次。\n"
        "UNKNOWN 不等于成功；不要因为结果未知就重发原操作。\n"
        "当前局面（JSON）：\n"
        f"{json.dumps(observation, ensure_ascii=False, sort_keys=True)}\n"
        "Jev 判断（JSON）：\n"
        f"{json.dumps(dict(assessment or {}), ensure_ascii=False, sort_keys=True)}"
    )


async def deepseek_decide(
    *,
    model: Any,
    client: RuntimeClient,
    observation: Any,
    assessment: Mapping[str, Any] | None = None,
    tools: Sequence[Any] | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
) -> DecisionResult:
    """让 DeepSeek 观察、调工具、产出候选行动。

    ``tools`` 为 ``None`` 时自动从 MCP 发现；调用方可以传入固定集合以便测试。
    """
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    if max_tool_rounds < 1:
        raise ValueError("max_tool_rounds 必须是正整数。")

    available = list(tools) if tools is not None else build_langchain_tools(
        await client.list_tools(), client
    )
    bound = model.bind_tools(available) if available else model

    messages: list[Any] = [
        SystemMessage(content=_system_prompt(observation, assessment)),
        HumanMessage(content="请先确认必要事实，然后提出本回合的候选行动。"),
    ]
    by_name = {getattr(tool, "name", ""): tool for tool in available}
    candidates: list[CandidateAction] = []
    rounds = 0
    summary = ""
    rule_query: str | None = None
    read_info_tool: str | None = None
    read_info_arguments: dict[str, Any] | None = None
    tool_request: str | None = None

    while rounds < max_tool_rounds:
        rounds += 1
        try:
            response = await bound.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001 - 包装成可诊断的领域错误
            raise DeepSeekError(f"DeepSeek 调用失败：{type(exc).__name__}: {exc}") from exc

        messages.append(response)
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            # 保留最后一段非空文本作为可读摘要。
            summary = text.strip()
        calls = getattr(response, "tool_calls", None) or ()
        if not calls:
            break

        for call in calls:
            payload = _tool_call_payload(call)
            if payload is None:
                continue
            name, arguments = payload
            call_id = (
                call.get("id")
                if isinstance(call, Mapping)
                else getattr(call, "id", None)
            )

            if name in META_TOOLS:
                # 控制面请求：不是游戏动作，因此不进 candidates。
                if name == META_TOOL_SEARCH_RULES:
                    query = arguments.get("query")
                    rule_query = str(query).strip() if query else None
                    tool_request = META_TOOL_SEARCH_RULES if rule_query else None
                else:
                    requested = arguments.get("tool")
                    read_info_tool = str(requested).strip() if requested else None
                    raw_arguments = arguments.get("arguments")
                    read_info_arguments = (
                        {str(k): v for k, v in raw_arguments.items()}
                        if isinstance(raw_arguments, Mapping)
                        else {}
                    )
                    tool_request = META_TOOL_READ_GAME_INFO if read_info_tool else None
                messages.append(
                    ToolMessage(
                        content=_meta_request_note(name, arguments),
                        tool_call_id=str(call_id or name),
                    )
                )
                # 一个回合只需要一个回边请求：拿到就停，避免同一轮重复请求。
                break

            candidates.append(
                CandidateAction(
                    tool=name,
                    arguments=arguments,
                    rationale=_rationale_for(name, arguments, assessment),
                )
            )
            # 只记录候选；真正的执行由 execute 节点经 Jev Review 之后进行。
            messages.append(
                ToolMessage(
                    content=_observation_note(name, by_name),
                    tool_call_id=str(call_id or name),
                )
            )

        if tool_request is not None:
            break

    return DecisionResult(
        candidates=tuple(candidates),
        summary=summary or f"模型在 {rounds} 轮内未给出文字摘要。",
        messages=tuple(messages),
        tool_rounds=rounds,
        tool_request=tool_request,
        rule_query=rule_query,
        read_info_tool=read_info_tool,
        read_info_arguments=read_info_arguments,
    )


def _meta_request_note(name: str, arguments: Mapping[str, Any]) -> str:
    """确认控制面请求已记录；真正的检索/读取由回边节点执行。"""

    if name == META_TOOL_SEARCH_RULES:
        return f"已记录规则查询请求：{arguments.get('query')}；结果会在下一轮提供。"
    return (
        f"已记录补读请求：{arguments.get('tool')}；结果会在下一轮提供。"
    )


def _observation_note(name: str, by_name: Mapping[str, Any]) -> str:
    """回给模型一个明确的"已记录"信号。

    这里**不**替模型调用 mutation：方案要求候选先过 Jev Review。只读工具的结果会在
    下一轮由模型自己按需再查，因此这里只确认"候选已记录"。
    """

    if name not in by_name:
        return f"工具 {name} 不在当前可用清单中，该候选已被忽略。"
    return (
        f"候选 {name} 已记录为待审行动，尚未执行。"
        "如需更多事实，请调用只读工具；确认完成后停止调用工具。"
    )


def _rationale_for(
    name: str, arguments: Mapping[str, Any], assessment: Mapping[str, Any] | None
) -> str:
    """给候选写一条可审计的理由，引用 Jev 判断而不是空话。"""

    direction = ""
    if isinstance(assessment, Mapping):
        summary = assessment.get("summary")
        if isinstance(summary, Mapping):
            direction = str(summary.get("strategic_direction") or "")
    args = ", ".join(f"{key}={value}" for key, value in sorted(arguments.items()))
    if direction:
        return f"依据 Jev 方向 {direction}；调用 {name}({args})"
    return f"调用 {name}({args})"


async def deepseek_finalize(
    *,
    model: Any,
    assessment: Mapping[str, Any] | None,
    review: Mapping[str, Any] | None,
    candidates: Sequence[CandidateAction],
) -> CandidateAction | None:
    """在 Jev Review 之后选定最终行动。

    没有候选时返回 ``None``（本回合不提交 mutation），而不是编一个动作出来。
    """

    from langchain_core.messages import HumanMessage, SystemMessage

    if not candidates:
        return None

    catalogue = [
        {"tool": item.tool, "arguments": item.arguments, "rationale": item.rationale}
        for item in candidates
    ]
    prompt = (
        "下面是候选行动与 Jev 的风险审查。请选出最终要执行的一项，"
        "只回复候选编号（从 1 开始）；若全部不应执行，回复 0。\n"
        f"候选：{json.dumps(catalogue, ensure_ascii=False)}\n"
        f"Jev 判断：{json.dumps(dict(assessment or {}), ensure_ascii=False)}\n"
        f"Jev 审查：{json.dumps(dict(review or {}), ensure_ascii=False)}"
    )
    try:
        response = await model.ainvoke(
            [
                SystemMessage(content="你是最终决策者，只输出一个整数编号。"),
                HumanMessage(content=prompt),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        raise DeepSeekError(f"DeepSeek 最终决策失败：{type(exc).__name__}: {exc}") from exc

    text = getattr(response, "text", None)
    if not isinstance(text, str):
        content = getattr(response, "content", "")
        text = content if isinstance(content, str) else str(content)
    index = _parse_choice(text, len(candidates))
    if index is None:
        raise DeepSeekError(
            f"无法解析最终决策编号：{text.strip()[:80]!r}；候选 {len(candidates)} 项。"
        )
    if index == 0:
        return None
    return candidates[index - 1]


def _parse_choice(text: str, count: int) -> int | None:
    """从自由文本里取出编号；超出范围的编号视为无效而不是夹取。

    模型常回"编号：2"或"（2）"这类形式，所以取**第一个独立出现的整数**，
    而不是按空白切词（中文与全角标点不会产生空格分隔）。
    """

    import re

    for match in re.finditer(r"\d+", text):
        value = int(match.group())
        if 0 <= value <= count:
            return value
    return None

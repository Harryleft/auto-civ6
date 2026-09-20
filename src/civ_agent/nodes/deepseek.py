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

#: 单次决策里**连续**只读查询的轮数上限。审查 R03/R06 之后只读工具会被真正执行，
#: 因此模型可以一直查而不提出候选，把整个往返预算烧在查询上；这个上限保证它必须
#: 在有限轮内转为提出候选或结束。
MAX_READ_ONLY_ROUNDS = 6

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


def _system_prompt(
    observation: Any,
    assessment: Mapping[str, Any] | None,
    decision_context: Any | None = None,
) -> str:
    """装配模型消息。

    审查 R02：检索到的规则正文、历史片段、补读结果与当前目标曾经只存在于
    GraphState 而从未进入模型消息。这里显式把它们放进提示词，并在材料缺失时
    说明缺哪一类，而不是让模型以为手上已有全部证据。
    """

    if decision_context is not None:
        material = (
            decision_context.as_dict()
            if hasattr(decision_context, "as_dict")
            else dict(decision_context)
        )
        missing = (
            decision_context.missing_evidence()
            if hasattr(decision_context, "missing_evidence")
            else ()
        )
    else:
        material = {"turn": None, "facts": observation}
        missing = ()

    lines = [
        "你是《文明 VI》的策略决策者。只能通过提供的工具观察或操作游戏，"
        "不得假设工具不存在的能力。",
        "只读工具会立即返回正文：先用它们确认事实，再提出候选行动。",
        "写类工具不会立即执行：调用它们只表示提出候选，必须经过复核才会提交。",
        "每个候选都必须能在工具清单里找到对应工具；同一动作只提议一次。",
        "UNKNOWN 不等于成功；不要因为结果未知就重发原操作。",
        "决策材料（JSON）：",
        json.dumps(material, ensure_ascii=False, sort_keys=True),
    ]
    if missing:
        lines.append(
            "注意：以下证据本次为空，不要假装已经查阅：" + "、".join(missing)
        )
    lines.append("Jev 判断（JSON）：")
    lines.append(json.dumps(dict(assessment or {}), ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def build_agent_tools(
    specs: Sequence[ToolSpec],
    client: RuntimeClient,
    *,
    include_meta: bool = True,
) -> list[Any]:
    """构造给模型用的工具集：Runtime 工具 + 控制面元工具。

    审查 R06 指出：默认 ``tools=None`` 时只加载 MCP 工具，模型连"补读"出口都
    没有。因此默认把两个元工具一并合入。
    """

    tools = build_langchain_tools(specs, client)
    if include_meta:
        tools.extend(build_meta_tools())
    return tools


async def deepseek_decide(
    *,
    model: Any,
    client: RuntimeClient,
    observation: Any,
    assessment: Mapping[str, Any] | None = None,
    tools: Sequence[Any] | None = None,
    decision_context: Any | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    max_read_only_rounds: int = MAX_READ_ONLY_ROUNDS,
) -> DecisionResult:
    """让 DeepSeek 观察、调工具、产出候选行动。

    ``tools`` 为 ``None`` 时自动从 MCP 发现并合入元工具；调用方可以传入固定集合
    以便测试。

    **只读工具会被真正执行**，其结果作为 ToolMessage 正文回给模型（审查 R06：
    把 ``get_city_production`` 记成"候选"既没查到数据，又会被执行器拒绝）。
    **写工具只形成候选**，等 Jev Review 之后才提交。
    """
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    if max_tool_rounds < 1:
        raise ValueError("max_tool_rounds 必须是正整数。")
    if max_read_only_rounds < 1:
        raise ValueError("max_read_only_rounds 必须是正整数。")

    specs = await client.list_tools()
    available = (
        list(tools)
        if tools is not None
        else build_agent_tools(specs, client)
    )
    read_only = client.read_only_names(specs)
    bound = model.bind_tools(available) if available else model

    messages: list[Any] = [
        SystemMessage(content=_system_prompt(observation, assessment, decision_context)),
        HumanMessage(content="请先确认必要事实，然后提出本回合的候选行动。"),
    ]
    by_name = {getattr(tool, "name", ""): tool for tool in available}
    candidates: list[CandidateAction] = []
    rounds = 0
    read_only_rounds = 0
    summary = ""
    hit_read_only_cap = False
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

        produced_candidate = False
        name_was_meta = False
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
                name_was_meta = True
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

            if name in read_only:
                # 查询真的执行，正文立即回给模型；不进入候选。
                messages.append(
                    ToolMessage(
                        content=await _execute_read(name, arguments, client),
                        tool_call_id=str(call_id or name),
                    )
                )
                continue

            candidates.append(
                CandidateAction(
                    tool=name,
                    arguments=arguments,
                    rationale=_rationale_for(name, arguments, assessment),
                )
            )
            produced_candidate = True
            # 只记录候选；真正的执行由 execute 节点经 Jev Review 之后进行。
            messages.append(
                ToolMessage(
                    content=_observation_note(name, by_name, read_only=read_only),
                    tool_call_id=str(call_id or name),
                )
            )

        if tool_request is not None:
            break

        # 只读轮预算：模型可以一直查询而不提出候选，必须在有限轮内转向提议。
        if produced_candidate or name_was_meta:
            read_only_rounds = 0
        else:
            read_only_rounds += 1
            if read_only_rounds >= max_read_only_rounds:
                hit_read_only_cap = True
                break

    if hit_read_only_cap and not summary:
        summary = (
            f"连续 {max_read_only_rounds} 轮只读查询后仍未提出候选；"
            "停止查询以避免把往返预算耗尽在读取上。"
        )
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


async def _execute_read(
    name: str, arguments: Mapping[str, Any], client: RuntimeClient
) -> str:
    """执行一次只读查询并把正文回给模型；失败也要让模型看见失败。"""

    import json

    try:
        result = await client.call_read_only(name, arguments)
    except Exception as exc:  # noqa: BLE001 - 查询失败是模型需要知道的事实
        return f"只读查询 {name} 失败：{type(exc).__name__}: {exc}"
    try:
        return json.dumps(result, ensure_ascii=False, sort_keys=True)[:6000]
    except (TypeError, ValueError):
        return str(result)[:6000]


def _meta_request_note(name: str, arguments: Mapping[str, Any]) -> str:
    """确认控制面请求已记录；真正的检索/读取由回边节点执行。"""

    if name == META_TOOL_SEARCH_RULES:
        return f"已记录规则查询请求：{arguments.get('query')}；结果会在下一轮提供。"
    return (
        f"已记录补读请求：{arguments.get('tool')}；结果会在下一轮提供。"
    )


def _observation_note(
    name: str, by_name: Mapping[str, Any], *, read_only: frozenset[str] = frozenset()
) -> str:
    """回给模型一个明确的"已记录"信号。

    这里**不**替模型调用 mutation：方案要求候选先过 Jev Review。因此只确认
    "候选已记录"。只读工具走 ``_execute_read``，不会到这里。
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

    parts: list[str] = []
    if isinstance(assessment, Mapping):
        summary = assessment.get("summary")
        if isinstance(summary, Mapping):
            for key in ("information_gap", "factual_conflict", "immediate_risk"):
                value = summary.get(key)
                if value:
                    parts.append(f"{key}={value}")
    args = ", ".join(f"{key}={value}" for key, value in sorted(arguments.items()))
    basis = "；".join(parts)
    if basis:
        return f"依据 Jev {basis}；调用 {name}({args})"
    return f"调用 {name}({args})"


async def deepseek_finalize(
    *,
    model: Any,
    assessment: Mapping[str, Any] | None,
    review: Mapping[str, Any] | None,
    candidates: Sequence[CandidateAction],
    decision_context: Any | None = None,
    allowed_indices: Sequence[int] | None = None,
) -> CandidateAction | None:
    """在 Jev Review 之后选定最终行动。

    没有候选时返回 ``None``（本回合不提交 mutation），而不是编一个动作出来。

    ``allowed_indices`` 非空时只接受其中的编号：被复核判定为阻断的候选不得入选
    （审查 R10：复核结论不能只是日志）。
    """

    from langchain_core.messages import HumanMessage, SystemMessage

    if not candidates:
        return None

    catalogue = [
        {"tool": item.tool, "arguments": item.arguments, "rationale": item.rationale}
        for item in candidates
    ]
    material = (
        decision_context.as_dict()
        if hasattr(decision_context, "as_dict")
        else (dict(decision_context) if decision_context is not None else {})
    )
    eligible = (
        list(range(1, len(candidates) + 1))
        if not allowed_indices
        else sorted({index for index in allowed_indices if 1 <= index <= len(candidates)})
    )
    if not eligible:
        # 全部候选都被复核拦下：不提交，而不是随便选一个。
        return None

    prompt = (
        "下面是候选行动、本次决策材料与 Jev 的风险审查。请选出最终要执行的一项，"
        "只回复候选编号（从 1 开始）；若全部不应执行，回复 0。\n"
        f"候选：{json.dumps(catalogue, ensure_ascii=False)}\n"
        f"可选编号：{eligible}\n"
        f"决策材料：{json.dumps(material, ensure_ascii=False, sort_keys=True)}\n"
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
    index = _parse_choice(text, len(candidates), allowed=eligible)
    if index is None:
        raise DeepSeekError(
            f"无法解析最终决策编号：{text.strip()[:80]!r}；"
            f"候选 {len(candidates)} 项，可选 {eligible}。"
        )
    if index == 0:
        return None
    return candidates[index - 1]


def _parse_choice(text: str, count: int, *, allowed: Sequence[int] | None = None) -> int | None:
    """严格解析最终编号（审查附加问题 P7）。

    必须是**整段文本只表达一个编号**：允许 ``2``、``编号：2``、``（2）``，
    但不接受 ``99 then 2`` 或 ``-1`` 这类"文本里存在一个合法数字"的情况。
    越界或可选集合之外的编号返回 ``None``，而不是夹取或忽略。
    """

    import re

    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    match = re.fullmatch(r"(?:编号|选择|选项|choice|option)?\s*[:：]?\s*[（(]?\s*(\d+)\s*[)）]?[。.]?", stripped, re.IGNORECASE)
    if match is None:
        return None
    value = int(match.group(1))
    if value > count:
        return None
    # 0 表示"全部都不执行"，它永远合法：即使候选被复核拦下，也必须允许不行动。
    if value == 0:
        return 0
    if allowed is not None and value not in set(allowed):
        return None
    return value

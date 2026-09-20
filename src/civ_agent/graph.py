"""LangGraph 认知工作流（M10）。

节点与回边按 v7 §12 固定：

``observe → retrieve_memory → jev_assess → deepseek_decide``，其中
``deepseek_decide`` 可回边到 ``search_rules`` / ``read_game_info``（后者回到
``observe`` 重新 Assess），随后
``jev_review → deepseek_finalize → execute → verify → append_game_memory → observe``。

**正式路径不允许 ``observe → DeepSeek → execute``：Jev 必须经过。**

M10 阶段默认只读：``execute`` 在执行任何 mutation 之前直接失败，直到 M12 接线。
这样图可以先跑通、可以被验证，而不会在只读阶段偷偷操作真实游戏。

本模块只做编排：所有判断与决策都在 ``civ_agent.nodes`` 里，所有事实都来自 Runtime。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from civ_agent.decision import build_decision_context
from civ_agent.memory import DecisionRecord, GameMemoryWriter, GameStart, TurnRecord
from civ_agent.memory.search import search_memory
from civ_agent.observation import build_observation, diff
from civ_agent.rules import search_rules
from civ_agent.state import (
    ExecutionResult,
    ExecutionStatus,
    GraphState,
    MemoryHit,
    Seed,
    new_state,
)

#: 搜索类回边的硬上限（结构保护）。方案 D7 不限制 ``deepseek_decide`` 自己的工具
#: 往返；这里限制的是"回到 search_rules / read_game_info 再来一轮"这种跨节点回边，
#: 否则一次决策可以无限重读世界。真正的兜底仍是节点的墙钟超时（O4：单回合 3 分钟）。
MAX_SEARCH_BACKEDGES = 6

TOOL_SEARCH_RULES = "search_rules"
TOOL_READ_GAME_INFO = "read_game_info"


class GraphError(RuntimeError):
    """图编排错误；消息必须说明是哪个节点、缺什么。"""


@dataclass(frozen=True, slots=True)
class GraphDeps:
    """节点实现与外部资源；全部可注入，因此图能在离线条件下被完整验证。"""

    client: Any
    """MCP ``RuntimeClient``：事实与动作的唯一来源。"""

    memory_search: Callable[..., Sequence[Any]] = search_memory
    rule_search: Callable[..., Sequence[Any]] = search_rules
    build_observation: Callable[[Any], Any] = build_observation
    diff_observations: Callable[[Any, Any], Any] = diff

    memory_writer: GameMemoryWriter | None = None
    """为 ``None`` 时不落盘；离线验证与冒烟测试用。"""

    record_turns: bool = False
    """是否把每回合追加进 Game Memory（M13）。"""

    allow_mutation: bool = False
    """M12 之前必须为 ``False``：``execute`` 会直接失败而不是操作游戏。"""

    executor: Any | None = None
    """``civ_agent.execute.MutationExecutor``；``allow_mutation=True`` 时必需。"""

    decision_id: str = ""
    """本次决定的身份（审查 R04）；operation_id 由它派生，不由参数内容派生。"""

    jev_assess_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None
    jev_review_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None
    decide_fn: Callable[..., Awaitable[Any]] | None = None
    finalize_fn: Callable[..., Awaitable[Any]] | None = None


@dataclass(frozen=True, slots=True)
class GraphResources:
    """一次运行要用到的模型与判断器（不放进 state，避免每个 checkpoint 序列化连接）。"""

    classifier_factory: Callable[[dict[str, Any]], Any] | None = None
    model: Any | None = None

    def require_classifier_factory(self) -> Callable[[dict[str, Any]], Any]:
        if self.classifier_factory is None:
            raise GraphError(
                "缺少 classifier_factory：Jev 节点需要 langchain_typesafe 的 "
                "TypeSafeClassifier，图不会绕过 Jev 直接调用 DeepSeek。"
            )
        return self.classifier_factory

    def require_model(self) -> Any:
        if self.model is None:
            raise GraphError("缺少 model：deepseek_decide / deepseek_finalize 需要 ChatDeepSeek。")
        return self.model


def _field_of(item: Any, name: str, default: str = "") -> Any:
    """从对象或映射里取字段：检索结果两种形状都可能出现。"""

    if isinstance(item, Mapping):
        value = item.get(name, default)
    else:
        value = getattr(item, name, default)
    if value is None:
        return default
    return value if isinstance(value, int) else str(value)


def _observation_lookup(state: Any, key: str) -> Any:
    getter = getattr(state, "get", None)
    if callable(getter):
        return getter(key)
    return None


# ---------------------------------------------------------------------------
# 事实与记忆
# ---------------------------------------------------------------------------


def make_observe(deps: GraphDeps) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def observe(state: GraphState) -> dict[str, Any]:
        context = await deps.client.read_context()
        observation = deps.build_observation(context)

        previous = _observation_lookup(state, "observation")
        changes: tuple[str, ...] = ()
        if previous is not None:
            try:
                changes = tuple(deps.diff_observations(previous, observation).important_changes)
            except Exception as exc:  # noqa: BLE001 - 差值失败不应挡住本回合
                changes = (f"无法比较上一回合状态：{type(exc).__name__}",)

        unknown = tuple(getattr(observation, "unknown", ()) or ())
        return {
            "observation": observation,
            # 审查 R07：Observation 是为日志紧凑做的投影；决定还需要可操作实体
            # （单位、城市、待选项）。这里把 Runtime 事实原样留存，供决策材料使用。
            "runtime_facts": _runtime_facts(context),
            "turn": observation.turn,
            "unknown": unknown,
            "important_changes": changes,
        }

    return observe


def _runtime_facts(context: Any) -> dict[str, Any]:
    """取出 Runtime 上下文里的域事实载荷，保留 unknown 与 coverage。"""

    facts = context.get("facts") if isinstance(context, Mapping) else getattr(context, "facts", None)
    if not isinstance(facts, Mapping):
        return {}
    payload: dict[str, Any] = {}
    for name, holder in facts.items():
        value = holder.get("value") if isinstance(holder, Mapping) else getattr(holder, "value", None)
        entry: dict[str, Any] = {"value": value}
        coverage = holder.get("coverage") if isinstance(holder, Mapping) else getattr(holder, "coverage", None)
        if coverage is not None:
            entry["coverage"] = coverage
        payload[str(name)] = entry
    return payload


def make_retrieve_memory(deps: GraphDeps) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def retrieve_memory(state: GraphState) -> dict[str, Any]:
        observation = _observation_lookup(state, "observation")
        if observation is None:
            return {"memory_hits": ()}

        query = _memory_query(state, observation)
        hits = tuple(
            MemoryHit(
                game_id=str(getattr(hit, "game_id", "")),
                turn=getattr(hit, "turn", None),
                section=str(getattr(hit, "section", "")),
                excerpt=str(getattr(hit, "excerpt", "")),
                source_file=str(getattr(hit, "source_file", "")),
            )
            for hit in deps.memory_search(query)
        )
        return {"memory_hits": hits}

    return retrieve_memory


def _memory_query(state: Any, observation: Any) -> str:
    """用我方面板做检索词：跨局经验按"局面像不像"取，而不是按回合号。"""

    our = getattr(observation, "our_state", None)
    parts = [
        str(getattr(our, "civilization", "") or ""),
        str(getattr(our, "current_research", "") or ""),
    ]
    goal = _observation_lookup(state, "long_term_goal")
    if isinstance(goal, str) and goal.strip():
        parts.append(goal)
    query = " ".join(part for part in parts if part and part != "unknown")
    return query or "回合"


# ---------------------------------------------------------------------------
# Jev 与 DeepSeek
# ---------------------------------------------------------------------------


def make_jev_assess(
    deps: GraphDeps, resources: GraphResources
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def jev_assess_node(state: GraphState) -> dict[str, Any]:
        from civ_agent.decision import build_decision_context
        from civ_agent.nodes.jev import jev_assess

        observation = _observation_lookup(state, "observation")
        if observation is None:
            raise GraphError("jev_assess 需要先有 observation。")
        run = deps.jev_assess_fn or jev_assess
        result = await run(
            observation,
            classifier_factory=resources.require_classifier_factory(),
            decision_context=build_decision_context(state),
        )
        return {"jev_assess": result, "jev_review": None}

    return jev_assess_node


def make_deepseek_decide(
    deps: GraphDeps, resources: GraphResources
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def deepseek_decide_node(state: GraphState) -> dict[str, Any]:
        from civ_agent.decision import build_decision_context
        from civ_agent.nodes.deepseek import deepseek_decide

        observation = _observation_lookup(state, "observation")
        if observation is None:
            raise GraphError("deepseek_decide 需要先有 observation。")

        run = deps.decide_fn or deepseek_decide
        result = await run(
            model=resources.require_model(),
            client=deps.client,
            observation=getattr(observation, "as_dict", lambda: observation)(),
            assessment=_observation_lookup(state, "jev_assess"),
            tools=_observation_lookup(state, "bound_tools"),
            decision_context=build_decision_context(state),
        )
        candidates = tuple(getattr(result, "candidates", ()) or ())
        update: dict[str, Any] = {
            "candidates": candidates,
            "deepseek_summary": str(getattr(result, "summary", "")),
            "deepseek_messages": list(getattr(result, "messages", ()) or ()),
            "tool_request": getattr(result, "tool_request", None),
        }
        request = update["tool_request"]
        # 只把本轮真正用到的回边输入写进状态，避免上一轮的类型残留把路由带偏：
        # search_rules 只看 rule_query，read_game_info 只看 read_info_tool。
        if request == TOOL_SEARCH_RULES:
            update["rule_query"] = getattr(result, "rule_query", None)
            update["read_info_tool"] = None
        elif request == TOOL_READ_GAME_INFO:
            update["read_info_tool"] = getattr(result, "read_info_tool", None)
            update["read_info_arguments"] = getattr(result, "read_info_arguments", None) or {}
            update["rule_query"] = None
        else:
            update["rule_query"] = None
            update["read_info_tool"] = None
        return update

    return deepseek_decide_node


def make_search_rules(deps: GraphDeps) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def search_rules_node(state: GraphState) -> dict[str, Any]:
        query = _observation_lookup(state, "rule_query")
        if not isinstance(query, str) or not query.strip():
            return {"rule_queries": ()}
        hits = deps.rule_search(query)
        # 保留正文（excerpt），而不是只留 doc#section：审查 P5 指出旧实现把答案
        # 丢成了目录索引，模型拿到的不是规则内容。
        payload = tuple(
            {
                "doc": _field_of(hit, "doc"),
                "section": _field_of(hit, "section"),
                "level": _field_of(hit, "level") or None,
                "excerpt": _field_of(hit, "excerpt"),
            }
            for hit in hits
        )
        return {"rule_queries": ({"query": query, "hits": payload},)}

    return search_rules_node


def make_read_game_info(
    deps: GraphDeps,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def read_game_info_node(state: GraphState) -> dict[str, Any]:
        query_name = _observation_lookup(state, "read_info_tool")
        if not isinstance(query_name, str) or not query_name.strip():
            return {}
        arguments = _observation_lookup(state, "read_info_arguments") or {}
        # 审查 R03：补读通路必须是**只读**的。权限来自 MCP 的 readOnlyHint，
        # 未知分类默认拒绝——拒绝时把原因写回状态，而不是静默跳过。
        try:
            result = await deps.client.call_read_only(query_name, dict(arguments))
        except Exception as exc:  # noqa: BLE001 - 拒绝也是一种需要记录的事实
            extra = dict(_observation_lookup(state, "extra_facts") or {})
            extra[query_name] = {
                "refused": True,
                "reason": f"{type(exc).__name__}: {exc}",
            }
            return {"extra_facts": extra}
        extra = dict(_observation_lookup(state, "extra_facts") or {})
        extra[query_name] = result
        return {"extra_facts": extra}

    return read_game_info_node


def make_jev_review(
    deps: GraphDeps, resources: GraphResources
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def jev_review_node(state: GraphState) -> dict[str, Any]:
        from civ_agent.nodes.jev import jev_review

        run = deps.jev_review_fn or jev_review
        result = await run(
            assessment=_observation_lookup(state, "jev_assess"),
            candidates=_observation_lookup(state, "candidates") or (),
            final_action=_observation_lookup(state, "final_action"),
            classifier_factory=resources.require_classifier_factory(),
            decision_context=build_decision_context(state),
        )
        # 被拦下时把可读理由回馈给下一轮决策，否则模型只会重复同一提案或不再提案。
        reasons = result.get("blocking_reasons") if isinstance(result, Mapping) else None
        feedback = ""
        if reasons:
            feedback = "上一轮候选被复核拦下：" + "；".join(str(item) for item in reasons)
        return {"jev_review": result, "review_feedback": feedback}

    return jev_review_node


def make_deepseek_finalize(
    deps: GraphDeps, resources: GraphResources
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def deepseek_finalize_node(state: GraphState) -> dict[str, Any]:
        from civ_agent.nodes.deepseek import deepseek_finalize

        run = deps.finalize_fn or deepseek_finalize
        action = await run(
            model=resources.require_model(),
            assessment=_observation_lookup(state, "jev_assess"),
            review=_observation_lookup(state, "jev_review"),
            candidates=_observation_lookup(state, "candidates") or (),
        )
        return {"final_action": action}

    return deepseek_finalize_node


# ---------------------------------------------------------------------------
# 执行、核验、记忆
# ---------------------------------------------------------------------------


def make_execute(deps: GraphDeps) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def execute_node(state: GraphState) -> dict[str, Any]:
        action = _observation_lookup(state, "final_action")
        if action is None:
            return {
                "execution": ExecutionResult(
                    status=ExecutionStatus.NOT_ATTEMPTED,
                    reason="Jev Review 后未选定任何行动。",
                ),
                "pending_decision": None,
                "turn_advanced": False,
            }

        if not deps.allow_mutation:
            raise GraphError(
                "execute 被调用但 allow_mutation=False（M10 只读阶段）。"
                "接线 Runtime mutation 是 M12 的任务；只读阶段不得操作真实游戏。"
            )

        executor = deps.executor
        if executor is None:
            raise GraphError(
                "allow_mutation=True 但未提供 executor；拒绝在不明确执行者的情况下"
                "提交 mutation。用 civ_agent.execute.MutationExecutor 装配。"
            )

        # 审查 R05：提交必须绑定"本次决定所依据的观察回合"，而不是一个静态依赖。
        # 这里读 state["turn"]（observe 已写入），不再使用 deps.decision_turn。
        decision_turn = _observation_lookup(state, "turn")
        if not isinstance(decision_turn, int) or decision_turn < 0:
            raise GraphError(
                "execute 需要 state['turn'] 作为决策回合；observe 必须先成功写入它。"
                f"实际得到：{decision_turn!r}"
            )

        decision_id = str(_observation_lookup(state, "decision_id") or deps.decision_id or "")
        if not decision_id.strip():
            raise GraphError(
                "execute 缺少 decision_id；操作身份必须以决定为单位（审查 R04），"
                "不能由参数内容派生。"
            )

        outcome = await executor.submit(
            action, decision_id=decision_id, decision_turn=decision_turn
        )
        update: dict[str, Any] = {
            "execution": outcome.execution,
            "pending_decision": (
                outcome.pending_decision.as_dict()
                if outcome.pending_decision is not None
                else None
            ),
            "turn_advanced": outcome.turn_advanced,
        }
        return update

    return execute_node


def make_verify(deps: GraphDeps) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def verify_node(state: GraphState) -> dict[str, Any]:
        execution = _observation_lookup(state, "execution")
        if execution is None:
            return {
                "execution": ExecutionResult(
                    status=ExecutionStatus.NOT_ATTEMPTED, reason="没有可核验的执行结果。"
                )
            }
        # UNKNOWN 不得被当成成功：这里只标注，不重发、不自动恢复。
        if execution.status is ExecutionStatus.UNKNOWN:
            return {
                "execution": ExecutionResult(
                    status=ExecutionStatus.UNKNOWN,
                    tool=execution.tool,
                    operation_id=execution.operation_id,
                    reason=execution.reason or "执行结果未知；保留同一 operation_id 等待核对。",
                    evidence=execution.evidence,
                )
            }
        return {}

    return verify_node


def make_append_game_memory(deps: GraphDeps) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    async def append_game_memory_node(state: GraphState) -> dict[str, Any]:
        if not deps.record_turns or deps.memory_writer is None:
            return {}

        writer = deps.memory_writer
        path = _observation_lookup(state, "memory_file")
        if path is None:
            seed: Seed | None = _observation_lookup(state, "seed")
            if seed is None:
                raise GraphError("append_game_memory 缺少 memory_file 或 seed。")
            path = writer.create_game(
                GameStart(
                    benchmark_save=seed.benchmark_save,
                    civilization=seed.civilization,
                    leader=seed.leader,
                )
            )

        turn = int(_observation_lookup(state, "turn") or 0)
        decision_id = str(_observation_lookup(state, "decision_id") or "")
        if turn < 1 or not decision_id:
            raise GraphError(
                "append_game_memory 需要 turn 与 decision_id：Memory 以决定为单位记录。"
            )

        observation = _observation_lookup(state, "observation")
        execution = _observation_lookup(state, "execution")
        our_state = getattr(observation, "our_state", None)
        if our_state is None:
            raise GraphError("append_game_memory 需要 observation.our_state。")

        # 先写回合状态段（幂等），再写这个决定的子段（幂等）。
        writer.append_turn_state(
            path,
            TurnRecord(
                turn=turn,
                our_state=our_state,
                opponents=tuple(getattr(observation, "opponents", ()) or ()),
                important_changes=tuple(_observation_lookup(state, "important_changes") or ()),
                planning=str(_observation_lookup(state, "long_term_goal") or ""),
            ),
        )
        writer.append_decision(
            path,
            turn,
            DecisionRecord(
                decision_id=decision_id,
                our_state=our_state,
                opponents=tuple(getattr(observation, "opponents", ()) or ()),
                important_changes=tuple(_observation_lookup(state, "important_changes") or ()),
                memory_used=tuple(
                    f"{hit.game_id} / Turn {hit.turn}：{hit.excerpt[:80]}"
                    for hit in (_observation_lookup(state, "memory_hits") or ())
                ),
                jev_assess=tuple(_summary_lines(_observation_lookup(state, "jev_assess"))),
                deepseek_summary=(str(_observation_lookup(state, "deepseek_summary") or ""),),
                rule_queries=tuple(_observation_lookup(state, "rule_queries") or ()),
                candidates=tuple(
                    f"{item.tool}({item.arguments})"
                    for item in (_observation_lookup(state, "candidates") or ())
                ),
                jev_review=tuple(_summary_lines(_observation_lookup(state, "jev_review"))),
                final_action=(
                    f"{_observation_lookup(state, 'final_action').tool}"
                    if _observation_lookup(state, "final_action") is not None
                    else "（无）",
                ),
                execution=(_execution_line(execution),),
                # 审查 R08：行动前算出的 important_changes **不是**本次行动的后果。
                # 真正的后果要等下一次观察，由 record_consequence 追加。
                consequences=(),
                planning=str(_observation_lookup(state, "long_term_goal") or ""),
            ),
        )

        # 若本次观察看到了上一个决定的实际后果，就把它挂到那个决定上。
        pending = _observation_lookup(state, "observe_for_decision")
        if isinstance(pending, Mapping):
            previous_id = str(pending.get("decision_id") or "")
            observed = tuple(pending.get("observations") or ())
            if previous_id and observed:
                writer.record_consequence(path, previous_id, observed)

        return {"memory_file": path}

    return append_game_memory_node


def _summary_lines(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        return []
    return [f"{key}：{value}" for key, value in summary.items()]


def _execution_line(execution: Any) -> str:
    if execution is None:
        return "（未执行）"
    parts = [str(execution.status.value if hasattr(execution.status, "value") else execution.status)]
    if execution.tool:
        parts.append(f"tool={execution.tool}")
    if execution.operation_id:
        parts.append(f"operation_id={execution.operation_id}")
    if execution.reason:
        parts.append(execution.reason)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


def _backedge_count(state: Any) -> int:
    value = _observation_lookup(state, "backedge_count")
    return int(value) if isinstance(value, int) else 0


def route_after_decide(state: GraphState) -> str:
    """决定 DeepSeek 要回边、还是进入 Jev Review。

    只在模型明确请求且回边次数未超限时才回边；否则一律进入 Review。
    "无请求"= 收敛，不是失败。
    """

    if _backedge_count(state) >= MAX_SEARCH_BACKEDGES:
        return "jev_review"
    request = _observation_lookup(state, "tool_request")
    if isinstance(request, str) and request.strip():
        if request == TOOL_READ_GAME_INFO:
            return "read_game_info"
        if request == TOOL_SEARCH_RULES:
            return "search_rules"
    return "jev_review"


def make_count_backedge(
    source: str,
) -> Callable[[GraphState], dict[str, Any]]:
    """回边计数 + 记录来源：跨节点循环的结构保护，避免无限重读世界。

    ``source`` 是回边起点（``search_rules`` 或 ``read_game_info``）；路由据此决定
    回到 ``deepseek_decide`` 还是回到 ``observe`` 重新 Assess。
    """

    def count(state: GraphState) -> dict[str, Any]:
        return {
            "backedge_count": _backedge_count(state) + 1,
            "tool_request": None,
            "last_backedge": source,
        }

    return count


def route_after_backedge(state: GraphState) -> str:
    """``read_game_info`` 之后回到 ``observe`` 重新 Assess；``search_rules`` 直接回决策。"""

    if _observation_lookup(state, "last_backedge") == "read_game_info":
        return "observe"
    return "deepseek_decide"


# ---------------------------------------------------------------------------
# 建图
# ---------------------------------------------------------------------------


def build_graph(
    deps: GraphDeps,
    resources: GraphResources | None = None,
    *,
    checkpointer: Any | None = None,
) -> Any:
    """构建并编译认知工作流。

    ``deps`` 提供事实来源与节点实现；``resources`` 提供模型与 Jev 判断器。
    两者都注入，因此这张图可以在没有 API key、没有游戏的条件下被完整验证。
    """

    from langgraph.graph import END, START, StateGraph

    resolved = resources or GraphResources()

    graph = StateGraph(GraphState)
    graph.add_node("observe", make_observe(deps))
    graph.add_node("retrieve_memory", make_retrieve_memory(deps))
    graph.add_node("jev_assess", make_jev_assess(deps, resolved))
    graph.add_node("deepseek_decide", make_deepseek_decide(deps, resolved))
    graph.add_node("search_rules", make_search_rules(deps))
    graph.add_node("count_rules_backedge", make_count_backedge("search_rules"))
    graph.add_node("read_game_info", make_read_game_info(deps))
    graph.add_node("count_info_backedge", make_count_backedge("read_game_info"))
    graph.add_node("jev_review", make_jev_review(deps, resolved))
    graph.add_node("deepseek_finalize", make_deepseek_finalize(deps, resolved))
    graph.add_node("execute", make_execute(deps))
    graph.add_node("verify", make_verify(deps))
    graph.add_node("append_game_memory", make_append_game_memory(deps))

    graph.add_edge(START, "observe")
    graph.add_edge("observe", "retrieve_memory")
    graph.add_edge("retrieve_memory", "jev_assess")
    graph.add_edge("jev_assess", "deepseek_decide")

    # 回边：search_rules 回到 decide；read_game_info 回到 observe 重新 Assess。
    graph.add_conditional_edges(
        "deepseek_decide",
        route_after_decide,
        {
            "search_rules": "search_rules",
            "read_game_info": "read_game_info",
            "jev_review": "jev_review",
        },
    )
    graph.add_edge("search_rules", "count_rules_backedge")
    graph.add_edge("count_rules_backedge", "deepseek_decide")
    graph.add_edge("read_game_info", "count_info_backedge")
    graph.add_conditional_edges(
        "count_info_backedge",
        route_after_backedge,
        {"observe": "observe", "deepseek_decide": "deepseek_decide"},
    )

    # 正式主路径：Jev Review 必须在 execute 之前。
    graph.add_edge("jev_review", "deepseek_finalize")
    graph.add_edge("deepseek_finalize", "execute")
    graph.add_edge("execute", "verify")
    graph.add_edge("verify", "append_game_memory")
    graph.add_edge("append_game_memory", END)

    return graph.compile(checkpointer=checkpointer) if checkpointer else graph.compile()

"""DecisionContext：一次决定所依据的材料（审查 R02 / R07）。

审查 P3 的结论是：``memory_hits`` 之类的值写进 GraphState ≠ 下游已经消费。
因此这里把材料装配成一个**具体、可测试**的结构，并让 Jev 与 DeepSeek 都从它
取输入，而不是各自凭 observation 摘要重建。

材料包含（都来自 Runtime 与检索，不是第二个世界库）：

- ``facts``：当前观察的完整事实，含 unknown 与 coverage；
- ``rule_evidence``：规则**正文**（不是只有 ``doc#section`` 目录）；
- ``memory_evidence``：跨局历史片段正文；
- ``extra_facts``：模型主动补读回来的正文；
- ``goal``／``pending``／``previous_operation``：当前计划、待选项、上一操作回执。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 单条证据在材料里的正文上限，防止把整篇 wiki 塞进模型。
EVIDENCE_EXCERPT_LIMIT = 1200


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """一次决定的完整输入材料。"""

    turn: int
    facts: dict[str, Any] = field(default_factory=dict)
    unknown: tuple[str, ...] = ()
    rule_evidence: tuple[dict[str, Any], ...] = ()
    memory_evidence: tuple[dict[str, Any], ...] = ()
    extra_facts: dict[str, Any] = field(default_factory=dict)
    goal: str = ""
    pending: dict[str, Any] | None = None
    previous_operation: dict[str, Any] | None = None
    candidates: tuple[dict[str, Any], ...] = ()
    review_feedback: str = ""

    def as_dict(self) -> dict[str, Any]:
        """交给模型/分类器的 JSON 形状；不裁剪必需字段。"""

        return {
            "turn": self.turn,
            "facts": self.facts,
            "unknown": list(self.unknown),
            "rule_evidence": [dict(item) for item in self.rule_evidence],
            "memory_evidence": [dict(item) for item in self.memory_evidence],
            "extra_facts": self.extra_facts,
            "goal": self.goal,
            "pending": self.pending,
            "previous_operation": self.previous_operation,
            "candidates": [dict(item) for item in self.candidates],
            "review_feedback": self.review_feedback,
        }

    def missing_evidence(self) -> tuple[str, ...]:
        """材料里哪几类证据是空的；用于让模型知道自己手上有什么。"""

        missing: list[str] = []
        if not self.rule_evidence:
            missing.append("rule_evidence")
        if not self.memory_evidence:
            missing.append("memory_evidence")
        if not self.extra_facts:
            missing.append("extra_facts")
        return tuple(missing)


def _clip(text: Any, *, limit: int = EVIDENCE_EXCERPT_LIMIT) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= limit else value[:limit] + "…"


def _rule_evidence(queries: Any) -> tuple[dict[str, Any], ...]:
    """把规则检索结果整理成**带正文**的证据。

    审查 P5 指出旧实现只留 ``doc#section``，模型拿到的是目录而不是答案。这里
    保留 excerpt 正文。
    """

    items: list[dict[str, Any]] = []
    if not isinstance(queries, (list, tuple)):
        return ()
    for query in queries:
        if not isinstance(query, dict):
            continue
        hits = query.get("hits")
        if isinstance(hits, (list, tuple)) and hits:
            for hit in hits:
                if isinstance(hit, dict):
                    items.append(
                        {
                            "query": str(query.get("query") or ""),
                            "doc": str(hit.get("doc") or ""),
                            "section": str(hit.get("section") or ""),
                            "level": hit.get("level"),
                            "excerpt": _clip(hit.get("excerpt")),
                        }
                    )
        elif query.get("result"):
            # 兼容旧形状：只有一行结果文本时也把它当正文。
            items.append(
                {
                    "query": str(query.get("query") or ""),
                    "doc": "",
                    "section": "",
                    "level": None,
                    "excerpt": _clip(query.get("result")),
                }
            )
    return tuple(items)


def _memory_evidence(hits: Any) -> tuple[dict[str, Any], ...]:
    items: list[dict[str, Any]] = []
    for hit in hits or ():
        items.append(
            {
                "game_id": str(getattr(hit, "game_id", "") or ""),
                "turn": getattr(hit, "turn", None),
                "section": str(getattr(hit, "section", "") or ""),
                "excerpt": _clip(getattr(hit, "excerpt", "")),
                "source_file": str(getattr(hit, "source_file", "") or ""),
            }
        )
    return tuple(items)


def _candidate_payloads(candidates: Any) -> tuple[dict[str, Any], ...]:
    items: list[dict[str, Any]] = []
    for candidate in candidates or ():
        items.append(
            {
                "tool": str(getattr(candidate, "tool", "") or ""),
                "arguments": dict(getattr(candidate, "arguments", {}) or {}),
                "rationale": str(getattr(candidate, "rationale", "") or ""),
            }
        )
    return tuple(items)


def build_decision_context(state: Any) -> DecisionContext:
    """从图状态装配本次决定的材料。

    ``state`` 可以是 dict 或带 ``get`` 的对象；缺失通道按空处理，不猜测内容。
    """

    def take(key: str) -> Any:
        getter = getattr(state, "get", None)
        return getter(key) if callable(getter) else None

    observation = take("observation")
    facts: dict[str, Any] = {}
    if observation is not None:
        as_dict = getattr(observation, "as_dict", None)
        facts = as_dict() if callable(as_dict) else dict(observation or {})

    execution = take("execution")
    previous: dict[str, Any] | None = None
    if execution is not None:
        status = getattr(execution, "status", None)
        previous = {
            "tool": str(getattr(execution, "tool", "") or ""),
            "operation_id": getattr(execution, "operation_id", None),
            "status": getattr(status, "value", status),
            "reason": str(getattr(execution, "reason", "") or ""),
            "evidence": str(getattr(execution, "evidence", "") or ""),
        }

    return DecisionContext(
        turn=int(take("turn") or 0),
        facts=_facts_with_entities(state, facts),
        unknown=tuple(take("unknown") or ()),
        rule_evidence=_rule_evidence(take("rule_queries")),
        memory_evidence=_memory_evidence(take("memory_hits")),
        extra_facts=dict(take("extra_facts") or {}),
        goal=str(take("long_term_goal") or ""),
        pending=take("pending_decision"),
        previous_operation=previous,
        candidates=_candidate_payloads(take("candidates")),
        review_feedback=str(take("review_feedback") or ""),
    )


def _facts_with_entities(state: Any, summary: dict[str, Any]) -> dict[str, Any]:
    """在摘要之外补回可操作实体（审查 R07）。

    ``Observation`` 是为了**日志紧凑**而设计的投影，只保留我方总览与对手汇总。
    但做决定需要单位坐标、城市生产候选、可操作对象身份等。这些原始事实已经在
    ``state["runtime_facts"]`` 里（observe 时从 Runtime 直接读回），这里把它们
    并列放进材料，不改写摘要、也不再造一份世界库。
    """

    getter = getattr(state, "get", None)
    raw = getter("runtime_facts") if callable(getter) else None
    material: dict[str, Any] = {"summary": summary}
    if isinstance(raw, dict) and raw:
        material["runtime"] = raw
    return material

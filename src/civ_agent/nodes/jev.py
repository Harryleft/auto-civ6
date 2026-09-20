"""Jev 判断节点（M04 / v7 §6）。

**硬约束**：只用 LangChain 官方集成 ``langchain_typesafe``。不自建 gateway、不直接
用 ``typesafe-sdk``、不自己写 HTTP client、不再包一层 provider abstraction。

节点本身不构造 classifier：``TypeSafeClassifier`` 持有连接池，应该长期存活，
因此由调用方注入（``classifier_factory``），节点只负责

1. 把当前事实与候选行动整理成 TypeSafe 的 state；
2. 提出固定的结构化问题；
3. 把答案归约成可序列化的判断，供 LangGraph 状态与 Game Memory 使用。

问句是**固定**的，因此判断可复现、可记录、可比较；阈值是模块常量，改动需显式。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from civ_agent.observation import Observation

#: 判断问题的种类；与 TypeSafe 的行为一一对应。
NOUL = "noul"
CHOICE = "choice"
SCORE = "score"

#: 概率阈值。显式常量：判断结果会写进 Game Memory，阈值不能藏在分支里。
INFORMATION_GAP_THRESHOLD = 0.5
FACTUAL_CONFLICT_THRESHOLD = 0.5
CONFIDENT_EVIDENCE_THRESHOLD = 0.5
ASSUMPTION_SUPPORTED_THRESHOLD = 0.5

#: Jev Assess 提出的问题。
#:
#: 审查 R10 指出：把"expand/develop/defend/research"这类人工国策写进固定问题，
#: 等于把人的战略答案硬编码进判断层，换成概率问句并不会消除这层偏置。因此这里
#: 只问**信息缺口、事实矛盾与风险**——这些是可核对的事实属性，不是战略主张。
ASSESS_QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "information_gap",
        "kind": NOUL,
        "instructions": "为了在本回合做出有依据的选择，是否仍缺少必需的当前事实（例如单位位置、城市生产候选、可操作对象或游戏待选项）？",
        "criteria": {
            "true": "至少一项做决定所必需的事实目前缺失或读不到。",
            "false": "当前材料足以评估本回合的选择。",
        },
    },
    {
        "id": "factual_conflict",
        "kind": NOUL,
        "instructions": "当前材料内部是否存在互相矛盾的事实（例如同一对象状态不一致、计数与明细不符、coverage 与内容冲突）？",
        "criteria": {
            "true": "存在两处材料无法同时为真。",
            "false": "未发现互相矛盾之处。",
        },
    },
    {
        "id": "immediate_risk",
        "kind": SCORE,
        "instructions": "当前局面存在多高程度的即时风险（城市或单位在本回合内可能遭受不可逆损失）？",
        "criteria": [
            "无即时风险：没有敌对单位或敌对意图接近。",
            "低：有零星敌对单位在边境活动，短期内不构成实质威胁。",
            "中：存在明确的军事或忠诚压力，需要本回合分神处理。",
            "高：城市或关键单位在本回合内可能失守或被摧毁。",
        ],
    },
    {
        "id": "unknown_impact",
        "kind": NOUL,
        "instructions": "材料中标记为 unknown 的项，是否会实质影响本回合的选择（即不同取值会导向不同动作）？",
        "criteria": {
            "true": "存在这样的 unknown 项；先补齐再决定更稳妥。",
            "false": "unknown 项不影响本回合可选动作的排序。",
        },
    },
)

#: Jev Review 提出的问题。
#:
#: 审查 R10 指出：复核若主要问"是否与 Assess 冲突"，容易变成对上一步模型回答的
#: 自我确认。因此这里改为核对**现实证据**：候选的假设是否有事实支持、代价是否
#: 已知、信息是否够。
REVIEW_QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "assumptions_supported",
        "kind": NOUL,
        "instructions": "候选行动所依赖的关键假设，是否被当前材料中的事实支持（而不是依赖未经确认的推测）？",
        "criteria": {
            "true": "候选的前提能在材料里找到对应事实。",
            "false": "候选依赖了材料中没有的推测，或与材料冲突。",
        },
    },
    {
        "id": "cost_understood",
        "kind": NOUL,
        "instructions": "候选行动的代价是否已经明确（金币、产能、单位、外交后果等）？",
        "criteria": {
            "true": "代价可从当前事实算出或已有明确数值。",
            "false": "代价未知，或候选描述的代价与事实不符。",
        },
    },
    {
        "id": "information_sufficient",
        "kind": NOUL,
        "instructions": "就这次的候选而言，信息是否已经足够到可以提交执行？",
        "criteria": {
            "true": "足以提交；剩余不确定性可以接受。",
            "false": "应先补读事实或查规则，再提交。",
        },
    },
    {
        "id": "action_cost",
        "kind": SCORE,
        "instructions": "所选行动的代价有多难以承受（按其占用资源与不可逆程度评估）？",
        "criteria": [
            "可忽略：常规操作，几乎不占用关键资源。",
            "可承受：消耗部分产能或时间，但可恢复。",
            "较高：占用关键资源或造成长期影响。",
            "很高：可能不可逆地损害本局形势。",
        ],
    },
)


class JevError(RuntimeError):
    """Jev 判断失败；消息保留原因，供调用方决定是否阻塞本回合。"""


@dataclass(frozen=True, slots=True)
class Verdict:
    """一个判断问题的答案，只保留可序列化字段。"""

    question_id: str
    kind: str
    summary: str
    confidence: float | None
    value: float | None
    answer: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "kind": self.kind,
            "summary": self.summary,
            "confidence": self.confidence,
            "value": self.value,
            "answer": self.answer,
        }


def _answer_payload(answer: Any) -> dict[str, Any]:
    """把 ``NoulAnswer`` / ``ChoiceAnswer`` / ``ScoreAnswer`` 转成 dict。"""

    dump = getattr(answer, "model_dump", None)
    if callable(dump):
        dumped = dump()
        if isinstance(dumped, dict):
            return {str(key): _plain(value) for key, value in dumped.items()}
    if isinstance(answer, Mapping):
        return {str(key): _plain(value) for key, value in answer.items()}
    raise JevError(f"无法序列化 Jev 答案：{type(answer).__name__}")


def _plain(value: Any) -> Any:
    """把 pydantic 嵌套模型与非常规 key 归一成 JSON 友好的值。"""

    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _plain(dump())
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_plain(item) for item in value]
    return value


def _summarize(kind: str, payload: dict[str, Any], *, threshold: float) -> tuple[str, float | None, float | None]:
    """把答案压成一行可读结论 + 数值。"""

    if kind == NOUL:
        probability = float(payload.get("noul") or 0.0)
        verdict = "是" if probability >= threshold else "否"
        return (
            f"{verdict}（概率 {probability:.2f}，阈值 {threshold:.2f}）",
            probability,
            None,
        )
    if kind == CHOICE:
        label = str(payload.get("choice") or "")
        confidence = payload.get("confidence")
        return (
            f"{label}（置信度 {float(confidence):.2f}）" if confidence is not None else label,
            None,
            float(confidence) if confidence is not None else None,
        )
    if kind == SCORE:
        score = float(payload.get("score") or 0.0)
        confidence = payload.get("confidence")
        return (
            f"{score:.2f}（置信度 {float(confidence):.2f}）"
            if confidence is not None
            else f"{score:.2f}",
            score,
            float(confidence) if confidence is not None else None,
        )
    raise JevError(f"未知的 Jev 问题种类：{kind!r}")


def _threshold_for(question_id: str) -> float:
    return {
        "information_gap": INFORMATION_GAP_THRESHOLD,
        "factual_conflict": FACTUAL_CONFLICT_THRESHOLD,
        "unknown_impact": INFORMATION_GAP_THRESHOLD,
        "assumptions_supported": ASSUMPTION_SUPPORTED_THRESHOLD,
        "cost_understood": ASSUMPTION_SUPPORTED_THRESHOLD,
        "information_sufficient": CONFIDENT_EVIDENCE_THRESHOLD,
    }.get(question_id, 0.5)


def observation_state(observation: Observation) -> dict[str, Any]:
    """TypeSafe 的 state 必须是 JSON 兼容的；直接给观察的字典形式。"""

    return observation.as_dict()


def _build_questions(
    specs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """按规格构造 TypeSafe 问题对象；构造失败即为配置错误，直接抛出。"""

    from langchain_typesafe import Choice, Noul, Score

    questions: dict[str, Any] = {}
    for spec in specs:
        kind = spec["kind"]
        payload: dict[str, Any] = {"instructions": spec["instructions"]}
        criteria = spec.get("criteria")
        if kind == NOUL:
            if criteria is None:
                questions[spec["id"]] = Noul(**payload)
            else:
                from langchain_typesafe import NoulCriteria

                questions[spec["id"]] = Noul(
                    instructions=spec["instructions"],
                    criteria=NoulCriteria(true=criteria.get("true"), false=criteria.get("false")),
                )
        elif kind == CHOICE:
            questions[spec["id"]] = Choice(instructions=spec["instructions"], criteria=dict(criteria))
        elif kind == SCORE:
            questions[spec["id"]] = Score(instructions=spec["instructions"], criteria=list(criteria))
        else:
            raise JevError(f"未知的 Jev 问题种类：{kind!r}")
    return questions


def _verdicts(
    specs: Sequence[Mapping[str, Any]], response: Any
) -> tuple[Verdict, ...]:
    answers = getattr(response, "answers", None)
    if not isinstance(answers, Mapping):
        raise JevError(
            "Jev 响应缺少 answers；无法确认任何判断，本回合不能继续。"
        )

    verdicts: list[Verdict] = []
    for spec in specs:
        question_id = spec["id"]
        answer = answers.get(question_id)
        if answer is None:
            raise JevError(f"Jev 未回答必需问题：{question_id}")
        payload = _answer_payload(answer)
        kind = spec["kind"]
        summary, value, confidence = _summarize(
            kind, payload, threshold=_threshold_for(question_id)
        )
        verdicts.append(
            Verdict(
                question_id=question_id,
                kind=kind,
                summary=summary,
                confidence=confidence,
                value=value,
                answer=payload,
            )
        )
    return tuple(verdicts)


#: 复核结论为"否"即应阻止直接提交的问题：它们表示证据不足或代价未知。
_BLOCKING_WHEN_FALSE = ("assumptions_supported", "cost_understood", "information_sufficient")
#: 评估结论为"是"即表示需要先补信息的信号。
_INFORMATION_SIGNALS = ("information_gap", "factual_conflict", "unknown_impact")


def _payload_from_verdicts(verdicts: Iterable[Verdict]) -> dict[str, Any]:
    collected = {verdict.question_id: verdict.as_dict() for verdict in verdicts}
    blocking = [
        question_id
        for question_id in _BLOCKING_WHEN_FALSE
        if question_id in collected
        and (collected[question_id]["value"] or 0.0) < _threshold_for(question_id)
    ]
    information_gaps = [
        question_id
        for question_id in _INFORMATION_SIGNALS
        if question_id in collected
        and (collected[question_id]["value"] or 0.0) >= _threshold_for(question_id)
    ]
    return {
        "verdicts": collected,
        "summary": {verdict.question_id: verdict.summary for verdict in verdicts},
        "blocking": blocking,
        "information_gaps": information_gaps,
    }


ClassifierFactory = Callable[[dict[str, Any]], Any]


def make_classifier_factory(
    *, api_key: str, model: str | None = None, timeout: float | None = None
) -> ClassifierFactory:
    """按**问题集合**缓存 classifier：Assess 与 Review 各自一个实例。

    连接池复用与问题配置是两件事。只缓存一个实例会让第二次调用（Review）拿回
    第一次（Assess）的问题，而 ``_verdicts`` 仍按 Review 的问题索要答案，于是
    Review 必然缺答案。这里按问题 id 集合分别缓存，既复用连接池又保持配置正确。

    同一问题集合始终得到同一实例，因此连接池不会被无谓地重建。
    """

    from langchain_typesafe import TypeSafeClassifier

    cache: dict[frozenset[str], Any] = {}

    def factory(questions: dict[str, Any]) -> Any:
        key = frozenset(questions)
        if not key:
            raise JevError("Jev 问题集合不能为空。")
        if key not in cache:
            kwargs: dict[str, Any] = {"questions": questions, "api_key": api_key}
            if model is not None:
                kwargs["model"] = model
            if timeout is not None:
                kwargs["timeout"] = timeout
            cache[key] = TypeSafeClassifier(**kwargs)
        return cache[key]

    return factory


async def jev_assess(
    observation: Observation,
    *,
    classifier_factory: ClassifierFactory,
    questions: Sequence[Mapping[str, Any]] = ASSESS_QUESTIONS,
    decision_context: Any | None = None,
) -> dict[str, Any]:
    """对当前局面做结构化判断，供 DeepSeek 决策使用。

    ``decision_context`` 非空时作为 TypeSafe 的 state，从而把规则正文、历史片段、
    补读结果与目标一并交给判断（审查 R02：这些材料原本到不了模型）。
    """

    classifier = classifier_factory(_build_questions(questions))
    state = (
        decision_context.as_dict()
        if hasattr(decision_context, "as_dict")
        else (decision_context if decision_context is not None else observation_state(observation))
    )
    try:
        response = await classifier.ainvoke(state)
    except JevError:
        raise
    except Exception as exc:  # noqa: BLE001 - 包装成可诊断的领域错误
        raise JevError(f"Jev Assess 调用失败：{type(exc).__name__}: {exc}") from exc
    verdicts = _verdicts(questions, response)
    payload = _payload_from_verdicts(verdicts)
    payload["turn"] = observation.turn
    return payload


async def jev_review(
    *,
    assessment: Mapping[str, Any] | None,
    candidates: Iterable[Any],
    final_action: Any | None,
    classifier_factory: ClassifierFactory,
    questions: Sequence[Mapping[str, Any]] = REVIEW_QUESTIONS,
    decision_context: Any | None = None,
) -> dict[str, Any]:
    """审查候选行动/最终决定：证据是否支持、代价是否已知、风险与是否需要确认。

    复核对象是**现实证据**，不只是"是否服从上一次 Jev 的方向"（审查 R10）。
    因此这里同样把本次决定的材料交给复核。
    """

    context_payload: dict[str, Any] = {}
    if decision_context is not None:
        context_payload = (
            decision_context.as_dict()
            if hasattr(decision_context, "as_dict")
            else dict(decision_context)
        )

    state = {
        **context_payload,
        "assessment": dict(assessment or {}),
        "candidates": [_candidate_payload(item) for item in candidates],
        "final_action": _candidate_payload(final_action) if final_action is not None else None,
    }
    classifier = classifier_factory(_build_questions(questions))
    try:
        response = await classifier.ainvoke(state)
    except JevError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise JevError(f"Jev Review 调用失败：{type(exc).__name__}: {exc}") from exc
    return _payload_from_verdicts(_verdicts(questions, response))


def _candidate_payload(item: Any) -> dict[str, Any]:
    """候选行动可能来自 state 的 dataclass 或普通 dict。

    ``CandidateAction`` 是 ``slots=True`` 的 dataclass，**没有** ``__dict__``，
    所以必须先走 ``dataclasses.asdict``，否则会把真实候选判成不可序列化。
    """

    if item is None:
        return {}
    as_dict = getattr(item, "as_dict", None)
    if callable(as_dict):
        payload = as_dict()
        if isinstance(payload, Mapping):
            return {str(key): _plain(value) for key, value in payload.items()}
    if isinstance(item, Mapping):
        return {str(key): _plain(value) for key, value in item.items()}
    if is_dataclass(item) and not isinstance(item, type):
        return {str(key): _plain(value) for key, value in asdict(item).items()}
    fields = getattr(item, "__dict__", None)
    if isinstance(fields, dict):
        return {str(key): _plain(value) for key, value in fields.items()}
    raise JevError(f"无法序列化候选行动：{type(item).__name__}")

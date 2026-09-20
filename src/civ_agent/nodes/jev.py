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
EXPANSION_OPEN_THRESHOLD = 0.6
GROWTH_RISK_THRESHOLD = 0.5
REVERSIBILITY_THRESHOLD = 0.5

#: Jev Assess 提出的问题（v7 §12 的 ``jev_assess`` 阶段）。
ASSESS_QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "expansion_open",
        "kind": NOUL,
        "instructions": "当前局面是否仍然存在值得占用的、未被阻挡的扩张机会？",
        "criteria": {
            "true": "还有可占资源/淡水/战略要地，且我方有能力派出开拓者。",
            "false": "可占位置已被占满、被强敌封锁，或当前无力承担新城成本。",
        },
    },
    {
        "id": "growth_risk",
        "kind": NOUL,
        "instructions": "当前是否存在必须立刻处理的增长或经济风险（粮食盈余不足、住房或宜居度压制、金币转负）？",
        "criteria": {
            "true": "任一城市增长停滞或国库每回合净减。",
            "false": "各城增长正常且国库净增。",
        },
    },
    {
        "id": "military_pressure",
        "kind": SCORE,
        "instructions": "当前面临的军事压力有多高？",
        "criteria": [
            "无敌意单位接近，无战争状态。",
            "有零星蛮族或邻国单位在边境活动。",
            "存在明确敌对意图或已在交战。",
        ],
    },
    {
        "id": "strategic_direction",
        "kind": CHOICE,
        "instructions": "本回合最应当投入的方向是什么？",
        "criteria": {
            "expand": "铺城、占资源、扩张领土。",
            "develop": "建设已占城市：区域、改良、建筑。",
            "defend": "应对眼前的军事或忠诚威胁。",
            "research": "推进科技或市政路线以解锁关键能力。",
        },
    },
)

#: Jev Review 提出的问题（v7 §12 的 ``jev_review`` 阶段）。
REVIEW_QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "plan_conflicts_with_assessment",
        "kind": NOUL,
        "instructions": "候选行动是否与本次 Jev Assess 的判断相冲突（例如评估为需要防御却继续无防守的扩张）？",
        "criteria": {
            "true": "行动直接违背已识别的风险或方向。",
            "false": "行动与判断一致或至少不冲突。",
        },
    },
    {
        "id": "needs_confirmation",
        "kind": NOUL,
        "instructions": "候选行动是否属于不可逆或高代价操作，应当在执行前再确认一次？",
        "criteria": {
            "true": "宣战、和谈、放弃城市、大量购金等难以撤回的操作。",
            "false": "常规生产、移动、研究选择等可调整操作。",
        },
    },
    {
        "id": "reversibility",
        "kind": "score",
        "instructions": "候选行动的后果有多容易撤回？",
        "criteria": [
            "容易撤回：下一回合即可改回，几乎无损失。",
            "部分可撤回：会损失产能或时间，但可纠正。",
            "难以撤回：会造成长期或不可逆的后果。",
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
        "expansion_open": EXPANSION_OPEN_THRESHOLD,
        "growth_risk": GROWTH_RISK_THRESHOLD,
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


def _payload_from_verdicts(verdicts: Iterable[Verdict]) -> dict[str, Any]:
    collected = {verdict.question_id: verdict.as_dict() for verdict in verdicts}
    return {
        "verdicts": collected,
        "summary": {verdict.question_id: verdict.summary for verdict in verdicts},
        "blocking": [
            verdict.question_id
            for verdict in verdicts
            if verdict.question_id == "plan_conflicts_with_assessment"
            and (verdict.value or 0.0) >= 0.5
        ],
    }


ClassifierFactory = Callable[[dict[str, Any]], Any]


def make_classifier_factory(
    *, api_key: str, model: str | None = None, timeout: float | None = None
) -> ClassifierFactory:
    """返回一个长期存活的 classifier 工厂；连接池随实例复用。"""

    from langchain_typesafe import TypeSafeClassifier

    cache: dict[str, Any] = {}

    def factory(questions: dict[str, Any]) -> Any:
        # 问题固定，因此只需要一个实例；连接池得以复用。
        if "classifier" not in cache:
            kwargs: dict[str, Any] = {"questions": questions, "api_key": api_key}
            if model is not None:
                kwargs["model"] = model
            if timeout is not None:
                kwargs["timeout"] = timeout
            cache["classifier"] = TypeSafeClassifier(**kwargs)
        return cache["classifier"]

    return factory


async def jev_assess(
    observation: Observation,
    *,
    classifier_factory: ClassifierFactory,
    questions: Sequence[Mapping[str, Any]] = ASSESS_QUESTIONS,
) -> dict[str, Any]:
    """对当前局面做结构化判断，供 DeepSeek 决策使用。"""

    classifier = classifier_factory(_build_questions(questions))
    try:
        response = await classifier.ainvoke(observation_state(observation))
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
) -> dict[str, Any]:
    """审查候选行动/最终决定：风险、冲突与是否需要确认。"""

    state = {
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

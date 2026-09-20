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
#:
#: 校准依据（真实两回合验收，2026-09-20）：Jev 对 Turn 1「建都 / 选制陶术 /
#: 选法典」这类**完全合理**的动作给出的 assumptions_supported 只有 0.20–0.26，
#: cost_understood 只有 0.06–0.10。原先 0.50 的门槛落在实际分布**之外**，导致
#: 任何动作都被拦下、回合永不推进。复核的本意是"防止无依据的推测"，不是"要求
#: 高置信"，因此门槛按实测分布下调，并保留显式常量以便再校准。
BLOCKING_THRESHOLD = 0.25
INFORMATION_GAP_THRESHOLD = 0.5
FACTUAL_CONFLICT_THRESHOLD = 0.5
#: Civ6 特有红线（审查 R10 之后新增）：军力代差硬打、攻城手段不足、战狂代价
#: 未评估等，wiki 的 L0 速查把它们列为可直接依据的硬规则。
CIV6_REDLINE_THRESHOLD = 0.5

#: 问题角色，决定答案如何被使用：
#: - ``blocking``：为"否"即可阻止提交（复核用）
#: - ``informative``：只作为信号交回模型（评估用）
#: - ``descriptive``：只记录，不参与任何判定（Choice/Score 多为这一类）
ROLE_BLOCKING = "blocking"
ROLE_INFORMATIVE = "informative"
ROLE_DESCRIPTIVE = "descriptive"

#: Jev Assess 提出的问题。
#:
#: 设计依据是 ``docs/wiki/`` 的 L0 决策速查（游戏机制事实），不是凭印象编的
#: 战略主张：
#:
#: - ``victory.md``：不要平均发展，第一章就选定唯一主路线；每 10 回合重估瓶颈；
#:   AI 接近任意胜利都要立刻反制。
#: - ``economy.md``：商路闲置 = 机会成本；宜居度是隐藏产出税；金币/信仰是时间
#:   加速器，不要无计划囤积。
#: - ``military.md``：敌方在边境集结攻城单位 = 宣战信号；军力差 2 倍视为实质威胁。
#:
#: 这些问句问的是**局面与事实**（走哪条路、瓶颈在哪、威胁多高、有没有浪费），
#: 而不是"应该走哪条路"的答案；答案由模型给，Jev 只做结构化判断。
ASSESS_QUESTIONS: tuple[dict[str, Any], ...] = (
    # ---- 战略取向（对应 victory.md：不平均发展、每 10 回合重估）----
    {
        "id": "primary_victory_path",
        "kind": CHOICE,
        "role": ROLE_DESCRIPTIVE,
        "instructions": "按当前已知事实，本局最可能投入的主路线是哪一条（选最符合现状的一项，不要求已决定）？",
        "criteria": {
            "science": "科技：宇航中心 + 太空项目链；需要产能与铝。",
            "culture": "文化：旅游压过对手国内游客；需要伟作/奇观/商路乘区。",
            "religion": "宗教：己方宗教成为所有文明主流；需要圣地与大预言家。",
            "domination": "征服：占领对手首都；军事是清道夫，需回填主路线。",
            "diplomatic": "外交：外交支持度点数制，硬上限 20 点。",
            "score": "分数兜底：仅在稳居第一且无人接近其他胜利时考虑。",
        },
    },
    {
        "id": "current_bottleneck",
        "kind": CHOICE,
        "role": ROLE_DESCRIPTIVE,
        "instructions": "当前限制主路线推进的最大瓶颈是哪一项（据实选，无明确短板选 none）？",
        "criteria": {
            "production": "产能不足，关键区域/单位/项目排队过长。",
            "science": "科技产出或研究顺序拖慢解锁。",
            "culture": "文化/市政或旅游乘区不足。",
            "faith": "信仰不足，无法支撑宗教线或买伟人/移民。",
            "gold": "金币不足，无法购买或维持军费。",
            "amenities": "宜居度不足，全城产出与增长被百分比惩罚。",
            "housing": "住房不足，人口增长停滞。",
            "military": "军力不足，无法应对威胁或推进征服。",
            "population": "人口/城市数量不足，复利基数太小。",
            "diplomacy": "外交支持度或关系管理不足。",
            "none": "没有明确单一瓶颈。",
        },
    },
    {
        "id": "bottleneck_actionable",
        "kind": NOUL,
        "role": ROLE_INFORMATIVE,
        "instructions": "当前材料是否足以支撑**本回合就针对上述瓶颈**做一个具体动作？",
        "criteria": {
            "true": "材料里能指到具体城市/单位/科技/政策与可执行动作。",
            "false": "还缺关键事实，先补读再决定更稳妥。",
        },
    },
    # ---- 威胁与战备（对应 military.md：攻城单位集结 = 宣战信号）----
    {
        "id": "military_threat",
        "kind": SCORE,
        "role": ROLE_DESCRIPTIVE,
        "instructions": "当前面临的军事威胁程度有多高（按 wiki 口径：军力差 2 倍或边境集结即为实质威胁）？",
        "criteria": [
            "无威胁：无敌意单位接近，无战争状态。",
            "边境骚扰：少量蛮族或外国单位在边境活动。",
            "明确威胁：存在敌对意图或军力接近 2 倍差距。",
            "迫在眉睫：已在交战，或城市/关键单位本回合内可能失守。",
        ],
    },
    {
        "id": "enemy_siege_massing",
        "kind": NOUL,
        "role": ROLE_INFORMATIVE,
        "instructions": "是否有敌方攻城单位（Catapult/Trebuchet 类）正在向边境集结？",
        "criteria": {
            "true": "观察到攻城单位移动或已就位——wiki 视其为宣战信号。",
            "false": "未观察到，或视野内没有这类单位。",
        },
    },
    {
        "id": "defense_inadequate",
        "kind": NOUL,
        "role": ROLE_INFORMATIVE,
        "instructions": "在可预见的威胁下，当前防御是否明显不足（照 wiki：城墙是最高性价比防御）？",
        "criteria": {
            "true": "关键城市无城墙/驻军，且存在明确威胁。",
            "false": "防线够用，或当前没有需要防御的方向。",
        },
    },
    # ---- 经济与增长（对应 economy.md：宜居度税、商路闲置、金信仰是加速器）----
    {
        "id": "amenities_penalty",
        "kind": NOUL,
        "role": ROLE_INFORMATIVE,
        "instructions": "是否存在宜居度不足导致的产出/增长惩罚？",
        "criteria": {
            "true": "至少一座城市的宜居度为负，正在承受百分比惩罚。",
            "false": "各城宜居度正常。",
        },
    },
    {
        "id": "idle_trade_capacity",
        "kind": NOUL,
        "role": ROLE_INFORMATIVE,
        "instructions": "是否存在闲置的商路容量（可建商队却未建，或商路无目的地）？",
        "criteria": {
            "true": "商路容量未被用满——wiki 视为白送的机会成本。",
            "false": "商路容量已用满或尚未解锁。",
        },
    },
    # ---- 事实质量（保留审查 R10 要求的信息类判断）----
    {
        "id": "information_gap",
        "kind": NOUL,
        "role": ROLE_INFORMATIVE,
        "instructions": "为了在本回合做出有依据的选择，是否仍缺少必需的当前事实？",
        "criteria": {
            "true": "至少一项做决定所必需的事实缺失或读不到。",
            "false": "当前材料足以评估本回合的选择。",
        },
    },
    {
        "id": "factual_conflict",
        "kind": NOUL,
        "role": ROLE_INFORMATIVE,
        "instructions": "当前材料内部是否存在互相矛盾的事实？",
        "criteria": {
            "true": "存在两处材料无法同时为真。",
            "false": "未发现互相矛盾之处。",
        },
    },
)

#: Jev Review 提出的问题。
#:
#: 分两类：
#:
#: - **证据类**（blocking）：候选的假设是否有事实支持、信息是否够提交；
#: - **Civ6 红线类**（blocking）：wiki L0 明确列为可直接依据的硬规则——军力代差
#:   硬打、防御不足还要拖延、战狂代价未评估等。这类问题的"是"表示**确实踩线**，
#:   因此其 blocking 语义是"为是则拦"。
REVIEW_QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "assumptions_supported",
        "kind": NOUL,
        "role": ROLE_BLOCKING,
        "instructions": "候选行动所依赖的关键假设，是否被当前材料中的事实支持？",
        "criteria": {
            "true": "候选的前提能在材料里找到对应事实。",
            "false": "候选依赖了材料中没有的推测，或与材料冲突。",
        },
    },
    {
        "id": "information_sufficient",
        "kind": NOUL,
        "role": ROLE_BLOCKING,
        "instructions": "就这次的候选而言，信息是否已经足够到可以提交执行？",
        "criteria": {
            "true": "足以提交；剩余不确定性可以接受。",
            "false": "应先补读事实或查规则，再提交。",
        },
    },
    {
        "id": "combat_power_deficit",
        "kind": NOUL,
        "role": ROLE_BLOCKING,
        "blocking_when": "true",
        "instructions": "候选行动是否要用明显低一级的兵种硬打高一级兵种（wiki：差 10 点战力伤害显著拉开，差 36+ 必被秒杀）？",
        "criteria": {
            "true": "确实存在代差硬打，且没有夹击/地形/驻防补偿。",
            "false": "不涉及此类攻击，或已有足够补偿。",
        },
    },
    {
        "id": "siege_capability_missing",
        "kind": NOUL,
        "role": ROLE_BLOCKING,
        "blocking_when": "true",
        "instructions": "候选若是攻城，是否缺少必要手段（wiki：远程只打兵不清城防，破墙必须靠攻城单位或破城锤，最后需近战占领）？",
        "criteria": {
            "true": "计划攻城但没有攻城单位/破城锤，或没有近战单位收尾。",
            "false": "不是攻城动作，或手段齐备。",
        },
    },
    {
        "id": "unprepared_war",
        "kind": NOUL,
        "role": ROLE_BLOCKING,
        "blocking_when": "true",
        "instructions": "候选是否在防御明显不足的情况下主动开启或扩大战争？",
        "criteria": {
            "true": "要在无城墙/无驻军/无盟友的状态下开战或扩大战线。",
            "false": "不涉及开战，或防御已就绪。",
        },
    },
    {
        "id": "action_cost",
        "kind": SCORE,
        "role": ROLE_DESCRIPTIVE,
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


#: 每条问句的阈值：红线类用 CIV6_REDLINE_THRESHOLD，其余按角色取。
_THRESHOLDS: dict[str, float] = {
    "information_gap": INFORMATION_GAP_THRESHOLD,
    "factual_conflict": FACTUAL_CONFLICT_THRESHOLD,
    "bottleneck_actionable": INFORMATION_GAP_THRESHOLD,
    "assumptions_supported": BLOCKING_THRESHOLD,
    "information_sufficient": BLOCKING_THRESHOLD,
    "combat_power_deficit": CIV6_REDLINE_THRESHOLD,
    "siege_capability_missing": CIV6_REDLINE_THRESHOLD,
    "unprepared_war": CIV6_REDLINE_THRESHOLD,
}


def _threshold_for(question_id: str, spec: Mapping[str, Any] | None = None) -> float:
    """问句阈值；未登记的按 0.5。"""

    if question_id in _THRESHOLDS:
        return _THRESHOLDS[question_id]
    if spec is not None and spec.get("threshold") is not None:
        return float(spec["threshold"])
    return 0.5


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


def _payload_from_verdicts(
    verdicts: Iterable[Verdict],
    specs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """按**问句规格**决定哪些答案参与判定，而不是硬编码问句名。

    ``role`` 决定用途（blocking / informative / descriptive）；``blocking_when``
    决定方向：

    - ``"false"``（默认）：证据不足类——为否即拦；
    - ``"true"``：Civ6 红线类——**踩线**（为是）即拦。
    """

    collected = {verdict.question_id: verdict.as_dict() for verdict in verdicts}
    by_id = {spec["id"]: spec for spec in specs}

    blocking: list[str] = []
    information_gaps: list[str] = []
    for question_id, verdict in collected.items():
        spec = by_id.get(question_id)
        if spec is None or spec.get("role") != ROLE_BLOCKING:
            continue
        threshold = _threshold_for(question_id, spec)
        value = verdict["value"]
        if value is None:
            # Score 类不参与判定；缺少数值时也不猜。
            continue
        direction = str(spec.get("blocking_when") or "false")
        triggered = value >= threshold if direction == "true" else value < threshold
        if triggered:
            blocking.append(question_id)

    for question_id, spec in by_id.items():
        if spec.get("role") != ROLE_INFORMATIVE:
            continue
        verdict = collected.get(question_id)
        if verdict is None or verdict["value"] is None:
            continue
        if verdict["value"] >= _threshold_for(question_id, spec):
            information_gaps.append(question_id)

    reasons = [
        f"{question_id}={collected[question_id]['summary']}" for question_id in blocking
    ]
    return {
        "verdicts": collected,
        "summary": {verdict.question_id: verdict.summary for verdict in verdicts},
        "blocking": blocking,
        # 被拦时必须给出**可读理由**：模型下一轮要靠它调整，否则只会重复同一提案
        # 或干脆不再提案。
        "blocking_reasons": reasons,
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
    payload = _payload_from_verdicts(verdicts, questions)
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
    return _payload_from_verdicts(_verdicts(questions, response), questions)


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

"""M04：Jev 判断节点。

用假 classifier 跑节点逻辑（离线、无网络），另用真实 ``langchain_typesafe`` 的
pydantic 答案模型构造响应，因此测的是真实字段名而不是自造的假结构。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from civ_agent.nodes.jev import (
    ASSESS_QUESTIONS,
    REVIEW_QUESTIONS,
    JevError,
    Verdict,
    _build_questions,
    _summarize,
    jev_assess,
    jev_review,
    make_classifier_factory,
    observation_state,
)
from civ_agent.observation import OurState, OpponentState, Observation
from langchain_typesafe import ChoiceAnswer, NoulAnswer, ScoreAnswer


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _observation() -> Observation:
    return Observation(
        turn=7,
        our_state=OurState(
            civilization="巴比伦",
            leader="汉谟拉比",
            cities=3,
            total_population=9,
            gold=120.0,
            gold_per_turn=8.5,
            science=14.2,
            culture=9.0,
            military_strength=210,
            current_research="TECH_POTTERY",
            score=88,
        ),
        opponents=(OpponentState(player_id=2, civilization="埃及", known_cities=2),),
        unknown=("units: TimeoutError",),
    )


class FakeResponse:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers


class FakeClassifier:
    """记录收到的 state 与问题，返回预设答案。"""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.questions: list[dict[str, Any]] = []
        self.states: list[Any] = []

    async def ainvoke(self, state: Any) -> FakeResponse:
        self.states.append(state)
        return FakeResponse(self.answers)


def _factory_for(answers: dict[str, Any]) -> tuple[Any, FakeClassifier]:
    classifier = FakeClassifier(answers)

    def factory(questions: dict[str, Any]) -> FakeClassifier:
        classifier.questions.append(questions)
        return classifier

    return factory, classifier


def _assess_answers(**overrides: Any) -> dict[str, Any]:
    answers: dict[str, Any] = {
        "expansion_open": NoulAnswer(type="noul", noul=0.81),
        "growth_risk": NoulAnswer(type="noul", noul=0.12),
        "military_pressure": ScoreAnswer(
            type="score", score=0.4, legend={0: "低", 1: "中", 2: "高"}, probabilities={0: 0.6, 1: 0.4, 2: 0.0}, confidence=0.7
        ),
        "strategic_direction": ChoiceAnswer(
            type="choice",
            choice="expand",
            probabilities={"expand": 0.7, "develop": 0.3},
            confidence=0.7,
        ),
    }
    answers.update(overrides)
    return answers


def _review_answers(**overrides: Any) -> dict[str, Any]:
    answers: dict[str, Any] = {
        "plan_conflicts_with_assessment": NoulAnswer(type="noul", noul=0.9),
        "needs_confirmation": NoulAnswer(type="noul", noul=0.2),
        "reversibility": ScoreAnswer(
            type="score", score=2.1, legend={0: "易", 1: "部分", 2: "难"}, probabilities={2: 0.9}, confidence=0.9
        ),
    }
    answers.update(overrides)
    return answers


# ---------------------------------------------------------------------------
# 问题构造
# ---------------------------------------------------------------------------


def test_assess_questions_use_the_three_typesafe_kinds() -> None:
    kinds = {spec["kind"] for spec in ASSESS_QUESTIONS}

    assert kinds == {"noul", "choice", "score"}


def test_questions_are_built_as_real_typesafe_objects() -> None:
    questions = _build_questions(ASSESS_QUESTIONS)

    from langchain_typesafe import Choice, Noul, Score

    assert set(questions) == {spec["id"] for spec in ASSESS_QUESTIONS}
    assert isinstance(questions["expansion_open"], Noul)
    assert isinstance(questions["strategic_direction"], Choice)
    assert isinstance(questions["military_pressure"], Score)


def test_questions_carry_instructions_every_time() -> None:
    """TypeSafe 要求 instructions 写完整问题，不能只靠 id 自解释。"""

    for spec in (*ASSESS_QUESTIONS, *REVIEW_QUESTIONS):
        assert spec["instructions"].strip().endswith(("？", "?"))


def test_unknown_question_kind_is_rejected() -> None:
    with pytest.raises(JevError, match="种类"):
        _build_questions([{"id": "x", "kind": "telepathy", "instructions": "?"}])


# ---------------------------------------------------------------------------
# jev_assess
# ---------------------------------------------------------------------------


def test_jev_assess_returns_serializable_verdicts() -> None:
    factory, _ = _factory_for(_assess_answers())

    result = _run(jev_assess(_observation(), classifier_factory=factory))

    assert set(result["verdicts"]) == {spec["id"] for spec in ASSESS_QUESTIONS}
    assert result["turn"] == 7
    assert result["verdicts"]["expansion_open"]["kind"] == "noul"


def test_jev_assess_applies_the_expansion_threshold() -> None:
    factory, _ = _factory_for(_assess_answers())
    result = _run(jev_assess(_observation(), classifier_factory=factory))

    # 0.81 >= 0.6 → 是
    assert result["verdicts"]["expansion_open"]["summary"].startswith("是")
    # 0.12 < 0.5 → 否
    assert result["verdicts"]["growth_risk"]["summary"].startswith("否")


def test_jev_assess_records_the_probability_and_threshold_for_audit() -> None:
    factory, _ = _factory_for(_assess_answers())
    result = _run(jev_assess(_observation(), classifier_factory=factory))

    summary = result["verdicts"]["expansion_open"]["summary"]
    assert "0.81" in summary
    assert "0.60" in summary
    assert result["verdicts"]["expansion_open"]["value"] == pytest.approx(0.81)


def test_jev_assess_keeps_choice_label_and_confidence() -> None:
    factory, _ = _factory_for(_assess_answers())
    result = _run(jev_assess(_observation(), classifier_factory=factory))

    verdict = result["verdicts"]["strategic_direction"]
    assert "expand" in verdict["summary"]
    assert verdict["confidence"] == pytest.approx(0.7)
    assert verdict["answer"]["choice"] == "expand"


def test_jev_assess_sends_a_json_compatible_state() -> None:
    import json

    factory, classifier = _factory_for(_assess_answers())
    _run(jev_assess(_observation(), classifier_factory=factory))

    # 不抛异常即说明 state 是 JSON 兼容的；同时确认关键事实真的进去了。
    encoded = json.dumps(classifier.states[0], ensure_ascii=False)
    assert "巴比伦" in encoded
    assert "埃及" in encoded
    assert classifier.states[0]["turn"] == 7


def test_jev_assess_raises_when_a_required_answer_is_missing() -> None:
    answers = _assess_answers()
    del answers["growth_risk"]
    factory, _ = _factory_for(answers)

    with pytest.raises(JevError, match="growth_risk"):
        _run(jev_assess(_observation(), classifier_factory=factory))


def test_jev_assess_wraps_transport_failures_as_jev_error() -> None:
    class BoomClassifier:
        async def ainvoke(self, state: Any) -> Any:
            raise TimeoutError("typesafe 超时")

    with pytest.raises(JevError, match="TimeoutError"):
        _run(
            jev_assess(
                _observation(), classifier_factory=lambda _questions: BoomClassifier()
            )
        )


def test_jev_assess_rejects_a_response_without_answers() -> None:
    class NoAnswers:
        async def ainvoke(self, state: Any) -> Any:
            return object()

    with pytest.raises(JevError, match="answers"):
        _run(
            jev_assess(
                _observation(), classifier_factory=lambda _questions: NoAnswers()
            )
        )


def test_assessment_summary_maps_question_ids_to_readable_text() -> None:
    factory, _ = _factory_for(_assess_answers())
    result = _run(jev_assess(_observation(), classifier_factory=factory))

    assert set(result["summary"]) == {spec["id"] for spec in ASSESS_QUESTIONS}
    assert isinstance(result["summary"]["military_pressure"], str)


# ---------------------------------------------------------------------------
# jev_review
# ---------------------------------------------------------------------------


def test_jev_review_flags_a_plan_that_conflicts_with_the_assessment() -> None:
    factory, _ = _factory_for(_review_answers())

    result = _run(
        jev_review(
            assessment={"summary": {"expansion_open": "是"}},
            candidates=[{"tool": "found_city", "arguments": {"x": 1, "y": 2}}],
            final_action=None,
            classifier_factory=factory,
        )
    )

    assert result["blocking"] == ["plan_conflicts_with_assessment"]
    assert result["verdicts"]["plan_conflicts_with_assessment"]["value"] == pytest.approx(0.9)


def test_jev_review_without_conflict_is_not_blocking() -> None:
    factory, _ = _factory_for(
        _review_answers(plan_conflicts_with_assessment=NoulAnswer(type="noul", noul=0.1))
    )

    result = _run(
        jev_review(
            assessment=None,
            candidates=[],
            final_action={"tool": "set_research", "arguments": {"tech": "TECH_POTTERY"}},
            classifier_factory=factory,
        )
    )

    assert result["blocking"] == []


def test_jev_review_sends_candidates_and_final_action() -> None:
    factory, classifier = _factory_for(_review_answers())

    _run(
        jev_review(
            assessment={"summary": {"strategic_direction": "expand"}},
            candidates=[{"tool": "found_city", "arguments": {"x": 3, "y": 4}}],
            final_action={"tool": "found_city", "arguments": {"x": 3, "y": 4}},
            classifier_factory=factory,
        )
    )

    state = classifier.states[0]
    assert state["candidates"][0]["tool"] == "found_city"
    assert state["final_action"]["arguments"] == {"x": 3, "y": 4}
    assert state["assessment"]["summary"]["strategic_direction"] == "expand"


def test_jev_review_handles_missing_final_action() -> None:
    factory, classifier = _factory_for(_review_answers())

    _run(
        jev_review(
            assessment=None,
            candidates=[],
            final_action=None,
            classifier_factory=factory,
        )
    )

    assert classifier.states[0]["final_action"] is None


# ---------------------------------------------------------------------------
# 归约与摘要
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "payload", "expected_prefix"),
    [
        ("noul", {"noul": 0.9}, "是"),
        ("noul", {"noul": 0.1}, "否"),
        ("choice", {"choice": "defend", "confidence": 0.5}, "defend"),
        ("score", {"score": 1.5, "confidence": 0.4}, "1.50"),
    ],
)
def test_summarize_covers_every_kind(kind: str, payload: dict[str, Any], expected_prefix: str) -> None:
    summary, _value, _confidence = _summarize(kind, payload, threshold=0.5)

    assert summary.startswith(expected_prefix)


def test_summarize_rejects_unknown_kind() -> None:
    with pytest.raises(JevError, match="种类"):
        _summarize("vibes", {}, threshold=0.5)


def test_verdict_as_dict_is_plain_data() -> None:
    verdict = Verdict(
        question_id="q",
        kind="noul",
        summary="是",
        confidence=None,
        value=0.9,
        answer={"noul": 0.9, "type": "noul"},
    )

    assert verdict.as_dict() == {
        "question_id": "q",
        "kind": "noul",
        "summary": "是",
        "confidence": None,
        "value": 0.9,
        "answer": {"noul": 0.9, "type": "noul"},
    }


def test_observation_state_is_plain_and_json_ready() -> None:
    import json

    state = observation_state(_observation())

    assert json.loads(json.dumps(state, ensure_ascii=False))["turn"] == 7


# ---------------------------------------------------------------------------
# classifier 工厂
# ---------------------------------------------------------------------------


def test_classifier_factory_reuses_one_instance() -> None:
    """连接池复用：同一个工厂不应每次新建 classifier。"""

    factory = make_classifier_factory(api_key="test-key")
    questions = _build_questions(ASSESS_QUESTIONS)

    first = factory(questions)
    second = factory(questions)

    assert first is second


def test_classifier_factory_passes_the_api_key_explicitly() -> None:
    """不能只依赖 TYPESAFE_API_KEY 环境变量：配置层的映射结果要显式传入。"""

    factory = make_classifier_factory(api_key="explicit-key")
    classifier = factory(_build_questions(ASSESS_QUESTIONS))

    assert classifier.questions
    # SecretStr 不应在 repr 中泄漏
    assert "explicit-key" not in repr(classifier)

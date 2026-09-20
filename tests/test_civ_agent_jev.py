"""M04：Jev 判断节点。

用假 classifier 跑节点逻辑（离线、无网络），另用真实 ``langchain_typesafe`` 的
pydantic 答案模型构造响应，因此测的是真实字段名而不是自造的假结构。
"""

from __future__ import annotations

import asyncio
import json
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
from langchain_typesafe import NoulAnswer, ScoreAnswer


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
        "information_gap": NoulAnswer(type="noul", noul=0.81),
        "factual_conflict": NoulAnswer(type="noul", noul=0.12),
        "immediate_risk": ScoreAnswer(
            type="score", score=0.4, legend={0: "无", 1: "低", 2: "中", 3: "高"}, probabilities={0: 0.6, 1: 0.4}, confidence=0.7
        ),
        "unknown_impact": NoulAnswer(type="noul", noul=0.7),
    }
    answers.update(overrides)
    return answers


def _review_answers(**overrides: Any) -> dict[str, Any]:
    answers: dict[str, Any] = {
        "assumptions_supported": NoulAnswer(type="noul", noul=0.2),
        "cost_understood": NoulAnswer(type="noul", noul=0.3),
        "information_sufficient": NoulAnswer(type="noul", noul=0.15),
        "action_cost": ScoreAnswer(
            type="score", score=2.1, legend={0: "可忽略", 1: "可承受", 2: "较高", 3: "很高"}, probabilities={2: 0.9}, confidence=0.9
        ),
    }
    answers.update(overrides)
    return answers


# ---------------------------------------------------------------------------
# 问题构造
# ---------------------------------------------------------------------------


def test_assess_questions_use_the_typesafe_kinds() -> None:
    kinds = {spec["kind"] for spec in ASSESS_QUESTIONS}

    assert kinds == {"noul", "score"}


def test_assess_questions_ask_about_evidence_not_human_strategy() -> None:
    """审查 R10：固定问题不得硬编码人工国策（expand/develop/defend/research）。"""

    blob = json.dumps(ASSESS_QUESTIONS, ensure_ascii=False)

    for banned in ("expand", "develop", "defend", "research"):
        assert banned not in blob
    assert {spec["id"] for spec in ASSESS_QUESTIONS} == {
        "information_gap",
        "factual_conflict",
        "immediate_risk",
        "unknown_impact",
    }


def test_review_questions_check_reality_not_obedience_to_the_assessment() -> None:
    """审查 R10：复核不得退化为"是否服从上一次模型回答"的自我确认。"""

    ids = {spec["id"] for spec in REVIEW_QUESTIONS}

    assert ids == {
        "assumptions_supported",
        "cost_understood",
        "information_sufficient",
        "action_cost",
    }
    assert not any("conflict" in question_id for question_id in ids)


def test_questions_are_built_as_real_typesafe_objects() -> None:
    questions = _build_questions(ASSESS_QUESTIONS)

    from langchain_typesafe import Noul, Score

    assert set(questions) == {spec["id"] for spec in ASSESS_QUESTIONS}
    assert isinstance(questions["information_gap"], Noul)
    assert isinstance(questions["immediate_risk"], Score)


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
    assert result["verdicts"]["information_gap"]["kind"] == "noul"


def test_jev_assess_applies_the_information_threshold() -> None:
    factory, _ = _factory_for(_assess_answers())
    result = _run(jev_assess(_observation(), classifier_factory=factory))

    # 0.81 >= 0.5 → 是
    assert result["verdicts"]["information_gap"]["summary"].startswith("是")
    # 0.12 < 0.5 → 否
    assert result["verdicts"]["factual_conflict"]["summary"].startswith("否")


def test_jev_assess_records_the_probability_and_threshold_for_audit() -> None:
    factory, _ = _factory_for(_assess_answers())
    result = _run(jev_assess(_observation(), classifier_factory=factory))

    summary = result["verdicts"]["information_gap"]["summary"]
    assert "0.81" in summary
    assert "0.50" in summary
    assert result["verdicts"]["information_gap"]["value"] == pytest.approx(0.81)


def test_jev_assess_reports_information_gaps_separately() -> None:
    """评估为"有信息缺口"时必须能被下游单独看见，而不是只留在日志里。"""

    factory, _ = _factory_for(_assess_answers())
    result = _run(jev_assess(_observation(), classifier_factory=factory))

    assert "information_gap" in result["information_gaps"]
    assert "unknown_impact" in result["information_gaps"]
    assert "factual_conflict" not in result["information_gaps"]


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
    del answers["factual_conflict"]
    factory, _ = _factory_for(answers)

    with pytest.raises(JevError, match="factual_conflict"):
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
    assert isinstance(result["summary"]["immediate_risk"], str)


# ---------------------------------------------------------------------------
# jev_review
# ---------------------------------------------------------------------------


def test_jev_review_blocks_when_evidence_does_not_support_the_candidate() -> None:
    """复核以现实证据为准：假设无支持 / 代价未知 / 信息不足 → 阻断提交。"""

    factory, _ = _factory_for(_review_answers())

    result = _run(
        jev_review(
            assessment={"summary": {"information_gap": "是"}},
            candidates=[{"tool": "found_city", "arguments": {"x": 1, "y": 2}}],
            final_action=None,
            classifier_factory=factory,
        )
    )

    assert set(result["blocking"]) == {
        "assumptions_supported",
        "cost_understood",
        "information_sufficient",
    }
    assert result["verdicts"]["assumptions_supported"]["value"] == pytest.approx(0.2)


def test_jev_review_passes_when_evidence_is_confirmed() -> None:
    factory, _ = _factory_for(
        _review_answers(
            assumptions_supported=NoulAnswer(type="noul", noul=0.9),
            cost_understood=NoulAnswer(type="noul", noul=0.8),
            information_sufficient=NoulAnswer(type="noul", noul=0.85),
        )
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

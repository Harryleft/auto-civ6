"""Deterministic, read-only science department plugin.

The department turns the typed turn snapshot into evidence for the national
coordinator.  It deliberately keeps technology ``unlocks`` opaque: the game
adapter's text is recorded for later cross-department verification, but this
module does not infer that a technology guarantees a particular unit.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..models import Outcome, OutcomeStatus
from .base import (
    Department,
    DepartmentAssessment,
    DepartmentContext,
    ReviewDisposition,
    SupportRequest,
    Workstream,
)


_MISSING_CURRENT = (
    "current research (tech_civic.current_research or overview.current_research)"
)
_MISSING_CURRENT_PROGRESS = "current research progress (tech_civic.current_research_turns)"
_MISSING_AVAILABLE = "available technologies (tech_civic.available_techs)"
_MISSING_UNLOCKS = "technology unlock text (available_techs[].unlocks)"
_MISSING_BARBARIANS = "barbarian overview (snapshot.barbarians)"
_SCIENCE_TERMS = (
    "science",
    "technology",
    "tech",
    "research",
    "科技",
    "研究",
    "科研",
    "解锁",
    "unlock",
)


def _meaningful_text(value: Any) -> str | None:
    """Return stable user data, treating sentinel values as absent evidence."""

    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.upper() in {"NONE", "N/A", "NA"}:
        return None
    return value


def _scalar_sort_text(value: Any) -> str:
    """Build a deterministic sort key without invoking arbitrary ``repr``."""

    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return ""


def _option_sort_key(option: Any) -> tuple[str, str, str, str, str]:
    return (
        _meaningful_text(getattr(option, "tech_type", None)) or "",
        _meaningful_text(getattr(option, "name", None)) or "",
        _scalar_sort_text(getattr(option, "cost", None)),
        _scalar_sort_text(getattr(option, "turns", None)),
        _meaningful_text(getattr(option, "unlocks", None)) or "",
    )


def _available_techs(context: DepartmentContext) -> tuple[Any, ...]:
    status = context.snapshot.tech_civic
    if status is None:
        return ()
    raw_options = getattr(status, "available_techs", None)
    if raw_options is None:
        return ()
    try:
        options = tuple(option for option in raw_options if option is not None)
    except TypeError:
        return ()
    return tuple(sorted(options, key=_option_sort_key))


def _current_research(context: DepartmentContext) -> str | None:
    status = context.snapshot.tech_civic
    overview = context.snapshot.overview
    for source in (status, overview):
        if source is None:
            continue
        current = _meaningful_text(getattr(source, "current_research", None))
        if current is not None:
            return current
    return None


def _current_research_turns(context: DepartmentContext) -> str | None:
    status = context.snapshot.tech_civic
    if status is None:
        return None
    value = getattr(status, "current_research_turns", None)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    return str(value)


def _unlock_text(option: Any) -> str | None:
    return _meaningful_text(getattr(option, "unlocks", None))


def _option_label(option: Any) -> str:
    tech_type = _meaningful_text(getattr(option, "tech_type", None))
    name = _meaningful_text(getattr(option, "name", None))
    if name and tech_type and name != tech_type:
        return f"{name} ({tech_type})"
    return tech_type or name or "unknown technology"


def _barbarian_counts(context: DepartmentContext) -> tuple[int, int] | None:
    barbarians = context.snapshot.barbarians
    if barbarians is None:
        return None
    camps = getattr(barbarians, "camps", ()) or ()
    units = getattr(barbarians, "units", ()) or ()
    try:
        return len(camps), len(units)
    except TypeError:
        return 0, 0


def _science_agenda(context: DepartmentContext) -> bool:
    texts = list(context.agenda)
    for goal in context.goals:
        texts.append(goal.statement)
        texts.extend(goal.tags)
    return any(
        any(term in text.lower() for term in _SCIENCE_TERMS)
        for text in texts
        if isinstance(text, str)
    )


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


class ScienceDepartment:
    """Science specialist that only reads a typed ``DepartmentContext``."""

    department: ClassVar[Department] = Department.SCIENCE

    @staticmethod
    def _relevance(context: DepartmentContext) -> float:
        current = _current_research(context)
        options = _available_techs(context)
        unlocks = tuple(option for option in options if _unlock_text(option))
        barbarian_counts = _barbarian_counts(context)

        relevance = 0.0
        if current is not None:
            relevance += 0.35
        if options:
            relevance += 0.35
        if unlocks:
            relevance += 0.15
        if _science_agenda(context):
            relevance += 0.10
        if barbarian_counts is not None and sum(barbarian_counts) > 0:
            relevance += 0.05
        return min(1.0, relevance)

    @staticmethod
    def _missing_evidence(
        context: DepartmentContext,
        current: str | None,
        current_turns: str | None,
        options: tuple[Any, ...],
        unlocks: tuple[Any, ...],
    ) -> tuple[str, ...]:
        missing: list[str] = []
        if current is None:
            missing.append(_MISSING_CURRENT)
        if current_turns is None:
            missing.append(_MISSING_CURRENT_PROGRESS)
        status = context.snapshot.tech_civic
        raw_options = getattr(status, "available_techs", None) if status else None
        if status is None or raw_options is None or not options:
            missing.append(_MISSING_AVAILABLE)
            missing.append(_MISSING_UNLOCKS)
        elif not unlocks:
            missing.append(_MISSING_UNLOCKS)
        elif len(unlocks) != len(options):
            missing.append("technology unlock text for one or more available technologies")
        if context.snapshot.barbarians is None:
            missing.append(_MISSING_BARBARIANS)
        return _unique(missing)

    def match(self, context: DepartmentContext) -> float:
        """Return a deterministic relevance score in the closed interval [0, 1]."""

        return self._relevance(context)

    def assess(self, context: DepartmentContext) -> DepartmentAssessment:
        """Assess research evidence without proposing or executing an action."""

        snapshot = context.snapshot
        relevance = self.match(context)
        current = _current_research(context)
        current_turns = _current_research_turns(context)
        options = _available_techs(context)
        unlock_options = tuple(option for option in options if _unlock_text(option))
        missing = self._missing_evidence(
            context,
            current,
            current_turns,
            options,
            unlock_options,
        )

        facts: list[str] = [f"turn: {snapshot.turn}"]
        if current is not None:
            facts.append(f"current research: {current}")
        if current_turns is not None:
            facts.append(f"current research turns: {current_turns}")
        for option in options:
            facts.append(f"available technology: {_option_label(option)}")
        for option in unlock_options:
            # Keep this explicitly raw and unverified.  A display string is not
            # a ruleset proof that a unit or capability is actually available.
            facts.append(
                "unlock text (raw, unverified) for "
                f"{_option_label(option)}: {_unlock_text(option)}"
            )

        barbarian_counts = _barbarian_counts(context)
        if barbarian_counts is not None:
            camps, units = barbarian_counts
            facts.append(f"known barbarians: {camps} camps, {units} units")

        risks: list[str] = []
        if current is None:
            risks.append("缺少当前研究证据，无法判断研究路线的连续性")
        if not options:
            risks.append("缺少可选科技证据，无法比较研究时序")
        if not unlock_options:
            risks.append("缺少可用的 unlock 原文，无法判断科技的具体协作价值")
        if barbarian_counts is not None and sum(barbarian_counts) > 0:
            risks.append("已知蛮族威胁需要军事部门核对力量、位置和防御缺口")
        elif barbarian_counts is None:
            risks.append("未提供蛮族概览，不能把未知区域解释为安全")

        opportunities: list[str] = []
        if current is not None and options:
            opportunities.append("比较当前研究与可选科技的完成时点")
        if unlock_options:
            opportunities.append("保留可选科技的原始 unlock 文本，供后续规则核对")
        if barbarian_counts is not None and sum(barbarian_counts) > 0:
            opportunities.append("将原始 unlock 文本交由军事部门核对蛮族应对机会")

        capability_gaps: list[str] = []
        if not options:
            capability_gaps.append("无法形成可比较的研究候选集")
        if not unlock_options:
            capability_gaps.append("无法基于 unlock 原文评估军事协作机会")
        if barbarian_counts is not None and sum(barbarian_counts) > 0 and not unlock_options:
            capability_gaps.append("蛮族威胁存在，但缺少可交给军事部门核对的科技解锁证据")

        support_requests: tuple[SupportRequest, ...] = ()
        workstreams: tuple[Workstream, ...] = ()
        barbarian_present = barbarian_counts is not None and sum(barbarian_counts) > 0
        if barbarian_present:
            camps, units = barbarian_counts
            support_requests = (
                SupportRequest(
                    requester=Department.SCIENCE,
                    target=Department.MILITARY,
                    objective="评估可选科技的军事解锁机会与蛮族应对需求",
                    reason=(
                        f"第 {snapshot.turn} 回合已知 {camps} 个蛮族营地和 {units} 个蛮族单位；"
                        "科技部门只提供 unlock 原文，不推断具体兵种或确定解锁结果"
                    ),
                ),
            )

        if relevance > 0.0:
            candidate_actions = ["比较当前研究与可选科技的完成时点"]
            if unlock_options:
                candidate_actions.append("记录可选科技的原始 unlock 文本并交叉核对")
            if barbarian_present:
                candidate_actions.append("请军事部门核对原始 unlock 文本与蛮族威胁")
            if missing:
                candidate_actions.append("补齐缺失证据后重新评估")
            objective = "评估研究路线与可选科技证据"
            if barbarian_present:
                objective = "在蛮族威胁下评估研究路线与军事解锁机会"
            workstreams = (
                Workstream(
                    workstream_id=f"science:{snapshot.snapshot_id}:research",
                    department=Department.SCIENCE,
                    objective=objective,
                    priority=int(round(relevance * 100)),
                    candidate_actions=tuple(candidate_actions),
                    exit_conditions=(
                        "研究路线已完成比较，或已明确记录仍缺失的证据",
                    ),
                ),
            )

        core_missing = tuple(item for item in missing if item != _MISSING_BARBARIANS)
        degraded = bool(core_missing)
        if degraded:
            summary = f"第 {snapshot.turn} 回合科技评估退化：核心科技证据不完整"
        else:
            current_label = current or "未提供"
            summary = (
                f"第 {snapshot.turn} 回合科技评估：当前研究 {current_label}，"
                f"可选科技 {len(options)} 项"
            )
        if barbarian_present:
            summary += "；已知蛮族威胁已生成军事协作信号"

        return DepartmentAssessment(
            department=Department.SCIENCE,
            snapshot_id=snapshot.snapshot_id,
            # Keep the exact value returned by match; the coordinator treats
            # any drift between these two calls as a contract violation.
            relevance=relevance,
            summary=summary,
            facts=_unique(facts),
            risks=_unique(risks),
            opportunities=_unique(opportunities),
            capability_gaps=_unique(capability_gaps),
            evidence_missing=missing,
            support_requests=support_requests,
            workstreams=workstreams,
            degraded=degraded,
        )

    def review(self, context: DepartmentContext, outcome: Outcome) -> ReviewDisposition:
        """Review an outcome without observing the game or mutating state."""

        if outcome.status is not OutcomeStatus.SUCCEEDED:
            return ReviewDisposition.REPLAN
        assessment = self.assess(context)
        if assessment.degraded:
            return ReviewDisposition.REPLAN
        if assessment.relevance > 0.0:
            return ReviewDisposition.CONTINUE
        return ReviewDisposition.EXIT

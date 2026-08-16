"""Deterministic, read-only diplomacy department."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from ..graph_snapshot import graph_goals, graph_snapshot
from ..models import Outcome
from .base import (
    BaseDepartment,
    Department,
    DepartmentAssessment,
    DepartmentContext,
    ReviewDisposition,
    SupportRequest,
    Workstream,
)


_DIPLOMACY_MARKERS = (
    "外交",
    "贸易",
    "联盟",
    "和平",
    "战争",
    "diplom",
    "trade",
    "alliance",
    "peace",
    "war",
)
_BARBARIAN_MARKERS = ("蛮族", "边境", "barbarian", "frontier")
_HOSTILE_STATES = frozenset({"HOSTILE", "UNFRIENDLY", "DENOUNCED"})


class _DiplomacyCivView(Protocol):
    """Minimum rival fields required by the diplomacy department."""

    player_id: int
    civ_name: str
    leader_name: str
    has_met: bool
    is_at_war: bool
    diplomatic_state: str
    relationship_score: int
    military_strength: int


def _contains_marker(values: Iterable[str], markers: tuple[str, ...]) -> bool:
    return any(
        marker in value.casefold()
        for value in values
        for marker in markers
    )


def _goal_texts(context: DepartmentContext) -> tuple[str, ...]:
    return tuple(
        text
        for goal in graph_goals(context)
        for text in (goal.statement, *goal.tags)
    )


def _explicit_diplomacy_request(context: DepartmentContext) -> bool:
    return _contains_marker(
        (*context.agenda, *_goal_texts(context)), _DIPLOMACY_MARKERS
    )


def _barbarian_request(context: DepartmentContext) -> bool:
    return _contains_marker(
        (*context.agenda, *_goal_texts(context)), _BARBARIAN_MARKERS
    )


def _barbarian_counts(context: DepartmentContext) -> tuple[int, int] | None:
    overview = graph_snapshot(context).barbarians
    if overview is None:
        return None
    return len(overview.camps), len(overview.units)


def _has_barbarian_activity(context: DepartmentContext) -> bool:
    counts = _barbarian_counts(context)
    return counts is not None and any(counts)


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _diplomacy_civs(context: DepartmentContext) -> tuple[_DiplomacyCivView, ...]:
    """Return the one authoritative source for this department's civ rows."""

    return tuple(graph_snapshot(context).diplomacy)


def _contacted_civs(context: DepartmentContext) -> tuple[_DiplomacyCivView, ...]:
    return tuple(
        sorted(
            (civ for civ in _diplomacy_civs(context) if civ.has_met),
            key=lambda civ: (civ.player_id, civ.civ_name, civ.leader_name),
        )
    )


def _uncontacted_civs(context: DepartmentContext) -> tuple[_DiplomacyCivView, ...]:
    return tuple(
        sorted(
            (civ for civ in _diplomacy_civs(context) if not civ.has_met),
            key=lambda civ: (civ.player_id, civ.civ_name, civ.leader_name),
        )
    )


class DiplomacyDepartment(BaseDepartment):
    """Assess diplomatic exposure without issuing game actions.

    The module treats an absent or uncontacted diplomatic row as unknown
    evidence.  It never turns that absence into a claim that a rival or a
    second front is safe.
    """

    department = Department.DIPLOMACY

    def match(self, context: DepartmentContext) -> float:
        """Return deterministic decision pressure in the inclusive range [0, 1]."""

        if not isinstance(context, DepartmentContext):
            raise TypeError("context must be DepartmentContext")

        scores: list[float] = []
        if _explicit_diplomacy_request(context):
            scores.append(1.0)

        civs = _diplomacy_civs(context)
        if any(civ.has_met for civ in civs):
            scores.append(0.45)
        if any(not civ.has_met for civ in civs):
            scores.append(0.70)
        if any(civ.is_at_war for civ in civs):
            scores.append(0.90)
        if any(
            civ.diplomatic_state.upper() in _HOSTILE_STATES
            or civ.relationship_score < 0
            for civ in civs
            if civ.has_met
        ):
            scores.append(0.65)
        if _has_barbarian_activity(context):
            scores.append(0.80)

        return max(scores, default=0.0)

    def assess(self, context: DepartmentContext) -> DepartmentAssessment:
        """Build an immutable assessment from the supplied snapshot only."""

        if not isinstance(context, DepartmentContext):
            raise TypeError("context must be DepartmentContext")

        snapshot = graph_snapshot(context)
        relevance = self.match(context)
        civs = _diplomacy_civs(context)
        contacted = _contacted_civs(context)
        uncontacted = _uncontacted_civs(context)
        facts: list[str] = []
        risks: list[str] = []
        opportunities: list[str] = []
        capability_gaps: list[str] = []
        evidence_missing: list[str] = []

        if not civs:
            evidence_missing.append(
                "缺少接触文明、战争状态、关系和对方军力证据；空结果不能解释为安全"
            )
            capability_gaps.append("外交态势查询未提供")

        for civ in contacted:
            state = civ.diplomatic_state.strip() or "UNKNOWN"
            facts.append(
                "已接触文明 "
                f"{civ.civ_name} (player_id={civ.player_id}): "
                f"war={'yes' if civ.is_at_war else 'no'}, "
                f"relationship={civ.relationship_score}, "
                f"state={state}, military_strength={civ.military_strength}"
            )
            if civ.is_at_war:
                risks.append(
                    f"与 {civ.civ_name} 处于战争，外交线与军事线形成并发战线"
                )
            if state.upper() in _HOSTILE_STATES or civ.relationship_score < 0:
                risks.append(
                    f"{civ.civ_name} 的关系信号偏敌对，边境与谈判风险升高"
                )
            if state.upper() == "UNKNOWN":
                evidence_missing.append(
                    f"{civ.civ_name} 的外交关系状态未知，不能用默认值推断安全"
                )
                capability_gaps.append(f"缺少 {civ.civ_name} 的关系状态证据")
            if civ.military_strength <= 0:
                evidence_missing.append(
                    f"{civ.civ_name} 的对方军力为零或未提供，不能视为无威胁"
                )
                capability_gaps.append(f"缺少 {civ.civ_name} 的可用军力证据")

        for civ in uncontacted:
            facts.append(
                f"存在未接触文明记录 player_id={civ.player_id}；"
                "其关系、战争状态和军力未知"
            )
        if uncontacted:
            risks.append("未接触信息不等于安全，未知对象可能构成额外外交或边境风险")
            evidence_missing.append("未接触文明的关系、战争状态和军力未知")
            capability_gaps.append("缺少未接触文明的外交与军力情报")

        counts = _barbarian_counts(context)
        barbarian_active = counts is not None and any(counts)
        if barbarian_active:
            camps, units = counts
            facts.append(
                f"蛮族态势：camps={camps}, units={units}；信息仅覆盖当前可见或已揭示区域"
            )
            risks.append(
                "蛮族行动可能与文明战争形成多线战争，必须同时评估边境防御和战线承压"
            )
            if not snapshot.units:
                evidence_missing.append("蛮族行动期间缺少本方单位证据，无法评估边境兵力")
                capability_gaps.append("缺少本方边境兵力证据")
        elif counts == (0, 0) and _barbarian_request(context):
            facts.append("当前蛮族查询未发现营地或单位；空结果仅代表当前可见信息")
            risks.append("蛮族空结果受可见性限制，不能证明边境全局安全")
        elif _barbarian_request(context):
            evidence_missing.append("缺少蛮族态势证据，无法排除额外边境战线")
            capability_gaps.append("缺少蛮族营地和单位态势")

        if contacted and not any(civ.is_at_war for civ in contacted):
            opportunities.append("已接触文明尚无战争状态，可在保持和平的前提下评估合作窗口")

        support_requests: tuple[SupportRequest, ...] = ()
        if barbarian_active:
            camps, units = counts
            support_requests = (
                SupportRequest(
                    requester=Department.DIPLOMACY,
                    target=Department.MILITARY,
                    objective="评估蛮族与文明战争的并发战线",
                    reason=(
                        f"发现 {camps} 个蛮族营地和 {units} 个可见单位；"
                        "需要联合评估边境风险，不能单点处理"
                    ),
                ),
            )

        if barbarian_active:
            objective = "评估蛮族与文明战争的多线外交及边境风险"
            priority = 90
            candidate_actions = ("coordinate_multi_front_assessment",)
            exit_conditions = ("蛮族与文明战线风险已重新评估",)
        elif evidence_missing:
            objective = "补齐接触文明、战争状态、关系和对方军力证据"
            priority = 80
            candidate_actions = ("refresh_diplomatic_evidence",)
            exit_conditions = ("当前回合外交证据完整且可核验",)
        else:
            objective = "维护已接触文明的战争、关系与军力态势"
            priority = 40
            candidate_actions = ("review_war_state", "review_relationships", "review_military_balance")
            exit_conditions = ("当前回合外交态势已复核",)

        workstreams = (
            Workstream(
                workstream_id=f"diplomacy:{snapshot.snapshot_id}",
                department=Department.DIPLOMACY,
                objective=objective,
                priority=priority,
                resource_claims={"diplomatic_attention": 1.0},
                candidate_actions=candidate_actions,
                exit_conditions=exit_conditions,
            ),
        )

        all_risks = _unique_sorted(risks)
        missing = _unique_sorted(evidence_missing)
        gaps = _unique_sorted(capability_gaps)
        if missing:
            summary = "外交态势证据不完整，不能据此判定安全"
        elif all_risks:
            summary = "外交态势存在多线风险"
        else:
            summary = "外交态势已基于当前接触文明证据完成评估"
        return DepartmentAssessment(
            department=Department.DIPLOMACY,
            snapshot_id=snapshot.snapshot_id,
            relevance=relevance,
            summary=summary,
            facts=_unique_sorted(facts),
            risks=all_risks,
            opportunities=_unique_sorted(opportunities),
            capability_gaps=gaps,
            evidence_missing=missing,
            support_requests=support_requests,
            workstreams=workstreams,
            degraded=bool(missing),
        )

    def review(
        self, context: DepartmentContext, outcome: Outcome
    ) -> ReviewDisposition:
        """Replan failed/stale work; continue while diplomatic pressure remains."""

        precheck = self._review_precheck(context, outcome)
        if precheck is not None:
            return precheck
        assessment = self.assess(context)
        if assessment.degraded:
            return ReviewDisposition.REPLAN
        return (
            ReviewDisposition.CONTINUE
            if assessment.relevance > 0
            else ReviewDisposition.EXIT
        )

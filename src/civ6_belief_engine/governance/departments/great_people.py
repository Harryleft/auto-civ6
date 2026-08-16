"""Deterministic, read-only great people department for the national strategy loop.

The department consumes only the immutable typed ``TurnSnapshot`` (the
``great_people`` overview). It never calls external services and never chooses
a concrete individual. Race pressure produces one workstream; the faith budget
for patronizing stays with the economy department via a support request, so
budget authority is not duplicated.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Outcome, OutcomeStatus, TurnSnapshot
from .base import (
    Department,
    DepartmentAssessment,
    DepartmentContext,
    ReviewDisposition,
    SupportRequest,
    Workstream,
)

_GREAT_PEOPLE_KEYWORDS = (
    "great",
    "伟人",
    "科学家",
    "工程师",
    "大商",
    "作家",
    "艺术家",
    "音乐家",
    "大将军",
    "海军统帅",
    "大预言家",
    "招募",
    "资助",
    "patron",
    "recruit",
)
# 与 derivation 规则一致的竞争阈值：领先者领先比例超过 50% 视为放弃竞争。
_GP_MAX_RACE_GAP_RATIO = 0.5


def _contains_keyword(values: Iterable[str], keywords: tuple[str, ...]) -> bool:
    haystack = " ".join(values).casefold()
    return any(keyword.casefold() in haystack for keyword in keywords)


def _context_text(context: DepartmentContext) -> tuple[str, ...]:
    values = list(context.agenda)
    for goal in context.goals:
        values.append(goal.statement)
        values.extend(goal.tags)
    return tuple(values)


class GreatPeopleDepartment:
    """Assess great people race pressure and request a faith budget from economy."""

    department = Department.GREAT_PEOPLE

    def match(self, context: DepartmentContext) -> float:
        """Deterministic relevance: evidence presence + agenda keywords."""

        snapshot: TurnSnapshot = context.snapshot
        relevance = 0.0
        if snapshot.great_people is not None and snapshot.great_people.standings:
            relevance += 0.55
        if _contains_keyword(_context_text(context), _GREAT_PEOPLE_KEYWORDS):
            relevance += 0.30
        if self._race_pressure(snapshot):
            relevance += 0.15
        return round(min(1.0, relevance), 6)

    @staticmethod
    def _race_pressure(snapshot: TurnSnapshot) -> bool:
        gp = snapshot.great_people
        if gp is None:
            return False
        for standing in gp.standings:
            if not standing.class_name or not standing.entries:
                continue
            entries = standing.entries
            ours = entries[0]
            leader = max(
                (e for e in entries[1:]),
                key=lambda e: e.points_total,
                default=None,
            )
            if leader is None or leader.points_total <= ours.points_total:
                continue
            gap_ratio = (
                (leader.points_total - ours.points_total) / leader.points_total
                if leader.points_total > 0
                else 0.0
            )
            if gap_ratio <= _GP_MAX_RACE_GAP_RATIO:
                return True
        return False

    def assess(self, context: DepartmentContext) -> DepartmentAssessment:
        """Build a stable assessment; race pressure yields one workstream."""

        snapshot: TurnSnapshot = context.snapshot
        relevance = self.match(context)
        missing: list[str] = []
        facts: list[str] = []
        risks: list[str] = []
        opportunities: list[str] = []
        capability_gaps: list[str] = []

        gp = snapshot.great_people
        if gp is None or not gp.standings:
            missing.append("great_people")
            return DepartmentAssessment(
                department=Department.GREAT_PEOPLE,
                snapshot_id=snapshot.snapshot_id,
                relevance=relevance,
                summary="伟人评估退化：缺少伟人态势证据，不提出竞争工作流",
                facts=(),
                risks=("缺少伟人态势证据，不能把未观测视为没有竞争压力",),
                evidence_missing=tuple(missing),
                degraded=True,
            )

        pressure: list[dict[str, object]] = []
        leading: list[str] = []
        for standing in gp.standings:
            if not standing.class_name or not standing.entries:
                continue
            entries = standing.entries
            ours = entries[0]
            leader = max(
                (e for e in entries[1:]),
                key=lambda e: e.points_total,
                default=None,
            )
            class_name = standing.class_name
            if leader is None or leader.points_total <= ours.points_total:
                leading.append(f"{class_name}({ours.points_total})")
                continue
            gap = leader.points_total - ours.points_total
            gap_ratio = gap / leader.points_total if leader.points_total > 0 else 0.0
            if gap_ratio <= _GP_MAX_RACE_GAP_RATIO:
                pressure.append(
                    {
                        "class_name": class_name,
                        "leader_name": leader.player_name,
                        "gap": gap,
                        "gap_ratio": gap_ratio,
                    }
                )
            else:
                risks.append(
                    f"great_people.{class_name}: 领先者差距过大（{gap} 点），竞争不划算"
                )
        if leading:
            facts.append("great_people: 我们领先 " + "、".join(leading))

        support_requests: list[SupportRequest] = []
        workstreams: list[Workstream] = []
        if pressure:
            names = "、".join(str(item["class_name"]) for item in pressure)
            opportunities.append(
                f"{len(pressure)} 个伟人类别处于可竞争差距内（{names}）"
            )
            risks.append("多个类别同时竞争会摊薄信仰储备，需与经济部门排序")
            support_requests.append(
                SupportRequest(
                    requester=Department.GREAT_PEOPLE,
                    target=Department.ECONOMY,
                    objective="评估伟人资助可动用的信仰预算上限",
                    reason=(
                        "伟人部门只报告竞争压力，不自行计算预算；"
                        "是否资助及额度由经济部门给出保守上限后交议会仲裁"
                    ),
                )
            )
            tightest = min(pressure, key=lambda item: float(item["gap_ratio"]))
            priority = (
                85
                if float(tightest["gap_ratio"]) <= 0.1
                else 75
                if float(tightest["gap_ratio"]) <= 0.25
                else 65
            )
            labels = "、".join(
                f"{item['class_name']}(对 {item['leader_name']})" for item in pressure
            )
            workstreams.append(
                Workstream(
                    workstream_id=f"great_people:races:{snapshot.turn}",
                    department=Department.GREAT_PEOPLE,
                    objective="竞争可追赶的伟人类别，等待经济部门给出信仰预算",
                    priority=priority,
                    candidate_actions=(
                        f"竞争类别：{labels}",
                        "优先自然点数节奏，资助作为最后手段",
                        "不得为竞争伟人牺牲关键城市生产或军事需求",
                    ),
                    exit_conditions=(
                        "压力类别被超越或领先者差距超过 50%",
                        "伟人已被我方或对手招募",
                    ),
                )
            )
        elif not risks and not opportunities:
            opportunities.append("当前没有伟人竞争压力，保持自然点数积累")

        summary = (
            f"伟人评估：{len(gp.standings)} 个类别，"
            f"{len(pressure)} 个处于竞争压力"
        )
        return DepartmentAssessment(
            department=Department.GREAT_PEOPLE,
            snapshot_id=snapshot.snapshot_id,
            relevance=relevance,
            summary=summary,
            facts=tuple(dict.fromkeys(facts)),
            risks=tuple(dict.fromkeys(risks)),
            opportunities=tuple(dict.fromkeys(opportunities)),
            capability_gaps=tuple(capability_gaps),
            evidence_missing=tuple(missing),
            support_requests=tuple(support_requests),
            workstreams=tuple(workstreams),
            degraded=False,
        )

    def review(
        self, context: DepartmentContext, outcome: Outcome
    ) -> ReviewDisposition:
        """Review an outcome and choose a deterministic next disposition."""

        if not isinstance(context, DepartmentContext):
            raise TypeError("context must be DepartmentContext")
        if not isinstance(outcome, Outcome):
            raise TypeError("outcome must be Outcome")
        if outcome.turn != context.snapshot.turn:
            return ReviewDisposition.REPLAN
        if outcome.status is not OutcomeStatus.SUCCEEDED:
            return ReviewDisposition.REPLAN
        if outcome.result.get("campaign_complete") is True:
            return ReviewDisposition.EXIT
        if self.assess(context).degraded:
            return ReviewDisposition.REPLAN
        return ReviewDisposition.CONTINUE

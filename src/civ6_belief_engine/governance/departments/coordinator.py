"""Deterministic coordinator for independently pluggable departments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .base import (
    Department,
    DepartmentAssessment,
    DepartmentContext,
    DepartmentPlugin,
    SupportRequest,
    Workstream,
)
from ..models import StrategicGoal, TurnSnapshot


@dataclass(frozen=True, slots=True)
class CampaignDraft:
    campaign_id: str
    turn: int
    objective: str
    trigger: str
    workstreams: tuple[Workstream, ...]
    ready_workstream_ids: tuple[str, ...]
    blocked_workstream_ids: tuple[str, ...]
    coordination_links: tuple[SupportRequest, ...]
    unresolved_support_requests: tuple[SupportRequest, ...]
    missing_departments: tuple[Department, ...]
    blind_spots: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NationalStrategyBrief:
    snapshot_id: str
    turn: int
    assessments: tuple[DepartmentAssessment, ...]
    campaign: CampaignDraft


class DepartmentRegistry:
    def __init__(self, plugins: tuple[DepartmentPlugin, ...] = ()) -> None:
        self._plugins: dict[Department, DepartmentPlugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: DepartmentPlugin) -> None:
        if not isinstance(plugin, DepartmentPlugin):
            raise TypeError("plugin must implement DepartmentPlugin")
        if plugin.department in self._plugins:
            raise ValueError(f"department already registered: {plugin.department.value}")
        self._plugins[plugin.department] = plugin

    def unregister(self, department: Department) -> DepartmentPlugin | None:
        return self._plugins.pop(department, None)

    def plugins(self) -> tuple[DepartmentPlugin, ...]:
        return tuple(self._plugins[key] for key in Department if key in self._plugins)


class NationalStrategyCoordinator:
    def __init__(self, registry: DepartmentRegistry) -> None:
        self.registry = registry

    def run(
        self,
        snapshot: TurnSnapshot,
        *,
        agenda: tuple[str, ...] = (),
        goals: tuple[StrategicGoal, ...] = (),
    ) -> NationalStrategyBrief:
        context = DepartmentContext(snapshot=snapshot, agenda=agenda, goals=goals)
        assessments: list[DepartmentAssessment] = []
        for plugin in self.registry.plugins():
            try:
                relevance = plugin.match(context)
                if isinstance(relevance, bool) or not isinstance(relevance, (int, float)):
                    raise TypeError("match must return a number")
                assessment = plugin.assess(context)
                if assessment.department != plugin.department:
                    raise ValueError("assessment department does not match plugin")
                if assessment.snapshot_id != snapshot.snapshot_id:
                    raise ValueError("assessment does not reference current snapshot")
                if abs(assessment.relevance - float(relevance)) > 1e-9:
                    raise ValueError("assessment relevance does not match match result")
            except Exception as exc:
                assessment = DepartmentAssessment(
                    department=plugin.department,
                    snapshot_id=snapshot.snapshot_id,
                    relevance=0.0,
                    summary=f"{plugin.department.value} module degraded",
                    evidence_missing=(f"plugin failure: {type(exc).__name__}: {exc}",),
                    degraded=True,
                )
            assessments.append(assessment)
        campaign = self._synthesize(snapshot, tuple(assessments), agenda)
        return NationalStrategyBrief(
            snapshot_id=snapshot.snapshot_id,
            turn=snapshot.turn,
            assessments=tuple(assessments),
            campaign=campaign,
        )

    @staticmethod
    def _synthesize(
        snapshot: TurnSnapshot,
        assessments: tuple[DepartmentAssessment, ...],
        agenda: tuple[str, ...],
    ) -> CampaignDraft:
        missing = tuple(
            department
            for department in Department
            if all(item.department != department for item in assessments)
        )
        barbarian_count = 0
        if snapshot.barbarians is not None:
            barbarian_count = len(snapshot.barbarians.camps) + len(snapshot.barbarians.units)
        if barbarian_count:
            objective = "清除已知蛮族威胁，同时维持本土防御和国家发展"
            trigger = f"发现 {len(snapshot.barbarians.camps)} 个营地和 {len(snapshot.barbarians.units)} 个可见蛮族单位"
        elif agenda:
            objective = agenda[0]
            trigger = "显式国家议程"
        else:
            objective = "维持国家发展并处理当前最高风险"
            trigger = "例行全局评估"
        workstreams = tuple(
            workstream
            for assessment in assessments
            for workstream in assessment.workstreams
        )
        ids = {item.workstream_id for item in workstreams}
        assessment_by_department = {
            assessment.department: assessment for assessment in assessments
        }
        coordination_links = tuple(
            request
            for assessment in assessments
            for request in assessment.support_requests
        )
        available_departments = {
            assessment.department
            for assessment in assessments
            if not assessment.degraded
        }
        unresolved_requests = tuple(
            request
            for request in coordination_links
            if request.target not in available_departments
        )
        # A draft has no execution ledger yet.  Fail closed: an absent dependency
        # is unknown, not evidence that it has completed.
        ready = tuple(
            item.workstream_id
            for item in workstreams
            if not item.dependencies
            and not assessment_by_department[item.department].degraded
        )
        blocked = tuple(item.workstream_id for item in workstreams if item.workstream_id not in ready)
        blind_spots = [
            gap
            for assessment in assessments
            for gap in assessment.evidence_missing
        ]
        blind_spots.extend(f"missing plugin: {item.value}" for item in missing)
        blind_spots.extend(
            f"degraded department: {assessment.department.value}"
            for assessment in assessments
            if assessment.degraded
        )
        blind_spots.extend(
            f"unresolved support target: {request.target.value}"
            for request in unresolved_requests
        )
        blind_spots.extend(
            f"unresolved dependency: {dependency}"
            for item in workstreams
            for dependency in item.dependencies
            if dependency not in ids
        )
        digest = hashlib.sha256(
            f"{snapshot.snapshot_id}:{objective}".encode("utf-8")
        ).hexdigest()[:16]
        return CampaignDraft(
            campaign_id=f"campaign:{snapshot.turn}:{digest}",
            turn=snapshot.turn,
            objective=objective,
            trigger=trigger,
            workstreams=workstreams,
            ready_workstream_ids=ready,
            blocked_workstream_ids=blocked,
            coordination_links=coordination_links,
            unresolved_support_requests=unresolved_requests,
            missing_departments=missing,
            blind_spots=tuple(dict.fromkeys(blind_spots)),
        )

from __future__ import annotations

from dataclasses import dataclass

from civ6_belief_engine.governance import GraphSnapshotView
from civ6_belief_engine.governance.departments.base import (
    Department,
    DepartmentAssessment,
    DepartmentContext,
    ReviewDisposition,
    SupportRequest,
    Workstream,
)
from civ6_belief_engine.governance.departments.coordinator import (
    DepartmentRegistry,
    NationalStrategyCoordinator,
)


def _snapshot() -> GraphSnapshotView:
    return GraphSnapshotView(
        snapshot_id="snapshot:9",
        turn=9,
        player_id=0,
        ready=True,
        source="test",
    )


@dataclass
class _Plugin:
    department: Department
    relevance: float = 0.5
    fail: bool = False

    def match(self, _context: DepartmentContext) -> float:
        return self.relevance

    def assess(self, context: DepartmentContext) -> DepartmentAssessment:
        if self.fail:
            raise RuntimeError("isolated failure")
        return DepartmentAssessment(
            department=self.department,
            snapshot_id=context.snapshot.snapshot_id,
            relevance=self.relevance,
            summary=f"{self.department.value} ready",
            workstreams=(
                Workstream(
                    workstream_id=f"ws:{self.department.value}",
                    department=self.department,
                    objective="observe",
                    priority=10,
                ),
            ),
        )

    def review(self, _context, _outcome) -> ReviewDisposition:
        return ReviewDisposition.CONTINUE


def test_registry_is_pluggable_and_rejects_duplicate_department() -> None:
    registry = DepartmentRegistry((_Plugin(Department.MILITARY),))
    assert [item.department for item in registry.plugins()] == [Department.MILITARY]
    try:
        registry.register(_Plugin(Department.MILITARY))
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate department should be rejected")
    assert registry.unregister(Department.MILITARY) is not None
    assert registry.plugins() == ()


def test_coordinator_isolates_failure_and_reports_missing_plugins() -> None:
    registry = DepartmentRegistry(
        (
            _Plugin(Department.MILITARY),
            _Plugin(Department.SCIENCE, fail=True),
        )
    )
    brief = NationalStrategyCoordinator(registry).run(_snapshot())

    assert len(brief.assessments) == 2
    science = next(
        item for item in brief.assessments if item.department == Department.SCIENCE
    )
    assert science.degraded is True
    assert science.evidence_missing[0].startswith("plugin failure: RuntimeError")
    assert Department.CIVICS in brief.campaign.missing_departments
    assert brief.campaign.ready_workstream_ids == ("ws:military",)


def test_dependencies_fail_closed_until_execution_state_exists() -> None:
    class _Dependent(_Plugin):
        def assess(self, context: DepartmentContext) -> DepartmentAssessment:
            return DepartmentAssessment(
                department=self.department,
                snapshot_id=context.snapshot.snapshot_id,
                relevance=self.relevance,
                summary="dependent",
                workstreams=(
                    Workstream(
                        workstream_id="ws:dependent",
                        department=self.department,
                        objective="wait",
                        priority=20,
                        dependencies=("ws:unknown",),
                    ),
                ),
            )

    brief = NationalStrategyCoordinator(
        DepartmentRegistry((_Dependent(Department.PRODUCTION),))
    ).run(_snapshot())

    assert brief.campaign.ready_workstream_ids == ()
    assert brief.campaign.blocked_workstream_ids == ("ws:dependent",)
    assert "unresolved dependency: ws:unknown" in brief.campaign.blind_spots


def test_cross_department_requests_are_visible_and_fail_closed_when_target_missing() -> None:
    class _Requester(_Plugin):
        def assess(self, context: DepartmentContext) -> DepartmentAssessment:
            return DepartmentAssessment(
                department=self.department,
                snapshot_id=context.snapshot.snapshot_id,
                relevance=self.relevance,
                summary="needs support",
                support_requests=(
                    SupportRequest(
                        requester=self.department,
                        target=Department.ECONOMY,
                        objective="reserve budget",
                        reason="minimum support required",
                    ),
                ),
            )

    brief = NationalStrategyCoordinator(
        DepartmentRegistry((_Requester(Department.MILITARY),))
    ).run(_snapshot())

    assert len(brief.campaign.coordination_links) == 1
    assert brief.campaign.unresolved_support_requests == brief.campaign.coordination_links
    assert "unresolved support target: economy" in brief.campaign.blind_spots


def test_default_registry_contains_all_six_replaceable_departments() -> None:
    from civ6_belief_engine.governance.departments import default_department_registry

    registry = default_department_registry()

    assert tuple(plugin.department for plugin in registry.plugins()) == tuple(Department)


def test_degraded_department_workstream_is_not_marked_ready() -> None:
    class _Degraded(_Plugin):
        def assess(self, context: DepartmentContext) -> DepartmentAssessment:
            return DepartmentAssessment(
                department=self.department,
                snapshot_id=context.snapshot.snapshot_id,
                relevance=self.relevance,
                summary="degraded evidence",
                evidence_missing=("missing evidence",),
                workstreams=(
                    Workstream(
                        workstream_id="ws:degraded",
                        department=self.department,
                        objective="wait for evidence",
                        priority=90,
                    ),
                ),
                degraded=True,
            )

    brief = NationalStrategyCoordinator(
        DepartmentRegistry((_Degraded(Department.DIPLOMACY),))
    ).run(_snapshot())

    assert brief.campaign.ready_workstream_ids == ()
    assert brief.campaign.blocked_workstream_ids == ("ws:degraded",)
    assert "degraded department: diplomacy" in brief.campaign.blind_spots

"""Deterministic, read-only production and city department."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from ..models import Outcome, OutcomeStatus
from .base import (
    Department,
    DepartmentAssessment,
    DepartmentContext,
    ReviewDisposition,
    SupportRequest,
    Workstream,
)


class ProductionDepartment:
    """Assess city production without owning game state or executing actions.

    The department deliberately produces planning signals instead of exact
    action intents.  A national coordinator can combine these signals with
    military, science, civic, economy, and diplomacy departments before the
    single game writer is allowed to act.
    """

    department: Department = Department.PRODUCTION

    _AGENDA_SIGNALS = (
        "production",
        "city",
        "defense",
        "reinforcement",
        "barbarian",
        "生产",
        "城市",
        "防御",
        "增援",
        "蛮族",
    )

    def match(self, context: DepartmentContext) -> float:
        """Return a stable relevance value for the current typed snapshot."""

        snapshot = context.snapshot
        if not snapshot.cities:
            return 0.0

        if self._has_agenda_signal(context) or (
            snapshot.barbarians is not None
            and (
                snapshot.barbarians.camps or snapshot.barbarians.units
            )
        ):
            return 1.0
        if snapshot.barbarians is not None:
            return 0.75
        # City evidence exists, but the optional barbarian query is absent.
        return 0.5

    def assess(self, context: DepartmentContext) -> DepartmentAssessment:
        """Build an immutable assessment from evidence already in ``context``."""

        snapshot = context.snapshot
        relevance = self.match(context)
        cities = self._ordered_cities(snapshot.cities)

        if not cities:
            return DepartmentAssessment(
                department=self.department,
                snapshot_id=snapshot.snapshot_id,
                relevance=relevance,
                summary="生产部门退化：缺少城市生产证据，无法提出生产建议",
                capability_gaps=("city_production_evidence",),
                evidence_missing=(
                    "cities",
                    "city.production",
                    "city.currently_building",
                    "city.production_turns_left",
                    "city.defense_strength",
                ),
                degraded=True,
            )

        key_cities = self._key_cities(cities)
        protected_ids = self._unique(
            f"city:{city.city_id}" for city in key_cities
        )
        facts = [self._city_fact(city) for city in cities]
        facts.append(f"key_city_ids={','.join(protected_ids)}")

        risks: list[str] = []
        low_defense_ids = self._unique(
            f"city:{city.city_id}"
            for city in key_cities
            if city.defense_strength <= 0
        )
        if low_defense_ids:
            risks.append(
                "关键城市防御值为零或未建立防御：" + ",".join(low_defense_ids)
            )

        opportunities = [
            f"city:{city.city_id} has an available production window"
            for city in cities
            if self._production_window_available(city)
        ]

        workstreams = [self._protection_workstream(key_cities)]
        evidence_missing: list[str] = []
        capability_gaps: list[str] = []
        support_requests: tuple[SupportRequest, ...] = ()
        degraded = False
        barbarian_overview = snapshot.barbarians

        own_power = self._own_military_power(snapshot.units)
        facts.append(f"own_military_power={own_power:.1f}")

        if barbarian_overview is None:
            evidence_missing.append("barbarian_overview")
            risks.append("缺少蛮族态势证据，不能把未观测到蛮族视为安全")
            degraded = True
            summary = "生产部门完成城市评估，但蛮族证据缺失，结果已降级"
        else:
            camp_count = len(barbarian_overview.camps)
            unit_count = len(barbarian_overview.units)
            threat_power = self._barbarian_threat_power(barbarian_overview)
            facts.extend(
                (
                    f"barbarian_camps={camp_count}",
                    f"barbarian_units={unit_count}",
                    f"barbarian_threat_power={threat_power:.1f}",
                )
            )
            if camp_count or unit_count:
                if own_power < threat_power:
                    minimum_count = max(
                        1, math.ceil((threat_power - own_power) / 20.0)
                    )
                    reinforcement_city = self._reinforcement_city(cities, key_cities)
                    workstreams.append(
                        self._minimum_reinforcement_workstream(
                            minimum_count=minimum_count,
                            reinforcement_city=reinforcement_city,
                        )
                    )
                    capability_gaps.extend(
                        (
                            "barbarian_minimum_reinforcement",
                            "military_production_coordination",
                        )
                    )
                    support_requests = (
                        SupportRequest(
                            requester=self.department,
                            target=Department.MILITARY,
                            objective="evaluate_minimum_barbarian_reinforcement",
                            reason=(
                                f"已知蛮族威胁力量 {threat_power:.1f} 高于当前可用军力 "
                                f"{own_power:.1f}；先调动现有单位，再补足最少 {minimum_count} 个单位"
                            ),
                        ),
                    )
                    facts.append(f"minimum_reinforcement_count={minimum_count}")
                    risks.append("蛮族威胁超过当前可用军力")
                    summary = "蛮族威胁下提出最小增援，同时保护关键城市生产"
                else:
                    summary = "蛮族已被观测，但当前粗略军力门槛未显示需要增援"
                    facts.append("barbarian_reinforcement_required=false")
            else:
                summary = "未发现已知蛮族威胁，保持关键城市生产队列"
                facts.append("barbarian_reinforcement_required=false")

        return DepartmentAssessment(
            department=self.department,
            snapshot_id=snapshot.snapshot_id,
            relevance=relevance,
            summary=summary,
            facts=self._unique(facts),
            risks=self._unique(risks),
            opportunities=self._unique(opportunities),
            capability_gaps=self._unique(capability_gaps),
            evidence_missing=self._unique(evidence_missing),
            support_requests=support_requests,
            workstreams=tuple(workstreams),
            degraded=degraded,
        )

    def review(self, context: DepartmentContext, outcome: Outcome) -> ReviewDisposition:
        """Review an outcome without changing state or issuing a new action."""

        if not isinstance(outcome, Outcome):
            raise TypeError("outcome must be Outcome")
        if outcome.status in (OutcomeStatus.FAILED, OutcomeStatus.RETRYABLE):
            return ReviewDisposition.REPLAN

        result = outcome.result
        if result.get("needs_replan") is True or result.get("production_queue_invalid") is True:
            return ReviewDisposition.REPLAN
        if any(
            result.get(key) is True
            for key in (
                "workflow_complete",
                "campaign_complete",
                "production_complete",
                "reinforcement_complete",
            )
        ):
            return ReviewDisposition.EXIT
        if self.assess(context).degraded:
            return ReviewDisposition.REPLAN
        return ReviewDisposition.CONTINUE

    @classmethod
    def _has_agenda_signal(cls, context: DepartmentContext) -> bool:
        statements = [*context.agenda]
        statements.extend(goal.statement for goal in context.goals)
        for goal in context.goals:
            statements.extend(goal.tags)
        haystack = " ".join(statements).casefold()
        return any(signal.casefold() in haystack for signal in cls._AGENDA_SIGNALS)

    @staticmethod
    def _ordered_cities(cities: Iterable[Any]) -> tuple[Any, ...]:
        return tuple(sorted(cities, key=lambda city: (city.city_id, city.name)))

    @staticmethod
    def _key_cities(cities: tuple[Any, ...]) -> tuple[Any, ...]:
        count = max(1, (len(cities) + 1) // 2)
        return tuple(
            sorted(
                cities,
                key=lambda city: (
                    -float(city.production),
                    -int(city.defense_strength),
                    city.city_id,
                    city.name,
                ),
            )[:count]
        )

    @staticmethod
    def _city_fact(city: Any) -> str:
        return (
            f"city:{city.city_id} name={city.name} production={float(city.production):.2f} "
            f"currently_building={city.currently_building} "
            f"production_turns_left={city.production_turns_left} "
            f"defense_strength={city.defense_strength}"
        )

    @staticmethod
    def _production_window_available(city: Any) -> bool:
        return city.currently_building in {"", "NONE", "NO_PRODUCTION"} or (
            city.production_turns_left <= 0
        )

    @staticmethod
    def _reinforcement_city(cities: tuple[Any, ...], key_cities: tuple[Any, ...]) -> Any | None:
        protected_ids = {city.city_id for city in key_cities}
        candidates = [city for city in cities if city.city_id not in protected_ids]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda city: (
                float(city.production),
                city.production_turns_left,
                city.city_id,
                city.name,
            ),
        )[0]

    @staticmethod
    def _own_military_power(units: Iterable[Any]) -> float:
        power = 0.0
        for unit in sorted(units, key=ProductionDepartment._unit_sort_key):
            raw_power = max(
                0.0,
                float(unit.combat_strength),
                float(unit.ranged_strength),
            )
            if raw_power <= 0:
                continue
            max_health = float(unit.max_health)
            health_ratio = 1.0 if max_health <= 0 else max(0.0, min(1.0, float(unit.health) / max_health))
            power += raw_power * health_ratio
        return power

    @staticmethod
    def _barbarian_threat_power(overview: Any) -> float:
        power = 20.0 * len(overview.camps)
        for unit in sorted(overview.units, key=ProductionDepartment._unit_sort_key):
            raw_power = max(
                0.0,
                float(unit.combat_strength),
                float(unit.ranged_strength),
            )
            max_health = float(unit.max_hp)
            health_ratio = 1.0 if max_health <= 0 else max(0.0, min(1.0, float(unit.hp) / max_health))
            # A visible barbarian unit with incomplete combat metadata is still
            # a threat; do not let a missing strength field erase it.
            power += max(20.0, raw_power * health_ratio)
        return power

    @staticmethod
    def _unit_sort_key(unit: Any) -> tuple[Any, ...]:
        return (
            unit.unit_id,
            unit.unit_type,
            unit.x,
            unit.y,
            unit.combat_strength,
            unit.ranged_strength,
            getattr(unit, "health", getattr(unit, "hp", 0)),
            getattr(unit, "max_health", getattr(unit, "max_hp", 0)),
        )

    @staticmethod
    def _protection_workstream(key_cities: tuple[Any, ...]) -> Workstream:
        ids = tuple(f"city:{city.city_id}" for city in key_cities)
        labels = ",".join(ids)
        claims = {f"city_production:{city_id}": 1.0 for city_id in ids}
        return Workstream(
            workstream_id="production:protect-key-cities",
            department=Department.PRODUCTION,
            objective="保护关键城市的既有生产队列",
            priority=80,
            resource_claims=claims,
            candidate_actions=(
                f"保留关键城市生产队列：{labels}",
                "仅在明确防御阈值触发时调整关键城市生产",
                "不得把所有城市强制切换为军事生产",
            ),
            exit_conditions=(
                "关键城市生产队列仍有明确目标",
                "关键城市防御风险已被单独评估",
            ),
        )

    @staticmethod
    def _minimum_reinforcement_workstream(
        *, minimum_count: int, reinforcement_city: Any | None
    ) -> Workstream:
        if reinforcement_city is None:
            city_action = "没有可安全分流的非关键城市，优先调动现有军事单位"
            claims: dict[str, float] = {}
        else:
            city_action = (
                f"仅考虑非关键城市 {reinforcement_city.name}(city:{reinforcement_city.city_id}) "
                "在当前生产完成后补充最少军事单位"
            )
            claims = {
                f"city_production:city:{reinforcement_city.city_id}": 1.0
            }
        return Workstream(
            workstream_id="production:minimum-barbarian-reinforcement",
            department=Department.PRODUCTION,
            objective="以最小生产代价补足蛮族防线",
            priority=95,
            resource_claims=claims,
            candidate_actions=(
                "先由军事部门评估并调动现有单位",
                city_action,
                f"最低补充数量：{minimum_count} 个单位",
                "不得无条件中断关键城市生产，也不得要求所有城市造兵",
            ),
            exit_conditions=(
                "蛮族营地已清除或威胁力量不再超过可用军力",
                "关键城市生产保护条件仍然满足",
            ),
        )

    @staticmethod
    def _unique(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for value in values if value))

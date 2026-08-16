"""Deterministic, read-only economic department for the national strategy loop."""

from __future__ import annotations

from math import isfinite
from typing import Any

from ..graph_snapshot import graph_goals, graph_snapshot
from .base import (
    BaseDepartment,
    Department,
    DepartmentAssessment,
    DepartmentContext,
    ReviewDisposition,
    SupportRequest,
    Workstream,
)


_ECONOMIC_KEYWORDS = (
    "budget",
    "buy",
    "econom",
    "faith",
    "gold",
    "maintenance",
    "purchase",
    "resource",
    "upgrade",
    "信仰",
    "经济",
    "购买",
    "资源",
    "维护",
    "升级",
    "黄金",
    "预算",
)
_BARBARIAN_KEYWORDS = ("barbar", "camp", "蛮族", "营地")


def _contains_keyword(values: tuple[str, ...], keywords: tuple[str, ...]) -> bool:
    haystack = " ".join(values).casefold()
    return any(keyword.casefold() in haystack for keyword in keywords)


def _context_text(context: DepartmentContext) -> tuple[str, ...]:
    values = list(context.agenda)
    for goal in graph_goals(context):
        values.append(goal.statement)
        values.extend(goal.tags)
    return tuple(values)


def _barbarian_signal(context: DepartmentContext) -> bool:
    barbarians = graph_snapshot(context).barbarians
    if barbarians is not None and (barbarians.camps or barbarians.units):
        return True
    return _contains_keyword(_context_text(context), _BARBARIAN_KEYWORDS)


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(float(value))
    )


def _non_negative(value: Any) -> bool:
    return _finite(value) and float(value) >= 0


def _format_number(value: float) -> str:
    rendered = f"{float(value):.2f}"
    rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in ("", "-0") else rendered


def _safe_gold_budget(gold: float, gold_per_turn: float, maintenance: float) -> float:
    """Return a reserve-protected budget, ignoring optimistic future income.

    Half of the treasury remains untouched. A deficit is protected for ten
    turns and two turns of maintenance are also retained. This is deliberately
    a budget ceiling for coordination, not an affordability authorization.
    """

    treasury = max(0.0, float(gold))
    protected = treasury * 0.5
    protected += max(0.0, -float(gold_per_turn)) * 10.0
    protected += max(0.0, float(maintenance)) * 2.0
    return round(max(0.0, treasury - protected), 2)


class EconomyDepartment(BaseDepartment):
    """Assess treasury capacity and provide abstract budget support signals.

    The department only consumes an immutable :class:`TurnSnapshot`. It never
    calls external services, mutates state, or chooses a concrete unit/item.
    """

    department = Department.ECONOMY
    completion_flags = ("campaign_complete", "economy_goal_complete")

    def match(self, context: DepartmentContext) -> float:
        """Return deterministic relevance for the current national context."""

        snapshot = graph_snapshot(context)
        if snapshot.overview is None:
            return 0.0

        relevance = 0.40
        if snapshot.resources:
            relevance += 0.15
        if snapshot.overview.num_units == 0 or snapshot.units:
            relevance += 0.15
        if _contains_keyword(_context_text(context), _ECONOMIC_KEYWORDS):
            relevance += 0.20
        if _barbarian_signal(context):
            relevance += 0.10
        if (_finite(snapshot.overview.gold_per_turn) and snapshot.overview.gold_per_turn < 0) or any(
            unit.can_upgrade for unit in snapshot.units
        ):
            relevance += 0.10
        return round(min(1.0, relevance), 6)

    def assess(self, context: DepartmentContext) -> DepartmentAssessment:
        """Build a stable assessment without producing executable actions."""

        snapshot = graph_snapshot(context)
        relevance = self.match(context)
        missing: list[str] = []
        facts: list[str] = []
        risks: list[str] = []
        opportunities: list[str] = []
        capability_gaps: list[str] = []

        overview = snapshot.overview
        if overview is None:
            for field_name in (
                "gold",
                "gold_per_turn",
                "total_maintenance",
                "faith",
            ):
                missing.append(f"overview.{field_name}")
        else:
            overview_fields = (
                ("gold", overview.gold, _non_negative),
                ("gold_per_turn", overview.gold_per_turn, _finite),
                ("total_maintenance", overview.total_maintenance, _non_negative),
                ("faith", overview.faith, _non_negative),
            )
            for field_name, value, validator in overview_fields:
                if not validator(value):
                    missing.append(f"overview.{field_name}")
                else:
                    facts.append(f"{field_name}={_format_number(float(value))}")

            if type(overview.num_units) is not int or overview.num_units < 0:
                missing.append("overview.num_units")
            elif overview.num_units > 0 and not snapshot.units:
                missing.append("units")

        if not snapshot.resources:
            missing.append("resource_stockpiles")
        else:
            for index, resource in enumerate(
                sorted(
                    snapshot.resources,
                    key=lambda item: (
                        item.name.casefold() if isinstance(item.name, str) else "",
                        item.name if isinstance(item.name, str) else "",
                    ),
                )
            ):
                resource_name = resource.name.strip() if isinstance(resource.name, str) else ""
                label = resource_name or f"index:{index}"
                valid = bool(resource_name)
                valid = valid and type(resource.amount) is int and resource.amount >= 0
                valid = valid and type(resource.cap) is int and resource.cap >= 0
                valid = valid and type(resource.per_turn) is int
                valid = valid and type(resource.demand) is int and resource.demand >= 0
                valid = valid and type(resource.imported) is int and resource.imported >= 0
                if not valid:
                    missing.append(f"resource_stockpile.{label}")
                    continue
                facts.append(
                    "resource="
                    f"{resource_name}:amount={int(resource.amount)},"
                    f"cap={int(resource.cap)},per_turn={_format_number(resource.per_turn)},"
                    f"demand={int(resource.demand)},imported={int(resource.imported)}"
                )
                if resource.amount < resource.demand:
                    risks.append(
                        f"资源 {resource_name} 的库存低于当前需求，新增相关支出前需先补足"
                    )
                elif resource.imported > 0 and resource.amount <= resource.demand:
                    risks.append(f"资源 {resource_name} 依赖进口且库存缓冲不足")

        upgrade_candidates: list[tuple[int, float]] = []
        for unit in sorted(snapshot.units, key=lambda item: item.unit_id):
            unit_id = str(unit.unit_id)
            if type(unit.can_upgrade) is not bool:
                missing.append(f"unit_upgrade_status:{unit_id}")
                continue
            if not unit.can_upgrade:
                if not _non_negative(unit.upgrade_cost):
                    missing.append(f"unit_upgrade_cost:{unit_id}")
                continue
            if not _finite(unit.upgrade_cost) or float(unit.upgrade_cost) <= 0:
                missing.append(f"unit_upgrade_cost:{unit_id}")
                continue
            cost = float(unit.upgrade_cost)
            upgrade_candidates.append((unit.unit_id, cost))
            facts.append(f"upgrade_cost:{unit_id}={_format_number(cost)}")

        if not snapshot.units and overview is not None and overview.num_units == 0:
            facts.append("upgrade_costs=none")
        elif snapshot.units and not upgrade_candidates:
            facts.append("upgrade_costs=none currently available")

        missing = list(dict.fromkeys(missing))
        capability_gaps.extend(missing)
        degraded = bool(missing)

        safe_budget = 0.0
        if overview is not None and not any(
            item.startswith("overview.") for item in missing
        ):
            safe_budget = _safe_gold_budget(
                overview.gold,
                overview.gold_per_turn,
                overview.total_maintenance,
            )
            facts.append(f"safe_gold_budget_ceiling={_format_number(safe_budget)}")

            if overview.gold_per_turn < 0:
                turns = int(overview.gold / abs(overview.gold_per_turn)) if overview.gold else 0
                risks.append(
                    f"黄金净流入为负，按当前速度约 {turns} 回合耗尽库存；忽略未来乐观收入"
                )
            if overview.faith > 0:
                opportunities.append(
                    "存在信仰储备；如规则集允许，可作为替代购买资源，使用前需再次核验"
                )

        if upgrade_candidates:
            remaining = safe_budget
            covered_cost = 0.0
            covered_count = 0
            for _, cost in sorted(upgrade_candidates, key=lambda item: (item[1], item[0])):
                if cost <= remaining:
                    remaining = round(remaining - cost, 2)
                    covered_cost += cost
                    covered_count += 1
            if not degraded:
                opportunities.append(
                    f"发现 {len(upgrade_candidates)} 个升级预算需求；保守预算可覆盖 {covered_count} 个，"
                    f"合计不超过 {_format_number(covered_cost)} 黄金"
                )
            else:
                risks.append("存在单位升级成本，但经济证据不完整，不宣称其可负担")
        elif any(item.startswith("unit_upgrade_cost:") for item in missing):
            risks.append("存在单位升级成本，但经济证据不完整，不宣称其可负担")

        barbarian_signal = _barbarian_signal(context)
        support_requests: list[SupportRequest] = []
        if overview is not None and barbarian_signal:
            if degraded:
                reason = "蛮族行动需要经济协作，但当前预算证据不完整；军事部门不得假设有可用资金"
            else:
                reason = (
                    f"经济部门仅提供不超过 {_format_number(safe_budget)} 黄金的保守上限；"
                    "军事部门需先给出最低军力与行动优先级"
                )
            support_requests.append(
                SupportRequest(
                    requester=Department.ECONOMY,
                    target=Department.MILITARY,
                    objective="为蛮族清剿保留升级或购买预算",
                    reason=reason,
                )
            )
            risks.append("蛮族行动会与常规升级/购买竞争有限预算，需要军事与经济联合排序")

        has_budget_signal = bool(upgrade_candidates) or barbarian_signal or _contains_keyword(
            _context_text(context), ("budget", "purchase", "upgrade", "购买", "升级", "预算")
        )
        if not degraded and has_budget_signal and safe_budget <= 0:
            risks.append("当前没有可安全动用的黄金预算，升级或购买只能作为待补充支援")

        workstreams: list[Workstream] = []
        if not degraded and overview is not None and has_budget_signal and safe_budget > 0:
            priority = 80 if barbarian_signal else 60
            workstreams.append(
                Workstream(
                    workstream_id=f"economy:budget:{snapshot.turn}",
                    department=Department.ECONOMY,
                    objective="提供升级或购买的保守预算支援，由国家协调器决定具体分配",
                    priority=priority,
                    resource_claims={"gold": safe_budget},
                    candidate_actions=(
                        "upgrade_budget_support",
                        "purchase_budget_support",
                    ),
                    exit_conditions=(
                        "预算已由国家协调器分配",
                        "分配后重新读取同回合经济快照",
                    ),
                )
            )

        if missing:
            risks.insert(0, "经济评估证据不完整，所有预算上限均按不可确认处理")
        if not risks and not opportunities:
            opportunities.append("当前未发现需要经济部门立即协调的预算压力")

        if overview is None:
            summary = "经济评估退化：缺少总览证据，不提出预算支援"
        elif degraded:
            summary = f"经济评估退化：缺少或无效证据（{', '.join(missing)}）"
        else:
            summary = (
                f"经济评估：黄金 {_format_number(overview.gold)}，"
                f"GPT {_format_number(overview.gold_per_turn)}，"
                f"维护费 {_format_number(overview.total_maintenance)}，"
                f"信仰 {_format_number(overview.faith)}；"
                f"保守预算上限 {_format_number(safe_budget)}"
            )

        return DepartmentAssessment(
            department=Department.ECONOMY,
            snapshot_id=snapshot.snapshot_id,
            relevance=relevance,
            summary=summary,
            facts=tuple(facts),
            risks=tuple(dict.fromkeys(risks)),
            opportunities=tuple(dict.fromkeys(opportunities)),
            capability_gaps=tuple(capability_gaps),
            evidence_missing=tuple(missing),
            support_requests=tuple(support_requests),
            workstreams=tuple(workstreams),
            degraded=degraded,
        )

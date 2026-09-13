"""Read-only military department for the pluggable strategy coordinator.

The department turns one immutable :class:`GraphSnapshotView` into a deterministic
military assessment and may emit an exact proposal. It never accesses the game,
persists state, or executes an action.
"""

from __future__ import annotations

import re
from math import isfinite
from numbers import Real
from typing import Any, ClassVar, Iterable, Protocol

from ...graph import Edge, Node, city_node_id
from ..graph_snapshot import graph_agenda, graph_goals, graph_snapshot
from ..models import (
    ActionIntent,
    BudgetLock,
    EvidenceRequirement,
    Outcome,
    ProbabilityConfidence,
    Proposal,
)
from .base import (
    BaseDepartment,
    Department,
    DepartmentAssessment,
    DepartmentContext,
    ReviewDisposition,
    SupportRequest,
    Workstream,
)


class _UnitView(Protocol):
    unit_id: int
    unit_type: str
    name: str
    x: int
    y: int
    health: Any
    max_health: Any
    moves_remaining: Any
    combat_strength: Any
    ranged_strength: Any
    fortify_turns: int
    can_fortify: bool


class _CampView(Protocol):
    x: int
    y: int
    visibility: str
    distance_to_city: int


class _BarbarianUnitView(Protocol):
    unit_id: int
    unit_type: str
    x: int
    y: int


class MilitaryDepartment(BaseDepartment):
    """Assess military readiness and known barbarian pressure.

    Unit rows are all inspected, but only rows with explicit combat evidence or
    a known combat unit type are counted as combat units.  Inspection is not a
    command to mobilize every unit; a separate home-defense reserve remains a
    hard condition of the military workstream.
    """

    department: ClassVar[Department] = Department.MILITARY

    _AGENDA_SIGNAL = re.compile(
        r"军事|军队|战斗|防御|蛮族|清剿|进攻|守住|保住|生存|存续|安全|"
        r"military|barbarian|combat|defen[cs]e|war|attack|surviv|protect|preserv",
        re.IGNORECASE,
    )
    _NON_COMBAT_MARKERS = (
        "BUILDER",
        "SETTLER",
        "TRADER",
        "CARAVAN",
        "MISSIONARY",
        "APOSTLE",
        "INQUISITOR",
        "SPY",
        "GREAT_PERSON",
        "GREAT_PROPHET",
        "GREAT_WRITER",
        "GREAT_ARTIST",
        "GREAT_MUSICIAN",
        "GREAT_MERCHANT",
        "GREAT_SCIENTIST",
        "GREAT_ENGINEER",
        "MILITARY_ENGINEER",
        "WORKER",
    )
    _COMBAT_MARKERS = (
        "WARRIOR",
        "SWORDSMAN",
        "SPEARMAN",
        "PIKEMAN",
        "ARCHER",
        "SLINGER",
        "CROSSBOWMAN",
        "MUSKETMAN",
        "RIFLEMAN",
        "INFANTRY",
        "ANTI_TANK",
        "MACHINE_GUN",
        "GUNNER",
        "SCOUT",
        "HORSEMAN",
        "KNIGHT",
        "CURASSIER",
        "CAVALRY",
        "TANK",
        "ARTILLERY",
        "CATAPULT",
        "TREBUCHET",
        "BOMBARD",
        "CHARIOT",
        "GALLEY",
        "QUADRIREME",
        "FRIGATE",
        "DESTROYER",
        "SUBMARINE",
        "BATTLESHIP",
        "CARRIER",
        "IRONCLAD",
        "PRIVATEER",
        "FIGHTER",
        "BOMBER",
        "HELICOPTER",
        "JET",
        "NUCLEAR",
        "GIANT_DEATH_ROBOT",
        "CORPS",
        "ARMY",
        "FLEET",
        "NAVAL",
        "MELEE",
        "RANGED",
    )

    def match(self, context: DepartmentContext) -> float:
        """Return deterministic military relevance for the current context."""

        if not isinstance(context, DepartmentContext):
            raise TypeError("context must be DepartmentContext")
        snapshot = graph_snapshot(context)
        if self._has_military_agenda(context):
            return 1.0
        if self._nearby_threats(context):
            return 1.0
        if snapshot.barbarians is not None and (
            snapshot.barbarians.camps or snapshot.barbarians.units
        ):
            return 1.0
        if snapshot.units:
            return 1.0
        if snapshot.barbarians is not None:
            return 0.5
        if snapshot.overview is not None and snapshot.overview.num_units > 0:
            return 0.5
        return 0.0

    def assess(self, context: DepartmentContext) -> DepartmentAssessment:
        """Build a pure, immutable assessment from the supplied snapshot."""

        if not isinstance(context, DepartmentContext):
            raise TypeError("context must be DepartmentContext")
        snapshot = graph_snapshot(context)
        relevance = self.match(context)
        units = self._ordered_units(snapshot.units)
        combat_units = tuple(unit for unit in units if self._is_combat_unit(unit))
        damaged_units = tuple(
            unit for unit in combat_units if self._is_damaged(unit)
        )
        actionable_units = tuple(
            unit for unit in combat_units if self._is_actionable(unit)
        )
        camps, barbarian_units = self._barbarian_rows(snapshot.barbarians)
        barbarian_present = bool(camps or barbarian_units)
        nearby_threats = self._nearby_threats(context)
        threat_present = barbarian_present or bool(nearby_threats)
        graph_current = self._graph_is_current(context)

        evidence_missing = self._missing_evidence(
            snapshot=snapshot,
            units=units,
            combat_units=combat_units,
            threat_present=threat_present,
        )
        if context.graph is not None and not graph_current:
            evidence_missing = self._dedupe(
                (*evidence_missing, "图视图不是当前快照")
            )
        elif context.graph is not None and not snapshot.threat_scan_available:
            evidence_missing = self._dedupe(
                (*evidence_missing, "城市周边敌军扫描")
            )
        facts = self._facts(
            snapshot=snapshot,
            units=units,
            combat_units=combat_units,
            damaged_units=damaged_units,
            actionable_units=actionable_units,
            camps=camps,
            barbarian_units=barbarian_units,
        )
        if context.graph is not None and not graph_current:
            facts = (*facts, "图视图不是当前快照；不能据此判断当前城市威胁",)
        elif context.graph is not None and snapshot.threat_scan_available:
            facts = (
                *facts,
                f"图查询确认城市三格内当前可见敌军: {len(nearby_threats)} 个",
            )
        elif context.graph is not None:
            facts = (*facts, "城市周边敌军扫描不可用；不能据此认定城市安全",)
        risks = self._risks(
            snapshot=snapshot,
            combat_units=combat_units,
            damaged_units=damaged_units,
            camps=camps,
            barbarian_units=barbarian_units,
        )
        if nearby_threats:
            risks = self._dedupe(
                (*risks, "城市周边存在当前可见敌军，防御动作必须绑定精确城市与单位证据")
            )
        opportunities = self._opportunities(
            combat_units=combat_units,
            actionable_units=actionable_units,
            threat_present=threat_present,
        )
        capability_gaps = self._capability_gaps(
            evidence_missing=evidence_missing,
            units=units,
            combat_units=combat_units,
            actionable_units=actionable_units,
            threat_present=threat_present,
        )
        support_requests = self._support_requests(
            threat_present=threat_present,
            camp_count=len(camps),
            barbarian_unit_count=len(barbarian_units),
            nearby_hostile_count=len(nearby_threats),
        )
        workstream = self._workstream(
            threat_present=threat_present,
            degraded=bool(evidence_missing),
        )
        proposals = self._defense_proposals(
            context=context,
            combat_units=combat_units,
            nearby_threats=nearby_threats,
            evidence_missing=evidence_missing,
        )

        if evidence_missing:
            summary = (
                "军事评估退化：证据不足，已采取保守防御假设；"
                + "、".join(evidence_missing)
            )
        elif threat_present:
            summary = (
                f"军事评估：城市三格内有 {len(nearby_threats)} 个当前可见敌军，"
                f"另有 {len(camps)} 个已知蛮族营地和 "
                f"{len(barbarian_units)} 个可见蛮族单位，需协调防御并保留本土兵力"
            )
        else:
            summary = (
                f"军事评估：识别 {len(combat_units)} 个己方战斗单位，"
                "当前没有已知蛮族威胁"
            )

        return DepartmentAssessment(
            department=self.department,
            snapshot_id=snapshot.snapshot_id,
            relevance=relevance,
            summary=summary,
            facts=facts,
            risks=risks,
            opportunities=opportunities,
            capability_gaps=capability_gaps,
            evidence_missing=evidence_missing,
            support_requests=support_requests,
            workstreams=(workstream,),
            proposals=proposals,
            degraded=bool(evidence_missing),
        )

    def review(
        self, context: DepartmentContext, outcome: Outcome
    ) -> ReviewDisposition:
        """Review an outcome without assuming that fog-of-war means safety."""

        precheck = self._review_precheck(context, outcome)
        if precheck is not None:
            return precheck
        snapshot = graph_snapshot(context)
        if self._nearby_threats(context):
            return ReviewDisposition.CONTINUE
        if snapshot.barbarians is None:
            return ReviewDisposition.REPLAN
        if snapshot.barbarians.camps or snapshot.barbarians.units:
            return ReviewDisposition.CONTINUE
        return ReviewDisposition.EXIT

    @staticmethod
    def _has_military_agenda(context: DepartmentContext) -> bool:
        if context.graph is not None:
            text_parts: list[str] = []
            for goal in MilitaryDepartment._active_graph_goals(context):
                attributes = goal.attributes
                text_parts.extend(
                    (
                        str(attributes.get("goal_id") or goal.node_id),
                        str(attributes.get("statement") or ""),
                        *(str(tag) for tag in attributes.get("tags") or ()),
                    )
                )
        else:
            text_parts = list(graph_agenda(context))
            for goal in graph_goals(context):
                text_parts.extend((goal.goal_id, goal.statement, *goal.tags))
        return bool(MilitaryDepartment._AGENDA_SIGNAL.search(" ".join(text_parts)))

    @staticmethod
    def _graph_is_current(context: DepartmentContext) -> bool:
        return bool(
            context.graph is not None
            and context.graph.snapshot_id == context.snapshot.snapshot_id
            and context.graph.turn == context.snapshot.turn
        )

    @classmethod
    def _goal_is_military(cls, goal: Node) -> bool:
        attributes = goal.attributes
        text = " ".join(
            (
                str(attributes.get("goal_id") or goal.node_id),
                str(attributes.get("statement") or ""),
                *(str(tag) for tag in attributes.get("tags") or ()),
            )
        )
        return bool(cls._AGENDA_SIGNAL.search(text))

    @staticmethod
    def _active_graph_goals(context: DepartmentContext) -> tuple[Node, ...]:
        if not MilitaryDepartment._graph_is_current(context):
            return ()
        return context.graph.active_goals()

    @staticmethod
    def _nearby_threats(context: DepartmentContext) -> tuple[Edge, ...]:
        if not MilitaryDepartment._graph_is_current(context):
            return ()
        threats: dict[tuple[str, str, str], Edge] = {}
        for city in graph_snapshot(context).cities:
            city_id = city_node_id(city.x, city.y)
            for edge in context.graph.threats_near_city(city_id, max_distance=3):
                threats[edge.key] = edge
        return tuple(
            sorted(
                threats.values(),
                key=lambda edge: (
                    edge.attributes["distance"],
                    edge.target_id,
                    edge.source_id,
                ),
            )
        )

    @staticmethod
    def _ordered_units(units: Iterable[_UnitView]) -> tuple[_UnitView, ...]:
        return tuple(
            sorted(
                units,
                key=lambda unit: (
                    unit.unit_id,
                    unit.x,
                    unit.y,
                    unit.unit_type,
                    unit.name,
                ),
            )
        )

    @classmethod
    def _defense_proposals(
        cls,
        *,
        context: DepartmentContext,
        combat_units: tuple[_UnitView, ...],
        nearby_threats: tuple[Edge, ...],
        evidence_missing: tuple[str, ...],
    ) -> tuple[Proposal, ...]:
        """Build at most one exact, non-offensive defense proposal."""

        snapshot = graph_snapshot(context)
        if (
            context.graph is None
            or not snapshot.ready
            or not snapshot.threat_scan_available
            or not nearby_threats
            or evidence_missing
        ):
            return ()
        goals = tuple(
            goal
            for goal in cls._active_graph_goals(context)
            if cls._goal_is_military(goal)
        )
        if not goals:
            return ()
        for threat in nearby_threats:
            city = context.graph.node(threat.target_id)
            if city is None:
                continue
            x = city.attributes.get("x")
            y = city.attributes.get("y")
            defenders = tuple(
                unit
                for unit in combat_units
                if unit.x == x
                and unit.y == y
                and unit.fortify_turns == 0
                and unit.can_fortify
                and cls._is_actionable(unit)
            )
            if not defenders:
                continue
            defender = min(defenders, key=lambda unit: unit.unit_id)
            proposal_id = (
                f"military:defend:{snapshot.turn}:"
                f"{threat.target_id}:{defender.unit_id}"
            )
            evidence = EvidenceRequirement(
                requirement_id=f"defense-units:{snapshot.turn}:{defender.unit_id}",
                tool="get_units",
                params={},
                max_age_turns=0,
                required_facts=(f"unit_position:{defender.unit_id}",),
                required_metrics=("observed_unit_count",),
                expected_facts={
                    f"unit_position:{defender.unit_id}": [defender.x, defender.y]
                },
                description="重新确认守军仍存在后再执行精确防御动作",
            )
            intent = ActionIntent(
                intent_id=f"intent:fortify:{snapshot.turn}:{defender.unit_id}",
                tool="unit_action",
                arguments={"unit_id": defender.unit_id, "action": "fortify"},
                proposal_id=proposal_id,
                evidence_requirements=(evidence,),
                allowed_turn=snapshot.turn,
            )
            return (
                Proposal(
                    proposal_id=proposal_id,
                    department=cls.department.value,
                    summary=(
                        f"让单位 {defender.unit_id} 在受威胁城市 "
                        f"{threat.target_id} 原地设防"
                    ),
                    goal_ids=(str(goals[0].attributes["goal_id"]),),
                    success=ProbabilityConfidence(probability=0.9, confidence=0.8),
                    priority=80,
                    hard_constraints={
                        "current_hostile_threat": True,
                        "defender_on_city_center": True,
                        "same_turn_graph": True,
                    },
                    budget_locks=(
                        BudgetLock(
                            resource="unit_action",
                            amount=1,
                            scope=f"unit:{defender.unit_id}",
                            exclusive=True,
                            reason="同一守军本回合只能执行一个治理动作",
                        ),
                    ),
                    benefits={"city_defense": 1.0},
                    costs={"unit_turn": 1.0},
                    opportunity_cost=1.0,
                    failure_cost=0.2,
                    action_intents=(intent,),
                    expires_turn=snapshot.turn,
                ),
            )
        return ()

    @staticmethod
    def _barbarian_rows(
        barbarians: Any,
    ) -> tuple[tuple[_CampView, ...], tuple[_BarbarianUnitView, ...]]:
        if barbarians is None:
            return (), ()
        camps = tuple(
            sorted(
                barbarians.camps,
                key=lambda camp: (camp.x, camp.y, camp.visibility, camp.distance_to_city),
            )
        )
        units = tuple(
            sorted(
                barbarians.units,
                key=lambda unit: (
                    unit.unit_id,
                    unit.x,
                    unit.y,
                    unit.unit_type,
                ),
            )
        )
        return camps, units

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, Real):
            return None
        numeric = float(value)
        return numeric if isfinite(numeric) else None

    @classmethod
    def _is_combat_unit(cls, unit: _UnitView) -> bool:
        combat_strength = cls._number(unit.combat_strength)
        ranged_strength = cls._number(unit.ranged_strength)
        if (combat_strength is not None and combat_strength > 0) or (
            ranged_strength is not None and ranged_strength > 0
        ):
            return True
        label = f"{unit.unit_type} {unit.name}".upper()
        if any(marker in label for marker in cls._NON_COMBAT_MARKERS):
            return False
        return any(marker in label for marker in cls._COMBAT_MARKERS)

    @classmethod
    def _is_damaged(cls, unit: _UnitView) -> bool:
        health = cls._number(unit.health)
        max_health = cls._number(unit.max_health)
        return (
            health is not None
            and max_health is not None
            and max_health > 0
            and 0 <= health < max_health
        )

    @classmethod
    def _is_actionable(cls, unit: _UnitView) -> bool:
        moves_remaining = cls._number(unit.moves_remaining)
        return moves_remaining is not None and moves_remaining > 0

    @classmethod
    def _missing_evidence(
        cls,
        *,
        snapshot: Any,
        units: tuple[_UnitView, ...],
        combat_units: tuple[_UnitView, ...],
        threat_present: bool,
    ) -> tuple[str, ...]:
        missing: list[str] = []
        if not units and (
            snapshot.overview is None or snapshot.overview.num_units > 0
        ):
            missing.append("己方单位明细")
        if snapshot.barbarians is None:
            missing.append("蛮族情报（已知营地与可见单位）")
        if threat_present and not snapshot.cities:
            missing.append("城市防御与驻防信息")
        if combat_units and any(
            cls._number(unit.health) is None
            or cls._number(unit.max_health) is None
            for unit in combat_units
        ):
            missing.append("战斗单位生命值")
        if combat_units and any(
            cls._number(unit.moves_remaining) is None for unit in combat_units
        ):
            missing.append("战斗单位行动点")
        return cls._dedupe(missing)

    @classmethod
    def _facts(
        cls,
        *,
        snapshot: Any,
        units: tuple[_UnitView, ...],
        combat_units: tuple[_UnitView, ...],
        damaged_units: tuple[_UnitView, ...],
        actionable_units: tuple[_UnitView, ...],
        camps: tuple[_CampView, ...],
        barbarian_units: tuple[_BarbarianUnitView, ...],
    ) -> tuple[str, ...]:
        facts = [
            f"已评估己方单位总数: {len(units)}；全体单位仅用于筛选，不自动成为投入对象",
            f"己方战斗单位: {len(combat_units)} 个（{cls._unit_ids(combat_units)}）",
            f"受伤战斗单位: {len(damaged_units)} 个（{cls._unit_ids(damaged_units)}）",
            f"当前可行动战斗单位: {len(actionable_units)} 个（{cls._unit_ids(actionable_units)}）",
        ]
        if snapshot.barbarians is None:
            facts.append("蛮族情报未提供；不能据此认定迷雾区没有蛮族")
        elif camps or barbarian_units:
            facts.append(
                f"已知蛮族营地: {len(camps)} 个（{cls._camp_positions(camps)}）"
            )
            facts.append(
                "当前可见蛮族单位: "
                f"{len(barbarian_units)} 个（{cls._barbarian_unit_ids(barbarian_units)}）"
            )
        else:
            facts.append(
                "本轮已知蛮族营地和可见单位均为 0；不代表迷雾区没有蛮族"
            )
        return tuple(facts)

    @classmethod
    def _risks(
        cls,
        *,
        snapshot: Any,
        combat_units: tuple[_UnitView, ...],
        damaged_units: tuple[_UnitView, ...],
        camps: tuple[_CampView, ...],
        barbarian_units: tuple[_BarbarianUnitView, ...],
    ) -> tuple[str, ...]:
        risks = ["进攻编组不得抽空城市周边防御，必须保留本土防御余量"]
        if camps or barbarian_units:
            risks.append("蛮族情报受可见性限制，未发现营地不能等同于不存在")
        if damaged_units:
            risks.append("受伤战斗单位不应在未评估风险前承担高风险交战")
        if (camps or barbarian_units) and not snapshot.cities:
            risks.append("缺少城市防御证据，当前不能验证本土防御余量")
        if combat_units and not damaged_units:
            risks.append("健康状态不是战术胜率；仍需结合位置、兵种和敌方力量核验")
        return cls._dedupe(risks)

    @classmethod
    def _opportunities(
        cls,
        *,
        combat_units: tuple[_UnitView, ...],
        actionable_units: tuple[_UnitView, ...],
        threat_present: bool,
    ) -> tuple[str, ...]:
        if threat_present:
            return (
                "从当前可行动战斗单位中筛选满足防御余量的响应编组",
                "将生产、经济和市政支援纳入同一威胁响应方案",
            )
        if combat_units and actionable_units:
            return ("利用可行动战斗单位维护防御态势，并持续核对蛮族情报",)
        return ("先补齐军事态势证据，再决定是否需要投入战斗力量",)

    @classmethod
    def _capability_gaps(
        cls,
        *,
        evidence_missing: tuple[str, ...],
        units: tuple[_UnitView, ...],
        combat_units: tuple[_UnitView, ...],
        actionable_units: tuple[_UnitView, ...],
        threat_present: bool,
    ) -> tuple[str, ...]:
        gaps = list(evidence_missing)
        if units and not combat_units:
            gaps.append("当前单位清单中未识别到具备战斗证据的单位")
        if threat_present and not combat_units:
            gaps.append("尚无已识别己方战斗单位，不能直接组织威胁响应")
        elif threat_present and not actionable_units:
            gaps.append("当前没有可行动己方战斗单位，需先恢复或补充兵力")
        return cls._dedupe(gaps)

    @staticmethod
    def _support_requests(
        *,
        threat_present: bool,
        camp_count: int,
        barbarian_unit_count: int,
        nearby_hostile_count: int,
    ) -> tuple[SupportRequest, ...]:
        if not threat_present:
            return ()
        threat = (
            f"城市三格内 {nearby_hostile_count} 个当前可见敌军、"
            f"已知 {camp_count} 个营地、{barbarian_unit_count} 个可见蛮族单位"
        )
        return (
            SupportRequest(
                requester=Department.MILITARY,
                target=Department.PRODUCTION,
                objective="补充或升级应对当前威胁所需的战斗力量",
                reason=f"军事评估发现{threat}，需要评估生产补充方案",
            ),
            SupportRequest(
                requester=Department.MILITARY,
                target=Department.ECONOMY,
                objective="评估战斗力量的金币、升级和维护负担",
                reason=f"军事评估发现{threat}，需要确认经济承受能力",
            ),
            SupportRequest(
                requester=Department.MILITARY,
                target=Department.CIVICS,
                objective="评估军事政策与市政路径对防御和清剿的支持",
                reason=f"军事评估发现{threat}，需要核对可用市政支持",
            ),
        )

    @staticmethod
    def _workstream(*, threat_present: bool, degraded: bool) -> Workstream:
        if threat_present:
            objective = "处理已知城市周边威胁，同时保留本土防御"
            priority = 80
            candidate_actions = (
                "评估全部己方战斗单位的伤势、行动点、位置和防御职责",
                "仅从满足本土防御余量的单位中筛选清剿编组",
                "协调生产、经济和市政支援后，再决定投入或补充战斗力量",
            )
            exit_conditions = (
                "已知营地与当前可见蛮族单位清零，并完成一次新情报确认",
                "城市周边仍保留可验证的本土防御余量",
            )
            workstream_id = "military:threat-response"
        elif degraded:
            objective = "补齐军事态势证据并维持本土防御"
            priority = 60
            candidate_actions = (
                "补齐己方单位和蛮族情报后重新评估军事态势",
                "证据不足时不进行进攻性兵力投入",
            )
            exit_conditions = ("完成同一回合的己方单位与蛮族情报采集",)
            workstream_id = "military:evidence-recovery"
        else:
            objective = "维持本土防御并持续核对已知军事态势"
            priority = 40
            candidate_actions = (
                "持续评估全部己方战斗单位，但只筛选承担明确防御职责的单位",
                "根据新出现的蛮族情报重新生成跨部门响应信号",
            )
            exit_conditions = ("军事态势已完成本轮核对",)
            workstream_id = "military:readiness"
        return Workstream(
            workstream_id=workstream_id,
            department=Department.MILITARY,
            objective=objective,
            priority=priority,
            resource_claims={"home_defense_reserve": 1.0},
            candidate_actions=candidate_actions,
            exit_conditions=exit_conditions,
        )

    @staticmethod
    def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
        result: list[str] = []
        for value in values:
            if value not in result:
                result.append(value)
        return tuple(result)

    @staticmethod
    def _joined(values: Iterable[str]) -> str:
        """Comma-join, or ``无`` when there is nothing.

        Three helpers encoded this convention separately (unit ids, barbarian
        unit ids, camp positions); the empty case is the part worth sharing.
        """

        materialized = tuple(values)
        return ", ".join(materialized) if materialized else "无"

    @classmethod
    def _unit_ids(cls, units: Iterable[_UnitView]) -> str:
        return cls._joined(str(unit.unit_id) for unit in units)

    @classmethod
    def _camp_positions(cls, camps: Iterable[_CampView]) -> str:
        return cls._joined(f"({camp.x},{camp.y})" for camp in camps)

    @classmethod
    def _barbarian_unit_ids(cls, units: Iterable[_BarbarianUnitView]) -> str:
        return cls._joined(str(unit.unit_id) for unit in units)

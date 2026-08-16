"""Automatic belief/prediction derivation from normalized observations.

Beliefs and predictions only existed when the agent explicitly called the
upsert tools — local audits show ~0.8% of decisions referenced any belief.
This module flips the default: every successful query observation is offered
to a registry of derivation rules, and matching rules create/update/retire
beliefs and predictions with evidence references, tags, and resolution
triggers. The agent's explicit calls remain a supplement, not the source.

Design rules honored here:

- Facts stay facts. A rule only emits a *belief* where there is genuine
  inference (threat, intent) and a *prediction* where a claim is falsifiable
  (timing, damage, race outcome). Deterministic values are never dressed up
  as beliefs.
- Idempotency. Entity ids are stable per subject; re-observation updates
  only changed fields (``BeliefEngine.update`` skips no-op patches), so
  repeated queries do not spam the journal.
- Everything is tagged ``derived`` so the coverage audit can separate
  system-generated beliefs from agent self-reports.
- Rules return ops and never touch the engine's write path directly; the
  engine applies ops through its own validated methods, one failure must
  not break recording.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

DERIVED_TAG = "derived"


from .ids import slugify as _slug


# ---------------------------------------------------------------------------
# Rule context and operations
# ---------------------------------------------------------------------------


@dataclass
class RuleContext:
    """Everything a rule may read. The engine applies writes, not rules."""

    engine: Any  # BeliefEngine; loosely typed to avoid an import cycle
    tool: str
    facts: dict[str, Any]
    metrics: dict[str, Any]
    turn: int
    observation_id: str

    def active(self, entity_type: str, id_prefix: str = "") -> list[dict[str, Any]]:
        return [
            entity
            for entity in self.engine.list(entity_type, status="active")
            if entity["id"].startswith(id_prefix)
        ]


@dataclass
class CreateBelief:
    entity_id: str
    payload: dict[str, Any]

    def apply(self, engine: Any, *, turn: int) -> None:
        existing = engine.get("belief", self.entity_id)
        if existing is not None and existing.get("status") == "archived":
            # Resurrect (e.g. a camp re-seen after a fog gap) instead of
            # tripping create()'s exists check.
            patch = {
                key: value
                for key, value in self.payload.items()
                if existing.get(key) != value and key != "first_seen_turn"
            }
            patch["status"] = "active"
            engine.update("belief", self.entity_id, patch, turn=turn)
            return
        if existing is not None and existing.get("status") != "deleted":
            patch = {
                key: value
                for key, value in self.payload.items()
                if existing.get(key) != value and key != "first_seen_turn"
            }
            if patch:
                engine.update("belief", self.entity_id, patch, turn=turn)
            return
        engine.create("belief", self.payload, turn=turn, entity_id=self.entity_id)


@dataclass
class UpdateEntity:
    entity_type: str
    entity_id: str
    patch: dict[str, Any]

    def apply(self, engine: Any, *, turn: int) -> None:
        engine.update(self.entity_type, self.entity_id, self.patch, turn=turn)


@dataclass
class ArchiveEntity:
    entity_type: str
    entity_id: str
    note: str

    def apply(self, engine: Any, *, turn: int) -> None:
        engine.update(
            self.entity_type,
            self.entity_id,
            {"status": "archived", "resolution": self.note},
            turn=turn,
        )


@dataclass
class CreatePrediction:
    entity_id: str
    payload: dict[str, Any]

    def apply(self, engine: Any, *, turn: int) -> None:
        existing = engine.get("prediction", self.entity_id)
        if existing is not None and existing.get("status") == "archived":
            patch = {
                key: value
                for key, value in self.payload.items()
                if existing.get(key) != value
                and key not in {"probability", "claimed_turn"}
            }
            patch["status"] = "active"
            engine.update("prediction", self.entity_id, patch, turn=turn)
            return
        if existing is not None and existing.get("status") != "deleted":
            patch = {
                key: value
                for key, value in self.payload.items()
                if existing.get(key) != value
                and key not in {"probability", "claimed_turn"}
            }
            if patch:
                engine.update("prediction", self.entity_id, patch, turn=turn)
            return
        engine.create("prediction", self.payload, turn=turn, entity_id=self.entity_id)


@dataclass
class ResolvePredictionOp:
    entity_id: str
    outcome: bool
    actual: Any

    def apply(self, engine: Any, *, turn: int) -> None:
        engine.resolve_prediction(
            self.entity_id,
            outcome=self.outcome,
            actual=self.actual,
            turn=turn,
            source="derived",
        )


Op = CreateBelief | UpdateEntity | ArchiveEntity | CreatePrediction | ResolvePredictionOp


# ---------------------------------------------------------------------------
# Rule: barbarian camp threats (belief + retire)
# ---------------------------------------------------------------------------

_CAMP_UNSEEN_RETIRE_TURNS = 5


def _camp_threat_belief_payload(camp: dict[str, Any], ctx: RuleContext) -> dict[str, Any]:
    distance = camp.get("distance_to_city")
    priority = camp.get("priority") or "WATCH"
    if distance is None:
        probability = 0.5
    elif distance <= 5:
        probability = 0.9
    elif distance <= 10:
        probability = 0.7
    else:
        probability = 0.5
    x, y = camp["x"], camp["y"]
    distance_text = (
        f"{distance} tiles from nearest city" if distance is not None else "unknown city distance"
    )
    return {
        "statement": f"Barbarian camp at ({x},{y}) threatens our cities: {distance_text}.",
        "category": "military",
        "probability": probability,
        "confidence": 0.8,
        "evidence_ids": [ctx.observation_id],
        "falsifiers": [
            "camp tile cleared by our military unit",
            f"camp absent from { _CAMP_UNSEEN_RETIRE_TURNS } consecutive barbarian overviews",
        ],
        "tags": ["automatic", DERIVED_TAG, "military", "barbarian"],
        "camp_x": x,
        "camp_y": y,
        "camp_priority": priority,
        "distance_to_city": distance,
        "first_seen_turn": ctx.turn,
        "last_seen_turn": ctx.turn,
    }


def _camp_threats(ctx: RuleContext) -> Iterator[Op]:
    camps = ctx.facts.get("barbarian_camps") or []
    observed_ids: set[str] = set()
    for camp in camps:
        entity_id = f"auto:belief:camp_threat:{camp['x']}_{camp['y']}"
        observed_ids.add(entity_id)
        existing = ctx.engine.get("belief", entity_id)
        if existing is not None and existing.get("status") == "active":
            patch: dict[str, Any] = {}
            if existing.get("distance_to_city") != camp.get("distance_to_city"):
                patch["distance_to_city"] = camp.get("distance_to_city")
            if existing.get("camp_priority") != camp.get("priority"):
                patch["camp_priority"] = camp.get("priority")
            if existing.get("last_seen_turn") != ctx.turn:
                patch["last_seen_turn"] = ctx.turn
                evidence = list(existing.get("evidence_ids") or [])
                if ctx.observation_id not in evidence:
                    evidence.append(ctx.observation_id)
                patch["evidence_ids"] = evidence[-10:]
            if patch:
                yield UpdateEntity("belief", entity_id, patch)
        else:
            yield CreateBelief(entity_id, _camp_threat_belief_payload(camp, ctx))

    # Absence from an overview is not proof of clearing (fog), so retire only
    # after several consecutive overviews have not seen the camp.
    for belief in ctx.active("belief", "auto:belief:camp_threat:"):
        if belief["id"] in observed_ids:
            continue
        unseen = ctx.turn - int(belief.get("last_seen_turn") or ctx.turn)
        if unseen >= _CAMP_UNSEEN_RETIRE_TURNS:
            yield ArchiveEntity(
                "belief",
                belief["id"],
                f"camp not observed for {unseen} turns (cleared or lost to fog)",
            )


# ---------------------------------------------------------------------------
# Rule: research/civic completion timing (prediction + precise resolution)
# ---------------------------------------------------------------------------


def _timing_prediction(
    ctx: RuleContext,
    *,
    kind: str,  # "tech" | "civic"
    subject: str,
    turns_remaining: int,
    completed_metric: str,
    completed_now: int | None,
) -> Iterator[Op]:
    entity_id = f"auto:pred:{kind}:{_slug(subject)}"
    predicted_turn = ctx.turn + max(1, int(turns_remaining))
    payload = {
        "statement": f"{subject} ({kind}) completes by T{predicted_turn}.",
        "probability": 0.85,
        "confidence": 0.8,
        "deadline_turn": predicted_turn + 2,
        "subject": subject,
        "subject_kind": kind,
        "predicted_turn": predicted_turn,
        "claimed_turn": ctx.turn,
        "completed_at_claim": completed_now,
        "evidence_ids": [ctx.observation_id],
        "tags": ["automatic", DERIVED_TAG, "science" if kind == "tech" else "civics"],
    }
    if completed_now is not None:
        # Safety net: review() resolves True as soon as any completion is
        # recorded. The precise rule-side resolution below fires first when
        # evidence arrives, so this only catches what the rule missed.
        payload["evaluation"] = {
            "metric": completed_metric,
            "operator": ">=",
            "value": completed_now + 1,
        }
    yield CreatePrediction(entity_id, payload)

    # Precise resolution: a subject that is no longer being researched either
    # completed (counter moved) or was switched away from (deadline passed).
    current_subject = {"tech": ctx.metrics.get("research.current"), "civic": ctx.metrics.get("civic.current")}
    completed_key = {
        "tech": "research.completed_techs",
        "civic": "civics.completed_civics",
    }
    for prediction in ctx.active("prediction", f"auto:pred:{kind}:"):
        subject_p = prediction.get("subject")
        if subject_p in (None, "", current_subject[kind]):
            continue
        completed_now_typed = ctx.metrics.get(completed_key[kind])
        claimed = prediction.get("completed_at_claim")
        if (
            isinstance(completed_now_typed, int)
            and isinstance(claimed, int)
            and completed_now_typed > claimed
        ):
            yield ResolvePredictionOp(
                prediction["id"],
                outcome=True,
                actual={
                    "completed_by_turn": ctx.turn,
                    "predicted_turn": prediction.get("predicted_turn"),
                },
            )
        elif ctx.turn > int(prediction.get("deadline_turn") or 0):
            yield ResolvePredictionOp(
                prediction["id"],
                outcome=False,
                actual="switched away before completion or stalled past deadline",
            )


def _research_timing(ctx: RuleContext) -> Iterator[Op]:
    if ctx.tool != "get_tech_civics":
        return
    research = ctx.metrics.get("research.current")
    if isinstance(research, str) and research.strip() not in ("", "None"):
        turns = ctx.metrics.get("research.turns_remaining")
        if isinstance(turns, (int, float)):
            yield from _timing_prediction(
                ctx,
                kind="tech",
                subject=research.strip(),
                turns_remaining=int(turns),
                completed_metric="research.completed_techs",
                completed_now=_as_int(ctx.metrics.get("research.completed_techs")),
            )
    civic = ctx.metrics.get("civic.current")
    if isinstance(civic, str) and civic.strip() not in ("", "None"):
        turns = ctx.metrics.get("civic.turns_remaining")
        if isinstance(turns, (int, float)):
            yield from _timing_prediction(
                ctx,
                kind="civic",
                subject=civic.strip(),
                turns_remaining=int(turns),
                completed_metric="civics.completed_civics",
                completed_now=_as_int(ctx.metrics.get("civics.completed_civics")),
            )


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


# ---------------------------------------------------------------------------
# Rule: victory race ETA (rate-based prediction; review() resolves arrival)
# ---------------------------------------------------------------------------

_VICTORY_MIN_RATIO = 0.15
_VICTORY_DEADLINE_SLACK = 20


def _victory_race(ctx: RuleContext) -> Iterator[Op]:
    if ctx.tool != "get_victory_progress":
        return
    vp_entries: list[tuple[float, str, int, int]] = []  # ratio, metric key, vp, target
    for key, value in ctx.metrics.items():
        if not key.startswith("victory.") or not key.endswith("_vp"):
            continue
        target = ctx.metrics.get(f"{key}_target")
        vp = _as_int(value)
        target_i = _as_int(target)
        if vp is None or target_i is None or target_i <= 0 or vp <= 0:
            continue
        vp_entries.append((vp / target_i, key, vp, target_i))
    vp_entries.sort(reverse=True)

    for ratio, key, vp, target in vp_entries:
        if ratio < _VICTORY_MIN_RATIO:
            continue
        parts = key.split(".")
        slug = parts[1] if len(parts) >= 3 else "world"
        section = (
            parts[2][:-3]
            if len(parts) >= 3 and parts[2].endswith("_vp")
            else "vp"
        )
        entity_id = f"auto:pred:victory:{slug}:{section}"
        existing = ctx.engine.get("prediction", entity_id)
        if existing is not None and existing.get("status") == "active":
            last_vp = _as_int(existing.get("last_vp"))
            last_turn = _as_int(existing.get("last_turn"))
            rate = (
                (vp - last_vp) / (ctx.turn - last_turn)
                if isinstance(last_vp, int) and isinstance(last_turn, int) and ctx.turn > last_turn and vp > last_vp
                else None
            )
            predicted_turn = (
                ctx.turn + math.ceil((target - vp) / rate) if rate and rate > 0 else existing.get("predicted_turn")
            )
            patch: dict[str, Any] = {"last_vp": vp, "last_turn": ctx.turn}
            if predicted_turn != existing.get("predicted_turn"):
                patch["predicted_turn"] = predicted_turn
                patch["statement"] = f"{slug} reaches {target} VP ({section}) by T{predicted_turn}."
            new_deadline = min(
                (predicted_turn if isinstance(predicted_turn, int) else ctx.turn + 60) + _VICTORY_DEADLINE_SLACK,
                ctx.turn + 200,
            )
            if new_deadline != existing.get("deadline_turn"):
                patch["deadline_turn"] = new_deadline
            yield UpdateEntity("prediction", entity_id, patch)
            if vp >= target:
                yield ResolvePredictionOp(
                    entity_id,
                    outcome=True,
                    actual={"vp": vp, "turn": ctx.turn},
                )
            continue

        predicted_turn = None
        statement = f"{slug} is progressing toward {target} VP ({section})."
        yield CreatePrediction(
            entity_id,
            {
                "statement": statement,
                "probability": 0.6,
                "confidence": 0.6,
                "deadline_turn": ctx.turn + 150,
                "subject": f"{slug}:{section}",
                "predicted_turn": predicted_turn,
                "last_vp": vp,
                "last_turn": ctx.turn,
                "vp_target": target,
                "evidence_ids": [ctx.observation_id],
                # review() resolves True on arrival and False at deadline —
                # this rule only maintains the ETA.
                "evaluation": {"metric": key, "operator": ">=", "value": target},
                "tags": ["automatic", DERIVED_TAG, "victory", section],
            },
        )


# ---------------------------------------------------------------------------
# Rule: combat damage expectation (prediction, resolved by the next estimate)
# ---------------------------------------------------------------------------

_COMBAT_DAMAGE_TOLERANCE = 0.25
_COMBAT_MIN_TOLERANCE = 3
_COMBAT_PREDICTION_WINDOW = 3


def _combat_damage(ctx: RuleContext) -> Iterator[Op]:
    if ctx.tool != "get_combat_estimate":
        return
    matchup = ctx.facts.get("matchup")
    expected = ctx.metrics.get("combat.expected_damage_to_defender")
    defender_hp = _as_int(ctx.metrics.get("combat.defender_hp"))
    if not isinstance(matchup, dict) or not isinstance(expected, (int, float)):
        return
    defender = str(matchup.get("defender_type") or "unknown")
    expected_i = int(expected)

    # Any fresh estimate reveals this defender type's HP: resolve active
    # predictions whose baseline HP has dropped (the attack happened).
    for prediction in ctx.active("prediction", "auto:pred:combat:"):
        if prediction.get("subject") != defender:
            continue
        baseline_hp = _as_int(prediction.get("hp_at_estimate"))
        predicted_damage = _as_int(prediction.get("expected_damage"))
        if baseline_hp is None or predicted_damage is None or defender_hp is None:
            continue
        actual_damage = baseline_hp - defender_hp
        if actual_damage <= 0:
            continue
        tolerance = max(_COMBAT_MIN_TOLERANCE, int(predicted_damage * _COMBAT_DAMAGE_TOLERANCE))
        yield ResolvePredictionOp(
            prediction["id"],
            outcome=abs(actual_damage - predicted_damage) <= tolerance,
            actual={"damage": actual_damage, "expected": predicted_damage},
        )

    entity_id = f"auto:pred:combat:{_slug(defender)}:{ctx.turn}"
    existing = ctx.engine.get("prediction", entity_id)
    if existing is not None and existing.get("status") == "active":
        if defender_hp is not None:
            yield UpdateEntity(
                "prediction",
                entity_id,
                {
                    "hp_at_estimate": defender_hp,
                    "expected_damage": expected_i,
                    "evidence_ids": [ctx.observation_id],
                },
            )
        return
    yield CreatePrediction(
        entity_id,
        {
            "statement": f"Attack on {defender} (HP:{defender_hp}) deals ~{expected_i} damage.",
            "probability": 0.8,
            "confidence": 0.75,
            "deadline_turn": ctx.turn + _COMBAT_PREDICTION_WINDOW,
            "subject": defender,
            "predicted_turn": ctx.turn + _COMBAT_PREDICTION_WINDOW,
            "hp_at_estimate": defender_hp,
            "expected_damage": expected_i,
            "evidence_ids": [ctx.observation_id],
            "tags": ["automatic", DERIVED_TAG, "military", "combat"],
        },
    )


# ---------------------------------------------------------------------------
# Rule: rival military superiority / at-war threat (belief)
# ---------------------------------------------------------------------------

_RIVAL_MILITARY_ALERT_RATIO = 2.0
_RIVAL_MILITARY_RETIRE_RATIO = 1.5
_RIVAL_UNSEEN_RETIRE_TURNS = 10


def _rival_military_threats(ctx: RuleContext) -> Iterator[Op]:
    """Belief per rival whose military is at least 2x ours or that is at war."""
    if ctx.tool != "get_diplomacy":
        return
    rivals = ctx.facts.get("rivals") or {}
    our_military = ctx.metrics.get("our_military")
    observed_ids: set[str] = set()
    for player_id, info in rivals.items():
        military = info.get("military")
        if not isinstance(military, int) or military <= 0:
            continue
        at_war = bool(info.get("at_war"))
        ratio = (
            military / our_military
            if isinstance(our_military, int) and our_military > 0
            else None
        )
        if not at_war and (ratio is None or ratio < _RIVAL_MILITARY_ALERT_RATIO):
            continue
        player_n = player_id.removeprefix("player_")
        entity_id = f"auto:belief:rival_threat:{player_n}"
        observed_ids.add(entity_id)
        if at_war and ratio is not None and ratio >= _RIVAL_MILITARY_ALERT_RATIO:
            probability = 0.85
        elif at_war:
            probability = 0.7
        else:
            probability = 0.6
        ratio_text = f"{ratio:.1f}x" if ratio is not None else "unknown ratio"
        state_text = "and at war" if at_war else "without war"
        statement = (
            f"Player {player_n} ({info.get('civilization')}) military {military} "
            f"is {ratio_text} ours, {state_text}."
        )
        yield CreateBelief(
            entity_id,
            {
                "statement": statement,
                "category": "military",
                "probability": probability,
                "confidence": 0.7,
                "evidence_ids": [ctx.observation_id],
                "falsifiers": [
                    "rival military drops below 1.5x ours",
                    "war ends",
                ],
                "tags": ["automatic", DERIVED_TAG, "military", "diplomacy"],
                "player_id": player_n,
                "civilization": info.get("civilization"),
                "rival_military": military,
                "our_military": our_military,
                "military_ratio": ratio,
                "at_war": at_war,
                "first_seen_turn": ctx.turn,
                "last_seen_turn": ctx.turn,
            },
        )

    # Retire: threat resolved (below 1.5x and at peace) or the rival dropped
    # out of the diplomacy list entirely (defeated / never met again).
    for belief in ctx.active("belief", "auto:belief:rival_threat:"):
        if belief["id"] in observed_ids:
            continue
        player_n = belief["id"].rsplit(":", 1)[-1]
        rival = rivals.get(f"player_{player_n}")
        if rival is None:
            unseen = ctx.turn - int(belief.get("last_seen_turn") or ctx.turn)
            if unseen >= _RIVAL_UNSEEN_RETIRE_TURNS:
                yield ArchiveEntity(
                    "belief",
                    belief["id"],
                    f"player {player_n} absent from diplomacy list for {unseen} turns",
                )
            continue
        military = rival.get("military")
        if not isinstance(military, int):
            continue
        at_war = bool(rival.get("at_war"))
        ratio = (
            military / our_military
            if isinstance(our_military, int) and our_military > 0
            else None
        )
        if not at_war and (ratio is None or ratio < _RIVAL_MILITARY_RETIRE_RATIO):
            yield ArchiveEntity(
                "belief",
                belief["id"],
                f"threat resolved: rival {military} vs our {our_military}",
            )


# ---------------------------------------------------------------------------
# Rule: great people race pressure (belief, retired when we lead)
# ---------------------------------------------------------------------------

_GP_RACE_MAX_GAP_RATIO = 0.5


def _great_people_race(ctx: RuleContext) -> Iterator[Op]:
    """Belief per great-person class where a rival leads us by a small gap.

    A large gap (leader >50% ahead of their own points) means the race is
    effectively over — no belief is created and existing ones are retired.
    """
    if ctx.tool != "get_great_people_overview":
        return
    classes = ctx.facts.get("great_people_classes") or []
    observed_ids: set[str] = set()
    for cls in classes:
        if cls.get("leader_name") in (None, "YOU"):
            continue
        leader_points = int(cls.get("leader_points") or 0)
        gap = int(cls.get("lead_gap") or 0)
        entity_id = f"auto:belief:gp_race:{_slug(cls.get('class', 'unknown')).lower()}"
        observed_ids.add(entity_id)
        if leader_points <= 0 or gap <= 0:
            continue
        gap_ratio = gap / leader_points
        if gap_ratio > _GP_RACE_MAX_GAP_RATIO:
            existing = ctx.engine.get("belief", entity_id)
            if existing is not None and existing.get("status") == "active":
                yield ArchiveEntity(
                    "belief",
                    entity_id,
                    "leader's lead exceeds 50% of their points; race abandoned",
                )
            continue
        yield CreateBelief(entity_id, _gp_race_belief_payload(cls, ctx))

    for belief in ctx.active("belief", "auto:belief:gp_race:"):
        if belief["id"] in observed_ids:
            continue
        row = next(
            (c for c in classes if str(c.get("class")) == belief.get("gp_class")),
            None,
        )
        if row is not None:
            if row.get("leader_name") == "YOU":
                yield ArchiveEntity("belief", belief["id"], "we now lead the race")
            continue
        unseen = ctx.turn - int(belief.get("last_seen_turn") or ctx.turn)
        if unseen >= _RIVAL_UNSEEN_RETIRE_TURNS:
            yield ArchiveEntity(
                "belief",
                belief["id"],
                f"class absent from overview for {unseen} turns",
            )


def _gp_race_belief_payload(cls: dict[str, Any], ctx: RuleContext) -> dict[str, Any]:
    leader_points = int(cls.get("leader_points") or 0)
    our_points = int(cls.get("our_points") or 0)
    gap = int(cls.get("lead_gap") or 0)
    gap_ratio = gap / leader_points if leader_points > 0 else 0.0
    if gap_ratio <= 0.1:
        probability = 0.8
    elif gap_ratio <= 0.25:
        probability = 0.65
    else:
        probability = 0.5
    class_name = str(cls.get("class") or "unknown")
    return {
        "statement": (
            f"{cls.get('leader_name')} leads {class_name} race "
            f"{gap} pts ahead ({our_points} vs {leader_points})."
        ),
        "category": "culture",
        "probability": probability,
        "confidence": 0.7,
        "evidence_ids": [ctx.observation_id],
        "falsifiers": [
            "we overtake the leader in this class",
            "leader's lead grows beyond 50% of their points",
        ],
        "tags": ["automatic", DERIVED_TAG, "great_people", "culture"],
        "gp_class": class_name,
        "leader_name": cls.get("leader_name"),
        "leader_points": leader_points,
        "our_points": our_points,
        "lead_gap": gap,
        "first_seen_turn": ctx.turn,
        "last_seen_turn": ctx.turn,
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

RULES = (
    _camp_threats,
    _research_timing,
    _victory_race,
    _combat_damage,
    _rival_military_threats,
    _great_people_race,
)


def run_rules(ctx: RuleContext) -> list[Op]:
    """Run every derivation rule, collecting ops. Rules must stay pure."""

    ops: list[Op] = []
    for rule in RULES:
        ops.extend(rule(ctx))
    return ops

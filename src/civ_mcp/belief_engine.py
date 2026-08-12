"""Event-sourced world model for long-running Civilization VI agents.

The belief engine deliberately separates immutable history from mutable current
state.  Every create/update/delete operation appends an event; the current
world model is reconstructed by replaying those events.  This preserves the
evidence needed for prediction scoring and post-game attribution while still
providing normal CRUD semantics to MCP clients.
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any


BELIEF_ENTITY_TYPES = frozenset(
    {
        "observation",
        "belief",
        "hypothesis",
        "prediction",
        "plan",
        "surprise",
        "contradiction",
        "decision",
        "action",
        "attribution",
    }
)

_PROBABILITY_FIELDS = {
    "probability",
    "confidence",
    "reliability",
    "probability_of_success",
}
_IMPACT_SCORE = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}
_URGENCY_SCORE = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}


class BeliefEngineError(ValueError):
    """Raised when an invalid belief-engine operation is requested."""


def _slug(value: str) -> str:
    clean = re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-.")
    return clean or "unknown"


def _now() -> float:
    return time.time()


def _coerce_number(value: str) -> int | float:
    number = float(value.replace(",", ""))
    return int(number) if number.is_integer() else number


def normalize_tool_result(tool: str, result: str) -> dict[str, Any]:
    """Extract stable facts/metrics from narrated MCP query results.

    Raw text is always retained by the caller.  This normalizer intentionally
    extracts only values with unambiguous textual contracts; interpretations
    belong in beliefs, not observations.
    """

    metrics: dict[str, Any] = {}
    facts: dict[str, Any] = {"tool": tool}
    first_line = next((line.strip() for line in result.splitlines() if line.strip()), "")
    if first_line:
        facts["summary"] = first_line[:500]

    if tool == "get_game_overview":
        patterns: tuple[tuple[str, str], ...] = (
            ("turn", r"^Turn\s+(\d+)"),
            ("score", r"\| Score:\s*(-?[\d,.]+)"),
            ("gold", r"^Gold:\s*(-?[\d,.]+)"),
            ("gold_per_turn", r"^Gold:[^\n]*?\(([+-]?[\d,.]+)/turn\)"),
            ("science", r"\| Science:\s*(-?[\d,.]+)"),
            ("culture", r"\| Culture:\s*(-?[\d,.]+)"),
            ("faith", r"\| Faith:\s*(-?[\d,.]+)"),
            ("favor", r"\| Favor:\s*(-?[\d,.]+)"),
            ("cities", r"^Cities:\s*(\d+)"),
            ("population", r"\| Population:\s*(\d+)"),
            ("units", r"\| Units:\s*(\d+)"),
            ("exploration_pct", r"^Explored:\s*(\d+)%"),
            ("era_score", r"^Era:[^\n]*?\| Score:\s*(-?[\d,.]+)"),
        )
        for key, pattern in patterns:
            match = re.search(pattern, result, re.MULTILINE)
            if match:
                metrics[key] = _coerce_number(match.group(1))

    elif tool == "get_diplomacy":
        current_key: str | None = None
        rivals: dict[str, dict[str, Any]] = {}
        header_re = re.compile(
            r"^\s{2}(.+?) \((.+?)\) — (.+?) \(([+-]?\d+)\)"
            r"(?P<war> \*\*AT WAR\*\*)?.*\[player (\d+)\]$"
        )
        for line in result.splitlines():
            match = header_re.match(line)
            if match:
                player_id = match.group(6)
                current_key = f"player_{player_id}"
                rivals[current_key] = {
                    "civilization": match.group(1),
                    "leader": match.group(2),
                    "state": match.group(3),
                    "relationship_score": int(match.group(4)),
                    "at_war": bool(match.group("war")),
                }
                metrics[f"diplomacy.{current_key}.relationship_score"] = int(
                    match.group(4)
                )
                metrics[f"diplomacy.{current_key}.at_war"] = bool(match.group("war"))
                continue
            if current_key:
                military = re.match(r"^\s+Military:\s*(\d+)(?: vs our (\d+))?", line)
                if military:
                    rivals[current_key]["military"] = int(military.group(1))
                    metrics[f"diplomacy.{current_key}.military"] = int(
                        military.group(1)
                    )
                    if military.group(2):
                        metrics["our_military"] = int(military.group(2))
                cities = re.match(r"^\s+Cities(?: \((\d+)\)|:\s*(\d+))", line)
                if cities:
                    city_count = int(cities.group(1) or cities.group(2))
                    rivals[current_key]["cities"] = city_count
                    metrics[f"diplomacy.{current_key}.cities"] = city_count
        if rivals:
            facts["rivals"] = rivals

    elif tool == "get_combat_estimate":
        matchup = re.search(
            r"^\s*(\S+) \(CS:(\d+), HP:(\d+)\) vs "
            r"(\S+) \(CS:(\d+), HP:(\d+)\)",
            result,
            re.MULTILINE,
        )
        damage_to_defender = re.search(
            r"^\s*Est damage to defender:\s*~(\d+)", result, re.MULTILINE
        )
        damage_to_attacker = re.search(
            r"^\s*Est damage to attacker:\s*~(\d+)", result, re.MULTILINE
        )
        if matchup:
            facts["matchup"] = {
                "attacker_type": matchup.group(1),
                "defender_type": matchup.group(4),
            }
            for key, value in (
                ("combat.attacker_cs", matchup.group(2)),
                ("combat.attacker_hp", matchup.group(3)),
                ("combat.defender_cs", matchup.group(5)),
                ("combat.defender_hp", matchup.group(6)),
            ):
                metrics[key] = int(value)
            if damage_to_defender:
                metrics["combat.expected_damage_to_defender"] = int(
                    damage_to_defender.group(1)
                )
            # Ranged estimates intentionally omit retaliation damage; zero is
            # an observed property of a valid preview, not missing evidence.
            metrics["combat.expected_damage_to_attacker"] = (
                int(damage_to_attacker.group(1)) if damage_to_attacker else 0
            )

    elif tool == "get_cities":
        city_rows = re.findall(r"^\s{2}(.+?) \(pop (\d+)\) at \((\d+),(\d+)\)", result, re.MULTILINE)
        metrics["observed_city_count"] = len(city_rows)
        facts["cities"] = [
            {"name": name, "population": int(pop), "x": int(x), "y": int(y)}
            for name, pop, x, y in city_rows
        ]

    elif tool == "get_units":
        unit_ids = re.findall(r"\[id:(\d+)", result)
        metrics["observed_unit_count"] = len(unit_ids)
        facts["unit_ids"] = [int(unit_id) for unit_id in unit_ids]

    elif tool == "get_victory_progress":
        section = ""
        enabled = re.search(r"^Enabled:\s*(.+)$", result, re.MULTILINE)
        disabled = re.search(r"^Disabled:\s*(.+)$", result, re.MULTILINE)
        if enabled:
            facts["enabled_victories"] = [
                item.strip() for item in enabled.group(1).split(",") if item.strip()
            ]
        if disabled:
            facts["disabled_victories"] = [
                item.strip() for item in disabled.group(1).split(",") if item.strip()
            ]
        for line in result.splitlines():
            stripped = line.strip()
            if stripped.startswith("SCIENCE VICTORY"):
                section = "science"
                continue
            if stripped.startswith("DOMINATION"):
                section = "domination"
                continue
            if stripped.startswith("CULTURE"):
                section = "culture"
                continue
            if stripped.startswith("RELIGION"):
                section = "religion"
                continue
            if stripped.startswith("SCORE"):
                section = "score"
                continue
            if stripped.startswith("RIVAL INTELLIGENCE"):
                section = "rivals"
                continue
            if stripped.startswith("DEMOGRAPHICS"):
                section = "demographics"
                continue
            if stripped.startswith("VICTORY ASSESSMENT"):
                section = "assessment"
                continue

            vp = re.match(r"^\s{2}([^:]+):\s*(\d+)/(\d+) VP(?:\s*\|\s*(\d+) techs)?", line)
            if vp:
                key = _slug(vp.group(1)).lower()
                prefix = f"victory.{key}.{section}_vp" if section else f"victory.{key}.vp"
                metrics[prefix] = int(vp.group(2))
                metrics[f"{prefix}_target"] = int(vp.group(3))
                if vp.group(4):
                    metrics[f"victory.{key}.techs"] = int(vp.group(4))
                continue
            military = re.match(r"^\s{2}([^:]+):.*\| military (\d+)", line)
            if section == "domination" and military:
                metrics[f"victory.{_slug(military.group(1)).lower()}.military"] = int(
                    military.group(2)
                )
                continue
            rival = re.match(
                r"^\s{2}([^:]+):\s*(\d+) cities \| Sci ([\d.]+) Cul ([\d.]+) "
                r"Gold ([+-]?[\d.]+) \| Mil (\d+)",
                line,
            )
            if section == "rivals" and rival:
                key = f"victory.{_slug(rival.group(1)).lower()}"
                for suffix, value in (
                    ("cities", rival.group(2)),
                    ("science", rival.group(3)),
                    ("culture", rival.group(4)),
                    ("gold_per_turn", rival.group(5)),
                    ("military", rival.group(6)),
                ):
                    metrics[f"{key}.{suffix}"] = _coerce_number(value)
                continue
            score = re.match(r"^\s{2}([^:]+):\s*(-?[\d,.]+)(?:\s+<--)?$", line)
            if section == "score" and score:
                metrics[f"victory.{_slug(score.group(1)).lower()}.score"] = _coerce_number(
                    score.group(2)
                )

    return {"facts": facts, "metrics": metrics}


def _validate_probability(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BeliefEngineError(f"{name} must be a number between 0 and 1")
    if not 0 <= float(value) <= 1:
        raise BeliefEngineError(f"{name} must be between 0 and 1")


def _validate_entity(entity_type: str, entity: dict[str, Any]) -> None:
    if entity_type not in BELIEF_ENTITY_TYPES:
        raise BeliefEngineError(f"Unsupported entity_type: {entity_type}")
    for field in _PROBABILITY_FIELDS:
        if field in entity and entity[field] is not None:
            _validate_probability(field, entity[field])

    required: dict[str, tuple[str, ...]] = {
        "observation": ("statement", "source"),
        "belief": ("statement", "category", "probability", "confidence"),
        "hypothesis": (
            "statement",
            "topic_id",
            "probability",
            "confidence",
        ),
        "prediction": ("statement", "probability", "confidence", "deadline_turn"),
        "plan": ("goal", "horizon", "probability_of_success"),
        "surprise": ("statement", "severity"),
        "contradiction": ("statement", "severity"),
        "decision": ("statement", "route"),
        "action": ("statement", "tool"),
        "attribution": ("failure", "candidates"),
    }
    missing = [field for field in required[entity_type] if entity.get(field) in (None, "")]
    if missing:
        raise BeliefEngineError(
            f"Missing required {entity_type} fields: {', '.join(missing)}"
        )
    if entity_type == "plan" and entity["horizon"] not in (5, 10, 20):
        raise BeliefEngineError("plan horizon must be 5, 10, or 20 turns")
    if entity_type == "prediction":
        deadline = entity["deadline_turn"]
        if isinstance(deadline, bool) or not isinstance(deadline, int) or deadline < 0:
            raise BeliefEngineError("deadline_turn must be a non-negative integer")


def evaluate_condition(rule: dict[str, Any], metrics: dict[str, Any]) -> bool | None:
    """Evaluate a declarative metric rule, returning None when data is absent."""

    metric = rule.get("metric")
    if not metric or metric not in metrics:
        return None
    actual = metrics[metric]
    expected = rule.get("value")
    operator = rule.get("operator", "==")
    try:
        if operator == ">=":
            return actual >= expected
        if operator == ">":
            return actual > expected
        if operator == "<=":
            return actual <= expected
        if operator == "<":
            return actual < expected
        if operator == "==":
            tolerance = float(rule.get("tolerance", 0))
            if tolerance and isinstance(actual, (int, float)) and isinstance(
                expected, (int, float)
            ):
                return math.isclose(actual, expected, abs_tol=tolerance)
            return actual == expected
        if operator == "!=":
            return actual != expected
        if operator == "contains":
            return expected in actual
        if operator == "not_contains":
            return expected not in actual
    except (TypeError, ValueError):
        return None
    raise BeliefEngineError(f"Unsupported condition operator: {operator}")


class BeliefEngine:
    """Persistent current-world model backed by an append-only event log."""

    def __init__(self, run_id: str, directory: Path | None = None) -> None:
        self.run_id = run_id
        self.directory = directory or Path.home() / ".civ6-mcp" / "beliefs"
        self.game_id: str | None = None
        self.path: Path | None = None
        self._events: list[dict[str, Any]] = []
        self._entities: dict[str, dict[str, dict[str, Any]]] = {
            entity_type: {} for entity_type in BELIEF_ENTITY_TYPES
        }
        self._sequence = 0
        self._pending_events: list[dict[str, Any]] = []

    @property
    def bound(self) -> bool:
        return self.path is not None

    def bind_game(self, civ: str, seed: int) -> None:
        game_id = f"{civ}_{seed}"
        if game_id == self.game_id:
            return
        self.game_id = game_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"belief_{_slug(game_id)}.jsonl"
        self._load()

    def _require_bound(self) -> Path:
        if self.path is None or self.game_id is None:
            raise BeliefEngineError(
                "Belief Engine is not bound to a game; call get_game_overview first"
            )
        return self.path

    def _load(self) -> None:
        path = self._require_bound()
        self._events = []
        self._entities = {entity_type: {} for entity_type in BELIEF_ENTITY_TYPES}
        self._sequence = 0
        self._pending_events = []
        if not path.exists():
            return
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            self._events.append(event)
            self._sequence = max(self._sequence, int(event.get("sequence", 0)))
            self._reduce(event)

    def _reduce(self, event: dict[str, Any]) -> None:
        entity = event.get("entity")
        entity_type = event.get("entity_type")
        if entity_type not in self._entities or not isinstance(entity, dict):
            return
        entity_id = entity.get("id")
        if entity_id:
            self._entities[entity_type][entity_id] = deepcopy(entity)

    def _append(
        self,
        event_type: str,
        entity_type: str,
        entity: dict[str, Any],
        *,
        turn: int,
        changes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self._require_bound()
        self._sequence += 1
        event: dict[str, Any] = {
            "v": 1,
            "event_id": str(uuid.uuid4()),
            "sequence": self._sequence,
            "timestamp": _now(),
            "game_id": self.game_id,
            "run_id": self.run_id,
            "turn": turn,
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity["id"],
            "entity": deepcopy(entity),
        }
        if changes:
            event["changes"] = changes
        with path.open("a") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._events.append(event)
        self._reduce(event)
        self._pending_events.append(deepcopy(event))
        return event

    def drain_events(self) -> list[dict[str, Any]]:
        events = self._pending_events
        self._pending_events = []
        return events

    def create(
        self,
        entity_type: str,
        payload: dict[str, Any],
        *,
        turn: int,
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_bound()
        if entity_type not in BELIEF_ENTITY_TYPES:
            raise BeliefEngineError(f"Unsupported entity_type: {entity_type}")
        entity_id = entity_id or f"{entity_type}_{uuid.uuid4().hex[:12]}"
        existing = self._entities[entity_type].get(entity_id)
        if existing and existing.get("status") != "deleted":
            raise BeliefEngineError(f"{entity_type} already exists: {entity_id}")
        entity = deepcopy(payload)
        entity.update(
            {
                "id": entity_id,
                "entity_type": entity_type,
                "status": entity.get("status", "active"),
                "created_turn": turn,
                "last_updated_turn": turn,
                "created_at": _now(),
                "updated_at": _now(),
                "version": 1,
            }
        )
        _validate_entity(entity_type, entity)
        self._append("entity.created", entity_type, entity, turn=turn)
        return deepcopy(entity)

    def update(
        self,
        entity_type: str,
        entity_id: str,
        patch: dict[str, Any],
        *,
        turn: int,
    ) -> dict[str, Any]:
        current = self._entities.get(entity_type, {}).get(entity_id)
        if not current:
            raise BeliefEngineError(f"Unknown {entity_type}: {entity_id}")
        if current.get("status") == "deleted":
            raise BeliefEngineError(f"Cannot update deleted {entity_type}: {entity_id}")
        protected = {"id", "entity_type", "created_turn", "created_at", "version"}
        illegal = protected.intersection(patch)
        if illegal:
            raise BeliefEngineError(f"Cannot update protected fields: {', '.join(sorted(illegal))}")
        updated = deepcopy(current)
        changes: dict[str, Any] = {}
        for key, value in patch.items():
            if updated.get(key) != value:
                changes[key] = {"from": updated.get(key), "to": deepcopy(value)}
                updated[key] = deepcopy(value)
        if not changes:
            return deepcopy(current)
        updated["last_updated_turn"] = turn
        updated["updated_at"] = _now()
        updated["version"] = int(current.get("version", 1)) + 1
        _validate_entity(entity_type, updated)
        self._append(
            "entity.updated",
            entity_type,
            updated,
            turn=turn,
            changes=changes,
        )
        return deepcopy(updated)

    def upsert(
        self,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        *,
        turn: int,
    ) -> dict[str, Any]:
        current = self._entities.get(entity_type, {}).get(entity_id)
        if current and current.get("status") != "deleted":
            return self.update(entity_type, entity_id, payload, turn=turn)
        return self.create(entity_type, payload, turn=turn, entity_id=entity_id)

    def delete(
        self,
        entity_type: str,
        entity_id: str,
        *,
        reason: str,
        turn: int,
    ) -> dict[str, Any]:
        current = self._entities.get(entity_type, {}).get(entity_id)
        if not current:
            raise BeliefEngineError(f"Unknown {entity_type}: {entity_id}")
        if current.get("status") == "deleted":
            return deepcopy(current)
        deleted = deepcopy(current)
        deleted.update(
            {
                "status": "deleted",
                "deleted_reason": reason,
                "deleted_turn": turn,
                "last_updated_turn": turn,
                "updated_at": _now(),
                "version": int(current.get("version", 1)) + 1,
            }
        )
        self._append(
            "entity.deleted",
            entity_type,
            deleted,
            turn=turn,
            changes={"status": {"from": current.get("status"), "to": "deleted"}},
        )
        return deepcopy(deleted)

    def get(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        entity = self._entities.get(entity_type, {}).get(entity_id)
        return deepcopy(entity) if entity else None

    def list(
        self,
        entity_type: str | None = None,
        *,
        status: str | None = "active",
    ) -> list[dict[str, Any]]:
        types = [entity_type] if entity_type else sorted(BELIEF_ENTITY_TYPES)
        entities: list[dict[str, Any]] = []
        for kind in types:
            if kind not in self._entities:
                raise BeliefEngineError(f"Unsupported entity_type: {kind}")
            for entity in self._entities[kind].values():
                if status and entity.get("status") != status:
                    continue
                entities.append(deepcopy(entity))
        return sorted(
            entities,
            key=lambda item: (item.get("last_updated_turn", -1), item.get("updated_at", 0)),
            reverse=True,
        )

    def history(
        self,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        last_n: int = 100,
    ) -> list[dict[str, Any]]:
        events = self._events
        if entity_type:
            events = [event for event in events if event.get("entity_type") == entity_type]
        if entity_id:
            events = [event for event in events if event.get("entity_id") == entity_id]
        return deepcopy(events[-max(1, min(last_n, 1000)) :])

    def current_metrics(self) -> dict[str, Any]:
        observations = self.list("observation", status="active")
        # Metrics are snapshots per source tool. Keeping every historical key
        # would make removed/schema-corrected fields live forever. Select the
        # newest successful observation from each source, then merge sources.
        newest_by_source: dict[str, dict[str, Any]] = {}
        for observation in observations:
            source = str(observation.get("source") or observation.get("id"))
            previous = newest_by_source.get(source)
            marker = (observation.get("observed_turn", -1), observation.get("updated_at", 0))
            previous_marker = (
                (previous.get("observed_turn", -1), previous.get("updated_at", 0))
                if previous
                else (-1, 0)
            )
            if previous is None or marker > previous_marker:
                newest_by_source[source] = observation
        observations = sorted(
            newest_by_source.values(),
            key=lambda item: (item.get("observed_turn", -1), item.get("updated_at", 0)),
        )
        metrics: dict[str, Any] = {}
        for observation in observations:
            metrics.update(observation.get("metrics") or {})
        return metrics

    def record_tool_result(
        self,
        *,
        tool: str,
        params: dict[str, Any],
        result: str,
        turn: int,
        category: str,
        success: bool,
        duration_ms: int,
    ) -> dict[str, Any] | None:
        if not self.bound:
            return None
        if category == "query" and success:
            normalized = normalize_tool_result(tool, result)
            observation = self.create(
                "observation",
                {
                    "statement": normalized["facts"].get("summary")
                    or f"Observed result from {tool}",
                    "source": f"mcp:{tool}",
                    "source_params": deepcopy(params),
                    "raw": result,
                    "facts": normalized["facts"],
                    "metrics": normalized["metrics"],
                    "reliability": 1.0,
                    "observed_turn": turn,
                    "tags": ["automatic", "mcp", tool],
                },
                turn=turn,
            )
            self.review(turn=turn)
            return observation
        if category in {"action", "turn"}:
            return self.create(
                "action",
                {
                    "statement": f"{tool} {'succeeded' if success else 'failed'}",
                    "tool": tool,
                    "params": deepcopy(params),
                    "result": result,
                    "success": success,
                    "duration_ms": duration_ms,
                    "selected_turn": turn,
                    "verification": {
                        "source": "tool_result",
                        "verified": success,
                        "result": result[:2000],
                    },
                },
                turn=turn,
            )
        return None

    def resolve_prediction(
        self,
        prediction_id: str,
        *,
        outcome: bool,
        actual: Any,
        turn: int,
        source: str = "manual",
    ) -> dict[str, Any]:
        prediction = self.get("prediction", prediction_id)
        if not prediction:
            raise BeliefEngineError(f"Unknown prediction: {prediction_id}")
        probability = float(prediction["probability"])
        error = abs((1.0 if outcome else 0.0) - probability)
        updated = self.update(
            "prediction",
            prediction_id,
            {
                "status": "confirmed" if outcome else "disconfirmed",
                "outcome": outcome,
                "actual": actual,
                "resolved_turn": turn,
                "resolution_source": source,
                "prediction_error": round(error, 4),
            },
            turn=turn,
        )
        self._create_surprise_for_prediction(updated, turn=turn)
        return updated

    def _create_surprise_for_prediction(
        self, prediction: dict[str, Any], *, turn: int
    ) -> dict[str, Any] | None:
        probability = float(prediction["probability"])
        outcome = bool(prediction.get("outcome"))
        unexpectedness = (1 - probability) if outcome else probability
        if unexpectedness < 0.4:
            return None
        severity = (
            "major" if unexpectedness >= 0.75 else "high" if unexpectedness >= 0.6 else "medium"
        )
        surprise_id = f"surprise_{prediction['id']}_{turn}"
        existing = self.get("surprise", surprise_id)
        if existing and existing.get("status") != "deleted":
            return existing
        surprise = self.create(
            "surprise",
            {
                "statement": f"Prediction diverged from reality: {prediction['statement']}",
                "severity": severity,
                "prediction_id": prediction["id"],
                "expected_probability": probability,
                "actual": prediction.get("actual"),
                "prediction_error": prediction.get("prediction_error"),
                "requires_slow_review": severity in {"high", "major"},
            },
            turn=turn,
            entity_id=surprise_id,
        )
        for belief_id in prediction.get("belief_ids") or []:
            belief = self.get("belief", belief_id)
            if belief and belief.get("status") == "active":
                self.update(
                    "belief",
                    belief_id,
                    {
                        "review_required": True,
                        "review_reason": f"Surprise from prediction {prediction['id']}",
                    },
                    turn=turn,
                )
        return surprise

    def rebalance_hypotheses(
        self, topic_id: str, probabilities: dict[str, float], *, turn: int
    ) -> list[dict[str, Any]]:
        if not probabilities:
            raise BeliefEngineError("probabilities cannot be empty")
        for probability in probabilities.values():
            _validate_probability("probability", probability)
        if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.001):
            raise BeliefEngineError("Hypothesis probabilities for a topic must sum to 1")
        members: dict[str, dict[str, Any]] = {}
        # Validate the complete pool before appending any event.  Event sourcing
        # cannot roll back a partially written redistribution.
        for hypothesis_id in probabilities:
            hypothesis = self.get("hypothesis", hypothesis_id)
            if not hypothesis or hypothesis.get("topic_id") != topic_id:
                raise BeliefEngineError(
                    f"Hypothesis {hypothesis_id} does not belong to topic {topic_id}"
                )
            members[hypothesis_id] = hypothesis
        updated: list[dict[str, Any]] = []
        for hypothesis_id, probability in probabilities.items():
            updated.append(
                self.update(
                    "hypothesis",
                    hypothesis_id,
                    {"probability": probability},
                    turn=turn,
                )
            )
        return updated

    def assess_route_combat_risk(
        self,
        belief_id: str,
        *,
        nearby_hostiles: list[dict[str, Any]],
        assessment: dict[str, Any] | None,
        turn: int,
    ) -> dict[str, Any]:
        """Gate route-belief revisions on a quantified combat matchup.

        A threat scan establishes that verification is warranted, not that a
        route is unsafe.  Only a complete game-derived combat estimate can
        change the probability.  A downward revision must also agree with the
        numerical matchup instead of merely carrying a `combat_estimate` label.
        """
        belief = self.get("belief", belief_id)
        if not belief or belief.get("status") != "active":
            raise BeliefEngineError(f"Unknown active belief: {belief_id}")
        if assessment is None:
            return {
                "belief_id": belief_id,
                "belief_updated": False,
                "route": "verify_then_fast" if nearby_hostiles else "fast",
                "required_evidence": "combat_estimate" if nearby_hostiles else None,
                "nearby_hostiles": deepcopy(nearby_hostiles),
                "probability": belief["probability"],
            }

        required = {
            "source",
            "revised_probability",
            "attacker_cs",
            "defender_cs",
            "attacker_hp",
            "defender_hp",
            "expected_damage_to_attacker",
            "expected_damage_to_defender",
        }
        missing = sorted(required.difference(assessment))
        if missing or assessment.get("source") != "combat_estimate":
            detail = f"; missing {', '.join(missing)}" if missing else ""
            raise BeliefEngineError(
                "A complete game-derived combat assessment is required" + detail
            )
        revised_probability = assessment["revised_probability"]
        _validate_probability("revised_probability", revised_probability)
        numeric_fields = required - {"source", "revised_probability"}
        if any(
            isinstance(assessment[name], bool)
            or not isinstance(assessment[name], (int, float))
            or not math.isfinite(float(assessment[name]))
            or float(assessment[name]) < 0
            for name in numeric_fields
        ):
            raise BeliefEngineError("combat assessment values must be finite non-negative numbers")
        if assessment["attacker_hp"] <= 0 or assessment["defender_hp"] <= 0:
            raise BeliefEngineError("combat assessment HP values must be greater than zero")

        current_probability = float(belief["probability"])
        if float(revised_probability) < current_probability:
            attacker_loss = min(
                1.0,
                float(assessment["expected_damage_to_attacker"])
                / float(assessment["attacker_hp"]),
            )
            defender_loss = min(
                1.0,
                float(assessment["expected_damage_to_defender"])
                / float(assessment["defender_hp"]),
            )
            materially_disadvantaged = (
                float(assessment["defender_cs"]) > float(assessment["attacker_cs"])
                and attacker_loss > defender_loss
            )
            if not materially_disadvantaged:
                raise BeliefEngineError(
                    "combat assessment does not numerically justify lowering the route belief"
                )

        updated = self.update(
            "belief",
            belief_id,
            {
                "probability": revised_probability,
                "last_combat_risk_assessment": deepcopy(assessment),
                "last_combat_risk_assessment_turn": turn,
                "review_required": False,
            },
            turn=turn,
        )
        return {
            "belief_id": belief_id,
            "belief_updated": updated["version"] != belief["version"],
            "route": "fast",
            "required_evidence": None,
            "probability": updated["probability"],
            "assessment": deepcopy(assessment),
        }

    def review(self, *, turn: int) -> dict[str, Any]:
        """Evaluate predictions, belief expectations, and plan triggers."""

        metrics = self.current_metrics()
        resolved: list[str] = []
        overdue: list[str] = []
        contradictions: list[str] = []
        replans: list[str] = []

        for prediction in self.list("prediction", status="active"):
            rule = prediction.get("evaluation")
            evaluation = evaluate_condition(rule, metrics) if isinstance(rule, dict) else None
            if evaluation is True:
                self.resolve_prediction(
                    prediction["id"],
                    outcome=True,
                    actual=metrics.get(rule.get("metric")) if rule else None,
                    turn=turn,
                    source="automatic",
                )
                resolved.append(prediction["id"])
            elif turn >= int(prediction["deadline_turn"]):
                if evaluation is False:
                    self.resolve_prediction(
                        prediction["id"],
                        outcome=False,
                        actual=metrics.get(rule.get("metric")) if rule else None,
                        turn=turn,
                        source="automatic",
                    )
                    resolved.append(prediction["id"])
                else:
                    self.update(
                        "prediction",
                        prediction["id"],
                        {"status": "overdue", "review_required": True},
                        turn=turn,
                    )
                    overdue.append(prediction["id"])

        existing_contradiction_keys = {
            entity.get("contradiction_key")
            for entity in self.list("contradiction", status="active")
        }
        for belief in self.list("belief", status="active"):
            for index, expectation in enumerate(belief.get("expectations") or []):
                if not isinstance(expectation, dict):
                    continue
                evaluation = evaluate_condition(expectation, metrics)
                expected_result = bool(expectation.get("expected", True))
                if evaluation is not None and evaluation != expected_result:
                    key = f"{belief['id']}:{index}"
                    if key in existing_contradiction_keys:
                        continue
                    contradiction = self.create(
                        "contradiction",
                        {
                            "statement": f"Evidence contradicts belief: {belief['statement']}",
                            "severity": expectation.get("severity", "high"),
                            "belief_id": belief["id"],
                            "contradiction_key": key,
                            "rule": deepcopy(expectation),
                            "actual": metrics.get(expectation.get("metric")),
                            "requires_slow_review": True,
                        },
                        turn=turn,
                    )
                    contradictions.append(contradiction["id"])
                    self.update(
                        "belief",
                        belief["id"],
                        {
                            "review_required": True,
                            "review_reason": f"Contradiction {contradiction['id']}",
                        },
                        turn=turn,
                    )

        for plan in self.list("plan", status="active"):
            triggered = []
            for condition in plan.get("exit_conditions") or []:
                if isinstance(condition, dict) and evaluate_condition(condition, metrics) is True:
                    triggered.append(condition)
            broken_assumptions = []
            for belief_id, threshold in (plan.get("assumption_thresholds") or {}).items():
                belief = self.get("belief", belief_id)
                if not belief or belief.get("status") != "active":
                    broken_assumptions.append(belief_id)
                elif float(belief.get("probability", 0)) < float(threshold):
                    broken_assumptions.append(belief_id)
            if triggered or broken_assumptions:
                self.update(
                    "plan",
                    plan["id"],
                    {
                        "status": "needs_replan",
                        "triggered_exit_conditions": triggered,
                        "broken_assumptions": broken_assumptions,
                        "review_required": True,
                    },
                    turn=turn,
                )
                replans.append(plan["id"])
            elif turn >= int(plan.get("review_turn", turn + 1)) and not plan.get(
                "review_required"
            ):
                self.update(
                    "plan",
                    plan["id"],
                    {"review_required": True, "review_reason": "scheduled"},
                    turn=turn,
                )

        return {
            "turn": turn,
            "metrics": metrics,
            "predictions_resolved": resolved,
            "predictions_overdue": overdue,
            "contradictions_created": contradictions,
            "plans_needing_replan": replans,
        }

    def turn_brief(self, *, turn: int, limit: int = 12) -> dict[str, Any]:
        """Return the decision-facing belief state for the current turn.

        Automatic observations are useful only when they are put back in the
        agent's decision context.  This method is the deliberately small
        bridge between the event-sourced model and the turn loop: review due
        entities first, then expose the active beliefs, predictions, plans,
        surprises, and contradictions that should affect action selection.
        """
        review = self.review(turn=turn)
        take = max(1, min(int(limit), 50))

        beliefs = self.list("belief", status="active")
        beliefs.sort(
            key=lambda item: (
                bool(item.get("review_required")),
                _IMPACT_SCORE.get(str(item.get("impact", "medium")).lower(), 0.5),
                _URGENCY_SCORE.get(str(item.get("urgency", "medium")).lower(), 0.5),
                item.get("last_updated_turn", -1),
            ),
            reverse=True,
        )
        predictions = [
            item
            for item in self.list("prediction", status=None)
            if item.get("status") in {"active", "overdue"}
        ]
        plans = [
            item
            for item in self.list("plan", status=None)
            if item.get("status") in {"active", "needs_replan"}
        ]
        surprises = self.list("surprise", status="active")
        contradictions = self.list("contradiction", status="active")

        def compact(entity: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
            return {
                key: deepcopy(entity[key])
                for key in ("id", *fields)
                if key in entity
            }

        gated_beliefs = [
            item["id"] for item in beliefs if item.get("review_required")
        ]
        replan_plans = [
            item["id"]
            for item in plans
            if item.get("status") == "needs_replan" or item.get("review_required")
        ]
        if replan_plans or contradictions or any(
            item.get("severity") in {"high", "major"} for item in surprises
        ):
            default_route = "slow"
        elif gated_beliefs or surprises:
            default_route = "verify_then_fast"
        else:
            default_route = "fast"

        return {
            "game_id": self.game_id,
            "turn": turn,
            "review": review,
            "current_metrics": self.current_metrics(),
            "decision_gate": {
                "default_route": default_route,
                "beliefs_requiring_review": gated_beliefs[:take],
                "plans_requiring_review": replan_plans[:take],
                "active_surprises": [item["id"] for item in surprises[:take]],
                "active_contradictions": [item["id"] for item in contradictions[:take]],
            },
            "beliefs": [
                compact(
                    item,
                    (
                        "statement",
                        "category",
                        "probability",
                        "confidence",
                        "impact",
                        "urgency",
                        "review_required",
                        "review_reason",
                    ),
                )
                for item in beliefs[:take]
            ],
            "predictions": [
                compact(
                    item,
                    (
                        "statement",
                        "probability",
                        "confidence",
                        "deadline_turn",
                        "review_required",
                    ),
                )
                for item in predictions[:take]
            ],
            "plans": [
                compact(
                    item,
                    (
                        "goal",
                        "horizon",
                        "status",
                        "review_turn",
                        "review_required",
                        "status_reason",
                    ),
                )
                for item in plans[:take]
            ],
            "surprises": [
                compact(item, ("statement", "severity", "prediction_id", "turn"))
                for item in surprises[:take]
            ],
            "contradictions": [
                compact(item, ("statement", "severity", "belief_id", "requires_slow_review"))
                for item in contradictions[:take]
            ],
            "guardrails": [
                "Nearby hostiles trigger verification; they do not lower route belief by themselves.",
                "Use get_combat_estimate before assess_route_combat_risk when route safety is in question.",
                "Use route_belief_decision before high-impact or irreversible actions.",
            ],
        }

    def route_decision(
        self,
        *,
        statement: str,
        probability: float,
        confidence: float,
        impact: str,
        urgency: str,
        irreversibility: float,
        turn: int,
        belief_ids: list[str] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        _validate_probability("probability", probability)
        _validate_probability("confidence", confidence)
        _validate_probability("irreversibility", irreversibility)
        impact_score = _IMPACT_SCORE.get(impact.lower())
        urgency_score = _URGENCY_SCORE.get(urgency.lower())
        if impact_score is None or urgency_score is None:
            raise BeliefEngineError("impact and urgency must be low, medium, high, or critical")
        uncertainty = max(1 - abs(probability - 0.5) * 2, 1 - confidence)
        expected_loss = probability * impact_score
        active_surprises = self.list("surprise", status="active")
        surprise_score = max(
            (
                {"medium": 0.4, "high": 0.7, "major": 1.0}.get(
                    item.get("severity", ""), 0
                )
                for item in active_surprises
            ),
            default=0,
        )
        score = min(
            1.0,
            expected_loss * 0.35
            + uncertainty * impact_score * 0.25
            + urgency_score * 0.15
            + irreversibility * 0.2
            + surprise_score * 0.05,
        )
        if score >= 0.55 or surprise_score >= 0.7:
            route = "slow"
            budget = "high" if score >= 0.75 or surprise_score >= 1 else "medium"
        elif score >= 0.35:
            route = "verify_then_fast"
            budget = "low"
        else:
            route = "fast"
            budget = "none"
        assessment = {
            "statement": statement,
            "route": route,
            "slow_thinking_budget": budget,
            "priority_score": round(score, 4),
            "expected_loss": round(expected_loss, 4),
            "uncertainty": round(uncertainty, 4),
            "impact": impact.lower(),
            "urgency": urgency.lower(),
            "irreversibility": irreversibility,
            "probability": probability,
            "confidence": confidence,
            "belief_ids": belief_ids or [],
            "active_surprise_score": surprise_score,
        }
        if persist:
            return self.create("decision", assessment, turn=turn)
        return assessment

    def update_attribution_posteriors(
        self, attribution_id: str, *, turn: int
    ) -> dict[str, Any]:
        attribution = self.get("attribution", attribution_id)
        if not attribution:
            raise BeliefEngineError(f"Unknown attribution: {attribution_id}")
        weighted: list[tuple[dict[str, Any], float]] = []
        for candidate in attribution.get("candidates") or []:
            prior = float(candidate.get("prior", 0))
            _validate_probability("candidate prior", prior)
            support = sum(float(item.get("weight", 0)) for item in candidate.get("evidence_for", []))
            oppose = sum(
                float(item.get("weight", 0)) for item in candidate.get("evidence_against", [])
            )
            likelihood = max(0.01, 1 + support - oppose)
            weighted.append((candidate, prior * likelihood))
        total = sum(value for _, value in weighted)
        if total <= 0:
            raise BeliefEngineError("Attribution candidates must have positive total prior")
        candidates = []
        for candidate, value in weighted:
            updated = deepcopy(candidate)
            updated["posterior"] = round(value / total, 6)
            candidates.append(updated)
        return self.update(
            "attribution",
            attribution_id,
            {"candidates": candidates, "last_computed_turn": turn},
            turn=turn,
        )

    def metrics(self) -> dict[str, Any]:
        predictions = [
            item
            for item in self.list("prediction", status=None)
            if item.get("status") in {"confirmed", "disconfirmed"}
        ]
        errors = [float(item.get("prediction_error", 0)) for item in predictions]
        overconfident = [
            item
            for item in predictions
            if float(item.get("probability", 0)) >= 0.75
            and item.get("status") == "disconfirmed"
        ]
        plans = self.list("plan", status=None)
        completed_plans = [item for item in plans if item.get("status") == "completed"]
        decisions = self.list("decision", status=None)
        return {
            "belief_count": len(self.list("belief", status="active")),
            "hypothesis_count": len(self.list("hypothesis", status="active")),
            "prediction_count": len(self.list("prediction", status=None)),
            "resolved_prediction_count": len(predictions),
            "mean_prediction_error": round(sum(errors) / len(errors), 4) if errors else None,
            "overconfidence_rate": round(len(overconfident) / len(predictions), 4)
            if predictions
            else None,
            "surprise_frequency": len(self.list("surprise", status=None)),
            "contradiction_count": len(self.list("contradiction", status=None)),
            "plan_completion_rate": round(len(completed_plans) / len(plans), 4)
            if plans
            else None,
            "plan_revision_count": sum(
                1
                for event in self._events
                if event.get("entity_type") == "plan"
                and event.get("event_type") == "entity.updated"
            ),
            "slow_thinking_trigger_rate": round(
                sum(1 for item in decisions if item.get("route") == "slow") / len(decisions),
                4,
            )
            if decisions
            else None,
            "event_count": len(self._events),
        }

    def snapshot(self, *, include_deleted: bool = False) -> dict[str, Any]:
        status = None if include_deleted else "active"
        return {
            "game_id": self.game_id,
            "run_id": self.run_id,
            "current_metrics": self.current_metrics(),
            "entities": {
                entity_type: self.list(entity_type, status=status)
                for entity_type in sorted(BELIEF_ENTITY_TYPES)
            },
            "research_metrics": self.metrics(),
            "last_sequence": self._sequence,
        }

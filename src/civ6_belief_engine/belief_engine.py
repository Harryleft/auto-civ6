"""Event-sourced world model for long-running Civilization VI agents.

The belief engine deliberately separates immutable history from mutable current
state.  Every create/update/delete operation appends an event; the current
world model is reconstructed by replaying those events.  This preserves the
evidence needed for prediction scoring and post-game attribution while still
providing normal CRUD semantics to MCP clients.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .graph import (
    GRAPH_DELTA_EVENT,
    GraphDelta,
    GraphReplayError,
    GraphView,
    replay_graph_events,
)

log = logging.getLogger(__name__)


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
        "world_entity",
        "goal",
        "proposal",
        "critic_review",
        "council_decision",
        "budget_lock",
        "outcome",
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
_RESULT_SUMMARY_CHARS = 500


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


def _result_summary(result: str) -> str:
    match = re.search(r"(?m)^[ \t]*(\S[^\r\n]*)", result)
    return match.group(1).strip()[:_RESULT_SUMMARY_CHARS] if match else ""


def normalize_tool_result(tool: str, result: str) -> dict[str, Any]:
    """Extract stable facts/metrics from narrated MCP query results.

    Raw text remains owned by the tool transcript and telemetry. This
    normalizer intentionally extracts only values with unambiguous textual
    contracts; interpretations belong in beliefs, not observations.
    """

    metrics: dict[str, Any] = {}
    facts: dict[str, Any] = {"tool": tool}
    first_line = _result_summary(result)
    if first_line:
        facts["summary"] = first_line

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
        # Game speed drives every cost multiplier (pantheon 25→17 on Quick,
        # era thresholds, production costs). Expose it as a stable metric so
        # beliefs/predictions never reason with standard-speed assumptions.
        speed_match = re.search(
            r"\|\s*(\w+)\s*speed(?:\s*\((\d+)% costs\))?", result
        )
        if speed_match:
            metrics["game_speed"] = speed_match.group(1)
            if speed_match.group(2):
                metrics["speed_cost_multiplier"] = _coerce_number(
                    speed_match.group(2)
                )

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


def tool_result_reference(result: str) -> dict[str, Any]:
    """Return a compact, stable pointer to a raw result owned by telemetry."""

    encoded = result.encode("utf-8")
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "utf8_bytes": len(encoded),
        "summary": _result_summary(result),
    }


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
        "world_entity": ("node_type", "attributes"),
        "goal": ("statement", "priority"),
        "proposal": ("statement", "department", "action_intent"),
        "critic_review": ("proposal_id", "verdict"),
        "council_decision": ("statement", "selected_proposal_id"),
        "budget_lock": ("resource", "amount", "proposal_id"),
        "outcome": ("statement", "action_intent", "success"),
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
    if entity_type == "critic_review":
        verdict = entity.get("verdict")
        if verdict not in {"agree", "agree_with_conditions", "object"}:
            raise BeliefEngineError(
                "critic verdict must be agree, agree_with_conditions, or object"
            )
        if verdict == "object" and not any(
            entity.get(field)
            for field in (
                "counterevidence",
                "invalidated_assumptions",
                "alternative",
            )
        ):
            raise BeliefEngineError(
                "critic objection requires counterevidence, an invalidated "
                "assumption, or a concrete alternative"
            )


def _canonical_params(params: dict[str, Any]) -> str:
    """Stable action/evidence identity without relying on prose matching."""

    return json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def action_args_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_params(params).encode("utf-8")).hexdigest()


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
        # Events are grouped into epochs: each game reload (autosave rollback)
        # starts a new one so conflicting histories are never replayed as one.
        self._epoch: int = 1
        self._graph_view = GraphView.empty()
        self._graph_replay_error: str | None = None

    @property
    def bound(self) -> bool:
        return self.path is not None

    @property
    def epoch(self) -> int:
        """Current game-history branch for derived read models."""

        return self._epoch

    @property
    def graph_view(self) -> GraphView:
        """Current derived graph, rebuilt from graph.delta journal events."""

        return self._graph_view

    @property
    def graph_replay_error(self) -> str | None:
        return self._graph_replay_error

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
        self._epoch = 1
        self._graph_view = GraphView.empty()
        self._graph_replay_error = None
        if not path.exists():
            return
        # errors="replace": a crash mid-write can also truncate a UTF-8
        # sequence; a replacement character still fails JSON parsing and is
        # quarantined instead of raising out of the whole load.
        raw_lines = path.read_text(errors="replace").splitlines()
        good_lines: list[str] = []
        bad_line_numbers: list[int] = []
        for line_number, line in enumerate(raw_lines, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                bad_line_numbers.append(line_number)
                continue
            if not isinstance(event, dict) or not self._valid_event_schema(event):
                bad_line_numbers.append(line_number)
                continue
            good_lines.append(line)
            self._events.append(event)
            self._sequence = max(self._sequence, int(event.get("sequence", 0)))
            self._reduce(event)
        # Events written before the epoch mechanism exist belong to epoch 1,
        # and every game.reloaded marker starts exactly one new epoch after
        # them, so the marker count recovers the current epoch on restart.
        self._epoch = 1 + sum(
            1 for event in self._events if event.get("event_type") == "game.reloaded"
        )
        if bad_line_numbers:
            self._quarantine_corrupt_lines(path, good_lines, bad_line_numbers, raw_lines)
        try:
            self._graph_view = replay_graph_events(self._events, epoch=self._epoch)
        except GraphReplayError as exc:
            # The graph is a derived read model. A damaged graph event must be
            # visible, but it must not make the existing belief model unusable.
            self._graph_view = GraphView.empty(epoch=self._epoch)
            self._graph_replay_error = str(exc)
            log.error("Belief Engine: graph replay failed: %s", exc)
        self._recover_orphaned_executing_decisions()

    @staticmethod
    def _valid_event_schema(event: dict[str, Any]) -> bool:
        """Minimal structural contract every persisted event must satisfy.

        A line can parse as valid JSON yet still break the replay loop: a
        non-numeric ``sequence`` crashes ``int()`` during load, and a missing
        ``event_type`` or ``entity.id`` silently corrupts the projection.
        Such lines are quarantined like unreadable ones.
        """

        if not isinstance(event.get("event_type"), str) or not event["event_type"]:
            return False
        if not isinstance(event.get("entity_type"), str) or not event["entity_type"]:
            return False
        try:
            int(event.get("sequence", 0))
        except (TypeError, ValueError):
            return False
        entity = event.get("entity")
        if not isinstance(entity, dict):
            return False
        entity_id = entity.get("id")
        return isinstance(entity_id, str) and bool(entity_id)

    def _quarantine_corrupt_lines(
        self,
        path: Path,
        good_lines: list[str],
        bad_line_numbers: list[int],
        raw_lines: list[str],
    ) -> None:
        """Isolate corrupt JSONL lines so future appends cannot fuse with them.

        A crash can truncate the final line. Without repair the next append
        concatenates onto that broken line, and every later event then
        silently fails to parse on reload. Rewrite the log with only intact
        lines (atomically, so a crash mid-repair cannot destroy history) and
        append an auditable marker describing exactly what was dropped.
        """
        entries = [
            {
                "line_number": line_number,
                "sha256": hashlib.sha256(
                    raw_lines[line_number - 1].encode("utf-8", "replace")
                ).hexdigest(),
                "preview": raw_lines[line_number - 1][:80],
            }
            for line_number in bad_line_numbers
        ]
        try:
            temp_path = path.with_name(f"{path.name}.repair-{uuid.uuid4().hex[:8]}")
            with temp_path.open("w", encoding="utf-8") as handle:
                for line in good_lines:
                    handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except OSError:
            # Keep the original file rather than losing history; the marker
            # below still documents the damage and the next load retries.
            log.exception(
                "Belief Engine: failed to rewrite log %s with corrupt lines removed",
                path,
            )
        marker_turn = int(self._events[-1].get("turn", 0)) if self._events else 0
        fingerprint = hashlib.sha256(
            "".join(entry["sha256"] for entry in entries).encode("utf-8")
        ).hexdigest()[:12]
        # entity_type "log_integrity" is intentionally outside
        # BELIEF_ENTITY_TYPES: _reduce skips it, so the marker stays a pure
        # log-level audit record that never enters the projected model.
        self._append(
            "log.integrity",
            "log_integrity",
            {
                "id": f"log_integrity_{marker_turn}_{fingerprint}",
                "quarantined_line_count": len(entries),
                "quarantined_line_numbers": bad_line_numbers,
                "quarantined_lines": entries,
                "repaired_at": _now(),
            },
            turn=marker_turn,
        )
        log.warning(
            "Belief Engine: quarantined %d corrupt line(s) %s in %s; "
            "log rewritten atomically to %d intact line(s), "
            "log.integrity marker appended at turn %d",
            len(entries),
            bad_line_numbers,
            path,
            len(good_lines),
            marker_turn,
        )

    def _recover_orphaned_executing_decisions(self) -> None:
        """Downgrade executing decisions found at load time to retryable.

        "executing" means a caller is between authorize_action and the
        outcome recording. If that state survives a restart, the caller is
        gone and can never complete it, which used to deadlock the
        governance turn gate. Recovering to retryable keeps the obligation
        visible while making retry or cancellation possible again; the
        recovery updates are persisted so the intervention is auditable.
        """
        orphan_ids: list[str] = []
        last_event_turn = (
            int(self._events[-1].get("turn", 0)) if self._events else 0
        )
        for decision in self.list("decision", status=None):
            if decision.get("decision_state") != "executing":
                continue
            self.update(
                "decision",
                decision["id"],
                {
                    "decision_state": "retryable",
                    "recovery_reason": "process_restarted_during_execution",
                },
                turn=int(decision.get("execution_started_turn", last_event_turn)),
            )
            orphan_ids.append(decision["id"])
        if orphan_ids:
            log.warning(
                "Belief Engine: recovered %d orphaned executing decision(s) "
                "to retryable after restart: %s",
                len(orphan_ids),
                orphan_ids,
            )

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
            "epoch": self._epoch,
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity["id"],
            "entity": deepcopy(entity),
        }
        if changes:
            event["changes"] = changes
        with path.open("a") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            # A crash between write() and fsync() can leave a truncated final
            # line; flushing on every append shrinks that window and keeps the
            # on-disk log usable without waiting for interpreter shutdown.
            handle.flush()
            os.fsync(handle.fileno())
        self._events.append(event)
        self._reduce(event)
        self._pending_events.append(deepcopy(event))
        return event

    def drain_events(self) -> list[dict[str, Any]]:
        events = self._pending_events
        self._pending_events = []
        return events

    def record_graph_delta(self, delta: GraphDelta) -> GraphView:
        """Persist one derived delta and advance the current graph atomically."""

        if not isinstance(delta, GraphDelta):
            raise TypeError("delta must be GraphDelta")
        if delta.epoch != self._epoch:
            raise BeliefEngineError(
                f"graph delta epoch {delta.epoch} does not match current epoch {self._epoch}"
            )
        base = self._graph_view
        if base.epoch != self._epoch:
            base = GraphView.empty(epoch=self._epoch, turn=delta.turn)
        next_view = base.apply(delta)
        self._append(
            GRAPH_DELTA_EVENT,
            "graph_delta",
            {
                "id": f"graph_delta:{self._epoch}:{delta.snapshot_id}",
                "delta": delta.to_dict(),
                "state_hash": next_view.state_hash,
            },
            turn=delta.turn,
        )
        self._graph_view = next_view
        self._graph_replay_error = None
        return next_view

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
        _remove_fields: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        current = self._entities.get(entity_type, {}).get(entity_id)
        if not current:
            raise BeliefEngineError(f"Unknown {entity_type}: {entity_id}")
        if current.get("status") == "deleted":
            raise BeliefEngineError(f"Cannot update deleted {entity_type}: {entity_id}")
        protected = {"id", "entity_type", "created_turn", "created_at", "version"}
        remove_fields = set(_remove_fields)
        illegal = protected.intersection(set(patch) | remove_fields)
        if illegal:
            raise BeliefEngineError(f"Cannot update protected fields: {', '.join(sorted(illegal))}")
        overlap = set(patch).intersection(remove_fields)
        if overlap:
            raise BeliefEngineError(
                f"Cannot update and remove the same fields: {', '.join(sorted(overlap))}"
            )
        updated = deepcopy(current)
        changes: dict[str, Any] = {}
        for key in remove_fields:
            if key in updated:
                changes[key] = {"from": deepcopy(updated[key]), "to": None}
                del updated[key]
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
        decision_id: str | None = None,
        decision_route: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.bound:
            return None
        if category == "query" and success:
            normalized = normalize_tool_result(tool, result)
            result_ref = tool_result_reference(result)
            observation = self.create(
                "observation",
                {
                    "statement": normalized["facts"].get("summary")
                    or f"Observed result from {tool}",
                    "source": f"mcp:{tool}",
                    "source_params": deepcopy(params),
                    "result_ref": result_ref,
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
            executed = not result.startswith("BELIEF_GATE_REQUIRED")
            result_ref = tool_result_reference(result)
            action = self.create(
                "action",
                {
                    "statement": f"{tool} {'succeeded' if success else 'failed'}",
                    "tool": tool,
                    "params": deepcopy(params),
                    "result_ref": result_ref,
                    "success": success,
                    "duration_ms": duration_ms,
                    "selected_turn": turn,
                    "decision_id": decision_id,
                    "decision_route": decision_route,
                    "executed": executed,
                    "verification": {
                        "source": "tool_result",
                        "verified": success,
                    },
                },
                turn=turn,
            )
            # A successful action is factual evidence. Persist it as an
            # observation so predictions and plan conditions can be reviewed
            # without a second agent-side record_observation call.
            if success and executed:
                normalized = normalize_tool_result(tool, result)
                facts = deepcopy(normalized.get("facts") or {})
                facts.update({"action_success": True, "action_id": action["id"]})
                self.create(
                    "observation",
                    {
                        "statement": f"Observed result from {tool}",
                        "source": f"action:{tool}",
                        "facts": facts,
                        "metrics": deepcopy(normalized.get("metrics") or {}),
                        "reliability": 1.0,
                        "observed_turn": turn,
                        "tags": ["automatic", "action", tool],
                    },
                    turn=turn,
                )
            if decision_id and executed:
                self.create(
                    "outcome",
                    {
                        "statement": f"Outcome of {tool}: {'success' if success else 'failure'}",
                        "action_intent": {
                            "tool": tool,
                            "params": deepcopy(params),
                            "args_hash": action_args_hash(params),
                        },
                        "decision_id": decision_id,
                        "action_id": action["id"],
                        "success": success,
                        "observed_turn": turn,
                    },
                    turn=turn,
                )
                if self.get("decision", decision_id):
                    self.complete_action_authorization(
                        decision_id,
                        tool=tool,
                        success=success,
                        result=result,
                        turn=turn,
                    )
            self.review(turn=turn)
            return action
        return None

    @staticmethod
    def _action_matches(selected_action: Any, tool: str, params: dict[str, Any]) -> bool:
        """Match a decision to a concrete MCP call.

        Only structured action intents can authorize execution. Tool, exact
        parameter hash, and the parameter values must all match.
        """
        if isinstance(selected_action, dict):
            if str(selected_action.get("tool", "")).strip().lower() != tool.lower():
                return False
            expected = selected_action.get("params") or {}
            if not isinstance(expected, dict) or not all(
                params.get(key) == value for key, value in expected.items()
            ):
                return False
            expected_hash = selected_action.get("args_hash")
            return not expected_hash or expected_hash == action_args_hash(params)
        return False

    @staticmethod
    def _scope_conflicts(left: str, right: str) -> bool:
        left = (left or "global").strip().lower()
        right = (right or "global").strip().lower()
        return (
            "global" in {left, right}
            or left == right
            or left.startswith(right + ":")
            or right.startswith(left + ":")
        )

    def _evidence_requirements_satisfied(
        self,
        requirements: list[dict[str, Any]],
        *,
        after_sequence: int,
        turn: int,
    ) -> tuple[bool, list[dict[str, Any]]]:
        missing: list[dict[str, Any]] = []
        events = [
            event
            for event in self._events
            if event.get("entity_type") == "observation"
            and int(event.get("sequence", 0)) > after_sequence
        ]
        for requirement in requirements:
            tool = str(requirement.get("tool") or "").strip()
            expected_params = requirement.get("params") or {}
            metric_keys = (
                requirement.get("metric_keys")
                or requirement.get("required_metrics")
                or []
            )
            fact_keys = requirement.get("required_facts") or []
            minimum_sequence = max(
                after_sequence,
                int(requirement.get("min_observation_sequence", 0)),
            )
            max_age = requirement.get("max_age_turns", 0)
            matched = False
            for event in events:
                if int(event.get("sequence", 0)) <= minimum_sequence:
                    continue
                observation = event.get("entity") or {}
                if observation.get("source") != f"mcp:{tool}":
                    continue
                if not isinstance(expected_params, dict) or not all(
                    (observation.get("source_params") or {}).get(key) == value
                    for key, value in expected_params.items()
                ):
                    continue
                observed_turn = int(observation.get("observed_turn", event.get("turn", 0)))
                if isinstance(max_age, int) and max_age >= 0 and turn - observed_turn > max_age:
                    continue
                metrics = observation.get("metrics") or {}
                if not all(key in metrics for key in metric_keys):
                    continue
                facts = observation.get("facts") or {}
                if not all(key in facts for key in fact_keys):
                    continue
                matched = True
                break
            if not matched:
                missing.append(deepcopy(requirement))
        return not missing, missing

    def authorize_action(
        self,
        *,
        tool: str,
        params: dict[str, Any],
        turn: int,
        required: bool,
    ) -> dict[str, Any]:
        """Consume an explicit routed decision before a key MCP action."""
        brief = self.turn_brief(turn=turn)
        gate = brief["decision_gate"]
        decisions = [
            item
            for item in self.list("decision", status="active")
            if item.get("decision_state") in {"authorized", "retryable"}
            and self._authorization_valid_on_turn(item, turn=turn)
            and self._action_matches(item.get("action_intent"), tool, params)
        ]
        decision = decisions[0] if decisions else None
        if not required and decision is None:
            return {
                "authorized": True,
                "decision_id": None,
                "route": "routine",
                "decision_gate": gate,
            }
        if decision is None:
            return {
                "authorized": False,
                "decision_id": None,
                "route": gate.get("default_route", "fast"),
                "decision_gate": gate,
                "reason": (
                    f"No authorized belief decision for {tool}. Call "
                    "route_belief_decision with a structured action_intent "
                    "matching this tool and its exact arguments before retrying."
                ),
            }
        decision_scope = str(decision.get("gate_scope") or "global")
        blocking_scopes = gate.get("blocking_scopes") or []
        conflicting_scopes = [
            scope
            for scope in blocking_scopes
            if self._scope_conflicts(decision_scope, str(scope))
        ]
        if conflicting_scopes:
            return {
                "authorized": False,
                "decision_id": decision["id"],
                "route": "slow",
                "decision_gate": gate,
                "reason": (
                    "The action scope is blocked by unresolved governance gates: "
                    + ", ".join(conflicting_scopes)
                ),
            }
        if decision.get("route") == "slow":
            return {
                "authorized": False,
                "decision_id": decision["id"],
                "route": "slow",
                "decision_gate": gate,
                "reason": (
                    "The routed decision is slow: gather the missing evidence "
                    "and replan before executing this action."
                ),
            }
        if decision.get("route") == "verify_then_fast":
            decision_sequences = [
                int(event.get("sequence", 0))
                for event in self._events
                if event.get("entity_type") == "decision"
                and event.get("entity_id") == decision["id"]
            ]
            decision_sequence = max(decision_sequences, default=0)
            requirements = decision.get("evidence_requirements") or []
            if not requirements:
                return {
                    "authorized": False,
                    "decision_id": decision["id"],
                    "route": "verify_then_fast",
                    "decision_gate": gate,
                    "reason": (
                        "This decision requires explicit relevant evidence; route it "
                        "with evidence_requirements before executing."
                    ),
                }
            verified, missing = self._evidence_requirements_satisfied(
                requirements,
                after_sequence=decision_sequence,
                turn=turn,
            )
            if not verified:
                return {
                    "authorized": False,
                    "decision_id": decision["id"],
                    "route": "verify_then_fast",
                    "decision_gate": gate,
                    "missing_evidence": missing,
                    "reason": (
                        "This decision requires fresh, relevant game evidence first. "
                        "Run the exact get_* queries listed in missing_evidence, "
                        "then retry the action."
                    ),
                }

        executing = self.update(
            "decision",
            decision["id"],
            {
                "decision_state": "executing",
                "executing_action_tool": tool,
                "executing_args_hash": action_args_hash(params),
                "execution_attempt": int(decision.get("execution_attempt", 0)) + 1,
                "execution_started_turn": turn,
            },
            turn=turn,
        )
        return {
            "authorized": True,
            "decision_id": executing["id"],
            "route": executing.get("route", "fast"),
            "decision_gate": gate,
        }

    def complete_action_authorization(
        self,
        decision_id: str,
        *,
        tool: str,
        success: bool,
        result: str,
        turn: int,
    ) -> dict[str, Any]:
        """Finish an executing authorization after the game returns a result."""

        decision = self.get("decision", decision_id)
        if not decision:
            raise BeliefEngineError(f"Unknown decision: {decision_id}")
        if decision.get("decision_state") != "executing":
            return decision
        result_ref = tool_result_reference(result)
        if success:
            patch = {
                "status": "resolved",
                "decision_state": "succeeded",
                "completed_action_tool": tool,
                "completed_turn": turn,
                "execution_result_ref": result_ref,
            }
            obsolete_result_fields = (
                "execution_result",
                "last_failure",
                "last_failure_ref",
            )
        else:
            patch = {
                "decision_state": "retryable",
                "last_failed_action_tool": tool,
                "last_failed_turn": turn,
                "last_failure_ref": result_ref,
            }
            obsolete_result_fields = ("execution_result", "last_failure")
        return self.update(
            "decision",
            decision_id,
            patch,
            turn=turn,
            _remove_fields=obsolete_result_fields,
        )

    def cancel_action_authorization(
        self,
        decision_id: str,
        *,
        reason: str,
        turn: int,
    ) -> dict[str, Any]:
        """Explicitly close an unexecuted/retryable action with an audit outcome."""

        decision = self.get("decision", decision_id)
        if not decision:
            raise BeliefEngineError(f"Unknown decision: {decision_id}")
        if not isinstance(reason, str) or not reason.strip():
            raise BeliefEngineError("Cancellation reason must be non-empty")
        state = decision.get("decision_state")
        # Cancelling an executing decision is allowed even mid-turn: if the
        # outcome recording path itself failed (record exceptions are
        # swallowed by design), refusing here was the same-turn deadlock —
        # nothing but a process restart could clear the gate. Cancelling is
        # safe because complete_action_authorization no-ops once the decision
        # is no longer executing: a late result from a genuinely in-flight
        # action is still recorded as an action event, it just cannot
        # resurrect the authorization or its budget locks.
        if state == "succeeded":
            raise BeliefEngineError("Cannot cancel a succeeded action")
        if state == "cancelled":
            return decision
        action_intent = decision.get("action_intent")
        if not isinstance(action_intent, dict):
            raise BeliefEngineError("Decision has no structured action intent to cancel")
        updated = self.update(
            "decision",
            decision_id,
            {
                "status": "resolved",
                "decision_state": "cancelled",
                "cancelled_turn": turn,
                "cancellation_reason": reason.strip(),
            },
            turn=turn,
        )
        # Release budget locks reserved by the same council decision: a
        # cancelled authorization must not leave exclusive reservations
        # blocking later proposals until the next turn rolls over.
        council_id = decision.get("council_decision_id")
        if council_id:
            for lock in self.list("budget_lock", status="active"):
                if lock.get("council_decision_id") != council_id:
                    continue
                self.update(
                    "budget_lock",
                    lock["id"],
                    {
                        "status": "archived",
                        "released_turn": turn,
                        "release_reason": (
                            "decision_cancelled: "
                            + reason.strip()
                        ),
                    },
                    turn=turn,
                )
        self.create(
            "outcome",
            {
                "statement": f"Action intent cancelled: {reason.strip()}",
                "action_intent": deepcopy(action_intent),
                "decision_id": decision_id,
                "success": False,
                "executed": False,
                "cancelled": True,
                "result": reason.strip(),
                "observed_turn": turn,
            },
            turn=turn,
        )
        return updated

    def _current_epoch_max_turn(self) -> int | None:
        """Highest turn recorded in the current epoch, or None if empty."""

        return max(
            (
                int(event.get("turn", 0))
                for event in self._events
                if int(event.get("epoch", 1)) == self._epoch
            ),
            default=None,
        )

    def record_game_reload(
        self,
        *,
        reason: str,
        turn: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Mark the start of a new epoch after the game rolled back.

        Loading an autosave moves the game to an earlier turn while the
        append-only log still describes the abandoned future. The epoch
        marker makes that boundary explicit so replays can separate the two
        histories, and pending authorizations from the invalidated epoch are
        voided instead of being retried against a game state that no longer
        contains their motivation.
        """

        if self.path is None:
            return None
        prior_epoch_max_turn = self._current_epoch_max_turn()
        self._epoch += 1
        marker_turn = (
            turn
            if turn is not None
            else (prior_epoch_max_turn if prior_epoch_max_turn is not None else 0)
        )
        self._graph_view = GraphView.empty(epoch=self._epoch, turn=marker_turn)
        self._graph_replay_error = None
        # entity_type "epoch_marker" is intentionally outside
        # BELIEF_ENTITY_TYPES so the marker never enters the projected model.
        marker = self._append(
            "game.reloaded",
            "epoch_marker",
            {
                "id": f"epoch_{self._epoch}_{marker_turn}",
                "epoch": self._epoch,
                "reason": reason,
                "turn": marker_turn,
                "prior_epoch_max_turn": prior_epoch_max_turn,
                "details": deepcopy(details or {}),
            },
            turn=marker_turn,
        )
        voided_ids: list[str] = []
        for decision in self.list("decision", status=None):
            if decision.get("decision_state") not in {
                "authorized",
                "executing",
                "retryable",
            }:
                continue
            self.update(
                "decision",
                decision["id"],
                {
                    "status": "resolved",
                    "decision_state": "cancelled",
                    "cancellation_reason": f"invalidated_by_game_reload:{reason}",
                    "cancelled_turn": marker_turn,
                },
                turn=marker_turn,
            )
            voided_ids.append(decision["id"])
            # Mirror cancel_action_authorization: a voided decision must not
            # leave exclusive budget locks blocking later proposals.
            council_id = decision.get("council_decision_id")
            if not council_id:
                continue
            for lock in self.list("budget_lock", status="active"):
                if lock.get("council_decision_id") != council_id:
                    continue
                self.update(
                    "budget_lock",
                    lock["id"],
                    {
                        "status": "archived",
                        "released_turn": marker_turn,
                        "release_reason": f"decision_voided_by_reload:{reason}",
                    },
                    turn=marker_turn,
                )
        if voided_ids:
            log.warning(
                "Belief Engine: game reload (epoch %d, %s) voided %d pending "
                "authorization(s): %s",
                self._epoch,
                reason,
                len(voided_ids),
                voided_ids,
            )
        # Old-epoch facts describe a future that no longer happened: the
        # rolled-back game replays those turns differently. Archiving keeps
        # them queryable through history() while every status="active" read —
        # current_metrics, the turn gate's typed-snapshot lookup, and the
        # server's snapshot-reuse check — stays epoch-clean instead of
        # preferring pre-rollback observations with fresher observed_turn.
        for entity_type in ("observation", "world_entity"):
            for entity in self.list(entity_type, status="active"):
                self.update(
                    entity_type,
                    entity["id"],
                    {
                        "status": "archived",
                        "archived_turn": marker_turn,
                        "archived_reason": f"epoch_superseded_by_reload:{reason}",
                    },
                    turn=marker_turn,
                )
        return marker

    @staticmethod
    def _authorization_valid_on_turn(decision: dict[str, Any], *, turn: int) -> bool:
        """Return whether a pending authorization may execute on ``turn``.

        An intent with an explicit ``allowed_turn`` is dormant beforehand and
        expires afterwards.  An intent without one keeps the legacy same-turn
        authorization contract.  Stale pending decisions remain visible to the
        governance turn gate and must be cancelled or replaced; they are never
        silently executed against a later game state.
        """

        intent = decision.get("action_intent") or {}
        allowed_turn = intent.get("allowed_turn") if isinstance(intent, dict) else None
        if allowed_turn is not None:
            return type(allowed_turn) is int and allowed_turn == turn
        return decision.get("created_turn") == turn

    def ingest_typed_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        turn: int,
    ) -> dict[str, Any]:
        """Project a typed GameState snapshot into the existing event graph."""

        snapshot_id = str(snapshot.get("snapshot_id") or "")
        if not snapshot_id:
            raise BeliefEngineError("typed snapshot requires snapshot_id")
        if int(snapshot.get("turn_before", turn)) != int(snapshot.get("turn_after", turn)):
            raise BeliefEngineError("typed snapshot spans multiple turns")
        # A snapshot older than anything already recorded in this epoch means
        # the game itself rolled back (for example a manual autosave load that
        # skipped record_game_reload). Start a new epoch before projecting so
        # the two conflicting histories are never replayed as one.
        prior_epoch_max_turn = self._current_epoch_max_turn()
        if prior_epoch_max_turn is not None and prior_epoch_max_turn > int(turn):
            self.record_game_reload(
                reason="detected_turn_regression",
                turn=int(turn),
                details={
                    "prior_epoch_max_turn": prior_epoch_max_turn,
                    "observed_turn": int(turn),
                },
            )
        links_by_entity: dict[str, list[dict[str, Any]]] = {}
        for relation in snapshot.get("relations") or []:
            if not isinstance(relation, dict):
                raise BeliefEngineError("typed snapshot relations must be JSON objects")
            source_id = str(relation.get("source_id") or "")
            target_id = str(relation.get("target_id") or "")
            relation_type = str(relation.get("relation_type") or "")
            if not source_id or not target_id or not relation_type:
                raise BeliefEngineError(
                    "typed snapshot relation requires source_id, target_id, and relation_type"
                )
            attributes = deepcopy(relation.get("attributes") or {})
            links_by_entity.setdefault(source_id, []).append(
                {
                    "relation": relation_type,
                    "direction": "outgoing",
                    "entity_id": target_id,
                    "attributes": attributes,
                }
            )
            links_by_entity.setdefault(target_id, []).append(
                {
                    "relation": relation_type,
                    "direction": "incoming",
                    "entity_id": source_id,
                    "attributes": attributes,
                }
            )
        changed: list[str] = []
        current_entity_ids: set[str] = set()
        for node in snapshot.get("entities") or []:
            if not isinstance(node, dict) or not (node.get("id") or node.get("entity_id")):
                raise BeliefEngineError("typed snapshot entity requires a stable id")
            entity_id = str(node.get("id") or node.get("entity_id"))
            current_entity_ids.add(entity_id)
            payload = {
                "status": "active",
                "node_type": str(
                    node.get("node_type") or node.get("entity_type") or "entity"
                ),
                "attributes": deepcopy(node.get("attributes") or {}),
                "links": sorted(
                    deepcopy(node.get("links") or links_by_entity.get(entity_id, [])),
                    key=lambda item: (
                        str(item.get("relation")),
                        str(item.get("direction")),
                        str(item.get("entity_id")),
                    ),
                ),
                "snapshot_id": snapshot_id,
                "source": "game_state:typed",
                "observed_turn": turn,
            }
            before = self.get("world_entity", entity_id)
            if before and before.get("status") != "deleted":
                # An entity may disappear from one authoritative snapshot and
                # legitimately return later (for example a unit after a parser
                # recovery, or a tile when a unit moves back). Reactivation must
                # remove archival provenance rather than leave an active entity
                # carrying stale tombstone fields.
                tombstones = tuple(
                    key for key in before if key.startswith("archived_")
                )
                after = self.update(
                    "world_entity",
                    entity_id,
                    payload,
                    turn=turn,
                    _remove_fields=tombstones,
                )
            else:
                after = self.upsert("world_entity", entity_id, payload, turn=turn)
            if before is None or after.get("version") != before.get("version"):
                changed.append(entity_id)
        archived: list[str] = []
        for entity in self.list("world_entity", status="active"):
            if (
                entity.get("source") != "game_state:typed"
                or entity["id"] in current_entity_ids
            ):
                continue
            self.update(
                "world_entity",
                entity["id"],
                {
                    "status": "archived",
                    "archived_turn": turn,
                    "archived_reason": "absent_from_authoritative_typed_snapshot",
                    "snapshot_id": snapshot_id,
                },
                turn=turn,
            )
            archived.append(entity["id"])
        observation = self.create(
            "observation",
            {
                "statement": f"Typed GameState snapshot {snapshot_id}",
                "source": "game_state:typed_snapshot",
                "facts": {
                    "snapshot_id": snapshot_id,
                    "entity_count": len(snapshot.get("entities") or []),
                    "relation_count": len(snapshot.get("relations") or []),
                    "capabilities": deepcopy(snapshot.get("capabilities") or {}),
                },
                "metrics": deepcopy(snapshot.get("metrics") or {}),
                "reliability": 1.0,
                "observed_turn": turn,
                "tags": ["automatic", "typed", "game_state"],
            },
            turn=turn,
        )
        self.review(turn=turn)
        return {
            "snapshot_id": snapshot_id,
            "world_entities_changed": changed,
            "world_entities_archived": archived,
            "observation_id": observation["id"],
        }

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
        # 1. source 必须来自 get_combat_estimate 工具(单独报错,避免与缺字段混淆)
        if assessment.get("source") != "combat_estimate":
            raise BeliefEngineError(
                "combat assessment 校验失败: source 必须是 'combat_estimate' "
                f"(即 get_combat_estimate 工具返回的 source 字段), 实际为 "
                f"{assessment.get('source')!r}。\n"
                "用法: 先调用 get_combat_estimate(unit_id, target_x, target_y) 获取"
                "真实战斗预估, 再将其结果(含 source 字段)原样传给 assess_route_combat_risk。"
            )
        missing = sorted(required.difference(assessment))
        if missing:
            raise BeliefEngineError(
                "combat assessment 校验失败: 缺少必填字段 "
                f"{', '.join(missing)}。\n"
                "完整字段: "
                "source, revised_probability, attacker_cs, defender_cs, "
                "attacker_hp, defender_hp, expected_damage_to_attacker, "
                "expected_damage_to_defender。\n"
                "提示: 这些值应来自 get_combat_estimate 工具的返回。"
            )
        revised_probability = assessment["revised_probability"]
        try:
            _validate_probability("revised_probability", revised_probability)
        except BeliefEngineError as exc:
            raise BeliefEngineError(
                "combat assessment 校验失败: " + str(exc)
            ) from exc
        numeric_fields = required - {"source", "revised_probability"}
        bad_fields = [
            name
            for name in numeric_fields
            if isinstance(assessment[name], bool)
            or not isinstance(assessment[name], (int, float))
            or not math.isfinite(float(assessment[name]))
            or float(assessment[name]) < 0
        ]
        if bad_fields:
            raise BeliefEngineError(
                "combat assessment 校验失败: 以下字段必须是有限非负数字: "
                + ", ".join(bad_fields)
            )
        if assessment["attacker_hp"] <= 0 or assessment["defender_hp"] <= 0:
            raise BeliefEngineError(
                "combat assessment 校验失败: attacker_hp/defender_hp 必须大于 0, "
                f"当前 attacker_hp={assessment['attacker_hp']}, "
                f"defender_hp={assessment['defender_hp']}"
            )

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
                    "combat assessment 校验失败: 该战斗预估在数值上不支持下调路线信念。\n"
                    f"  当前信念 p={current_probability:.2f}, 申请下调至 {revised_probability:.2f}。\n"
                    f"  战力对比: defender_cs={assessment['defender_cs']} vs "
                    f"attacker_cs={assessment['attacker_cs']} "
                    f"({'防御方占优' if assessment['defender_cs'] > assessment['attacker_cs'] else '防御方未占优'})。\n"
                    f"  预期互伤比例: 攻击方损失 {attacker_loss:.0%}, "
                    f"防御方损失 {defender_loss:.0%} "
                    f"({'攻击方更亏' if attacker_loss > defender_loss else '攻击方未更亏'})。\n"
                    "  下调要求: 同时满足 defender_cs > attacker_cs 且 攻击方损失比例 > 防御方损失比例。"
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
        blocking_scopes: set[str] = set()
        for item in beliefs:
            if item.get("review_required"):
                blocking_scopes.add(str(item.get("gate_scope") or "global"))
        for item in plans:
            if item.get("status") == "needs_replan" or item.get("review_required"):
                blocking_scopes.add(str(item.get("gate_scope") or "global"))
        for item in contradictions:
            belief = self.get("belief", str(item.get("belief_id") or ""))
            blocking_scopes.add(
                str(item.get("gate_scope") or (belief or {}).get("gate_scope") or "global")
            )
        for item in surprises:
            if item.get("severity") in {"high", "major"}:
                blocking_scopes.add(str(item.get("gate_scope") or "global"))
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
                "blocking_scopes": sorted(blocking_scopes),
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
                        "gate_scope",
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
                        "gate_scope",
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

    def governance_turn_gate(self, *, turn: int) -> dict[str, Any]:
        """Return the non-bypassable governance obligations for ``turn``.

        A typed snapshot is the minimum decision context. Submitted proposals
        must be arbitrated, selected action intents must reach a successful
        outcome, and routed authorizations cannot be silently abandoned before
        ending the turn.
        """

        typed_snapshot = next(
            (
                item
                for item in self.list("observation", status="active")
                if item.get("source") == "game_state:typed_snapshot"
                and item.get("observed_turn") == turn
            ),
            None,
        )
        active_proposals = self.list("proposal", status="active")
        # An executing decision from a previous turn can never be completed by
        # its original caller: the action window has passed. Reclaim it to
        # retryable so the gate still demands resolution, but cancellation and
        # re-authorization become possible instead of deadlocking the turn.
        for item in self.list("decision", status=None):
            if item.get("decision_state") != "executing":
                continue
            if int(item.get("execution_started_turn", turn)) >= turn:
                continue
            self.update(
                "decision",
                item["id"],
                {
                    "decision_state": "retryable",
                    "recovery_reason": "stale_executing_reclaimed_turn_boundary",
                },
                turn=turn,
            )
        all_decisions = self.list("decision", status=None)

        def structured_action_intent(item: dict[str, Any]) -> dict[str, Any]:
            intent = item.get("action_intent")
            return intent if isinstance(intent, dict) else {}

        def intent_due(item: dict[str, Any], *, fallback_turn: int) -> bool:
            """Future intents are dormant until their allowed turn is reached."""

            intent = structured_action_intent(item) or item
            allowed_turn = (
                intent.get("allowed_turn") if isinstance(intent, dict) else None
            )
            if allowed_turn is None:
                return turn >= fallback_turn
            return type(allowed_turn) is int and turn >= allowed_turn

        pending_authorizations = [
            {
                "decision_id": item["id"],
                "decision_state": item.get("decision_state"),
                "created_turn": item.get("created_turn"),
                "allowed_turn": structured_action_intent(item).get("allowed_turn"),
            }
            for item in all_decisions
            if item.get("decision_state") in {"authorized", "executing", "retryable"}
            and intent_due(item, fallback_turn=int(item.get("created_turn", turn)))
        ]

        pending_council_intents: list[dict[str, Any]] = []
        approved_proposals = [
            item
            for item in self.list("proposal", status=None)
            if item.get("council_state") == "approved"
        ]
        for proposal in approved_proposals:
            council_id = proposal.get("council_decision_id")
            for intent in proposal.get("action_intents") or []:
                if not isinstance(intent, dict):
                    continue
                due_turn = intent.get("allowed_turn")
                fallback_turn = int(
                    proposal.get("last_updated_turn", proposal.get("created_turn", turn))
                )
                if not intent_due(intent, fallback_turn=fallback_turn):
                    continue
                intent_id = str(intent.get("intent_id") or "")
                matching = [
                    decision
                    for decision in all_decisions
                    if decision.get("council_decision_id") == council_id
                    and structured_action_intent(decision).get("proposal_id")
                    == proposal["id"]
                    and (
                        not intent_id
                        or structured_action_intent(decision).get("intent_id")
                        == intent_id
                    )
                ]
                terminal = next(
                    (
                        decision
                        for decision in matching
                        if decision.get("decision_state") in {"succeeded", "cancelled"}
                    ),
                    None,
                )
                if terminal is None:
                    matched = matching[0] if matching else None
                    pending_council_intents.append(
                        {
                            "proposal_id": proposal["id"],
                            "intent_id": intent_id or None,
                            "allowed_turn": due_turn,
                            "decision_id": matched.get("id") if matched else None,
                            "decision_state": (
                                matched.get("decision_state") if matched else "not_routed"
                            ),
                        }
                    )

        blockers: list[str] = []
        if typed_snapshot is None:
            blockers.append("current_turn_typed_snapshot_missing")
        if active_proposals:
            blockers.append("governance_proposals_not_arbitrated")
        if pending_council_intents:
            blockers.append("council_action_intents_not_completed")
        if pending_authorizations:
            blockers.append("routed_actions_not_completed")
        return {
            "turn": turn,
            "ready": not blockers,
            "typed_snapshot_id": (
                (typed_snapshot.get("facts") or {}).get("snapshot_id")
                if typed_snapshot
                else None
            ),
            "blockers": blockers,
            "active_proposal_ids": [item["id"] for item in active_proposals],
            "pending_council_intents": pending_council_intents,
            "pending_authorizations": pending_authorizations,
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
        action_intent: dict[str, Any] | None = None,
        evidence_requirements: list[dict[str, Any]] | None = None,
        gate_scope: str = "global",
        council_decision_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        _validate_probability("probability", probability)
        _validate_probability("confidence", confidence)
        _validate_probability("irreversibility", irreversibility)
        if action_intent is not None:
            if not isinstance(action_intent, dict):
                raise BeliefEngineError("action_intent must be a JSON object")
            allowed_turn = action_intent.get("allowed_turn")
            if allowed_turn is not None:
                if type(allowed_turn) is not int or allowed_turn < 0:
                    raise BeliefEngineError(
                        "action_intent allowed_turn must be a non-negative integer"
                    )
                if allowed_turn < turn:
                    raise BeliefEngineError(
                        f"action_intent allowed_turn {allowed_turn} has already expired"
                    )
        impact_score = _IMPACT_SCORE.get(impact.lower())
        urgency_score = _URGENCY_SCORE.get(urgency.lower())
        if impact_score is None or urgency_score is None:
            raise BeliefEngineError("impact and urgency must be low, medium, high, or critical")
        referenced = []
        for belief_id in belief_ids or []:
            belief = self.get("belief", belief_id)
            if not belief or belief.get("status") != "active":
                raise BeliefEngineError(
                    f"Referenced active belief not found: {belief_id}"
                )
            referenced.append(belief)

        # Agent estimates remain explicit inputs, but the current world model
        # now changes the route instead of serving as decorative provenance.
        # Conflicting priors and beliefs awaiting review increase uncertainty;
        # the engine never silently replaces the agent's probability.
        belief_probability = (
            sum(float(item.get("probability", 0.5)) for item in referenced)
            / len(referenced)
            if referenced
            else None
        )
        belief_confidence = (
            min(float(item.get("confidence", 0.0)) for item in referenced)
            if referenced
            else None
        )
        belief_disagreement = (
            min(1.0, abs(float(probability) - belief_probability))
            if belief_probability is not None
            else 0.0
        )
        belief_review_required = any(
            bool(item.get("review_required")) for item in referenced
        )
        uncertainty = max(
            1 - abs(probability - 0.5) * 2,
            1 - confidence,
            1 - belief_confidence if belief_confidence is not None else 0.0,
            belief_disagreement,
        )
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
            + surprise_score * 0.05
            + belief_disagreement * 0.1,
        )
        if belief_review_required:
            score = max(score, 0.75)
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
            "belief_context": {
                "referenced_probability": belief_probability,
                "referenced_confidence": belief_confidence,
                "disagreement": round(belief_disagreement, 4),
                "review_required": belief_review_required,
            },
            "active_surprise_score": surprise_score,
            "action_intent": deepcopy(action_intent),
            "evidence_requirements": deepcopy(evidence_requirements or []),
            "gate_scope": str(gate_scope or "global"),
            "council_decision_id": council_decision_id,
            "decision_state": "authorized" if action_intent else "unbound",
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

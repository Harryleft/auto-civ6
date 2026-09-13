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
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .canonical import arguments_hash
from .forecast import bayes
from .graph import (
    GRAPH_DELTA_EVENT,
    GraphDelta,
    GraphReplayError,
    GraphView,
    project_governance_state,
    replay_graph_events,
)
from .graph.model import thaw_json as _thaw_json

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
        "simulation",
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

# Fields of a ``decision`` that carry its authorization contract. They are set
# once by route_decision and are not patchable afterwards; see BeliefEngine.update.
_PROTECTED_DECISION_FIELDS = frozenset(
    {"action_intent", "council_decision_id", "args_hash"}
)
_RESULT_SUMMARY_CHARS = 500
# stale-knowledge：自动信念超过 N 回合未刷新时进入 turn_brief 提示
# （不阻断）。阈值取 derivation 归档阈值的一半：camp 5 → 3，rival 10 → 5。
_STALE_AUTO_BELIEF_TURNS = {
    "auto:belief:camp_threat:": 3,
    "auto:belief:rival_threat:": 5,
}
_STALE_REFRESH_TOOLS = {
    "auto:belief:camp_threat:": "get_barbarian_overview",
    "auto:belief:rival_threat:": "get_diplomacy",
}
_ACTION_OUTCOME_STATUSES = frozenset(
    {"succeeded", "failed", "unknown", "blocked"}
)
# The decision authorization lifecycle. ``governance_turn_gate`` treats only
# succeeded/cancelled as terminal, and every internal transition writes one of
# these values; anything else in ``decision_state`` came from an out-of-band
# patch and silently breaks both the gate and the recovery tools.
_DECISION_STATES = frozenset(
    {
        "unbound",
        "authorized",
        "executing",
        "succeeded",
        "retryable",
        "outcome_unknown",
        "cancelled",
    }
)
_TERMINAL_DECISION_STATES = frozenset({"succeeded", "cancelled"})
# States that terminally close a council intent.  ``failed`` is not a
# sanctioned lifecycle state (only pre-a0b487c journals contain it, written
# out-of-band), but for those legacy records the failure IS final accounting:
# nothing can re-open it through sanctioned paths, so the intent it bound is
# closed.  ``retryable`` deliberately stays out — retry remains rational and
# the agent must answer the gate (retry or cancel).
_INTENT_CLOSING_STATES = frozenset({"succeeded", "cancelled", "failed"})
_GOVERNANCE_GRAPH_ENTITY_TYPES = frozenset(
    {
        "observation",
        "belief",
        "goal",
        "proposal",
        "critic_review",
        "council_decision",
        "budget_lock",
        "decision",
        "action",
        "outcome",
        "hypothesis",
        "prediction",
        "plan",
        "surprise",
        "contradiction",
        "attribution",
        "simulation",
    }
)


class BeliefEngineError(ValueError):
    """Raised when an invalid belief-engine operation is requested."""


from .ids import slugify as _slug


def _now() -> float:
    return time.time()


def _coerce_number(value: str) -> int | float:
    number = float(value.replace(",", ""))
    return int(number) if number.is_integer() else number


def _result_summary(result: str) -> str:
    match = re.search(r"(?m)^[ \t]*(\S[^\r\n]*)", result)
    return match.group(1).strip()[:_RESULT_SUMMARY_CHARS] if match else ""


def _parse_fact_envelope(result: str) -> dict[str, Any] | None:
    """若结果为 civ_mcp.facts 单轨信封则返回其 dict，否则返回 None。"""
    try:
        payload = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != 1 or not isinstance(payload.get("facts"), dict):
        return None
    return payload


# 仍保留纯文本正则分支的工具只有 get_game_overview（观测可靠性 0.9）；
# 其余工具走信封字段级提取（1.0）或纯摘要（0.8）。
_RELIABLE_TOOLS = frozenset(
    {
        "get_game_overview",
        "get_diplomacy",
        "get_combat_estimate",
        "get_cities",
        "get_units",
        "get_tech_civics",
        "get_barbarian_overview",
        "get_victory_progress",
        "get_great_people_overview",
    }
)


def _envelope_observation_facts(
    tool: str, envelope: dict[str, Any]
) -> dict[str, Any]:
    """从单轨信封的结构化 facts 生成 observation facts（保持既有键名）。

    值来自字段级 schema，不依赖任何文本格式；叙述正则路径的解析损失
    （例如 999 距离被叙述成 "no city distance" 而丢失）在这里不存在。
    """
    payload = envelope.get("facts") or {}
    facts: dict[str, Any] = {}
    if tool == "get_units":
        own = payload.get("own_units") or []
        facts["unit_ids"] = [int(u["unit_id"]) for u in own if "unit_id" in u]
        for u in own:
            if "unit_id" in u and "x" in u and "y" in u:
                facts[f"unit_position:{int(u['unit_id'])}"] = [
                    int(u["x"]),
                    int(u["y"]),
                ]
    elif tool == "get_cities":
        cities = payload.get("cities") or []
        facts["cities"] = [
            {
                "name": c.get("name", ""),
                "population": int(c.get("population", 0)),
                "x": int(c.get("x", 0)),
                "y": int(c.get("y", 0)),
            }
            for c in cities
        ]
    elif tool == "get_barbarian_overview":
        camps = payload.get("camps") or []
        if camps:
            facts["barbarian_camps"] = [
                {
                    "x": int(c["x"]),
                    "y": int(c["y"]),
                    "visibility": c.get("visibility", "revealed"),
                    "priority": (
                        "CRITICAL"
                        if c.get("distance_to_city", 999) <= 5
                        else "HIGH"
                        if c.get("distance_to_city", 999) <= 10
                        else "WATCH"
                    ),
                    "distance_to_city": c.get("distance_to_city"),
                    "distance_to_military": c.get("distance_to_military"),
                }
                for c in camps
            ]
    elif tool == "get_diplomacy":
        civs = payload.get("civs") or []
        rivals: dict[str, dict[str, Any]] = {}
        for civ in civs:
            if not civ.get("has_met"):
                continue
            key = f"player_{civ.get('player_id')}"
            rivals[key] = {
                "civilization": civ.get("civ_name", ""),
                "leader": civ.get("leader_name", ""),
                "state": civ.get("diplomatic_state", "UNKNOWN"),
                "relationship_score": int(civ.get("relationship_score", 0)),
                "at_war": bool(civ.get("is_at_war", False)),
            }
            if civ.get("military_strength"):
                rivals[key]["military"] = int(civ["military_strength"])
            if civ.get("num_cities"):
                rivals[key]["cities"] = int(civ["num_cities"])
                # known-gap：已知存在但未观测的城市数（迷雾盲区占位）。
                # visible_cities 为空列表 → 全部未观测（与叙述 "all in fog" 一致）。
                visible = civ.get("visible_cities") or []
                visible_count = (
                    len(visible) if isinstance(visible, list) else 0
                )
                rivals[key]["visible_cities"] = visible_count
                rivals[key]["unobserved_cities"] = max(
                    0, int(civ["num_cities"]) - visible_count
                )
        if rivals:
            facts["rivals"] = rivals
    elif tool == "get_combat_estimate":
        estimate = payload.get("estimate") or {}
        if estimate:
            facts["matchup"] = {
                "attacker_type": estimate.get("attacker_type", ""),
                "defender_type": estimate.get("defender_type", ""),
            }
    elif tool == "get_great_people_overview":
        standings = payload.get("standings") or []
        classes: list[dict[str, Any]] = []
        for standing in standings:
            entries = standing.get("entries") or []
            if not entries:
                continue
            ours = entries[0]  # official order: self first
            entry: dict[str, Any] = {
                "class": standing.get("class_name", ""),
                "our_points": int(ours.get("points_total", -1)),
                "our_per_turn": int(ours.get("points_per_turn", -1)),
            }
            leader = max(
                (
                    e
                    for e in entries[1:]
                    if str(e.get("player_name", "")) != "YOU"
                ),
                key=lambda e: int(e.get("points_total", -1)),
                default=None,
            )
            if (
                leader is not None
                and int(leader.get("points_total", -1))
                > int(ours.get("points_total", -1))
            ):
                entry.update(
                    {
                        "leader_name": leader.get("player_name", ""),
                        "leader_points": int(leader.get("points_total", -1)),
                        "lead_gap": int(leader.get("points_total", -1))
                        - int(ours.get("points_total", -1)),
                    }
                )
            else:
                entry.update(
                    {
                        "leader_name": "YOU",
                        "leader_points": int(ours.get("points_total", -1)),
                        "lead_gap": 0,
                    }
                )
            classes.append(entry)
        if classes:
            facts["great_people_classes"] = classes
    elif tool == "get_tech_civics":
        # 结构化信封携带当前研究/市政与完成数（字段级，无解析损失）。
        if payload.get("current_research"):
            facts["current_research"] = payload["current_research"]
        if payload.get("current_civic"):
            facts["current_civic"] = payload["current_civic"]
        if isinstance(payload.get("completed_tech_count"), int):
            facts["completed_tech_count"] = payload["completed_tech_count"]
        if isinstance(payload.get("completed_civic_count"), int):
            facts["completed_civic_count"] = payload["completed_civic_count"]
    elif tool == "get_victory_progress":
        enabled = payload.get("enabled_victories")
        if isinstance(enabled, list):
            facts["enabled_victories"] = [str(item) for item in enabled if item]
    return facts


def _envelope_observation_metrics(
    tool: str, envelope: dict[str, Any]
) -> dict[str, Any]:
    """从信封的结构化 facts 直接产出 observation metrics。

    键名与旧叙述正则路径保持一致，避免派生规则（derivation）与覆盖审计
    的既有消费者感知到差异。
    """
    payload = envelope.get("facts") or {}
    metrics: dict[str, Any] = {}
    if tool == "get_units":
        own = payload.get("own_units") or []
        metrics["observed_unit_count"] = len(own)
    elif tool == "get_cities":
        cities = payload.get("cities") or []
        metrics["observed_city_count"] = len(cities)
    elif tool == "get_diplomacy":
        our_military = payload.get("our_military")
        if isinstance(our_military, int):
            metrics["our_military"] = our_military
        for civ in payload.get("civs") or []:
            if not civ.get("has_met"):
                continue
            key = f"player_{civ.get('player_id')}"
            metrics[f"diplomacy.{key}.relationship_score"] = int(
                civ.get("relationship_score", 0)
            )
            metrics[f"diplomacy.{key}.at_war"] = bool(civ.get("is_at_war", False))
            if civ.get("military_strength"):
                metrics[f"diplomacy.{key}.military"] = int(
                    civ["military_strength"]
                )
            if civ.get("num_cities"):
                metrics[f"diplomacy.{key}.cities"] = int(civ["num_cities"])
                visible = civ.get("visible_cities") or []
                visible_count = len(visible) if isinstance(visible, list) else 0
                metrics[f"diplomacy.{key}.visible_cities"] = visible_count
                # known-gap：已知存在但未观测的城市数（迷雾盲区）。
                metrics[f"diplomacy.{key}.unobserved_cities"] = max(
                    0, int(civ["num_cities"]) - visible_count
                )
    elif tool == "get_combat_estimate":
        estimate = payload.get("estimate") or {}
        if estimate:
            for metric_key, field in (
                ("combat.attacker_cs", "attacker_cs"),
                ("combat.attacker_hp", "attacker_hp"),
                ("combat.defender_cs", "defender_cs"),
                ("combat.defender_hp", "defender_hp"),
                ("combat.expected_damage_to_defender", "est_damage_to_defender"),
                ("combat.expected_damage_to_attacker", "est_damage_to_attacker"),
            ):
                value = estimate.get(field)
                if isinstance(value, int):
                    metrics[metric_key] = value
            # Ranged estimates intentionally omit retaliation damage; zero is
            # an observed property of a valid preview, not missing evidence.
            metrics.setdefault("combat.expected_damage_to_attacker", 0)
    elif tool == "get_tech_civics":
        if payload.get("current_research"):
            metrics["research.current"] = payload["current_research"]
        if isinstance(payload.get("current_research_turns"), int):
            metrics["research.turns_remaining"] = payload[
                "current_research_turns"
            ]
        if payload.get("current_civic"):
            metrics["civic.current"] = payload["current_civic"]
        if isinstance(payload.get("current_civic_turns"), int):
            metrics["civic.turns_remaining"] = payload["current_civic_turns"]
        if isinstance(payload.get("completed_tech_count"), int):
            metrics["research.completed_techs"] = payload["completed_tech_count"]
        if isinstance(payload.get("completed_civic_count"), int):
            metrics["civics.completed_civics"] = payload[
                "completed_civic_count"
            ]
    elif tool == "get_barbarian_overview":
        camps = payload.get("camps") or []
        if camps:
            metrics["barbarian.camp_count"] = len(camps)
            distances = [
                camp["distance_to_city"]
                for camp in camps
                if isinstance(camp.get("distance_to_city"), int)
            ]
            if distances:
                metrics["barbarian.nearest_camp_distance"] = min(distances)
    elif tool == "get_victory_progress":
        for player in payload.get("players") or []:
            slug = _slug(str(player.get("name") or "")).lower()
            if not slug:
                continue
            science_vp = player.get("science_vp")
            science_vp_needed = player.get("science_vp_needed")
            if isinstance(science_vp, int) and isinstance(
                science_vp_needed, int
            ):
                metrics[f"victory.{slug}.science_vp"] = science_vp
                metrics[f"victory.{slug}.science_vp_target"] = science_vp_needed
            diplomatic_vp = player.get("diplomatic_vp")
            if isinstance(diplomatic_vp, int):
                metrics[f"victory.{slug}.diplomatic_vp"] = diplomatic_vp
                metrics[f"victory.{slug}.diplomatic_vp_target"] = 20
            for suffix, field in (
                ("tourism", "tourism"),
                ("military", "military_strength"),
                ("techs", "techs_researched"),
                ("cities", "num_cities"),
                ("science", "science_yield"),
                ("culture", "culture_yield"),
                ("gold_per_turn", "gold_yield"),
                ("score", "score"),
            ):
                value = player.get(field)
                if isinstance(value, (int, float)):
                    metrics[f"victory.{slug}.{suffix}"] = value
    elif tool == "get_great_people_overview":
        for standing in payload.get("standings") or []:
            entries = standing.get("entries") or []
            if not entries:
                continue
            slug = _slug(str(standing.get("class_name") or "")).lower()
            if not slug:
                continue
            ours = entries[0]
            metrics[f"great_people.{slug}.our_points"] = int(
                ours.get("points_total", -1)
            )
            metrics[f"great_people.{slug}.our_per_turn"] = int(
                ours.get("points_per_turn", -1)
            )
            leader = max(
                (
                    e
                    for e in entries[1:]
                    if str(e.get("player_name", "")) != "YOU"
                ),
                key=lambda e: int(e.get("points_total", -1)),
                default=None,
            )
            if (
                leader is not None
                and int(leader.get("points_total", -1))
                > int(ours.get("points_total", -1))
            ):
                metrics[f"great_people.{slug}.leader_points"] = int(
                    leader.get("points_total", -1)
                )
                metrics[f"great_people.{slug}.lead_gap"] = (
                    int(leader.get("points_total", -1))
                    - int(ours.get("points_total", -1))
                )
    return metrics


def normalize_tool_result(tool: str, result: str) -> dict[str, Any]:
    """Extract stable facts/metrics from MCP query results.

    Raw text remains owned by the tool transcript and telemetry. This
    normalizer intentionally extracts only values with unambiguous contracts;
    interpretations belong in beliefs, not observations.

    Envelope results (civ_mcp.facts) feed both facts and metrics from the
    structured payload (reliability 1.0); the only live plain-text result
    (get_game_overview) falls back to the regex path below.
    """

    envelope = _parse_fact_envelope(result)
    if envelope is not None and envelope.get("tool") == tool:
        facts = {"tool": tool}
        facts.update(_envelope_observation_facts(tool, envelope))
        return {
            "facts": facts,
            "metrics": _envelope_observation_metrics(tool, envelope),
            "reliability": 1.0,  # 字段级 schema，无解析损失
        }

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
            ("era.dark_threshold", r"\(Dark:\s*(\d+)"),
            ("era.golden_threshold", r"Golden:\s*(\d+)"),
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
        # Research/civic names and era thresholds feed the derivation rules;
        # overview-level names let timing predictions track subject switches
        # without waiting for a dedicated get_tech_civics call.
        research_match = re.search(r"^Research:\s*(.*?)\s*\|", result, re.MULTILINE)
        if research_match:
            metrics["research.current"] = research_match.group(1)
        civic_match = re.search(r"\|\s*Civic:\s*(\S.*?)\s*$", result, re.MULTILINE)
        if civic_match:
            metrics["civic.current"] = civic_match.group(1)
        era_match = re.search(r"^Era:\s*([^|]+?)\s*\|", result, re.MULTILINE)
        if era_match:
            metrics["era"] = era_match.group(1)

    # --- 以下为纯文本结果的兼容解析（回退路径）---
    # 实时工具结果全部是信封；纯文本只出现在 get_game_overview（主路径）与
    # 历史/降级场景。解析结果与原叙述轨保持相同键，供派生规则与覆盖审计消费。
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
                    # known-gap：从叙述行的 "+ N in fog" / "(all in fog)" 提取
                    # 未观测城市数，让"已知存在但未见"成为显式事实。
                    fog = re.search(r"\+ (\d+) in fog", line)
                    if fog:
                        hidden = int(fog.group(1))
                        visible = max(0, city_count - hidden)
                        rivals[current_key]["unobserved_cities"] = hidden
                        metrics[
                            f"diplomacy.{current_key}.unobserved_cities"
                        ] = hidden
                    elif "all in fog" in line:
                        rivals[current_key]["unobserved_cities"] = city_count
                        metrics[
                            f"diplomacy.{current_key}.unobserved_cities"
                        ] = city_count
                        visible = 0
                    else:
                        visible = city_count
                    rivals[current_key]["visible_cities"] = visible
                    metrics[
                        f"diplomacy.{current_key}.visible_cities"
                    ] = visible
        if rivals:
            facts["rivals"] = rivals

    elif tool == "get_combat_estimate":
        matchup = re.search(
            r"^\s*(.+?) \(CS:(\d+), HP:(\d+)\) vs "
            r"(.+?) \(CS:(\d+), HP:(\d+)\)",
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
        for x, y, unit_id in re.findall(
            r"at \((-?\d+),(-?\d+)\).*?\[id:(\d+)",
            result,
        ):
            facts[f"unit_position:{unit_id}"] = [int(x), int(y)]

    elif tool == "get_tech_civics":
        researching = re.search(
            r"^Researching:\s*(.+?)\s*\((\d+)\s*turns\)", result, re.MULTILINE
        )
        if researching:
            metrics["research.current"] = researching.group(1)
            metrics["research.turns_remaining"] = int(researching.group(2))
        civic = re.search(
            r"^Civic:\s*(.+?)\s*\((\d+)\s*turns\)", result, re.MULTILINE
        )
        if civic:
            metrics["civic.current"] = civic.group(1)
            metrics["civic.turns_remaining"] = int(civic.group(2))
        completed = re.search(
            r"Completed:\s*(\d+)\s*techs,\s*(\d+)\s*civics", result
        )
        if completed:
            metrics["research.completed_techs"] = int(completed.group(1))
            metrics["civics.completed_civics"] = int(completed.group(2))

    elif tool == "get_barbarian_overview":
        camp_re = re.compile(
            r"^ {2}\[(CRITICAL|HIGH|WATCH)\]\s+\((-?\d+),(-?\d+)\)\s+\[(\w+)\]\s+—\s+(.+)$",
            re.MULTILINE,
        )
        camps: list[dict[str, Any]] = []
        for match in camp_re.finditer(result):
            priority, x, y, visibility, rest = match.groups()
            city = re.match(r"(\d+) tiles from nearest city", rest)
            military = re.search(r"(\d+) from nearest military", rest)
            camps.append(
                {
                    "x": int(x),
                    "y": int(y),
                    "visibility": visibility,
                    "priority": priority,
                    "distance_to_city": int(city.group(1)) if city else None,
                    "distance_to_military": int(military.group(1)) if military else None,
                }
            )
        if camps:
            facts["barbarian_camps"] = camps
            metrics["barbarian.camp_count"] = len(camps)
            distances = [
                camp["distance_to_city"]
                for camp in camps
                if camp["distance_to_city"] is not None
            ]
            if distances:
                metrics["barbarian.nearest_camp_distance"] = min(distances)

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

    elif tool == "get_great_people_overview":
        # Standings rows: "  {class}: YOU {pts}/{ppt} ({n}) | {rival} {pts}/{ppt} ({n}) | ..."
        # First entry is self; the rest are rivals sorted by points desc.
        cell_re = re.compile(r"^(.+?) (\d+)/(\d+) \((\d+)\)$")
        for line in result.splitlines():
            header = re.match(r"^  ([^:]+): (.*)$", line)
            if not header:
                continue
            cells: list[dict[str, Any]] = []
            for cell in header.group(2).split(" | "):
                match = cell_re.match(cell.strip())
                if match:
                    cells.append(
                        {
                            "who": match.group(1),
                            "points": int(match.group(2)),
                            "per_turn": int(match.group(3)),
                            "instances": int(match.group(4)),
                        }
                    )
            if not cells:
                continue
            ours = cells[0]  # official order: self first
            leader = max(
                (c for c in cells[1:] if c["who"] != "YOU"),
                key=lambda c: c["points"],
                default=None,
            )
            slug = _slug(header.group(1)).lower()
            metrics[f"great_people.{slug}.our_points"] = ours["points"]
            metrics[f"great_people.{slug}.our_per_turn"] = ours["per_turn"]
            class_fact: dict[str, Any] = {
                "class": header.group(1).strip(),
                "our_points": ours["points"],
                "our_per_turn": ours["per_turn"],
            }
            if leader is not None and leader["points"] > ours["points"]:
                gap = leader["points"] - ours["points"]
                class_fact.update(
                    {
                        "leader_name": leader["who"],
                        "leader_points": leader["points"],
                        "lead_gap": gap,
                    }
                )
                metrics[f"great_people.{slug}.leader_points"] = leader["points"]
                metrics[f"great_people.{slug}.lead_gap"] = gap
            else:
                class_fact.update(
                    {"leader_name": "YOU", "leader_points": ours["points"], "lead_gap": 0}
                )
            facts.setdefault("great_people_classes", []).append(class_fact)

    return {
        "facts": facts,
        "metrics": metrics,
        "reliability": (
            0.9 if tool in _RELIABLE_TOOLS else 0.8  # 正则提取 vs 纯摘要
        ),
    }


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
        "simulation": ("branch_label", "scenario", "projections"),
    }
    missing = [field for field in required[entity_type] if entity.get(field) in (None, "")]
    if missing:
        raise BeliefEngineError(
            f"Missing required {entity_type} fields: {', '.join(missing)}"
        )
    if entity_type == "belief":
        # 信念必须有事实基础：要么引用观测证据，要么显式声明未知基础
        # （unknown_basis）。拒绝"既无证据又无声明"的信念——引擎无法
        # 验证其语义，必须让调用方承认这一点。
        if not (entity.get("evidence_ids") or []) and not entity.get(
            "unknown_basis"
        ):
            raise BeliefEngineError(
                "belief requires evidence_ids (observation references) or "
                "unknown_basis=true to declare an unverified basis"
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


def action_args_hash(params: dict[str, Any]) -> str:
    """Stable identity of an action's arguments.

    Delegates to the domain-wide canonical hash so the value the council
    approves and the value execution verifies can never drift apart.
    """

    return arguments_hash(params)


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


def default_beliefs_directory() -> Path:
    """Where journals live when a caller does not pick a directory.

    A seam rather than an inline expression: tests redirect it so that an
    engine constructed without ``directory=`` can never append to the user's
    real evidence journal.
    """

    return Path.home() / ".civ6-mcp" / "beliefs"


try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


# One exclusive OS lock per journal, per process. Keyed by path because
# reloading a journal inside one process (a restart, or a test rebinding the
# same directory) is legitimate; a *second process* appending to the same
# append-only file is not — it would interleave sequence numbers and, because
# the loader rewrites the file when it meets a torn line, could drop events.
_JOURNAL_LOCKS: dict[Path, Any] = {}


def _acquire_journal_lock(path: Path) -> None:
    """Take an exclusive advisory lock, or raise if another process holds it."""

    if path in _JOURNAL_LOCKS:
        return
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:  # pragma: no cover - Windows
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - no locking primitive available
            log.warning(
                "No file-locking primitive available; concurrent writers to %s "
                "cannot be detected",
                path,
            )
    except OSError as exc:
        handle.close()
        raise BeliefEngineError(
            "another process is already writing this belief journal "
            f"({path}); FireTuner and the belief journal both allow only one "
            "writer — stop the other client first"
        ) from exc
    _JOURNAL_LOCKS[path] = handle


class BeliefEngine:
    """Persistent current-world model backed by an append-only event log."""

    def __init__(self, run_id: str, directory: Path | None = None) -> None:
        self.run_id = run_id
        self.directory = directory or default_beliefs_directory()
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
        # Sequence through which the in-memory graph has been materialized.
        # Lazy compatibility migration must not append a journal event merely
        # because a caller performs a read; explicit pipeline syncs still
        # persist graph.delta events for replay and audit.
        self._graph_materialized_sequence = 0
        self._graph_persisted_sequence = 0
        # Derived read models keyed by journal sequence. Every append advances
        # ``_sequence`` (and a load recomputes it), so a write invalidates both
        # caches without any explicit bookkeeping. ``review()`` appends events,
        # which is exactly why the turn-brief cache is keyed *after* it runs.
        # Newest governance-typed event sequence, maintained incrementally by
        # _reduce so _latest_governance_sequence stays O(1).
        self._governance_sequence = 0
        # (entity_type, entity_id) -> epoch of that entity's first event.
        self._entity_creation_epoch: dict[tuple[str, str], int] = {}
        self._metrics_sequence: int | None = None
        self._metrics_cache: dict[str, Any] | None = None
        self._review_key: tuple[int, int] | None = None
        self._review_cache: dict[str, Any] | None = None
        self._gate_key: tuple[int, int] | None = None
        self._gate_cache: dict[str, Any] | None = None
        self._turn_brief_key: tuple[int, int, int] | None = None
        self._turn_brief_cache: dict[str, Any] | None = None

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
        # Fail loudly if another process already owns this journal, instead of
        # interleaving sequence numbers and losing events to the loader's
        # repair rewrite.
        _acquire_journal_lock(self.path)
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
        self._graph_materialized_sequence = 0
        self._graph_persisted_sequence = 0
        self._governance_sequence = 0
        self._entity_creation_epoch = {}
        self._metrics_sequence = None
        self._metrics_cache = None
        self._review_key = None
        self._review_cache = None
        self._gate_key = None
        self._gate_cache = None
        self._turn_brief_key = None
        self._turn_brief_cache = None
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
            self._graph_materialized_sequence = max(
                (
                    int(event.get("sequence", 0))
                    for event in self._events
                    if event.get("event_type") == GRAPH_DELTA_EVENT
                ),
                default=0,
            )
            self._graph_persisted_sequence = self._graph_materialized_sequence
        except GraphReplayError as exc:
            # The graph is a derived read model. A damaged graph event must be
            # visible, but it must not make the existing belief model unusable.
            self._graph_view = GraphView.empty(epoch=self._epoch)
            self._graph_replay_error = str(exc)
            self._graph_materialized_sequence = 0
            self._graph_persisted_sequence = 0
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
        # Advance the governance cursor before the entity-store check: it must
        # track every governance-typed event, including ones whose payload is
        # not a reducible entity. Keeping it incremental turns what used to be
        # an O(events) scan on the hot path into a comparison.
        if entity_type in _GOVERNANCE_GRAPH_ENTITY_TYPES:
            try:
                sequence = int(event.get("sequence", 0))
            except (TypeError, ValueError):
                sequence = 0
            if sequence > self._governance_sequence:
                self._governance_sequence = sequence
        if entity_type not in self._entities or not isinstance(entity, dict):
            return
        entity_id = entity.get("id")
        if entity_id:
            # First event wins: events stream in file order, so the first write
            # for a key records the epoch the entity was created in.
            key = (entity_type, entity_id)
            if key not in self._entity_creation_epoch:
                try:
                    self._entity_creation_epoch[key] = int(event.get("epoch", 1))
                except (TypeError, ValueError):
                    self._entity_creation_epoch[key] = 1
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

    def record_graph_delta(self, delta: GraphDelta, *, kind: str = "world") -> GraphView:
        """Persist one derived delta and advance the current graph atomically.

        ``kind`` distinguishes the delta stream (``world``/``goals``) so two
        deltas recorded for the same snapshot cannot share one event id —
        audit searches would otherwise conflate them.
        """

        if not isinstance(delta, GraphDelta):
            raise TypeError("delta must be GraphDelta")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("kind must be a non-empty string")
        if delta.epoch != self._epoch:
            raise BeliefEngineError(
                f"graph delta epoch {delta.epoch} does not match current epoch {self._epoch}"
            )
        normalized_kind = kind.strip()
        # A lazy read may have materialized governance nodes in memory without
        # writing a graph.delta. Persist that derived state before appending a
        # world/goal delta; otherwise the latter's state_hash would depend on
        # in-memory nodes that a fresh replay cannot reconstruct.
        if (
            not normalized_kind.startswith("governance:")
            and self.governance_graph_materialized()
            and self._graph_persisted_sequence < self._latest_governance_sequence()
        ):
            self._persist_current_governance_graph(turn=delta.turn)
        base = self._graph_view
        if base.epoch != self._epoch:
            base = GraphView.empty(epoch=self._epoch, turn=delta.turn)
        next_view = base.apply(delta)
        event = self._append(
            GRAPH_DELTA_EVENT,
            "graph_delta",
            {
                "id": f"graph_delta:{normalized_kind}:{self._epoch}:{delta.snapshot_id}",
                "kind": normalized_kind,
                "delta": delta.to_dict(),
                "state_hash": next_view.state_hash,
            },
            turn=delta.turn,
        )
        self._graph_view = next_view
        self._graph_materialized_sequence = int(event["sequence"])
        self._graph_persisted_sequence = int(event["sequence"])
        self._graph_replay_error = None
        return next_view

    def _persist_current_governance_graph(self, *, turn: int) -> GraphView:
        """Persist governance state that was materialized only in memory."""

        try:
            persisted_base = replay_graph_events(self._events, epoch=self._epoch)
        except GraphReplayError as exc:
            raise BeliefEngineError(
                "cannot persist governance GraphView after replay failure: " + str(exc)
            ) from exc
        snapshot_id = (
            persisted_base.snapshot_id
            or self._graph_view.snapshot_id
            or f"belief:{self.game_id}:{turn}"
        )
        graph_turn = (
            max(persisted_base.turn, turn)
            if persisted_base.snapshot_id
            else turn
        )
        entities = {
            entity_type: self.list(entity_type, status=None)
            for entity_type in _GOVERNANCE_GRAPH_ENTITY_TYPES
        }
        delta = project_governance_state(
            entities,
            previous=persisted_base,
            snapshot_id=snapshot_id,
            turn=graph_turn,
            epoch=self._epoch,
        )
        if not (
            delta.upsert_nodes
            or delta.upsert_edges
            or delta.remove_node_ids
            or delta.remove_edge_keys
        ):
            self._graph_persisted_sequence = self._latest_governance_sequence()
            return self._graph_view
        return self.record_graph_delta(
            delta,
            kind=f"governance:{self._sequence + 1}",
        )

    def sync_governance_graph(
        self,
        *,
        turn: int,
        persist: bool = True,
    ) -> GraphView:
        """Materialize current governance entities into the canonical graph.

        ``persist=False`` is reserved for lazy migration of an old journal
        during a read. It updates the in-memory read model without turning a
        read into a journal write; the next explicit pipeline sync persists a
        replayable ``graph.delta`` event.
        """

        self._require_bound()
        # Fast path: the read model already reflects every governance event and,
        # when the caller wants persistence, so does the journal. The projection
        # below can then only produce an empty delta — and that branch returns
        # ``base`` untouched after setting these same cursors — so skipping it
        # is equivalent. This runs on every tool result and re-read the whole
        # governance entity set plus a full re-projection: ~292ms per call on a
        # 110-turn journal (~4.2k entities / 4.3k nodes).
        #
        # Both cursors must be checked. A lazy read materializes the graph
        # without writing a graph.delta, so a materialized-but-unpersisted graph
        # still has to go down the full path to persist (see
        # test_direct_graph_read_materializes_legacy_journal_without_read_write).
        latest_sequence = self._latest_governance_sequence()
        if self._graph_materialized_sequence >= latest_sequence and (
            not persist or self._graph_persisted_sequence >= latest_sequence
        ):
            self._graph_materialized_sequence = latest_sequence
            if persist:
                self._graph_persisted_sequence = latest_sequence
            return self._graph_view
        if (
            persist
            and self.governance_graph_materialized()
            and self._graph_persisted_sequence < latest_sequence
        ):
            self._persist_current_governance_graph(turn=turn)
        base = self._graph_view
        snapshot_id = base.snapshot_id or f"belief:{self.game_id}:{turn}"
        graph_turn = max(base.turn, turn) if base.snapshot_id else turn
        entities = {
            entity_type: self.list(entity_type, status=None)
            for entity_type in _GOVERNANCE_GRAPH_ENTITY_TYPES
        }
        delta = project_governance_state(
            entities,
            previous=base,
            snapshot_id=snapshot_id,
            turn=graph_turn,
            epoch=self._epoch,
        )
        if not (
            delta.upsert_nodes
            or delta.upsert_edges
            or delta.remove_node_ids
            or delta.remove_edge_keys
        ):
            self._graph_materialized_sequence = self._latest_governance_sequence()
            if persist:
                self._graph_persisted_sequence = self._latest_governance_sequence()
            return base
        if not persist:
            next_view = base.apply(delta)
            self._graph_view = next_view
            self._graph_materialized_sequence = self._latest_governance_sequence()
            return next_view
        return self.record_graph_delta(
            delta,
            kind=f"governance:{self._sequence + 1}",
        )

    def _latest_governance_sequence(self) -> int:
        """Sequence of the newest governance-typed event, maintained by _reduce.

        Scanning for this cost ~3.2ms per call against a 14.7k-event journal and
        ran on every tool result via the sync guard.
        """

        return self._governance_sequence

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
        if entity_type == "decision" and entity.get("decision_state") is not None:
            self._validate_decision_state_patch(
                current_state=None, new_state=entity["decision_state"]
            )
        self._append("entity.created", entity_type, entity, turn=turn)
        return deepcopy(entity)

    @staticmethod
    def _validate_decision_state_patch(
        *, current_state: Any, new_state: Any
    ) -> None:
        """Reject out-of-band ``decision_state`` writes.

        Only ``succeeded``/``cancelled``/``failed`` close a council intent, and the
        recovery tools key on the exact lifecycle states. A generic entity
        patch writing any other value (the 2026-08-15 turn-97 deadlock wrote
        ``resolved`` over both live and already-cancelled decisions) makes the
        turn gate refuse ``end_turn`` while also removing every official
        closure path.
        """

        if new_state not in _DECISION_STATES:
            raise BeliefEngineError(
                "decision_state must be one of "
                f"{', '.join(sorted(_DECISION_STATES))}; close the "
                "authorization with cancel_routed_action or settle it with "
                "record_action_verification instead of writing the field "
                "directly"
            )
        # ``failed`` is frozen like the sanctioned terminal states: the gate
        # already counts it as final accounting for its council intent, so
        # reopening it would resurrect a closed intent as pending state.
        if (
            current_state in _INTENT_CLOSING_STATES
            and new_state != current_state
        ):
            raise BeliefEngineError(
                f"Cannot reopen a {current_state} decision; the audit "
                "outcome is final. Route a new decision for further attempts"
            )

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
        if entity_type == "decision":
            # A decision's authorization contract is fixed when the decision is
            # created. Allowing a generic update to rewrite it would re-point an
            # approval at a different action — the exact thing the council pin
            # and the args_hash check exist to prevent. Lifecycle fields
            # (decision_state, route, cancellation_reason, ...) stay updatable.
            protected |= _PROTECTED_DECISION_FIELDS
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
        if entity_type == "decision" and "decision_state" in patch:
            self._validate_decision_state_patch(
                current_state=current.get("decision_state"),
                new_state=patch["decision_state"],
            )
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

    def graph_entities(
        self,
        entity_type: str,
        *,
        status: str | None = "active",
    ) -> list[dict[str, Any]]:
        """Read current governance entities from the materialized GraphView."""

        if entity_type not in _GOVERNANCE_GRAPH_ENTITY_TYPES:
            return []
        self._ensure_governance_graph_current()
        nodes = self._graph_view.nodes_of_type(entity_type)
        # ``status`` lives inside the frozen attributes, so the filter can run
        # before the thaw. Most nodes of a type are filtered out (e.g. 614
        # decisions of which few are active), and thawing dominates this loop.
        wanted_status = status if status and status not in {"all", ""} else None
        entities: list[dict[str, Any]] = []
        for node in nodes:
            if not node.observed and status not in (None, "all", "deleted"):
                continue
            attributes = node.attributes
            if not isinstance(attributes, Mapping):
                continue
            if wanted_status is not None and attributes.get("status") != wanted_status:
                continue
            # ``thaw_json`` already returns a fresh mutable tree, so the extra
            # deepcopy that used to follow node.to_dict() was a second full copy.
            item = _thaw_json(attributes)
            if not isinstance(item, dict):
                continue
            item.setdefault("id", node.node_id)
            item.setdefault("entity_type", entity_type)
            item.setdefault("last_updated_turn", node.last_observed_turn)
            entities.append(item)
        return sorted(
            entities,
            key=lambda item: (
                item.get("last_updated_turn", -1),
                item.get("updated_at", 0),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )

    def governance_graph_materialized(self) -> bool:
        """Whether the current GraphView contains a governance read model."""

        return any(
            node.node_type in _GOVERNANCE_GRAPH_ENTITY_TYPES
            for node in self._graph_view.nodes.values()
        )

    def governance_graph_current(self) -> bool:
        """Whether no governance journal event is newer than the last graph delta."""

        if not self.governance_graph_materialized():
            return False
        return self._latest_governance_sequence() <= self._graph_materialized_sequence

    def current_governance_entities(
        self,
        entity_type: str,
        *,
        status: str | None = "active",
    ) -> list[dict[str, Any]]:
        """Read governance state from GraphView after ensuring materialization."""

        if entity_type not in _GOVERNANCE_GRAPH_ENTITY_TYPES:
            return self.list(entity_type, status=status)
        self._ensure_governance_graph_current()
        return self.graph_entities(entity_type, status=status)

    def _ensure_governance_graph_current(self) -> None:
        """Materialize the event source before any governance current-state read.

        JSONL is retained as the append-only event source and as input to the
        materializer. It is deliberately not a fallback current-state model:
        a stale or damaged GraphView must surface an explicit error instead of
        silently reintroducing the old read path.
        """

        if self._graph_replay_error:
            raise BeliefEngineError(
                "governance GraphView replay failed: " + self._graph_replay_error
            )
        if self.governance_graph_current():
            return
        if not any(
            self._entities.get(entity_type)
            for entity_type in _GOVERNANCE_GRAPH_ENTITY_TYPES
        ):
            return
        turn = max(
            (
                int(event.get("turn", 0))
                for event in self._events
                if int(event.get("epoch", 1)) == self._epoch
            ),
            default=0,
        )
        self.sync_governance_graph(turn=turn, persist=False)
        if not self.governance_graph_current():
            raise BeliefEngineError(
                "governance GraphView is not current after materialization"
            )

    def current_governance_entity(
        self,
        entity_type: str,
        entity_id: str,
    ) -> dict[str, Any] | None:
        """Look up one governance record in the current graph read model."""

        return next(
            (
                item
                for item in self.current_governance_entities(entity_type, status=None)
                if str(item.get("id") or "") == str(entity_id)
            ),
            None,
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
        # Called by review() on every turn_brief, i.e. several times per tool
        # call. It scans every active observation (~1.1k at T110) and groups by
        # source, which measured ~61ms — over half of review()'s cost. The
        # result depends only on the entity store, and every mutation appends an
        # event, so keying on the journal sequence is a sound invalidation.
        if self._metrics_cache is not None and self._metrics_sequence == self._sequence:
            return deepcopy(self._metrics_cache)
        observations = self.current_governance_entities("observation", status="active")
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
        self._metrics_sequence = self._sequence
        self._metrics_cache = deepcopy(metrics)
        return metrics

    def _run_derivation_rules(
        self, observation: dict[str, Any], *, turn: int
    ) -> None:
        """Derive beliefs/predictions from a fresh query observation.

        Rules run after the observation is durable and before ``review()`` so
        precise evidence-driven resolution wins over the metric-based
        evaluation safety net. A rule failure must never break recording.
        """

        from . import derivation

        try:
            context = derivation.RuleContext(
                engine=self,
                tool=str((observation.get("facts") or {}).get("tool") or ""),
                facts=observation.get("facts") or {},
                metrics=observation.get("metrics") or {},
                turn=turn,
                observation_id=str(observation.get("id")),
            )
            for op in derivation.run_rules(context):
                try:
                    op.apply(self, turn=turn)
                except BeliefEngineError:
                    log.debug("Derivation op rejected: %r", op, exc_info=True)
        except Exception:
            log.warning("Belief Engine: derivation failed", exc_info=True)

    @staticmethod
    def _action_target_from_result(action: dict[str, Any]) -> tuple[int, int] | None:
        """Return the founded-city tile recorded by a successful action.

        ``unit_action(found_city)`` consumes the settler, so a later city
        observation cannot safely identify the original unit by id.  The
        action result is the stable bridge: FireTuner records the requested
        founding tile as ``FOUND_REQUESTED|x,y`` before the city is visible.
        """

        summary = str((action.get("result_ref") or {}).get("summary") or "")
        match = re.search(r"(?:FOUNDED|FOUND_REQUESTED)\|(-?\d+),(-?\d+)", summary)
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    def _entity_is_in_current_epoch(self, entity_type: str, entity_id: str) -> bool:
        """Whether an entity's latest event belongs to the active game branch."""

        for event in reversed(self._events):
            if (
                event.get("entity_type") == entity_type
                and event.get("entity_id") == entity_id
            ):
                return int(event.get("epoch", 1)) == self._epoch
        return False

    def _entity_created_in_current_epoch(self, entity_type: str, entity_id: str) -> bool:
        """Whether an entity was *created* in the active game branch.

        Unlike :meth:`_entity_is_in_current_epoch`, this inspects the first
        event rather than the latest. Load-time recovery (for example
        ``_recover_orphaned_executing_decisions``) rewrites an entity and, in
        doing so, stamps a fresh event at the current epoch — which would make a
        decision from an abandoned branch look current again.

        The first-event epoch is recorded by ``_reduce`` as events stream in, so
        this is a lookup rather than a scan of the whole journal.
        """

        return self._entity_creation_epoch.get((entity_type, entity_id)) == self._epoch

    def _reconcile_observed_action_effects(
        self, observation: dict[str, Any], *, turn: int
    ) -> None:
        """Close superseded routed actions only when game state proves the effect.

        This is intentionally a narrow, evidence-first reconciler.  At the
        moment Civ VI gives us a complete, directly queryable postcondition
        for city founding: a successful ``found_city`` call naming a tile and
        a later ``get_cities`` observation containing a city at that tile.
        When that proof exists, any still-open authorization with the exact
        same founding action is a duplicate of an already completed action,
        not a new obligation for the agent to rediscover and cancel manually.
        """

        facts = observation.get("facts") or {}
        if facts.get("tool") != "get_cities":
            return
        city_tiles = {
            (city.get("x"), city.get("y"))
            for city in facts.get("cities") or []
            if isinstance(city, dict)
            and type(city.get("x")) is int
            and type(city.get("y")) is int
        }
        if not city_tiles:
            return

        proven_actions = [
            action
            for action in self.current_governance_entities("action", status="active")
            if action.get("tool") == "unit_action"
            and action.get("success") is True
            and action.get("outcome_status") == "succeeded"
            and (action.get("params") or {}).get("action") == "found_city"
            and self._entity_is_in_current_epoch("action", action["id"])
            and self._action_target_from_result(action) in city_tiles
        ]
        if not proven_actions:
            return

        open_states = {"authorized", "executing", "retryable", "outcome_unknown"}
        for action in proven_actions:
            action_params = action.get("params") or {}
            founded_tile = self._action_target_from_result(action)
            source_decision_id = action.get("decision_id")
            for decision in self.current_governance_entities("decision", status=None):
                if decision.get("id") == source_decision_id:
                    continue
                if decision.get("decision_state") not in open_states:
                    continue
                intent = decision.get("action_intent") or {}
                if intent.get("tool") != "unit_action":
                    continue
                intent_params = intent.get("params") or intent.get("arguments") or {}
                candidate_x = intent_params.get("target_x")
                candidate_y = intent_params.get("target_y")
                if (
                    intent_params.get("action") != "found_city"
                    or intent_params.get("unit_id") != action_params.get("unit_id")
                    or (
                        (candidate_x is not None or candidate_y is not None)
                        and (
                            type(candidate_x) is not int
                            or type(candidate_y) is not int
                            or (candidate_x, candidate_y) != founded_tile
                        )
                    )
                ):
                    continue

                source_id = str(action.get("id"))
                if decision.get("decision_state") == "outcome_unknown":
                    # A verified world-state postcondition settles the only
                    # remaining uncertainty, so preserve that fact as a
                    # successful completion instead of pretending it was an
                    # unexecuted cancellation.
                    resolved = self.complete_action_authorization(
                        decision["id"],
                        tool="unit_action",
                        success=True,
                        outcome_status="succeeded",
                        result=(
                            "Reconciled from get_cities observation "
                            f"{observation['id']} after successful action {source_id}."
                        ),
                        turn=turn,
                    )
                    self.create(
                        "outcome",
                        {
                            "statement": (
                                "Unknown action outcome reconciled from verified "
                                "game state."
                            ),
                            "action_intent": deepcopy(intent),
                            "decision_id": decision["id"],
                            "action_id": source_id,
                            "success": True,
                            "executed": True,
                            "outcome_status": "succeeded",
                            "reconciliation_observation_id": observation["id"],
                            "reconciled_from_action_id": source_id,
                            "result": resolved.get("execution_result_ref"),
                            "observed_turn": turn,
                        },
                        turn=turn,
                    )
                    continue

                self.cancel_action_authorization(
                    decision["id"],
                    reason=(
                        "Superseded by verified game-state effect: get_cities "
                        f"observation {observation['id']} confirms the city founded "
                        f"by successful action {source_id}."
                    ),
                    turn=turn,
                )

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
        execution_status: str | None = None,
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
                    "reliability": normalized.get("reliability", 0.8),
                    "observed_turn": turn,
                    "tags": ["automatic", "mcp", tool],
                },
                turn=turn,
            )
            self._reconcile_observed_action_effects(observation, turn=turn)
            self._run_derivation_rules(observation, turn=turn)
            self.review(turn=turn)
            return observation
        if category in {"action", "turn"}:
            if execution_status is None:
                if result.startswith("BELIEF_GATE_REQUIRED"):
                    execution_status = "blocked"
                elif result.startswith("ERR:OUTCOME_UNKNOWN"):
                    execution_status = "unknown"
                elif success:
                    execution_status = "succeeded"
                else:
                    execution_status = "failed"
            if execution_status not in _ACTION_OUTCOME_STATUSES:
                raise BeliefEngineError(
                    "execution_status must be succeeded, failed, unknown, or blocked"
                )
            if success != (execution_status == "succeeded"):
                raise BeliefEngineError(
                    "success must be true exactly when execution_status is succeeded"
                )
            executed = execution_status != "blocked"
            decision = (
                self.current_governance_entity("decision", decision_id)
                if decision_id
                else None
            )
            intent = (
                deepcopy(decision.get("action_intent") or {})
                if decision is not None
                else {}
            )
            args_hash = action_args_hash(params)
            intent_id = str(intent.get("intent_id") or "") or (
                f"intent:{decision_id}:{args_hash}" if decision_id else None
            )
            linkage = {
                "action_intent_id": intent_id,
                "proposal_id": intent.get("proposal_id"),
                "proposal_version": (
                    intent.get("proposal_version")
                    if decision
                    else None
                ),
                "council_decision_id": (
                    decision.get("council_decision_id")
                    if decision
                    else None
                ),
                "execution_attempt": (
                    decision.get("execution_attempt") if decision else None
                ),
            }
            result_ref = tool_result_reference(result)
            action = self.create(
                "action",
                {
                    "statement": f"{tool} {execution_status}",
                    "tool": tool,
                    "params": deepcopy(params),
                    "result_ref": result_ref,
                    "success": success,
                    "duration_ms": duration_ms,
                    "selected_turn": turn,
                    "decision_id": decision_id,
                    "decision_route": decision_route,
                    "executed": executed,
                    "outcome_status": execution_status,
                    **linkage,
                    "verification": {
                        "source": "tool_result",
                        "verified": success,
                    },
                },
                turn=turn,
            )
            verification_observation_ids: list[str] = []
            # A successful action is factual evidence. Persist it as an
            # observation so predictions and plan conditions can be reviewed
            # without a second agent-side record_observation call.
            if success and executed:
                normalized = normalize_tool_result(tool, result)
                facts = deepcopy(normalized.get("facts") or {})
                facts.update({"action_success": True, "action_id": action["id"]})
                observation = self.create(
                    "observation",
                    {
                        "statement": f"Observed result from {tool}",
                        "source": f"action:{tool}",
                        "facts": facts,
                        "metrics": deepcopy(normalized.get("metrics") or {}),
                        "reliability": normalized.get("reliability", 0.8),
                        "observed_turn": turn,
                        "tags": ["automatic", "action", tool],
                    },
                    turn=turn,
                )
                verification_observation_ids.append(observation["id"])
                action = self.update(
                    "action",
                    action["id"],
                    {
                        "verification_observation_ids": (
                            verification_observation_ids
                        )
                    },
                    turn=turn,
                )
            if decision_id and executed:
                self.create(
                    "outcome",
                    {
                        "statement": f"Outcome of {tool}: {execution_status}",
                        "action_intent": {
                            "tool": tool,
                            "params": deepcopy(params),
                            "args_hash": args_hash,
                        },
                        "decision_id": decision_id,
                        "action_id": action["id"],
                        "success": success,
                        "executed": True,
                        "outcome_status": execution_status,
                        "verification_observation_ids": (
                            verification_observation_ids
                        ),
                        **linkage,
                        "observed_turn": turn,
                    },
                    turn=turn,
                )
                if self.current_governance_entity("decision", decision_id):
                    self.complete_action_authorization(
                        decision_id,
                        tool=tool,
                        success=success,
                        outcome_status=execution_status,
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
            expected_facts = requirement.get("expected_facts") or {}
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
                if not isinstance(expected_facts, dict) or not all(
                    facts.get(key) == value
                    for key, value in expected_facts.items()
                ):
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
            for item in self.current_governance_entities("decision", status="active")
            if item.get("decision_state") in {"authorized", "retryable"}
            and self._authorization_valid_on_turn(item, turn=turn)
            and self._action_matches(item.get("action_intent"), tool, params)
            # An authorization minted in a game branch that no longer exists
            # must not be consumable. record_game_reload normally invalidates
            # them, but that depends on the rollback being detected (a manual
            # load can skip it), so make the guarantee structural instead.
            and self._entity_created_in_current_epoch("decision", item["id"])
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
        # Persisted decisions created before council-aware route classification
        # may still say ``slow``. A resolved council is the completed slow
        # review, so derive the executable route from its evidence contract.
        effective_route = str(decision.get("route") or "fast")
        if decision.get("council_decision_id"):
            effective_route = (
                "verify_then_fast"
                if decision.get("evidence_requirements")
                else "fast"
            )
        if effective_route == "slow":
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
        if effective_route == "verify_then_fast":
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
        outcome_status: str | None = None,
        result: str,
        turn: int,
    ) -> dict[str, Any]:
        """Finish an executing authorization after the game returns a result."""

        decision = self.current_governance_entity("decision", decision_id)
        if not decision:
            raise BeliefEngineError(f"Unknown decision: {decision_id}")
        if decision.get("decision_state") not in {
            "executing",
            "outcome_unknown",
        }:
            return decision
        result_ref = tool_result_reference(result)
        resolved_status = outcome_status or ("succeeded" if success else "failed")
        if resolved_status not in {"succeeded", "failed", "unknown"}:
            raise BeliefEngineError(
                "outcome_status must be succeeded, failed, or unknown"
            )
        if success != (resolved_status == "succeeded"):
            raise BeliefEngineError(
                "success must be true exactly when outcome_status is succeeded"
            )
        if resolved_status == "succeeded":
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
        elif resolved_status == "failed":
            patch = {
                "decision_state": "retryable",
                "last_failed_action_tool": tool,
                "last_failed_turn": turn,
                "last_failure_ref": result_ref,
            }
            obsolete_result_fields = ("execution_result", "last_failure")
        else:
            patch = {
                "decision_state": "outcome_unknown",
                "last_unknown_action_tool": tool,
                "last_unknown_turn": turn,
                "unknown_result_ref": result_ref,
            }
            obsolete_result_fields = (
                "execution_result",
                "last_failure",
                "last_failure_ref",
            )
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

        decision = self.current_governance_entity("decision", decision_id)
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
        if state == "outcome_unknown":
            raise BeliefEngineError(
                "Unknown action outcome must be verified before cancellation or retry"
            )
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
            for lock in self.current_governance_entities("budget_lock", status="active"):
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
                "outcome_status": "cancelled",
                "action_intent_id": (
                    str(action_intent.get("intent_id") or "")
                    or f"intent:{decision_id}:{action_args_hash(action_intent.get('arguments') or action_intent.get('params') or {})}"
                ),
                "proposal_id": action_intent.get("proposal_id"),
                "proposal_version": action_intent.get("proposal_version"),
                "council_decision_id": decision.get("council_decision_id"),
                "execution_attempt": decision.get("execution_attempt", 0),
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
        self._graph_persisted_sequence = 0
        self._graph_materialized_sequence = 0
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
                "outcome_unknown",
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
                # 引擎结构化聚合的间接观测：可靠但非原始查询。
                "reliability": 0.85,
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
        prediction = self.current_governance_entity("prediction", prediction_id)
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
        existing = self.current_governance_entity("surprise", surprise_id)
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
            belief = self.current_governance_entity("belief", belief_id)
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
            hypothesis = self.current_governance_entity("hypothesis", hypothesis_id)
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

    def rebalance_hypotheses_bayesian(
        self,
        topic_id: str,
        likelihood_ratios: dict[str, float],
        *,
        turn: int,
        evidence_id: str = "",
    ) -> list[dict[str, Any]]:
        """Rebalance a hypothesis topic via deterministic Bayes.

        The caller supplies one likelihood ratio per hypothesis (evidence
        does not discriminate → omit it and the ratio defaults to 1.0).  The
        posterior arithmetic runs here rather than in the caller's head, so
        each redistribution is auditable from the event log.
        """
        members = [
            hypothesis
            for hypothesis in self.current_governance_entities("hypothesis", status="active")
            if hypothesis.get("topic_id") == topic_id
        ]
        if not members:
            raise BeliefEngineError(f"No active hypotheses for topic: {topic_id}")
        prior = {hypothesis["id"]: float(hypothesis["probability"]) for hypothesis in members}
        try:
            updated_probabilities = bayes.posterior(prior, likelihood_ratios)
        except ValueError as exc:
            raise BeliefEngineError(str(exc)) from exc
        if not math.isclose(sum(updated_probabilities.values()), 1.0, abs_tol=0.001):
            raise BeliefEngineError("Bayesian posterior failed to normalize")
        # Validate the whole pool before writing, mirroring
        # rebalance_hypotheses: event sourcing cannot roll back a partial
        # redistribution.
        for hypothesis_id in updated_probabilities:
            if hypothesis_id not in prior:
                raise BeliefEngineError(
                    f"Hypothesis {hypothesis_id} does not belong to topic {topic_id}"
                )
        updated: list[dict[str, Any]] = []
        for hypothesis_id, probability in updated_probabilities.items():
            patch: dict[str, Any] = {
                "probability": probability,
                "last_likelihood_ratio": likelihood_ratios.get(hypothesis_id, 1.0),
            }
            if evidence_id:
                patch["last_evidence_id"] = evidence_id
            updated.append(
                self.update("hypothesis", hypothesis_id, patch, turn=turn)
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
        belief = self.current_governance_entity("belief", belief_id)
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
        """Evaluate predictions, belief expectations, and plan triggers.

        Has side effects: resolving a prediction or recording a contradiction
        appends events. The memo below is therefore only populated when a run
        wrote nothing — in that case the entity store is byte-identical to what
        the next call would read, and ``review`` depends on nothing else (no
        wall clock), so repeating it would again write nothing and return the
        same value. Skipping it is equivalent, not merely similar.
        """

        cache_key = (int(turn), self._sequence)
        if self._review_cache is not None and self._review_key == cache_key:
            return deepcopy(self._review_cache)
        sequence_before = self._sequence

        metrics = self.current_metrics()
        resolved: list[str] = []
        overdue: list[str] = []
        contradictions: list[str] = []
        replans: list[str] = []
        # stale-knowledge：自动信念超过阈值回合未刷新 → 提示（不阻断）。
        # 引擎无法感知"应该查而没查"，这是对观测新鲜度的最低限度告警。
        stale_knowledge: list[dict[str, Any]] = []
        for belief in self.current_governance_entities("belief", status="active"):
            belief_id = str(belief.get("id") or "")
            stale_turns = next(
                (
                    turns
                    for prefix, turns in _STALE_AUTO_BELIEF_TURNS.items()
                    if belief_id.startswith(prefix)
                ),
                None,
            )
            if stale_turns is None:
                continue
            last_seen = int(belief.get("last_seen_turn") or turn)
            unseen = turn - last_seen
            if unseen >= stale_turns:
                refresh_tool = _STALE_REFRESH_TOOLS.get(
                    next(
                        (
                            prefix
                            for prefix in _STALE_AUTO_BELIEF_TURNS
                            if belief_id.startswith(prefix)
                        ),
                        "",
                    ),
                    "get_turn_brief",
                )
                stale_knowledge.append(
                    {
                        "id": belief_id,
                        "subject": belief.get("statement", "")[:80],
                        "unseen_turns": unseen,
                        "refresh_tool": refresh_tool,
                    }
                )

        for prediction in self.current_governance_entities("prediction", status="active"):
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
                    reason = (
                        "metric_unavailable"
                        if isinstance(rule, dict) and rule.get("metric") not in metrics
                        else "no_evaluation_rule"
                    )
                    self.update(
                        "prediction",
                        prediction["id"],
                        {
                            "status": "overdue",
                            "review_required": True,
                            "overdue_reason": reason,
                        },
                        turn=turn,
                    )
                    overdue.append(prediction["id"])

        # Overdue predictions stay under examination.  A rule that finally
        # evaluates False once the metric arrives still settles the claim
        # disconfirmed (the outcome demonstrably does not hold); a late True
        # is ambiguous about *when* it became true and stays overdue.
        for prediction in self.current_governance_entities("prediction", status="overdue"):
            rule = prediction.get("evaluation")
            if not isinstance(rule, dict):
                continue
            if evaluate_condition(rule, metrics) is False:
                self.resolve_prediction(
                    prediction["id"],
                    outcome=False,
                    actual=metrics.get(rule.get("metric")),
                    turn=turn,
                    source="automatic_late",
                )
                resolved.append(prediction["id"])

        existing_contradiction_keys = {
            entity.get("contradiction_key")
            for entity in self.current_governance_entities("contradiction", status="active")
        }
        for belief in self.current_governance_entities("belief", status="active"):
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

        for plan in self.current_governance_entities("plan", status="active"):
            triggered = []
            for condition in plan.get("exit_conditions") or []:
                if isinstance(condition, dict) and evaluate_condition(condition, metrics) is True:
                    triggered.append(condition)
            broken_assumptions = []
            for belief_id, threshold in (plan.get("assumption_thresholds") or {}).items():
                belief = self.current_governance_entity("belief", belief_id)
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

        result = {
            "turn": turn,
            "metrics": metrics,
            "predictions_resolved": resolved,
            "predictions_overdue": overdue,
            "contradictions_created": contradictions,
            "plans_needing_replan": replans,
            "knowledge_stale": stale_knowledge,
        }
        if self._sequence == sequence_before:
            self._review_key = cache_key
            self._review_cache = deepcopy(result)
        return result

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
        # review() can append events (resolving predictions, recording
        # contradictions), so the cache key is taken *after* it runs: any write
        # advances the journal sequence and therefore misses the cache. Without
        # that ordering a memoized brief would skip review's side effects
        # entirely. Several call sites ask for the same turn within one tool
        # call (pipeline._append_belief_context, authorize_action, ...).
        cache_key = (int(turn), take, self._sequence)
        if self._turn_brief_cache is not None and self._turn_brief_key == cache_key:
            return deepcopy(self._turn_brief_cache)

        beliefs = self.current_governance_entities("belief", status="active")
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
            for item in self.current_governance_entities("prediction", status=None)
            if item.get("status") in {"active", "overdue"}
        ]
        plans = [
            item
            for item in self.current_governance_entities("plan", status=None)
            if item.get("status") in {"active", "needs_replan"}
        ]
        surprises = self.current_governance_entities("surprise", status="active")
        contradictions = self.current_governance_entities("contradiction", status="active")

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
            belief = self.current_governance_entity(
                "belief", str(item.get("belief_id") or "")
            )
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

        brief = {
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
                "knowledge_stale": review.get("knowledge_stale") or [],
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
        self._turn_brief_key = cache_key
        self._turn_brief_cache = deepcopy(brief)
        return brief

    def governance_turn_gate(self, *, turn: int) -> dict[str, Any]:
        """Return the non-bypassable governance obligations for ``turn``.

        A typed snapshot is the minimum decision context. Submitted proposals
        must be arbitrated, selected action intents must reach a successful
        outcome, and routed authorizations cannot be silently abandoned before
        ending the turn.

        Like :meth:`review`, this reclaims stale decisions and therefore appends
        events. The memo below is only populated when a run wrote nothing, in
        which case the entity store is unchanged and a repeat call would write
        nothing again. The gate runs on every ``end_turn`` preflight and walks
        five entity collections (~193ms at T110).
        """

        cache_key = (int(turn), self._sequence)
        if self._gate_cache is not None and self._gate_key == cache_key:
            return deepcopy(self._gate_cache)
        sequence_before = self._sequence

        typed_snapshot = next(
            (
                item
                for item in self.current_governance_entities("observation", status="active")
                if item.get("source") == "game_state:typed_snapshot"
                and item.get("observed_turn") == turn
            ),
            None,
        )
        active_proposals = self.current_governance_entities("proposal", status="active")
        # An executing decision from a previous turn can never be completed by
        # its original caller: the action window has passed. Reclaim it to
        # retryable so the gate still demands resolution, but cancellation and
        # re-authorization become possible instead of deadlocking the turn.
        for item in self.current_governance_entities("decision", status=None):
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
        all_decisions = self.current_governance_entities("decision", status=None)

        def structured_action_intent(item: dict[str, Any]) -> dict[str, Any]:
            intent = item.get("action_intent")
            return intent if isinstance(intent, dict) else {}

        def intent_params(intent: dict[str, Any]) -> dict[str, Any]:
            params = intent.get("params") or intent.get("arguments") or {}
            return params if isinstance(params, dict) else {}

        def matches_council_intent(
            decision: dict[str, Any], proposal: dict[str, Any], intent: dict[str, Any]
        ) -> bool:
            """Match a routed decision to its approved council intent.

            Current records use the immutable ``intent_id``.  Older event
            streams predate that field on the routed decision, however, and
            would keep an already-cancelled/succeeded action in the turn gate
            forever.  For those legacy records only, the exact tool and
            canonical arguments are an equally unambiguous migration key.
            """

            decision_intent = structured_action_intent(decision)
            if decision.get("council_decision_id") != proposal.get("council_decision_id"):
                return False
            if decision_intent.get("proposal_id") != proposal["id"]:
                return False
            intent_id = str(intent.get("intent_id") or "")
            decision_intent_id = str(decision_intent.get("intent_id") or "")
            if not intent_id or decision_intent_id == intent_id:
                return True
            return not decision_intent_id and (
                decision_intent.get("tool") == intent.get("tool")
                and action_args_hash(intent_params(decision_intent))
                == action_args_hash(intent_params(intent))
            )

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
            if item.get("decision_state")
            in {"authorized", "executing", "retryable", "outcome_unknown"}
            and intent_due(item, fallback_turn=int(item.get("created_turn", turn)))
        ]

        pending_council_intents: list[dict[str, Any]] = []
        approved_proposals = [
            item
            for item in self.current_governance_entities("proposal", status=None)
            if item.get("council_state") == "approved"
        ]
        for proposal in approved_proposals:
            for intent in proposal.get("action_intents") or []:
                if not isinstance(intent, dict):
                    continue
                due_turn = intent.get("allowed_turn")
                fallback_turn = int(
                    proposal.get("last_updated_turn", proposal.get("created_turn", turn))
                )
                if not intent_due(intent, fallback_turn=fallback_turn):
                    continue
                matching = [
                    decision
                    for decision in all_decisions
                    if matches_council_intent(decision, proposal, intent)
                ]
                intent_id = str(intent.get("intent_id") or "")
                terminal = next(
                    (
                        decision
                        for decision in matching
                        if decision.get("decision_state") in _INTENT_CLOSING_STATES
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
        gate = {
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
        if self._sequence == sequence_before:
            self._gate_key = cache_key
            self._gate_cache = deepcopy(gate)
        return gate

    def find_duplicate_pending_intent(
        self,
        *,
        tool: str,
        params: dict[str, Any],
        exclude_proposal_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return an approved-but-unfinished council intent matching tool+args.

        Guards proposal submission against re-authorizing an action that is
        already in flight. Matching mirrors ``governance_turn_gate``: an
        intent stops being pending once a decision reaches ``succeeded`` or
        ``cancelled``, so a cancelled duplicate never blocks a legitimate
        retry. ``exclude_proposal_id`` lets a proposal resubmit itself —
        editing one's own authorization is not a duplicate. Born from the
        2026-08-15 duplicate-settle incident, where three parallel
        settle-capital authorizations deadlocked ``end_turn`` for half an
        hour.
        """

        wanted_hash = action_args_hash(params)
        for proposal in self.current_governance_entities("proposal", status=None):
            if proposal.get("council_state") != "approved":
                continue
            if exclude_proposal_id and proposal["id"] == exclude_proposal_id:
                continue
            for intent in proposal.get("action_intents") or []:
                if not isinstance(intent, dict) or intent.get("tool") != tool:
                    continue
                intent_params = intent.get("params") or intent.get("arguments") or {}
                if (
                    not isinstance(intent_params, dict)
                    or action_args_hash(intent_params) != wanted_hash
                ):
                    continue
                if self._intent_has_terminal_decision(proposal, intent):
                    continue
                return {
                    "proposal_id": proposal["id"],
                    "intent_id": intent.get("intent_id") or None,
                }
        return None

    def _intent_has_terminal_decision(
        self, proposal: dict[str, Any], intent: dict[str, Any]
    ) -> bool:
        """Whether a decision already closed this council intent terminally.

        Uses the same matching key as the turn gate: council decision id,
        proposal id, then ``intent_id`` — falling back to tool + canonical
        argument hash for legacy decisions that predate ``intent_id``.
        """

        for decision in self.current_governance_entities("decision", status=None):
            if decision.get("decision_state") not in _INTENT_CLOSING_STATES:
                continue
            decision_intent = decision.get("action_intent")
            if not isinstance(decision_intent, dict):
                continue
            if decision.get("council_decision_id") != proposal.get("council_decision_id"):
                continue
            if decision_intent.get("proposal_id") != proposal["id"]:
                continue
            intent_id = str(intent.get("intent_id") or "")
            decision_intent_id = str(decision_intent.get("intent_id") or "")
            if not intent_id or decision_intent_id == intent_id:
                return True
            if not decision_intent_id:
                decision_params = (
                    decision_intent.get("params")
                    or decision_intent.get("arguments")
                    or {}
                )
                intent_params = intent.get("params") or intent.get("arguments") or {}
                if (
                    decision_intent.get("tool") == intent.get("tool")
                    and isinstance(decision_params, dict)
                    and isinstance(intent_params, dict)
                    and action_args_hash(decision_params)
                    == action_args_hash(intent_params)
                ):
                    return True
        return False

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
            belief = self.current_governance_entity("belief", belief_id)
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
        active_surprises = self.current_governance_entities("surprise", status="active")
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
        if council_decision_id:
            # A resolved council decision is the completed slow deliberation
            # for a high-impact action. Reclassifying it as ``slow`` here
            # creates an unsatisfiable gate: ``slow`` carries no concrete
            # evidence contract, so the caller cannot know what to gather.
            # The selected proposal's explicit evidence contract remains the
            # only post-council requirement.
            route = "verify_then_fast" if evidence_requirements else "fast"
            budget = "low" if evidence_requirements else "none"
        else:
            # A hard ``surprise_score >= 0.7 -> slow`` rule made routing a
            # function of global world noise, not of this action: the same
            # args_hash flipped slow->fast as surprises churned, forcing the
            # cancel-and-reroute loop. Surprise still weighs in via the 0.05
            # term; scoped effects live in belief_review_required, which only
            # fires when a referenced belief is under review.
            if score >= 0.55:
                route = "slow"
                budget = "high" if score >= 0.75 else "medium"
            elif score >= 0.35:
                route = "verify_then_fast"
                budget = "low"
            else:
                route = "fast"
                budget = "none"
            if evidence_requirements and route == "fast":
                # An explicit evidence contract is mandatory, not advisory. A
                # low-risk action may avoid slow deliberation but cannot bypass
                # the proposal's required read-before-write query.
                route = "verify_then_fast"
                budget = "low"
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
        if route == "slow" and not council_decision_id:
            # A slow route blocks execution and has no evidence contract to
            # satisfy, so it must state its exits instead of being a dead end
            # the caller can only escape by cancelling.
            assessment["route_guidance"] = (
                "slow 路由不可执行且无证据契约。出路：1) 修正评估后重新 route_belief_decision；"
                "2) 经议会仲裁后路由（council_decision_id 复路由）；"
                "3) cancel_routed_action 显式放弃。不要无变更地重复路由同一 intent。"
            )
        if persist and action_intent:
            # Re-routing the same intent (the documented slow-route exits 1/2)
            # supersedes the stale authorization instead of stacking a second
            # one: a lingering ``authorized`` decision would keep blocking
            # end_turn and fight the new decision for consumption. In-flight
            # states (executing/outcome_unknown) are never superseded — that
            # is the duplicate-authorization hazard the gate exists to stop.
            # Budget locks stay reserved: superseding is replacement, not
            # abandonment, so the council's single-execution reservation
            # carries over to the new decision.
            new_hash = action_args_hash(
                action_intent.get("params") or action_intent.get("arguments") or {}
            )
            for stale in self.current_governance_entities("decision", status="active"):
                if stale.get("decision_state") not in {"authorized", "retryable"}:
                    continue
                if not self._entity_is_in_current_epoch("decision", stale["id"]):
                    continue
                stale_intent = stale.get("action_intent")
                if not isinstance(stale_intent, dict):
                    continue
                if stale_intent.get("tool") != action_intent.get("tool"):
                    continue
                stale_params = stale_intent.get("params") or stale_intent.get("arguments") or {}
                if not isinstance(stale_params, dict):
                    continue
                if action_args_hash(stale_params) != new_hash:
                    continue
                self.update(
                    "decision",
                    stale["id"],
                    {
                        "status": "resolved",
                        "decision_state": "cancelled",
                        "cancelled_turn": turn,
                        "cancellation_reason": "superseded_by_reroute",
                    },
                    turn=turn,
                )
        if persist:
            return self.create("decision", assessment, turn=turn)
        return assessment

    def update_attribution_posteriors(
        self, attribution_id: str, *, turn: int
    ) -> dict[str, Any]:
        attribution = self.current_governance_entity("attribution", attribution_id)
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
            for item in self.current_governance_entities("prediction", status=None)
            if item.get("status") in {"confirmed", "disconfirmed"}
        ]
        errors = [float(item.get("prediction_error", 0)) for item in predictions]
        overconfident = [
            item
            for item in predictions
            if float(item.get("probability", 0)) >= 0.75
            and item.get("status") == "disconfirmed"
        ]
        plans = self.current_governance_entities("plan", status=None)
        completed_plans = [item for item in plans if item.get("status") == "completed"]
        decisions = self.current_governance_entities("decision", status=None)
        return {
            "belief_count": len(self.current_governance_entities("belief", status="active")),
            "hypothesis_count": len(self.current_governance_entities("hypothesis", status="active")),
            "prediction_count": len(self.current_governance_entities("prediction", status=None)),
            "resolved_prediction_count": len(predictions),
            "mean_prediction_error": round(sum(errors) / len(errors), 4) if errors else None,
            "overconfidence_rate": round(len(overconfident) / len(predictions), 4)
            if predictions
            else None,
            "surprise_frequency": len(self.current_governance_entities("surprise", status=None)),
            "contradiction_count": len(self.current_governance_entities("contradiction", status=None)),
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
                entity_type: (
                    self.current_governance_entities(entity_type, status=status)
                    if entity_type in _GOVERNANCE_GRAPH_ENTITY_TYPES
                    else self.list(entity_type, status=status)
                )
                for entity_type in sorted(BELIEF_ENTITY_TYPES)
            },
            "research_metrics": self.metrics(),
            "last_sequence": self._sequence,
        }

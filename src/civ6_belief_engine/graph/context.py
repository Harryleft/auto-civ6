"""Bounded decision context from a real typed-world delta, without writes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .model import Coverage, Edge, GraphDelta, Node
from .project import WORLD_SOURCE
from .view import GraphView


_TYPE_DOMAINS = {
    "technology": {"science", "production"},
    "civic": {"culture", "production"},
    "city": {"production", "economy"},
    "unit": {"military"},
    "barbarian_camp": {"military"},
    "tile": {"map"},
    "resource_stockpile": {"economy", "production", "military"},
    "government": {"culture", "economy", "production"},
    "policy_slot": {"culture", "economy", "production"},
    "great_person_class": {"culture", "science"},
}
_DOMAIN_REASONS = {
    "science": "重评科研选择及解锁依赖。",
    "culture": "重评市政、政策与文化目标。",
    "production": "重评受影响城市的生产候选及前置条件。",
    "economy": "重评可用预算、资源及城市发展。",
    "military": "重评单位行动、威胁与交战条件；未观测对象需先核实。",
    "diplomacy": "重评外交关系及相关协定。",
    "map": "重评已观测地块相关路径与选址；范围限于本次采集。",
}
_DIPLOMACY_FIELDS = frozenset(
    {
        "is_at_war",
        "diplomatic_state",
        "relationship_score",
        "grievances",
        "access_level",
        "has_delegation",
        "has_embassy",
        "alliance_type",
        "alliance_level",
        "defensive_pacts",
        "military_strength",
    }
)
_ECONOMY_FIELDS = frozenset(
    {
        "gold",
        "gold_per_turn",
        "gold_income",
        "total_maintenance",
        "unit_maintenance",
        "num_cities",
        "total_population",
    }
)


def _attribute_changes(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """Name changed fields, including one nested level, without copying blobs."""
    changed: list[str] = []
    unavailable: list[str] = []
    for key in sorted(before.keys() | after.keys()):
        if key not in after:
            changed.append(key)
            unavailable.append(key)
        elif key not in before:
            changed.append(key)
        elif before[key] != after[key]:
            old, new = before[key], after[key]
            if isinstance(old, Mapping) and isinstance(new, Mapping):
                for child in sorted(old.keys() | new.keys()):
                    name = f"{key}.{child}"
                    if child not in new:
                        changed.append(name)
                        unavailable.append(name)
                    elif child not in old or old[child] != new[child]:
                        changed.append(name)
            else:
                changed.append(key)
    return changed, unavailable


def _change_kind(prior: Node | Edge | None, current: Node | Edge) -> str:
    if not current.observed:
        return (
            "no_longer_visible"
            if current.coverage is Coverage.CURRENTLY_VISIBLE
            else "not_observed"
        )
    if prior is None:
        return "added"
    if not prior.observed:
        return "reobserved"
    return "changed"


def _node_domains(node: Node, fields: list[str]) -> set[str]:
    domains = set(_TYPE_DOMAINS.get(node.node_type, ()))
    if node.node_type == "player":
        if any(
            field.startswith(("current_research", "science"))
            or field == "tech_civic"
            or (
                field.startswith("tech_civic.")
                and any(part in field.split(".")[-1] for part in ("tech", "research"))
            )
            for field in fields
        ):
            domains.update(("science", "production"))
        if any(
            field.startswith(("current_civic", "culture", "policies"))
            for field in fields
        ):
            domains.update(("culture", "production"))
        if any(
            field.startswith("tech_civic.") and "civic" in field.split(".")[-1]
            for field in fields
        ):
            domains.update(("culture", "production"))
        if set(fields) & _DIPLOMACY_FIELDS:
            domains.update(("diplomacy", "military"))
        if set(fields) & _ECONOMY_FIELDS:
            domains.add("economy")
        if set(fields) & {
            "num_units",
            "threat_scan_available",
            "barbarian_overview_available",
        }:
            domains.add("military")
        if set(fields) & {"explored_land", "total_land"}:
            domains.add("map")
    if node.node_type == "city" and set(fields) & {
        "owner_id",
        "defense_strength",
        "wall_hp",
        "health",
        "loyalty",
    }:
        domains.add("military")
    return domains


def summarize_world_changes(
    previous: GraphView,
    delta: GraphDelta,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Describe actual world changes; missing observations never prove destruction.

    Snapshot metadata dates the acquisition batch, not every retained graph
    node. Content-deduplicated ``last_observed_turn`` is deliberately ignored.
    New epochs establish a baseline instead of comparing alternate timelines.
    """
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    limit = min(limit, 20)
    prior_world = any(node.source == WORLD_SOURCE for node in previous.nodes.values())
    baseline = (
        previous.epoch != delta.epoch or not previous.snapshot_id or not prior_world
    )
    result: dict[str, Any] = {
        "mode": "baseline" if baseline else "changes",
        "snapshot_id": delta.snapshot_id,
        "turn": delta.turn,
        "epoch": delta.epoch,
        "freshness_note": "回合与快照标识指本次采集批次；未观测或保留的历史对象不因此变新。",
        "counts": {
            "node_changes": 0,
            "edge_changes": 0,
            "removed": 0,
            "no_longer_visible": 0,
            "not_observed": 0,
        },
        "node_changes": [],
        "edge_changes": [],
        "affected_domains": [],
        "reevaluate": [],
        "checks_required": [],
        "omitted_change_count": 0,
    }
    if baseline:
        result["baseline_reason"] = (
            "epoch_changed" if previous.epoch != delta.epoch else "first_world_snapshot"
        )
        result["note"] = (
            "建立本局分支的观测基线；不将首次采集或读档差异当作已发生的世界变化。"
        )
        result["baseline_counts"] = {
            "nodes": sum(node.source == WORLD_SOURCE for node in delta.upsert_nodes),
            "edges": sum(edge.source == WORLD_SOURCE for edge in delta.upsert_edges),
        }
        return result

    changes: list[tuple[str, dict[str, Any]]] = []
    domains: set[str] = set()
    needs_resource_check = False
    world_upserts = {
        node.node_id: node for node in delta.upsert_nodes if node.source == WORLD_SOURCE
    }

    def add_change(kind: str, entry: dict[str, Any]) -> None:
        result["counts"][f"{kind}_changes"] += 1
        state = entry["change"]
        if state in result["counts"]:
            result["counts"][state] += 1
        changes.append((kind, entry))

    for node in world_upserts.values():
        prior = previous.node(node.node_id)
        if prior is not None and prior.source != WORLD_SOURCE:
            prior = None
        fields, unavailable = _attribute_changes(
            prior.attributes if prior else {}, node.attributes
        )
        if node.node_type == "player":
            # GameOverview repeats the acquisition turn inside the player;
            # the batch metadata already conveys it without a fake change.
            fields = [field for field in fields if field != "turn"]
            unavailable = [field for field in unavailable if field != "turn"]
        if prior is not None:
            fields += [
                name
                for name in ("node_type", "coverage", "observed")
                if getattr(prior, name) != getattr(node, name)
            ]
        if prior is not None and not fields:
            continue
        change = _change_kind(prior, node)
        entry = {
            "node_id": node.node_id,
            "node_type": node.node_type,
            "change": change,
            "changed_fields": fields[:8],
            "changed_field_count": len(fields),
            "unavailable_fields": unavailable[:8],
            "unavailable_field_count": len(unavailable),
        }
        add_change("node", entry)
        domains.update(_node_domains(node, fields))
        if (
            change in {"no_longer_visible", "not_observed", "reobserved"}
            and node.node_type == "player"
        ):
            domains.update(("diplomacy", "military"))
        # Only a confirmed change to completed technology evidence warrants
        # a resource visibility check. A selection/countdown change does not.
        if (
            node.observed
            and prior is not None
            and any(
                field
                in {"tech_civic.completed_techs", "tech_civic.completed_tech_count"}
                and field not in unavailable
                for field in fields
            )
        ):
            needs_resource_check = True

    for node_id in delta.remove_node_ids:
        prior = previous.node(node_id)
        if prior is None or prior.source != WORLD_SOURCE:
            continue
        change = (
            "removed"
            if prior.coverage is Coverage.COMPLETE
            else (
                "no_longer_visible"
                if prior.coverage is Coverage.CURRENTLY_VISIBLE
                else "not_observed"
            )
        )
        add_change(
            "node",
            {
                "node_id": node_id,
                "node_type": prior.node_type,
                "change": change,
                "changed_fields": [],
                "changed_field_count": 0,
            },
        )
        domains.update(_node_domains(prior, list(prior.attributes)))

    def edge_domains(edge: Edge) -> None:
        if edge.relation_type == "DIPLOMACY_WITH":
            domains.update(("diplomacy", "military"))
        elif edge.relation_type == "THREATENS":
            domains.add("military")
        for node_id in (edge.source_id, edge.target_id):
            node = world_upserts.get(node_id) or previous.node(node_id)
            if node is not None and node.source == WORLD_SOURCE:
                domains.update(_node_domains(node, []))

    for edge in delta.upsert_edges:
        if edge.source != WORLD_SOURCE:
            continue
        prior = previous.edges.get(edge.key)
        if prior is not None and prior.source != WORLD_SOURCE:
            prior = None
        fields, unavailable = _attribute_changes(
            prior.attributes if prior else {}, edge.attributes
        )
        if prior is not None:
            fields += [
                name
                for name in ("coverage", "observed")
                if getattr(prior, name) != getattr(edge, name)
            ]
        if prior is not None and not fields:
            continue
        add_change(
            "edge",
            {
                "relation_type": edge.relation_type,
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "change": _change_kind(prior, edge),
                "changed_fields": fields[:8],
                "changed_field_count": len(fields),
                "unavailable_fields": unavailable[:8],
                "unavailable_field_count": len(unavailable),
            },
        )
        edge_domains(edge)

    for key in delta.remove_edge_keys:
        prior = previous.edges.get(key)
        if prior is None or prior.source != WORLD_SOURCE:
            continue
        # Removing a location/threat edge ends this observed relationship;
        # it never establishes that its endpoint entity was destroyed.
        change = "removed" if prior.coverage is Coverage.COMPLETE else "not_observed"
        add_change(
            "edge",
            {
                "relation_type": prior.relation_type,
                "source_id": prior.source_id,
                "target_id": prior.target_id,
                "change": change,
                "changed_fields": [],
                "changed_field_count": 0,
            },
        )
        edge_domains(prior)

    for kind, entry in changes[:limit]:
        result[f"{kind}_changes"].append(entry)
    result["omitted_change_count"] = max(0, len(changes) - limit)
    result["affected_domains"] = sorted(domains)
    result["reevaluate"] = [
        {"domain": domain, "reason": _DOMAIN_REASONS[domain]}
        for domain in sorted(domains)
    ]
    if needs_resource_check:
        result["checks_required"].append(
            {
                "domain": "map",
                "status": "needs_observation",
                "reason": "科技完成信息变化；按解锁规则补查资源可见性，本摘要未确认地图资源变化。",
            }
        )
    result["note"] = (
        "仅报告世界来源的内容与观测状态变化；字段缺失表示本次未提供。"
        "removed 仅表示从完整覆盖结果移除，不推断摧毁或失去已解锁能力。"
    )
    return result

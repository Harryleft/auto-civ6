"""Deterministic local filtering for oversized MCP tool results.

The filter runs only on the model-facing copy of a result. Callers must persist
the original result to local telemetry before invoking :func:`filter_tool_result`.
Small results are returned byte-for-byte unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping
from civ_mcp.result_json import json_object


FILTER_ENV = "CIV_MCP_RESULT_FILTER"
MAX_CHARS_ENV = "CIV_MCP_RESULT_MAX_CHARS"
HISTORY_ITEMS_ENV = "CIV_MCP_RESULT_HISTORY_ITEMS"

_DEFAULT_MAX_CHARS = 20_000
_DEFAULT_HISTORY_ITEMS = 3
_MIN_MAX_CHARS = 2_000
_MAX_MAX_CHARS = 200_000
# v7 M01 删除了提供这三个工具的控制面（civ_mcp.server）。保留本模块是因为
# 「过滤只作用于模型面副本、遥测保留原始全文」这条分层仍有价值，但当前没有
# 任何工具命中这个集合；接入 civ_agent 的工具面时必须重新决定受管名单。
_FILTERED_TOOLS = frozenset(
    {"get_governance_brief", "get_belief_state", "get_belief_trace"}
)
_METRIC_PREFIXES = (
    "player.",
    "barbarian.",
    "combat.",
    "diplomacy.",
    "government.",
    "research.",
    "civic.",
    "resource.",
    "victory.",
)
_METRIC_NAMES = frozenset(
    {
        "turn",
        "score",
        "gold",
        "gold_per_turn",
        "science",
        "culture",
        "faith",
        "favor",
        "cities",
        "population",
        "units",
        "exploration_pct",
        "era_score",
        "game_speed",
        "speed_cost_multiplier",
        "observed_city_count",
        "observed_unit_count",
    }
)


@dataclass(frozen=True, slots=True)
class ResultFilterConfig:
    """Resolved local filtering policy."""

    enabled: bool = True
    max_chars: int = _DEFAULT_MAX_CHARS
    history_items: int = _DEFAULT_HISTORY_ITEMS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ResultFilterConfig":
        source = os.environ if environ is None else environ
        enabled = str(source.get(FILTER_ENV, "1")).strip().lower() not in {
            "0",
            "false",
            "off",
            "no",
        }
        max_chars = _bounded_int(
            source.get(MAX_CHARS_ENV),
            default=_DEFAULT_MAX_CHARS,
            minimum=_MIN_MAX_CHARS,
            maximum=_MAX_MAX_CHARS,
        )
        history_items = _bounded_int(
            source.get(HISTORY_ITEMS_ENV),
            default=_DEFAULT_HISTORY_ITEMS,
            minimum=1,
            maximum=50,
        )
        return cls(
            enabled=enabled,
            max_chars=max_chars,
            history_items=history_items,
        )


def _bounded_int(
    raw: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def filter_tool_result(
    tool_name: str,
    result: str,
    *,
    params: Mapping[str, Any] | None = None,
    config: ResultFilterConfig | None = None,
) -> str:
    """Return a smaller model-facing result without changing local evidence.

    Only known control-plane JSON is compacted. Unknown tools, targeted belief
    queries, malformed JSON, and already-filtered results pass through unchanged
    so action/control markers can never be damaged by a generic truncation.
    """

    policy = config or ResultFilterConfig.from_env()
    if (
        not policy.enabled
        or len(result) <= policy.max_chars
        or tool_name not in _FILTERED_TOOLS
    ):
        return result

    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
    request = params or {}
    if tool_name == "get_governance_brief":
        return _compact_governance_brief(
            result,
            digest=digest,
            history_items=policy.history_items,
        ) or result
    if tool_name == "get_belief_state":
        if str(request.get("entity_type") or "").strip():
            return result
        return _compact_belief_state(
            result,
            digest=digest,
            history_items=policy.history_items,
        ) or result
    if str(request.get("entity_type") or "").strip() or str(
        request.get("entity_id") or ""
    ).strip():
        return result
    return _compact_belief_trace(
        result,
        digest=digest,
        history_items=policy.history_items,
    ) or result


def _compact_governance_brief(
    result: str,
    *,
    digest: str,
    history_items: int,
) -> str | None:
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if _already_filtered(parsed):
        return result

    compacted = deepcopy(parsed)
    omitted: dict[str, int] = {}
    belief_brief = compacted.get("belief_brief")
    if isinstance(belief_brief, dict):
        current_metrics = belief_brief.get("current_metrics")
        if isinstance(current_metrics, dict):
            kept = _decision_metrics(current_metrics)
            omitted["belief_brief.current_metrics"] = len(current_metrics) - len(kept)
            belief_brief["current_metrics"] = kept
        review = belief_brief.get("review")
        if isinstance(review, dict) and isinstance(review.get("metrics"), dict):
            review_metrics = review["metrics"]
            kept = _decision_metrics(review_metrics)
            omitted["belief_brief.review.metrics"] = len(review_metrics) - len(kept)
            review["metrics"] = kept
            if kept and kept == belief_brief.get("current_metrics"):
                # The reference is local to this self-contained response;
                # it never assumes the model retained an earlier baseline.
                del review["metrics"]
                review["metrics_ref"] = "belief_brief.current_metrics"
                omitted["belief_brief.review.metrics"] += len(kept)

    snapshot = compacted.get("snapshot")
    if isinstance(snapshot, dict):
        # Changed object details and all affected domains live in the bounded
        # world_changes view. Keep counts and a few IDs for these audit lists.
        for key in ("world_entities_changed", "world_entities_archived"):
            values = snapshot.get(key)
            if isinstance(values, list) and len(values) > history_items:
                snapshot[f"{key}_count"] = len(values)
                snapshot[key] = values[:history_items]
                omitted[f"snapshot.{key}"] = len(values) - history_items

    governance = compacted.get("governance")
    if isinstance(governance, dict):
        for key in ("council_decisions", "released_budget_locks"):
            values = governance.get(key)
            if isinstance(values, list) and len(values) > history_items:
                omitted[f"governance.{key}"] = len(values) - history_items
                # Preserve the upstream relevance order (BeliefEngine.list is
                # newest-first for council decisions).
                governance[key] = values[:history_items]

    _add_filter_metadata(
        compacted,
        result=result,
        digest=digest,
        policy="governance_semantic_v2",
        omitted=omitted,
    )
    return _dump_compact(compacted)


def _compact_belief_state(
    result: str,
    *,
    digest: str,
    history_items: int,
) -> str | None:
    parsed = json_object(result)
    if parsed is None or _already_filtered(parsed):
        return result if parsed is not None else None

    compacted = deepcopy(parsed)
    omitted: dict[str, int] = {}
    metrics = compacted.get("current_metrics")
    if isinstance(metrics, dict):
        kept = _decision_metrics(metrics)
        omitted["current_metrics"] = len(metrics) - len(kept)
        compacted["current_metrics"] = kept
    entities = compacted.get("entities")
    if isinstance(entities, dict):
        for entity_type, values in entities.items():
            if isinstance(values, list) and len(values) > history_items:
                omitted[f"entities.{entity_type}"] = len(values) - history_items
                entities[entity_type] = values[:history_items]
    _add_filter_metadata(
        compacted,
        result=result,
        digest=digest,
        policy="belief_state_semantic_v1",
        omitted=omitted,
    )
    return _dump_compact(compacted)


def _compact_belief_trace(
    result: str,
    *,
    digest: str,
    history_items: int,
) -> str | None:
    parsed = json_object(result)
    if parsed is None or _already_filtered(parsed):
        return result if parsed is not None else None

    compacted = deepcopy(parsed)
    events = compacted.get("events")
    if not isinstance(events, list):
        return None
    kept_events = events[-history_items:]
    compacted["events"] = [_event_header(event) for event in kept_events]
    omitted = {"events": max(0, len(events) - len(kept_events))}
    _add_filter_metadata(
        compacted,
        result=result,
        digest=digest,
        policy="belief_trace_headers_v1",
        omitted=omitted,
    )
    return _dump_compact(compacted)


def _event_header(event: Any) -> Any:
    """Reduce one event to its header fields.

    ``changes`` keeps only the modified key names, not the from/to values:
    a world_entity update's ``changes`` embeds full link arrays (KB-scale per
    event). What changed stays visible; the values live in the untouched raw
    result owned by telemetry and remain reachable via a targeted trace.
    """
    if not isinstance(event, dict):
        return deepcopy(event)
    keys = (
        "v",
        "event_id",
        "sequence",
        "timestamp",
        "game_id",
        "run_id",
        "turn",
        "epoch",
        "event_type",
        "entity_type",
        "entity_id",
        "changes",
    )
    header = {key: deepcopy(event[key]) for key in keys if key in event}
    changes = header.get("changes")
    if isinstance(changes, dict):
        header["changes"] = sorted(changes)
    return header


def _dump_compact(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _already_filtered(payload: Mapping[str, Any]) -> bool:
    metadata = payload.get("_local_filter")
    return isinstance(metadata, dict) and metadata.get("applied") is True


def _add_filter_metadata(
    payload: dict[str, Any],
    *,
    result: str,
    digest: str,
    policy: str,
    omitted: Mapping[str, int],
) -> None:
    # This block rides along on every filtered, model-facing result, so it
    # stays minimal: the sha is truncated to 16 chars (collision-free within
    # a game) and line counts were dropped as near-zero signal. The full
    # digest remains in telemetry alongside the raw result.
    payload["_local_filter"] = {
        "applied": True,
        "policy": policy,
        "original_chars": len(result),
        "original_sha256": digest[:16],
        "omitted_counts": {key: value for key, value in omitted.items() if value > 0},
        "raw_owner": "local_telemetry",
    }


def _decision_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): deepcopy(value)
        for key, value in metrics.items()
        if str(key) in _METRIC_NAMES
        or any(str(key).startswith(prefix) for prefix in _METRIC_PREFIXES)
    }

"""Pure MCP-boundary adapters for governance contracts.

This module contains validation and serialization only. It does not register
tools or capture game state; runtime orchestration stays in the server layer.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
import json
from typing import Any

from civ6_belief_engine.belief_engine import BeliefEngineError
from civ_mcp.server import pipeline


def _belief_json_object(raw: str | dict[str, Any], label: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise BeliefEngineError(f"{label} must be a JSON object")
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise BeliefEngineError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise BeliefEngineError(f"{label} must be a JSON object")
    return value


def _belief_json_list(raw: str | list[Any], label: str) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        raise BeliefEngineError(f"{label} must be a JSON array")
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise BeliefEngineError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, list):
        raise BeliefEngineError(f"{label} must be a JSON array")
    return value


def _governance_payload(value: Any) -> Any:
    """Serialize frozen governance contracts without losing typed boundaries."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _governance_payload(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _governance_payload(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_governance_payload(item) for item in value]
    return value


def _national_strategy_payload(value: Any, *, include_details: bool = False) -> dict[str, Any]:
    """Keep the six-module result visible before large governance history spills."""

    campaign = value.campaign
    payload: dict[str, Any] = {
        "snapshot_id": value.snapshot_id,
        "turn": value.turn,
        "departments": [
            {
                "department": assessment.department.value,
                "relevance": assessment.relevance,
                "degraded": assessment.degraded,
                "summary": assessment.summary,
                "support_request_count": len(assessment.support_requests),
                "workstream_ids": [
                    workstream.workstream_id for workstream in assessment.workstreams
                ],
                "proposal_ids": [
                    proposal.proposal_id for proposal in assessment.proposals
                ],
                "evidence_missing": list(assessment.evidence_missing),
            }
            for assessment in value.assessments
        ],
        "campaign": {
            "campaign_id": campaign.campaign_id,
            "objective": campaign.objective,
            "trigger": campaign.trigger,
            "ready_workstream_ids": list(campaign.ready_workstream_ids),
            "blocked_workstream_ids": list(campaign.blocked_workstream_ids),
            "coordination_link_count": len(campaign.coordination_links),
            "unresolved_support_request_count": len(
                campaign.unresolved_support_requests
            ),
            "blind_spots": list(campaign.blind_spots),
        },
    }
    if include_details:
        payload["details"] = _governance_payload(value)
    return payload


def _score_value(value: Any, name: str) -> float:
    """Coerce LLM score drift (bools, numeric strings) into real floats."""

    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise BeliefEngineError(
        f"{name} must be a real number (e.g. 0.8), not {type(value).__name__}"
    )


def _score_mapping(raw: Any, name: str) -> dict[str, float]:
    """Coerce one benefits/costs mapping, tolerating null/empty pseudo-values."""

    if raw is None or hasattr(raw, "__len__") and not len(raw):
        return {}
    if not isinstance(raw, Mapping):
        raise BeliefEngineError(f"{name} must be a JSON object")
    return {
        key: _score_value(value, f"{name}[{key!r}]")
        for key, value in raw.items()
    }


def _proposal_priority(value: Any) -> int:
    """Normalize legacy JSON numeric forms without accepting booleans/fractions."""

    if type(value) is int and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise BeliefEngineError("proposal priority must be a non-negative int")


_IMPACT_URGENCY_LEVELS = frozenset({"low", "medium", "high", "critical"})

# The four canonical levels the domain package scores (_IMPACT_SCORE /
# _URGENCY_SCORE); LLMs routinely substitute plain-language temporal or
# severity words for them.  critical ≈ immediate time pressure or top
# severity (now/asap/紧急/立刻/最高), high ≈ prominent but deferrable
# (high priority/important/重要/高), medium ≈ routine (med/normal/中/一般/普通),
# low ≈ negligible (minor/低/轻微/小).
_IMPACT_URGENCY_SYNONYMS = {
    "now": "critical",
    "immediate": "critical",
    "immediately": "critical",
    "asap": "critical",
    "urgent": "critical",
    "最高": "critical",
    "紧急": "critical",
    "立即": "critical",
    "马上": "critical",
    "立刻": "critical",
    "high priority": "high",
    "important": "high",
    "significant": "high",
    "major": "high",
    "高": "high",
    "重要": "high",
    "med": "medium",
    "normal": "medium",
    "中": "medium",
    "一般": "medium",
    "普通": "medium",
    "minor": "low",
    "低": "low",
    "轻微": "low",
    "小": "low",
}


def _normalize_impact_urgency(value: Any, name: str) -> str:
    """Coerce LLM enum drift (synonyms, case, whitespace) to canonical levels."""

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _IMPACT_URGENCY_LEVELS:
            return normalized
        mapped = _IMPACT_URGENCY_SYNONYMS.get(normalized)
        if mapped is not None:
            return mapped
    raise BeliefEngineError(
        f"{name} must be low, medium, high, or critical; got {value!r}"
    )


def _governance_proposal_from_dict(raw: dict[str, Any]):
    """Validate one ministerial proposal against the shared governance schema."""

    from civ6_belief_engine.governance import (
        ActionIntent,
        BudgetLock,
        EvidenceRequirement,
        ProbabilityConfidence,
        Proposal,
    )

    proposal_id = str(raw.get("proposal_id") or "")

    def evidence(item: dict[str, Any]) -> EvidenceRequirement:
        return EvidenceRequirement(
            requirement_id=str(item.get("requirement_id") or ""),
            tool=str(item.get("tool") or ""),
            target_entity_id=item.get("target_entity_id"),
            params=item.get("params") or {},
            min_observation_sequence=item.get("min_observation_sequence", 0),
            max_age_turns=item.get("max_age_turns"),
            required_facts=tuple(item.get("required_facts") or ()),
            required_metrics=tuple(item.get("required_metrics") or ()),
            expected_facts=item.get("expected_facts") or {},
            description=str(item.get("description") or ""),
        )

    intents = []
    for item in raw.get("action_intents") or []:
        if not isinstance(item, dict):
            raise BeliefEngineError("action_intents must contain JSON objects")
        requirements = item.get("evidence_requirements") or []
        if not all(isinstance(requirement, dict) for requirement in requirements):
            raise BeliefEngineError(
                "action intent evidence_requirements must contain JSON objects"
            )
        intent_tool = str(item.get("tool") or "")
        supplied_arguments = item.get("arguments")
        supplied_params = item.get("params")
        if (
            supplied_arguments is not None
            and supplied_params is not None
            and supplied_arguments != supplied_params
        ):
            raise BeliefEngineError(
                "action intent arguments and params must match when both are present"
            )
        intent_arguments = pipeline._canonical_action_params(
            intent_tool,
            supplied_arguments
            if supplied_arguments is not None
            else supplied_params
            if supplied_params is not None
            else {},
        )
        intents.append(
            ActionIntent(
                intent_id=str(item.get("intent_id") or ""),
                tool=intent_tool,
                arguments=intent_arguments,
                proposal_id=proposal_id,
                evidence_requirements=tuple(evidence(req) for req in requirements),
                allowed_turn=item.get("allowed_turn"),
                arguments_hash=str(item.get("arguments_hash") or ""),
            )
        )
    locks = []
    for item in raw.get("budget_locks") or []:
        if not isinstance(item, dict):
            raise BeliefEngineError("budget_locks must contain JSON objects")
        locks.append(
            BudgetLock(
                resource=str(item.get("resource") or ""),
                amount=item.get("amount", 1),
                scope=str(item.get("scope") or "global"),
                exclusive=item.get("exclusive", False),
                reason=str(item.get("reason") or ""),
            )
        )
    success = raw.get("success") or {}
    if not isinstance(success, dict):
        raise BeliefEngineError("proposal success must be a JSON object")
    return Proposal(
        proposal_id=proposal_id,
        department=str(raw.get("department") or ""),
        summary=str(raw.get("summary") or raw.get("statement") or ""),
        goal_ids=tuple(raw.get("goal_ids") or ()),
        success=ProbabilityConfidence(
            success.get("probability"), success.get("confidence")
        ),
        priority=_proposal_priority(raw.get("priority")),
        hard_constraints=raw.get("hard_constraints") or {},
        budget_locks=tuple(locks),
        benefits=_score_mapping(raw.get("benefits"), "benefits"),
        costs=_score_mapping(raw.get("costs"), "costs"),
        opportunity_cost=_score_value(raw.get("opportunity_cost", 0), "opportunity_cost"),
        failure_cost=_score_value(raw.get("failure_cost", 0), "failure_cost"),
        action_intents=tuple(intents),
        belief_ids=tuple(raw.get("belief_ids") or ()),
        expires_turn=raw.get("expires_turn"),
    )


def _goal_priority(raw: Mapping[str, Any]) -> int:
    """Normalize legacy JSON numeric forms without accepting booleans/fractions."""

    value = raw.get("priority")
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise BeliefEngineError("goal priority must be a non-negative integer")


def _governance_goal_from_dict(raw: Mapping[str, Any]):
    """Restore one active goal into the immutable governance contract."""

    from civ6_belief_engine.governance import ProbabilityConfidence, StrategicGoal

    if not isinstance(raw, Mapping):
        raise BeliefEngineError("active goal must be a JSON object")
    success = raw.get("success")
    if success is None:
        # Pre-typed Goal records only guaranteed statement and priority.
        # Keep unknown likelihood explicit rather than rejecting old journals.
        probability = raw.get("probability", 0.5)
        confidence = raw.get("confidence", 0.0)
    elif isinstance(success, Mapping):
        probability = success.get("probability")
        confidence = success.get("confidence")
    else:
        raise BeliefEngineError("goal success must be a JSON object")
    return StrategicGoal(
        goal_id=str(raw.get("goal_id") or raw.get("id") or ""),
        statement=str(raw.get("statement") or ""),
        priority=_goal_priority(raw),
        success=ProbabilityConfidence(probability, confidence),
        hard_constraints=tuple(raw.get("hard_constraints") or ()),
        deadline_turn=raw.get("deadline_turn"),
        parent_goal_id=raw.get("parent_goal_id") or None,
        tags=tuple(raw.get("tags") or ()),
    )


def _goal_graph_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project the complete typed goal contract into the current graph."""

    return {
        "goal_id": str(raw.get("goal_id") or raw.get("id") or ""),
        "statement": str(raw.get("statement") or ""),
        "priority": _goal_priority(raw),
        "success": raw.get("success")
        or {
            "probability": raw.get("probability", 0.5),
            "confidence": raw.get("confidence", 0.0),
        },
        "hard_constraints": list(raw.get("hard_constraints") or ()),
        "deadline_turn": raw.get("deadline_turn"),
        "parent_goal_id": raw.get("parent_goal_id"),
        "tags": list(raw.get("tags") or ()),
    }

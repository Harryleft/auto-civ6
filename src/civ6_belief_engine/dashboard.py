"""Build dashboard state from a belief journal, read-only.

The run dashboard is a monitoring surface: it must never touch FireTuner,
the live MCP process, or write to the journal. Everything here derives
from journal files via the coverage reducer plus small mirrors of engine
semantics (turn-gate readiness, civ identity). The authoritative gate
remains ``BeliefEngine.governance_turn_gate``; the mirror here exists so
a second process can display readiness without mutating anything.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .belief_engine import action_args_hash
from .coverage import _reduce_entities, load_journal, summarize_game

DEFAULT_JOURNAL_DIR = Path.home() / ".civ6-mcp" / "beliefs"

CIV_NAMES: dict[str, tuple[str, str]] = {
    "england": ("ENGLAND", "英格兰"),
    "france": ("FRANCE", "法兰西"),
    "rome": ("ROME", "罗马"),
    "greece": ("GREECE", "希腊"),
    "germany": ("GERMANY", "德意志"),
    "korea": ("KOREA", "高丽"),
    "china": ("CHINA", "中国"),
    "japan": ("JAPAN", "日本"),
    "russia": ("RUSSIA", "俄罗斯"),
    "spain": ("SPAIN", "西班牙"),
    "egypt": ("EGYPT", "埃及"),
    "america": ("AMERICA", "美国"),
    "india": ("INDIA", "印度"),
    "aztec": ("AZTEC", "阿兹特克"),
    "arabia": ("ARABIA", "阿拉伯"),
}

DIFFICULTY_NAMES = {"神": "神级 DEITY", "帝王": "帝王 IMMORTAL", "不朽": "不朽 EMPEROR", "王子": "王子 KING"}

_KINDS: dict[str, tuple[str, str]] = {
    "observation": ("观测", "OBSERVATION"),
    "decision": ("决策", "DECISION"),
    "action": ("动作", "ACTION"),
    "outcome": ("结局", "OUTCOME"),
    "prediction": ("预测", "PREDICTION"),
    "proposal": ("政务", "PROPOSAL"),
    "goal": ("目标", "GOAL"),
    "council_decision": ("议会", "COUNCIL"),
    "budget_lock": ("预算锁", "BUDGET"),
    "surprise": ("惊异", "SURPRISE"),
    "contradiction": ("矛盾", "CONTRADICTION"),
    "hypothesis": ("假说", "HYPOTHESIS"),
    "attribution": ("归因", "ATTRIBUTION"),
}

_STAMPS: dict[tuple[str, str], tuple[str, str]] = {
    ("decision", "succeeded"): ("SUCCEEDED", "ok"),
    ("decision", "cancelled"): ("CANCELLED", "bad"),
    ("decision", "authorized"): ("AUTHORIZED", "wait"),
    ("decision", "executing"): ("EXECUTING", "wait"),
    ("decision", "retryable"): ("RETRYABLE", "wait"),
    ("decision", "outcome_unknown"): ("UNKNOWN", "wait"),
    ("action", "succeeded"): ("SUCCEEDED", "ok"),
    ("action", "failed"): ("FAILED", "bad"),
    ("action", "blocked"): ("BLOCKED", "bad"),
    ("action", "unknown"): ("UNKNOWN", "wait"),
    ("prediction", "active"): ("PENDING", "wait"),
    ("prediction", "confirmed"): ("CONFIRMED", "ok"),
    ("prediction", "disconfirmed"): ("DISCONFIRMED", "bad"),
    ("prediction", "overdue"): ("OVERDUE", "wait"),
    ("proposal", "approved"): ("APPROVED", "wait"),
    ("proposal", "resolved"): ("RESOLVED", "ok"),
    ("proposal", "rejected"): ("REJECTED", "bad"),
    ("proposal", "proposed"): ("PROPOSED", "wait"),
}

_CHRONICLE_LIMIT = 40


def pick_live_journal(directory: Path = DEFAULT_JOURNAL_DIR) -> Path | None:
    """Return the most recently modified journal, or None when absent."""

    if not directory.is_dir():
        return None
    journals = [p for p in directory.glob("belief_*.jsonl") if p.stat().st_size > 0]
    if not journals:
        return None
    return max(journals, key=lambda p: p.stat().st_mtime)


def _civ_from_journal(path: Path) -> tuple[str, str, str]:
    token = path.stem[len("belief_") :].rsplit("_", 1)[0]
    key = token.lower().removeprefix("civilization_")
    english, chinese = CIV_NAMES.get(key, (key.upper(), key))
    return key, english, chinese


def _latest_by_source(entities: dict, source_tool: str) -> dict[str, Any] | None:
    candidates = [
        entity
        for entity in entities.get("observation", {}).values()
        if (entity.get("facts") or {}).get("tool") == source_tool
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda e: (e.get("observed_turn", 0), e.get("updated_at", 0)))


def _intent_terminal(proposal: dict, intent: dict, decisions: list[dict]) -> bool:
    """Mirror of the turn gate's terminal-decision matching (read-only)."""

    intent_id = str(intent.get("intent_id") or "")
    intent_params = intent.get("params") or intent.get("arguments") or {}
    for decision in decisions:
        if decision.get("decision_state") not in {"succeeded", "cancelled"}:
            continue
        di = decision.get("action_intent")
        if not isinstance(di, dict):
            continue
        if decision.get("council_decision_id") != proposal.get("council_decision_id"):
            continue
        if di.get("proposal_id") != proposal["id"]:
            continue
        decision_intent_id = str(di.get("intent_id") or "")
        if not intent_id or decision_intent_id == intent_id:
            return True
        if not decision_intent_id:
            dp = di.get("params") or di.get("arguments") or {}
            if (
                di.get("tool") == intent.get("tool")
                and isinstance(dp, dict)
                and isinstance(intent_params, dict)
                and action_args_hash(dp) == action_args_hash(intent_params)
            ):
                return True
    return False


def _gate_mirror(entities: dict, turn: int) -> dict[str, Any]:
    decisions = list(entities.get("decision", {}).values())
    pending_auth = [
        {"decision_id": d["id"], "decision_state": d.get("decision_state")}
        for d in decisions
        if d.get("decision_state") in {"authorized", "executing", "retryable", "outcome_unknown"}
    ]
    active_proposals = [
        p["id"] for p in entities.get("proposal", {}).values() if p.get("status") == "active"
    ]
    pending_intents: list[dict[str, Any]] = []
    for proposal in entities.get("proposal", {}).values():
        if proposal.get("council_state") != "approved":
            continue
        for intent in proposal.get("action_intents") or []:
            if not isinstance(intent, dict):
                continue
            if _intent_terminal(proposal, intent, decisions):
                continue
            pending_intents.append(
                {"proposal_id": proposal["id"], "intent_id": intent.get("intent_id") or None}
            )
    blockers: list[str] = []
    if active_proposals:
        blockers.append("governance_proposals_not_arbitrated")
    if pending_intents:
        blockers.append("council_action_intents_not_completed")
    if pending_auth:
        blockers.append("routed_actions_not_completed")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "pending_council_intents": pending_intents,
        "pending_authorizations": pending_auth,
    }


def _chronicle(events: list[dict]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_epoch: int | None = None
    seen_run: str | None = None
    last_graph_turn: int | None = None
    for event in events:
        entity_type = event.get("entity_type")
        epoch = event.get("epoch")
        run_id = str(event.get("run_id") or "")
        if seen_epoch is not None and epoch != seen_epoch or (seen_run and run_id and run_id != seen_run):
            entries.append(
                {
                    "break": True,
                    "epoch": epoch,
                    "run_changed": bool(run_id and seen_run and run_id != seen_run),
                    "turn": event.get("turn"),
                }
            )
        seen_epoch = epoch if epoch is not None else seen_epoch
        seen_run = run_id or seen_run

        if event.get("event_type") == "graph.delta" or entity_type == "graph_delta":
            if last_graph_turn == event.get("turn"):
                continue
            last_graph_turn = event.get("turn")
            entries.append(_entry(event, "graph", ("图纪", "GRAPH DELTA"), None, None))
            continue
        if entity_type in {"world_entity"}:
            continue
        kind = _KINDS.get(str(entity_type))
        if kind is None:
            continue
        entity = event.get("entity") or {}
        stamp_key = (
            entity.get("decision_state")
            if entity_type == "decision"
            else entity.get("outcome_status")
            if entity_type == "action"
            else entity.get("council_state") or entity.get("status")
            if entity_type in {"prediction", "proposal", "goal"}
            else None
        )
        stamp, stamp_class = _STAMPS.get((str(entity_type), str(stamp_key)), (None, None))
        text = entity.get("cancellation_reason") or entity.get("statement") or ""
        entry = _entry(event, entity_type, kind, stamp, stamp_class)
        entry["text"] = str(text)
        # Consecutive observations from the same tool (the turn loop re-reads
        # overview/units every turn) carry no new story: keep the latest only.
        source = str(entity.get("source") or "")
        if (
            entity_type == "observation"
            and entries
            and entries[-1].get("entity_type") == "observation"
            and entries[-1].get("_source") == source
        ):
            entry["_source"] = source
            entries[-1] = entry
            continue
        entry["_source"] = source if entity_type == "observation" else None
        entries.append(entry)
    entries.reverse()
    for entry in entries:
        entry.pop("_source", None)
    return entries[:_CHRONICLE_LIMIT]


def _entry(event: dict, entity_type: str, kind: tuple[str, str], stamp: str | None, stamp_class: str | None) -> dict:
    return {
        "turn": event.get("turn"),
        "time": time.strftime("%H:%M", time.localtime(event.get("timestamp") or 0)),
        "entity_type": entity_type,
        "kind_zh": kind[0],
        "kind_en": kind[1],
        "stamp": stamp,
        "stamp_class": stamp_class,
    }


def build_dashboard_state(path: str | Path) -> dict[str, Any]:
    """Derive the full dashboard state from one journal file."""

    journal = Path(path)
    events, skipped = load_journal(journal)
    if not events:
        return {"status": "empty", "journal": str(journal)}

    entities = _reduce_entities(events)
    key, english, chinese = _civ_from_journal(journal)
    overview = _latest_by_source(entities, "get_game_overview")
    metrics = (overview or {}).get("metrics") or {}
    facts = (overview or {}).get("facts") or {}
    summary = str(facts.get("summary") or "")

    leader = None
    for segment in summary.split("|"):
        seg = segment.strip()
        if "(" in seg and ")" in seg:
            inner = seg[seg.index("(") + 1 : seg.rindex(")")]
            if inner and "Score" not in inner and "turn" not in inner.lower():
                leader = inner
                break
    difficulty = DIFFICULTY_NAMES.get(summary.split("|")[-1].strip()) if summary else None

    cities_observation = _latest_by_source(entities, "get_cities")
    victory_metrics: dict[str, Any] = {}
    for entity in entities.get("observation", {}).values():
        if (entity.get("facts") or {}).get("tool") == "get_victory_progress":
            for k, v in (entity.get("metrics") or {}).items():
                if k.startswith("victory.") and (k.endswith("_vp") or k.endswith("_target")):
                    victory_metrics[k] = v

    victory_rows: list[dict[str, Any]] = []
    for vp_key, vp in sorted(victory_metrics.items()):
        if not vp_key.endswith("_vp"):
            continue
        target = victory_metrics.get(f"{vp_key}_target")
        if not isinstance(target, (int, float)) or target <= 0 or not isinstance(vp, (int, float)) or vp <= 0:
            continue
        parts = vp_key.split(".")
        slug = parts[1] if len(parts) >= 3 else "world"
        section = parts[2][:-3] if len(parts) >= 3 and parts[2].endswith("_vp") else "vp"
        prediction = entities.get("prediction", {}).get(f"auto:pred:victory:{slug}:{section}")
        victory_rows.append(
            {
                "name": slug,
                "section": section,
                "vp": vp,
                "target": target,
                "eta_turn": (prediction or {}).get("predicted_turn"),
            }
        )
    victory_rows.sort(key=lambda r: -(r["vp"] / r["target"]))
    victory_rows = victory_rows[:4]

    predictions = sorted(
        entities.get("prediction", {}).values(),
        key=lambda p: -(p.get("last_updated_turn") or p.get("created_turn") or 0),
    )[:6]

    age = time.time() - journal.stat().st_mtime
    council_count = len(entities.get("council_decision", {}))
    turn = max((e.get("turn") or 0) for e in events)

    return {
        "status": "ok",
        "journal": str(journal),
        "game_id": events[-1].get("game_id"),
        "run_id": next((e.get("run_id") for e in reversed(events) if e.get("run_id")), None),
        "live": age < 300,
        "last_event_age_s": int(age),
        "events_total": len(events),
        "skipped_lines": skipped,
        "civ": {"key": key, "english": english, "chinese": chinese, "leader": leader},
        "difficulty": difficulty,
        "turn": turn,
        "epoch": max((e.get("epoch") or 0) for e in events),
        "policy": {
            "belief_events": "RECORDED",
            "governance": "ENFORCED" if council_count else ("OBSERVE" if events else "OFF"),
        },
        "empire": {
            "score": metrics.get("score"),
            "gold": metrics.get("gold"),
            "gold_per_turn": metrics.get("gold_per_turn"),
            "science": metrics.get("science"),
            "culture": metrics.get("culture"),
            "faith": metrics.get("faith"),
            "units": metrics.get("units"),
            "explored_pct": metrics.get("exploration_pct"),
            "era": metrics.get("era"),
            "era_score": metrics.get("era_score"),
            "era_dark": metrics.get("era.dark_threshold"),
            "era_golden": metrics.get("era.golden_threshold"),
            "cities": [
                {"name": c.get("name"), "population": c.get("population"), "x": c.get("x"), "y": c.get("y")}
                for c in (cities_observation or {}).get("facts", {}).get("cities") or []
            ][:5],
            "victory_rows": victory_rows,
        },
        "chronicle": _chronicle(events),
        "predictions": [
            {
                "id": p["id"],
                "statement": p.get("statement"),
                "status": p.get("status"),
                "derived": "derived" in (p.get("tags") or []),
                "predicted_turn": p.get("predicted_turn"),
                "deadline_turn": p.get("deadline_turn"),
                "resolution_source": p.get("resolution_source"),
                "prediction_error": p.get("prediction_error"),
                "updated_turn": p.get("last_updated_turn") or p.get("created_turn"),
            }
            for p in predictions
        ],
        "ledger": summarize_game(events),
        "gate": _gate_mirror(entities, turn),
    }

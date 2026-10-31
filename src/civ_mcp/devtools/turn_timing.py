"""Timing from existing CLI receipts only; no game queries or action replay."""
from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import re

OPERATORS = {"setup", "repair", "final", "offline"}
LOAD_TOOLS = {"load_game_save", "reload_game_save", "load_save_from_menu", "restart_and_load", "load_save"}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


DECISION_CHECKS = ("stable_world_turn", "overview_scope", "unit_scope", "city_scope", "victory_scope")


def valid_identity(identity):
    return (isinstance(identity, dict) and isinstance(identity.get("civilization"), str)
            and bool(identity["civilization"].strip())
            and all(type(identity.get(k)) is int for k in ("seed", "player_id", "turn"))
            and identity["player_id"] >= 0 and identity["turn"] >= 0)


def decision_identity(result):
    """Accept only the known decision envelope, including MCP's duplicate wrapper.

    All representations must certify the same identity. Content prose and JSON
    from other tools are deliberately outside this parser.
    """
    values = [c.get("text") for c in result.get("content", []) if c.get("type") == "text"]
    if "structuredContent" in result:
        structured = result["structuredContent"]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            structured = structured["result"]
        values.append(structured)
    identities = []
    for value in values:
        try:
            data = json.loads(value) if isinstance(value, str) else value
        except (ValueError, TypeError):
            return None, "decision_anchor_invalid"
        if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
                or data["schema_version"] != 1 or data.get("status") != "ready"
                or not isinstance(data.get("checks"), dict)
                or not all(data["checks"].get(k) is True for k in DECISION_CHECKS)
                or not valid_identity(data.get("identity"))):
            return None, "decision_anchor_invalid"
        identities.append(data["identity"])
    if not identities:
        return None, "decision_anchor_invalid"
    keys = ("civilization", "seed", "player_id", "turn")
    if len({tuple(value[k] for k in keys) for value in identities}) != 1:
        return None, "decision_anchor_conflict"
    return identities[0], None


class TurnTiming:
    """One row per observed world turn; only confirmed end-turn closes a row.

    Inter-command gaps include model/provider, shell and handoff waits. They
    cannot establish pure model thinking time. The last observed turn stays
    partial when the test stops without confirming its next turn.
    """

    def __init__(self, actors=None):
        self.actors = set(actors) if actors else None
        self.seen = set()
        self.segment = 1
        self.identity = None
        self.turn = None
        self.boundary = None
        self.current = None
        self.rounds = []
        self.commands = []
        self.issues = []
        self.unattributed_confirmed_advance = []

    def reset(self, reason):
        if self.current:
            self.current["status"] = reason
        self.segment += 1
        self.current = self.boundary = self.turn = self.identity = None

    def observe(self, record):
        request_id = record.get("request_id")
        if request_id in self.seen:
            return
        self.seen.add(request_id)
        events = record.get("events", [])
        command = record.get("command", "")
        if events and (events[0].get("event") != "command" or events[0].get("input") != command):
            self.issues.append({"request_id": request_id, "reason": "reply_frame_mismatch"})
            return
        tools = [e for e in events if e.get("event") == "tool_result"]
        text = "\n".join(c.get("text", "") for e in tools
                         if e.get("tool") == "get_game_overview" and not e.get("is_error")
                         for c in e.get("result", {}).get("content", []) if c.get("type") == "text")
        loading = any(e.get("tool") in LOAD_TOOLS for e in tools)
        if loading:
            self.reset("load_boundary")
        observed = None
        for event in events:
            if event.get("event") == "verification" and event.get("status") == "PASS":
                identity = event.get("identity", {})
                if not valid_identity(identity):
                    self.issues.append({"request_id": request_id, "reason": "identity_incomplete"})
                    continue
                world = tuple(identity.get(k) for k in ("civilization", "seed", "player_id"))
                if self.identity and world != self.identity:
                    self.reset("world_changed")
                self.identity = world
                observed = identity.get("turn")
        decision_anchors = []
        decision_error = None
        for event in tools:
            if event.get("tool") == "get_decision_context" and not event.get("is_error"):
                identity, error = decision_identity(event.get("result", {}))
                if error:
                    decision_error = error
                else:
                    decision_anchors.append(identity)
        keys = ("civilization", "seed", "player_id", "turn")
        if len({tuple(value[k] for k in keys) for value in decision_anchors}) > 1:
            decision_error = "decision_anchor_conflict"
        if decision_error:
            self.issues.append({"request_id": request_id, "reason": decision_error})
        elif decision_anchors:
            identity = decision_anchors[0]
            world = tuple(identity[k] for k in keys[:3])
            if self.identity and world != self.identity:
                self.reset("world_changed")
            self.identity = world
            observed = identity["turn"]
        overview = re.search(r"^Turn (\d+) \|", text, re.M)
        if overview:
            observed = int(overview[1])
        if type(observed) is int:
            if self.turn is not None and self.turn != observed:
                if self.current:
                    self.current["status"] = "unconfirmed_boundary"
                self.current = self.boundary = None
            self.turn = observed

        actor = record.get("actor")
        if (self.actors is not None and actor not in self.actors) or (self.actors is None and actor in OPERATORS):
            return
        start, end = record.get("started"), record.get("finished")
        if not number(start) or not number(end):
            self.issues.append({"request_id": request_id, "reason": "incomplete_or_invalid_time"})
            return
        mono_start, mono_end = record.get("started_monotonic"), record.get("finished_monotonic")
        monotonic = number(mono_start) and number(mono_end) and mono_end >= mono_start
        if not monotonic and end < start:
            self.issues.append({"request_id": request_id, "reason": "backward_wall_clock"})
            return
        clock = "monotonic" if monotonic else "wall"
        tick_start, tick_end = (mono_start, mono_end) if monotonic else (start, end)
        duration = tick_end - tick_start
        rpc = 0.0
        rpc_known = not (record.get("state") == "outcome_unknown" and not events)
        starts = {e["sequence"]: e for e in events if e.get("event") == "tool_started"}
        for event in events:
            if event.get("event") in {"tool_result", "tool_outcome_unknown"} and event.get("request") not in starts:
                rpc_known = False
                self.issues.append({"request_id": request_id, "reason": "rpc_start_unavailable"})
            if event.get("event") in {"tool_result", "tool_outcome_unknown"} and event.get("request") in starts:
                first = starts[event["request"]]
                try:
                    elapsed = (datetime.fromisoformat(event["timestamp"]) - datetime.fromisoformat(first["timestamp"])).total_seconds()
                    if elapsed >= 0:
                        rpc += elapsed
                    else:
                        rpc_known = False
                        self.issues.append({"request_id": request_id, "reason": "rpc_clock_reversed"})
                except (KeyError, ValueError, TypeError):
                    rpc_known = False
                    self.issues.append({"request_id": request_id, "reason": "rpc_time_unavailable"})
        if rpc > duration + .01:
            rpc_known = False
            self.issues.append({"request_id": request_id, "reason": "rpc_exceeds_command_span"})
        if not rpc_known:
            rpc = None
        errors = sum(bool(e.get("is_error")) or e.get("event") == "command_blocked" for e in events)
        unknown = record.get("state") == "outcome_unknown" or any(
            e.get("event") == "tool_outcome_unknown" or (e.get("error") or {}).get("code") in {"OUTCOME_UNKNOWN", "QUERY_UNCONFIRMED"} for e in events)
        row = {"request_id": request_id, "segment": self.segment, "turn": self.turn,
               "actor": actor, "command": command, "started": start, "finished": end,
               "cli_seconds": duration, "rpc_seconds": rpc, "clock_source": clock,
               "error_count": errors, "outcome_unknown": bool(unknown)}
        self.commands.append(row)
        advances = []
        for event in tools:
            if event.get("tool") == "end_turn" and not event.get("is_error"):
                result = "\n".join(c.get("text", "") for c in event.get("result", {}).get("content", [])
                                   if c.get("type") == "text")
                match = re.search(r"^Turn (\d+) -> (\d+)(?:\n|$)", result)
                if match and int(match[2]) > int(match[1]):
                    advances.append((int(match[1]), int(match[2])))
        for before, after in advances:
            if self.turn is None or before != self.turn:
                self.unattributed_confirmed_advance.append({"request_id": request_id,
                    "segment": self.segment, "from": before, "to": after})
        if self.turn is None:
            return
        if self.current is None:
            boundary = self.boundary if self.boundary and self.boundary[2] == clock else (start, tick_start, clock)
            self.current = {"segment": self.segment, "turn": self.turn, "actors": [],
                            "status": "partial", "started": boundary[0], "finished": end,
                            "wall_seconds": 0.0, "cli_seconds": 0.0, "rpc_seconds": 0.0,
                            "gap_seconds": 0.0, "command_count": 0, "error_count": 0,
                            "unknown_count": 0, "clock_source": clock, "_start_tick": boundary[1]}
            self.rounds.append(self.current)
        current = self.current
        if clock != current["clock_source"]:
            self.issues.append({"request_id": request_id, "reason": "clock_source_changed"})
            tick_end = end
            current["_start_tick"] = current["started"]
            current["clock_source"] = "wall"
        if actor not in current["actors"]:
            current["actors"].append(actor)
        current["finished"] = end
        current["wall_seconds"] = tick_end - current["_start_tick"]
        current["cli_seconds"] += duration
        current["rpc_seconds"] = current["rpc_seconds"] + rpc if current["rpc_seconds"] is not None and rpc is not None else None
        current["command_count"] += 1
        current["error_count"] += errors
        current["unknown_count"] += bool(unknown)
        current["gap_seconds"] = max(0.0, current["wall_seconds"] - current["cli_seconds"])
        if current["wall_seconds"] < current["cli_seconds"] - .001:
            self.issues.append({"request_id": request_id, "reason": "overlapping_commands_or_clock_jump"})
        # Only the existing end_turn's confirmed receipt establishes a boundary.
        # A newer overview alone never marks the previous turn completed.
        for before, after in advances:
            if before == self.turn:
                current["status"] = "completed"
                self.turn = after
                self.boundary = (end, tick_end, current["clock_source"])
                self.current = None
                break

    def report(self):
        rounds = [{k: v for k, v in row.items() if not k.startswith("_")} for row in self.rounds]
        by_actor, by_command = {}, {}
        for command in self.commands:
            key = " ".join(command["command"].split()[:2])
            for target, name in [(by_actor, command["actor"]), (by_command, key)]:
                bucket = target.setdefault(name, {"calls": 0, "cli_seconds": 0.0, "rpc_seconds": 0.0,
                                                  "max_cli_seconds": 0.0, "error_count": 0, "unknown_count": 0})
                bucket["calls"] += 1
                bucket["cli_seconds"] += command["cli_seconds"]
                bucket["rpc_seconds"] = bucket["rpc_seconds"] + command["rpc_seconds"] if bucket["rpc_seconds"] is not None and command["rpc_seconds"] is not None else None
                bucket["max_cli_seconds"] = max(bucket["max_cli_seconds"], command["cli_seconds"])
                bucket["error_count"] += command["error_count"]
                bucket["unknown_count"] += command["outcome_unknown"]
        return {"schema_version": 1, "scope": "observed_cli_receipts", "rounds": rounds,
                "completed_turns": sum(r["status"] == "completed" for r in rounds),
                "partial_turns": sum(r["status"] == "partial" for r in rounds),
                "command_count": len(self.commands), "issues": self.issues,
                "unattributed_confirmed_advance": self.unattributed_confirmed_advance,
                "quality": "partial" if self.issues or self.unattributed_confirmed_advance else "complete_for_observed_commands",
                "by_actor": by_actor, "by_command": by_command,
                "gap_meaning": "Model/provider, shell, human and handoff waits; not pure thinking time",
                "unknown_duration_meaning": "Time observed until the error; not proof the game operation completed"}

    def export(self, directory):
        directory = Path(directory)
        report = self.report()
        for name, value in [("turn-timing.json", report),
                            ("command-timing.json", {"schema_version": 1, "commands": self.commands})]:
            temp = directory / (name + ".tmp")
            temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            temp.replace(directory / name)
        return report


def read_relay(directory, actors=None):
    timing = TurnTiming(actors)
    paths = list((Path(directory) / "requests").glob("*.json"))
    if not paths:
        raise ValueError("No relay request receipts found; supply its evidence directory")
    records = [json.loads(p.read_text()) for p in paths]
    order = "started_monotonic" if all(number(r.get("started_monotonic")) for r in records) else "started"
    for record in sorted(records, key=lambda value: value.get(order, math.inf)):
        timing.observe(record)
    return timing

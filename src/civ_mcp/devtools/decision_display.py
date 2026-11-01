"""Lossless presentation of newly read decisions, never a game-state cache.

One foreground display baseline requires the caller's explicit token. Runtime
still reads every fact and owns all pending/confirmation decisions. Text edits
refer to exact old line offsets; anchors are current text for human orientation,
not extra facts or a strategy filter. A decoder verifies exact reconstruction.
"""
from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import json

from civ_mcp.devtools.turn_timing import DECISION_CHECKS, valid_identity
from civ_mcp.results import explicit_outcome


CORE = frozenset(("overview", "units", "cities", "threats", "victory", "notifications", "blockers", "outcome"))
PENDING = frozenset(("action", "turn", "diplomacy", "congress", "save", "replacement", "popup"))
# These calls may replace the world even if a later response is lost/refused.
# An ordinary end-turn is deliberately not a replacement.
REPLACEMENT_TOOLS = frozenset(("load_save", "load_game_save", "reload_game_save", "kill_game",
    "return_to_main_menu", "launch_game", "load_save_from_menu", "restart_and_load", "run_lua",
    "begin_hotseat_turn", "end_hotseat_turn"))


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _ready(value):
    return (isinstance(value, dict) and value.get("schema_version") == 1 and value.get("status") == "ready"
        and valid_identity(value.get("identity")) and isinstance(value.get("pending"), dict)
        and set(value["pending"]) == PENDING and all(v is False for v in value["pending"].values())
        and isinstance(value.get("checks"), dict) and all(value["checks"].get(k) is True for k in DECISION_CHECKS)
        and isinstance(value.get("sections"), dict) and CORE <= value["sections"].keys()
        and all(isinstance(s, dict) and s.get("status") == "ready" and "data" in s for s in value["sections"].values()))


def _entity_id(line):
    if line.startswith("["):
        try:
            row = json.loads(line)
            if isinstance(row, list) and row and type(row[0]) is int:
                return row[0]
        except ValueError:
            pass
    return None


def _anchors(lines, ranges, section):
    """Attach current owning row/header, without interpreting its game meaning."""
    found = {}
    for start, end in ranges:
        # Only added/replacement text needs a current parent. A deletion at the
        # end of an entity must not be labelled with the next surviving entity.
        if start == end:
            continue
        for index in range(start, -1, -1):
            line = lines[index]
            entity = section in {"cities", "units"} and _entity_id(line) is not None
            if entity or line.startswith("JSON rows:") or (section == "victory" and line.strip().isupper()):
                if not any(a <= index < b for a, b in ranges):
                    found[index] = line
                break
    return [[index, line] for index, line in sorted(found.items())]


def _text_patch(old, new, section):
    before, after = old.splitlines(keepends=True), new.splitlines(keepends=True)
    splices, ranges, deletions = [], [], []
    for tag, i, j, x, y in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            splices.append([i, j - i, "".join(after[x:y])])
            ranges.append((x, y))
            if i < j:
                deletions.append((i, j))
    patch = {"encoding": "line-splices-v1", "base_sha256": _digest(old),
             "splices": splices, "anchors": _anchors(after, ranges, section)}
    if section in {"cities", "units"}:
        observed_ids = {_entity_id(line) for line in after}
        removed = [[index, line] for index, line in enumerate(before)
                   if _entity_id(line) is not None and _entity_id(line) not in observed_ids]
        if removed:
            # Absence from a current observation is not evidence of death.
            patch["removed_from_observation"] = removed
        # Deleted attached lines have no replacement to orient the reader.
        # Their old owner is explicit, even when the next row is another city.
        old_anchors = {}
        for i, j in deletions:
            if _entity_id(before[i]) is not None:
                continue
            for index in range(i - 1, -1, -1):
                if _entity_id(before[index]) is not None:
                    old_anchors[index] = before[index]
                    break
        if old_anchors:
            patch["old_anchors"] = [[i, line] for i, line in sorted(old_anchors.items())]
    # Small/full-changing strings cost less and are clearer without a patch.
    return patch if len(_json(patch)) < len(_json(new)) else new


class DecisionDisplay:
    """Own one bounded display document; it never answers a game query."""
    def __init__(self, namespace):
        self.namespace = namespace
        self.serial = 0
        self.baseline = None
        self.reason = "baseline_unavailable"

    def invalidate(self, reason):
        self.baseline = None
        self.reason = reason

    def before_call(self, tool):
        if tool in REPLACEMENT_TOOLS:
            self.invalidate("world_replacement")

    def project(self, value, *, arguments, owner, since=None):
        current = deepcopy(value)
        if not _ready(value):
            self.invalidate("unconfirmed_observation")
            current["presentation"] = {"encoding": "decision-lines-v1", "mode": "full", "token": None,
                                       "base": None, "reset_reason": self.reason}
            return current
        old = self.baseline
        reason = None
        if since is None:
            reason = "baseline_not_requested"
        elif old is None:
            reason = self.reason
        elif since != old["token"]:
            reason = "baseline_mismatch"
        elif owner != old["owner"]:
            reason = "owner_changed"
        elif arguments != old["arguments"]:
            reason = "arguments_changed"
        else:
            before, after = old["value"]["identity"], value["identity"]
            if any(before[k] != after[k] for k in ("civilization", "seed", "player_id")):
                reason = "world_changed"
            elif after["turn"] not in (before["turn"], before["turn"] + 1):
                reason = "turn_gap"
        self.serial += 1
        digest = _digest(value)
        token = _digest([self.namespace, self.serial, digest])[:24]
        metadata = {"encoding": "decision-lines-v1", "mode": "full" if reason else "delta", "token": token,
                    "base": None if reason else since, "sha256": digest}
        if reason:
            metadata["reset_reason"] = reason
        else:
            for name, section in current["sections"].items():
                previous = old["value"]["sections"].get(name, {}).get("data")
                if isinstance(section["data"], str) and isinstance(previous, str):
                    section["data"] = _text_patch(previous, section["data"], name)
        current["presentation"] = metadata
        self.baseline = {"token": token, "value": deepcopy(value), "arguments": deepcopy(arguments), "owner": owner}
        return current


def restore_display(baseline, displayed):
    """Reconstruct the exact current payload; never interpolate missing facts.

Splice offsets/counts are zero-based old-text lines (line terminators retained),
and all offsets refer to the same base. Anchors are redundant current context.
Pass the previous *full reconstructed document plus its presentation metadata*
as baseline, not an undecoded chain of patches.
"""
    value = deepcopy(displayed)
    metadata = value.pop("presentation")
    if metadata.get("encoding") != "decision-lines-v1":
        raise ValueError("Unknown decision presentation encoding")
    if metadata["mode"] == "delta":
        if baseline is None or baseline.get("presentation", {}).get("token") != metadata["base"]:
            raise ValueError("Explicit full display baseline required")
        for name, section in value["sections"].items():
            patch = section.get("data")
            if not isinstance(patch, dict) or patch.get("encoding") != "line-splices-v1":
                continue
            old = baseline["sections"][name]["data"]
            if not isinstance(old, str) or _digest(old) != patch["base_sha256"]:
                raise ValueError("Decision text baseline digest mismatch")
            lines = old.splitlines(keepends=True)
            parts, position = [], 0
            for start, delete, replacement in patch["splices"]:
                if (type(start) is not int or type(delete) is not int or start < position
                        or delete < 0 or start + delete > len(lines) or not isinstance(replacement, str)):
                    raise ValueError("Invalid decision text splice")
                parts.extend(("".join(lines[position:start]), replacement))
                position = start + delete
            parts.append("".join(lines[position:]))
            section["data"] = "".join(parts)
    elif metadata["mode"] != "full":
        raise ValueError("Unknown decision presentation mode")
    if "sha256" in metadata and _digest(value) != metadata["sha256"]:
        raise ValueError("Reconstructed decision digest mismatch")
    return value


def project_result(display, result, *, arguments, owner, since):
    """Project only the known duplicated query envelope after raw persistence."""
    content, structured = result.get("content"), result.get("structuredContent")
    if not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
        display.invalidate("unsupported_response")
        return result
    try:
        payload = json.loads(content[0]["text"])
        if not isinstance(payload, dict):
            raise ValueError("Expected decision envelope")
        outcome = explicit_outcome(result)
        if (result.get("isError") is True or not isinstance(outcome, dict)
                or outcome["kind"] != "query" or outcome["data"] != payload
                or outcome.get("status") != payload.get("status")
                or (payload.get("status") == "ready" and outcome.get("code") is not None)
                or ("result" in structured and structured["result"] != content[0]["text"])):
            raise ValueError("Conflicting decision representations")
        projected = display.project(payload, arguments=arguments, owner=owner, since=since)
    except (ValueError, TypeError, KeyError):
        display.invalidate("unsupported_response")
        return result
    result = deepcopy(result)
    result["content"][0]["text"] = json.dumps(projected, ensure_ascii=False, allow_nan=False)
    result["structuredContent"].pop("result", None)
    result["structuredContent"]["outcome"]["data"] = projected
    return result

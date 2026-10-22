"""Identity expectations for explicit recovery, bound to exact file contents.

Only our native-completion-confirmed saves can create records. Explicitly
authorized originals are trusted by their archived SHA-256, never by name alone.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from civ_mcp.lua.models import GameOverview

CHECKPOINT_DIR = Path.home() / ".civ6-mcp" / "checkpoints"
BASELINE_SHA256 = "688b12eddbf1ec6bdb44da4f558d39191e96a6893a538f57ccc531beb5339ffd"
# Names select an approved original; exact file contents establish trust.
_BASELINES = {
    "benchmark_gold_v2": (
        "cb84122718eac4e4e21514de1e6cb9fa2f850d9036c2180656cf92b354f85dff",
        ("china", 1501391872, 1),
    ),
    "benchmark_turn_1": (BASELINE_SHA256, ("sumeria", 719665280, 1)),
    "benchmark_gold_tun_1": (
        "0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf",
        ("sumeria", -1896272000, 1),
    ),
}
# Short-lived evidence between native completion and GameState post-checks.
_completed: dict[str, str] = {}
# Revoke before touching disk: a failed metadata unlink must not leave an old
# checkpoint usable while the same native save may still be writing.
_revoked: set[str] = set()


def is_original_baseline_name(name: str) -> bool:
    """Protect approved original names; this alone never authorizes loading."""
    normalized = name.rstrip(" .").casefold().removesuffix(".civ6save").rstrip(" .")
    return normalized in _BASELINES


def _current_run() -> str | None:
    try:
        data = json.loads((CHECKPOINT_DIR / "current-run.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("baseline_sha256") in {baseline[0] for baseline in _BASELINES.values()} and isinstance(data.get("run_id"), str) and data["run_id"]:
            return data["run_id"]
    except (OSError, ValueError, TypeError):
        pass
    return None


def begin_baseline_run(name: str = "benchmark_turn_1") -> str | None:
    """Rotate ownership only after the loader verifies the original baseline.

    This small durable marker survives an MCP restart and checkpoint recovery;
    loading the baseline starts a new attempt even when civ and seed match.
    """
    baseline = _BASELINES.get(name.removesuffix(".Civ6Save"))
    if baseline is None:
        return "Error: Unregistered original cannot establish run ownership."
    try:
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        target = CHECKPOINT_DIR / "current-run.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"run_id": uuid.uuid4().hex, "baseline_sha256": baseline[0]}), encoding="utf-8")
        temporary.replace(target)
        _completed.clear()
        _revoked.clear()
    except OSError as exc:
        return f"ERR:OUTCOME_UNKNOWN|Baseline loaded but run ownership could not be recorded: {exc}"
    return None


def _file(name: str) -> Path:
    from civ_mcp.game_launcher import SINGLE_SAVE_DIR
    return Path(SINGLE_SAVE_DIR) / (name.removesuffix(".Civ6Save") + ".Civ6Save")


def _digest(name: str) -> str:
    path = _file(name)
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    fingerprint = lambda stat: (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    if fingerprint(before) != fingerprint(after) or len(data) != after.st_size or not data:
        raise ValueError("Checkpoint changed while reading or is empty")
    return hashlib.sha256(data).hexdigest()


def forget_completion(name: str) -> None:
    name = name.removesuffix(".Civ6Save")
    _revoked.add(name)
    _completed.pop(name, None)
    # A new attempted overwrite must not leave an older record eligible while
    # its file may still be writing, even if the first observation is unchanged.
    target = CHECKPOINT_DIR / (hashlib.sha256(name.encode()).hexdigest() + ".json")
    target.unlink(missing_ok=True)


def mark_complete(name: str) -> None:
    _completed[name.removesuffix(".Civ6Save")] = _digest(name)


def score_evidence(overview: GameOverview) -> dict:
    """Official native total and an optional complete positive-weight vector."""
    return {
        "official": overview.score,
        "categories": (
            None if overview.score_categories is None else {
                category.category_type: category.score
                for category in overview.score_categories
            }
        ),
    }


def _validated_score(value: object) -> dict | None:
    if (
        not isinstance(value, dict)
        or type(value.get("official")) is not int
        or value["official"] < 0
        or "categories" not in value
    ):
        return None
    categories = value["categories"]
    if categories is not None and (
        not isinstance(categories, dict) or not categories or any(
            not isinstance(key, str) or not key.isidentifier()
            or type(score) is not int or score < 0
            for key, score in categories.items()
        )
    ):
        return None
    return {"official": value["official"], "categories": categories}


def record_checkpoint(
    name: str, identity: tuple[str, int], turn: int, score: dict | None = None
) -> str | None:
    """Finish a checkpoint only after the caller verified identity and turn."""
    name = name.removesuffix(".Civ6Save")
    digest = _completed.pop(name, None)
    try:
        run_id = _current_run()
        if not run_id or not digest or digest != _digest(name) or turn <= 0 or identity == ("unknown", 0):
            return "ERR:OUTCOME_UNKNOWN|Checkpoint completion, identity or current-run ownership evidence was lost; recovery is unavailable."
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        record = {"name": name, "sha256": digest, "civilization": identity[0], "seed": identity[1], "turn": turn, "run_id": run_id}
        evidence = _validated_score(score)
        if evidence is not None:
            record["score"] = evidence
        target = CHECKPOINT_DIR / (hashlib.sha256(name.encode()).hexdigest() + ".json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
        _revoked.discard(name)
    except (OSError, ValueError) as exc:
        return f"ERR:OUTCOME_UNKNOWN|Checkpoint record could not be confirmed: {exc}"
    return None


def _verified_record(name: str) -> dict | None:
    name = name.removesuffix(".Civ6Save")
    if name in _revoked:
        return None
    try:
        digest = _digest(name)
        target = CHECKPOINT_DIR / (hashlib.sha256(name.encode()).hexdigest() + ".json")
        data = json.loads(target.read_text(encoding="utf-8"))
        run_id = _current_run()
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("civilization"), str)
            or not data["civilization"] or data["civilization"] == "unknown"
            or type(data.get("seed")) is not int or type(data.get("turn")) is not int
        ):
            return None
        if run_id and data.get("run_id") == run_id and data["name"] == name and data["sha256"] == digest and data["turn"] > 0:
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def expected_checkpoint(name: str) -> tuple[str, int, int] | None:
    """Return verified expected civ/seed/turn, or refuse unknown/stale files."""
    name = name.removesuffix(".Civ6Save")
    try:
        baseline = _BASELINES.get(name)
        if baseline is not None:
            return baseline[1] if _digest(name) == baseline[0] else None
        data = _verified_record(name)
        if data is not None:
            return (str(data["civilization"]), int(data["seed"]), int(data["turn"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def expected_score(name: str) -> dict | None:
    """Optional save-time evidence, trusted only with the same file/run record."""
    record = _verified_record(name)
    return None if record is None else _validated_score(record.get("score"))

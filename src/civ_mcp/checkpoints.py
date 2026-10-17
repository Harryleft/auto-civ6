"""Identity expectations for explicit recovery, bound to exact file contents.

Only our native-completion-confirmed saves can create records. The single
user-confirmed baseline is trusted by its archived SHA-256, never by its name.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

CHECKPOINT_DIR = Path.home() / ".civ6-mcp" / "checkpoints"
BASELINE_SHA256 = "688b12eddbf1ec6bdb44da4f558d39191e96a6893a538f57ccc531beb5339ffd"
# Short-lived evidence between native completion and GameState post-checks.
_completed: dict[str, str] = {}


def _current_run() -> str | None:
    try:
        data = json.loads((CHECKPOINT_DIR / "current-run.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("baseline_sha256") == BASELINE_SHA256 and isinstance(data.get("run_id"), str) and data["run_id"]:
            return data["run_id"]
    except (OSError, ValueError, TypeError):
        pass
    return None


def begin_baseline_run() -> str | None:
    """Rotate ownership only after the loader verifies the original baseline.

    This small durable marker survives an MCP restart and checkpoint recovery;
    loading the baseline starts a new attempt even when civ and seed match.
    """
    try:
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        target = CHECKPOINT_DIR / "current-run.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"run_id": uuid.uuid4().hex, "baseline_sha256": BASELINE_SHA256}), encoding="utf-8")
        temporary.replace(target)
        _completed.clear()
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
    _completed.pop(name, None)
    # A new attempted overwrite must not leave an older record eligible while
    # its file may still be writing, even if the first observation is unchanged.
    target = CHECKPOINT_DIR / (hashlib.sha256(name.encode()).hexdigest() + ".json")
    target.unlink(missing_ok=True)


def mark_complete(name: str) -> None:
    _completed[name.removesuffix(".Civ6Save")] = _digest(name)


def record_checkpoint(name: str, identity: tuple[str, int], turn: int) -> str | None:
    """Finish a checkpoint only after the caller verified identity and turn."""
    name = name.removesuffix(".Civ6Save")
    digest = _completed.pop(name, None)
    try:
        run_id = _current_run()
        if not run_id or not digest or digest != _digest(name) or turn <= 0 or identity == ("unknown", 0):
            return "ERR:OUTCOME_UNKNOWN|Checkpoint completion, identity or current-run ownership evidence was lost; recovery is unavailable."
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        record = {"name": name, "sha256": digest, "civilization": identity[0], "seed": identity[1], "turn": turn, "run_id": run_id}
        target = CHECKPOINT_DIR / (hashlib.sha256(name.encode()).hexdigest() + ".json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
    except (OSError, ValueError) as exc:
        return f"ERR:OUTCOME_UNKNOWN|Checkpoint record could not be confirmed: {exc}"
    return None


def expected_checkpoint(name: str) -> tuple[str, int, int] | None:
    """Return verified expected civ/seed/turn, or refuse unknown/stale files."""
    name = name.removesuffix(".Civ6Save")
    try:
        digest = _digest(name)
        if name == "benchmark_turn_1" and digest == BASELINE_SHA256:
            return ("sumeria", 719665280, 1)
        target = CHECKPOINT_DIR / (hashlib.sha256(name.encode()).hexdigest() + ".json")
        data = json.loads(target.read_text(encoding="utf-8"))
        run_id = _current_run()
        if run_id and data.get("run_id") == run_id and data["name"] == name and data["sha256"] == digest and int(data["turn"]) > 0:
            return (str(data["civilization"]), int(data["seed"]), int(data["turn"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None

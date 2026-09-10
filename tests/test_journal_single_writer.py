"""Only one process may write a belief journal.

The journal is append-only with a process-local sequence counter, and the
loader rewrites the whole file when it meets a torn line. Two writers therefore
interleave sequence numbers and can lose events outright. FireTuner already
allows a single client; this makes the same rule structural for the journal.
"""

from __future__ import annotations

import subprocess
import sys

from civ6_belief_engine.belief_engine import BeliefEngine

_CHILD_SCRIPT = """
import pathlib
import sys

from civ6_belief_engine.belief_engine import BeliefEngine, BeliefEngineError

engine = BeliefEngine(run_id="child", directory=pathlib.Path(sys.argv[1]))
try:
    engine.bind_game("CIVILIZATION_TEST", 42)
except BeliefEngineError as exc:
    print("LOCKED", exc)
else:
    print("ACQUIRED")
"""


def _run_child(directory) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(directory)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_a_second_process_cannot_bind_the_same_journal(tmp_path):
    engine = BeliefEngine(run_id="parent", directory=tmp_path)
    engine.bind_game("CIVILIZATION_TEST", 42)

    output = _run_child(tmp_path)
    assert "LOCKED" in output, output
    assert "another process is already writing" in output


def test_a_second_process_can_bind_an_unclaimed_journal(tmp_path):
    """Control: the lock must not block a journal nobody holds."""

    output = _run_child(tmp_path)
    assert "ACQUIRED" in output, output

    # The child exited, so its lock is gone; this process can take over.
    engine = BeliefEngine(run_id="parent", directory=tmp_path)
    engine.bind_game("CIVILIZATION_TEST", 42)
    assert engine.path is not None
    assert engine.path.with_name(engine.path.name + ".lock").exists()


def test_rebinding_the_same_journal_in_process_is_allowed(tmp_path):
    """A restart or a reload re-binds the same directory in one process."""

    first = BeliefEngine(run_id="run-a", directory=tmp_path)
    first.bind_game("CIVILIZATION_TEST", 42)
    second = BeliefEngine(run_id="run-b", directory=tmp_path)
    second.bind_game("CIVILIZATION_TEST", 42)

    assert second.epoch >= 1


def test_distinct_journals_do_not_conflict(tmp_path):
    first = BeliefEngine(run_id="run-a", directory=tmp_path)
    first.bind_game("CIVILIZATION_TEST", 42)
    second = BeliefEngine(run_id="run-b", directory=tmp_path)
    second.bind_game("CIVILIZATION_ROME", 7)

    assert first.path != second.path


def test_lock_file_sits_next_to_the_journal(tmp_path):
    engine = BeliefEngine(run_id="parent", directory=tmp_path)
    engine.bind_game("CIVILIZATION_TEST", 42)

    assert engine.path is not None
    assert engine.path.with_name(engine.path.name + ".lock").exists()

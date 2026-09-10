"""No test may append to the user's real evidence journal.

``BeliefEngine(run_id=...)`` without ``directory=`` used to fall back to
``~/.civ6-mcp/beliefs``. Two tests in ``test_gate_fixes.py`` did that and wrote
their fixture decisions into ``belief_CIVILIZATION_TEST_42.jsonl`` — fabricated
records mixed into an append-only audit log, and a file that grows with every
run so the journal had to be replayed forever.
"""

from __future__ import annotations

import pathlib

from civ6_belief_engine import belief_engine
from civ6_belief_engine.belief_engine import BeliefEngine


def test_default_directory_is_redirected_into_the_test_tmpdir(tmp_path):
    assert belief_engine.default_beliefs_directory() == tmp_path / "beliefs"


def test_engine_built_without_a_directory_writes_under_the_tmpdir(tmp_path):
    engine = BeliefEngine(run_id="isolation")
    engine.bind_game("CIVILIZATION_TEST", 42)
    engine.create(
        "belief",
        {
            "statement": "isolation probe",
            "category": "military",
            "probability": 0.5,
            "confidence": 0.5,
            "unknown_basis": True,
        },
        turn=1,
    )

    assert engine.path is not None
    assert engine.directory == tmp_path / "beliefs"
    assert tmp_path in engine.path.parents
    assert engine.path.exists()


def test_default_directory_is_not_the_user_home():
    real_home = pathlib.Path.home() / ".civ6-mcp" / "beliefs"
    # The autouse fixture redirects the default in every test, so any engine
    # that forgot directory= now resolves somewhere else entirely.
    assert belief_engine.default_beliefs_directory() != real_home

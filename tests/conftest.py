"""Opt-in fixtures for offline tests; no live game or runtime defaults are changed."""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture(scope='session')
def committed_knowledge_root(tmp_path_factory):
    """Build once for read-only CLI checks of the committed snapshot.

    Tests that change sources, corrupt outputs or move the build must keep
    their own directories rather than consume this shared root.
    """
    root = Path(__file__).resolve().parents[1]
    output = tmp_path_factory.mktemp('committed-knowledge') / 'knowledge'
    result = subprocess.run([
        sys.executable, str(root / 'civ6_knowledge/build.py'),
        '--source', str(root / 'civ6_knowledge/sources/snapshot.json'),
        '--output', str(output),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return output


@pytest.fixture
def fast_action_confirmation(monkeypatch):
    """Scripted responses need loop yields, not a real game's waiting budget.

    Deadline-specific tests can override these values locally. Keep enough
    budget for multiple observations without relying on a ten-millisecond run.
    """
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', 0.1)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', 0.001)

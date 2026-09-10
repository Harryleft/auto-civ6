"""共享测试夹具：事件溯源引擎实例。

各测试文件不再各自定义 ``engine`` fixture——统一由这里提供，保证
``run_id``/``bind_game`` 参数一致，避免夹具语义漂移。
"""

from __future__ import annotations

import pytest

from civ6_belief_engine import belief_engine
from civ6_belief_engine.belief_engine import BeliefEngine


@pytest.fixture(autouse=True)
def isolated_belief_journal(tmp_path, monkeypatch):
    """Keep every test out of the user's real ``~/.civ6-mcp/beliefs`` journal.

    ``BeliefEngine(run_id=...)`` without ``directory=`` defaults to the real
    home directory. Two tests in ``test_gate_fixes.py`` did exactly that and
    appended their fixture decisions to
    ``belief_CIVILIZATION_TEST_42.jsonl``, mixing fabricated audit records into
    the append-only evidence log. Redirect the default itself so a future
    omission cannot reach it either.
    """

    monkeypatch.setattr(
        belief_engine,
        "default_beliefs_directory",
        lambda: tmp_path / "beliefs",
    )


@pytest.fixture
def engine(tmp_path):
    instance = BeliefEngine(run_id="test-run", directory=tmp_path)
    instance.bind_game("CIVILIZATION_TEST", 42)
    return instance

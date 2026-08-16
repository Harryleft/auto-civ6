"""共享测试夹具：事件溯源引擎实例。

各测试文件不再各自定义 ``engine`` fixture——统一由这里提供，保证
``run_id``/``bind_game`` 参数一致，避免夹具语义漂移。
"""

from __future__ import annotations

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine


@pytest.fixture
def engine(tmp_path):
    instance = BeliefEngine(run_id="test-run", directory=tmp_path)
    instance.bind_game("CIVILIZATION_TEST", 42)
    return instance

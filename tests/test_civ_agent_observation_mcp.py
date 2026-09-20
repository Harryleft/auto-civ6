"""M06 改造验证：Observation 必须能从 **MCP 的 JSON** 组合出来。

这是 O2 修订后的关键不变量：事实走 ``get_runtime_context`` 的 JSON，而不是内存
里的 ``CivReadResult``。测试跑真实的内存 MCP 协议，因此能捕捉字段名或嵌套形状
漂移——那正是内调路径掩盖不了的失效。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from civ_agent.mcp_client import RuntimeClient
from civ_agent.observation import (
    UNKNOWN,
    Observation,
    build_observation,
    context_snapshot,
    coverage_map,
    diff,
)
from mcp_fixtures import build_fake_server, fake_runtime_context


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _observation_from_fake_server() -> Observation:
    async def scenario() -> Observation:
        async with create_connected_server_and_client_session(build_fake_server()) as session:
            context = await RuntimeClient(session).read_context()
            return build_observation(context)

    return _run(scenario())


# ---------------------------------------------------------------------------
# 端到端：MCP JSON → Observation
# ---------------------------------------------------------------------------


def test_observation_is_built_from_the_mcp_json_payload() -> None:
    observation = _observation_from_fake_server()

    assert observation.turn == 7
    assert observation.our_state.civilization == "巴比伦"
    assert observation.our_state.leader == "汉谟拉比"
    assert observation.our_state.cities == 1
    assert observation.our_state.science == 4.5


def test_mcp_path_uses_stable_tech_and_civic_ids() -> None:
    observation = _observation_from_fake_server()

    assert observation.our_state.current_research == "TECH_POTTERY"
    assert observation.our_state.current_civic == "CIVIC_CODE_OF_LAWS"


def test_mcp_path_sums_city_population_from_json() -> None:
    observation = _observation_from_fake_server()

    assert observation.our_state.total_population == 2


def test_mcp_path_reads_our_military_strength_from_victory_progress() -> None:
    observation = _observation_from_fake_server()

    assert observation.our_state.military_strength == 210


def test_mcp_path_omits_unmet_civilizations() -> None:
    observation = _observation_from_fake_server()

    assert [opponent.civilization for opponent in observation.opponents] == ["埃及"]


def test_mcp_path_keeps_unknown_and_coverage_distinguishable() -> None:
    observation = _observation_from_fake_server()

    assert observation.unknown == ("units: TimeoutError",)
    assert observation.coverage["diplomacy"].endswith("UNMET_CIVILIZATIONS:UNOBSERVED")
    assert "coverage" in observation.as_dict()


def test_mcp_observation_round_trips_through_json() -> None:
    """Observation 必须能序列化：它会进 Game Memory 与节点状态。"""

    observation = _observation_from_fake_server()

    restored = json.loads(json.dumps(observation.as_dict(), ensure_ascii=False))
    assert restored["turn"] == 7
    assert restored["our_state"]["civilization"] == "巴比伦"
    assert restored["opponents"][0]["civilization"] == "埃及"


def test_mcp_observation_survives_diff_against_a_second_read() -> None:
    """两次读取之间没有变化时，diff 不应报告任何"重要变化"。"""

    previous = _observation_from_fake_server()
    current = _observation_from_fake_server()

    assert diff(previous, current).important_changes == ()


# ---------------------------------------------------------------------------
# context_snapshot 归一化
# ---------------------------------------------------------------------------


def test_context_snapshot_accepts_mcp_json() -> None:
    snapshot = context_snapshot(fake_runtime_context())

    assert snapshot["unknown"] == ("units: TimeoutError",)
    assert "overview" in snapshot["facts"]


def test_context_snapshot_accepts_runtime_context_objects() -> None:
    from types import SimpleNamespace

    class Context:
        facts = {"overview": SimpleNamespace(value={"turn": 3, "player_id": 0})}
        unknown = ("cities: TimeoutError",)

    snapshot = context_snapshot(Context())

    assert snapshot["unknown"] == ("cities: TimeoutError",)
    assert snapshot["facts"]["overview"].value["turn"] == 3


def test_context_snapshot_normalizes_missing_unknown_to_empty() -> None:
    snapshot = context_snapshot({"facts": {"overview": {"value": {"turn": 1, "player_id": 0}}}})

    assert snapshot["unknown"] == ()


def test_context_snapshot_rejects_payload_without_facts() -> None:
    with pytest.raises(ValueError, match="facts"):
        context_snapshot({"unknown": []})


def test_context_snapshot_rejects_non_sequence_unknown() -> None:
    with pytest.raises(ValueError, match="unknown"):
        context_snapshot({"facts": {}, "unknown": "not-a-list"})


def test_observation_raises_without_overview_even_on_mcp_shape() -> None:
    context = fake_runtime_context()
    del context["facts"]["overview"]

    with pytest.raises(ValueError, match="overview"):
        build_observation(context)


def test_build_observation_rejects_overview_without_integer_turn() -> None:
    context = fake_runtime_context()
    context["facts"]["overview"]["value"]["turn"] = "seven"

    with pytest.raises(ValueError, match="turn"):
        build_observation(context)


# ---------------------------------------------------------------------------
# coverage_map
# ---------------------------------------------------------------------------


def test_coverage_map_reads_dict_and_object_holders() -> None:
    from types import SimpleNamespace

    facts = {
        "as_dict": {"value": 1, "coverage": "A:COMPLETE"},
        "as_object": SimpleNamespace(value=2, coverage="B:PARTIAL"),
        "bare": {"value": 3},
    }

    assert coverage_map(facts) == {"as_dict": "A:COMPLETE", "as_object": "B:PARTIAL"}


def test_unknown_is_not_confused_with_a_real_zero() -> None:
    """读不到的军力必须写 unknown，不能写成 0。"""

    context = fake_runtime_context()
    context["facts"]["victory_progress"]["value"] = []

    observation = build_observation(context)

    assert observation.our_state.military_strength == UNKNOWN

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from civ_mcp.server import pipeline as server_module
from civ_mcp.result_filter import ResultFilterConfig, filter_tool_result
from civ_mcp.server.pipeline import _belief_tool, _filter_downstream_result


def _config(*, max_chars: int = 2_000, history_items: int = 2):
    return ResultFilterConfig(
        enabled=True,
        max_chars=max_chars,
        history_items=history_items,
    )


def test_small_results_are_returned_byte_for_byte_unchanged():
    raw = "Turn 80 | England\nCRITICAL: keep this exact text"

    assert filter_tool_result("get_game_overview", raw, config=_config()) == raw


def test_filter_can_be_disabled_without_changing_results():
    raw = "x" * 5_000

    assert filter_tool_result(
        "get_units",
        raw,
        config=ResultFilterConfig(enabled=False, max_chars=2_000, history_items=2),
    ) == raw


def test_governance_filter_keeps_decision_metrics_and_limits_history():
    noisy_metrics = {f"city.{index}.production": index for index in range(300)}
    payload = {
        "turn": 80,
        "belief_brief": {
            "current_metrics": {
                "player.gold": 200,
                "barbarian.known_camps": 1,
                "victory.rival_science.science_vp": 12,
                "city.1.production": 8,
                "unit.7.health": 90,
                **noisy_metrics,
            },
            "review": {
                "metrics": {
                    "turn": 80,
                    "combat.attacker_hp": 60,
                    "city.1.food": 10,
                }
            },
            "decision_gate": {"default_route": "fast"},
        },
        "governance": {
            "council_decisions": [{"id": f"decision-{i}"} for i in range(5)],
            "released_budget_locks": [{"id": f"lock-{i}"} for i in range(4)],
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2)

    filtered = filter_tool_result(
        "get_governance_brief",
        raw,
        config=_config(max_chars=10_000, history_items=2),
    )
    parsed = json.loads(filtered)

    assert parsed["belief_brief"]["current_metrics"] == {
        "player.gold": 200,
        "barbarian.known_camps": 1,
        "victory.rival_science.science_vp": 12,
    }
    assert parsed["belief_brief"]["review"]["metrics"] == {
        "turn": 80,
        "combat.attacker_hp": 60,
    }
    assert [item["id"] for item in parsed["governance"]["council_decisions"]] == [
        "decision-0",
        "decision-1",
    ]
    metadata = parsed["_local_filter"]
    assert len(metadata["original_sha256"]) == 16
    assert "original_lines" not in metadata
    assert metadata["raw_owner"] == "local_telemetry"
    assert metadata["omitted_counts"] == {
        "belief_brief.current_metrics": 301,
        "belief_brief.review.metrics": 1,
        "governance.council_decisions": 3,
        "governance.released_budget_locks": 2,
    }


def test_unknown_and_malformed_results_are_never_truncated():
    action_result = "HANG: " + ("x" * 6_000) + " BELIEF_GATE_REQUIRED"
    malformed = "{not-json " + ("x" * 6_000)

    assert filter_tool_result("move_unit", action_result, config=_config()) == action_result
    assert (
        filter_tool_result("get_belief_state", malformed, config=_config())
        == malformed
    )


def test_belief_state_filters_only_broad_queries_and_is_idempotent():
    payload = {
        "game_id": "game-1",
        "turn": 9,
        "current_metrics": {
            "player.gold": 100,
            **{f"unit.{index}.health": 100 for index in range(300)},
        },
        "entities": {
            "belief": [{"id": f"belief-{index}"} for index in range(5)],
            "decision": [{"id": f"decision-{index}"} for index in range(4)],
        },
    }
    raw = json.dumps(payload, indent=2)

    filtered = filter_tool_result(
        "get_belief_state", raw, params={"entity_type": ""}, config=_config()
    )
    parsed = json.loads(filtered)

    assert parsed["current_metrics"] == {"player.gold": 100}
    assert [item["id"] for item in parsed["entities"]["belief"]] == [
        "belief-0",
        "belief-1",
    ]
    assert filter_tool_result(
        "get_belief_state", filtered, params={"entity_type": ""}, config=_config()
    ) == filtered
    assert filter_tool_result(
        "get_belief_state",
        raw,
        params={"entity_type": "belief"},
        config=_config(),
    ) == raw


def test_belief_trace_keeps_recent_headers_but_targeted_trace_is_raw():
    events = [
        {
            "event_id": f"event-{index}",
            "sequence": index,
            "turn": 10,
            "event_type": "entity.updated",
            "entity_type": "decision",
            "entity_id": f"decision-{index}",
            "entity": {"id": f"decision-{index}", "details": "x" * 2_000},
            "changes": {"status": {"from": "pending", "to": "completed"}},
        }
        for index in range(5)
    ]
    raw = json.dumps({"game_id": "game-1", "turn": 10, "events": events}, indent=2)

    filtered = filter_tool_result(
        "get_belief_trace", raw, params={}, config=_config(history_items=2)
    )
    parsed = json.loads(filtered)

    assert [event["event_id"] for event in parsed["events"]] == ["event-3", "event-4"]
    assert all("entity" not in event for event in parsed["events"])
    # changes keep key names only — world_entity link diffs are KB-scale
    assert all(event["changes"] == ["status"] for event in parsed["events"])
    assert filter_tool_result(
        "get_belief_trace",
        raw,
        params={"entity_id": "decision-4"},
        config=_config(),
    ) == raw


def test_belief_tool_logs_raw_result_before_filtering(monkeypatch):
    captured: dict[str, str] = {}
    payload = {
        "game_id": "game-1",
        "turn": 11,
        "current_metrics": {
            "player.gold": 90,
            **{f"city.{index}.food": index for index in range(150)},
        },
        "entities": {"belief": [{"id": f"belief-{index}"} for index in range(6)]},
    }

    class Logger:
        async def log_tool_call(self, _tool, _params, result, _duration_ms):
            captured["telemetry"] = result

    async def belief_context(_ctx):
        return object(), 11

    async def flush(_ctx):
        return None

    monkeypatch.setenv("CIV_MCP_RESULT_MAX_CHARS", "2000")
    monkeypatch.setattr(
        server_module,
        "_get_belief_mode",
        lambda _ctx: SimpleNamespace(records_events=True),
    )
    monkeypatch.setattr(server_module, "_belief_context", belief_context)
    monkeypatch.setattr(server_module, "_flush_belief_events", flush)
    monkeypatch.setattr(server_module, "_get_logger", lambda _ctx: Logger())

    returned = asyncio.run(
        _belief_tool(
            object(),
            "get_belief_state",
            {"entity_type": "", "status": "active", "last_n": 50},
            lambda _engine, _turn: payload,
        )
    )

    assert "_local_filter" not in json.loads(captured["telemetry"])
    assert json.loads(returned)["_local_filter"]["applied"] is True
    assert len(returned) < len(captured["telemetry"])


def test_filter_boundary_fails_open(monkeypatch):
    monkeypatch.setattr(
        server_module,
        "filter_tool_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken")),
    )

    result = _filter_downstream_result("get_belief_state", {}, "RAW")
    assert result.startswith("【中文运行信息】")
    assert result.endswith("\n\nRAW")

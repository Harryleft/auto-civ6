from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from civ_mcp.presentation import action_receipt_status, localize_model_result
from civ_mcp.server.pipeline import _filter_downstream_result, _logged


@pytest.mark.parametrize(
    ("raw", "expected_machine", "expected_label"),
    [
        ("FOUNDED|12,8 (verified)", "succeeded", "已验证成功"),
        (
            "FOUND_REQUESTED|12,8 — city not yet present, verify with get_cities()",
            "submitted",
            "已提交待验证",
        ),
        ("Error: OUTCOME_UNKNOWN|state did not change", "unknown", "结果未知"),
        ("BELIEF_GATE_REQUIRED: route the action first", "blocked", "受阻"),
        ("Error: CANNOT_FOUND|no valid tile", "failed", "失败"),
    ],
)
def test_action_receipt_status_distinguishes_machine_and_chinese_semantics(
    raw: str, expected_machine: str, expected_label: str
) -> None:
    assert action_receipt_status("unit_action", raw) == (
        expected_machine,
        expected_label,
    )
    rendered = localize_model_result("unit_action", raw)
    assert f"状态：{expected_label}" in rendered
    # The raw status marker remains available below the Chinese summary for
    # exact parser/audit correlation.
    assert raw.replace("Error:", "错误：") in rendered


def test_non_mutating_query_keeps_the_normal_success_semantics() -> None:
    assert action_receipt_status("get_units", "unit list") is None
    rendered = localize_model_result("get_units", "unit list")
    assert "状态：成功" in rendered


def test_end_turn_advance_is_a_verified_receipt() -> None:
    rendered = localize_model_result("end_turn", "Turn 8 -> 9 | Score: 42")
    assert "状态：已验证成功" in rendered


def test_logged_submitted_action_stays_open_in_audit_and_is_pending_to_model(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class Logger:
        _turn = 9

        async def log_tool_call(self, _tool, _params, result, _duration_ms):
            captured["telemetry"] = result

        async def log_error(self, *_args):
            captured["error"] = True

    async def preflight(*_args, **_kwargs):
        return {"authorized": True, "decision_id": None, "route": "routine"}

    async def record(_ctx, _tool, _params, _result, *_args, **kwargs):
        captured["success"] = kwargs["success"]
        captured["execution_status"] = kwargs["execution_status"]

    async def operation():
        return "FOUND_REQUESTED|12,8 — city not yet present, verify with get_cities()"

    monkeypatch.setattr("civ_mcp.server.pipeline._belief_action_preflight", preflight)
    monkeypatch.setattr("civ_mcp.server.pipeline._record_belief_tool_result", record)
    monkeypatch.setattr("civ_mcp.server.pipeline.heartbeat.write", lambda *_args, **_kwargs: None)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(logger=Logger())
        )
    )

    rendered = asyncio.run(
        _logged(
            ctx,
            "unit_action",
            {"unit_id": 1, "action": "found_city"},
            operation,
        )
    )

    assert "状态：已提交待验证" in rendered
    assert captured["success"] is False
    # The belief engine keeps its existing fail-closed machine vocabulary;
    # a submitted request is not allowed to close the routed action.
    assert captured["execution_status"] == "unknown"
    assert "FOUND_REQUESTED|12,8" in str(captured["telemetry"])


def test_logged_action_early_error_is_failed_and_not_unknown(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Logger:
        _turn = 9

        async def log_error(self, _tool, result):
            captured["telemetry"] = result

    async def preflight(*_args, **_kwargs):
        return {"authorized": True, "decision_id": None, "route": "routine"}

    async def record(_ctx, _tool, _params, _result, *_args, **kwargs):
        captured["success"] = kwargs["success"]
        captured["execution_status"] = kwargs["execution_status"]

    async def operation():
        raise ValueError("target coordinates are invalid")

    monkeypatch.setattr("civ_mcp.server.pipeline._belief_action_preflight", preflight)
    monkeypatch.setattr("civ_mcp.server.pipeline._record_belief_tool_result", record)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(logger=Logger())
        )
    )

    rendered = asyncio.run(
        _logged(
            ctx,
            "unit_action",
            {"unit_id": 1, "action": "move", "target_x": 99, "target_y": 99},
            operation,
        )
    )

    assert "状态：失败" in rendered
    assert captured["success"] is False
    assert captured["execution_status"] == "failed"
    assert "Error: target coordinates are invalid" in str(captured["telemetry"])


def test_logged_can_return_raw_result_for_wrappers_with_machine_control_flow(
    monkeypatch,
) -> None:
    class Logger:
        _turn = 9

        async def log_tool_call(self, *_args):
            pass

        async def log_error(self, *_args):
            pass

    async def preflight(*_args, **_kwargs):
        return {"authorized": True, "decision_id": None, "route": "routine"}

    async def record(*_args, **_kwargs):
        pass

    async def operation():
        return "HANG:9:0_MCP_0009|Turn requested"

    monkeypatch.setattr("civ_mcp.server.pipeline._belief_action_preflight", preflight)
    monkeypatch.setattr("civ_mcp.server.pipeline._record_belief_tool_result", record)
    monkeypatch.setattr("civ_mcp.server.pipeline.heartbeat.write", lambda *_args, **_kwargs: None)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(logger=Logger())
        )
    )

    raw = asyncio.run(
        _logged(ctx, "end_turn", {}, operation, localize=False)
    )

    assert raw.startswith("HANG:9:0_MCP_0009|")


def test_textual_runtime_information_is_localized_to_chinese() -> None:
    result = localize_model_result(
        "end_turn",
        "Turn 8 -> 9 | Score: 42\n=== RUNTIME POLICY ===\nError: none",
    )

    assert result.startswith("【中文运行信息】")
    assert "工具：结束回合（end_turn）" in result
    assert "状态：失败" in result
    assert "回合 8 → 9" in result
    assert "得分： 42" in result
    assert "=== 运行策略 ===" in result
    assert "错误： none" in result
    assert "Turn " not in result
    assert "Error:" not in result


def test_json_results_keep_machine_contract_and_add_chinese_semantics() -> None:
    result = localize_model_result(
        "get_governance_brief",
        json.dumps({"turn": 8, "decision_gate": {"default_route": "fast"}}),
    )

    payload = json.loads(result)
    assert payload["turn"] == 8
    assert payload["decision_gate"]["default_route"] == "fast"
    assert payload["中文说明"] == {
        "工具": "读取治理简报",
        "状态": "成功",
        "说明": "以下原有字段为机器契约和游戏证据，字段名、ID、枚举及原始值保持不变。",
    }


def test_pipeline_localizes_even_when_result_filter_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "civ_mcp.server.pipeline.filter_tool_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken")),
    )

    result = _filter_downstream_result("end_turn", {}, "Error: unavailable")
    assert result.startswith("【中文运行信息】")
    assert "状态：失败" in result
    assert "错误： unavailable" in result

from __future__ import annotations

import json

from civ_mcp.presentation import localize_model_result
from civ_mcp.server.pipeline import _filter_downstream_result


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

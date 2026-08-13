"""The human CLI precheck must enter the same governance loop as MCP agents."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_assist_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "civ6_assist.py"
    spec = importlib.util.spec_from_file_location("civ6_assist_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_precheck_starts_with_typed_governance_brief(monkeypatch, capsys):
    assist = _load_assist_module()
    calls: list[str] = []
    governance = json.dumps(
        {
            "turn": 42,
            "snapshot": {
                "snapshot_id": "snapshot:42",
                "ruleset": "RULESET_STANDARD",
            },
            "capabilities": {
                "ruleset": "RULESET_STANDARD",
                "governors": False,
                "world_congress": False,
            },
            "belief_brief": {
                "decision_gate": {"default_route": "fast"},
                "beliefs": [],
            },
            "governance": {},
        }
    )

    def fake_call(name: str, *_args, **_kwargs):
        calls.append(name)
        return governance if name == "get_governance_brief" else ""

    monkeypatch.setattr(assist, "call", fake_call)
    assist.cmd_precheck()

    output = capsys.readouterr().out
    assert calls[0] == "get_governance_brief"
    assert "snapshot=snapshot:42" in output
    assert "规则集禁用: governors, world_congress" in output
    assert "get_turn_brief" not in calls

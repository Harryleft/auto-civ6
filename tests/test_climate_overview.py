"""Climate overview: narrate, registration, and Standard ruleset rejection."""

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp import narrate
from civ_mcp.lua.climate import parse_climate_response
from civ_mcp.server.assembly import mcp


def _fixture() -> "object":
    return parse_climate_response(
        [
            "CLIMATE|4|Phase IV|31.0|3.0|28.0|28.0|5|-1|12|4|1.4|410.0|60.0|55.0|0.0|Heavy",
            "CLIMATE_RISK|22.0|10.0|15.0|6.0|30.0|12.0|5.0|18|6|4|2|3.0",
            "CLIMATE_CO2|0|You|60.0",
            "CLIMATE_CO2|1|France|240.0",
            "CLIMATE_CUR|85|RANDOM_EVENT_RIVER_FLOOD|FLOODPLAIN|River Flood|0|1|34|21|1|3|1|2",
            "CLIMATE_CITY|0|4|Ravenna",
            "CLIMATE_EV|84|RANDOM_EVENT_BLIZZARD|STORM|Blizzard|0|1|10|12|0|4|0|0",
            "CLIMATE_EV|60|RANDOM_EVENT_SEA_LEVEL|SEA_LEVEL|Sea Rise|1|1|-1|-1|0|0|0|0",
        ]
    )


def test_narrate_climate_phase_warning_and_share() -> None:
    text = narrate.narrate_climate_overview(_fixture())

    assert "SEA LEVEL PHASE IV" in text  # phase >= 4 告警必须前置
    assert "move coastal assets NOW" in text
    assert "(15% of world)" in text  # 60.0 / 410.0 的 Python 侧 share 计算
    assert "Top emitter: France 240.0" in text
    assert "storms 22% (+10)" in text
    assert "This turn: River Flood at (34,21) [FLOODPLAIN]" in text
    assert "3 tiles damaged, 1 units lost, 2 pop lost" in text
    assert "Affected cities: Ravenna" in text
    assert "T84 Blizzard" in text


def test_narrate_climate_phase0_early_game() -> None:
    overview = parse_climate_response(
        [
            "CLIMATE|0|Phase 0|0.0|0.0|0.0|-1.0|-1|-1|0|0|0.0|0.0|0.0|0.0|0.0|",
            "CLIMATE_RISK|1.0|0.0|1.0|0.0|1.0|1.0|0.0|10|2|1|0|0.0",
        ]
    )

    text = narrate.narrate_climate_overview(overview)

    assert "Sea level phase 0 — no rise yet" in text
    assert "No weather events in recent history." in text


def test_climate_tool_registered_read_only() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool = next(t for t in tools if t.name == "get_climate_overview")
    annotations = tool.model_dump()["annotations"]
    assert annotations is not None
    assert annotations.get("readOnlyHint") is True


def test_standard_ruleset_rejected_at_game_state_level() -> None:
    from civ_mcp.connection import LuaError
    from civ_mcp.game_state import GameState
    from civ_mcp.lua._helpers import SENTINEL

    gs = GameState.__new__(GameState)

    class _Conn:
        async def execute_write(self, _lua, **_kwargs):
            return ["ERR:NO_CLIMATE_IN_RULESET", SENTINEL]

    gs.conn = _Conn()

    with pytest.raises(LuaError, match="ERR:NO_CLIMATE_IN_RULESET"):
        asyncio.run(gs.get_climate_overview())


def test_standard_ruleset_error_wrapped_by_pipeline_logged(monkeypatch) -> None:
    from civ_mcp.connection import LuaError
    from civ_mcp.server import pipeline as pipeline_module

    class Logger:
        _turn = 9

        async def log_tool_call(self, *_args, **_kwargs):
            pass

        async def log_error(self, *_args, **_kwargs):
            pass

    async def preflight(*_args, **_kwargs):
        return {"authorized": True, "decision_id": None, "route": "routine"}

    async def record(*_args, **_kwargs):
        pass

    monkeypatch.setattr(pipeline_module, "_belief_action_preflight", preflight)
    monkeypatch.setattr(pipeline_module, "_record_belief_tool_result", record)
    monkeypatch.setattr(
        pipeline_module.heartbeat, "write", lambda *_args, **_kwargs: None
    )
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(logger=Logger())
        )
    )

    async def operation():
        raise LuaError("ERR:NO_CLIMATE_IN_RULESET")

    result = asyncio.run(
        pipeline_module._logged(ctx, "get_climate_overview", {}, operation)
    )

    # 规格期望 "Error: ERR:..."; 中文化管道层(presentation._TEXT_REPLACEMENTS)
    # 现将前缀替换为 "错误：", 语义不变: 错误码原样透传给调用方。
    assert result.endswith("错误： ERR:NO_CLIMATE_IN_RULESET")
    assert "失败" in result

"""The live-game driver must use MCP once and stop on uncertainty; no live I/O."""

import asyncio
from contextlib import asynccontextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = spec_from_file_location("integration_driver", Path(__file__).parents[1] / "scripts/integration_turns.py")
driver = module_from_spec(spec)
spec.loader.exec_module(driver)


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def call_tool(self, tool, arguments):
        self.calls.append((tool, arguments))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=response)], isError=False,
        )


def test_existing_game_advances_via_mcp_only():
    session = Session(["Turn 1 | Sumeria", "Turn 1 -> 2\nChanges", "Turn 2 -> 3\nChanges"])
    assert asyncio.run(driver.run(session, None, 2)) == 0
    assert session.calls == [("get_game_overview", {}), ("end_turn", {}), ("end_turn", {})]


@pytest.mark.parametrize("result", [
    "HANG: turn 1", "ERR:OUTCOME_UNKNOWN|already sent", "Turn 1 -> 1",
    "Turn 2 -> 3", "OK:TURN_ENDED", "Cannot confirm end-turn blockers: no valid status",
    TimeoutError("lost response"),
])
def test_uncertain_result_never_replayed_or_recovered(result):
    session = Session(["Turn 1 | Sumeria", result])
    assert asyncio.run(driver.run(session, None, 1)) == 1
    assert [name for name, _ in session.calls] == ["get_game_overview", "end_turn"]


def test_save_replaces_loaded_session_explicitly():
    session = Session(["Turn 20 | Rome", "Loaded fixture: Sumeria, turn 1.", "Turn 1 | Sumeria", "Turn 1 -> 2"])
    assert asyncio.run(driver.run(session, "fixture", 1)) == 0
    assert session.calls[1] == ("restart_and_load", {"save_name": "fixture"})
    assert not any(name == "launch_game" for name, _ in session.calls)


def test_save_launches_when_no_loaded_game_confirmed():
    session = Session([
        "Error: game not running", "Game launched. FireTuner port is open.",
        "Loaded fixture: Sumeria, turn 1.", "Turn 1 | Sumeria", "Turn 1 -> 2",
    ])
    assert asyncio.run(driver.run(session, "fixture", 1)) == 0
    assert session.calls[1:3] == [("launch_game", {}), ("load_game_save", {"save_name": "fixture"})]


def test_loading_is_not_confirmation():
    session = Session(["Turn 20 | Rome", "Loading fixture..."])
    assert asyncio.run(driver.run(session, "fixture", 1)) == 1
    assert len(session.calls) == 2


def test_no_game_and_no_save_does_not_launch():
    session = Session(["Error: game not running"])
    assert asyncio.run(driver.run(session, None, 1)) == 1
    assert session.calls == [("get_game_overview", {})]


def test_research_selection_uses_available_section_not_current_or_locked():
    session = Session([
        "Turn 1 | Sumeria", "Cannot end turn — resolve these blockers:\n  - Research: choose -> set_research",
        "Researching: Mining (TECH_MINING)\n\nAvailable techs:\n  Pottery (TECH_POTTERY) — 0%, 3 turns\n\nLocked techs:\n  Writing (TECH_WRITING)",
        "Selected: Pottery", "Turn 1 -> 2",
    ])
    assert asyncio.run(driver.run(session, None, 1)) == 0
    assert session.calls[3] == ("set_research", {"tech_or_civic": "TECH_POTTERY", "category": "tech"})


def test_production_uses_idle_city_and_available_option():
    session = Session([
        "Turn 1 | Sumeria", "Cannot end turn — resolve these blockers:\n  - Production: choose",
        "City A | Building: Warrior [id:4]\nCity B | Building: nothing [id:7]",
        "Units:\n  UNIT_SCOUT (cost 30, 3 turns)\nDistricts:\n  DISTRICT_CAMPUS (cost 50)",
        "Production set: Scout", "Turn 1 -> 2",
    ])
    assert asyncio.run(driver.run(session, None, 1)) == 0
    assert session.calls[3] == ("get_city_production", {"city_id": 7})
    assert session.calls[4] == ("set_city_production", {"city_id": 7, "item_type": "UNIT", "item_name": "UNIT_SCOUT"})


def test_blocker_rounds_are_bounded():
    blocked = "Cannot end turn — resolve these blockers:\n  - Units: needs orders"
    session = Session(["Turn 1 | Sumeria", blocked, "Skipped", blocked])
    assert asyncio.run(driver.run(session, None, 1, max_blocker_rounds=1)) == 1
    assert [name for name, _ in session.calls] == ["get_game_overview", "end_turn", "skip_remaining_units", "end_turn"]


def test_unsupported_blocker_aborts_without_actions():
    session = Session(["Turn 1 | Sumeria", "Cannot end turn: World Congress requires votes"])
    assert asyncio.run(driver.run(session, None, 1)) == 1
    assert len(session.calls) == 2


def test_stdio_entry_uses_same_server_without_direct_game_calls(monkeypatch):
    captured = {}
    session = Session(["Turn 1 | Sumeria", "Turn 1 -> 2"])

    @asynccontextmanager
    async def fake_stdio(parameters):
        captured["parameters"] = parameters
        yield "read", "write"

    @asynccontextmanager
    async def fake_client(read, write, **kwargs):
        captured["streams"] = (read, write)
        async def initialize():
            captured["initialized"] = True
        session.initialize = initialize
        yield session

    monkeypatch.setattr(driver, "stdio_client", fake_stdio)
    monkeypatch.setattr(driver, "ClientSession", fake_client)
    assert asyncio.run(driver.play(None, 1)) == 0
    assert captured["parameters"].command == driver.sys.executable
    assert captured["parameters"].args == ["-m", "civ_mcp"]
    assert captured["streams"] == ("read", "write")
    assert captured["initialized"] is True

"""MCP dependency gates; all game behavior is isolated in a scripted stand-in."""

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp import server


class PendingGame:
    pending_action_entity = ("unit", 7)

    def __init__(self, confirmation):
        self.confirmation = confirmation
        self.actions = []

    async def confirm_pending_action(self):
        if self.confirmation.startswith("CONFIRMED|"):
            self.pending_action_entity = None
        return self.confirmation

    async def fortify_unit(self, unit_index):
        self.actions.append(("fortify", unit_index))
        return "FORTIFIED"

    async def skip_unit(self, unit_index):
        self.actions.append(("skip", unit_index))
        return "SKIPPED"

    async def get_units(self):
        return []

    async def upgrade_unit(self, unit_id):
        self.actions.append(("upgrade", unit_id))
        return "UPGRADED"


class Logger:
    async def log_tool_call(self, *_args):
        pass


def context(game):
    return SimpleNamespace(request_context=SimpleNamespace(
        lifespan_context=server.AppContext(game=game, logger=Logger()),
    ))


@pytest.mark.parametrize("action", ["fortify", "skip"])
def test_unknown_unit_action_blocks_dependent_mcp_commands(action):
    game = PendingGame("ERR:OUTCOME_UNKNOWN|move unit 7 is still unconfirmed")
    result = asyncio.run(server.unit_action(context(game), 65536 + 7, action))
    assert "OUTCOME_UNKNOWN" in result
    assert game.actions == []


def test_resolved_unit_action_allows_next_mcp_command():
    game = PendingGame("CONFIRMED|move reached destination")
    result = asyncio.run(server.unit_action(context(game), 65536 + 7, "fortify"))
    assert result == "FORTIFIED"
    assert game.actions == [("fortify", 7)]


def test_other_unit_does_not_depend_on_pending_position():
    game = PendingGame("ERR:OUTCOME_UNKNOWN|move unit 7 is unconfirmed")
    result = asyncio.run(server.unit_action(context(game), 65536 + 8, "fortify"))
    assert result == "FORTIFIED"
    assert game.actions == [("fortify", 8)]


@pytest.mark.parametrize("pending_flag", ["_pending_load", "_pending_save"])
def test_unknown_lifecycle_blocks_gameplay_even_without_pending_unit(pending_flag):
    game = PendingGame("CONFIRMED|unused")
    game.pending_action_entity = None
    setattr(game, pending_flag, True)
    result = asyncio.run(server.unit_action(context(game), 7, "fortify"))
    assert "OUTCOME_UNKNOWN" in result
    assert game.actions == []


def test_unknown_load_still_allows_read_only_unit_query():
    game = PendingGame("CONFIRMED|unused")
    game._pending_load = True
    result = asyncio.run(server.get_units(context(game)))
    assert "OUTCOME_UNKNOWN" not in result
    assert game.actions == []


def test_separate_upgrade_tool_cannot_bypass_pending_unit():
    game = PendingGame("ERR:OUTCOME_UNKNOWN|move unit 7 is unconfirmed")
    result = asyncio.run(server.upgrade_unit(context(game), 65536 + 7))
    assert "OUTCOME_UNKNOWN" in result
    assert game.actions == []

"""Heavy mutations must not inherit the 5s read timeout.

A timeout on a mutation is reported as ``MutationOutcomeUnknownError`` — "the
command may already have run, verify before retrying". For a command that is
merely slow that is a false positive: it costs the agent a verification round
and invites duplicate actions. These commands make the game do real work
inline (fortify/skip the whole army, recompute yields after a policy swap,
resolve a World Congress round), so they need the longer ceiling.
"""

from __future__ import annotations

import asyncio

import pytest

from civ_mcp.connection import DEFAULT_TIMEOUT, SLOW_MUTATION_TIMEOUT
from civ_mcp.game_state import GameState


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_mutation(self, lua_code, timeout=None, context="ingame", *, turn_action=None, expected_world_epoch=None):
        self.calls.append({"timeout": timeout, "context": context, "turn_action": turn_action, "lua": lua_code})
        return ["OK:done"]

    async def execute_write(self, lua_code, timeout=None, **kwargs):
        """``set_policies`` post-verifies by re-reading the policy slots."""

        return []


def _state() -> tuple[GameState, _RecordingConnection]:
    connection = _RecordingConnection()
    state = GameState.__new__(GameState)
    state.conn = connection
    return state, connection


def test_slow_mutation_timeout_is_longer_than_the_read_default():
    assert SLOW_MUTATION_TIMEOUT > DEFAULT_TIMEOUT


@pytest.mark.parametrize(
    "invoke",
    [
        pytest.param(lambda gs: gs.set_policies({0: "POLICY_X"}), id="set_policies"),
        pytest.param(lambda gs: gs.submit_congress(), id="submit_congress"),
        pytest.param(lambda gs: gs.queue_wc_votes([]), id="queue_wc_votes"),
        pytest.param(lambda gs: gs.skip_remaining_units(), id="skip_remaining_units"),
    ],
)
def test_heavy_mutations_request_the_slow_timeout(invoke):
    state, connection = _state()
    asyncio.run(invoke(state))

    assert connection.calls, "the command never reached the connection"
    for call in connection.calls:
        assert call["timeout"] == SLOW_MUTATION_TIMEOUT, call


@pytest.mark.parametrize("method", ["submit_congress", "queue_wc_votes", "drive_world_congress"])
def test_congress_mutations_use_only_the_named_phase_exception(method):
    state, connection = _state()
    state._pending_end_turn = True
    action = getattr(state, method)
    asyncio.run(action([]) if method == "queue_wc_votes" else action())
    assert connection.calls[0]["turn_action"] == "congress"
    assert "UI.RequestAction(ActionTypes.ACTION_ENDTURN)" not in connection.calls[0]["lua"]

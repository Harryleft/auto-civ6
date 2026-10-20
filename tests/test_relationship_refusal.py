"""原生明确拒绝使团时完成请求，缺少证据时保留不确定状态。"""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from test_access_confirmation_lua import AccessConn


class RefusalConn(AccessConn):
    def __init__(self, action):
        super().__init__()
        self.lua.globals().refusal_action = action
        self.lua.execute('''
charge_access=false
refusal={SessionID=80,StatementType=refusal_action,StatementSubType="AI_REFUSE_DEAL",ResponseType="NEGATIVE",FromPlayer=1}
local listeners={}
Events.DiplomacyStatement={Add=function(fn) listeners[fn]=true end,Remove=function(fn) listeners[fn]=nil end}
DiplomacyManager.GetKeyName=function(value) return value end
local close=DiplomacyManager.CloseSession
DiplomacyManager.CloseSession=function(sid)
 close(sid)
 for fn in pairs(listeners) do fn(1,0,refusal) end
 infos[sid]=nil
end
''')


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", 0.02)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", 0.001)


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
def test_explicit_native_refusal_with_closed_session_and_unchanged_gold_completes(action):
    conn = RefusalConn(action)
    state = GameState(conn)
    result = asyncio.run(state.send_diplomatic_action(1, action))
    assert result.startswith("REJECTED|Sweden refused your ")
    assert not state.pending_action_summary
    assert conn.lua.globals().gold == 100
    assert len(conn.lua.globals().friendship_requests) == 1


@pytest.mark.parametrize("setup", [
    "refusal.SessionID=81",
    "refusal.FromPlayer=2",
    'refusal.StatementType="DECLARE_FRIENDSHIP"',
    'refusal.StatementSubType="AI_ACCEPT_DEAL"',
    'refusal.ResponseType="POSITIVE"',
    "charge_access=true",
    "access_steps={true}",
])
def test_unrelated_or_conflicting_refusal_does_not_retire_or_resubmit(setup):
    conn = RefusalConn("DIPLOMATIC_DELEGATION")
    conn.lua.execute(setup)
    state = GameState(conn)
    first = asyncio.run(state.send_diplomatic_action(1, "DIPLOMATIC_DELEGATION"))
    second = asyncio.run(state.send_diplomatic_action(1, "DIPLOMATIC_DELEGATION"))
    assert "OUTCOME_UNKNOWN" in first and "OUTCOME_UNKNOWN" in second
    assert state.pending_action_summary
    assert len(conn.lua.globals().friendship_requests) == 1


def test_lost_response_is_confirmed_as_refused_without_second_submission():
    async def scenario():
        conn = RefusalConn("DIPLOMATIC_DELEGATION")
        original = conn.execute_write
        lost = False

        async def transport(source):
            nonlocal lost
            result = await original(source)
            if len(conn.lua.globals().friendship_requests) and not lost:
                lost = True
                raise OSError("native request ran but response was lost")
            return result

        conn.execute_write = transport
        state = GameState(conn)
        assert "OUTCOME_UNKNOWN" in await state.send_diplomatic_action(1, "DIPLOMATIC_DELEGATION")
        assert (await state.send_diplomatic_action(1, "DIPLOMATIC_DELEGATION")).startswith("REJECTED|")
        assert len(conn.lua.globals().friendship_requests) == 1

    asyncio.run(scenario())


def test_friendship_refusal_uses_native_declare_friend_key_and_unchanged_bilateral_states():
    conn = RefusalConn("DECLARE_FRIEND")
    state = GameState(conn)
    result = asyncio.run(state.send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result == "REJECTED|Sweden refused your friendship"
    assert not state.pending_action_summary
    assert len(conn.lua.globals().friendship_requests) == 1


@pytest.mark.parametrize("change", [
    'refusal.StatementType="DECLARE_FRIENDSHIP"',
    'refusal.StatementSubType="AI_ACCEPT_DEAL"',
    'refusal.ResponseType="POSITIVE"',
    'refusal.SessionID=81',
    'refusal.FromPlayer=2',
    'readbacks={{[0]=22,[1]=60}}',
    'readbacks={{[0]=87,[1]=60}}',
    'readbacks={{[0]=60,[1]=87}}',
    'DiplomacyManager.IsSessionIDOpen=function() return true end',
])
def test_conflicting_friendship_refusal_stays_pending_and_never_replays(change):
    conn = RefusalConn("DECLARE_FRIEND")
    conn.lua.execute(change)
    state = GameState(conn)
    for _ in range(2):
        result = asyncio.run(state.send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
        assert "OUTCOME_UNKNOWN" in result
        assert "REJECTED" not in result
    assert state.pending_action_summary
    assert len(conn.lua.globals().friendship_requests) == 1


def test_lost_friendship_refusal_reply_confirms_without_reentering_request():
    async def scenario():
        conn = RefusalConn("DECLARE_FRIEND")
        original = conn.execute_write
        lost = False

        async def transport(source):
            nonlocal lost
            rows = await original(source)
            if len(conn.lua.globals().friendship_requests) and not lost:
                lost = True
                raise OSError("原生拒绝已发生，传输返回丢失")
            return rows

        conn.execute_write = transport
        state = GameState(conn)
        assert "OUTCOME_UNKNOWN" in await state.send_diplomatic_action(1, "DECLARE_FRIENDSHIP")
        assert (await state.send_diplomatic_action(1, "DECLARE_FRIENDSHIP")).startswith("REJECTED|")
        assert not state.pending_action_summary
        assert len(conn.lua.globals().friendship_requests) == 1

    asyncio.run(scenario())

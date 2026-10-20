"""公开 MCP、GameState 与真实 Lua 的指定会话取消验证。"""

import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_unit_readiness import ReadyConn


class CloseConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.lua.execute('''
Players[2]={};targetCiv="CIVILIZATION_BRAZIL"
PlayerConfigurations[2]={GetCivilizationTypeName=function() return targetCiv end}
infos={[2]={FromPlayer=2,ToPlayer=0}}
open=true;current=2;eventID=2;emit=true
local listeners={}
Events={DiplomacySessionClosed={
 Add=function(fn) listeners[fn]=true end,
 Remove=function(fn) listeners[fn]=nil end}}
function finishClose()
 open=false;current=nil
 if emit then for fn in pairs(listeners) do fn(eventID) end end
end
DiplomacyManager={
 FindOpenSessionID=function(owner,target) assert(owner==0 and target==2);return current end,
 IsSessionIDOpen=function(sid) assert(sid==2);return open end,
 GetSessionInfo=function(sid) return infos[sid] end,
 CloseSession=function(sid)
  table.insert(submissions,sid)
  if not deferred then finishClose() end
  if fail_request then error("原生关闭进入后报错") end
 end}
''')

    async def execute_write(self, code):
        before = self.lua.eval("#submissions")
        rows = await super().execute_write(code)
        if self.lose_reply and self.lua.eval("#submissions") > before:
            self.lose_reply = False
            raise OSError("关闭完成后的回应丢失")
        return rows


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", .01)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", .001)


def test_explicit_close_without_dialogue_or_ui_confirms_exact_original_session():
    conn = CloseConn()
    state = GameState(conn)
    result = asyncio.run(server.close_diplomacy_session(make_ctx(game=state), 2))
    assert result == "DIPLOMACY_SESSION_CLOSED|player:2|session:2"
    assert conn.lua.eval("#submissions") == 1
    assert state.pending_action_summary is None


def test_lost_reply_only_confirms_existing_close():
    conn = CloseConn()
    state = GameState(conn)
    conn.lose_reply = True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    assert asyncio.run(state.close_diplomacy_session(2)).startswith("DIPLOMACY_SESSION_CLOSED|")
    assert conn.lua.eval("#submissions") == 1


def test_negative_no_session_sentinel_also_requires_exact_close_receipt():
    conn = CloseConn()
    state = GameState(conn)
    conn.lua.execute("deferred=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    conn.lua.execute("finishClose();current=-1")
    assert asyncio.run(state.close_diplomacy_session(2)).startswith("DIPLOMACY_SESSION_CLOSED|")
    assert conn.lua.eval("#submissions") == 1


def test_failed_absence_getter_is_unknown_even_with_closed_event():
    conn = CloseConn()
    state = GameState(conn)
    conn.lua.execute("deferred=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    conn.lua.execute('finishClose();DiplomacyManager.FindOpenSessionID=function() error("不可读") end')
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    assert conn.lua.eval("#submissions") == 1


@pytest.mark.parametrize("setup", [
    "current=-1", "current=nil", "open=false", "open=nil",
    "infos[2]=nil", "infos[2]={FromPlayer=1,ToPlayer=0}",
    "infos[2]={FromPlayer=2,ToPlayer=1}", "targetCiv=nil",
    "Events.DiplomacySessionClosed=nil", "busy=true",
])
def test_unknown_or_changed_participants_are_refused_without_submission(setup):
    conn = CloseConn()
    state = GameState(conn)
    conn.lua.execute(setup)
    assert asyncio.run(state.close_diplomacy_session(2)).startswith("Error:")
    assert conn.lua.eval("#submissions") == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize("setup", ["emit=false", "eventID=3", "fail_request=true"])
def test_missing_receipt_or_native_error_never_replays_close(setup):
    conn = CloseConn()
    state = GameState(conn)
    conn.lua.execute(setup)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    assert state.pending_action_summary
    assert conn.lua.eval("#submissions") == 1


@pytest.mark.parametrize("change,restore", [
    ("turn=42", "turn=41"), ("seed=1", "seed=719665280"),
    ('targetCiv="CIVILIZATION_CANADA"', 'targetCiv="CIVILIZATION_BRAZIL"'),
])
def test_observed_identity_change_is_sticky(change, restore):
    conn = CloseConn()
    state = GameState(conn)
    conn.lua.execute("deferred=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    conn.lua.execute(change + ";finishClose()")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    conn.lua.execute(restore)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    assert conn.lua.eval("#submissions") == 1


def test_pending_close_blocks_other_gameplay_and_delayed_close_can_confirm():
    conn = CloseConn()
    state = GameState(conn)
    conn.lua.execute("deferred=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.skip_unit(196608))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(1))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.end_turn())
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.save_game("pending_close"))
    conn.lua.execute("finishClose()")
    assert asyncio.run(state.close_diplomacy_session(2)).startswith("DIPLOMACY_SESSION_CLOSED|")
    assert conn.lua.eval("#submissions") == 1


def test_replacement_session_cannot_be_closed_by_retry():
    conn = CloseConn()
    state = GameState(conn)
    conn.lua.execute("deferred=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    conn.lua.execute("finishClose();current=3")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.close_diplomacy_session(2))
    assert conn.lua.eval("#submissions") == 1


@pytest.mark.parametrize("target", [-1, 63, True, "2"])
def test_invalid_target_is_rejected(target):
    conn = CloseConn()
    assert asyncio.run(GameState(conn).close_diplomacy_session(target)).startswith("Error: INVALID_PLAYER_ID|")
    assert conn.lua.eval("#submissions") == 0

"""通过 GameState 和实际 Lua 验证多个原生会话的读取与响应归属。"""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from test_diplomacy_context_lua import DiplomacyConn, incoming
from test_trade_progress import AsyncDiplomacyConn


class MultiSessionConn(AsyncDiplomacyConn):
    def __init__(self):
        super().__init__()
        self.setup('''
sessions={}; infos={}; selected=2; native_context="InGame"
ContextPtr.GetID=function() return native_context end
GetSelectedPlayerID=function() return selected end
local handler=GetStatementHandler()
handler.OnSelectionButtonClicked=function(key)
 local sid=sessions[selected]
 table.insert(mutations,"response:"..selected..":"..key)
 sessions[selected]=nil; infos[sid]=nil
 Events.DiplomacySessionClosed.Emit(sid)
 selected=selected==2 and 1 or 2
 if not sessions[selected] then controls["/InGame/DiplomacyActionView"].hidden=true end
end
''')

    async def execute_write(self, source):
        self.lua.globals().native_context = "InGame"
        return await DiplomacyConn.execute_write(self, source)

    async def execute_in_named_state(self, name, source, timeout=5):
        assert name in {"DiplomacyActionView", "DiplomacyDealView"}
        self.lua.globals().native_context = name
        try:
            return await DiplomacyConn.execute_write(self, source)
        finally:
            self.lua.globals().native_context = "InGame"

    def meet_both(self, selected=2):
        self.setup('''
sessions={[1]=71,[2]=72}; infos={[71]={FromPlayer=1,ToPlayer=0},[72]={FromPlayer=2,ToPlayer=0}}
controls["/InGame/DiplomacyActionView"].hidden=false
for other,sid in pairs(sessions) do
 Events.DiplomacyStatement.Emit(other,0,{SessionID=sid,StatementType="GREETING",
 StatementSubType="INITIAL",FromPlayer=other,FromPlayerMood=0,Initiator=0})
end
''')
        self.lua.globals().selected = selected


def prepared(selected=2):
    conn = MultiSessionConn()
    state = GameState(conn)
    # 原生事件必须来自观察器安装之后，不能伪造已经丢失的会面语句。
    assert asyncio.run(state.get_diplomacy_sessions()).status == "NONE"
    conn.meet_both(selected)
    return conn, state


@pytest.mark.parametrize("selected", [1, 2])
def test_current_native_leader_is_read_and_both_sessions_can_be_handled(selected):
    conn, state = prepared(selected)
    context = asyncio.run(state.get_diplomacy_sessions())
    assert context.status == "DIALOGUE"
    assert [item.other_player_id for item in context.sessions] == [selected]
    assert context.sessions[0].choice_keys == ["CHOICE_POSITIVE", "CHOICE_NEGATIVE"]
    assert asyncio.run(state.get_pending_deals()).status == "DIALOGUE"
    assert list(conn.lua.globals().mutations.values()) == []

    assert "SESSION_CLOSED" in asyncio.run(state.diplomacy_respond(selected, "POSITIVE"))
    remaining = 2 if selected == 1 else 1
    context = asyncio.run(state.get_diplomacy_sessions())
    assert [item.other_player_id for item in context.sessions] == [remaining]
    assert "SESSION_CLOSED" in asyncio.run(state.diplomacy_respond(remaining, "NEGATIVE"))
    assert asyncio.run(state.get_diplomacy_sessions()).status == "NONE"
    assert list(conn.lua.globals().mutations.values()) == [
        f"response:{selected}:CHOICE_POSITIVE", f"response:{remaining}:CHOICE_NEGATIVE",
    ]


def test_queued_target_cannot_receive_current_dialogue_response():
    conn, state = prepared()
    assert "UNKNOWN" in asyncio.run(state.diplomacy_respond(1, "POSITIVE"))
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("setup", [
    "selected=99",
    "GetSelectedPlayerID=function() error('transition') end",
    'controls["/InGame/DiplomacyActionView"].hidden=true',
    "infos[71]=nil",
])
def test_missing_or_conflicting_native_context_stays_unknown_without_mutation(setup):
    conn, state = prepared()
    conn.setup(setup)
    for context in (asyncio.run(state.get_diplomacy_sessions()), asyncio.run(state.get_pending_deals())):
        assert context.status == "UNKNOWN"
        assert all(not item.dialogue_text for item in context.sessions)
    assert "UNKNOWN" in asyncio.run(state.diplomacy_respond(2, "POSITIVE"))
    assert list(conn.lua.globals().mutations.values()) == []


def test_multiple_sessions_incoming_deal_only_belongs_to_current_target():
    conn, state = prepared()
    incoming(conn)
    context = asyncio.run(state.get_pending_deals())
    assert context.status == "INCOMING"
    assert [item.other_player_id for item in context.deals] == [2]
    assert list(conn.lua.globals().mutations.values()) == []


def test_native_target_change_after_choices_prevents_submission(monkeypatch):
    conn, state = prepared()
    original = state._diplomacy_progress.choices

    async def switch_after_choices(*args):
        result = await original(*args)
        conn.setup("selected=1")
        return result

    monkeypatch.setattr(state._diplomacy_progress, "choices", switch_after_choices)
    result = asyncio.run(state.diplomacy_respond(2, "POSITIVE"))
    assert "NOT_SUBMITTED" in result or "UNKNOWN" in result
    assert list(conn.lua.globals().mutations.values()) == []

"""GameState queries/actions execute Lua 5.1 against isolated native API fixtures."""

import asyncio

import pytest

from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState
from civ_mcp import narrate


class DiplomacyConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
Game = {GetLocalPlayer=function() return 0 end}
Locale = {Lookup=function(value) return value end}
local diplo = {IsAtWarWith=function() return false end, HasMet=function() return true end}
local function player()
    return {IsAlive=function() return true end, IsMajor=function() return true end,
            GetDiplomacy=function() return diplo end}
end
Players = {[0]=player(), [1]=player(), [2]=player()}
local function config(name)
    return {GetCivilizationShortDescription=function() return name end,
            GetLeaderName=function() return name .. " leader" end}
end
PlayerConfigurations = {[1]=config("Sweden"), [2]=config("Rome")}
sessions = {[1]=71}
infos = {[71]={FromPlayer=1, ToPlayer=0}}
mutations, reads = {}, 0
DiplomacyManager = {
    FindOpenSessionID=function(me, other) return sessions[other] end,
    GetSessionInfo=function(sid) return infos[sid] end,
    IsSessionIDOpen=function(sid) return infos[sid] ~= nil end,
    AddResponse=function() table.insert(mutations, "response") end,
    CloseSession=function(sid)
        table.insert(mutations, "close")
        for other, value in pairs(sessions) do if value == sid then sessions[other]=nil end end
    end,
}
local function control(hidden, text)
    return {hidden=hidden, text=text, IsHidden=function(self) return self.hidden end,
            GetText=function(self) return self.text end,
            GetParent=function(self) return self end}
end
controls = {
    ["/InGame/DiplomacyActionView"]=control(false),
    ["/InGame/DiplomacyDealView"]=control(true),
    ["/InGame/DiplomacyActionView/ConversationContainer"]=control(false),
    ["/InGame/DiplomacyActionView/LeaderResponseText"]=control(false, "May we establish an embassy?"),
    ["/InGame/DiplomacyActionView/LeaderReasonText"]=control(false, ""),
    ["/InGame/DiplomacyActionView/SelectionStack/Selection1Button/SelectionText"]=control(false, "Welcome"),
    ["/InGame/DiplomacyDealView/LeaderResponseText"]=control(false, "Old great work proposal"),
}
ContextPtr = {LookUpControl=function(_, path) return controls[path] end}
DealDirection = {INCOMING=1, OUTGOING=2}
DealItemTypes = {GOLD=1, RESOURCES=2, AGREEMENTS=3, FAVOR=4, CITIES=5, GREATWORK=6}
DealProposalAction = {ACCEPTED=1, REJECTED=2}
local item = {
    GetFromPlayerID=function() return 1 end, GetType=function() return DealItemTypes.GREATWORK end,
    GetValueTypeID=function() return "GREATWORK_OLD" end,
    GetSubTypeID=function() return "" end, GetValueType=function() return 9 end,
    GetSubType=function() return 0 end, GetAmount=function() return 1 end,
    GetDuration=function() return 0 end,
}
deal = {GetItemCount=function() return 1 end, IsValid=function() return true end,
        Items=function() local used=false; return function()
            if not used then used=true; return item end
        end end}
DealManager = {
    GetWorkingDeal=function(direction, me, other) reads=reads+1; return deal end,
    SendWorkingDeal=function(action, me, other) table.insert(mutations, "send:" .. action .. ":" .. other) end,
    ClearWorkingDeal=function() table.insert(mutations, "clear") end,
    CopyIncomingToOutgoingWorkingDeal=function() table.insert(mutations, "copy") end,
}
LuaEvents = {DiplomacyActionView_ShowIngameUI=function() table.insert(mutations, "show") end}
Events = {HideLeaderScreen=function() table.insert(mutations, "hide") end}
''')

    def setup(self, source):
        self.lua.execute(source)

    async def execute_write(self, source):
        self.rows.clear()
        self.lua.execute(source)
        return [row for row in self.rows if row != "---END---"]


def run(awaitable):
    return asyncio.run(awaitable)


def test_embassy_dialogue_does_not_read_or_display_valid_old_working_deal():
    conn = DiplomacyConn()
    state = GameState(conn)
    text = narrate.narrate_diplomacy_sessions(run(state.get_diplomacy_sessions()))
    assert "May we establish an embassy?" in text
    assert "Great Work" not in text and "Old great work proposal" not in text
    assert conn.lua.globals().reads == 0
    assert list(conn.lua.globals().mutations.values()) == []


def test_trade_response_rejects_dialogue_before_send_or_session_cleanup():
    conn = DiplomacyConn()
    result = run(GameState(conn).respond_to_deal(1, True))
    assert result.startswith("ERR:")
    assert "DIALOGUE" in result
    assert list(conn.lua.globals().mutations.values()) == []
    assert conn.lua.globals().reads == 0


def incoming(conn):
    conn.setup('''
controls["/InGame/DiplomacyDealView"].hidden=false
controls["/InGame/DiplomacyActionView/ConversationContainer"].hidden=true
controls["/InGame/DiplomacyDealView/LeaderResponseText"].text="Current demand"
controls["/InGame/DiplomacyDealView/AcceptDeal"]={IsHidden=function() return false end,IsDisabled=function() return false end}
DealManager.HasPendingDeal=function() return false end
DealManager.AreWorkingDealsEqual=function() return true end
''')


def test_current_incoming_with_both_roots_visible_is_readable_without_mutation():
    conn = DiplomacyConn()
    incoming(conn)
    state = GameState(conn)
    sessions = run(state.get_diplomacy_sessions())
    deals = run(state.get_pending_deals())
    assert sessions.status == deals.status == "INCOMING"
    assert sessions.sessions[0].dialogue_text == "Current demand"
    assert deals.deals[0].items_from_them[0].name == "Great Work"
    assert "deal or demand" in narrate.narrate_diplomacy_sessions(sessions)
    assert "deal or demand" in narrate.narrate_pending_deals(deals)
    assert list(conn.lua.globals().mutations.values()) == []
    # Queries stay useful without the named native observer; mutation requires it.
    assert "NOT_SUBMITTED" in run(state.respond_to_deal(1, True))
    assert list(conn.lua.globals().mutations.values()) == []


def test_both_queries_preserve_dialogue_without_reading_the_trade_cache():
    conn = DiplomacyConn()
    state = GameState(conn)
    sessions = run(state.get_diplomacy_sessions())
    deals = run(state.get_pending_deals())
    assert sessions.status == deals.status == "DIALOGUE"
    assert deals.deals == []
    assert "DIALOGUE" in narrate.narrate_pending_deals(deals)
    assert "Welcome" in narrate.narrate_diplomacy_sessions(sessions)
    assert conn.lua.globals().reads == 0


def test_finished_trade_returns_to_dialogue_while_the_working_cache_remains_valid():
    conn = DiplomacyConn()
    incoming(conn)
    state = GameState(conn)
    assert run(state.get_pending_deals()).status == "INCOMING"
    conn.setup('''
controls["/InGame/DiplomacyDealView"].hidden=true
controls["/InGame/DiplomacyActionView/ConversationContainer"].hidden=false
controls["/InGame/DiplomacyActionView/LeaderResponseText"].text="No, thank you."
''')
    conn.lua.globals().reads = 0
    context = run(state.get_diplomacy_sessions())
    assert context.status == "DIALOGUE"
    assert context.sessions[0].dialogue_text == "No, thank you."
    assert context.sessions[0].deal_summary == ""
    assert run(state.get_pending_deals()).status == "DIALOGUE"
    assert "ERR:" in run(state.respond_to_deal(1, False))
    assert conn.lua.globals().reads == 0
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("setup, reason", [
    ('sessions[2]=72; infos[72]={FromPlayer=2, ToPlayer=0}', "MULTIPLE_SESSIONS"),
    ('infos[71]=nil', "SESSION_PARTICIPANTS_UNKNOWN"),
    ('infos[71]={FromPlayer=2, ToPlayer=0}', "SESSION_PARTICIPANTS_UNKNOWN"),
    ('infos[71]={FromPlayer=1}', "SESSION_PARTICIPANTS_UNKNOWN"),
    ('infos[71]={FromPlayer=1, ToPlayer=2}', "SESSION_PARTICIPANTS_UNKNOWN"),
    ('infos[71]={FromPlayer=0, ToPlayer=1}', "OUTGOING_SESSION"),
    ('controls["/InGame/DiplomacyDealView"]=nil', "VIEW_UNAVAILABLE"),
    ('controls["/InGame/DiplomacyActionView"].IsHidden=function() error("transition") end', "VIEW_UNAVAILABLE"),
    ('controls["/InGame/DiplomacyActionView/ConversationContainer"].hidden=false', "CONFLICTING_VIEWS"),
    ('deal.IsValid=function() return false end', "INCOMING_UNAVAILABLE"),
    ('deal.GetItemCount=function() return 0 end', "INCOMING_UNAVAILABLE"),
    ('deal=nil', "INCOMING_UNAVAILABLE"),
    ('DealManager.GetWorkingDeal=function() error("unavailable") end', "INCOMING_UNAVAILABLE"),
])
def test_ambiguous_context_is_unknown_in_both_tools_and_cannot_send_or_close(setup, reason):
    conn = DiplomacyConn()
    incoming(conn)
    conn.setup(setup)
    state = GameState(conn)
    sessions = run(state.get_diplomacy_sessions())
    deals = run(state.get_pending_deals())
    assert sessions.status == deals.status == "UNKNOWN"
    assert sessions.reason == deals.reason == reason
    assert deals.deals == []
    assert all(not (s.dialogue_text or s.reason_text or s.buttons or s.deal_summary) for s in sessions.sessions)
    for text in (narrate.narrate_diplomacy_sessions(sessions), narrate.narrate_pending_deals(deals)):
        assert "UNKNOWN" in text and "Current demand" not in text
        assert "No pending" not in text
    for accept in (True, False):
        assert run(state.respond_to_deal(1, accept)).startswith("ERR:")
    assert list(conn.lua.globals().mutations.values()) == []


def test_hidden_roots_cannot_authorize_their_locally_visible_children_or_valid_cached_deal():
    conn = DiplomacyConn()
    conn.setup('controls["/InGame/DiplomacyActionView"].hidden=true')
    state = GameState(conn)
    context = run(state.get_diplomacy_sessions())
    assert context.status == "UNKNOWN" and context.reason == "NO_CURRENT_VIEW"
    assert context.sessions[0].dialogue_text == ""
    assert run(state.get_pending_deals()).status == "UNKNOWN"
    assert run(state.respond_to_deal(1, True)).startswith("ERR:")
    assert conn.lua.globals().reads == 0
    assert list(conn.lua.globals().mutations.values()) == []


def test_no_session_is_none_even_if_valid_cached_deal_and_child_text_remain():
    conn = DiplomacyConn()
    conn.setup('sessions={}; controls["/InGame/DiplomacyActionView"].hidden=true')
    state = GameState(conn)
    for context in (run(state.get_diplomacy_sessions()), run(state.get_pending_deals())):
        assert context.status == "NONE"
        assert context.sessions == context.deals == []
    assert "No pending" in narrate.narrate_pending_deals(run(state.get_pending_deals()))
    assert run(state.respond_to_deal(1, True)).startswith("ERR:")
    assert conn.lua.globals().reads == 0
    assert list(conn.lua.globals().mutations.values()) == []


def test_repeated_native_sid_does_not_duplicate_a_verified_session_or_global_text():
    conn = DiplomacyConn()
    conn.setup('sessions[2]=71')
    context = run(GameState(conn).get_diplomacy_sessions())
    assert context.status == "DIALOGUE"
    assert len(context.sessions) == 1
    assert context.sessions[0].other_player_id == 1
    assert narrate.narrate_diplomacy_sessions(context).count("May we establish an embassy?") == 1


def test_target_mismatch_cannot_submit_or_close_the_other_current_proposal():
    conn = DiplomacyConn()
    incoming(conn)
    for accept in (True, False):
        assert run(GameState(conn).respond_to_deal(2, accept)).startswith("ERR:")
    assert list(conn.lua.globals().mutations.values()) == []


def test_unknown_after_explicit_dialogue_response_does_not_close_or_hide_uncertainty(monkeypatch):
    async def instant_sleep(_):
        pass
    monkeypatch.setattr(asyncio, "sleep", instant_sleep)
    from test_trade_progress import AsyncDiplomacyConn
    conn = AsyncDiplomacyConn()
    conn.setup('''
DiplomacyManager.AddResponse=function()
    table.insert(mutations, "response")
    controls["/InGame/DiplomacyActionView"].hidden=true
end
''')
    state = GameState(conn)
    run(state.get_diplomacy_sessions())
    conn.statement()
    result = run(state.diplomacy_respond(1, "POSITIVE"))
    assert "UNKNOWN" in result
    assert list(conn.lua.globals().mutations.values()) == ["response"]


@pytest.mark.parametrize("setup", [
    'controls["/InGame/DiplomacyActionView/LeaderResponseText"].text=""',
    'DiplomacyManager.AddResponse=function() table.insert(mutations, "response"); sessions[1]=72; infos[72]={FromPlayer=1, ToPlayer=0} end',
])
def test_empty_text_or_a_different_verified_dialogue_is_not_automatic_goodbye(monkeypatch, setup):
    async def instant_sleep(_):
        pass
    monkeypatch.setattr(asyncio, "sleep", instant_sleep)
    from test_trade_progress import AsyncDiplomacyConn
    conn = AsyncDiplomacyConn()
    conn.setup(setup)
    state = GameState(conn)
    run(state.get_diplomacy_sessions())
    conn.statement()
    assert "OUTCOME_UNKNOWN" in run(state.diplomacy_respond(1, "POSITIVE"))
    assert list(conn.lua.globals().mutations.values()) == ["response"]


def test_unchanged_text_never_auto_closes_native_dialogue_without_new_statement(monkeypatch):
    async def instant_sleep(_):
        pass
    monkeypatch.setattr(asyncio, "sleep", instant_sleep)
    from test_trade_progress import AsyncDiplomacyConn
    conn = AsyncDiplomacyConn()
    state = GameState(conn)
    run(state.get_diplomacy_sessions())
    conn.statement()
    assert "OUTCOME_UNKNOWN" in run(state.diplomacy_respond(1, "POSITIVE"))
    assert "OUTCOME_UNKNOWN" in run(state.diplomacy_respond(1, "POSITIVE"))
    assert list(conn.lua.globals().mutations.values()) == ["response"]


@pytest.mark.parametrize("rows", [
    ["CONTEXT|UNKNOWN|VIEW_UNAVAILABLE", "CONTEXT_COMPLETE"],
    ["CONTEXT|DIALOGUE|CURRENT_DIALOGUE_VIEW", "CONTEXT_COMPLETE"],
    ["CONTEXT|NONE|NO_OPEN_SESSION", "SESSION|71|1|Sweden|Kristina||||0", "CONTEXT_COMPLETE"],
    ["CONTEXT|INCOMING|CURRENT_INCOMING_VIEW", "SESSION|71|1|Sweden|Kristina||||0", "CONTEXT_COMPLETE"],
    ["CONTEXT|NONE|NO_OPEN_SESSION", "CONTEXT|NONE|NO_OPEN_SESSION", "CONTEXT_COMPLETE"],
    ["SESSION|71|1|Sweden|Kristina||||0", "CONTEXT_COMPLETE"],
])
def test_incomplete_or_unknown_protocol_never_narrates_absent_diplomacy(rows):
    class ProtocolConn:
        async def execute_write(self, _):
            return rows
    state = GameState(ProtocolConn())
    for context in (run(state.get_diplomacy_sessions()), run(state.get_pending_deals())):
        assert context.status == "UNKNOWN"
        assert context.deals == []
        assert "UNKNOWN" in narrate.narrate_diplomacy_sessions(context)
        assert "UNKNOWN" in narrate.narrate_pending_deals(context)


def test_open_defeat_dialogue_is_not_absent_just_because_the_speaker_is_no_longer_alive():
    conn = DiplomacyConn()
    conn.setup('Players[1].IsAlive=function() return false end')
    context = run(GameState(conn).get_diplomacy_sessions())
    assert context.status == "DIALOGUE"
    assert context.sessions[0].other_player_id == 1

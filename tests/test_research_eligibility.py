"""Research selection through GameState and the native Lua 5.1 boundary."""

import asyncio

import pytest
from lupa.lua51 import LuaError as NativeLuaError

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from civ_mcp.connection import LuaError
from test_civic_progress import CivicConn


class SelectionConn(CivicConn):
    """Native facts plus a forbidden GameCore setter counter."""

    def __init__(self):
        super().__init__()
        self.contexts = []
        self.lose_reply = False
        self.cancel_reply = False
        self.lua.execute('''
context="InGame"
ui_allowed=true; gc_allowed=true; completed=false
ui_requests=0; gc_requests=0; progress_writes=0; accept_ui=true
current_tech=-1; current_civic=0
turn,seed=41,719665280
Game.GetCurrentGameTurn=function() return turn end
GameConfiguration={GetValue=function() return seed end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end}}
for row in GameInfo.Civics() do row.Hash=300+row.Index end
local tech={Index=0,Hash=200,TechnologyType="TECH_POTTERY",Name="Pottery"}
GameInfo.Technologies=setmetatable({[0]=tech,TECH_POTTERY=tech},{__call=function()
    local delivered=false
    return function() if not delivered then delivered=true; return tech end end
end})
local function allowed()
    if context=="InGame" then return ui_allowed end
    return gc_allowed
end
techs={
    HasTech=function() return completed end,
    CanResearch=allowed,
    GetResearchingTech=function() return current_tech end,
    GetResearchCost=function() return 25 end,
    GetResearchProgress=function() return 0 end,
    GetTurnsToResearch=function() return 3 end,
    HasBoostBeenTriggered=function() return false end,
    SetResearchingTech=function(_,idx) gc_requests=gc_requests+1; current_tech=idx end,
    SetResearchProgress=function() progress_writes=progress_writes+1 end,
    ChangeResearchProgress=function() progress_writes=progress_writes+1 end,
}
Players[0].GetTechs=function() return techs end
culture.HasCivic=function() return completed end
culture.CanProgress=allowed
culture.GetProgressingCivic=function() return current_civic end
culture.SetProgressingCivic=function(_,idx) gc_requests=gc_requests+1; current_civic=idx end
culture.SetCulturalProgress=function() progress_writes=progress_writes+1 end
culture.ChangeCurrentCulturalProgress=function() progress_writes=progress_writes+1 end
PlayerOperations={PARAM_TECH_TYPE="tech",PARAM_CIVIC_TYPE="civic",
                  PARAM_INSERT_MODE="mode",VALUE_EXCLUSIVE=1,RESEARCH="research",PROGRESS_CIVIC="culture"}
UI={IsGameCoreBusy=function() return false end,RequestPlayerOperation=function(_,operation,params)
    assert(params.mode==1)
    ui_requests=ui_requests+1
    if operation=="research" then assert(params.tech==200) else assert(params.civic==301) end
    if accept_ui then
        if operation=="research" then current_tech=0
        else current_civic=1 end
    end
    if fail_request then error('native selection failed after entry') end
end}
NotificationManager={GetList=function() return {} end}
''')

    async def execute_read(self, source):
        self.contexts.append("GameCore")
        self.lua.globals().context = "GameCore"
        return await super().execute_read(source)

    async def execute_write(self, source):
        self.contexts.append("InGame")
        self.lua.globals().context = "InGame"
        before = self.lua.globals().ui_requests
        try:
            result = await super().execute_read(source)
        except NativeLuaError as exc:
            raise LuaError(str(exc)) from exc
        if self.lua.globals().ui_requests > before:
            if self.cancel_reply:
                raise asyncio.CancelledError()
            if self.lose_reply:
                self.lose_reply = False
                raise OSError('native selection ran; response lost')
        return result

    def select(self, category):
        state = GameState(self)
        operation = state.set_research("TECH_POTTERY") if category == "tech" else state.set_civic("CIVIC_PARTIAL")
        return asyncio.run(operation)

    @property
    def writes(self):
        native = self.lua.globals()
        return native.ui_requests, native.gc_requests, native.progress_writes


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


@pytest.mark.parametrize('category',['tech','civic'])
def test_delayed_selection_only_confirms_original_and_never_uses_gamecore(category):
    conn = SelectionConn(); state = GameState(conn)
    conn.lua.execute('accept_ui=false')
    method,args = (state.set_research,('TECH_POTTERY',)) if category=='tech' else (state.set_civic,('CIVIC_PARTIAL',))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(method(*args))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(method(*args))
    conn.lua.execute('current_tech=0; current_civic=1')
    assert ('RESEARCHING' if category=='tech' else 'PROGRESSING') in asyncio.run(method(*args))
    assert conn.writes==(1,0,0)
    assert state.pending_action_summary is None


@pytest.mark.parametrize("category", ["tech", "civic"])
def test_native_denial_stops_before_ui_or_gamecore_submission(category):
    conn = SelectionConn()
    conn.lua.execute('ui_allowed=false')
    result = conn.select(category)
    assert "NOT_ELIGIBLE" in result
    assert conn.writes == (0, 0, 0)
    assert set(conn.contexts) == {"InGame"}


@pytest.mark.parametrize("category", ["tech", "civic"])
@pytest.mark.parametrize("value", ["nil", "1", '"true"'])
def test_unknown_native_eligibility_is_distinct_from_denial_and_never_submits(category, value):
    conn = SelectionConn()
    conn.lua.execute(f'ui_allowed={value}')
    assert "ELIGIBILITY_UNKNOWN" in conn.select(category)
    assert conn.writes == (0, 0, 0)


@pytest.mark.parametrize("category", ["tech", "civic"])
def test_already_selected_unfinished_target_only_confirms_without_writes(category):
    conn = SelectionConn()
    conn.lua.execute('current_tech=0; current_civic=1; ui_allowed=false')
    assert "ALREADY_SELECTED" in conn.select(category)
    assert conn.writes == (0, 0, 0)
    assert set(conn.contexts) == {"InGame"}


@pytest.mark.parametrize("category", ["tech", "civic"])
@pytest.mark.parametrize("patch", [
    'completed=nil',
    'completed=1',
    'current_tech=nil; current_civic=nil',
    'current_tech="0"; current_civic="0"',
])
def test_unknown_completion_or_current_target_stops_without_writes(category, patch):
    conn = SelectionConn()
    conn.lua.execute(patch)
    assert "ELIGIBILITY_UNKNOWN" in conn.select(category)
    assert conn.writes == (0, 0, 0)


@pytest.mark.parametrize("category", ["tech", "civic"])
@pytest.mark.parametrize("api", ["completion", "current", "eligibility"])
@pytest.mark.parametrize("native_api", ['nil', 'function() error("API unavailable") end'])
def test_missing_or_throwing_native_selection_apis_stop_without_submission(category, api, native_api):
    conn = SelectionConn()
    native_object, methods = (
        ("techs", {"completion": "HasTech", "current": "GetResearchingTech", "eligibility": "CanResearch"})
        if category == "tech" else
        ("culture", {"completion": "HasCivic", "current": "GetProgressingCivic", "eligibility": "CanProgress"})
    )
    patch = f'{native_object}.{methods[api]}={native_api}'
    conn.lua.execute(patch)
    assert "ELIGIBILITY_UNKNOWN" in conn.select(category)
    assert conn.writes == (0, 0, 0)


@pytest.mark.parametrize("category", ["tech", "civic"])
def test_completed_target_is_rejected_even_if_it_is_current(category):
    conn = SelectionConn()
    patch = 'completed=true; current_tech=0; current_civic=1'
    conn.lua.execute(patch)
    assert "ALREADY_COMPLETED" in conn.select(category)
    assert conn.writes == (0, 0, 0)


@pytest.mark.parametrize("category", ["tech", "civic"])
@pytest.mark.parametrize("accept_ui,expected_writes", [(True, (1, 0, 0)), (False, (1, 0, 0))])
def test_native_legal_selection_never_switches_write_channels(category, accept_ui, expected_writes):
    conn = SelectionConn()
    conn.lua.globals().accept_ui = accept_ui
    result = conn.select(category)
    assert (("RESEARCHING" if category == "tech" else "PROGRESSING") if accept_ui else "OUTCOME_UNKNOWN") in result
    assert conn.writes == expected_writes


@pytest.mark.parametrize("value", ["nil", "1", '"true"'])
def test_public_query_rejects_unknown_native_technology_eligibility(value):
    conn = SelectionConn()
    conn.lua.execute(f'ui_allowed={value}')
    with pytest.raises(ValueError, match="ELIGIBILITY_UNKNOWN.*TECH_POTTERY"):
        conn.query()


@pytest.mark.parametrize('category,target,label', [
    ('tech', 'TECH_POTTERY', 'RESEARCHING'),
    ('civic', 'CIVIC_PARTIAL', 'PROGRESSING'),
])
@pytest.mark.parametrize('boundary', ['lost', 'cancelled', 'throw'])
def test_mcp_selection_uncertain_transport_keeps_one_native_submission(category,target,label,boundary):
    conn = SelectionConn(); state = GameState(conn); ctx = make_ctx(game=state)
    if boundary == 'lost': conn.lose_reply = True
    if boundary == 'cancelled': conn.cancel_reply = True
    if boundary == 'throw': conn.lua.execute('fail_request=true')
    if boundary == 'cancelled':
        with pytest.raises(asyncio.CancelledError): asyncio.run(server.set_research(ctx,target,category))
    else:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,target,category))
    result = asyncio.run(server.set_research(ctx,target,category))
    assert (label if boundary != 'throw' else 'OUTCOME_UNKNOWN') in result
    assert conn.writes == (1,0,0)


@pytest.mark.parametrize('category,target', [('tech','TECH_POTTERY'),('civic','CIVIC_PARTIAL')])
def test_shared_research_mcp_blocks_switched_category_and_target_until_original_confirms(category,target):
    conn = SelectionConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('accept_ui=false')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,target,category))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,'TECH_OTHER','tech'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,'CIVIC_OTHER','civic'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_government('GOVERNMENT_CHIEFDOM'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    conn.lua.execute('current_tech=0;current_civic=1')
    assert ('RESEARCHING' if category=='tech' else 'PROGRESSING') in asyncio.run(server.set_research(ctx,target,category))
    assert conn.writes == (1,0,0)


@pytest.mark.parametrize('category,target', [('tech','TECH_POTTERY'),('civic','CIVIC_PARTIAL')])
def test_selection_world_mismatch_cannot_be_cleared_by_matching_target_in_another_world(category,target):
    conn = SelectionConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('accept_ui=false')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,target,category))
    conn.lua.execute('seed=99;current_tech=0;current_civic=1')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,target,category))
    conn.lua.execute('seed=719665280')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,target,category))
    assert conn.writes == (1,0,0)

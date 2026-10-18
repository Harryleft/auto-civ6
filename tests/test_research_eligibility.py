"""Research selection through GameState and the native Lua 5.1 boundary."""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from test_civic_progress import CivicConn


class SelectionConn(CivicConn):
    """The existing UI and GameCore paths see independently changing facts."""

    def __init__(self):
        super().__init__()
        self.contexts = []
        self.fallback_patch = None
        self.lua.execute('''
context="InGame"
ui_allowed=true; gc_allowed=true; completed=false
ui_requests=0; gc_requests=0; progress_writes=0; accept_ui=true
current_tech=-1; current_civic=0
local tech={Index=0,TechnologyType="TECH_POTTERY",Name="Pottery"}
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
                  RESEARCH="research",PROGRESS_CIVIC="culture"}
UI={RequestPlayerOperation=function(_,operation,params)
    ui_requests=ui_requests+1
    if accept_ui then
        if operation=="research" then current_tech=params.tech
        else current_civic=params.civic end
    end
end}
NotificationManager={GetList=function() return {} end}
''')

    async def execute_read(self, source):
        self.contexts.append("GameCore")
        self.lua.globals().context = "GameCore"
        if self.contexts.count("GameCore") == 2 and self.fallback_patch:
            self.lua.execute(self.fallback_patch)
        return await super().execute_read(source)

    async def execute_write(self, source):
        self.contexts.append("InGame")
        self.lua.globals().context = "InGame"
        return await super().execute_read(source)

    def select(self, category):
        state = GameState(self)
        operation = state.set_research("TECH_POTTERY") if category == "tech" else state.set_civic("CIVIC_PARTIAL")
        return asyncio.run(operation)

    @property
    def writes(self):
        native = self.lua.globals()
        return native.ui_requests, native.gc_requests, native.progress_writes


@pytest.mark.parametrize("category", ["tech", "civic"])
def test_native_denial_stops_before_ui_or_gamecore_submission(category):
    conn = SelectionConn()
    conn.lua.execute('ui_allowed=false')
    result = conn.select(category)
    assert "NOT_ELIGIBLE" in result
    assert conn.writes == (0, 0, 0)
    assert conn.contexts == ["InGame"]


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
    assert conn.contexts == ["InGame"]


@pytest.mark.parametrize("category", ["tech", "civic"])
@pytest.mark.parametrize("stage", ["ui", "gc"])
@pytest.mark.parametrize("patch", [
    'completed=nil',
    'completed=1',
    'current_tech=nil; current_civic=nil',
    'current_tech="0"; current_civic="0"',
])
def test_unknown_completion_or_current_target_stops_without_writes(category, stage, patch):
    conn = SelectionConn()
    if stage == "gc":
        conn.lua.execute('accept_ui=false')
        conn.fallback_patch = patch
    else:
        conn.lua.execute(patch)
    assert "ELIGIBILITY_UNKNOWN" in conn.select(category)
    assert conn.writes == ((0, 0, 0) if stage == "ui" else (1, 0, 0))


@pytest.mark.parametrize("category", ["tech", "civic"])
def test_eligibility_changed_before_gamecore_fallback_is_rejected(category):
    conn = SelectionConn()
    conn.lua.execute('accept_ui=false; gc_allowed=false')
    assert "NOT_ELIGIBLE" in conn.select(category)
    assert conn.writes == (1, 0, 0)


@pytest.mark.parametrize("category", ["tech", "civic"])
@pytest.mark.parametrize("value", ["nil", "1", '"true"'])
def test_gamecore_unknown_eligibility_does_not_use_previous_ui_permission(category, value):
    conn = SelectionConn()
    conn.lua.execute(f'accept_ui=false; gc_allowed={value}')
    assert "ELIGIBILITY_UNKNOWN" in conn.select(category)
    assert conn.writes == (1, 0, 0)


@pytest.mark.parametrize("category", ["tech", "civic"])
@pytest.mark.parametrize("stage", ["ui", "gc"])
@pytest.mark.parametrize("api", ["completion", "current", "eligibility"])
@pytest.mark.parametrize("native_api", ['nil', 'function() error("API unavailable") end'])
def test_missing_or_throwing_native_selection_apis_stop_without_submission(category, stage, api, native_api):
    conn = SelectionConn()
    native_object, methods = (
        ("techs", {"completion": "HasTech", "current": "GetResearchingTech", "eligibility": "CanResearch"})
        if category == "tech" else
        ("culture", {"completion": "HasCivic", "current": "GetProgressingCivic", "eligibility": "CanProgress"})
    )
    patch = f'{native_object}.{methods[api]}={native_api}'
    if stage == "gc":
        conn.lua.execute('accept_ui=false')
        conn.fallback_patch = patch
    else:
        conn.lua.execute(patch)
    assert "ELIGIBILITY_UNKNOWN" in conn.select(category)
    assert conn.writes == ((0, 0, 0) if stage == "ui" else (1, 0, 0))


@pytest.mark.parametrize("category", ["tech", "civic"])
@pytest.mark.parametrize("stage", ["ui", "gc"])
def test_completed_target_is_rejected_even_if_it_is_current(category, stage):
    conn = SelectionConn()
    patch = 'completed=true; current_tech=0; current_civic=1'
    if stage == "gc":
        conn.lua.execute('accept_ui=false')
        conn.fallback_patch = patch
    else:
        conn.lua.execute(patch)
    assert "ALREADY_COMPLETED" in conn.select(category)
    assert conn.writes == ((0, 0, 0) if stage == "ui" else (1, 0, 0))


@pytest.mark.parametrize("category", ["tech", "civic"])
def test_target_selected_between_ui_readback_and_fallback_only_confirms(category):
    conn = SelectionConn()
    conn.lua.execute('accept_ui=false')
    conn.fallback_patch = 'current_tech=0; current_civic=1; gc_allowed=false'
    assert "ALREADY_SELECTED" in conn.select(category)
    assert conn.writes == (1, 0, 0)


@pytest.mark.parametrize("category", ["tech", "civic"])
@pytest.mark.parametrize("accept_ui,expected_writes", [(True, (1, 0, 0)), (False, (1, 1, 0))])
def test_native_legal_selection_still_uses_the_existing_paths(category, accept_ui, expected_writes):
    conn = SelectionConn()
    conn.lua.globals().accept_ui = accept_ui
    result = conn.select(category)
    assert ("RESEARCHING" if category == "tech" else "PROGRESSING") in result
    assert conn.writes == expected_writes


@pytest.mark.parametrize("value", ["nil", "1", '"true"'])
def test_public_query_rejects_unknown_native_technology_eligibility(value):
    conn = SelectionConn()
    conn.lua.execute(f'ui_allowed={value}')
    with pytest.raises(ValueError, match="ELIGIBILITY_UNKNOWN.*TECH_POTTERY"):
        conn.query()

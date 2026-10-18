"""Research facts through GameState with only the native Lua 5.1 API faked."""

import asyncio

from lupa.lua51 import LuaRuntime
import pytest

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_tech_civics


class CivicConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
local function rows(items)
    local lookup={}
    for _,row in ipairs(items) do
        if row.Index~=nil then lookup[row.Index]=row end
        if row.CivicType then lookup[row.CivicType]=row end
    end
    return setmetatable(lookup,{__call=function()
        local i=0
        return function() i=i+1; return items[i] end
    end})
end
Game={GetLocalPlayer=function() return 0 end,
      GetEras=function() return {GetCurrentEra=function() return 0 end} end}
Locale={Lookup=function(text) return text end}
GameInfo={Civics=rows({
    {Index=0,CivicType="CIVIC_CURRENT",Name="Current civic",EraType="ERA_ANCIENT"},
    {Index=1,CivicType="CIVIC_PARTIAL",Name="Partial civic",EraType="ERA_ANCIENT"}
}),Eras=rows({{Index=0,EraType="ERA_ANCIENT"}})}
for _,name in ipairs({"Technologies","Boosts","TechnologyPrereqs","CivicPrereqs",
                     "Units","Buildings","Districts","Improvements","Resources","Projects"}) do
    GameInfo[name]=rows({})
end
culture={
    GetProgressingCivic=function() return 0 end,
    HasCivic=function() return false end,
    GetCultureCost=function() return 100 end,
    GetCulturalProgress=function(_,idx) return idx==0 and 65 or 25 end,
    GetCultureYield=function() return 10 end,
    HasBoostBeenTriggered=function(_,idx) return idx==0 end,
    -- The installed native UI uses these two APIs; the old query calls neither.
    GetTurnsLeft=function() return 3 end,
    GetTurnsToProgressCivic=function(_,idx) return idx==0 and 4 or 8 end,
}
Players={[0]={GetCulture=function() return culture end,
              GetTechs=function() return {
                  GetResearchingTech=function() return -1 end,
                  HasTech=function() return false end} end}}
''')

    async def execute_read(self, source):
        self.rows.clear()
        self.lua.execute(source)
        return [row for row in self.rows if row != "---END---"]

    async def disconnect(self):
        pass

    def query(self):
        return asyncio.run(GameState(self).get_tech_civics())


def test_current_and_candidate_share_native_progress_and_current_turns():
    status = CivicConn().query()
    current, partial = status.available_civics
    assert status.current_civic_turns == current.turns == 3
    assert status.current_civic_progress_pct == current.progress_pct == 65
    assert current.boosted is True
    assert (partial.progress_pct, partial.turns) == (25, 8)
    text = narrate_tech_civics(status)
    assert "Civic: Current civic (65%, 3 turns)" in text


def test_unavailable_native_turns_estimate_only_the_remaining_cost():
    conn = CivicConn()
    conn.lua.execute('culture.GetTurnsLeft=nil; culture.GetTurnsToProgressCivic=nil')
    status = conn.query()
    current, partial = status.available_civics
    assert status.current_civic_turns == current.turns == 4
    assert partial.turns == 8
    assert current.turns_source == partial.turns_source == "remaining_cost_estimate"
    assert "4 turns (estimate)" in narrate_tech_civics(status)


def test_failed_progress_read_is_unknown_in_header_and_candidate_not_zero():
    conn = CivicConn()
    conn.lua.execute('culture.GetCulturalProgress=function() error("native read failed") end')
    status = conn.query()
    current, partial = status.available_civics
    assert status.current_civic_progress_pct is current.progress_pct is partial.progress_pct is None
    assert status.current_civic_turns is current.turns is partial.turns is None
    text = narrate_tech_civics(status)
    assert "Civic: Current civic (progress unknown, turns unknown)" in text
    assert "0%" not in text and "None turns" not in text


def test_inspiration_and_overflow_are_not_added_or_subtracted_again():
    conn = CivicConn()
    conn.lua.execute('''
culture.GetTurnsLeft=nil; culture.GetTurnsToProgressCivic=nil
culture.GetCulturalProgress=function(_,idx) return idx==0 and 113 or 50 end
culture.HasBoostBeenTriggered=function() return true end
''')
    status = conn.query()
    current, partial = status.available_civics
    assert (current.progress_pct, current.turns) == (113, 0)
    assert (partial.progress_pct, partial.turns) == (50, 5)
    assert status.current_civic_progress_pct == 113
    assert status.current_civic_turns == 0


def test_current_falls_back_to_per_civic_native_time_before_estimation():
    conn = CivicConn()
    conn.lua.execute('culture.GetTurnsLeft=function() error("temporarily unavailable") end')
    status = conn.query()
    current = status.available_civics[0]
    assert status.current_civic_turns == current.turns == 4
    assert status.current_civic_turns_source == current.turns_source == "native_civic"
    assert "(estimate)" not in narrate_tech_civics(status)


@pytest.mark.parametrize("yield_expr", ["0", "nil", "-5", "0/0", "math.huge", '"10"', 'error("unavailable")'])
def test_zero_or_unavailable_culture_keeps_progress_but_time_unknown(yield_expr):
    conn = CivicConn()
    conn.lua.execute(f'culture.GetCultureYield=function() return {yield_expr} end')
    status = conn.query()
    current = status.available_civics[0]
    assert status.current_civic_progress_pct == current.progress_pct == 65
    assert status.current_civic_turns is current.turns is None
    assert "Civic: Current civic (65%, turns unknown)" in narrate_tech_civics(status)
    assert "GRAB THIS" not in narrate_tech_civics(status)


@pytest.mark.parametrize("progress_expr", ["nil", "-1", "0/0", "math.huge", '"65"'])
def test_invalid_progress_is_unknown_not_fabricated_zero(progress_expr):
    conn = CivicConn()
    conn.lua.execute(f'culture.GetCulturalProgress=function() return {progress_expr} end')
    status = conn.query()
    assert status.current_civic_progress_pct is None
    assert status.current_civic_turns is None
    assert all(c.progress_pct is None and c.turns is None for c in status.available_civics)
    assert "progress unknown" in narrate_tech_civics(status)


@pytest.mark.parametrize("method", ["GetCulturalProgress", "GetCultureCost", "GetCultureYield"])
def test_missing_native_fact_does_not_abort_other_candidates(method):
    conn = CivicConn()
    conn.lua.execute(f'culture.{method}=nil')
    status = conn.query()
    assert len(status.available_civics) == 2
    assert status.current_civic_turns is None
    assert all(c.turns is None for c in status.available_civics)
    if method != "GetCultureYield":
        assert status.current_civic_progress_pct is None
        assert all(c.progress_pct is None for c in status.available_civics)


@pytest.mark.parametrize("cost_expr", ["0", "nil", "-1", "0/0", "math.huge", '"100"'])
def test_invalid_cost_never_yields_a_finite_time_or_percentage(cost_expr):
    conn = CivicConn()
    conn.lua.execute(f'culture.GetCultureCost=function() return {cost_expr} end')
    status = conn.query()
    assert status.current_civic_progress_pct is None
    assert status.current_civic_turns is None
    assert all(c.progress_pct is None and c.turns is None for c in status.available_civics)


class LegacyConn:
    async def execute_read(self, _source):
        return [
            "CURRENT|Pottery|2|Code of Laws|3",
            "TECH|Pottery|TECH_POTTERY|40|20|2|UNBOOSTED||Granary||ERA_ANCIENT",
            "CIVIC|Code of Laws|CIVIC_CODE_OF_LAWS|20|30|3|UNBOOSTED|||ERA_ANCIENT",
        ]

    async def disconnect(self):
        pass


def test_existing_numeric_response_still_parses_and_narrates():
    status = asyncio.run(GameState(LegacyConn()).get_tech_civics())
    assert status.current_civic_turns == 3
    assert status.current_civic_progress_pct is None
    assert status.available_techs[0].turns == 2
    assert status.available_civics[0].progress_pct == 30
    assert status.available_civics[0].turns == 3
    assert "Civic: Code of Laws (3 turns)" in narrate_tech_civics(status)

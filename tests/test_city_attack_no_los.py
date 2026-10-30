"""Native Lua behavior for a city shot rejected before command submission."""
import asyncio

from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState
from civ_mcp.lua.cities import build_city_attack


def _city_runtime():
    lua = LuaRuntime()
    rows = []
    lua.globals().print = lambda value: rows.append(str(value))
    lua.execute('''
Game = {GetLocalPlayer=function() return 0 end}
CityCommandTypes = {RANGE_ATTACK=1, PARAM_X=2, PARAM_Y=3}
CityCommandResults={PLOTS=1,MODIFIERS=2,MODIFIER_IS_TARGET=9}
DefenseTypes = {DISTRICT_OUTER=1}
GameInfo = {Districts={DISTRICT_CITY_CENTER={Index=0}}, Units={[1]={UnitType="UNIT_CATAPULT"}}}
local center = {GetType=function() return 0 end, GetMaxDamage=function() return 100 end}
local city = {
  GetID=function() return 131073 end, GetX=function() return 35 end, GetY=function() return 22 end,
  GetName=function() return "Eridu" end,
  GetDistricts=function() return {Members=function() return ipairs({center}) end} end,
}
CityManager = {
  GetCity=function() return city end,
  GetCommandTargets=function() return {{1233, 1235},{9,9}} end,
  CanStartCommand=function() return true end,
  RequestCommand=function() error("unexpected command submission") end,
}
local enemy = {GetOwner=function() return 1 end, GetID=function() return 6422531 end,
  GetType=function() return 1 end, GetDamage=function() return 0 end, GetMaxDamage=function() return 100 end}
Map = {GetPlotDistance=function() return 2 end, GetGridSize=function() return 60 end,
  GetUnitsAt=function() return {Units=function()
    local sent = false
    return function() if sent then return nil end; sent = true; return enemy end
  end} end}
''')
    return lua, rows


def test_generated_no_los_lua_reports_coordinates_before_request_or_witness():
    lua, rows = _city_runtime()
    source = build_city_attack(131073, 34, 20, witness_token="abc123")
    lua.execute(source)
    assert rows == ["ERR:TARGET_NOT_AVAILABLE|Native ranged targets do not allow this tile; cause not reported", "---END---"]
    assert lua.globals().__civmcpCityAttack is None


class NoLosConnection:
    async def execute_write(self, code):
        if "ACTION_SCOPE|" in code:
            return ["ACTION_SCOPE|CIVILIZATION_SUMERIA|1896272000|138|0"]
        if "ERR:TARGET_NOT_AVAILABLE|" in code:
            return ["ERR:TARGET_NOT_AVAILABLE|Native ranged targets do not allow this tile; cause not reported"]
        return ["EMPTY"]


def test_game_state_releases_definite_no_los_refusal_and_allows_next_gate():
    state = GameState(NoLosConnection())
    refusal = asyncio.run(state.city_attack(131073, 34, 20))
    assert refusal.startswith("Error: TARGET_NOT_AVAILABLE|")
    assert state.pending_action_summary is None
    assert state.pending_action_blocks_mutations is False
    # A repeated call must reach a new submission and receive a fresh refusal,
    # rather than being routed into confirmation of a retained owner.
    next_refusal = asyncio.run(state.city_attack(131073, 34, 20))
    assert next_refusal.startswith("Error: TARGET_NOT_AVAILABLE|")
    assert state.pending_action_summary is None


def test_unrelated_native_array_cannot_authorize_a_city_shot():
    lua, rows = _city_runtime()
    lua.execute('''
CityCommandResults={PLOTS='plots',MODIFIERS='mods',MODIFIER_IS_TARGET=1}
CityManager.GetCommandTargets=function()return {plots={1233},mods={0},other={1234}}end
''')
    lua.execute(build_city_attack(131073,34,20))
    assert rows[0].startswith('ERR:TARGET_NOT_AVAILABLE|')
    assert lua.globals().__civmcpCityAttack is None

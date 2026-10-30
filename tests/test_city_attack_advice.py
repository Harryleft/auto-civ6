"""Read advice must not expose nearby units as legal city shots."""
import asyncio

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_cities
from test_resource_hint_visibility import ResourceConn


def attack_conn():
    conn = ResourceConn()
    conn.lua.execute('''
DefenseTypes={DISTRICT_GARRISON=0,DISTRICT_OUTER=1}
local center={GetType=function()return 0 end,GetDefenseStrength=function()return 20 end,
 GetMaxDamage=function(_,kind)return kind==1 and 100 or 200 end,GetDamage=function()return 0 end,
 IsPillaged=function()return false end}
city.GetDistricts=function()return {Members=function()return ipairs({center})end}end
GameInfo.Units[1]={UnitType="UNIT_WARRIOR"}
local unit={GetOwner=function()return 1 end,GetType=function()return 1 end,
 GetMaxDamage=function()return 100 end,GetDamage=function()return 0 end}
Map.GetUnitsAt=function(x,y)
 if x==1 and y==0 then return {Units=function()local done=false;return function()if not done then done=true;return unit end end end}end
end
Map.GetPlotByIndex=function(index)return {GetX=function()return index end,GetY=function()return 0 end,GetIndex=function()return index end}end
CityCommandTypes={RANGE_ATTACK=1,PARAM_X='x',PARAM_Y='y',PARAM_RANGED_ATTACK='attack'}
CityCommandResults={PLOTS='plots',MODIFIERS='modifiers',MODIFIER_IS_TARGET=1}
CityManager.GetCommandTargets=function()return {plots={1},modifiers={1},unrelated={2}}end
allowed,visible,war=true,true,true
CityManager.CanStartCommand=function(_,command,params)assert(command==1);return allowed end
Players[0].GetDiplomacy=function()return {IsAtWarWith=function(_,other)assert(other==1);return war end}end
PlayersVisibility={[0]={IsVisible=function(_,index)return visible end,IsUnitVisible=function()return true end}}
''')
    return conn


def read(conn):
    return asyncio.run(GameState(conn).get_cities())[0][0]


def test_only_visible_hostile_native_target_with_current_command_permission_is_a_shot():
    for change in ['visible=false', 'war=false', 'allowed=false',
                   "CityManager.GetCommandTargets=function()return {plots={1},modifiers={0},unrelated={1}}end"]:
        conn = attack_conn();conn.lua.execute(change)
        assert read(conn).attack_targets == [], change
        assert conn.lua.eval('native_requests') == 0
    city = read(attack_conn())
    assert city.attack_targets == ['UNIT_WARRIOR@1,0(100hp)']


def test_failed_native_target_observation_is_explicit_unknown_not_empty_success():
    conn = attack_conn()
    conn.lua.execute("CityManager.GetCommandTargets=function()error('unavailable')end")
    city = read(conn)
    assert city.attack_targets_known is False
    assert city.attack_targets == []
    assert 'City ranged targets: unknown' in narrate_cities([city], [])


def test_invisible_unit_on_visible_native_target_plot_is_not_disclosed():
    conn=attack_conn()
    conn.lua.execute('PlayersVisibility[0].IsUnitVisible=function()return false end')
    city=read(conn)
    assert city.attack_targets==[] and city.attack_targets_known is True


def test_unknown_individual_visibility_marks_the_target_board_incomplete():
    conn=attack_conn()
    conn.lua.execute('PlayersVisibility[0].IsUnitVisible=function()return nil end')
    city=read(conn)
    assert city.attack_targets==[] and city.attack_targets_known is False

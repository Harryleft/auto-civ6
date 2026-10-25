"""Native influence eligibility distinguishes city-states from free cities."""
import pytest
from lupa.lua51 import LuaRuntime, LuaError
from civ_mcp.lua.governance import build_city_states_query


def native():
    lua=LuaRuntime()
    rows=[]
    lua.globals().capture=lambda row:rows.append(str(row))
    lua.execute('''
print=capture
Game={GetLocalPlayer=function()return 0 end}
local influence={GetTokensToGive=function()return 1 end,CanGiveInfluence=function()return true end,CanGiveTokensToPlayer=function()return true end}
local dip={HasMet=function()return true end}
local recipient={CanReceiveInfluence=function()return true end,GetTokensReceived=function()return 1 end,GetSuzerain=function()return -1 end}
local free={CanReceiveInfluence=function()return false end,GetTokensReceived=function()error('Must not read excluded free city details')end}
Players={[0]={GetInfluence=function()return influence end,GetDiplomacy=function()return dip end,IsMajor=function()return true end},
 [7]={GetInfluence=function()return recipient end,IsAlive=function()return true end,IsMajor=function()return false end,IsBarbarian=function()return false end},
 [62]={GetInfluence=function()return free end,IsAlive=function()return true end,IsMajor=function()return false end,IsBarbarian=function()return false end}}
Players[0].IsAlive=function()return true end
PlayerConfigurations={[7]={GetPlayerName=function()return 'Hong Kong'end,GetLeaderTypeName=function()return 'LEADER_HK'end}}
GameInfo={Leaders={LEADER_HK={InheritFrom='LEADER_MINOR_CIV_INDUSTRIAL'}}}
Locale={Lookup=function(s)return s end}
''')
    return lua,rows


def test_known_free_cities_are_excluded_before_reading_their_details():
    lua,rows=native()
    lua.execute(build_city_states_query())
    city_rows=[r for r in rows if r.startswith('CS|')]
    assert len(city_rows)==1 and city_rows[0].startswith('CS|7|Hong Kong|Industrial|')


def test_unavailable_city_state_identity_is_an_error_not_an_empty_list():
    lua,_=native()
    lua.execute('Players[7].GetInfluence=function()return{CanReceiveInfluence=function()return nil end}end')
    with pytest.raises(LuaError,match='CITY_STATE_IDENTITY_UNKNOWN'):
        lua.execute(build_city_states_query())

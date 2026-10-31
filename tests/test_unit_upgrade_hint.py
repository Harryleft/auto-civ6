"""Upgrade hints must use the target already returned by the native command."""
import asyncio

import pytest
from lupa.lua51 import LuaRuntime

from civ_mcp import narrate
from civ_mcp.game_state import GameState


class UpgradeHintConn:
    def __init__(self, patch=""):
        self.lua = LuaRuntime()
        self.rows = []
        self.queries = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
checks,costReads,requests=0,0,0
allowed,target=true,94
unit={GetID=function()return 131073 end,GetType=function()return 21 end,
 GetX=function()return 20 end,GetY=function()return 16 end,GetName=function()return 'Warrior'end,
 GetMovesRemaining=function()return 2 end,GetMaxMoves=function()return 2 end,
 GetMaxDamage=function()return 100 end,GetDamage=function()return 0 end,
 GetBuildCharges=function()return 0 end,GetGreatPerson=function()return nil end,
 GetSpreadCharges=function()return 0 end,GetReligionType=function()return -1 end,
 GetUpgradeCost=function()costReads=costReads+1;return 80 end}
Game={GetLocalPlayer=function()return 0 end}
GameInfo={Units={
 [21]={UnitType='UNIT_WARRIOR',Combat=20,UpgradeUnitCollection={{UpgradeUnit='UNIT_SWORDSMAN'}}},
 [94]={UnitType='UNIT_MAN_AT_ARMS'},[0]={UnitType='UNIT_NATIVE_ZERO'}}}
Players={[0]={GetUnits=function()return{Members=function()local done=false;return function()if not done then done=true;return 1,unit end end end}end}}
Locale={Lookup=function(value)return value end}
Map={GetPlotDistance=function()return 99 end}
UnitCommandTypes={UPGRADE=7};UnitCommandResults={UNIT_TYPE=8}
UnitManager={CanStartCommand=function(u,command,ui,results)
 assert(u==unit and command==7 and ui==nil and results==true)
 checks=checks+1;return allowed,{[8]=target}
 end,RequestCommand=function()requests=requests+1;error('hint must not submit')end}
''' + patch)

    async def execute_write(self, query, **kwargs):
        self.queries.append(query)
        self.rows.clear()
        self.lua.execute(query)
        return [row for row in self.rows if row != "---END---"]


def read(conn):
    result = asyncio.run(GameState(conn).get_units())
    assert len(result) == 1 and len(conn.queries) == 1
    assert conn.lua.eval("checks") == 1 and conn.lua.eval("requests") == 0
    assert conn.lua.eval("costReads") == 1
    return result[0]


def test_native_man_at_arms_target_does_not_use_static_swordsman_first_hop():
    value = read(UpgradeHintConn())
    assert value.can_upgrade is True and value.upgrade_cost == 80
    assert value.upgrade_target == "UNIT_MAN_AT_ARMS"
    text = narrate.narrate_units([value])
    assert "CAN UPGRADE to UNIT_MAN_AT_ARMS (80g)" in text
    assert "SWORDSMAN" not in text


@pytest.mark.parametrize("patch", [
    "target=nil",
    "target=false",
    "target='94'",
    "target=94.5",
    "target=math.huge",
    "target=0/0",
    "target=999",
    "target=21",
    "GameInfo.Units[94]={}",
    "GameInfo.Units[94]={UnitType=94}",
    "UnitCommandResults={}",
    "UnitCommandResults=nil",
    "UnitManager.CanStartCommand=function()checks=checks+1;return true,nil end",
])
def test_unknown_native_target_does_not_fall_back_to_static_first_hop(patch):
    value = read(UpgradeHintConn(patch))
    assert value.can_upgrade is True and value.upgrade_cost == 80
    assert value.upgrade_target == ""
    text = narrate.narrate_units([value])
    assert "target unknown" in text and "80g" in text
    assert "SWORDSMAN" not in text


def test_native_zero_reference_is_not_treated_as_absent():
    value = read(UpgradeHintConn("target=0"))
    assert value.upgrade_target == "UNIT_NATIVE_ZERO"

"""验证真实Lua资源查询包含原生顶栏使用的全部收支来源。"""

from dataclasses import replace

import pytest
from lupa.lua51 import LuaRuntime

from civ_mcp import lua as lq
from civ_mcp.game_state import GameState


@pytest.mark.parametrize('query', [lq.build_stockpile_query, lq.build_empire_resources_query])
@pytest.mark.parametrize('local_income,imported,bonus,unit_demand,power_demand,expected', [
    (0, 0, 3, 3, 0, 0),
    (4, 3, 2, 1, 2, 6),
    (0, 0, 0, 3, 0, -3),
    (7, 0, 0, 0, 3, 4),
    (0, 0, 0, 0, 0, 0),
])
def test_resource_queries_use_total_native_income_and_consumption(
    query, local_income, imported, bonus, unit_demand, power_demand, expected,
):
    lua = LuaRuntime()
    rows = []
    lua.globals().print = lambda value: rows.append(str(value))
    for name, value in dict(local_income=local_income, imported=imported, bonus=bonus,
                            unit_demand=unit_demand, power_demand=power_demand).items():
        lua.globals()[name] = value
    lua.execute('''
Game={GetLocalPlayer=function() return 0 end}
PlayersVisibility={[0]={}}
local res={
 IsResourceVisible=function() return true end,
 GetResourceAmount=function() return 0 end,
 GetResourceStockpileCap=function() return 33 end,
 GetResourceAccumulationPerTurn=function() return local_income end,
 GetResourceImportPerTurn=function() return imported end,
 GetBonusResourcePerTurn=function() return bonus end,
 GetUnitResourceDemandPerTurn=function() return unit_demand end,
 GetPowerResourceDemandPerTurn=function() return power_demand end,
}
Players={[0]={GetResources=function() return res end,
 GetCities=function() return {Members=function() return ipairs({}) end} end}}
GameInfo={Resources=function() local done=false;return function()
 if not done then done=true;return {Index=1,ResourceType="RESOURCE_URANIUM",ResourceClassType="RESOURCECLASS_STRATEGIC"} end
end end}
Map={GetGridSize=function() return 0,0 end}
''')
    lua.execute(query())
    stock, = lq.parse_stockpile_response(rows)
    empire_stock, = lq.parse_empire_resources_response(rows)[0]
    assert stock == empire_stock
    assert stock.per_turn == local_income + imported + bonus
    assert stock.demand == unit_demand + power_demand
    assert stock.imported == imported
    assert stock.per_turn - stock.demand == expected
    report = GameState._build_turn_report(1, 2, [], [], [replace(stock, amount=1)])
    assert f'URANIUM 1/33 ({expected:+d}/t)' in report
    before = lq.TurnSnapshot(turn=1, units={}, cities={}, current_research='', current_civic='',
                             stockpiles=[replace(stock, amount=1)])
    after = replace(before, turn=2, stockpiles=[stock])
    event, = GameState._diff_snapshots(before, after)
    assert f'({expected:+d}/t)' in event.message

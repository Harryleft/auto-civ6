"""Named entity queries reconcile one siege receipt through real Runtime/Lua."""

import asyncio
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent

from civ_mcp import server
from civ_mcp.devtools.debug import DebugSession
from civ_mcp.devtools.evidence import Evidence
from test_attack_mode_lua import state_for
from test_unit_attack_city import terminal_capture_conn


def queryable_capture():
    conn = terminal_capture_conn()
    conn.is_connected = True
    # Native read APIs at the connection boundary. Both entity queries still
    # execute their production Lua; no GameState/query/confirmation is mocked.
    conn.lua.execute('''
capture_change=function() winner=-1 end
unitInfo.UnitType='UNIT_DESTROYER';unitInfo.Hash=1;enemyInfo.Hash=2
unit.GetName=function() return 'Destroyer' end
unit.GetBuildCharges=function() return 0 end
unit.GetGreatPerson=function() return nil end
unit.GetMaxMoves=function() return 2 end
local function rows(t)
 return setmetatable(t,{__call=function(self)
  local key=nil
  return function() local value;key,value=next(self,key);return value end
 end})
end
GameInfo.Units=rows(GameInfo.Units)
GameInfo.Buildings=rows({});GameInfo.Projects=rows({})
local district={Index=0,Hash=0,DistrictType='DISTRICT_CITY_CENTER'}
GameInfo.Districts=rows({[0]=district,DISTRICT_CITY_CENTER=district})
local function members(value)
 return function() local seen=false;return function()
  if not seen then seen=true;return 1,value end
 end end
end
Players[0].GetCities=function() return {Members=members(city)} end
city.GetPopulation=function() return 9 end
city.GetYield=function() return 0 end
city.GetCulturalIdentity=function() return nil end
city.GetBuildQueue=function() return {GetSize=function() return 0 end} end
city.GetBuildings=function() return {HasBuilding=function() return false end} end
city.GetGrowth=function() return {
 GetAmenities=function() return 2 end,GetHousing=function() return 12 end,
 GetTurnsUntilGrowth=function() return 10 end,GetFoodSurplus=function() return 2 end,
 GetFood=function() return 0 end,GetGrowthThreshold=function() return 20 end
} end
center.IsPillaged=function() return false end
center.IsComplete=function() return true end
local plot=Map.GetPlot
Map.GetPlot=function(px,py)
 local value=plot(px,py);value.GetOwner=function() return -1 end;return value
end
''')
    return conn


class Logger:
    async def log_tool_call(self, *_args):
        pass


class LocalTransport:
    """In-process MCP boundary: real named-command schemas and tool handlers."""

    def __init__(self, game):
        self.ctx = SimpleNamespace(request_context=SimpleNamespace(
            lifespan_context=server.AppContext(game=game, logger=Logger())))
        self.calls = []

    async def list_tools(self):
        return ListToolsResult(tools=await server.mcp.list_tools())

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        # No connection or new MCP process can be constructed in this adapter.
        assert name in {'get_runtime_status', 'unit_action', 'get_units', 'get_cities'}
        result = await getattr(server, name)(ctx=self.ctx, **arguments)
        return CallToolResult(content=[TextContent(type='text', text=result)])


@pytest.mark.parametrize('command,tool,expected', [
    ('unit list', 'get_units', 'at (20,16)'),
    ('city list', 'get_cities', 'at (19,16)'),
])
@pytest.mark.parametrize('terminal_arrives', [True, False])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_named_entity_query_confirms_delayed_conquest_without_another_attack(
        tmp_path, command, tool, expected, terminal_arrives):
    conn = queryable_capture()
    game = state_for(conn)
    transport = LocalTransport(game)
    events = []
    session = DebugSession(transport, Evidence(tmp_path, ['terminal-query']),
                           {'tool_calls': 0}, events.append, offline=False)

    async def scenario():
        await session.start()
        await session.execute_command('unit attack 8 19 16')
        await session.execute_command('pending')
        assert len(conn.operations) == 1, events
        assert game.pending_action_summary is not None
        attack = next(row for row in events if row.get('tool') == 'unit_action'
                      and row['event'] == 'tool_result')
        assert attack['error']['code'] == 'OUTCOME_UNKNOWN'
        query_start = len(transport.calls)
        if terminal_arrives:
            conn.lua.execute('winner=7')
        await session.execute_command(command)
        await session.execute_command('pending')
        await session.execute_command(command)
        # A later entity query cannot invoke the attack entrypoint or submit
        # movement, even when it retires the original request.
        assert set(transport.calls[query_start:]) <= {'get_runtime_status', tool}

    asyncio.run(scenario())
    pending = [row['runtime'] for row in events if row['event'] == 'pending']
    assert [row['has_pending'] for row in pending] == [True, not terminal_arrives]
    assert len({(row['session_id'], row['process_id']) for row in pending}) == 1
    assert len(conn.operations) == 1
    assert (game.pending_action_summary is None) is terminal_arrives
    assert (conn.lua.globals().x, conn.lua.globals().y) == (20, 16)
    results = [row for row in events if row.get('tool') == tool
               and row['event'] == 'tool_result']
    assert len(results) == 2
    result, subsequent = results
    assert not result['is_error'], result
    text = '\n'.join(item['text'] for item in result['result']['content'])
    assert expected in text
    if terminal_arrives:
        assert 'Action confirmation: UNIT_CITY_ATTACK_CONFIRMED|' in text
        assert 'completion:terminal_conquest' in text
    else:
        assert 'Action confirmation:' not in text
        assert 'completion:terminal_conquest' not in text
    subsequent_text = '\n'.join(item['text'] for item in subsequent['result']['content'])
    assert not subsequent['is_error'], subsequent
    assert expected in subsequent_text
    assert 'Action confirmation:' not in subsequent_text
    assert 'completion:terminal_conquest' not in subsequent_text
    assert not any(row['event'] in {'command_blocked', 'tool_outcome_unknown'} for row in events)
    # Preserve the actual adjacent position, not an invented final entry.
    assert not (tool == 'get_units' and 'at (19,16)' in text)


@pytest.mark.parametrize('query,position', [
    ('get_units', (20, 16)), ('get_cities', (19, 16)),
])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_default_entity_queries_preserve_their_entity_return_contract(query, position):
    conn = queryable_capture()
    game = state_for(conn)

    async def scenario():
        assert 'OUTCOME_UNKNOWN' in await game.attack_unit(8, 19, 16)
        conn.lua.execute('winner=7')
        entities = await getattr(game, query)()
        if query == 'get_cities':
            assert isinstance(entities, tuple) and len(entities) == 2
            entities, distances = entities
            assert isinstance(distances, list)
        assert isinstance(entities, list) and len(entities) == 1
        assert (entities[0].x, entities[0].y) == position
        assert game.pending_action_summary is None

    asyncio.run(scenario())
    assert len(conn.operations) == 1

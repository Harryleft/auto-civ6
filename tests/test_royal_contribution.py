"""Royal Society contributions through GameState and the native Lua boundary."""
import asyncio
import pytest

from test_unit_readiness import ReadyConn
from civ_mcp.game_state import GameState

run = asyncio.run


class ContributionConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
builder=units[196608];builder.charges=6
builder.GetType=function() return 1 end
builder.GetBuildCharges=function(self) return self.charges end
GameInfo.Units={[1]={UnitType="UNIT_BUILDER"}}
GameInfo.Buildings={BUILDING_GOV_SCIENCE={Index=8}}
GameInfo.Districts={[2]={DistrictType="DISTRICT_SPACEPORT"}}
project={Index=4,Hash=44,ProjectType="PROJECT_LAUNCH_MOON_LANDING",PrereqDistrict="DISTRICT_SPACEPORT"}
GameInfo.Projects=setmetatable({[4]=project},{__call=function() local done=false;return function() if not done then done=true;return project end end end})
progress=100;cost=1000;current=44;royal=true;allowed=true;cityID=65539;cityOwner=0;districtID=9;districtType=2
plot={GetOwner=function() return cityOwner end,GetIndex=function() return 77 end,GetDistrictType=function() return districtType end}
Map={GetPlot=function() return plot end}
district={GetID=function() return districtID end,GetType=function() return districtType end,GetOwner=function() return cityOwner end,GetX=function() return 19 end,GetY=function() return 17 end}
bq={GetCurrentProductionTypeHash=function() return current end,GetProjectProgress=function(_,id) assert(id==4);return progress end,GetProjectCost=function(_,id) assert(id==4);return cost end}
city={GetID=function() return cityID end,GetOwner=function() return cityOwner end,
 GetBuildings=function() return {HasBuilding=function() return royal end} end,
 GetDistricts=function() return {Members=function() return ipairs({district}) end} end,
 GetBuildQueue=function() return bq end}
Cities={GetPlotPurchaseCity=function() return city end}
Players[0].GetCities=function() return {Members=function() return ipairs({city}) end} end
Players[0].GetUnits=function() return {FindID=function(_,id) return units[id] end} end
GameInfo.UnitCommands={UNITCOMMAND_PROJECT_PRODUCTION={Hash=55}}
UnitManager.CanStartCommand=function(u,hash,visible,results) assert(hash==55 and visible==false and results==true);return allowed end
handlers={removed={},completed={},command={},charges={}}
local function event(key) return {Add=function(h) table.insert(handlers[key],h);if event_on_arm then h(0,196608) end end,
 Remove=function(h) for i=#handlers[key],1,-1 do if handlers[key][i]==h then table.remove(handlers[key],i) end end end} end
Events={UnitRemovedFromMap=event('removed'),CityProjectCompleted=event('completed'),UnitCommandStarted=event('command'),UnitChargesChanged=event('charges')}
function emit(key,...) local args={...};local copy={};for _,h in ipairs(handlers[key]) do table.insert(copy,h) end;for _,h in ipairs(copy) do h(unpack(args)) end end
function applyContribution()
 emit('command',0,196608,55);emit('charges',0,196608,0,builder.charges)
 if remove_builder then units[196608]=nil;emit('removed',0,196608)
 else builder.charges=0 end
 if complete_project then progress=0;current=0;emit('completed',0,65539,4)
 else progress=220 end
end
UnitManager.RequestCommand=function(u,hash,params)
 assert(hash==55 and params==nil,'standard UI command has no parameters')
 table.insert(submissions,{id=u:GetID(),op=hash})
 if fail_request then error('native failure after entry') end
 if not deferred then applyContribution() end
 if fail_after_effect then error('native failure before return') end
end
''')


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', .008)
    monkeypatch.setattr(module, 'ACTION_CONFIRM_INTERVAL', .001)


def test_deferred_contribution_only_confirms_original_request():
    conn = ContributionConn();state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('applyContribution()')
    result = run(state.sacrifice_builder_charges(196608))
    assert result.startswith('SACRIFICED|unit:196608|charges:6')
    assert 'progress:100->220' in result
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('completed', [False, True])
def test_removed_builder_confirms_only_with_bound_project_effect(completed):
    conn = ContributionConn();state = GameState(conn)
    conn.lua.execute('remove_builder=true;complete_project='+str(completed).lower())
    result = run(state.sacrifice_builder_charges(196608))
    assert result.startswith('SACRIFICED|unit:196608|charges:6')
    assert ('completed:event' if completed else 'progress:100->220') in result
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('#handlers.removed + #handlers.completed + #handlers.command + #handlers.charges') == 0
    assert state.pending_action_summary is None


def test_mcp_preserves_full_identity_and_repeat_only_confirms_original():
    from civ_mcp import server
    from test_server import make_ctx, app_of
    conn = ContributionConn();ctx = make_ctx();state = GameState(conn);app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(server.unit_action(ctx, 196608, 'sacrifice_charges'))
    conn.lua.execute('applyContribution()')
    assert run(server.unit_action(ctx, 196608, 'sacrifice_charges')).startswith('SACRIFICED|unit:196608|charges:6')
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('action', ['sleep', 'heal', 'automate'])
def test_mcp_other_units_cannot_cross_unconfirmed_contribution_gate(action):
    from civ_mcp import server
    from test_server import make_ctx, app_of
    conn = ContributionConn();ctx = make_ctx();state = GameState(conn);app_of(ctx).game = state
    conn.lua.execute('deferred=true;addUnit(65537,true,0,0)')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert 'OUTCOME_UNKNOWN' in run(server.unit_action(ctx, 65537, action))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'allowed=false', 'allowed=nil', 'royal=false', 'builder.charges=0', 'builder.moves=0',
    'busy=true', 'busy=nil', 'builder.id=262144', 'builder.GetOwner=function() return 1 end',
    'cityOwner=1', 'current=99', 'project.PrereqDistrict="DISTRICT_CAMPUS"',
    'UnitManager.CanStartCommand=nil', 'UnitManager.RequestCommand=nil',
    'Events=nil', 'Events.CityProjectCompleted=nil', 'Events.UnitRemovedFromMap=nil',
    'progress=nil', 'cost=nil', 'bq.GetProjectProgress=function() error("unknown") end',
])
def test_ineligible_or_missing_native_capabilities_never_submit(setup):
    conn = ContributionConn();state = GameState(conn);conn.lua.execute(setup)
    assert run(state.sacrifice_builder_charges(196608)).startswith('Error:')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('change', [
    'builder.id=262144', 'builder.charges=5', 'builder.moves=0', 'allowed=false',
    'cityID=65540', 'cityOwner=1', 'districtID=10', 'current=99',
    'progress=101', 'seed=99', 'turn=42',
])
def test_prepared_intent_is_revalidated_before_standard_command(change):
    class ChangedConn(ContributionConn):
        async def execute_write(self, source):
            if '-- ACTION_SUBMIT_SCOPE|' in source:
                self.lua.execute(change)
            return await super().execute_write(source)
    conn = ChangedConn();state = GameState(conn)
    assert run(state.sacrifice_builder_charges(196608)).startswith('Error:')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('change', [
    'builder.id=262144', 'builder.GetOwner=function() return 1 end',
    'cityID=65540', 'cityOwner=1', 'districtID=10', 'districtType=3', 'current=99',
    'seed=99', 'turn=42', 'busy=true', 'busy=nil',
    '__civmcpContribution=nil', '__civmcpContribution.token="other"',
    '__civmcpContribution.returned=false', 'bq.GetProjectProgress=function() error("unknown") end',
])
def test_changed_identity_target_scope_or_missing_receipt_retains_gate(change):
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute('applyContribution();'+change)
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert 'OUTCOME_UNKNOWN' in run(state.skip_unit(65537))
    assert 'OUTCOME_UNKNOWN' in run(state.save_game('no_unknown_contribution'))
    assert 'OUTCOME_UNKNOWN' in run(state.end_turn())
    assert state.pending_action_summary and conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('event', [
    '', "emit('removed',0,0)", "emit('removed',1,196608)", "emit('removed',0,262144)",
    "seed=99;emit('removed',0,196608);seed=719665280",
    "turn=42;emit('removed',0,196608);turn=41",
])
def test_gone_builder_and_progress_without_matching_removal_remain_unknown(event):
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute((event+';' if event else '')+'units[196608]=nil;progress=220')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1 and state.pending_action_summary


@pytest.mark.parametrize('event', [
    '', "emit('completed',1,65539,4)", "emit('completed',0,65540,4)",
    "emit('completed',0,65539,5)",
])
def test_consumed_builder_and_empty_queue_require_exact_project_completion(event):
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('command',0,196608,55);emit('charges',0,196608,0,6);units[196608]=nil;emit('removed',0,196608);current=0;progress=0;"+event)
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert state.pending_action_summary
    conn.lua.execute("emit('completed',0,65539,4)")
    assert 'completed:event' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


def test_consumption_and_project_gain_are_independent_required_evidence():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute('progress=220')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute('progress=100;builder.charges=0')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('command',0,196608,55);emit('charges',0,196608,0,6);progress=220")
    assert 'progress:100->220' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('exception', [OSError, asyncio.CancelledError])
@pytest.mark.parametrize('removed', [False, True])
def test_transport_lost_reply_and_cancel_can_confirm_cached_returned_native_request(exception, removed):
    class LostConn(ContributionConn):
        lost = False
        async def execute_write(self, source):
            rows = await super().execute_write(source)
            if 'UnitManager.RequestCommand(unit,state.command)' in source and not self.lost:
                self.lost = True
                raise exception('response lost after native request returned')
            return rows
    conn = LostConn();state = GameState(conn)
    conn.lua.execute('remove_builder='+str(removed).lower())
    if exception is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            run(state.sacrifice_builder_charges(196608))
    else:
        assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert state.pending_action_summary
    assert run(state.sacrifice_builder_charges(196608)).startswith('SACRIFICED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', ['fail_request=true', 'remove_builder=true;fail_after_effect=true'])
def test_exception_inside_native_request_never_replays_or_fabricates_return_receipt(setup):
    conn = ContributionConn();state = GameState(conn);conn.lua.execute(setup)
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute('builder.charges=0;progress=220')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1 and state.pending_action_summary


def test_events_before_request_entry_do_not_attribute_external_removal():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true;event_on_arm=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute('units[196608]=nil;progress=220')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


def test_consumed_identity_cannot_target_reused_index_on_repeat():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('remove_builder=true')
    assert run(state.sacrifice_builder_charges(196608)).startswith('SACRIFICED|')
    conn.lua.execute('replacement=addUnit(262144,true,0,0)')
    assert run(state.sacrifice_builder_charges(196608)).startswith('Error:')
    assert conn.lua.eval('#submissions') == 1


def test_observed_scope_change_latches_even_if_original_world_returns():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute('seed=99')
    assert run(state.get_game_identity()) == ('sumeria', 99)
    conn.lua.execute('seed=719665280;applyContribution()')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('removed', [False, True])
def test_normal_reply_without_native_return_witness_never_confirms(removed):
    class LostWitnessConn(ContributionConn):
        async def execute_write(self, source):
            rows = await super().execute_write(source)
            if 'UnitManager.RequestCommand(unit,state.command)' in source:
                self.lua.execute('__civmcpContribution=nil')
            return rows
    conn = LostWitnessConn();state = GameState(conn)
    conn.lua.execute('remove_builder='+str(removed).lower())
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert state.pending_action_summary and conn.lua.eval('#submissions') == 1


def test_stale_confirmation_frame_does_not_finish_until_both_changes_are_visible():
    class StaleConn(ContributionConn):
        async def execute_write(self, source):
            rows = await super().execute_write(source)
            if source.startswith('local actionMe =') and not self.fresh:
                return [row for row in rows if row.startswith('ACTION_IDENTITY|')]
            return rows
        fresh = False
    conn = StaleConn();state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.fresh = True
    assert run(state.sacrifice_builder_charges(196608)).startswith('SACRIFICED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('pending', ['_pending_end_turn', '_pending_load', '_pending_save'])
def test_pending_turn_or_checkpoint_prevents_contribution(pending):
    conn = ContributionConn();state = GameState(conn);setattr(state, pending, True)
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 0


def test_changed_player_does_not_substitute_other_owners_identical_index():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute('applyContribution();Game.GetLocalPlayer=function() return 1 end;PlayerConfigurations[1]=PlayerConfigurations[0]')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


def test_reports_actual_partial_charge_change_without_assuming_six():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('command',0,196608,55);emit('charges',0,196608,2,6);builder.charges=2;progress=180")
    assert 'charges:4|progress:100->180' in run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


def test_real_memory_mcp_session_repeats_confirmation_without_replaying_command(monkeypatch, tmp_path):
    import anyio
    from mcp import ClientSession
    from civ_mcp import server
    from civ_mcp.logger import GameLogger

    class MemoryConn(ContributionConn):
        async def disconnect(self):
            pass
    conn = MemoryConn();conn.lua.execute('deferred=true')
    monkeypatch.setattr(server, 'GameConnection', lambda: conn)
    monkeypatch.setattr(server, 'GameLogger', lambda: GameLogger(tmp_path / 'actions.jsonl'))

    async def scenario():
        client_write, server_read = anyio.create_memory_object_stream(10)
        server_write, client_read = anyio.create_memory_object_stream(10)
        lowlevel = server.mcp._mcp_server
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(lowlevel.run, server_read, server_write, lowlevel.create_initialization_options())
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                args = {'unit_id': 196608, 'action': 'sacrifice_charges'}
                first = await session.call_tool('unit_action', args)
                assert 'OUTCOME_UNKNOWN' in first.content[0].text
                blocked = await session.call_tool('unit_action', {'unit_id': 65537, 'action': 'sleep'})
                assert 'OUTCOME_UNKNOWN' in blocked.content[0].text
                conn.lua.execute('remove_builder=true;complete_project=true;applyContribution()')
                confirmed = await session.call_tool('unit_action', args)
                assert confirmed.content[0].text.startswith('SACRIFICED|unit:196608|charges:6|completed:event')
            tasks.cancel_scope.cancel()
    anyio.run(scenario)
    assert conn.lua.eval('#submissions') == 1


def test_consumed_userdata_is_never_read_after_native_command_returns():
    conn = ContributionConn();state = GameState(conn)
    conn.lua.execute('''remove_builder=true
local original=UnitManager.RequestCommand
UnitManager.RequestCommand=function(...)
 original(...)
 for key,value in pairs(builder) do if type(value)=="function" then builder[key]=function() error("dead userdata") end end end
end''')
    assert run(state.sacrifice_builder_charges(196608)).startswith('SACRIFICED|unit:196608|charges:6')
    assert conn.lua.eval('#submissions') == 1


def test_mcp_write_without_unit_id_cannot_cross_unconfirmed_contribution():
    from civ_mcp import server
    from test_server import make_ctx, app_of
    conn = ContributionConn();ctx = make_ctx();state = GameState(conn);app_of(ctx).game = state
    conn.lua.execute('''deferred=true;tile_purchases=0
city.GetGold=function() return {GetPlotPurchaseCost=function() return 50 end} end
Players[0].GetTreasury=function() return {GetGoldBalance=function() return 100 end} end
CityCommandTypes={PARAM_PLOT_PURCHASE=1,PARAM_X=2,PARAM_Y=3,PURCHASE=4}
CityManager={GetCity=function() return city end,RequestCommand=function() tile_purchases=tile_purchases+1 end}''')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert 'OUTCOME_UNKNOWN' in run(server.purchase_tile(ctx, 65539, 20, 17))
    assert conn.lua.eval('tile_purchases') == 0
    assert conn.lua.eval('#submissions') == 1


def test_external_removal_and_unrelated_progress_cannot_be_spliced_into_contribution():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("units[196608]=nil;emit('removed',0,196608);progress=101")
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert state.pending_action_summary and conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('events', [
    "emit('charges',0,196608,0,6)",
    "emit('command',0,0,55);emit('charges',0,196608,0,6)",
    "emit('command',1,196608,55);emit('charges',0,196608,0,6)",
    "emit('command',0,262144,55);emit('charges',0,196608,0,6)",
    "emit('command',0,196608,56);emit('charges',0,196608,0,6)",
    "emit('command',0,196608,55)",
    "emit('command',0,196608,55);emit('charges',1,196608,0,6)",
    "emit('command',0,196608,55);emit('charges',0,0,0,6)",
    "emit('command',0,196608,55);emit('charges',0,262144,0,6)",
    "emit('command',0,196608,55);emit('charges',0,196608,0,5)",
    "emit('command',0,196608,55);emit('charges',0,196608,nil,6)",
    "emit('command',0,196608,55);emit('charges',0,196608,6,6)",
    "emit('command',0,196608,55);emit('charges',0,196608,-1,6)",
    "emit('command',0,196608,55);emit('charges',0,196608,0,6);emit('command',0,196608,56)",
    "seed=99;emit('command',0,196608,55);emit('charges',0,196608,0,6);seed=719665280",
])
def test_missing_or_mismatched_command_consumption_events_cannot_attribute_removal(events):
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute(events+";units[196608]=nil;emit('removed',0,196608);progress=101")
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert state.pending_action_summary and conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('capability', [
    'UnitCommandStarted', 'UnitChargesChanged',
    'UnitCommandStarted.Add', 'UnitCommandStarted.Remove',
    'UnitChargesChanged.Add', 'UnitChargesChanged.Remove',
])
def test_missing_command_or_charge_observer_refuses_before_submission(capability):
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('Events.'+capability+'=nil')
    assert run(state.sacrifice_builder_charges(196608)).startswith('Error:')
    assert state.pending_action_summary is None and conn.lua.eval('#submissions') == 0
    assert conn.lua.eval('#handlers.removed+#handlers.completed+#handlers.command+#handlers.charges') == 0


def test_exact_delayed_consumption_event_confirms_removed_builder_with_actual_delta():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('command',0,196608,55);units[196608]=nil;emit('removed',0,196608);progress=101")
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('charges',0,196608,0,6)")
    assert 'SACRIFICED|unit:196608|charges:6|progress:100->101' == run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


def test_charge_event_before_command_started_is_retained_until_exact_command_matches():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('charges',0,196608,0,6);units[196608]=nil;emit('removed',0,196608);progress=220")
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('command',0,196608,55)")
    assert 'SACRIFICED|unit:196608|charges:6|progress:100->220' == run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


def test_unknown_confirmation_exposes_token_bound_native_facts_and_failure_reason():
    import re
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    first = run(state.sacrifice_builder_charges(196608))
    token = re.search(r'CONTRIBUTION_OBSERVED\|([a-f0-9]+)\|unit:196608', first)
    assert token is not None
    assert 'reason:COMMAND_NOT_OBSERVED' in first
    conn.lua.execute("emit('command',0,196608,55);units[196608]=nil;emit('removed',0,196608);progress=220")
    unknown = run(state.sacrifice_builder_charges(196608))
    assert 'CONTRIBUTION_OBSERVED|'+token[1]+'|unit:196608' in unknown
    for fact in ('entered:true', 'returned:true', 'started:true', 'chargeBefore:nil',
                 'chargeAfter:nil', 'removed:true', 'completed:nil', 'invalid:nil',
                 'reason:CHARGES_NOT_OBSERVED'):
        assert fact in unknown
    assert state.pending_action_summary and conn.lua.eval('#submissions') == 1


def test_early_invalid_numeric_charge_event_remains_visible_without_confirming():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('charges',0,196608,-1,6);emit('command',0,196608,55);units[196608]=nil;emit('removed',0,196608);progress=220")
    unknown = run(state.sacrifice_builder_charges(196608))
    for fact in ('chargeBefore:nil', 'chargeAfter:nil', 'chargeEvents:1', 'rawChargeBefore:6',
                 'rawChargeAfter:-1', 'commandSeq:2', 'chargeSeq:1', 'removedSeq:3',
                 'reason:CHARGE_EVENT_MISMATCH'):
        assert fact in unknown
    assert 'OUTCOME_UNKNOWN' in unknown
    assert state.pending_action_summary and conn.lua.eval('#submissions') == 1


def test_successful_query_also_reports_native_event_sequence_without_extra_command():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('remove_builder=true;complete_project=true')
    assert run(state.sacrifice_builder_charges(196608)).startswith('SACRIFICED|')
    facts = next(row for row in conn.rows if row.startswith('CONTRIBUTION_OBSERVED|'))
    for fact in ('chargeBefore:6', 'chargeAfter:0', 'chargeEvents:1', 'rawChargeBefore:6',
                 'rawChargeAfter:0', 'commandSeq:1', 'chargeSeq:2', 'removedSeq:3',
                 'completedSeq:4', 'reason:CONFIRMED'):
        assert fact in facts
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('charges,counter_before', [(6, 6), (4, 10)])
def test_royal_counter_increase_for_all_remaining_charges_confirms_exact_removed_builder(charges, counter_before):
    conn = ContributionConn();state = GameState(conn)
    conn.lua.execute(f'deferred=true;builder.charges={charges}')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute(f"emit('charges',0,196608,{counter_before+charges},{counter_before});units[196608]=nil;emit('removed',0,196608);emit('command',0,196608,55);progress=220")
    assert f'SACRIFICED|unit:196608|charges:{charges}|progress:100->220' == run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


def test_present_builder_uses_remaining_charge_readback_to_check_counter_delta():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('charges',0,196608,12,8);emit('command',0,196608,55);progress=180")
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute('builder.charges=2')
    assert 'SACRIFICED|unit:196608|charges:4|progress:100->180' == run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('raw_after,raw_before', [
    ('7', '6'), ('11', '6'), ('13', '6'), ('6', '6'),
    ('6.5', '0.5'), ('-6', '0'), ('6', '-6'),
    ('math.huge', '6'), ('0/0', '6'), ('nil', '6'), ('"12"', '6'),
])
def test_removed_builder_rejects_wrong_or_invalid_counter_delta_despite_project_growth(raw_after, raw_before):
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute(f"emit('charges',0,196608,{raw_after},{raw_before});units[196608]=nil;emit('removed',0,196608);emit('command',0,196608,55);progress=101")
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert state.pending_action_summary and conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change', [
    'builder.charges=3;progress=220',
    'units[196608]=nil;progress=220',
    "units[196608]=nil;emit('removed',0,196608);progress=100",
    "units[196608]=nil;emit('removed',0,196608);emit('command',0,196608,56);progress=220",
])
def test_counter_delta_alone_never_replaces_remaining_removal_or_project_evidence(change):
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('charges',0,196608,12,6);emit('command',0,196608,55);"+change)
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    assert state.pending_action_summary and conn.lua.eval('#submissions') == 1


def test_counter_increase_still_confirms_exact_completed_project_after_removal():
    conn = ContributionConn();state = GameState(conn);conn.lua.execute('deferred=true;builder.charges=5')
    assert 'OUTCOME_UNKNOWN' in run(state.sacrifice_builder_charges(196608))
    conn.lua.execute("emit('charges',0,196608,12,7);units[196608]=nil;emit('removed',0,196608);emit('command',0,196608,55);progress=0;current=0;emit('completed',0,65539,4)")
    assert 'SACRIFICED|unit:196608|charges:5|completed:event' == run(state.sacrifice_builder_charges(196608))
    assert conn.lua.eval('#submissions') == 1

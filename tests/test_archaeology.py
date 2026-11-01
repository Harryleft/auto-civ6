"""Public archaeology operations execute Lua 5.1 against native API fixtures."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_unit_effect_confirmation import EffectConn


class ArchaeologyConn(EffectConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
u.kind=7;extracting=false
GameInfo.Units[7]={UnitType='UNIT_ARCHAEOLOGIST'}
GameInfo.UnitOperations.UNITOPERATION_EXCAVATE={Hash=70}
GameInfo.Eras={[0]={EraType='ERA_ANCIENT'}}
artifact={ActingPlayerID=0,TargetPlayerID=1,ActingPlayerEra=0,Type=3}
u.GetArchaeology=function() return {GetArtifactIndex=function() return 8 end} end
local oldUnits=Players[0].GetUnits
Players[0].GetUnits=function()
 local t=oldUnits();t.GetNextExtractingArchaeologist=function() if extracting then return u end end;return t
end
Game.GetArtifactByIndex=function(i) assert(i==8);return artifact end
Events.GreatWorkCreated=event()
PlayerOperations={CHOOSE_ARTIFACT_PLAYER=71,PARAM_PLAYER_ONE='player'}
GameInfo.Buildings=setmetatable({[20]={Index=20}}, {__call=function()local done=false;return function()if not done then done=true;return {Index=20} end end end})
GameInfo.GreatWorks={[5]={GreatWorkObjectType='GREATWORKOBJECT_ARTIFACT',EraType='ERA_ANCIENT'}}
city.owner=0;storedWork=-1;chosenPlayer=-1
city.GetBuildings=function() return {
 HasBuilding=function(_,b)return b==20 end,
 GetNumGreatWorkSlots=function()return 1 end,
 GetGreatWorkInSlot=function()return storedWork end,
 GetGreatWorkTypeFromIndex=function(_,index)assert(index==900);return 5 end} end
Players[0].GetCities=function()return {Members=function()return pairs({city})end}end
Game.GetGreatWorkPlayer=function(index)assert(index==900);return chosenPlayer end
function applyExcavation()
 Events.UnitOperationStarted.Fire(0,196608,70);extracting=true
end
function applyChoice()
 extracting=false;storedWork=900;chosenPlayer=selectedPlayer
 Events.GreatWorkCreated.Fire(0,196608,city.x,city.y,20,900)
 if consume then removeUnit() end
end
UnitManager.CanStartOperation=function(actor,op,unused,testVisible)
 assert(actor==u and op==70 and unused==nil and testVisible==false);return u.allowed
end
UnitManager.RequestOperation=function(actor,op,...)
 assert(actor==u and op==70 and select('#',...)==0)
 table.insert(submissions,{id=actor.id,op=op})
 if not deferred then applyExcavation() end
end
UI.RequestPlayerOperation=function(owner,op,params)
 assert(owner==0 and op==71 and (params.player==0 or params.player==1))
 table.insert(submissions,{id=u.id,op=op});selectedPlayer=params.player
 if not deferred then applyChoice() end
end
''')


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_excavate_then_explicit_artifact_choice_requires_new_native_work_in_owned_slot():
    conn = ArchaeologyConn(); state = GameState(conn); ctx = make_ctx(game=state)
    assert 'NO_ARTIFACT_CHOICE' in asyncio.run(server.get_artifact_choices(ctx))
    result = asyncio.run(server.unit_action(ctx, 196608, 'excavate'))
    assert result.startswith('EXCAVATED|unit:196608|artifact:8|era:0|type:3|acting_player:0|target_player:1')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('storedWork') == -1
    assert 'ARTIFACT_CHOICE|' in asyncio.run(server.get_artifact_choices(ctx))
    result = asyncio.run(server.choose_artifact(ctx, 196608, 8, 1))
    assert result.startswith('ARTIFACT_CHOSEN|unit:196608|artifact:8|player:1|work:900|city:65539|building:20|slot:0')
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 2


@pytest.mark.parametrize('kind', ['excavate', 'choose'])
def test_lost_reply_same_intent_confirms_only_and_plain_query_never_releases_gate(kind):
    conn = ArchaeologyConn(); state = GameState(conn); ctx = make_ctx(game=state)
    if kind == 'choose':
        conn.lua.execute('extracting=true')
    async def invoke():
        if kind == 'excavate':
            return await server.unit_action(ctx, 196608, 'excavate')
        return await server.choose_artifact(ctx, 196608, 8, 1)
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(invoke())
    original = state.pending_action_summary
    asyncio.run(server.get_artifact_choices(ctx))
    assert state.pending_action_summary == original
    assert conn.lua.eval('#submissions') == 1
    assert asyncio.run(invoke()).startswith('EXCAVATED|' if kind == 'excavate' else 'ARTIFACT_CHOSEN|')
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_deferred_choice_blocks_changed_choice_and_other_writes_without_resubmitting():
    conn = ArchaeologyConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('extracting=true;deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.choose_artifact(ctx, 196608, 8, 1))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.choose_artifact(ctx, 196608, 8, 0))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'excavate'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(196608))
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('applyChoice()')
    assert asyncio.run(server.choose_artifact(ctx, 196608, 8, 1)).startswith('ARTIFACT_CHOSEN|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change', ['u.allowed=false', 'u.kind=1', 'u.id=262144', 'extracting=true'])
def test_excavation_native_preflight_refuses_without_submission(change):
    conn = ArchaeologyConn(); state = GameState(conn); conn.lua.execute(change)
    assert 'REFUSED' in asyncio.run(state.excavate_unit(196608))
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('unit,index,player', [(262144, 8, 1), (196608, 9, 1), (196608, 8, 2)])
def test_wrong_selection_identity_or_player_never_submits(unit, index, player):
    conn = ArchaeologyConn(); state = GameState(conn); conn.lua.execute('extracting=true')
    assert 'REFUSED' in asyncio.run(state.choose_artifact(unit, index, player))
    assert conn.lua.eval('#submissions') == 0


def test_single_native_choice_rejects_target_and_last_charge_removal_is_confirmable():
    conn = ArchaeologyConn(); state = GameState(conn)
    conn.lua.execute('extracting=true;artifact.Type=2;artifact.TargetPlayerID=-1;consume=true')
    assert 'target_player:-1' in asyncio.run(state.get_artifact_choices())
    assert 'REFUSED' in asyncio.run(state.choose_artifact(196608, 8, 1))
    assert asyncio.run(state.choose_artifact(196608, 8, 0)).startswith('ARTIFACT_CHOSEN|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change', [
    'storedWork=-1', 'chosenPlayer=0', "GameInfo.GreatWorks[5].GreatWorkObjectType='GREATWORKOBJECT_WRITING'",
    "GameInfo.GreatWorks[5].EraType='ERA_CLASSICAL'", 'city.owner=1', 'extracting=true',
    '__civmcpArchaeology.work=nil',
])
def test_incomplete_or_wrong_native_artifact_evidence_remains_pending(change):
    conn = ArchaeologyConn(); state = GameState(conn)
    conn.lua.execute('extracting=true;deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_artifact(196608, 8, 1))
    conn.lua.execute('applyChoice();' + change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_artifact(196608, 8, 1))
    assert state.pending_action_summary is not None
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change,restore', [('u.id=262144', 'u.id=196608'), ('turn=42', 'turn=41'), ('seed=9', 'seed=719665280')])
def test_changed_scope_or_actor_stays_invalid_after_restoration(change, restore):
    conn = ArchaeologyConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.excavate_unit(196608))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.excavate_unit(196608))
    conn.lua.execute(restore + ';applyExcavation()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.excavate_unit(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('line,tool,args', [
    ('unit excavate 196608 --dry-run', 'unit_action', {'unit_id': 196608, 'action': 'excavate'}),
    ('unit artifact-choices', 'get_artifact_choices', {}),
    ('unit choose-artifact 196608 8 1 --dry-run', 'choose_artifact', {'unit_id': 196608, 'artifact_index': 8, 'player_id': 1}),
])
def test_named_commands_validate_against_real_tool_schema_without_game(line, tool, args):
    from civ_mcp.devtools.commands import parse_command
    from jsonschema import validate
    intent = parse_command(line)
    assert intent.tool == tool and intent.arguments == args
    native = next(t for t in server.mcp._tool_manager.list_tools() if t.name == tool)
    validate(intent.arguments, native.parameters)
    assert intent.dry_run == ('--dry-run' in line)


def test_plain_unit_and_city_queries_do_not_consume_a_resolvable_archaeology_receipt(monkeypatch):
    conn = ArchaeologyConn(); state = GameState(conn)
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.excavate_unit(196608))
    original = state.pending_action_summary
    # Only unrelated entity serialization is stubbed; the real pending owner and
    # native archaeological receipt are live throughout these ordinary reads.
    monkeypatch.setattr('civ_mcp.game_state.lq.build_units_query', lambda: 'print("entity units")')
    monkeypatch.setattr('civ_mcp.game_state.lq.build_cities_query', lambda: 'print("entity cities")')
    from types import SimpleNamespace
    monkeypatch.setattr('civ_mcp.game_state.observe_units_response', lambda _: SimpleNamespace(require_complete=lambda: []))
    monkeypatch.setattr('civ_mcp.game_state.lq.parse_cities_response', lambda _: ([], []))
    asyncio.run(state.get_units());asyncio.run(state.get_cities())
    assert state.pending_action_summary == original
    assert conn.lua.eval('#submissions') == 1
    assert asyncio.run(state.excavate_unit(196608)).startswith('EXCAVATED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('kind', ['excavate', 'choose'])
@pytest.mark.parametrize('other', ['server_sleep', 'server_excavate_other', 'server_choice_changed',
                                  'server_choice_other_unit', 'server_city_focus', 'direct_skip', 'direct_choice_changed'])
def test_resolvable_lost_reply_cannot_be_confirmed_by_a_different_write(kind, other):
    conn = ArchaeologyConn(); state = GameState(conn); ctx = make_ctx(game=state)
    if kind == 'choose':
        conn.lua.execute('extracting=true')
    async def original():
        if kind == 'excavate':
            return await server.unit_action(ctx, 196608, 'excavate')
        return await server.choose_artifact(ctx, 196608, 8, 1)
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(original())
    pending = state.pending_action_summary
    receipt = state._actions.pending
    # Both mutations have completed in the engine; only transport lost the reply.
    assert conn.lua.eval('extracting' if kind == 'excavate' else 'storedWork==900')
    async def different():
        if other == 'server_sleep':
            return await server.unit_action(ctx, 196608, 'sleep')
        if other == 'server_excavate_other':
            return await server.unit_action(ctx, 262144, 'excavate')
        if other == 'server_choice_changed':
            return await server.choose_artifact(ctx, 196608, 8, 0)
        if other == 'server_choice_other_unit':
            return await server.choose_artifact(ctx, 262144, 8, 1)
        if other == 'server_city_focus':
            return await server.set_city_focus(ctx, 65539, 'science')
        if other == 'direct_skip':
            return await state.skip_unit(196608)
        return await state.choose_artifact(196608, 8, 0)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(different())
    assert state._actions.pending is receipt
    assert state.pending_action_summary == pending
    assert conn.lua.eval('#submissions') == 1
    assert asyncio.run(original()).startswith('EXCAVATED|' if kind == 'excavate' else 'ARTIFACT_CHOSEN|')
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1


def test_explicit_confirmation_can_retire_resolvable_archaeology_pending():
    conn = ArchaeologyConn(); state = GameState(conn)
    conn.lua.execute('extracting=true');conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_artifact(196608, 8, 1))
    assert asyncio.run(state.confirm_pending_action()).startswith('ARTIFACT_CHOSEN|')
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change', [
    'turn=42', 'u.x=20', 'artifact.ActingPlayerEra=1', 'artifact.TargetPlayerID=2',
    'artifact.ActingPlayerID=2', 'artifact.Type=1',
    '''local original=Players[0].GetUnits
       local other={GetOwner=function()return 0 end,GetID=function()return 262145 end,
        GetX=function()return 19 end,GetY=function()return 17 end,GetArchaeology=u.GetArchaeology}
       Players[0].GetUnits=function()local t=original();t.GetNextExtractingArchaeologist=function()return other end;return t end''',
    'u.GetArchaeology=function()return {GetArtifactIndex=function()return 9 end}end;Game.GetArtifactByIndex=function()return artifact end',
])
def test_choice_target_and_scope_change_during_slot_read_refuses_before_submission(change):
    conn = ArchaeologyConn(); state = GameState(conn)
    conn.lua.execute('''extracting=true
local original=city.GetBuildings
city.GetBuildings=function()
 local b=original();local getSlots=b.GetNumGreatWorkSlots
 b.GetNumGreatWorkSlots=function(...) ''' + change + ''';return getSlots(...) end
 return b
end
''')
    assert 'REFUSED' in asyncio.run(state.choose_artifact(196608, 8, 1))
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None
    assert conn.lua.eval('Events.UnitOperationStarted.Count()+Events.UnitRemovedFromMap.Count()+Events.GreatWorkCreated.Count()') == 0


@pytest.mark.parametrize('kind', ['excavate', 'choose'])
@pytest.mark.parametrize('change', ['turn=42', 'u.x=20', 'u.id=262144'])
def test_scope_or_actor_changed_during_observer_installation_refuses_before_submission(kind, change):
    conn = ArchaeologyConn(); state = GameState(conn)
    conn.lua.execute(('extracting=true;' if kind == 'choose' else '') + '''
local add=Events.GreatWorkCreated.Add
Events.GreatWorkCreated.Add=function(handler) add(handler);''' + change + ''' end
''')
    result = asyncio.run(state.choose_artifact(196608, 8, 1) if kind == 'choose' else state.excavate_unit(196608))
    assert 'REFUSED' in result
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None
    assert conn.lua.eval('Events.UnitOperationStarted.Count()+Events.UnitRemovedFromMap.Count()+Events.GreatWorkCreated.Count()') == 0


@pytest.mark.parametrize('change', ['u.allowed=false', 'extracting=true'])
def test_excavation_rechecks_eligibility_and_no_choice_after_installing_observers(change):
    conn = ArchaeologyConn(); state = GameState(conn)
    conn.lua.execute('''local add=Events.GreatWorkCreated.Add
Events.GreatWorkCreated.Add=function(handler)add(handler);''' + change + ''' end''')
    assert 'REFUSED' in asyncio.run(state.excavate_unit(196608))
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('entrypoint', ['dismiss_popup', 'restart_and_load'])
def test_explicit_popup_and_recovery_bypass_archaeology_gate_without_implicit_confirmation(monkeypatch, entrypoint):
    conn = ArchaeologyConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('extracting=true');conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.choose_artifact(ctx, 196608, 8, 1))
    pending = state._actions.pending
    called = []
    async def explicit_operation(*args):
        called.append(args)
        return 'EXPLICIT_OPERATION_ENTERED'
    # Only the permitted explicit lifecycle action is isolated. The dispatch
    # and already-confirmable archaeological receipt remain real; no live
    # popup dismissal, process restart or save loading occurs in this test.
    monkeypatch.setattr(state, entrypoint, explicit_operation)
    if entrypoint == 'dismiss_popup':
        result = asyncio.run(server.dismiss_popup(ctx))
        assert called == [()]
    else:
        result = asyncio.run(server.restart_and_load(ctx, 'isolated-checkpoint'))
        assert called == [('isolated-checkpoint',)]
    assert result == 'EXPLICIT_OPERATION_ENTERED'
    assert state._actions.pending is pending
    assert conn.lua.eval('#submissions') == 1
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'sleep'))
    assert state._actions.pending is pending
    assert asyncio.run(server.choose_artifact(ctx, 196608, 8, 1)).startswith('ARTIFACT_CHOSEN|')
    assert conn.lua.eval('#submissions') == 1

"""Public formation entrypoints execute real Lua; only native APIs are simulated."""
import asyncio
import json

import pytest
from jsonschema import validate

from civ_mcp import server
from civ_mcp.game_state import GameState
from civ_mcp.devtools.commands import parse_command
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class FormationConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.cancel_reply = False
        self.lua.execute('''
function event()
 local listeners={}
 return {Add=function(f)listeners[f]=true end,Remove=function(f)listeners[f]=nil end,
 Fire=function(...)for f in pairs(listeners)do f(...)end end}
end
Events={UnitCommandStarted=event(),UnitEnterFormation=event(),UnitExitFormation=event(),
 UnitFormCorps=event(),UnitFormArmy=event(),UnitRemovedFromMap=event()}
UnitCommandTypes={ENTER_FORMATION=10,EXIT_FORMATION=11,FORM_CORPS=12,FORM_ARMY=13,
 PARAM_UNIT_PLAYER='owner',PARAM_UNIT_ID='target'}
UnitCommandResults={UNITS='units'}
MilitaryFormationTypes={CORPS_FORMATION=1,ARMY_FORMATION=2}
local a=units[196608];local b=addUnit(196609,true,0,0)
for _,u in pairs(units)do
 u.kind=1;u.military=0;u.count=1
 u.GetType=function(self)return self.kind end
 u.GetMilitaryFormation=function(self)return self.military end
 u.GetFormationUnitCount=function(self)return self.count end
end
nativeAllowed,emitEvents,keepTarget=true,true,false
function candidateList(u)
 local other=u.id==196608 and units[196609]or units[196608]
 return other and {{player=0,id=other.id}}or{}
end
UnitManager.CanStartCommand=function(u,command,params,results)
 if params and params.target then assert(params.owner==0 and params.target~=u.id)end
 return nativeAllowed and (command~=11 or u.count>1),{units=candidateList(u)}
end
UnitManager.GetCommandTargets=function(u,command)return{units=candidateList(u)}end
function fire(name,...)
 if emitEvents then Events[name].Fire(...)end
end
function applyFormation(u,command,params)
 fire('UnitCommandStarted',0,u.id,command)
 local other=params.target and units[params.target]
 if command==10 then
  local count=math.max(1,u.count)+math.max(1,other.count);u.count=count;other.count=count
  fire('UnitEnterFormation',0,u.id,0,other.id)
 elseif command==11 then
  for _,other in pairs(units)do other.count=1 end
  fire('UnitExitFormation',0,u.id,0,196609)
 else
  local survivor=keepTarget and other or u;local consumed=keepTarget and u or other
  survivor.military=command==12 and 1 or 2
  units[consumed.id]=nil
  fire('UnitRemovedFromMap',0,consumed.id)
  fire(command==12 and 'UnitFormCorps'or'UnitFormArmy',0,survivor.id)
  consumed.GetType=function()error('consumed userdata')end
 end
end
UnitManager.RequestCommand=function(u,command,params)
 table.insert(submissions,{id=u.id,command=command,target=params.target})
 if not deferred then applyFormation(u,command,params)end
 if fail_request then error('native failure after entry')end
 return commandResult
end
''')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        rows = await super().execute_write(code)
        if self.lua.eval('#submissions') > before:
            if self.cancel_reply:
                self.cancel_reply = False
                raise asyncio.CancelledError()
            if self.lose_reply:
                self.lose_reply = False
                raise OSError('Native formation applied; reply lost')
        return rows


def ctx_for(state):
    ctx = make_ctx()
    app_of(ctx).game = state
    return ctx


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .005)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


@pytest.mark.parametrize('action', ['link', 'unlink', 'corps', 'army'])
def test_public_mcp_confirms_exact_native_effect(action):
    conn = FormationConn()
    if action == 'unlink':
        conn.lua.execute('units[196608].count=2;units[196609].count=2')
    if action == 'army':
        conn.lua.execute('units[196608].military=1')
    state = GameState(conn)
    result = asyncio.run(server.set_unit_formation(ctx_for(state),196608,action,None if action == 'unlink' else 196609))
    assert result.startswith('FORMATION_CONFIRMED|'+action),result
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None
    if action in {'corps','army'}:
        assert conn.lua.eval('units[196609]') is None
        assert conn.lua.eval('units[196608].military') == (1 if action=='corps'else 2)
    elif action == 'link':
        assert conn.lua.eval('units[196608].count') == 2


@pytest.mark.parametrize('action', ['link','corps','army'])
def test_lost_reply_retains_request_and_never_resubmits(action):
    conn=FormationConn();conn.lose_reply=True
    state=GameState(conn);ctx=ctx_for(state)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_unit_formation(ctx,196608,action,196609))
    assert asyncio.run(server.set_unit_formation(ctx,196608,action,196609)).startswith('FORMATION_CONFIRMED|')
    assert conn.lua.eval('#submissions') == 1


def test_surviving_target_id_is_reported_without_reading_consumed_actor():
    conn=FormationConn();conn.lua.execute('keepTarget=true')
    result=asyncio.run(GameState(conn).change_unit_formation(196608,'corps',196609))
    assert result == 'FORMATION_CONFIRMED|corps|unit:196609'


@pytest.mark.parametrize('setup',[
 'nativeAllowed=false','UnitManager.GetCommandTargets=nil','UnitCommandTypes.FORM_CORPS=nil',
 'Events.UnitFormCorps=nil','units[196609].kind=2','units[196609].id=262145',
 'units[196609].GetOwner=function()return 1 end','busy=true',
 'UnitManager.GetCommandTargets=function()return{units={{player=0,id=196609},{player=0,id=196609}}}end',
])
def test_ineligible_unknown_or_stale_pairs_are_zero_submission_refusals(setup):
    conn=FormationConn();conn.lua.execute(setup);state=GameState(conn)
    result=asyncio.run(state.change_unit_formation(196608,'corps',196609))
    assert 'UNIT_FORMATION_REFUSED' in result,result
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', ['emitEvents=false','fail_request=true','commandResult=false'])
def test_state_change_without_complete_native_receipt_stays_pending(setup):
    conn=FormationConn();conn.lua.execute(setup);state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_unit_formation(196608,'corps',196609))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_unit_formation(196608,'corps',196609))
    assert conn.lua.eval('#submissions') == 1


def test_pending_pair_blocks_changes_to_consumable_target_and_other_intents():
    conn=FormationConn();conn.lua.execute('deferred=true');state=GameState(conn);ctx=ctx_for(state)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_unit_formation(ctx,196608,'corps',196609))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196609,'delete'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_unit_formation(ctx,196609,'army',196608))
    assert conn.lua.eval('#submissions') == 1


def test_scope_mismatch_remains_latched_after_original_world_returns():
    conn=FormationConn();conn.lua.execute('deferred=true');state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_unit_formation(196608,'corps',196609))
    conn.lua.execute('seed=1')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute('seed=719665280;applyFormation(units[196608],12,{target=196609})')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_unit_formation(196608,'corps',196609))
    assert conn.lua.eval('#submissions') == 1


def test_cancelled_transport_retains_submitted_pair():
    conn=FormationConn();conn.cancel_reply=True;state=GameState(conn)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.change_unit_formation(196608,'corps',196609))
    assert state.pending_action_summary is not None
    assert asyncio.run(state.change_unit_formation(196608,'corps',196609)).startswith('FORMATION_CONFIRMED|')
    assert conn.lua.eval('#submissions') == 1


def test_formation_query_reports_exact_native_targets_and_no_mutation():
    conn=FormationConn();state=GameState(conn)
    result=json.loads(asyncio.run(server.get_unit_formations(ctx_for(state),196608)))
    assert result['status']=='ready',result
    assert result['data']['actions']['corps']['targets'][0]['id']==196609
    assert result['data']['actions']['unlink']=={'allowed':False,'targets':[]}
    assert conn.lua.eval('#submissions') == 0


def test_missing_formation_api_is_unknown_not_ineligible():
    conn=FormationConn();conn.lua.execute('UnitManager.GetCommandTargets=nil')
    result=asyncio.run(GameState(conn).get_unit_formations(196608))
    assert result['status']=='partial'
    assert result['data']['actions']['corps']=={'allowed':None,'targets':None}


@pytest.mark.parametrize('unit,action,target',[(True,'link',2),(1,'link',1),(1,'army',None),(1,'unlink',2),(1,'invalid',2)])
def test_invalid_input_does_not_access_connection(unit,action,target):
    result=asyncio.run(GameState(object()).change_unit_formation(unit,action,target))
    assert result.startswith('Error: INVALID_FORMATION_ARGUMENT')


@pytest.mark.parametrize('line,tool,args',[
 ('unit delete 196608 --dry-run','unit_action',{'unit_id':196608,'action':'delete'}),
 ('unit alert 196608 --dry-run','unit_action',{'unit_id':196608,'action':'alert'}),
 ('unit cancel-move 196608 --dry-run','unit_action',{'unit_id':196608,'action':'cancel_move'}),
 ('unit formations 196608','get_unit_formations',{'unit_id':196608}),
 ('unit link 196608 196609 --dry-run','set_unit_formation',{'unit_id':196608,'action':'link','target_unit_id':196609}),
 ('unit unlink 196608 --dry-run','set_unit_formation',{'unit_id':196608,'action':'unlink'}),
 ('unit corps 196608 196609 --dry-run','set_unit_formation',{'unit_id':196608,'action':'corps','target_unit_id':196609}),
 ('unit army 196608 196609 --dry-run','set_unit_formation',{'unit_id':196608,'action':'army','target_unit_id':196609}),
 ('unit religious-attack 196608 4 5 --dry-run','unit_action',{'unit_id':196608,'action':'religious_attack','target_x':4,'target_y':5}),
])
def test_named_cli_commands_use_public_schema(line,tool,args):
    intent=parse_command(line)
    assert intent.tool==tool and intent.arguments==args
    native=next(t for t in server.mcp._tool_manager.list_tools()if t.name==tool)
    validate(args,native.parameters)
    assert intent.dry_run==('--dry-run'in line)


def test_read_only_formation_query_preserves_original_pending():
    conn=FormationConn();conn.lua.execute('deferred=true');state=GameState(conn);ctx=ctx_for(state)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_unit_formation(ctx,196608,'link',196609))
    pending=state._actions.pending
    conn.lua.execute('applyFormation(units[196608],10,{target=196609})')
    value=json.loads(asyncio.run(server.get_unit_formations(ctx,196608)))
    assert value['status']=='ready' and value['data']['escort_unit_count']==2
    assert state._actions.pending is pending
    assert asyncio.run(server.set_unit_formation(ctx,196608,'link',196609)).startswith('FORMATION_CONFIRMED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('alter',[
 "fire=function(name,...)if name=='UnitFormCorps'then Events[name].Fire(0,999)else Events[name].Fire(...)end end",
 "fire=function(name,...)if name~='UnitRemovedFromMap'then Events[name].Fire(...)end end",
 "fire=function(name,...)if name=='UnitCommandStarted'then Events[name].Fire(0,196608,999)else Events[name].Fire(...)end end",
])
def test_wrong_or_incomplete_participant_events_cannot_confirm_merge(alter):
    conn=FormationConn();conn.lua.execute(alter);state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_unit_formation(196608,'corps',196609))
    assert conn.lua.eval('#submissions')==1


def test_scope_change_during_native_eligibility_never_submits():
    conn=FormationConn();state=GameState(conn)
    conn.lua.execute('''
local original=UnitManager.CanStartCommand
UnitManager.CanStartCommand=function(...)seed=1;return original(...)end
''')
    assert 'UNIT_FORMATION_REFUSED' in asyncio.run(state.change_unit_formation(196608,'corps',196609))
    assert conn.lua.eval('#submissions')==0


@pytest.mark.parametrize('name',[
 'pillage','found-city','repair','remove-improvement','remove-feature','build-route',
 'automate','sleep','activate','sacrifice-charges','spread-religion','launch-inquisition',
 'condemn-heretic','remove-heresy','trade-route','teleport',
])
def test_other_native_actions_have_named_cli_entrypoints(name):
    target=' 4 5'if name in {'trade-route','teleport'}else''
    intent=parse_command(f'unit {name} 196608{target} --dry-run')
    assert intent.tool=='unit_action' and intent.arguments['action']==name.replace('-','_')
    assert intent.dry_run
    validate(intent.arguments,server.mcp._tool_manager.get_tool('unit_action').parameters)


@pytest.mark.parametrize('action',['corps','army'])
def test_military_component_targets_allow_native_implicit_selected_player(action):
    conn=FormationConn();state=GameState(conn)
    conn.lua.execute('UnitManager.GetCommandTargets=function()return{units={{id=196609}}}end')
    options=asyncio.run(state.get_unit_formations(196608))
    assert options['status']=='ready'
    assert options['data']['actions'][action]['targets'][0]['id']==196609
    assert asyncio.run(state.change_unit_formation(196608,action,196609)).startswith('FORMATION_CONFIRMED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('action',['link','unlink'])
def test_native_unlinked_count_zero_is_not_misclassified_as_a_linked_group(action):
    conn=FormationConn();state=GameState(conn)
    if action=='link':
        conn.lua.execute('units[196608].count=0;units[196609].count=0')
    else:
        conn.lua.execute('''
units[196608].count=2;units[196609].count=2
local apply=applyFormation
applyFormation=function(...)apply(...);units[196608].count=0;units[196609].count=0 end
''')
    result=asyncio.run(state.change_unit_formation(196608,action,None if action=='unlink'else 196609))
    assert result.startswith('FORMATION_CONFIRMED|'),result
    assert conn.lua.eval('#submissions')==1


def test_civilian_no_military_formation_sentinel_still_allows_escort_linking():
    conn=FormationConn();state=GameState(conn)
    conn.lua.execute('units[196609].military=-1')
    value=asyncio.run(state.get_unit_formations(196609))
    assert value['status']=='ready' and value['data']['military_formation']==-1
    assert asyncio.run(state.change_unit_formation(196608,'link',196609)).startswith('FORMATION_CONFIRMED|')

"""Independent native previews use real GameState/Lua and cannot submit attacks."""
import asyncio
import json

import pytest

from civ_mcp import server
from civ_mcp.devtools.commands import parse_command
from civ_mcp.game_state import GameState
from test_attack_mode_lua import AttackConn


def test_combat_preview_has_named_read_only_cli_and_mcp_contract():
    intent=parse_command('unit combat-preview 1245192 19 16')
    assert intent.tool=='get_combat_preview' and not intent.dry_run
    assert intent.arguments=={'unit_id':1245192,'target_x':19,'target_y':16}
    tool=server.mcp._tool_manager.get_tool('get_combat_preview')
    assert tool is not None and tool.annotations.readOnlyHint is True


def test_native_melee_preview_is_independent_of_attack_submission():
    conn=AttackConn();conn.lua.execute('unitInfo.RangedCombat=0;enemyHP=100')
    state=GameState(conn)
    result=json.loads(asyncio.run(state.get_combat_preview(1245192,19,16)))
    assert result['status']=='estimated'
    assert result['attacker']['strength']==50 and result['target']['strength']==55
    assert result['damage_to_attacker']==0 and result['damage_to_target']==5
    assert result['attacker']['id']==1245192 and result['target']['id']==5111813
    assert conn.operations==[] and state.pending_action_summary is None


class PreviewConn(AttackConn):
    def __init__(self):
        super().__init__()
        self.queries=[]
        self.lua.execute('''
unitInfo.RangedCombat=0;enemyHP=100
gold,resourceStock,queuedOrder,popupRequests=123,7,'original',0
UI.RequestPlayerOperation=function()popupRequests=popupRequests+1;error('write forbidden')end
UnitManager.RequestCommand=function()record_operation('command',{});error('write forbidden')end
''')

    async def execute_write(self, code):
        self.queries.append(code)
        return await super().execute_write(code)

    def facts(self):
        return self.lua.eval("table.concat({x,y,moves,attacks,unit:GetMaxDamage()-unit:GetDamage(),enemyHP,gold,resourceStock,queuedOrder,popupRequests},'|')")


def preview(conn,unit=1245192,x=19,y=16):
    state=GameState(conn)
    return json.loads(asyncio.run(state.get_combat_preview(unit,x,y))),state


@pytest.mark.parametrize('mode,setup',[
    ('MELEE',''),('RANGED','unitInfo.RangedCombat=40;allowed.range=true'),
    ('BOMBARD','unitInfo.Bombard=55;allowed.range=true'),
])
def test_native_preview_modes_preserve_all_engine_and_runtime_state(mode,setup):
    conn=PreviewConn();conn.lua.execute(setup);before=conn.facts()
    result,state=preview(conn)
    assert result['status']=='estimated' and result['mode']==mode
    assert result['scope']=={'civilization':'CIVILIZATION_SUMERIA','seed':719665280,'turn':113,'owner':0}
    assert result['eligibility']=='native_current_turn_passed'
    assert result['strength_source']=='Native COMBAT_STRENGTH + STRENGTH_MODIFIER; no base-unit fallback'
    assert result['damage_source']=='Native DAMAGE_TO for each side'
    assert 'does not guarantee actual damage' in result['limitations']
    assert result['attacker']['component']=={'player':0,'id':1245192,'type':0}
    assert result['target']['component']=={'player':2,'id':5111813,'type':0}
    assert conn.facts()==before and conn.operations==[] and len(conn.queries)==2
    assert state.pending_action_summary is None and not state.popup_pending
    assert conn.previews==[{'MELEE':'Melee','RANGED':'Ranged','BOMBARD':'Bombard'}[mode]]


def test_low_hp_and_fractional_native_strength_and_damage_are_not_base_forecasts():
    conn=PreviewConn();conn.lua.execute('''
unit.GetDamage=function()return 49 end
CombatManager.SimulateAttackVersus=function(a,d,mode)
 record_preview(mode or 'Melee')
 return {attacker={id=a,strength=30,modifier=-4.75,damage=39.125},
  defender={id=d,strength=30,modifier=6.5,damage=17.875}}
end
''')
    result,_=preview(conn)
    assert result['attacker']['hp']==51 and result['attacker']['strength']==25.25
    assert result['target']['strength']==36.5
    assert result['damage_to_attacker']==39.125 and result['damage_to_target']==17.875
    assert conn.operations==[]


@pytest.mark.parametrize('setup',[
    'PlayersVisibility[0].IsVisible=function()return false end',
    'PlayersVisibility[0].IsVisible=function()return nil end',
    'PlayersVisibility[0].IsVisible=function()error("unknown")end',
    'PlayersVisibility[0].IsUnitVisible=function()return false end;enemy.GetType=function()error("hidden read")end',
    'PlayersVisibility[0].IsUnitVisible=function()return nil end',
    'PlayersVisibility[0].IsUnitVisible=nil',
    'atPeace=true',
    'enemy.GetX=function()return 99 end',
    'unit.GetOwner=function()return 2 end',
    'unit.GetComponentID=function()return {player=2,id=1245192,type=0}end',
    'enemy.GetComponentID=function()return {player=2,id=5111813}end',
    "unitInfo.Domain='DOMAIN_AIR'",
    'CombatManager=nil',
    'unitInfo.Combat=0;unitInfo.RangedCombat=0',
    'Map.GetUnitsAt=function()return nil end',
])
def test_nonpublic_unbound_unsupported_or_peace_target_is_unknown_without_simulation(setup):
    conn=PreviewConn();conn.lua.execute(setup);before=conn.facts()
    result,state=preview(conn)
    assert result['status']=='unknown' and result['estimate'] is None
    assert 'damage_to_target' not in result and 'strength' not in result
    assert conn.operations==[] and conn.previews==[] and conn.facts()==before
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup,eligibility',[
    ('moves=0','native_current_turn_refused'),
    ('attacks=0','native_current_turn_refused'),
    ('allowed.move=false','native_current_turn_refused'),
    ('movedIntoZOC=true','native_current_turn_refused'),
    ('arrivalTurn=2','native_current_turn_refused'),
    ('UnitManager.CanStartOperation=function()return nil end','unknown'),
    ('UnitManager.GetMoveToPathEx=nil','unknown'),
    ('unitInfo.RangedCombat=40;allowed.range=false','native_current_turn_refused'),
    ('unitInfo.RangedCombat=40;UnitManager.CanStartOperation=function()return nil end','unknown'),
])
def test_native_refusal_and_unavailable_eligibility_are_distinct(setup,eligibility):
    conn=PreviewConn();conn.lua.execute(setup)
    result,state=preview(conn)
    assert result['status']=='unknown' and result['eligibility']==eligibility
    assert conn.operations==[] and conn.previews==[] and state.pending_action_summary is None


@pytest.mark.parametrize('change',[
    'Game.GetCurrentGameTurn=function()return 114 end',
    'GameConfiguration.GetValue=function()return 123 end',
    'Game.GetLocalPlayer=function()return 2 end',
    'PlayerConfigurations[0].GetCivilizationTypeName=function()return "CIVILIZATION_ROME" end',
    'unit.GetID=function()return 999 end',
    'enemy.GetID=function()return 999 end',
    'enemy.GetOwner=function()return 3 end',
    'enemy.GetX=function()return 99 end',
    'moves=1',
    'attacks=0',
    'enemyHP=51',
    'PlayersVisibility[0].IsUnitVisible=function()return false end',
    'UI.IsGameCoreBusy=function()return true end',
])
def test_changes_during_native_simulation_discard_estimate_without_writes(change):
    conn=PreviewConn();conn.lua.execute('''
local simulate=CombatManager.SimulateAttackVersus
CombatManager.SimulateAttackVersus=function(...)
 local result=simulate(...)
''' + change + '''
 return result
end
''')
    result,state=preview(conn)
    assert result['status']=='unknown' and result['estimate'] is None
    assert 'damage_to_target' not in result and conn.operations==[]
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup',[
    'Game.GetCurrentGameTurn=function()return 114 end;error("unavailable")',
    'GameConfiguration.GetValue=function()return 123 end;return false',
])
def test_scope_change_in_eligibility_read_cannot_be_hidden_by_refusal_or_error(setup):
    conn=PreviewConn();conn.lua.execute('UnitManager.CanStartOperation=function()'+setup+' end')
    result,_=preview(conn)
    assert result['status']=='unknown' and result['eligibility']=='unknown'
    assert 'world or turn changed' in result['reason'] and conn.operations==[]


@pytest.mark.parametrize('bad',[
    'result.attacker.id.id=999', 'result.defender.id.id=999',
    'result.defender.damage=nil','result.attacker.modifier=nil',
    'result.defender.damage=0/0','result.attacker.strength=math.huge',
])
def test_malformed_native_estimates_never_become_zero_or_base_values(bad):
    conn=PreviewConn();conn.lua.execute('''
local simulate=CombatManager.SimulateAttackVersus
CombatManager.SimulateAttackVersus=function(...)
 local result=simulate(...)
''' + bad + '''
 return result
end
''')
    result,_=preview(conn)
    assert result['status']=='unknown' and result['estimate'] is None and conn.operations==[]


def test_ambiguous_visible_unit_stack_is_not_automatically_selected():
    conn=PreviewConn();conn.lua.execute('''
Map.GetUnitsAt=function()return {Units=function()
 local i=0;return function()i=i+1;if i<=2 then return enemy end end
end}end
''')
    result,_=preview(conn)
    assert result['status']=='unknown' and 'ambiguous' in result['reason']
    assert conn.previews==[] and conn.operations==[]


@pytest.mark.parametrize('pending_kind',['action','turn','diplomacy','congress','save','replacement','popup'])
def test_every_pending_owner_blocks_preview_before_any_native_io_or_cleanup(pending_kind):
    from civ_mcp.actions import _PendingAction
    from civ_mcp.snapshot import pending_facts
    from test_server import make_ctx
    conn=PreviewConn();state=GameState(conn)
    if pending_kind=='action':state._pending_action=_PendingAction('attack',('unit',1245192),'unread',details={'target':(19,16)})
    elif pending_kind=='turn':state._pending_end_turn=True
    elif pending_kind=='diplomacy':state._diplomacy_progress.response=('original',1,2,3)
    elif pending_kind=='congress':state._congress_progress.request=object()
    elif pending_kind=='save':state._pending_save=True
    elif pending_kind=='replacement':state._pending_load=True
    elif pending_kind=='popup':state._popup_unknown='original'
    before=pending_facts(state);original=state._pending_action
    result=json.loads(asyncio.run(server.get_combat_preview(make_ctx(game=state),1245192,19,16)))
    assert result['status']=='unknown' and 'Pending' in result['reason']
    assert conn.queries==[] and conn.operations==[] and conn.previews==[]
    assert pending_facts(state)==before and state._pending_action is original


@pytest.mark.parametrize('args',[(True,19,16),(-1,19,16),(1245192,19,None),(1245192,19.5,16),(8,19,16)])
def test_invalid_or_reused_actor_identity_cannot_get_bound_estimate(args):
    conn=PreviewConn();result,_=preview(conn,*args)
    assert result['status']=='unknown' and conn.previews==[] and conn.operations==[]


def test_original_known_world_is_required_without_replacing_runtime_identity():
    conn=PreviewConn();state=GameState(conn);state._game_identity=('rome',123)
    result=json.loads(asyncio.run(state.get_combat_preview(1245192,19,16)))
    assert result['status']=='unknown' and state._game_identity==('rome',123)
    assert conn.previews==[] and conn.operations==[]


def test_original_city_center_component_is_bound_for_native_into_preview():
    conn=PreviewConn();conn.lua.execute('''
GameInfo.Districts.DISTRICT_CITY_CENTER={Index=7};DefenseTypes={DISTRICT_GARRISON=0}
local district={GetType=function()return 7 end,GetComponentID=function()return {player=2,id=9,type=1}end,
 GetMaxDamage=function()return 200 end,GetDamage=function()return 25 end}
city={GetOwner=function()return 2 end,GetID=function()return 65539 end,GetX=function()return 19 end,GetY=function()return 16 end,
 GetDistricts=function()return {Members=function()local done=false;return function()if not done then done=true;return 1,district end end end}end}
CityManager.GetCityAt=function()return city end
Map.GetUnitsAt=function()return nil end
CombatManager.SimulateAttackInto=function(a,mode,tx,ty)
 assert(mode==nil and tx==19 and ty==16);record_preview('CityMelee')
 return {attacker={id=a,strength=42,modifier=1,damage=23},
  defender={id={player=2,id=9,type=1},strength=40,modifier=2,damage=31}}
end
''')
    result,state=preview(conn)
    assert result['status']=='estimated' and result['target']['kind']=='city'
    assert result['target']['id']==65539 and result['target']['hp']==175
    assert result['target']['component']=={'player':2,'id':9,'type':1}
    assert conn.previews==['CityMelee'] and conn.operations==[] and state.pending_action_summary is None


@pytest.mark.parametrize('tamper',[
    lambda rows:rows+[rows[0]],
    lambda rows:[rows[0].replace('COMBAT_PREVIEW_BINDING|','COMBAT_PREVIEW_BINDING|wrong')],
    lambda rows:[rows[0].replace('|1245192|20|16|','|999|20|16|')],
    lambda rows:[rows[0].replace('|unit|2|5111813|19|16|','|unit|2|5111813|99|16|')],
    lambda rows:[rows[0].replace('|MELEE|','|AIR|')],
    lambda rows:[rows[0].splitlines()[-1]],
])
def test_missing_duplicate_or_tampered_native_binding_stays_unknown(tamper):
    conn=PreviewConn();execute=conn.execute_write
    async def altered(code):
        rows=await execute(code)
        return tamper(rows) if any(row.startswith('COMBAT_PREVIEW_BINDING|') for row in rows) else rows
    conn.execute_write=altered
    result,state=preview(conn)
    assert result['status']=='unknown' and result['estimate'] is None
    assert conn.operations==[] and state.pending_action_summary is None


def test_scope_change_between_probe_and_preview_never_simulates_new_world():
    conn=PreviewConn();execute=conn.execute_write
    async def changed(code):
        rows=await execute(code)
        if 'print("ACTION_SCOPE|' in code:
            conn.lua.execute('Game.GetCurrentGameTurn=function()return 114 end')
        return rows
    conn.execute_write=changed
    result,_=preview(conn)
    assert result['status']=='unknown' and conn.previews==[] and conn.operations==[]


STDIO_DRIVER = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from test_combat_preview import PreviewConn
from civ_mcp import server
conn=PreviewConn();conn.is_connected=True
before=conn.facts()
def persist():
 Path(sys.argv[1]).write_text(json.dumps({'before':before,'after':conn.facts(),
  'operations':conn.operations,'previews':conn.previews,'queries':len(conn.queries),
  'pending':game.pending_action_summary,'popup_pending':game.popup_pending}))
async def disconnect():persist()
conn.disconnect=disconnect
server.GameConnection=lambda:conn
game=server.GameState(conn)
server.GameState=lambda connection:game
class Logger:
 async def log_tool_call(self,*args):persist()
server.GameLogger=Logger
server.main()
'''


@pytest.mark.parametrize('shared',[False,True])
def test_named_preview_real_stdio_schema_dry_run_and_shared_owner_gate(tmp_path,shared):
    from io import StringIO
    from pathlib import Path
    import sys
    from mcp import StdioServerParameters
    from civ_mcp.devtools.debug import serve
    from civ_mcp.devtools.evidence import Evidence
    project=Path(__file__).resolve().parents[1]
    driver=tmp_path/'driver.py';driver.write_text(STDIO_DRIVER)
    receipt=tmp_path/'receipt.json'
    pending_path=tmp_path/'shared-pending.json'
    if shared:pending_path.write_text('{"original":"held"}')
    evidence=Evidence(project,['combat-preview-stdio'],str(tmp_path/'evidence'))
    events=[];state={}
    command='unit combat-preview 1245192 19 16'
    status=asyncio.run(serve(evidence,state,offline=False,timeout=5,
        source=StringIO(command+' --dry-run\n'+command+'\npending\nquit --abandon\n'),emit=events.append,
        server=StdioServerParameters(command=sys.executable,args=[str(driver),str(receipt),str(project/'tests')],cwd=str(project)),
        shared_pending_path=pending_path if shared else None))
    engine=json.loads(receipt.read_text())
    dry=next(row for row in events if row['event']=='dry_run')
    assert dry['scope']=='schema_only' and dry['would_send'] is False
    assert dry['potentially_mutating'] is False and dry['pending_checked'] is False
    assert engine['before']==engine['after'] and engine['operations']==[] and engine['pending'] is None
    if shared:
        assert status=='BLOCKED' and engine['queries']==0 and engine['previews']==[]
        assert any(row.get('code')=='VERIFY_PENDING' for row in events)
        assert pending_path.read_text()=='{"original":"held"}'
    else:
        assert status=='PASS' and engine['queries']==2 and engine['previews']==['Melee']
        result=next(row for row in events if row['event']=='tool_result' and row['tool']=='get_combat_preview')
        text=next(item['text'] for item in result['result']['content'] if item['type']=='text')
        assert json.loads(text)['status']=='estimated' and state['tool_calls']==1


@pytest.mark.parametrize('after',['scope','preview'])
def test_pending_appearing_during_query_is_preserved_and_estimate_discarded(after):
    conn=PreviewConn();state=GameState(conn);execute=conn.execute_write
    async def appeared(code):
        rows=await execute(code)
        if (after=='scope' and 'print("ACTION_SCOPE|' in code) or (after=='preview' and 'local function preview()' in code):
            state._pending_save=True
        return rows
    conn.execute_write=appeared
    result=json.loads(asyncio.run(state.get_combat_preview(1245192,19,16)))
    assert result['status']=='unknown' and state.save_pending
    assert conn.operations==[] and len(conn.queries)==(1 if after=='scope' else 2)
    assert len(conn.previews)==(0 if after=='scope' else 1)


@pytest.mark.parametrize('mode',['MELEE','RANGED'])
@pytest.mark.parametrize('change',[
    'unit.GetID=function()return 999 end',
    'unit.GetOwner=function()return 2 end',
    'PlayersVisibility[0].IsVisible=function()return nil end',
    'PlayersVisibility[0].IsVisible=function()return false end',
])
def test_native_refusal_requires_final_actor_and_visible_target_binding(mode,change):
    conn=PreviewConn()
    if mode=='RANGED':conn.lua.execute('unitInfo.RangedCombat=40')
    conn.lua.execute('UnitManager.CanStartOperation=function()'+change+';return false end')
    result,state=preview(conn)
    assert result['status']=='unknown' and result['eligibility']=='unknown', result
    assert conn.previews==[] and conn.operations==[] and state.pending_action_summary is None


def test_native_success_cannot_be_combined_with_a_refusal_receipt():
    conn=PreviewConn();execute=conn.execute_write
    async def conflicted(code):
        rows=await execute(code)
        if any(row.startswith('COMBAT_PREVIEW_BINDING|') for row in rows):
            return rows+['COMBAT_PREVIEW_INELIGIBLE|NO_MOVES']
        return rows
    conn.execute_write=conflicted
    result,state=preview(conn)
    assert result['status']=='unknown' and result['eligibility']=='unknown', result
    assert result['estimate'] is None and conn.operations==[] and state.pending_action_summary is None


def test_enemy_city_with_garrison_and_civilian_uses_city_component_not_unit_stack():
    conn=PreviewConn();conn.lua.execute('''
GameInfo.Districts.DISTRICT_CITY_CENTER={Index=7};DefenseTypes={DISTRICT_GARRISON=0}
local district={GetType=function()return 7 end,GetComponentID=function()return {player=2,id=9,type=1}end,
 GetMaxDamage=function()return 200 end,GetDamage=function()return 25 end}
city={GetOwner=function()return 2 end,GetID=function()return 65539 end,GetX=function()return 19 end,GetY=function()return 16 end,
 GetDistricts=function()return {Members=function()local done=false;return function()if not done then done=true;return 1,district end end end}end}
CityManager.GetCityAt=function()return city end
local civilian={};for k,v in pairs(enemy)do civilian[k]=v end
civilian.GetID=function()return 999 end;civilian.GetType=function()return 3 end
GameInfo.Units[3]={UnitType='UNIT_BUILDER',Combat=0}
Map.GetUnitsAt=function()return {Units=function()
 local values={enemy,civilian};local i=0;return function()i=i+1;return values[i]end
end}end
CombatManager.SimulateAttackInto=function(a,mode,tx,ty)
 assert(mode==nil and tx==19 and ty==16);record_preview('CityMelee')
 return {attacker={id=a,strength=42,modifier=1,damage=23},
  defender={id={player=2,id=9,type=1},strength=40,modifier=2,damage=31}}
end
''')
    before=conn.facts();result,state=preview(conn)
    assert result['status']=='estimated' and result['target']['kind']=='city', result
    assert result['target']['id']==65539 and result['target']['hp']==175
    assert conn.previews==['CityMelee'] and conn.operations==[] and conn.facts()==before
    assert state.pending_action_summary is None


def test_target_hp_changing_on_first_read_cannot_publish_stale_initial_hp():
    conn=PreviewConn();conn.lua.execute('''
damageReads=0
enemy.GetDamage=function()
 damageReads=damageReads+1
 if damageReads==1 then enemyHP=51;return 0 end
 return 100-enemyHP
end
''')
    result,state=preview(conn)
    assert result['status']=='unknown' and result['estimate'] is None, result
    assert conn.operations==[] and state.pending_action_summary is None



@pytest.mark.parametrize('extra',['COMBAT_PREVIEW_INELIGIBLE|NO_MOVES','ESTIMATE_UNKNOWN|changed','UNEXPECTED_RECEIPT'])
def test_estimate_requires_exact_mutually_exclusive_receipt_set(extra):
    conn=PreviewConn();execute=conn.execute_write
    async def altered(code):
        rows=await execute(code)
        return rows+[extra] if any(row.startswith('COMBAT_PREVIEW_BINDING|') for row in rows) else rows
    conn.execute_write=altered
    result,state=preview(conn)
    assert result['status']=='unknown' and result['eligibility']=='unknown'
    assert conn.operations==[] and state.pending_action_summary is None


@pytest.mark.parametrize('change',[
    'enemy.GetID=function()return 999 end',
    'enemy.GetOwner=function()return 3 end',
    'enemyHP=51',
    'PlayersVisibility[0].IsUnitVisible=function()return false end',
    'PlayersVisibility[0].IsUnitVisible=function()return nil end',
    'x=99',
])
def test_native_refusal_cannot_hide_target_identity_hp_or_unit_visibility_change(change):
    conn=PreviewConn();conn.lua.execute('UnitManager.CanStartOperation=function()'+change+';return false end')
    result,state=preview(conn)
    assert result['status']=='unknown' and result['eligibility']=='unknown'
    assert conn.operations==[] and conn.previews==[] and state.pending_action_summary is None


@pytest.mark.parametrize('tamper',[
    lambda rows:[row for row in rows if row.startswith('COMBAT_PREVIEW_INELIGIBLE|')],
    lambda rows:rows+[rows[-1]],
    lambda rows:[row.replace('COMBAT_PREVIEW_INELIGIBLE|','COMBAT_PREVIEW_INELIGIBLE|wrong') for row in rows],
    lambda rows:[row.replace('|ATTACK_BLOCKED|','|UNRECOGNIZED|') for row in rows],
    lambda rows:[row.replace('|1245192|20|16|','|999|20|16|') for row in rows],
    lambda rows:[row.replace('|unit|2|5111813|19|16|','|unit|2|5111813|99|16|') for row in rows],
])
def test_native_refusal_requires_one_matching_binding_and_one_bound_known_reason(tamper):
    conn=PreviewConn();conn.lua.execute('allowed.move=false');execute=conn.execute_write
    async def altered(code):
        rows=await execute(code)
        lines=[line for row in rows for line in row.splitlines()]
        return tamper(lines) if any(row.startswith('COMBAT_PREVIEW_INELIGIBLE|') for row in lines) else rows
    conn.execute_write=altered
    result,state=preview(conn)
    assert result['status']=='unknown' and result['eligibility']=='unknown'
    assert conn.operations==[] and conn.previews==[] and state.pending_action_summary is None


def test_unchanged_refused_target_has_final_identity_visibility_hp_and_world_binding():
    conn=PreviewConn();conn.lua.execute('allowed.move=false');before=conn.facts()
    result,state=preview(conn)
    assert result['status']=='unknown' and result['eligibility']=='native_current_turn_refused'
    assert result['attacker']['id']==1245192 and result['attacker']['hp']==100
    assert result['target']['id']==5111813 and result['target']['hp']==100
    assert result['scope']['turn']==113 and 'ATTACK_BLOCKED' in result['reason']
    assert conn.facts()==before and conn.operations==[] and conn.previews==[] and state.pending_action_summary is None


def test_transport_hp_must_match_original_bound_target_hp():
    conn=PreviewConn();execute=conn.execute_write
    async def altered(code):
        rows=await execute(code)
        changed=[]
        for row in rows:
            for line in row.splitlines():
                if line.startswith('NATIVE_ESTIMATE|'):
                    fields=line.split('|');fields[7]='51';line='|'.join(fields)
                changed.append(line)
        return changed
    conn.execute_write=altered
    result,state=preview(conn)
    assert result['status']=='unknown' and result['estimate'] is None
    assert conn.operations==[] and state.pending_action_summary is None

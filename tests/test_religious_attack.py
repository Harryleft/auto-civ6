"""Public theological combat executes generated Lua with native-only adapters."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_unit_effect_confirmation import EffectConn


class ReligiousCombatConn(EffectConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
u.kind=4;u.religion=12;u.damage=0;u.maxDamage=100
u.GetDamage=function(self) return self.damage end
u.GetMaxDamage=function(self) return self.maxDamage end
u.HasMovedIntoZOC=function() return false end
u.GetComponentID=function(self) return {type=1,player=self.owner,id=self.id} end
GameInfo.Units[4]={UnitType='UNIT_APOSTLE',TrackReligion=true,ReligiousStrength=110}
GameInfo.Units[5]={UnitType='UNIT_MISSIONARY',TrackReligion=true,ReligiousStrength=100}
enemy={id=5767169,owner=1,kind=5,religion=8,x=20,y=17,damage=0,maxDamage=100}
for _,name in ipairs({'GetID','GetOwner','GetType','GetReligionType','GetX','GetY','GetDamage','GetMaxDamage','GetComponentID'}) do enemy[name]=u[name] end
units[enemy.id]=enemy
Players[1]={GetUnits=function() return {FindID=function(_,id) local unit=units[id];if unit and unit.owner==1 then return unit end end} end}
Players[0].GetUnits=function() return {FindID=function(_,id) local unit=units[id];if unit and unit.owner==0 then return unit end end} end
UnitManager.GetUnit=function(owner,id) for _,unit in pairs(units) do if unit.owner==owner and (unit.id==id or unit.id%65536==id) then return unit end end end
visible,unitVisible=true,true
PlayersVisibility={[0]={IsVisible=function(_,x,y) return visible end,IsUnitVisible=function(_,unit) return unitVisible end}}
MapLayers={ANY=-1};ComponentType={UNIT=1};CombatTypes={RELIGIOUS=4}
CombatVisType={ATTACKER=0,DEFENDER=1}
CombatResultParameters={ATTACKER=0,DEFENDER=1,ID=2,COMBAT_TYPE=3}
UnitOperationTypes.MOVE_TO=7;UnitOperationTypes.PARAM_MODIFIERS='modifiers'
UnitOperationMoveModifiers={ATTACK=16}
Units={GetUnitsInPlotLayerID=function(x,y,layer) assert(layer==-1);local out={};for _,unit in pairs(units) do if unit.x==x and unit.y==y then table.insert(out,unit) end end;return out end}
local function plot(x,y,index) return {GetX=function() return x end,GetY=function() return y end,GetIndex=function() return index end} end
plots={[299]=plot(19,17,299),[300]=plot(20,17,300)}
Map.GetPlot=function(x,y) for _,p in pairs(plots) do if p:GetX()==x and p:GetY()==y then return p end end end
Map.GetPlotByIndex=function(id) return plots[id] end
path={plots={299,300},turns={1,1}}
UnitManager.GetMoveToPathEx=function(actor,destination) assert(actor==u and destination==300);return path end
CombatManager={IsAttackChangeWarState=function(component,x,y) return {} end,
 SimulateAttackInto=function(component,mode,x,y) assert(mode==nil);return {[0]={[2]=u:GetComponentID()},[1]={[2]=enemy:GetComponentID()},[3]=4} end}
Events.CombatVisEnd=event()
function applyCombat()
 Events.UnitOperationStarted.Fire(0,196608,7)
 Events.CombatVisEnd.Fire({[0]={componentType=1,playerID=0,componentID=196608},[1]={componentType=1,playerID=1,componentID=5767169}})
 u.damage=20;enemy.damage=35
end
UnitManager.RequestOperation=function(actor,operation,params)
 assert(actor==u and operation==7 and params.x==20 and params.y==17 and params.modifiers==16)
 table.insert(submissions,{id=actor.id,op=operation,params=params})
 if not deferred then applyCombat() end
 if fail_request then error('entered native then failed') end
end
''')


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_native_peace_religious_attack_confirms_exact_combat_and_hp_effect_once():
    conn=ReligiousCombatConn();state=GameState(conn)
    result=asyncio.run(server.unit_action(make_ctx(game=state),196608,'religious_attack',target_x=20,target_y=17))
    assert result.startswith('RELIGIOUS_COMBAT_CONFIRMED|unit:196608|target:1:5767169|')
    assert 'attacker_hp:100->80' in result and 'defender_hp:100->65' in result
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'u.allowed=false', 'u.allowed=nil', 'u.allowed=1', 'busy=true',
    'visible=false', 'visible=nil', 'unitVisible=false', 'unitVisible=nil',
    'enemy.religion=12', 'enemy.religion=nil', 'enemy.kind=999', 'enemy.owner=0',
    'UnitManager.CanStartOperation=nil', 'UnitManager.GetMoveToPathEx=nil',
    'path.turns[2]=2', 'path.turns[1]=0', 'path.plots[2]=299', 'path.plots[3]=300',
    'path.turns[2]=nil', 'path.turns[4]=1', 'path.plots={299}', 'u.moves=0',
    'u.HasMovedIntoZOC=function() return true end',
    'CombatManager.IsAttackChangeWarState=nil',
    "CombatManager.IsAttackChangeWarState=function() error('unreadable native war state') end",
    'CombatManager.IsAttackChangeWarState=function() return false end',
    'CombatManager.IsAttackChangeWarState=function() return 0 end',
    "CombatManager.IsAttackChangeWarState=function() return 'unknown' end",
    'CombatManager.IsAttackChangeWarState=function() return {unexpected=true} end',
    'CombatManager.IsAttackChangeWarState=function() return {1} end',
    'CombatManager.SimulateAttackInto=function() return {[0]={[2]=u:GetComponentID()},[1]={[2]=enemy:GetComponentID()},[3]=0} end',
    'CombatManager.SimulateAttackInto=function() return {[0]={[2]=u:GetComponentID()},[1]={[2]={type=1,player=1,id=999}},[3]=4} end',
    'Events.CombatVisEnd=nil', 'Events.UnitOperationStarted=nil', 'Events.UnitRemovedFromMap=nil',
    'another={};for k,v in pairs(enemy) do another[k]=v end;another.id=5832705;units[another.id]=another',
])
def test_unavailable_illegal_ambiguous_or_deferred_native_attack_has_zero_submission(setup):
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute(setup)
    result=asyncio.run(state.religious_attack(196608,20,17))
    assert result.startswith('Error: RELIGIOUS_ATTACK_REFUSED|')
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_same_plot_military_layer_does_not_steal_religious_target():
    conn=ReligiousCombatConn();state=GameState(conn)
    conn.lua.execute("GameInfo.Units[6]={TrackReligion=false};military={};for k,v in pairs(enemy) do military[k]=v end;military.kind=6;military.id=5832706;units[military.id]=military")
    assert asyncio.run(state.religious_attack(196608,20,17)).startswith('RELIGIOUS_COMBAT_CONFIRMED|')


@pytest.mark.parametrize('events', [
    'enemy.damage=35',
    'Events.UnitOperationStarted.Fire(0,196608,7);enemy.damage=35',
    'Events.CombatVisEnd.Fire({[0]={componentType=1,playerID=0,componentID=196608},[1]={componentType=1,playerID=1,componentID=5767169}});enemy.damage=35',
    'Events.UnitOperationStarted.Fire(0,196608,7);Events.CombatVisEnd.Fire({[0]={componentType=1,playerID=0,componentID=196608},[1]={componentType=1,playerID=1,componentID=5832705}});enemy.damage=35',
    'Events.UnitOperationStarted.Fire(0,196608,7);Events.CombatVisEnd.Fire({[0]={componentType=1,playerID=1,componentID=196608},[1]={componentType=1,playerID=1,componentID=5767169}});enemy.damage=35',
    'Events.UnitOperationStarted.Fire(0,196608,8);applyCombat()',
    'applyCombat();u.damage=0;enemy.damage=0',
    'applyCombat();units[enemy.id]=nil',
    'applyCombat();Events.UnitRemovedFromMap.Fire(1,5767169)',
    'u.x=20;u.moves=0',
])
def test_wrong_or_incomplete_combat_evidence_does_not_confirm_or_resubmit(events):
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute(events)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_kind=='religious_attack'


@pytest.mark.parametrize('side', ['actor','target','both'])
@pytest.mark.parametrize('order', ['before','after'])
def test_exact_combat_with_death_in_either_event_order_uses_dual_absence_without_dead_userdata(side,order):
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    death='''
function die(unit)
 local id,owner=unit.id,unit.owner
 units[id]=nil;Events.UnitRemovedFromMap.Fire(owner,id)
 for k,v in pairs(unit) do if type(v)=='function' then unit[k]=function() error('destroyed userdata') end end end
end
'''
    selected={'actor':'die(u)','target':'die(enemy);u.x=20','both':'die(u);die(enemy)'}[side]
    # applyCombat writes only adapter scalars and emits first-party event schema.
    conn.lua.execute(death+(selected+';applyCombat()' if order=='before' else 'applyCombat();'+selected))
    result=asyncio.run(state.religious_attack(196608,20,17))
    assert result.startswith('RELIGIOUS_COMBAT_CONFIRMED|')
    if side in ('actor','both'): assert 'attacker_hp:100->0' in result
    if side in ('target','both'): assert 'defender_hp:100->0' in result
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change,restore', [
    ('u.kind=5','u.kind=4'),('u.religion=8','u.religion=12'),('u.id=262144','u.id=196608'),
    ('enemy.kind=4','enemy.kind=5'),('enemy.religion=12','enemy.religion=8'),
    ('enemy.x=21','enemy.x=20'),('enemy.id=5832705','enemy.id=5767169'),
    ('turn=42','turn=41'),('seed=8','seed=719665280'),
    ('Game.GetLocalPlayer=function() return 1 end','Game.GetLocalPlayer=function() return 0 end'),
])
def test_changed_then_restored_identity_stays_invalid(change,restore):
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute(restore+';applyCombat()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    assert conn.lua.eval('#submissions')==1


def test_unknown_entity_getter_does_not_hide_readable_mismatch_or_poison_recoverable_unknown():
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute('u.kind=nil;enemy.religion=12')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute('u.kind=4;enemy.religion=8;applyCombat()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    clean=ReligiousCombatConn();state=GameState(clean);clean.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    clean.lua.execute('enemy.religion=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    clean.lua.execute('enemy.religion=8;applyCombat()')
    assert asyncio.run(state.religious_attack(196608,20,17)).startswith('RELIGIOUS_COMBAT_CONFIRMED|')


@pytest.mark.parametrize('interruption',['lost','cancelled','throw'])
def test_lost_cancelled_or_throwing_submission_never_repeats_combat(interruption):
    class CancelConn(ReligiousCombatConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions')
            rows=await super().execute_write(code)
            if self.lua.eval('#submissions')>before: raise asyncio.CancelledError()
            return rows
    conn=CancelConn() if interruption=='cancelled' else ReligiousCombatConn();state=GameState(conn)
    if interruption=='lost': conn.lose_reply=True
    if interruption=='throw': conn.lua.execute('fail_request=true')
    if interruption=='cancelled':
        with pytest.raises(asyncio.CancelledError): asyncio.run(state.religious_attack(196608,20,17))
    else: assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    result=asyncio.run(state.religious_attack(196608,20,17))
    assert ('OUTCOME_UNKNOWN' in result) if interruption=='throw' else result.startswith('RELIGIOUS_COMBAT_CONFIRMED|')
    assert conn.lua.eval('#submissions')==1


def test_pending_attack_blocks_retarget_other_mutations_save_and_turn():
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    for call in [state.religious_attack(196608,21,17),state.skip_unit(196608),state.save_game('pending'),state.end_turn()]:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(call)
    conn.lua.execute('applyCombat()')
    assert asyncio.run(state.religious_attack(196608,20,17)).startswith('RELIGIOUS_COMBAT_CONFIRMED|')
    assert conn.lua.eval('#submissions')==1


def test_final_native_path_change_cannot_submit_deferred_order():
    conn=ReligiousCombatConn();state=GameState(conn)
    conn.lua.execute('''
local original=UnitManager.GetMoveToPathEx
local calls=0
UnitManager.GetMoveToPathEx=function(...)
 calls=calls+1
 if calls==2 then return {plots={299,300},turns={1,2}} end
 return original(...)
end
''')
    result=asyncio.run(state.religious_attack(196608,20,17))
    assert result.startswith('Error: RELIGIOUS_ATTACK_REFUSED|')
    assert conn.lua.eval('#submissions')==0


def test_callback_identity_change_is_sticky_after_restoration():
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute('enemy.religion=12;Events.UnitOperationStarted.Fire(0,196608,7);enemy.religion=8;applyCombat()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup', [
    "UnitManager.CanStartOperation=function() enemy.religion=12;return true end",
    "UnitManager.GetMoveToPathEx=function() u.kind=5;return path end",
    "CombatManager.IsAttackChangeWarState=function() turn=42;return {} end",
    "CombatManager.SimulateAttackInto=function() enemy.id=5832706;return {[0]={[2]=u:GetComponentID()},[1]={[2]=enemy:GetComponentID()},[3]=4} end",
])
def test_native_eligibility_mutating_original_identity_refuses_before_entry(setup):
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute(setup)
    assert asyncio.run(state.religious_attack(196608,20,17)).startswith('Error: RELIGIOUS_ATTACK_REFUSED|')
    assert conn.lua.eval('#submissions')==0


def test_combat_visual_can_precede_started_with_same_original_participants():
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute('''
Events.CombatVisEnd.Fire({[0]={componentType=1,playerID=0,componentID=196608},[1]={componentType=1,playerID=1,componentID=5767169}})
enemy.damage=35;Events.UnitOperationStarted.Fire(0,196608,7)
''')
    assert asyncio.run(state.religious_attack(196608,20,17)).startswith('RELIGIOUS_COMBAT_CONFIRMED|')


def test_cancelled_confirmation_retains_receipt_and_does_not_repeat_attack():
    class CancelConfirmConn(ReligiousCombatConn):
        cancel=False
        async def execute_write(self,code):
            rows=await super().execute_write(code)
            if self.cancel:
                self.cancel=False
                raise asyncio.CancelledError()
            return rows
    conn=CancelConfirmConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute('applyCombat()');conn.cancel=True
    with pytest.raises(asyncio.CancelledError): asyncio.run(state.religious_attack(196608,20,17))
    assert state.pending_action_kind=='religious_attack'
    assert asyncio.run(state.religious_attack(196608,20,17)).startswith('RELIGIOUS_COMBAT_CONFIRMED|')
    assert conn.lua.eval('#submissions')==1


def test_unknown_scope_beside_known_entity_mismatch_cannot_recover_after_restoration():
    conn=ReligiousCombatConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute('seed=nil;enemy.religion=12')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    conn.lua.execute('seed=719665280;enemy.religion=8;applyCombat()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608,20,17))
    assert conn.lua.eval('#submissions')==1


def test_successful_native_nil_war_change_result_allows_peace_religious_attack():
    conn = ReligiousCombatConn()
    state = GameState(conn)
    conn.lua.execute('CombatManager.IsAttackChangeWarState=function() return nil end')
    result = asyncio.run(server.unit_action(
        make_ctx(game=state), 196608, 'religious_attack', target_x=20, target_y=17))
    assert result.startswith('RELIGIOUS_COMBAT_CONFIRMED|unit:196608|target:1:5767169|')
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_readable_target_x_change_latches_when_y_getter_is_unknown():
    conn = ReligiousCombatConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    conn.lua.execute("enemy.x=21;enemy.GetY=function() error('unknown Y') end")
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    conn.lua.execute('enemy.x=20;enemy.GetY=u.GetY;applyCombat()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    assert state.pending_action_kind == 'religious_attack'
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('subject', ['u', 'enemy'])
@pytest.mark.parametrize('changed_axis', ['x', 'y'])
@pytest.mark.parametrize('observation', ['probe', 'callback'])
@pytest.mark.parametrize('unavailable', ['throw', 'nil'])
def test_each_readable_coordinate_mismatch_stays_invalid_beside_unknown_sibling(
    subject, changed_axis, observation, unavailable,
):
    conn = ReligiousCombatConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    unknown_method = 'GetY' if changed_axis == 'x' else 'GetX'
    changed_value = 21 if changed_axis == 'x' else 18
    unknown_body = "error('coordinate unavailable')" if unavailable == 'throw' else 'return nil'
    conn.lua.execute(f'''
savedCoordinate={subject}.{changed_axis}
savedGetter={subject}.{unknown_method}
{subject}.{changed_axis}={changed_value}
{subject}.{unknown_method}=function() {unknown_body} end
''')
    if observation == 'probe':
        assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    else:
        # The mismatch exists only inside the native event callback window.
        conn.lua.execute('Events.UnitOperationStarted.Fire(0,196608,7)')
    conn.lua.execute(f'{subject}.{changed_axis}=savedCoordinate;{subject}.{unknown_method}=savedGetter;applyCombat()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    assert state.pending_action_kind == 'religious_attack'
    assert conn.lua.eval('#submissions') == 1


def test_partial_actor_coordinate_on_approved_path_can_recover_without_replay():
    conn = ReligiousCombatConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    conn.lua.execute("u.x=20;u.GetY=function() error('unknown Y') end")
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    conn.lua.execute('u.GetY=enemy.GetY;applyCombat()')
    result = asyncio.run(state.religious_attack(196608, 20, 17))
    assert result.startswith('RELIGIOUS_COMBAT_CONFIRMED|unit:196608|target:1:5767169|')
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1


def test_actor_can_enter_original_target_plot_and_confirm_native_combat():
    conn = ReligiousCombatConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    conn.lua.execute('u.x=20;u.y=17;applyCombat()')
    assert asyncio.run(state.religious_attack(196608, 20, 17)).startswith('RELIGIOUS_COMBAT_CONFIRMED|')
    assert conn.lua.eval('#submissions') == 1


def test_complete_actor_coordinates_must_match_one_path_point_not_separate_components():
    conn = ReligiousCombatConn(); state = GameState(conn)
    conn.lua.execute('''
plots[301]={GetX=function() return 19 end,GetY=function() return 18 end,GetIndex=function() return 301 end}
path={plots={299,301,300},turns={1,1,1}}
deferred=true
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    # X=20 and Y=18 each occur on the native path, but never together.
    conn.lua.execute('u.x=20;u.y=18')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    conn.lua.execute('u.x=19;u.y=17;applyCombat()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.religious_attack(196608, 20, 17))
    assert conn.lua.eval('#submissions') == 1

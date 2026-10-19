"""Public mutations with real Lua; only native unit/transport APIs are simulated."""
import asyncio
import pytest
from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class TransformConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.lua.execute('''
balance,cost,targetType=100,25,2
u=units[196608];u.type=1;u.damage=30
u.GetType=function(self) return self.type end
u.GetName=function() return 'Warrior' end
u.GetDamage=function(self) return self.damage end
u.GetUpgradeCost=function() return cost end
u.GetUpgradeResourceCost=function() return -1,0 end
Players[0].GetTreasury=function() return {GetGoldBalance=function() return balance end} end
UnitCommandTypes={UPGRADE=21,DELETE=22}
UnitCommandResults={UNIT_TYPE='type'}
GameInfo.Units={[1]={UnitType='UNIT_WARRIOR'},[2]={UnitType='UNIT_SWORDSMAN'}}
UnitManager.CanStartCommand=function(unit,command,visible,check)
 assert(visible==false and check==true)
 return unit.allowed,{type=targetType}
end
function applyUpgrade() u.type=targetType;balance=balance-cost end
UnitManager.RequestCommand=function(unit,command)
 table.insert(submissions,{id=unit.id,command=command})
 if not deferred then applyUpgrade() end
 if fail_request then error('native threw after entry') end
end
''')

    async def execute_write(self,code):
        before=self.lua.eval('#submissions')
        rows=await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions')>before:
            self.lose_reply=False
            raise OSError('executed but reply lost')
        return rows


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT',.01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL',.001)


def test_upgrade_lost_reply_preserves_exact_target_and_does_not_pay_twice():
    conn=TransformConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.upgrade_unit(ctx,196608))
    assert asyncio.run(server.upgrade_unit(ctx,196608)).startswith('UPGRADED|')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('balance')==75
    assert conn.lua.eval('u.type')==2
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup',[
    'u.allowed=false','UnitManager.CanStartCommand=nil','UnitManager.RequestCommand=nil',
    'targetType=nil','targetType=1','balance=1','cost=nil','u.id=262144','busy=true',
])
def test_upgrade_preflight_refusals_never_submit(setup):
    conn=TransformConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'UNIT_UPGRADE_REFUSED' in asyncio.run(state.upgrade_unit(196608))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_upgrade_waits_for_both_type_and_consumption_and_blocks_other_mutations():
    conn=TransformConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    conn.lua.execute('u.type=2')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.delete_unit(196608))
    conn.lua.execute('balance=75')
    assert asyncio.run(state.upgrade_unit(196608)).startswith('UPGRADED|')
    assert conn.lua.eval('#submissions')==1


def test_upgrade_native_throw_cannot_be_confirmed_or_replayed():
    conn=TransformConn();state=GameState(conn);conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    assert conn.lua.eval('#submissions')==1


def test_upgrade_requires_strategic_resource_cost_as_reported_by_native_engine():
    conn=TransformConn();state=GameState(conn)
    conn.lua.execute('''
GameInfo.Units_XP2={};stock=20
u.GetUpgradeResourceCost=function() return 3,10 end
Players[0].GetResources=function() return {GetResourceAmount=function(self,id) assert(id==3);return stock end} end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    conn.lua.execute('stock=10')
    assert asyncio.run(state.upgrade_unit(196608)).startswith('UPGRADED|')
    assert conn.lua.eval('#submissions')==1


class DeleteConn(TransformConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
function event()
 local listeners={}
 return {Add=function(f) listeners[f]=true end,Remove=function(f) listeners[f]=nil end,
 Fire=function(...) for f in pairs(listeners) do f(...) end end}
end
Events={UnitRemovedFromMap=event()}
function applyDelete()
 local id=u.id
 units[id]=nil
 u.GetName=function() error('consumed userdata') end
 u.GetX=function() error('consumed userdata') end
 Events.UnitRemovedFromMap.Fire(0,id)
end
UnitManager.RequestCommand=function(unit,command)
 assert(command==22)
 table.insert(submissions,{id=unit.id,command=command})
 if not deferred then applyDelete() end
 if fail_request then error('delete threw after entry') end
end
''')


def test_delete_lost_reply_uses_exact_removal_and_never_reads_consumed_unit():
    conn=DeleteConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,'delete'))
    assert asyncio.run(server.unit_action(ctx,196608,'delete')).startswith('DELETED|unit:196608')
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_summary is None


class PromotionConn(TransformConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
promotions,stored,xp,heals,clears={},2,100,0,0
options={14,15};eligibilityCalls=0
UnitCommandTypes.PROMOTE=-1259482049
UnitCommandTypes.PARAM_PROMOTION_TYPE=-927495623
UnitCommandResults.PROMOTIONS='promotions'
UnitManager.CanStartCommand=function(unit,command,visible,check)
 assert(check==true)
 if command==-1259482049 then
  assert(visible==false)
  eligibilityCalls=eligibilityCalls+1
  return unit.allowed and (not promotions[14] or unit.id~=196608),{promotions=options}
 end
 assert(visible==false)
 return unit.allowed,{type=targetType}
end
Locale={Lookup=function(s) return s end}
local promo={Index=14,Hash=664752614,UnitPromotionType='PROMOTION_VOLLEY',Name='Volley'}
GameInfo.UnitPromotions={PROMOTION_VOLLEY=promo,[14]=promo}
exp={HasPromotion=function(self,id) return promotions[id]==true end,
 GetStoredPromotions=function() return stored end,
 SetPromotion=function() error('direct promotion forbidden') end,
 ChangeStoredPromotions=function() clears=clears+1;error('direct XP write forbidden') end}
u.GetExperience=function() return exp end
u.SetDamage=function() heals=heals+1;error('direct healing forbidden') end
function applyPromotion()
 promotions[14]=true;stored=1;u.damage=10;u.moves=0
end
UnitManager.RequestCommand=function(unit,command,parameters)
 assert(nativeContext=='ingame','native command belongs in InGame')
 assert(command==-1259482049 and parameters[-927495623]==14,'exact native index required')
 table.insert(submissions,{id=unit.id,command=command,promotion=parameters[-927495623]})
 if not deferred then applyPromotion() end
 if fail_request then error('native threw after entry') end
end
Players[0].GetUnits=function() return {
 Members=function() return pairs(units) end,
 FindID=function(self,id) return UnitManager.GetUnit(0,id) end}
end
''')

    async def execute_write(self,code):
        self.lua.execute("nativeContext='ingame'")
        return await super().execute_write(code)

    async def execute_read(self,code):
        self.lua.execute("nativeContext='gamecore'")
        return await ReadyConn.execute_write(self,code)


def test_promotion_native_index_in_game_and_lost_reply_confirms_actual_engine_result():
    conn=PromotionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.promote_unit(ctx,196608,'PROMOTION_VOLLEY'))
    result=asyncio.run(server.promote_unit(ctx,196608,'PROMOTION_VOLLEY'))
    assert result.startswith('PROMOTED|unit:196608|PROMOTION_VOLLEY')
    assert 'damage:10' in result and 'moves:0' in result and 'stored_promotions:1' in result
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('submissions[1].promotion')==14
    assert conn.lua.eval('heals')==0
    assert conn.lua.eval('clears')==0
    assert conn.lua.eval('stored')==1
    assert conn.lua.eval('u.damage')==10


def test_promotion_unreturned_native_command_keeps_gate_without_replaying():
    conn=PromotionConn();state=GameState(conn);conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('promotions[14]')
    assert conn.lua.eval('heals')==0
    assert conn.lua.eval('clears')==0


@pytest.mark.parametrize('setup', [
    'u.id=262144','u.allowed=false','u.GetOwner=function() return 1 end','u.GetX=function() return -9999 end',
    'promotions[14]=true','busy=true','exp.HasPromotion=function() return nil end',
    'UnitManager.RequestCommand=nil','UnitManager.CanStartCommand=nil',
    'UnitCommandTypes.PROMOTE=nil','UnitCommandTypes.PARAM_PROMOTION_TYPE=nil',
    'UnitCommandResults.PROMOTIONS=nil','GameInfo.UnitPromotions.PROMOTION_VOLLEY.Index=nil',
    'UnitCommandTypes.PROMOTE=false','UnitCommandTypes.PARAM_PROMOTION_TYPE=false',
    'UnitManager.CanStartCommand=function() return nil end',
    'UnitManager.CanStartCommand=function() return true,nil end',
    'UnitManager.CanStartCommand=function() return true,{} end',
    'options={}','options={8}','options={664752614}','options={"14"}','options={14,14}',
])
def test_promotion_ineligible_or_unreadable_native_choice_does_not_submit(setup):
    conn=PromotionConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'UNIT_PROMOTION_REFUSED' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    assert conn.lua.eval('#submissions')==0
    assert conn.lua.eval('clears')==0
    assert conn.lua.eval('heals')==0
    assert state.pending_action_summary is None


def test_promotion_delayed_exact_result_is_observed_without_second_command():
    conn=PromotionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    conn.lua.execute('promotions[8]=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    conn.lua.execute('applyPromotion()')
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY')).startswith('PROMOTED|')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('heals')==0
    assert conn.lua.eval('clears')==0


@pytest.mark.parametrize('at_call', [1,2])
@pytest.mark.parametrize('change', ['options={8}','u.allowed=false','u.id=262144','turn=42'])
def test_changed_native_promotion_option_or_identity_is_refused_before_entry(change,at_call):
    conn=PromotionConn();state=GameState(conn)
    conn.lua.execute('''
local original=UnitManager.CanStartCommand
UnitManager.CanStartCommand=function(...)
 local allowed,results=original(...)
 if eligibilityCalls=='''+str(at_call)+''' then
'''+change+'''
  return u.allowed,{promotions=options}
 end
 return allowed,results
end
''')
    assert 'UNIT_PROMOTION_REFUSED' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_deletion_needs_exact_removal_event_and_fresh_absence():
    conn=DeleteConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.delete_unit(196608))
    conn.lua.execute('Events.UnitRemovedFromMap.Fire(0,196608)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.delete_unit(196608))
    conn.lua.execute('units[196608]=nil;addUnit(262144,true,0,0)')
    assert asyncio.run(state.delete_unit(196608)).startswith('DELETED|')
    assert conn.lua.eval('units[262144]~=nil')
    assert conn.lua.eval('#submissions')==1


def test_deleted_without_matching_event_cannot_be_attributed_to_our_request():
    conn=DeleteConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.delete_unit(196608))
    conn.lua.execute('units[196608]=nil;Events.UnitRemovedFromMap.Fire(1,196608);Events.UnitRemovedFromMap.Fire(0,262144)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.delete_unit(196608))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup', ['u.id=262144','u.allowed=false','Events.UnitRemovedFromMap=nil','UnitManager.RequestCommand=nil'])
def test_delete_refuses_missing_eligibility_or_identity_before_submission(setup):
    conn=DeleteConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'UNIT_DELETE_REFUSED' in asyncio.run(state.delete_unit(196608))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('kind', ['upgrade','delete','promote'])
def test_cancelled_native_return_can_only_confirm_original(kind):
    cls={'upgrade':TransformConn,'delete':DeleteConn,'promote':PromotionConn}[kind]
    class CancelConn(cls):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions')
            rows=await super().execute_write(code)
            if self.lua.eval('#submissions')>before: raise asyncio.CancelledError()
            return rows
        async def execute_read(self,code):
            before=self.lua.eval('#submissions')
            rows=await super().execute_read(code)
            if self.lua.eval('#submissions')>before: raise asyncio.CancelledError()
            return rows
    conn=CancelConn();state=GameState(conn)
    method=getattr(state,kind+'_unit');args=(196608,'PROMOTION_VOLLEY') if kind=='promote' else (196608,)
    with pytest.raises(asyncio.CancelledError): asyncio.run(method(*args))
    assert state.pending_action_summary is not None
    assert 'OUTCOME_UNKNOWN' not in asyncio.run(method(*args))
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('kind', ['upgrade','delete','promote'])
def test_changed_world_latches_before_original_world_returns(kind):
    conn={'upgrade':TransformConn,'delete':DeleteConn,'promote':PromotionConn}[kind]()
    state=GameState(conn);conn.lose_reply=True
    method=getattr(state,kind+'_unit');args=(196608,'PROMOTION_VOLLEY') if kind=='promote' else (196608,)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(method(*args))
    conn.lua.execute('seed=11')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(method(*args))
    conn.lua.execute('seed=719665280')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(method(*args))
    assert conn.lua.eval('#submissions')==1


class PromotionPopupConn(PromotionConn):
    def __init__(self):
        super().__init__()
        self.lua_states={}
        self.lua.execute('''
notificationDismissed=false;notificationDismissCalls=0
EndTurnBlockingTypes={ENDTURN_BLOCKING_UNIT_PROMOTION=41}
NotificationManager={GetList=function() return {9} end,
 Find=function() return {IsDismissed=function() return notificationDismissed end,
 GetEndTurnBlocking=function() return 41 end,GetTypeName=function() return 'NOTIFICATION_UNIT_PROMOTION_AVAILABLE' end} end,
 Dismiss=function(owner,id) assert(owner==0 and id==9);notificationDismissCalls=notificationDismissCalls+1;notificationDismissed=true end}
ContextPtr={LookUpControl=function() end}
InterfaceModeTypes={CINEMATIC=7,SELECTION=0}
UI.GetInterfaceMode=function() return 0 end
PlayerOperations={}
''')


def test_explicit_popup_cleanup_resolves_stale_promotion_notification_without_rehealing(monkeypatch):
    from civ_mcp import game_launcher
    async def no_dialogs(): return []
    monkeypatch.setattr(game_launcher,'dismiss_crash_dialogs',no_dialogs)
    conn=PromotionPopupConn();state=GameState(conn)
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY')).startswith('PROMOTED|')
    assert 'UnitPromotion' in asyncio.run(state.dismiss_popup())
    assert conn.lua.eval('notificationDismissed')
    asyncio.run(state.dismiss_popup())
    assert conn.lua.eval('notificationDismissCalls')==1
    assert conn.lua.eval('heals')==0
    assert conn.lua.eval('clears')==0


@pytest.mark.parametrize('setup',[
    'addUnit(196609,true,0,0)',
    'UnitManager.CanStartCommand=function() return nil end',
    'UnitManager.CanStartCommand=nil',
    'UnitManager.CanStartCommand=function() error("unavailable") end',
])
def test_popup_cleanup_preserves_real_or_unknown_promotion_choices(monkeypatch,setup):
    from civ_mcp import game_launcher
    async def no_dialogs(): return []
    monkeypatch.setattr(game_launcher,'dismiss_crash_dialogs',no_dialogs)
    conn=PromotionPopupConn();state=GameState(conn)
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY')).startswith('PROMOTED|')
    conn.lua.execute(setup)
    assert 'Promotion notification retained' in asyncio.run(state.dismiss_popup())
    assert not conn.lua.eval('notificationDismissed')
    assert conn.lua.eval('notificationDismissCalls')==0
    assert conn.lua.eval('clears')==0
    assert conn.lua.eval('heals')==0


def test_native_eligibility_is_authoritative_without_compatibility_xp_or_mutators():
    conn=PromotionConn();state=GameState(conn)
    conn.lua.execute('''
xp=0;exp.CanPromote=nil;exp.GetExperiencePoints=nil;exp.GetExperienceForNextLevel=nil
exp.SetPromotion=nil;exp.ChangeStoredPromotions=nil;u.SetDamage=nil
''')
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY')).startswith('PROMOTED|')
    assert conn.lua.eval('#submissions')==1


def test_legacy_low_id_delete_retry_cannot_confirm_then_delete_replacement():
    conn=DeleteConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,0,'delete'))
    conn.lua.execute('addUnit(262144,true,0,0)')
    assert asyncio.run(server.unit_action(ctx,0,'delete')).startswith('DELETED|unit:196608')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('units[262144]~=nil')


@pytest.mark.parametrize('kind', ['upgrade','delete','promote'])
def test_scope_changed_between_prepare_and_native_submission_sends_nothing(kind):
    cls={'upgrade':TransformConn,'delete':DeleteConn,'promote':PromotionConn}[kind]
    class ChangedConn(cls):
        async def execute_write(self,code):
            if '-- ACTION_SUBMIT_SCOPE|' in code: self.lua.execute('turn=42')
            return await super().execute_write(code)
        async def execute_read(self,code):
            if '-- ACTION_SUBMIT_SCOPE|' in code: self.lua.execute('turn=42')
            return await super().execute_read(code)
    conn=ChangedConn();state=GameState(conn)
    method=getattr(state,kind+'_unit');args=(196608,'PROMOTION_VOLLEY') if kind=='promote' else (196608,)
    assert 'ACTION_SCOPE_NO_SUBMIT' in asyncio.run(method(*args))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_unreturned_delete_native_call_keeps_gate_after_effect():
    conn=DeleteConn();state=GameState(conn);conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.delete_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.delete_unit(196608))
    assert conn.lua.eval('#submissions')==1


def test_upgrade_wrong_full_id_cannot_supply_target_effect_on_confirmation():
    conn=TransformConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    conn.lua.execute('u.id=262144;u.type=2;balance=75')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    assert conn.lua.eval('#submissions')==1


def test_promotion_already_granted_is_a_no_submit_result_without_rehealing():
    conn=PromotionConn();state=GameState(conn)
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY')).startswith('PROMOTED|')
    conn.lua.execute('u.damage=10')
    assert 'ALREADY_HAS_PROMOTION' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('u.damage')==10
    assert conn.lua.eval('heals')==0


def test_native_promotion_confirmation_does_not_require_gamecore_stored_counter():
    conn=PromotionConn();state=GameState(conn)
    conn.lua.execute('exp.GetStoredPromotions=nil')
    result=asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    assert result.startswith('PROMOTED|')
    assert 'damage:10' in result and 'moves:0' in result
    assert 'stored_promotions:unknown' in result
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change', [
    "__civmcpPromotion.token='another-token'",
    '__civmcpPromotion.owner=1',
    '__civmcpPromotion.id=262144',
    '__civmcpPromotion.requested=262144',
    "__civmcpPromotion.promotion='PROMOTION_OTHER'",
    '__civmcpPromotion.index=8;promotions[8]=true',
    '__civmcpPromotion.entered=false',
    '__civmcpPromotion.returned=false',
    'u.id=262144',
    'u.GetOwner=function() return 1 end',
])
def test_native_promotion_wrong_receipt_or_full_identity_cannot_confirm(change):
    conn=PromotionConn();state=GameState(conn);conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    assert state.pending_action_summary is not None
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('getter', ['exp.HasPromotion','u.GetDamage','u.GetMovesRemaining'])
def test_native_promotion_temporarily_unreadable_result_recovers_without_resubmission(getter):
    conn=PromotionConn();state=GameState(conn);conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    conn.lua.execute(f'original={getter};{getter}=function() return nil end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY'))
    conn.lua.execute(f'{getter}=original')
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_VOLLEY')).startswith('PROMOTED|')
    assert conn.lua.eval('#submissions')==1

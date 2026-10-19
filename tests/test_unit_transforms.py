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
UnitCommandTypes.PROMOTE=23
UnitManager.CanStartCommand=function(unit,command,visible,check)
 assert(visible==false and check==true)
 if command==23 then return not promotions[7] or unit.id~=196608 end
 return unit.allowed,{type=targetType}
end
Locale={Lookup=function(s) return s end}
GameInfo.UnitPromotionPrereqs=function() return function() end end
local promo={Index=7,UnitPromotionType='PROMOTION_BATTLECRY',Name='Battlecry',PromotionClass='MELEE'}
GameInfo.Units[1].PromotionClass='MELEE'
GameInfo.UnitPromotions=setmetatable({PROMOTION_BATTLECRY=promo,[7]=promo},{__call=function()
 local done=false
 return function() if not done then done=true;return promo end end
end})
exp={HasPromotion=function(self,id) return promotions[id]==true end,
 CanPromote=function(self,id) return u.allowed end,
 GetExperiencePoints=function() return xp end,
 GetExperienceForNextLevel=function() return 15 end,
 GetStoredPromotions=function() return stored end,
 SetPromotion=function(self,id)
  assert(nativeContext=='gamecore','wrong mutation context')
  table.insert(submissions,{id=u.id,promotion=id})
  if not deferred then promotions[id]=true end
  if failStage=='promotion' then error('promotion throw') end
 end,
 ChangeStoredPromotions=function(self,n)
  clears=clears+1;stored=stored+n
  if failStage=='stored' then error('stored throw') end
 end}
u.GetExperience=function() return exp end
u.SetDamage=function(self,n)
 heals=heals+1;self.damage=n
 if failStage=='heal' then error('heal throw') end
end
Players[0].GetUnits=function() return {
 Members=function() return pairs(units) end,
 FindID=function(self,id) assert(nativeContext=='gamecore');return UnitManager.GetUnit(0,id) end}
end
''')

    async def execute_write(self,code):
        self.lua.execute("nativeContext='ingame'")
        return await super().execute_write(code)

    async def execute_read(self,code):
        self.lua.execute("nativeContext='gamecore'")
        before=self.lua.eval('#submissions')
        rows=await ReadyConn.execute_write(self,code)
        if self.lose_reply and self.lua.eval('#submissions')>before:
            self.lose_reply=False
            raise OSError('GameCore response lost')
        return rows


def test_promotion_uses_gamecore_and_lost_reply_never_repeats_heal_or_stored_cleanup():
    conn=PromotionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.promote_unit(ctx,196608,'PROMOTION_BATTLECRY'))
    assert asyncio.run(server.promote_unit(ctx,196608,'PROMOTION_BATTLECRY')).startswith('PROMOTED|')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('heals')==1
    assert conn.lua.eval('clears')==1
    assert conn.lua.eval('stored')==0
    assert conn.lua.eval('u.damage')==0


@pytest.mark.parametrize('stage', ['promotion','stored','heal'])
def test_promotion_partial_exception_retains_all_completed_steps_without_replaying(stage):
    conn=PromotionConn();state=GameState(conn);conn.lua.globals().failStage=stage
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY'))
    before=(conn.lua.eval('#submissions'),conn.lua.eval('clears'),conn.lua.eval('heals'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    assert before==(conn.lua.eval('#submissions'),conn.lua.eval('clears'),conn.lua.eval('heals'))


@pytest.mark.parametrize('setup', [
    'u.id=262144','u.allowed=false','xp=0','stored=nil','u.damage=nil',
    'promotions[7]=true','exp.ChangeStoredPromotions=nil','u.SetDamage=nil',
])
def test_promotion_ineligible_or_stale_identity_does_not_heal_or_clear_stored(setup):
    conn=PromotionConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'UNIT_PROMOTION_REFUSED' in asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY'))
    assert conn.lua.eval('#submissions')==0
    assert conn.lua.eval('clears')==0
    assert conn.lua.eval('heals')==0
    assert state.pending_action_summary is None


def test_promotion_delayed_heal_is_observed_without_healing_again():
    conn=PromotionConn();state=GameState(conn)
    conn.lua.execute('u.SetDamage=function(self,n) heals=heals+1 end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY'))
    conn.lua.execute('u.damage=0')
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY')).startswith('PROMOTED|')
    assert conn.lua.eval('heals')==1
    assert conn.lua.eval('clears')==1


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
    method=getattr(state,kind+'_unit');args=(196608,'PROMOTION_BATTLECRY') if kind=='promote' else (196608,)
    with pytest.raises(asyncio.CancelledError): asyncio.run(method(*args))
    assert state.pending_action_summary is not None
    assert 'OUTCOME_UNKNOWN' not in asyncio.run(method(*args))
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('kind', ['upgrade','delete','promote'])
def test_changed_world_latches_before_original_world_returns(kind):
    conn={'upgrade':TransformConn,'delete':DeleteConn,'promote':PromotionConn}[kind]()
    state=GameState(conn);conn.lose_reply=True
    method=getattr(state,kind+'_unit');args=(196608,'PROMOTION_BATTLECRY') if kind=='promote' else (196608,)
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
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY')).startswith('PROMOTED|')
    assert 'UnitPromotion' in asyncio.run(state.dismiss_popup())
    assert conn.lua.eval('notificationDismissed')
    asyncio.run(state.dismiss_popup())
    assert conn.lua.eval('notificationDismissCalls')==1
    assert conn.lua.eval('heals')==1
    assert conn.lua.eval('clears')==1


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
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY')).startswith('PROMOTED|')
    conn.lua.execute(setup)
    assert 'Promotion notification retained' in asyncio.run(state.dismiss_popup())
    assert not conn.lua.eval('notificationDismissed')
    assert conn.lua.eval('notificationDismissCalls')==0
    assert conn.lua.eval('clears')==1
    assert conn.lua.eval('heals')==1


def test_promotion_missing_prerequisite_is_rejected_before_native_mutation():
    conn=PromotionConn();state=GameState(conn)
    conn.lua.execute('''
GameInfo.UnitPromotionPrereqs=function()
 local done=false
 return function() if not done then done=true;return {UnitPromotion='PROMOTION_BATTLECRY',PrereqUnitPromotion='PROMOTION_PREREQUISITE'} end end
end
GameInfo.UnitPromotions.PROMOTION_PREREQUISITE={Index=8}
''')
    assert 'UNIT_PROMOTION_REFUSED' in asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY'))
    assert conn.lua.eval('#submissions')==0
    assert conn.lua.eval('u.damage')==30


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
    method=getattr(state,kind+'_unit');args=(196608,'PROMOTION_BATTLECRY') if kind=='promote' else (196608,)
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
    assert asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY')).startswith('PROMOTED|')
    conn.lua.execute('u.damage=10')
    assert 'ALREADY_HAS_PROMOTION' in asyncio.run(state.promote_unit(196608,'PROMOTION_BATTLECRY'))
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('u.damage')==10
    assert conn.lua.eval('heals')==1

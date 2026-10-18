"""Full native spy identity and single escape choice via public GameState/Lua."""
import asyncio
import pytest
from test_unit_readiness import ReadyConn
from civ_mcp.game_state import GameState
from civ_mcp import narrate

run = asyncio.run


class EscapeConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
units={};spy=addUnit(2949124,true,0,0)
spy.GetType=function() return 1 end;spy.GetName=function() return "Spy" end
spy.GetSpyOperation=function() return 1 end
spy.GetExperience=function() return {GetLevel=function() return 2 end,GetExperiencePoints=function() return 50 end} end
Players[0].GetUnits=function() return {Members=function() return pairs(units) end,FindID=function(_,id) return units[id] end} end
GameInfo.Units={[1]={UnitType="UNIT_SPY"}}
GameInfo.UnitOperations=setmetatable({}, {__call=function() local done=false;return function() if not done then done=true;return {Index=1,OperationType="UNITOPERATION_SPY_STEAL_TECH_BOOST"} end end end})
Locale={Lookup=function(s) return s end}
blocked=true;dismissed=false;pending_spy=2949124;dangerous_reads=0;escape_deferred=false;escape_requests={};has_harbor=true;city_missing=false
EndTurnBlockingTypes={ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE=7}
NotificationManager={GetList=function() return blocked and {91} or {} end,
 Find=function() return {IsDismissed=function() return dismissed end,GetEndTurnBlocking=function() return 7 end} end}
Players[0].GetDiplomacy=function() return {GetNextEscapingSpyID=function()
 dangerous_reads=dangerous_reads+1;if not blocked or dismissed then error("dangerous lookup without gate") end;return pending_spy end} end
city={GetID=function() return 65537 end,GetOwner=function() return 1 end,GetName=function() return "Orebo" end,
 GetDistricts=function() return {HasDistrict=function(_,i) return i==0 or (i==2 and has_harbor) end} end}
Cities={GetPlotPurchaseCity=function() if not city_missing then return city end end}
CityManager={GetCityAt=function() return city end}
GameInfo.Districts={DISTRICT_CITY_CENTER={Index=0,TravelTime=4},DISTRICT_AERODROME={Index=1,TravelTime=1},DISTRICT_HARBOR={Index=2,TravelTime=2},DISTRICT_COMMERCIAL_HUB={Index=3,TravelTime=3}}
UnitOperationTypes.PARAM_X0=1;UnitOperationTypes.PARAM_Y0=2
UnitManager.CanStartOperation=function() return true end
PlayerOperations={SET_ESCAPE_ROUTE=22,PARAM_DISTRICT_TYPE="district"}
removal_handlers={}
Events={UnitRemovedFromMap={
 Add=function(handler) table.insert(removal_handlers,handler);if event_on_arm then handler(0,2949124) end end,
 Remove=function(handler) for i=#removal_handlers,1,-1 do if removal_handlers[i]==handler then table.remove(removal_handlers,i) end end end}}
function emitRemoval(owner,id)
 local copy={};for _,handler in ipairs(removal_handlers) do table.insert(copy,handler) end
 for _,handler in ipairs(copy) do handler(owner,id) end
end
UI.RequestPlayerOperation=function(owner,operation,params)
 table.insert(escape_requests,{owner=owner,operation=operation,district=params.district})
 if fail_request then error("native failure after entry") end
 if remove_on_request then units[2949124]=nil;emitRemoval(owner,2949124) end
 if not escape_deferred then blocked=false end
 if fail_after_removal then error("request did not return after removal") end
end
ContextPtr={LookUpControl=function() return {SetHide=function() end} end}
''')


def test_query_exposes_real_escaping_identity_and_only_native_legal_routes():
    conn = EscapeConn();state = GameState(conn)
    spies = run(state.get_spies())
    assert len(spies) == 1 and spies[0].unit_id == 2949124
    assert spies[0].is_escaping and spies[0].status == "escaping"
    assert spies[0].available_ops == [] and spies[0].current_mission == "none"
    assert set(spies[0].escape_routes) == {"DISTRICT_HARBOR", "DISTRICT_CITY_CENTER"}
    text = narrate.narrate_spies(spies)
    assert "ESCAPING" in text and "DISTRICT_HARBOR" in text
    assert "DISTRICT_AERODROME" not in text
    assert conn.lua.eval('#escape_requests') == 0


def test_full_native_identity_submits_requested_route_once_and_confirms_gate_resolution():
    conn = EscapeConn();state = GameState(conn)
    result = run(state.spy_escape(2949124, "DISTRICT_HARBOR"))
    assert result.startswith("ESCAPE_ROUTE_CONFIRMED|spy:2949124")
    assert "DISTRICT_HARBOR" in result
    assert conn.lua.eval('#escape_requests') == 1
    assert conn.lua.eval('escape_requests[1].district') == 2
    assert state.pending_action_summary is None


@pytest.fixture
def quick_escape(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, "ACTION_CONFIRM_TIMEOUT", .012)
    monkeypatch.setattr(module, "ACTION_CONFIRM_INTERVAL", .001)


@pytest.mark.parametrize('setup', [
    'blocked=false', 'dismissed=true',
    'NotificationManager.Find=function() return {IsDismissed=function() return false end,GetEndTurnBlocking=function() return 0 end} end',
])
def test_no_active_escape_notification_never_calls_dangerous_getter(setup):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute(setup)
    spies = run(state.get_spies())
    assert not spies[0].is_escaping
    assert run(state.spy_escape(2949124, "DISTRICT_CITY_CENTER")).startswith("Error:")
    assert conn.lua.eval('dangerous_reads') == 0
    assert conn.lua.eval('#escape_requests') == 0


@pytest.mark.parametrize('setup', [
    'NotificationManager.GetList=nil', 'EndTurnBlockingTypes=nil',
    'NotificationManager.Find=function() return nil end',
    'NotificationManager.Find=function() return {IsDismissed=function() return nil end} end',
])
def test_unknown_notification_gate_does_not_probe_native_escaping_id(setup):
    from civ_mcp.connection import LuaError
    conn = EscapeConn();state = GameState(conn);conn.lua.execute(setup)
    with pytest.raises(LuaError, match="unknown"):
        run(state.get_spies())
    assert run(state.spy_escape(2949124, "DISTRICT_CITY_CENTER")).startswith("Error:")
    assert conn.lua.eval('dangerous_reads') == 0
    assert conn.lua.eval('#escape_requests') == 0


@pytest.mark.parametrize('unit_id,change', [
    (4, ''), (2949124, 'spy.id=3014660'),
    (2949124, 'pending_spy=3014660'), (2949124, 'units[2949124]=nil'),
])
def test_low_index_or_other_generation_cannot_replace_native_spy(unit_id, change):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute(change)
    assert run(state.spy_escape(unit_id, "DISTRICT_HARBOR")).startswith("Error:")
    assert conn.lua.eval('#escape_requests') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('route,expected', [('DISTRICT_CITY_CENTER', 0), ('DISTRICT_HARBOR', 2)])
def test_exact_legal_route_is_selected_without_fallback(route, expected):
    conn = EscapeConn();state = GameState(conn)
    assert "ESCAPE_ROUTE_CONFIRMED" in run(state.spy_escape(2949124, route))
    assert conn.lua.eval('escape_requests[1].district') == expected
    assert conn.lua.eval('#escape_requests') == 1


@pytest.mark.parametrize('route,setup', [
    ('DISTRICT_AERODROME', ''), ('DISTRICT_COMMERCIAL_HUB', ''),
    ('DISTRICT_HARBOR', 'has_harbor=false'),
    ('DISTRICT_HARBOR', 'city_missing=true'),
    ('DISTRICT_HARBOR', 'GameInfo.Districts.DISTRICT_HARBOR=nil'),
])
def test_unavailable_route_is_zero_submission_instead_of_city_center_fallback(route, setup):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute(setup)
    assert run(state.spy_escape(2949124, route)).startswith("Error:")
    assert conn.lua.eval('#escape_requests') == 0
    assert state.pending_action_summary is None


def test_deferred_choice_requires_gate_resolution_not_old_mission_or_hidden_popup(quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('escape_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, "DISTRICT_HARBOR"))
    assert state.pending_action_entity == ('unit', 4)
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, "DISTRICT_CITY_CENTER"))
    assert "OUTCOME_UNKNOWN" in run(state.spy_travel(4, 0, 0))
    assert "OUTCOME_UNKNOWN" in run(state.spy_mission(4, "GAIN_SOURCES", 0, 0))
    assert conn.lua.eval('#escape_requests') == 1
    assert conn.lua.eval('#submissions') == 0
    conn.lua.execute('blocked=false')
    reads = conn.lua.eval('dangerous_reads')
    assert "ESCAPE_ROUTE_CONFIRMED" in run(state.spy_escape(2949124, "DISTRICT_HARBOR"))
    assert conn.lua.eval('dangerous_reads') == reads
    assert conn.lua.eval('#escape_requests') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('exception', [OSError, asyncio.CancelledError])
@pytest.mark.parametrize('remove', [False, True])
def test_lost_reply_and_cancel_only_confirm_original_route(exception, remove, quick_escape):
    class LostConn(EscapeConn):
        lost = False
        async def execute_write(self, source):
            rows = await super().execute_write(source)
            if 'UI.RequestPlayerOperation(me,PlayerOperations.SET_ESCAPE_ROUTE,params)' in source and not self.lost:
                self.lost = True
                raise exception('lost callback reply')
            return rows
    conn = LostConn();state = GameState(conn)
    if remove:
        conn.lua.execute("remove_on_request=true")
    if exception is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            run(state.spy_escape(2949124, "DISTRICT_HARBOR"))
    else:
        assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, "DISTRICT_HARBOR"))
    assert state.pending_action_summary
    assert "ESCAPE_ROUTE_CONFIRMED" in run(state.spy_escape(2949124, "DISTRICT_HARBOR"))
    assert conn.lua.eval('#escape_requests') == 1


@pytest.mark.parametrize('change', [
    'seed=99', 'turn=42', 'spy.id=3014660', 'units[2949124]=nil',
    '__civmcpSpyEscape[2949124].token="other"',
])
def test_unknown_confirmation_preserves_pending_identity_or_receipt_gate(change, quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('escape_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, "DISTRICT_HARBOR"))
    conn.lua.execute(change)
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, "DISTRICT_HARBOR"))
    assert state.pending_action_summary
    assert conn.lua.eval('#escape_requests') == 1


def test_pending_escape_blocks_save_and_end_turn(quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('escape_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, "DISTRICT_HARBOR"))
    assert "OUTCOME_UNKNOWN" in run(state.save_game('escape_pending'))
    assert "OUTCOME_UNKNOWN" in run(state.end_turn())
    assert state.pending_action_summary and conn.lua.eval('#escape_requests') == 1


@pytest.mark.parametrize('route', ['', 'DISTRICT_CAMPUS', 'DISTRICT_HARBOR"];print("bad")'])
def test_invalid_route_fails_before_any_native_lookup(route):
    conn = EscapeConn();state = GameState(conn)
    with pytest.raises(ValueError, match='Unknown spy escape route'):
        run(state.spy_escape(2949124, route))
    assert conn.lua.eval('dangerous_reads') == 0
    assert conn.lua.eval('#escape_requests') == 0


def test_live_t148_only_city_center_route_is_exposed():
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('has_harbor=false')
    spy = run(state.get_spies())[0]
    assert spy.unit_id == 2949124 and spy.unit_index == 4
    assert spy.escape_routes == ['DISTRICT_CITY_CENTER']
    assert "DISTRICT_HARBOR" not in narrate.narrate_spies([spy])


def test_query_preserves_native_identity_for_nonzero_local_player():
    conn = EscapeConn();state = GameState(conn)
    conn.lua.execute('''Game.GetLocalPlayer=function() return 2 end;Players[2]=Players[0];
PlayerConfigurations[2]=PlayerConfigurations[0];spy.GetOwner=function() return 2 end''')
    spy = run(state.get_spies())[0]
    assert spy.unit_id == 2949124 and spy.is_escaping


def test_another_escaping_spy_does_not_require_replaying_the_first_choice(quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('escape_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    conn.lua.execute('pending_spy=3670029;other=addUnit(3670029,true,0,0);other.GetType=spy.GetType')
    assert "ESCAPE_ROUTE_CONFIRMED" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    assert conn.lua.eval('#escape_requests') == 1
    assert state.pending_action_summary is None


def test_missing_original_spy_cannot_be_confirmed_by_removed_notification(quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('escape_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    conn.lua.execute('blocked=false;units[2949124]=nil')
    reads = conn.lua.eval('dangerous_reads')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    assert state.pending_action_summary
    assert conn.lua.eval('dangerous_reads') == reads


def test_native_failure_after_entry_cannot_be_replayed_or_confirmed_by_hidden_popup(quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('fail_request=true')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    conn.lua.execute('blocked=false')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    assert state.pending_action_summary and conn.lua.eval('#escape_requests') == 1


def test_changed_world_latch_survives_current_identity_cache_refresh(quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('escape_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    conn.lua.execute('seed=99')
    assert run(state.get_game_identity()) == ('sumeria', 99)
    conn.lua.execute('seed=719665280;blocked=false')
    assert run(state.get_game_identity()) == ('sumeria', 719665280)
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    assert state.pending_action_summary and conn.lua.eval('#escape_requests') == 1


def test_changed_world_at_native_submission_is_zero_dangerous_reads_and_operations():
    class ChangedConn(EscapeConn):
        async def execute_write(self, source):
            if '-- ACTION_SUBMIT_SCOPE|' in source:
                self.lua.execute('seed=99')
            return await super().execute_write(source)
    conn = ChangedConn();state = GameState(conn)
    assert run(state.spy_escape(2949124, 'DISTRICT_HARBOR')).startswith('Error:')
    assert conn.lua.eval('#escape_requests') == 0 and conn.lua.eval('dangerous_reads') == 0
    assert state.pending_action_summary is None


def test_missing_next_spy_cannot_confirm_queue_advancement(quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('escape_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    conn.lua.execute('pending_spy=3670029')
    assert "OUTCOME_UNKNOWN" in run(state.spy_escape(2949124, 'DISTRICT_HARBOR'))
    assert state.pending_action_summary and conn.lua.eval('#escape_requests') == 1


def test_precise_native_removal_after_route_selection_confirms_choice_without_claiming_survival():
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('remove_on_request=true')
    result = run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    assert result.startswith('ESCAPE_ROUTE_CONFIRMED|spy:2949124')
    assert 'entity:removed_event' in result
    assert 'survival outcome not measured' in result
    assert conn.lua.eval('#escape_requests') == 1
    assert conn.lua.eval('#removal_handlers') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('event', [
    'emitRemoval(0,4)', 'emitRemoval(0,3014660)', 'emitRemoval(1,2949124)',
    'seed=99;emitRemoval(0,2949124);seed=719665280',
])
def test_unattributed_or_other_world_removal_cannot_confirm_missing_original(event, quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('escape_deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    conn.lua.execute(event+';units[2949124]=nil;blocked=false')
    assert 'OUTCOME_UNKNOWN' in run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    assert state.pending_action_summary and conn.lua.eval('#escape_requests') == 1


def test_event_before_request_entry_is_not_evidence_of_selected_route(quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('event_on_arm=true;escape_deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    conn.lua.execute('units[2949124]=nil;blocked=false')
    assert 'OUTCOME_UNKNOWN' in run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    assert state.pending_action_summary


def test_late_precise_removal_only_confirms_original_pending_choice(quick_escape):
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('escape_deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    conn.lua.execute('units[2949124]=nil;emitRemoval(0,2949124);blocked=false')
    result = run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    assert result.startswith('ESCAPE_ROUTE_CONFIRMED') and 'entity:removed_event' in result
    assert conn.lua.eval('#escape_requests') == 1
    assert conn.lua.eval('#removal_handlers') == 0


def test_unavailable_native_removal_observer_is_zero_submission():
    conn = EscapeConn();state = GameState(conn);conn.lua.execute('Events=nil')
    assert run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER')).startswith('Error:')
    assert state.pending_action_summary is None and conn.lua.eval('#escape_requests') == 0


def test_precise_removal_without_resolved_gate_remains_unknown(quick_escape):
    conn = EscapeConn();state = GameState(conn)
    conn.lua.execute('remove_on_request=true;escape_deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    assert state.pending_action_summary and conn.lua.eval('#escape_requests') == 1
    conn.lua.execute('blocked=false')
    assert 'ESCAPE_ROUTE_CONFIRMED' in run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    assert conn.lua.eval('#escape_requests') == 1


def test_precise_removal_without_returned_request_cannot_confirm(quick_escape):
    conn = EscapeConn();state = GameState(conn)
    conn.lua.execute('remove_on_request=true;fail_after_removal=true')
    assert 'OUTCOME_UNKNOWN' in run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    assert 'OUTCOME_UNKNOWN' in run(state.spy_escape(2949124, 'DISTRICT_CITY_CENTER'))
    assert state.pending_action_summary and conn.lua.eval('#escape_requests') == 1

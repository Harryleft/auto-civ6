"""Read-only religious defense via public MCP/GameState and executed Lua 5.1."""
import asyncio

from lupa.lua51 import LuaRuntime, LuaError as NativeLuaError

from civ_mcp import server
from civ_mcp.game_state import GameState
from civ_mcp.connection import LuaError
from test_server import app_of, make_ctx


class DefenseConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.context = 'InGame'
        self.after_core = None
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.globals().nativeContext = lambda: self.context
        self.lua.execute('''
turn,seed,owner=139,-1896272000,0
coreTurn,coreSeed,coreOwner=139,-1896272000,0
faith,created,inquisition=77,12,false
coreCreated,coreInquisition=12,true
mutations=0
Game={GetLocalPlayer=function() if nativeContext()=='GameCore' then return coreOwner else return owner end end,
 GetCurrentGameTurn=function() if nativeContext()=='GameCore' then return coreTurn else return turn end end}
GameConfiguration={GetValue=function() if nativeContext()=='GameCore' then return coreSeed else return seed end end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function() return 'CIVILIZATION_SUMERIA' end}}
Locale={Lookup=function(s) return s end}
GameInfo={Religions={[12]={ReligionType='RELIGION_ZOROASTRIANISM'},[7]={ReligionType='RELIGION_PROTESTANTISM'}},
 Units={[1]={UnitType='UNIT_MUSKETMAN',TrackReligion=false,ReligiousStrength=0},
 [2]={UnitType='UNIT_MISSIONARY',TrackReligion=true,ReligiousStrength=100}},
 UnitCommands={UNITCOMMAND_CONDEMN_HERETIC={CommandType='UNITCOMMAND_CONDEMN_HERETIC',Hash=99}}}
religion={GetReligionTypeCreated=function() if nativeContext()=='GameCore' then return coreCreated else return created end end,
 HasLaunchedInquisition=function() if nativeContext()=='GameCore' then return coreInquisition else return inquisition end end,
 GetFaithBalance=function() return faith end,GetReligionInMajorityOfCities=function() return 12 end}
function makeCity(id,name,majority,rows)
 local c={id=id,name=name,majority=majority,rows=rows}
 c.GetID=function(self) return self.id end;c.GetOwner=function() return 0 end
 c.GetName=function(self) return self.name end;c.GetX=function() return 34 end;c.GetY=function() return 26 end
 c.GetPopulation=function() return 5 end
 c.rel={GetMajorityReligion=function() return c.majority end,GetReligionsInCity=function() return c.rows end,
 GetTotalPressureOnCity=function(_,rel) return rel==7 and 6 or 2 end}
 c.GetReligion=function() return c.rel end
 return c
end
cities={makeCity(65536,'Uruk',12,{{Religion=12,Followers=4,Pressure=1200},{Religion=7,Followers=1,Pressure=100}}),
 makeCity(327684,'Lagash',12,{{Religion=12,Followers=3,Pressure=400},{Religion=7,Followers=2,Pressure=350}})}
function makeUnit(id,pid,typ,x,y)
 local u={id=id,owner=pid,typ=typ,x=x,y=y,visible=true,charges=3}
 u.GetID=function(self) return self.id end;u.GetOwner=function(self) return self.owner end
 u.GetType=function(self) return self.typ end;u.GetX=function(self) return self.x end;u.GetY=function(self) return self.y end
 u.GetReligionType=function() return 7 end;u.GetSpreadCharges=function(self) return self.charges end
 u.GetMovesRemaining=function() return 2 end;u.IsReadyToMove=function() return true end
 return u
end
ownUnits={makeUnit(196608,0,1,34,26)}
foreignUnits={makeUnit(262145,1,2,34,26),makeUnit(327682,1,2,80,80)}
Players={[0]={GetCities=function() return {Members=function() return ipairs(cities) end} end,
 GetUnits=function() return {Members=function() return ipairs(ownUnits) end} end,GetReligion=function() return religion end,
 GetStats=function() return {GetNumCitiesFollowingReligion=function() return 9 end} end,
 GetDiplomacy=function() return {IsAtWarWith=function() return false end} end},
 [1]={GetUnits=function() return {Members=function() return ipairs(foreignUnits) end} end}}
PlayersVisibility={[0]={IsVisible=function(_,x,y) return x~=80 end,IsUnitVisible=function() return true end}}
UnitManager={CanStartCommand=function(u,cmd,a,b) assert(cmd==99 and a==false and b==true);return true end,
 RequestOperation=function() mutations=mutations+1;error('read query mutated') end,
 RequestCommand=function() mutations=mutations+1;error('read query mutated') end}
UI={RequestPlayerOperation=function() mutations=mutations+1;error('read query mutated') end,
 SetInterfaceMode=function() mutations=mutations+1;error('read query changed UI') end}
''')

    async def execute_write(self, code):
        self.context = 'InGame'
        self.rows.clear()
        try:
            self.lua.execute(code)
        except NativeLuaError as exc:
            raise LuaError(str(exc)) from exc
        return [r for r in self.rows if r != '---END---']

    async def execute_read(self, code):
        self.context = 'GameCore'
        self.rows.clear()
        self.lua.execute(code)
        rows = [r for r in self.rows if r != '---END---']
        if self.after_core:
            self.lua.execute(self.after_core)
        return rows


def query(conn):
    ctx = make_ctx(); app_of(ctx).game = GameState(conn)
    return asyncio.run(server.get_religion_defense(ctx))


def test_public_query_reports_owned_pressure_and_visible_peaceful_religious_layer():
    conn = DefenseConn()
    result = query(conn)
    assert 'Lagash' in result and 'accumulated_pressure=350' in result and 'incoming_pressure=6' in result
    assert 'owned_city_majorities: religion=12 count=2 / owned_cities=2' in result
    assert 'native_religion_city_stat=9 (engine statistic; not owned-city count)' in result
    assert 'unit=262145 owner=1' in result and 'at_war=false' in result
    assert '327682' not in result
    assert 'actor=196608 target=262145 native_can_condemn=true' in result
    assert 'permission_source=unknown expiry=unknown' in result
    assert 'inquisition: InGame=false GameCore=true conflict' in result
    assert conn.lua.eval('mutations') == 0


def test_unknown_native_getters_stay_unknown_instead_of_false_or_zero():
    conn = DefenseConn()
    conn.lua.execute('''
religion.GetFaithBalance=function() error('unavailable') end
foreignUnits[1].GetSpreadCharges=function() return nil end
foreignUnits[1].IsReadyToMove=function() error('unavailable') end
Players[0].GetDiplomacy=function() return {IsAtWarWith=function() return nil end} end
UnitManager.CanStartCommand=function() return nil end
cities[2].rows[2].Pressure=nil
cities[2].rel.GetTotalPressureOnCity=function() return nil end
''')
    result = query(conn)
    assert 'faith: InGame=unknown GameCore=unknown' in result
    assert 'charges=unknown' in result and 'ready=unknown' in result
    assert 'at_war=unknown' in result
    assert 'native_can_condemn=unknown' in result
    assert 'accumulated_pressure=unknown incoming_pressure=unknown' in result
    assert 'city=327684 pressure_records=partial' in result


def test_visible_plot_does_not_disclose_hidden_unit_or_claim_complete_absence():
    conn = DefenseConn()
    conn.lua.execute('''
PlayersVisibility[0].IsUnitVisible=function(_,u) return u.id~=262145 end
foreignUnits[1].GetReligionType=function() error('hidden religious details must not be read') end
''')
    result = query(conn)
    assert 'unit=262145' not in result
    assert 'target=262145' not in result
    assert 'hidden units not inspected' in result


def test_scope_change_is_exposed_even_beside_unknown_field_and_final_restoration():
    conn = DefenseConn()
    conn.lua.execute('coreSeed=4;coreTurn=nil')
    result = query(conn)
    assert 'context_scope=conflicting' in result
    assert 'scope seed mismatch' in result
    assert 'GameCore:start owner=0' in result and 'turn=unknown' in result


def test_final_ingame_scope_is_read_again_after_core():
    conn = DefenseConn(); conn.after_core = 'turn=140'
    result = query(conn)
    assert 'context_scope=conflicting' in result
    assert 'scope turn mismatch' in result


def test_native_local_majority_conflict_does_not_relabel_global_stat():
    conn = DefenseConn()
    conn.lua.execute('cities[1].majority=7;cities[2].majority=7')
    result = query(conn)
    assert 'owned_city_majorities: religion=7 count=2 / owned_cities=2' in result
    assert 'owned-city majority=7 disagrees with native_majority=12' in result
    assert 'native_religion_city_stat=9 (engine statistic; not owned-city count)' in result


def test_unknown_visibility_and_city_getter_report_partial_scan():
    conn = DefenseConn()
    conn.lua.execute('''
PlayersVisibility[0].IsUnitVisible=function() return nil end
Players[0].GetCities=function() error('unavailable') end
''')
    result = query(conn)
    assert 'visible_religious_units_scan=partial' in result
    assert 'unit=262145' not in result
    assert 'owned_city_majorities:' not in result
    assert 'owned_cities_scan=partial' in result


def test_active_congress_raw_rows_are_not_historical_permission_or_guessed_target():
    conn = DefenseConn()
    conn.lua.execute('''
GameInfo.Resolutions={[6]={ResolutionType='WC_RES_WORLD_RELIGION'}}
Game.GetWorldCongress=function() return {
 GetResolutions=function(_,pid) assert(pid==0);return {Stage=2,{Type=6,ChosenOption='LOC_RES_B',ChosenThing='LOC_PROTESTANT',ChosenLabel='LOC_RELIGION'}} end,
 GetMeetingStatus=function() return {TurnsLeft=9} end,
 GetReview=function() mutations=mutations+1;error('historical review must not be used') end} end
UnitManager.CanStartCommand=function() return false end
''')
    result = query(conn)
    assert 'active_world_religion=present' in result
    assert 'ChosenOption=LOC_RES_B ChosenThing=LOC_PROTESTANT ChosenLabel=LOC_RELIGION' in result
    assert 'meeting_turns_left_raw=9 ui_turns_remaining=10' in result
    assert 'native_can_condemn=false' in result
    assert 'target_religion=unknown effect=unknown' in result
    assert 'permission_source=unknown expiry=unknown' in result
    assert conn.lua.eval('mutations') == 0


def test_read_query_preserves_existing_unknown_action_and_performs_no_cleanup(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)
    conn = DefenseConn()
    conn.lua.execute('''
ActivityTypes={ACTIVITY_AWAKE=0,ACTIVITY_HOLD=2,ACTIVITY_OPERATION=3}
GameInfo.UnitOperations={UNITOPERATION_SKIP_TURN={Hash=10}}
UnitManager.GetUnit=function(pid,id) return ownUnits[1] end
UnitManager.GetActivityType=function() return 0 end
UnitManager.CanStartOperation=function() return true end
''')
    state = GameState(conn)
    ctx = make_ctx(); app_of(ctx).game = state
    result = asyncio.run(state.skip_unit(196608))
    assert 'OUTCOME_UNKNOWN' in result
    original = state.pending_action_summary
    before = conn.lua.eval('mutations')
    assert 'Lagash' in asyncio.run(server.get_religion_defense(ctx))
    assert state.pending_action_summary == original
    assert conn.lua.eval('mutations') == before


def test_city_majority_and_follower_disagreement_is_exposed():
    conn = DefenseConn()
    conn.lua.execute('cities[2].rows[1].Followers=2;cities[2].rows[2].Followers=3')
    result = query(conn)
    assert 'city=327684 native majority=12 disagrees with follower majority=7' in result


def test_ambiguous_visible_religious_targets_do_not_bind_native_qualification_to_one():
    conn = DefenseConn()
    conn.lua.execute('foreignUnits[3]=makeUnit(458755,1,2,34,26)')
    result = query(conn)
    assert 'actor=196608 target=unknown native_can_condemn=true binding=ambiguous_or_partial' in result


def test_complete_active_list_absence_is_distinct_from_unavailable_congress():
    conn = DefenseConn()
    assert 'active_world_religion=unknown active_resolution_scan=partial' in query(conn)
    conn.lua.execute('Game.GetWorldCongress=function() return {GetResolutions=function() return {Stage=1} end,GetMeetingStatus=function() return {TurnsLeft=0} end} end')
    result = query(conn)
    assert 'active_world_religion=absent active_resolution_scan=complete' in result
    assert 'meeting_turns_left_raw=0 ui_turns_remaining=1' in result


def test_foreign_cities_are_never_queried_or_disclosed():
    conn = DefenseConn()
    conn.lua.execute("Players[1].GetCities=function() mutations=mutations+1;error('foreign city details are forbidden') end")
    assert 'owned_cities=2' in query(conn)
    assert conn.lua.eval('mutations') == 0


def test_native_global_stat_disagreement_is_scoped_and_distinct_from_owned_counts():
    conn = DefenseConn()
    conn.lua.execute("Players[0].GetStats=function() return {GetNumCitiesFollowingReligion=function() if nativeContext()=='GameCore' then return 8 else return 9 end end} end")
    result = query(conn)
    assert 'native_religion_city_stat: InGame=9 GameCore=8 conflict' in result
    assert 'native_religion_city_stat context disagreement' in result
    assert 'owned_city_majorities: religion=12 count=2 / owned_cities=2' in result


def test_missing_core_context_remains_partial_without_erasing_ingame_facts():
    class UnavailableCore(DefenseConn):
        async def execute_read(self, code):
            raise OSError('GameCore unavailable')
    conn = UnavailableCore()
    result = query(conn)
    assert 'context_scope=partial' in result
    assert 'inquisition: InGame=false GameCore=unknown' in result
    assert 'unavailable: GameCore: OSError' in result
    assert 'accumulated_pressure=350 incoming_pressure=6' in result
    assert conn.lua.eval('mutations') == 0


def test_native_command_failure_reasons_do_not_become_inferred_permission_source():
    conn = DefenseConn()
    conn.lua.execute("UnitOperationResults={FAILURE_REASONS=4};UnitManager.CanStartCommand=function() return false,{[4]={'LOC_RELIGION_SAME_AS_CITY'}} end")
    result = query(conn)
    assert 'actor=196608 failure_reason=LOC_RELIGION_SAME_AS_CITY' in result
    assert 'permission_source=unknown expiry=unknown' in result


def test_t131_native_half_population_majority_is_not_overruled_by_derived_threshold():
    # Original run #2494: Eridu had 12 people, own religion 6, Protestant 4,
    # and no religion 2; the native majority getter still returned religion 12.
    # Pressure stocks below are adapter fixtures, not historical measurements.
    conn = DefenseConn()
    conn.lua.execute('''
turn,coreTurn=131,131
cities[2]=makeCity(131073,'Eridu',12,{
 {Religion=12,Followers=6,Pressure=1200},
 {Religion=7,Followers=4,Pressure=500},
 {Religion=-1,Followers=2,Pressure=100}})
cities[2].GetPopulation=function() return 12 end
cities[2].GetX=function() return 35 end;cities[2].GetY=function() return 22 end
''')
    result = query(conn)
    assert 'name=Eridu plot=35,22 population=12 majority=12' in result
    assert 'city=131073 native majority=12 disagrees' not in result
    assert 'city=131073 follower_majority_consistency=unknown' in result


def test_half_owned_city_count_does_not_overrule_native_majority():
    conn = DefenseConn()
    conn.lua.execute('cities[2].majority=7;cities[2].rows[1].Followers=2;cities[2].rows[2].Followers=3')
    result = query(conn)
    assert 'owned_city_majorities: religion=12 count=1 / owned_cities=2' in result
    assert 'owned_city_majorities: religion=7 count=1 / owned_cities=2' in result
    assert 'disagrees with native_majority=12' not in result
    assert 'owned_city_majority_consistency=unknown' in result

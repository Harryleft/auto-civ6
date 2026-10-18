"""Native victory facts through public GameState, with isolated Lua 5.1 data."""

import asyncio

from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_victory_progress


class VictoryConn:
    def __init__(self):
        self.lua=LuaRuntime()
        self.rows=[]
        self.calls=[]
        self.lua.globals().print=lambda row:self.rows.append(str(row))
        self.lua.execute('''
turn=80
science={[0]=0,[1]=0,[2]=30}
needed=33.5
ports={[0]=1,[1]=1,[2]=4}
completed={}
current_hash=-1
listed,ready=true,false
Game={GetLocalPlayer=function() return 0 end,GetCurrentGameTurn=function() return turn end,
      IsVictoryEnabled=function() return true end}
GlobalParameters={DIPLOMATIC_VICTORY_POINTS_REQUIRED=20}
Locale={Lookup=function(value) return value end}
local function iterable(rows,field)
    local result={}
    for _,row in ipairs(rows) do
        result[row.Index]=row
        if field then result[row[field]]=row end
    end
    return setmetatable(result,{__call=function() local i=0;return function() i=i+1;return rows[i] end end})
end
GameInfo={
    Projects=iterable({
        {Index=201,Hash=501,ProjectType="PROJECT_LAUNCH_EARTH_SATELLITE",Name="Satellite",PrereqTech="TECH_MODDED_ROCKETS"},
        {Index=202,Hash=502,ProjectType="PROJECT_LAUNCH_MOON_LANDING",Name="Moon",PrereqTech="TECH_SATELLITES"},
        {Index=203,Hash=503,ProjectType="PROJECT_LAUNCH_MARS_BASE",Name="Mars",PrereqTech="TECH_NANOTECHNOLOGY"},
        {Index=204,Hash=504,ProjectType="PROJECT_LAUNCH_EXOPLANET_EXPEDITION",Name="Exoplanet",PrereqTech="TECH_SMART_MATERIALS"},
    },"ProjectType"),
    Technologies={TECH_MODDED_ROCKETS={Index=10},TECH_SATELLITES={Index=11},
                  TECH_NANOTECHNOLOGY={Index=12},TECH_SMART_MATERIALS={Index=13}},
    Districts={[1]={DistrictType="DISTRICT_SPACEPORT"}},Religions={},
    Victories=iterable({{Index=1,VictoryType="VICTORY_TECHNOLOGY"},
        {Index=2,VictoryType="VICTORY_CULTURE"},{Index=3,VictoryType="VICTORY_DIPLOMATIC"},
        {Index=4,VictoryType="VICTORY_RELIGIOUS"},{Index=5,VictoryType="VICTORY_CONQUEST"}},"VictoryType"),
}
Players,PlayerConfigurations={},{}
for owner=0,2 do
    local id=owner
    local queue={
        GetCurrentProductionTypeHash=function() return current_hash end,
        GetProductionCost=function() return 100 end,GetProductionProgress=function() return 10 end,
        GetProjectCost=function() return 100 end,GetProjectProgress=function() return 10 end,
        GetTurnsLeft=function() return 4 end,
        CanProduce=function(_,hash,exclusion,results)
            assert(hash>=501 and hash<=504)
            if exclusion then return listed end
            assert(exclusion==false and results==true)
            return ready
        end,
    }
    local city={GetName=function() return "Capital" end,GetPopulation=function() return 5 end,
        GetYield=function() return 10 end,IsOriginalCapital=function() return true end,
        GetBuildQueue=function() return queue end,
        GetDistricts=function() return {Members=function()
            local rows={}
            for i=1,ports[id] do rows[i]={GetType=function() return 1 end,IsComplete=function() return true end} end
            return ipairs(rows)
        end} end,
    }
    local stats={
        GetScienceVictoryPoints=function() return science[id] end,
        GetScienceVictoryPointsTotalNeeded=function() return needed end,
        GetScienceVictoryPointsPerTurn=function() return 2 end,
        GetNumProjectsAdvanced=function(_,index) return completed[index] or 0 end,
        GetDiplomaticVictoryPoints=function() return 0 end,GetTourism=function() return 2 end,
        GetMilitaryStrength=function() return 100 end,GetNumTechsResearched=function() return 10 end,
        GetNumCivicsCompleted=function() return 10 end,GetNumCitiesFollowingReligion=function() return 0 end,
    }
    local culture={GetStaycationers=function() return id==2 and 40 or 20 end,
        GetTouristsTo=function() return 1 end,GetTouristsFrom=function() return 0 end,
        IsDominantOver=function() return false end,GetCultureYield=function() return 5 end}
    Players[id]={IsAlive=function() return true end,IsMajor=function() return true end,
        GetTeam=function() return id end,GetStats=function() return stats end,
        GetScore=function() return 100 end,GetCulture=function() return culture end,
        GetReligion=function() return {GetReligionTypeCreated=function() return -1 end,
                                     GetReligionInMajorityOfCities=function() return -1 end} end,
        GetTechs=function() return {HasTech=function() return true end,GetScienceYield=function() return 10 end} end,
        GetTreasury=function() return {GetGoldYield=function() return 10 end,GetTotalMaintenance=function() return 2 end} end,
        GetDiplomacy=function() return {HasMet=function(_,other) return other~=2 end} end,
        GetCities=function() return {Members=function() return ipairs({city}) end,
                                    GetCapitalCity=function() return city end} end,
    }
    PlayerConfigurations[id]={GetCivilizationShortDescription=function() return id==2 and "Hidden rival" or id==1 and "Known rival" or "Our civilization" end}
end
Map={GetCityPlots=function() return {GetPurchasedPlots=function() return {1,2,3} end} end}
''')

    async def execute_write(self,source):
        self.calls.append(source)
        self.rows.clear()
        self.lua.execute(source)
        return [row for row in self.rows if row!='---END---']


def run(awaitable):
    return asyncio.run(awaitable)


def test_science_progress_keeps_fractional_native_requirement_in_public_query_and_text():
    conn=VictoryConn()
    facts=run(GameState(conn).get_victory_progress())
    assert facts.players[0].science_vp_needed==33.5
    text=narrate_victory_progress(facts)
    assert '0/33.5 VP' in text
    assert 'Hidden rival' not in text


def test_active_projects_use_native_prerequisites_completion_and_actual_production_eligibility():
    conn=VictoryConn()
    facts=run(GameState(conn).get_victory_progress())
    projects={project.project_type:project for project in facts.space_projects}
    assert set(projects)=={
        'PROJECT_LAUNCH_EARTH_SATELLITE','PROJECT_LAUNCH_MOON_LANDING',
        'PROJECT_LAUNCH_MARS_BASE','PROJECT_LAUNCH_EXOPLANET_EXPEDITION',
    }
    assert projects['PROJECT_LAUNCH_EARTH_SATELLITE'].tech_prereq=='TECH_MODDED_ROCKETS'
    assert projects['PROJECT_LAUNCH_MARS_BASE'].tech_prereq=='TECH_NANOTECHNOLOGY'
    assert projects['PROJECT_LAUNCH_EXOPLANET_EXPEDITION'].tech_prereq=='TECH_SMART_MATERIALS'
    assert all(project.status=='unlocked' for project in projects.values())
    assert all(project.can_produce is False and project.completed_count==0 for project in projects.values())


def test_project_native_queue_and_completion_take_precedence_and_missing_api_is_unknown():
    conn=VictoryConn()
    conn.lua.execute('science[0]=15; completed[201]=1; current_hash=501; listed=true; ready=true')
    projects=run(GameState(conn).get_victory_progress()).space_projects
    satellite=projects[0]
    assert satellite.status=='building'
    assert (satellite.progress_pct,satellite.turns_remaining,satellite.cost)==(10,4,100)
    assert satellite.city_name=='Capital'
    conn.lua.execute('current_hash=-1')
    assert run(GameState(conn).get_victory_progress()).space_projects[0].status=='completed'
    conn.lua.execute('Players[0]:GetStats().GetNumProjectsAdvanced=nil')
    projects=run(GameState(conn).get_victory_progress()).space_projects
    assert all(project.status=='unknown' and project.completed_count is None for project in projects)


def test_project_missing_prerequisite_row_and_failed_eligibility_are_explicit_unknowns():
    conn=VictoryConn()
    conn.lua.execute('GameInfo.Technologies.TECH_MODDED_ROCKETS=nil; ready=nil')
    satellite=run(GameState(conn).get_victory_progress()).space_projects[0]
    assert satellite.status=='unknown'
    assert satellite.has_tech is None
    assert satellite.can_produce is None


def test_zero_progress_spaceport_warns_early_with_native_culture_and_diplomatic_thresholds():
    conn=VictoryConn()
    conn.lua.execute('GlobalParameters.DIPLOMATIC_VICTORY_POINTS_REQUIRED=25')
    facts=run(GameState(conn).get_victory_progress())
    assert facts.turn==80 and facts.local_player_id==0 and facts.complete
    assert facts.enabled_known is True
    assert facts.diplomatic_vp_needed==25
    rival=next(player for player in facts.players if player.player_id==1)
    assert rival.culture_visiting==1 and rival.culture_required==41
    assert rival.science_rate==2 and rival.spaceports==1
    text=narrate_victory_progress(facts)
    assert 'Known rival: space race observed' in text
    assert '0/33.5' in text and '1 completed spaceport' in text
    assert 'visiting tourists 1/41' in text
    assert '0/25 VP' in text
    assert 'Hidden rival' not in text
    assert 'heuristic' in text and 'get_victory_progress' in text


def test_base_rules_only_report_existing_milestones_including_mars_habitation():
    conn=VictoryConn()
    conn.lua.execute('''
GameInfo.Projects.PROJECT_LAUNCH_MARS_BASE=nil
GameInfo.Projects.PROJECT_LAUNCH_EXOPLANET_EXPEDITION=nil
for index,key in ipairs({'PROJECT_LAUNCH_MARS_REACTOR','PROJECT_LAUNCH_MARS_HABITATION','PROJECT_LAUNCH_MARS_HYDROPONICS'}) do
    GameInfo.Projects[key]={Index=210+index,Hash=501,ProjectType=key,Name=key,PrereqTech='TECH_MODDED_ROCKETS'}
end
''')
    facts=run(GameState(conn).get_victory_progress())
    assert {project.project_type for project in facts.space_projects}=={
        'PROJECT_LAUNCH_EARTH_SATELLITE','PROJECT_LAUNCH_MOON_LANDING',
        'PROJECT_LAUNCH_MARS_REACTOR','PROJECT_LAUNCH_MARS_HABITATION','PROJECT_LAUNCH_MARS_HYDROPONICS',
    }
    assert all(project.status=='unlocked' for project in facts.space_projects)


def test_ready_requires_both_native_checks_and_locked_technology_stays_locked():
    conn=VictoryConn()
    conn.lua.execute('listed=false; ready=true')
    assert all(project.status=='unlocked' and project.can_produce is False
               for project in run(GameState(conn).get_victory_progress()).space_projects)
    conn.lua.execute('listed=true')
    assert all(project.status=='ready' and project.can_produce is True
               for project in run(GameState(conn).get_victory_progress()).space_projects)
    conn.lua.execute('Players[0].GetTechs=function() return {HasTech=function() return false end,GetScienceYield=function() return 10 end} end')
    assert all(project.status=='locked' for project in run(GameState(conn).get_victory_progress()).space_projects)


def test_risk_uses_native_fractional_progress_and_real_culture_and_diplomatic_requirements():
    conn=VictoryConn()
    conn.lua.execute('''
science[1]=25.5
GlobalParameters.DIPLOMATIC_VICTORY_POINTS_REQUIRED=25
Players[1]:GetStats().GetDiplomaticVictoryPoints=function() return 13 end
Players[1]:GetCulture().GetTouristsTo=function() return 40 end
''')
    text=narrate_victory_progress(run(GameState(conn).get_victory_progress()))
    assert '25.5/33.5 VP | near requirement (>=75%)' in text
    assert 'visiting tourists 40/41 (>=75%)' in text
    assert 'diplomatic 13/25 VP (>=50%)' in text
    assert 'Hidden rival' not in text


def test_disabled_victories_do_not_generate_risk_even_with_apparent_winning_values():
    conn=VictoryConn()
    conn.lua.execute('Game.IsVictoryEnabled=function() return false end; science[1]=1000')
    facts=run(GameState(conn).get_victory_progress())
    assert facts.enabled_known and not facts.enabled_victories
    text=narrate_victory_progress(facts)
    assert 'Enabled: none.' in text
    assert 'Victory risk' not in text and 'SCIENCE' not in text


def test_unknown_critical_native_fields_remain_unknown_without_default_requirements():
    conn=VictoryConn()
    conn.lua.execute('''
needed=0/0
science[1]=math.huge
GlobalParameters.DIPLOMATIC_VICTORY_POINTS_REQUIRED=nil
Players[1]:GetCulture().GetStaycationers=nil
''')
    facts=run(GameState(conn).get_victory_progress())
    rival=next(player for player in facts.players if player.player_id==1)
    assert rival.science_vp is None and rival.science_vp_needed is None
    assert facts.diplomatic_vp_needed is None
    assert facts.players[0].culture_required is None
    text=narrate_victory_progress(facts)
    assert 'unknown/unknown VP' in text
    assert 'Unknown observations:' in text
    assert '/50 VP' not in text and '/20 VP' not in text


def test_missing_enabled_api_marks_unknown_instead_of_inventing_safe_or_disabled_state():
    conn=VictoryConn()
    conn.lua.execute('Game.IsVictoryEnabled=nil')
    facts=run(GameState(conn).get_victory_progress())
    assert facts.enabled_known is False
    assert 'Victory warnings unavailable' in narrate_victory_progress(facts)


def test_full_and_confirmed_turn_views_share_native_facts_without_unmet_player_details():
    from civ_mcp import lua as lq
    from civ_mcp.narrate import narrate_victory_warnings

    conn=VictoryConn()
    conn.lua.execute('science[1]=25.5; science[2]=999; needed=33.5')
    full=run(GameState(conn).get_victory_progress())
    light=lq.parse_victory_progress_response(run(conn.execute_write(lq.build_victory_proximity_query())))
    assert [(p.player_id,p.science_vp,p.science_vp_needed,p.spaceports,p.culture_required)
            for p in full.players]==[(p.player_id,p.science_vp,p.science_vp_needed,p.spaceports,p.culture_required)
                                    for p in light.players]
    assert [player.player_id for player in light.players]==[0,1]
    assert narrate_victory_warnings(full)==narrate_victory_warnings(light)
    assert 'Hidden rival' not in '\n'.join(conn.rows)
    assert '999' not in '\n'.join(conn.rows)
    assert not any('Request' in source or 'Network.SaveGame' in source for source in conn.calls)


def test_completed_native_project_warns_even_if_port_and_progress_are_currently_zero():
    conn=VictoryConn()
    conn.lua.execute('ports[1]=0; completed[201]=1')
    text=narrate_victory_progress(run(GameState(conn).get_victory_progress()))
    assert 'Known rival: space race observed | 0 completed spaceport(s) | 0/33.5 VP' in text


def test_religious_warning_uses_majority_aggregate_without_naming_unmet_civilizations():
    conn=VictoryConn()
    conn.lua.execute('''
GameInfo.Religions[1]={ReligionType='RELIGION_TEST',Name='Known religion'}
Players[1].GetReligion=function() return {GetReligionTypeCreated=function() return 1 end,
    GetReligionInMajorityOfCities=function() return 1 end} end
Players[2].GetReligion=function() return {GetReligionTypeCreated=function() return -1 end,
    GetReligionInMajorityOfCities=function() return 1 end} end
''')
    text=narrate_victory_progress(run(GameState(conn).get_victory_progress()))
    assert 'Known rival: founded religion is majority in 2/3 civilizations' in text
    assert 'Hidden rival' not in text


def test_native_localized_names_keep_utf8_and_cannot_split_protocol_fields():
    conn=VictoryConn()
    conn.lua.execute("PlayerConfigurations[1].GetCivilizationShortDescription=function() return '世界博览会|交流\\n对象' end")
    facts=run(GameState(conn).get_victory_progress())
    rival=next(player for player in facts.players if player.player_id==1)
    assert rival.name=='世界博览会/交流 对象'
    assert '世界博览会/交流 对象: space race observed' in narrate_victory_progress(facts)

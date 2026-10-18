"""Native pillage submission and damage witnesses, all in the InGame context."""
from civ_mcp.lua._helpers import SENTINEL, _bail, _lua_get_unit


# Official UnitPanel submits UNITOPERATION_PILLAGE. PlotToolTip reads the
# improvement, city district, and every building at this plot independently:
# a building can be damaged while the district itself remains intact.
_LUA_PILLAGE_STATE = '''
local function pillageState(unit)
    local x,y=unit:GetX(),unit:GetY()
    assert(type(x)=="number" and type(y)=="number" and x>=0 and y>=0)
    local plot=Map.GetPlot(x,y)
    assert(plot)
    local owner,index=plot:GetOwner(),plot:GetIndex()
    local improvement,district=plot:GetImprovementType(),plot:GetDistrictType()
    assert(type(owner)=="number" and type(index)=="number")
    assert(type(improvement)=="number" and type(district)=="number")
    local city=Cities.GetPlotPurchaseCity(plot)
    local cityID,cityOwner=-1,-1
    local states={}
    local function addState(key,value)
        assert(type(value)=="boolean")
        states[key]=value
    end
    if city then cityID,cityOwner=city:GetID(),city:GetOwner() end
    if improvement>=0 then addState("improvement:"..improvement,plot:IsImprovementPillaged()) end
    if district>=0 then
        assert(city and cityOwner==owner)
        addState("district:"..district,city:GetDistricts():IsPillaged(district,index))
        local cityBuildings=city:GetBuildings()
        local ids=cityBuildings:GetBuildingsAtLocation(index)
        assert(type(ids)=="table")
        for _,id in ipairs(ids) do
            assert(type(id)=="number")
            addState("building:"..id,cityBuildings:IsPillaged(id))
        end
    end
    local keys={}
    for key in pairs(states) do
        table.insert(keys,key)
    end
    assert(#keys>0)
    table.sort(keys)
    local target=table.concat({owner,index,cityOwner,cityID,improvement,district},",")
    local encoded={target}
    for _,key in ipairs(keys) do table.insert(encoded,key..":"..tostring(states[key])) end
    return {x=x,y=y,owner=owner,target=target,states=states,keys=keys,
            fingerprint=table.concat(encoded,";")}
end
local function pillageEligibility(unit,me)
    assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
    assert(unit:GetOwner()==me,"UNIT_OWNER_CHANGED")
    local state=pillageState(unit)
    assert(state.owner>=0 and state.owner~=me,"NOT_ENEMY_TARGET")
    assert(Players[me]:GetDiplomacy():IsAtWarWith(state.owner)==true,"NOT_AT_WAR")
    local operation=GameInfo.UnitOperations["UNITOPERATION_PILLAGE"]
    assert(operation and type(operation.Hash)=="number" and UnitOperationTypes
        and operation.Hash==UnitOperationTypes.PILLAGE,"OPERATION_UNAVAILABLE")
    assert(type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
    local allowed=UnitManager.CanStartOperation(unit,operation.Hash,nil,false,true)
    assert(type(allowed)=="boolean","ELIGIBILITY_UNKNOWN")
    assert(allowed,"CANNOT_PILLAGE")
    return state,operation
end
'''


def build_pillage_preflight(unit_id: int) -> str:
    return _LUA_PILLAGE_STATE + f'''
{_lua_get_unit(unit_id)}
if {unit_id} >= 65536 and unit:GetID() ~= {unit_id} then
    {_bail("ERR:UNIT_ID_CHANGED|Runtime unit identity no longer matches")}
end
local known,state=pcall(pillageEligibility,unit,me)
if not known then print("ERR:PILLAGE_REFUSED|"..tostring(state))
else print("PILLAGE_PREPARED|"..unit:GetID().."|"..state.x.."|"..state.y.."|"..state.fingerprint) end
print("{SENTINEL}")
'''


def build_pillage_unit(unit_id: int, x: int, y: int, fingerprint: str, token: str) -> str:
    return _LUA_PILLAGE_STATE + f'''
{_lua_get_unit(unit_id)}
if unit:GetID() ~= {unit_id} or unit:GetX() ~= {x} or unit:GetY() ~= {y} then
    {_bail("ERR:PILLAGE_TARGET_CHANGED|Original unit or position changed; no action submitted")}
end
local known,state,operation=pcall(pillageEligibility,unit,me)
if not known then {_bail('ERR:PILLAGE_REFUSED|Atomic eligibility or target unavailable; no action submitted')} end
if state.fingerprint ~= "{fingerprint}" then
    {_bail("ERR:PILLAGE_TARGET_CHANGED|Original target changed; no action submitted")}
end
__civmcpPillageRequest={{token="{token}",owner=me,id=unit:GetID(),
    civ=PlayerConfigurations[me]:GetCivilizationTypeName(),seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
    turn=Game.GetCurrentGameTurn(),before=state,entered=true}}
UnitManager.RequestOperation(unit,operation.Hash)
print("OK:PILLAGE_REQUESTED|unit:"..unit:GetID())
print("{SENTINEL}")
'''


def build_pillage_query(unit_id: int, token: str) -> str:
    return _LUA_PILLAGE_STATE + f'''
local me=Game.GetLocalPlayer()
local record=__civmcpPillageRequest
local unit=UnitManager.GetUnit(me,{unit_id})
local known,state=pcall(function()
    assert(record and record.token=="{token}" and record.entered==true)
    assert(record.owner==me and record.turn==Game.GetCurrentGameTurn())
    assert(record.civ==PlayerConfigurations[me]:GetCivilizationTypeName())
    assert(record.seed==GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"))
    assert(unit and unit:GetID()==record.id and unit:GetOwner()==me)
    assert(UI.IsGameCoreBusy()==false)
    local now=pillageState(unit)
    assert(now.x==record.before.x and now.y==record.before.y)
    assert(now.target==record.before.target and #now.keys==#record.before.keys)
    local damaged={{}}
    for _,key in ipairs(record.before.keys) do
        assert(type(now.states[key])=="boolean")
        if record.before.states[key]==false and now.states[key]==true then
            table.insert(damaged,key)
        else assert(now.states[key]==record.before.states[key]) end
    end
    assert(#damaged==1)
    return damaged[1]
end)
if known then
    print("PILLAGE_DAMAGE|"..record.token.."|"..record.owner.."|"..record.id
        .."|"..record.before.x.."|"..record.before.y.."|"..record.before.fingerprint.."|"..state)
end
print("{SENTINEL}")
'''

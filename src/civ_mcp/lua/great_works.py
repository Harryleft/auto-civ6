"""文化伟人创作的原生作品与城市槽位证据。"""


LUA_GREAT_WORK = '''
local culturalUnits={UNIT_GREAT_WRITER="GREAT_PERSON_CLASS_WRITER",
 UNIT_GREAT_ARTIST="GREAT_PERSON_CLASS_ARTIST",UNIT_GREAT_MUSICIAN="GREAT_PERSON_CLASS_MUSICIAN"}
local function prepareGreatWork(r)
 local individual=GameInfo.GreatPersonIndividuals[r.individual]
 local class=GameInfo.GreatPersonClasses[r.class]
 local expected=culturalUnits[GameInfo.Units[r.unitType].UnitType]
 assert(individual and class and class.GreatPersonClassType==expected
  and individual.GreatPersonClassType==expected,"GREAT_WORK_CREATOR_UNKNOWN")
 assert(type(individual.GreatWorkCollection)=="table","GREAT_WORK_COLLECTION_UNKNOWN")
 assert(type(Game.GetGreatWorkDataFromIndex)=="function","GREAT_WORK_DATA_UNAVAILABLE")
 r.workTypes={};r.individualType=individual.GreatPersonIndividualType;r.creatorName=individual.Name
 assert(type(r.individualType)=="string" and type(r.creatorName)=="string","GREAT_WORK_CREATOR_UNKNOWN")
 for _,work in pairs(individual.GreatWorkCollection) do
  assert(scopeIndex(work.Index) and type(work.GreatWorkType)=="string"
   and work.GreatPersonIndividualType==r.individualType and not r.workTypes[work.Index],"GREAT_WORK_COLLECTION_UNKNOWN")
  r.workTypes[work.Index]=work.GreatWorkType
 end
 assert(next(r.workTypes),"NO_GREAT_WORK_COLLECTION")
 -- 只读取本方城市，保留提交前实际存在的槽位与作品，不能将静态作品总数当成剩余次数。
 r.workCities={};r.beforeWorks={}
 for _,city in Players[r.owner]:GetCities():Members() do
  local id=count(city:GetID())
  assert(city:GetOwner()==r.owner and not r.workCities[id],"GREAT_WORK_CITY_UNKNOWN")
  local before={id=id,owner=r.owner,x=count(city:GetX()),y=count(city:GetY()),slots={}}
  local buildings=city:GetBuildings()
  for building in GameInfo.Buildings() do
   if buildings:HasBuilding(building.Index) then
    local slots={}
    for slot=0,count(buildings:GetNumGreatWorkSlots(building.Index))-1 do
     local work=buildings:GetGreatWorkInSlot(building.Index,slot)
     assert(scopeInteger(work) and work>=-1,"GREAT_WORK_SLOT_UNKNOWN")
     slots[slot]=work
     if work>=0 then r.beforeWorks[work]=true end
    end
    before.slots[building.Index]=slots
   end
  end
  r.workCities[id]=before
 end
end
local function observeGreatWorkActor(r)
 assert(not r.invalid,"GREAT_WORK_SCOPE_OR_IDENTITY_CHANGED")
 local unit=UnitManager.GetUnit(r.owner,r.id%65536)
 local owned=Players[r.owner]:GetUnits():FindID(r.id)
 local known=true
 for _,actor in pairs({native=unit,owned=owned}) do
  if not sameEffectEntity(r,actor,r) then known=false end
  local gpOK,gp=pcall(function() return actor:GetGreatPerson() end)
  if not gpOK or not gp then known=false
  else
   -- 独立读取两个字段；未知不失效，已知不匹配也不能被另一字段的异常遮蔽。
   for _,field in ipairs({{"GetIndividual",r.individual},{"GetClass",r.class}}) do
    local ok,value=pcall(function() return gp[field[1]](gp) end)
    if not ok or not scopeIndex(value) then known=false
    elseif value~=field[2] then r.invalid=true end
   end
  end
 end
 assert(known and not r.invalid,"GREAT_WORK_ACTOR_CHANGED_OR_UNKNOWN")
 assert((unit and owned) or (unit==nil and owned==nil and r.removed==true),"GREAT_WORK_ACTOR_UNKNOWN")
end
local function observeGreatWorkCity(r)
 local work=r.createdWork
 assert(work and scopeIndex(work.x) and scopeIndex(work.y) and scopeIndex(work.building)
  and scopeIndex(work.index) and not r.beforeWorks[work.index],"GREAT_WORK_EVENT_UNCONFIRMED")
 local city=CityManager.GetCityAt(work.x,work.y)
 assert(city,"GREAT_WORK_CITY_UNKNOWN")
 local before
 for _,candidate in pairs(r.workCities) do
  if candidate.x==work.x and candidate.y==work.y then before=candidate end
 end
 assert(before and sameEffectEntity(r,city,before),"GREAT_WORK_CITY_CHANGED")
 return city,before
end
local function confirmGreatWork(r)
 observeGreatWorkActor(r)
 local city,before=observeGreatWorkCity(r)
 local work=r.createdWork
 local slots=before.slots[work.building]
 assert(slots,"GREAT_WORK_BUILDING_UNKNOWN")
 local buildings=city:GetBuildings()
 assert(buildings:HasBuilding(work.building)==true,"GREAT_WORK_BUILDING_MISSING")
 local slotFound
 for slot=0,count(buildings:GetNumGreatWorkSlots(work.building))-1 do
  if buildings:GetGreatWorkInSlot(work.building,slot)==work.index then
   assert(slotFound==nil and slots[slot]==-1,"GREAT_WORK_SLOT_NOT_NEW")
   slotFound=slot
  end
 end
 assert(slotFound~=nil,"GREAT_WORK_NOT_IN_SLOT")
 local workType=buildings:GetGreatWorkTypeFromIndex(work.index)
 local expected=r.workTypes[workType]
 local info=GameInfo.GreatWorks[workType]
 local data=Game.GetGreatWorkDataFromIndex(work.index)
 assert(expected and info and info.GreatWorkType==expected
  and info.GreatPersonIndividualType==r.individualType,"GREAT_WORK_CREATOR_MISMATCH")
 assert(data and data.GreatWorkType==workType and data.CreatorName==r.creatorName
  and data.TurnCreated==r.turn,"GREAT_WORK_DATA_UNCONFIRMED")
 observeGreatWorkActor(r)
 assert(sameEffectEntity(r,city,before) and sameScope(r),"GREAT_WORK_SCOPE_OR_CITY_CHANGED")
 return "work:"..expected.." index:"..work.index,
  "city:"..before.id.." building:"..work.building.." slot:"..slotFound
end
'''

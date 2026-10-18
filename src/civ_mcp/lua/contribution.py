"""Royal Society single-submit command and request-bound native evidence."""
import json

from civ_mcp.lua._helpers import SENTINEL, _bail, _lua_get_unit


_CONTEXT = '''
local function targetAt(x,y,owner)
 local plot=Map.GetPlot(x,y)
 assert(plot and plot:GetOwner()==owner,"DISTRICT_OWNER_CHANGED")
 local city=Cities.GetPlotPurchaseCity(plot)
 assert(city and city:GetOwner()==owner,"CITY_OWNER_CHANGED")
 local district=nil
 for _,d in city:GetDistricts():Members() do
  if d:GetX()==x and d:GetY()==y then assert(not district);district=d end
 end
 assert(district and district:GetOwner()==owner and district:GetType()==plot:GetDistrictType(),"DISTRICT_CHANGED")
 local target=table.concat({x,y,plot:GetIndex(),owner,city:GetID(),district:GetID(),district:GetType()},",")
 return city,city:GetBuildQueue(),target,district:GetType()
end
local function prepareContribution(unit,me,id)
 assert(unit:GetID()==id and unit:GetOwner()==me,"UNIT_ID_CHANGED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 assert(GameInfo.Units[unit:GetType()].UnitType=="UNIT_BUILDER","NOT_A_BUILDER")
 local charges=unit:GetBuildCharges()
 assert(type(charges)=="number" and charges>0 and unit:GetMovesRemaining()>0,"NO_CHARGES_OR_MOVES")
 local royal=GameInfo.Buildings.BUILDING_GOV_SCIENCE
 assert(royal,"ROYAL_SOCIETY_UNAVAILABLE")
 local hasRoyal=false
 for _,c in Players[me]:GetCities():Members() do
  if c:GetOwner()==me and c:GetBuildings():HasBuilding(royal.Index)==true then hasRoyal=true end
 end
 assert(hasRoyal,"NO_ROYAL_SOCIETY")
 local x,y=unit:GetX(),unit:GetY()
 local city,bq,target,districtType=targetAt(x,y,me)
 local hash=bq:GetCurrentProductionTypeHash()
 local project=nil
 for row in GameInfo.Projects() do if row.Hash==hash then project=row end end
 assert(project and GameInfo.Districts[districtType].DistrictType==project.PrereqDistrict,"NO_MATCHING_PROJECT")
 local progress,cost=bq:GetProjectProgress(project.Index),bq:GetProjectCost(project.Index)
 assert(type(progress)=="number" and type(cost)=="number" and progress>=0 and cost>progress,"PROJECT_PROGRESS_UNKNOWN")
 local command=GameInfo.UnitCommands.UNITCOMMAND_PROJECT_PRODUCTION
 assert(command and type(command.Hash)=="number" and type(UnitManager.RequestCommand)=="function","COMMAND_UNAVAILABLE")
 local allowed=UnitManager.CanStartCommand(unit,command.Hash,false,true)
 assert(allowed==true,"CANNOT_CONTRIBUTE")
 local fingerprint=table.concat({id,target,project.Index,project.Hash,charges,progress,cost},",")
 return {id=id,x=x,y=y,city=city:GetID(),project=project.Index,hash=project.Hash,
         target=target,charges=charges,progress=progress,cost=cost,fingerprint=fingerprint,command=command.Hash}
end
local function sameScope(r)
 local ok,same=pcall(function()
  return Game.GetLocalPlayer()==r.owner and Game.GetCurrentGameTurn()==r.turn
   and PlayerConfigurations[r.owner]:GetCivilizationTypeName()==r.civ
   and GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")==r.seed
 end)
 if not ok or not same then r.invalid=true;return false end
 return not r.invalid
end
local function retireContribution(r)
 if r then
  pcall(function() Events.UnitRemovedFromMap.Remove(r.removalHandler) end)
  pcall(function() Events.CityProjectCompleted.Remove(r.projectHandler) end)
  pcall(function() Events.UnitCommandStarted.Remove(r.commandHandler) end)
  pcall(function() Events.UnitChargesChanged.Remove(r.chargesHandler) end)
 end
end
'''


def build_contribution_preflight(unit_id: int) -> str:
    return _CONTEXT + f'''
{_lua_get_unit(unit_id)}
local ok,state=pcall(prepareContribution,unit,me,{unit_id})
if ok then print("CONTRIBUTION_PREPARED|"..state.fingerprint)
else print("ERR:CONTRIBUTION_REFUSED|"..tostring(state)) end
print("{SENTINEL}")
'''


def build_sacrifice_builder_charges(unit_id: int, fingerprint: str, token: str) -> str:
    return _CONTEXT + f'''
{_lua_get_unit(unit_id)}
local ok,state=pcall(prepareContribution,unit,me,{unit_id})
if not ok or state.fingerprint~={json.dumps(fingerprint)} then
 {_bail("ERR:CONTRIBUTION_REFUSED|Prepared identity, target or eligibility changed; no command submitted")}
end
local receipt={{token={json.dumps(token)},id={unit_id},owner=me,before=state,
 civ=PlayerConfigurations[me]:GetCivilizationTypeName(),seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
 turn=Game.GetCurrentGameTurn(),entered=false,returned=false}}
if __civmcpContribution~=nil and type(__civmcpContribution)~="table" then
 {_bail("ERR:CONTRIBUTION_REFUSED|Native receipt storage unavailable")}
end
retireContribution(__civmcpContribution)
__civmcpContribution=receipt
local function sequenceEvent()
 receipt.eventSequence=(receipt.eventSequence or 0)+1
 return receipt.eventSequence
end
-- Native UnitCommandStarted carries the command hash; another command on this
-- full identity invalidates attribution rather than borrowing its charge delta.
receipt.commandHandler=function(owner,id,command)
 if __civmcpContribution~=receipt or not receipt.entered or owner~=receipt.owner or id~=receipt.id then return end
 if not sameScope(receipt) then return end
 receipt.commandSeq=sequenceEvent()
 if command==state.command then receipt.started=true else receipt.invalid=true end
end
-- TutorialUIRoot's native signature is (playerID,unitID,newCharges,oldCharges).
-- Event delivery order is not guaranteed: retain an entered request's exact
-- numeric delta even before CommandStarted; confirmation still requires both.
-- Keep consumption before userdata removal; Gone is never a delta.
receipt.chargesHandler=function(owner,id,newCharges,oldCharges)
 if __civmcpContribution~=receipt or not receipt.entered
  or owner~=receipt.owner or id~=receipt.id then return end
 if not sameScope(receipt) then return end
 receipt.chargeEvents=(receipt.chargeEvents or 0)+1
 receipt.chargeSeq=sequenceEvent()
 receipt.rawChargeBefore=type(oldCharges)=="number" and oldCharges or nil
 receipt.rawChargeAfter=type(newCharges)=="number" and newCharges or nil
 if oldCharges==state.charges and type(newCharges)=="number" and newCharges>=0 and newCharges<oldCharges then
  receipt.chargeBefore=oldCharges;receipt.chargeAfter=newCharges
 end
end
-- Numeric event identities survive removal; never capture consumed userdata.
receipt.removalHandler=function(owner,id)
 if __civmcpContribution~=receipt or not receipt.entered or owner~=receipt.owner or id~=receipt.id then return end
 if sameScope(receipt) then receipt.removed=true;receipt.removedSeq=sequenceEvent() end
end
-- Original Expansion2 TutorialLoader: CityProjectCompleted(playerID,cityID,projectID).
receipt.projectHandler=function(owner,cityID,projectID)
 if __civmcpContribution~=receipt or not receipt.entered or owner~=receipt.owner
  or cityID~=state.city or projectID~=state.project then return end
 if sameScope(receipt) then receipt.completed=true;receipt.completedSeq=sequenceEvent() end
end
local armed=pcall(function()
 for _,event in ipairs({{Events.UnitRemovedFromMap,Events.CityProjectCompleted,Events.UnitCommandStarted,Events.UnitChargesChanged}}) do
  assert(type(event.Add)=="function" and type(event.Remove)=="function")
 end
 Events.UnitCommandStarted.Add(receipt.commandHandler)
 Events.UnitChargesChanged.Add(receipt.chargesHandler)
 Events.UnitRemovedFromMap.Add(receipt.removalHandler)
 Events.CityProjectCompleted.Add(receipt.projectHandler)
end)
if not armed then
 retireContribution(receipt)
 {_bail("ERR:CONTRIBUTION_REFUSED|Required native observers unavailable; no command submitted")}
end
receipt.entered=true
-- UnitPanel.OnUnitActionClicked uses this standard no-interface command path.
UnitManager.RequestCommand(unit,state.command)
receipt.returned=true
print("OK:CONTRIBUTION_REQUESTED|unit:{unit_id}")
print("{SENTINEL}")
'''


def build_contribution_query(unit_id: int, fingerprint: str, token: str) -> str:
    return _CONTEXT + f'''
local receipt=__civmcpContribution
local reason="RECEIPT_MISSING"
local matched=type(receipt)=="table" and receipt.token=={json.dumps(token)} and receipt.id=={unit_id}
local ok,consumed,effect=pcall(function()
 assert(type(receipt)=="table")
 reason="RECEIPT_BINDING_MISMATCH";assert(matched)
 reason="REQUEST_NOT_RETURNED";assert(receipt.entered==true and receipt.returned==true)
 reason="COMMAND_NOT_OBSERVED";assert(receipt.started==true)
 reason="SCOPE_CHANGED";assert(sameScope(receipt))
 local before=receipt.before
 reason="BEFORE_BINDING_MISMATCH";assert(before.fingerprint=={json.dumps(fingerprint)})
 reason="CORE_BUSY_OR_UNKNOWN";assert(UI.IsGameCoreBusy()==false)
 reason=(receipt.chargeEvents or 0)>0 and "CHARGE_EVENT_MISMATCH" or "CHARGES_NOT_OBSERVED"
 assert(receipt.chargeBefore==before.charges and type(receipt.chargeAfter)=="number"
  and receipt.chargeAfter>=0 and receipt.chargeAfter<receipt.chargeBefore)
 local consumed=receipt.chargeBefore-receipt.chargeAfter
 reason="UNIT_LOOKUP_FAILED"
 local unit=UnitManager.GetUnit(receipt.owner,{unit_id})
 if unit then
  reason="UNIT_IDENTITY_CHANGED";assert(unit:GetID()=={unit_id} and unit:GetOwner()==receipt.owner)
  reason="UNIT_POSITION_CHANGED";assert(unit:GetX()==before.x and unit:GetY()==before.y)
  reason="UNIT_CHARGES_MISMATCH";assert(unit:GetBuildCharges()==receipt.chargeAfter)
 else
  reason="REMOVAL_NOT_OBSERVED";assert(receipt.removed==true)
  reason="REMOVAL_NOT_CONFIRMED";assert(Players[receipt.owner]:GetUnits():FindID({unit_id})==nil)
 end
 reason="TARGET_UNAVAILABLE"
 local city,bq,target=targetAt(before.x,before.y,receipt.owner)
 reason="TARGET_CHANGED";assert(target==before.target)
 if receipt.completed==true then return consumed,"completed:event" end
 reason="PROJECT_CHANGED";assert(bq:GetCurrentProductionTypeHash()==before.hash)
 reason="PROJECT_PROGRESS_UNAVAILABLE"
 local progress=bq:GetProjectProgress(before.project)
 assert(type(progress)=="number")
 reason="PROJECT_COST_CHANGED";assert(bq:GetProjectCost(before.project)==before.cost)
 reason="PROJECT_NOT_ADVANCED";assert(progress>before.progress)
 return consumed,"progress:"..before.progress.."->"..progress
end)
-- Always expose this query's token and only its matched receipt facts, so a
-- pending gate can be diagnosed through existing read-only confirmations.
local facts=matched and receipt or {{}}
print("CONTRIBUTION_OBSERVED|{token}|unit:{unit_id}|receipt:"..(matched and "matched" or "unmatched")
 .."|entered:"..tostring(facts.entered).."|returned:"..tostring(facts.returned)
 .."|started:"..tostring(facts.started).."|chargeBefore:"..tostring(facts.chargeBefore)
 .."|chargeAfter:"..tostring(facts.chargeAfter).."|chargeEvents:"..tostring(facts.chargeEvents or 0)
 .."|rawChargeBefore:"..tostring(facts.rawChargeBefore).."|rawChargeAfter:"..tostring(facts.rawChargeAfter)
 .."|commandSeq:"..tostring(facts.commandSeq).."|chargeSeq:"..tostring(facts.chargeSeq)
 .."|removed:"..tostring(facts.removed).."|removedSeq:"..tostring(facts.removedSeq)
 .."|completed:"..tostring(facts.completed).."|completedSeq:"..tostring(facts.completedSeq).."|invalid:"..tostring(facts.invalid)
 .."|reason:"..(ok and "CONFIRMED" or reason))
if ok then retireContribution(receipt);print("CONTRIBUTION_CONFIRMED|"..receipt.token.."|{unit_id}|"..receipt.before.fingerprint.."|"..consumed.."|"..effect) end
print("{SENTINEL}")
'''

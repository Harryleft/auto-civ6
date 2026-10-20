"""Native theological MOVE_TO+ATTACK with exact combat and effect witnesses.

Contracts: shipped Civ6Common/WorldInput/UnitPanel and UnitFlagManager. Combat
preview is not an event: CombatVisEnd supplies participant identity only; HP or
exact removal supplies the independently observed effect.
"""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE
from civ_mcp.lua.units import _LUA_MELEE_ELIGIBILITY


_CONTEXT = _LUA_ACTION_SCOPE + _LUA_MELEE_ELIGIBILITY + '''
local function hp(unit)
 local maximum,damage=unit:GetMaxDamage(),unit:GetDamage()
 assert(scopeIndex(maximum) and maximum>0 and scopeIndex(damage) and damage<=maximum,"HP_UNKNOWN")
 return maximum-damage
end
local function snapshot(unit)
 local s={id=unit:GetID(),owner=unit:GetOwner(),unitType=unit:GetType(),religion=unit:GetReligionType(),
  x=unit:GetX(),y=unit:GetY(),hp=hp(unit)}
 assert(scopeIndex(s.id) and scopePlayer(s.owner) and scopeIndex(s.unitType)
  and scopeIndex(s.religion) and s.religion>0 and scopeIndex(s.x) and scopeIndex(s.y),"IDENTITY_UNKNOWN")
 local info=GameInfo.Units[s.unitType]
 assert(info and info.TrackReligion==true and type(info.ReligiousStrength)=="number"
  and info.ReligiousStrength>0 and info.ReligiousStrength<math.huge,"NOT_RELIGIOUS")
 local c=unit:GetComponentID()
 assert(type(c)=="table" and c.type==ComponentType.UNIT and c.player==s.owner and scopeIndex(c.id),"COMPONENT_UNKNOWN")
 local lookup=UnitManager.GetUnit(c.player,c.id)
 assert(lookup and lookup:GetID()==s.id,"COMPONENT_ID_CHANGED")
 s.component={type=c.type,player=c.player,id=c.id}
 return s
end
local function visibleTarget(owner,x,y)
 local visibility=PlayersVisibility[owner]
 assert(visibility:IsVisible(x,y)==true,"TARGET_NOT_VISIBLE")
 assert(scopeInteger(MapLayers.ANY),"LAYER_UNKNOWN")
 local all=Units.GetUnitsInPlotLayerID(x,y,MapLayers.ANY)
 assert(type(all)=="table","TARGET_UNKNOWN")
 local target
 for _,unit in ipairs(all) do
  local other=unit:GetOwner();assert(scopePlayer(other),"TARGET_OWNER_UNKNOWN")
  if other~=owner and visibility:IsUnitVisible(unit)==true then
   local info=GameInfo.Units[unit:GetType()]
   assert(info and type(info.TrackReligion)=="boolean","TARGET_TYPE_UNKNOWN")
   if info.TrackReligion then
    assert(not target,"AMBIGUOUS_RELIGIOUS_TARGET")
    target=snapshot(unit)
    assert(target.x==x and target.y==y,"TARGET_POSITION_CHANGED")
   end
  end
 end
 assert(target,"NO_VISIBLE_RELIGIOUS_TARGET")
 return target
end
local function matching(r,unit,s,actor)
 local known=true
 -- Each readable mismatch latches even if a sibling getter is unavailable.
 for _,pair in ipairs({{"GetID","id"},{"GetOwner","owner"},{"GetType","unitType"},{"GetReligionType","religion"}}) do
  local ok,value=pcall(function() return unit[pair[1]](unit) end)
  local valid=ok and (pair[2]=="owner" and scopePlayer(value) or pair[2]~="owner" and scopeIndex(value))
  if not valid then known=false elseif value~=s[pair[2]] then r.invalid=true end
 end
 local ox,x=pcall(function() return unit:GetX() end)
 local oy,y=pcall(function() return unit:GetY() end)
 local knownX,knownY=ox and scopeIndex(x),oy and scopeIndex(y)
 if not knownX or not knownY then known=false end
 if actor and r.entered then
  -- A partial coordinate can still disprove every approved path point.
  -- Do not let an unreadable sibling hide that observed mismatch.
  local onX,onY,onPoint=false,false,false
  for _,point in ipairs(r.path) do
   if knownX and point.x==x then onX=true end
   if knownY and point.y==y then onY=true end
   if knownX and knownY and point.x==x and point.y==y then onPoint=true end
  end
  if (knownX and not onX) or (knownY and not onY)
   or (knownX and knownY and not onPoint) then r.invalid=true end
 else
  if knownX and x~=s.x then r.invalid=true end
  if knownY and y~=s.y then r.invalid=true end
 end
 return known and not r.invalid
end
local function facts(r,s,actor)
 local okA,a=pcall(function() return UnitManager.GetUnit(s.owner,s.id%65536) end)
 local okB,b=pcall(function() return Players[s.owner]:GetUnits():FindID(s.id) end)
 local known=okA and okB
 for _,unit in pairs({a=a,b=b}) do
  local ok,same=pcall(matching,r,unit,s,actor)
  if not ok or not same then known=false end
 end
 if not known or r.invalid then return false end
 if not a and not b then
  return s.removed==true and true or false,0
 end
 if not a or not b or s.removed then return false end
 local ha,va=pcall(hp,a);local hb,vb=pcall(hp,b)
 if not ha or not hb or va~=vb then return false end
 return true,va
end
local function observe(r)
 local scoped=sameScope(r)
 -- Call both independently: unavailable data on one side cannot hide a known
 -- mismatch on the other. Resolve fresh handles; never retain dead userdata.
 pcall(facts,r,r.actor,true);pcall(facts,r,r.target,false)
 return scoped and not r.invalid
end
local function retire(r)
 if not r or not r.handlers then return end
 for name,handler in pairs(r.handlers) do pcall(function() Events[name].Remove(handler) end) end
end
local function eligible(r,unit)
 local target=visibleTarget(r.owner,r.target.x,r.target.y)
 for _,key in ipairs({"owner","id","unitType","religion","x","y"}) do assert(target[key]==r.target[key],"TARGET_CHANGED") end
 assert(target.hp==r.target.hp,"TARGET_HP_CHANGED")
 assert(matching(r,unit,r.actor,true) and hp(unit)==r.actor.hp,"ACTOR_CHANGED")
 assert(type(CombatManager.IsAttackChangeWarState)=="function","WAR_STATE_UNKNOWN")
 local warOK,wars=pcall(CombatManager.IsAttackChangeWarState,r.actor.component,r.target.x,r.target.y)
 -- Civ6Common: a successful nil or empty list has no war declaration target.
 -- Missing/throwing API and malformed values remain unavailable, not peace.
 assert(warOK and (wars==nil or type(wars)=="table" and next(wars)==nil),"WAR_STATE_CHANGE_OR_UNKNOWN")
 local preview=CombatManager.SimulateAttackInto(r.actor.component,nil,r.target.x,r.target.y)
 assert(scopeInteger(CombatTypes.RELIGIOUS) and type(preview)=="table"
  and preview[CombatResultParameters.COMBAT_TYPE]==CombatTypes.RELIGIOUS,"NOT_RELIGIOUS_PREVIEW")
 for _,side in ipairs({{CombatResultParameters.ATTACKER,r.actor},{CombatResultParameters.DEFENDER,r.target}}) do
  local row=preview[side[1]];local c=type(row)=="table" and row[CombatResultParameters.ID]
  local original=side[2].component
  assert(type(c)=="table" and c.type==original.type and c.player==original.player and c.id==original.id,"PREVIEW_ID_CHANGED")
 end
 local code,reason=meleeAttackEligibility(unit,r.params)
 assert(code==nil,reason or code)
 local path=UnitManager.GetMoveToPathEx(unit,Map.GetPlot(r.target.x,r.target.y):GetIndex())
 assert(type(path)=="table" and type(path.plots)=="table" and type(path.turns)=="table","PATH_UNKNOWN")
 local count=0
 for key in pairs(path.plots) do assert(scopeIndex(key) and key>=1,"PATH_UNKNOWN");count=count+1 end
 local turns=0
 for key in pairs(path.turns) do assert(scopeIndex(key) and key>=1,"PATH_UNKNOWN");turns=turns+1 end
 assert(count>=2 and turns==count,"PATH_UNKNOWN")
 r.path={}
 for index=1,count do
  local id=path.plots[index]
  assert(scopeIndex(id) and path.turns[index]==1,"PATH_NOT_IMMEDIATE")
  local plot=Map.GetPlotByIndex(id);assert(plot,"PATH_UNKNOWN")
  local x,y=plot:GetX(),plot:GetY()
  assert(scopeIndex(x) and scopeIndex(y),"PATH_UNKNOWN")
  if index==1 then assert(x==r.actor.x and y==r.actor.y,"PATH_ORIGIN_CHANGED") end
  if index==count then assert(x==r.target.x and y==r.target.y,"PATH_TARGET_CHANGED") end
  table.insert(r.path,{x=x,y=y})
 end
 -- Eligibility helpers can synchronously change native state. Inspect again
 -- after the final engine calls and before RequestOperation entry.
 local actorKnown=matching(r,unit,r.actor,false)
 local current=visibleTarget(r.owner,r.target.x,r.target.y)
 for _,key in ipairs({"owner","id","unitType","religion","x","y","hp"}) do assert(current[key]==r.target[key],"TARGET_CHANGED") end
 local actorViews,actorHP=facts(r,r.actor,true)
 local targetViews,targetHP=facts(r,r.target,false)
 assert(actorKnown and actorViews and targetViews and actorHP==r.actor.hp
  and targetHP==r.target.hp and sameScope(r),"FINAL_IDENTITY_CHANGED_OR_UNKNOWN")
end
'''


def build_religious_attack(unit_id: int, x: int, y: int, token: str) -> str:
    return _CONTEXT + f'''
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local actor=UnitManager.GetUnit(owner,{unit_id}%65536)
 assert(actor and actor:GetID()=={unit_id} and actor:GetOwner()==owner,"ACTOR_ID_CHANGED")
 local r={{token={json.dumps(token)},owner=owner,civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn(),handlers={{}}}}
 r.actor=snapshot(actor);r.target=visibleTarget(owner,{x},{y})
 assert(r.actor.religion~=r.target.religion,"SAME_RELIGION")
 assert(scopeInteger(UnitOperationTypes.MOVE_TO) and scopeInteger(UnitOperationMoveModifiers.ATTACK),"OPERATION_UNKNOWN")
 r.operation=UnitOperationTypes.MOVE_TO;r.params={{}}
 r.params[UnitOperationTypes.PARAM_X]={x};r.params[UnitOperationTypes.PARAM_Y]={y}
 r.params[UnitOperationTypes.PARAM_MODIFIERS]=UnitOperationMoveModifiers.ATTACK
 for _,name in ipairs({{"UnitOperationStarted","CombatVisEnd","UnitRemovedFromMap"}}) do
  assert(Events[name] and type(Events[name].Add)=="function" and type(Events[name].Remove)=="function","OBSERVER_UNKNOWN")
 end
 assert(type(UnitManager.RequestOperation)=="function","OPERATION_UNKNOWN")
 eligible(r,actor)
 return r
end)
if not ok then print("ERR:RELIGIOUS_ATTACK_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retire(__civmcpReligiousCombat);__civmcpReligiousCombat=r
r.handlers.UnitOperationStarted=function(owner,id,operation)
 if __civmcpReligiousCombat~=r or not r.entered then return end
 local scoped=observe(r)
 if scoped and owner==r.owner and id==r.actor.id then
  if operation==r.operation then r.started=true else r.invalid=true end
 end
end
r.handlers.CombatVisEnd=function(data)
 if __civmcpReligiousCombat~=r or not r.entered then return end
 local scoped=observe(r)
 local function side(key,original)
  local s=type(data)=="table" and data[key]
  return type(s)=="table" and s.componentType==ComponentType.UNIT
   and s.playerID==original.owner and s.componentID==original.component.id
 end
 if scoped and side(CombatVisType.ATTACKER,r.actor) and side(CombatVisType.DEFENDER,r.target) then r.combat=true end
end
r.handlers.UnitRemovedFromMap=function(owner,id)
 if __civmcpReligiousCombat~=r or not r.entered then return end
 local scoped=observe(r)
 for _,s in ipairs({{r.actor,r.target}}) do if scoped and owner==s.owner and id==s.id then s.removed=true end end
end
local armed=pcall(function() for name,handler in pairs(r.handlers) do Events[name].Add(handler) end end)
if not armed then retire(r);print("ERR:RELIGIOUS_ATTACK_REFUSED|OBSERVER_UNKNOWN");print("{SENTINEL}");return end
local final,actor=pcall(function()
 local actor=UnitManager.GetUnit(r.owner,r.actor.id%65536)
 assert(actor,"ACTOR_MISSING");eligible(r,actor)
 return actor
end)
if not final then retire(r);print("ERR:RELIGIOUS_ATTACK_REFUSED|"..tostring(actor));print("{SENTINEL}");return end
r.entered=true
local requested,err=pcall(function() UnitManager.RequestOperation(actor,r.operation,r.params) end)
r.returned=requested
if requested then print("RELIGIOUS_ATTACK_REQUESTED|"..r.token) else print("RELIGIOUS_ATTACK_ENTERED_UNKNOWN|"..tostring(err)) end
print("{SENTINEL}")
'''


def build_religious_attack_query(unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpReligiousCombat
local ok,detail=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.actor.id=={unit_id},"RECEIPT_UNKNOWN")
 local scoped=sameScope(r)
 local actorKnown,actorHP=facts(r,r.actor,true)
 local targetKnown,targetHP=facts(r,r.target,false)
 local finalScope=sameScope(r)
 assert(not r.invalid,"IDENTITY_CHANGED")
 assert(scoped and finalScope and actorKnown and targetKnown,"IDENTITY_OR_HP_UNKNOWN")
 assert(r.entered and r.returned and r.started and r.combat,"COMBAT_WITNESS_INCOMPLETE")
 assert(actorHP<r.actor.hp or targetHP<r.target.hp,"COMBAT_EFFECT_UNCONFIRMED")
 return "target:"..r.target.owner..":"..r.target.id.."|attacker_hp:"..r.actor.hp.."->"..actorHP
  .."|defender_hp:"..r.target.hp.."->"..targetHP
end)
if ok then print("RELIGIOUS_COMBAT_CONFIRMED|"..r.token.."|{unit_id}|"..detail)
else print("RELIGIOUS_COMBAT_OBSERVED|{token}|"..tostring(detail)) end
print("{SENTINEL}")
'''


def parse_religious_attack(lines: list[str], unit_id: int, token: str) -> str | None:
    rows = [line.split('|') for line in lines if line.startswith('RELIGIOUS_COMBAT_CONFIRMED|')]
    if len(rows) == 1 and len(rows[0]) == 6 and rows[0][1:3] == [token, str(unit_id)]:
        return f"RELIGIOUS_COMBAT_CONFIRMED|unit:{unit_id}|" + '|'.join(rows[0][3:])
    return None

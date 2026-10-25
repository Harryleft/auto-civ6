"""Unit-versus-city combat, using native preview and scoped CombatVisEnd.

Cities are district defenders, not their garrisons. Keep their receipt separate
from unit HP followups, including when a melee capture replaces the city ID.
"""

import json
import re

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


_CONTEXT = _LUA_ACTION_SCOPE + '''
local function siegeComponent(object,kind)
 local c=object:GetComponentID()
 assert(type(c)=="table" and c.type==kind and scopePlayer(c.player) and scopeIndex(c.id),"COMPONENT_UNKNOWN")
 return {type=c.type,player=c.player,id=c.id}
end
local function siegeSame(a,b)
 return type(a)=="table" and a.type==b.type and a.player==b.player and a.id==b.id
end
local function siegeCity(x,y)
 local city=CityManager.GetCityAt(x,y)
 assert(city and city:GetX()==x and city:GetY()==y,"CITY_UNKNOWN")
 local s={owner=city:GetOwner(),id=city:GetID()}
 assert(scopePlayer(s.owner) and scopeIndex(s.id),"CITY_ID_UNKNOWN")
 local info=GameInfo.Districts.DISTRICT_CITY_CENTER
 assert(info,"CENTER_UNKNOWN")
 for _,d in city:GetDistricts():Members() do
  if d:GetType()==info.Index then
   assert(not s.center,"AMBIGUOUS_CENTER")
   s.center=siegeComponent(d,ComponentType.DISTRICT)
   local function health(kind)
    local maximum,damage=d:GetMaxDamage(kind),d:GetDamage(kind)
    assert(scopeIndex(maximum) and scopeIndex(damage) and damage<=maximum,"CITY_HP_UNKNOWN")
    return maximum-damage
   end
   s.wall=health(DefenseTypes.DISTRICT_OUTER)
   s.garrison=health(DefenseTypes.DISTRICT_GARRISON)
  end
 end
 assert(s.center and s.center.player==s.owner,"CENTER_UNKNOWN")
 return s
end
local function siegeActor(unit)
 local s={owner=unit:GetOwner(),id=unit:GetID(),unitType=unit:GetType(),
  x=unit:GetX(),y=unit:GetY(),attacks=unit:GetAttacksRemaining(),
  hp=unit:GetMaxDamage()-unit:GetDamage(),component=siegeComponent(unit,ComponentType.UNIT)}
 assert(scopePlayer(s.owner) and scopeIndex(s.id) and scopeIndex(s.unitType)
  and scopeIndex(s.x) and scopeIndex(s.y) and scopeIndex(s.attacks) and scopeIndex(s.hp),"ACTOR_UNKNOWN")
 return s
end
local function siegeRetire(r)
 for name,handler in pairs(r and r.handlers or {}) do pcall(function() Events[name].Remove(handler) end) end
end
local function siegeActorIdentity(r,actor)
 for _,pair in ipairs({{"GetOwner","owner"},{"GetID","id"},{"GetType","unitType"}}) do
  local ok,value=pcall(function() return actor[pair[1]](actor) end)
  if ok and scopeIndex(value) and value~=r.actor[pair[2]] then r.invalid=true end
 end
 local ok,c=pcall(function() return actor:GetComponentID() end)
 if ok and type(c)=="table" and scopeIndex(c.type) and scopePlayer(c.player) and scopeIndex(c.id)
  and not siegeSame(c,r.actor.component) then r.invalid=true end
end
local function siegeCityIdentity(r)
 local ok,city=pcall(CityManager.GetCityAt,r.x,r.y)
 if not ok or not city then return end
 local own,owner=pcall(function() return city:GetOwner() end)
 local ident,id=pcall(function() return city:GetID() end)
 if own and scopePlayer(owner) then
  if owner~=r.city.owner and (owner~=r.owner or not r.melee) then r.invalid=true end
  if owner==r.city.owner then
   if ident and scopeIndex(id) and id~=r.city.id then r.invalid=true end
   pcall(function()
    for _,d in city:GetDistricts():Members() do
     if d:GetType()==GameInfo.Districts.DISTRICT_CITY_CENTER.Index then
      local c=d:GetComponentID()
      if type(c)=="table" and scopeIndex(c.type) and scopePlayer(c.player) and scopeIndex(c.id)
       and not siegeSame(c,r.city.center) then r.invalid=true end
     end
    end
   end)
  end
 end
end
local function siegeEligible(r,actor)
 assert(sameScope(r) and UI.IsGameCoreBusy()==false,"SCOPE_OR_CORE_UNKNOWN")
 assert(PlayersVisibility[r.owner]:IsVisible(r.x,r.y)==true,"TARGET_NOT_VISIBLE")
 local a=siegeActor(actor)
 for _,k in ipairs({"owner","id","unitType","x","y","attacks","hp"}) do assert(a[k]==r.actor[k],"ACTOR_CHANGED") end
 assert(siegeSame(a.component,r.actor.component),"ACTOR_COMPONENT_CHANGED")
 local s=siegeCity(r.x,r.y)
 for _,k in ipairs({"owner","id","wall","garrison"}) do assert(s[k]==r.city[k],"CITY_CHANGED") end
 assert(siegeSame(s.center,r.city.center),"CENTER_CHANGED")
 assert(s.owner~=r.owner and Players[r.owner]:GetDiplomacy():IsAtWarWith(s.owner)==true,"NOT_AT_WAR")
 assert(actor:GetMovesRemaining()>0 and actor:GetAttacksRemaining()>0,"NO_ATTACKS_OR_MOVES")
 local war=CombatManager.IsAttackChangeWarState(a.component,r.x,r.y)
 assert(war==nil or type(war)=="table" and next(war)==nil,"WAR_STATE_CHANGE")
 if r.melee then
  local code,reason=meleeAttackEligibility(actor,r.params)
  assert(code==nil,reason or code)
 else
  assert(type(r.range)=="number" and r.range>0
   and Map.GetPlotDistance(a.x,a.y,r.x,r.y)<=r.range,"OUT_OF_RANGE")
 end
 assert(UnitManager.CanStartOperation(actor,r.operation,nil,r.params)==true,"NATIVE_TARGET_REFUSED")
 local preview=CombatManager.SimulateAttackInto(a.component,r.combatType,r.x,r.y)
 assert(type(preview)=="table","PREVIEW_UNKNOWN")
 local atk=preview[CombatResultParameters.ATTACKER]
 local def=preview[CombatResultParameters.DEFENDER]
 assert(type(atk)=="table" and siegeSame(atk[CombatResultParameters.ID],a.component)
  and type(def)=="table" and siegeSame(def[CombatResultParameters.ID],s.center),"PREVIEW_TARGET_CHANGED")
 -- Eligibility/preview are native calls too. Recheck facts at the entry boundary.
 local after=siegeCity(r.x,r.y)
 for _,k in ipairs({"owner","id","wall","garrison"}) do assert(after[k]==s[k],"CITY_CHANGED") end
 assert(siegeSame(after.center,s.center) and sameScope(r),"FINAL_IDENTITY_CHANGED")
 local final=siegeActor(actor)
 for _,k in ipairs({"owner","id","unitType","x","y","attacks","hp"}) do assert(final[k]==a[k],"ACTOR_CHANGED") end
end
'''


def build_city_attack_branch(unit_id: int, x: int, y: int, token: str) -> str:
    """Embedded after attack-mode classification, before looking for units."""
    template = '''
local siegeKnown,siegeTarget=pcall(function()
 return CityManager and CityManager.GetCityAt and CityManager.GetCityAt(__X__,__Y__)
end)
if not siegeKnown then print("ERR:UNIT_CITY_ATTACK_REFUSED|CITY_TARGET_UNKNOWN");print("__END__");return end
if siegeTarget then
__CONTEXT__
 local ok,r=pcall(function()
  local prior=__civmcpSiege
  assert(not prior or prior.closed,"PRIOR_SIEGE_UNRESOLVED")
  local r={token=__TOKEN__,owner=me,civ=PlayerConfigurations[me]:GetCivilizationTypeName(),
   seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn(),
   x=__X__,y=__Y__,handlers={},melee=not isRanged and not isAir,range=attackRange}
  r.actor=siegeActor(unit)
  assert(r.actor.id%65536==__UNIT__%65536 and (__UNIT__<65536 or r.actor.id==__UNIT__),"ACTOR_CHANGED")
  r.city=siegeCity(r.x,r.y)
  r.operation=isAir and UnitOperationTypes.AIR_ATTACK or isRanged and UnitOperationTypes.RANGE_ATTACK or UnitOperationTypes.MOVE_TO
  assert(r.operation~=nil,"OPERATION_UNKNOWN")
  r.params={};r.params[UnitOperationTypes.PARAM_X]=r.x;r.params[UnitOperationTypes.PARAM_Y]=r.y
  if r.melee then r.params[UnitOperationTypes.PARAM_MODIFIERS]=UnitOperationMoveModifiers.ATTACK end
  if mode=="RANGED" then r.combatType=CombatTypes.RANGED;assert(r.combatType~=nil,"COMBAT_TYPE_UNKNOWN") end
  if mode=="BOMBARD" then r.combatType=CombatTypes.BOMBARD;assert(r.combatType~=nil,"COMBAT_TYPE_UNKNOWN") end
  for _,name in ipairs({"CombatVisEnd","UnitRemovedFromMap"}) do
   assert(Events[name] and type(Events[name].Add)=="function" and type(Events[name].Remove)=="function","OBSERVER_UNKNOWN")
  end
  assert(CombatVisType and scopeIndex(CombatVisType.ATTACKER) and scopeIndex(CombatVisType.DEFENDER),"COMBAT_OBSERVER_UNKNOWN")
  siegeEligible(r,unit)
  return r
 end)
 if not ok then print("ERR:UNIT_CITY_ATTACK_REFUSED|"..tostring(r));print("__END__");return end
 siegeRetire(__civmcpSiege);__civmcpSiege=r
 r.handlers.CombatVisEnd=function(data)
  if __civmcpSiege~=r or not r.entered or not sameScope(r) then return end
  local function matches(key,c)
   local s=type(data)=="table" and data[key]
   return type(s)=="table" and s.componentType==c.type and s.playerID==c.player and s.componentID==c.id
  end
  if matches(CombatVisType.ATTACKER,r.actor.component) then
   if matches(CombatVisType.DEFENDER,r.city.center) then r.combat=true else r.invalid=true end
  end
 end
 r.handlers.UnitRemovedFromMap=function(owner,id)
  if __civmcpSiege==r and r.entered and sameScope(r) and owner==r.owner and id==r.actor.id then r.removed=true end
 end
 local armed=pcall(function() for name,handler in pairs(r.handlers) do Events[name].Add(handler) end end)
 local final,reason=pcall(function() assert(armed,"OBSERVER_UNKNOWN");siegeEligible(r,unit) end)
 if not final then
  r.closed=true;siegeRetire(r);print("ERR:UNIT_CITY_ATTACK_REFUSED|"..tostring(reason));print("__END__");return
 end
 r.entered=true
 local requested,reason=pcall(function() UnitManager.RequestOperation(unit,r.operation,r.params) end)
 r.returned=requested
 if requested then print("OK:UNIT_CITY_ATTACK_REQUESTED|"..r.token)
 else print("ERR:OUTCOME_UNKNOWN|CITY_ATTACK_ENTERED|"..tostring(reason)) end
 print("__END__");return
end
'''
    return (template.replace('__CONTEXT__', _CONTEXT).replace('__TOKEN__', json.dumps(token))
            .replace('__UNIT__', str(unit_id)).replace('__X__', str(x)).replace('__Y__', str(y)).replace('__END__', SENTINEL))


def build_city_attack_observation(unit_id: int, token: str) -> str:
    """Use the receipt when present; otherwise continue ordinary unit followup."""
    template = '''
if __civmcpSiege and __civmcpSiege.token==__TOKEN__ then
__CONTEXT__
 local r=__civmcpSiege
 local ok,detail=pcall(function()
  assert(r.actor.id%65536==__UNIT__%65536 and (__UNIT__<65536 or r.actor.id==__UNIT__)
   and sameScope(r) and UI.IsGameCoreBusy()==false,"SCOPE_OR_CORE_UNKNOWN")
  assert(r.entered and r.returned and r.combat,"COMBAT_WITNESS_INCOMPLETE")
  assert(PlayersVisibility[r.owner]:IsVisible(r.x,r.y)==true,"TARGET_NOT_VISIBLE")
  local actor=UnitManager.GetUnit(r.owner,r.actor.id%65536)
  if actor then siegeActorIdentity(r,actor) end
  siegeCityIdentity(r)
  assert(not r.invalid,"COMBAT_ID_CHANGED")
  local s=siegeCity(r.x,r.y)
  local a=actor and siegeActor(actor)
  if a then
   assert(a.owner==r.owner and a.id==r.actor.id and a.unitType==r.actor.unitType
    and siegeSame(a.component,r.actor.component) and not r.removed,"ACTOR_CHANGED")
  else assert(r.removed,"ACTOR_UNKNOWN") end
  local captured=r.melee and a and s.owner==r.owner and a.x==r.x and a.y==r.y
  if not captured then
   -- Owner transition can precede the final move into the captured city.
   -- Keep observing that allowed intermediate state; do not latch it invalid.
   assert(not (r.melee and s.owner==r.owner),"CAPTURE_POSITION_UNCONFIRMED")
   if s.owner~=r.city.owner or s.id~=r.city.id or not siegeSame(s.center,r.city.center) then r.invalid=true end
   assert(not r.invalid,"CITY_ID_CHANGED")
  end
  assert(captured or s.wall<r.city.wall or s.garrison<r.city.garrison
   or a and a.hp<r.actor.hp or r.removed,"CITY_EFFECT_UNCONFIRMED")
  assert(sameScope(r),"FINAL_SCOPE_CHANGED")
  return "target:"..r.city.owner..":"..r.city.id.."|wall:"..r.city.wall.."->"..s.wall
   .."|garrison:"..r.city.garrison.."->"..s.garrison.."|owner:"..r.city.owner.."->"..s.owner
   .."|attacker_hp:"..r.actor.hp.."->"..(a and a.hp or 0)
 end)
 if ok then
  r.closed=true;siegeRetire(r)
  print("UNIT_CITY_ATTACK_CONFIRMED|"..r.token.."|__UNIT__|"..detail)
 else print("UNIT_CITY_ATTACK_OBSERVED|"..r.token.."|"..tostring(detail)) end
 print("__END__");return
end
'''
    return (template.replace('__CONTEXT__', _CONTEXT).replace('__TOKEN__', json.dumps(token))
            .replace('__UNIT__', str(unit_id)).replace('__END__', SENTINEL))


def parse_city_attack(lines: list[str], unit_id: int, token: str) -> str | None:
    rows = [row.split('|') for row in lines if row.startswith('UNIT_CITY_ATTACK_CONFIRMED|')]
    if len(rows) != 1 or len(rows[0]) != 8 or rows[0][1:3] != [token, str(unit_id)]:
        return None
    row = rows[0]
    if not (re.fullmatch(r'target:\d+:\d+', row[3])
            and all(re.fullmatch(label + r':\d+->\d+', value)
                    for label, value in zip(('wall', 'garrison', 'owner', 'attacker_hp'), row[4:]))):
        return None
    return f"UNIT_CITY_ATTACK_CONFIRMED|unit:{unit_id}|" + '|'.join(row[3:])

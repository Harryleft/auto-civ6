"""Read-only binding around the existing InGame native combat simulation."""
from __future__ import annotations

import json
import re

from civ_mcp.lua._helpers import _LUA_ACTION_SCOPE


def binding_context(unit_id: int, x: int, y: int, scope: tuple, token: str) -> str:
    civ, seed, turn, owner = scope
    return _LUA_ACTION_SCOPE + f'''
local previewScope={{civ={json.dumps(civ)},seed={seed},turn={turn},owner={owner}}}
local previewActorFacts,previewTargetFacts,previewTargetKind
local function previewFinite(v)
 return type(v)=="number" and v==v and math.abs(v)<math.huge
end
local function previewComponent(component)
 assert(type(component)=="table" and scopePlayer(component.player)
  and scopeIndex(component.id) and scopeIndex(component.type),"component identity unavailable")
 return {{component.player,component.id,component.type}}
end
local function previewUnitFacts(value)
 local component=previewComponent(value:GetComponentID())
 local facts={{value:GetOwner(),value:GetID(),value:GetX(),value:GetY(),value:GetType(),
  value:GetMaxDamage()-value:GetDamage(),component[1],component[2],component[3]}}
 assert(scopePlayer(facts[1]) and scopeIndex(facts[2]) and scopeIndex(facts[3])
  and scopeIndex(facts[4]) and scopeIndex(facts[5]) and previewFinite(facts[6]) and facts[6]>0
  and facts[1]==component[1],"unit identity unavailable")
 return facts
end
local function previewCityFacts(city)
 local center=GameInfo.Districts["DISTRICT_CITY_CENTER"]
 assert(center,"city center unavailable")
 local component,hp
 for _,district in city:GetDistricts():Members() do
  if district:GetType()==center.Index then
   assert(not component,"ambiguous city center")
   component=previewComponent(district:GetComponentID())
   hp=district:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON)-district:GetDamage(DefenseTypes.DISTRICT_GARRISON)
  end
 end
 assert(component and previewFinite(hp) and hp>0,"city defense unavailable")
 local facts={{city:GetOwner(),city:GetID(),city:GetX(),city:GetY(),center.Index,hp,component[1],component[2],component[3]}}
 assert(scopePlayer(facts[1]) and scopeIndex(facts[2]) and scopeIndex(facts[3])
  and scopeIndex(facts[4]) and scopeIndex(facts[5]) and facts[1]==component[1],"city identity unavailable")
 return facts
end
local function previewSame(actual,expected)
 for i=1,#expected do assert(actual[i]==expected[i],"actor or target changed during preview") end
end
local function previewStart(unit)
 assert(sameScope(previewScope),"world or turn changed or unavailable")
 previewActorFacts=previewUnitFacts(unit)
 assert(previewActorFacts[1]=={owner} and previewActorFacts[2]=={unit_id},"original actor identity changed")
 previewActorFacts[10]=unit:GetMovesRemaining()
 previewActorFacts[11]=unit:GetAttacksRemaining()
 assert(previewFinite(previewActorFacts[10]) and scopeIndex(previewActorFacts[11]),"actor attack availability unknown")
 if previewActorFacts[10]<=0 then return "COMBAT_PREVIEW_INELIGIBLE|NO_MOVES" end
 if previewActorFacts[11]==0 then return "COMBAT_PREVIEW_INELIGIBLE|NO_ATTACKS_REMAINING" end
end
local function previewBind(enemy,city)
 previewTargetKind=city and "city" or "unit"
 previewTargetFacts=city and previewCityFacts(city) or previewUnitFacts(enemy)
 assert(previewTargetFacts[3]=={x} and previewTargetFacts[4]=={y},"target position changed")
end
local function previewFinish(mode)
 assert(sameScope(previewScope),"world or turn changed or unavailable")
 assert(UI.IsGameCoreBusy()==false,"core state changed or unavailable")
 assert(attackTargetVisibility({owner},{x},{y})==true,"target visibility changed or unavailable")
 local actor=UnitManager.GetUnit({owner},{unit_id})
 assert(actor,"original actor unavailable")
 local actorFacts=previewUnitFacts(actor)
 actorFacts[10]=actor:GetMovesRemaining();actorFacts[11]=actor:GetAttacksRemaining()
 previewSame(actorFacts,previewActorFacts)
 local target
 if previewTargetKind=="city" then
  target=CityManager.GetCityAt({x},{y})
  assert(target,"original city unavailable")
  previewSame(previewCityFacts(target),previewTargetFacts)
 else
  assert(CityManager.GetCityAt({x},{y})==nil,"target plot became a city")
  local units=Map.GetUnitsAt({x},{y})
  assert(units,"original target unavailable")
  for other in units:Units() do
   local visible=PlayersVisibility[{owner}]:IsUnitVisible(other)
   assert(type(visible)=="boolean","unit visibility unavailable")
   assert(visible,"hidden unit target is not supported")
   if other:GetOwner()~={owner} then
    assert(not target,"ambiguous unit target")
    target=other
   end
  end
  assert(target,"original target unavailable")
  previewSame(previewUnitFacts(target),previewTargetFacts)
 end
 local targetOwner=previewTargetFacts[1]
 assert(targetOwner~={owner} and (targetOwner==63 or Players[{owner}]:GetDiplomacy():IsAtWarWith(targetOwner)==true),"target is not at war")
 assert(sameScope(previewScope),"world or turn changed or unavailable")
 local a,t=previewActorFacts,previewTargetFacts
 return table.concat({{"COMBAT_PREVIEW_BINDING",{json.dumps(token)},mode,
  a[1],a[2],a[3],a[4],a[7],a[8],a[9],previewTargetKind,
  t[1],t[2],t[3],t[4],t[7],t[8],t[9]}},"|")
end
'''


def parse_binding(lines: list[str], token: str, unit_id: int, x: int, y: int, owner: int) -> dict | None:
    rows = [line.split('|') for line in lines if line.startswith('COMBAT_PREVIEW_BINDING|')]
    if len(rows) != 1:
        return None
    p = rows[0]
    if len(p) != 18 or p[1] != token or p[2] not in {'MELEE', 'RANGED', 'BOMBARD'} or p[10] not in {'unit', 'city'}:
        return None
    indices = (3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17)
    if any(not re.fullmatch(r'\d+', p[i]) for i in indices):
        return None
    n = {i: int(p[i]) for i in indices}
    if (n[3] != owner or n[4] != unit_id or n[7] != owner or n[11] == owner
        or n[11] > 63 or n[15] != n[11] or (n[13], n[14]) != (x, y)):
        return None
    def entity(start: int, component: int) -> dict:
        return {'owner': n[start], 'id': n[start+1], 'x': n[start+2], 'y': n[start+3],
                'component': {'player': n[component], 'id': n[component+1], 'type': n[component+2]}}
    return {'mode': p[2], 'attacker': entity(3, 7), 'target': entity(11, 15) | {'kind': p[10]}}

"""Native escort and military formations, with exact participants and receipts.

UnitPanel supplies ENTER_FORMATION candidates; WorldInput supplies FORM_CORPS /
FORM_ARMY targets. Never set formation levels or remove the consumed unit here.
"""
from __future__ import annotations

import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE

COMMANDS = {"link": "ENTER_FORMATION", "unlink": "EXIT_FORMATION", "corps": "FORM_CORPS", "army": "FORM_ARMY"}

_CONTEXT = _LUA_ACTION_SCOPE + r'''
local function ownUnit(owner,id)
 assert(scopeIndex(id),"INVALID_UNIT_ID")
 local u=UnitManager.GetUnit(owner,id%65536)
 assert(u and u:GetOwner()==owner and (id<65536 or u:GetID()==id),"UNIT_ID_CHANGED")
 assert(scopeIndex(u:GetID()) and scopeIndex(u:GetX()) and scopeIndex(u:GetY()),"UNIT_OFF_MAP")
 return u
end
local function index(v)assert(scopeIndex(v),"NATIVE_VALUE_UNKNOWN");return v end
local function facts(u)
 return {id=index(u:GetID()),type=index(u:GetType()),x=index(u:GetX()),y=index(u:GetY()),
  military=index(u:GetMilitaryFormation()),count=index(u:GetFormationUnitCount())}
end
local function frame()
 local owner=Game.GetLocalPlayer()
 return {owner=owner,civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}
end
local function candidates(u,command,link)
 local allowed,result
 if link then allowed,result=UnitManager.CanStartCommand(u,command,nil,true)
 else allowed,result=UnitManager.CanStartCommand(u,command,false,true)end
 assert(type(allowed)=="boolean","ELIGIBILITY_UNKNOWN")
 if not allowed then return false,{} end
 if not link then result=UnitManager.GetCommandTargets(u,command) end
 assert(type(result)=="table" and UnitCommandResults.UNITS~=nil,"TARGETS_UNKNOWN")
 local units=result[UnitCommandResults.UNITS]
 assert(type(units)=="table","TARGETS_UNKNOWN")
 return true,units
end
local function ownedTarget(target,owner,link)
 -- WorldInput resolves military targets through the selected player's units;
 -- unlike escort candidates, those component IDs need not carry .player.
 return target.player==owner or (not link and target.player==nil)
end
local function retire(r)
 for name,handler in pairs(r and r.handlers or {})do pcall(function()Events[name].Remove(handler)end)end
end
'''


def _validate(unit_id, action=None, target_unit_id=None):
    if type(unit_id) is not int or unit_id < 0:
        raise ValueError("unit_id must be a nonnegative integer")
    if action is not None:
        if action not in COMMANDS:
            raise ValueError("action must be link, unlink, corps or army")
        if action == "unlink":
            if target_unit_id is not None:
                raise ValueError("unlink does not accept a target")
        elif type(target_unit_id) is not int or target_unit_id < 0 or target_unit_id == unit_id:
            raise ValueError("a distinct nonnegative target_unit_id is required")


def build_options(unit_id: int) -> str:
    _validate(unit_id)
    return _CONTEXT + f'''
local ok,err=pcall(function()
 local r=frame();local u=ownUnit(r.owner,{unit_id});local f=facts(u)
 local lines={{"FORMATION_UNIT|"..f.id.."|"..f.type.."|"..f.x.."|"..f.y.."|"..f.military.."|"..f.count}}
 for _,choice in ipairs({{{','.join(json.dumps(a) for a in COMMANDS)}}})do
  local known,allowed,targets=pcall(function()
   local names={{link="ENTER_FORMATION",unlink="EXIT_FORMATION",corps="FORM_CORPS",army="FORM_ARMY"}}
   local command=UnitCommandTypes[names[choice]]
   assert(scopeInteger(command),"COMMAND_UNAVAILABLE")
   if choice=="unlink"then
    local allowed=UnitManager.CanStartCommand(u,command,false,true)
    assert(type(allowed)=="boolean","ELIGIBILITY_UNKNOWN");return allowed,{{}}
   end
   return candidates(u,command,choice=="link")
  end)
  if not known then lines[#lines+1]="FORMATION_UNKNOWN|"..choice
  else
   local optionLines={{}};local seen={{}}
   local valid=pcall(function()
    for _,target in pairs(targets)do
     assert(ownedTarget(target,r.owner,choice=="link") and scopeIndex(target.id),"TARGET_ID_UNKNOWN")
     local other=facts(ownUnit(r.owner,target.id))
     assert(not seen[other.id] and other.id~=f.id,"AMBIGUOUS_TARGET")
     seen[other.id]=true
     optionLines[#optionLines+1]="FORMATION_TARGET|"..choice.."|"..other.id.."|"..other.type.."|"..other.x.."|"..other.y.."|"..other.military.."|"..other.count
    end
   end)
   if not valid then lines[#lines+1]="FORMATION_UNKNOWN|"..choice
   else
    lines[#lines+1]="FORMATION_OPTION|"..choice.."|"..tostring(allowed)
    for _,line in ipairs(optionLines)do lines[#lines+1]=line end
   end
  end
 end
 assert(sameScope(r),"WORLD_CHANGED")
 print("FORMATION_SCOPE|"..r.owner.."|"..r.turn.."|"..r.seed.."|"..r.civ)
 for _,line in ipairs(lines)do print(line)end
end)
if not ok then print("FORMATION_ERROR|"..tostring(err))end
print("{SENTINEL}")
'''


def parse_options(lines: list[str]) -> dict:
    try:
        errors = [r.split("|", 1)[1] for r in lines if r.startswith("FORMATION_ERROR|")]
        if errors:
            return {"status": "unknown", "data": None, "error": errors[0]}
        scope, = [r.split("|") for r in lines if r.startswith("FORMATION_SCOPE|")]
        unit, = [r.split("|") for r in lines if r.startswith("FORMATION_UNIT|")]
        def unit_fields(row):
            values = dict(zip(("id", "unit_type_index", "x", "y", "military_formation", "escort_unit_count"), map(int, row), strict=True))
            if any(v < 0 for v in values.values()):
                raise ValueError("Invalid native unit facts")
            return values
        data = unit_fields(unit[1:]) | {"actions": {}}
        unknown = []
        for action in COMMANDS:
            options = [r.split("|") for r in lines if r.startswith("FORMATION_OPTION|" + action + "|")]
            if f"FORMATION_UNKNOWN|{action}" in lines:
                unknown.append(action)
                data["actions"][action] = {"allowed": None, "targets": None}
            else:
                option, = options
                if len(option) != 3 or option[2] not in {"true", "false"}:
                    raise ValueError("Invalid native formation option")
                targets = [unit_fields(r.split("|")[2:]) for r in lines if r.startswith("FORMATION_TARGET|" + action + "|")]
                data["actions"][action] = {"allowed": option[2] == "true", "targets": targets}
        if len(scope) != 5:
            raise ValueError("Invalid formation scope")
        return {"status": "partial" if unknown else "ready", "data": data, "unknown_fields": unknown,
                "identity": {"player_id": int(scope[1]), "turn": int(scope[2]), "seed": int(scope[3]), "civilization": scope[4]}}
    except (ValueError, IndexError) as error:
        return {"status": "unknown", "data": None, "error": "Incomplete native formation query: " + str(error)}


def build_submit(unit_id: int, action: str, target_unit_id: int | None, token: str) -> str:
    _validate(unit_id, action, target_unit_id)
    target = "nil" if target_unit_id is None else str(target_unit_id)
    return _CONTEXT + f'''
local action,token={json.dumps(action)},{json.dumps(token)}
local ok,r,u,params=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local r=frame();local u=ownUnit(r.owner,{unit_id});r.actor=facts(u)
 r.token,r.action,r.requested,r.target_requested=token,action,{unit_id},{target}
 r.command=UnitCommandTypes.{COMMANDS[action]}
 assert(scopeInteger(r.command) and type(UnitManager.RequestCommand)=="function","COMMAND_UNAVAILABLE")
 local params={{}}
 if action~="unlink"then
  local other=ownUnit(r.owner,{target});r.target=facts(other)
  assert(r.target.id~=r.actor.id,"SAME_UNIT")
  local allowed,targets=candidates(u,r.command,action=="link");local matches=0
  for _,candidate in pairs(targets)do
   if ownedTarget(candidate,r.owner,action=="link") and candidate.id==r.target.id then matches=matches+1 end
  end
  assert(allowed and matches==1,"TARGET_NOT_ELIGIBLE")
  assert(UnitCommandTypes.PARAM_UNIT_PLAYER~=nil and UnitCommandTypes.PARAM_UNIT_ID~=nil,"PARAMETERS_UNAVAILABLE")
  params[UnitCommandTypes.PARAM_UNIT_PLAYER]=r.owner;params[UnitCommandTypes.PARAM_UNIT_ID]=r.target.id
  if action=="link"then
   assert(r.actor.x==r.target.x and r.actor.y==r.target.y,"LINK_STATE_UNAVAILABLE")
  else
   assert(r.actor.type==r.target.type,"DIFFERENT_UNIT_TYPES")
   r.expected=(action=="corps")and MilitaryFormationTypes.CORPS_FORMATION or MilitaryFormationTypes.ARMY_FORMATION
   assert(scopeIndex(r.expected) and r.actor.military~=r.expected and r.target.military~=r.expected,"FORMATION_LEVEL_UNAVAILABLE")
  end
 else assert(r.actor.count>1,"NOT_LINKED")end
 assert(UnitManager.CanStartCommand(u,r.command,params)==true,"CANNOT_START_COMMAND")
 r.handlers={{}};r.removed={{}};r.formed={{}}
 assert(sameScope(r),"WORLD_CHANGED")
 return r,u,params
end)
if not ok then print("ERR:UNIT_FORMATION_REFUSED|"..tostring(r));print("{SENTINEL}");return end
local function bind(name,fn)
 local event=Events[name];assert(type(event.Add)=="function" and type(event.Remove)=="function","EVENT_UNAVAILABLE:"..name)
 local handler=function(...)
  if __civmcpFormation==r and r.entered and sameScope(r)then fn(...)end
 end
 r.handlers[name]=handler;event.Add(handler)
end
retire(__civmcpFormation);__civmcpFormation=r
local armed,reason=pcall(function()
 bind("UnitCommandStarted",function(owner,id,command)
  if owner==r.owner and id==r.actor.id and command==r.command then r.started=true end
 end)
 if action=="link"or action=="unlink"then
  bind(action=="link"and "UnitEnterFormation"or "UnitExitFormation",function(p1,id1,p2,id2)
   if p1~=r.owner or p2~=r.owner or id1==id2 then return end
   if action=="link"then
    if (id1==r.actor.id and id2==r.target.id)or(id2==r.actor.id and id1==r.target.id)then r.linked=true end
   elseif id1==r.actor.id or id2==r.actor.id then r.linked=true end
  end)
 else
  bind(action=="corps"and "UnitFormCorps"or "UnitFormArmy",function(owner,id)
   if owner==r.owner and (id==r.actor.id or id==r.target.id)then r.formed[id]=true end
  end)
  bind("UnitRemovedFromMap",function(owner,id)
   if owner==r.owner and (id==r.actor.id or id==r.target.id)then r.removed[id]=true end
  end)
 end
end)
if not armed then retire(r);print("ERR:UNIT_FORMATION_REFUSED|"..tostring(reason));print("{SENTINEL}");return end
-- Getters and listener installation may dispatch events. Revalidate exact inputs
-- immediately before native entry; a refusal here still proves zero submissions.
local valid,actorOrError=pcall(function()
 local actor=ownUnit(r.owner,r.actor.id)
 local f=facts(actor)
 assert(f.type==r.actor.type and f.x==r.actor.x and f.y==r.actor.y and f.military==r.actor.military and f.count==r.actor.count,"ACTOR_CHANGED")
 if r.target then
  local f=facts(ownUnit(r.owner,r.target.id))
  for _,key in ipairs({{"id","type","x","y","military","count"}})do assert(f[key]==r.target[key],"TARGET_CHANGED")end
 end
 assert(UnitManager.CanStartCommand(actor,r.command,params)==true,"CANNOT_START_COMMAND")
 assert(sameScope(r),"WORLD_CHANGED")
 return actor
end)
if not valid then retire(r);print("ERR:UNIT_FORMATION_REFUSED|"..tostring(actorOrError));print("{SENTINEL}");return end
r.entered=true
local accepted=UnitManager.RequestCommand(actorOrError,r.command,params)
r.returned=accepted~=false
print("OK:UNIT_FORMATION_REQUESTED|"..r.actor.id.."|"..action)
print("{SENTINEL}")
'''


def build_confirmation(unit_id: int, action: str, target_unit_id: int | None, token: str) -> str:
    _validate(unit_id, action, target_unit_id)
    target = "nil" if target_unit_id is None else str(target_unit_id)
    return _CONTEXT + f'''
local r=__civmcpFormation
local ok,survivor=pcall(function()
 assert(r and r.token=={json.dumps(token)} and r.requested=={unit_id} and r.action=={json.dumps(action)} and r.target_requested=={target})
 assert(sameScope(r) and r.entered and r.returned and r.started and UI.IsGameCoreBusy()==false)
 local function current(id)
  local unit=UnitManager.GetUnit(r.owner,id%65536)
  if unit and unit:GetID()==id and unit:GetOwner()==r.owner then return unit end
 end
 local actor=current(r.actor.id)
 if r.action=="link"then
  local other=current(r.target.id);assert(actor and other and r.linked)
  -- UnitPanel defines linked as count>1. Preserve either native standalone
  -- representation (0 or 1), while requiring the exact combined group size.
  local expected=math.max(1,r.actor.count)+math.max(1,r.target.count)
  assert(actor:GetFormationUnitCount()==expected and other:GetFormationUnitCount()==expected)
  assert(actor:GetX()==other:GetX() and actor:GetY()==other:GetY())
  assert(sameScope(r));return r.actor.id
 elseif r.action=="unlink"then
  assert(actor and r.linked)
  local count=actor:GetFormationUnitCount();assert(scopeIndex(count) and count<=1)
  assert(sameScope(r));return r.actor.id
 else
  local other=current(r.target.id);assert((actor~=nil)~=(other~=nil))
  local survivor=actor or other;local consumed=actor and r.target.id or r.actor.id
  assert(r.removed[consumed] and r.formed[survivor:GetID()])
  assert(survivor:GetType()==r.actor.type and survivor:GetMilitaryFormation()==r.expected)
  assert(sameScope(r));return survivor:GetID()
 end
end)
if ok then
 retire(r)
 print("UNIT_FORMATION_CONFIRMED|{token}|{unit_id}|{action}|"..survivor)
else print("UNIT_FORMATION_OBSERVED|{token}|UNKNOWN")end
print("{SENTINEL}")
'''


def parse_confirmation(lines: list[str], unit_id: int, action: str, token: str) -> str | None:
    rows = [r.split("|") for r in lines if r.startswith("UNIT_FORMATION_CONFIRMED|")]
    if len(rows) == 1 and len(rows[0]) == 5:
        row = rows[0]
        if row[1:4] == [token, str(unit_id), action] and row[4].isdigit():
            return f"FORMATION_CONFIRMED|{action}|unit:{row[4]}"
    return None

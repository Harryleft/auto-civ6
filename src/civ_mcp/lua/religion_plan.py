"""Religion choices retain original targets and confirm each native phase."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


_FACTS = _LUA_ACTION_SCOPE + '''
local function integer(value)
 assert(type(value)=="number" and math.abs(value)<math.huge and value%1==0,"FACT_UNAVAILABLE")
 return value
end
local function available(belief,religion)
 local gameReligion=Game.GetReligion()
 assert(gameReligion:IsInSomePantheon(belief)==false and gameReligion:IsInSomeReligion(belief)==false
  and gameReligion:IsTooManyForReligion(belief,religion)==false,"BELIEF_UNAVAILABLE")
end
local function beliefSet(owner,religion)
 local entry=nil
 local records=Game.GetReligion():GetReligions()
 assert(type(records)=="table","RELIGIONS_UNAVAILABLE")
 for _,record in pairs(records) do
  if record.Religion==religion then
   assert(entry==nil and record.Founder==owner,"RELIGION_IDENTITY_CHANGED")
   entry=record
  end
 end
 assert(entry and type(entry.Beliefs)=="table","RELIGION_UNCONFIRMED")
 local set={}
 for _,id in pairs(entry.Beliefs) do
  integer(id);assert(id>=0 and not set[id],"BELIEF_SET_UNAVAILABLE");set[id]=true
 end
 return set
end
local function exactSet(actual,expected)
 for id in pairs(actual) do assert(expected[id],"UNEXPECTED_BELIEF") end
 for id in pairs(expected) do assert(actual[id],"BELIEF_UNCONFIRMED") end
end
local function parameters()
 assert(type(UI.RequestPlayerOperation)=="function" and PlayerOperations.PARAM_INSERT_MODE~=nil
  and PlayerOperations.VALUE_EXCLUSIVE~=nil,"OPERATION_UNAVAILABLE")
 local params={}
 params[PlayerOperations.PARAM_INSERT_MODE]=PlayerOperations.VALUE_EXCLUSIVE
 return params
end
'''


def build_pantheon_plan(belief_type: str, token: str) -> str:
    return _FACTS + f'''
local ok,r=pcall(function()
 assert(__civmcpReligionPlan==nil or type(__civmcpReligionPlan)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local religion=Players[owner]:GetReligion()
 local belief=GameInfo.Beliefs[{json.dumps(belief_type)}]
 assert(belief and belief.BeliefClassType=="BELIEF_CLASS_PANTHEON","INVALID_PANTHEON_BELIEF")
 local current=integer(religion:GetPantheon())
 local noop=current==belief.Index
 local params
 if not noop then
  assert(current<0,"ALREADY_HAS_PANTHEON")
  assert(religion:CanCreatePantheon()==true,"CANNOT_CREATE_PANTHEON")
  available(belief.Index,integer(religion:GetReligionTypeCreated()))
  params=parameters()
  params[PlayerOperations.PARAM_BELIEF_TYPE]=integer(belief.Hash)
  assert(PlayerOperations.FOUND_PANTHEON~=nil,"OPERATION_UNAVAILABLE")
 end
 return {{token={json.dumps(token)},kind="choose_pantheon",owner=owner,belief=integer(belief.Index),
  beliefType={json.dumps(belief_type)},params=params,noop=noop,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
  turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:RELIGION_PLAN_REFUSED|"..tostring(r));print("{SENTINEL}");return end
__civmcpReligionPlan=r
if not r.noop then
 r.entered=true
 UI.RequestPlayerOperation(r.owner,PlayerOperations.FOUND_PANTHEON,r.params)
 r.returned=true
end
print("OK:RELIGION_PLAN_REQUESTED|"..r.token)
print("{SENTINEL}")
'''


def build_religion_plan(religion_type: str, follower_belief: str, founder_belief: str, token: str) -> str:
    return _FACTS + f'''
local ok,r=pcall(function()
 assert(__civmcpReligionPlan==nil or type(__civmcpReligionPlan)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local playerReligion=Players[owner]:GetReligion()
 local gameReligion=Game.GetReligion()
 local current=integer(playerReligion:GetReligionTypeCreated())
 local pantheon=integer(playerReligion:GetPantheon())
 local earned=integer(playerReligion:GetNumBeliefsEarned())
 local religion=GameInfo.Religions[{json.dumps(religion_type)}]
 local follower=GameInfo.Beliefs[{json.dumps(follower_belief)}]
 local founder=GameInfo.Beliefs[{json.dumps(founder_belief)}]
 assert(religion and religion.Pantheon==false and religion.RequiresCustomName~=true,"INVALID_RELIGION")
 assert(follower and follower.BeliefClassType=="BELIEF_CLASS_FOLLOWER","INVALID_FOLLOWER_BELIEF")
 assert(founder and founder.BeliefClassType=="BELIEF_CLASS_FOUNDER","INVALID_FOUNDER_BELIEF")
 assert(follower.Index~=founder.Index,"DUPLICATE_BELIEF")
 local r={{token={json.dumps(token)},kind="found_religion",owner=owner,religion=integer(religion.Index),
  religionType={json.dumps(religion_type)},followerType={json.dumps(follower_belief)},founderType={json.dumps(founder_belief)},
  pantheon=pantheon,earned=earned,baseline={{}},confirmed=0,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
  turn=Game.GetCurrentGameTurn()}}
 if current>=0 then
  assert(current==r.religion,"RELIGION_ALREADY_CREATED")
  r.baseline=beliefSet(owner,current)
  assert(r.baseline[follower.Index] and r.baseline[founder.Index],"RELIGION_ALREADY_CREATED_WITH_OTHER_BELIEFS")
  r.noop=true;return r
 end
 assert(pantheon>=0 and earned>=2,"FOUNDING_NOT_READY")
 assert(gameReligion:HasBeenFounded(religion.Index)==false,"RELIGION_UNAVAILABLE")
 local records=gameReligion:GetReligions()
 assert(type(records)=="table","RELIGIONS_UNAVAILABLE")
 for _,entry in pairs(records) do
  assert(entry.Religion~=religion.Index and entry.Founder~=owner,"RELIGION_BASELINE_CHANGED")
 end
 available(follower.Index,current);available(founder.Index,current)
 assert(PlayerOperations.FOUND_RELIGION~=nil and PlayerOperations.ADD_BELIEF~=nil,"OPERATION_UNAVAILABLE")
 local p1,p2,p3=parameters(),parameters(),parameters()
 p1[PlayerOperations.PARAM_RELIGION_TYPE]=integer(religion.Hash)
 p2[PlayerOperations.PARAM_BELIEF_TYPE]=integer(follower.Hash)
 p3[PlayerOperations.PARAM_BELIEF_TYPE]=integer(founder.Hash)
 r.phases={{{{op=PlayerOperations.FOUND_RELIGION,params=p1}},
   {{op=PlayerOperations.ADD_BELIEF,params=p2,belief=integer(follower.Index)}},
   {{op=PlayerOperations.ADD_BELIEF,params=p3,belief=integer(founder.Index)}}}}
 return r
end)
if not ok then print("ERR:RELIGION_PLAN_REFUSED|"..tostring(r));print("{SENTINEL}");return end
__civmcpReligionPlan=r
if not r.noop then
 local phase=r.phases[1]
 phase.entered=true
 UI.RequestPlayerOperation(r.owner,phase.op,phase.params)
 phase.returned=true
end
print("OK:RELIGION_PLAN_REQUESTED|"..r.token)
print("{SENTINEL}")
'''


_OBSERVE = _FACTS + '''
local function observe(r,token)
 assert(type(r)=="table" and r.token==token,"RECEIPT_UNAVAILABLE")
 assert(sameScope(r),"SCOPE_CHANGED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local playerReligion=Players[r.owner]:GetReligion()
 if r.kind=="choose_pantheon" then
  assert(r.noop or r.entered==true and r.returned==true,"REQUEST_NOT_RETURNED")
  assert(playerReligion:GetPantheon()==r.belief,"PANTHEON_UNCONFIRMED")
  return r.noop and "UNCHANGED" or "CONFIRMED"
 end
 assert(playerReligion:GetPantheon()==r.pantheon,"PANTHEON_CHANGED")
 if r.noop then
  assert(playerReligion:GetReligionTypeCreated()==r.religion,"RELIGION_CHANGED")
  exactSet(beliefSet(r.owner,r.religion),r.baseline)
  return "UNCHANGED"
 end
 assert(r.phases[1].entered==true and r.phases[1].returned==true,"FOUNDING_REQUEST_NOT_RETURNED")
 assert(playerReligion:GetReligionTypeCreated()==r.religion,"RELIGION_UNCONFIRMED")
 local actual=beliefSet(r.owner,r.religion)
 local expected={}
 for id in pairs(r.baseline) do expected[id]=true end
 r.confirmed=1
 for i=2,3 do
  local phase=r.phases[i]
  if phase.entered then
   assert(phase.returned==true,"BELIEF_REQUEST_NOT_RETURNED:"..i)
   assert(actual[phase.belief],"BELIEF_UNCONFIRMED:"..i)
   expected[phase.belief]=true;r.confirmed=i
  else
   exactSet(actual,expected)
   assert(integer(playerReligion:GetNumBeliefsEarned())>=2,"EARNED_BELIEFS_UNAVAILABLE")
   available(phase.belief,r.religion)
   assert(type(UI.RequestPlayerOperation)=="function" and PlayerOperations.ADD_BELIEF==phase.op,"OPERATION_UNAVAILABLE")
   return "READY",i
  end
 end
 exactSet(actual,expected)
 return "CONFIRMED"
end
'''


def build_religion_confirmation(token: str) -> str:
    return _OBSERVE + f'''
local r=__civmcpReligionPlan
local ok,status,nextPhase=pcall(observe,r,{json.dumps(token)})
if type(r)=="table" and r.token=={json.dumps(token)} and r.phases then
 local parts={{}}
 for i,phase in ipairs(r.phases) do
  table.insert(parts,i..":"..tostring(phase.entered==true).."/"..tostring(phase.returned==true))
 end
 print("RELIGION_PROGRESS|"..r.token.."|confirmed:"..r.confirmed.."/3|entered/returned:"..table.concat(parts,","))
end
if ok and status=="READY" then print("RELIGION_PLAN_READY|"..r.token.."|"..nextPhase)
elseif ok and r.kind=="choose_pantheon" then print("RELIGION_PLAN_CONFIRMED|"..r.token.."|"..r.kind.."|"..r.beliefType.."|"..status)
elseif ok then print("RELIGION_PLAN_CONFIRMED|"..r.token.."|"..r.kind.."|"..r.religionType.."|"..r.followerType.."|"..r.founderType.."|"..status)
else print("RELIGION_PLAN_OBSERVED|{token}|"..tostring(status)) end
print("{SENTINEL}")
'''


def build_religion_continuation(token: str) -> str:
    """Only an explicit matching request may enter its next unstarted belief phase."""
    return _OBSERVE + f'''
local r=__civmcpReligionPlan
local ok,status,nextPhase=pcall(observe,r,{json.dumps(token)})
if ok and status=="READY" then
 local phase=r.phases[nextPhase]
 phase.entered=true
 UI.RequestPlayerOperation(r.owner,phase.op,phase.params)
 phase.returned=true
 print("OK:RELIGION_PLAN_CONTINUED|"..r.token.."|"..nextPhase)
else print("RELIGION_PLAN_OBSERVED|{token}|"..tostring(status)) end
print("{SENTINEL}")
'''


def religion_plan_ready(lines: list[str], token: str) -> bool:
    rows = [line for line in lines if line.startswith("RELIGION_PLAN_READY|")]
    return len(rows) == 1 and rows[0] in {f"RELIGION_PLAN_READY|{token}|2", f"RELIGION_PLAN_READY|{token}|3"}


def parse_religion_confirmation(lines: list[str], kind: str, token: str) -> str | None:
    rows = [line.split("|") for line in lines if line.startswith("RELIGION_PLAN_CONFIRMED|")]
    if len(rows) != 1 or rows[0][1:3] != [token, kind]:
        return None
    row = rows[0]
    if row[-1] not in {"CONFIRMED", "UNCHANGED"}:
        return None
    suffix = "UNCHANGED" if row[-1] == "UNCHANGED" else "FOUNDED"
    if kind == "choose_pantheon" and len(row) == 5:
        return f"PANTHEON_{suffix}|{row[3]}"
    if kind == "found_religion" and len(row) == 7:
        return f"RELIGION_{suffix}|{row[3]}|{row[4]}|{row[5]}"
    return None

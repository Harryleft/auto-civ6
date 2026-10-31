"""Native player selections: request receipts and domain-specific completion."""
import json

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.government_conditions import LUA_GOVERNMENT_CONDITIONS


_CONTEXT = '''
local function integer(value)
 assert(type(value)=="number" and value==value and math.abs(value)<math.huge and value==math.floor(value),"ELIGIBILITY_UNKNOWN")
 return value
end
local function readBool(fn)
 local ok,value=pcall(fn);assert(ok and type(value)=="boolean","ELIGIBILITY_UNKNOWN");return value
end
local function readInt(fn)
 local ok,value=pcall(fn);assert(ok,"ELIGIBILITY_UNKNOWN");return integer(value)
end
local function eraIndex(value)
 integer(value);assert(value>=0 and GameInfo.Eras[value]~=nil,"ERA_UNAVAILABLE");return value
end
local function includes(values,target)
 assert(type(values)=="table","CHOICES_UNAVAILABLE")
 for _,value in ipairs(values) do if integer(value)==target then return true end end
 return false
end
'''


def _research(operation: str) -> tuple[str, str]:
    tech = operation == 'set_research'
    table, getter, has, current, can, op, param = (
        ('Technologies', 'GetTechs', 'HasTech', 'GetResearchingTech', 'CanResearch', 'RESEARCH', 'PARAM_TECH_TYPE')
        if tech else ('Civics', 'GetCulture', 'HasCivic', 'GetProgressingCivic', 'CanProgress', 'PROGRESS_CIVIC', 'PARAM_CIVIC_TYPE')
    )
    prepare = f'''
 local row=GameInfo.{table}[r.target];assert(row,"TARGET_NOT_FOUND")
 r.index=integer(row.Index);r.hash=integer(row.Hash)
 local domain=Players[r.owner]:{getter}()
 assert(not readBool(function() return domain:{has}(r.index) end),"ALREADY_COMPLETED")
 local current=readInt(function() return domain:{current}() end);assert(current>=-1,"ELIGIBILITY_UNKNOWN")
 r.noop=current==r.index
 if not r.noop then
  assert(readBool(function() return domain:{can}(r.index) end),"NOT_ELIGIBLE")
  r.op=PlayerOperations.{op}
  assert(r.op~=nil and PlayerOperations.VALUE_EXCLUSIVE~=nil and type(UI.RequestPlayerOperation)=="function","OPERATION_UNAVAILABLE")
  r.params={{[PlayerOperations.{param}]=r.hash,[PlayerOperations.PARAM_INSERT_MODE]=PlayerOperations.VALUE_EXCLUSIVE}}
 end
'''
    confirm = f'''
 assert(Players[r.owner]:{getter}():{current}()==r.index,"TARGET_UNCONFIRMED")
 return r.noop and "ALREADY_SELECTED" or "{'RESEARCHING' if tech else 'PROGRESSING'}",r.target
'''
    return prepare, confirm


def _government() -> tuple[str, str]:
    return LUA_GOVERNMENT_CONDITIONS + '''
 local culture=Players[r.owner]:GetCulture()
 local row=GameInfo.Governments[r.target];assert(row,"GOVERNMENT_NOT_FOUND")
 r.index=integer(row.Index);r.hash=integer(row.Hash)
 local current=readInt(function() return culture:GetCurrentGovernment() end)
 r.noop=current==r.index
 assert(type(culture.SetGovernmentChangeConsidered)=="function"
  and type(culture.GovernmentChangeConsidered)=="function","GOVERNMENT_CONSIDERATION_UNAVAILABLE")
 readBool(function() return culture:GovernmentChangeConsidered() end)
 -- Keeping the current government still writes its native acknowledgment.
 assert(governmentTurnAllowed(readBool(function() return Players[r.owner]:IsTurnActive() end),
  function() return readBool(function() return Game.IsAllowStrategicCommands(r.owner) end) end),"CANNOT_CHANGE_GOVERNMENT")
 if not r.noop then
  assert(readBool(function() return culture:IsGovernmentUnlocked(r.hash) end),"GOVERNMENT_LOCKED")
  assert(governmentNewChangeAllowed(readBool(function() return culture:CanChangeGovernmentAtAll() end),
   function() return readBool(function() return culture:GovernmentChangeMade() end) end,
   function() return governmentFreeChangeWindow(current,
    function() return readBool(function() return culture:CivicCompletedThisTurn() end) end) end),"CANNOT_CHANGE_GOVERNMENT")
  assert(not readBool(function() return culture:IsInAnarchy() end),"ANARCHY_ALREADY_ACTIVE")
  r.anarchyTurns=readInt(function() return culture:GetAnarchyTurns(r.index) end)
  assert(r.anarchyTurns>=0,"ANARCHY_DURATION_UNAVAILABLE")
  r.turn=Game.GetCurrentGameTurn()
  if r.anarchyTurns>0 then r.previousAnarchyEnd=readInt(function() return culture:GetAnarchyEndTurn() end) end
  assert(type(culture.RequestChangeGovernment)=="function","OPERATION_UNAVAILABLE")
 end
''', '''
 local culture=Players[r.owner]:GetCulture()
 local function confirmConsideration(fact)
  assert(r.considerationEntered==true and r.considerationReturned==true,fact.."_CONSIDERATION_NOT_RETURNED")
  local ok,considered=pcall(function() return culture:GovernmentChangeConsidered() end)
  assert(ok and considered==true,fact.."_CONSIDERATION_UNCONFIRMED")
 end
 if culture:GetCurrentGovernment()==r.index and culture:IsInAnarchy()==false then
  local label=r.noop and "GOVERNMENT_UNCHANGED" or "GOVERNMENT_CHANGED"
  confirmConsideration(label)
  return label,r.target
 end
 -- The native UI treats true as accepted, not as final adoption. Bind that
 -- receipt to the newly started interval before releasing this command.
 if not r.noop and r.accepted==true and r.anarchyTurns>0 and culture:IsInAnarchy()==true
  and culture:GovernmentChangeMade()==true then
  local ending=integer(culture:GetAnarchyEndTurn())
  assert(ending==r.turn+r.anarchyTurns and ending>r.turn and ending~=r.previousAnarchyEnd,"ANARCHY_INTERVAL_UNCONFIRMED")
  confirmConsideration("GOVERNMENT_CHANGE_STARTED")
  return "GOVERNMENT_CHANGE_STARTED","target:"..r.target..",anarchy_until:"..ending..",remaining:"..r.anarchyTurns
 end
 error("GOVERNMENT_UNCONFIRMED")
'''


def _parts(operation: str) -> tuple[str, str]:
    if operation == 'change_government':
        return _government()
    if operation == 'choose_dedication':
        return '''
 local eras=Game.GetEras()
 r.index=integer(r.target)
 local row=GameInfo.CommemorationTypes[r.index];assert(row and row.Index==r.index,"DEDICATION_NOT_FOUND")
 r.name=row.CommemorationType
 r.era=eraIndex(eras:GetCurrentEra())
 r.noop=includes(eras:GetPlayerActiveCommemorations(r.owner),r.index)
 if not r.noop then
  assert(integer(eras:GetPlayerNumAllowedCommemorations(r.owner))>0,"NO_DEDICATION_NEEDED")
  assert(includes(eras:GetPlayerCommemorateChoices(r.owner),r.index),"DEDICATION_NOT_AVAILABLE")
  r.op=PlayerOperations.COMMEMORATE
  assert(r.op~=nil and type(UI.RequestPlayerOperation)=="function","OPERATION_UNAVAILABLE")
  r.params={[PlayerOperations.PARAM_COMMEMORATION_TYPE]=r.index}
 end
''', '''
 local eras=Game.GetEras()
 local era=eraIndex(eras:GetCurrentEra())
 if era~=r.era then r.invalid=true end
 assert(not r.invalid,"ERA_CHANGED")
 assert(includes(eras:GetPlayerActiveCommemorations(r.owner),r.index),"DEDICATION_UNCONFIRMED")
 return r.noop and "DEDICATION_UNCHANGED" or "DEDICATION_CHOSEN",r.name
'''
    return _research(operation)


def build_player_choice(operation: str, target: str | int, token: str) -> str:
    prepare, _ = _parts(operation)
    submit = ('r.accepted=Players[r.owner]:GetCulture():RequestChangeGovernment(r.hash)'
              if operation == 'change_government' else 'UI.RequestPlayerOperation(r.owner,r.op,r.params)')
    acknowledge = ('r.considerationEntered=true; Players[r.owner]:GetCulture():SetGovernmentChangeConsidered(true); r.considerationReturned=true'
                   if operation == 'change_government' else '')
    return _CONTEXT + f'''
local token={json.dumps(token)}
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local r={{token=token,operation={json.dumps(operation)},target={json.dumps(target)},owner=Game.GetLocalPlayer()}}
 {prepare}
 return r
end)
if not ok then print("ERR:CHOICE_REFUSED|"..tostring(r));print("{SENTINEL}");return end
if not (__civmcpPlayerChoice and __civmcpPlayerChoice.token==token) then
 __civmcpPlayerChoice=r
 if not r.noop then
  r.entered=true
  {submit}
  r.returned=true
 end
 {acknowledge}
end
print("OK:CHOICE_REQUESTED|"..r.operation.."|"..tostring(r.target))
print("{SENTINEL}")
'''


def build_player_choice_confirmation(operation: str, target: str | int, token: str) -> str:
    _, confirm = _parts(operation)
    return _CONTEXT + f'''
local r=__civmcpPlayerChoice
local ok,label,detail=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.operation=={json.dumps(operation)}
  and r.target=={json.dumps(target)},"RECEIPT_MISSING_OR_MISMATCHED")
 assert(r.noop or r.entered==true and r.returned==true,"REQUEST_NOT_RETURNED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 {confirm}
end)
if ok then print("CHOICE_CONFIRMED|"..r.token.."|"..r.operation.."|"..tostring(r.target).."|"..label.."|"..detail)
else print("CHOICE_OBSERVED|{token}|"..tostring(label)) end
print("{SENTINEL}")
'''


def parse_player_choice_confirmation(lines: list[str], operation: str, target: str | int, token: str) -> str | None:
    rows = [row.split('|') for row in lines if row.startswith('CHOICE_CONFIRMED|')]
    if len(rows) != 1 or len(rows[0]) != 6:
        return None
    row = rows[0]
    if row[1:4] != [token, operation, str(target)]:
        return None
    if row[4] not in {'ALREADY_SELECTED', 'RESEARCHING', 'PROGRESSING', 'GOVERNMENT_UNCHANGED', 'GOVERNMENT_CHANGED', 'GOVERNMENT_CHANGE_STARTED', 'DEDICATION_UNCHANGED', 'DEDICATION_CHOSEN'}:
        return None
    return f'{row[4]}|{row[5]}'

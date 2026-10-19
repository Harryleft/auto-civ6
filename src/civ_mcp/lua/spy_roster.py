"""Native own-spy identities shared by assignment evidence and the spy query."""

# EspionageOverview.RefreshOperatives/GetNthOffMapSpy expose no unit ID.
# Preserve raw names (not translated display names) and do not invent one.
SPY_ROSTER = '''
local function spyInteger(value,minimum)
 assert(type(value)=="number" and value<math.huge and value==math.floor(value)
  and value> -math.huge and value>=minimum,"SPY_ROSTER_NUMBER_UNAVAILABLE")
 return value
end
local function spyName(value)
 assert(type(value)=="string" and #value>0 and not value:find("[|\\r\\n]"),"SPY_NAME_UNAVAILABLE")
 return value
end
local function spyIdentity(unit)
 local name=spyName(unit:GetName())
 local level=spyInteger(unit:GetExperience():GetLevel(),1)
 return {name=name,level=level,unitType=unit:GetType()}
end
local function mapSpyRoster(owner)
 local roster={}
 for _,unit in Players[owner]:GetUnits():Members() do
  local info=GameInfo.Units[unit:GetType()]
  assert(info,"SPY_ROSTER_TYPE_UNAVAILABLE")
  if info.UnitType=="UNIT_SPY" then
   local id=spyInteger(unit:GetID(),0)
   assert(unit:GetOwner()==owner and roster[id]==nil,"SPY_ROSTER_ID_UNAVAILABLE")
   roster[id]=spyIdentity(unit)
  end
 end
 return roster
end
local function offMapSpyRoster(owner,observeRow,observeKeys)
 local diplomacy=Players[owner]:GetDiplomacy()
 assert(type(diplomacy.GetNumSpiesOffMap)=="function" and type(diplomacy.GetNthOffMapSpy)=="function",
  "SPY_OFFMAP_API_UNAVAILABLE")
 local count=spyInteger(diplomacy:GetNumSpiesOffMap(),0)
 local rows={}
 local complete=true
 for index=0,count-1 do
  local ok=pcall(function()
   local row=diplomacy:GetNthOffMapSpy(owner,index)
   assert(type(row)=="table","SPY_OFFMAP_ROW_UNAVAILABLE")
   local key=spyInteger(row.NameIndex,0)
   -- Observe every identifiable bound row even if another row is unreadable.
   if observeRow then observeRow(key,row) end
   assert(rows[key]==nil,"SPY_OFFMAP_DUPLICATE_IDENTITY")
   rows[key]=row
  end)
  if not ok then complete=false end
 end
 -- Missing keys are evidence only when every native row/key was readable.
 if complete and observeKeys then observeKeys(rows) end
 assert(complete,"SPY_OFFMAP_ROSTER_UNKNOWN")
 local roster={}
 for key,row in pairs(rows) do
  roster[key]={name=spyName(row.Name),level=spyInteger(row.Level,1),
   x=spyInteger(row.XLocation,-9999),y=spyInteger(row.YLocation,-9999),
   finish=spyInteger(row.ReturnTurn,-1)}
 end
 return roster,count
end
local function sameSpyIdentity(a,b)
 return a and b and a.name==b.name and a.level==b.level
end
local function sameOffMapSpy(a,b)
 return sameSpyIdentity(a,b) and a.x==b.x and a.y==b.y and a.finish==b.finish
end
'''

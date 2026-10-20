"""Read-only religious defense observations; no action or strategic safety policy."""
from __future__ import annotations

from dataclasses import dataclass, field

from civ_mcp.lua._helpers import SENTINEL


_READ = r'''
local function read(fn) local ok,v=pcall(fn);if ok then return v end;return nil end
local function number(v) if type(v)=='number' and v==v and v>=0 and v<math.huge then return v end;return nil end
local function integer(v) if number(v) and v%1==0 then return v end;return nil end
local function religionID(v) if v==-1 or integer(v) then return v end;return nil end
local function boolean(v) if type(v)=='boolean' then return v end;return nil end
local function wire(v)
 if v==nil then return 'unknown' end
 return tostring(v):gsub('|','/'):gsub('%c',' ')
end
local function emit(tag,...) local a={tag};for i=1,select('#',...) do a[#a+1]=wire(select(i,...)) end;print(table.concat(a,'|')) end
local function scope(label)
 local owner=integer(read(function() return Game.GetLocalPlayer() end))
 if owner and owner>63 then owner=nil end
 local civ=read(function() return PlayerConfigurations[owner]:GetCivilizationTypeName() end)
 if type(civ)~='string' or not civ:match('^CIVILIZATION_[A-Z0-9_]+$') then civ=nil end
 local seed=read(function() return GameConfiguration.GetValue('GAME_SYNC_RANDOM_SEED') end)
 if type(seed)~='number' or seed~=seed or math.abs(seed)==math.huge or seed%1~=0 then seed=nil end
 emit('RD_SCOPE',label,owner,civ,seed,integer(read(function() return Game.GetCurrentGameTurn() end)))
 return owner
end
local function playerFacts(label,owner)
 local p=read(function() return Players[owner] end)
 local r=read(function() return p:GetReligion() end)
 emit('RD_PLAYER',label,religionID(read(function() return r:GetReligionTypeCreated() end)),
  number(read(function() return r:GetFaithBalance() end)),boolean(read(function() return r:HasLaunchedInquisition() end)),
  religionID(read(function() return r:GetReligionInMajorityOfCities() end)),
  integer(read(function() return p:GetStats():GetNumCitiesFollowingReligion() end)))
 return p
end
'''


def build_query() -> str:
    """InGame: own cities, own/visible foreign religious units, current commands."""
    return _READ + r'''
local owner=scope('InGame:start')
local p=playerFacts('InGame',owner)
local cityScan=pcall(function()
 for _,c in p:GetCities():Members() do
  local id=integer(read(function() return c:GetID() end))
  local co=integer(read(function() return c:GetOwner() end))
  -- A collection mismatch is evidence of a partial scan, never a foreign-city disclosure.
  if co~=owner or id==nil then error('OWN_CITY_IDENTITY_UNKNOWN') end
  local cr=read(function() return c:GetReligion() end)
  emit('RD_CITY',id,co,read(function() return Locale.Lookup(c:GetName()) end),
   integer(read(function() return c:GetX() end)),integer(read(function() return c:GetY() end)),
   integer(read(function() return c:GetPopulation() end)),religionID(read(function() return cr:GetMajorityReligion() end)))
  local rows=read(function() return cr:GetReligionsInCity() end)
  local complete=type(rows)=='table';local seen={}
  if complete then
   for _,r in pairs(rows) do
    if type(r)~='table' or religionID(r.Religion)==nil or seen[r.Religion] then complete=false
    else
     seen[r.Religion]=true
     local followers=integer(r.Followers);local stock=number(r.Pressure)
     local flow=number(read(function() return cr:GetTotalPressureOnCity(r.Religion) end))
     if followers==nil or stock==nil then complete=false end
     emit('RD_PRESSURE',id,r.Religion,followers,stock,flow)
    end
   end
  end
  emit('RD_CITY_PRESSURE',id,complete and 'complete' or 'partial')
 end
end)
emit('RD_CITY_SCAN',cityScan and 'complete' or 'partial')
local own,foreign={},{};local unitScan=true
for pid=0,63 do
 local player=read(function() return Players[pid] end)
 if player then
  local ok=pcall(function()
   for _,u in player:GetUnits():Members() do
    local x=integer(read(function() return u:GetX() end));local y=integer(read(function() return u:GetY() end))
    local visible=true
    if pid~=owner then
     local plotVisible=boolean(read(function() return PlayersVisibility[owner]:IsVisible(x,y) end))
     visible=plotVisible
     if plotVisible==true then visible=boolean(read(function() return PlayersVisibility[owner]:IsUnitVisible(u) end)) end
    end
    if visible==nil then unitScan=false end
    if visible==true then
     local id=integer(read(function() return u:GetID() end));local actual=integer(read(function() return u:GetOwner() end))
     local typ=integer(read(function() return u:GetType() end))
     local entry=read(function() return GameInfo.Units[typ] end)
     local tracks=boolean(read(function() return entry.TrackReligion end))
     if id==nil or actual~=pid or typ==nil or x==nil or y==nil or tracks==nil then unitScan=false
     else
      if pid==owner then own[#own+1]={unit=u,id=id,x=x,y=y} end
      if tracks then
       local rel=religionID(read(function() return u:GetReligionType() end))
       local charges=integer(read(function() return u:GetSpreadCharges() end))
       local moves=number(read(function() return u:GetMovesRemaining() end))
       local ready=boolean(read(function() return u:IsReadyToMove() end))
       local strength=number(read(function() return entry.ReligiousStrength end))
       local war=false
       if pid~=owner then war=boolean(read(function() return p:GetDiplomacy():IsAtWarWith(pid) end)) end
       emit('RD_UNIT',id,pid,typ,read(function() return entry.UnitType end),x,y,rel,charges,moves,ready,strength,war)
       if pid~=owner then foreign[#foreign+1]={id=id,owner=pid,x=x,y=y} end
      end
     end
    end
   end
  end)
  if not ok then unitScan=false end
 end
end
emit('RD_UNIT_SCAN',unitScan and 'complete' or 'partial')
local command=read(function() return GameInfo.UnitCommands['UNITCOMMAND_CONDEMN_HERETIC'] end)
for _,a in ipairs(own) do
 local targets={}
 for _,t in ipairs(foreign) do if a.x==t.x and a.y==t.y then targets[#targets+1]=t end end
 if #targets>0 then
  local ok,can,results=pcall(function()
   assert(command.CommandType=='UNITCOMMAND_CONDEMN_HERETIC' and type(command.Hash)=='number')
   return UnitManager.CanStartCommand(a.unit,command.Hash,false,true)
  end)
  local eligible=nil;if ok then eligible=boolean(can) end
  local reasons=read(function() return results[UnitOperationResults.FAILURE_REASONS] end)
  if type(reasons)=='table' then
   for _,reason in ipairs(reasons) do
    if type(reason)=='string' then emit('RD_CONDEMN_REASON',a.id,reason) end
   end
  end
  -- The command has no explicit target parameter: only a unique observed religious
  -- target can bind its qualification. A partial scan cannot prove uniqueness.
  local target=(#targets==1 and unitScan) and targets[1].id or nil
  emit('RD_CONDEMN',a.id,target,eligible,#targets==1 and unitScan and 'unique_visible_target' or 'ambiguous_or_partial',
   'unknown','unknown')
 end
end
-- The current active-effects API differs from historical GetReview. Preserve
-- raw localized keys; these fields do not prove numeric target/effect identity.
local congress=read(function() return Game.GetWorldCongress() end)
local resolutions=read(function() return congress:GetResolutions(owner) end)
local meeting=read(function() return congress:GetMeetingStatus() end)
local turns=integer(read(function() return meeting.TurnsLeft end))
local remaining=nil;if turns~=nil then remaining=turns+1 end
local resolutionScan=type(resolutions)=='table';local found=false
if resolutionScan then
 for key,row in pairs(resolutions) do
  if type(row)=='table' then
   local typ=read(function() return GameInfo.Resolutions[row.Type].ResolutionType end)
   if type(typ)~='string' then resolutionScan=false
   elseif typ=='WC_RES_WORLD_RELIGION' then
    found=true
    emit('RD_CONGRESS_ROW',row.Type,row.ChosenOption,row.ChosenThing,row.ChosenLabel)
   end
  elseif key~='Stage' then resolutionScan=false end
 end
end
local active='unknown';if found then active='present' elseif resolutionScan then active='absent' end
emit('RD_CONGRESS',active,resolutionScan and 'complete' or 'partial',turns,remaining)
scope('InGame:end')
''' + f'\nprint("{SENTINEL}")\n'


def build_core_query() -> str:
    return _READ + "\nlocal owner=scope('GameCore:start');playerFacts('GameCore',owner);scope('GameCore:end')\n" + f'print("{SENTINEL}")\n'


def build_final_scope_query() -> str:
    return _READ + "\nscope('InGame:final')\n" + f'print("{SENTINEL}")\n'


@dataclass
class ReligionDefense:
    """Original context-tagged facts, including incomplete observations."""
    records: list[list[str]] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    def add(self, rows: list[str]) -> None:
        self.records.extend(row.split('|') for row in rows if row.startswith('RD_'))


def narrate(report: ReligionDefense) -> str:
    def records(tag: str):
        return [r[1:] for r in report.records if r[0] == tag]

    scopes = records('RD_SCOPE')
    conflicts: list[str] = []
    unknown = bool(report.unavailable)
    labels = {r[0] for r in scopes if len(r) == 5}
    required = {'InGame:start', 'InGame:end', 'GameCore:start', 'GameCore:end', 'InGame:final'}
    if labels != required or len(scopes) != 5:
        unknown = True
    for column, name in enumerate(('owner', 'civilization', 'seed', 'turn'), 1):
        values = {r[column] for r in scopes if len(r) == 5 and r[column] != 'unknown'}
        if len(values) > 1:
            conflicts.append(f'scope {name} mismatch')
        if any(len(r) != 5 or r[column] == 'unknown' for r in scopes):
            unknown = True
    status = 'conflicting' if conflicts else 'partial' if unknown else 'consistent'
    lines = [f'Religion defense observation: context_scope={status}']
    for scope in scopes:
        if len(scope) == 5:
            lines.append(f'  {scope[0]} owner={scope[1]} civilization={scope[2]} seed={scope[3]} turn={scope[4]}')
    players = {r[0]: r[1:] for r in records('RD_PLAYER') if len(r) == 6}
    ig, core = players.get('InGame', ['unknown'] * 5), players.get('GameCore', ['unknown'] * 5)
    for idx, label in enumerate(('founded_religion', 'faith', 'inquisition', 'native_majority', 'native_religion_city_stat')):
        a, b = ig[idx], core[idx]
        flag = ' conflict' if a != 'unknown' and b != 'unknown' and a != b else ''
        if flag:
            conflicts.append(f'{label} context disagreement')
        lines.append(f'{label}: InGame={a} GameCore={b}{flag}')
    lines.append(f'native_religion_city_stat={ig[4]} (engine statistic; not owned-city count)')
    cities = records('RD_CITY')
    counts: dict[str, int] = {}
    city_complete = records('RD_CITY_SCAN') == [['complete']]
    majority_complete = city_complete
    for c in cities:
        if len(c) != 7:
            majority_complete = False
            continue
        if c[6] == 'unknown':
            majority_complete = False
        else:
            counts[c[6]] = counts.get(c[6], 0) + 1
        lines.append(f'city={c[0]} owner={c[1]} name={c[2]} plot={c[3]},{c[4]} population={c[5]} majority={c[6]}')
    total = len(cities) if city_complete else 'unknown'
    lines.append(f'owned_cities_scan={"complete" if city_complete else "partial"}; owned_cities={total}')
    for religion, count in sorted(counts.items()):
        lines.append(f'owned_city_majorities: religion={religion} count={count} / owned_cities={total}' + ('' if majority_complete else ' (partial)'))
    if majority_complete:
        # A strict majority proves a contradiction with a different native value.
        # No strict majority does NOT prove the native no-religion sentinel: the
        # native boundary/tie rule is not established by these count observations.
        majority = next((r for r, n in counts.items() if r != '-1' and n > len(cities) / 2), None)
        if majority is None:
            lines.append('owned_city_majority_consistency=unknown (native boundary rule not inferred)')
        elif ig[3] != 'unknown' and ig[3] != majority:
            conflicts.append(f'owned-city majority={majority} disagrees with native_majority={ig[3]}')
    for p in records('RD_PRESSURE'):
        if len(p) == 5:
            lines.append(f'  city={p[0]} religion={p[1]} followers={p[2]} accumulated_pressure={p[3]} incoming_pressure={p[4]}')
    pressure_complete = {r[0]: r[1] == 'complete' for r in records('RD_CITY_PRESSURE') if len(r) == 2}
    for city in cities:
        if len(city) != 7 or not pressure_complete.get(city[0]) or not city[5].isdigit() or city[6] == 'unknown':
            continue
        stocks = [r for r in records('RD_PRESSURE') if len(r) == 5 and r[0] == city[0]]
        if any(not r[2].isdigit() for r in stocks):
            continue
        follower_majority = next((r[1] for r in stocks if r[1] != '-1' and int(r[2]) > int(city[5]) / 2), None)
        if follower_majority is None:
            # Native T131 Eridu reported religion 12 with 6/12 followers. Do not
            # replace that observation with an invented strict >50% necessity.
            lines.append(f'city={city[0]} follower_majority_consistency=unknown (native boundary rule not inferred)')
        elif follower_majority != city[6]:
            conflicts.append(f'city={city[0]} native majority={city[6]} disagrees with follower majority={follower_majority}')
    for c in records('RD_CITY_PRESSURE'):
        if len(c) == 2:
            lines.append(f'  city={c[0]} pressure_records={c[1]}')
    unit_scan = records('RD_UNIT_SCAN') == [['complete']]
    lines.append(f'visible_religious_units_scan={"complete" if unit_scan else "partial"}; hidden units not inspected')
    for u in records('RD_UNIT'):
        if len(u) == 12:
            lines.append(f'unit={u[0]} owner={u[1]} type={u[3]} type_id={u[2]} plot={u[4]},{u[5]} religion={u[6]} charges={u[7]} moves={u[8]} ready={u[9]} base_religious_strength={u[10]} at_war={u[11]}')
    for c in records('RD_CONDEMN'):
        if len(c) == 6:
            lines.append(f'condemn: actor={c[0]} target={c[1]} native_can_condemn={c[2]} binding={c[3]} permission_source={c[4]} expiry={c[5]}')
    for row in records('RD_CONDEMN_REASON'):
        if len(row) == 2:
            lines.append(f'condemn: actor={row[0]} failure_reason={row[1]} (raw native reason)')
    if not records('RD_CONDEMN'):
        lines.append('condemn: no bound visible same-plot candidate observed; other targets/permissions unknown')
    for row in records('RD_CONGRESS'):
        if len(row) == 4:
            lines.append(f'active_world_religion={row[0]} active_resolution_scan={row[1]} meeting_turns_left_raw={row[2]} ui_turns_remaining={row[3]}; target_religion=unknown effect=unknown')
    for row in records('RD_CONGRESS_ROW'):
        if len(row) == 4:
            lines.append(f'  current_resolution_type={row[0]} ChosenOption={row[1]} ChosenThing={row[2]} ChosenLabel={row[3]} (raw native fields)')
    lines.extend(f'unavailable: {context}' for context in report.unavailable)
    lines.extend(f'conflict: {item}' for item in conflicts)
    lines.append('Observation only; no defense outcome or remaining safe turns inferred.')
    return '\n'.join(lines)

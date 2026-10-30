"""Native built-district observations and the current Rationalism rule graph.

CitySupport reads district:GetAdjacencyYield, not a placement formula. The plot
getter has different modifier semantics; retain both instead of substituting.
"""
from urllib.parse import unquote

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.notifications import LUA_TURN_WORLD


def build_engine_query(city_id=None):
    """GameCore's official Modifiers tuner reader, restricted to our policy."""
    return LUA_TURN_WORLD + r'''
local before=turnWorld()
local me=Game.GetLocalPlayer()
local wantedCity=CITY_FILTER
local cities={}
for _,c in Players[me]:GetCities():Members() do
 if c:GetOwner()==me then cities[c:GetID()]=c end
end
local function wire(v)
 if v==nil then return '~' end
 if type(v)=='number' then return string.format('%.17g',v) end
 return tostring(v):gsub('%%','%%25'):gsub('|','%%7C'):gsub('\r','%%0D'):gsub('\n','%%0A')
end
local function emit(tag,...)
 local row={tag};for i=1,select('#',...) do row[#row+1]=wire(select(i,...)) end;print(table.concat(row,'|'))
end
local wanted={}
for row in GameInfo.PolicyModifiers() do if row.PolicyType=='POLICY_RATIONALISM' then wanted[row.ModifierId]=true end end
local ok,err=pcall(function()
 local modifiers=GameEffects.GetModifiers();assert(type(modifiers)=='table','MODIFIER_SCAN_UNKNOWN')
 for _,inst in ipairs(modifiers) do
  local def=GameEffects.GetModifierDefinition(inst)
  if def and wanted[def.Id] then
   local owner=GameEffects.GetModifierOwner(inst)
   if GameEffects.GetObjectsPlayerId(owner)==me then
    local subjectCount=GameEffects.GetModifierSubjectCount(inst)
    local trackedCount=GameEffects.GetModifierTrackedObjectCount(inst)
    emit('CY_ENGINE_MOD',inst,def.Id,GameEffects.GetModifierActive(inst),subjectCount,trackedCount)
    local subjects=GameEffects.GetModifierSubjects(inst) or {};local tracked=GameEffects.GetModifierTrackedObjects(inst) or {}
    assert(type(subjects)=='table' and type(tracked)=='table' and #subjects==subjectCount and #tracked==trackedCount,'SUBJECT_SCAN_UNKNOWN')
    local seenTracked={};for _,obj in ipairs(tracked)do assert(not seenTracked[obj],'DUPLICATE_TRACKED_OBJECT');seenTracked[obj]=true end
    local met={};for _,obj in ipairs(subjects)do met[obj]=true end
    for _,obj in ipairs(subjects)do assert(seenTracked[obj],'SUBJECT_OUTSIDE_TRACKED_SCOPE')end
    for _,obj in ipairs(tracked) do
     if GameEffects.GetObjectsPlayerId(obj)==me then
      local typ=GameEffects.GetObjectType(obj);local name=GameEffects.GetObjectName(obj);local debug=GameEffects.GetObjectString(obj)
      -- This is the engine's component diagnostic, not a localized city label.
      -- Bind only its exact supported format, owner, live city ID and raw name;
      -- format changes stay unbound rather than falling back to name matching.
      local cid,co,rawName=tostring(debug):match('^City %((%d+)%), Owner: (%d+), Name: (.*)$')
      cid=tonumber(cid);co=tonumber(co)
      local c=cid and cities[cid]
      local bound=typ=='LOC_MODIFIER_OBJECT_CITY' and co==me and c and c:GetName()==name and rawName==name
      if not bound then cid=nil;co=nil end
      if wantedCity==nil or cid==wantedCity then
      emit('CY_ENGINE_OBJECT',inst,obj,typ,name,debug,met[obj]==true,cid,co)
      local set=GameEffects.GetModifierTrackedOjectRequirementSet(inst,obj)
      if set then
       emit('CY_ENGINE_SET',inst,obj,set,GameEffects.GetRequirementSetState(set),GameEffects.GetRequirementSetDefinition(set).Id)
       for _,req in ipairs(GameEffects.GetRequirementSetInnerRequirements(set)) do
        local r=GameEffects.GetRequirementDefinition(req)
        emit('CY_ENGINE_REQUIREMENT',inst,obj,req,r.Id,GameEffects.GetRequirementState(req))
       end
      end
      end
     end
    end
   end
  end
 end
end)
emit('CY_ENGINE_SCAN',ok,not ok and err or nil)
printTurnWorld(before)
print('SENTINEL')
'''.replace('CITY_FILTER', 'nil' if city_id is None else str(city_id)).replace("'SENTINEL'", repr(SENTINEL))


def build_query(city_id=None, *, evidence=False):
    if city_id is not None and (type(city_id) is not int or city_id < 0):
        raise ValueError("Expected an owned native city ID")
    return LUA_TURN_WORLD + r'''
local before=turnWorld()
local wanted=CITY_ID
local audit=AUDIT
local me=Game.GetLocalPlayer()
local function read(fn) local ok,v=pcall(fn);if ok then return v end end
local function finite(v) if type(v)=='number' and v==v and math.abs(v)<math.huge then return v end;return nil end
local function boolean(v) if type(v)=='boolean' then return v end end
local function wire(v)
 if v==nil then return '~' end
 if type(v)=='number' then return string.format('%.17g',v) end
 return tostring(v):gsub('%%','%%25'):gsub('|','%%7C'):gsub('\r','%%0D'):gsub('\n','%%0A')
end
local function emit(tag,...)
 local row={tag};for i=1,select('#',...) do row[#row+1]=wire(select(i,...)) end
 print(table.concat(row,'|'))
end
local owner=Players[me]
local found,count=false,0
local scan,scanError=pcall(function()
 for _,city in owner:GetCities():Members() do
  local id=city:GetID()
  assert(city:GetOwner()==me,'OWN_CITY_SCOPE_MISMATCH')
  if wanted==nil or id==wanted then
   found=true;count=count+1
   emit('CY_CITY',id,me,Locale.Lookup(city:GetName()),finite(read(function()return city:GetPopulation()end)),
    finite(read(function()return city:GetYield(GameInfo.Yields.YIELD_SCIENCE.Index)end)),
    finite(read(function()return city:GetGrowth():GetFoodSurplus()end)))
   for _,d in city:GetDistricts():Members() do
    local did=d:GetID();local info=GameInfo.Districts[d:GetType()]
    assert(info and type(did)=='number','DISTRICT_IDENTITY_UNKNOWN')
    emit('CY_DISTRICT',id,did,info.DistrictType,d:GetX(),d:GetY(),
     boolean(read(function()return d:IsComplete()end)),boolean(read(function()return d:IsPillaged()end)))
    for _,name in ipairs({'FOOD','PRODUCTION','GOLD','SCIENCE','CULTURE','FAITH'}) do
     local key='YIELD_'..name
     local idx=read(function()return GameInfo.Yields[key].Index end)
     emit('CY_YIELD',id,did,key,
      finite(read(function()return d:GetAdjacencyYield(idx)end)),
      finite(read(function()return d:GetYield(idx)end)),
      finite(read(function()return Map.GetPlot(d:GetX(),d:GetY()):GetAdjacencyYield(me,id,info.Index,idx)end)))
    end
   end
   local b=city:GetBuildings()
   for row in GameInfo.Buildings() do
    if b:HasBuilding(row.Index) then
     emit('CY_BUILDING',id,row.BuildingType,row.PrereqDistrict,boolean(read(function()return b:IsPillaged(row.Index)end)))
     for _,name in ipairs({'FOOD','PRODUCTION','GOLD','SCIENCE','CULTURE','FAITH'}) do
      local key='YIELD_'..name
      emit('CY_BUILDING_YIELD',id,row.BuildingType,key,finite(read(function()return city:GetBuildingYield(row.Index,key)end)))
     end
    end
   end
   if audit then emit('CY_TOOLTIP',id,read(function()return city:GetYieldToolTip(GameInfo.Yields.YIELD_SCIENCE.Index)end)) end
  end
 end
end)
emit('CY_SCAN',scan,count,wanted==nil or found)
if not scan then emit('CY_ERROR','cities',scanError) end
local policy=read(function()return GameInfo.Policies.POLICY_RATIONALISM end)
local culture=read(function()return owner:GetCulture()end)
local slotsOK,slotted,canSlot=true,false,false
local slotScan=pcall(function()
 assert(policy,'POLICY_UNAVAILABLE')
 for slot=0,culture:GetNumPolicySlots()-1 do
  if culture:GetSlotPolicy(slot)==policy.Index then slotted=true end
  local can= culture:CanSlotPolicy(policy.Index,slot)
  assert(type(can)=='boolean','POLICY_ELIGIBILITY_UNKNOWN')
  if can then canSlot=true end
 end
end)
if not slotScan then slotted=nil;canSlot=nil end
emit('CY_POLICY','POLICY_RATIONALISM',boolean(read(function()return culture:IsPolicyUnlocked(policy.Hash)end)),canSlot,slotted)

-- Export actual current rows once, including flags and unfamiliar fields.
-- This is graph collection, not a generic rule interpreter.
local seen={}
local function record(tab,key,row)
 if seen[tab..':'..key] then return end;seen[tab..':'..key]=true
 assert(row,'RULE_ROW_MISSING:'..tab..':'..key)
 local fields={};for k in pairs(row) do fields[#fields+1]=k end;table.sort(fields)
 for _,k in ipairs(fields) do
  local v=row[k];local typ=type(v)
  -- GameInfo augments SQL rows with cached relation tables (for example a
  -- Policies row's PolicyModifiers). Traverse actual relation tables below,
  -- once, rather than recursively copying these cached objects. SQL scalar
  -- columns, including NULL, remain complete and explicit.
  if typ=='table' then emit('CY_RELATION',tab,key,k)
  else
   assert(typ=='string' or typ=='number' or typ=='boolean' or typ=='nil','RULE_FIELD_UNSUPPORTED:'..tab..':'..tostring(k)..':'..typ)
   emit('CY_RULE',tab,key,k,typ,v)
  end
 end
 emit('CY_ROW',tab,key)
end
local function rows(tab,field,value)
 local list={}
 for row in GameInfo[tab]() do if row[field]==value then list[#list+1]=row end end
 return list
end
local visitSet,visitRequirement,visitModifier
visitRequirement=function(id)
 if seen['r:'..id] then return end;seen['r:'..id]=true
 record('Requirements',id,GameInfo.Requirements[id])
 for i,row in ipairs(rows('RequirementArguments','RequirementId',id)) do
  record('RequirementArguments',id..':'..i,row)
  if row.Name=='RequirementSetId' then visitSet(row.Value) end
 end
end
visitSet=function(id)
 if seen['s:'..id] then return end;seen['s:'..id]=true
 record('RequirementSets',id,GameInfo.RequirementSets[id])
 for i,row in ipairs(rows('RequirementSetRequirements','RequirementSetId',id)) do
  record('RequirementSetRequirements',id..':'..i,row);visitRequirement(row.RequirementId)
 end
end
visitModifier=function(id)
 if seen['m:'..id] then return end;seen['m:'..id]=true
 local row=GameInfo.Modifiers[id];record('Modifiers',id,row)
 record('DynamicModifiers',row.ModifierType,GameInfo.DynamicModifiers[row.ModifierType])
 for i,arg in ipairs(rows('ModifierArguments','ModifierId',id)) do
  record('ModifierArguments',id..':'..i,arg)
  if arg.Name=='ModifierId' then visitModifier(arg.Value) end
 end
 if row.OwnerRequirementSetId then visitSet(row.OwnerRequirementSetId) end
 if row.SubjectRequirementSetId then visitSet(row.SubjectRequirementSetId) end
end
local ruleOK,ruleError=pcall(function()
 record('Policies','POLICY_RATIONALISM',policy)
 for i,row in ipairs(rows('PolicyModifiers','PolicyType','POLICY_RATIONALISM')) do
  record('PolicyModifiers',tostring(i),row);visitModifier(row.ModifierId)
 end
end)
emit('CY_RULE_DONE',ruleOK)
if not ruleOK then emit('CY_ERROR','rules',ruleError) end
printTurnWorld(before)
print('SENTINEL')
'''.replace('CITY_ID', 'nil' if city_id is None else str(city_id)).replace('AUDIT', 'true' if evidence else 'false').replace("'SENTINEL'", repr(SENTINEL))


def parse_response(rows):
    result = {'cities': [], 'policy': {}, 'rules': {}, 'rules_complete': False,
              'scan_complete': False, 'requested_city_found': False, 'errors': [], 'cached_relations': []}
    cities, districts, buildings, completed_rows = {}, {}, {}, set()
    def scalar(v):
        if v == '~':
            return None
        if v in ('true', 'false'):
            return v == 'true'
        return float(v)
    for line in rows:
        p = [unquote(v) for v in line.split('|')]
        tag = p[0]
        if tag == 'CY_CITY':
            cid = int(p[1])
            if cid in cities:
                raise ValueError('Duplicate city observation')
            c = dict(city_id=cid, owner=int(p[2]), name=p[3], population=scalar(p[4]),
                     science=scalar(p[5]), food_surplus=scalar(p[6]), districts=[], buildings=[])
            cities[cid] = c; result['cities'].append(c)
        elif tag == 'CY_DISTRICT':
            cid, did = int(p[1]), int(p[2])
            if (cid, did) in districts:
                raise ValueError('Duplicate district observation')
            d = dict(district_id=did, district_type=p[3], x=int(p[4]), y=int(p[5]),
                     complete=scalar(p[6]), pillaged=scalar(p[7]), adjacency={}, yields={})
            districts[cid, did] = d; cities[cid]['districts'].append(d)
        elif tag == 'CY_YIELD':
            d = districts[int(p[1]), int(p[2])]
            if p[3] in d['adjacency']:
                raise ValueError('Duplicate yield observation')
            d['adjacency'][p[3]], d['yields'][p[3]] = scalar(p[4]), scalar(p[5])
            d.setdefault('plot_adjacency', {})[p[3]] = scalar(p[6])
        elif tag == 'CY_BUILDING':
            key = (int(p[1]), p[2])
            if key in buildings:
                raise ValueError('Duplicate building observation')
            b = dict(building_type=p[2], district_type=p[3], pillaged=scalar(p[4]), yields={})
            buildings[key] = b
            cities[key[0]]['buildings'].append(b)
        elif tag == 'CY_BUILDING_YIELD':
            b = buildings[int(p[1]), p[2]]
            if p[3] in b['yields']:
                raise ValueError('Duplicate building yield observation')
            b['yields'][p[3]] = scalar(p[4])
        elif tag == 'CY_TOOLTIP':
            cities[int(p[1])]['science_tooltip'] = None if p[2] == '~' else p[2]
        elif tag == 'CY_POLICY':
            result['policy'] = dict(policy_type=p[1], unlocked=scalar(p[2]), can_slot=scalar(p[3]), slotted=scalar(p[4]))
        elif tag == 'CY_RULE':
            target = result['rules'].setdefault(p[1], {}).setdefault(p[2], {})
            if p[3] in target:
                raise ValueError('Duplicate rule field')
            target[p[3]] = scalar(p[5]) if p[4] in ('number', 'boolean', 'nil') else p[5]
        elif tag == 'CY_ROW':
            completed_rows.add((p[1], p[2]))
        elif tag == 'CY_RELATION':
            result['cached_relations'].append(p[1:])
        elif tag == 'CY_RULE_DONE':
            result['rules_complete'] = p[1] == 'true'
        elif tag == 'CY_SCAN':
            result['scan_complete'] = p[1] == 'true' and int(p[2]) == len(cities)
            result['requested_city_found'] = p[3] == 'true'
        elif tag == 'CY_ERROR':
            result['errors'].append({'section': p[1], 'error': p[2]})
    keys = {(tab, key) for tab, values in result['rules'].items() for key in values}
    result['rules_complete'] &= keys == completed_rows and bool(keys)
    expected = {'YIELD_' + v for v in ('FOOD', 'PRODUCTION', 'GOLD', 'SCIENCE', 'CULTURE', 'FAITH')}
    result['scan_complete'] &= all(set(d['adjacency']) == expected for d in districts.values())
    return result

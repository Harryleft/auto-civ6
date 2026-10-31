"""Public UI-equivalent reports: read getters only, no popup opening or events."""
from __future__ import annotations

import json

from civ_mcp.lua._helpers import _LUA_KNOWN_RESOURCE, SENTINEL
from civ_mcp.lua.government_conditions import LUA_GOVERNMENT_CONDITIONS
from civ_mcp.lua.resource_facts import LUA_RESOURCE_FACTS

SECTIONS = {"empire", "governments", "climate", "climate-history", "history", "great-people-history",
            "gossip", "world-resources", "encountered", "map-search"}
MAP_KINDS = {"resource", "luxury", "strategic", "antiquity", "village"}

HEADER = r'''
local NULL={__null=true}
local function A(t) t=t or {};t.__array=true;return t end
local function escape(s)
 s=s:gsub("\\","\\\\"):gsub('"','\\"'):gsub("\n","\\n"):gsub("\r","\\r"):gsub("\t","\\t")
 return s:gsub("[%z\1-\31]",function(c)return string.format("\\u%04x",string.byte(c))end)
end
local function encode(v)
 if v==nil or v==NULL then return "null" end
 local k=type(v)
 if k=="boolean" then return v and "true" or "false" end
 if k=="number" then if v~=v or math.abs(v)==math.huge then return "null" end;return tostring(v) end
 if k=="string" then return '"'..escape(v)..'"' end
 if k~="table" then return "null" end
 local out={}
 if v.__array then for _,x in ipairs(v)do out[#out+1]=encode(x)end;return "["..table.concat(out,",").."]" end
 for key,x in pairs(v)do out[#out+1]=encode(tostring(key))..":"..encode(x)end
 table.sort(out);return "{"..table.concat(out,",").."}"
end
local unknown=A();local seenUnknown={}
local function missing(label)
 if not seenUnknown[label]then unknown[#unknown+1]=label;seenUnknown[label]=true end
 return NULL
end
local function read(label,fn)
 local ok,v=pcall(fn)
 if not ok or v==nil or (type(v)=="number" and (v~=v or math.abs(v)==math.huge)) then return missing(label) end
 return v
end
local function scalar(v)return v==nil and NULL or v end
local function name(row)return row and Locale.Lookup(row.Name) or NULL end
local me=Game.GetLocalPlayer();local turn=Game.GetCurrentGameTurn()
local seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
local civ=PlayerConfigurations[me]:GetCivilizationTypeName()
local p=Players[me];local dip=p:GetDiplomacy();local vis=PlayersVisibility[me]
local function met(id)return id==me or (Players[id] and dip:HasMet(id)==true)end
local function who(id)
 if type(id)~="number" or not met(id)then return NULL end
 return Locale.Lookup(PlayerConfigurations[id]:GetCivilizationShortDescription())
end
local rows=A();local data={};local total=0;local mapCountComplete=nil
local function add(row)
 total=total+1
 if total>offset and #rows<limit then rows[#rows+1]=row end
end
'''

BODIES = {
    "governments": LUA_GOVERNMENT_CONDITIONS + r'''
local culture=p:GetCulture()
local function boolean(label,fn)
 local value=read(label,fn)
 if type(value)~="boolean" then return missing(label) end
 return value
end
local function integer(label,fn,minimum)
 local value=read(label,fn)
 if type(value)~="number" or value==NULL or value~=math.floor(value) or value<minimum then return missing(label) end
 return value
end
local current=integer("government.current",function()return culture:GetCurrentGovernment()end,-1)
local busy=boolean("government.core_busy",function()return UI.IsGameCoreBusy()end)
local conditions={
 core_idle=scalar(governmentNot(busy)),
 active_turn=boolean("government.active_turn",function()return p:IsTurnActive()end),
 strategic_commands_allowed=boolean("government.strategic_commands_allowed",function()return Game.IsAllowStrategicCommands(me)end),
 can_change_at_all=boolean("government.can_change_at_all",function()return culture:CanChangeGovernmentAtAll()end),
 government_change_made=boolean("government.government_change_made",function()return culture:GovernmentChangeMade()end),
 civic_completed_this_turn=boolean("government.civic_completed_this_turn",function()return culture:CivicCompletedThisTurn()end),
 in_anarchy=boolean("government.in_anarchy",function()return culture:IsInAnarchy()end),
 consideration_available=type(culture.SetGovernmentChangeConsidered)=="function" and type(culture.GovernmentChangeConsidered)=="function",
 change_considered=boolean("government.change_considered",function()return culture:GovernmentChangeConsidered()end),
 request_available=type(culture.RequestChangeGovernment)=="function",
}
conditions.initial_selection=current==NULL and NULL or current==-1
conditions.free_change_window=scalar(governmentFreeChangeWindow(current,conditions.civic_completed_this_turn))
conditions.turn_allowed=scalar(governmentTurnAllowed(conditions.active_turn,conditions.strategic_commands_allowed))
conditions.new_change_allowed=scalar(governmentNewChangeAllowed(conditions.can_change_at_all,
 conditions.government_change_made,conditions.free_change_window))
local function assessment(same,unlocked,duration,previousEnd)
 local reasons=A();local unknownCondition=false
 local function requireFact(value,code)
  value=governmentBool(value)
  if value==false then reasons[#reasons+1]=code elseif value==nil then unknownCondition=true end
 end
 requireFact(conditions.core_idle,"CORE_BUSY_OR_UNKNOWN")
 requireFact(conditions.active_turn,"NOT_ACTIVE_TURN")
 requireFact(conditions.strategic_commands_allowed,"STRATEGIC_COMMANDS_DISABLED")
 requireFact(conditions.consideration_available,"GOVERNMENT_CONSIDERATION_UNAVAILABLE")
 requireFact(type(conditions.change_considered)=="boolean" and true or nil,"GOVERNMENT_CONSIDERATION_UNREADABLE")
 -- Unknown current government cannot distinguish an acknowledgment from a new
 -- selection. New-selection-only failures must not reject a possible keep.
 if same==nil then
  if #reasons>0 then return false,reasons end
  return NULL,reasons
 end
 if same~=true then
  requireFact(unlocked,"GOVERNMENT_LOCKED")
  requireFact(conditions.can_change_at_all,"GOVERNMENT_CHANGE_DISABLED")
  requireFact(governmentNot(conditions.government_change_made),"GOVERNMENT_CHANGE_ALREADY_MADE")
  requireFact(conditions.free_change_window,"NO_FREE_GOVERNMENT_CHANGE_WINDOW")
  requireFact(governmentNot(conditions.in_anarchy),"ANARCHY_ALREADY_ACTIVE")
  requireFact(duration~=NULL and type(duration)=="number" and duration>=0 or nil,"ANARCHY_DURATION_UNAVAILABLE")
  if type(duration)=="number" and duration>0 then
   requireFact(type(previousEnd)=="number" and true or nil,"ANARCHY_INTERVAL_UNKNOWN")
  end
  requireFact(conditions.request_available,"GOVERNMENT_REQUEST_UNAVAILABLE")
 end
 if #reasons>0 then return false,reasons end
 return unknownCondition and NULL or true,reasons
end
-- Legacy can_change is the native general switch, not current target eligibility.
data.can_change=conditions.can_change_at_all;data.can_change_at_all=conditions.can_change_at_all
data.change_conditions=conditions;data.current_government=scalar(current)
data.eligibility_scope="InGame native choice preflight; Runtime pending gates still apply; keep-current acknowledges consideration; paid policy unlock is separate; eligibility is not a submission or completion guarantee"
local governments_known=false;local government_count=0;local anyChange=false;local changeUnknown=false
local previousEnd=NULL;local previousEndRead=false
for row in GameInfo.Governments()do
 government_count=government_count+1
 local unlocked=boolean("government.unlocked."..row.GovernmentType,function()return culture:IsGovernmentUnlocked(row.Hash)end)
 if unlocked~=NULL then governments_known=true end
 if unlocked==NULL then changeUnknown=true end
 if unlocked==true then
  local slots=A()
  for slot in GameInfo.Government_SlotCounts()do
   if slot.GovernmentType==row.GovernmentType then slots[#slots+1]={type=slot.GovernmentSlotType,count=slot.NumSlots}end
  end
  local same=current~=NULL and current==row.Index or nil
  if current~=NULL and current~=row.Index then same=false end
  local duration=integer("government.anarchy_turns."..row.GovernmentType,function()return culture:GetAnarchyTurns(row.Index)end,0)
  if same~=true and type(duration)=="number" and duration>0 and not previousEndRead then
   previousEnd=integer("government.previous_anarchy_end_turn",function()return culture:GetAnarchyEndTurn()end,-math.huge)
   previousEndRead=true
  end
  local selectable,reasons=assessment(same,unlocked,duration,previousEnd)
  if same~=true then
   if selectable==true then anyChange=true elseif selectable==NULL then changeUnknown=true end
  end
  add({type=row.GovernmentType,name=name(row),index=row.Index,current=scalar(same),slots=slots,
   can_select_now=selectable,refusal_reasons=reasons,anarchy_turns=duration,
   previous_anarchy_end_turn=(same~=true and type(duration)=="number" and duration>0)and previousEnd or NULL})
 end
end
data.can_change_now=anyChange or (changeUnknown and NULL or false)
data.governments=(government_count==0 or governments_known)and rows or NULL
''',
    "empire": r'''
data.gold={balance=read("gold.balance",function()return p:GetTreasury():GetGoldBalance()end),
 per_turn=read("gold.per_turn",function()local t=p:GetTreasury();return t:GetGoldYield()-t:GetTotalMaintenance()end)}
data.science_per_turn=read("science.per_turn",function()return p:GetTechs():GetScienceYield()end)
data.culture_per_turn=read("culture.per_turn",function()return p:GetCulture():GetCultureYield()end)
data.faith={balance=read("faith.balance",function()return p:GetReligion():GetFaithBalance()end),
 per_turn=read("faith.per_turn",function()return p:GetReligion():GetFaithYield()end)}
data.favor={balance=read("favor.balance",function()return p:GetFavor()end),per_turn=read("favor.per_turn",function()return p:GetFavorPerTurn()end)}
data.envoys_available=read("envoys.available",function()return p:GetInfluence():GetTokensToGive()end)
''',
    "climate": r'''
data.total_co2_footprint=read("climate.total_co2_footprint",function()return GameClimate.GetTotalCO2Footprint()end)
data.player_co2_footprint=read("climate.player_co2_footprint",function()return GameClimate.GetPlayerCO2Footprint(me,false)end)
data.temperature_change_celsius=read("climate.temperature_change",function()return GameClimate.GetTemperatureChange()end)
data.climate_change_points=read("climate.change_points",function()return GameClimate.GetClimateChangeLevel()end)
data.deforestation_type=read("climate.deforestation",function()return GameClimate.GetDeforestationType()end)
data.co2_modifier=read("climate.co2_modifier",function()return GameClimate.GetCO2FootprintModifier()end)
data.tiles_flooded=read("climate.tiles_flooded",function()return GameClimate.GetTilesFlooded()end)
data.tiles_submerged=read("climate.tiles_submerged",function()return GameClimate.GetTilesSubmerged()end)
data.next_sea_level_rise_turns=read("climate.next_sea_rise",function()return GameClimate.GetNextSeaLevelRiseTurns()end)
data.co2_concentration_ppm=NULL
data.co2_unit="Native game carbon footprint; no physical ppm concentration is inferred"
''',
    "climate-history": r'''
local events_known=since_turn>turn
for t=turn,math.max(0,since_turn),-1 do
 local available,event=pcall(function()return GameRandomEvents.GetEventsForTurn(t)end)
 if not available then missing("climate.events");event=NULL end
 if available then events_known=true end
 -- Native nil means no event for this turn, unlike a missing getter.
 if event~=NULL and event~=nil then
  local def=GameInfo.RandomEvents[event.RandomEvent]
  if def and def.EffectOperatorType~="NUCLEAR_ACCIDENT" then
   local plot=event.CurrentLocation and Map.GetPlotByIndex(event.CurrentLocation)
   if def.Global==true or def.Global==1 or (plot and vis:IsRevealed(plot:GetX(),plot:GetY())) then
    add({turn=t,type=def.RandomEventType,name=name(def),
         x=plot and plot:GetX()or NULL,y=plot and plot:GetY()or NULL,
         climate_change_points=scalar(def.ClimateChangePoints),global=def.Global==true or def.Global==1})
   end
  end
 end
end
data.events=events_known and rows or NULL
''',
    "history": r'''
local moments=read("history.moments",function()return Game.GetHistoryManager():GetAllMomentsData(me,1)end)
if type(moments)=="table" and moments~=NULL then
 for _,m in ipairs(moments)do
  if type(m.Turn)=="number" and m.Turn>=since_turn then
   local def=GameInfo.Moments[m.Type]
   add({id=scalar(m.ID),turn=m.Turn,era=scalar(m.GameEra),era_score=scalar(m.EraScore),
        type=def and def.MomentType or NULL,description=scalar(m.InstanceDescription)})
  end
 end
 data.moments=rows
else data.moments=NULL end
''',
    "great-people-history": r'''
local timeline=read("great_people.past_timeline",function()return Game.GetGreatPeople():GetPastTimeline()end)
if type(timeline)=="table" and timeline~=NULL then
 for _,entry in ipairs(timeline)do
  if type(entry.Claimant)=="number"and entry.Claimant>=0 and (target_player<0 or entry.Claimant==target_player)then
   local individual=entry.Individual and GameInfo.GreatPersonIndividuals[entry.Individual]
   add({individual_id=scalar(entry.Individual),name=name(individual),
        claimant_id=met(entry.Claimant)and entry.Claimant or NULL,claimant=who(entry.Claimant),
        turn=scalar(entry.Turn),era=scalar(entry.Era),class=scalar(entry.Class),
        payment_method=NULL,payment_amount=NULL})
  end
 end
 data.people=rows;data.payment_history="The native past timeline does not establish currency or actual historical payment"
else data.people=NULL end
''',
    "gossip": r'''
local targets={}
local gossip_known=false
for id=0,63 do if Players[id]and Players[id]:IsMajor()and met(id)and id~=me and (target_player<0 or target_player==id)then targets[#targets+1]=id end end
for _,id in ipairs(targets)do
 local messages=read("gossip.visible_strings",function()return Game.GetGossipManager():GetRecentVisibleGossipStrings(since_turn,me,id)end)
 if type(messages)=="table" and messages~=NULL then
  gossip_known=true
  for _,message in pairs(messages)do
   if type(message)=="string"then add({player_id=id,civilization=who(id),message=message})
   elseif type(message)=="table"then
    local item={player_id=id,civilization=who(id)}
    for k,v in pairs(message)do if type(v)=="string"or type(v)=="number"or type(v)=="boolean"then item[tostring(k)]=v end end
    add(item)
   end
  end
 end
end
data.messages=(#targets==0 or gossip_known)and rows or NULL
''',
    "encountered": r'''
for id=0,63 do
 if Players[id]and Players[id]:IsMajor()and met(id)and (target_player<0 or target_player==id)then
  local cfg=PlayerConfigurations[id]
  add({player_id=id,civilization=cfg:GetCivilizationTypeName(),leader=cfg:GetLeaderTypeName(),name=who(id),local_player=id==me})
 end
end
data.civilizations=rows
''',
    "world-resources": r'''
local resources_known=false;local resource_players=0
for id=0,63 do
 if Players[id]and Players[id]:IsMajor()and met(id)and (target_player<0 or target_player==id)then
  resource_players=resource_players+1
  if id==me then
   for row in GameInfo.Resources()do
    local amount=read("resources.own",function()return p:GetResources():GetResourceAmount(row.Index)end)
    if amount~=NULL then resources_known=true end
    if amount~=NULL and amount>0 then add({player_id=id,civilization=who(id),resource=row.ResourceType,name=name(row),amount=amount,scope="own stock"})end
   end
  else
   local deal=DealManager.GetWorkingDeal(DealDirection.OUTGOING,me,id)
   local available,items=pcall(function()return DealManager.GetPossibleDealItems(id,id,DealItemTypes.RESOURCES,deal)end)
   if not available then missing("resources.tradable");items=NULL end
   if available then resources_known=true end
   if type(items)=="table"and items~=NULL then
    for _,item in ipairs(items)do
     local row=GameInfo.Resources[item.ForType]
     if row and type(item.MaxAmount)=="number"and item.MaxAmount>0 then
      add({player_id=id,civilization=who(id),resource=row.ResourceType,name=name(row),amount=item.MaxAmount,scope="native tradable amount, not total deposits"})
     end
    end
   end
  end
 end
end
data.resources=(resource_players==0 or resources_known)and rows or NULL;data.scope="Global resource UI: own stocks and met civilizations' native tradable resources, not hidden world deposits"
''',
    "map-search": r'''
mapCountComplete=true
local function incomplete(label)mapCountComplete=false;missing(label)end
local function observedBoolean(label,fn)
 local value=read(label,fn)
 if type(value)~="boolean"then incomplete(label);return nil end
 return value
end
local function inspectPlot(i)
 local plot=Map.GetPlotByIndex(i);local x,y=plot:GetX(),plot:GetY()
 local path="map.plot."..x..","..y
 if observedBoolean(path..".revealed",function()return vis:IsRevealed(x,y)end)~=true then return end
 if search_kind=="village"then
  -- Unknown visibility cannot establish that a dynamic village is absent.
  if observedBoolean(path..".visible_now",function()return vis:IsVisible(x,y)end)~=true then return end
  local index=plot:GetImprovementType()
  if type(index)~="number"or index~=index or math.abs(index)==math.huge or index~=math.floor(index)then incomplete(path..".improvement_type");return end
  local row=index>=0 and GameInfo.Improvements[index]
  if index>=0 and not row then incomplete(path..".improvement_type");return end
  if row and (row.Goody==true or row.Goody==1)then
   local label=Locale.Lookup(row.Name)
   if query==""or label:lower():find(query:lower(),1,true)or row.ImprovementType:lower():find(query:lower(),1,true)then add({x=x,y=y,type=row.ImprovementType,name=label,visible_now=true})end
  end
 else
  local resource=knownResourceIndex(me,plot)
  if resource==nil then incomplete("map.resource_visibility")
  elseif resource>=0 then
   local row=GameInfo.Resources[resource]
   if row then
    local match=search_kind=="resource"or (search_kind=="luxury"and row.ResourceClassType=="RESOURCECLASS_LUXURY")or
       (search_kind=="strategic"and row.ResourceClassType=="RESOURCECLASS_STRATEGIC")or
       (search_kind=="antiquity"and (row.ResourceType=="RESOURCE_ANTIQUITY_SITE"or row.ResourceType=="RESOURCE_SHIPWRECK"))
    local consumable=row.ResourceType=="RESOURCE_ANTIQUITY_SITE"or row.ResourceType=="RESOURCE_SHIPWRECK"or row.ResourceClassType=="RESOURCECLASS_BONUS"or row.ResourceClassType=="RESOURCECLASS_ARTIFACT"
    local label=Locale.Lookup(row.Name)
    if match and (query==""or label:lower():find(query:lower(),1,true)or row.ResourceType:lower():find(query:lower(),1,true))then
     if consumable and observedBoolean(path..".visible_now",function()return vis:IsVisible(x,y)end)~=true then return end
     local facts=resourcePlotFacts(me,plot,row)
     for _,field in ipairs(facts.unknown_fields)do missing(path..'.'..field)end
     add({x=x,y=y,type=row.ResourceType,name=label,class=scalar(row.ResourceClassType),visible_now=scalar(facts.visible_now),
      owner_id=scalar(facts.owner_id),improvement_type=scalar(facts.improvement_type),pillaged=scalar(facts.pillaged),
      work_unit=scalar(facts.work_unit),details_status=facts.details_status,scope="Resource sighting; dynamic ownership/facilities only on owned or currently visible plots; purchase/development eligibility unverified"})
    end
   else incomplete(path..".resource_type")end
  end
 end
end
local plotCount=read("map.plot_count",function()return Map.GetPlotCount()end)
if type(plotCount)=="number"and plotCount>=0 and plotCount==math.floor(plotCount)then
 for i=0,plotCount-1 do
  -- A failed plot must not discard sightings already read or prevent later reads.
  local ok=pcall(inspectPlot,i)
  if not ok then incomplete("map.plot."..i..".read")end
 end
else incomplete("map.plot_count")end
data.locations=rows;data.scope="No fog reveal; huts/consumable sites/bonus resources require current visibility; other resources require revealed terrain and native resource visibility. Pagination covers observed matches; incomplete matching counts leave total/truncated unknown"
''',
}


def build(section, *, target_player=-1, limit=50, offset=0, since_turn=0, search_kind="resource", query=""):
    if section not in SECTIONS or search_kind not in MAP_KINDS:
        raise ValueError("Unknown report section or map search kind")
    if any(type(x) is not int for x in (target_player, limit, offset, since_turn)) or not -1 <= target_player <= 63 or not 1 <= limit <= 200 or min(offset, since_turn) < 0:
        raise ValueError("Invalid player, pagination or turn filter")
    if not isinstance(query, str) or any(ord(c) < 32 for c in query):
        raise ValueError("query must be text without control characters")
    params = f"local target_player,limit,offset,since_turn={target_player},{limit},{offset},{since_turn}\nlocal search_kind,query={json.dumps(search_kind)},{json.dumps(query, ensure_ascii=False)}\n"
    return params + HEADER + _LUA_KNOWN_RESOURCE + LUA_RESOURCE_FACTS + r'''
local ok,err=pcall(function()
if target_player>=0 and not met(target_player)then error("Target civilization has not been encountered")end
''' + BODIES[section] + r'''
end)
local knownIdentity=type(me)=='number'and me>=0 and me<=63 and me==math.floor(me)and type(turn)=='number'and turn>=0 and math.abs(turn)<math.huge and turn==math.floor(turn)and type(seed)=='number'and seed==seed and math.abs(seed)<math.huge and seed==math.floor(seed)and type(civ)=='string'and civ:match('^CIVILIZATION_.+')~=nil
if not knownIdentity then missing('world.identity')end
local stable=knownIdentity and Game.GetLocalPlayer()==me and Game.GetCurrentGameTurn()==turn and GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")==seed and PlayerConfigurations[me]:GetCivilizationTypeName()==civ
local result={schema_version=1,status=(ok and stable)and (#unknown==0 and "ready"or "partial")or "unknown",
 identity={player_id=scalar(me),turn=scalar(turn),seed=scalar(seed),civilization=scalar(civ)},data=(ok and stable)and data or NULL,
 unknown_fields=unknown,total=total,offset=offset,limit=limit,truncated=total>offset+limit}
if mapCountComplete~=nil then
 result.count_complete=mapCountComplete;result.observed_total=total
 if not mapCountComplete then result.total=NULL;result.truncated=NULL end
end
if not ok then result.error=tostring(err)end
if not stable then result.error="World identity incomplete or changed during read"end
print("PUBLIC_REPORT|"..encode(result))
''' + f'print("{SENTINEL}")'


def parse(rows):
    values = [json.loads(row.split("|", 1)[1]) for row in rows if row.startswith("PUBLIC_REPORT|")]
    if len(values) != 1:
        return {"status": "unknown", "error": "Native public report unavailable", "data": None}
    return values[0]

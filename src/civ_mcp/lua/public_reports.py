"""Public UI-equivalent reports: read getters only, no popup opening or events."""
from __future__ import annotations

import json

from civ_mcp.lua._helpers import _LUA_KNOWN_RESOURCE, SENTINEL
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
local rows=A();local data={};local total=0
local function add(row)
 total=total+1
 if total>offset and #rows<limit then rows[#rows+1]=row end
end
'''

BODIES = {
    "governments": r'''
local culture=p:GetCulture()
local current=read("government.current",function()return culture:GetCurrentGovernment()end)
data.can_change=read("government.can_change",function()return culture:CanChangeGovernmentAtAll()end)
local governments_known=false;local government_count=0
for row in GameInfo.Governments()do
 government_count=government_count+1
 local unlocked=read("government.unlocked",function()return culture:IsGovernmentUnlocked(row.Hash)end)
 if unlocked~=NULL then governments_known=true end
 if unlocked==true then
  local slots=A()
  for slot in GameInfo.Government_SlotCounts()do
   if slot.GovernmentType==row.GovernmentType then slots[#slots+1]={type=slot.GovernmentSlotType,count=slot.NumSlots}end
  end
  add({type=row.GovernmentType,name=name(row),index=row.Index,current=current==NULL and NULL or current==row.Index,slots=slots})
 end
end
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
for i=0,Map.GetPlotCount()-1 do
 local plot=Map.GetPlotByIndex(i);local x,y=plot:GetX(),plot:GetY()
 if vis:IsRevealed(x,y)then
  local resource=search_kind=="village"and -1 or knownResourceIndex(me,plot)
  if resource==nil then missing("map.resource_visibility")
  elseif resource>=0 then
   local row=GameInfo.Resources[resource]
   if row then
    local match=search_kind=="resource"or (search_kind=="luxury"and row.ResourceClassType=="RESOURCECLASS_LUXURY")or
       (search_kind=="strategic"and row.ResourceClassType=="RESOURCECLASS_STRATEGIC")or
       (search_kind=="antiquity"and (row.ResourceType=="RESOURCE_ANTIQUITY_SITE"or row.ResourceType=="RESOURCE_SHIPWRECK"))
    local consumable=row.ResourceType=="RESOURCE_ANTIQUITY_SITE"or row.ResourceType=="RESOURCE_SHIPWRECK"or row.ResourceClassType=="RESOURCECLASS_BONUS"
    if consumable and not vis:IsVisible(x,y)then match=false end
    local label=Locale.Lookup(row.Name)
    if match and (query==""or label:lower():find(query:lower(),1,true)or row.ResourceType:lower():find(query:lower(),1,true))then
     local facts=resourcePlotFacts(me,plot,row)
     add({x=x,y=y,type=row.ResourceType,name=label,class=scalar(row.ResourceClassType),visible_now=scalar(facts.visible_now),
      owner_id=scalar(facts.owner_id),improvement_type=scalar(facts.improvement_type),pillaged=scalar(facts.pillaged),
      work_unit=scalar(facts.work_unit),scope="Resource sighting; dynamic ownership/facilities only on owned or currently visible plots; purchase/development eligibility unverified"})
    end
   end
  end
  -- Improvements are dynamic: do not inspect current hidden/fogged huts.
  if search_kind=="village"and vis:IsVisible(x,y)then
   local index=plot:GetImprovementType();local row=index>=0 and GameInfo.Improvements[index]
   if row and (row.Goody==true or row.Goody==1)then
    local label=Locale.Lookup(row.Name)
    if query==""or label:lower():find(query:lower(),1,true)or row.ImprovementType:lower():find(query:lower(),1,true)then add({x=x,y=y,type=row.ImprovementType,name=label,visible_now=true})end
   end
  end
 end
end
data.locations=rows;data.scope="No fog reveal; huts/consumable sites/bonus resources require current visibility; other resources require revealed terrain and native resource visibility"
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
local stable=Game.GetLocalPlayer()==me and Game.GetCurrentGameTurn()==turn and GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")==seed and PlayerConfigurations[me]:GetCivilizationTypeName()==civ
local result={schema_version=1,status=(ok and stable)and (#unknown==0 and "ready"or "partial")or "unknown",
 identity={player_id=me,turn=turn,seed=seed,civilization=civ},data=(ok and stable)and data or NULL,
 unknown_fields=unknown,total=total,offset=offset,limit=limit,truncated=total>offset+limit}
if not ok then result.error=tostring(err)end
if not stable then result.error="World identity or turn changed during read"end
print("PUBLIC_REPORT|"..encode(result))
''' + f'print("{SENTINEL}")'


def parse(rows):
    values = [json.loads(row.split("|", 1)[1]) for row in rows if row.startswith("PUBLIC_REPORT|")]
    if len(values) != 1:
        return {"status": "unknown", "error": "Native public report unavailable", "data": None}
    return values[0]

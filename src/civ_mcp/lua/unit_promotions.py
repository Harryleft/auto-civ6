"""Read the native UI/current promotion sets without performing a command."""
from __future__ import annotations

import json
import math
import re

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.models import PromotionOption, UnitPromotionStatus


def build(unit_id: int) -> str:
    if type(unit_id) is not int or unit_id < 0:
        raise ValueError("Expected the current unit's full native ID")
    return r'''
local NULL={}
local function array(t)t=t or {};t.__array=true;return t end
local function quote(s)
 s=s:gsub("\\","\\\\"):gsub('"','\\"'):gsub("\n","\\n"):gsub("\r","\\r"):gsub("\t","\\t")
 return '"'..s:gsub("[%z\1-\31]",function(c)return string.format("\\u%04x",string.byte(c))end)..'"'
end
local function encode(v)
 if v==nil or v==NULL then return "null" end
 local kind=type(v)
 if kind=="boolean" then return v and "true" or "false" end
 if kind=="number" then return v==math.floor(v)and string.format("%.0f",v)or tostring(v)end
 if kind=="string" then return quote(v) end
 local parts={}
 if v.__array then
  for _,item in ipairs(v)do parts[#parts+1]=encode(item)end
  return "["..table.concat(parts,",").."]"
 end
 for key,item in pairs(v)do parts[#parts+1]=quote(key)..":"..encode(item)end
 table.sort(parts);return "{"..table.concat(parts,",").."}"
end
local function finite(v)return type(v)=="number" and v==v and math.abs(v)<math.huge end
local function integer(v)return finite(v) and v==math.floor(v)end
local function index(v)return integer(v)and v>=0 end
local function text(v)return type(v)=="string"and #v>0 end
local function boolean(v)return type(v)=="boolean"end
local unknown=array();local missingFields={}
local function missing(label)
 if not missingFields[label]then unknown[#unknown+1]=label;missingFields[label]=true end
 return NULL
end
local function read(label,fn,valid)
 local ok,value=pcall(fn)
 if not ok or not valid(value)then return missing(label)end
 return value
end
local requested=__UNIT_ID__
local ok,result=pcall(function()
 local owner,turn=Game.GetLocalPlayer(),Game.GetCurrentGameTurn()
 local seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
 assert(index(owner)and owner<=63 and index(turn)and integer(seed),"WORLD_IDENTITY_UNAVAILABLE")
 local civ=PlayerConfigurations[owner]:GetCivilizationTypeName()
 assert(text(civ)and civ:match("^CIVILIZATION_.+"),"WORLD_IDENTITY_UNAVAILABLE")
 local function actor()
  local unit=UnitManager.GetUnit(owner,requested%65536)
  assert(unit and unit:GetOwner()==owner and unit:GetID()==requested,"UNIT_ID_CHANGED")
  return unit
 end
 local unit=actor()
 local x,y,unitType=unit:GetX(),unit:GetY(),unit:GetUnitType()
 assert(index(x)and index(y)and index(unitType),"UNIT_IDENTITY_UNAVAILABLE")
 local info=GameInfo.Units[unitType]
 assert(info and text(info.UnitType),"UNIT_TYPE_UNAVAILABLE")
 local exp=unit:GetExperience()
 local function candidates(loose,label)
  local called,allowed,results=pcall(function()
   return UnitManager.CanStartCommand(unit,UnitCommandTypes.PROMOTE,loose,true)
  end)
  if not called or not boolean(allowed)then
   return missing(label..".allowed"),missing(label..".candidates")
  end
  if type(results)~="table" or not UnitCommandResults or UnitCommandResults.PROMOTIONS==nil then
   return allowed,missing(label..".candidates")
  end
  local values=results[UnitCommandResults.PROMOTIONS]
  if type(values)~="table" then return allowed,missing(label..".candidates")end
  local list=array();local seen={}
  for _,value in pairs(values)do
   if not index(value)or seen[value]then return allowed,missing(label..".candidates")end
   local row=GameInfo.UnitPromotions[value]
   if not row or row.Index~=value or not text(row.UnitPromotionType)then return allowed,missing(label..".candidates")end
   seen[value]=true;list[#list+1]=value
  end
  table.sort(list);return allowed,list
 end
 -- Official UnitPanel/UnitPromotionPopup use true for UI candidates; the
 -- existing write preflight uses false for permission to execute now.
 local uiAllowed,ui=candidates(true,"ui")
 local currentAllowed,current=candidates(false,"current")
 local catalog={};local options=array()
 local function add(values)
  if values==NULL then return end
  for _,value in ipairs(values)do
   if not catalog[value]then
    local row=GameInfo.UnitPromotions[value]
    catalog[value]={index=value,promotion_type=row.UnitPromotionType,
     name=read("promotion."..value..".name",function()return Locale.Lookup(row.Name)end,text),
     description=read("promotion."..value..".description",function()return Locale.Lookup(row.Description)end,text),
     has_promotion=read("promotion."..value..".has_promotion",function()return exp:HasPromotion(value)end,boolean)}
    options[#options+1]=catalog[value]
   end
  end
 end
 add(ui);add(current);table.sort(options,function(a,b)return a.index<b.index end)
 if ui==NULL and current==NULL then options=NULL end
 local moves=read("moves",function()return unit:GetMovesRemaining()end,function(v)return finite(v)and v>=0 end)
 local xp=read("xp",function()return exp:GetExperiencePoints()end,function(v)return finite(v)and v>=0 end)
 local xpNeeded=read("xp_needed",function()return exp:GetExperienceForNextLevel()end,function(v)return finite(v)and v>=0 end)
 local owned=read("owned_promotions",function()return exp:GetPromotions()end,function(v)return type(v)=="table"end)
 local count=NULL
 if owned~=NULL then
  local seen={};count=0
  for _,value in pairs(owned)do
   if not index(value)or seen[value]then count=missing("owned_promotions");break end
   seen[value]=true;count=count+1
  end
 end
 local busy=read("core_busy",function()return UI.IsGameCoreBusy()end,boolean)
 local after=actor()
 assert(Game.GetLocalPlayer()==owner and Game.GetCurrentGameTurn()==turn
  and GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")==seed
  and PlayerConfigurations[owner]:GetCivilizationTypeName()==civ
  and after:GetX()==x and after:GetY()==y and after:GetUnitType()==unitType,"WORLD_OR_UNIT_CHANGED_DURING_READ")
 return {schema_version=1,status=#unknown==0 and "ready"or "partial",requested_unit_id=requested,
  identity={player_id=owner,turn=turn,seed=seed,civilization=civ,unit_id=requested,
   unit_index=requested%65536,unit_type=info.UnitType,x=x,y=y},
  can_offer_promotions=uiAllowed,can_promote_now=currentAllowed,
  ui_candidates=ui,current_candidates=current,promotions=options,
  moves=moves,xp=xp,xp_needed=xpNeeded,promotion_count=count,core_busy=busy,unknown_fields=unknown}
end)
if not ok then result={schema_version=1,status="unknown",requested_unit_id=requested,error=tostring(result),unknown_fields=array({"native.report"})}end
print("UNIT_PROMOTIONS|"..encode(result))
'''.replace("__UNIT_ID__", str(unit_id)) + f'print("{SENTINEL}")'


def unavailable(unit_id: int | None, reason: str) -> UnitPromotionStatus:
    requested = unit_id if type(unit_id) is int and unit_id >= 0 else -1
    return UnitPromotionStatus(requested, requested % 65536 if requested >= 0 else -1,
                               "UNKNOWN", status="unknown", error=reason,
                               unknown_fields=["native.report"])


def _integer(value, minimum=None):
    return (type(value) in (int, float) and math.isfinite(value) and value == math.floor(value)
            and (minimum is None or value >= minimum))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _bool(value):
    return value is None or type(value) is bool


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate native promotion report field")
        result[key] = value
    return result


def parse(lines: list[str], unit_id: int | None = None) -> UnitPromotionStatus:
    try:
        rows = [line.split("|", 1)[1] for line in lines if line.startswith("UNIT_PROMOTIONS|")]
        if len(rows) != 1:
            raise ValueError("Expected one native promotion report")
        value = json.loads(rows[0], object_pairs_hook=_object)
        if type(value) is not dict or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("Native promotion report schema unavailable")
        requested = value["requested_unit_id"]
        if not _integer(requested, 0) or (unit_id is not None and requested != unit_id):
            raise ValueError("Native promotion report requested unit mismatch")
        requested = int(requested)
        if value.get("status") == "unknown":
            return unavailable(requested, str(value.get("error", "Native promotion observation unknown")))
        identity = value["identity"]
        if (type(identity) is not dict or not all(_integer(identity.get(k), 0) for k in
                ("player_id", "turn", "unit_id", "unit_index", "x", "y"))
                or identity["player_id"] > 63 or identity["unit_id"] != requested
                or identity["unit_index"] != requested % 65536
                or not _integer(identity.get("seed"))
                or not isinstance(identity.get("civilization"), str)
                or not re.fullmatch(r"CIVILIZATION_.+", identity["civilization"])
                or not isinstance(identity.get("unit_type"), str) or not identity["unit_type"]):
            raise ValueError("Native promotion report identity unavailable")
        identity = dict(identity)
        for key in ("player_id", "turn", "unit_id", "unit_index", "x", "y", "seed"):
            identity[key] = int(identity[key])
        unknown = value["unknown_fields"]
        if (type(unknown) is not list or any(type(k) is not str or not k for k in unknown)
                or len(set(unknown)) != len(unknown)
                or value.get("status") != ("partial" if unknown else "ready")):
            raise ValueError("Native promotion report completeness unavailable")
        for key in ("can_offer_promotions", "can_promote_now", "core_busy"):
            if key not in value or not _bool(value[key]):
                raise ValueError("Native promotion permission malformed")
        sets = {}
        for key in ("ui_candidates", "current_candidates"):
            values = value[key]
            if values is not None and (type(values) is not list or any(not _integer(i, 0) for i in values)
                    or len(set(values)) != len(values)):
                raise ValueError("Native promotion candidate set malformed")
            if values is not None:
                value[key] = [int(i) for i in values]
            sets[key] = None if values is None else set(values)
        union = (sets["ui_candidates"] or set()) | (sets["current_candidates"] or set())
        rows = value["promotions"]
        if rows is None:
            if any(s is not None for s in sets.values()):
                raise ValueError("Native promotion catalog missing")
            options = None
        else:
            if type(rows) is not list or all(s is None for s in sets.values()):
                raise ValueError("Native promotion catalog malformed")
            options = [];seen = set();types = set()
            for row in rows:
                index, kind = row["index"], row["promotion_type"]
                if (not _integer(index, 0) or index in seen or not isinstance(kind, str)
                        or not kind or kind in types or not _bool(row["has_promotion"])
                        or any(row[k] is not None and (type(row[k]) is not str or not row[k]) for k in ("name", "description"))):
                    raise ValueError("Native promotion catalog entry malformed")
                index = int(index)
                seen.add(index);types.add(kind)
                ui = None if sets["ui_candidates"] is None else index in sets["ui_candidates"]
                current = None if sets["current_candidates"] is None else index in sets["current_candidates"]
                checks = (current, value["can_promote_now"],
                          None if row["has_promotion"] is None else not row["has_promotion"],
                          None if value["core_busy"] is None else not value["core_busy"])
                selectable = False if False in checks else True if all(c is True for c in checks) else None
                options.append(PromotionOption(kind, row["name"], row["description"], index=index,
                    ui_candidate=ui, current_candidate=current, has_promotion=row["has_promotion"],
                    can_select_now=selectable))
            if seen != union:
                raise ValueError("Native promotion catalog does not match candidate sets")
        for key in ("moves", "xp", "xp_needed"):
            if key not in value or (value[key] is not None and not _number(value[key])):
                raise ValueError("Native promotion reference number malformed")
        if value["promotion_count"] is not None and not _integer(value["promotion_count"], 0):
            raise ValueError("Native owned promotion count malformed")
        return UnitPromotionStatus(requested, identity["unit_index"], identity["unit_type"], options,
            value["xp"], value["xp_needed"], value["promotion_count"],
            status=value["status"], identity=identity, can_offer_promotions=value["can_offer_promotions"],
            can_promote_now=value["can_promote_now"], ui_candidates=value["ui_candidates"],
            current_candidates=value["current_candidates"], moves=value["moves"], core_busy=value["core_busy"],
            unknown_fields=unknown)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        return unavailable(unit_id, str(error))

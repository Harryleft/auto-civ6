"""Internal support for bounded native JSON queries.

Encoding and world witnesses belong here; domain completeness, visibility,
pagination and pending ownership remain with their existing readers.
"""
from __future__ import annotations


def json_query_header(*, precise_numbers: bool = False) -> str:
    """Preserve the caller's wire precision without editing shared Lua source."""
    number = "string.format('%.17g',v)" if precise_numbers else "tostring(v)"
    return "local function encodeQueryNumber(v)return " + number + " end\n" + _LUA_JSON + _WORLD_START


_LUA_JSON = r'''
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
 if k=="number" then if v~=v or math.abs(v)==math.huge then return "null" end;return encodeQueryNumber(v) end
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
'''

_WORLD_START = r'''
local me=Game.GetLocalPlayer();local turn=Game.GetCurrentGameTurn()
local seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
local civ=PlayerConfigurations[me]:GetCivilizationTypeName()
local p=Players[me];local vis=PlayersVisibility[me]
local function queryWorldKnown(player,gameTurn,gameSeed,civilization)
 local function integer(value)
  return type(value)=='number'and value==value and math.abs(value)<math.huge and value==math.floor(value)
 end
 return integer(player)and player>=0 and player<=63 and integer(gameTurn)and gameTurn>=0
  and integer(gameSeed)and type(civilization)=='string'and civilization:match('^CIVILIZATION_[A-Z0-9_]+$')~=nil
end
local function queryWorldStable()
 if not queryWorldKnown(me,turn,seed,civ)then missing('world.identity');return false end
 local ok,same=pcall(function()
  return Game.GetLocalPlayer()==me and Game.GetCurrentGameTurn()==turn
   and GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")==seed
   and PlayerConfigurations[me]:GetCivilizationTypeName()==civ
 end)
 return ok and same
end
'''

#!/usr/bin/env python3
"""读取当前 Civ VI 对局的关键状态（通过 FireTuner 执行 Lua）。

只在没有其他 FireTuner 客户端时使用；连完即断。
"""

from __future__ import annotations

import asyncio
import sys

REPO_SRC = r"S:\vibe_coding\auto-civ6\src"
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from civ_mcp.connection import GameConnection  # noqa: E402
from civ_mcp import lua as lq  # noqa: E402

LUA = """
local lines = {}
local function add(s) lines[#lines+1] = tostring(s) end
local me = Game.GetLocalPlayer()
add("PLAYER=" .. me)
add("TURN=" .. Game.GetCurrentGameTurn() .. "/" .. (GameConfiguration.GetValue("GAME_MAX_TURNS") or 0))

local p = Players[me]
local cfg = PlayerConfigurations[me]
add("CIV=" .. tostring(cfg:GetCivilizationTypeName()))
add("LEADER=" .. tostring(cfg:GetLeaderTypeName()))
add("TEAM=" .. tostring(p:GetTeam()))

local t = p:GetTreasury()
add("GOLD=" .. tostring(t:GetGoldBalance()))
add("GOLD_PT=" .. tostring(t:GetGoldYield() - t:GetTotalMaintenance()))

local sci = p:GetScience()
add("SCIENCE=" .. tostring(sci:GetScienceYield()))
local cul = p:GetCulture()
add("CULTURE=" .. tostring(cul:GetCultureYield()))

add("CITIES=" .. tostring(p:GetCities():GetCount()))
add("UNITS=" .. tostring(p:GetUnits():GetCount()))

pcall(function()
  local seen = {}
  local rows = DB.Query("SELECT VictoryType FROM Victories")
  if rows then
    for _, r in ipairs(rows) do
      if r.VictoryType and not seen[r.VictoryType] then
        seen[r.VictoryType] = true
        add("VICTORY_TYPE=" .. r.VictoryType)
      end
    end
  end
end)
add("SENTINEL_OK")
"""


async def main() -> int:
    conn = GameConnection()
    await conn.connect()
    gc = conn.gamecore_index
    print(f"gamecore_index={gc}  ingame_index={conn.ingame_index}")
    if gc is None:
        print("未进入对局")
        await conn.disconnect()
        return 1
    lines = await conn.execute_in_state(gc, LUA, timeout=20.0, require_sentinel=False)
    for ln in lines:
        print(ln)
    await conn.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

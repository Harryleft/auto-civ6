"""信条查询与提交共用原生可用性事实，不把已被占用的信条列为可选。"""

import asyncio

import pytest

from civ_mcp.connection import LuaError
from civ_mcp.game_state import GameState
from test_religion_plan_confirmation import ReligionConn


def query_conn():
    conn = ReligionConn()
    conn.lua.execute('''
for _,player in pairs(Players) do player.IsAlive=function() return true end end
for _,data in ipairs({GameInfo.Beliefs,GameInfo.Religions}) do
 local rows={}
 for key,row in pairs(data) do
  if key==row.Index then row.Description=row.Name;table.insert(rows,row) end
 end
 setmetatable(data,{__call=function()
  local n=0;return function() n=n+1;return rows[n] end
 end})
end
''')
    return conn


@pytest.mark.parametrize("restriction", ["takenBeliefs[11]=true", "takenPantheon[11]=true", "tooMany[11]=true"])
def test_query_excludes_unavailable_belief_but_keeps_legal_alternative(restriction):
    conn = query_conn()
    conn.lua.execute("pantheon=1;" + restriction)
    state = GameState(conn)
    status = asyncio.run(state.get_religion_founding_status())
    assert [item.belief_type for item in status.beliefs_by_class["BELIEF_CLASS_FOLLOWER"]] == ["BELIEF_WORK"]
    assert conn.lua.eval("#submissions") == 0
    result = asyncio.run(state.found_religion("RELIGION_BUDDHISM", "BELIEF_WORK", "BELIEF_PROPERTY"))
    assert result.startswith("RELIGION_FOUNDED|")


def test_unknown_native_availability_fails_read_instead_of_advertising_a_choice():
    conn = query_conn()
    conn.lua.execute("religion.IsInSomeReligion=function() return nil end")
    with pytest.raises(LuaError, match="BELIEF_AVAILABILITY_UNKNOWN"):
        asyncio.run(GameState(conn).get_religion_founding_status())
    assert conn.lua.eval("#submissions") == 0

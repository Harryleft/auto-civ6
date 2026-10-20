"""Stable identity through real queries, with an isolated Lua 5.1 native API."""

import asyncio

from lupa.lua51 import LuaRuntime
import pytest

from civ_mcp.connection import LuaError
from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_overview


class IdentityConn:
    """Only the native boundary is fake; builders and parsers run unchanged."""

    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.calls = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
local function empty() return ipairs({}) end
local collection={Members=empty}
local diplo={
    HasMet=function(_,other) return other~=3 end,
    IsAtWarWith=function() return false end,
    GetGrievancesAgainst=function() return 0 end,
    GetVisibilityOn=function() return 1 end,
    HasDelegationAt=function() return false end,
    HasEmbassyAt=function() return false end,
}
Game={GetLocalPlayer=function() return 0 end,
      GetCurrentGameTurn=function() return 1 end,
      GetEras=function() return {
          GetCurrentEra=function() return 0 end,
          GetPlayerCurrentScore=function() return 0 end,
          GetPlayerDarkAgeThreshold=function() return 0 end,
          GetPlayerGoldenAgeThreshold=function() return 0 end} end}
GameConfiguration={GetValue=function() return 0 end}
Locale={Lookup=function(text) return text end}
GameInfo={Technologies={},Civics={},Units={},Governments={},Religions={},
          Eras={[0]={Name="Ancient"}},Victories={}}
Map={GetPlotCount=function() return 0 end}
PlayersVisibility={[0]={IsRevealed=function() return false end}}
Players,PlayerConfigurations={},{}
identity_reads={}
for owner=0,3 do
    local id=owner
    local civs={[0]="CIVILIZATION_BABYLON_STK",[1]="CIVILIZATION_EGYPT",
                [2]="CIVILIZATION_EGYPT",[3]="CIVILIZATION_HIDDEN"}
    local leaders={[0]="LEADER_HAMMURABI",[1]="LEADER_CLEOPATRA",
                   [2]="LEADER_CLEOPATRA_ALT",[3]="LEADER_HIDDEN"}
    PlayerConfigurations[id]={
        GetCivilizationShortDescription=function() return id==0 and "巴比伦" or "Egypt" end,
        GetLeaderName=function() return id==0 and "汉谟拉比" or "Cleopatra" end,
        GetCivilizationTypeName=function()
            identity_reads[#identity_reads+1]="civ:"..id
            assert(id~=3,"unmet civilization type must not be read")
            return civs[id]
        end,
        GetLeaderTypeName=function()
            identity_reads[#identity_reads+1]="leader:"..id
            assert(id~=3,"unmet leader type must not be read")
            return leaders[id]
        end,
    }
    Players[id]={
        IsAlive=function() return true end,IsMajor=function() return true end,
        GetDiplomacy=function() return diplo end,
        GetDiplomaticAI=function() return {
            GetDiplomaticStateIndex=function() return 3 end,
            GetDiplomaticModifiers=function() return {} end} end,
        GetCities=function() return collection end,GetUnits=function() return collection end,
        GetScore=function() return 10 end,GetFavor=function() return 0 end,
        GetFavorPerTurn=function() return 0 end,
        GetTreasury=function() return {
            GetGoldBalance=function() return 30 end,GetGoldYield=function() return 5 end,
            GetTotalMaintenance=function() return 1 end} end,
        GetTechs=function() return {
            GetResearchingTech=function() return -1 end,
            GetScienceYield=function() return 2 end} end,
        GetCulture=function() return {
            GetProgressingCivic=function() return -1 end,
            GetCultureYield=function() return 1 end,
            GetCurrentGovernment=function() return -1 end} end,
        GetReligion=function() return {
            GetFaithBalance=function() return 0 end,
            GetReligionTypeCreated=function() return -1 end} end,
    }
end
''')

    async def execute_write(self, source):
        self.calls.append(source)
        self.rows.clear()
        try:
            self.lua.execute(source)
        except Exception as error:
            raise LuaError(str(error)) from error
        return [row for row in self.rows if row != "---END---"]

    async def execute_read(self, source):
        return await self.execute_write(source)

    async def disconnect(self):
        pass


def test_overview_returns_native_civilization_and_leader_ids_alongside_localized_names():
    overview = asyncio.run(GameState(IdentityConn()).get_game_overview())
    assert overview.civ_name == "巴比伦"
    assert overview.leader_name == "汉谟拉比"
    assert overview.civilization_type == "CIVILIZATION_BABYLON_STK"
    assert overview.leader_type == "LEADER_HAMMURABI"
    text = narrate_overview(overview)
    assert "CIVILIZATION_BABYLON_STK" in text
    assert "LEADER_HAMMURABI" in text


@pytest.mark.parametrize("native_rate", [12, 0, -7])
def test_overview_uses_native_net_favor_including_bonuses_and_penalties(native_rate):
    conn = IdentityConn()
    # 原生收益包含四座文艺复兴城墙，或因外交惩罚降到零和负数。
    conn.lua.execute(f"Players[0].GetFavorPerTurn=function() return {native_rate} end")
    overview = asyncio.run(GameState(conn).get_game_overview())
    assert overview.favor_per_turn == native_rate
    assert f"({native_rate:+d}/turn)" in narrate_overview(overview)


def test_diplomacy_keeps_variants_distinct_and_never_reads_unmet_identity():
    from civ_mcp.narrate import narrate_diplomacy

    conn = IdentityConn()
    rivals = asyncio.run(GameState(conn).get_diplomacy())
    first, variant, hidden = rivals
    assert first.leader_name == variant.leader_name == "Cleopatra"
    assert first.civilization_type == variant.civilization_type == "CIVILIZATION_EGYPT"
    assert first.leader_type == "LEADER_CLEOPATRA"
    assert variant.leader_type == "LEADER_CLEOPATRA_ALT"
    assert hidden.has_met is False
    assert hidden.civilization_type is None and hidden.leader_type is None
    assert "civ:3" not in list(conn.lua.globals().identity_reads.values())
    assert "leader:3" not in list(conn.lua.globals().identity_reads.values())
    text = narrate_diplomacy(rivals)
    assert "LEADER_CLEOPATRA_ALT" in text and "LEADER_CLEOPATRA" in text
    assert "CIVILIZATION_HIDDEN" not in text and "LEADER_HIDDEN" not in text


@pytest.mark.parametrize("native_value", ["nil", "42", '"Babylon"', '"LEADER_BAD|injected"', 'error("API unavailable")'])
def test_unavailable_or_invalid_native_identity_does_not_break_other_facts(native_value):
    from civ_mcp.narrate import narrate_diplomacy

    conn = IdentityConn()
    conn.lua.execute(f'''
for i=0,2 do
    PlayerConfigurations[i].GetCivilizationTypeName=function() return {native_value} end
    PlayerConfigurations[i].GetLeaderTypeName=nil
end
''')
    game = GameState(conn)
    overview = asyncio.run(game.get_game_overview())
    rivals = asyncio.run(game.get_diplomacy())
    assert overview.civilization_type is None and overview.leader_type is None
    assert overview.civ_name == "巴比伦" and overview.gold == 30
    assert all(c.civilization_type is None and c.leader_type is None for c in rivals)
    assert rivals[0].diplomatic_state == "NEUTRAL"
    assert "civilization_type=unknown | leader_type=unknown" in narrate_overview(overview)
    assert "civilization_type=unknown | leader_type=unknown" in narrate_diplomacy(rivals)


class ResponseConn:
    """A recorded native response; subsequent optional bootstrap is unavailable."""

    def __init__(self, rows):
        self.rows = rows

    async def execute_write(self, source):
        if self.rows is None:
            raise LuaError("optional query unavailable")
        rows, self.rows = self.rows, None
        return rows


@pytest.mark.parametrize("markers", [[], ["IDENTITY|9|CIVILIZATION_WRONG|LEADER_WRONG"],
                                     ["IDENTITY|0|显示名称|bad"], ["IDENTITY|bad|x|y"],
                                     ["IDENTITY|0|CIVILIZATION_BABYLON_STK"]])
def test_legacy_or_unusable_overview_identity_remains_unknown(markers):
    rows = ["1|0|巴比伦|汉谟拉比|30|4|2|1|0|None|None|0|0|10", *markers]
    overview = asyncio.run(GameState(ResponseConn(rows)).get_game_overview())
    assert overview.civ_name == "巴比伦"
    assert overview.civilization_type is None and overview.leader_type is None


def test_diplomacy_identity_joins_by_player_and_does_not_attach_unmatched_or_unmet_rows():
    rows = [
        "IDENTITY|2|CIVILIZATION_EGYPT|LEADER_CLEOPATRA_ALT",
        "IDENTITY|3|CIVILIZATION_HIDDEN|LEADER_HIDDEN",
        "IDENTITY|9|CIVILIZATION_WRONG|LEADER_WRONG",
        "CIV|1|Egypt|Cleopatra|1|0|NEUTRAL|0|1|0|0|0|0",
        "CIV|2|Egypt|Cleopatra|1|0|NEUTRAL|0|1|0|0|0|0",
        "CIV|3|Unmet Civilization|Unknown Leader|0|0|UNKNOWN|0|0|0|0|0|0",
        "IDENTITY|1|CIVILIZATION_EGYPT|LEADER_CLEOPATRA",
    ]
    first, variant, hidden = asyncio.run(GameState(ResponseConn(rows)).get_diplomacy())
    assert first.leader_type == "LEADER_CLEOPATRA"
    assert variant.leader_type == "LEADER_CLEOPATRA_ALT"
    assert hidden.civilization_type is None and hidden.leader_type is None


def test_conflicting_identity_records_do_not_guess_one_of_the_native_claims():
    rows = [
        "1|0|巴比伦|汉谟拉比|30|4|2|1|0|None|None|0|0|10",
        "IDENTITY|0|CIVILIZATION_BABYLON_STK|LEADER_HAMMURABI",
        "IDENTITY|0|CIVILIZATION_EGYPT|LEADER_CLEOPATRA",
    ]
    overview = asyncio.run(GameState(ResponseConn(rows)).get_game_overview())
    assert overview.civilization_type is None and overview.leader_type is None


def test_conflicting_diplomacy_identity_is_unknown_instead_of_last_record_winning():
    rows = [
        "CIV|1|Egypt|Cleopatra|1|0|NEUTRAL|0|1|0|0|0|0",
        "IDENTITY|1|CIVILIZATION_EGYPT|LEADER_CLEOPATRA",
        "IDENTITY|1|CIVILIZATION_EGYPT|LEADER_CLEOPATRA_ALT",
    ]
    rival, = asyncio.run(GameState(ResponseConn(rows)).get_diplomacy())
    assert rival.civilization_type is None and rival.leader_type is None


@pytest.mark.parametrize("missing,expected_civ,expected_leader", [
    ("GetCivilizationTypeName", None, "LEADER_HAMMURABI"),
    ("GetLeaderTypeName", "CIVILIZATION_BABYLON_STK", None),
])
def test_one_unavailable_identity_api_does_not_erase_the_other(missing, expected_civ, expected_leader):
    conn = IdentityConn()
    conn.lua.execute(f"PlayerConfigurations[0].{missing}=nil")
    overview = asyncio.run(GameState(conn).get_game_overview())
    assert overview.civilization_type == expected_civ
    assert overview.leader_type == expected_leader


def test_real_mcp_overview_and_diplomacy_expose_only_public_stable_identities(monkeypatch, tmp_path):
    import anyio
    from mcp import ClientSession
    from civ_mcp import server
    from civ_mcp.logger import GameLogger

    conn = IdentityConn()
    monkeypatch.setattr(server, "GameConnection", lambda: conn)
    monkeypatch.setattr(server, "GameLogger", lambda: GameLogger(tmp_path / "tools.jsonl"))

    async def go():
        client_write, server_read = anyio.create_memory_object_stream(10)
        server_write, client_read = anyio.create_memory_object_stream(10)
        lowlevel = server.mcp._mcp_server
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(lowlevel.run, server_read, server_write,
                             lowlevel.create_initialization_options())
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                required = {"get_game_overview", "get_diplomacy", "get_turn_status"}
                assert required <= tools.keys()
                assert all(tools[name].annotations.readOnlyHint is True for name in required)
                assert tools["get_turn_status"].inputSchema.get("properties", {}) == {}
                overview = await session.call_tool("get_game_overview", {})
                assert not overview.isError
                text = overview.content[0].text
                assert "巴比伦 (汉谟拉比)" in text
                assert "civilization_type=CIVILIZATION_BABYLON_STK | leader_type=LEADER_HAMMURABI" in text
                diplomacy = await session.call_tool("get_diplomacy", {})
                assert not diplomacy.isError
                text = diplomacy.content[0].text
                assert "leader_type=LEADER_CLEOPATRA" in text
                assert "leader_type=LEADER_CLEOPATRA_ALT" in text
                assert "Unmet Civilization (Unknown Leader) — not met" in text
                assert "CIVILIZATION_HIDDEN" not in text and "LEADER_HIDDEN" not in text
            tasks.cancel_scope.cancel()

    anyio.run(go)
    assert "civ:3" not in list(conn.lua.globals().identity_reads.values())
    assert "leader:3" not in list(conn.lua.globals().identity_reads.values())


@pytest.mark.parametrize("marker", ["", "IDENTITY|9|CIVILIZATION_WRONG|LEADER_WRONG",
                                    "IDENTITY|²|CIVILIZATION_WRONG|LEADER_WRONG"])
def test_legacy_or_wrong_player_diplomacy_record_preserves_unknown_identity(marker):
    rows = ["CIV|1|Egypt|Cleopatra|1|0|NEUTRAL|0|1|0|0|0|0"]
    if marker:
        rows.append(marker)
    rival, = asyncio.run(GameState(ResponseConn(rows)).get_diplomacy())
    assert rival.civilization_type is None and rival.leader_type is None


def test_native_identity_does_not_reveal_random_agenda_below_required_visibility():
    conn = IdentityConn()
    conn.lua.execute('''
Players[1].GetAgendaTypes=function() return {11,12} end
GameInfo.Agendas={
    [11]={Index=11,Name="Historical agenda",Description="Known preferences"},
    [12]={Index=12,Name="Secret random agenda",Description="Hidden preferences"},
    AGENDA_HISTORY={Index=11},
}
GameInfo.HistoricalAgendas=function()
    local done=false
    return function()
        if done then return nil end
        done=true
        return {LeaderType="LEADER_CLEOPATRA",AgendaType="AGENDA_HISTORY"}
    end
end
''')
    first, _, _ = asyncio.run(GameState(conn).get_diplomacy())
    assert first.leader_type == "LEADER_CLEOPATRA"
    assert [(agenda.category, agenda.name) for agenda in first.agendas] == [
        ("HISTORICAL", "Historical agenda"), ("HIDDEN", "???")]
    from civ_mcp.narrate import narrate_diplomacy
    text = narrate_diplomacy([first])
    assert "Secret random agenda" not in text and "Hidden preferences" not in text

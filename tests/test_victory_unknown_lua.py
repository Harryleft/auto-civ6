"""Unknown native facts stay visible through full and confirmed-turn views."""

import asyncio

import pytest

from civ_mcp import lua as lq
from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_victory_progress
from test_end_turn import GameStateConn
from test_victory_lua_execution import VictoryConn


class VictoryReportConn(GameStateConn):
    def __init__(self, native):
        super().__init__()
        self.native = native

    async def execute_write(self, source):
        if source == lq.build_victory_proximity_query():
            self.calls.append(source)
            self.native.lua.globals().turn = self.turn
            return await self.native.execute_write(source)
        return await super().execute_write(source)


def read_full_and_report(native):
    facts = asyncio.run(GameState(native).get_victory_progress())
    conn = VictoryReportConn(native)
    gs = GameState(conn)
    report = asyncio.run(gs.end_turn())
    assert "Turn 50 -> 51" in report
    assert not gs.turn_pending and len(conn.requests) == 1
    assert conn.calls.count(lq.build_victory_proximity_query()) == 1
    assert not any("Network.SaveGame" in source or "RequestOperation" in source for source in conn.calls)
    return facts, narrate_victory_progress(facts), report


@pytest.mark.parametrize("result", ["return nil", "error('unavailable')", "return 1", "return 'unknown'"])
def test_unknown_has_met_is_reported_without_disclosing_unverified_identity(result):
    native = VictoryConn()
    native.lua.execute("""
Players[0].GetDiplomacy = function()
    return {HasMet = function(_, id)
        if id == 1 then %s end
        return false
    end}
end
""" % result)
    facts, full, report = read_full_and_report(native)
    assert [player.player_id for player in facts.players] == [0]
    assert facts.contacts_known is False
    for text in (full, report):
        assert "Unknown observations: contact status" in text
        assert "Known rival" not in text and "Hidden rival" not in text
        assert "space race observed" not in text


def test_unknown_encounter_does_not_hide_a_separately_verified_rival_risk():
    native = VictoryConn()
    native.lua.execute("""
Players[0].GetDiplomacy = function()
    return {HasMet = function(_, id)
        if id == 1 then return true end
        error('unavailable')
    end}
end
""")
    facts, full, report = read_full_and_report(native)
    assert facts.contacts_known is False
    for text in (full, report):
        assert "Known rival: space race observed" in text
        assert "Unknown observations: contact status" in text
        assert "Hidden rival" not in text


@pytest.mark.parametrize("result", ["return nil", "return 1", "return 'unknown'", "error('unavailable')"])
def test_non_boolean_spaceport_completion_is_unknown_in_both_public_views(result):
    native = VictoryConn()
    native.lua.execute("""
Game.IsVictoryEnabled = function(id) return id == 1 end
local city = Players[1]:GetCities():GetCapitalCity()
city.GetDistricts = function()
    return {Members = function()
        return ipairs({{GetType = function() return 1 end, IsComplete = function() %s end}})
    end}
end
""" % result)
    facts, full, report = read_full_and_report(native)
    rival = next(player for player in facts.players if player.player_id == 1)
    assert rival.spaceports is None
    for text in (full, report):
        assert "Unknown observations: science" in text
        assert "Known rival: space race observed" not in text


def test_unknown_port_count_preserves_independent_native_progress_risk():
    native = VictoryConn()
    native.lua.execute("""
Game.IsVictoryEnabled = function(id) return id == 1 end
science[1] = 25.5
local city = Players[1]:GetCities():GetCapitalCity()
city.GetDistricts = function()
    return {Members = function()
        return ipairs({{GetType = function() return 1 end, IsComplete = function() return nil end}})
    end}
end
""")
    facts, full, report = read_full_and_report(native)
    assert next(player for player in facts.players if player.player_id == 1).spaceports is None
    for text in (full, report):
        assert "Known rival: space race observed" in text
        assert "25.5/33.5 VP | near requirement" in text
        assert "Unknown observations: science" in text


def test_one_verified_spaceport_still_warns_when_another_completion_is_unknown():
    native = VictoryConn()
    native.lua.execute("""
Game.IsVictoryEnabled = function(id) return id == 1 end
local city = Players[1]:GetCities():GetCapitalCity()
city.GetDistricts = function()
    return {Members = function()
        return ipairs({
            {GetType = function() return 1 end, IsComplete = function() return true end},
            {GetType = function() return 1 end, IsComplete = function() return nil end},
        })
    end}
end
""")
    facts, full, report = read_full_and_report(native)
    rival = next(player for player in facts.players if player.player_id == 1)
    assert rival.spaceports is None
    for text in (full, report):
        assert "Known rival: space race observed" in text
        assert "at least 1 verified completed spaceport" in text
        assert "Unknown observations: science" in text


@pytest.mark.parametrize("method", ["GetReligionInMajorityOfCities", "GetReligionTypeCreated"])
@pytest.mark.parametrize("result", [
    "return 0/0", "return math.huge", "return -math.huge", "return -2", "return 1.5",
    "return nil", "return true", "return '1'", "error('unavailable')", "return 999",
])
def test_invalid_religion_ids_remain_unknown_without_erasing_known_science_risk(method, result):
    native = VictoryConn()
    native.lua.execute("""
Game.IsVictoryEnabled = function(id) return id == 1 or id == 4 end
GameInfo.Religions[1] = {ReligionType = 'RELIGION_TEST', Name = 'Known religion'}
Players[1].GetReligion = function()
    return {GetReligionTypeCreated = function() return 1 end,
            GetReligionInMajorityOfCities = function() return 1 end}
end
local original = Players[1].GetReligion
Players[1].GetReligion = function()
    local religion = original()
    religion.%s = function() %s end
    return religion
end
""" % (method, result))
    facts, full, report = read_full_and_report(native)
    rival = next(player for player in facts.players if player.player_id == 1)
    assert rival.religion_civilizations is None
    if method == "GetReligionInMajorityOfCities":
        assert facts.religion_majority["Known rival"] == "unknown"
        assert rival.religion_civilizations_required is None
    else:
        assert rival.has_religion is None
        assert facts.religions_founded is None
    for text in (full, report):
        assert "Unknown observations: religious" in text
        assert "Known rival: space race observed" in text
        assert "founded religion is majority in 0/3" not in text


def test_explicit_unmet_status_is_known_and_does_not_disclose_identity():
    native = VictoryConn()
    native.lua.execute("""
Game.IsVictoryEnabled = function(id) return id == 1 end
Players[0].GetDiplomacy = function() return {HasMet = function() return false end} end
""")
    facts, full, report = read_full_and_report(native)
    assert facts.contacts_known is True
    assert [player.player_id for player in facts.players] == [0]
    for text in (full, report):
        assert "Unknown observations" not in text
        assert "Known rival" not in text and "Hidden rival" not in text
        assert "space race observed" not in text


@pytest.mark.parametrize("complete, count", [("false", 0), ("true", 1)])
def test_explicit_boolean_spaceport_completion_remains_a_known_count(complete, count):
    native = VictoryConn()
    native.lua.execute("""
Game.IsVictoryEnabled = function(id) return id == 1 end
local city = Players[1]:GetCities():GetCapitalCity()
city.GetDistricts = function()
    return {Members = function()
        return ipairs({{GetType = function() return 1 end, IsComplete = function() return %s end}})
    end}
end
""" % complete)
    facts, full, report = read_full_and_report(native)
    rival = next(player for player in facts.players if player.player_id == 1)
    assert rival.spaceports == count
    for text in (full, report):
        assert "Unknown observations" not in text
        assert ("Known rival: space race observed" in text) is bool(count)


def test_native_no_religion_sentinel_and_active_zero_id_are_valid_distinct_facts():
    native = VictoryConn()
    native.lua.execute("Game.IsVictoryEnabled = function(id) return id == 4 end")
    facts, full, report = read_full_and_report(native)
    rival = next(player for player in facts.players if player.player_id == 1)
    assert rival.has_religion is False
    assert rival.religion_civilizations == 0 and rival.religion_civilizations_required == 3
    assert facts.religion_majority["Known rival"] == "none"
    assert "Unknown observations" not in full and "Unknown observations" not in report

    native.lua.execute("""
GameInfo.Religions[0] = {ReligionType = 'RELIGION_TEST', Name = 'Active religion'}
Players[1].GetReligion = function()
    return {GetReligionTypeCreated = function() return 0 end,
            GetReligionInMajorityOfCities = function() return 0 end}
end
""")
    facts, full, report = read_full_and_report(native)
    rival = next(player for player in facts.players if player.player_id == 1)
    assert rival.has_religion is True
    assert rival.religion_civilizations == 1 and rival.religion_civilizations_required == 3
    assert facts.religion_majority["Known rival"] == "RELIGION_TEST"
    assert "Unknown observations" not in full and "Unknown observations" not in report

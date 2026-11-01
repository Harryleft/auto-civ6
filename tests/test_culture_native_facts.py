"""Optional native culture observations stay read-only and scoped to contacts."""

from dataclasses import asdict
import json

import pytest

from civ_mcp import lua as lq
from civ_mcp.game_state import GameState
from civ_mcp.lua.victory import observe_victory_progress_response
from civ_mcp.narrate import narrate_victory_progress
from test_victory_lua_execution import VictoryConn, run


def test_native_dominance_is_not_inferred_from_tourist_totals_and_keeps_both_directions():
    conn = VictoryConn()
    conn.lua.execute('''
Players[1]:GetCulture().IsDominantOver=function(_, target) assert(target==0); return true end
Players[1]:GetCulture().GetTurnsUntilVictory=function() return 17 end
''')
    facts = run(GameState(conn).get_victory_progress())
    assert facts.culture_dominance == {0: {1: False}, 1: {0: True}}
    assert facts.culture_turns_until_victory == {0: None, 1: 17}
    assert facts.players[1].culture_visiting == 1
    assert facts.players[1].culture_required == 41
    assert asdict(facts)["culture_dominance"][1][0] is True
    assert "Known rival -> Our civilization: native cultural dominance true" in narrate_victory_progress(facts)
    rows = [json.loads(row) for row in narrate_victory_progress(facts, compact=True).splitlines() if row.startswith("[")]
    assert ["Known rival", "Our civilization", True] in rows
    assert ["Known rival", 17, "native estimate"] in rows
    assert not any("Request" in source or "Network.SaveGame" in source for source in conn.calls)


@pytest.mark.parametrize("raw", [0, -1, -99])
def test_nonpositive_forecast_preserves_raw_value_without_claiming_victory(raw):
    conn = VictoryConn()
    conn.lua.execute(f"Players[1]:GetCulture().GetTurnsUntilVictory=function() return {raw} end")
    facts = run(GameState(conn).get_victory_progress())
    assert facts.culture_turns_until_victory[1] == raw
    text = narrate_victory_progress(facts)
    assert f"native turns until victory {raw} (no positive forecast reported)" in text
    assert "forecasts are not victory confirmation" in text


@pytest.mark.parametrize("body", ["return nil", "error('unavailable')", "return 0/0", "return math.huge", "return '17'"])
def test_missing_or_invalid_optional_api_keeps_unknown_and_query_complete(body):
    conn = VictoryConn()
    conn.lua.execute(f'''
Players[0]:GetCulture().IsDominantOver=nil
Players[1]:GetCulture().IsDominantOver=function() return nil end
Players[1]:GetCulture().GetTurnsUntilVictory=function() {body} end
''')
    facts = run(GameState(conn).get_victory_progress())
    observation = observe_victory_progress_response([row for row in conn.rows if row != "---END---"])
    assert observation.problems == ()
    assert facts.complete
    assert facts.culture_dominance == {0: {1: None}, 1: {0: None}}
    assert facts.culture_turns_until_victory == {0: None, 1: None}
    assert 'native cultural dominance unknown' in narrate_victory_progress(facts)


def test_new_native_calls_omit_unmet_players_and_matrix_omits_same_team():
    conn = VictoryConn()
    conn.lua.execute('''
hiddenCalls=0
Players[2]:GetCulture().GetTurnsUntilVictory=function() hiddenCalls=hiddenCalls+1; return 1 end
Players[2]:GetCulture().IsDominantOver=function() hiddenCalls=hiddenCalls+1; return true end
Players[0]:GetCulture().IsDominantOver=function(_, target) assert(target~=2); return false end
Players[1]:GetCulture().IsDominantOver=function(_, target) assert(target~=2); return true end
Players[1].GetTeam=function() return 0 end
''')
    facts = run(GameState(conn).get_victory_progress())
    assert conn.lua.globals().hiddenCalls == 0
    assert facts.culture_dominance == {}
    assert set(facts.culture_turns_until_victory) == {0, 1}
    assert "Hidden rival" not in narrate_victory_progress(facts)
    assert "Hidden rival" not in "\n".join(conn.rows)


def test_unknown_team_does_not_manufacture_dominance_or_call_native_pair():
    conn = VictoryConn()
    conn.lua.execute('''
Players[1].GetTeam=nil
Players[1]:GetCulture().IsDominantOver=function() error('must not query pair with unknown team') end
''')
    facts = run(GameState(conn).get_victory_progress())
    assert facts.culture_dominance == {0: {1: None}, 1: {0: None}}
    assert facts.culture_turns_until_victory == {0: None, 1: None}


def test_missing_optional_rows_in_legacy_response_are_not_false_or_zero():
    facts = lq.parse_victory_progress_response(["CULTURE|Known rival|1|20|false"])
    assert facts.culture_dominance == {}
    assert facts.culture_turns_until_victory == {}
    assert facts.our_tourists_from == {"Known rival": 1}


def test_met_rival_to_rival_dominance_is_observed_but_dead_major_is_excluded():
    conn = VictoryConn()
    conn.lua.execute('''
Players[0].GetDiplomacy=function() return {HasMet=function() return true end} end
Players[1]:GetCulture().IsDominantOver=function(_, target) return target==2 end
Players[2]:GetCulture().IsDominantOver=function() return nil end
''')
    facts = run(GameState(conn).get_victory_progress())
    assert facts.culture_dominance == {0: {1: False, 2: False}, 1: {0: False, 2: True}, 2: {0: None, 1: None}}
    conn.lua.execute('Players[2].IsAlive=function() return false end')
    facts = run(GameState(conn).get_victory_progress())
    assert facts.culture_dominance == {0: {1: False}, 1: {0: False}}
    assert set(facts.culture_turns_until_victory) == {0, 1}


def test_raw_native_rows_validate_player_ids_boolean_and_finite_forecast():
    for row in ["CULTURE_NATIVE|1|nan", "CULTURE_NATIVE|63|1", "CULTURE_NATIVE|1.5|3",
                "CULTURE_DOMINANCE|1|2|invalid", "CULTURE_DOMINANCE|1|1|true"]:
        assert observe_victory_progress_response([row]).problems
    facts = lq.parse_victory_progress_response(["CULTURE_NATIVE|1|-1", "CULTURE_DOMINANCE|1|2|unknown"])
    assert facts.culture_turns_until_victory[1] == -1
    assert facts.culture_dominance[1][2] is None

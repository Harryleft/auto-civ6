"""Domain readers own completeness; callers need not interpret wire markers."""
import pytest

from civ_mcp.lua.notifications import observe_end_turn_blocking, observe_notifications_response
from civ_mcp.lua.overview import observe_gameover_response, parse_gameover_response
from civ_mcp.lua.units import (
    observe_threat_scan_response, observe_units_response, parse_units_response,
)
from civ_mcp.lua.victory import observe_victory_progress_response


UNIT = "65536|0|Warrior|UNIT_WARRIOR|1,2|2/2|100/100"


def test_units_keep_available_values_without_certifying_a_dropped_row():
    rows = [UNIT, "truncated"]
    observation = observe_units_response(rows)
    assert observation.value == parse_units_response(rows)
    assert len(observation.value) == 1 and not observation.complete
    with pytest.raises(ValueError, match="Unit rows"):
        observation.require_complete()
    assert observe_units_response([UNIT]).require_complete() == parse_units_response([UNIT])
    assert observe_units_response([]).require_complete() == []


@pytest.mark.parametrize("reader,empty", [
    (observe_threat_scan_response, ["NO_THREATS"]),
    (observe_notifications_response, ["TOTAL|0|0"]),
    (observe_end_turn_blocking, ["NONE"]),
])
def test_observed_empty_is_distinct_from_absent_response(reader, empty):
    assert reader(empty).require_complete() == []
    missing = reader([])
    assert missing.value == [] and not missing.complete
    with pytest.raises(ValueError):
        missing.require_complete()


def test_threat_truncation_cannot_become_an_observed_empty_scan():
    assert not observe_threat_scan_response(["THREAT|unreadable"]).complete
    assert not observe_threat_scan_response(["NO_THREATS", "unexpected"]).complete


def test_active_game_and_missing_outcome_retain_legacy_value_but_different_completeness():
    assert parse_gameover_response([]) is None
    assert observe_gameover_response(["GAME_ACTIVE"]).require_complete() is None
    assert not observe_gameover_response([]).complete
    assert not observe_gameover_response(["GAME_ACTIVE", "GAME_OVER|VICTORY|Rome"]).complete
    rows = ["GAME_OVER|VICTORY|Rome|VICTORY_DOMINATION|alive|Trajan"]
    assert observe_gameover_response(rows).require_complete() == parse_gameover_response(rows)


def test_victory_reader_keeps_native_partial_facts_without_certifying_completion():
    partial = observe_victory_progress_response(["VICTORY_CONTEXT|12|0"])
    assert partial.value.turn == 12 and not partial.complete
    with pytest.raises(ValueError, match="Victory response incomplete"):
        partial.require_complete()


@pytest.mark.parametrize('rows', [
    ['TOTAL|broken', 'NOTIF|truncated'],
    ['TOTAL|1|1'],
    ['TOTAL|0|0', 'TOTAL|0|0'],
    ['TOTAL|0|1'],
    ['TOTAL|1|0', 'NOTIF|TYPE|message|bad|0,0'],
    ['TOTAL|1|1', 'NOTIF|truncated'],
    ['TOTAL|0|0', 'unexpected'],
])
def test_notification_counts_and_bad_rows_never_certify_an_empty_list(rows):
    assert not observe_notifications_response(rows).complete


def test_notification_total_includes_dismissed_entries_and_preserves_valid_neighbours():
    row = 'NOTIF|NOTIFICATION_CHOOSE_TECH|Choose a technology|12|-1,-1|Research'
    assert len(observe_notifications_response(['TOTAL|4|1', row]).require_complete()) == 1
    partial = observe_notifications_response(['TOTAL|4|2', row, 'NOTIF|truncated'])
    assert len(partial.value) == 1 and not partial.complete


@pytest.mark.parametrize('rows', [
    ['NONE', 'BLOCKING|CHOOSE_TECH|Choose tech'],
    ['NONE', 'NONE'], ['BLOCKING|'], ['BLOCKING||unknown'],
    ['BLOCKING|CHOOSE_TECH|First', 'BLOCKING|CHOOSE_TECH|Again'],
])
def test_blocker_conflicts_or_truncation_do_not_clear_decisions(rows):
    observation = observe_end_turn_blocking(rows)
    assert not observation.complete
    with pytest.raises(ValueError, match='Blocker completeness'):
        observation.require_complete()


@pytest.mark.parametrize('rows', [
    ['GAME_OVER|'], ['GAME_OVER|garbage|Rome|VICTORY_DOMINATION|alive|Trajan'],
    ['GAME_OVER|VICTORY|Rome|VICTORY_DOMINATION|unknown|Trajan'],
    ['GAME_OVER|VICTORY|Rome|VICTORY_DOMINATION|alive|Trajan|bad'],
    ['GAME_OVER|VICTORY|Rome|VICTORY_DOMINATION|alive|Trajan', 'GAME_ACTIVE'],
    ['GAME_OVER|VICTORY|Rome|VICTORY_DOMINATION|alive|Trajan'] * 2,
])
def test_terminal_witness_must_be_unique_and_complete(rows):
    observation = observe_gameover_response(rows)
    assert not observation.complete and observation.value is None


@pytest.mark.parametrize('row', [
    UNIT.replace('2/2', 'nan/2'), UNIT.replace('2/2', '2/inf'),
    UNIT + '|0|0|0||maybe',
    UNIT + '|0|0|0||0|0||unknown',
])
def test_bad_unit_types_retain_other_entities_without_claiming_completion(row):
    observation = observe_units_response([UNIT, row])
    assert len(observation.value) == 1 and not observation.complete


@pytest.mark.parametrize('row', [
    'THREAT|UNIT_WARRIOR|1,2|100/100|unknown|RS:0|dist:1',
    'THREAT|1|Rome|UNIT_WARRIOR|1,2|100/100|CS:20|RS:0|dist:1|cs:unknown|uid:1',
    'THREAT|1|Rome|UNIT_WARRIOR|1,2|100/100|CS:20|RS:0|dist:1|cs:0|bad:1',
])
def test_bad_threat_fields_do_not_default_to_zero_or_false(row):
    assert not observe_threat_scan_response([row]).complete


@pytest.mark.parametrize('bad', [
    'PLAYER|truncated', 'DEMO|science|0|nan|0|0|0',
    'VICTORY_CONTACTS_KNOWN|unknown', 'CAPITAL|Rome|unknown',
    'VFACT|1|Rival|garbage|33.5|0|0|0|0|1|0|0|0',
])
def test_victory_completion_marker_cannot_hide_malformed_facts(bad):
    rows = ['VICTORY_CONTEXT|12|0', 'VICTORY_ENABLED_KNOWN|true',
            'VFACT|0|Rome|0|33.5|0|0|0|0|1|0|0|0', 'VICTORY_COMPLETE']
    assert observe_victory_progress_response(rows).complete
    assert not observe_victory_progress_response([*rows, bad]).complete


def test_complete_native_lua_victory_rows_keep_optional_unknown_facts():
    import asyncio
    from test_victory_lua_execution import VictoryConn
    from civ_mcp.lua.victory import build_victory_progress_query

    conn = VictoryConn()
    conn.lua.execute('Players[0]:GetStats().GetScienceVictoryPoints=function()return nil end')
    rows = asyncio.run(conn.execute_write(build_victory_progress_query()))
    observation = observe_victory_progress_response(rows)
    facts = observation.require_complete()
    assert facts.players[0].science_vp is None
    assert facts.players[0].science_vp_needed == 33.5


def test_complete_native_notification_rows_keep_summary_and_negative_location():
    import asyncio
    from test_notification_details import NotificationConn
    from civ_mcp.lua.notifications import build_notifications_query
    from civ_mcp.lua import SENTINEL

    rows = asyncio.run(NotificationConn().execute_write(build_notifications_query()))
    notification, = observe_notifications_response([row for row in rows if row != SENTINEL]).require_complete()
    assert notification.summary == '一名未相遇的城邦已被击败。'
    assert (notification.x, notification.y) == (-1, -1)

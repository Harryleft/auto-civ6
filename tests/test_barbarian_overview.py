"""Tests for the dedicated barbarian intelligence query."""

from civ_mcp import narrate
from civ_mcp.end_turn import _barbarian_attack_opportunities
from civ_mcp.lua.barbarians import (
    build_barbarian_overview_query,
    parse_barbarian_overview_response,
)
from civ_mcp.lua.models import UnitInfo


def test_barbarian_query_scans_camps_and_visible_units() -> None:
    query = build_barbarian_overview_query()

    assert 'IMPROVEMENT_BARBARIAN_CAMP' in query
    assert 'vis:IsRevealed' in query
    assert 'vis:IsVisible' in query
    assert 'BARB_CAMP|' in query
    assert 'BARB_UNIT|' in query
    assert 'print("---END---")' in query


def test_parse_barbarian_overview() -> None:
    overview = parse_barbarian_overview_response(
        [
            'BARB_CAMP|12,24|revealed|6|3',
            'BARB_UNIT|42|UNIT_WARRIOR|13,25|80/100|20|0|7|2',
            '---END---',
        ]
    )

    assert len(overview.camps) == 1
    assert overview.camps[0].distance_to_city == 6
    assert overview.camps[0].distance_to_military == 3
    assert len(overview.units) == 1
    assert overview.units[0].unit_id == 42
    assert overview.units[0].hp == 80
    assert overview.units[0].distance_to_military == 2


def test_parse_barbarian_overview_skips_malformed_lines() -> None:
    overview = parse_barbarian_overview_response(
        [
            'BARB_CAMP|not-a-coordinate|revealed|6|3',
            'BARB_UNIT|bad|UNIT_WARRIOR|13,25|80/100|20|0|7|2',
            'BARB_CAMP|5,5|visible|4|1',
        ]
    )

    assert len(overview.camps) == 1
    assert overview.camps[0].x == 5
    assert not overview.units


def test_narrate_barbarian_overview_requires_camp_clearance() -> None:
    overview = parse_barbarian_overview_response(
        [
            'BARB_CAMP|12,24|revealed|6|3',
            'BARB_UNIT|42|UNIT_WARRIOR|13,25|80/100|20|0|7|2',
        ]
    )

    text = narrate.narrate_barbarian_overview(overview)

    assert '[HIGH]' in text
    assert 'ACTION REQUIRED' in text
    assert 'get_combat_estimate' in text
    assert '(12,24)' in text


def test_barbarian_attack_opportunity_matches_visible_barbarian_only() -> None:
    overview = parse_barbarian_overview_response(
        ['BARB_UNIT|42|UNIT_WARRIOR|13,25|80/100|20|0|7|2']
    )
    units = [
        UnitInfo(
            unit_id=7,
            unit_index=7,
            name='Warrior',
            unit_type='UNIT_WARRIOR',
            x=12,
            y=25,
            moves_remaining=1,
            max_moves=2,
            health=100,
            max_health=100,
            combat_strength=20,
            targets=['13,25', '20,20'],
        )
    ]

    assert _barbarian_attack_opportunities(units, overview) == [(7, 13, 25)]

"""Tests for the tribal village (goody hut) query."""

from civ_mcp import narrate
from civ_mcp.lua.notifications import NOTIFICATION_TOOL_MAP
from civ_mcp.lua.villages import (
    build_village_overview_query,
    parse_village_overview_response,
)


def test_village_query_scans_revealed_huts() -> None:
    query = build_village_overview_query()

    assert 'IMPROVEMENT_GOODY_HUT' in query
    assert 'vis:IsRevealed' in query
    assert 'vis:IsVisible' in query
    assert 'VILLAGE|' in query
    assert 'print("---END---")' in query
    assert 'RequestOperation' not in query  # 纯读查询, 不得混入任何变异调用


def test_parse_village_overview() -> None:
    overview = parse_village_overview_response(
        [
            'VILLAGE|12,24|revealed|none|6|3',
            'VILLAGE|40,10|visible|France|12|9',
            '---END---',
        ]
    )

    assert len(overview.huts) == 2
    assert overview.huts[0].x == 12
    assert overview.huts[0].owner == "none"
    assert overview.huts[0].distance_to_city == 6
    assert overview.huts[0].distance_to_military == 3
    assert overview.huts[1].visibility == "visible"
    assert overview.huts[1].owner == "France"


def test_parse_village_overview_skips_malformed_lines() -> None:
    overview = parse_village_overview_response(
        [
            'VILLAGE|not-a-coordinate|revealed|none|6|3',
            'VILLAGE|5,5|visible|none|4|1',
        ]
    )

    assert len(overview.huts) == 1
    assert overview.huts[0].x == 5


def test_narrate_village_overview_empty_states_fog_semantics() -> None:
    overview = parse_village_overview_response([])

    text = narrate.narrate_village_overview(overview)

    assert "迷雾说明" in text


def test_narrate_village_overview_lists_priority_and_ownership() -> None:
    overview = parse_village_overview_response(['VILLAGE|12,24|revealed|none|6|3'])

    text = narrate.narrate_village_overview(overview)

    assert '[速取]' in text
    assert '(12,24)' in text
    assert '无主' in text
    assert '消失' in text  # 取用即消失 + 不保留历史的语义句必须存在


def test_goody_hut_notification_has_resolution_hint() -> None:
    assert (
        NOTIFICATION_TOOL_MAP["NOTIFICATION_DISCOVER_GOODY_HUT"]
        == "get_village_overview() (one-shot reward, grab before rivals)"
    )

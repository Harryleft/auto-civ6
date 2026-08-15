"""Offline tests for the world religion overview query."""

import asyncio

from civ_mcp import narrate
from civ_mcp.lua.religion import (
    build_religion_overview_query,
    parse_religion_overview_response,
)
from civ_mcp.server.assembly import mcp


def test_overview_query_covers_official_religion_api() -> None:
    query = build_religion_overview_query()

    assert "Game.GetReligion()" in query
    assert "HasBeenFounded" in query
    assert "GetHolyCityID" in query
    assert "HasMet" in query
    assert 'print("---END---")' in query
    # 只读契约: 不得混入任何变异调用
    assert "UI.RequestPlayerOperation" not in query
    assert "UnitManager.RequestOperation" not in query
    assert "RequestCommand" not in query


def test_overview_query_has_no_ruleset_gate() -> None:
    # 宗教是 STANDARD/R&F/GS 共有能力, 不设 ruleset 门控(与 governor 的
    # 正向断言互为镜像, 固化"共有能力不设门"的决策)。
    query = build_religion_overview_query()

    assert "GameConfiguration.GetRuleSet" not in query


def test_parse_overview_happy_path() -> None:
    overview = parse_religion_overview_response(
        [
            "SELF|0|412.0|18.0|RELIGION_CATHOLICISM|RELIGION_CATHOLICISM|BELIEF_RELIGIOUS_IDOLS|-1|2|6",
            "WREL|1|RELIGION_CATHOLICISM|Catholicism|1|France|Paris|BELIEF_GOD_KING|BELIEF_TITHE;BELIEF_PILGRIMAGE;BELIEF_WORLD_CHURCH;BELIEF_CATHEDRALS",
            "WREL|2|RELIGION_CUSTOM_RELIGION|Custom Faith|3|Unmet|unknown|None|BELIEF_FERTILITY_RITES",
            "RSPAN|RELIGION_CATHOLICISM|24|310",
            "RSPAN|RELIGION_CUSTOM_RELIGION|11|122",
            "PSTATE|0|Rome|RELIGION_CATHOLICISM|Catholicism|RELIGION_CATHOLICISM|BELIEF_RELIGIOUS_IDOLS",
            "PSTATE|2|Egypt|None|None|RELIGION_CATHOLICISM|BELIEF_RIVER_GODDESS",
            "---END---",
        ]
    )

    assert overview.player_id == 0
    assert overview.faith_balance == 412.0
    assert overview.faith_per_turn == 18.0
    assert overview.my_created_religion_type == "RELIGION_CATHOLICISM"
    assert overview.my_pantheon_belief_type == "BELIEF_RELIGIOUS_IDOLS"
    assert overview.pantheon_cost == -1
    assert overview.religions_founded == 2
    assert overview.religions_max == 6

    assert len(overview.religions) == 2
    catholicism = overview.religions[0]
    assert catholicism.religion_index == 1
    assert catholicism.religion_type == "RELIGION_CATHOLICISM"
    assert catholicism.founder_player_id == 1
    assert catholicism.founder_civ_name == "France"
    assert catholicism.holy_city_name == "Paris"
    assert catholicism.pantheon_belief_type == "BELIEF_GOD_KING"
    assert catholicism.belief_types == [
        "BELIEF_TITHE",
        "BELIEF_PILGRIMAGE",
        "BELIEF_WORLD_CHURCH",
        "BELIEF_CATHEDRALS",
    ]

    assert overview.followers == [
        ("RELIGION_CATHOLICISM", 24, 310),
        ("RELIGION_CUSTOM_RELIGION", 11, 122),
    ]

    egypt = overview.players[-1]
    assert egypt.civ_name == "Egypt"
    assert egypt.founded_religion_type is None
    assert egypt.founded_religion_name is None
    assert egypt.majority_religion_type == "RELIGION_CATHOLICISM"


def test_parse_overview_edge_cases() -> None:
    # 仅 SELF 行: 空列表 + 默认值(开局未创任何宗教)
    overview = parse_religion_overview_response(
        ["SELF|0|25.0|3.0|None|None|None|145|0|6", "---END---"]
    )
    assert overview.religions == []
    assert overview.followers == []
    assert overview.players == []
    assert overview.my_created_religion_type is None
    assert overview.pantheon_cost == 145

    # "Unmet"/"unknown" 保留字面值; 畸形行跳过不抛
    overview = parse_religion_overview_response(
        [
            "SELF|0|25.0|3.0|None|None|None|145|1|6",
            "WREL|9|RELIGION_CUSTOM_RELIGION|Custom Faith|3|Unmet|unknown|None|BELIEF_DANCE_OF_THE_AURORA",
            "WREL|bad",
            "PSTATE|not-an-int|Rome|None|None|None|None",
            "garbage",
        ]
    )
    assert len(overview.religions) == 1
    assert overview.religions[0].founder_civ_name == "Unmet"
    assert overview.religions[0].holy_city_name == "unknown"
    assert overview.religions[0].religion_type == "RELIGION_CUSTOM_RELIGION"
    assert overview.players == []


def test_narrate_overview_early_game_hint() -> None:
    overview = parse_religion_overview_response(
        ["SELF|0|25.0|3.0|None|None|None|145|0|6", "---END---"]
    )

    text = narrate.narrate_religion_overview(overview)

    assert "现在选万神殿的成本: 145" in text
    assert "已用名额 0/6" in text
    assert "get_religion_spread" in text
    assert "已创立宗教" not in text


def test_narrate_overview_lists_religions_and_players() -> None:
    overview = parse_religion_overview_response(
        [
            "SELF|0|412.0|18.0|RELIGION_CATHOLICISM|RELIGION_CATHOLICISM|BELIEF_RELIGIOUS_IDOLS|-1|2|6",
            "WREL|1|RELIGION_CATHOLICISM|Catholicism|1|France|Paris|BELIEF_GOD_KING|BELIEF_TITHE;BELIEF_PILGRIMAGE",
            "RSPAN|RELIGION_CATHOLICISM|24|310",
            "PSTATE|2|Egypt|None|None|RELIGION_CATHOLICISM|BELIEF_RIVER_GODDESS",
            "---END---",
        ]
    )

    text = narrate.narrate_religion_overview(overview)

    assert "France" in text
    assert "Paris" in text
    assert "信条 x2" in text
    assert "24 座主流城市, 310 信徒" in text
    assert "Egypt" in text
    assert "get_religion_spread" in text
    assert "get_religion_beliefs" in text


def test_religion_overview_tool_registered_read_only() -> None:
    from civ_mcp.server.tools import world

    assert hasattr(world, "get_religion_overview")

    tools = asyncio.run(mcp.list_tools())
    tool = next(t for t in tools if t.name == "get_religion_overview")
    annotations = tool.model_dump()["annotations"]
    assert annotations is not None
    assert annotations.get("readOnlyHint") is True

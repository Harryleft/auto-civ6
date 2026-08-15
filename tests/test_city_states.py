from civ_mcp.lua.governance import (
    build_city_states_query,
    parse_city_states_response,
)
from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.narrate import narrate_city_states


def _full_city_state_lines() -> list[str]:
    return [
        "TOKENS|4",
        "CS|3|Kumasi|Scientific|2|0|Rome|1|5|6|1|1|120|30|0|0",
        "CSCOMP|3|0|Rome|5",
        "CSCOMP|3|2|Egypt|4",
        "CSBONUS|3|1|One Envoy|+2 science",
        "CSBONUS|3|3|Three Envoys|+2 science in every Campus",
        "CSBONUS|3|6|Six Envoys|+4 science in every Campus",
        "CSSUZ|3|Suzerain|+2 science in every Campus",
        "CSQUEST|3|QUEST_SEND_TRADE_ROUTE|Send trade route|Send a Trade Route|Envoy|ICON_TRADE",
        "---END---",
    ]


def test_city_state_query_contains_read_only_decision_contract() -> None:
    query = build_city_states_query()

    for marker in (
        "GetTokensReceived",
        "GetMostTokensReceived",
        "CanLevyMilitary",
        "GetLevyMilitaryCost",
        "GetLevyTurnLimit",
        "GetLevyTurnCounter",
        "GetBonusText",
        "GetSuzerainBonusText",
        "HasActiveQuestFromPlayer",
        "GetActiveQuestDescription",
        "GetActiveQuestName",
        "GetActiveQuestReward",
        "CSCOMP|",
        "CSBONUS|",
        "CSSUZ|",
        "CSQUEST|",
        "pcall",
    ):
        assert marker in query
    assert "UI.RequestPlayerOperation" not in query
    assert "UnitManager.RequestOperation" not in query
    assert f'print("{SENTINEL}")' in query


def test_parse_city_state_full_contract() -> None:
    status = parse_city_states_response(_full_city_state_lines())

    assert status.tokens_available == 4
    assert len(status.city_states) == 1
    city_state = status.city_states[0]
    assert city_state.player_id == 3
    assert city_state.city_state_type == "Scientific"
    assert city_state.leading_envoys == 5
    assert city_state.suzerain_tokens_needed == 6
    assert city_state.competition_complete is True
    assert city_state.can_levy_military is True
    assert city_state.levy_cost == 120
    assert city_state.levy_turn_limit == 30
    assert city_state.levy_active is False
    assert city_state.levy_turns_remaining == 0
    assert [(item.player_id, item.envoys) for item in city_state.influence] == [
        (0, 5),
        (2, 4),
    ]
    assert [item.threshold for item in city_state.bonuses] == [1, 3, 6, 0]
    assert city_state.bonuses[-1].is_suzerain is True
    assert len(city_state.quests) == 1
    assert city_state.quests[0].quest_type == "QUEST_SEND_TRADE_ROUTE"


def test_parse_city_state_legacy_and_nil_fields_are_safe() -> None:
    status = parse_city_states_response(
        [
            "TOKENS|?",
            "CS|7|Auckland|Trade|?| -1|None|0|?|?|?|?|?|?|?|?",
            "CS|bad|ignored|row|1|-1|None|0",
            "CSCOMP|bad|0|Civ|2",
            "CSBONUS|7|?|title|details",
            "CSQUEST|7|QUEST|name|description",
        ]
    )

    assert status.tokens_available == 0
    assert len(status.city_states) == 0

    legacy = parse_city_states_response(
        ["TOKENS|2", "CS|7|Auckland|Trade|1|-1|None|1"]
    ).city_states[0]
    assert legacy.competition_complete is True
    assert legacy.leading_envoys is None
    assert legacy.can_levy_military is None
    assert legacy.bonuses == []
    assert legacy.quests == []


def test_narrate_city_state_contract_is_chinese_and_keeps_raw_types_ids() -> None:
    text = narrate_city_states(parse_city_states_response(_full_city_state_lines()))

    assert "可用使者：4" in text
    assert "Scientific" in text
    assert "ID：3" in text
    assert "1 使者奖励" in text
    assert "3 使者奖励" in text
    assert "6 使者奖励" in text
    assert "宗主国奖励" in text
    assert "活动任务" in text
    assert "QUEST_SEND_TRADE_ROUTE" in text
    assert "军事征募：可用" in text
    assert "Envoy tokens available" not in text

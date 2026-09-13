"""Offline contracts for live research state with incremental rule reuse."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from civ_mcp.lua.tech import build_tech_civics_query, parse_tech_civics_response
from civ_mcp.research_cache import ResearchCache


TECH = "TECH|采矿|TECH_MINING|25|0|5|UNBOOSTED|建造矿山|矿山||ERA_ANCIENT"
CIVIC = "CIVIC|技艺|CIVIC_CRAFTSMANSHIP|40|0|8|UNBOOSTED|改良三块地|CIVIC_CODE_OF_LAWS|ERA_ANCIENT|POLICY:POLICY_AGOGE:斯巴达教育"
LOCKED_TECH = (
    "LOCKED_TECH|炼铁术|TECH_IRON_WORKING|青铜铸造|ERA_CLASSICAL|UNBOOSTED|建造铁矿"
)
LOCKED_CIVIC = "LOCKED_CIVIC|政治哲学|CIVIC_POLITICAL_PHILOSOPHY|帝国初期,国家劳动力|ERA_CLASSICAL|UNBOOSTED|遇见城邦"


def _response(
    *rows: str, identity: str = "rules-A", current: str = "采矿|5|技艺|8"
) -> list[str]:
    return [
        f"RESEARCH_RULES|{identity}",
        f"CURRENT|{current}",
        *rows,
        "COMPLETED|1|1",
        "COMPLETED_TECH|制陶",
        "COMPLETED_CIVIC|法典",
    ]


class FakeConnection:
    def __init__(self, *responses: list[str]) -> None:
        self.execute_read = AsyncMock(side_effect=list(responses))
        self.generation = 1


def test_rules_are_reused_but_all_dynamic_values_are_current() -> None:
    async def exercise():
        initial = _response(TECH, CIVIC, LOCKED_TECH, LOCKED_CIVIC)
        updated = _response(
            "TECH_STATE|TECH_MINING|22|70|1|BOOSTED",
            "CIVIC_STATE|CIVIC_CRAFTSMANSHIP|36|50|2|BOOSTED",
            "LOCKED_TECH_STATE|TECH_IRON_WORKING|青铜铸造|BOOSTED",
            "LOCKED_CIVIC_STATE|CIVIC_POLITICAL_PHILOSOPHY|国家劳动力|BOOSTED",
            current="采矿|1|技艺|2",
        )
        conn = FakeConnection(initial, updated)
        cache = ResearchCache()
        first = await cache.read(conn, generation=(1, 0))
        second = await cache.read(conn, generation=(1, 0))

        assert first == parse_tech_civics_response(initial)
        tech = second.available_techs[0]
        assert (tech.cost, tech.progress_pct, tech.turns, tech.boosted) == (
            22,
            70,
            1,
            True,
        )
        assert (tech.name, tech.boost_desc, tech.unlocks, tech.era) == (
            "采矿",
            "建造矿山",
            "矿山",
            "ERA_ANCIENT",
        )
        civic = second.available_civics[0]
        assert (civic.cost, civic.progress_pct, civic.turns, civic.boosted) == (
            36,
            50,
            2,
            True,
        )
        assert civic.unlocks == first.available_civics[0].unlocks
        assert second.locked_civics[0].missing_prereqs == ["国家劳动力"]
        assert second.locked_civics[0].boosted is True
        assert second.locked_techs[0].boosted is True
        assert second.current_research_turns == 1
        assert second.current_civic_turns == 2
        request = conn.execute_read.call_args_list[1].args[0]
        assert '["TECH:TECH_MINING"]=true' in request
        assert 'ruleIdentity == "rules-A"' in request

    asyncio.run(exercise())


def test_unavailable_options_disappear_and_completed_sets_replace_old_sets() -> None:
    async def exercise():
        completed = [
            "RESEARCH_RULES|rules-A",
            "CURRENT|None|-1|None|-1",
            "COMPLETED|2|0",
            "COMPLETED_TECH|采矿",
            "COMPLETED_TECH|航海术",
        ]
        conn = FakeConnection(_response(TECH, CIVIC, LOCKED_TECH), completed)
        cache = ResearchCache()
        await cache.read(conn, generation=1)
        result = await cache.read(conn, generation=1)
        assert result.available_techs == []
        assert result.available_civics == []
        assert result.locked_techs is None
        assert result.completed_techs == ["采矿", "航海术"]
        assert result.completed_civics == []
        assert result.completed_tech_count == 2
        assert result.completed_civic_count == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("reset", ["clear", "epoch", "connection"])
def test_lifecycle_change_does_not_offer_old_rules(reset: str) -> None:
    async def exercise():
        conn = FakeConnection(_response(TECH), _response(TECH))
        cache = ResearchCache()
        await cache.read(conn, generation=(1, 0))
        generation = (1, 0)
        if reset == "clear":
            cache.clear()
        elif reset == "epoch":
            generation = (1, 1)
        else:
            conn = FakeConnection(_response(TECH))
        await cache.read(conn, generation=generation)
        assert '["TECH:TECH_MINING"]=true' not in conn.execute_read.call_args.args[0]

    asyncio.run(exercise())


def test_live_rule_identity_change_replaces_cached_descriptions() -> None:
    async def exercise():
        modified = TECH.replace("矿山||", "新规则矿山||")
        conn = FakeConnection(
            _response(TECH),
            _response(modified, identity="rules-B"),
            _response("TECH_STATE|TECH_MINING|25|0|5|UNBOOSTED", identity="rules-B"),
        )
        cache = ResearchCache()
        await cache.read(conn, generation=1)
        await cache.read(conn, generation=1)
        result = await cache.read(conn, generation=1)
        assert result.available_techs[0].unlocks == "新规则矿山"
        assert 'ruleIdentity == "rules-B"' in conn.execute_read.call_args.args[0]

    asyncio.run(exercise())


@pytest.mark.parametrize("generation,identity", [(None, "rules-A"), (1, "")])
def test_unknown_lifecycle_or_shuffle_configuration_bypasses_cache(
    generation, identity
) -> None:
    async def exercise():
        rows = _response(TECH, identity=identity)
        conn = FakeConnection(rows, rows)
        cache = ResearchCache()
        await cache.read(conn, generation=generation)
        await cache.read(conn, generation=generation)
        assert '["TECH:TECH_MINING"]=true' not in conn.execute_read.call_args.args[0]

    asyncio.run(exercise())


def test_unknown_compact_rule_retries_full_query_without_stale_merge() -> None:
    async def exercise():
        conn = FakeConnection(
            _response(TECH),
            _response("TECH_STATE|TECH_UNKNOWN|5|0|1|BOOSTED"),
            _response(CIVIC),
        )
        cache = ResearchCache()
        await cache.read(conn, generation=1)
        result = await cache.read(conn, generation=1)
        assert result.available_techs == []
        assert result.available_civics[0].civic_type == "CIVIC_CRAFTSMANSHIP"
        assert conn.execute_read.call_count == 3
        assert conn.execute_read.call_args.args[0] == build_tech_civics_query()

    asyncio.run(exercise())


def test_identity_mismatch_cannot_rehydrate_compact_response() -> None:
    async def exercise():
        conn = FakeConnection(
            _response(TECH),
            _response("TECH_STATE|TECH_MINING|5|0|1|BOOSTED", identity="rules-B"),
            _response(CIVIC, identity="rules-B"),
        )
        cache = ResearchCache()
        await cache.read(conn, generation=1)
        result = await cache.read(conn, generation=1)
        assert result.available_techs == []
        assert conn.execute_read.call_count == 3

    asyncio.run(exercise())


def test_broken_fallback_is_reported_instead_of_returning_partial_options() -> None:
    async def exercise():
        compact = _response("TECH_STATE|TECH_UNKNOWN|5|0|1|BOOSTED")
        conn = FakeConnection(compact, compact)
        with pytest.raises(ValueError):
            await ResearchCache().read(conn, generation=1)
        assert conn.execute_read.call_count == 2

    asyncio.run(exercise())


def test_old_wire_records_keep_default_dto_compatibility() -> None:
    async def exercise():
        legacy = [
            "CURRENT|None|-1|None|-1",
            "TECH|采矿|TECH_MINING",
            "CIVIC|法典|CIVIC_CODE_OF_LAWS",
            "COMPLETED|0|0",
        ]
        conn = FakeConnection(legacy, legacy)
        cache = ResearchCache()
        expected = parse_tech_civics_response(legacy)
        assert await cache.read(conn, generation=1) == expected
        assert await cache.read(conn, generation=1) == expected
        assert '["TECH:TECH_MINING"]=true' not in conn.execute_read.call_args.args[0]

    asyncio.run(exercise())


def test_clear_during_read_cannot_repopulate_abandoned_epoch() -> None:
    async def exercise():
        cache = ResearchCache()
        conn = FakeConnection()

        async def read_and_clear(query):
            cache.clear()
            return _response(TECH)

        conn.execute_read.side_effect = read_and_clear
        with pytest.raises(RuntimeError):
            await cache.read(conn, generation=1)
        conn.execute_read.side_effect = [_response(TECH)]
        await cache.read(conn, generation=2)
        assert '["TECH:TECH_MINING"]=true' not in conn.execute_read.call_args.args[0]

    asyncio.run(exercise())


def test_reconnect_inside_query_forces_a_full_read() -> None:
    async def exercise():
        cache = ResearchCache()
        conn = FakeConnection(_response(TECH))
        await cache.read(conn, generation=(1, 0))
        reads = 0

        async def read_with_reconnect(query):
            nonlocal reads
            reads += 1
            conn.generation = 2
            return (
                _response("TECH_STATE|TECH_MINING|5|0|1|BOOSTED")
                if reads == 1
                else _response(CIVIC)
            )

        conn.execute_read.side_effect = read_with_reconnect
        result = await cache.read(conn, generation=(1, 0))
        assert reads == 2
        assert result.available_techs == []
        assert conn.execute_read.call_args.args[0] == build_tech_civics_query()

    asyncio.run(exercise())


def test_lua_rule_reuse_is_guarded_and_does_not_read_full_hidden_tree() -> None:
    query = build_tech_civics_query(
        cache_identity="rules-A", known_rules=["TECH:TECH_MINING"]
    )
    assert 'GameConfiguration.GetValue("GAMEMODE_TREE_RANDOMIZER")' in query
    assert "shuffle == false or shuffle == 0" in query
    assert "ruleIdentity ~= nil and ruleIdentity ==" in query
    assert "te:CanResearch(tech.Index) and not te:HasTech(tech.Index)" in query
    assert "local boostTag = boosted" in query
    assert query.index('if hasCachedRule("TECH"') < query.index(
        "for u in GameInfo.Units()"
    )
    assert query.endswith('print("---END---")\n')


def test_rule_identifiers_are_quoted_as_data() -> None:
    query = build_tech_civics_query(
        cache_identity='x"\nerror("oops")', known_rules=['TECH:x"\nerror("oops")']
    )
    assert "\\034" not in query  # quotes use Lua's ordinary escaped-quote form
    assert '\\010error(\\"oops\\")' in query

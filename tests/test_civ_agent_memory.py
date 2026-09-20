"""M06 / M07 / M08：Observation 组合、Game Memory 读写、全文检索。

这些测试只覆盖新 ``civ_agent`` 包，不触碰旧认知栈。所有游戏事实都用
``SimpleNamespace`` 伪造，与 ``tests/test_runtime_context.py`` 的约定一致。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from civ_agent.memory import GameMemoryWriter, GameStart, TurnRecord, memory_dir, search_memory
from civ_agent.observation import UNKNOWN, Observation, OpponentState, OurState, build_observation, diff
from civ_mcp.runtime.context import RuntimeContext

# ---------------------------------------------------------------------------
# 伪造 Runtime 事实
# ---------------------------------------------------------------------------


def _fact(value: Any, *, coverage: str = "TEST:COMPLETE") -> SimpleNamespace:
    """模拟 ``CivReadResult``：只有 ``.value`` / ``.coverage`` 被组合读取。"""

    return SimpleNamespace(value=value, coverage=coverage)


def _overview(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "turn": 7,
        "player_id": 0,
        "civ_name": "巴比伦",
        "leader_name": "汉谟拉比",
        "difficulty": "Emperor",
        "num_cities": 3,
        "total_population": 9,
        "gold": 120.0,
        "gold_per_turn": 8.5,
        "science_yield": 14.2,
        "culture_yield": 9.0,
        "faith": 0.0,
        "score": 88,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _context(**facts: Any) -> RuntimeContext:
    """缺省给一份最小可用的 facts，其余按需覆盖。"""

    base: dict[str, Any] = {
        "overview": _fact(_overview()),
        "cities": _fact([]),
        "units": _fact([]),
        "diplomacy": _fact([]),
        "tech_civics": _fact(
            SimpleNamespace(
                current_research="Pottery",
                current_research_type="TECH_POTTERY",
                current_civic="Code of Laws",
                current_civic_type="CIVIC_CODE_OF_LAWS",
            )
        ),
        "governments": _fact([]),
        "victory_progress": _fact([]),
    }
    base.update(facts)
    return RuntimeContext(
        facts=base,
        unknown=(),
        pending_decisions=(),
        unfinished_intents=(),
        handoff=None,
        further_queries=(),
    )


# ---------------------------------------------------------------------------
# M06 Observation
# ---------------------------------------------------------------------------


def test_observation_uses_stable_tech_and_civic_ids() -> None:
    observation = build_observation(_context())

    assert observation.turn == 7
    assert observation.our_state.current_research == "TECH_POTTERY"
    assert observation.our_state.current_civic == "CIVIC_CODE_OF_LAWS"


def test_observation_falls_back_to_localized_name_without_type_id() -> None:
    context = _context(
        tech_civics=_fact(
            SimpleNamespace(
                current_research="陶器",
                current_research_type="",
                current_civic="Code of Laws",
                current_civic_type="",
            )
        )
    )

    observation = build_observation(context)

    assert observation.our_state.current_research == "陶器"
    assert observation.our_state.current_civic == "Code of Laws"


def test_observation_marks_absent_selection_as_unknown_not_empty() -> None:
    context = _context(
        tech_civics=_fact(
            SimpleNamespace(
                current_research="NONE",
                current_research_type="",
                current_civic="",
                current_civic_type="",
            )
        )
    )

    observation = build_observation(context)

    assert observation.our_state.current_research == UNKNOWN
    assert observation.our_state.current_civic == UNKNOWN


def test_observation_sums_city_population_when_city_detail_reads() -> None:
    context = _context(
        cities=_fact(
            [
                SimpleNamespace(population=4),
                SimpleNamespace(population=3),
            ]
        )
    )

    observation = build_observation(context)

    assert observation.our_state.total_population == 7


def test_observation_falls_back_to_overview_population_without_city_detail() -> None:
    observation = build_observation(_context(cities=_fact([])))

    assert observation.our_state.total_population == 9


def test_observation_does_not_invent_military_strength() -> None:
    """概览没有我方军力；胜利进度也读不到时必须写 unknown，不能写 0。"""

    observation = build_observation(_context(victory_progress=_fact([])))

    assert observation.our_state.military_strength == UNKNOWN


def test_observation_reads_military_strength_from_our_victory_progress() -> None:
    context = _context(
        victory_progress=_fact(
            [SimpleNamespace(player_id=0, score=88, military_strength=210)]
        )
    )

    observation = build_observation(context)

    assert observation.our_state.military_strength == 210


def test_observation_omits_unmet_civilizations_entirely() -> None:
    """v7 §10：未遇到 → 不写该文明。"""

    context = _context(
        diplomacy=_fact(
            [
                SimpleNamespace(
                    player_id=1,
                    civ_name="罗马",
                    leader_name="图拉真",
                    has_met=False,
                    is_at_war=False,
                ),
                SimpleNamespace(
                    player_id=2,
                    civ_name="埃及",
                    leader_name="克利奥帕特拉",
                    has_met=True,
                    is_at_war=False,
                    diplomatic_state="FRIENDLY",
                    num_cities=2,
                    military_strength=90,
                ),
            ]
        )
    )

    observation = build_observation(context)

    assert [opponent.civilization for opponent in observation.opponents] == ["埃及"]


def test_observation_records_unknown_metrics_for_met_civilization() -> None:
    """v7 §10：遇到但不知道具体数值 → unknown。"""

    context = _context(
        diplomacy=_fact(
            [
                SimpleNamespace(
                    player_id=2,
                    civ_name="埃及",
                    leader_name="克利奥帕特拉",
                    has_met=True,
                    is_at_war=True,
                    diplomatic_state="UNFRIENDLY",
                )
            ]
        ),
        victory_progress=_fact([]),
    )

    observation = build_observation(context)

    opponent = observation.opponents[0]
    assert opponent.science == UNKNOWN
    assert opponent.culture == UNKNOWN
    assert opponent.is_at_war is True
    assert observation.our_state.at_war_with == ("埃及",)


def test_observation_keeps_coverage_so_unknown_is_distinguishable() -> None:
    context = _context(
        diplomacy=_fact([], coverage="MET_CIVILIZATIONS:COMPLETE;UNMET_CIVILIZATIONS:UNOBSERVED")
    )
    context = RuntimeContext(
        facts=context.facts,
        unknown=("units: TimeoutError",),
        pending_decisions=(),
        unfinished_intents=(),
        handoff=None,
        further_queries=(),
    )

    observation = build_observation(context)

    assert observation.unknown == ("units: TimeoutError",)
    assert (
        observation.coverage["diplomacy"]
        == "MET_CIVILIZATIONS:COMPLETE;UNMET_CIVILIZATIONS:UNOBSERVED"
    )


def test_observation_requires_overview() -> None:
    context = _context()
    del context.facts["overview"]

    with pytest.raises(ValueError, match="overview"):
        build_observation(context)


def test_diff_reports_no_change_when_both_sides_are_unknown() -> None:
    previous = build_observation(_context(victory_progress=_fact([])))
    current = build_observation(_context(victory_progress=_fact([])))

    assert diff(previous, current).important_changes == ()


def test_diff_reports_real_field_change() -> None:
    previous = build_observation(_context())
    current = build_observation(_context(overview=_fact(_overview(gold=140.0, turn=8))))

    changes = diff(previous, current).important_changes

    assert "gold: 120.0 → 140.0" in changes


def test_diff_reports_newly_met_civilization() -> None:
    previous = build_observation(_context())
    current = build_observation(
        _context(
            diplomacy=_fact(
                [
                    SimpleNamespace(
                        player_id=2,
                        civ_name="埃及",
                        leader_name="克利奥帕特拉",
                        has_met=True,
                        is_at_war=False,
                    )
                ]
            )
        )
    )

    changes = diff(previous, current).important_changes

    assert changes == ("新遇到文明 埃及 (player 2)",)


def test_diff_reports_opponent_metric_change() -> None:
    def _with_science(science: float) -> Observation:
        return build_observation(
            _context(
                diplomacy=_fact(
                    [
                        SimpleNamespace(
                            player_id=2,
                            civ_name="埃及",
                            leader_name="克利奥帕特拉",
                            has_met=True,
                            is_at_war=False,
                        )
                    ]
                ),
                victory_progress=_fact(
                    [
                        SimpleNamespace(
                            player_id=2,
                            score=40,
                            science_yield=science,
                            culture_yield=5.0,
                            military_strength=90,
                            num_cities=2,
                        )
                    ]
                ),
            )
        )

    changes = diff(_with_science(10.0), _with_science(12.5)).important_changes

    assert "埃及.science: 10.0 → 12.5" in changes


# ---------------------------------------------------------------------------
# M07 Game Memory Writer
# ---------------------------------------------------------------------------


def _our_state(**overrides: Any) -> OurState:
    fields: dict[str, Any] = {
        "civilization": "巴比伦",
        "leader": "汉谟拉比",
        "cities": 1,
        "total_population": 1,
        "gold": 0.0,
        "gold_per_turn": 1.0,
        "science": 2.5,
        "culture": 1.0,
        "faith": 0.0,
        "military_strength": UNKNOWN,
        "current_research": "TECH_POTTERY",
        "current_civic": UNKNOWN,
        "government": UNKNOWN,
        "at_war_with": (),
        "score": 12,
    }
    fields.update(overrides)
    return OurState(**fields)


def test_create_game_uses_timestamp_filename(tmp_path: Path) -> None:
    writer = GameMemoryWriter(tmp_path)

    path = writer.create_game(
        GameStart(benchmark_save="benchmark_start.Civ6Save", civilization="巴比伦", leader="汉谟拉比"),
        now=datetime(2026, 9, 20, 8, 35, 0),
    )

    assert path.name == "game_20260920_083500.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Game game_20260920_083500\n")
    assert "| **基准存档** | benchmark_start.Civ6Save |" in text
    assert "| **最终结果** | unknown |" in text


def test_create_game_does_not_overwrite_same_second_run(tmp_path: Path) -> None:
    writer = GameMemoryWriter(tmp_path)
    moment = datetime(2026, 9, 20, 8, 35, 0)

    first = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"), now=moment)
    second = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"), now=moment)

    assert first != second
    assert second.name == "game_20260920_083500_1.md"
    assert first.read_text(encoding="utf-8") != ""
    assert second.read_text(encoding="utf-8") != ""


def test_append_turn_writes_every_required_section(tmp_path: Path) -> None:
    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))

    writer.append_turn(
        path,
        TurnRecord(
            turn=1,
            our_state=_our_state(),
            opponents=(
                OpponentState(player_id=2, civilization="埃及", known_cities=1, science=UNKNOWN),
            ),
            important_changes=("gold: 0.0 → 5.0",),
            memory_used=("Game game_20260901_120000 / Turn 30：早战压制有效",),
            jev_assess=("扩张优先",),
            deepseek_summary=("先出投石手",),
            rule_queries=({"query": "蛮族营地刷新", "result": "距离城市 7 格内不刷新"},),
            candidates=("建立首都", "训练投石手"),
            jev_review=("无冲突",),
            final_action=("训练投石手",),
            execution=("CONFIRMED operation_id=op-1",),
            consequences=("下回合可探索"),
            planning="三城开局",
        ),
    )

    text = path.read_text(encoding="utf-8")
    assert "## Turn 1" in text
    for heading in (
        "### 我方基本信息",
        "### 对手基本信息",
        "### 本回合重要变化",
        "### 历史经验",
        "### Jev Assess",
        "### DeepSeek 决策摘要",
        "### Rule Search",
        "### 候选行动",
        "### Jev Review",
        "### 最终行动",
        "### Runtime 执行结果",
        "### 本回合后果",
    ):
        assert heading in text, heading
    assert "#### 埃及" in text
    assert "| 科技值 | unknown |" in text
    assert "1. 建立首都" in text
    assert "三城开局" in text


def test_append_turn_records_no_opponents_and_no_rule_search_explicitly(tmp_path: Path) -> None:
    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))

    writer.append_turn(path, TurnRecord(turn=1, our_state=_our_state()))

    text = path.read_text(encoding="utf-8")
    assert "> 未遇到任何其他文明，本回合不记录对手。" in text
    assert "无\n" in text
    assert "- （无）" in text


def test_append_turn_escapes_table_separator(tmp_path: Path) -> None:
    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))

    writer.append_turn(path, TurnRecord(turn=1, our_state=_our_state(civilization="A|B")))

    assert "| 文明 | A\\|B |" in path.read_text(encoding="utf-8")


def test_append_turn_keeps_turns_in_order_and_adds_one_file_per_game(tmp_path: Path) -> None:
    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))

    writer.append_turn(path, TurnRecord(turn=1, our_state=_our_state()))
    writer.append_turn(path, TurnRecord(turn=2, our_state=_our_state(cities=2)))

    text = path.read_text(encoding="utf-8")
    assert text.index("## Turn 1") < text.index("## Turn 2")
    assert len(list(tmp_path.glob("*.md"))) == 1


def test_append_turn_rejects_missing_file(tmp_path: Path) -> None:
    writer = GameMemoryWriter(tmp_path)

    with pytest.raises(FileNotFoundError):
        writer.append_turn(tmp_path / "game_20260920_083500.md", TurnRecord(turn=1, our_state=_our_state()))


def test_turn_record_rejects_invalid_turn() -> None:
    with pytest.raises(ValueError, match="turn"):
        TurnRecord(turn=0, our_state=_our_state())


def test_finish_game_fills_result_without_touching_turns(tmp_path: Path) -> None:
    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))
    writer.append_turn(path, TurnRecord(turn=1, our_state=_our_state(), planning="三城开局"))
    before = path.read_text(encoding="utf-8")

    writer.finish_game(path, result="科技胜利", end_turn=312)

    text = path.read_text(encoding="utf-8")
    assert "| **最终结果** | 科技胜利 |" in text
    assert "| **结束回合** | 312 |" in text
    assert "## Turn 1" in text
    assert "三城开局" in text
    # 回合小节必须逐字不变：finish_game 只回填元信息表。
    assert before.split("## Turn 1", 1)[1] == text.split("## Turn 1", 1)[1]


def test_finish_game_rejects_invalid_end_turn(tmp_path: Path) -> None:
    writer = GameMemoryWriter(tmp_path)
    path = writer.create_game(GameStart(benchmark_save="benchmark_start.Civ6Save"))

    with pytest.raises(ValueError, match="end_turn"):
        writer.finish_game(path, result="科技胜利", end_turn=0)


def test_memory_dir_prefers_explicit_base(tmp_path: Path) -> None:
    assert memory_dir(tmp_path) == tmp_path


def test_memory_dir_uses_environment_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIV6_AGENT_MEMORY_DIR", str(tmp_path / "games"))

    assert memory_dir() == tmp_path / "games"


def test_memory_dir_defaults_to_repo_relative_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CIV6_AGENT_MEMORY_DIR", raising=False)

    assert memory_dir() == Path("memory") / "games"


# ---------------------------------------------------------------------------
# M08 Memory Search
# ---------------------------------------------------------------------------


def _seed_memory(tmp_path: Path, *, game_id: str, turns: dict[int, str]) -> None:
    writer = GameMemoryWriter(tmp_path)
    moment = datetime.strptime(game_id.removeprefix("game_"), "%Y%m%d_%H%M%S")
    path = writer.create_game(
        GameStart(benchmark_save="benchmark_start.Civ6Save"), now=moment
    )
    for turn, planning in turns.items():
        writer.append_turn(
            path,
            TurnRecord(
                turn=turn,
                our_state=_our_state(),
                jev_assess=(planning,),
                planning=planning,
            ),
        )


def test_search_memory_finds_matching_turn_section(tmp_path: Path) -> None:
    _seed_memory(tmp_path, game_id="game_20260901_120000", turns={1: "先出投石手", 30: "转向早战压制"})

    hits = search_memory("早战", directory=tmp_path)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.game_id == "game_20260901_120000"
    assert hit.turn == 30
    assert hit.source_file == "game_20260901_120000.md"
    assert "早战" in hit.excerpt


def test_search_memory_matches_metadata_section_without_turn(tmp_path: Path) -> None:
    _seed_memory(tmp_path, game_id="game_20260901_120000", turns={1: "先出投石手"})

    hits = search_memory("基准存档", directory=tmp_path)

    assert hits
    assert hits[0].turn is None
    assert hits[0].section == "游戏基本信息"


def test_search_memory_ranks_more_matches_first(tmp_path: Path) -> None:
    _seed_memory(tmp_path, game_id="game_20260901_120000", turns={1: "投石手", 2: "投石手 投石手 投石手"})

    hits = search_memory("投石手", directory=tmp_path)

    assert [hit.turn for hit in hits] == [2, 1]


def test_search_memory_respects_limit(tmp_path: Path) -> None:
    _seed_memory(
        tmp_path,
        game_id="game_20260901_120000",
        turns={turn: "投石手" for turn in range(1, 8)},
    )

    assert len(search_memory("投石手", limit=3, directory=tmp_path)) == 3


def test_search_memory_rejects_invalid_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limit"):
        search_memory("投石手", limit=0, directory=tmp_path)


def test_search_memory_returns_empty_for_blank_query(tmp_path: Path) -> None:
    _seed_memory(tmp_path, game_id="game_20260901_120000", turns={1: "投石手"})

    assert search_memory("   ", directory=tmp_path) == ()


def test_search_memory_returns_empty_for_missing_directory(tmp_path: Path) -> None:
    assert search_memory("投石手", directory=tmp_path / "nope") == ()


def test_search_memory_truncates_long_excerpt(tmp_path: Path) -> None:
    _seed_memory(
        tmp_path,
        game_id="game_20260901_120000",
        turns={1: "x" * 2000 + "投石手" + "y" * 2000},
    )

    hit = search_memory("投石手", directory=tmp_path)[0]

    assert len(hit.excerpt) < 400
    assert "投石手" in hit.excerpt

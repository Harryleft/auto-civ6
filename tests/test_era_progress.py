"""Narrate, registration, and gating-identity tests for era progress."""

import asyncio

from civ6_belief_engine.governance.capabilities import capability_enabled
from civ6_belief_engine.governance.capabilities import normalize_ruleset
from civ_mcp import narrate
from civ_mcp.lua.eras import parse_era_progress_response
from civ_mcp.lua.models import EraAgeDetail, EraProgress
from civ_mcp.server.assembly import mcp


def _standard_progress() -> EraProgress:
    return parse_era_progress_response(
        [
            "RULESET|RULESET_STANDARD",
            "LOCAL|0",
            "ERAS|idx0|ERA_ANCIENT|Ancient",
            "ERAS|idx1|ERA_CLASSICAL|Classical",
            "GAMEERA|1|ERA_CLASSICAL|Classical|false",
            "PERA|0|Rome|1|ERA_CLASSICAL|Classical|-|-1",
            "PERA|1|Egypt|1|ERA_CLASSICAL|Classical|-|-1",
            "---END---",
        ]
    )


def test_narrate_standard_reports_age_block_unavailable() -> None:
    text = narrate.narrate_era_progress(_standard_progress())

    assert "not available in RULESET_STANDARD" in text
    assert "Era Score" not in text
    assert "GOLDEN AGE" not in text
    assert "countdown" not in text


def _xp2_progress(age: EraAgeDetail) -> EraProgress:
    progress = parse_era_progress_response(
        [
            "RULESET|RULESET_EXPANSION_2",
            "LOCAL|0",
            "ERAS|idx0|ERA_ANCIENT|Ancient",
            "ERAS|idx1|ERA_CLASSICAL|Classical",
            "GAMEERA|1|ERA_CLASSICAL|Classical|false",
            "CLOCK|21|7|40|60|1|2",
            "PERA|0|Rome|1|ERA_CLASSICAL|Classical|Normal|15",
            "PERA|1|Egypt|1|ERA_CLASSICAL|Classical|Normal|9",
            "---END---",
        ]
    )
    progress.local_age = age
    return progress


def test_narrate_xp2_projects_golden_age() -> None:
    progress = _xp2_progress(
        EraAgeDetail(era_score=24, dark_threshold=12, golden_threshold=24,
                     threshold_baseline=0, previous_era_score=5)
    )

    text = narrate.narrate_era_progress(progress)

    assert "-> GOLDEN AGE projected" in text
    assert "countdown 7" in text


def test_narrate_xp2_warns_dark_age_deficit() -> None:
    progress = _xp2_progress(
        EraAgeDetail(era_score=9, dark_threshold=12, golden_threshold=24,
                     threshold_baseline=0, previous_era_score=5)
    )

    text = narrate.narrate_era_progress(progress)

    assert "!! 3 short of avoiding Dark Age" in text


def test_narrate_xp2_empty_breakdown_and_dramatic_ages() -> None:
    normal = narrate.narrate_era_progress(
        _xp2_progress(
            EraAgeDetail(era_score=15, dark_threshold=12, golden_threshold=24,
                         threshold_baseline=0, previous_era_score=5)
        )
    )
    assert "-> NORMAL AGE projected" in normal
    assert "(no score sources yet)" in normal

    dramatic = narrate.narrate_era_progress(
        _xp2_progress(
            EraAgeDetail(era_score=15, dark_threshold=24, golden_threshold=24,
                         threshold_baseline=0, previous_era_score=5)
        )
    )
    assert "Dramatic Ages mode threshold detected" in dramatic


def test_era_progress_tool_registered_read_only() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool = next(t for t in tools if t.name == "get_era_progress")
    annotations = tool.model_dump()["annotations"]
    assert annotations is not None
    assert annotations.get("readOnlyHint") is True


def test_ages_supported_matches_capability_enabled() -> None:
    for ruleset in ("RULESET_STANDARD", "RULESET_EXPANSION_1", "RULESET_EXPANSION_2"):
        lines = [
            f"RULESET|{ruleset}",
            "LOCAL|0",
            "GAMEERA|1|ERA_CLASSICAL|Classical|false",
            "PERA|0|Rome|1|ERA_CLASSICAL|Classical|-|-1",
        ]
        progress = parse_era_progress_response(lines)
        assert progress.ages_supported == capability_enabled(ruleset, "ages")

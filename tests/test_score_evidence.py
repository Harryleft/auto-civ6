"""Native score facts remain distinct from their diagnostic category sum."""

import asyncio
import json

import pytest

from civ_mcp import checkpoints, game_launcher
from civ_mcp.game_state import GameState
from civ_mcp.lua.overview import parse_overview_response
from civ_mcp.narrate import narrate_overview, narrate_score_comparison


MAIN = "50|0|Sumeria|Gilgamesh|136|4|5.5|5.5|13|Wheel|None|1|7|72"
CATEGORIES = [
    "SCORECATEGORY|CATEGORY_CIVICS|Civics|3|21",
    "SCORECATEGORY|CATEGORY_EMPIRE|Empire|1|18",
    "SCORECATEGORY|CATEGORY_GREAT_PEOPLE|Great People|1|0",
    "SCORECATEGORY|CATEGORY_RELIGION|Religion|1|0",
    "SCORECATEGORY|CATEGORY_TECH|Technology|1|16",
    "SCORECATEGORY|CATEGORY_WONDER|Wonders|1|0",
    "SCORECATEGORY|CATEGORY_ERA_SCORE|Era Score|1|21",
    "SCORECATEGORIES|7|50|0",
]


def test_overview_explains_native_score_mismatch_without_replacing_official_total():
    overview = parse_overview_response([MAIN, *CATEGORIES])
    text = narrate_overview(overview)
    assert overview.score == 72
    assert overview.score_category_total == 76
    assert "Score: 72" in text
    assert "GetCategoryScore" in text
    assert "Civics [CATEGORY_CIVICS]: 21" in text
    assert "category sum: 76" in text
    assert "differs from official GetScore: 72" in text


@pytest.mark.parametrize("categories", [
    [],
    CATEGORIES[:-1],
    CATEGORIES[1:],
    [*CATEGORIES, CATEGORIES[0]],
    [*CATEGORIES, CATEGORIES[-1]],
    [*CATEGORIES[:-1], "SCORECATEGORIES|unknown|50|0"],
    [*CATEGORIES[:-1], "SCORECATEGORIES|7|49|0"],
    [*CATEGORIES[:-1], "SCORECATEGORIES|7|50|1"],
    ["SCORECATEGORY| |Civics|1|21", *CATEGORIES[1:]],
    ["SCORECATEGORY|CATEGORY_CIVICS|Civics|0|21", *CATEGORIES[1:]],
    ["SCORECATEGORY|CATEGORY_CIVICS|Civics|1|nil", *CATEGORIES[1:]],
    ["SCORECATEGORY|CATEGORY_CIVICS|Civics|1|-3", *CATEGORIES[1:]],
])
def test_incomplete_or_invalid_categories_remain_unknown(categories):
    overview = parse_overview_response([MAIN, *categories])
    assert overview.score == 72
    assert overview.score_category_total is None
    text = narrate_overview(overview)
    assert "Score categories (GetCategoryScore): unknown" in text
    assert "category sum:" not in text


def test_checkpoint_score_evidence_is_bound_to_save_bytes_and_current_run(monkeypatch, tmp_path):
    monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "records")
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))
    assert checkpoints.begin_baseline_run() is None
    saved_file = tmp_path / "result.Civ6Save"
    saved_file.write_bytes(b"confirmed completed save")
    checkpoints.mark_complete("result")
    overview = parse_overview_response([MAIN, *CATEGORIES])
    evidence = checkpoints.score_evidence(overview)
    assert checkpoints.record_checkpoint("result", ("sumeria", 719665280), 50, evidence) is None
    saved = checkpoints.expected_score("result")
    assert saved["official"] == 72
    assert saved["categories"]["CATEGORY_CIVICS"] == 21
    assert saved["categories"]["CATEGORY_ERA_SCORE"] == 21
    saved_file.write_bytes(b"different save bytes")
    assert checkpoints.expected_score("result") is None
    saved_file.write_bytes(b"confirmed completed save")
    assert checkpoints.expected_score("result") == saved
    assert checkpoints.begin_baseline_run() is None
    assert checkpoints.expected_score("result") is None


def test_save_game_records_existing_presave_overview_without_an_extra_query(monkeypatch, tmp_path):
    monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "records")
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))
    assert checkpoints.begin_baseline_run() is None

    class FakeConn:
        overview_reads = 0

        async def execute_write(self, code, timeout=None):
            if "GAMESEED|" in code:
                return ["GAMESEED|CIVILIZATION_SUMERIA|719665280"]
            if "local tr = p:GetTreasury()" in code:
                self.overview_reads += 1
                return [MAIN if self.overview_reads == 1 else MAIN.replace("|7|72", "|7|76"), *CATEGORIES]
            if "Network.SaveGame" in code:
                (tmp_path / "result.Civ6Save").write_bytes(b"native complete save")
                return ["MCP_SAVE|true"]
            if "MCP_SAVE_COMPLETION_STATUS" in code:
                return ["MCP_SAVE_COMPLETE|true"]
            return []

    conn = FakeConn()
    result = asyncio.run(GameState(conn).save_game("result"))
    assert result.startswith("Saved: result;")
    assert checkpoints.expected_score("result") == {
        "official": 72,
        "categories": {
            "CATEGORY_CIVICS": 21, "CATEGORY_EMPIRE": 18, "CATEGORY_GREAT_PEOPLE": 0,
            "CATEGORY_RELIGION": 0, "CATEGORY_TECH": 16, "CATEGORY_WONDER": 0,
            "CATEGORY_ERA_SCORE": 21,
        },
    }
    assert conn.overview_reads == 2


def test_checkpoint_score_requires_a_valid_identity_record(monkeypatch, tmp_path):
    monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "records")
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))
    assert checkpoints.begin_baseline_run() is None
    (tmp_path / "result.Civ6Save").write_bytes(b"confirmed save")
    checkpoints.mark_complete("result")
    evidence = checkpoints.score_evidence(parse_overview_response([MAIN, *CATEGORIES]))
    assert checkpoints.record_checkpoint("result", ("sumeria", 719665280), 50, evidence) is None
    path = next(path for path in (tmp_path / "records").glob("*.json") if path.name != "current-run.json")
    record = json.loads(path.read_text())
    del record["civilization"]
    path.write_text(json.dumps(record))
    assert checkpoints.expected_checkpoint("result") is None
    assert checkpoints.expected_score("result") is None


def test_same_total_does_not_hide_native_category_changes():
    before = checkpoints.score_evidence(parse_overview_response([MAIN, *CATEGORIES]))
    changed = [line.replace("|Civics|3|21", "|Civics|3|24").replace("|Empire|1|18", "|Empire|1|15") for line in CATEGORIES]
    after = checkpoints.score_evidence(parse_overview_response([MAIN, *changed]))
    text = narrate_score_comparison(before, after)
    assert "official GetScore: 72 -> 72" in text
    assert "categories changed" in text
    assert "CATEGORY_CIVICS: 21 -> 24" in text
    assert "CATEGORY_EMPIRE: 18 -> 15" in text
    assert "categories unchanged" not in text


@pytest.mark.parametrize("missing_before", [True, False])
def test_missing_native_categories_cannot_be_reported_unchanged(missing_before):
    complete = checkpoints.score_evidence(parse_overview_response([MAIN, *CATEGORIES]))
    unknown = checkpoints.score_evidence(parse_overview_response([MAIN]))
    before, after = (unknown, complete) if missing_before else (complete, unknown)
    text = narrate_score_comparison(before, after)
    assert "category comparison unknown" in text
    assert "categories unchanged" not in text

"""Unit tests for pure helper functions in convex_sync.py."""

import asyncio
import json
import sys
from pathlib import Path

# convex_sync.py is a standalone script, not a package — add scripts/ to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from convex_sync import (
    _belief_batch_rows,
    _chunk_map_frames,
    _download_cloud_run,
    _extract_outcome,
    _extract_outcome_from_tool_calls,
    classify_file,
    extract_game_id,
    sync_beliefs,
)


# ---------------------------------------------------------------------------
# classify_file
# ---------------------------------------------------------------------------


class TestClassifyFile:
    def test_diary(self):
        assert classify_file("diary_india_123.jsonl") == "diary"

    def test_cities(self):
        assert classify_file("diary_india_123_cities.jsonl") == "cities"

    def test_spatial(self):
        assert classify_file("spatial_india_123.jsonl") == "spatial"

    def test_mapturns(self):
        assert classify_file("mapturns_india_123.jsonl") == "mapturns"

    def test_beliefs(self):
        assert classify_file("beliefs_india_123_runabc.jsonl") == "beliefs"

    def test_unknown(self):
        assert classify_file("random_file.txt") is None

    def test_wrong_suffix(self):
        assert classify_file("diary_india_123.txt") is None

    def test_cities_takes_priority_over_diary(self):
        """Cities suffix is checked before diary prefix."""
        result = classify_file("diary_foo_cities.jsonl")
        assert result == "cities"


# ---------------------------------------------------------------------------
# extract_game_id
# ---------------------------------------------------------------------------


class TestExtractGameId:
    def test_diary(self):
        assert extract_game_id("diary_india_123.jsonl") == "india_123"

    def test_cities(self):
        assert extract_game_id("diary_india_123_cities.jsonl") == "india_123"

    def test_spatial(self):
        assert extract_game_id("spatial_india_123.jsonl") == "india_123"

    def test_mapturns(self):
        assert extract_game_id("mapturns_india_123.jsonl") == "india_123"

    def test_mapstatic(self):
        assert extract_game_id("mapstatic_india_123.json") == "india_123"

    def test_beliefs(self):
        assert extract_game_id("beliefs_india_123_runabc.jsonl") == "india_123_runabc"

    def test_complex_game_id(self):
        """Game IDs with multiple underscores and hash suffixes."""
        assert (
            extract_game_id("diary_babylon_stk_-1851106432_4fee9865.jsonl")
            == "babylon_stk_-1851106432_4fee9865"
        )


# ---------------------------------------------------------------------------
# _extract_outcome
# ---------------------------------------------------------------------------


class TestExtractOutcome:
    def test_no_game_over(self):
        lines = [
            json.dumps({"type": "turn_start", "turn": 1}),
            json.dumps({"type": "action", "tool": "move"}),
        ]
        assert _extract_outcome(lines) is None

    def test_victory(self):
        lines = [
            json.dumps({"type": "turn_start", "turn": 100}),
            json.dumps(
                {
                    "type": "game_over",
                    "turn": 100,
                    "outcome": {
                        "is_defeat": False,
                        "winner_civ": "CIVILIZATION_INDIA",
                        "winner_leader": "Gandhi",
                        "victory_type": "SCIENCE",
                        "player_alive": True,
                    },
                }
            ),
        ]
        result = _extract_outcome(lines)
        assert result is not None
        assert result["result"] == "victory"
        assert result["winnerCiv"] == "CIVILIZATION_INDIA"
        assert result["winnerLeader"] == "Gandhi"
        assert result["victoryType"] == "SCIENCE"
        assert result["turn"] == 100
        assert result["playerAlive"] is True

    def test_defeat(self):
        lines = [
            json.dumps(
                {
                    "type": "game_over",
                    "turn": 200,
                    "outcome": {
                        "is_defeat": True,
                        "winner_civ": "CIVILIZATION_SUMERIA",
                        "winner_leader": "Gilgamesh",
                        "victory_type": "DOMINATION",
                        "player_alive": False,
                    },
                }
            ),
        ]
        result = _extract_outcome(lines)
        assert result["result"] == "defeat"
        assert result["playerAlive"] is False

    def test_malformed_json_skipped(self):
        lines = [
            "this is not json",
            json.dumps({"type": "game_over", "turn": 50, "outcome": {}}),
        ]
        result = _extract_outcome(lines)
        assert result is not None
        assert result["result"] == "victory"  # is_defeat defaults falsy
        assert result["turn"] == 50

    def test_multiple_game_over_last_wins(self):
        lines = [
            json.dumps(
                {"type": "game_over", "turn": 50, "outcome": {"winner_civ": "A"}}
            ),
            json.dumps(
                {"type": "game_over", "turn": 100, "outcome": {"winner_civ": "B"}}
            ),
        ]
        result = _extract_outcome(lines)
        assert result["winnerCiv"] == "B"
        assert result["turn"] == 100

    def test_empty_lines(self):
        assert _extract_outcome([]) is None


# ---------------------------------------------------------------------------
# _extract_outcome_from_tool_calls
# ---------------------------------------------------------------------------


class TestExtractOutcomeFromToolCalls:
    def test_defeat_from_end_turn_result(self):
        lines = [
            json.dumps(
                {"type": "tool_call", "tool": "get_units", "turn": 320, "result": "..."}
            ),
            json.dumps(
                {
                    "type": "tool_call",
                    "tool": "end_turn",
                    "turn": 326,
                    "result": (
                        "GAME OVER — DEFEAT. Hojo Tokimune of Japan won a Culture victory. "
                        "The game has ended. No further actions are possible."
                    ),
                }
            ),
        ]
        result = _extract_outcome_from_tool_calls(
            lines, civ="Babylon", leader="Hammurabi"
        )
        assert result is not None
        assert result["result"] == "defeat"
        assert result["winnerLeader"] == "Hojo Tokimune"
        assert result["winnerCiv"] == "Japan"
        assert result["victoryType"] == "Culture"
        assert result["turn"] == 326
        assert result["playerAlive"] is True

    def test_victory_from_end_turn_result(self):
        lines = [
            json.dumps(
                {
                    "type": "tool_call",
                    "tool": "end_turn",
                    "turn": 238,
                    "result": (
                        "Turn 237 -> 238\n"
                        "GAME OVER — VICTORY! You won a Technology victory! The game has ended."
                    ),
                }
            ),
        ]
        result = _extract_outcome_from_tool_calls(
            lines, civ="Babylon", leader="Hammurabi"
        )
        assert result is not None
        assert result["result"] == "victory"
        assert result["winnerCiv"] == "Babylon"
        assert result["winnerLeader"] == "Hammurabi"
        assert result["victoryType"] == "Technology"
        assert result["turn"] == 238
        assert result["playerAlive"] is True

    def test_no_game_over_in_tool_calls(self):
        lines = [
            json.dumps(
                {
                    "type": "tool_call",
                    "tool": "end_turn",
                    "turn": 10,
                    "result": "Turn 10 -> 11",
                }
            ),
            json.dumps(
                {"type": "tool_call", "tool": "get_units", "turn": 11, "result": "..."}
            ),
        ]
        assert _extract_outcome_from_tool_calls(lines) is None

    def test_ignores_non_tool_call_entries(self):
        lines = [
            json.dumps({"type": "game_over", "turn": 100, "outcome": {}}),
            json.dumps({"type": "diary", "turn": 100}),
        ]
        assert _extract_outcome_from_tool_calls(lines) is None

    def test_empty_lines(self):
        assert _extract_outcome_from_tool_calls([]) is None

    def test_elimination_detected(self):
        lines = [
            json.dumps(
                {
                    "type": "tool_call",
                    "tool": "end_turn",
                    "turn": 150,
                    "result": (
                        "GAME OVER — DEFEAT. Alexander of Macedon won a Domination victory. "
                        "You have been eliminated. The game has ended."
                    ),
                }
            ),
        ]
        result = _extract_outcome_from_tool_calls(
            lines, civ="Egypt", leader="Cleopatra"
        )
        assert result is not None
        assert result["result"] == "defeat"
        assert result["playerAlive"] is False


# ---------------------------------------------------------------------------
# _chunk_map_frames
# ---------------------------------------------------------------------------


class TestChunkMapFrames:
    def test_empty_input(self):
        assert _chunk_map_frames([]) == []

    def test_single_turn_fits_one_chunk(self):
        entries = [
            {
                "turn": 1,
                "owners": [10, 0, 15, 1],  # 2 ownership changes
                "cities": [{"x": 5, "y": 6, "pid": 0, "pop": 3}],
                "roads": [],
            }
        ]
        chunks = _chunk_map_frames(entries)
        assert len(chunks) == 1
        # Verify the packed format
        owners = json.loads(chunks[0]["ownerFrames"])
        assert owners[0] == 1  # turn
        assert owners[1] == 2  # count (4 ints / 2)
        assert owners[2:] == [10, 0, 15, 1]

        cities = json.loads(chunks[0]["cityFrames"])
        assert cities[0] == 1  # turn
        assert cities[1] == 1  # count
        assert cities[2:] == [5, 6, 0, 3]

    def test_empty_fields(self):
        """Turn with no changes — nothing to pack, so no chunks emitted."""
        entries = [{"turn": 5, "owners": [], "cities": [], "roads": []}]
        chunks = _chunk_map_frames(entries)
        assert len(chunks) == 0

    def test_round_trip_multiple_turns(self):
        """Multiple turns in one chunk should concatenate correctly."""
        entries = [
            {"turn": 1, "owners": [0, 1], "cities": [], "roads": []},
            {"turn": 2, "owners": [5, 2, 6, 3], "cities": [], "roads": []},
        ]
        chunks = _chunk_map_frames(entries)
        assert len(chunks) == 1
        owners = json.loads(chunks[0]["ownerFrames"])
        # Turn 1: [1, 1, 0, 1] + Turn 2: [2, 2, 5, 2, 6, 3]
        assert owners == [1, 1, 0, 1, 2, 2, 5, 2, 6, 3]

    def test_large_data_splits(self):
        """Data exceeding chunk limit should produce multiple chunks."""
        # Create entries large enough to exceed the 700KB limit
        # Each int takes ~5 chars in JSON, so 200K ints ~ 1MB
        big_owners = list(range(200_000))
        entries = [
            {"turn": i, "owners": big_owners, "cities": [], "roads": []}
            for i in range(3)
        ]
        chunks = _chunk_map_frames(entries)
        assert len(chunks) > 1
        # Each chunk should be valid JSON
        for chunk in chunks:
            json.loads(chunk["ownerFrames"])
            json.loads(chunk["cityFrames"])
            json.loads(chunk["roadFrames"])


# ---------------------------------------------------------------------------
# Belief Engine telemetry
# ---------------------------------------------------------------------------


class TestBeliefBatchRows:
    def test_projects_event_snapshot_and_numeric_observation_metrics(self):
        events, entities, metrics = _belief_batch_rows(
            [
                json.dumps(
                    {
                        "event_id": "event-observation",
                        "sequence": 4,
                        "timestamp": 1_710_000_000.25,
                        "game_id": "india_123",
                        "run_id": "runabc",
                        "turn": 17,
                        "event_type": "entity.created",
                        "entity_type": "observation",
                        "entity_id": "observation_1",
                        "entity": {
                            "id": "observation_1",
                            "status": "active",
                            "created_turn": 17,
                            "last_updated_turn": 17,
                            "updated_at": 1_710_000_000.5,
                            "metrics": {
                                "score": 245,
                                "exploration_pct": 36.5,
                                "at_war": False,
                                "summary": "not a metric",
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_id": "event-prediction",
                        "sequence": 5,
                        "timestamp": 1_710_000_001,
                        "turn": 18,
                        "event_type": "entity.updated",
                        "entity_type": "prediction",
                        "entity_id": "prediction_1",
                        "changes": {"status": {"from": "active", "to": "confirmed"}},
                        "entity": {
                            "id": "prediction_1",
                            "status": "confirmed",
                            "created_turn": 12,
                            "last_updated_turn": 18,
                        },
                    }
                ),
            ]
        )

        assert [event["operation"] for event in events] == ["create", "resolve"]
        assert events[0]["recordedAt"] == 1_710_000_000_250
        assert events[0]["payload"]["entity"]["metrics"]["score"] == 245
        assert events[1]["payload"]["changes"]["status"]["to"] == "confirmed"
        assert events[1]["current"]["status"] == "resolved"

        assert len(entities) == 2
        assert entities[1]["status"] == "resolved"
        assert entities[1]["createdTurn"] == 12
        assert entities[1]["lastEventId"] == "event-prediction"

        assert {metric["metric"] for metric in metrics} == {"score", "exploration_pct"}
        assert {metric["metricId"] for metric in metrics} == {
            "event-observation:score",
            "event-observation:exploration_pct",
        }
        assert all(metric["dimensions"]["runId"] == "runabc" for metric in metrics)

    def test_delete_is_tombstoned_and_invalid_lines_do_not_block_batch(self):
        events, entities, metrics = _belief_batch_rows(
            [
                "not json",
                json.dumps({"event_id": "missing-entity", "turn": 1}),
                json.dumps(
                    {
                        "event_id": "event-delete",
                        "timestamp": 1_710_000_000,
                        "turn": 20,
                        "event_type": "entity.deleted",
                        "entity_type": "belief",
                        "entity_id": "war-risk",
                        "entity": {
                            "id": "war-risk",
                            "status": "deleted",
                            "created_turn": "invalid-but-recoverable",
                            "last_updated_turn": 20,
                        },
                    }
                ),
            ]
        )

        assert len(events) == 1
        assert events[0]["operation"] == "delete"
        assert events[0]["current"]["status"] == "deleted"
        assert entities[0]["createdTurn"] == 20
        assert metrics == []


class _FakeConvexClient:
    def __init__(self):
        self.calls = []

    async def mutation(self, path, args):
        self.calls.append((path, args))


class _FakeCloudFs:
    def __init__(self, files):
        self.files = files

    def cat_file(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class TestBeliefSync:
    def test_sync_is_incremental_but_replays_tail_idempotently(self, tmp_path):
        path = tmp_path / "beliefs_india_123_runabc.jsonl"
        first = {
            "event_id": "event-1",
            "timestamp": 1_710_000_000,
            "turn": 7,
            "event_type": "entity.created",
            "entity_type": "belief",
            "entity_id": "risk",
            "entity": {"id": "risk", "status": "active", "created_turn": 7},
        }
        second = {
            "event_id": "event-2",
            "timestamp": 1_710_000_001,
            "turn": 8,
            "event_type": "entity.updated",
            "entity_type": "belief",
            "entity_id": "risk",
            "entity": {"id": "risk", "status": "active", "created_turn": 7},
        }
        path.write_text(json.dumps(first) + "\n")
        state = {"files": {}, "game_last_seen": {}}
        client = _FakeConvexClient()

        asyncio.run(sync_beliefs(path, "india_123_runabc", state, client))
        assert len(client.calls) == 1
        assert client.calls[0][0] == "ingest:ingestBeliefBatch"
        assert [event["eventId"] for event in client.calls[0][1]["events"]] == ["event-1"]

        # Re-running an unchanged watcher batch does not write again.
        asyncio.run(sync_beliefs(path, "india_123_runabc", state, client))
        assert len(client.calls) == 1

        path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
        asyncio.run(sync_beliefs(path, "india_123_runabc", state, client))
        # The tail event is intentionally retried with the new append; Convex
        # de-duplicates event-1 and records event-2 by its stable eventId.
        assert len(client.calls) == 2
        assert [event["eventId"] for event in client.calls[1][1]["events"]] == [
            "event-1",
            "event-2",
        ]

    def test_missing_belief_file_is_a_noop(self, tmp_path):
        state = {"files": {}, "game_last_seen": {}}
        client = _FakeConvexClient()
        asyncio.run(sync_beliefs(tmp_path / "missing.jsonl", "india_123", state, client))
        assert client.calls == []

    def test_cloud_download_includes_belief_trace_and_tolerates_absence(self, tmp_path):
        fs = _FakeCloudFs(
            {
                "telemetry/runs/runabc/beliefs.jsonl": b'{"event_id":"event-1"}\n',
            }
        )
        game_id = _download_cloud_run(
            fs,
            "telemetry",
            "runabc",
            {"civ": "india", "seed": 123},
            tmp_path,
        )
        assert game_id == "india_123_runabc"
        assert (tmp_path / "beliefs_india_123_runabc.jsonl").read_bytes() == (
            b'{"event_id":"event-1"}\n'
        )

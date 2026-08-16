"""Property-based tests for the event-sourced belief engine.

Mutants are cheap to write by hand but expensive to enumerate; these
properties cover the invariants that hold for *any* operation sequence:

- replay consistency: whatever operations succeeded, replaying the journal
  from scratch yields byte-identical entity state;
- derived-entity idempotency: re-recording the same tool result on the same
  turn never bumps a derived belief's version;
- derived probability bounds: every derived belief probability stays in
  [0, 1] no matter the input text.
"""

from __future__ import annotations

import string
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

pytestmark = pytest.mark.property

from civ6_belief_engine.belief_engine import BeliefEngine, BeliefEngineError

_ENTITY_IDS = ("e:1", "e:2", "e:3")
_TOOLS = ("get_units", "get_cities", "get_barbarian_overview", "get_diplomacy")

_PROB = st.floats(
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
)
_TEXT = st.text(alphabet=string.printable, min_size=0, max_size=120)
_TURN = st.integers(min_value=1, max_value=50)


@st.composite
def op_sequences(draw):
    """Random operation sequences: creates/updates/archives/deletes + tool records."""
    ops = []
    for _ in range(draw(st.integers(min_value=0, max_value=20))):
        kind = draw(st.sampled_from(("create", "update", "archive", "delete", "record")))
        if kind == "record":
            ops.append(("record", draw(st.sampled_from(_TOOLS)), draw(_TEXT), draw(_TURN)))
        else:
            ops.append((kind, draw(st.sampled_from(_ENTITY_IDS)), draw(_PROB)))
    return ops


def _apply(engine: BeliefEngine, op: tuple) -> None:
    """Apply one op; illegal sequences are part of the input space (ignored)."""
    kind = op[0]
    try:
        if kind == "create":
            entity_id, probability = op[1], op[2]
            engine.create(
                "belief",
                {
                    "entity_type": "belief",
                    "statement": f"statement {entity_id}",
                    "category": "test",
                    "probability": probability,
                    "confidence": 0.5,
                    "unknown_basis": True,
                    "tags": [],
                },
                turn=1,
                entity_id=entity_id,
            )
        elif kind == "update":
            engine.update("belief", op[1], {"probability": op[2]}, turn=2)
        elif kind == "archive":
            engine.update(
                "belief", op[1], {"status": "archived", "resolution": "prop"}, turn=3
            )
        elif kind == "delete":
            engine.delete("belief", op[1], turn=4)
        else:
            engine.record_tool_result(
                tool=op[1],
                params={},
                result=op[2],
                turn=op[3],
                category="query",
                success=True,
                duration_ms=1,
            )
    except (BeliefEngineError, ValueError, TypeError, KeyError):
        pass


def _entity_state(engine: BeliefEngine) -> dict:
    return {
        (entity_type, entity_id): dict(entity)
        for entity_type, entities in engine._entities.items()
        for entity_id, entity in entities.items()
    }


@given(ops=op_sequences())
@settings(max_examples=100, deadline=None)
def test_replay_produces_identical_entity_state(ops):
    directory = Path(tempfile.mkdtemp())
    first = BeliefEngine(run_id="first", directory=directory)
    first.bind_game("CIVILIZATION_PROP", 7)
    for op in ops:
        _apply(first, op)

    replay = BeliefEngine(run_id="replay", directory=directory)
    replay.bind_game("CIVILIZATION_PROP", 7)

    assert _entity_state(first) == _entity_state(replay)
    # 事件流行数一致（无丢失、无重复回放）；空序列不产生 journal 文件。
    def journal_lines(path: Path) -> int:
        return len(path.read_text().splitlines()) if path.exists() else 0

    assert journal_lines(first.path) == journal_lines(replay.path)


@st.composite
def camp_overview_text(draw):
    x = draw(st.integers(min_value=0, max_value=200))
    y = draw(st.integers(min_value=0, max_value=200))
    distance = draw(st.integers(min_value=0, max_value=30))
    extra = draw(st.sampled_from(("", "; something else")))
    return x, y, distance, (
        f"=== BARBARIAN OVERVIEW ===\n"
        f"Camps (1 revealed):\n"
        f"  [WATCH] ({x},{y}) [revealed] — {distance} tiles from nearest city; "
        f"3 from nearest military{extra}\n"
    )


@given(camp=camp_overview_text())
@settings(max_examples=100, deadline=None)
def test_camp_belief_version_stable_across_duplicate_records(camp):
    x, y, _distance, text = camp
    engine = BeliefEngine(run_id="dup", directory=Path(tempfile.mkdtemp()))
    engine.bind_game("CIVILIZATION_PROP", 7)
    entity_id = f"auto:belief:camp_threat:{x}_{y}"

    engine.record_tool_result(
        tool="get_barbarian_overview", params={}, result=text,
        turn=5, category="query", success=True, duration_ms=1,
    )
    belief = engine.get("belief", entity_id)
    assert belief is not None, f"no belief for {entity_id} from {text!r}"
    assert 0.0 <= belief["probability"] <= 1.0
    version_before = belief["version"]

    engine.record_tool_result(
        tool="get_barbarian_overview", params={}, result=text,
        turn=5, category="query", success=True, duration_ms=1,
    )
    assert engine.get("belief", entity_id)["version"] == version_before
    # 同回合重复记录只产生一条活跃 belief。
    active = [
        item for item in engine.list("belief", status="active")
        if item["id"].startswith("auto:belief:camp_threat:")
    ]
    assert len(active) == 1


@given(text=_TEXT)
@settings(max_examples=100, deadline=None)
def test_derived_beliefs_stay_in_probability_bounds(text):
    engine = BeliefEngine(run_id="bounds", directory=Path(tempfile.mkdtemp()))
    engine.bind_game("CIVILIZATION_PROP", 7)
    engine.record_tool_result(
        tool="get_barbarian_overview", params={}, result=text,
        turn=3, category="query", success=True, duration_ms=1,
    )
    for belief in engine.list("belief", status="active"):
        assert 0.0 <= belief["probability"] <= 1.0
        assert 0.0 <= belief["confidence"] <= 1.0

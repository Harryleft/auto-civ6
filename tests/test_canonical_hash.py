"""One canonical argument hash for both the council and execution.

Two implementations existed: ``governance.models._arguments_hash`` normalized
values (dataclasses, mapping keys, sets) before hashing, while
``belief_engine.action_args_hash`` hashed a raw ``json.dumps`` and raised on
anything the normalizer existed to handle.

The council matches an approved intent by this hash and ``authorize_action``
re-verifies it at execution time, so a divergence between the two would approve
one action and authorise another — silently. These tests pin them to one
implementation and to the stricter behaviour.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from civ6_belief_engine import canonical
from civ6_belief_engine.belief_engine import action_args_hash
from civ6_belief_engine.governance.models import arguments_hash as models_hash

DOMAIN = pathlib.Path(__file__).resolve().parents[1] / "src" / "civ6_belief_engine"


def test_both_entry_points_are_the_same_function():
    assert models_hash is canonical.arguments_hash


def test_engine_hash_delegates_to_the_canonical_one():
    sample = {"tool": "unit_action", "params": {"unit_id": 1, "action": "move"}}
    assert action_args_hash(sample) == canonical.arguments_hash(sample)


def test_hash_is_stable_across_key_order():
    left = {"a": 1, "b": {"c": 2, "d": 3}}
    right = {"b": {"d": 3, "c": 2}, "a": 1}
    assert canonical.arguments_hash(left) == canonical.arguments_hash(right)


def test_hash_handles_values_the_raw_json_dump_could_not():
    """Sets and dataclasses used to raise TypeError on the engine side."""

    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Intent:
        tool: str

    assert canonical.arguments_hash({"tags": {"b", "a"}}) == canonical.arguments_hash(
        {"tags": {"a", "b"}}
    )
    assert canonical.arguments_hash({"intent": Intent("unit_action")}) == (
        canonical.arguments_hash({"intent": {"tool": "unit_action"}})
    )


def test_unsupported_values_fail_loudly():
    with pytest.raises(TypeError, match="unsupported action argument type"):
        canonical.arguments_hash({"bad": object()})


def test_mapping_keys_are_stringified_consistently():
    assert canonical.arguments_hash({1: "x"}) == canonical.arguments_hash({"1": "x"})


@pytest.mark.parametrize("forbidden", ["_arguments_hash", "_canonical_params"])
def test_the_former_duplicate_implementations_are_gone(forbidden):
    """A reappearing copy is how the two sides drifted apart in the first place."""

    offenders = []
    for path in sorted(DOMAIN.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == forbidden:
                offenders.append(str(path.relative_to(DOMAIN)))
    assert not offenders, f"{forbidden} 又被定义于：{offenders}；请复用 canonical.arguments_hash"

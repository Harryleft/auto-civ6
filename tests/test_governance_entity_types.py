"""The governance entity-type list must have exactly one definition.

It existed three times — ``belief_engine._GOVERNANCE_GRAPH_ENTITY_TYPES``,
``graph.project._GOVERNANCE_ENTITY_TYPES`` and
``belief_tools._GRAPH_GOVERNANCE_ENTITY_TYPES`` — all 17 members identical. The
graph decides which entity types it materializes, so the list lives there now
and everyone imports it.

Three identical copies are a standing invitation to update one of them. If a new
governance entity type is added to the graph but not to a copy, the projection
silently drops it; if the reverse, a type is queried that was never projected.
"""

from __future__ import annotations

import ast
import pathlib

from civ6_belief_engine.belief_engine import GOVERNANCE_ENTITY_TYPES as engine_view
from civ6_belief_engine.graph import GOVERNANCE_ENTITY_TYPES

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

EXPECTED = {
    "observation",
    "belief",
    "goal",
    "proposal",
    "critic_review",
    "council_decision",
    "budget_lock",
    "decision",
    "action",
    "outcome",
    "hypothesis",
    "prediction",
    "plan",
    "surprise",
    "contradiction",
    "attribution",
    "simulation",
}


def _assignment_names() -> dict[str, list[str]]:
    """Module-level assignments whose name ends in the list's own name."""

    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and "GOVERNANCE" in target.id and "ENTITY_TYPES" in target.id:
                    found.setdefault(target.id, []).append(str(path.relative_to(SRC)))
    return found


def test_only_one_module_defines_the_list():
    definitions = _assignment_names()
    assert definitions == {"GOVERNANCE_ENTITY_TYPES": ["civ6_belief_engine/graph/project.py"]}, (
        "治理实体类型清单只能有一处定义（graph/project.py）：\n  "
        + "\n  ".join(f"{name}: {paths}" for name, paths in definitions.items())
    )


def test_consumers_share_the_same_object():
    assert engine_view is GOVERNANCE_ENTITY_TYPES


def test_the_membership_is_pinned():
    """Adding a type is a deliberate act: it changes what the graph materializes."""

    assert GOVERNANCE_ENTITY_TYPES == EXPECTED

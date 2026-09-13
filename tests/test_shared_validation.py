"""The domain package must keep exactly one copy of each shared validator.

Four identical ``_nonempty`` implementations existed under two names
(``_nonempty`` and ``_text``), plus three near-identical ``_strings``/``_texts``
of which one had grown a tuple coercion the others lacked. They now live in
``civ6_belief_engine.validation``; this test fails if a copy reappears, which is
how the divergence started in the first place.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from civ6_belief_engine import validation

DOMAIN = pathlib.Path(__file__).resolve().parents[1] / "src" / "civ6_belief_engine"

# Names the shared helpers used to be duplicated under.
FORMER_NAMES = {"_nonempty", "_text", "_texts", "_strings"}


def _defined_function_names() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(DOMAIN.rglob("*.py")):
        if path.name == "validation.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in FORMER_NAMES:
                    found.setdefault(node.name, []).append(str(path.relative_to(DOMAIN)))
    return found


def test_no_module_reintroduces_a_private_validator_copy():
    rediscovered = _defined_function_names()
    assert not rediscovered, (
        "共享校验助手又被复制到了模块里；请从 civ6_belief_engine.validation 导入：\n  "
        + "\n  ".join(f"{name}: {', '.join(paths)}" for name, paths in rediscovered.items())
    )


def test_require_text_strips_and_rejects_empty():
    assert validation.require_text("  value  ", "name") == "value"
    for bad in ("", "   ", None, 3, ["value"]):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            validation.require_text(bad, "name")


def test_require_texts_normalizes_and_rejects_duplicates():
    assert validation.require_texts((" a ", "b"), "name") == ("a", "b")
    # Accepts any iterable, which is the behaviour the copies disagreed on.
    assert validation.require_texts(["a", "b"], "name") == ("a", "b")
    with pytest.raises(ValueError, match="must not contain duplicates"):
        validation.require_texts(("a", " a "), "name")
    with pytest.raises(ValueError, match="must be a non-empty string"):
        validation.require_texts(("a", ""), "name")


def test_the_helpers_are_used_by_every_module_that_once_copied_them():
    users = {
        "graph/model.py": "require_text",
        "governance/models.py": "require_text",
        "governance/devils_advocate.py": "require_text",
        "governance/departments/base.py": "require_text",
    }
    for relative, symbol in users.items():
        source = (DOMAIN / relative).read_text(encoding="utf-8")
        assert "validation import" in source and symbol in source, relative


# The department-level helpers were also duplicated; keep them in one place too.
_DEPARTMENT_HELPERS = {"_contains_keyword", "_context_text"}


def test_department_text_helpers_are_not_reimplemented():
    """``contains_keyword`` / ``context_text`` live in ``departments/base.py``.

    Economy and GreatPeople each had a copy — identical apart from annotating
    the parameter ``tuple`` in one and ``Iterable`` in the other.
    """

    offenders: dict[str, list[str]] = {}
    for path in sorted(DOMAIN.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _DEPARTMENT_HELPERS:
                offenders.setdefault(node.name, []).append(str(path.relative_to(DOMAIN)))
    assert not offenders, (
        "这两个部门助手已上提到 governance/departments/base.py，请改为导入：\n  "
        + "\n  ".join(f"{n}: {p}" for n, p in offenders.items())
    )


def test_departments_share_the_base_helpers():
    from civ6_belief_engine.governance.departments import base, economy, great_people

    assert economy.contains_keyword is base.contains_keyword
    assert economy.context_text is base.context_text
    assert great_people.contains_keyword is base.contains_keyword
    assert great_people.context_text is base.context_text

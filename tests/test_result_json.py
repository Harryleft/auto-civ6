"""One definition of "this tool result is a JSON object, or it is not".

``presentation`` and ``result_filter`` each had a copy. Both are
dependency-free leaf modules, so the shared helper lives in a leaf too — the
semantic home (``facts``) drags in the Lua package, which these tests also
guard: importing either consumer must stay cheap.
"""

from __future__ import annotations

import ast
import pathlib
import pytest

from civ_mcp.result_json import json_object

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
MCP = SRC / "civ_mcp"


def test_parses_a_json_object():
    assert json_object('{"a": 1}') == {"a": 1}
    assert json_object("{}") == {}


@pytest.mark.parametrize(
    "result",
    [
        "not json at all",
        "",
        "[1, 2, 3]",          # valid JSON, but not an object
        "42",
        '"a string"',
        "null",
        '{"unterminated": ',
    ],
)
def test_reports_anything_that_is_not_an_object(result):
    assert json_object(result) is None


def test_only_one_module_defines_it():
    offenders = []
    for path in sorted(MCP.glob("*.py")):
        if path.name == "result_json.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_json_object":
                offenders.append(path.name)
    assert not offenders, f"_json_object 又被定义于 {offenders}；请导入 civ_mcp.result_json"


def test_consumers_share_the_same_function():
    from civ_mcp import presentation, result_filter

    assert presentation.json_object is json_object
    assert result_filter.json_object is json_object


def test_consumers_stay_free_of_the_lua_package():
    """A structural check, not a timing one.

    ``facts`` is the semantic home for the envelope contract, but it reaches the
    Lua builder package (~34ms above the package baseline). Both consumers are
    leaf modules; this pins that they stay that way.
    """

    for name in ("presentation", "result_filter", "result_json"):
        tree = ast.parse((MCP / f"{name}.py").read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imported |= {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        heavy = {m for m in imported if m.startswith("civ_mcp.lua") or m == "civ_mcp.facts"}
        assert not heavy, f"civ_mcp.{name} 依赖了 {heavy}，不再是叶子模块"

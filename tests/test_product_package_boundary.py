"""Product boundary: the domain package must not depend on the MCP adapter.

The legacy ``civ_mcp.belief_engine`` / ``civ_mcp.governance`` compatibility
shims were removed; tests and production code import the product domain
package (``civ6_belief_engine``) directly.

Adapter types are allowed only at the calling ``GameState`` boundary. The
domain package receives adapter-neutral inputs and must not import any
``civ_mcp`` module at runtime.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "src" / "civ6_belief_engine"

# The adapter namespace belongs only to the calling GameState boundary.
ADAPTER_IMPORT_PREFIX = "civ_mcp"


def _adapter_imports() -> set[str]:
    imports: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"import {ADAPTER_IMPORT_PREFIX}") or stripped.startswith(
                f"from {ADAPTER_IMPORT_PREFIX}"
            ):
                imports.add(stripped.split()[1])
    return imports


def test_domain_package_imports_no_adapter_modules():
    unexpected = _adapter_imports()
    assert unexpected == set(), (
        "civ6_belief_engine must not import adapter modules:",
        sorted(unexpected),
    )


def test_legacy_compatibility_shims_are_removed():
    with pytest.raises(ImportError):
        importlib.import_module("civ_mcp.belief_engine")
    with pytest.raises(ImportError):
        importlib.import_module("civ_mcp.governance")


def test_phase4_server_tool_boundaries_are_migrated():
    with pytest.raises(ImportError):
        importlib.import_module("civ_mcp.server.tools.belief")

    belief_tools = importlib.import_module("civ_mcp.server.tools.belief_tools")
    end_turn = importlib.import_module("civ_mcp.server.tools.end_turn")
    end_turn_flow = importlib.import_module("civ_mcp.server.tools.end_turn_flow")
    assert callable(belief_tools.get_governance_brief)
    assert callable(end_turn.end_turn)
    assert callable(end_turn_flow.run_end_turn)

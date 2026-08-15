"""Product boundary: the domain package must not depend on the MCP adapter.

The legacy ``civ_mcp.belief_engine`` / ``civ_mcp.governance`` compatibility
shims were removed; tests and production code import the product domain
package (``civ6_belief_engine``) directly.

The only tolerated adapter reference left is the typed Lua DTO
(``civ_mcp.lua.models``) used by the governance snapshot layer — known debt
being inverted by the graph plan (phases 3–4). Tighten this guard when that
debt is paid off.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "src" / "civ6_belief_engine"

# Known, in-flight debt: typed Lua DTOs leaking into governance models/snapshot.
ALLOWED_ADAPTER_MODULES = {"civ_mcp.lua.models"}


def _adapter_imports() -> set[str]:
    imports: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import civ_mcp") or stripped.startswith("from civ_mcp"):
                imports.add(stripped.split()[1])
    return imports


def test_domain_package_imports_no_adapter_module_beyond_allowed_dto():
    unexpected = _adapter_imports() - ALLOWED_ADAPTER_MODULES
    assert unexpected == set(), (
        "civ6_belief_engine must not import adapter modules:",
        sorted(unexpected),
    )


def test_legacy_compatibility_shims_are_removed():
    with pytest.raises(ImportError):
        importlib.import_module("civ_mcp.belief_engine")
    with pytest.raises(ImportError):
        importlib.import_module("civ_mcp.governance")

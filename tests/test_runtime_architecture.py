"""DSM import gates for the new Runtime Core."""

from __future__ import annotations

import ast
from pathlib import Path

import civ_mcp.runtime.context as context
import civ_mcp.runtime.mcp_surface as surface


def _import_modules(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }


def test_mcp_surface_cannot_import_transport_or_civ_adapter() -> None:
    imports = _import_modules(surface)
    assert "civ_mcp.runtime.transport" not in imports
    assert "civ_mcp.civ.adapter" not in imports
    assert "civ6_belief_engine" not in imports


def test_context_builder_has_no_mutation_sender_dependency() -> None:
    imports = _import_modules(context)
    assert "civ_mcp.runtime.transport" not in imports
    source = Path(context.__file__).read_text()
    assert ".execute(" not in source
    assert "MutationExecution" not in source


def test_new_runtime_has_no_belief_engine_imports() -> None:
    runtime_dir = Path(surface.__file__).parent
    for source_file in runtime_dir.glob("*.py"):
        assert "civ6_belief_engine" not in source_file.read_text(), source_file

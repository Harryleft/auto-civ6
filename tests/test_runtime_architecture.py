"""DSM import gates for the new Runtime Core."""

from __future__ import annotations

import ast
from pathlib import Path

import civ_mcp.runtime.context as context
import civ_mcp.runtime.connection as connection
import civ_mcp.runtime.bootstrap as bootstrap
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


def test_runtime_connection_does_not_reuse_the_legacy_connection_or_tuner_client() -> None:
    imports = _import_modules(connection)
    assert "civ_mcp.connection" not in imports
    assert "civ_mcp.tuner_client" not in imports


def test_runtime_bootstrap_has_no_legacy_server_or_game_state_dependency() -> None:
    imports = _import_modules(bootstrap)
    assert "civ_mcp.server" not in imports
    assert "civ_mcp.game_state" not in imports


def test_new_runtime_has_no_belief_engine_imports() -> None:
    runtime_dir = Path(surface.__file__).parent
    for source_file in runtime_dir.glob("*.py"):
        assert "civ6_belief_engine" not in source_file.read_text(), source_file

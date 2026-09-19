"""Task D1: the Civ adapter has no strategy, recovery, or legacy dependency."""

from __future__ import annotations

import ast
from pathlib import Path

from civ_mcp.civ import adapter


def test_adapter_does_not_depend_on_legacy_runtime_or_control_layers() -> None:
    source = Path(adapter.__file__).read_text()
    imports = [
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "civ_mcp.runtime.transport" in imports
    assert not {"civ6_belief_engine", "civ_mcp.game_state", "civ_mcp.game_launcher"} & set(imports)


def test_adapter_command_binds_lua_to_the_selected_discovered_state() -> None:
    assert adapter.CivAdapter._command(8, "return 1") == "CMD:8:return 1"

"""DSM import gates for the new Runtime Core."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from importlib.util import resolve_name
from pathlib import Path

import civ_mcp.runtime.context as context
import civ_mcp.runtime.connection as connection
import civ_mcp.runtime.bootstrap as bootstrap
import civ_mcp.runtime.eval_harness as eval_harness
import civ_mcp.runtime.mcp_surface as surface
import civ_mcp.runtime.server as server
import civ_mcp.runtime.session as session
import civ_mcp.runtime.telemetry as telemetry
import civ_mcp.civ.mutations as mutations
from civ_mcp.civ.mutations import CivMutationFactory


RUNTIME_DIR = Path(surface.__file__).parent


def _source_files() -> tuple[Path, ...]:
    return tuple(sorted(RUNTIME_DIR.rglob("*.py")))


def _module_name(source_file: Path) -> str:
    relative = source_file.relative_to(RUNTIME_DIR).with_suffix("")
    if relative.name == "__init__":
        relative = relative.parent
    suffix = ".".join(relative.parts)
    return "civ_mcp.runtime" if not suffix else f"civ_mcp.runtime.{suffix}"


def _tree(source_file: Path) -> ast.Module:
    return ast.parse(source_file.read_text(), filename=str(source_file))


def _import_from(node: ast.ImportFrom, *, package: str) -> str | None:
    if node.level == 0:
        return node.module
    relative_name = "." * node.level + (node.module or "")
    return resolve_name(relative_name, package)


def _import_modules(module) -> set[str]:
    source_file = Path(module.__file__)
    package = _module_name(source_file).rpartition(".")[0]
    imports: set[str] = set()
    for node in ast.walk(_tree(source_file)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported = _import_from(node, package=package)
            if imported is not None:
                imports.add(imported)
    return imports


def _has_import(imports: Iterable[str], forbidden: str) -> bool:
    return any(item == forbidden or item.startswith(f"{forbidden}.") for item in imports)


def _attribute_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_name(node.value)
        return f"{parent}.{node.attr}" if parent is not None else None
    return None


def test_mcp_surface_cannot_import_transport_or_civ_adapter() -> None:
    imports = _import_modules(surface)
    assert not _has_import(imports, "civ_mcp.runtime.transport")
    assert not _has_import(imports, "civ_mcp.civ.adapter")
    assert not _has_import(imports, "civ6_belief_engine")


def test_context_builder_has_no_mutation_sender_dependency() -> None:
    imports = _import_modules(context)
    assert not _has_import(imports, "civ_mcp.runtime.transport")
    source = Path(context.__file__).read_text()
    assert ".execute(" not in source
    assert "MutationExecution" not in source
    assert "CivMutationRequest" not in source


def test_runtime_connection_does_not_reuse_the_legacy_connection_or_tuner_client() -> None:
    imports = _import_modules(connection)
    assert not _has_import(imports, "civ_mcp.connection")
    assert not _has_import(imports, "civ_mcp.tuner_client")


def test_runtime_bootstrap_has_no_legacy_server_or_game_state_dependency() -> None:
    imports = _import_modules(bootstrap)
    assert not _has_import(imports, "civ_mcp.server")
    assert not _has_import(imports, "civ_mcp.game_state")


def test_runtime_server_does_not_import_legacy_server_or_belief_engine() -> None:
    imports = _import_modules(server)
    assert not _has_import(imports, "civ_mcp.server")
    assert not _has_import(imports, "civ6_belief_engine")


def test_new_runtime_has_no_belief_engine_imports() -> None:
    for source_file in _source_files():
        assert "civ6_belief_engine" not in source_file.read_text(), source_file


def test_only_session_kernel_submits_civ_mutations() -> None:
    """The adapter is the game-write boundary, and SessionKernel owns it."""
    submit_callers: list[Path] = []
    for source_file in _source_files():
        for node in ast.walk(_tree(source_file)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = _attribute_name(node.func.value)
            if node.func.attr == "submit" and receiver and receiver.endswith("adapter"):
                submit_callers.append(source_file)
    assert submit_callers == [Path(session.__file__)]


def test_domain_mutation_factory_cannot_submit_directly() -> None:
    """Factories may read and construct requests, but cannot bypass SessionKernel."""
    source_file = Path(mutations.__file__)
    calls = [
        node
        for node in ast.walk(_tree(source_file))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "submit"
        and _attribute_name(node.func.value)
        and _attribute_name(node.func.value).endswith("adapter")
    ]
    assert calls == []


def test_model_visible_server_mutations_go_through_the_mcp_surface() -> None:
    calls = [
        _attribute_name(node.func.value)
        for node in ast.walk(_tree(Path(server.__file__)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_mutation"
    ]
    assert calls
    assert set(calls) == {"assembly.surface"}


def test_runtime_import_graph_is_acyclic() -> None:
    """A reverse Runtime dependency must fail before it becomes a second core."""
    modules = {_module_name(source_file): source_file for source_file in _source_files()}
    graph = {
        module: {dependency for dependency in _import_modules_from(source_file) if dependency in modules}
        for module, source_file in modules.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        assert module not in visiting, f"Runtime import cycle at {module}"
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module)


def _import_modules_from(source_file: Path) -> set[str]:
    package = _module_name(source_file).rpartition(".")[0]
    imports: set[str] = set()
    for node in ast.walk(_tree(source_file)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported = _import_from(node, package=package)
            if imported is not None:
                imports.add(imported)
    return imports


def test_runtime_has_no_play_profile_or_belief_mode_branch() -> None:
    for source_file in _source_files():
        source = source_file.read_text()
        assert "PlayProfile" not in source, source_file
        assert "BeliefMode" not in source, source_file


def test_recovery_is_not_imported_by_the_model_tool_path() -> None:
    for module in (server, surface):
        assert not _has_import(_import_modules(module), "civ_mcp.runtime.recovery")


def test_telemetry_has_no_runtime_control_dependency() -> None:
    imports = _import_modules(telemetry)
    for forbidden in (
        "civ_mcp.civ.adapter",
        "civ_mcp.runtime.session",
        "civ_mcp.runtime.turn",
        "civ_mcp.runtime.mcp_surface",
        "civ_mcp.runtime.recovery",
        "civ_mcp.runtime.server",
    ):
        assert not _has_import(imports, forbidden)
    source = Path(telemetry.__file__).read_text()
    assert ".submit(" not in source
    assert ".execute(" not in source
    assert "save_operation" not in source


def test_eval_harness_is_not_part_of_the_model_or_runtime_control_path() -> None:
    imports = _import_modules(eval_harness)
    for forbidden in (
        "civ_mcp.civ.adapter",
        "civ_mcp.runtime.connection",
        "civ_mcp.runtime.session",
        "civ_mcp.runtime.turn",
        "civ_mcp.runtime.mcp_surface",
        "civ_mcp.runtime.server",
    ):
        assert not _has_import(imports, forbidden)
    assert not _has_import(_import_modules(server), "civ_mcp.runtime.eval_harness")

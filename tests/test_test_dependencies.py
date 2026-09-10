"""Test-suite dependencies must be declared, so a fresh clone can collect.

The failure this guards against: ``hypothesis`` was imported by three test
modules but declared nowhere, so ``uv sync`` on a clean checkout left pytest
unable to import them. A collection error interrupts the whole run, which
meant **no** test executed while the author's machine stayed green on an
undeclared hand-installed copy.

The check is deliberately narrow: only ``tests/`` is scanned, and only
imports that resolve outside the repository are considered. Production code
imports optional platform backends lazily, so scanning ``src/`` here would
report false positives.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

# Import name -> distribution name, where the two differ. Optional platform
# backends are listed even though the suite does not import them today, so the
# message stays actionable if a test starts doing so.
MODULE_TO_DISTRIBUTION = {
    "PIL": "pillow",
    "Quartz": "pyobjc-framework-quartz",
    "Vision": "pyobjc-framework-vision",
    "AppKit": "pyobjc-framework-cocoa",
    "winrt": "winrt-windows-media-ocr",
    "inspect_ai": "inspect-ai",
}


def _normalize(distribution: str) -> str:
    name = distribution.split("[", 1)[0]
    for separator in "><=!~;":
        name = name.split(separator, 1)[0]
    return name.strip().lower().replace("-", "_")


def _declared_distributions() -> set[str]:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {_normalize(item) for item in metadata["project"].get("dependencies", [])}
    for group in metadata.get("dependency-groups", {}).values():
        declared |= {_normalize(item) for item in group}
    for extra in metadata["project"].get("optional-dependencies", {}).values():
        declared |= {_normalize(item) for item in extra}
    return declared


def _local_top_level_modules() -> set[str]:
    """Top-level names that resolve to this repository rather than to a wheel."""

    local = {"tests", "conftest"}
    for base in (ROOT / "src", ROOT):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.name.startswith(".") or entry.name in {"tests", "web"}:
                continue
            # Importable from the repository root: a package or a module, but
            # not a data directory such as docs/ or graph_plan/.
            if (entry / "__init__.py").exists() or entry.suffix == ".py":
                local.add(entry.stem if entry.suffix == ".py" else entry.name)
    local |= {path.stem for path in TESTS.glob("*.py")}
    # scripts/ holds loose modules that tests import directly by putting the
    # directory on sys.path (for example scripts/convex_sync.py).
    local |= {path.stem for path in (ROOT / "scripts").glob("*.py")}
    return local


def _imported_top_level_modules() -> dict[str, set[str]]:
    imported: dict[str, set[str]] = {}
    for path in sorted(TESTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                top = module.split(".", 1)[0]
                if top:
                    imported.setdefault(top, set()).add(str(path.relative_to(ROOT)))
    return imported


def test_every_third_party_test_import_is_declared() -> None:
    declared = _declared_distributions()
    local = _local_top_level_modules()
    undeclared: list[str] = []
    for module, users in sorted(_imported_top_level_modules().items()):
        if module in sys.stdlib_module_names or module in local:
            continue
        distribution = MODULE_TO_DISTRIBUTION.get(module, module)
        if _normalize(distribution) not in declared:
            undeclared.append(
                f"{module} (distribution {distribution!r}) imported by "
                + ", ".join(sorted(users))
            )
    assert not undeclared, (
        "测试引用了未在 pyproject.toml 中声明的第三方依赖；"
        "新克隆执行 uv sync 后 pytest 会收集失败并中断整个测试运行：\n  "
        + "\n  ".join(undeclared)
    )


@pytest.mark.parametrize("module", ["pytest", "hypothesis"])
def test_test_frameworks_are_dev_dependencies(module: str) -> None:
    """Regression: hypothesis was imported by tests but declared nowhere."""

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = {_normalize(item) for item in metadata["dependency-groups"]["dev"]}
    assert module in dev, (
        f"{module} 必须声明在 [dependency-groups].dev 中，"
        "否则 CI 与全新克隆无法收集测试"
    )
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert f'name = "{module}"' in lock, f"{module} 未出现在 uv.lock 中，请运行 uv lock"

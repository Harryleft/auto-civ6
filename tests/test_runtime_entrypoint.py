"""K1 contracts for the formal Runtime Core command entry points."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from civ_mcp.runtime import server as runtime_server


ROOT = Path(__file__).resolve().parents[1]


def test_console_script_starts_the_runtime_core() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["scripts"]["civ-mcp"] == "civ_mcp.runtime.server:main"


def test_python_module_starts_the_runtime_core() -> None:
    entrypoint = (ROOT / "src" / "civ_mcp" / "__main__.py").read_text(encoding="utf-8")

    assert "from civ_mcp.runtime.server import main" in entrypoint
    assert "from civ_mcp.server import main" not in entrypoint


def test_formal_dsh_overlay_forwards_runtime_binding() -> None:
    overlay = (
        ROOT / "integrations" / "deepseek-harness" / "civ6.cordis.yml"
    ).read_text(encoding="utf-8")

    assert "args: [run, civ-mcp]" in overlay
    assert "CIV_MCP_RUNTIME_BRANCH" in overlay
    assert "CIV_MCP_RUNTIME_STORE" in overlay


def test_runtime_entry_import_does_not_require_the_legacy_package() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                (
                    "import importlib.abc",
                    "import sys",
                    "class BlockLegacy(importlib.abc.MetaPathFinder):",
                    "    def find_spec(self, fullname, path=None, target=None):",
                    "        if fullname == 'civ6_belief_engine' or fullname.startswith('civ6_belief_engine.'):",
                    "            raise ModuleNotFoundError(fullname)",
                    "        return None",
                    "sys.meta_path.insert(0, BlockLegacy())",
                    "import civ_mcp.runtime.server",
                )
            ),
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_runtime_main_uses_stdio(monkeypatch) -> None:
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(runtime_server.mcp, "run", lambda **kwargs: calls.append(kwargs))

    runtime_server.main()

    assert calls == [{"transport": "stdio"}]

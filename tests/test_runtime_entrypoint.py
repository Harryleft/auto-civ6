"""K1 contracts for the formal Runtime Core command entry points."""

from __future__ import annotations

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


def test_runtime_main_uses_stdio(monkeypatch) -> None:
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(runtime_server.mcp, "run", lambda **kwargs: calls.append(kwargs))

    runtime_server.main()

    assert calls == [{"transport": "stdio"}]

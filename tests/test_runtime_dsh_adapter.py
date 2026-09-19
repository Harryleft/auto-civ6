"""M9 boundaries for the experimental DSH Runtime adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "integrations" / "deepseek-harness" / "civ6-runtime.cordis.yml"
ENTRYPOINT = ROOT / "scripts" / "runtime_dsh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ENTRYPOINT), *args],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )


def test_runtime_dsh_overlay_starts_only_the_runtime_server() -> None:
    overlay = OVERLAY.read_text(encoding="utf-8")

    assert "id: mcp-civ6runtime" in overlay
    assert "serverName: civ6runtime" in overlay
    assert "args: [run, python, -m, civ_mcp.runtime.server]" in overlay
    assert "CIV_MCP_RUNTIME_BRANCH" in overlay
    assert "CIV_MCP_RUNTIME_STORE" in overlay
    assert "CIV_MCP_PLAY_PROFILE" not in overlay
    assert "CIV_MCP_BELIEF_MODE" not in overlay
    assert "CIV_MCP_DSH_AUTO_RESUME" not in overlay
    assert "args: [run, civ-mcp]" not in overlay
    assert "mcp__civ6runtime__*" in overlay


def test_runtime_dsh_overlay_removes_non_runtime_model_tools() -> None:
    overlay = OVERLAY.read_text(encoding="utf-8")

    for tool_id in (
        "tool-bash",
        "tool-fs",
        "tool-fs-search",
        "tool-web",
        "tool-skill",
        "command-goal",
        "command-compact",
        "tool-goal",
        "code-runtime",
    ):
        assert f"- id: {tool_id}\n  disabled: true" in overlay


def test_runtime_dsh_dry_run_requires_no_dsh_checkout_or_game_connection() -> None:
    result = _run("--branch", "save-0001", "--store", "/tmp/runtime.sqlite3", "--dry-run")

    assert result.returncode == 0
    assert "Runtime DSH dry run" in result.stdout
    assert "branch=save-0001" in result.stdout
    assert "store=/tmp/runtime.sqlite3" in result.stdout


def test_runtime_dsh_rejects_a_composed_branch_identifier_before_startup() -> None:
    result = _run("--branch", "game-a:save-0001", "--dry-run")

    assert result.returncode == 2
    assert "host token" in result.stderr

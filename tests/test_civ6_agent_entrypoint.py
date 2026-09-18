"""Contract tests for the no-Web Civ-only entrypoint."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "civ6_agent"
HARNESS = ROOT / "scripts" / "deepseek_harness"
PROFILE = ROOT / "integrations" / "deepseek-harness" / "civ6-agent.cordis.yml"
CODING_PROFILE = ROOT / "integrations" / "deepseek-harness" / "civ6-agent-coding.cordis.yml"
LEAN_PROFILE = ROOT / "integrations" / "deepseek-harness" / "civ6-lean.cordis.yml"


def run_entrypoint(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ENTRYPOINT), *args],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )


def test_dry_run_is_bounded_and_enables_recovery_without_web_ui() -> None:
    result = run_entrypoint("--turns", "3", "--dry-run")

    assert result.returncode == 0
    assert "turns=3" in result.stdout
    assert "play_profile=legacy" in result.stdout
    assert "auto_recovery=1" in result.stdout
    assert "web_ui=disabled" in result.stdout
    assert "coding_module=0" in result.stdout
    assert "恰好 3 个回合" in result.stdout


def test_default_profile_is_legacy() -> None:
    result = run_entrypoint("--turns", "1", "--dry-run")

    assert result.returncode == 0
    assert "play_profile=legacy" in result.stdout
    assert "belief_mode=enforce" in result.stdout


def test_dry_run_reports_the_effective_lean_configuration() -> None:
    """The preview must describe the surface the MCP child would really serve."""

    result = run_entrypoint("--play-profile", "lean", "--turns", "1", "--dry-run")

    assert result.returncode == 0
    assert "play_profile=lean" in result.stdout
    assert "belief_mode=off" in result.stdout
    assert "hidden_control_plane=29" in result.stdout
    assert "reflections_required=no" in result.stdout
    # The previewed host deadline must be the real one, and it must clear the
    # derived worst-case budget rather than merely being printed.
    shown = re.search(r"Host tool deadline: (\d+) ms", result.stdout)
    assert shown, result.stdout
    from civ_mcp.end_turn import end_turn_budget

    assert int(shown.group(1)) > end_turn_budget().total_seconds * 1000


def test_lean_and_an_explicit_governance_mode_are_rejected() -> None:
    result = run_entrypoint(
        "--play-profile", "lean", "--turns", "1", "--dry-run",
        env={"CIV_MCP_BELIEF_MODE": "observe"},
    )

    assert result.returncode == 2
    assert "冲突" in result.stderr
    assert "--play-profile legacy" in result.stderr


def test_lean_with_governance_explicitly_off_is_allowed() -> None:
    result = run_entrypoint(
        "--play-profile", "lean", "--turns", "1", "--dry-run",
        env={"CIV_MCP_BELIEF_MODE": "off"},
    )

    assert result.returncode == 0
    assert "belief_mode=off" in result.stdout


def test_unknown_play_profile_is_rejected() -> None:
    result = run_entrypoint("--play-profile", "turbo", "--dry-run")

    assert result.returncode == 2
    assert "legacy or lean" in result.stderr


def test_rejects_non_positive_turn_count() -> None:
    result = run_entrypoint("--turns", "0", "--dry-run")

    assert result.returncode == 2
    assert "positive integer" in result.stderr


def test_civ_profile_uses_a_hard_boundary_for_the_optional_coding_module() -> None:
    text = PROFILE.read_text(encoding="utf-8")
    coding_text = CODING_PROFILE.read_text(encoding="utf-8")

    for tool_id in ("tool-bash", "tool-fs", "tool-fs-search", "tool-web", "tool-skill", "command-goal", "command-compact", "tool-goal", "code-runtime"):
        assert f"- id: {tool_id}\n  disabled: true" in text
    assert "mcp__civ6__*" in text
    assert "Coding 模块未启用" in text
    assert "参数/验证错误，立刻停止当前会话" in text
    assert "每个 `action_intents` 项必须有非空" in text
    assert "而不是\n      `success_probability`" in text
    for tool_id in ("tool-bash", "tool-fs", "tool-fs-search", "code-runtime"):
        assert f"- id: {tool_id}\n  disabled: false" in coding_text


def test_lean_overlay_replaces_only_the_role_text() -> None:
    """Lean keeps the same DSH module boundary; it changes the persona."""

    text = LEAN_PROFILE.read_text(encoding="utf-8")

    assert "- id: system-prompt" in text
    # No module is re-enabled by the lean overlay.
    assert "disabled: false" not in text
    assert "tool-bash" not in text


def test_with_coding_is_an_explicit_opt_in() -> None:
    result = run_entrypoint("--turns", "1", "--with-coding", "--dry-run")

    assert result.returncode == 0
    assert "coding_module=1" in result.stdout

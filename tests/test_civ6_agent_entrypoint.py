"""Contract tests for the no-Web Civ-only entrypoint."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "civ6_agent"
PROFILE = ROOT / "integrations" / "deepseek-harness" / "civ6-agent.cordis.yml"
CODING_PROFILE = ROOT / "integrations" / "deepseek-harness" / "civ6-agent-coding.cordis.yml"


def run_entrypoint(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ENTRYPOINT), *args],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )


def test_dry_run_is_bounded_and_enables_recovery_without_web_ui() -> None:
    result = run_entrypoint("--turns", "3", "--dry-run")

    assert result.returncode == 0
    assert "turns=3" in result.stdout
    assert "auto_recovery=1" in result.stdout
    assert "web_ui=disabled" in result.stdout
    assert "coding_module=0" in result.stdout
    assert "恰好 3 个回合" in result.stdout


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


def test_with_coding_is_an_explicit_opt_in() -> None:
    result = run_entrypoint("--turns", "1", "--with-coding", "--dry-run")

    assert result.returncode == 0
    assert "coding_module=1" in result.stdout

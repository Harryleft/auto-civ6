"""Run the real hook process against isolated Git repositories, without a game."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "codex_hooks.py"


def git(root, *arguments):
    return subprocess.run(["git", *arguments], cwd=root, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    shutil.copyfile(SOURCE, root / "scripts" / "codex_hooks.py")
    (root / "src" / "example.py").write_text("value = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text(".scratch/\n", encoding="utf-8")
    git(root, "init")
    git(root, "config", "user.name", "Hook Test")
    git(root, "config", "user.email", "hook@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")
    return root


def hook(root, kind, **extra):
    event = {"hook_event_name": kind, "cwd": str(root), "session_id": "test-session", **extra}
    result = subprocess.run([sys.executable, str(root / "scripts" / "codex_hooks.py")],
                            cwd=root, input=json.dumps(event), text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    return json.loads(result.stdout)


def report(root):
    return json.loads(next((root / ".scratch" / "codex-hooks").glob("*/report.json")).read_text(encoding="utf-8"))


def test_start_in_subdirectory_and_unchanged_stop(repo):
    output = hook(repo, "SessionStart", cwd=str(repo / "src"))
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert hook(repo, "Stop") == {}
    assert report(repo)["status"] == "NOT_RUN"


def test_new_syntax_error_blocks_once_then_fix_passes(repo):
    hook(repo, "SessionStart")
    source = repo / "src" / "example.py"
    source.write_text("value = (\n", encoding="utf-8")
    output = hook(repo, "Stop")
    assert output["decision"] == "block" and "example.py" in output["reason"]
    assert report(repo)["status"] == "FAIL"
    assert hook(repo, "Stop", stop_hook_active=True) == {}
    source.write_text("value = 2\n", encoding="utf-8")
    assert "PASS" in hook(repo, "Stop", stop_hook_active=True)["systemMessage"]
    assert report(repo)["behavior_tests"] == "NOT_RUN"
    assert report(repo)["game_verification"] == "not_run"


def test_continuation_does_not_block_again_even_for_a_new_error(repo):
    hook(repo, "SessionStart")
    (repo / "src" / "example.py").write_text("value = (\n", encoding="utf-8")
    hook(repo, "Stop")
    (repo / "src" / "example.py").write_text("value = [\n", encoding="utf-8")
    output = hook(repo, "Stop", stop_hook_active=True)
    assert "decision" not in output and "FAIL" in output["systemMessage"]


def test_untracked_file_is_checked_without_executing_it(repo):
    hook(repo, "SessionStart")
    (repo / "tests" / "test_new.py").write_text("raise RuntimeError('must not execute')  \n", encoding="utf-8")
    output = hook(repo, "Stop")
    assert output["decision"] == "block"
    data = report(repo)
    assert data["python_files_checked"] == 1
    assert any("whitespace" in issue for issue in data["issues"])
    assert "uv run --locked pytest -q tests/test_new.py" in data["suggested_commands"]


def test_staged_and_committed_edits_still_checked(repo):
    hook(repo, "SessionStart")
    (repo / "src" / "example.py").write_text("value = (\n", encoding="utf-8")
    git(repo, "add", "src/example.py")
    git(repo, "commit", "-m", "invalid fixture")
    assert hook(repo, "Stop")["decision"] == "block"
    assert report(repo)["changed_files"] == ["src/example.py"]


@pytest.mark.parametrize("source", ["compact", "resume", "fork"])
def test_start_again_preserves_original_baseline(repo, source):
    hook(repo, "SessionStart")
    (repo / "src" / "example.py").write_text("value = (\n", encoding="utf-8")
    hook(repo, "SessionStart", source=source)
    assert hook(repo, "Stop")["decision"] == "block"


def test_existing_dirty_error_is_not_attributed_to_this_session(repo):
    (repo / "src" / "example.py").write_text("value = (\n", encoding="utf-8")
    hook(repo, "SessionStart")
    (repo / "tests" / "test_new.py").write_text("value = 2\n", encoding="utf-8")
    assert "PASS" in hook(repo, "Stop")["systemMessage"]
    assert report(repo)["changed_files"] == ["tests/test_new.py"]


def test_missing_baseline_is_explicitly_not_run(repo):
    output = hook(repo, "Stop")
    assert "NOT_RUN" in output["systemMessage"] and "decision" not in output


def test_different_sessions_have_independent_baselines(repo):
    hook(repo, "SessionStart")
    (repo / "src" / "example.py").write_text("value = (\n", encoding="utf-8")
    hook(repo, "SessionStart", session_id="other-session")
    assert hook(repo, "Stop", session_id="other-session") == {}
    assert hook(repo, "Stop")["decision"] == "block"


def test_invalid_json_returns_visible_blocked_message(repo):
    result = subprocess.run([sys.executable, str(repo / "scripts" / "codex_hooks.py")],
                            input="{", text=True, capture_output=True, timeout=30)
    assert result.returncode == 0
    assert "BLOCKED" in json.loads(result.stdout)["systemMessage"]


def test_deletion_does_not_attempt_to_compile_a_missing_file(repo):
    hook(repo, "SessionStart")
    (repo / "src" / "example.py").unlink()
    assert "PASS" in hook(repo, "Stop")["systemMessage"]
    assert report(repo)["python_files_checked"] == 0

"""Contracts for the experiment baseline recorder.

The lean-vs-legacy comparison is only interpretable if both arms ran the same
code, model, rules, and starting save. ``scripts/experiment_baseline.py``
captures that record offline and without touching the user's saves, so these
tests pin the parts a silent regression would quietly falsify: the file digests,
the DSH config parsing, and the never-destructive backup rule.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_SAVE = ROOT / "evals" / "saves" / "0A_GROUND_CONTROL.Civ6Save"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "experiment_baseline", ROOT / "scripts" / "experiment_baseline.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = _load_module()


# ---------------------------------------------------------------------------
# Save header facts (offline)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not FIXTURE_SAVE.is_file(), reason="benchmark save fixture absent")
def test_save_facts_come_from_the_file_not_from_memory() -> None:
    facts = baseline.extract_save_facts(FIXTURE_SAVE)

    assert facts["ruleset"] == "EXPANSION_2"
    assert facts["game_speed"] == "QUICK"
    assert facts["map_size"] == "STANDARD"
    assert facts["difficulty"] == "PRINCE"
    assert facts["bytes"] == FIXTURE_SAVE.stat().st_size
    assert len(facts["sha256"]) == 64
    assert facts["name"] == "0A_GROUND_CONTROL"


@pytest.mark.skipif(not FIXTURE_SAVE.is_file(), reason="benchmark save fixture absent")
def test_save_digest_is_reproducible() -> None:
    assert baseline.sha256_file(FIXTURE_SAVE) == baseline.extract_save_facts(
        FIXTURE_SAVE
    )["sha256"]


def test_unreadable_save_facts_do_not_fabricate_values(tmp_path) -> None:
    """A file with no Civ VI header must report nulls, not plausible defaults."""

    blank = tmp_path / "blank.Civ6Save"
    blank.write_bytes(b"not a save")

    facts = baseline.extract_save_facts(blank)

    assert facts["ruleset"] is None
    assert facts["game_speed"] is None
    assert facts["map_size"] is None
    assert facts["difficulty"] is None
    assert facts["enabled_mods"] == []


# ---------------------------------------------------------------------------
# DSH composed-config parsing
# ---------------------------------------------------------------------------

_DUMP = """\
# == @deepseek-ai/dsh-base
- id: agent
  name: '@deepseek-ai/dsh-agent'
- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: deepseek-official
    model: deepseek-v4-flash
- id: system-prompt
  config:
    includeHarnessIdentity: false
    persona: |-
      你是《文明 VI》回合制策略智能体。
      第二行。
- id: agent-instructions
  config:
    maxBytes: 65536
  disabled: true
"""


def test_model_id_is_read_from_the_composed_config() -> None:
    """``deepseek-harness`` is a client label, not a model version."""

    rows = baseline._rows_for(_DUMP, "agent-default-model")

    assert rows == [{"provider": "deepseek-official", "model": "deepseek-v4-flash"}]


def test_block_scalar_persona_is_joined_for_a_meaningful_digest() -> None:
    rows = baseline._rows_for(_DUMP, "system-prompt")

    assert rows[0]["persona"] == (
        "你是《文明 VI》回合制策略智能体。\n第二行。"
    )
    assert rows[0]["includeHarnessIdentity"] == "false"


def test_a_row_without_config_does_not_borrow_the_next_rows_config() -> None:
    """``agent`` has no ``config:`` block, so the model must not leak into it."""

    rows = baseline._rows_for(_DUMP, "agent")

    assert rows == [{}]
    assert "model" not in rows[0]
    assert "provider" not in rows[0]


def test_disabled_modules_are_detected() -> None:
    """AGENTS.md only reaches the model through ``agent-instructions``."""

    assert baseline._row_is_disabled(_DUMP, "agent-instructions") is True
    assert baseline._row_is_disabled(_DUMP, "system-prompt") is False


def test_overlay_timeout_is_read_from_the_shipped_overlay() -> None:
    assert baseline._overlay_timeout_ms() == 3_600_000


# ---------------------------------------------------------------------------
# Backups must never be destructive
# ---------------------------------------------------------------------------


def test_backup_copies_and_leaves_the_original_untouched(tmp_path) -> None:
    source = tmp_path / "0_MCP_0141.Civ6Save"
    source.write_bytes(b"save-bytes")
    before = baseline.sha256_file(source)

    result = baseline.backup_save(source, tmp_path / "experiments" / "saves")

    assert result["copied"] is True
    assert pathlib.Path(result["path"]).read_bytes() == b"save-bytes"
    assert source.is_file(), "备份绝不能移动或删除原存档"
    assert baseline.sha256_file(source) == before


def test_backup_is_idempotent_for_identical_bytes(tmp_path) -> None:
    source = tmp_path / "0_MCP_0141.Civ6Save"
    source.write_bytes(b"save-bytes")
    destination = tmp_path / "out"

    first = baseline.backup_save(source, destination)
    second = baseline.backup_save(source, destination)

    assert first["copied"] is True
    assert second["copied"] is False
    assert "error" not in second


def test_backup_refuses_to_overwrite_different_bytes(tmp_path) -> None:
    """The game can rewrite a save in place; destroying the old copy loses evidence."""

    destination = tmp_path / "out"
    destination.mkdir()
    (destination / "0_MCP_0141.Civ6Save").write_bytes(b"older-branch")
    source = tmp_path / "0_MCP_0141.Civ6Save"
    source.write_bytes(b"newer-branch")

    result = baseline.backup_save(source, destination)

    assert result["copied"] is False
    assert "refusing to overwrite" in result["error"]
    assert (destination / "0_MCP_0141.Civ6Save").read_bytes() == b"older-branch"


# ---------------------------------------------------------------------------
# Manifest writing
# ---------------------------------------------------------------------------


def test_manifest_is_written_atomically(tmp_path) -> None:
    manifest = {"schema_version": 1, "run_id": "run"}

    path = baseline.write_manifest(manifest, tmp_path)

    assert json.loads(path.read_text(encoding="utf-8")) == manifest
    assert not (tmp_path / "manifest.json.tmp").exists()


def test_manifest_records_the_tool_surface_and_unverified_items(tmp_path) -> None:
    manifest = baseline.build_manifest(
        run_id="unit-run",
        play_profile="legacy",
        save_reference=str(FIXTURE_SAVE),
        backup=False,
        dsh_dir=tmp_path,  # no checkout here -> must degrade, not fail
        out_dir=tmp_path / "out",
    )

    assert manifest["run_id"] == "unit-run"
    assert manifest["tools"]["count"] > 0
    assert manifest["tools"]["surface_sha256"]
    assert manifest["dsh"]["tool_call_timeout_ms"] == 3_600_000
    assert manifest["dsh"]["unavailable"], "缺少 checkout 时必须显式标注不可用"
    assert manifest["live_only_unverified"], "现场未验证项必须显式列出"
    assert manifest["save"]["sha256"]


def test_manifest_flags_a_missing_save_instead_of_guessing(tmp_path) -> None:
    manifest = baseline.build_manifest(
        run_id="unit-run",
        play_profile="legacy",
        save_reference="definitely-not-a-save",
        backup=False,
        dsh_dir=tmp_path,
        out_dir=tmp_path / "out",
    )

    assert "unavailable" in manifest["save"]

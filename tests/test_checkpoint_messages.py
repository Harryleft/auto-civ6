"""Unregistered checkpoint names must explain themselves.

The live run could not recover from the native T60 autosave because only
CLI-registered checkpoints carry verifiable identity. The refusal is correct;
the message must say so and list what is usable, instead of an opaque
"checkpoint completion and expected identity are unavailable".
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from civ_mcp import checkpoints, game_launcher
from civ_mcp import game_state as gs_module
from civ_mcp.game_state import GameState


def prepare(monkeypatch, tmp_path):
    monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "records")
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))
    assert checkpoints.begin_baseline_run() is None
    for name, payload, turn in (("run_t51", b"t51", 51), ("run_t60", b"t60", 60)):
        (tmp_path / f"{name}.Civ6Save").write_bytes(payload)
        checkpoints.mark_complete(name)
        assert checkpoints.record_checkpoint(name, ("sumeria", -1896272000), turn) is None


def test_registered_names_lists_the_current_run_in_turn_order(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)

    assert checkpoints.registered_names() == ["run_t51", "run_t60"]


def test_native_autosave_name_is_never_registered(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    (tmp_path / "AutoSave_0100.Civ6Save").write_bytes(b"native autosave")

    assert checkpoints.expected_checkpoint("AutoSave_0100") is None
    assert "AutoSave_0100" not in checkpoints.registered_names()


def test_restart_message_names_the_problem_and_usable_checkpoints(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    state = GameState(SimpleNamespace())

    result = asyncio.run(state.restart_and_load("AutoSave_0100"))

    assert result.startswith("Error: UNREGISTERED_CHECKPOINT|")
    assert "no restart performed" in result
    assert "Native game autosaves (AutoSave_*) are never CLI-registered" in result
    assert "run_t51, run_t60" in result


def test_load_message_uses_the_same_explanation(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    state = GameState(SimpleNamespace())

    result = asyncio.run(state.load_game_save("AutoSave_0100"))

    assert result.startswith("Error: UNREGISTERED_CHECKPOINT|")
    assert "no load sent" in result
    assert "Registered checkpoints for this run: run_t51, run_t60." in result

"""Current baseline-run ownership at the checkpoint and public loader seams."""
import importlib

from civ_mcp import checkpoints, game_launcher


def test_starting_same_baseline_again_rejects_prior_run_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "records")
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))
    (tmp_path / "result.Civ6Save").write_bytes(b"complete run A")
    assert checkpoints.begin_baseline_run() is None
    checkpoints.mark_complete("result")
    assert checkpoints.record_checkpoint("result", ("sumeria", 719665280), 21) is None
    assert checkpoints.expected_checkpoint("result") == ("sumeria", 719665280, 21)
    assert checkpoints.begin_baseline_run() is None
    assert checkpoints.expected_checkpoint("result") is None


def test_run_ownership_survives_in_memory_completion_loss(monkeypatch, tmp_path):
    monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "records")
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))
    assert checkpoints.begin_baseline_run() is None
    (tmp_path / "result.Civ6Save").write_bytes(b"completed checkpoint")
    checkpoints.mark_complete("result")
    assert checkpoints.record_checkpoint("result", ("sumeria", 719665280), 21) is None
    importlib.reload(checkpoints)
    monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "records")
    assert checkpoints.expected_checkpoint("result") == ("sumeria", 719665280, 21)

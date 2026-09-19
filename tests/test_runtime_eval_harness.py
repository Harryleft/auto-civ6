"""M11 proof requirements for K1/J2 cutover evidence."""

from __future__ import annotations

import pytest

from civ_mcp.runtime.eval_harness import (
    EvidenceFormatError,
    assess_cutover_evidence,
    load_json_documents,
)


def _k1_documents(*, outcome: str = "ADVANCED", unknown: object = {}) -> tuple[dict, ...]:
    return (
        {
            "phase": "READ_CONTEXT",
            "game_id": "game-a",
            "branch_id": "game-a:before-recovery",
            "turn": 10,
            "unknown": unknown,
        },
        {
            "phase": "END_TURN",
            "outcome": outcome,
            "operation_id": "end-turn-10",
            "send_state": "MAYBE_SENT",
            "operation_outcome": "CONFIRMED",
            "evidence": [
                {
                    "source": "read_overview",
                    "observed_turn": 11,
                    "detail": "turn advanced",
                }
            ],
        },
    )


def _j2_documents(*, branch: str = "game-a:after-recovery") -> tuple[dict, ...]:
    return (
        {
            "outcome": "RECOVERED",
            "reason": "recovered",
            "new_branch_id": branch,
            "checkpoint": {
                "id": "AutoSave_0010",
                "game_id": "game-a",
                "expected_turn": 10,
            },
        },
    )


def test_evaluator_accepts_evidence_only_when_k1_and_j2_close_their_contracts() -> None:
    report = assess_cutover_evidence(_k1_documents(), _j2_documents())

    assert report.approved is True
    assert report.blockers == ()
    assert report.k1_branch_id == "game-a:before-recovery"
    assert report.recovered_branch_id == "game-a:after-recovery"


def test_evaluator_rejects_unknown_or_a_non_advancing_end_turn() -> None:
    report = assess_cutover_evidence(
        _k1_documents(outcome="RECOVERY_REQUIRED", unknown={"cities": "timeout"}),
        _j2_documents(),
    )

    assert report.approved is False
    assert any("unknown" in blocker for blocker in report.blockers)
    assert any("ADVANCED" in blocker for blocker in report.blockers)


def test_evaluator_rejects_recovery_on_the_old_branch_or_another_game() -> None:
    invalid_recovery = _j2_documents(branch="game-b:before-recovery")
    invalid_recovery[0]["checkpoint"]["game_id"] = "game-b"

    report = assess_cutover_evidence(_k1_documents(), invalid_recovery)

    assert report.approved is False
    assert any("game_id" in blocker for blocker in report.blockers)


def test_loader_accepts_k1s_consecutive_json_documents(tmp_path) -> None:
    output = tmp_path / "k1.stdout"
    output.write_text('{"phase":"READ_CONTEXT"}\n{"phase":"END_TURN"}\n', encoding="utf-8")

    assert load_json_documents(output) == (
        {"phase": "READ_CONTEXT"},
        {"phase": "END_TURN"},
    )


def test_loader_rejects_non_json_stdout(tmp_path) -> None:
    output = tmp_path / "bad.stdout"
    output.write_text("connection refused\n", encoding="utf-8")

    with pytest.raises(EvidenceFormatError, match="非 JSON"):
        load_json_documents(output)

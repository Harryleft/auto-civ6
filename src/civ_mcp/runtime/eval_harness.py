"""Read-only acceptance evaluator for Runtime Core live-game evidence.

The evaluator consumes outputs captured from manual K1 and J2 runs.  It does
not open FireTuner, create a Runtime session, change an OperationRecord, or
switch the production entry point.  A green report is evidence for review, not
an authorization to perform K1--K4 automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


class EvidenceFormatError(ValueError):
    """A supplied manual-evidence file is not the expected JSON sequence."""


@dataclass(frozen=True, slots=True)
class CutoverEvidenceReport:
    """A deterministic assessment of captured K1/J2 evidence."""

    approved: bool
    blockers: tuple[str, ...]
    game_id: str | None
    k1_branch_id: str | None
    recovered_branch_id: str | None


def load_json_documents(path: Path | str) -> tuple[dict[str, Any], ...]:
    """Read one or more consecutive JSON documents captured from stdout."""
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    documents: list[dict[str, Any]] = []
    offset = 0
    while True:
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        try:
            decoded, offset = decoder.raw_decode(text, offset)
        except json.JSONDecodeError as exc:
            raise EvidenceFormatError(f"{source} 包含无法解析的非 JSON 输出。") from exc
        if not isinstance(decoded, dict):
            raise EvidenceFormatError(f"{source} 的每段证据必须是 JSON object。")
        documents.append(decoded)
    if not documents:
        raise EvidenceFormatError(f"{source} 没有任何 JSON 证据。")
    return tuple(documents)


def assess_cutover_evidence(
    k1_documents: tuple[dict[str, Any], ...],
    j2_documents: tuple[dict[str, Any], ...],
) -> CutoverEvidenceReport:
    """Require real successful K1 end-turn and J2 recovery observations."""
    blockers: list[str] = []
    context = _one_document(k1_documents, "READ_CONTEXT", blockers)
    end_turn = _one_document(k1_documents, "END_TURN", blockers)
    recovery = _one_recovery_document(j2_documents, blockers)

    game_id = _text(context, "game_id", blockers, "K1 game_id") if context else None
    branch_id = _text(context, "branch_id", blockers, "K1 branch_id") if context else None
    initial_turn = _nonnegative_int(context, "turn", blockers, "K1 初始 turn") if context else None

    if context is not None and context.get("unknown"):
        blockers.append("K1 context 含有 unknown，不能作为完整现场读取证据。")
    if end_turn is not None:
        if end_turn.get("outcome") != "ADVANCED":
            blockers.append("K1 end_turn 没有返回 ADVANCED。")
        if end_turn.get("send_state") != "MAYBE_SENT":
            blockers.append("K1 end_turn 没有跨越唯一发送边界。")
        if end_turn.get("operation_outcome") != "CONFIRMED":
            blockers.append("K1 end_turn 没有以领域 Evidence 确认。")
        _text(end_turn, "operation_id", blockers, "K1 end_turn operation_id")
        evidence = end_turn.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            blockers.append("K1 end_turn 缺少领域 Evidence。")
        elif initial_turn is not None and not any(
            isinstance(item, dict)
            and isinstance(item.get("observed_turn"), int)
            and item["observed_turn"] > initial_turn
            for item in evidence
        ):
            blockers.append("K1 Evidence 没有证明 turn 已推进。")

    recovered_branch = None
    if recovery is not None:
        if recovery.get("outcome") != "RECOVERED":
            blockers.append("J2 recovery 没有返回 RECOVERED。")
        recovered_branch = _text(
            recovery, "new_branch_id", blockers, "J2 new_branch_id"
        )
        checkpoint = recovery.get("checkpoint")
        if not isinstance(checkpoint, dict):
            blockers.append("J2 recovery 缺少已验证 checkpoint。")
        else:
            _text(checkpoint, "id", blockers, "J2 checkpoint id")
            checkpoint_game = _text(
                checkpoint, "game_id", blockers, "J2 checkpoint game_id"
            )
            _nonnegative_int(checkpoint, "expected_turn", blockers, "J2 checkpoint turn")
            if game_id is not None and checkpoint_game is not None and checkpoint_game != game_id:
                blockers.append("J2 checkpoint game_id 与 K1 game_id 不一致。")
        if game_id is not None and recovered_branch is not None:
            if not recovered_branch.startswith(f"{game_id}:"):
                blockers.append("J2 new_branch_id 未绑定 K1 game_id。")
            if recovered_branch == branch_id:
                blockers.append("J2 recovery 复用了 K1 branch_id。")

    return CutoverEvidenceReport(
        approved=not blockers,
        blockers=tuple(blockers),
        game_id=game_id,
        k1_branch_id=branch_id,
        recovered_branch_id=recovered_branch,
    )


def _one_document(
    documents: tuple[dict[str, Any], ...], phase: str, blockers: list[str]
) -> dict[str, Any] | None:
    matches = [document for document in documents if document.get("phase") == phase]
    if len(matches) != 1:
        blockers.append(f"K1 证据必须恰有一段 phase={phase}。")
        return None
    return matches[0]


def _one_recovery_document(
    documents: tuple[dict[str, Any], ...], blockers: list[str]
) -> dict[str, Any] | None:
    if len(documents) != 1:
        blockers.append("J2 证据必须恰有一个 JSON object。")
        return None
    return documents[0]


def _text(
    payload: dict[str, Any], field: str, blockers: list[str], label: str
) -> str | None:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"{label} 缺失或为空。")
        return None
    return value


def _nonnegative_int(
    payload: dict[str, Any], field: str, blockers: list[str], label: str
) -> int | None:
    value = payload.get(field)
    if not isinstance(value, int) or value < 0:
        blockers.append(f"{label} 缺失或非法。")
        return None
    return value

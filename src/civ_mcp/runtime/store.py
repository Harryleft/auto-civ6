"""SQLite persistence for Runtime operation facts.

The store persists execution continuity only.  It does not contain a world
model, a prediction engine, or any model-facing method that can change an
operation's lifecycle.  Callers pass immutable ``OperationRecord`` instances
created by the trusted SessionKernel boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Self

from civ_mcp.runtime.contracts import (
    BranchIdentity,
    ContractViolation,
    Evidence,
    GameIdentity,
    IntentMismatchError,
    OperationId,
    OperationIntent,
    OperationRecord,
    OutcomeState,
    SendState,
)


class OperationStoreError(RuntimeError):
    """A SQLite failure left operation facts unchanged or indeterminate."""


class OperationBranchMismatchError(ContractViolation):
    """An operation from an abandoned branch was requested for another one."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise OperationStoreError("Operation Store 中存在没有时区的时间。")
    return parsed


@dataclass(frozen=True, slots=True)
class HandoffNote:
    """Human/model strategic continuity, separate from executable facts."""

    game_id: GameIdentity
    branch_id: BranchIdentity
    strategic_focus: str
    existing_arrangements: str
    rationale: str
    change_conditions: str
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.branch_id.game_id != self.game_id:
            raise ContractViolation("handoff note 的 branch_id 必须属于 game_id。")


class OperationStore:
    """The sole SQLite owner for games, branches, operations and handoffs."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self.path, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._create_schema()
        except sqlite3.Error as exc:
            self.close()
            raise OperationStoreError("无法初始化 Operation Store。") from exc

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def save_operation(self, record: OperationRecord) -> None:
        """Atomically persist one valid lifecycle state and its evidence."""
        connection = self._require_connection()
        try:
            with connection:
                self._ensure_game_and_branch(record)
                existing = self._find_operation_row(record.operation_id)
                if existing is None:
                    self._insert_operation(record)
                else:
                    self._validate_existing_operation(existing, record)
                    self._update_operation(record)
                self._append_evidence(record)
        except sqlite3.Error as exc:
            raise OperationStoreError(
                "Operation Store 写入失败；没有将游戏执行结果伪造成已确认。"
            ) from exc

    def get_operation(self, operation_id: OperationId) -> OperationRecord | None:
        row = self._find_operation_row(operation_id)
        return self._record_from_row(row) if row is not None else None

    def get_operation_for_branch(
        self, operation_id: OperationId, branch_id: BranchIdentity
    ) -> OperationRecord | None:
        record = self.get_operation(operation_id)
        if record is not None and record.branch_id != branch_id:
            raise OperationBranchMismatchError(
                "operation 属于另一条 branch，不能在当前对局分支复用。"
            )
        return record

    def save_handoff_note(self, note: HandoffNote) -> None:
        """Save strategy-only context without touching operation facts."""
        connection = self._require_connection()
        try:
            with connection:
                self._ensure_game_and_branch_id(note.game_id, note.branch_id)
                connection.execute(
                    """
                    INSERT INTO handoff_notes (
                        branch_id, game_id, strategic_focus, existing_arrangements,
                        rationale, change_conditions, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(branch_id) DO UPDATE SET
                        strategic_focus = excluded.strategic_focus,
                        existing_arrangements = excluded.existing_arrangements,
                        rationale = excluded.rationale,
                        change_conditions = excluded.change_conditions,
                        updated_at = excluded.updated_at
                    """,
                    (
                        note.branch_id.value,
                        note.game_id.value,
                        note.strategic_focus,
                        note.existing_arrangements,
                        note.rationale,
                        note.change_conditions,
                        _timestamp(note.updated_at),
                    ),
                )
        except sqlite3.Error as exc:
            raise OperationStoreError("Handoff note 写入失败。") from exc

    def get_handoff_note(self, branch_id: BranchIdentity) -> HandoffNote | None:
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT game_id, branch_id, strategic_focus, existing_arrangements,
                   rationale, change_conditions, updated_at
            FROM handoff_notes WHERE branch_id = ?
            """,
            (branch_id.value,),
        ).fetchone()
        if row is None:
            return None
        game_id = GameIdentity(row["game_id"])
        note_branch = BranchIdentity(game_id, row["branch_id"])
        if note_branch != branch_id:
            raise OperationBranchMismatchError("handoff note 的 game identity 不匹配。")
        return HandoffNote(
            game_id=game_id,
            branch_id=note_branch,
            strategic_focus=row["strategic_focus"],
            existing_arrangements=row["existing_arrangements"],
            rationale=row["rationale"],
            change_conditions=row["change_conditions"],
            updated_at=_parse_timestamp(row["updated_at"]),
        )

    def _create_schema(self) -> None:
        connection = self._require_connection()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS games (
                game_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS branches (
                branch_id TEXT PRIMARY KEY,
                game_id TEXT NOT NULL REFERENCES games(game_id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                game_id TEXT NOT NULL REFERENCES games(game_id),
                branch_id TEXT NOT NULL REFERENCES branches(branch_id),
                decision_turn INTEGER NOT NULL,
                tool TEXT NOT NULL,
                intent_json TEXT NOT NULL,
                intent_hash TEXT NOT NULL,
                send_state TEXT NOT NULL,
                outcome_state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS operation_evidence (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                evidence_index INTEGER NOT NULL,
                source TEXT NOT NULL,
                observed_turn INTEGER,
                detail TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                PRIMARY KEY (operation_id, evidence_index)
            );

            CREATE TABLE IF NOT EXISTS handoff_notes (
                branch_id TEXT PRIMARY KEY REFERENCES branches(branch_id),
                game_id TEXT NOT NULL REFERENCES games(game_id),
                strategic_focus TEXT NOT NULL,
                existing_arrangements TEXT NOT NULL,
                rationale TEXT NOT NULL,
                change_conditions TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    def _ensure_game_and_branch(self, record: OperationRecord) -> None:
        self._ensure_game_and_branch_id(record.game_id, record.branch_id)

    def _ensure_game_and_branch_id(
        self, game_id: GameIdentity, branch_id: BranchIdentity
    ) -> None:
        connection = self._require_connection()
        now = _timestamp(_utc_now())
        connection.execute(
            "INSERT OR IGNORE INTO games (game_id, created_at) VALUES (?, ?)",
            (game_id.value, now),
        )
        existing = connection.execute(
            "SELECT game_id FROM branches WHERE branch_id = ?", (branch_id.value,)
        ).fetchone()
        if existing is not None and existing["game_id"] != game_id.value:
            raise OperationBranchMismatchError("branch_id 已被另一局游戏占用。")
        connection.execute(
            "INSERT OR IGNORE INTO branches (branch_id, game_id, created_at) VALUES (?, ?, ?)",
            (branch_id.value, game_id.value, now),
        )

    def _find_operation_row(self, operation_id: OperationId) -> sqlite3.Row | None:
        connection = self._require_connection()
        return connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id.value,)
        ).fetchone()

    def _insert_operation(self, record: OperationRecord) -> None:
        connection = self._require_connection()
        connection.execute(
            """
            INSERT INTO operations (
                operation_id, game_id, branch_id, decision_turn, tool, intent_json,
                intent_hash, send_state, outcome_state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _operation_values(record),
        )

    def _update_operation(self, record: OperationRecord) -> None:
        connection = self._require_connection()
        connection.execute(
            """
            UPDATE operations SET send_state = ?, outcome_state = ?, updated_at = ?
            WHERE operation_id = ?
            """,
            (
                record.send_state.value,
                record.outcome_state.value,
                _timestamp(record.updated_at),
                record.operation_id.value,
            ),
        )

    def _append_evidence(self, record: OperationRecord) -> None:
        connection = self._require_connection()
        persisted = connection.execute(
            """
            SELECT evidence_index, source, observed_turn, detail, captured_at
            FROM operation_evidence WHERE operation_id = ? ORDER BY evidence_index
            """,
            (record.operation_id.value,),
        ).fetchall()
        if len(persisted) > len(record.evidence):
            raise ContractViolation("operation evidence 只能追加，不能删除。")
        for index, row in enumerate(persisted):
            expected = record.evidence[index]
            if (
                row["source"],
                row["observed_turn"],
                row["detail"],
                row["captured_at"],
            ) != (
                expected.source,
                expected.observed_turn,
                expected.detail,
                _timestamp(expected.captured_at),
            ):
                raise ContractViolation("operation evidence 只能追加，不能改写。")
        for index, evidence in enumerate(record.evidence[len(persisted):], start=len(persisted)):
            connection.execute(
                """
                INSERT INTO operation_evidence (
                    operation_id, evidence_index, source, observed_turn, detail, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.operation_id.value,
                    index,
                    evidence.source,
                    evidence.observed_turn,
                    evidence.detail,
                    _timestamp(evidence.captured_at),
                ),
            )

    def _validate_existing_operation(
        self, existing: sqlite3.Row, incoming: OperationRecord
    ) -> None:
        current = self._record_from_row(existing)
        if current.intent.intent_hash != incoming.intent.intent_hash:
            raise IntentMismatchError("同一个 operation_id 不能绑定不同的 intent_hash。")
        if (
            current.game_id != incoming.game_id
            or current.branch_id != incoming.branch_id
            or current.decision_turn != incoming.decision_turn
            or current.intent.tool != incoming.intent.tool
            or current.intent.arguments_json != incoming.intent.arguments_json
        ):
            raise ContractViolation("已有 operation 的 identity 或 intent 不能改写。")
        if (
            current.send_state != incoming.send_state
            or current.outcome_state != incoming.outcome_state
        ):
            _validate_transition(current, incoming)

    def _record_from_row(self, row: sqlite3.Row) -> OperationRecord:
        game_id = GameIdentity(row["game_id"])
        branch_id = BranchIdentity(game_id, row["branch_id"])
        evidence_rows = self._require_connection().execute(
            """
            SELECT source, observed_turn, detail, captured_at
            FROM operation_evidence WHERE operation_id = ? ORDER BY evidence_index
            """,
            (row["operation_id"],),
        ).fetchall()
        return OperationRecord(
            operation_id=OperationId(row["operation_id"]),
            game_id=game_id,
            branch_id=branch_id,
            decision_turn=row["decision_turn"],
            intent=OperationIntent(
                tool=row["tool"],
                arguments_json=row["intent_json"],
                intent_hash=row["intent_hash"],
            ),
            send_state=SendState(row["send_state"]),
            outcome_state=OutcomeState(row["outcome_state"]),
            evidence=tuple(
                Evidence(
                    source=evidence["source"],
                    observed_turn=evidence["observed_turn"],
                    detail=evidence["detail"],
                    captured_at=_parse_timestamp(evidence["captured_at"]),
                )
                for evidence in evidence_rows
            ),
            created_at=_parse_timestamp(row["created_at"]),
            updated_at=_parse_timestamp(row["updated_at"]),
        )

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise OperationStoreError("Operation Store 已关闭。")
        return self._connection


def _operation_values(record: OperationRecord) -> tuple[object, ...]:
    return (
        record.operation_id.value,
        record.game_id.value,
        record.branch_id.value,
        record.decision_turn,
        record.intent.tool,
        record.intent.arguments_json,
        record.intent.intent_hash,
        record.send_state.value,
        record.outcome_state.value,
        _timestamp(record.created_at),
        _timestamp(record.updated_at),
    )


def _validate_transition(current: OperationRecord, incoming: OperationRecord) -> None:
    allowed = {
        (SendState.CREATED, OutcomeState.NOT_STARTED): {
            (SendState.PRECHECKED, OutcomeState.NOT_STARTED),
            (SendState.NOT_SENT, OutcomeState.STALE_INTENT),
        },
        (SendState.PRECHECKED, OutcomeState.NOT_STARTED): {
            (SendState.SENDING, OutcomeState.NOT_STARTED),
            (SendState.NOT_SENT, OutcomeState.STALE_INTENT),
        },
        (SendState.SENDING, OutcomeState.NOT_STARTED): {
            (SendState.NOT_SENT, OutcomeState.NOT_STARTED),
            (SendState.MAYBE_SENT, OutcomeState.OBSERVING),
        },
        (SendState.MAYBE_SENT, OutcomeState.OBSERVING): {
            (SendState.MAYBE_SENT, OutcomeState.UNKNOWN),
            (SendState.MAYBE_SENT, OutcomeState.CONFIRMED),
            (SendState.MAYBE_SENT, OutcomeState.REJECTED),
        },
        (SendState.MAYBE_SENT, OutcomeState.UNKNOWN): {
            (SendState.MAYBE_SENT, OutcomeState.OBSERVING),
            (SendState.MAYBE_SENT, OutcomeState.CONFIRMED),
            (SendState.MAYBE_SENT, OutcomeState.REJECTED),
        },
    }
    previous = (current.send_state, current.outcome_state)
    next_state = (incoming.send_state, incoming.outcome_state)
    if next_state not in allowed.get(previous, set()):
        raise ContractViolation(
            f"Operation Store 拒绝无效状态迁移：{previous} -> {next_state}。"
        )

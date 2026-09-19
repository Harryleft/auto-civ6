"""Schema checks for Runtime Core Replacement task C1."""

from __future__ import annotations

import sqlite3

from civ_mcp.runtime.store import OperationStore


def test_operation_store_creates_the_runtime_owned_tables(tmp_path) -> None:
    database = tmp_path / "runtime.sqlite3"
    with OperationStore(database):
        pass

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {"games", "branches", "operations", "operation_evidence", "handoff_notes"} <= tables

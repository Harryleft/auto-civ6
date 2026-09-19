"""Manual K1 smoke for the isolated Runtime Core.

Run this file directly, never through pytest.  It opens the new Runtime
connection and reads the current context.  It sends an end-turn only when the
operator explicitly passes ``--confirm-end-turn``; a decision interrupt or an
unknown outcome is reported and never retried here.

Examples:

    .venv/bin/python tests/manual/test_runtime_core_smoke.py \
      --branch smoke-20260920 --store /tmp/civ6-runtime-smoke.sqlite3

    .venv/bin/python tests/manual/test_runtime_core_smoke.py \
      --branch smoke-20260920 --store /tmp/civ6-runtime-smoke.sqlite3 \
      --confirm-end-turn
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import NoReturn


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from civ_mcp.civ.adapter import CivAdapter
from civ_mcp.runtime.bootstrap import RuntimeAssembly, assemble_runtime
from civ_mcp.runtime.contracts import OperationId
from civ_mcp.runtime.connection import RuntimeConnection
from civ_mcp.runtime.context import RuntimeContext
from civ_mcp.runtime.store import OperationStore
from civ_mcp.runtime.turn import TurnOutcome, TurnResult


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a manual Runtime Core K1 smoke.")
    parser.add_argument(
        "--branch",
        required=True,
        help="host-selected stable branch token for this concrete save timeline",
    )
    parser.add_argument(
        "--store",
        required=True,
        type=Path,
        help="explicit SQLite operation-store path; it is not deleted after the smoke",
    )
    parser.add_argument(
        "--confirm-end-turn",
        action="store_true",
        help="send exactly one end-turn operation after the read-only phase",
    )
    return parser.parse_args()


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def _context_payload(
    assembly: RuntimeAssembly, context: RuntimeContext
) -> dict[str, object]:
    overview = context.facts["overview"]
    return {
        "phase": "READ_CONTEXT",
        "game_id": assembly.binding.game_id.value,
        "branch_id": assembly.binding.branch_id.value,
        "turn": overview.value.turn,
        "unknown": context.unknown,
        "pending_decisions": [
            {
                "type": decision.decision_type,
                "allowed_choices": decision.allowed_choices,
                "operation_id": decision.continuation_operation_id.value,
            }
            for decision in context.pending_decisions
        ],
        "unfinished_operation_ids": [
            operation.operation_id.value for operation in context.unfinished_intents
        ],
    }


def _turn_payload(result: TurnResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "phase": "END_TURN",
        "outcome": result.outcome.value,
        "reason": result.reason,
        "operation_id": result.operation.operation_id.value,
        "send_state": result.operation.send_state.value,
        "operation_outcome": result.operation.outcome_state.value,
        "evidence": [
            {
                "source": evidence.source,
                "observed_turn": evidence.observed_turn,
                "detail": evidence.detail,
            }
            for evidence in result.operation.evidence
        ],
    }
    if result.decision is not None:
        payload["decision"] = {
            "type": result.decision.decision_type,
            "facts": result.decision.facts,
            "allowed_choices": result.decision.allowed_choices,
            "continuation_operation_id": result.decision.continuation_operation_id.value,
        }
    return payload


async def _no_immediate_evidence():
    """Make TurnLoop collect only fresh observer evidence after the send."""
    return None


async def _run(args: argparse.Namespace) -> int:
    connection: RuntimeConnection | None = None
    store: OperationStore | None = None
    try:
        connection = await RuntimeConnection.connect()
        adapter = CivAdapter(connection.transport, state_resolver=connection.state_for)
        store = OperationStore(args.store)
        assembly = await assemble_runtime(adapter, store, branch_token=args.branch)

        context = await assembly.surface.get_context()
        _print_json(_context_payload(assembly, context))
        if context.unknown:
            print("K1 read phase is incomplete; no mutation was sent.", file=sys.stderr)
            return 2
        if not args.confirm_end_turn:
            return 0

        result = await assembly.surface.end_turn(
            assembly.mutations.end_turn(
                operation_id=OperationId.new(),
                readback=_no_immediate_evidence,
            ),
            decision_turn=context.facts["overview"].value.turn,
        )
        _print_json(_turn_payload(result))
        if result.outcome is not TurnOutcome.ADVANCED:
            print(
                "K1 end-turn phase did not advance. Do not rerun it or apply a decision automatically; "
                "preserve this operation record for explicit diagnosis.",
                file=sys.stderr,
            )
            return 2
        return 0
    finally:
        if store is not None:
            store.close()
        if connection is not None:
            await connection.close()


def main() -> NoReturn:
    args = _arguments()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()

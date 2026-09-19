"""Manual J2 recovery verification for a checkpoint already loaded by the host.

This script never starts Civ6, loads a save, sends a mutation, or retries an
operation.  The operator performs those host-owned actions first; the script
then uses the new Runtime connection to prove that the loaded checkpoint has a
stable identity and the expected turn before a new branch may be bound.

Example:

    .venv/bin/python tests/manual/test_runtime_recovery_smoke.py \\
      --game-id civilization_france_42 \\
      --old-branch before-recovery \\
      --checkpoint AutoSave_0010 \\
      --checkpoint-turn 10 \\
      --new-branch after-recovery
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
from civ_mcp.runtime.connection import RuntimeConnection
from civ_mcp.runtime.contracts import BranchIdentity, GameIdentity
from civ_mcp.runtime.recovery import (
    RecoveryCheckpoint,
    RecoveryInput,
    RecoveryOutcome,
    RecoverySupervisor,
    VerifiedRecoveryDriver,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify a host-loaded Civ6 checkpoint with the isolated Runtime."
    )
    parser.add_argument(
        "--game-id",
        required=True,
        help="game_id captured before recovery; it must match the loaded game",
    )
    parser.add_argument(
        "--old-branch",
        required=True,
        help="host token for the abandoned branch; do not include game_id:",
    )
    parser.add_argument(
        "--checkpoint",
        required=True,
        help="exact host checkpoint identifier that was loaded",
    )
    parser.add_argument(
        "--checkpoint-turn",
        type=int,
        required=True,
        help="turn recorded for that checkpoint before recovery",
    )
    parser.add_argument(
        "--new-branch",
        required=True,
        help="new host token for the recovered timeline; do not include game_id:",
    )
    return parser.parse_args()


def _payload(result) -> dict[str, object]:
    return {
        "outcome": result.outcome.value,
        "reason": result.reason,
        "new_branch_id": result.new_branch.value if result.new_branch is not None else None,
        "checkpoint": (
            {
                "id": result.checkpoint.checkpoint_id,
                "game_id": result.checkpoint.game_id.value,
                "expected_turn": result.checkpoint.expected_turn,
            }
            if result.checkpoint is not None
            else None
        ),
    }


async def _run(args: argparse.Namespace) -> int:
    connection: RuntimeConnection | None = None
    try:
        game_id = GameIdentity(args.game_id)
        checkpoint = RecoveryCheckpoint(
            args.checkpoint,
            game_id,
            args.checkpoint_turn,
        )
        request = RecoveryInput(
            game_id=game_id,
            current_branch=BranchIdentity.from_token(game_id, args.old_branch),
            operation=None,
            process_running=True,
            connection_available=False,
            checkpoints=(checkpoint,),
        )
        connection = await RuntimeConnection.connect()
        adapter = CivAdapter(connection.transport, state_resolver=connection.state_for)
        driver = VerifiedRecoveryDriver(
            adapter,
            checkpoint_id=checkpoint.checkpoint_id,
            branch_token=args.new_branch,
        )
        result = await RecoverySupervisor(driver).recover(request)
        print(json.dumps(_payload(result), ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result.outcome is RecoveryOutcome.RECOVERED else 2
    finally:
        if connection is not None:
            await connection.close()


def main() -> NoReturn:
    raise SystemExit(asyncio.run(_run(_arguments())))


if __name__ == "__main__":
    main()

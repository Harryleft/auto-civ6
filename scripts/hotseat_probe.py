#!/usr/bin/env python3
"""Bounded native-context console for the authorized hotseat experiment.

Reuses GameConnection, releases FireTuner after each invocation, and refuses
to resubmit a recorded mutation operation ID. Transport return is not a game
completion claim: the operator must inspect native receipts and read back.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
from pathlib import Path
import time

from civ_mcp.connection import GameConnection


async def execute(args) -> dict:
    code = args.code.read_text() if args.code else None
    record = {"context": args.context, "operation": args.operation,
              "started": time.time(), "code_sha256": hashlib.sha256((code or "").encode()).hexdigest()}
    operation_file = None
    if args.operation:
        if not args.code or not args.operation.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Mutation needs a source file and simple unique operation ID")
        operation_file = args.run / "evidence" / ("operation-" + args.operation + ".json")
        with operation_file.open("x") as stream:
            json.dump(record | {"state": "possibly_submitted"}, stream, indent=2)
    connection = GameConnection()
    try:
        await connection.connect()
        record["contexts"] = connection.lua_states
        if code:
            if args.context not in connection.lua_states.values():
                record["state"] = "not_submitted_context_missing"
                raise ValueError(f"Native context {args.context!r} absent; no command submitted")
            record["rows"] = await connection.execute_in_named_state(args.context, code, timeout=args.timeout)
        record["state"] = "response_received"
    except Exception as exc:
        record.setdefault("state", "unknown" if args.operation else "read_failed")
        record["error"] = str(exc)
    finally:
        await connection.disconnect()
    record["finished"] = time.time()
    if operation_file:
        operation_file.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    if args.output:
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--context", default="InGame")
    parser.add_argument("--code", type=Path)
    parser.add_argument("--operation")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=8)
    args = parser.parse_args()
    if args.timeout <= 0 or args.timeout > 30:
        parser.error("Per-command timeout must be between 0 and 30 seconds")
    with (args.run / "evidence" / "control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = asyncio.run(execute(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result.get("error") else 0)

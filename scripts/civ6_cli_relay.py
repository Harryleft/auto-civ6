#!/usr/bin/env python3
"""Foreground stdin relay for two local models sharing one civ6 debug session.

This transport executes no game API and selects no strategy. Each request is
one literal CLI command. Lost client replies are recorded, never replayed.
Stop with the explicit CLI quit command, after pending has been resolved.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

from civ_mcp.devtools.turn_timing import TurnTiming

DONE = {"tool_result", "tool_outcome_unknown", "command_blocked", "dry_run",
        "help", "tools", "tool_schema", "pending", "verification", "session_closing"}


def write(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def receive_command(event, command):
    """Consume one command, including all nested verification RPC results."""
    first = event()
    if first.get("event") != "command" or first.get("input") != command:
        raise RuntimeError("CLI command stream mismatch; retain original context")
    events = [first]
    completion = DONE if command.strip() != "verify state" else {
        "verification", "command_blocked", "session_closing"}
    while True:
        value = event()
        events.append(value)
        if value.get("event") == "command":
            raise RuntimeError("CLI command stream mismatch; retain original context")
        if value.get("event") in completion:
            return events


def serve(args):
    args.evidence.mkdir(parents=True, exist_ok=False)
    requests = args.evidence / "requests"
    requests.mkdir()
    timing = TurnTiming()

    def receipt(path, response):
        write(path, response)
        try:
            timing.observe(response)
            timing.export(args.evidence)
        except Exception as error:
            # Statistics never change the outcome or ownership of a game call.
            print(json.dumps({"event": "timing_export_failed", "error": str(error)}), flush=True)
    command = ["uv", "run", "--locked", "civ6", "--output",
               str(args.evidence / "cli"), "debug"]
    if args.offline:
        command.append("--offline")
    with (args.evidence / "stderr.log").open("w") as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, bufsize=1)
        with (args.evidence / "stdout.jsonl").open("a") as transcript:
            def event():
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError("CLI exited; retain evidence, do not replace its context")
                transcript.write(line)
                transcript.flush()
                return json.loads(line)

            while True:
                value = event()
                if value.get("event") == "session_ready":
                    write(args.evidence / "ready.json", value)
                    break
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(args.socket))
            os.chmod(args.socket, 0o600)
            listener.listen(4)
            print(json.dumps({"event": "relay_ready", "cli_pid": process.pid,
                              "socket": str(args.socket)}), flush=True)
            stopping = False
            transport_unknown = None
            while not stopping:
                client, _ = listener.accept()
                with client:
                    client.settimeout(5)
                    try:
                        request = json.loads(client.makefile("r").readline(131072))
                        request_id = request["request_id"]
                        uuid.UUID(request_id)
                        line = request["command"]
                        if not isinstance(line, str) or not line.strip() or "\n" in line or "\r" in line:
                            raise ValueError("Exactly one nonempty CLI command is required")
                        path = requests / (request_id + ".json")
                    except (ValueError, KeyError, TypeError, OSError) as error:
                        client.sendall((json.dumps({"state": "not_submitted", "error": str(error)}) + "\n").encode())
                        continue
                    if path.exists():
                        response = json.loads(path.read_text())
                        if any(response.get(k) != request.get(k) for k in ("actor", "command")):
                            client.sendall(b'{"state":"not_submitted","error":"Request ID belongs to different original parameters"}\n')
                            continue
                    elif transport_unknown:
                        response = request | {"state": "not_submitted",
                                              "error": "Original CLI transport outcome is unresolved",
                                              "original_receipt": transport_unknown}
                    else:
                        response = request | {"started": time.time(), "started_monotonic": time.monotonic(),
                                              "state": "possibly_submitted"}
                        write(path, response)
                        try:
                            process.stdin.write(line + "\n")
                            process.stdin.flush()
                            events = receive_command(event, line)
                        except Exception as error:
                            # Keep the CLI/MCP owner alive. A framing failure
                            # cannot authorize replacing its pending context.
                            transport_unknown = str(path)
                            response.update(state="outcome_unknown", error=str(error), finished=time.time(),
                                            finished_monotonic=time.monotonic())
                            receipt(path, response)
                            try:
                                client.sendall((json.dumps(response) + "\n").encode())
                            except OSError:
                                pass
                            continue
                        value = events[-1]
                        response.update(state="response_received", finished=time.time(),
                                        finished_monotonic=time.monotonic(), events=events)
                        receipt(path, response)
                        if value.get("event") == "session_closing":
                            # Drain the CLI summary before closing its only input.
                            for tail in process.stdout:
                                transcript.write(tail)
                            response["cli_returncode"] = process.wait(timeout=20)
                            write(path, response)
                            stopping = True
                    try:
                        client.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode())
                    except OSError:
                        print(json.dumps({"event": "client_reply_lost", "receipt": str(path)}), flush=True)
            listener.close()
            args.socket.unlink()


def send(args):
    request = {"request_id": args.request_id or str(uuid.uuid4()), "actor": args.actor,
               "command": args.command}
    # Print the request identity before connecting; no automatic retry on loss.
    print(json.dumps({"request": request}), file=sys.stderr, flush=True)
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(380)
        client.connect(str(args.socket))
        client.sendall((json.dumps(request) + "\n").encode())
        response = json.loads(client.makefile("r").readline())
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    owner = commands.add_parser("serve")
    owner.add_argument("--socket", type=Path, required=True)
    owner.add_argument("--evidence", type=Path, required=True)
    owner.add_argument("--offline", action="store_true")
    caller = commands.add_parser("send")
    caller.add_argument("--socket", type=Path, required=True)
    caller.add_argument("--actor", required=True)
    caller.add_argument("--command", required=True)
    caller.add_argument("--request-id")
    options = parser.parse_args()
    serve(options) if options.mode == "serve" else send(options)

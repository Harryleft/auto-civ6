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
import secrets
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
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def listen(path, *, force_tcp=False):
    """Keep Unix sockets, with authenticated loopback IPC on Windows.

    The endpoint file is exclusive and private. No external interface, stable
    port, credential persistence outside this run, or unauthenticated caller is
    introduced by the Windows fallback.
    """
    tcp = force_tcp or not hasattr(socket, "AF_UNIX")
    listener = socket.socket(socket.AF_INET if tcp else socket.AF_UNIX)
    try:
        if tcp:
            listener.bind(("127.0.0.1", 0))
            token = secrets.token_hex(32)
            endpoint = {"transport": "tcp", "host": "127.0.0.1",
                        "port": listener.getsockname()[1], "token": token}
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(endpoint, stream)
        else:
            listener.bind(str(path))
            os.chmod(path, 0o600)
            token = None
        listener.listen(4)
        return listener, token
    except BaseException:
        listener.close()
        raise


def connect(path):
    if path.is_file():
        endpoint = json.loads(path.read_text(encoding="utf-8"))
        if (endpoint.get("transport") != "tcp" or endpoint.get("host") != "127.0.0.1"
                or type(endpoint.get("port")) is not int or not 1 <= endpoint["port"] <= 65535
                or not isinstance(endpoint.get("token"), str) or len(endpoint["token"]) != 64):
            raise ValueError("Invalid local relay endpoint")
        client = socket.socket(socket.AF_INET)
        address, token = ("127.0.0.1", endpoint["port"]), endpoint["token"]
    else:
        client = socket.socket(socket.AF_UNIX)
        address, token = str(path), None
    try:
        client.settimeout(380)
        client.connect(address)
        return client, token
    except BaseException:
        client.close()
        raise


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
    # Fail before spawning an owner if the endpoint already exists or cannot bind.
    listener, token = listen(args.socket, force_tcp=getattr(args, "tcp", False))

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
    with (args.evidence / "stderr.log").open("w", encoding="utf-8") as errors:
        # Popen's encoding only configures our pipe wrappers. Pin the child's
        # redirected stdin too, overriding an inherited Windows cp936 setting.
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, encoding="utf-8", bufsize=1,
                                   env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        with (args.evidence / "stdout.jsonl").open("a", encoding="utf-8") as transcript:
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
            print(json.dumps({"event": "relay_ready", "cli_pid": process.pid,
                              "socket": str(args.socket)}), flush=True)
            stopping = False
            transport_unknown = None
            while not stopping:
                client, _ = listener.accept()
                with client:
                    client.settimeout(5)
                    try:
                        request = json.loads(client.makefile("r", encoding="utf-8").readline(131072))
                        if not isinstance(request, dict):
                            raise ValueError("One JSON request object is required")
                        supplied = request.pop("token", "")
                        if token is not None and (not isinstance(supplied, str) or not secrets.compare_digest(supplied, token)):
                            raise ValueError("Relay authentication failed; no command submitted")
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
                        response = json.loads(path.read_text(encoding="utf-8"))
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
    client, token = connect(args.socket)
    with client:
        if token is not None:
            request["token"] = token
        client.sendall((json.dumps(request) + "\n").encode("utf-8"))
        response = json.loads(client.makefile("r", encoding="utf-8").readline())
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    owner = commands.add_parser("serve")
    owner.add_argument("--socket", type=Path, required=True)
    owner.add_argument("--evidence", type=Path, required=True)
    owner.add_argument("--offline", action="store_true")
    owner.add_argument("--tcp", action="store_true", help="Use authenticated loopback IPC (automatic without AF_UNIX)")
    caller = commands.add_parser("send")
    caller.add_argument("--socket", type=Path, required=True)
    caller.add_argument("--actor", required=True)
    caller.add_argument("--command", required=True)
    caller.add_argument("--request-id")
    options = parser.parse_args()
    serve(options) if options.mode == "serve" else send(options)

"""Local FireTuner/MCP diagnostics; never sends a game command.

The CLI owns exactly one foreground session, but a crashed or replaced
session can leave a `civ-mcp` process holding the single FireTuner client
slot. This read-only check reports the socket state and the local processes
that could own it, so recovery does not start with manual `ps`/`lsof` work.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
from typing import Any

from civ_mcp.devtools.evidence import Evidence

TUNER_HOST = "127.0.0.1"
TUNER_PORT = 4318
PROCESS_PATTERNS = (
    re.compile(r"(?:^|\s)\S*/?civ-mcp(?:\s|$)"),
    re.compile(r"(?:^|\s)-m\s+civ_mcp(?:\s|$)"),
    re.compile(r"(?:^|\s)\S*civ6_cli_relay\.py(?:\s|$)"),
    re.compile(r"(?:^|\s)civ6(?:\s+\S+)*\s+debug(?:\s|$)"),
)


def probe_port(host: str = TUNER_HOST, port: int = TUNER_PORT, timeout: float = 0.5) -> str:
    """Classify the tuner socket: accepting, refused (closed) or unreachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "accepting"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "unreachable"


def socket_owners(port: int = TUNER_PORT) -> list[dict[str, Any]] | None:
    """Rows from lsof for the tuner port, or None when lsof is unavailable."""
    lsof = shutil.which("lsof")
    if lsof is None:
        return None
    try:
        completed = subprocess.run([lsof, "-nP", f"-iTCP:{port}"], capture_output=True,
                                   text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    owners = []
    for line in completed.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 9:
            continue
        state = fields[-1].strip("()") if fields[-1].startswith("(") else None
        owners.append({
            "command": fields[0],
            "pid": int(fields[1]) if fields[1].isdigit() else None,
            "state": state,
            "endpoint": fields[-2] if len(fields) > 9 else fields[8],
        })
    return owners


def mcp_processes() -> list[dict[str, Any]] | None:
    """Local CLI/MCP processes that could own the single FireTuner slot."""
    ps = shutil.which("ps")
    if ps is None:
        return None
    try:
        completed = subprocess.run([ps, "-axo", "pid=,command="], capture_output=True,
                                   text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    processes = []
    for line in completed.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        command = command.strip()
        if not pid.isdigit() or command.startswith(("bash -c", "sh -c", "zsh -c")):
            continue
        if not any(pattern.search(command) for pattern in PROCESS_PATTERNS):
            continue
        processes.append({"pid": int(pid), "command": command[:180]})
    return processes


def assess(port_state: str, owners: list[dict[str, Any]] | None,
           processes: list[dict[str, Any]] | None) -> tuple[str, list[str]]:
    """Turn the raw facts into one assessment plus hints; never guesses an owner."""
    hints: list[str] = []
    established = [row for row in (owners or []) if row.get("state") == "ESTABLISHED"]
    listening = [row for row in (owners or []) if row.get("state") == "LISTEN"]
    if established:
        # FireTuner refuses new connections while its single client slot is in
        # use, so an established pair means a healthy active session even when
        # a fresh probe is refused.
        hints.append("A client connection is established: keep using that session, or close it "
                     "(quit / quit --abandon) before starting another; a refused probe is expected here.")
        return "occupied", hints
    if port_state == "accepting" and processes:
        hints.append("The tuner is free, but other CLI/MCP processes exist without a connection: "
                     "they may be leftovers from an earlier session. Confirm they are not another active "
                     "controller before starting a new session; end stale ones explicitly.")
        return "available_with_stale_processes", hints
    if port_state == "accepting":
        hints.append("The tuner accepts connections but none is established; a new session can connect.")
        return "available", hints
    if processes:
        hints.append("The tuner port is not accepting but local CLI/MCP processes remain: "
                     "a crashed or replaced session likely holds the single client slot. "
                     "End those processes (or use quit --abandon in their session) before retrying.")
        return "stale_session_likely", hints
    hints.append("Civ 6 is not running with EnableTuner enabled, or the tuner port differs.")
    return "game_offline", hints


def status(evidence: Evidence, data: dict[str, Any]) -> None:
    port_state = probe_port()
    owners = socket_owners()
    processes = mcp_processes()
    assessment, hints = assess(port_state, owners, processes)
    data.update({
        "host": TUNER_HOST,
        "port": TUNER_PORT,
        "port_state": port_state,
        "socket_owners": owners,
        "cli_mcp_processes": processes,
        "assessment": assessment,
        "hints": hints,
        "listening": [row for row in (owners or []) if row.get("state") == "LISTEN"],
        "established": [row for row in (owners or []) if row.get("state") == "ESTABLISHED"],
    })
    evidence.json("tuner", data)

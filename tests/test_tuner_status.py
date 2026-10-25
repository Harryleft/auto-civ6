"""`civ6 tuner status` classifies tuner/socket facts without guessing ownership."""
from __future__ import annotations

from civ_mcp.devtools import tuner


class FakeEvidence:
    def __init__(self):
        self.saved = {}

    def json(self, name, data):
        self.saved[name] = data


def test_accepting_port_with_establishment_is_occupied():
    owners = [
        {"command": "Civ6_Exe_", "pid": 10, "state": "LISTEN", "endpoint": "127.0.0.1:4318"},
        {"command": "python3", "pid": 20, "state": "ESTABLISHED", "endpoint": "127.0.0.1:50000"},
    ]

    assessment, hints = tuner.assess("accepting", owners, [{"pid": 20, "command": "civ-mcp"}])

    assert assessment == "occupied"
    assert any("established" in hint for hint in hints)


def test_refused_probe_with_established_pair_is_a_healthy_session():
    owners = [
        {"command": "Civ6_Exe_", "pid": 10, "state": "LISTEN"},
        {"command": "python3.1", "pid": 20, "state": "ESTABLISHED"},
    ]

    assessment, hints = tuner.assess("refused", owners, [{"pid": 20, "command": "civ-mcp"}])

    assert assessment == "occupied"
    assert any("refused probe is expected" in hint for hint in hints)


def test_free_port_without_processes_is_available():
    assessment, _ = tuner.assess("accepting", [{"state": "LISTEN"}], [])

    assert assessment == "available"


def test_free_port_with_leftover_processes_warns_about_staleness():
    assessment, hints = tuner.assess("accepting", [{"state": "LISTEN"}], [{"pid": 1, "command": "civ-mcp"}])

    assert assessment == "available_with_stale_processes"
    assert any("leftovers" in hint for hint in hints)


def test_refused_port_with_processes_suggests_stale_session():
    assessment, hints = tuner.assess("refused", [], [{"pid": 1, "command": "civ-mcp"}])

    assert assessment == "stale_session_likely"
    assert any("single client slot" in hint for hint in hints)


def test_refused_port_with_no_processes_means_game_offline():
    assessment, _ = tuner.assess("refused", [], [])

    assert assessment == "game_offline"


def test_status_records_probes_and_evidence(monkeypatch):
    monkeypatch.setattr(tuner, "probe_port", lambda: "accepting")
    monkeypatch.setattr(tuner, "socket_owners", lambda: [{"command": "Civ6_Exe_", "pid": 7, "state": "LISTEN"}])
    monkeypatch.setattr(tuner, "mcp_processes", lambda: [])
    evidence = FakeEvidence()
    data = {}

    tuner.status(evidence, data)

    assert data["port_state"] == "accepting"
    assert data["assessment"] == "available"
    assert data["listening"]
    assert evidence.saved["tuner"]["port"] == tuner.TUNER_PORT


def test_process_filter_ignores_shell_heredocs(monkeypatch):
    class Completed:
        stdout = (
            "  100 /usr/bin/civ-mcp\n"
            "  101 bash -c echo src/civ_mcp/cli.py\n"
            "  102 /usr/bin/python3 -m civ_mcp\n"
            "  103 uv run --locked civ6 debug\n"
        )

    monkeypatch.setattr(tuner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tuner.subprocess, "run", lambda *args, **kwargs: Completed())

    rows = tuner.mcp_processes()

    assert [row["pid"] for row in rows] == [100, 102, 103]

"""Reply framing of the foreground CLI transport; no game access."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "cli_relay", Path(__file__).resolve().parents[1] / "scripts/civ6_cli_relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


def test_verification_drains_nested_results_before_next_command():
    stream = iter([
        {"event": "command", "input": "verify state"},
        {"event": "tool_started", "sequence": 2},
        {"event": "tool_result", "request": 2},
        {"event": "tool_started", "sequence": 4},
        {"event": "tool_result", "request": 4},
        {"event": "verification", "status": "PASS"},
        {"event": "command", "input": "unit list"},
        {"event": "tool_result", "tool": "get_units"},
    ])
    first = relay.receive_command(lambda: next(stream), "verify state")
    assert first[-1] == {"event": "verification", "status": "PASS"}
    assert len(first) == 6
    second = relay.receive_command(lambda: next(stream), "unit list")
    assert second[-1]["tool"] == "get_units"


@pytest.mark.parametrize("value", [
    {"event": "command", "input": "unit list"},
    {"event": "tool_result", "tool": "get_game_snapshot"},
])
def test_mismatched_or_stale_reply_fails_closed(value):
    with pytest.raises(RuntimeError, match="stream mismatch"):
        relay.receive_command(lambda: value, "state overview")


def test_unknown_transport_result_remains_the_original_reply():
    stream = iter([{"event": "command", "input": "turn end"},
                   {"event": "tool_started", "sequence": 11},
                   {"event": "tool_outcome_unknown", "request": 11}])
    result = relay.receive_command(lambda: next(stream), "turn end")
    assert result[-1] == {"event": "tool_outcome_unknown", "request": 11}


@pytest.mark.parametrize("statistics_failure", [False, True])
def test_framing_failure_retains_owner_and_refuses_next_submission(tmp_path, monkeypatch, statistics_failure):
    class Client:
        def __init__(self, command, request_id):
            self.request = json.dumps({"actor": "test", "command": command, "request_id": request_id})
            self.reply = None
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): pass
        def makefile(self, mode): return io.StringIO(self.request + "\n")
        def sendall(self, data): self.reply = json.loads(data)

    first = Client("turn end", "ad1f6590-2ec4-4352-9939-41cf48af5517")
    second = Client("unit move 1 2 3", "b62e684a-7bf3-4766-a9e8-4a1992333ca9")
    clients = iter([first, second])
    class Listener:
        def bind(self, path): Path(path).touch()
        def listen(self, count): pass
        def accept(self): return next(clients), None

    process = SimpleNamespace(pid=123, stdin=io.StringIO(), stdout=io.StringIO(
        '\n'.join(json.dumps(x) for x in [
            {"event": "session_ready"},
            {"event": "command", "input": "turn end"},
            {"event": "tool_started", "sequence": 7},
            {"event": "command", "input": "unexpected"},
        ]) + '\n'))
    monkeypatch.setattr(relay.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(relay.socket, "socket", lambda *a, **k: Listener())
    if statistics_failure:
        def broken_statistics(*args):
            raise KeyError("statistics failure must not replace the original outcome")
        monkeypatch.setattr(relay.TurnTiming, "observe", broken_statistics)
    with pytest.raises(StopIteration):
        relay.serve(SimpleNamespace(evidence=tmp_path/'evidence', socket=tmp_path/'socket', offline=True))
    assert first.reply["state"] == "outcome_unknown"
    assert second.reply["state"] == "not_submitted"
    assert process.stdin.getvalue() == "turn end\n"
    assert not process.stdin.closed
    receipt = json.loads(Path(second.reply["original_receipt"]).read_text())
    assert receipt["command"] == "turn end" and receipt["state"] == "outcome_unknown"

"""Foreground-session behavior through real stdio, with no game connection."""

import asyncio
from io import StringIO
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from mcp import StdioServerParameters

from civ_mcp import server
from civ_mcp.devtools.debug import serve
from civ_mcp.devtools.evidence import Evidence
from civ_mcp.game_state import GameState
from test_action_confirmation import Engine


ROOT = Path(__file__).resolve().parents[1]
MOVE = 'call unit_action {"unit_id":0,"action":"move","target_x":22,"target_y":9}\n'


def test_compact_only_removes_exact_duplicate_text_and_preserves_original():
    from civ_mcp.devtools.debug import compact_result
    original = dict(content=[dict(type="text", text="facts")], structuredContent={"result": "facts"}, isError=True)
    value = compact_result(original)
    assert value == dict(content=original["content"], isError=True)
    assert "structuredContent" in original
    for structured in ({"facts": 7}, {"result": "different"}, {"result": "facts", "status": "unknown"}):
        richer = original | {"structuredContent": structured}
        assert compact_result(richer) == richer


def test_compact_stdout_keeps_full_protocol_evidence_and_error_detection(tmp_path):
    _, _, events, _, evidence = run_fake(tmp_path, "state overview\nquit\n", scenario="ready", compact=True)
    event = next(r for r in events if r["event"] == "tool_result")
    raw = json.loads((evidence.path / f"debug-response-{event['request']:06}.json").read_text())
    assert raw["content"] == event["result"]["content"]
    assert "structuredContent" in raw and "structuredContent" not in event["result"]


def fake_cli(tmp_path, stdin=subprocess.PIPE):
    driver = tmp_path / "driver.py"
    driver.write_text('''
import sys
from mcp import StdioServerParameters
from civ_mcp.devtools import debug
from civ_mcp.cli import main
original = debug.serve
async def injected(*args, **kwargs):
    server = StdioServerParameters(command=sys.executable,
        args=[%r, '--receipt', %r], cwd=%r)
    return await original(*args, **kwargs, server=server)
debug.serve = injected
raise SystemExit(main())
''' % (str(ROOT / 'tests/fake_debug_server.py'), str(tmp_path / 'engine.json'), str(ROOT)))
    return subprocess.Popen([sys.executable, str(driver), "debug", "--output", str(tmp_path / 'proof')],
        cwd=ROOT, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8')


def run_fake(tmp_path, text, timeout=5, scenario=None, **options):
    receipt = tmp_path / "engine.json"
    evidence = Evidence(ROOT, ["debug-test"], str(tmp_path / "evidence"))
    state, events = {}, []
    params = StdioServerParameters(command=sys.executable,
        args=[str(ROOT / "tests/fake_debug_server.py"), "--receipt", str(receipt),
              *(["--snapshot", scenario] if scenario else [])], cwd=str(ROOT))
    status = asyncio.run(serve(evidence, state, offline=False, timeout=timeout,
                              source=StringIO(text), emit=events.append, server=params, **options))
    return status, state, events, json.loads(receipt.read_text()), evidence


def test_interrupted_terminal_empty_read_retains_the_same_pending_owner(tmp_path):
    interrupted = threading.Event()

    class Terminal(StringIO):
        first = True

        def isatty(self):
            return True

        def readline(self):
            if self.first:
                self.first = False
                interrupted.set()  # Windows ReadConsole can report ^C as an empty read.
                return ''
            return super().readline()

    receipt = tmp_path / 'engine.json'
    evidence = Evidence(ROOT, ['interrupted-input'], str(tmp_path / 'proof'))
    params = StdioServerParameters(command=sys.executable,
        args=[str(ROOT / 'tests/fake_debug_server.py'), '--receipt', str(receipt)], cwd=str(ROOT))
    state, events = {}, []
    status = asyncio.run(serve(evidence, state, offline=False, timeout=5,
        source=Terminal(MOVE + 'pending\n' + MOVE + 'pending\nquit\n'), emit=events.append,
        server=params, interrupted=interrupted))
    pending = [e['runtime'] for e in events if e['event'] == 'pending']
    assert [r['has_pending'] for r in pending] == [True, False]
    assert len({(r['session_id'], r['process_id']) for r in pending}) == 1
    assert json.loads(receipt.read_text())['submissions'] == 1
    assert status == 'PASS' and state['exit_reason'] == 'quit'


def test_real_pending_owner_survives_queries_and_confirms_without_new_submission(tmp_path):
    status, state, events, engine, evidence = run_fake(tmp_path,
        MOVE + 'pending\nstate units\npending\nquit\n' + MOVE + 'pending\nquit\n')
    pending = [event["runtime"] for event in events if event["event"] == "pending"]
    assert [value["has_pending"] for value in pending] == [True, True, False]
    assert len({(value["process_id"], value["session_id"]) for value in pending}) == 1
    assert engine["process_id"] == pending[0]["process_id"]
    assert engine["submissions"] == 1 and engine["remaining_reads"] == 0
    assert engine["observed"] == ["get_units"] and engine["phase"] == "closed"
    assert any(event.get("code") == "PENDING_ON_EXIT" for event in events)
    assert status == "PASS" and state["mcp_session_closed"] and not state["unresolved_on_exit"]
    assert (evidence.path / "debug.jsonl").is_file()
    timing = json.loads((evidence.path / "turn-timing.json").read_text())
    assert timing["command_count"] == sum(event["event"] == "command" for event in events)
    assert timing["by_actor"]["cli"]["unknown_count"] >= 1
    assert (evidence.path / "command-timing.json").is_file()


def test_popup_unknown_survives_stdio_status_blocks_mutation_and_quit(tmp_path):
    # 此用例验证门禁语义；Windows 替身冷启动实测超过 5 秒，单独给足初始化预算。
    status, state, events, engine, _ = run_fake(tmp_path,
        "popup dismiss --dry-run\npopup dismiss\npending\n"
        "research set TECH_MINING\npopup dismiss\npending\nquit\n",
        scenario="popup-unknown", timeout=30)
    pending = [event["runtime"] for event in events if event["event"] == "pending"]
    assert len(pending) == 2
    assert all(row["pending"]["popup"] and row["has_pending"] for row in pending)
    assert len({(row["session_id"], row["process_id"]) for row in pending}) == 1
    results = [event for event in events if event["event"] == "tool_result"]
    assert results and all(event["is_error"] for event in results)
    assert engine["popup_writes"] == 1 and engine["submissions"] == 0
    assert any(event.get("code") == "PENDING_ON_EXIT" for event in events)
    assert status == "BLOCKED" and state["unresolved_on_exit"]


def test_timing_export_failure_keeps_pending_owner_and_original_action(tmp_path, monkeypatch, capsys):
    from civ_mcp.devtools.turn_timing import TurnTiming
    def fail_export(*args):
        raise OSError("statistics output unavailable")
    monkeypatch.setattr(TurnTiming, "export", fail_export)
    status, state, events, engine, evidence = run_fake(tmp_path,
        MOVE + 'pending\nstate units\n' + MOVE + 'pending\nquit\n')
    assert status == "PASS" and engine["submissions"] == 1
    pending = [event["runtime"]["has_pending"] for event in events if event["event"] == "pending"]
    assert pending == [True, False] and not state["unresolved_on_exit"]
    assert 'Timing export failed' in capsys.readouterr().err


@pytest.mark.parametrize("ending,reason", [("quit --abandon\n", "abandoned"), ("", "eof_unresolved")])
def test_unresolved_exit_is_explicitly_blocked_and_keeps_original_snapshot(tmp_path, ending, reason):
    status, state, events, engine, _ = run_fake(tmp_path, MOVE + 'pending\n' + ending)
    assert status == "BLOCKED" and state["exit_reason"] == reason
    assert state["runtime"]["pending"]["action"] and state["mcp_session_closed"]
    assert engine["submissions"] == 1 and engine["remaining_reads"] == 1 and engine["has_pending"]


def test_timeout_retains_process_and_only_allows_read_only_inspection(tmp_path):
    status, state, events, engine, evidence = run_fake(tmp_path,
        'call test_slow {}\npending\nstate units\n' + MOVE + 'quit\nquit --abandon\n', timeout=2)
    assert status == "BLOCKED" and state["transport_unknown"]["tool"] == "test_slow"
    assert engine["submissions"] == 1
    assert any(event.get("code") == "TRANSPORT_UNRESOLVED" for event in events)
    assert any(event["event"] == "tool_outcome_unknown" for event in events)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    pending = next(event["runtime"] for event in events if event["event"] == "pending")
    assert pending["process_id"] == engine["process_id"] and pending["has_pending"]
    assert (evidence.path / "transport-unknown.json").is_file()


def test_later_read_timeout_does_not_replace_original_unknown_intent(tmp_path):
    status, state, events, engine, evidence = run_fake(tmp_path,
        'call test_slow {}\ncall test_slow_read {}\npending\nquit --abandon\n', timeout=2)
    unknowns = [event for event in events if event['event'] == 'tool_outcome_unknown']
    assert [row['tool'] for row in unknowns] == ['test_slow', 'test_slow_read']
    assert state['transport_unknown']['tool'] == 'test_slow'
    assert json.loads((evidence.path / 'transport-unknown.json').read_text())['tool'] == 'test_slow'
    assert status == 'BLOCKED' and engine['submissions'] == 1


def test_identity_change_is_latched_and_new_mutation_is_not_sent(tmp_path):
    status, state, events, engine, _ = run_fake(tmp_path,
        'call test_change_session {}\npending\n' + MOVE + 'quit --abandon\n')
    assert status == "BLOCKED" and engine["submissions"] == 0
    assert any(event.get("code") == "SESSION_CHANGED" for event in events)
    assert any(event.get("code") == "TRANSPORT_UNRESOLVED" for event in events)


def test_explicit_tool_call_checks_identity_even_without_pending_command(tmp_path):
    status, state, events, engine, _ = run_fake(tmp_path,
        'call test_change_session {}\n' + MOVE + 'quit --abandon\n')
    assert status == "BLOCKED" and engine["submissions"] == 0
    assert any(event.get("code") == "SESSION_CHANGED" for event in events)


def test_malformed_commands_and_schema_errors_do_not_replace_session(tmp_path):
    status, state, events, engine, _ = run_fake(tmp_path,
        'call unit_action {"unit_id":0,"unit_id":1}\ncall unit_action []\ncall unit_action {"unit_id":NaN}\n'
        'state wrong\ncall missing {}\ntools unit_action\ncall unit_action {}\npending\nquit\n')
    assert status == "PASS" and engine["submissions"] == 0
    assert len([event for event in events if event.get("code") == "INVALID_JSON"]) == 3
    assert any(event.get("code") == "INVALID_TOOL_ARGUMENT" and event["details"]["issues"] for event in events)
    assert any(event["event"] == "tool_schema" for event in events)


def test_runtime_status_is_pure_and_preserves_all_existing_owner_facts():
    engine = Engine()
    engine.is_connected = False
    game = GameState(engine)
    game._pending_save = True
    game._pending_load = True
    ctx = SimpleNamespace(request_context=SimpleNamespace(
        lifespan_context=server.AppContext(game=game, logger=None)))

    async def read_twice():
        first = json.loads(await server.get_runtime_status(ctx))
        second = json.loads(await server.get_runtime_status(ctx))
        assert first == second
        assert first["pending"]["save"] and first["pending"]["replacement"]
        assert first["has_pending"] and not first["connected"]

    asyncio.run(read_twice())
    assert engine.submissions == [] and engine.reads == [] and game.save_pending and game.replacement_pending


def test_real_cli_offline_mode_uses_one_actual_mcp_process_and_refuses_game_tools(tmp_path):
    output = tmp_path / "cli"
    result = subprocess.run([sys.executable, "-m", "civ_mcp.cli", "debug", "--offline", "--output", str(output)],
        input='pending\nstate units\ncall search_knowledge {"query":"写作","limit":1}\npending\nquit\n',
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    runtime = [record["runtime"] for record in records if record["event"] in {"session_ready", "pending"}]
    assert len({(value["session_id"], value["process_id"]) for value in runtime}) == 1
    assert any(record.get("code") == "OFFLINE_ONLY" for record in records)
    started = [record["tool"] for record in records if record["event"] == "tool_started"]
    assert started == ["search_knowledge"]
    assert records[-1]["event"] == "session_closed" and records[-1]["status"] == "PASS"
    assert json.loads((output / "summary.json").read_text()) == records[-1]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_sigint_keeps_foreground_and_child_until_explicit_quit(tmp_path):
    process = subprocess.Popen([sys.executable, "-m", "civ_mcp.cli", "debug", "--offline", "--output", str(tmp_path / 'proof')],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    try:
        first = json.loads(process.stdout.readline())
        process.send_signal(signal.SIGINT)
        process.stdin.write('pending\nquit\n'); process.stdin.flush()
        stdout, stderr = process.communicate(timeout=20)
        rows = [json.loads(line) for line in stdout.splitlines()]
        assert process.returncode == 0 and 'Session retained' in stderr
        assert next(row["runtime"] for row in rows if row["event"] == "pending")["process_id"] == first["runtime"]["process_id"]
    finally:
        if process.poll() is None:
            process.kill(); process.wait()


def test_dead_mcp_exits_without_waiting_for_stdin_and_never_restarts(tmp_path):
    process = fake_cli(tmp_path)
    try:
        first = json.loads(process.stdout.readline())
        process.stdin.write('call test_exit {}\n'); process.stdin.flush()
        process.wait(timeout=15)  # stdin deliberately remains open.
        records = [json.loads(line) for line in process.stdout.read().splitlines()]
        assert process.returncode == 1
        assert records[-1]['errors'][0]['code'] == 'MCP_CLOSED'
        assert records[-1]['data']['context_lost']
        assert len([row for row in records if row['event'] == 'session_ready']) == 0
        assert first['event'] == 'session_ready'
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        process.stdin.close()


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX signal delivery')
def test_sigint_during_request_does_not_cancel_or_duplicate_submission(tmp_path):
    process = fake_cli(tmp_path)
    try:
        first = json.loads(process.stdout.readline())
        process.stdin.write(MOVE); process.stdin.flush()
        while True:
            event = json.loads(process.stdout.readline())
            if event['event'] == 'tool_started':
                break
        process.send_signal(signal.SIGINT)
        process.stdin.write('pending\nquit --abandon\n'); process.stdin.flush()
        stdout, stderr = process.communicate(timeout=15)
        rows = [json.loads(line) for line in stdout.splitlines()]
        engine = json.loads((tmp_path / 'engine.json').read_text())
        assert process.returncode == 2 and engine['submissions'] == 1
        assert 'Session retained' in stderr
        assert next(row['runtime'] for row in rows if row['event'] == 'pending')['process_id'] == first['runtime']['process_id']
        assert not any(row['event'] == 'tool_outcome_unknown' for row in rows)
    finally:
        if process.poll() is None:
            process.kill(); process.wait()


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX pseudo-terminal')
def test_terminal_eof_refuses_exit_then_can_confirm_original_request(tmp_path):
    import pty
    master, slave = pty.openpty()
    process = fake_cli(tmp_path, stdin=slave)
    os.close(slave)
    try:
        first = json.loads(process.stdout.readline())
        os.write(master, MOVE.encode())
        while json.loads(process.stdout.readline())['event'] != 'tool_result':
            pass
        os.write(master, b'\x04')
        blocked = json.loads(process.stdout.readline())
        assert blocked['code'] == 'PENDING_ON_EXIT'
        os.write(master, ('pending\n' + MOVE + 'quit\n').encode())
        stdout, stderr = process.communicate(timeout=15)
        rows = [json.loads(line) for line in stdout.splitlines()]
        assert process.returncode == 0 and 'civ6> ' in stderr
        assert next(row['runtime'] for row in rows if row['event'] == 'pending')['process_id'] == first['runtime']['process_id']
        assert json.loads((tmp_path / 'engine.json').read_text())['submissions'] == 1
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        os.close(master)


def test_dry_run_validates_without_submitting_or_confirming_pending(tmp_path):
    preview = MOVE.replace("call ", "call --dry-run ")
    status, state, events, engine, _ = run_fake(tmp_path,
        preview + 'pending\n' + MOVE + preview + 'quit --abandon --dry-run\npending\n' + MOVE + 'quit\n')
    pending = [event['runtime'] for event in events if event['event'] == 'pending']
    assert [row['has_pending'] for row in pending] == [False, True]
    assert engine['submissions'] == 1 and engine['remaining_reads'] == 0
    assert engine['observed'] == []
    previews = [event for event in events if event['event'] == 'dry_run']
    assert len(previews) == 3
    assert all(row['would_send'] is False for row in previews)
    assert previews[0]['schema_valid'] and previews[0]['potentially_mutating']
    assert previews[1]['scope'] == 'schema_only' and not previews[1]['pending_checked']
    assert previews[2]['would_lose_context'] and previews[2]['session_retained']
    assert status == 'PASS' and state['tool_calls'] == 2


def test_discovery_and_field_errors_are_actionable_without_target_rpc(tmp_path):
    status, state, events, engine, _ = run_fake(tmp_path,
        'help\ncall --help\nhelp quit\ncall --dry-run unit_action {"unit_id":"bad","action":"move"}\n'
        'call --dry-run unit_action {"unit_id":1e999}\ncall --dry-run\nquit\n')
    overview = next(row for row in events if row['event'] == 'help' and row['topic'] == 'overview')
    assert 'topics' in overview and 'examples' not in overview
    assert 'named command' in overview['description']
    call_help = next(row for row in events if row['event'] == 'help' and row['topic'] == 'call')
    assert 'diagnostics only' in call_help['description']
    assert all('unit_action' not in example for example in call_help['examples'])
    assert any(row['event'] == 'help' and row.get('examples') for row in events)
    errors = [row for row in events if row['event'] == 'command_blocked']
    assert all(row['hint'] for row in errors)
    schema = next(row for row in errors if row['code'] == 'INVALID_TOOL_ARGUMENT')
    assert schema['details']['issues'][0]['path'] == ['unit_id']
    assert any(row['code'] == 'INVALID_JSON' for row in errors)
    assert engine['submissions'] == 0 and state['tool_calls'] == 0 and status == 'PASS'


def test_offline_can_preview_game_tool_without_sending_it(tmp_path):
    output = tmp_path / 'proof'
    result = subprocess.run([sys.executable, '-m', 'civ_mcp.cli', 'debug', '--offline', '--output', str(output)],
        input=MOVE.replace('call ', 'call --dry-run ') + 'pending\nquit\n',
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    preview = next(row for row in rows if row['event'] == 'dry_run')
    assert not preview['local_policy_allows_execution'] and not preview['would_send']
    assert not any(row['event'] == 'tool_started' for row in rows)
    assert result.returncode == 0 and rows[-1]['data']['tool_calls'] == 0


def test_legacy_native_refusal_and_unknown_are_machine_readable_without_replay(tmp_path):
    status,state,events,engine,_=run_fake(tmp_path,
        'call test_legacy_error {}\ncall test_unknown_error {}\npending\nquit\n')
    results=[row for row in events if row['event']=='tool_result']
    assert [row['error']['code'] for row in results]==['NO_TILES','OUTCOME_UNKNOWN']
    assert all(row['is_error'] and not row['protocol_is_error'] for row in results)
    assert 'different eligible city/district' in results[0]['hint']
    assert 'original arguments' in results[1]['hint']
    assert results[0]['result']['content'][0]['text']=='Error: ERR:NO_TILES|No valid placement tiles found'
    assert state['tool_calls']==2 and status=='PASS' and engine['submissions']==0


@pytest.mark.parametrize('text,protocol,code', [
    ('No promotions available',False,None),('A note mentions Error: in context',False,None),
    ('Error: failed',False,'TOOL_REPORTED_ERROR'),('failure',True,'MCP_TOOL_ERROR')])
def test_error_detection_only_uses_explicit_failure_boundaries(text,protocol,code):
    from mcp.types import CallToolResult,TextContent
    from civ_mcp.devtools.debug import tool_error
    result=CallToolResult(isError=protocol,content=[TextContent(type='text',text=text)])
    error=tool_error('get_units',result)
    assert (error['code'] if error else None)==code

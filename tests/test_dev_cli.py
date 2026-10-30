"""Exercise the offline CLI across its process, file and MCP interfaces."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from civ_mcp.devtools.evidence import CommandError, Evidence


ROOT = Path(__file__).resolve().parents[1]


def cli(tmp_path, *arguments, code=0, env=None):
    output = tmp_path / f"run-{len(list(tmp_path.glob('run-*')))}"
    result = subprocess.run([sys.executable, "-m", "civ_mcp.cli", "--output", str(output), *arguments],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90, env=env)
    assert result.returncode == code, result.stdout + result.stderr
    assert not result.stderr, result.stderr
    summary = json.loads(result.stdout)
    assert summary["exit_code"] == code
    assert summary["mode"] == "offline" and summary["game_verification"] == "not_run"
    if summary["evidence"]:
        stored = json.loads((output / "summary.json").read_text())
        assert stored == summary
        context = json.loads(Path(summary["context"]).read_text())
        assert context["source_sha256"]["src/civ_mcp/cli.py"] == hashlib.sha256(
            (ROOT / "src/civ_mcp/cli.py").read_bytes()).hexdigest()
    return summary, output


def row(time, tool="get_units", result="units", **extra):
    return {"timestamp": time, "tool": tool, "params": {}, "result": result, "duration_ms": 1, **extra}


def write_rows(path, rows):
    path.write_text("".join(json.dumps(value) + "\n" for value in rows), encoding="utf-8")


def test_doctor_and_real_knowledge_path_share_the_result_contract(tmp_path):
    doctor, doctor_dir = cli(tmp_path, "doctor")
    assert doctor["status"] == "PASS" and doctor["data"]["mcp_ready"]
    assert doctor["data"]["mcp_session_closed"]
    assert not (doctor_dir / "mcp-attempts.json").exists()
    verified, output = cli(tmp_path, "verify", "knowledge")
    data = verified["data"]
    assert verified["status"] == "PASS"
    assert data["knowledge_unchanged"] and data["scratch_removed"] and data["mcp_session_closed"]
    calls = json.loads((output / "mcp-attempts.json").read_text())
    assert [call["tool"] for call in calls] == ["search_knowledge", *["get_knowledge"] * 4]
    for level in ("summary", "brief", "rule", "evidence"):
        result = json.loads((output / f"mcp-{level}.json").read_text())
        payload = json.loads(result["content"][0]["text"])
        assert payload["id"] == "TECH_WRITING"
        assert payload["applicability"]["current_session_verified"] is False


def test_knowledge_corruption_fails_before_starting_mcp_and_keeps_receipt(tmp_path):
    bad = tmp_path / "bad-knowledge"
    bad.mkdir()
    (bad / "manifest.json").write_text('{}')
    (bad / "query.py").write_text('import json\nprint(json.dumps({"status":"error","error":"corrupt"}))\nraise SystemExit(2)\n')
    summary, output = cli(tmp_path, "doctor", "--knowledge-dir", str(bad), code=1)
    assert summary["status"] == "FAIL"
    assert json.loads((output / "cli-doctor.json").read_text())["exit_code"] == 2
    assert not (output / "mcp-initialize.json").exists()


def test_log_filters_sorting_partial_lines_and_retained_source(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    source = logs / "session_a.jsonl"
    write_rows(source, [row("2026-10-24T02:00:00Z", result="last"),
                        row("2026-10-24T01:00:00Z", result="first"),
                        row("2026-10-24T01:30:00Z", tool="get_cities")])
    with source.open("a") as stream:
        stream.write('{"timestamp":')
    before = source.read_bytes()
    listed, _ = cli(tmp_path, "logs", "list", "--dir", str(logs))
    assert listed["data"]["sessions"][0]["session"] == "session_a"
    timeline, output = cli(tmp_path, "logs", "timeline", "session_a", "--dir", str(logs), "--tool", "get_units")
    data = timeline["data"]
    assert [entry["result"] for entry in data["rows"]] == ["first", "last"]
    assert data["malformed_lines"] == 1 and data["tail_incomplete"]
    assert data["source_sha256"] == hashlib.sha256(before).hexdigest()
    shown, _ = cli(tmp_path, "logs", "show", "--file", str(source), "--limit", "1")
    assert shown["data"]["truncated"] and shown["data"]["rows"][0]["tool"] == "get_cities"
    bounded, _ = cli(tmp_path, "logs", "show", "--file", str(source),
                     "--since", "2026-10-24T01:15:00+00:00", "--until", "2026-10-24T01:45:00Z")
    assert bounded["data"]["matched"] == 1
    assert source.read_bytes() == before
    assert (output / "logs.json").is_file()


@pytest.mark.parametrize("arguments", [
    ("logs", "show", "../escape"),
    ("logs", "show", "missing"),
    ("logs", "show", "missing", "--since", "2026-10-24"),
    ("test", "actions", "--", "scripts/test_queries.py"),
    ("test", "actions", "--", "-p", "untrusted"),
    ("test", "actions", "--", "--override-ini=testpaths=scripts"),
    ("doctor", "--", "-q"),
    ("test",),
])
def test_bad_requests_are_json_and_do_not_start_execution(tmp_path, arguments):
    summary, output = cli(tmp_path, *arguments, code=2)
    assert summary["status"] == "BLOCKED" and summary["errors"]
    assert not (output / "pytest.json").exists()
    assert not (output / "mcp-initialize.json").exists()


@pytest.mark.parametrize("arguments", [("unknown",), ("logs", "list", "--limit", "0"),
                                       ("test", "actions", "--timeout", "nan")])
def test_parser_errors_are_json(tmp_path, arguments):
    result, _ = cli(tmp_path, *arguments, code=2)
    assert result["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert "--help" in result["errors"][0]["hint"]


def test_domain_discovery_and_actual_collection_ignore_ambient_pytest_options(tmp_path):
    import os
    listed, _ = cli(tmp_path, "test", "--list")
    assert listed["data"]["domains"]["actions"] == [
        "tests/test_action_confirmation.py", "tests/test_pending_dispatch.py"]
    environment = dict(os.environ, PYTEST_ADDOPTS="scripts/test_queries.py", PYTEST_PLUGINS="absent_plugin")
    collected, output = cli(tmp_path, "test", "knowledge", "--collect-only", "--", "-k", "real_stdio", env=environment)
    assert collected["status"] == "PASS" and collected["data"]["pytest_exit_code"] == 0
    receipt = json.loads((output / "pytest.json").read_text())
    assert "test_knowledge_search_and_read_through_real_stdio_without_game" in receipt["stdout"]
    assert not any("scripts/" in arg for arg in receipt["command"])
    none, _ = cli(tmp_path, "test", "knowledge", "--", "-k", "NO_MATCHING_TEST_987654321", code=2)
    assert none["data"]["pytest_exit_code"] == 5


def test_existing_evidence_is_never_overwritten(tmp_path):
    directory = tmp_path / "existing"
    directory.mkdir()
    proof = directory / "summary.json"
    proof.write_text('preserve me')
    result = subprocess.run([sys.executable, "-m", "civ_mcp.cli", "doctor", "--output", str(directory)],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 2 and json.loads(result.stdout)["evidence"] is None
    assert proof.read_text() == 'preserve me'


def test_nonstandard_or_missing_log_fields_are_counted_without_losing_good_rows(tmp_path):
    path = tmp_path / "mixed.jsonl"
    write_rows(path, [row(None), row(123), row("bad"), row("2026-10-24T00:00:00Z", params={"n": float('nan')}),
                      row("2026-10-24T00:00:01Z", result="原始结果 ✓")])
    result, _ = cli(tmp_path, "logs", "show", "--file", str(path))
    assert result["data"]["malformed_lines"] == 4
    assert result["data"]["matched"] == 1
    assert result["data"]["rows"][0]["result"] == "原始结果 ✓"


def test_knowledge_output_is_refused_before_changing_input_directory(tmp_path):
    knowledge = tmp_path / "distribution"
    knowledge.mkdir()
    output = knowledge / "evidence"
    result = subprocess.run([sys.executable, "-m", "civ_mcp.cli", "doctor", "--knowledge-dir", str(knowledge),
                             "--output", str(output)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 2 and json.loads(result.stdout)["errors"][0]["code"] == "INVALID_OUTPUT"
    assert not output.exists()


def test_process_timeout_retains_partial_receipt(tmp_path):
    evidence = Evidence(ROOT, ["timeout-test"], str(tmp_path / "proof"))
    with pytest.raises(CommandError) as error:
        evidence.run("slow", [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"], timeout=.3)
    assert error.value.code == "TIMEOUT"
    receipt = json.loads((evidence.path / "slow.json").read_text())
    assert receipt["timeout_seconds"] == .3 and receipt["finished_at"]
    assert "started" in receipt["stdout"]


@pytest.mark.parametrize('command,children', [([], ['doctor', 'tuner', 'verify', 'test', 'debug', 'logs']),
    (['logs'], ['list', 'show', 'timeline', 'turns']), (['verify'], ['knowledge', 'state']), (['debug'], [])])
def test_help_discloses_one_level_and_supports_json_without_side_effects(tmp_path, command, children):
    output = tmp_path / 'unused'
    prefix = [sys.executable, '-m', 'civ_mcp.cli', '--output', str(output)]
    human = subprocess.run([*prefix, *command, '--help'], cwd=ROOT, capture_output=True, text=True)
    machine = subprocess.run([*prefix, *command, '--help', '--json'], cwd=ROOT, capture_output=True, text=True)
    assert human.returncode == machine.returncode == 0
    assert not human.stderr and not machine.stderr and not output.exists()
    doc = json.loads(machine.stdout)
    assert doc['kind'] == 'help' and doc['schema_version'] == 1 and doc['description']
    assert [row['name'] for row in doc['subcommands']] == children
    assert doc['notes'] or children
    if not command:
        assert '--since' not in human.stdout and 'unit_action' not in machine.stdout
        assert 'developer CLI' not in doc['description']
    elif command == ['debug']:
        assert 'unit move --help' in doc['notes']
        assert 'appending --dry-run' in doc['notes']
        assert 'call --dry-run TOOL' not in doc['notes']
        assert 'Protocol diagnostics only' in doc['notes']


def test_log_errors_identify_the_input_to_change(tmp_path):
    result, _ = cli(tmp_path, 'logs', 'show', '--file', str(tmp_path / 'missing'), code=2)
    assert result['errors'][0]['code'] == 'LOG_NOT_FOUND'
    assert '--file' in result['errors'][0]['hint']


def test_failed_isolated_pytest_preserves_receipt_and_repair_hint(tmp_path, monkeypatch):
    from civ_mcp.devtools import knowledge, testing
    fixture = tmp_path / 'tests'
    fixture.mkdir()
    (fixture / 'test_failure.py').write_text('def test_failure():\n    assert 1 == 2\n')
    monkeypatch.setattr(knowledge, 'check_interpreter', lambda project: None)
    monkeypatch.setitem(testing.DOMAINS, 'fixture', ('test_failure.py',))
    evidence = Evidence(tmp_path, ['test', 'fixture'])
    state = {}
    with pytest.raises(CommandError) as error:
        testing.run_tests(evidence, 'fixture', [], 10, state)
    summary = evidence.finish(error.value.status, state, error.value)
    assert summary['status'] == 'FAIL' and summary['exit_code'] == 1
    assert summary['errors'][0]['code'] == 'PYTEST_FAILED'
    assert 'pytest.json' in summary['errors'][0]['hint']
    receipt = json.loads((evidence.path / 'pytest.json').read_text())
    assert 'assert 1 == 2' in receipt['stdout'] and state['pytest_exit_code'] == 1

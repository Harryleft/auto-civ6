"""Timing boundaries follow confirmed receipts, never queries or waits alone."""
import json

import pytest

from civ_mcp.devtools.turn_timing import TurnTiming, read_relay


def record(command, start, end, *, actor="codex", tool="get_units", text="units", error=None):
    return {"request_id": f"{actor}-{start}-{command}", "actor": actor, "command": command,
            "started": start, "finished": end, "state": "response_received", "events": [
                {"event": "command", "input": command},
                {"event": "tool_started", "sequence": 1, "timestamp": "2026-10-25T00:00:01+00:00"},
                {"event": "tool_result", "request": 1, "tool": tool, "is_error": bool(error), "error": error,
                 "timestamp": "2026-10-25T00:00:02+00:00",
                 "result": {"content": [{"type": "text", "text": text}]}}]}


def anchor(turn=1, seed=7, start=0):
    value = record("verify state", start, start + 1, actor="repair")
    value["events"].append({"event": "verification", "status": "PASS", "identity": {
        "turn": turn, "civilization": "sumeria", "seed": seed, "player_id": 0}})
    return value


def test_turn_wall_cli_and_gap_follow_confirmation_boundary_and_handoff():
    timing = TurnTiming()
    timing.observe(anchor())
    first = record("unit list", 10, 12)
    timing.observe(first)
    timing.observe(first)  # Same request receipt is not a second call.
    timing.observe(record("turn end", 20, 25, tool="end_turn", text="Turn 1 -> 2\n"))
    timing.observe(record("unit list", 35, 36, actor="claude"))
    timing.observe(record("turn end", 40, 45, actor="claude", tool="end_turn", text="Turn 2 -> 3\n"))
    timing.observe(record("unit list", 48, 50, actor="claude"))
    result = timing.report()
    first, second, last = result["rounds"]
    assert (first["wall_seconds"], first["cli_seconds"], first["gap_seconds"]) == (15, 7, 8)
    assert (second["wall_seconds"], second["cli_seconds"], second["gap_seconds"]) == (20, 6, 14)
    assert first["rpc_seconds"] == 2
    assert last["status"] == "partial" and last["wall_seconds"] == 5
    assert result["completed_turns"] == 2 and result["command_count"] == 5
    assert result["by_actor"]["codex"]["calls"] == 2


def test_unknown_end_does_not_finish_turn_and_later_confirmation_counts_once():
    timing = TurnTiming()
    timing.observe(anchor())
    timing.observe(record("turn end", 10, 12, tool="end_turn", text="ERR:OUTCOME_UNKNOWN",
                          error={"code": "OUTCOME_UNKNOWN"}))
    assert timing.report()["completed_turns"] == 0
    timing.observe(record("turn end", 15, 16, tool="end_turn", text="Turn 1 -> 2\n"))
    row = timing.report()["rounds"][0]
    assert row["status"] == "completed" and row["unknown_count"] == 1 and row["error_count"] == 1


def test_newer_overview_does_not_claim_missing_turns_completed():
    timing = TurnTiming()
    timing.observe(anchor())
    timing.observe(record("unit list", 10, 12))
    timing.observe(record("state overview", 20, 22, tool="get_game_overview", text="Turn 3 | 苏美尔"))
    rows = timing.report()["rounds"]
    assert [r["turn"] for r in rows] == [1, 3]
    assert rows[0]["status"] == "unconfirmed_boundary"
    assert timing.report()["completed_turns"] == 0


def test_knowledge_example_text_cannot_change_observed_game_turn():
    timing = TurnTiming()
    timing.observe(anchor())
    timing.observe(record("knowledge show example", 10, 12, tool="get_knowledge", text="Turn 99 | example"))
    assert timing.report()["rounds"][0]["turn"] == 1


@pytest.mark.parametrize("different_world", [True, False])
def test_reload_or_world_change_splits_even_equal_turns(different_world):
    timing = TurnTiming()
    timing.observe(anchor())
    timing.observe(record("unit list", 10, 12))
    if different_world:
        timing.observe(anchor(seed=8, start=13))
    else:
        timing.observe(record("save reload example", 13, 14, actor="setup", tool="reload_game_save", text="Loaded"))
        timing.observe(anchor(start=15))
    timing.observe(record("unit list", 20, 22))
    rows = timing.report()["rounds"]
    assert rows[0]["segment"] != rows[1]["segment"]
    assert timing.report()["completed_turns"] == 0


def test_monotonic_clock_preserves_elapsed_across_backward_wall_jump():
    timing = TurnTiming()
    timing.observe(anchor())
    value = record("unit list", 100, 90)
    value.update(started_monotonic=1000, finished_monotonic=1002)
    timing.observe(value)
    row = timing.report()["rounds"][0]
    assert row["cli_seconds"] == row["wall_seconds"] == 2
    assert row["clock_source"] == "monotonic"


@pytest.mark.parametrize("problem", ["reversed_clock", "missing_start"])
def test_unavailable_rpc_time_is_null_not_a_zero_duration(problem):
    timing = TurnTiming()
    timing.observe(anchor())
    value = record("unit list", 10, 12)
    if problem == "reversed_clock":
        value["events"][-1]["timestamp"] = "2026-10-25T00:00:00+00:00"
    else:
        value["events"].pop(1)
    timing.observe(value)
    assert timing.commands[0]["rpc_seconds"] is None
    assert timing.report()["rounds"][0]["rpc_seconds"] is None
    assert timing.report()["by_actor"]["codex"]["rpc_seconds"] is None
    assert timing.report()["quality"] == "partial"


def test_backfill_exports_without_modifying_source_or_counting_invalid_frames(tmp_path):
    source = tmp_path/'requests'
    source.mkdir()
    records = [anchor(), record("unit list", 10, 12),
               record("turn end", 13, 14, tool="end_turn", text="Turn 1 -> 2\n")]
    for i, value in enumerate(records):
        (source/f'{i}.json').write_text(json.dumps(value))
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    timing = read_relay(tmp_path)
    export = tmp_path/'export'
    export.mkdir()
    timing.export(export)
    rows = json.loads((export/'turn-timing.json').read_text())["rounds"]
    assert len(rows) == 1 and rows[0]["status"] == "completed"
    assert len(json.loads((export/'command-timing.json').read_text())["commands"]) == 2
    assert not list(export.glob('*.csv'))
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}
    invalid = record("unit list", 20, 22)
    invalid["events"][0]["input"] = "state overview"
    timing.observe(invalid)
    assert timing.report()["command_count"] == 2
    assert timing.report()["issues"][-1]["reason"] == "reply_frame_mismatch"


def test_logs_turns_cli_is_offline_and_exports_json_only(tmp_path, capsys):
    from civ_mcp.cli import main
    source = tmp_path/'relay/requests'
    source.mkdir(parents=True)
    for i, value in enumerate([anchor(), record("turn end", 10, 12, tool="end_turn", text="Turn 1 -> 2\n")]):
        (source/f'{i}.json').write_text(json.dumps(value))
    destination = tmp_path/'report'
    assert main(['--output', str(destination), 'logs', 'turns', '--relay', str(source.parent)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "offline" and result["game_verification"] == "not_run"
    assert result["data"]["completed_turns"] == 1
    assert (destination/'command-timing.json').is_file()
    assert not list(destination.glob('*.csv'))


def decision_anchor(turn=1, seed=7, start=0):
    data = {'schema_version': 1, 'status': 'ready',
            'identity': {'civilization': 'sumeria', 'seed': seed, 'player_id': 0, 'turn': turn},
            'checks': {k: True for k in ('stable_world_turn', 'overview_scope', 'unit_scope', 'city_scope', 'victory_scope')}}
    return record('state decision', start, start + 1, tool='get_decision_context', text=json.dumps(data))


def test_decision_anchor_attributes_native_confirmation_once():
    value = decision_anchor()
    payload = value['events'][-1]['result']
    payload['structuredContent'] = {'result': payload['content'][0]['text']}
    timing = TurnTiming()
    timing.observe(value)
    timing.observe(record('turn end', 2, 3, tool='end_turn', text='Turn 1 -> 2\n'))
    assert timing.report()['completed_turns'] == 1
    assert [r['turn'] for r in timing.report()['rounds']] == [1]
    assert timing.report()['command_count'] == 2


@pytest.mark.parametrize('change', ['schema', 'status', 'checks', 'false_check', 'integer_check',
    'empty_civ', 'bool_seed', 'bool_player', 'bool_turn', 'negative_player', 'negative_turn', 'other_tool', 'error'])
def test_invalid_decision_anchor_does_not_guess_turn(change):
    value = decision_anchor()
    event = value['events'][-1]
    data = json.loads(event['result']['content'][0]['text'])
    if change == 'schema': data['schema_version'] = True
    elif change == 'status': data['status'] = 'unknown'
    elif change == 'checks': del data['checks']['city_scope']
    elif change == 'false_check': data['checks']['city_scope'] = False
    elif change == 'integer_check': data['checks']['city_scope'] = 1
    elif change == 'empty_civ': data['identity']['civilization'] = ' '
    elif change.startswith('bool_'): data['identity'][change[5:] if change != 'bool_player' else 'player_id'] = True
    elif change.startswith('negative_'): data['identity']['player_id' if change == 'negative_player' else 'turn'] = -1
    elif change == 'other_tool': event['tool'] = 'get_knowledge'
    elif change == 'error': event['is_error'] = True
    event['result']['content'][0]['text'] = json.dumps(data)
    timing = TurnTiming()
    timing.observe(value)
    assert timing.turn is None and not timing.report()['rounds']


@pytest.mark.parametrize('structured', [False, True])
def test_conflicting_decision_contents_reject_anchor_and_record_issue(structured):
    value = decision_anchor()
    result = value['events'][-1]['result']
    other = json.loads(result['content'][0]['text'])
    other['identity']['turn'] = 99
    if structured: result['structuredContent'] = other
    else: result['content'].append({'type': 'text', 'text': json.dumps(other)})
    timing = TurnTiming()
    timing.observe(value)
    assert timing.turn is None
    assert any(i['reason'] == 'decision_anchor_conflict' for i in timing.report()['issues'])


def test_decision_world_change_uses_existing_segment_boundary():
    timing = TurnTiming()
    timing.observe(decision_anchor())
    timing.observe(decision_anchor(seed=8, start=2))
    assert [r['segment'] for r in timing.report()['rounds']] == [1, 2]
    assert timing.report()['rounds'][0]['status'] == 'world_changed'
    assert timing.report()['completed_turns'] == 0


def test_native_advance_without_anchor_is_explicitly_unattributed():
    timing = TurnTiming()
    timing.observe(record('turn end', 1, 2, tool='end_turn', text='Turn 5 -> 6\n'))
    result = timing.report()
    assert result['completed_turns'] == 0 and not result['rounds']
    assert result['unattributed_confirmed_advance'] == [{'request_id': 'codex-1-turn end', 'segment': 1, 'from': 5, 'to': 6}]
    assert result['quality'] == 'partial'
    assert timing.turn is None

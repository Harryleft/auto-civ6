"""Lossless foreground presentation; every observation still crosses Runtime."""
import asyncio
from copy import deepcopy
import json

import pytest

from civ_mcp.devtools.decision_display import DecisionDisplay, restore_display
from civ_mcp.devtools.commands import parse_command


OWNER = ("runtime-test", 42)
ARGS = {"view": "compact"}


def facts(turn=20):
    units = '\n'.join(["JSON rows: [id,name,position]", *(
        json.dumps([i, "unit name", [i, i]], ensure_ascii=False) for i in range(20))])
    cities = '\n'.join(["JSON rows: [id,name,pop]", '[65536,"Capital",10]',
                         "    Buildings: LIBRARY, UNIVERSITY", "    Districts: CAMPUS(1,2)",
                         '[131073,"Second",5]', "    Buildings: GRANARY"] * 5)
    return {"schema_version": 1, "status": "ready", "pending": dict.fromkeys(
        ("action", "turn", "diplomacy", "congress", "save", "replacement", "popup"), False),
        "scope": "current public facts; not atomic", "identity": {
            "civilization": "sumeria", "seed": 42, "player_id": 0, "turn": turn},
        "checks": dict.fromkeys(("stable_world_turn", "overview_scope", "unit_scope", "city_scope", "victory_scope"), True),
        "collection_seconds": .2, "sections": {k: {"status": "ready", "data": v} for k, v in {
            "overview": f"Turn {turn}", "units": units, "cities": cities, "threats": [],
            "victory": "Science: 0\nCulture: unknown\n" * 20,
            "notifications": "No active notifications.", "blockers": [], "outcome": None}.items()}}


def present(display, value, since=None, args=None, owner=OWNER):
    return display.project(value, arguments=ARGS if args is None else args, owner=owner, since=since)


def test_splices_reconstruct_exact_text_and_do_not_mutate_input():
    display = DecisionDisplay("test-run")
    a = facts()
    full = present(display, a)
    b = facts(21)
    b["sections"]["units"]["data"] = b["sections"]["units"]["data"].replace('[3, "unit name", [3, 3]]\n', '')
    b["sections"]["cities"]["data"] = b["sections"]["cities"]["data"].replace('LIBRARY, UNIVERSITY', 'LIBRARY')
    b["sections"]["victory"]["data"] += "Unicode 地点\r\nno final newline"
    original = deepcopy(b)
    delta = present(display, b, full["presentation"]["token"])
    assert delta["presentation"]["mode"] == "delta"
    assert delta["sections"]["cities"]["data"]["encoding"] == "line-splices-v1"
    assert delta["sections"]["cities"]["data"]["anchors"]
    assert len(json.dumps(delta, ensure_ascii=False)) < len(json.dumps(full, ensure_ascii=False))
    rebuilt = restore_display(full, delta)
    assert json.dumps(rebuilt, ensure_ascii=False).encode() == json.dumps(b, ensure_ascii=False).encode()
    assert b == original and a == restore_display(None, full)


@pytest.mark.parametrize("change,reason", [
    (lambda b: b["identity"].update(turn=19), "turn_gap"),
    (lambda b: b["identity"].update(turn=22), "turn_gap"),
    (lambda b: b["identity"].update(seed=43), "world_changed"),
])
def test_world_and_turn_discontinuity_returns_full(change, reason):
    display = DecisionDisplay("test")
    full = present(display, facts())
    b = facts(21)
    change(b)
    result = present(display, b, full["presentation"]["token"])
    assert result["presentation"]["mode"] == "full"
    assert result["presentation"]["reset_reason"] == reason
    assert restore_display(None, result) == b


@pytest.mark.parametrize("kwargs,reason", [
    ({"since": "unknown"}, "baseline_mismatch"),
    ({"since": None}, "baseline_not_requested"),
    ({"args": {"view": "compact", "include": ["research"]}}, "arguments_changed"),
    ({"owner": ("other-owner", 43)}, "owner_changed"),
])
def test_missing_or_incompatible_explicit_baseline_falls_back(kwargs, reason):
    display = DecisionDisplay("test")
    full = present(display, facts())
    options = {"since": full["presentation"]["token"]} | kwargs
    value = present(display, facts(21), **options)
    assert value["presentation"]["mode"] == "full"
    assert value["presentation"]["reset_reason"] == reason


@pytest.mark.parametrize("change", [
    lambda b: b.update(status="unknown", error="partial native read"),
    lambda b: b["pending"].update(action=True),
    lambda b: b["checks"].update(city_scope=False),
    lambda b: b["sections"].update(units={"status": "unknown", "error": "missing native rows"}),
    lambda b: b["sections"].pop("cities"),
])
def test_partial_error_or_pending_is_full_and_invalidates_baseline(change):
    display = DecisionDisplay("test")
    first = present(display, facts())
    b = facts(21)
    change(b)
    value = present(display, b, first["presentation"]["token"])
    assert value["presentation"]["mode"] == "full"
    assert value["presentation"]["token"] is None
    assert restore_display(None, value) == b
    next_value = present(display, facts(21), first["presentation"]["token"])
    assert next_value["presentation"]["mode"] == "full"


def test_null_and_errors_are_never_replaced_by_old_facts_and_old_token_expires():
    display = DecisionDisplay("test")
    a = present(display, facts())
    b = facts(21)
    b["sections"]["outcome"]["data"] = {"winner": None, "error": "not known"}
    d = present(display, b, a["presentation"]["token"])
    assert d["sections"]["outcome"]["data"] == b["sections"]["outcome"]["data"]
    assert restore_display(a, d) == b
    assert present(display, facts(21), a["presentation"]["token"])["presentation"]["mode"] == "full"
    with pytest.raises(ValueError):
        restore_display(None, d)
    corrupt = deepcopy(a)
    corrupt["sections"]["units"]["data"] += "changed"
    with pytest.raises(ValueError):
        restore_display(corrupt, d)


def test_cli_changes_is_only_presentation_metadata_not_a_server_argument():
    intent = parse_command("game decision --view changes --since example --include research --city 65536")
    assert intent.arguments == {"view": "compact", "include": ["research"], "city_ids": [65536]}
    assert intent.changes and intent.since == "example"
    with pytest.raises(Exception, match="since"):
        parse_command("game decision --view compact --since example")


def test_actual_stdio_changes_reads_every_time_retains_raw_and_timing(tmp_path):
    from test_debug_cli import run_fake
    from civ_mcp.devtools.turn_timing import decision_identity

    async def driver(debug):
        await debug.execute_command("game decision --view changes")
        records = [json.loads(l) for l in (debug.evidence.path / "debug.jsonl").read_text().splitlines()]
        first = next(e for e in reversed(records) if e["event"] == "tool_result")
        first_value = json.loads(first["result"]["content"][0]["text"])
        await debug.execute_command("game decision --view changes --since " + first_value["presentation"]["token"])
        return "PASS"

    status, _, events, engine, evidence = run_fake(tmp_path, "", scenario="decision", driver=driver, compact=True)
    assert status == "PASS" and engine["submissions"] == 0
    assert engine["observed"] == ["get_decision_context", "get_decision_context"]
    results = [e for e in events if e["event"] == "tool_result"]
    first, second = [json.loads(e["result"]["content"][0]["text"]) for e in results]
    assert second["presentation"]["mode"] == "delta"
    raw = json.loads((evidence.path / f"debug-response-{results[1]['request']:06}.json").read_text())
    assert "presentation" not in json.loads(raw["content"][0]["text"])
    assert restore_display(first, second) == json.loads(raw["content"][0]["text"])
    assert results[1]["result"]["structuredContent"]["outcome"]["data"] == second
    assert decision_identity(results[1]["result"])[1] is None
    assert json.loads((evidence.path / "turn-timing.json").read_text())["rounds"][0]["turn"] == 12


def test_stdio_pending_still_refuses_before_native_query(tmp_path):
    from test_debug_cli import run_fake, MOVE
    _, _, events, engine, _ = run_fake(tmp_path, MOVE + "game decision --view changes\npending\nquit --abandon\n")
    value = json.loads(next(e for e in events if e.get("tool") == "get_decision_context" and e["event"] == "tool_result")["result"]["content"][0]["text"])
    assert value["status"] == "blocked" and value["pending"]["action"]
    assert value["presentation"]["token"] is None
    assert "get_decision_context" not in engine["observed"]
    assert engine["submissions"] == 1 and engine["has_pending"]


@pytest.mark.parametrize("tool", ["reload_game_save", "load_game_save", "restart_and_load", "return_to_main_menu"])
def test_session_invalidates_before_world_replacement_even_if_refused(tmp_path, tool):
    from mcp.types import CallToolResult, TextContent, Tool
    from civ_mcp.devtools.debug import DebugSession
    from civ_mcp.devtools.evidence import Evidence
    from test_debug_cli import ROOT

    class Client:
        def __init__(self):
            self.reads = 0
            self.calls = []

        async def call_tool(self, name, arguments):
            self.calls.append(name)
            if name == "get_runtime_status":
                runtime = {"schema_version": 1, "session_id": OWNER[0], "process_id": OWNER[1],
                           "connected": True, "scope": "server_memory", "pending": facts()["pending"], "has_pending": False}
                return CallToolResult(content=[TextContent(type="text", text=json.dumps(runtime))])
            if name == "get_decision_context":
                self.reads += 1
                value = facts()
                text = json.dumps(value, ensure_ascii=False)
                return CallToolResult(content=[TextContent(type="text", text=text)], structuredContent={"result": text,
                    "outcome": {"version": 1, "kind": "query", "status": "ready", "code": None, "data": value}})
            return CallToolResult(content=[TextContent(type="text", text="Error: replacement refused before submission")])

    async def run():
        client, events = Client(), []
        debug = DebugSession(client, Evidence(ROOT, ["test"], str(tmp_path / "evidence")),
                             {"tool_calls": 0}, events.append, False)
        debug.tools = {name: Tool(name=name, inputSchema={"type": "object"}) for name in (tool, "get_decision_context")}
        await debug.call("get_decision_context", ARGS, changes=True)
        token = json.loads(events[-1]["result"]["content"][0]["text"])["presentation"]["token"]
        await debug.call(tool, {"save_name": "test"})
        await debug.call("get_decision_context", ARGS, changes=True, since=token)
        value = json.loads(events[-1]["result"]["content"][0]["text"])
        assert value["presentation"]["mode"] == "full"
        assert value["presentation"]["reset_reason"] == "world_replacement"
        assert client.reads == 2 and client.calls.count(tool) == 1

    asyncio.run(run())


def test_patch_chain_and_same_turn_reads_require_the_latest_decoded_document():
    display = DecisionDisplay("test")
    full = present(display, facts())
    prior = full
    for turn in (20, 21, 22, 23):
        current = facts(turn)
        current["sections"]["units"]["data"] += f"\nunit:{turn}"
        packet = present(display, current, prior["presentation"]["token"])
        decoded = restore_display(prior, packet)
        assert decoded == current
        prior = decoded | {"presentation": packet["presentation"]}
    assert display.baseline["value"] == current


def test_unchanged_null_facts_and_empty_text_survive_full_and_delta():
    display = DecisionDisplay("test")
    a = facts()
    a["sections"]["notifications"]["data"] = ""
    first = present(display, a)
    b = deepcopy(a)
    b["identity"]["turn"] += 1
    b["sections"]["cities"]["data"] = ""
    packet = present(display, b, first["presentation"]["token"])
    assert packet["sections"]["outcome"]["data"] is None
    assert restore_display(first, packet) == b


def test_removed_unit_is_explicit_observation_absence_not_neighbour_or_death():
    display = DecisionDisplay("test")
    a = facts()
    first = present(display, a)
    b = facts(21)
    row = '[3, "unit name", [3, 3]]\n'
    b["sections"]["units"]["data"] = b["sections"]["units"]["data"].replace(row, "")
    packet = present(display, b, first["presentation"]["token"])
    patch = packet["sections"]["units"]["data"]
    assert patch["removed_from_observation"] == [[4, row]]
    assert not patch["anchors"]
    assert restore_display(first, packet) == b


def test_only_building_deletion_retains_exact_old_city_owner():
    display = DecisionDisplay("test")
    a = facts()
    first = present(display, a)
    b = facts(21)
    b["sections"]["cities"]["data"] = b["sections"]["cities"]["data"].replace("    Buildings: LIBRARY, UNIVERSITY\n", "", 1)
    packet = present(display, b, first["presentation"]["token"])
    patch = packet["sections"]["cities"]["data"]
    assert patch["old_anchors"][0] == [1, '[65536,"Capital",10]\n']
    assert "removed_from_observation" not in patch
    assert restore_display(first, packet) == b


@pytest.mark.parametrize("change", [
    lambda r: r.update(isError=True),
    lambda r: r["structuredContent"]["outcome"].update(status="unknown"),
    lambda r: r["structuredContent"]["outcome"].update(code="QUERY_UNCONFIRMED"),
])
def test_conflicting_outer_result_cannot_certify_a_new_baseline(change):
    from civ_mcp.devtools.decision_display import project_result
    display = DecisionDisplay("test")
    first = present(display, facts())
    payload = facts(21)
    text = json.dumps(payload, ensure_ascii=False)
    result = {"content": [{"type": "text", "text": text}], "isError": False,
              "structuredContent": {"result": text, "outcome": {
                  "version": 1, "kind": "query", "status": "ready", "code": None, "data": payload}}}
    change(result)
    original = deepcopy(result)
    shown = project_result(display, result, arguments=ARGS, owner=OWNER, since=first["presentation"]["token"])
    assert shown == original and display.baseline is None

"""Discover required facts locally without crossing the Runtime I/O boundary."""

import pytest

from civ_mcp.devtools.commands import HelpRequested, parse_command
from test_debug_cli import MOVE, run_fake


def help_for(command):
    with pytest.raises(HelpRequested) as result:
        parse_command(command + " --help")
    return result.value.document


@pytest.mark.parametrize("command,required", [
    ("city list", ["majority religion ID/name", "-1=none", "0=valid ID", "null/unknown=unknown", "InGame", "GameCore freshness is unverified"]),
    ("map area", ["GameCore_Tuner Plot:GetYield", "native decimals and negative", "InGame city workforce", "F/P zeros", "G/S/C/Fa=0", "no yield block=unknown"]),
    ("government list", ["can_change_at_all", "can_change_now", "can_select_now/refusal_reasons", "Keep-current ACK", "Paid policy unlock"]),
    ("unit combat-preview", ["Runtime preflight", "before CanStartOperation", "theology and AIR are unsupported", "do not guarantee actual damage", "resources, queues and pending", "hidden state/RNG"]),
    ("save reload", ["active-game reload", "current run", "Save verification results separately", "state overview, unit list, city list", "then pending", "transport_unknown"]),
    ("save load", ["Menu-only load", "--source-run/--sha256", "creates a new run", "then pending"]),
    ("game recover", ["explicit authorized fault recovery", "slow turn alone", "registered checkpoints", "then pending"]),
])
def test_short_help_exposes_fact_and_refusal_contracts(command, required):
    doc = help_for(command)
    assert all(fragment in doc["notes"] for fragment in required)
    assert "Guide: docs/" in doc["notes"]
    assert len(doc["notes"].encode()) < 1400


def test_owned_city_aliases_expose_the_same_majority_and_confirmation_contract():
    city, alias = help_for("city list"), help_for("empire cities")
    assert city["notes"] == alias["notes"]
    assert "Ordinary unit/city reads may confirm" in city["notes"]
    assert "only while it remains pending" in city["notes"]
    assert "after confirmation another action call is a new intent" in city["notes"]
    assert parse_command("city list") == parse_command("empire cities")


def test_short_help_keeps_business_inputs_and_dry_run_scope_unchanged():
    for line, tool, arguments, dry_run in [
        ("city list", "get_cities", {}, False),
        ("map area 41 26 --radius 1", "get_map_area", {"center_x": 41, "center_y": 26, "radius": 1}, False),
        ("government list", "get_public_report", {"section": "governments", "limit": 50, "offset": 0}, False),
        ("unit combat-preview 131073 34 18 --dry-run", "get_combat_preview", {"unit_id": 131073, "target_x": 34, "target_y": 18}, True),
        ("save reload checkpoint --dry-run", "reload_game_save", {"save_name": "checkpoint"}, True),
    ]:
        intent = parse_command(line)
        assert (intent.tool, intent.arguments, intent.dry_run) == (tool, arguments, dry_run)


def test_nested_help_matches_command_help_without_rpc_or_developer_execution(tmp_path):
    commands = ["city list", "empire cities", "map area", "government list", "government change",
                "unit list", "unit move", "unit combat-preview", "save reload", "save load", "game recover", "developer lua"]
    text = "".join(f"help {command}\n{command} --help\n" for command in commands)
    status, state, events, engine, _ = run_fake(tmp_path, text + "quit\n")
    docs = [e for e in events if e["event"] == "help"]
    assert len(docs) == 2 * len(commands)
    for a, b in zip(docs[::2], docs[1::2]):
        assert {k: v for k, v in a.items() if k not in {"sequence", "timestamp"}} == {
            k: v for k, v in b.items() if k not in {"sequence", "timestamp"}}
    assert status == "PASS" and state["tool_calls"] == 0 and engine["submissions"] == 0


def test_help_rejects_action_arguments_and_broken_quotes_without_sending(tmp_path):
    status, state, events, engine, _ = run_fake(tmp_path,
        "help unit move 42 22 9\nhelp pending extra\nhelp city absent\nhelp 'city\nquit\n")
    errors = [e for e in events if e["event"] == "command_blocked"]
    assert len(errors) == 4 and all(e["hint"] for e in errors)
    assert status == "PASS" and state["tool_calls"] == 0 and engine["submissions"] == 0


def test_help_cannot_confirm_or_release_an_existing_action(tmp_path):
    status, state, events, engine, _ = run_fake(tmp_path,
        MOVE + "pending\nhelp city list\ncity list --help\nhelp unit move\nhelp developer lua\npending\n" + MOVE + "pending\nquit\n")
    assert [e["runtime"]["has_pending"] for e in events if e["event"] == "pending"] == [True, True, False]
    assert status == "PASS" and engine["submissions"] == 1 and state["tool_calls"] == 2

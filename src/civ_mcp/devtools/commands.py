"""Typed foreground commands; translate arguments only, never own game state."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import shlex
import re

from civ_mcp.devtools.evidence import CommandError


GROUPS = {
    "unit": "List units or explicitly issue one unit command.",
    "city": "Inspect cities/production or explicitly set a production item.",
    "research": "Inspect technologies/civics or choose research.",
    "turn": "Inspect or explicitly submit/confirm one end-turn request.",
    "save": "List, create or load saves through existing lifecycle tools.",
    "knowledge": "Search static rules and inspect one entry at a chosen depth.",
    "map": "Inspect known terrain around a queried coordinate.",
    "victory": "Read current victory progress and known rival progress.",
    "policy": "Inspect government slots or explicitly assign policy cards.",
    "city-state": "Inspect known city-states or explicitly send one envoy.",
    "governor": "Inspect, appoint, assign or promote governors.",
    "congress": "Read World Congress phase, resolutions and voting options.",
    "notifications": "Read current notifications without dismissing them.",
}


@dataclass(frozen=True)
class Intent:
    tool: str
    arguments: dict
    dry_run: bool = False


class HelpRequested(Exception):
    def __init__(self, document):
        self.document = document


def domain_parser(group: str):
    # Import lazily: the top-level CLI imports DebugSession only at dispatch.
    from civ_mcp.cli import Parser

    class SessionParser(Parser):
        def print_help(self, file=None):
            raise HelpRequested(self.help_document())

        def error(self, message):
            raise CommandError("INVALID_DEBUG_ARGUMENT", message,
                hint=f"In this debug session run {self.prog} --help, then correct the reported argument.")

    root = SessionParser(prog=group, description=GROUPS[group],
        epilog="Foreground session command; retains the same MCP process. Use COMMAND --help for details.")
    commands = root.add_subparsers(required=True)

    def command(name, tool, description, *, mutation=False):
        child = commands.add_parser(name, help=description, description=description,
            epilog=("Query current state first. Preview with --dry-run; actual execution sends once through Runtime. "
                    "Unknown outcomes require inspection of pending, never automatic replay." if mutation else
                    "Returns the original MCP result in a JSONL tool_result event."))
        child.set_defaults(tool=tool)
        if mutation:
            child.add_argument("--dry-run", action="store_true", help="Validate schema and emit a plan; never send the target tool")
        return child

    def coordinates(child):
        child.add_argument("--x", dest="target_x", type=int, default=argparse.SUPPRESS, help="Target X; requires --y")
        child.add_argument("--y", dest="target_y", type=int, default=argparse.SUPPRESS, help="Target Y; requires --x")

    def entity(child, name):
        child.add_argument(name, type=int, help="Native ID from this session's current state query")

    if group == "unit":
        command("list", "get_units", "List current units and native IDs.")
        move = command("move", "unit_action", "Move one unit to a queried coordinate.", mutation=True)
        entity(move, "unit_id")
        move.add_argument("target_x", type=int)
        move.add_argument("target_y", type=int)
        move.set_defaults(action="move")
        action = command("action", "unit_action", "Issue an action from tools unit_action (including destructive actions).", mutation=True)
        entity(action, "unit_id")
        action.add_argument("action", help="Native action name; inspect tools unit_action for supported actions")
        coordinates(action)
        action.add_argument("--improvement", default=argparse.SUPPRESS, help="Improvement ID for builder actions")
        for name in ("attack", "skip", "fortify", "heal", "improve"):
            child = command(name, "unit_action", f"Explicitly {name} one unit; native eligibility is checked at execution.", mutation=True)
            entity(child, "unit_id")
            child.set_defaults(action=name)
            if name == "attack":
                child.add_argument("target_x", type=int)
                child.add_argument("target_y", type=int)
            elif name == "improve":
                child.add_argument("improvement", help="Native improvement type, for example IMPROVEMENT_FARM")
        command("skip-remaining", "skip_remaining_units", "Skip current ready units; preserve standing orders and inspect each native outcome.", mutation=True)
        for name, tool, description, mutation in (
            ("promotions", "get_unit_promotions", "Read this unit's native promotion options.", False),
            ("promote", "promote_unit", "Choose one promotion returned by unit promotions.", True),
            ("upgrade", "upgrade_unit", "Upgrade this unit; costs gold/resources and movement.", True),
            ("path", "get_pathing_estimate", "Estimate a path to a queried coordinate; does not move the unit.", False),
        ):
            child = command(name, tool, description, mutation=mutation)
            entity(child, "unit_id")
            if name == "promote":
                child.add_argument("promotion_type", help="Native promotion type from unit promotions")
            elif name == "path":
                child.add_argument("target_x", type=int)
                child.add_argument("target_y", type=int)
        command("tasks", "get_builder_tasks", "Read builder tasks and current improvements.")
    elif group == "city":
        command("list", "get_cities", "List current cities and native IDs.")
        production = command("production", "get_city_production", "Read one city's available production items.")
        entity(production, "city_id")
        production = command("set-production", "set_city_production", "Choose one production item.", mutation=True)
        entity(production, "city_id")
        production.add_argument("item_type", choices=("UNIT", "BUILDING", "DISTRICT", "PROJECT"))
        production.add_argument("item_name", help="Native item ID from city production")
        coordinates(production)
        attack = command("attack", "city_action", "Fire once at a visible target; requires eligible city defenses.", mutation=True)
        entity(attack, "city_id")
        attack.add_argument("target_x", type=int)
        attack.add_argument("target_y", type=int)
        attack.set_defaults(action="attack")
        district = command("district", "get_district_advisor", "Read native district placement candidates and adjacency.")
        entity(district, "city_id")
        district.add_argument("district_type", help="Native district type, for example DISTRICT_CAMPUS")
        focus = command("focus", "set_city_focus", "Change citizen yield priority; default clears focus.", mutation=True)
        entity(focus, "city_id")
        focus.add_argument("focus", choices=("food", "production", "gold", "science", "culture", "faith", "default"))
        buy = command("buy", "purchase_item", "Purchase an item immediately; spends gold or faith.", mutation=True)
        entity(buy, "city_id")
        buy.add_argument("item_type", choices=("UNIT", "BUILDING"))
        buy.add_argument("item_name", help="Native item ID from city production")
        buy.add_argument("--yield", dest="yield_type", choices=("YIELD_GOLD", "YIELD_FAITH"), default="YIELD_GOLD")
    elif group == "research":
        command("show", "get_tech_civics", "Read available and current technologies/civics.")
        choose = command("set", "set_research", "Choose a technology or civic.", mutation=True)
        choose.add_argument("tech_or_civic", help="Native technology/civic ID from research show")
        choose.add_argument("--category", choices=("tech", "civic"), default="tech")
    elif group == "turn":
        command("status", "get_turn_status", "Read native turn phase and pending diagnostics.")
        command("end", "end_turn", "Submit or confirm exactly one turn request; no blocker auto-resolution.", mutation=True)
    elif group == "save":
        command("list", "list_saves", "List saves; the filesystem fallback currently scans Single, not Hotseat.")
        for name, tool, description in (
            ("create", "save_game", "Save to an independent name; existing ordinary files may be overwritten."),
            ("reload", "reload_game_save", "Return to menu and load a verified save; discards unsaved changes."),
            ("load", "load_game_save", "Load a verified save from the main menu; does not exit an active game."),
        ):
            child = command(name, tool, description, mutation=True)
            child.add_argument("save_name", help="Exact save name; keep baselines and retained checkpoints unchanged")
    elif group == "knowledge":
        search = command("search", "search_knowledge", "Search rules by name or native ID; quote queries containing spaces.")
        search.add_argument("query")
        search.add_argument("--topic", default=argparse.SUPPRESS, help="Knowledge topic; validated against the real tool schema")
        search.add_argument("--limit", type=int, default=5, help="Maximum results (1..20); default 5")
        show = command("show", "get_knowledge", "Read one static entry; does not establish game eligibility.")
        show.add_argument("entry_id")
        show.add_argument("--level", choices=("summary", "rule", "evidence"), default="summary")
    elif group == "map":
        area = command("area", "get_map_area", "Read known terrain; radius is 1..4 without silent clamping.")
        area.add_argument("center_x", type=int)
        area.add_argument("center_y", type=int)
        area.add_argument("--radius", type=int, choices=range(1, 5), default=2)
    elif group == "victory":
        command("show", "get_victory_progress", "Read victory progress; hidden rival facts remain unknown.")
    elif group == "policy":
        command("show", "get_policies", "Read government slots and available policy cards.")
        assign = command("set", "set_policies", "Assign explicit SLOT=POLICY_ID pairs; NONE clears a slot, omitted slots stay unchanged.", mutation=True)
        assign.add_argument("assignments", nargs="+", help="Pairs separated by spaces or commas; for example 0=POLICY_AGOGE 1=NONE")
    elif group == "city-state":
        command("list", "get_city_states", "Read known city-states, player IDs, envoys and suzerains.")
        envoy = command("envoy", "send_envoy", "Send one envoy; consumes one available token when confirmed.", mutation=True)
        envoy.add_argument("player_id", type=int, help="City-state player ID from city-state list")
    elif group == "governor":
        command("list", "get_governors", "Read governors, titles, assignments and native options.")
        for name, tool, description in (
            ("appoint", "appoint_governor", "Appoint one governor; spends an available title."),
            ("assign", "assign_governor", "Reassign one appointed governor to an identified city."),
            ("promote", "promote_governor", "Choose one promotion; spends an available title."),
        ):
            child = command(name, tool, description, mutation=True)
            child.add_argument("governor_type", help="Native governor type from governor list")
            if name == "assign":
                entity(child, "city_id")
                child.add_argument("--owner", dest="target_owner", type=int, default=argparse.SUPPRESS,
                                   help="Destination player ID (0..63); omit for the local player")
            elif name == "promote":
                child.add_argument("promotion_type", help="Native governor promotion from governor list")
    elif group == "congress":
        command("show", "get_world_congress", "Read native stage and choices; never opens voting or submits votes.")
    elif group == "notifications":
        command("show", "get_notifications", "Read notifications; never dismisses popups or chooses responses.")
    return root


def parse_command(line: str) -> Intent:
    try:
        words = shlex.split(line)
    except ValueError as exc:
        raise CommandError("INVALID_DEBUG_ARGUMENT", str(exc),
            hint="Close the quoted argument; use double quotes around names with spaces. No shell expansion is performed.") from exc
    values = vars(domain_parser(words[0]).parse_args(words[1:]))
    tool, dry_run = values.pop("tool"), values.pop("dry_run", False)
    if ("target_x" in values) != ("target_y" in values):
        raise CommandError("INVALID_DEBUG_ARGUMENT", "Target coordinates must include both --x and --y",
                           hint=f"Use {words[0]} COMMAND --help and supply both coordinates or neither.")
    if tool == "set_policies":
        pairs = re.split(r"\s*,\s*|\s+", " ".join(values["assignments"]).strip())
        slots = set()
        normalized = []
        for pair in pairs:
            match = re.fullmatch(r"([0-9]+)=(POLICY_[A-Z0-9_]+|NONE)", pair.strip())
            if match is None or int(match[1]) in slots:
                raise CommandError("INVALID_DEBUG_ARGUMENT", f"Malformed or duplicate policy slot assignment: {pair}",
                    hint="Use policy show, then policy set SLOT=POLICY_ID [SLOT=NONE] --dry-run; assign each slot only once.")
            slots.add(int(match[1]))
            normalized.append(f"{int(match[1])}={match[2]}")
        values["assignments"] = ",".join(normalized)
    return Intent(tool, values, dry_run)

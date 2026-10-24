"""Typed foreground commands; translate arguments only, never own game state."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import shlex

from civ_mcp.devtools.evidence import CommandError


GROUPS = {
    "unit": "List units or explicitly issue one unit command.",
    "city": "Inspect cities/production or explicitly set a production item.",
    "research": "Inspect technologies/civics or choose research.",
    "turn": "Inspect or explicitly submit/confirm one end-turn request.",
    "save": "List, create or load saves through existing lifecycle tools.",
    "knowledge": "Search static rules and inspect one entry at a chosen depth.",
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
    elif group == "city":
        command("list", "get_cities", "List current cities and native IDs.")
        production = command("production", "get_city_production", "Read one city's available production items.")
        entity(production, "city_id")
        production = command("set-production", "set_city_production", "Choose one production item.", mutation=True)
        entity(production, "city_id")
        production.add_argument("item_type", choices=("UNIT", "BUILDING", "DISTRICT", "PROJECT"))
        production.add_argument("item_name", help="Native item ID from city production")
        coordinates(production)
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
    return Intent(tool, values, dry_run)

"""Civ6 developer CLI: offline checks and an explicit foreground MCP session."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from civ_mcp.devtools.evidence import CommandError, Evidence, find_project, now
from civ_mcp.devtools.testing import DOMAINS, run_tests


class Parser(argparse.ArgumentParser):
    machine_help = False

    def help_document(self) -> dict:
        options, subcommands = [], []
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                subcommands = [{"name": name, "description": child.description,
                                "help_command": f"{child.prog} --help"}
                               for name, child in action.choices.items()]
            else:
                item = {"names": action.option_strings or [action.dest], "help": action.help,
                        "required": action.required}
                if action.choices is not None:
                    item["choices"] = list(action.choices)
                if action.default != argparse.SUPPRESS:
                    item["default"] = action.default
                options.append(item)
        return {"schema_version": 1, "kind": "help", "command": self.prog,
                "usage": self.format_usage().strip(), "description": self.description,
                "options": options, "subcommands": subcommands, "notes": self.epilog}

    def print_help(self, file=None):
        if self.machine_help:
            print(json.dumps(self.help_document(), ensure_ascii=False), file=file or sys.stdout)
        else:
            super().print_help(file)

    def configure_help(self, machine: bool):
        self.machine_help = machine
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    child.configure_help(machine)

    def error(self, message):
        raise CommandError("INVALID_ARGUMENT", message, hint=f"Run {self.prog} --help and correct the reported argument.")


def positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def limit(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError("must be between 1 and 1000")
    return number


def common(parser: Parser) -> None:
    parser.add_argument("--project", default=argparse.SUPPRESS, help="Source checkout; discovered from cwd or installed source")
    parser.add_argument("--output", default=argparse.SUPPRESS, help="New evidence directory; never overwrites existing paths")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSON results (default); combine with --help for a machine-readable help document")


def parser() -> Parser:
    result = Parser(prog="civ6", description=__doc__,
        epilog="Explore: civ6 COMMAND --help. Results: JSON; debug: line commands on stdin, JSONL on stdout. "
               "Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED, 130 interrupted. "
               "Help does not start MCP or create evidence.")
    common(result)
    commands = result.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Check local readiness without game access",
        description="Check interpreter, dependencies, knowledge integrity and real stdio MCP discovery. No game tools are called.",
        epilog="Example: civ6 doctor --knowledge-dir civ6_knowledge. Missing optional dev dependencies are NOT_RUN.")
    common(doctor)
    doctor.add_argument("--knowledge-dir", help="Complete knowledge package; default PROJECT/civ6_knowledge")
    verify = commands.add_parser("verify", help="Verify knowledge offline or inspect a live game", description="Choose a feature to verify; use civ6 verify FEATURE --help for its contract.")
    common(verify)
    verification = verify.add_subparsers(dest="feature", required=True)
    knowledge = verification.add_parser("knowledge", help="Compare CLI and MCP knowledge responses",
        description="Validate the knowledge package, query writing/TECH_WRITING through CLI and MCP, and compare results. No game access.",
        epilog="Example: civ6 verify knowledge --output .scratch/my-new-proof. Input knowledge files remain unchanged.")
    common(knowledge)
    knowledge.add_argument("--knowledge-dir", help="Complete knowledge package; default PROJECT/civ6_knowledge")
    state = verification.add_parser("state", help="Verify the current real game without mutations",
        description="Use one MCP session to read native identity/overview/entities and inspect pending. No load, actions, turn advance or retries.",
        epilog="Example: civ6 verify state --expect-turn 136 --expect-civilization CIVILIZATION_SWEDEN. "
               "Requires an already running game and exclusive control; never starts or replaces it.")
    common(state)
    state.add_argument("--timeout", type=positive, default=300, help="Per-RPC timeout in seconds; default 300")
    state.add_argument("--expect-turn", type=int, help="Require this current game turn; mismatch is BLOCKED")
    state.add_argument("--expect-civilization", help="Require this native CIVILIZATION_* ID; mismatch is BLOCKED")
    test = commands.add_parser("test", help="Run a named selection of isolated tests",
        description="Discover domains with --list, then run one registered offline selection.",
        epilog="Example: civ6 test turns -- -k pending -x. After --: -q/-v/-vv/-x, -k EXPR, -m EXPR, "
               "--maxfail N, --collect-only, --disable-warnings. Paths, plugins and config overrides are rejected.")
    common(test)
    test.add_argument("domain", nargs="?", choices=DOMAINS, help="One domain; --list shows its exact test files")
    test.add_argument("--list", action="store_true", help="List domain selections without running tests")
    test.add_argument("--collect-only", action="store_true", help="Collect tests without executing test cases; does not prove they pass")
    test.add_argument("--timeout", type=positive, default=300, help="Subprocess timeout in seconds; default 300")
    debug = commands.add_parser("debug", help="Keep one foreground MCP process; commands arrive on stdin",
        description="One MCP process and pending context for the entire foreground session. Input: one command per line; output: JSONL. Startup reads server memory only.",
        epilog="Start safely: civ6 debug --offline. In-session discovery: help, help call, tools, tools NAME. "
               "Preview: call --dry-run TOOL JSON_OBJECT. Quit may refuse while pending. "
               "No retry, restart or game load occurs automatically.")
    common(debug)
    debug.add_argument("--offline", action="store_true", help="Allow only knowledge tools and in-memory pending inspection")
    debug.add_argument("--timeout", type=positive, default=300, help="Per-RPC timeout; never retries or restarts")
    logs = commands.add_parser("logs", help="Read existing action logs; never query the game", description="Choose list, show or timeline. Reads bounded snapshots of existing JSONL files; no game queries.")
    common(logs)
    log_commands = logs.add_subparsers(dest="view", required=True)
    for view in ("list", "show", "timeline"):
        descriptions = {"list": "List action log sessions from newest file modification time.",
                        "show": "Read the last matching records in file order.",
                        "timeline": "Read the newest matching records, sorted by timestamp ascending."}
        log_parser = log_commands.add_parser(view, help=descriptions[view], description=descriptions[view],
            epilog="JSON data includes source paths and counts. " + ("Example: civ6 logs list --limit 10." if view == "list" else
            "Example: civ6 logs " + view + " --file PATH --tool get_units. Filters combine with AND; limits are 1..1000."))
        common(log_parser)
        log_parser.add_argument("--dir", help="Action log directory; default ~/.civ6-mcp/actions")
        log_parser.add_argument("--limit", type=limit, default=20 if view == "list" else 50, help="Maximum returned items (1..1000); default 20 for list, otherwise 50")
        if view != "list":
            log_parser.add_argument("session", nargs="?", help="Session ID from logs list; mutually exclusive with --file")
            log_parser.add_argument("--file", help="Read one explicit JSONL file instead of a session ID")
            log_parser.add_argument("--tool", help="Exact MCP tool name filter")
            log_parser.add_argument("--contains", help="Literal substring in result")
            log_parser.add_argument("--since", help="Inclusive ISO-8601 time with timezone")
            log_parser.add_argument("--until", help="Inclusive ISO-8601 time with timezone")
    return result


def execute(args: argparse.Namespace, evidence: Evidence, extra: list[str], data: dict) -> str:
    if extra and args.command != "test":
        raise CommandError("INVALID_ARGUMENT", "Arguments after -- are only accepted by test")
    if args.command == "verify" and args.feature == "state":
        from civ_mcp.devtools.live import verify_live
        return verify_live(evidence, data, timeout=args.timeout, expected_turn=args.expect_turn,
                           expected_civilization=args.expect_civilization)
    if args.command in {"doctor", "verify"}:
        from civ_mcp.devtools.knowledge import doctor, knowledge_root, verify
        root = knowledge_root(evidence, args.knowledge_dir)
        data["knowledge_directory"] = str(root)
        (doctor if args.command == "doctor" else verify)(evidence, root, data)
    elif args.command == "debug":
        from civ_mcp.devtools.debug import run_debug
        return run_debug(evidence, data, offline=args.offline, timeout=args.timeout)
    elif args.command == "test":
        if args.list:
            if args.domain or extra or args.collect_only:
                raise CommandError("INVALID_ARGUMENT", "--list does not run or select tests")
            data["domains"] = {key: [f"tests/{name}" for name in names] for key, names in DOMAINS.items()}
        else:
            if not args.domain:
                raise CommandError("INVALID_ARGUMENT", "Choose a test domain or --list")
            options = ["--collect-only"] if args.collect_only else []
            return run_tests(evidence, args.domain, [*options, *extra], args.timeout, data)
    else:
        from civ_mcp.devtools.logs import read_logs, sessions
        directory = Path(args.dir).expanduser().resolve() if args.dir else Path.home() / ".civ6-mcp/actions"
        if args.view == "list":
            data.update(sessions(directory, args.limit))
        else:
            if args.session and args.file:
                raise CommandError("INVALID_ARGUMENT", "Choose a session ID or --file, not both")
            data.update(read_logs(directory, args.session, args.file, limit=args.limit,
                                  tool=args.tool, contains=args.contains, since=args.since,
                                  until=args.until, timeline=args.view == "timeline"))
        evidence.json("logs", data)
    return "PASS"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    command = list(sys.argv[1:] if argv is None else argv)
    left, extra = (command[:command.index("--")], command[command.index("--") + 1:]) if "--" in command else (command, [])
    evidence = None
    data = {}
    fallback = {"schema_version": 1, "command": command, "mode": "offline", "game_verification": "not_run",
                "status": "BLOCKED", "exit_code": 2, "evidence": None, "data": {}, "errors": []}
    try:
        argument_parser = parser()
        argument_parser.configure_help("--json" in left)
        args = argument_parser.parse_args(left)
        project = find_project(getattr(args, "project", None))
        configured = getattr(args, "knowledge_dir", None)
        target = getattr(args, "output", None)
        if configured:
            candidate = Path(target).expanduser().resolve() if target else project / ".scratch/civ6"
            if candidate.is_relative_to(Path(configured).expanduser().resolve()):
                raise CommandError("INVALID_OUTPUT", "Evidence must be outside the knowledge directory")
        evidence = Evidence(project, command, getattr(args, "output", None))
        status = execute(args, evidence, extra, data)
        result = evidence.finish(status, data)
    except KeyboardInterrupt:
        error = CommandError("INTERRUPTED", "Interrupted; inspect retained evidence before retrying")
        result = evidence.finish("BLOCKED", data, error) if evidence else fallback
        if not evidence:
            result["errors"] = [error.as_dict()]
        result["exit_code"] = 130
        if evidence:
            evidence.save()
    except Exception as exc:
        error = exc if isinstance(exc, CommandError) else CommandError(
            type(exc).__name__, str(exc), "BLOCKED" if isinstance(exc, OSError) else "FAIL")
        if evidence:
            result = evidence.finish(error.status, data, error)
        else:
            result = fallback | {"status": error.status, "exit_code": 1 if error.status == "FAIL" else 2,
                                 "finished_at": now(), "errors": [error.as_dict()]}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

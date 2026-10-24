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
    def error(self, message):
        raise CommandError("INVALID_ARGUMENT", message)


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
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSON output (already the default)")


def parser() -> Parser:
    result = Parser(prog="civ6", description=__doc__)
    common(result)
    commands = result.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Check interpreter, knowledge and stdio MCP; no game access")
    common(doctor)
    doctor.add_argument("--knowledge-dir")
    verify = commands.add_parser("verify", help="Verify a real offline user path")
    common(verify)
    verification = verify.add_subparsers(dest="feature", required=True)
    knowledge = verification.add_parser("knowledge", help="Compare real CLI and stdio MCP knowledge responses")
    common(knowledge)
    knowledge.add_argument("--knowledge-dir")
    test = commands.add_parser("test", help="Run a named selection of isolated tests")
    common(test)
    test.add_argument("domain", nargs="?", choices=DOMAINS)
    test.add_argument("--list", action="store_true", help="List domain selections without running tests")
    test.add_argument("--collect-only", action="store_true")
    test.add_argument("--timeout", type=positive, default=300)
    debug = commands.add_parser("debug", help="Keep one foreground MCP process; commands arrive on stdin")
    common(debug)
    debug.add_argument("--offline", action="store_true", help="Allow only knowledge tools and in-memory pending inspection")
    debug.add_argument("--timeout", type=positive, default=300, help="Per-RPC timeout; never retries or restarts")
    logs = commands.add_parser("logs", help="Read existing action logs; never query the game")
    common(logs)
    log_commands = logs.add_subparsers(dest="view", required=True)
    for view in ("list", "show", "timeline"):
        log_parser = log_commands.add_parser(view)
        common(log_parser)
        log_parser.add_argument("--dir", help="Action log directory; default ~/.civ6-mcp/actions")
        log_parser.add_argument("--limit", type=limit, default=20 if view == "list" else 50)
        if view != "list":
            log_parser.add_argument("session", nargs="?")
            log_parser.add_argument("--file", help="Read one explicit JSONL file instead of a session ID")
            log_parser.add_argument("--tool")
            log_parser.add_argument("--contains", help="Literal substring in result")
            log_parser.add_argument("--since", help="Inclusive ISO-8601 time with timezone")
            log_parser.add_argument("--until", help="Inclusive ISO-8601 time with timezone")
    return result


def execute(args: argparse.Namespace, evidence: Evidence, extra: list[str], data: dict) -> str:
    if extra and args.command != "test":
        raise CommandError("INVALID_ARGUMENT", "Arguments after -- are only accepted by test")
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
        args = parser().parse_args(left)
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
            result["errors"] = [{"code": error.code, "message": str(error)}]
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
                                 "finished_at": now(), "errors": [{"code": error.code, "message": str(error)}]}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

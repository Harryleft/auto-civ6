"""One foreground client and one owned MCP process for a complete debug session."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from typing import Callable, TextIO

from civ_mcp.devtools.evidence import CommandError, Evidence, now
from civ_mcp.devtools.knowledge import check_interpreter
from civ_mcp.devtools.commands import GROUPS, HelpRequested, domain_parser, parse_command


OFFLINE_TOOLS = {"get_runtime_status", "search_knowledge", "search_research", "get_knowledge"}
STATES = {"overview": "get_game_overview", "units": "get_units", "cities": "get_cities",
          "turn": "get_turn_status", "snapshot": "get_game_snapshot"}
HELP = {
    "overview": {"description": "Choose a named command topic and use TOPIC --help for its arguments. tools/call are protocol diagnostics only.",
                 "topics": ["tools", "pending", "state", *GROUPS, "verify", "call", "quit"],
                 "usage": "help [TOPIC]", "output": "One JSON object per line; prompts use stderr."},
    "tools": {"usage": "tools [NAME]", "description": "Protocol diagnostics: list internal names, or inspect one tool's inputSchema and annotations. Normal gameplay uses named commands.",
              "examples": ["tools", "tools get_runtime_status"]},
    "pending": {"usage": "pending", "description": "Read server memory and local transport_unknown without game I/O or clearing pending."},
    "state": {"usage": "state overview|units|cities|turn|snapshot", "aliases": STATES,
              "description": "Query current game state through MCP. These queries are unavailable in --offline mode."},
    "verify": {"usage": "verify state", "description": "Run a bounded live read-only snapshot check in this same MCP session; no game mutations or retries."},
    "call": {"usage": "call [--dry-run] TOOL [JSON_OBJECT]",
             "description": "Protocol compatibility diagnostics only; normal gameplay uses named commands. Validate against tools TOOL inputSchema, then explicitly send once.",
             "dry_run": "Returns a schema-checked plan without sending the target tool or reading game state. Does not check gameplay prerequisites or confirm pending.",
             "examples": ['call get_runtime_status {}', 'call --dry-run get_units {}'],
             "json_input": "One strict JSON object; default {}. No shell expansion. Feed one command per line through stdin to compose scripts."},
    "quit": {"usage": "quit [--abandon] [--dry-run]",
             "description": "Normal quit refuses pending/unknown context. --abandon is the explicit escape: it closes the session, records the lost context and never retires or replays the game-side request.",
             "dry_run": "Shows whether exit would lose unresolved context; retains the session.",
             "examples": ["quit --abandon --dry-run", "quit"]},
}


class ObservedRead:
    """Expose stream closure while the foreground is waiting for terminal input."""
    def __init__(self, stream, closed: asyncio.Event):
        self.stream, self.closed = stream, closed

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.stream.__aexit__(*args)

    def __aiter__(self):
        return self

    async def __anext__(self):
        import anyio
        try:
            return await self.stream.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError):
            self.closed.set()
            raise StopAsyncIteration


async def next_line(source: TextIO, closed: asyncio.Event, interrupted: threading.Event | None = None) -> str:
    reading = asyncio.create_task(readline(source, interrupted))
    disconnected = asyncio.create_task(closed.wait())
    try:
        done, _ = await asyncio.wait({reading, disconnected}, return_when=asyncio.FIRST_COMPLETED)
        if disconnected in done:
            raise CommandError("MCP_CLOSED", "MCP process/pipe closed; context cannot be resumed automatically", "FAIL")
        return reading.result()
    finally:
        reading.cancel()
        disconnected.cancel()
        await asyncio.gather(reading, disconnected, return_exceptions=True)


async def readline(source: TextIO, interrupted: threading.Event | None = None) -> str:
    # A daemon input reader lets a broken MCP transport unwind while stdin is
    # still open. The default executor would wait for that blocked read at exit.
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(value, error):
        if not future.done():
            if error:
                future.set_exception(error)
            else:
                future.set_result(value)

    def read():
        try:
            while True:
                try:
                    value = source.readline()
                except InterruptedError:
                    continue
                signalled = interrupted is not None and interrupted.is_set()
                if signalled:
                    interrupted.clear()
                # Windows console ^C can return zero bytes instead of raising.
                # Retry that terminal read; real EOF and piped EOF keep their
                # original explicit shutdown semantics. Never replace the MCP.
                if value == "" and source.isatty() and signalled:
                    continue
                break
            error = None
        except Exception as exc:
            value, error = None, exc
        try:
            loop.call_soon_threadsafe(deliver, value, error)
        except RuntimeError:
            pass

    threading.Thread(target=read, daemon=True, name="civ6-debug-input").start()
    return await future


def strict_json(value: str) -> dict:
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"Duplicate key: {key}")
            result[key] = item
        return result

    def constant(value):
        raise ValueError(f"Non-JSON number: {value}")

    def number(value):
        import math
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"Non-finite JSON number: {value}")
        return result

    try:
        result = json.loads(value, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
        if not isinstance(result, dict):
            raise ValueError("Arguments must be a JSON object")
        return result
    except ValueError as exc:
        raise CommandError("INVALID_JSON", str(exc)) from exc


def tool_error(tool: str, result) -> dict | None:
    """Recognize only the server's explicit error envelopes, never infer success."""
    texts = [item.text for item in result.content if item.type == "text"]
    legacy = next((text for text in texts if text.startswith(("Error:", "ERR:"))), None)
    structured = None
    for text in texts:
        try:
            value = json.loads(text)
            if isinstance(value, dict) and value.get("status") in {"error", "unknown"} and value.get("error"):
                structured = value
                break
        except ValueError:
            pass
    if not result.isError and legacy is None and structured is None:
        return None
    message = legacy or (str(structured["error"]) if structured else None) or "\n".join(texts) or "MCP reported a tool error without text"
    match = re.match(r"^(?:Error:\s*)?ERR:([A-Z][A-Z0-9_]*)\b", message)
    code = match[1] if match else "MCP_TOOL_ERROR" if result.isError else "TOOL_REPORTED_ERROR"
    if structured and structured.get("status") == "unknown":
        code = "QUERY_UNCONFIRMED" if tool.startswith(("get_", "search_")) else "OUTCOME_UNKNOWN"
    hint = f"Inspect the original error, tools {tool} and pending before correcting arguments; never blindly replay an unknown request."
    if code == "QUERY_UNCONFIRMED":
        hint = "Inspect identity, unknown_fields and error; unavailable data is not an empty list or zero."
    if code == "NO_TILES":
        hint = "Use city production CITY_ID and current state to choose a different eligible city/district, then query city district CITY_ID DISTRICT_TYPE. No construction was submitted."
    elif code == "OUTCOME_UNKNOWN":
        hint = "Retain this session; use pending and read-only inspection, then confirm only through the original tool with its original arguments. Do not resubmit a new intent."
    return {"code": code, "message": message, "hint": hint,
            "details": {"tool": tool, "source": "protocol" if result.isError else "structured_json" if structured else "legacy_text"}}


@contextmanager
def keep_session_on_interrupt():
    previous = signal.getsignal(signal.SIGINT)
    interrupted = threading.Event()

    def interrupt(signum, frame):
        # The SDK isolates the child process group on POSIX. Do not cancel an
        # in-flight MCP call or replace its process when the terminal sends ^C.
        interrupted.set()
        print("\nSession retained; the current request continues. Use pending or quit when it returns.",
              file=sys.stderr, flush=True)

    signal.signal(signal.SIGINT, interrupt)
    try:
        yield interrupted
    finally:
        signal.signal(signal.SIGINT, previous)


class DebugSession:
    def __init__(self, client, evidence: Evidence, state: dict, emit: Callable[[dict], None], offline: bool,
                 *, developer: bool = False, shared_pending_path: Path | None = None):
        self.client, self.evidence, self.state, self.emit = client, evidence, state, emit
        self.offline = offline
        self.developer = developer
        self.shared_pending_path = shared_pending_path
        self.sequence = 0
        self.tools = {}
        self.identity = None
        self.transport_unknown = None
        self.identity_lost = False
        from civ_mcp.devtools.turn_timing import TurnTiming
        self.timing = TurnTiming()
        self.command_events = None

    def event(self, event: str, **data) -> dict:
        self.sequence += 1
        value = {"event": event, "sequence": self.sequence, "run_id": self.evidence.summary["run_id"],
                 "timestamp": now(), **data}
        if self.command_events is not None:
            self.command_events.append(value)
        with (self.evidence.path / "debug.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        self.emit(value)
        return value

    async def runtime(self) -> dict:
        result = await self.client.call_tool("get_runtime_status", {})
        value = strict_json("\n".join(item.text for item in result.content if item.type == "text"))
        flags = value.get("pending")
        if (result.isError or value.get("schema_version") != 1 or value.get("scope") != "server_memory"
                or type(value.get("connected")) is not bool
                or not isinstance(value.get("session_id"), str) or not value["session_id"]
                or type(value.get("process_id")) is not int or not isinstance(flags, dict)
                or set(flags) != {"action", "turn", "diplomacy", "congress", "save", "replacement"}
                or any(type(flag) is not bool for flag in flags.values())
                or type(value.get("has_pending")) is not bool or value["has_pending"] != any(flags.values())):
            raise CommandError("RUNTIME_STATUS_INVALID", "Server did not return complete pending facts")
        identity = (value["session_id"], value["process_id"])
        if self.identity is None:
            self.identity = identity
        elif self.identity != identity:
            self.identity_lost = True
        if self.identity_lost:
            raise CommandError("SESSION_CHANGED", "Original server identity was lost; do not continue game commands")
        if self.shared_pending_path is not None:
            # Experimental hotseat has an additional cross-seat owner on disk.
            # Inspect it without clearing it or relabelling it as server memory.
            try:
                shared = json.loads(self.shared_pending_path.read_text(encoding="utf-8"))
                if not isinstance(shared, dict) or not shared:
                    raise ValueError("Expected a nonempty hotseat request")
            except FileNotFoundError:
                shared = None
            except (OSError, ValueError) as exc:
                raise CommandError("HOTSEAT_PENDING_UNKNOWN", f"Shared pending is unreadable: {exc}") from exc
            value["shared_pending"] = shared
        self.state["runtime"] = value
        self.evidence.json("runtime-status", value)
        return value

    async def start(self):
        tools = await self.client.list_tools()
        self.evidence.json("debug-tools", tools.model_dump(mode="json"))
        self.tools = {tool.name: tool for tool in tools.tools if self.developer or tool.name != "run_lua"}
        if "get_runtime_status" not in self.tools:
            raise CommandError("RUNTIME_STATUS_MISSING", "Reload the current server code before debugging")
        value = await self.runtime()
        self.event("session_ready", runtime=value, offline=self.offline, help="help [TOPIC]")

    async def call(self, tool: str, arguments: dict, *, dry_run: bool = False):
        if tool == "run_lua" and not self.developer:
            raise CommandError("DEVELOPER_MODE_REQUIRED", "Arbitrary Lua is unavailable in gameplay sessions",
                               hint="Use named gameplay commands. Only an explicitly authorized diagnostic session starts with civ6 debug --developer.")
        if tool in {"begin_hotseat_turn", "end_hotseat_turn", "hotseat_actions"} and tool not in self.tools:
            raise CommandError("HOTSEAT_MODE_REQUIRED", "Native hotseat commands require the dedicated controller",
                               hint="Use the authorized existing run with civ6 debug --hotseat-seat N --hotseat-run DIR; normal turn end cannot hand off a human seat.")
        if tool not in self.tools:
            raise CommandError("UNKNOWN_TOOL", f"Unknown tool: {tool}; use tools")
        # Validate before sending even for normal calls, so invalid arguments have
        # stable field paths and repair instructions rather than opaque RPC text.
        from jsonschema import Draft202012Validator
        from referencing import Registry
        schema = self.tools[tool].inputSchema
        errors = list(Draft202012Validator(schema, registry=Registry()).iter_errors(arguments))
        if errors:
            issues = [{"path": list(error.absolute_path), "rule": error.validator,
                       "message": error.message} for error in errors]
            raise CommandError("INVALID_TOOL_ARGUMENT", f"{tool}: {issues[0]['message']}",
                hint=f"Use tools {tool}, correct arguments at the paths in details.issues, then repeat call --dry-run {tool} JSON_OBJECT.",
                details={"tool": tool, "issues": issues})
        readonly = self.tools[tool].annotations and self.tools[tool].annotations.readOnlyHint is True
        if dry_run:
            self.event("dry_run", tool=tool, arguments=arguments, schema_valid=True,
                       potentially_mutating=not bool(readonly), would_send=False,
                       local_policy_allows_execution=not (self.offline and tool not in OFFLINE_TOOLS)
                           and not bool((self.transport_unknown or self.identity_lost) and not readonly),
                       scope="schema_only", pending_checked=False,
                       hint="Preview checks input schema only. Query current state before using IDs/coordinates; Runtime still enforces pending and game rules.")
            return
        if self.offline and tool not in OFFLINE_TOOLS:
            raise CommandError("OFFLINE_ONLY", f"{tool} is unavailable in --offline sessions")
        if (self.transport_unknown or self.identity_lost) and not readonly:
            raise CommandError(
                "TRANSPORT_UNRESOLVED",
                "An RPC outcome/identity is unknown; only read-only inspection is allowed",
                hint="Inspect current state with reads; if the original outcome cannot be confirmed, "
                     "close this session explicitly with quit --abandon (records the lost context) "
                     "or keep it for evidence. Never start a second controller for the same game.",
            )
        runtime = await self.runtime()
        if tool == "get_game_snapshot" and runtime.get("shared_pending"):
            raise CommandError("VERIFY_PENDING", "A shared hotseat request still owns this run",
                               hint="Inspect pending and confirm its original intent; a snapshot cannot retire or bypass the shared request.")
        request = self.event("tool_started", tool=tool, arguments=arguments)
        self.state["tool_calls"] += 1
        try:
            # No retries, process replacement or client-side action confirmation.
            result = await self.client.call_tool(tool, arguments)
        except Exception as exc:
            unknown = {"tool": tool, "arguments": arguments, "request_sequence": request["sequence"],
                       "error": f"{type(exc).__name__}: {exc}"}
            if self.transport_unknown is None:
                self.transport_unknown = unknown
                self.state["transport_unknown"] = unknown
                self.evidence.json("transport-unknown", unknown)
            self.event("tool_outcome_unknown", request=request["sequence"], **unknown,
                       hint="Use pending and read-only inspection; retain this process and do not replay the unknown mutation.")
            return None
        self.evidence.json(f"debug-response-{request['sequence']:06}", result.model_dump(mode="json"))
        error = tool_error(tool, result)
        self.event("tool_result", request=request["sequence"], tool=tool,
                   is_error=error is not None, protocol_is_error=bool(result.isError),
                   error=error, result=result.model_dump(mode="json"), hint=error["hint"] if error else None)
        return result

    async def verify_state(self, *, expected_turn: int | None = None, expected_civilization: str | None = None):
        from civ_mcp.devtools.live import verify_session
        return await verify_session(self, expected_turn=expected_turn, expected_civilization=expected_civilization)

    async def close_request(self, abandon: bool, eof: bool = False, *, dry_run: bool = False) -> bool:
        try:
            runtime = await self.runtime()
            unresolved = runtime["has_pending"] or bool(runtime.get("shared_pending")) or bool(self.transport_unknown)
        except Exception as exc:
            unresolved = True
            self.state["close_status_error"] = f"{type(exc).__name__}: {exc}"
        if dry_run:
            self.event("dry_run", operation="quit", abandon=abandon, would_exit=abandon or not unresolved,
                       would_lose_context=unresolved, would_send=False, session_retained=True)
            return False
        if unresolved and not abandon and not eof:
            error = CommandError("PENDING_ON_EXIT", "Session retained; unresolved context prevents normal exit")
            self.event("command_blocked", **error.as_dict())
            return False
        self.state.update(unresolved_on_exit=unresolved,
                          exit_reason="eof_unresolved" if eof and unresolved else "abandoned" if abandon and unresolved else "eof" if eof else "quit")
        self.event("session_closing", unresolved=unresolved, reason=self.state["exit_reason"],
                   transport_unknown=self.transport_unknown)
        return True

    async def execute_command(self, line: str) -> bool:
        """Frame timing around the entire command, including verification RPCs."""
        started, tick = time.time(), time.monotonic()
        self.command_events = []
        first = self.event("command", input=line.rstrip("\r\n"))
        try:
            return await self.command(line)
        except Exception as exc:
            error = exc if isinstance(exc, CommandError) else CommandError(type(exc).__name__, str(exc))
            self.event("command_blocked", **error.as_dict())
            return False
        finally:
            events, self.command_events = self.command_events, None
            try:
                self.timing.observe({"request_id": f"{first['run_id']}:{first['sequence']}", "actor": "cli",
                                     "command": line.rstrip("\r\n"), "started": started, "finished": time.time(),
                                     "started_monotonic": tick, "finished_monotonic": time.monotonic(),
                                     "state": "response_received", "events": events})
                self.timing.export(self.evidence.path)
            except Exception as error:
                # Never change a game receipt, discard pending, or append an
                # unframed stdout event because optional statistics failed.
                print(f"Timing export failed: {error}", file=sys.stderr)

    async def command(self, line: str) -> bool:
        parts = line.strip().split(maxsplit=2)
        if not parts:
            return False
        head = parts[0]
        if head in GROUPS:
            try:
                intent = parse_command(line)
            except HelpRequested as help_request:
                self.event("help", topic=head, **help_request.document)
            else:
                await self.call(intent.tool, intent.arguments, dry_run=intent.dry_run)
        elif head == "verify" and parts == ["verify", "state"]:
            await self.verify_state()
        elif head in HELP and parts == [head, "--help"]:
            self.event("help", topic=head, **HELP[head])
        elif head in {"help", "--help"} and len(parts) <= 2:
            topic = parts[1] if len(parts) == 2 else "overview"
            if topic in GROUPS:
                self.event("help", topic=topic, **domain_parser(topic).help_document())
                return False
            if topic not in HELP:
                raise CommandError("INVALID_DEBUG_COMMAND", f"Unknown help topic: {topic}")
            self.event("help", topic=topic, **HELP[topic])
        elif head == "tools" and len(parts) <= 2:
            if len(parts) == 1:
                self.event("tools", tools=sorted(self.tools))
            elif parts[1] in self.tools:
                self.event("tool_schema", tool=self.tools[parts[1]].model_dump(mode="json"))
            else:
                raise CommandError("UNKNOWN_TOOL", f"Unknown tool: {parts[1]}")
        elif head == "pending" and len(parts) == 1:
            self.event("pending", runtime=await self.runtime(), transport_unknown=self.transport_unknown)
        elif head == "state" and len(parts) == 2 and parts[1] in STATES:
            await self.call(STATES[parts[1]], {})
        elif head == "call" and len(parts) >= 2:
            tail = line.strip()[len(head):].strip()
            dry_run = tail.split(maxsplit=1)[0] == "--dry-run"
            if dry_run:
                tail = tail[len("--dry-run"):].strip()
            call_parts = tail.split(maxsplit=1)
            if not call_parts:
                raise CommandError("INVALID_DEBUG_COMMAND", "Missing TOOL after call --dry-run")
            await self.call(call_parts[0], strict_json(call_parts[1]) if len(call_parts) == 2 else {}, dry_run=dry_run)
        elif head == "quit":
            options = line.strip().split()[1:]
            if len(options) != len(set(options)) or set(options) - {"--abandon", "--dry-run"}:
                raise CommandError("INVALID_DEBUG_COMMAND", "quit accepts --abandon and --dry-run at most once")
            return await self.close_request("--abandon" in options, dry_run="--dry-run" in options)
        else:
            raise CommandError("INVALID_DEBUG_COMMAND", "Use help for foreground session commands")
        return False


async def serve(evidence: Evidence, state: dict, *, offline: bool, timeout: float,
                source: TextIO | None, emit: Callable[[dict], None], server=None, driver=None,
                developer: bool = False, shared_pending_path: Path | None = None,
                interrupted: threading.Event | None = None) -> str:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    state.update(tool_calls=0, mcp_session_closed=False, offline=offline, developer=developer)
    params = server or StdioServerParameters(command=sys.executable,
        args=["-X", "utf8", "-B", "-m", "civ_mcp"], cwd=str(evidence.project),
        env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                 CIV6_KNOWLEDGE_DIR=str(evidence.project / "civ6_knowledge")))
    evidence.json("debug-process", {"command": [params.command, *params.args], "cwd": str(params.cwd),
                                   "rpc_timeout_seconds": timeout, "offline": offline})
    opened = False
    try:
        with (evidence.path / "debug-stderr.log").open("w", encoding="utf-8") as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                opened = True
                closed = asyncio.Event()
                async with ClientSession(ObservedRead(read, closed), write, read_timeout_seconds=timedelta(seconds=timeout)) as client:
                    initialized = await client.initialize()
                    evidence.json("debug-initialize", initialized.model_dump(mode="json"))
                    debug = DebugSession(client, evidence, state, emit, offline,
                                         developer=developer, shared_pending_path=shared_pending_path)
                    await debug.start()
                    if driver is not None:
                        status = await driver(debug)
                        await debug.close_request(False, eof=True)
                        return "BLOCKED" if state["unresolved_on_exit"] else status
                    while True:
                        if source.isatty():
                            print("civ6> ", file=sys.stderr, end="", flush=True)
                        line = await next_line(source, closed, interrupted)
                        if not line:
                            if await debug.close_request(False, eof=not source.isatty()):
                                break
                            continue
                        if await debug.execute_command(line):
                            break
    finally:
        state["mcp_session_closed"] = opened
        state["context_lost"] = state.get("unresolved_on_exit", True)
    return "BLOCKED" if state["unresolved_on_exit"] else "PASS"


def hotseat_server(project: Path, seat: int, run: Path):
    """Reuse the existing experiment; never replace it with single-player end_turn."""
    from mcp import StdioServerParameters

    run = run.expanduser().resolve()
    if not (run / "evidence/patch-manifest.json").is_file():
        raise CommandError("HOTSEAT_MANIFEST_REQUIRED", "Hotseat requires its existing verified experimental run",
                           hint="Choose the authorized two-human-seat run containing evidence/patch-manifest.json; this command does not create or apply patches.")
    pending = run / "evidence/shared-pending.json"
    if pending.exists():
        raise CommandError("HOTSEAT_PENDING_UNKNOWN", "An existing hotseat request still owns this run",
                           hint="Retain its original CLI/controller and confirm the original request; a new process cannot take ownership.")
    return StdioServerParameters(command=sys.executable,
        args=["-X", "utf8", "-B", str(project / "scripts/hotseat_agent.py"), "--seat", str(seat), "--run", str(run)],
        cwd=str(project), env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                                  CIV6_KNOWLEDGE_DIR=str(project / "civ6_knowledge"))), pending


def run_debug(evidence: Evidence, state: dict, *, offline: bool, timeout: float,
              developer: bool = False, hotseat_seat: int | None = None, hotseat_run: Path | None = None) -> str:
    check_interpreter(evidence.project)
    server, shared_pending = None, None
    if (hotseat_seat is None) != (hotseat_run is None):
        raise CommandError("INVALID_ARGUMENT", "--hotseat-seat and --hotseat-run must be supplied together")
    if hotseat_seat is not None:
        if offline or developer:
            raise CommandError("INVALID_ARGUMENT", "Hotseat cannot be combined with --offline or --developer")
        server, shared_pending = hotseat_server(evidence.project, hotseat_seat, hotseat_run)
    evidence.summary.update(mode="offline" if offline else "debug", game_verification="not_run" if offline else "unverified",
                            event="session_closed")
    evidence.save()

    def emit(value):
        print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)

    with keep_session_on_interrupt() as interrupted:
        options = {"developer": developer, "shared_pending_path": shared_pending, "interrupted": interrupted}
        if server is not None:
            options["server"] = server
        return run_session(serve(evidence, state, offline=offline, timeout=timeout, source=sys.stdin, emit=emit,
                                 **options))


def run_session(operation):
    """Keep actionable protocol errors when the SDK wraps task groups."""
    try:
        return asyncio.run(operation)
    except ExceptionGroup as group:
        def leaf(error):
            if isinstance(error, CommandError):
                return error
            if isinstance(error, BaseExceptionGroup):
                for child in error.exceptions:
                    found = leaf(child)
                    if found:
                        return found
            return None
        error = leaf(group)
        if error:
            raise error from group
        raise

"""One foreground client and one owned MCP process for a complete debug session."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import timedelta
import json
import os
import signal
import sys
import threading
from typing import Callable, TextIO

from civ_mcp.devtools.evidence import CommandError, Evidence, now
from civ_mcp.devtools.knowledge import check_interpreter


OFFLINE_TOOLS = {"get_runtime_status", "search_knowledge", "get_knowledge"}
STATES = {"overview": "get_game_overview", "units": "get_units", "cities": "get_cities",
          "turn": "get_turn_status"}
HELP = {"commands": ["help", "tools [NAME]", "pending", "state overview|units|cities|turn",
                     "call TOOL [JSON_OBJECT]", "quit", "quit --abandon"]}


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


async def next_line(source: TextIO, closed: asyncio.Event) -> str:
    reading = asyncio.create_task(readline(source))
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


async def readline(source: TextIO) -> str:
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
            value, error = source.readline(), None
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

    try:
        result = json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(result, dict):
            raise ValueError("Arguments must be a JSON object")
        return result
    except ValueError as exc:
        raise CommandError("INVALID_JSON", str(exc)) from exc


@contextmanager
def keep_session_on_interrupt():
    previous = signal.getsignal(signal.SIGINT)

    def interrupt(signum, frame):
        # The SDK isolates the child process group on POSIX. Do not cancel an
        # in-flight MCP call or replace its process when the terminal sends ^C.
        print("\nSession retained; the current request continues. Use pending or quit when it returns.",
              file=sys.stderr, flush=True)

    signal.signal(signal.SIGINT, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


class DebugSession:
    def __init__(self, client, evidence: Evidence, state: dict, emit: Callable[[dict], None], offline: bool):
        self.client, self.evidence, self.state, self.emit = client, evidence, state, emit
        self.offline = offline
        self.sequence = 0
        self.tools = {}
        self.identity = None
        self.transport_unknown = None
        self.identity_lost = False

    def event(self, event: str, **data) -> dict:
        self.sequence += 1
        value = {"event": event, "sequence": self.sequence, "run_id": self.evidence.summary["run_id"],
                 "timestamp": now(), **data}
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
        self.state["runtime"] = value
        self.evidence.json("runtime-status", value)
        return value

    async def start(self):
        tools = await self.client.list_tools()
        self.evidence.json("debug-tools", tools.model_dump(mode="json"))
        self.tools = {tool.name: tool for tool in tools.tools}
        if "get_runtime_status" not in self.tools:
            raise CommandError("RUNTIME_STATUS_MISSING", "Reload the current server code before debugging")
        value = await self.runtime()
        self.event("session_ready", runtime=value, offline=self.offline, **HELP)

    async def call(self, tool: str, arguments: dict):
        if tool not in self.tools:
            raise CommandError("UNKNOWN_TOOL", f"Unknown tool: {tool}; use tools")
        if self.offline and tool not in OFFLINE_TOOLS:
            raise CommandError("OFFLINE_ONLY", f"{tool} is unavailable in --offline sessions")
        readonly = self.tools[tool].annotations and self.tools[tool].annotations.readOnlyHint is True
        if (self.transport_unknown or self.identity_lost) and not readonly:
            raise CommandError("TRANSPORT_UNRESOLVED", "An RPC outcome/identity is unknown; only read-only inspection is allowed")
        await self.runtime()
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
            self.event("tool_outcome_unknown", request=request["sequence"], **unknown)
            return
        self.evidence.json(f"debug-response-{request['sequence']:06}", result.model_dump(mode="json"))
        self.event("tool_result", request=request["sequence"], tool=tool,
                   is_error=bool(result.isError), result=result.model_dump(mode="json"))

    async def close_request(self, abandon: bool, eof: bool = False) -> bool:
        try:
            runtime = await self.runtime()
            unresolved = runtime["has_pending"] or bool(self.transport_unknown)
        except Exception as exc:
            unresolved = True
            self.state["close_status_error"] = f"{type(exc).__name__}: {exc}"
        if unresolved and not abandon and not eof:
            self.event("command_blocked", code="PENDING_ON_EXIT", message="Session retained; inspect pending or explicitly quit --abandon")
            return False
        self.state.update(unresolved_on_exit=unresolved,
                          exit_reason="eof_unresolved" if eof and unresolved else "abandoned" if abandon and unresolved else "eof" if eof else "quit")
        self.event("session_closing", unresolved=unresolved, reason=self.state["exit_reason"],
                   transport_unknown=self.transport_unknown)
        return True

    async def command(self, line: str) -> bool:
        parts = line.strip().split(maxsplit=2)
        if not parts:
            return False
        head = parts[0]
        if head == "help" and len(parts) == 1:
            self.event("help", **HELP)
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
            await self.call(parts[1], strict_json(parts[2]) if len(parts) == 3 else {})
        elif head == "quit" and (len(parts) == 1 or parts[1:] == ["--abandon"]):
            return await self.close_request(len(parts) == 2)
        else:
            raise CommandError("INVALID_DEBUG_COMMAND", "Use help for foreground session commands")
        return False


async def serve(evidence: Evidence, state: dict, *, offline: bool, timeout: float,
                source: TextIO, emit: Callable[[dict], None], server=None) -> str:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    state.update(tool_calls=0, mcp_session_closed=False, offline=offline)
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
                    debug = DebugSession(client, evidence, state, emit, offline)
                    await debug.start()
                    while True:
                        if source.isatty():
                            print("civ6> ", file=sys.stderr, end="", flush=True)
                        line = await next_line(source, closed)
                        if not line:
                            if await debug.close_request(False, eof=not source.isatty()):
                                break
                            continue
                        debug.event("command", input=line.rstrip("\r\n"))
                        try:
                            if await debug.command(line):
                                break
                        except Exception as exc:
                            debug.event("command_blocked", code=exc.code if isinstance(exc, CommandError) else type(exc).__name__,
                                        message=str(exc))
    finally:
        state["mcp_session_closed"] = opened
        state["context_lost"] = state.get("unresolved_on_exit", True)
    return "BLOCKED" if state["unresolved_on_exit"] else "PASS"


def run_debug(evidence: Evidence, state: dict, *, offline: bool, timeout: float) -> str:
    check_interpreter(evidence.project)
    evidence.summary.update(mode="offline" if offline else "debug", game_verification="not_run" if offline else "unverified",
                            event="session_closed")
    evidence.save()

    def emit(value):
        print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)

    with keep_session_on_interrupt():
        try:
            return asyncio.run(serve(evidence, state, offline=offline, timeout=timeout, source=sys.stdin, emit=emit))
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

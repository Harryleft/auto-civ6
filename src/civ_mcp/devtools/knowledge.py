"""Exercise the real offline CLI and stdio MCP without game-tool calls."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tempfile

from civ_mcp.devtools.evidence import CommandError, Evidence


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CommandError("VERIFICATION_FAILED", message, "FAIL")


def knowledge_root(evidence: Evidence, configured: str | None) -> Path:
    root = Path(configured).expanduser().resolve() if configured else evidence.project / "civ6_knowledge"
    if not (root / "query.py").is_file() or not (root / "manifest.json").is_file():
        raise CommandError("KNOWLEDGE_MISSING", "Select a complete knowledge directory with --knowledge-dir PATH")
    if evidence.path.is_relative_to(root):
        raise CommandError("INVALID_OUTPUT", "Evidence must be outside the knowledge directory")
    return root


def query(evidence: Evidence, root: Path, name: str, *arguments: str) -> dict:
    result = evidence.run(name, [sys.executable, "-X", "utf8", "-B", str(root / "query.py"),
                                 "--root", str(root), *arguments])
    require(result.returncode == 0, f"{name}: CLI exit {result.returncode}; see {name}.json")
    data = json.loads(result.stdout)
    require(isinstance(data, dict), f"{name}: expected JSON object")
    return data


def validate(evidence: Evidence, root: Path) -> dict:
    data = query(evidence, root, "cli-doctor", "validate")
    require(data.get("status") == "valid", "Knowledge distribution is not valid")
    return data


@asynccontextmanager
async def session(evidence: Evidence, root: Path, state: dict, cwd: Path | None = None):
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as exc:
        raise CommandError("DEPENDENCY_MISSING", "Install project dependencies with uv sync --locked") from exc
    # The lifespan creates an unconnected GameConnection. Initialization and
    # tool discovery do not access the game; verify() has a fixed offline call set.
    params = StdioServerParameters(command=sys.executable,
        args=["-X", "utf8", "-B", "-m", "civ_mcp"], cwd=str(cwd or evidence.project),
        env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", CIV6_KNOWLEDGE_DIR=str(root)))
    evidence.json("mcp-process", {"command": [params.command, *params.args], "cwd": params.cwd,
                                "environment_overrides": {key: params.env[key] for key in
                                    ("PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "CIV6_KNOWLEDGE_DIR")},
                                "timeout_seconds": 90})
    with (evidence.path / "mcp-stderr.log").open("w", encoding="utf-8") as errors:
        async with asyncio.timeout(90):
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as client:
                    initialized = await client.initialize()
                    evidence.json("mcp-initialize", initialized.model_dump(mode="json"))
                    require(initialized.serverInfo.name == "Civilization VI", "Unexpected MCP server")
                    tools = await client.list_tools()
                    evidence.json("mcp-tools", tools.model_dump(mode="json"))
                    require({"search_knowledge", "get_knowledge"} <= {tool.name for tool in tools.tools},
                            "Knowledge tools missing")
                    state["mcp_ready"] = True
                    try:
                        yield client
                    finally:
                        state["mcp_scope_finished"] = True
    state["mcp_session_closed"] = True


def check_interpreter(project: Path) -> None:
    import civ_mcp
    if not Path(civ_mcp.__file__).resolve().is_relative_to(project / "src/civ_mcp"):
        raise CommandError("CHECKOUT_MISMATCH", "Interpreter loads another checkout; run uv sync --locked in --project")


def doctor(evidence: Evidence, root: Path, state: dict) -> None:
    check_interpreter(evidence.project)
    state["checks"] = []
    state["checks"].append({"name": "python", "status": "PASS" if sys.version_info >= (3, 12) else "BLOCKED"})
    import civ_mcp
    state["package"] = {"path": str(Path(__file__).resolve().parent.parent), "version": civ_mcp.__version__}
    runtime = ["mcp"]
    if sys.platform == "win32":
        runtime.append("pywin32")
    for name in [*runtime, "pytest", "lupa"]:
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = None
        state["checks"].append({"name": name, "version": version,
                                "status": "PASS" if version else "BLOCKED" if name in runtime else "NOT_RUN"})
    if any(check["status"] == "BLOCKED" for check in state["checks"]):
        raise CommandError("DEPENDENCY_MISSING", "Install project dependencies with uv sync --locked")
    validated = validate(evidence, root)
    state["knowledge"] = {"status": validated["status"], "counts": validated["counts"]}

    async def probe():
        async with session(evidence, root, state):
            pass

    asyncio.run(probe())


def fingerprints(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def verify(evidence: Evidence, root: Path, state: dict) -> None:
    check_interpreter(evidence.project)
    state.update(feature_ids=["knowledge.cli", "knowledge.mcp"], tool_calls=[], mcp_session_closed=False)
    before = fingerprints(root)
    state["knowledge_manifest_sha256"] = before["manifest.json"]
    state["knowledge_snapshot_sha256"] = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["snapshot_sha256"]
    work = None
    try:
        with tempfile.TemporaryDirectory(prefix="civ6-verify-") as temporary:
            work = Path(temporary)
            validate(evidence, root)
            reference = {"search": query(evidence, root, "cli-search", "search", "写作",
                                         "--topic", "technologies", "--limit", "5")}
            require(any(row["id"] == "TECH_WRITING" for row in reference["search"]["results"]),
                    "Search did not return TECH_WRITING")
            for level in ("summary", "brief", "rule", "evidence"):
                reference[level] = query(evidence, root, f"cli-{level}", "show", "TECH_WRITING", "--level", level)
                require(reference[level].get("id") == "TECH_WRITING", f"CLI {level}: wrong entry")
            require(bool(reference["rule"].get("source_refs")), "Rule has no source references")

            async def drive():
                async with session(evidence, root, state, cwd=work) as client:
                    async def call(tool: str, arguments: dict, label: str) -> dict:
                        require(tool in {"search_knowledge", "get_knowledge"}, "Non-offline tool refused")
                        state["tool_calls"].append({"tool": tool, "arguments": arguments})
                        evidence.json("mcp-attempts", state["tool_calls"])
                        result = await client.call_tool(tool, arguments)
                        evidence.json(label, result.model_dump(mode="json"))
                        require(not result.isError, f"{tool}: MCP error")
                        data = json.loads("\n".join(item.text for item in result.content if item.type == "text"))
                        require(data.get("status") == "found", f"{tool}: not found")
                        require(data.get("applicability", {}).get("current_session_verified") is False,
                                "Static knowledge presented as a verified game session")
                        return data

                    found = await call("search_knowledge", {"query": "写作", "topic": "technologies", "limit": 5}, "mcp-search")
                    require(found["results"] == reference["search"]["results"], "MCP/CLI search differs")
                    brief = await call("search_knowledge", {"query": "写作", "topic": "technologies",
                        "limit": 5, "view": "brief"}, "mcp-search-brief")
                    require({k: v for k, v in brief.items() if k not in {"profile", "profile_reference"}} ==
                            {k: v for k, v in found.items() if k != "profile"}, "Brief search changed results or coverage")
                    profile_hash = hashlib.sha256(json.dumps(found["profile"], ensure_ascii=False,
                        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
                    require(brief["profile_reference"]["sha256"] == profile_hash, "Brief profile fingerprint differs")
                    expansion = brief["profile_reference"]["expand"]
                    require(expansion["tool"] == "search_knowledge", "Brief expansion uses another tool")
                    expanded = await call(expansion["tool"], expansion["arguments"], "mcp-search-expanded")
                    require(expanded == found, "Brief evidence expansion differs")
                    for level in ("summary", "brief", "rule", "evidence"):
                        data = await call("get_knowledge", {"entry_id": "TECH_WRITING", "level": level}, f"mcp-{level}")
                        for key, value in reference[level].items():
                            require(data.get(key) == value, f"MCP/CLI {level} differs at {key}")
            asyncio.run(drive())
        state["knowledge_unchanged"] = fingerprints(root) == before
        require(state["knowledge_unchanged"], "Knowledge files changed during verification")
    finally:
        state["scratch_removed"] = work is not None and not work.exists()

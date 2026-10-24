#!/usr/bin/env python3
"""Verify offline knowledge through the real CLI and stdio MCP; retain receipts."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


REPO = Path(__file__).resolve().parents[4]
KNOWLEDGE = REPO / "civ6_knowledge"
ALLOWED_TOOLS = {"search_knowledge", "get_knowledge"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fingerprints() -> dict[str, str]:
    return {
        str(path.relative_to(KNOWLEDGE)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(KNOWLEDGE.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def cli(output: Path, work: Path, name: str, *arguments: str) -> dict:
    command = [sys.executable, "-X", "utf8", "-B", str(KNOWLEDGE / "query.py"),
               "--root", str(KNOWLEDGE), *arguments]
    try:
        result = subprocess.run(command, cwd=work, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
    except subprocess.TimeoutExpired as exc:
        write_json(output / f"{name}.json", {
            "command": command, "timeout_seconds": 60,
            "stdout": str(exc.stdout or ""), "stderr": str(exc.stderr or ""),
        })
        raise RuntimeError(f"{name}: CLI timed out") from exc
    write_json(output / f"{name}.json", {
        "command": command, "exit_code": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    })
    require(result.returncode == 0, f"{name}: CLI exit {result.returncode}")
    data = json.loads(result.stdout)
    require(isinstance(data, dict), f"{name}: expected JSON object")
    return data


async def drive_mcp(output: Path, work: Path, reference: dict, receipt: dict) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-X", "utf8", "-B", "-m", "civ_mcp"], cwd=str(work),
        env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                 CIV6_KNOWLEDGE_DIR=str(KNOWLEDGE)),
    )
    with (output / "mcp-stderr.log").open("w", encoding="utf-8") as error_log:
        async with asyncio.timeout(90):
            async with stdio_client(params, errlog=error_log) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                    initialized = await session.initialize()
                    write_json(output / "mcp-initialize.json", initialized.model_dump(mode="json"))
                    require(initialized.serverInfo.name == "Civilization VI", "Unexpected MCP server")
                    tools = await session.list_tools()
                    write_json(output / "mcp-tools.json", tools.model_dump(mode="json"))
                    require(ALLOWED_TOOLS <= {tool.name for tool in tools.tools}, "Knowledge tools missing")
                    receipt["mcp_ready"] = True

                    async def call(name: str, arguments: dict, label: str) -> dict:
                        require(name in ALLOWED_TOOLS, f"Offline tool forbidden: {name}")
                        attempt = {"tool": name, "arguments": arguments}
                        receipt["tool_calls"].append(attempt)
                        write_json(output / "mcp-attempts.json", receipt["tool_calls"])
                        result = await session.call_tool(name, arguments)
                        write_json(output / f"{label}.json", result.model_dump(mode="json"))
                        require(not result.isError, f"{name}: MCP error")
                        data = json.loads("\n".join(item.text for item in result.content if item.type == "text"))
                        require(data.get("status") == "found", f"{name}: result not found")
                        require(data.get("applicability", {}).get("current_session_verified") is False,
                                f"{name}: static knowledge presented as a verified game session")
                        return data

                    search = await call("search_knowledge", {
                        "query": "写作", "topic": "technologies", "limit": 5,
                    }, "mcp-search")
                    require(search["results"] == reference["search"]["results"], "MCP/CLI search differs")
                    for level in ("summary", "rule", "evidence"):
                        data = await call("get_knowledge", {"entry_id": "TECH_WRITING", "level": level},
                                          f"mcp-{level}")
                        require(data.get("id") == "TECH_WRITING", f"MCP {level}: wrong entry")
                        for key, value in reference[level].items():
                            require(data.get(key) == value, f"MCP/CLI {level} differs at {key}")
    receipt["mcp_session_closed"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New evidence directory; existing paths are refused")
    args = parser.parse_args()
    output = (args.output or REPO / ".scratch" / "verify-civ6" / uuid4().hex).resolve()
    if output.is_relative_to(KNOWLEDGE):
        parser.error("Evidence must be outside civ6_knowledge")
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("Evidence directory already exists; choose a new path")
    receipt = {
        "status": "FAIL", "scope": "offline knowledge CLI and real stdio MCP",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "feature_ids": ["knowledge.cli", "knowledge.mcp"], "tool_calls": [],
        "game_verification": "not_run", "mcp_ready": False, "mcp_session_closed": False,
    }
    before = None
    work = None
    try:
        spec = importlib.util.find_spec("civ_mcp")
        require(spec is not None and spec.origin is not None
                and Path(spec.origin).resolve().is_relative_to(REPO / "src" / "civ_mcp"),
                "Interpreter must load civ_mcp from this checkout; run uv sync --locked")
        receipt["checkout_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
        receipt["checkout_status"] = subprocess.check_output(
            ["git", "status", "--short"], cwd=REPO, text=True)
        receipt["helper_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        receipt["runtime_source_sha256"] = {
            str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((REPO / "src" / "civ_mcp").rglob("*.py"))
        }
        receipt["knowledge_manifest_sha256"] = hashlib.sha256(
            (KNOWLEDGE / "manifest.json").read_bytes()).hexdigest()
        receipt["knowledge_snapshot_sha256"] = json.loads(
            (KNOWLEDGE / "manifest.json").read_text(encoding="utf-8"))["snapshot_sha256"]
        before = fingerprints()
        with tempfile.TemporaryDirectory(prefix="civ6-verify-") as temporary:
            work = Path(temporary)
            doctor = cli(output, work, "cli-doctor", "validate")
            require(doctor.get("status") == "valid", "Knowledge distribution is not valid")
            receipt["cli_ready"] = True
            reference = {"search": cli(output, work, "cli-search", "search", "写作",
                                       "--topic", "technologies", "--limit", "5")}
            require(any(row["id"] == "TECH_WRITING" for row in reference["search"]["results"]),
                    "Writing search did not return TECH_WRITING")
            for level in ("summary", "rule", "evidence"):
                reference[level] = cli(output, work, f"cli-{level}", "show", "TECH_WRITING", "--level", level)
                require(reference[level].get("id") == "TECH_WRITING", f"CLI {level}: wrong entry")
            require(bool(reference["rule"].get("source_refs")), "Writing rule has no source references")
            asyncio.run(drive_mcp(output, work, reference, receipt))
        require(fingerprints() == before, "Knowledge files changed during verification")
        receipt["knowledge_unchanged"] = True
        receipt["status"] = "PASS"
    except (Exception, KeyboardInterrupt) as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        receipt["scratch_removed"] = work is not None and not work.exists()
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "summary.json", receipt)
    print(json.dumps({"status": receipt["status"], "evidence": str(output),
                      "game_verification": "not_run", "error": receipt.get("error")}, ensure_ascii=False))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

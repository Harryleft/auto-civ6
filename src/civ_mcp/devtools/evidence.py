"""One result envelope and retained subprocess receipts for offline commands."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


ERROR_HINTS = {
    "INVALID_ARGUMENT": "Read the selected command's --help, then correct the named argument or choose its subcommand.",
    "PROJECT_NOT_FOUND": "Set --project to a source checkout containing pyproject.toml and src/civ_mcp/server.py.",
    "INVALID_OUTPUT": "Change --output to a new directory outside the knowledge package.",
    "EVIDENCE_UNAVAILABLE": "Use --output with a new, writable directory; existing evidence is never overwritten.",
    "INVALID_PYTEST_ARGUMENT": "Use civ6 test --help for supported options after --; remove extra paths, plugins and config overrides.",
    "PYTEST_NOT_RUN": "Read pytest.json; fix test selection (-k/-m) or invocation before running again.",
    "INVALID_TIME": "Set --since/--until to an ISO-8601 timestamp with timezone, for example 2026-10-24T00:00:00+08:00.",
    "INVALID_TIME_RANGE": "Move --since before or equal to --until.",
    "LOG_SESSION_INVALID": "Use a session from civ6 logs list, or supply --file PATH to an existing JSONL file.",
    "LOG_NOT_FOUND": "Check --dir and the session from civ6 logs list, or correct --file PATH.",
    "LOG_DIRECTORY_INVALID": "Set --dir to a directory containing action JSONL files.",
    "INVALID_JSON": "Supply one strict JSON object with double-quoted keys; remove duplicate keys and non-finite numbers.",
    "UNKNOWN_TOOL": "Use tools to discover names, then tools NAME to inspect its input schema.",
    "OFFLINE_ONLY": "Keep --offline for knowledge/pending inspection; use call --dry-run TOOL JSON to preview other tools without sending them.",
    "INVALID_DEBUG_COMMAND": "Use help for topics, then help call, help state or help quit for syntax.",
    "PENDING_ON_EXIT": "Use pending and inspect the original request; quit --abandon explicitly discards context and returns BLOCKED.",
    "TRANSPORT_UNRESOLVED": "Use pending and read-only inspection in this session. Do not retry mutations or replace the MCP process.",
    "SESSION_CHANGED": "Stop game commands and inspect retained evidence; a new process cannot confirm the original pending request.",
    "RUNTIME_STATUS_INVALID": "Check debug-tools.json and the server version; retain this session and do not send mutations.",
    "RUNTIME_STATUS_MISSING": "Run uv sync --locked in this checkout and start debugging with its current server code.",
    "MCP_CLOSED": "Inspect debug-stderr.log and retained pending evidence; do not restart and replay an unknown request.",
    "TIMEOUT": "Inspect the timed-out process receipt before retrying; adjust --timeout only after checking its partial result.",
    "DEPENDENCY_MISSING": "Run uv sync --locked in the selected project and use uv run --locked civ6.",
    "CHECKOUT_MISMATCH": "Run uv sync --locked in --project and invoke its civ6 with uv run --locked.",
    "KNOWLEDGE_MISSING": "Set --knowledge-dir to a complete knowledge package containing manifest.json and query.py.",
    "VERIFICATION_FAILED": "Inspect the failed check and its CLI/MCP receipts in evidence; fix that source or knowledge package before verifying again.",
    "TEST_FILE_MISSING": "Restore the missing test file or update DOMAINS in src/civ_mcp/devtools/testing.py.",
    "PROCESS_UNAVAILABLE": "Inspect the recorded command and OS error; correct the executable path or permissions.",
    "INTERRUPTED": "Inspect retained evidence and pending state before deciding whether the command can be run again.",
}


class CommandError(Exception):
    def __init__(self, code: str, message: str, status: str = "BLOCKED", *, hint: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.code, self.status = code, status
        self.hint = hint or ERROR_HINTS.get(code, "Inspect the recorded error and evidence; correct the reported cause before retrying.")
        self.details = details or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "hint": self.hint, "details": self.details}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def find_project(explicit: str | None) -> Path:
    def is_project(path: Path) -> bool:
        return (path / "pyproject.toml").is_file() and (path / "src/civ_mcp/server.py").is_file()

    if explicit:
        root = Path(explicit).expanduser().resolve()
        if is_project(root):
            return root
    else:
        cwd = Path.cwd().resolve()
        for root in (cwd, *cwd.parents, Path(__file__).resolve().parents[3]):
            if is_project(root):
                return root
    raise CommandError("PROJECT_NOT_FOUND", "Select a civ6 source checkout with --project PATH")


class Evidence:
    def __init__(self, project: Path, command: list[str], output: str | None = None):
        self.project = project
        self.summary = {
            "schema_version": 1, "run_id": uuid4().hex, "command": command,
            "mode": "offline", "game_verification": "not_run", "status": "RUNNING",
            "exit_code": None, "started_at": now(), "project": str(project),
            "data": {}, "errors": [],
        }
        self.path = (Path(output).expanduser() if output else
                     project / ".scratch/civ6" / self.summary["run_id"]).resolve()
        if self.path.is_relative_to(project / "civ6_knowledge"):
            raise CommandError("INVALID_OUTPUT", "Evidence must be outside civ6_knowledge")
        try:
            self.path.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise CommandError("EVIDENCE_UNAVAILABLE", f"Cannot create new evidence directory: {exc}") from exc
        self.summary["evidence"] = str(self.path)
        context = {"python": {"executable": sys.executable, "version": sys.version}}
        context["source_sha256"] = {
            str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((project / "src/civ_mcp").rglob("*.py"))
        }
        provenance = {}
        for label, args in (("commit", ["rev-parse", "HEAD"]), ("status", ["status", "--short"])):
            try:
                result = subprocess.run(["git", *args], cwd=project, stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True, encoding="utf-8", timeout=5)
                provenance[label] = result.stdout.strip() if result.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                provenance[label] = None
        context["git"] = provenance
        self.json("context", context)
        self.summary["context"] = str(self.path / "context.json")
        self.summary["git"] = {"commit": provenance["commit"],
                               "dirty": bool(provenance["status"]) if provenance["status"] is not None else None}
        self.save()

    def save(self) -> None:
        write_json(self.path / "summary.json", self.summary)

    def json(self, name: str, value: object) -> None:
        write_json(self.path / f"{name}.json", value)

    def run(self, name: str, command: list[str], *, cwd: Path | None = None,
            timeout: float = 60, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        receipt = {"command": command, "cwd": str(cwd or self.project),
                   "started_at": now(), "timeout_seconds": timeout}
        self.json(name, receipt)
        try:
            result = subprocess.run(command, cwd=cwd or self.project, env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=timeout)
            receipt.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
            return result
        except subprocess.TimeoutExpired as exc:
            def decode(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            receipt.update(timeout_seconds=timeout, stdout=decode(exc.stdout), stderr=decode(exc.stderr))
            raise CommandError("TIMEOUT", f"{name} exceeded {timeout:g}s", "FAIL") from exc
        except KeyboardInterrupt:
            receipt["interrupted"] = True
            raise
        except OSError as exc:
            receipt["error"] = str(exc)
            raise CommandError("PROCESS_UNAVAILABLE", f"{name}: {exc}") from exc
        finally:
            receipt["finished_at"] = now()
            self.json(name, receipt)

    def finish(self, status: str, data: dict, error: CommandError | None = None) -> dict:
        self.summary.update(status=status, exit_code={"PASS": 0, "FAIL": 1, "BLOCKED": 2}[status],
                            data=data, finished_at=now())
        if error:
            self.summary["errors"].append(error.as_dict())
        self.save()
        return self.summary

"""One result envelope and retained subprocess receipts for offline commands."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


class CommandError(Exception):
    def __init__(self, code: str, message: str, status: str = "BLOCKED"):
        super().__init__(message)
        self.code, self.status = code, status


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
            self.summary["errors"].append({"code": error.code, "message": str(error)})
        self.save()
        return self.summary

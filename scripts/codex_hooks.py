"""Small, offline Codex hooks. Never import the runtime or execute game commands."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".lock"}
DIRECTORIES = {"src", "tests", "scripts", "docs", "civ6_knowledge"}


def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, capture_output=True, timeout=10, check=check,
    )


def snapshot() -> dict[str, str]:
    """Hash supported project files, including untracked files and committed edits."""
    names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout
    result = {}
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = os.fsdecode(raw)
        relative = Path(name)
        if relative.suffix not in SUFFIXES:
            continue
        if len(relative.parts) > 1 and relative.parts[0] not in DIRECTORIES:
            if name != ".codex/hooks.json":
                continue
        path = ROOT / relative
        # A tracked symlink must not make a hook read outside the checkout.
        if path.is_symlink():
            result[name] = "symlink:" + os.readlink(path)
        elif path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def baseline() -> dict:
    head = git("rev-parse", "--verify", "HEAD", check=False)
    return {"version": 1, "head": head.stdout.decode().strip() if head.returncode == 0 else None,
            "files": snapshot()}


def suggestions(names: list[str]) -> list[str]:
    """Routing hints only: these commands are never run by the hook."""
    commands = set()
    for name in names:
        if name.startswith("tests/test_") and name.endswith(".py") and (ROOT / name).is_file():
            commands.add("uv run --locked pytest -q " + name)
        if name in {"pyproject.toml", "uv.lock"}:
            commands.add("uv run --locked civ6 doctor")
        if name.startswith("civ6_knowledge/") or name in {
            "src/civ_mcp/cli.py", "src/civ_mcp/server.py", "src/civ_mcp/devtools/knowledge.py",
        }:
            commands.add("uv run --locked civ6 verify knowledge")
        domain = {
            "actions.py": "actions", "end_turn.py": "turns", "checkpoints.py": "checkpoints",
            "diplomacy.py": "diplomacy", "connection.py": "connection", "tuner_client.py": "connection",
            "cli.py": "debug",
        }.get(Path(name).name)
        if name.startswith("src/civ_mcp/") and domain:
            commands.add("uv run --locked civ6 test " + domain)
    return sorted(commands)


def quality(state: dict, current: dict[str, str]) -> dict:
    names = sorted(name for name in state["files"].keys() | current.keys()
                   if state["files"].get(name) != current.get(name))
    issues = []
    syntax_count = 0
    for name in names:
        path = ROOT / name
        if path.suffix != ".py" or not path.is_file():
            continue
        if path.is_symlink():
            issues.append(f"{name}: symlink, Python syntax check NOT_RUN")
            continue
        syntax_count += 1
        try:
            compile(path.read_bytes(), name, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as error:
            issues.append(f"{name}:{getattr(error, 'lineno', None)}: {error}")

    whitespace_runs = []
    if names:
        arguments = ["diff", "--check"]
        if state["head"]:
            arguments.append(state["head"])
        whitespace_runs.append(git(*arguments, "--", *names, check=False))
        if not state["head"]:
            whitespace_runs.append(git("diff", "--cached", "--check", "--", *names, check=False))
        # git diff doesn't include untracked files. Also cover new files already committed.
        tracked = set()
        if state["head"]:
            tracked = {os.fsdecode(n) for n in git("ls-tree", "-r", "--name-only", "-z", state["head"]).stdout.split(b"\0")}
        for name in names:
            path = ROOT / name
            if name not in tracked and path.is_file() and not path.is_symlink():
                result = git("diff", "--no-index", "--check", "--", os.devnull, name, check=False)
                # --no-index implies --exit-code: 1 with no diagnostics is an ordinary diff.
                if result.returncode != 1 or result.stdout or result.stderr:
                    whitespace_runs.append(result)
        for result in whitespace_runs:
            if result.returncode != 0:
                detail = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
                issues.append(detail or f"git diff --check exited {result.returncode}")
    return {
        "status": "FAIL" if issues else ("PASS" if names else "NOT_RUN"),
        "scope": "changed project files since SessionStart; syntax and diff whitespace only",
        "changed_files": names, "python_files_checked": syntax_count,
        "issues": issues, "suggested_commands": suggestions(names),
        "behavior_tests": "NOT_RUN", "game_verification": "not_run",
    }


def handle(event: dict) -> dict:
    if not isinstance(event, dict):
        raise ValueError("hook input must be a JSON object")
    kind = event.get("hook_event_name")
    if kind not in {"SessionStart", "Stop"}:
        return {}
    if not event.get("session_id"):
        raise ValueError("session_id missing; quality check NOT_RUN")
    cwd = Path(event.get("cwd", str(ROOT))).resolve()
    discovered = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                                capture_output=True, timeout=10, check=True)
    if Path(os.fsdecode(discovered.stdout).strip()).resolve() != ROOT:
        return {}
    key = hashlib.sha256(str(event["session_id"]).encode()).hexdigest()[:24]
    directory = ROOT / ".scratch" / "codex-hooks" / key
    path = directory / "state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if state is not None and (not isinstance(state, dict) or state.get("version") != 1
                              or not isinstance(state.get("files"), dict) or "head" not in state):
        raise ValueError("invalid hook state; preserve it for diagnosis and use a new session")
    if kind == "SessionStart":
        # Resume/compaction must not erase the original baseline or previous check receipt.
        if state is None:
            save(path, baseline())
        branch = git("branch", "--show-current").stdout.decode().strip() or "detached HEAD"
        return {"hookSpecificOutput": {"hookEventName": kind, "additionalContext": (
            f"Civ6 workspace ({branch}). Read AGENTS.md and route by task: development uses "
            "docs/development.md and docs/cli.md; game work uses the referenced playbooks. "
            "Stop hook checks only changed-file syntax/whitespace, not behavior tests. "
            "Choose affected tests and report actual evidence. Game operations require task authorization "
            "and the CLI; preserve pending and model per-turn strategy decisions."
        )}}
    if state is None:
        save(path, baseline())
        return {"systemMessage": "Civ6 hook: SessionStart baseline missing; initialized now. Quality check NOT_RUN."}
    current = snapshot()
    fingerprint = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    if state.get("checked_fingerprint") == fingerprint:
        return {}
    report = quality(state, current)
    save(directory / "report.json", report)
    state["checked_fingerprint"] = fingerprint
    save(path, state)
    if not report["changed_files"]:
        return {}
    message = f"Civ6 hook {report['status']}: {directory / 'report.json'}. Behavior tests and game verification NOT_RUN."
    if report["issues"] and not event.get("stop_hook_active", False):
        return {"decision": "block", "reason": (
            message + "\nFix only issues within the authorized task; preserve existing edits. "
            "If blocked, report the reason and remaining checks.\n" + "\n".join(report["issues"])[:1800]
        )}
    return {"systemMessage": message}


def main() -> None:
    try:
        event = json.load(sys.stdin)
        output = handle(event)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # Hook infrastructure failures must remain visible, without trapping the user in Stop loops.
        output = {"systemMessage": f"Civ6 hook BLOCKED: {error}. Quality check NOT_RUN; inspect hook state/config."}
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()

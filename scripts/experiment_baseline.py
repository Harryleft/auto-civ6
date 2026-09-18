#!/usr/bin/env python3
"""Record one reproducible experiment baseline before any play run.

Why this exists
---------------
The refactor compares a lean play path against the legacy one. A comparison is
only interpretable if both arms ran the same code, the same model, the same
game rules, and the same starting save — and if nobody later has to guess what
those were. This script captures that record up front, from sources that can be
re-read: the Git commit, the composed DSH config, the MCP tool registry, and the
save file's own header.

Design constraints (deliberate)
-------------------------------
* **Offline.** It never connects to FireTuner, never launches Civ VI, and never
  calls a model. Everything here is read from local files or from in-process
  introspection of the MCP server.
* **Never destructive.** It only ever *copies* a save. It does not move, rename,
  truncate, or delete anything under the Civ VI save directories, and it refuses
  to overwrite an existing backup whose hash differs.
* **Separate output tree.** Manifests, logs and save copies live under
  ``experiments/`` (git-ignored), never next to the user's real saves.
* ``deepseek-harness`` is a client label, not a model version, so the model id is
  read from the composed DSH config instead of from ``CIV_MCP_AGENT_MODEL``.

Usage::

    uv run python scripts/experiment_baseline.py --run-id crimson-amber-falcon-47 \
        --save 0_MCP_0141 --backup-save
    uv run python scripts/experiment_baseline.py --list-saves
    uv run python scripts/experiment_baseline.py --run-id RUN --save X --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSH_DIR = Path(
    os.environ.get("DEEPSEEK_HARNESS_DIR", REPO_ROOT.parent / "deepseek-harness")
)
MANIFEST_SCHEMA_VERSION = 1
DEFAULT_EXPERIMENTS_DIR = REPO_ROOT / "experiments"

# macOS ships a git shim that refuses to run until the Xcode licence is
# accepted; the CommandLineTools SDK runs the same binary without it. Try the
# plain command first so other platforms and CI are unaffected.
_GIT_FALLBACK_ENV = {"DEVELOPER_DIR": "/Library/Developer/CommandLineTools"}

_RULESET = re.compile(rb"RULESET_([A-Z0-9_]+)")
_GAMESPEED = re.compile(rb"GAMESPEED_([A-Z0-9_]+)")
_MAPSIZE = re.compile(rb"MAPSIZE_([A-Z0-9_]+)")
_DIFFICULTY = re.compile(rb"DIFFICULTY_([A-Z0-9_]+?)_NAME")
_MOD_LISTS = re.compile(rb"([A-Za-z0-9_]+)_MOD_TITLE\":\[([^\]]*)\]")


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def _run_git(*args: str) -> tuple[int, str]:
    for env in (None, _GIT_FALLBACK_ENV):
        environment = None if env is None else {**os.environ, **env}
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return 0, proc.stdout
        last_error = (proc.stderr or proc.stdout or "").strip()
    return 1, last_error


def git_state() -> dict:
    """Return commit, branch, and whether the worktree has uncommitted changes.

    A run whose worktree was dirty cannot be attributed to a commit, so the
    dirty file list is recorded verbatim rather than reduced to a boolean.
    """

    rc, head = _run_git("rev-parse", "HEAD")
    if rc != 0:
        return {
            "commit": None,
            "branch": None,
            "dirty": None,
            "dirty_files": [],
            "unavailable": head or "git is not usable in this environment",
        }
    _, branch = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    rc_status, status = _run_git("status", "--porcelain")
    dirty = None if rc_status != 0 else bool(status.strip())
    return {
        "commit": head.strip(),
        "branch": branch.strip() or None,
        "dirty": dirty,
        "dirty_files": sorted(
            line[3:].strip() for line in status.splitlines() if line.strip()
        )
        if rc_status == 0
        else [],
        "unavailable": None if rc_status == 0 else "git status failed",
    }


# ---------------------------------------------------------------------------
# Save file (offline header facts)
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_save_facts(path: Path) -> dict:
    """Read ruleset/speed/difficulty/mods straight from the save header.

    These markers are plain strings in the (partly uncompressed) save header —
    the same region ``scripts/parse_save.py`` reads for game speed and map size.
    Reading them here keeps the rules/enabled-content record offline and
    independent of any live session.
    """

    data = path.read_bytes()
    facts: dict = {
        "path": str(path),
        "name": path.stem,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "ruleset": None,
        "game_speed": None,
        "map_size": None,
        "difficulty": None,
        "enabled_mods": [],
    }
    for key, pattern in (
        ("ruleset", _RULESET),
        ("game_speed", _GAMESPEED),
        ("map_size", _MAPSIZE),
        ("difficulty", _DIFFICULTY),
    ):
        match = pattern.search(data)
        if match:
            facts[key] = match.group(1).decode("ascii")
    for group, payload in _MOD_LISTS.findall(data):
        entries = payload.decode("utf-8", "replace").strip()
        if entries:
            facts["enabled_mods"].append(
                f"{group.decode('ascii')}:{entries[:200]}"
            )
    facts["enabled_mods"].sort()
    return facts


def discover_saves() -> list[Path]:
    """List candidate saves without touching them, newest first."""

    try:
        from civ_mcp.game_launcher import SAVE_DIR, SINGLE_SAVE_DIR
    except Exception:
        return []
    found: list[Path] = []
    for directory in {SAVE_DIR, SINGLE_SAVE_DIR}:
        if directory and os.path.isdir(directory):
            found.extend(Path(directory).glob("*.Civ6Save"))
    return sorted(set(found), key=lambda p: p.stat().st_mtime, reverse=True)


def resolve_save(reference: str | None) -> Path | None:
    """Resolve a save name or path without assuming a platform's save layout."""

    if not reference:
        return None
    candidate = Path(reference).expanduser()
    if candidate.is_file():
        return candidate
    for suffix in ("", ".Civ6Save"):
        candidate = Path(f"{reference}{suffix}").expanduser()
        if candidate.is_file():
            return candidate
    for found in discover_saves():
        if found.stem == reference or found.name == reference:
            return found
    return None


def backup_save(source: Path, destination_dir: Path) -> dict:
    """Copy a save into the experiment tree. Never moves, renames, or deletes.

    Re-running with the same save is a no-op, but a name collision with
    *different* bytes is refused instead of overwritten: silently replacing a
    backup would destroy the evidence a previous run was based on.
    """

    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    source_hash = sha256_file(source)
    if destination.exists():
        existing = sha256_file(destination)
        if existing != source_hash:
            return {
                "path": str(destination),
                "copied": False,
                "error": (
                    "refusing to overwrite an existing backup with different "
                    f"bytes ({existing[:12]}… != {source_hash[:12]}…) — the "
                    "original save may have been overwritten in place by the "
                    "game, so keep both and record which one this run used"
                ),
            }
        return {"path": str(destination), "copied": False, "sha256": existing}
    shutil.copy2(source, destination)
    return {"path": str(destination), "copied": True, "sha256": sha256_file(destination)}


# ---------------------------------------------------------------------------
# MCP tool surface
# ---------------------------------------------------------------------------


def tool_surface() -> dict:
    """Record the tools the MCP server really exposes in this process."""

    from civ_mcp.server import mcp

    names = sorted(mcp._tool_manager._tools)
    digest = hashlib.sha256("\n".join(names).encode()).hexdigest()
    return {"count": len(names), "names": names, "surface_sha256": digest}


# ---------------------------------------------------------------------------
# DSH
# ---------------------------------------------------------------------------


def dsh_state(
    *, dsh_dir: Path, play_profile: str, belief_mode: str | None, timeout_ms: int | None
) -> dict:
    """Record the composed DSH config, its model, and its digests.

    ``--dump-config`` prints the post-patch entry list through the same code
    path that boots, so the digests below describe what would actually run
    rather than what the YAML files say in isolation.
    """

    state: dict = {
        "dir": str(dsh_dir),
        "version": None,
        "model": None,
        "provider": None,
        "sampling": {},
        "config_sha256": None,
        "prompt_sha256": None,
        "play_profile": play_profile,
        "belief_mode": belief_mode,
        "tool_call_timeout_ms": timeout_ms,
        "agent_instructions_enabled": None,
        "unavailable": None,
    }
    package_json = dsh_dir / "package.json"
    if package_json.is_file():
        try:
            state["version"] = json.loads(package_json.read_text(encoding="utf-8")).get(
                "version"
            )
        except (OSError, ValueError):
            state["unavailable"] = f"could not read {package_json}"

    dump = _dump_dsh_config(dsh_dir)
    if dump is None:
        if state["unavailable"] is None:
            state["unavailable"] = (
                f"DSH checkout not usable at {dsh_dir}; set DEEPSEEK_HARNESS_DIR"
            )
        return state

    text = dump.decode("utf-8", "replace")
    state["config_sha256"] = hashlib.sha256(dump).hexdigest()
    model_rows = _rows_for(text, "agent-default-model")
    if model_rows:
        state["model"] = model_rows[0].get("model")
        state["provider"] = model_rows[0].get("provider")
        state["sampling"] = {
            key: value
            for key, value in model_rows[0].items()
            if key not in {"model", "provider"}
        }
    personas = _rows_for(text, "system-prompt")
    if personas:
        persona = personas[0].get("persona") or ""
        state["prompt_sha256"] = hashlib.sha256(persona.encode()).hexdigest()
    state["agent_instructions_enabled"] = not _row_is_disabled(
        text, "agent-instructions"
    )
    state["unavailable"] = None
    return state


def _dump_dsh_config(dsh_dir: Path) -> bytes | None:
    """Run ``--dump-config`` for the Civ-only headless profile."""

    node_bin = dsh_dir / "apps" / "cli" / "lib" / "bin.js"
    if not node_bin.is_file():
        return None
    profiles_dir = REPO_ROOT / "integrations" / "deepseek-harness"
    args = [
        "node",
        str(node_bin),
        "--profile",
        "headless",
        "--patch",
        str(profiles_dir / "civ6.cordis.yml"),
        "--patch",
        str(profiles_dir / "civ6-agent.cordis.yml"),
        "--dump-config",
    ]
    environment = {**os.environ, "DSH_HOME": os.environ.get("CIV6_DSH_HOME", str(REPO_ROOT / ".dsh"))}
    try:
        proc = subprocess.run(
            args,
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def _rows_for(text: str, module_id: str) -> list[dict]:
    """Return the ``config`` block of every ``- id: <module>`` row as a dict.

    Block scalars (``persona: |-``) are joined, because the persona digest is
    useless if the value collapses to the literal ``|-``.
    """

    lines = text.splitlines()
    rows: list[dict] = []
    current: dict | None = None
    config_indent: int | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("- id: "):
            if current is not None:
                rows.append(current)
            current = (
                {} if stripped[len("- id: ") :].strip() == module_id else None
            )
            config_indent = None
            index += 1
            continue
        if current is not None:
            if config_indent is not None and indent > config_indent:
                match = re.match(r"([A-Za-z0-9_]+):\s*(.*)$", stripped)
                if match:
                    key, value = match.group(1), match.group(2).strip()
                    if value in {"|", "|-", "|+", ">", ">-", ">+"}:
                        block: list[str] = []
                        cursor = index + 1
                        while cursor < len(lines):
                            inner = lines[cursor]
                            inner_indent = len(inner) - len(inner.lstrip())
                            if inner.strip() and inner_indent <= indent:
                                break
                            block.append(inner.strip())
                            cursor += 1
                        current[key] = "\n".join(block).strip()
                        index = cursor
                        continue
                    current[key] = value
                index += 1
                continue
            if stripped == "config:":
                config_indent = indent
                index += 1
                continue
            if stripped and indent == 0:
                current = None
        index += 1
    if current is not None:
        rows.append(current)
    return rows


def _row_is_disabled(text: str, module_id: str) -> bool:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == f"- id: {module_id}":
            for follow in lines[index + 1 : index + 12]:
                if follow.strip().startswith("- id: "):
                    break
                if follow.strip() == "disabled: true":
                    return True
    return False


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    run_id: str,
    play_profile: str,
    save_reference: str | None,
    backup: bool,
    dsh_dir: Path,
    out_dir: Path,
) -> dict:
    belief_mode = os.environ.get("CIV_MCP_BELIEF_MODE")
    timeout_ms = _overlay_timeout_ms()
    save_path = resolve_save(save_reference)
    save_facts = (
        extract_save_facts(save_path)
        if save_path is not None
        else {"reference": save_reference, "unavailable": "save file not found"}
    )
    backup_result = (
        backup_save(save_path, out_dir / "saves")
        if backup and save_path is not None
        else None
    )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "repo": git_state(),
        "dsh": dsh_state(
            dsh_dir=dsh_dir,
            play_profile=play_profile,
            belief_mode=belief_mode,
            timeout_ms=timeout_ms,
        ),
        "tools": tool_surface(),
        "save": save_facts,
        "save_backup": backup_result,
        "experiment_dir": str(out_dir),
        # Rules, difficulty, DLC and the exact save identity are read from the
        # save header above. Anything that can only be confirmed by a live
        # single-client session stays explicitly unverified here rather than
        # being filled in from memory.
        "live_only_unverified": [
            "当前回合号与对局内实际启用内容（需 FireTuner 只读确认）",
            "存档是否仍与游戏内加载的分支一致（需 get_game_overview 回合号核对）",
        ],
    }


def _overlay_timeout_ms() -> int | None:
    overlay = REPO_ROOT / "integrations" / "deepseek-harness" / "civ6.cordis.yml"
    match = re.search(
        r"^\s*toolCallTimeoutMs:\s*(\d+)\s*$", overlay.read_text(encoding="utf-8"), re.M
    )
    return int(match.group(1)) if match else None


def write_manifest(manifest: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / "manifest.json"
    temporary = out_dir / "manifest.json.tmp"
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, destination)
    return destination


def render_summary(manifest: dict) -> str:
    repo = manifest["repo"]
    dsh = manifest["dsh"]
    save = manifest["save"]
    lines = [
        f"运行编号: {manifest['run_id']}",
        f"代码提交: {repo['commit'] or '无法读取'}"
        f" ({repo['branch'] or '?'})"
        + ("  ⚠ 工作区有未提交修改" if repo["dirty"] else ""),
        f"DSH: {dsh['version'] or '未找到'} @ {dsh['dir']}",
        f"实际模型: {dsh['model'] or '未能从 DSH 配置读取'}"
        f"（provider={dsh['provider'] or '?'}）",
        f"play_profile={dsh['play_profile']} belief_mode={dsh['belief_mode'] or '未设置'}",
        f"宿主超时: {dsh['tool_call_timeout_ms']} ms",
        f"工具数: {manifest['tools']['count']}"
        f" sha256={manifest['tools']['surface_sha256'][:12]}…",
        f"存档: {save.get('name', save.get('reference'))}"
        f" ruleset={save.get('ruleset')} speed={save.get('game_speed')}"
        f" size={save.get('map_size')} difficulty={save.get('difficulty')}",
        f"存档 sha256: {save.get('sha256') or '不可用'}",
    ]
    if manifest.get("save_backup"):
        lines.append(f"存档备份: {manifest['save_backup']}")
    lines.append(f"清单已写入: {manifest['experiment_dir']}/manifest.json")
    lines.append("仍需现场只读确认: " + "；".join(manifest["live_only_unverified"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", help="运行编号（默认用 time-run 生成）")
    parser.add_argument(
        "--save",
        help="存档名或路径；用于记录 ruleset/速度/难度/模组与 SHA-256",
    )
    parser.add_argument(
        "--backup-save",
        action="store_true",
        help="把 --save 复制到实验目录（只复制，绝不移动或删除原存档）",
    )
    parser.add_argument(
        "--play-profile",
        choices=("legacy", "lean"),
        default=os.environ.get("CIV_MCP_PLAY_PROFILE", "legacy"),
    )
    parser.add_argument("--dsh-dir", type=Path, default=DEFAULT_DSH_DIR)
    parser.add_argument("--out", type=Path, help="实验目录（默认 experiments/<run-id>）")
    parser.add_argument("--list-saves", action="store_true", help="只列出可用存档后退出")
    parser.add_argument("--json", action="store_true", help="只打印 JSON 清单")
    args = parser.parse_args(argv)

    if args.list_saves:
        saves = discover_saves()
        if not saves:
            print("未在本机 Civ VI 存档目录中找到 .Civ6Save。", file=sys.stderr)
            return 1
        for path in saves:
            facts = extract_save_facts(path)
            print(
                f"{path.name}\t{facts['game_speed']}\t{facts['map_size']}\t"
                f"{facts['difficulty']}\t{facts['ruleset']}\t{facts['sha256'][:12]}"
            )
        return 0

    run_id = args.run_id or "time-run"
    out_dir = args.out or (DEFAULT_EXPERIMENTS_DIR / run_id)

    os.environ.setdefault("CIV_MCP_PLAY_PROFILE", args.play_profile)
    manifest = build_manifest(
        run_id=run_id,
        play_profile=args.play_profile,
        save_reference=args.save,
        backup=args.backup_save,
        dsh_dir=args.dsh_dir,
        out_dir=out_dir,
    )
    write_manifest(manifest, out_dir)

    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(render_summary(manifest))
    if args.save and "unavailable" in manifest["save"]:
        print(
            f"警告: 未找到存档 {args.save}；规则/难度/DLC 未被记录。"
            "先用 --list-saves 选择可复现的中盘存档。",
            file=sys.stderr,
        )
        return 2
    if manifest["save_backup"] and manifest["save_backup"].get("error"):
        print(f"警告: {manifest['save_backup']['error']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

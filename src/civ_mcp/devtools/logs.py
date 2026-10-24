"""Bounded views of retained action logs; no gameplay status inference."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import heapq
import json
import os
from pathlib import Path
import re

from civ_mcp.devtools.evidence import CommandError


def reject_constant(value):
    raise ValueError(f"Non-JSON number: {value}")


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise CommandError("INVALID_TIME", "Expected a string timestamp with timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("missing timezone")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise CommandError("INVALID_TIME", f"Expected ISO-8601 timestamp with timezone: {value}") from exc


def sessions(directory: Path, limit: int) -> dict:
    if not directory.exists():
        return {"directory": str(directory), "directory_exists": False, "sessions": [], "total": 0}
    if not directory.is_dir():
        raise CommandError("LOG_DIRECTORY_INVALID", f"Not a directory: {directory}")
    files = sorted((path for path in directory.glob("*.jsonl") if path.is_file()),
                   key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    return {"directory": str(directory), "directory_exists": True, "total": len(files),
            "sessions": [{"session": path.stem, "file": str(path), "bytes": path.stat().st_size,
                          "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
                         for path in files[:limit]]}


def read_logs(directory: Path, session: str | None, file: str | None, *, limit: int,
              tool: str | None, contains: str | None, since: str | None,
              until: str | None, timeline: bool) -> dict:
    if file:
        path = Path(file).expanduser().resolve()
    elif session and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
        path = directory / f"{session}.jsonl"
    else:
        raise CommandError("LOG_SESSION_INVALID", "Supply a session ID or --file PATH")
    start, end = timestamp(since) if since else None, timestamp(until) if until else None
    if start and end and start > end:
        raise CommandError("INVALID_TIME_RANGE", "--since must not be after --until")
    if not path.is_file():
        raise CommandError("LOG_NOT_FOUND", f"Log file not found: {path}")
    count = matched = malformed = 0
    tail_incomplete = False
    digest = hashlib.sha256()
    rows = deque(maxlen=limit)
    newest = []
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        remaining = size
        # Bound the read to the initial file length. A concurrent writer may
        # append, but this query never follows the file or observes forever.
        while remaining > 0:
            raw = stream.readline(remaining)
            if not raw:
                break
            remaining -= len(raw)
            digest.update(raw)
            count += 1
            try:
                row = json.loads(raw, parse_constant=reject_constant)
                if (not isinstance(row, dict) or not isinstance(row.get("tool"), str)
                        or not isinstance(row.get("params"), dict) or not isinstance(row.get("result"), str)):
                    raise ValueError("Unsupported action log row")
                moment = timestamp(row.get("timestamp"))
            except (ValueError, UnicodeError, CommandError):
                malformed += 1
                tail_incomplete = not raw.endswith(b"\n") and remaining == 0
                continue
            if ((tool and row["tool"] != tool) or (contains and contains not in row["result"])
                    or (start and moment < start) or (end and moment > end)):
                continue
            matched += 1
            entry = {**row, "line": count}
            if timeline:
                heapq.heappush(newest, (moment, count, entry))
                if len(newest) > limit:
                    heapq.heappop(newest)
            else:
                rows.append(entry)
    return {"file": str(path), "captured_bytes": size - remaining, "source_sha256": digest.hexdigest(),
            "lines": count, "malformed_lines": malformed, "tail_incomplete": tail_incomplete,
            "matched": matched, "truncated": matched > limit,
            "order": "timestamp" if timeline else "file",
            "rows": [item[2] for item in sorted(newest)] if timeline else list(rows)}

#!/usr/bin/env -S .venv/bin/python
"""Run-dashboard server — live view over belief journals, read-only.

Serves design/run-dashboard.html and a /state JSON endpoint built from the
most recently modified belief journal. Reads files only: never touches
FireTuner, the live MCP process, or the journal itself, so it is safe to
run alongside a live DSH session at any time.

    ./scripts/run_dashboard.py                # http://127.0.0.1:8765
    ./scripts/run_dashboard.py --journal PATH # pin one journal file/dir
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from civ6_belief_engine.dashboard import (
    DEFAULT_JOURNAL_DIR,
    build_dashboard_state,
    pick_live_journal,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = REPO_ROOT / "design" / "run-dashboard.html"


def _resolve_journal(raw: str | None) -> Path | None:
    if not raw:
        return pick_live_journal()
    path = Path(raw).expanduser()
    if path.is_dir():
        return pick_live_journal(path)
    return path


class Handler(BaseHTTPRequestHandler):
    journal_arg: str | None = None

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path.startswith("/state"):
            journal = _resolve_journal(self.journal_arg)
            if journal is None:
                body = {"status": "no_journal", "directory": str(DEFAULT_JOURNAL_DIR)}
            else:
                try:
                    body = build_dashboard_state(journal)
                except Exception as exc:  # monitoring must never crash the game loop
                    body = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            self._send_json(body)
            return
        if self.path in ("/", "/index.html"):
            try:
                html = HTML_PATH.read_text(encoding="utf-8")
            except OSError as exc:
                self._send_json({"status": "error", "error": f"dashboard html missing: {exc}"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def _send_json(self, body: dict, code: int = 200) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"[run-dashboard] {self.address_string()} {format % args}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--journal", default=None, help="journal file or directory (default: latest in ~/.civ6-mcp/beliefs)")
    args = parser.parse_args()

    Handler.journal_arg = args.journal
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"run-dashboard → http://127.0.0.1:{args.port}  (journal: {args.journal or 'auto (latest)'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nrun-dashboard stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

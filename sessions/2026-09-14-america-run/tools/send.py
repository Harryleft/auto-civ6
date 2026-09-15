#!/usr/bin/env python3
"""向常驻 MCP 守护进程发一条工具调用，并等待结果。

用法：
    python send.py get_game_overview
    python send.py end_turn '{}'
    python send.py --timeout 900 end_turn '{}'
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(r"S:\vibe_coding\civ6-map-analysis")
CMD = HERE / "_cmd.jsonl"
OUT = HERE / "_out.jsonl"


def _lines(p: Path) -> list[str]:
    try:
        return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


def _coerce(v: str):
    """把 key=value 形式的字符串转成合适的类型。"""
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", ""):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    if v[:1] in "[{":
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
    return v


def main() -> int:
    argv = sys.argv[1:]
    timeout = 600.0
    if argv and argv[0] == "--timeout":
        timeout = float(argv[1])
        argv = argv[2:]
    if not argv:
        print("usage: send.py [--timeout S] <tool> [key=value ... | @args.json]")
        return 2

    tool = argv[0]
    rest = argv[1:]
    if len(rest) == 1 and rest[0].startswith("@"):
        args = json.loads(Path(rest[0][1:]).read_text(encoding="utf-8"))
    elif len(rest) == 1 and rest[0].lstrip()[:1] == "{":
        args = json.loads(rest[0])
    else:
        args = {}
        for item in rest:
            if "=" not in item:
                print(f"bad argument (expected key=value): {item!r}")
                return 2
            k, _, v = item.partition("=")
            args[k] = _coerce(v)

    before = len(_lines(OUT))
    with CMD.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": tool, "args": args}, ensure_ascii=False) + "\n")
        fh.flush()

    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = _lines(OUT)
        if len(lines) > before:
            rec = json.loads(lines[-1])
            print(f"=== {rec['tool']}  ok={rec['ok']} focused={rec.get('focused')} {rec.get('seconds')}s ===")
            print(rec["result"])
            return 0 if rec["ok"] else 1
        time.sleep(0.5)

    print(f"TIMEOUT after {timeout}s waiting for {tool}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())

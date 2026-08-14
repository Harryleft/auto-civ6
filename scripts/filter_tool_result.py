#!/usr/bin/env python3
"""Filter one Civ6 MCP tool result locally before downstream delivery.

Examples:
  uv run python scripts/filter_tool_result.py --tool get_governance_brief < raw.txt
  uv run python scripts/filter_tool_result.py --tool get_units --max-chars 12000 raw.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from civ_mcp.result_filter import ResultFilterConfig, filter_tool_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="input file; stdin by default")
    parser.add_argument("--tool", required=True, help="MCP tool name")
    parser.add_argument("--max-chars", type=int, default=20_000)
    parser.add_argument("--history-items", type=int, default=3)
    parser.add_argument(
        "--params",
        default="{}",
        help="JSON tool parameters; targeted belief queries pass through",
    )
    args = parser.parse_args()

    raw = args.input.read_text() if args.input else sys.stdin.read()
    config = ResultFilterConfig(
        enabled=True,
        max_chars=max(2_000, args.max_chars),
        history_items=max(1, args.history_items),
    )
    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as exc:
        parser.error(f"--params must be valid JSON: {exc.msg}")
    if not isinstance(params, dict):
        parser.error("--params must be a JSON object")
    sys.stdout.write(filter_tool_result(args.tool, raw, params=params, config=config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

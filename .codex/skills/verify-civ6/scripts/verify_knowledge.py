#!/usr/bin/env python3
"""Compatibility entry point for the project-local offline CLI."""

import sys

from civ_mcp.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["verify", "knowledge", *sys.argv[1:]]))

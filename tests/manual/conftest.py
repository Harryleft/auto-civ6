"""Exclude manual live-game smoke scripts from pytest collection.

These require a running Civ 6 instance (EnableTuner=1) and are meant to be run
directly, e.g.:

    uv run python tests/manual/test_connection.py
"""

collect_ignore_glob = ["test_*.py"]

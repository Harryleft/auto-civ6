"""Named offline test selections; never dispatch standalone live scripts."""

from __future__ import annotations

import importlib.util
import os
import re
import sys

from civ_mcp.devtools.evidence import CommandError, Evidence


DOMAINS = {
    "debug": ("test_debug_cli.py", "test_server.py"),
    "knowledge": ("test_knowledge_mcp.py", "test_knowledge_stdio.py"),
    "actions": ("test_action_confirmation.py", "test_pending_dispatch.py"),
    "turns": ("test_end_turn.py", "test_turn_status.py"),
    "checkpoints": ("test_game_lifecycle.py", "test_checkpoint_run.py"),
    "diplomacy": ("test_trade_progress.py", "test_incoming_negotiation.py"),
    "connection": ("test_connection.py", "test_tuner_client.py"),
}


def pytest_options(arguments: list[str]) -> list[str]:
    # Raw paths, plugins and config overrides can escape the offline selection.
    # Keep a small explicit set of common pytest controls; subprocess uses no shell.
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value in {"-q", "-v", "-vv", "-x", "--collect-only", "--disable-warnings"}:
            index += 1
        elif value in {"-k", "-m", "--maxfail"} and index + 1 < len(arguments):
            operand = arguments[index + 1]
            if value == "--maxfail" and not re.fullmatch(r"[1-9][0-9]*", operand):
                raise CommandError("INVALID_PYTEST_ARGUMENT", "--maxfail must be positive")
            index += 2
        elif re.fullmatch(r"--maxfail=[1-9][0-9]*", value):
            index += 1
        else:
            raise CommandError("INVALID_PYTEST_ARGUMENT", f"Unsupported pytest argument: {value}")
    return arguments


def run_tests(evidence: Evidence, domain: str, arguments: list[str], timeout: float, state: dict) -> str:
    from civ_mcp.devtools.knowledge import check_interpreter
    check_interpreter(evidence.project)
    if importlib.util.find_spec("pytest") is None:
        raise CommandError("DEPENDENCY_MISSING", "Install dev dependencies with uv sync --locked")
    files = [f"tests/{name}" for name in DOMAINS[domain]]
    state.update(domain=domain, selected_files=files, pytest_arguments=pytest_options(arguments),
                 collection_only="--collect-only" in arguments,
                 pytest_environment={"PYTEST_ADDOPTS": None, "PYTEST_PLUGINS": None,
                                     "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    evidence.json("test-selection", state)
    if not all((evidence.project / name).is_file() for name in files):
        raise CommandError("TEST_FILE_MISSING", "Domain test file missing; update the explicit selection")
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = evidence.run("pytest", [sys.executable, "-m", "pytest", "-q", *files, *arguments],
                          timeout=timeout, env=environment)
    state["pytest_exit_code"] = result.returncode
    if result.returncode in {4, 5}:
        raise CommandError("PYTEST_NOT_RUN", f"pytest exit {result.returncode}; inspect pytest.json")
    if result.returncode != 0:
        raise CommandError("PYTEST_FAILED", f"{domain} tests failed (pytest exit {result.returncode})", "FAIL",
            hint="Read pytest.json stdout/stderr, fix the reported test failures, then rerun the same domain.",
            details={"domain": domain, "pytest_exit_code": result.returncode})
    return "PASS"

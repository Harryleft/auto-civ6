"""The belief-tool pipeline must mirror ``_logged``'s two guarantees.

Belief tools run through ``_belief_tool`` rather than ``_logged``, so anything
``_logged`` does for every game tool has to be repeated here deliberately:

* wait for DSH auto-resume before touching the shared connection, and
* turn any failure into an ``Error: ...`` text result instead of letting it
  escape as a protocol-level error.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp.server import pipeline
from civ_mcp.server.assembly import PlayProfile


class _Logger:
    def __init__(self) -> None:
        self.tool_calls: list[tuple] = []
        self.errors: list[tuple[str, str]] = []

    async def log_tool_call(self, *args) -> None:
        self.tool_calls.append(args)

    async def log_error(self, tool: str, message: str) -> None:
        self.errors.append((tool, message))


def _install(monkeypatch, logger, order: list[str]):
    async def ready(_ctx):
        order.append("ready")

    async def context(_ctx):
        order.append("context")
        return object(), 11

    async def noop(*_args, **_kwargs):
        return None

    def sync_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        pipeline,
        "_get_belief_mode",
        lambda _ctx: SimpleNamespace(records_events=True),
    )
    monkeypatch.setattr(pipeline, "_get_logger", lambda _ctx: logger)
    # The lean profile guard consults the play profile; these tests describe
    # the legacy pipeline, where no tool is withheld.
    monkeypatch.setattr(
        pipeline, "_get_play_profile", lambda _ctx: PlayProfile.LEGACY
    )
    monkeypatch.setattr(pipeline, "_await_auto_resume_ready", ready)
    monkeypatch.setattr(pipeline, "_belief_context", context)
    monkeypatch.setattr(pipeline, "_flush_belief_events", noop)
    # _sync_governance_graph is synchronous in production.
    monkeypatch.setattr(pipeline, "_sync_governance_graph", sync_noop)


def test_readiness_is_awaited_before_any_game_access(monkeypatch):
    order: list[str] = []
    _install(monkeypatch, _Logger(), order)

    def operation(_engine, _turn):
        order.append("operation")
        return {"ok": True}

    asyncio.run(pipeline._belief_tool(object(), "get_belief_state", {}, operation))
    assert order == ["ready", "context", "operation"]


def test_unexpected_error_becomes_an_error_result(monkeypatch):
    """A KeyError must not escape as a protocol error the caller cannot read."""

    logger = _Logger()
    _install(monkeypatch, logger, [])

    def operation(_engine, _turn):
        raise KeyError("missing_entity")

    result = asyncio.run(
        pipeline._belief_tool(object(), "get_belief_state", {}, operation)
    )
    assert "missing_entity" in result
    assert "Error:" in result or "错误" in result
    assert logger.errors and logger.errors[-1][0] == "get_belief_state"


def test_verified_failures_still_report_their_own_message(monkeypatch):
    """The catch-all must not shadow the specific belief-engine branch."""

    logger = _Logger()
    _install(monkeypatch, logger, [])

    def operation(_engine, _turn):
        raise pipeline.BeliefEngineError("entity type is not supported")

    result = asyncio.run(
        pipeline._belief_tool(object(), "get_belief_state", {}, operation)
    )
    assert "entity type is not supported" in result


@pytest.mark.parametrize("tool_name", ["get_belief_state", "route_belief_decision"])
def test_disabled_mode_short_circuits_without_touching_the_game(
    monkeypatch, tool_name
):
    logger = _Logger()
    called: list[str] = []

    async def ready(_ctx):
        called.append("ready")

    monkeypatch.setattr(
        pipeline,
        "_get_belief_mode",
        lambda _ctx: SimpleNamespace(records_events=False, value="off"),
    )
    monkeypatch.setattr(pipeline, "_get_logger", lambda _ctx: logger)
    monkeypatch.setattr(
        pipeline, "_get_play_profile", lambda _ctx: PlayProfile.LEGACY
    )
    monkeypatch.setattr(pipeline, "_await_auto_resume_ready", ready)

    result = asyncio.run(
        pipeline._belief_tool(object(), tool_name, {}, lambda *_a: {"ok": True})
    )
    assert "disabled" in result
    assert called == []

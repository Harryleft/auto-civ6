"""Diplomacy response confirmation: polling and confirmation-only semantics.

The live T50→T100 run exposed two behaviours worth pinning down:

* a lost/unread confirmation was judged on a single frame, so a native
  statement that advanced a moment later still looked unconfirmed;
* a follow-up call that only confirmed the pending response was reported like
  a fresh submission, hiding that the requested choice was never sent.
"""

from __future__ import annotations

import asyncio

import pytest

from civ_mcp import diplomacy
from civ_mcp.diplomacy import DiplomacyProgress

SCOPE_ROWS = ["DIPLO_OBSERVER|READY", "DIPLO_SCOPE|0|CIVILIZATION_SUMERIA|7"]
CHOICES_ROWS = ["DIPLO_CHOICES|KNOWN|5", "DIPLO_CHOICE|CHOICE_POSITIVE", "DIPLO_CHOICE|CHOICE_EXIT"]


class ScriptedConn:
    """Answer each named-context query by its native intent, in scripted order."""

    def __init__(self, response_rows: list[list[str]], choices_rows=None):
        self.response_rows = [list(row) for row in response_rows]
        self.choices_rows = CHOICES_ROWS if choices_rows is None else list(choices_rows)
        self.calls: list[str] = []
        self.response_queries = 0

    async def execute_in_named_state(self, name, source, timeout=5):
        assert name == "DiplomacyActionView"
        self.calls.append(source)
        if "OnSelectionButtonClicked" in source:
            return ["DIPLO_RESPONSE|REQUESTED"]
        if "DiplomacyManager.IsSessionIDOpen" in source:
            self.response_queries += 1
            if len(self.response_rows) > 1:
                return self.response_rows.pop(0)
            return list(self.response_rows[0])
        if "readChoices" in source:
            return list(self.choices_rows)
        return list(SCOPE_ROWS)


@pytest.fixture(autouse=True)
def fast_confirmation(monkeypatch):
    monkeypatch.setattr(diplomacy, "RESPONSE_CONFIRM_SECONDS", 0.5)
    monkeypatch.setattr(diplomacy, "RESPONSE_POLL_SECONDS", 0.01)


def test_confirmation_polls_until_the_native_statement_advances():
    conn = ScriptedConn([["DIPLO_RESPONSE|WAITING"], ["DIPLO_RESPONSE|WAITING"], ["DIPLO_RESPONSE|CONTINUES"]])
    progress = DiplomacyProgress()

    result = asyncio.run(progress.respond(conn, 2, 1, "POSITIVE"))

    assert "SESSION_CONTINUES" in result
    assert conn.response_queries >= 3
    assert progress.response is None


def test_confirmation_only_call_reports_that_it_did_not_submit():
    conn = ScriptedConn([["DIPLO_RESPONSE|CONTINUES"]])
    progress = DiplomacyProgress()
    progress.response = ("token", 2, 1, 5)

    result = asyncio.run(progress.respond(conn, 2, 1, "EXIT"))

    assert result.startswith("OK:CONFIRMED|")
    assert "EXIT was NOT submitted" in result
    assert "only confirmed the pending response" in result
    assert not any("OnSelectionButtonClicked" in source for source in conn.calls)
    assert progress.response is None


def test_closed_session_is_confirmed_without_resubmission():
    conn = ScriptedConn([["DIPLO_RESPONSE|CLOSED"]])
    progress = DiplomacyProgress()
    progress.response = ("token", 2, 1, 5)

    result = asyncio.run(progress.respond(conn, 2, 1, "EXIT"))

    assert result.startswith("OK:CONFIRMED|SESSION_CLOSED")
    assert progress.response is None


def test_unconfirmed_response_keeps_the_pending_gate(monkeypatch):
    monkeypatch.setattr(diplomacy, "RESPONSE_CONFIRM_SECONDS", 0.05)
    conn = ScriptedConn([["DIPLO_RESPONSE|WAITING"]])
    progress = DiplomacyProgress()
    progress.response = ("token", 2, 1, 5)

    result = asyncio.run(progress.respond(conn, 2, 1, "EXIT"))

    assert result.startswith("ERR:OUTCOME_UNKNOWN|")
    assert "no response is resent" in result
    assert progress.response == ("token", 2, 1, 5)
    assert 1 <= conn.response_queries <= 10

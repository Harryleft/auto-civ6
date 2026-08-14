"""Tests for the side-effect-free Belief Engine mode configuration."""

from __future__ import annotations

import pytest

from civ_mcp.belief_mode import BELIEF_MODE_ENV, BeliefMode


def test_default_mode_is_enforce(monkeypatch):
    monkeypatch.delenv(BELIEF_MODE_ENV, raising=False)

    assert BeliefMode.from_env() is BeliefMode.ENFORCE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("off", BeliefMode.OFF),
        ("observe", BeliefMode.OBSERVE),
        ("enforce", BeliefMode.ENFORCE),
    ],
)
def test_environment_accepts_all_modes(monkeypatch, value, expected):
    monkeypatch.setenv(BELIEF_MODE_ENV, value)

    assert BeliefMode.from_env() is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" OFF ", BeliefMode.OFF),
        ("ObSeRvE", BeliefMode.OBSERVE),
        ("  ENFORCE  ", BeliefMode.ENFORCE),
    ],
)
def test_environment_normalizes_case_and_surrounding_whitespace(
    monkeypatch, value, expected
):
    monkeypatch.setenv(BELIEF_MODE_ENV, value)

    assert BeliefMode.from_env() is expected


def test_invalid_environment_value_raises_explicit_value_error(monkeypatch):
    monkeypatch.setenv(BELIEF_MODE_ENV, "audit")

    with pytest.raises(ValueError, match=r"CIV_MCP_BELIEF_MODE.*off, observe, enforce"):
        BeliefMode.from_env()


@pytest.mark.parametrize(
    ("mode", "records_events", "enforces_actions", "appends_context", "captures"),
    [
        (BeliefMode.OFF, False, False, False, False),
        (BeliefMode.OBSERVE, True, False, False, False),
        (BeliefMode.ENFORCE, True, True, True, True),
    ],
)
def test_mode_properties_form_the_expected_capability_matrix(
    mode, records_events, enforces_actions, appends_context, captures
):
    assert mode.records_events is records_events
    assert mode.enforces_actions is enforces_actions
    assert mode.appends_context is appends_context
    assert mode.captures_governance_snapshot is captures


def test_from_env_with_explicit_mapping_does_not_mutate_it():
    environ = {BELIEF_MODE_ENV: " observe "}

    assert BeliefMode.from_env(environ) is BeliefMode.OBSERVE
    assert environ == {BELIEF_MODE_ENV: " observe "}

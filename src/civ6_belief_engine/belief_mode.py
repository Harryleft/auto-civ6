"""Small, side-effect-free configuration for Belief Engine runtime behavior."""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum


BELIEF_MODE_ENV = "CIV_MCP_BELIEF_MODE"


class BeliefMode(StrEnum):
    """The amount of Belief Engine behavior enabled in the live game loop."""

    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"

    @classmethod
    def parse(cls, value: str) -> "BeliefMode":
        """Parse a mode value after normalizing case and surrounding whitespace."""

        if not isinstance(value, str):
            raise ValueError(
                f"{BELIEF_MODE_ENV} must be one of off, observe, enforce; "
                f"got {value!r}"
            )

        normalized = value.strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(
                f"{BELIEF_MODE_ENV} must be one of off, observe, enforce; "
                f"got {value!r}"
            ) from exc

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "BeliefMode":
        """Read the configured mode without modifying the environment.

        A blank value counts as "not configured" and yields the default. Both a
        shell ``VAR=`` and a process launcher that forwards a variable with an
        empty fallback produce that case, and it means "unset" rather than "an
        invalid mode"; treating it as an error would make an unrelated empty
        variable break startup.
        """

        source = os.environ if environ is None else environ
        raw = str(source.get(BELIEF_MODE_ENV, "")).strip()
        if not raw:
            return cls.ENFORCE
        return cls.parse(raw)

    @property
    def records_events(self) -> bool:
        """Whether Belief Engine events should be recorded."""

        return self in (self.OBSERVE, self.ENFORCE)

    @property
    def enforces_actions(self) -> bool:
        """Whether belief routing may enforce action decisions."""

        return self is self.ENFORCE

    @property
    def appends_context(self) -> bool:
        """Whether belief context should be appended to tool results."""

        return self is self.ENFORCE

    @property
    def captures_governance_snapshot(self) -> bool:
        """Whether the governance snapshot should be captured each turn."""

        return self is self.ENFORCE

    def runtime_policy(self) -> dict[str, str]:
        """Expose one machine-readable policy for every agent host."""

        return {
            "belief_mode": self.value,
            "turn_entry_tool": "get_game_overview",
            "belief_events": "recorded" if self.records_events else "disabled",
            "governance": (
                "enforced" if self.captures_governance_snapshot else "disabled"
            ),
            "action_routing": "enforced" if self.enforces_actions else "bypassed",
            "belief_context": "appended" if self.appends_context else "disabled",
        }

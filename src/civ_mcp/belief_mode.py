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
        """Read the configured mode without modifying the environment."""

        source = os.environ if environ is None else environ
        return cls.parse(source.get(BELIEF_MODE_ENV, cls.ENFORCE.value))

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

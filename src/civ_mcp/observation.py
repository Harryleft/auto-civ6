"""Completeness carried with a domain reader's value, never inferred by callers.

This describes one read, not an action receipt or permission to retire pending.
Legacy parsers remain available for callers that only need their original value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class Observation(Generic[T]):
    value: T
    problems: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.problems

    def require_complete(self) -> T:
        """Keep strict callers from silently interpreting missing data as empty."""
        if self.problems:
            raise ValueError("; ".join(self.problems))
        return self.value


def observed(parser: Callable[[list[str]], T]) -> Callable[[list[str]], Observation[T]]:
    """Adapt parsers whose validity is already checked by parsing and scope.

    Domains with an explicit completion marker provide their own observation
    reader instead. Parser errors retain the existing exception contract.
    """
    def read(rows: list[str]) -> Observation[T]:
        return Observation(parser(rows))
    return read

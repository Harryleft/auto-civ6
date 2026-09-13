"""Contract validators shared across the domain package.

These were copied into every module that needed them: four identical
``_nonempty`` implementations under two different names (``_nonempty`` and
``_text``), and three near-identical ``_strings``/``_texts`` of which one had
grown an extra tuple coercion the others lacked. Two rules, seven functions,
four files — one copy each now, with the coercion kept because it costs nothing
and makes the helper accept any iterable.

Only validation that is genuinely shared lives here. ``_strict_bool`` and
``_strict_int`` stay in ``governance.models`` because they have exactly one
implementation and one owner.
"""

from __future__ import annotations

from typing import Any


def require_text(value: Any, name: str) -> str:
    """Return ``value`` stripped, or raise when it is not a non-empty string."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def require_texts(values: Any, name: str) -> tuple[str, ...]:
    """Normalize an iterable of non-empty strings, rejecting duplicates."""

    if not isinstance(values, tuple):
        values = tuple(values)
    normalized = tuple(require_text(value, name) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicates")
    return normalized

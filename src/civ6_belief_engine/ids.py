"""Shared entity-id slug helpers.

Every derivation rule, normalizer, and snapshot projection that turns a
display name into a stable entity id uses this one implementation, so the
entity-id grammar cannot drift between layers. Callers decide case policy
explicitly (e.g. ``slugify(name).lower()`` for metrics keys).
"""

from __future__ import annotations

import re
from typing import Any


def slugify(value: Any) -> str:
    """Replace runs of non-word characters with ``-`` and strip edge dashes.

    Case is preserved: historical entity ids (e.g. tech predictions named
    after the subject) are case-sensitive, so lowercasing must stay a
    caller-side decision. Behaviour matches the original ``_slug`` helpers
    that this module replaces.
    """

    cleaned = re.sub(r"[^\w.-]+", "-", str(value), flags=re.UNICODE)
    return cleaned.strip("-.") or "unknown"

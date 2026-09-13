"""Canonical JSON identity for action arguments.

Two implementations of the same identity existed: ``governance.models``
normalized through ``_json_value`` (dataclasses to dicts, mapping keys to
strings, sets to sorted lists) before hashing, while ``belief_engine`` hashed a
raw ``json.dumps``. Measured over eight input shapes they agree wherever the raw
version works at all — it simply raises ``TypeError`` on sets and dataclasses,
which is precisely the normalizer's job.

The council matches a candidate action against the approved intent by this hash,
and ``authorize_action`` does the same at execution time. Two implementations
meant any future change to one side would silently desynchronise approval from
execution, so there is one now, and it is the stricter of the two.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any


def json_value(value: Any) -> Any:
    """Convert supported typed values to a stable JSON-compatible structure."""

    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, set):
        return sorted(json_value(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported action argument type: {type(value).__name__}")


def arguments_hash(arguments: Mapping[str, Any]) -> str:
    """Stable identity of an action's arguments, independent of key order."""

    canonical = json.dumps(
        json_value(arguments), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

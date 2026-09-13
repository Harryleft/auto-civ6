"""Reading a model-facing tool result as a JSON object.

``presentation`` and ``result_filter`` each carried an identical copy of this
five-line helper. Both are deliberately dependency-free leaf modules, and the
natural semantic home (``facts``, which owns the envelope contract) pulls in the
whole Lua builder package: importing it costs ~34ms more than the package
baseline, and it would give two leaf modules a dependency they do not otherwise
need — paying that to reuse five lines is the wrong trade.

So the helper lives in its own leaf instead: one definition, and both consumers
stay importable in isolation.
"""

from __future__ import annotations

import json
from typing import Any


def json_object(result: str) -> dict[str, Any] | None:
    """Parse ``result`` as a JSON object, or return ``None`` if it is not one.

    Malformed JSON and non-object JSON (arrays, scalars) are both reported the
    same way: the caller falls back to treating the result as plain text.
    """

    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None

"""Native identity facts for existing scripted FireTuner fixtures.

Scope probes are read-only InGame observations, separate from scripted actions.
Tests for absent/malformed native facts use the explicit ScopeEngine instead.
"""
DEFAULT_SCOPE = ("CIVILIZATION_SUMERIA", 719665280, 22, 0)


def scope_rows(scope=DEFAULT_SCOPE):
    return ["ACTION_SCOPE|" + "|".join(map(str, scope))]


def with_action_identity(code, rows, scope=DEFAULT_SCOPE, *, fill_missing=True):
    if "ACTION_IDENTITY|" not in code:
        return rows
    result = [row + "|0" if row.startswith("ACTION_IDENTITY|") and len(row.split("|")) == 4 else row for row in rows]
    if fill_missing and not any(row.startswith("ACTION_IDENTITY|") for row in result):
        result.insert(0, "ACTION_IDENTITY|" + "|".join(map(str, scope)))
    return result

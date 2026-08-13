"""Authoritative feature capabilities for each Civilization VI ruleset.

Ruleset identity, not the presence of a database row or Lua method, decides
whether an optional mechanic is available.  Keeping this table pure makes it
safe to use before any GameState integration exists.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from civ_mcp.governance.models import RulesetCapabilities


RULESET_STANDARD = "RULESET_STANDARD"
RULESET_EXPANSION_1 = "RULESET_EXPANSION_1"
RULESET_EXPANSION_2 = "RULESET_EXPANSION_2"


class UnsupportedRulesetError(ValueError):
    """Raised when no safe capability profile exists for a ruleset."""


_COMMON_CAPABILITIES = {
    "basic_diplomacy": True,
    "trade": True,
    "city_states": True,
    "religion": True,
    "combat_estimate": True,
}

CAPABILITY_NAMES = frozenset(
    {
        "governors",
        "ages",
        "dedications",
        "alliances",
        "diplomatic_favor",
        "world_congress",
        "resource_stockpiles",
        *_COMMON_CAPABILITIES,
    }
)


RULESET_CAPABILITIES: Mapping[str, RulesetCapabilities] = MappingProxyType({
    RULESET_STANDARD: RulesetCapabilities(
        ruleset=RULESET_STANDARD,
        governors=False,
        ages=False,
        dedications=False,
        alliances=False,
        diplomatic_favor=False,
        world_congress=False,
        resource_stockpiles=False,
        **_COMMON_CAPABILITIES,
    ),
    RULESET_EXPANSION_1: RulesetCapabilities(
        ruleset=RULESET_EXPANSION_1,
        governors=True,
        ages=True,
        dedications=True,
        alliances=True,
        diplomatic_favor=False,
        world_congress=False,
        resource_stockpiles=False,
        **_COMMON_CAPABILITIES,
    ),
    RULESET_EXPANSION_2: RulesetCapabilities(
        ruleset=RULESET_EXPANSION_2,
        governors=True,
        ages=True,
        dedications=True,
        alliances=True,
        diplomatic_favor=True,
        world_congress=True,
        resource_stockpiles=True,
        **_COMMON_CAPABILITIES,
    ),
})


_RULESET_ALIASES = {
    "STANDARD": RULESET_STANDARD,
    "RULESET_STANDARD": RULESET_STANDARD,
    "EXPANSION1": RULESET_EXPANSION_1,
    "EXPANSION_1": RULESET_EXPANSION_1,
    "RULESET_EXPANSION_1": RULESET_EXPANSION_1,
    "EXPANSION2": RULESET_EXPANSION_2,
    "EXPANSION_2": RULESET_EXPANSION_2,
    "RULESET_EXPANSION_2": RULESET_EXPANSION_2,
}


def normalize_ruleset(ruleset: str) -> str:
    """Return the canonical game ruleset name or fail closed.

    An empty or unknown value is not silently treated as the latest expansion:
    doing so would let governance reason about mechanics that may not exist.
    """

    if not isinstance(ruleset, str) or not ruleset.strip():
        raise UnsupportedRulesetError("A non-empty ruleset is required")
    key = ruleset.strip().upper().replace("-", "_").replace(" ", "_")
    canonical = _RULESET_ALIASES.get(key)
    if canonical is None:
        raise UnsupportedRulesetError(f"Unsupported ruleset: {ruleset}")
    return canonical


def capabilities_for_ruleset(ruleset: str) -> RulesetCapabilities:
    """Return the immutable capability profile for ``ruleset``."""

    return RULESET_CAPABILITIES[normalize_ruleset(ruleset)]


def capability_enabled(ruleset: str, capability: str) -> bool:
    """Check one declared capability without accepting arbitrary attributes."""

    if capability not in CAPABILITY_NAMES:
        raise ValueError(f"Unknown ruleset capability: {capability}")
    return bool(getattr(capabilities_for_ruleset(ruleset), capability))

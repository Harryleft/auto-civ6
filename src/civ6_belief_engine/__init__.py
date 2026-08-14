"""Product-level Belief Engine domain package.

The MCP adapter remains available from :mod:`civ_mcp`.  This package is the
canonical home for the product's belief and governance domain logic.
"""

from .belief_engine import BeliefEngine, BeliefEngineError
from .belief_mode import BELIEF_MODE_ENV, BeliefMode

__all__ = [
    "BELIEF_MODE_ENV",
    "BeliefEngine",
    "BeliefEngineError",
    "BeliefMode",
]

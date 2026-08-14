"""Compatibility exports for the legacy ``civ_mcp`` import path.

The implementation now lives in :mod:`civ6_belief_engine.belief_engine`.
Keep this module while external scripts and existing MCP deployments migrate.
"""

from civ6_belief_engine.belief_engine import *  # noqa: F401,F403

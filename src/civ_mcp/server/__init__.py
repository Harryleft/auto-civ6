"""MCP server for Civilization VI — lets LLM agents read game state and play.

Uses FastMCP with the lifespan pattern to maintain a persistent TCP connection
to the running game via FireTuner protocol. Formerly the server.py monolith;
assembly holds lifespan/entry, pipeline holds the _logged runtime, tools/
holds the MCP tool definitions grouped by domain.
"""

from civ_mcp.server.assembly import AppContext, lifespan, main, mcp
from civ_mcp.server import pipeline
from civ_mcp.server.tools import (  # noqa: F401  import side effect: tool registration
    actions,
    belief,
    end_turn,
    queries,
    system,
    world,
    world_model,
)

from civ_mcp import heartbeat  # noqa: F401  stable binding for test monkeypatching
from civ_mcp.server.tools.actions import propose_trade  # noqa: F401  stable import surface
from civ_mcp.server.tools.belief import (  # noqa: F401  stable import surface
    _capture_governance_snapshot,
    _governance_goal_from_dict,
    _governance_payload,
    _governance_proposal_from_dict,
    _national_strategy_payload,
    _normalize_impact_urgency,
    _release_stale_budget_locks,
    _reusable_typed_snapshot_for_turn,
    _typed_snapshot_observation_for_turn,
    get_governance_brief,
    get_turn_brief,
    record_action_verification,
    resolve_governance_council,
    route_belief_decision,
    submit_governance_proposal,
)
from civ_mcp.server.pipeline import (  # noqa: F401  stable import surface
    _append_belief_context,
    _await_auto_resume_ready,
    _belief_action_preflight,
    _belief_context,
    _belief_route_required,
    _belief_tool,
    _canonical_action_params,
    _filter_downstream_result,
    _flush_belief_events,
    _format_belief_turn_brief,
    _format_runtime_policy,
    _get_belief_mode,
    _get_beliefs,
    _get_camera,
    _get_game,
    _get_logger,
    _get_map_capture,
    _get_spatial,
    _get_watchdog,
    _governance_council_required,
    _logged,
    _narrate,
    _normalize_trade_mode,
    _param_summary,
    _record_belief_tool_result,
    _result_summary,
)

"""Lua code builders and response parsers for Civ 6 game state.

Each domain has:
- build_*_query() -> str: returns Lua code to execute via FireTuner
- parse_*_response() -> dataclass: parses pipe-delimited output lines

All queries use print() for output (not return) and terminate with ---END---.
Output format: pipe-delimited fields, one entity per line.

This package re-exports all public symbols so that::

    from civ_mcp import lua as lq
    lq.build_units_query()
    lq.UnitInfo(...)

works identically to the old monolithic ``lua_queries`` module.
"""

# Re-export helpers & sentinel
from civ_mcp.lua._helpers import SENTINEL  # noqa: F401
from civ_mcp.lua.pillage import (  # noqa: F401
    build_pillage_preflight, build_pillage_unit, build_pillage_query,
)
from civ_mcp.lua.espionage import (  # noqa: F401
    build_get_spies_query,
    build_spy_escape_route,
    build_verify_spy_escape,
    build_spy_mission,
    build_spy_travel,
    parse_spies_response,
)
from civ_mcp.lua.cities import (  # noqa: F401
    build_cities_query,
    build_city_attack,
    build_city_attack_followup,
    build_city_production_query,
    build_city_yield_focus_query,
    build_produce_item,
    build_resolve_city_capture,
    build_set_yield_focus,
    build_verify_production,
    parse_cities_response,
    parse_city_production_response,
)
from civ_mcp.lua.diplomacy import (  # noqa: F401
    build_check_diplomacy_session_state,
    build_check_friendship_state,
    build_check_diplomatic_access,
    build_check_alliance_state,
    build_check_war_state,
    build_deal_options_query,
    build_diplomacy_choices_query,
    build_diplomacy_query,
    build_diplomacy_respond,
    build_diplomacy_session_query,
    build_form_alliance,
    build_pending_deals_query,
    build_propose_peace,
    build_respond_to_deal,
    build_send_diplo_action,
    build_war_close_session,
    build_war_dismiss_view,
    parse_deal_options_response,
    parse_test_trade_response,
    parse_diplomacy_response,
    parse_diplomacy_sessions,
    parse_pending_deals_response,
)
from civ_mcp.lua.congress import (  # noqa: F401
    build_congress_submit,
    build_congress_vote,
    build_open_world_congress,
    build_register_wc_voter,
    build_world_congress_query,
    parse_world_congress_response,
)
from civ_mcp.lua.economy import (  # noqa: F401
    build_trade_capacity_check,
    build_trade_destinations_query,
    build_trade_routes_query,
    parse_trade_destinations_response,
    parse_trade_routes_response,
)
from civ_mcp.lua.great_people import (  # noqa: F401
    build_gp_advisor_query,
    build_great_people_query,
    parse_gp_advisor_response,
    parse_great_people_response,
)
from civ_mcp.lua.policies import (  # noqa: F401
    build_policy_plan,
    build_policy_confirmation,
    build_policy_continuation,
    parse_policy_confirmation,
    policy_plan_ready,
)
from civ_mcp.lua.choices import (  # noqa: F401
    build_player_choice,
    build_player_choice_confirmation,
    parse_player_choice_confirmation,
)
from civ_mcp.lua.governors import (  # noqa: F401
    build_governor_action,
    build_governor_confirmation,
    parse_governor_confirmation,
)
from civ_mcp.lua.governance import (  # noqa: F401
    build_available_governments_query,
    build_city_states_query,
    build_dedications_query,
    build_governors_query,
    build_policies_query,
    build_unit_promotions_query,
    build_unit_upgrade_query,
    parse_city_states_response,
    parse_dedications_response,
    parse_governors_response,
    parse_policies_response,
    parse_unit_promotions_response,
)
from civ_mcp.lua.map import (  # noqa: F401
    build_district_advisor_query,
    build_empire_resources_query,
    build_found_city,
    build_global_settle_scan,
    build_map_area_query,
    build_ownership_delta,
    build_purchasable_tiles_query,
    build_revealed_tiles_seed_query,
    build_settle_advisor_query,
    build_static_map_dump,
    build_stockpile_query,
    build_strategic_map_query,
    build_verify_city_at,
    build_wonder_advisor_query,
    parse_district_advisor_response,
    parse_empire_resources_response,
    parse_map_response,
    parse_ownership_delta,
    parse_purchasable_tiles_response,
    parse_revealed_tiles_seed,
    parse_settle_advisor_response,
    parse_static_map_dump,
    parse_stockpile_response,
    parse_strategic_map_response,
    parse_verify_city_at,
    parse_wonder_advisor_response,
)

# Re-export all models
from civ_mcp.lua.models import (  # noqa: F401
    AgendaInfo,
    AppointedGovernor,
    BuilderInfo,
    BuilderTaskReport,
    BuilderTask,
    BeliefInfo,
    CityInfo,
    CityReligionInfo,
    CitySnapshot,
    CityStateInfo,
    CivicOption,
    CivInfo,
    CombatEstimate,
    CongressProposal,
    CongressResolution,
    DealItem,
    DealOptions,
    DedicationChoice,
    DedicationStatus,
    DemographicEntry,
    DiplomacyChoice,
    DiplomacyContext,
    DiplomacyModifier,
    DiplomacySession,
    DistrictPlacement,
    EnvoyStatus,
    FogBoundary,
    GameNotification,
    GameOverStatus,
    GameOverview,
    GovernmentStatus,
    GovernorInfo,
    GovernorPromotion,
    GovernorStatus,
    GPAdvisorCity,
    GPAdvisorResult,
    GreatPersonInfo,
    LockedCivic,
    LockedTech,
    NearbyResource,
    OwnershipDelta,
    OwnedResource,
    PantheonStatus,
    PathingEstimate,
    PendingDeal,
    PolicyInfo,
    PolicySlot,
    ProductionOption,
    PromotionOption,
    PurchasableTile,
    ReligionBeliefOption,
    ReligionFoundingStatus,
    ReligionInfo,
    ReligionStatus,
    ReligionSummary,
    ResourceStockpile,
    ScoreEntry,
    SettleCandidate,
    SpyInfo,
    StaticMapDump,
    StaticMapTile,
    StrategicMapData,
    TechCivicStatus,
    TechOption,
    TestTradeItem,
    TestTradeResult,
    ThreatInfo,
    TileInfo,
    TradeableCity,
    TradeDestination,
    TraderInfo,
    TradeRouteStatus,
    TurnEvent,
    TurnSnapshot,
    UnclaimedResource,
    UnitInfo,
    UnitPromotionStatus,
    UnitUpgradeInfo,
    SpaceProject,
    VictoryPlayerProgress,
    VictoryProgress,
    VisibleCity,
    WorldCongressStatus,
    WonderPlacement,
)
from civ_mcp.lua.notifications import (  # noqa: F401
    BLOCKING_TOOL_MAP,
    NOTIFICATION_TOOL_MAP,
    build_end_turn,
    build_end_turn_blocking_query,
    build_notifications_query,
    parse_end_turn_blocking,
    parse_notifications_response,
)

# --- Domain module re-exports ---
from civ_mcp.lua.overview import (  # noqa: F401
    build_gameover_check,
    build_gameover_check_gamecore,
    build_overview_query,
    parse_gameover_response,
    parse_overview_response,
)
from civ_mcp.lua.religion import (  # noqa: F401
    build_choose_pantheon,
    build_found_religion,
    build_pantheon_status_query,
    build_religion_beliefs_query,
    build_religion_status_query,
    parse_pantheon_status_response,
    parse_religion_beliefs_response,
    parse_religion_status_response,
)
from civ_mcp.lua.tech import (  # noqa: F401
    build_tech_civics_query,
    parse_tech_civics_response,
)
from civ_mcp.lua.units import (  # noqa: F401
    build_attack_followup_query,
    build_attack_unit,
    build_builder_tasks_query,
    build_combat_estimate_query,
    build_fortify_unit,
    build_pathing_estimate_query,
    build_post_move_visibility_query,
    build_move_unit,
    build_skip_remaining_units,
    build_skip_unit,
    build_cancel_unit_move,
    build_cancel_unit_move_query,
    build_unit_readiness_query,
    build_fog_neighbor_query,
    build_threat_scan_query,
    build_unit_position_query,
    build_units_query,
    diff_threats,
    parse_blocked_diagnostic,
    parse_builder_tasks,
    parse_combat_estimate,
    parse_fog_neighbor_response,
    parse_pathing_estimate,
    parse_post_move_visibility,
    parse_threat_scan_response,
    parse_units_response,
)
from civ_mcp.lua.victory import (  # noqa: F401
    build_victory_progress_query,
    build_victory_proximity_query,
    parse_victory_progress_response,
)

from civ_mcp.lua.envoys import build_send_envoy, build_envoy_query, parse_envoy_confirmation  # noqa: F401
from civ_mcp.lua.tile_purchase import build_purchase_tile, build_tile_purchase_query, parse_tile_purchase_confirmation  # noqa: F401
from civ_mcp.lua.contribution import (  # noqa: F401
    build_contribution_preflight, build_sacrifice_builder_charges, build_contribution_query,
)

from civ_mcp.lua.item_purchase import build_purchase_item, build_item_purchase_query, parse_item_purchase_confirmation  # noqa: F401

from civ_mcp.lua.trader_actions import build_trader_action, build_trader_action_query, parse_trader_confirmation  # noqa: F401

from civ_mcp.lua.great_person_pool import (  # noqa: F401
    build_great_person_pool_action,
    build_great_person_pool_query,
    parse_great_person_pool_confirmation,
)

from civ_mcp.lua.unit_effects import (  # noqa: F401
    build_unit_effect,
    build_unit_effect_query,
    parse_unit_effect_confirmation,
)

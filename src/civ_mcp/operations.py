"""Static command contracts shared by CLI translation and Runtime dispatch.

These declarations never read game state, confirm requests or own pending state.
A read-only command may still confirm an existing request; callers must not infer
pending behaviour from its name or confuse static validation with eligibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Operation:
    readonly: bool = False
    game_io: bool = True
    bounded_read: bool = False
    may_confirm_pending: bool = True
    reject_shared_pending: bool = False
    allowed_pending: frozenset[str] = frozenset()
    destructive: bool = False
    idempotent: bool = False

    def allows_pending(self, domain: str) -> bool:
        # This is dispatch permission only. The original owner still validates
        # identity/intent and may refuse without submitting anything.
        return self.readonly or domain in self.allowed_pending


_READERS = frozenset({
    'get_runtime_status',
    'get_game_snapshot',
    'get_decision_context',
    'get_city_yields',
    'get_city_growth',
    'get_city_workforce',
    'search_knowledge',
    'search_research',
    'get_knowledge',
    'get_public_report',
    'get_game_overview',
    'get_combat_preview',
    'get_units',
    'get_artifact_choices',
    'get_unit_formations',
    'get_spies',
    'get_cities',
    'get_city_production',
    'get_map_area',
    'get_settle_advisor',
    'get_pathing_estimate',
    'get_global_settle_advisor',
    'get_builder_tasks',
    'get_empire_resources',
    'get_strategic_map',
    'get_diplomacy',
    'get_tech_civics',
    'get_pending_trades',
    'get_policies',
    'get_notifications',
    'get_pending_diplomacy',
    'get_governors',
    'get_unit_promotions',
    'get_city_states',
    'get_pantheon_beliefs',
    'get_religion_beliefs',
    'get_dedications',
    'get_trade_options',
    'get_turn_status',
    'get_trade_routes',
    'get_trade_destinations',
    'get_district_advisor',
    'get_wonder_advisor',
    'get_purchasable_tiles',
    'get_great_people',
    'get_gp_advisor',
    'get_world_congress',
    'get_victory_progress',
    'get_religion_defense',
    'get_religion_spread',
    'list_saves'
})
_MUTATIONS = frozenset({
    'set_unit_formation',
    'spy_action',
    'appoint_governor',
    'assign_governor',
    'promote_governor',
    'promote_unit',
    'send_envoy',
    'choose_pantheon',
    'choose_artifact',
    'found_religion',
    'upgrade_unit',
    'choose_dedication',
    'respond_to_trade',
    'negotiate_incoming_trade',
    'propose_trade',
    'propose_peace',
    'set_policies',
    'respond_to_diplomacy',
    'close_diplomacy_session',
    'send_diplomatic_action',
    'form_alliance',
    'city_action',
    'unit_action',
    'skip_remaining_units',
    'set_city_production',
    'purchase_item',
    'set_research',
    'end_turn',
    'purchase_tile',
    'change_government',
    'recruit_great_person',
    'patronize_great_person',
    'reject_great_person',
    'open_world_congress',
    'queue_wc_votes',
    'set_city_focus',
    'dismiss_popup',
    'run_lua',
    'save_game',
    'load_save',
    'load_game_save',
    'reload_game_save',
    'kill_game',
    'return_to_main_menu',
    'launch_game',
    'load_save_from_menu',
    'restart_and_load'
})
_DESTRUCTIVE = frozenset({
    'end_turn',
    'run_lua',
    'load_save',
    'load_game_save',
    'reload_game_save',
    'kill_game',
    'return_to_main_menu',
    'launch_game',
    'load_save_from_menu',
    'restart_and_load'
})
_IDEMPOTENT = frozenset({
    'get_runtime_status',
    'search_knowledge',
    'search_research',
    'get_knowledge'
})
OFFLINE_TOOLS = frozenset({"get_runtime_status", "search_knowledge", "search_research", "get_knowledge"})
STRICT_READERS = frozenset({"get_game_snapshot", "get_decision_context", "get_city_yields",
                            "get_city_growth", "get_city_workforce", "get_combat_preview"})

# Ordinary entity queries can reconcile a retained action; other reads cannot.
_CONFIRMING_READERS = frozenset({"get_units", "get_cities"})
_ALLOWED_PENDING = {
    "congress": {"open_world_congress", "queue_wc_votes", "end_turn", "dismiss_popup", "restart_and_load"},
    "popup": {"dismiss_popup", "restart_and_load"},
    "replacement": {"restart_and_load"},
    "save": {"restart_and_load"},
    "diplomacy": {"end_turn", "test_trade", "propose_trade", "negotiate_incoming_trade", "respond_to_trade",
                  "respond_to_diplomacy", "dismiss_popup", "restart_and_load"},
    "turn": {"end_turn", "respond_to_diplomacy", "respond_to_trade", "negotiate_incoming_trade",
             "queue_wc_votes", "open_world_congress", "dismiss_popup", "restart_and_load"},
    "action": {"restart_and_load", "dismiss_popup"},
}
_INTERNAL_ALIASES = {
    "city_attack": "city_action", "resolve_city_capture": "city_action", "test_trade": "propose_trade",
    "diplomacy_respond": "respond_to_diplomacy", "respond_to_deal": "respond_to_trade",
}
_HOTSEAT = frozenset({"begin_hotseat_turn", "end_hotseat_turn", "hotseat_actions"})


def _declaration(name: str) -> Operation:
    readonly = name in _READERS
    return Operation(
        readonly=readonly, game_io=name not in OFFLINE_TOOLS, bounded_read=readonly,
        may_confirm_pending=not readonly or name in _CONFIRMING_READERS,
        reject_shared_pending=name in STRICT_READERS,
        allowed_pending=frozenset(domain for domain, names in _ALLOWED_PENDING.items() if name in names),
        destructive=name in _DESTRUCTIVE, idempotent=name in _IDEMPOTENT,
    )


OPERATIONS = MappingProxyType({name: _declaration(name) for name in _READERS | _MUTATIONS | _HOTSEAT})
_UNKNOWN = Operation()  # Undeclared diagnostic hooks get no read/permission privileges.


def operation(name: str) -> Operation:
    return OPERATIONS.get(_INTERNAL_ALIASES.get(name, name), _UNKNOWN)


def tool_annotations(name: str) -> dict[str, bool]:
    # Registration is strict: every public tool must deliberately declare its
    # contract. Unknown internal diagnostic hooks remain conservatively mutable.
    spec = OPERATIONS[name]
    result = {}
    if spec.readonly:
        result["readOnlyHint"] = True
    if spec.destructive:
        result["destructiveHint"] = True
    if spec.idempotent:
        result["idempotentHint"] = True
    return result


@dataclass(frozen=True)
class ActionArguments:
    required: tuple[str, ...] = ()
    optional_coordinates: bool = False
    message: str | None = None


UNIT_ACTIONS = MappingProxyType({
    "move": ActionArguments(("target_x", "target_y")),
    "rebase": ActionArguments(("target_x", "target_y")),
    "cancel_move": ActionArguments(), "pillage": ActionArguments(),
    "attack": ActionArguments(("target_x", "target_y")),
    "fortify": ActionArguments(), "skip": ActionArguments(), "found_city": ActionArguments(),
    "improve": ActionArguments(("improvement",), message="improve requires improvement name (e.g. IMPROVEMENT_FARM). To repair a pillaged improvement, use action='repair' instead."),
    "repair": ActionArguments(), "remove_improvement": ActionArguments(), "remove_feature": ActionArguments(),
    "build_route": ActionArguments(), "automate": ActionArguments(), "heal": ActionArguments(),
    "alert": ActionArguments(), "sleep": ActionArguments(), "delete": ActionArguments(),
    "trade_route": ActionArguments(("target_x", "target_y"), message="trade_route requires target_x and target_y of destination city"),
    "excavate": ActionArguments(), "activate": ActionArguments(), "sacrifice_charges": ActionArguments(),
    "teleport": ActionArguments(("target_x", "target_y"), message="teleport requires target_x and target_y of the destination city"),
    "spread_religion": ActionArguments(optional_coordinates=True),
    "launch_inquisition": ActionArguments(), "condemn_heretic": ActionArguments(), "remove_heresy": ActionArguments(),
    "religious_attack": ActionArguments(("target_x", "target_y")),
})
CITY_ACTIONS = MappingProxyType({
    "attack": ActionArguments(("target_x", "target_y")),
    **{name: ActionArguments() for name in ("keep", "reject", "raze", "liberate_founder", "liberate_previous")},
})


def validate_arguments(tool: str, arguments: dict) -> list[dict]:
    """Validate action-specific static conditions after the tool schema.

    Preserve historical case handling (unit: insensitive; city: exact) and
    ignored optional fields. No entity existence, native eligibility or pending
    request is read here. Issues use the same field paths as schema validation.
    """
    actions = UNIT_ACTIONS if tool == "unit_action" else CITY_ACTIONS if tool == "city_action" else None
    if actions is None:
        return []
    raw = arguments.get("action")
    action = raw.lower() if tool == "unit_action" and isinstance(raw, str) else raw
    if not isinstance(action, str) or action not in actions:
        label, suffix = ("action", "Valid") if tool == "unit_action" else ("city action", "Available")
        return [{"path": ["action"], "rule": "enum", "message": f"Unknown {label} '{raw}'. {suffix}: {', '.join(actions)}"}]
    spec = actions[action]
    missing = [field for field in spec.required if arguments.get(field) is None or (field == "improvement" and not arguments.get(field))]
    if missing:
        message = spec.message or f"{action} requires {' and '.join(spec.required)}"
        return [{"path": [field], "rule": "required", "message": message} for field in missing]
    if spec.optional_coordinates and ((arguments.get("target_x") is None) != (arguments.get("target_y") is None)):
        field = "target_x" if arguments.get("target_x") is None else "target_y"
        return [{"path": [field], "rule": "dependentRequired", "message": "Target coordinates must include both target_x and target_y"}]
    return []

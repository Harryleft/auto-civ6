"""Task D1: the Civ adapter has no strategy, recovery, or legacy dependency."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from civ_mcp.civ import adapter
from civ_mcp.runtime.contracts import SendState
from civ_mcp.runtime.transport import Frame, TransportReceipt


def test_adapter_does_not_depend_on_legacy_runtime_or_control_layers() -> None:
    source = Path(adapter.__file__).read_text()
    imports = [
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "civ_mcp.runtime.transport" in imports
    assert not {"civ6_belief_engine", "civ_mcp.game_state", "civ_mcp.game_launcher"} & set(imports)


def test_adapter_command_binds_lua_to_the_selected_discovered_state() -> None:
    assert adapter.CivAdapter._command(8, "return 1") == "CMD:8:return 1"


class _Transport:
    def __init__(self, receipt: TransportReceipt):
        self.receipt = receipt
        self.commands: list[str] = []

    async def execute_read(self, command: str, *, is_complete):
        self.commands.append(command)
        return self.receipt


def _complete(*lines: str) -> TransportReceipt:
    return TransportReceipt(
        send_state=SendState.MAYBE_SENT,
        complete=True,
        frames=tuple(
            Frame(tag=2, payload=f"O\x00GameCore: {line}")
            for line in (*lines, "---END---")
        ),
        connection_usable=True,
    )


def test_overview_is_a_typed_game_fact_with_an_observed_turn() -> None:
    transport = _Transport(
        _complete("42|0|France|Catherine|100|10|5|3|1|Mining|Code of Laws|1|2|100")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_overview())

    assert result.value.turn == 42
    assert result.observed_turn == 42
    assert result.coverage == "CURRENT_GAME:COMPLETE"
    assert transport.commands[0].startswith("CMD:8:")


def test_game_identity_is_a_typed_runtime_identity_not_a_legacy_game_state_value() -> None:
    transport = _Transport(_complete("GAMESEED|CIVILIZATION_FRANCE|425675776"))
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_game_identity())

    assert result.value.value == "civilization_france_425675776"
    assert result.observed_turn is None
    assert transport.commands[0].startswith("CMD:8:")


def test_cities_read_is_bound_to_the_ingame_domain_context() -> None:
    transport = _Transport(_complete())
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_cities(observed_turn=42))

    assert result.value == []
    assert result.observed_turn == 42
    assert transport.commands[0].startswith("CMD:153:")


def test_city_purchase_candidates_are_typed_live_ingame_facts() -> None:
    transport = _Transport(_complete("PURCHASE|UNIT|UNIT_ARCHER|60"))
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(
        civ.read_city_purchases(
            city_id=4, yield_type="YIELD_GOLD", observed_turn=42
        )
    )

    assert result.value[0].item_name == "UNIT_ARCHER"
    assert result.value[0].cost == 60
    assert result.coverage == "CITY_PURCHASE_CANDIDATES:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_city_production_candidates_are_typed_live_ingame_facts() -> None:
    transport = _Transport(_complete("UNIT|UNIT_ARCHER|40|3|60"))
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_city_production(city_id=4, observed_turn=42))

    assert [(item.category, item.item_name) for item in result.value] == [
        ("UNIT", "UNIT_ARCHER")
    ]
    assert result.coverage == "CITY_PRODUCTION_CANDIDATES:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_attack_target_and_combat_readback_are_typed_ingame_facts() -> None:
    target_transport = _Transport(
        _complete("ATTACK_TARGET|2|9|UNIT_WARRIOR|100|100|RANGE")
    )
    civ = adapter.CivAdapter(target_transport, gamecore_state=8, ingame_state=153)
    target = asyncio.run(
        civ.read_attack_target(unit_index=4, target_x=5, target_y=7, observed_turn=42)
    )
    assert (target.value[0].owner_id, target.value[0].unit_index, target.value[1]) == (2, 9, "RANGE")

    readback_transport = _Transport(_complete("UNIT|2|9|UNIT_WARRIOR|80/100"))
    civ = adapter.CivAdapter(readback_transport, gamecore_state=8, ingame_state=153)
    observed = asyncio.run(civ.read_combat_targets(target_x=5, target_y=7, observed_turn=42))
    assert observed.value[0].health == 80


def test_city_attack_target_is_a_typed_live_ingame_fact() -> None:
    transport = _Transport(
        _complete("CITY_ATTACK_TARGET|2|9|UNIT_WARRIOR|100|100")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    target = asyncio.run(
        civ.read_city_attack_target(city_id=4, target_x=5, target_y=7, observed_turn=42)
    )

    assert (target.value.owner_id, target.value.unit_index, target.value.health) == (2, 9, 100)
    assert target.coverage == "CITY_ATTACK_TARGET:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_district_placements_are_typed_live_ingame_facts() -> None:
    transport = _Transport(
        _complete("DISTRICT_PLACEMENT|DISTRICT_HARBOR|5|7")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    placements = asyncio.run(
        civ.read_district_placements(
            city_id=4, district_type="DISTRICT_HARBOR", observed_turn=42
        )
    )

    assert [(item.district_type, item.x, item.y) for item in placements.value] == [
        ("DISTRICT_HARBOR", 5, 7)
    ]
    assert placements.coverage == "DISTRICT_PLACEMENT_CANDIDATES:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_builder_current_tile_candidates_and_tile_state_are_typed_facts() -> None:
    candidate_transport = _Transport(
        _complete("BUILDER_IMPROVEMENT|4|IMPROVEMENT_FARM|5|7|2")
    )
    civ = adapter.CivAdapter(candidate_transport, gamecore_state=8, ingame_state=153)
    candidates = asyncio.run(
        civ.read_builder_improvement_candidates(unit_index=4, observed_turn=42)
    )
    assert [(item.improvement_type, item.x, item.y) for item in candidates.value] == [
        ("IMPROVEMENT_FARM", 5, 7)
    ]
    assert candidates.coverage == "BUILDER_CURRENT_TILE_IMPROVEMENTS:COMPLETE"

    tile_transport = _Transport(
        _complete("TILE_IMPROVEMENT|5|7|IMPROVEMENT_FARM|false")
    )
    civ = adapter.CivAdapter(tile_transport, gamecore_state=8, ingame_state=153)
    tile = asyncio.run(civ.read_tile_improvement_state(x=5, y=7, observed_turn=42))
    assert tile.value.improvement_type == "IMPROVEMENT_FARM"
    assert tile.value.is_pillaged is False
    assert tile.coverage == "TILE_IMPROVEMENT_COORDINATE:COMPLETE"


def test_pending_deals_preserve_exact_structured_terms() -> None:
    transport = _Transport(
        _complete(
            "DEAL|2|Germany|Frederick",
            "ITEM|2|THEM|GOLD|Gold (lump sum)|50|0",
            "ITEM|2|US|RESOURCE|Iron|2|30",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    deals = asyncio.run(civ.read_pending_deals(observed_turn=42))

    assert deals.value[0].other_player_id == 2
    assert deals.value[0].items_from_them[0].amount == 50
    assert deals.value[0].items_from_us[0].name == "Iron"
    assert deals.coverage == "PENDING_DEALS:COMPLETE"


def test_trade_negotiation_is_a_typed_live_deal_manager_fact() -> None:
    transport = _Transport(_complete("TRADE_STATE|2|PROPOSED"))
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    negotiation = asyncio.run(
        civ.read_trade_negotiation(other_player_id=2, observed_turn=42)
    )

    assert negotiation.value.other_player_id == 2
    assert negotiation.value.state.value == "PROPOSED"
    assert negotiation.coverage == "TRADE_NEGOTIATION:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_world_congress_is_a_typed_live_ingame_fact() -> None:
    transport = _Transport(_complete("WC_STATUS|true|0|25|3|0,5,10,15"))
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    congress = asyncio.run(civ.read_world_congress(observed_turn=42))

    assert congress.value.is_in_session is True
    assert congress.value.favor == 25
    assert congress.coverage == "WORLD_CONGRESS:COMPLETE"


def test_climate_is_a_typed_live_ingame_fact() -> None:
    transport = _Transport(
        _complete(
            "CLIMATE|4|Phase IV|31.0|3.0|28.0|28.0|5|-1|12|4|1.4|410.0|60.0|55.0|0.0|Heavy",
            "CLIMATE_RISK|22.0|10.0|15.0|6.0|30.0|12.0|5.0|18|6|4|2|3.0",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    climate = asyncio.run(civ.read_climate_overview(observed_turn=42))

    assert climate.value.phase == 4
    assert climate.value.co2_total == 410.0
    assert climate.coverage == "CLIMATE:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_climate_ruleset_error_is_not_coerced_to_an_empty_fact() -> None:
    transport = _Transport(_complete("ERR:NO_CLIMATE_IN_RULESET"))
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    with pytest.raises(ValueError, match="climate query"):
        asyncio.run(civ.read_climate_overview(observed_turn=42))


def test_spies_are_typed_live_ingame_facts_without_starting_a_mission() -> None:
    transport = _Transport(
        _complete("65536|Artimpasa|4|5|2|30|2|Berlin|1|TRAVEL,COUNTERSPY|none|idle")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    spies = asyncio.run(civ.read_spies(observed_turn=42))

    assert spies.value[0].unit_index == 0
    assert spies.value[0].available_ops == ["TRAVEL", "COUNTERSPY"]
    assert spies.coverage == "SPIES:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_religion_overview_is_a_typed_live_ingame_fact() -> None:
    transport = _Transport(
        _complete(
            "SELF|0|412.0|18.0|RELIGION_CATHOLICISM|RELIGION_CATHOLICISM|BELIEF_RELIGIOUS_IDOLS|-1|2|6",
            "WREL|1|RELIGION_CATHOLICISM|Catholicism|1|France|Paris|BELIEF_GOD_KING|BELIEF_TITHE",
            "RSPAN|RELIGION_CATHOLICISM|24|310",
            "PSTATE|0|Rome|RELIGION_CATHOLICISM|Catholicism|RELIGION_CATHOLICISM|BELIEF_RELIGIOUS_IDOLS",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    religion = asyncio.run(civ.read_religion_overview(observed_turn=42))

    assert religion.value.religions[0].holy_city_name == "Paris"
    assert religion.value.followers == [("RELIGION_CATHOLICISM", 24, 310)]
    assert religion.coverage == "RELIGION_OVERVIEW:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_religion_overview_missing_primary_row_is_not_an_empty_fact() -> None:
    transport = _Transport(_complete("WREL|1|RELIGION_CATHOLICISM|Catholicism|1|France|Paris|None|"))
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    with pytest.raises(adapter.CivReadError, match="missing SELF row"):
        asyncio.run(civ.read_religion_overview(observed_turn=42))


def test_barbarian_overview_preserves_fog_limited_live_facts() -> None:
    transport = _Transport(
        _complete(
            "BARB_CAMP|12,24|revealed|6|3",
            "BARB_UNIT|42|UNIT_WARRIOR|13,25|80/100|20|0|7|2",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    barbarians = asyncio.run(civ.read_barbarian_overview(observed_turn=42))

    assert barbarians.value.camps[0].visibility == "revealed"
    assert barbarians.value.units[0].unit_type == "UNIT_WARRIOR"
    assert barbarians.coverage == (
        "BARBARIAN_CAMPS:REVEALED;BARBARIAN_UNITS:CURRENTLY_VISIBLE"
    )
    assert transport.commands[0].startswith("CMD:8:")


def test_barbarian_overview_allows_an_empty_fog_limited_snapshot() -> None:
    transport = _Transport(_complete())
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    barbarians = asyncio.run(civ.read_barbarian_overview(observed_turn=42))

    assert barbarians.value.camps == []
    assert barbarians.value.units == []


def test_village_overview_preserves_revealed_live_facts() -> None:
    transport = _Transport(
        _complete("VILLAGE|12,24|revealed|none|6|3")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    villages = asyncio.run(civ.read_village_overview(observed_turn=42))

    assert villages.value.huts[0].visibility == "revealed"
    assert villages.value.huts[0].distance_to_city == 6
    assert villages.coverage == "VILLAGES:REVEALED"
    assert transport.commands[0].startswith("CMD:8:")


def test_village_overview_allows_an_empty_revealed_snapshot() -> None:
    transport = _Transport(_complete())
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    villages = asyncio.run(civ.read_village_overview(observed_turn=42))

    assert villages.value.huts == []


def test_city_purchase_candidate_error_is_not_coerced_to_an_empty_result() -> None:
    transport = _Transport(_complete("ERR:CITY_NOT_FOUND"))
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    with pytest.raises(ValueError, match="无法读取购买候选"):
        asyncio.run(
            civ.read_city_purchases(
                city_id=4, yield_type="YIELD_GOLD", observed_turn=42
            )
        )


def test_trade_reads_preserve_live_destination_and_exact_route_identity() -> None:
    destinations_transport = _Transport(
        _complete(
            "TDEST|Berlin|Germany|8,5|0|0|0|0|0|Religion|0|Religion|F0|G0"
        )
    )
    civ = adapter.CivAdapter(destinations_transport, gamecore_state=8, ingame_state=153)

    destinations = asyncio.run(
        civ.read_trade_destinations(unit_index=7, observed_turn=42)
    )

    assert [(item.city_name, item.x, item.y) for item in destinations.value] == [
        ("Berlin", 8, 5)
    ]
    assert destinations.coverage == "TRADE_ROUTE_DESTINATIONS:COMPLETE"
    assert destinations_transport.commands[0].startswith("CMD:153:")

    routes_transport = _Transport(
        _complete(
            "ROUTE|7|Paris|Berlin|Germany|0|0|0|0|0|Religion|0|Religion|F0|G0|2|13|8,5",
            "TRADE_STATUS|2|1|0",
        )
    )
    civ = adapter.CivAdapter(routes_transport, gamecore_state=8, ingame_state=153)

    routes = asyncio.run(civ.read_trade_routes(observed_turn=42))

    trader = routes.value.traders[0]
    assert (trader.unit_id, trader.destination_player_id, trader.destination_city_id) == (
        7,
        2,
        13,
    )
    assert (trader.destination_x, trader.destination_y) == (8, 5)
    assert routes.coverage == "TRADE_ROUTES:COMPLETE"


def test_diplomacy_sessions_are_typed_read_only_facts() -> None:
    transport = _Transport(
        _complete("SESSION|9|2|Germany|Frederick|Greetings|First meeting|Accept;Reject|0")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_diplomacy_sessions(observed_turn=42))

    assert result.value[0].session_id == 9
    assert result.value[0].other_player_id == 2
    assert result.value[0].dialogue_text == "Greetings"
    assert result.coverage == "OPEN_DIPLOMACY_SESSIONS:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_pending_city_capture_and_its_coordinate_state_are_typed_facts() -> None:
    pending_transport = _Transport(
        _complete("PENDING_CITY_CAPTURE|captured|9|Berlin|4|5|7|0|2|3|KEEP;RAZE")
    )
    civ = adapter.CivAdapter(pending_transport, gamecore_state=8, ingame_state=153)

    pending = asyncio.run(civ.read_pending_city_capture(observed_turn=42))

    assert pending.value is not None
    assert pending.value.city_id == 9
    assert pending.value.allowed_choices == ("KEEP", "RAZE")
    assert pending.coverage == "PENDING_CITY_CAPTURE:COMPLETE"
    assert pending_transport.commands[0].startswith("CMD:153:")

    state_transport = _Transport(_complete("CITY_CAPTURE_STATE|9|0"))
    state_civ = adapter.CivAdapter(state_transport, gamecore_state=8, ingame_state=153)

    state = asyncio.run(
        state_civ.read_city_capture_state(x=4, y=5, observed_turn=42)
    )

    assert state.value.city_id == 9
    assert state.value.owner_id == 0
    assert state.coverage == "CITY_CAPTURE_COORDINATE:COMPLETE"
    assert state_transport.commands[0].startswith("CMD:153:")


def test_tech_civics_read_exposes_stable_current_selection_ids() -> None:
    transport = _Transport(
        _complete("CURRENT|Writing|3|Code of Laws|2|TECH_WRITING|CIVIC_CODE_OF_LAWS")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_tech_civics(observed_turn=42))

    assert result.value.current_research_type == "TECH_WRITING"
    assert result.value.current_civic_type == "CIVIC_CODE_OF_LAWS"
    assert result.coverage == "RESEARCH_AND_CIVICS:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_pantheon_read_exposes_current_and_legal_stable_belief_ids() -> None:
    transport = _Transport(
        _complete(
            "STATUS|0|None|None|30.0|25",
            "BELIEF|BELIEF_DIVINE_SPARK|Divine Spark|Great people points",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_pantheon_status(observed_turn=42))

    assert result.value.has_pantheon is False
    assert result.value.pantheon_cost == 25
    assert result.value.available_beliefs[0].belief_type == "BELIEF_DIVINE_SPARK"
    assert result.coverage == "PANTHEON:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_dedications_read_exposes_current_choices_and_active_types() -> None:
    transport = _Transport(
        _complete(
            "STATUS|Golden|2|48|24|40|1",
            "ACTIVE|COMMEMORATION_MONUMENTALITY",
            "CHOICE|4|COMMEMORATION_FREE_INQUIRY|Normal|Golden|Dark",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_dedications(observed_turn=42))

    assert result.value.selections_allowed == 1
    assert result.value.active == ["COMMEMORATION_MONUMENTALITY"]
    assert result.value.choices[0].name == "COMMEMORATION_FREE_INQUIRY"
    assert result.coverage == "DEDICATIONS:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_city_states_read_exposes_envoy_tokens_and_send_eligibility() -> None:
    transport = _Transport(
        _complete("TOKENS|2", "CS|3|Auckland|Trade|1|-1|None|1")
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_city_states(observed_turn=42))

    assert result.value.tokens_available == 2
    assert result.value.city_states[0].player_id == 3
    assert result.value.city_states[0].envoys_sent == 1
    assert result.value.city_states[0].can_send_envoy is True
    assert result.coverage == (
        "ENVOY_TOKENS:COMPLETE;MET_CITY_STATES:COMPLETE;"
        "UNMET_CITY_STATES:UNOBSERVED"
    )
    assert transport.commands[0].startswith("CMD:153:")


def test_governors_read_distinguishes_owned_and_currently_eligible_promotions() -> None:
    transport = _Transport(
        _complete(
            "STATUS|2|1|1",
            "APPOINTED|GOVERNOR_MAGNUS|Magnus|Steward|4|Paris|1|0",
            "GOV_OWNED|GOVERNOR_MAGNUS|GOVERNOR_PROMOTION_PROVISION",
            "GOV_PROMO|GOVERNOR_MAGNUS|GOVERNOR_PROMOTION_SURPLUS_LOGISTICS|Surplus Logistics|Growth|1|0",
            "GOV_PROMO_CANDIDATE|GOVERNOR_MAGNUS|GOVERNOR_PROMOTION_SURPLUS_LOGISTICS|Surplus Logistics|Growth|1|0",
            "AVAILABLE|GOVERNOR_PINGALA|Pingala|Educator|Science and culture",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_governors(observed_turn=42))

    magnus = result.value.appointed[0]
    assert result.value.points_available == 1
    assert magnus.assigned_city_id == 4
    assert magnus.owned_promotions == ["GOVERNOR_PROMOTION_PROVISION"]
    assert magnus.eligible_promotions[0].promotion_type == (
        "GOVERNOR_PROMOTION_SURPLUS_LOGISTICS"
    )
    assert result.value.available_to_appoint[0].governor_type == "GOVERNOR_PINGALA"
    assert result.coverage == "GOVERNORS:COMPLETE;RULESET:EXPANSION_ONLY"
    assert transport.commands[0].startswith("CMD:153:")


def test_governments_read_marks_one_current_unlocked_choice() -> None:
    transport = _Transport(
        _complete(
            "GOV|GOVERNMENT_CHIEFDOM|0|CURRENT|Chiefdom|SLOT_MILITARY,SLOT_ECONOMIC|",
            "GOV|GOVERNMENT_AUTOCRACY|1|AVAILABLE|Autocracy|SLOT_MILITARY,SLOT_ECONOMIC,SLOT_WILDCARD|Wonder production",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_governments(observed_turn=42))

    assert [government.government_type for government in result.value] == [
        "GOVERNMENT_CHIEFDOM",
        "GOVERNMENT_AUTOCRACY",
    ]
    assert result.value[0].is_current is True
    assert result.value[1].slots == (
        "SLOT_MILITARY",
        "SLOT_ECONOMIC",
        "SLOT_WILDCARD",
    )
    assert result.coverage == "UNLOCKED_GOVERNMENTS:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_policies_read_exposes_current_slots_and_per_slot_candidates() -> None:
    transport = _Transport(
        _complete(
            "GOV|GOVERNMENT_CHIEFDOM|Chiefdom|2",
            "SLOT|0|SLOT_MILITARY|NONE|Empty",
            "SLOT|1|SLOT_ECONOMIC|POLICY_URBAN_PLANNING|Urban Planning",
            "POLICY_SLOT|POLICY_AGOGE|0",
            "AVAIL|POLICY_AGOGE|Agoge|Unit production|SLOT_MILITARY",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_policies(observed_turn=42))

    assert result.value.slots[0].current_policy is None
    assert result.value.slots[1].current_policy == "POLICY_URBAN_PLANNING"
    assert result.value.available_policies[0].policy_type == "POLICY_AGOGE"
    assert result.value.available_policies[0].eligible_slots == [0]
    assert result.coverage == "POLICY_SLOTS:COMPLETE;POLICY_CANDIDATES:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_great_people_read_exposes_individual_and_local_claim_state() -> None:
    transport = _Transport(
        _complete(
            "GP_STATUS|0",
            "GP|Great Scientist|Hypatia|Classical|60|Unclaimed|80|Libraries|gold:0,faith:0,recruit:true|17|0",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_great_people(observed_turn=42))

    assert result.value[0].individual_id == 17
    assert result.value[0].can_recruit is True
    assert result.value[0].claimed_by_local is False
    assert result.coverage == "GREAT_PERSON_TIMELINE:COMPLETE"
    assert transport.commands[0].startswith("CMD:153:")


def test_unit_promotions_read_exposes_legal_and_owned_types() -> None:
    transport = _Transport(
        _complete(
            "UNIT|2|2|UNIT_WARRIOR",
            "XP|10|5|1",
            "OWNED|PROMOTION_BATTLECRY",
            "PROMO|PROMOTION_TORTOISE|Tortoise|Ranged defense",
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    result = asyncio.run(civ.read_unit_promotions(unit_index=2, observed_turn=42))

    assert result.value.unit_index == 2
    assert result.value.owned_promotions == ["PROMOTION_BATTLECRY"]
    assert result.value.promotions[0].promotion_type == "PROMOTION_TORTOISE"
    assert result.coverage == "UNIT_PROMOTIONS:COMPLETE"
    assert transport.commands[0].startswith("CMD:8:")


def test_adapter_can_resolve_lua_state_when_a_new_runtime_connection_refreshes_it() -> None:
    transport = _Transport(_complete())
    indexes = {"gamecore": 8, "ingame": 153}
    civ = adapter.CivAdapter(transport, state_resolver=indexes.__getitem__)

    asyncio.run(civ.read_cities(observed_turn=42))
    indexes["ingame"] = 154
    asyncio.run(civ.read_cities(observed_turn=43))

    assert len(transport.commands) == 2
    assert transport.commands[0].startswith("CMD:153:")
    assert transport.commands[1].startswith("CMD:154:")


def test_adapter_state_resolver_also_applies_to_direct_identity_reads() -> None:
    transport = _Transport(_complete("GAMESEED|CIVILIZATION_FRANCE|42"))
    indexes = {"gamecore": 8, "ingame": 153}
    civ = adapter.CivAdapter(transport, state_resolver=indexes.__getitem__)

    asyncio.run(civ.read_game_identity())
    indexes["gamecore"] = 9
    asyncio.run(civ.read_game_identity())

    assert transport.commands[0].startswith("CMD:8:")
    assert transport.commands[1].startswith("CMD:9:")


def test_incomplete_query_is_not_converted_to_an_empty_domain_result() -> None:
    transport = _Transport(
        TransportReceipt(
            send_state=SendState.MAYBE_SENT,
            complete=False,
            frames=(),
            connection_usable=False,
            error=TimeoutError("receipt missing"),
        )
    )
    civ = adapter.CivAdapter(transport, gamecore_state=8, ingame_state=153)

    with pytest.raises(adapter.CivReadError):
        asyncio.run(civ.read_cities(observed_turn=42))

"""Regression tests for the adapter-neutral governance input seam."""

from types import SimpleNamespace

import civ6_belief_engine.governance as governance
from civ6_belief_engine.governance import RulesetCapabilities, TypedTurnSnapshot


def _overview() -> SimpleNamespace:
    return SimpleNamespace(
        turn=12,
        player_id=0,
        ruleset="RULESET_STANDARD",
        num_cities=0,
        num_units=0,
        current_research="NONE",
        current_civic="NONE",
        gold=100.0,
        gold_per_turn=5.0,
        science_yield=10.0,
        culture_yield=8.0,
        faith=0.0,
        score=42,
        total_population=1,
        gold_income=5.0,
        total_maintenance=0.0,
        unit_maintenance=0,
        religions_founded=0,
        religions_max=0,
        explored_land=1,
        total_land=1,
        diplomatic_favor=0,
        favor_per_turn=0,
        era_score=0,
        era_dark_threshold=0,
        era_golden_threshold=0,
    )


def test_typed_turn_snapshot_accepts_adapter_neutral_structural_inputs() -> None:
    rival = SimpleNamespace(
        player_id=1,
        civ_name="Rival",
        leader_name="Leader",
        has_met=False,
        is_at_war=False,
        diplomatic_state="UNKNOWN",
        relationship_score=0,
        grievances=0,
        access_level=0,
        has_delegation=False,
        has_embassy=False,
        alliance_type=None,
        alliance_level=0,
        defensive_pacts=(),
        military_strength=0,
        num_cities=0,
    )

    snapshot = TypedTurnSnapshot(
        snapshot_id="snapshot:adapter-neutral",
        turn=12,
        turn_before=12,
        turn_after=12,
        player_id=0,
        captured_at=1.0,
        capabilities=RulesetCapabilities.standard(),
        overview=_overview(),
        diplomacy=(rival,),
    )

    assert snapshot.overview is not None
    assert snapshot.overview.player_id == 0
    assert snapshot.diplomacy[0].civ_name == "Rival"


def test_governance_public_api_names_typed_adapter_explicitly() -> None:
    assert governance.TypedTurnSnapshot is TypedTurnSnapshot
    assert not hasattr(governance, "TurnSnapshot")

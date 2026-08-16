"""Behavioural tests for automatic belief/prediction derivation.

Every scenario drives the real ``record_tool_result`` path with narrated
tool text copied from narrate.py formats — no live game, no rule bypasses.
"""

from __future__ import annotations

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine


@pytest.fixture
def engine(tmp_path):
    instance = BeliefEngine(run_id="derivation-test", directory=tmp_path)
    instance.bind_game("CIVILIZATION_TEST", 42)
    return instance


def observe(engine, *, tool, result, turn):
    return engine.record_tool_result(
        tool=tool,
        params={},
        result=result,
        turn=turn,
        category="query",
        success=True,
        duration_ms=5,
    )


BARB_TWO_CAMPS = """=== BARBARIAN OVERVIEW ===
Camps (2 revealed):
  [CRITICAL] (12,24) [revealed] — 4 tiles from nearest city; 3 from nearest military
  [WATCH] (30,40) [revealed] — 15 tiles from nearest city; 10 from nearest military
"""

BARB_ONE_CAMP_CLOSER = """=== BARBARIAN OVERVIEW ===
Camps (1 revealed):
  [CRITICAL] (12,24) [revealed] — 2 tiles from nearest city; 1 from nearest military
"""

BARB_NONE = """=== BARBARIAN OVERVIEW ===
No revealed barbarian camps or visible barbarian military units.
Fog note: no result means only that no camp is revealed and no unit is currently visible.
"""


def test_camp_threat_belief_lifecycle(engine):
    observe(engine, tool="get_barbarian_overview", result=BARB_TWO_CAMPS, turn=10)

    critical = engine.get("belief", "auto:belief:camp_threat:12_24")
    watch = engine.get("belief", "auto:belief:camp_threat:30_40")
    assert critical is not None and critical["status"] == "active"
    assert watch is not None and watch["probability"] == 0.5
    assert critical["probability"] == 0.9
    assert "derived" in critical["tags"]
    assert critical["evidence_ids"]

    # Re-observation updates distance and last-seen without duplicating.
    observe(engine, tool="get_barbarian_overview", result=BARB_ONE_CAMP_CLOSER, turn=11)
    critical = engine.get("belief", "auto:belief:camp_threat:12_24")
    assert critical["distance_to_city"] == 2
    assert critical["last_seen_turn"] == 11
    assert len(engine.list("belief", status="active")) == 2

    # Fog gaps under the retire threshold keep the belief alive.
    observe(engine, tool="get_barbarian_overview", result=BARB_NONE, turn=13)
    assert engine.get("belief", "auto:belief:camp_threat:12_24")["status"] == "active"

    # Sustained absence retires the belief as archived with a note.
    observe(engine, tool="get_barbarian_overview", result=BARB_NONE, turn=16)
    retired = engine.get("belief", "auto:belief:camp_threat:12_24")
    assert retired["status"] == "archived"
    assert "cleared" in retired["resolution"] or "fog" in retired["resolution"]

    # A camp seen again after the gap resurrects the same entity.
    observe(engine, tool="get_barbarian_overview", result=BARB_TWO_CAMPS, turn=20)
    assert engine.get("belief", "auto:belief:camp_threat:12_24")["status"] == "active"


TECH_T5 = """Researching: Writing (3 turns) | Completed: 4 techs, 2 civics
Civic: Code of Laws (5 turns)"""

TECH_T9_COMPLETED_SWITCH = """Researching: Pottery (2 turns) | Completed: 5 techs, 2 civics
Civic: Code of Laws (1 turns)"""

TECH_T20_CIVIC_SWITCHED = """Researching: Pottery (4 turns) | Completed: 5 techs, 2 civics
Civic: Foreign Trade (6 turns)"""


def test_research_and_civic_timing_predictions(engine):
    observe(engine, tool="get_tech_civics", result=TECH_T5, turn=5)

    tech = engine.get("prediction", "auto:pred:tech:Writing")
    civic = engine.get("prediction", "auto:pred:civic:Code-of-Laws")
    assert tech is not None and tech["status"] == "active"
    assert tech["predicted_turn"] == 8
    assert tech["deadline_turn"] == 10
    assert tech["evaluation"] == {
        "metric": "research.completed_techs",
        "operator": ">=",
        "value": 5,
    }
    assert civic["predicted_turn"] == 10

    # Writing is gone and the completed counter moved: precise resolution.
    observe(engine, tool="get_tech_civics", result=TECH_T9_COMPLETED_SWITCH, turn=9)
    tech = engine.get("prediction", "auto:pred:tech:Writing")
    assert tech["status"] == "confirmed"
    assert tech["resolution_source"] == "derived"
    assert engine.get("prediction", "auto:pred:tech:Pottery")["status"] == "active"

    # ETA moved for the still-current civic.
    civic = engine.get("prediction", "auto:pred:civic:Code-of-Laws")
    assert civic["predicted_turn"] == 10

    # Civic switched with no completion and past deadline: disconfirmed.
    observe(engine, tool="get_tech_civics", result=TECH_T20_CIVIC_SWITCHED, turn=20)
    civic = engine.get("prediction", "auto:pred:civic:Code-of-Laws")
    assert civic["status"] == "disconfirmed"
    assert civic["resolution_source"] == "derived"


VICTORY_T10 = """Enabled: Science, Domination, Culture, Religion, Score
Disabled: none

SCIENCE VICTORY
  Korea: 10/50 VP | 3 techs

DOMINATION
  Rome: 2/30 VP

VICTORY ASSESSMENT
  No victory imminent.
"""

VICTORY_T20 = VICTORY_T10.replace("Korea: 10/50", "Korea: 20/50")

VICTORY_T60 = VICTORY_T10.replace("Korea: 10/50", "Korea: 50/50")


def test_victory_race_eta_prediction(engine):
    # Rome at 2/30 stays below the noise floor: no prediction for it.
    observe(engine, tool="get_victory_progress", result=VICTORY_T10, turn=10)
    assert engine.get("prediction", "auto:pred:victory:rome:domination") is None

    race = engine.get("prediction", "auto:pred:victory:korea:science")
    assert race is not None and race["status"] == "active"
    assert race["evaluation"] == {
        "metric": "victory.korea.science_vp",
        "operator": ">=",
        "value": 50,
    }

    # Two observations give a rate of 1 VP/turn -> ETA T50.
    observe(engine, tool="get_victory_progress", result=VICTORY_T20, turn=20)
    race = engine.get("prediction", "auto:pred:victory:korea:science")
    assert race["predicted_turn"] == 50
    assert race["last_vp"] == 20

    # Arrival resolves the prediction with the rule's precise source.
    observe(engine, tool="get_victory_progress", result=VICTORY_T60, turn=60)
    race = engine.get("prediction", "auto:pred:victory:korea:science")
    assert race["status"] == "confirmed"


COMBAT_T10 = """Combat Estimate (Melee):
  Warrior (CS:20, HP:100) vs Barbarian Warrior (CS:20, HP:80)
  Modifiers: none
  Est damage to defender: ~40
  Est damage to attacker: ~30
"""

COMBAT_T11_AFTER_ATTACK = """Combat Estimate (Melee):
  Warrior (CS:20, HP:95) vs Barbarian Warrior (CS:20, HP:45)
  Modifiers: none
  Est damage to defender: ~38
  Est damage to attacker: ~25
"""


def test_combat_damage_prediction_resolves_from_next_estimate(engine):
    observe(engine, tool="get_combat_estimate", result=COMBAT_T10, turn=10)
    predictions = engine.list("prediction", status="active")
    assert len(predictions) == 1
    prediction = predictions[0]
    assert prediction["hp_at_estimate"] == 80
    assert prediction["expected_damage"] == 40
    assert prediction["subject"] == "Barbarian Warrior"

    # Defender dropped 80 -> 45: actual 35 within tolerance of ~40.
    observe(engine, tool="get_combat_estimate", result=COMBAT_T11_AFTER_ATTACK, turn=11)
    resolved = engine.get("prediction", prediction["id"])
    assert resolved["status"] == "confirmed"
    assert resolved["actual"]["damage"] == 35
    assert resolved["resolution_source"] == "derived"


COMBAT_T11_MISSED = """Combat Estimate (Melee):
  Warrior (CS:20, HP:95) vs Barbarian Warrior (CS:20, HP:75)
  Modifiers: none
  Est damage to defender: ~38
  Est damage to attacker: ~25
"""


def test_combat_damage_prediction_disconfirms_on_big_miss(engine):
    observe(engine, tool="get_combat_estimate", result=COMBAT_T10, turn=10)
    observe(engine, tool="get_combat_estimate", result=COMBAT_T11_MISSED, turn=11)
    resolved = engine.get("prediction", "auto:pred:combat:Barbarian-Warrior:10")
    assert resolved["status"] == "disconfirmed"
    assert resolved["actual"]["damage"] == 5


def test_derived_entities_are_idempotent_across_repeats(engine):
    observe(engine, tool="get_barbarian_overview", result=BARB_ONE_CAMP_CLOSER, turn=10)
    before = engine.get("belief", "auto:belief:camp_threat:12_24")
    observe(engine, tool="get_barbarian_overview", result=BARB_ONE_CAMP_CLOSER, turn=10)
    after = engine.get("belief", "auto:belief:camp_threat:12_24")
    # Identical evidence must not append journal events (no-op update).
    assert before["version"] == after["version"]
    assert len(engine.list("belief", status="active")) == 1


def test_derivation_never_breaks_on_foreign_tools(engine):
    # Tools without derivation rules record observations untouched.
    observation = observe(engine, tool="get_units", result="Units (1):\n  Warrior at (1,1) [id:7]", turn=3)
    assert observation is not None
    assert engine.list("belief", status="active") == []
    assert engine.list("prediction", status="active") == []


DIPLO_ALERT = """3 civilizations:
  Germany (Frederick) — UNFRIENDLY (-12) **AT WAR** [player 2]
    Cities: 3 (all in fog)
    Military: 150 vs our 100
  Rome (Trajan) — FRIENDLY (+8) [player 5]
    Cities: 4 (all in fog)
    Military: 220 vs our 100
  Persia (Cyrus) — FRIENDLY (+4) [player 7]
    Cities: 2 (all in fog)
    Military: 80 vs our 100
"""

DIPLO_CALM = """3 civilizations:
  Germany (Frederick) — FRIENDLY (+5) [player 2]
    Cities: 3 (all in fog)
    Military: 90 vs our 120
  Rome (Trajan) — FRIENDLY (+8) [player 5]
    Cities: 4 (all in fog)
    Military: 130 vs our 120
  Persia (Cyrus) — FRIENDLY (+4) [player 7]
    Cities: 2 (all in fog)
    Military: 80 vs our 120
"""


def test_rival_military_threat_belief_lifecycle(engine):
    observe(engine, tool="get_diplomacy", result=DIPLO_ALERT, turn=10)

    # At war with 1.5x military -> probability 0.7 (not 0.85: ratio < 2x).
    germany = engine.get("belief", "auto:belief:rival_threat:2")
    assert germany is not None and germany["status"] == "active"
    assert germany["probability"] == 0.7
    assert germany["military_ratio"] == pytest.approx(1.5)
    assert "derived" in germany["tags"]

    # 2.2x military without war -> 0.6.
    rome = engine.get("belief", "auto:belief:rival_threat:5")
    assert rome["probability"] == 0.6
    assert rome["military_ratio"] == pytest.approx(2.2)

    # Below threshold -> no belief.
    assert engine.get("belief", "auto:belief:rival_threat:7") is None

    # Threat resolved (peace + ratio < 1.5x) retires the beliefs.
    observe(engine, tool="get_diplomacy", result=DIPLO_CALM, turn=12)
    assert engine.get("belief", "auto:belief:rival_threat:2")["status"] == "archived"
    assert engine.get("belief", "auto:belief:rival_threat:5")["status"] == "archived"
    assert "resolved" in engine.get("belief", "auto:belief:rival_threat:2")["resolution"]

    # A re-escalation resurrects the same entity.
    observe(engine, tool="get_diplomacy", result=DIPLO_ALERT, turn=13)
    assert engine.get("belief", "auto:belief:rival_threat:2")["status"] == "active"


GP_RACE_T10 = """=== Great People Overview ===
Standings (total points / per turn / received):
  Scientist: YOU 40/3 (1) | Babylon 48/2 (1) | Rome 25/1 (0)
  Writer: YOU 20/1 (0)
Current pool:
  No Great People in timeline.
"""

GP_RACE_T12_WE_LEAD = """=== Great People Overview ===
Standings (total points / per turn / received):
  Scientist: YOU 60/3 (1) | Babylon 48/2 (1) | Rome 25/1 (0)
  Writer: YOU 22/1 (0)
Current pool:
  No Great People in timeline.
"""

GP_RACE_T15_GAP_WIDENS = """=== Great People Overview ===
Standings (total points / per turn / received):
  Scientist: YOU 30/2 (0) | Babylon 90/2 (1) | Rome 25/1 (0)
  Writer: YOU 25/1 (0)
Current pool:
  No Great People in timeline.
"""


def test_great_people_race_belief_lifecycle(engine):
    observe(engine, tool="get_great_people_overview", result=GP_RACE_T10, turn=10)

    # Babylon leads Scientist by 8/48 (gap ratio ~0.17) -> 0.65.
    scientist = engine.get("belief", "auto:belief:gp_race:scientist")
    assert scientist is not None and scientist["status"] == "active"
    assert scientist["leader_name"] == "Babylon"
    assert scientist["lead_gap"] == 8
    assert scientist["probability"] == 0.65

    # Writer class has no rival leader -> no belief.
    assert engine.get("belief", "auto:belief:gp_race:writer") is None

    # We take the lead -> archived.
    observe(engine, tool="get_great_people_overview", result=GP_RACE_T12_WE_LEAD, turn=12)
    assert engine.get("belief", "auto:belief:gp_race:scientist")["status"] == "archived"
    assert "lead" in engine.get("belief", "auto:belief:gp_race:scientist")["resolution"]

    # A rival leads again after we fall behind -> resurrected.
    observe(engine, tool="get_great_people_overview", result=GP_RACE_T10, turn=13)
    assert engine.get("belief", "auto:belief:gp_race:scientist")["status"] == "active"

    # Gap beyond 50% of leader points -> race abandoned, belief archived.
    observe(engine, tool="get_great_people_overview", result=GP_RACE_T15_GAP_WIDENS, turn=15)
    assert engine.get("belief", "auto:belief:gp_race:scientist")["status"] == "archived"
    assert "50%" in engine.get("belief", "auto:belief:gp_race:scientist")["resolution"]

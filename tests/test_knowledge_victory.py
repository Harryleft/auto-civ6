"""Victory and religion queries use the committed local-version fixture, never a live game."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / 'civ6_knowledge'


@pytest.fixture(scope='module')
def victory_root(tmp_path_factory):
    output = tmp_path_factory.mktemp('victory') / 'knowledge'
    result = subprocess.run(
        [sys.executable, str(KNOWLEDGE / 'build.py'), '--source',
         str(KNOWLEDGE / 'sources/snapshot.json'), '--output', str(output)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def query(root, *args):
    result = subprocess.run(
        [sys.executable, str(KNOWLEDGE / 'query.py'), '--root', str(root), *args],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_science_victory_keeps_exoplanet_goal_and_project_technology(victory_root):
    ident = 'MECHANIC_VICTORY_SCIENCE'
    summary = query(victory_root, 'show', ident)
    assert 'rule' not in summary and 'source_refs' not in summary
    facts = [entry['facts'] for entry in query(victory_root, 'show', ident, '--level', 'evidence')['evidence']]
    assert {'Name': 'SCIENCE_VICTORY_POINTS_REQUIRED', 'Value': '50'} in facts
    expedition = next(row for row in facts if row.get('ProjectType') == 'PROJECT_LAUNCH_EXOPLANET_EXPEDITION' and 'PrereqTech' in row)
    assert (expedition['PrereqTech'], expedition['PrereqDistrict']) == ('TECH_SMART_MATERIALS', 'DISTRICT_SPACEPORT')
    victory = query(victory_root, 'show', 'VICTORY_TECHNOLOGY', '--level', 'rule')
    assert victory['rule']['facts']['RequirementSetId'] == 'REQUIREMENTS_EXOPLANET_TECHNOLOGY_VICTORY'


def test_culture_victory_compares_visiting_tourists_against_opponents(victory_root):
    facts = [entry['facts'] for entry in query(victory_root, 'show', 'MECHANIC_VICTORY_CULTURE', '--level', 'evidence')['evidence']]
    assert any(row.get('RequirementId') == 'CULTURE_VICTORY_ENOUGH_TOURISTS_REQUIREMENT'
               and row['RequirementType'] == 'REQUIREMENT_TOURISTS_EXCEED_STAYCATIONERS' for row in facts)
    assert any(row.get('Name') == 'CollectionType' and row['Value'] == 'COLLECTION_MAJOR_OPPOSING_PLAYERS' for row in facts)


def test_domination_targets_original_capitals_instead_of_every_city(victory_root):
    evidence = query(victory_root, 'show', 'MECHANIC_VICTORY_DOMINATION', '--level', 'evidence')
    facts = [entry['facts'] for entry in evidence['evidence']]
    assert any(row.get('RequirementId') == 'CONQUEST_VICTORY_REQUIREMENT'
               and row['RequirementType'] == 'REQUIREMENT_TEAM_DOMINATION_VICTORY' for row in facts)
    paragraph = next(row for row in facts if row.get('Language') == 'en_US'
                     and row.get('Tag') == 'LOC_PEDIA_CONCEPTS_PAGE_VICTORY_2_CHAPTER_CONTENT_PARA_1')
    assert 'all original civilization' in paragraph['Text']


def test_diplomatic_goal_keeps_points_separate_from_favor(victory_root):
    facts = [entry['facts'] for entry in query(victory_root, 'show', 'MECHANIC_VICTORY_DIPLOMATIC', '--level', 'evidence')['evidence']]
    assert {'Name': 'DIPLOMATIC_VICTORY_POINTS_REQUIRED', 'Value': '20'} in facts
    assert any(row.get('RequirementId') == 'DIPLOMATIC_VICTORY_REQUIREMENT'
               and row['RequirementType'] == 'REQUIREMENT_TEAM_DIPLOMATIC_VICTORY' for row in facts)


def test_religious_victory_uses_opponent_majorities_and_distinguishes_pantheon(victory_root):
    facts = [entry['facts'] for entry in query(victory_root, 'show', 'MECHANIC_VICTORY_RELIGIOUS', '--level', 'evidence')['evidence']]
    assert any(row.get('RequirementType') == 'REQUIREMENT_PLAYER_MAJORITY_RELIGION_IS_OWNER' for row in facts)
    assert any(row.get('Name') == 'CollectionType' and row['Value'] == 'COLLECTION_MAJOR_OPPOSING_PLAYERS' for row in facts)
    pantheon = query(victory_root, 'show', 'RELIGION_PANTHEON', '--level', 'rule')['rule']['facts']
    assert pantheon['Pantheon'] == 1


def test_belief_effect_and_spread_pressure_keep_source_link_and_distinct_ranges(victory_root):
    facts = [entry['facts'] for entry in query(victory_root, 'show', 'MECHANIC_VICTORY_BELIEFS_SPREAD', '--level', 'evidence')['evidence']]
    assert {'BeliefType': 'BELIEF_RELIGIOUS_COLONIZATION', 'ModifierID': 'RELIGIOUS_COLONIZATION_AUTO_SPREAD'} in facts
    assert any(row.get('BeliefClassType') == 'BELIEF_CLASS_ENHANCER' and row['MaxInReligion'] == 1 for row in facts)
    assert {'Name': 'RELIGION_SPREAD_ADJACENT_CITY_DISTANCE', 'Value': '10'} in facts
    assert {'Name': 'RELIGION_SPREAD_RANGE_COMBAT_VICTORY', 'Value': '6'} in facts
    belief = query(victory_root, 'show', 'BELIEF_RELIGIOUS_COLONIZATION', '--level', 'rule')['rule']['facts']
    assert belief['BeliefClassType'] == 'BELIEF_CLASS_ENHANCER'

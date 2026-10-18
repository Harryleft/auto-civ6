"""Economy queries use the committed local-version fixture, never a live game."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / 'civ6_knowledge'


@pytest.fixture(scope='module')
def economy_root(tmp_path_factory):
    output = tmp_path_factory.mktemp('economy') / 'knowledge'
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


def test_population_growth_discloses_food_consumption_source(economy_root):
    ident = 'MECHANIC_ECONOMY_GROWTH'
    summary = query(economy_root, 'show', ident)
    assert summary['names']['zh_Hans_CN'] == '城市人口增长'
    assert 'rule' not in summary and 'source_refs' not in summary
    evidence = query(economy_root, 'show', ident, '--level', 'evidence')
    assert {'Name': 'CITY_FOOD_CONSUMPTION_PER_POPULATION', 'Value': '2'} in [
        row['facts'] for row in evidence['evidence']
    ]


def test_housing_rule_exposes_growth_thresholds_and_granary_capacity(economy_root):
    card = query(economy_root, 'show', 'MECHANIC_ECONOMY_HOUSING', '--level', 'rule')
    assert card['rule']['current_facts_needed']
    rows = query(economy_root, 'show', card['id'], '--level', 'evidence')['evidence']
    facts = [row['facts'] for row in rows]
    assert {'Name': 'CITY_HOUSING_LEFT_50PCT_GROWTH', 'Value': '1'} in facts
    assert {'Name': 'CITY_HOUSING_LEFT_25PCT_GROWTH', 'Value': '0'} in facts
    assert any(row.get('BuildingType') == 'BUILDING_GRANARY' and row['Housing'] == 2 for row in facts)


def test_amenity_demand_and_arena_source_are_separate_from_housing(economy_root):
    rows = query(economy_root, 'show', 'MECHANIC_ECONOMY_AMENITIES', '--level', 'evidence')['evidence']
    facts = [row['facts'] for row in rows]
    assert {'Name': 'CITY_POP_PER_AMENITY', 'Value': '2'} in facts
    arena = next(row for row in facts if row.get('BuildingType') == 'BUILDING_ARENA')
    assert (arena['Entertainment'], arena['Housing'], arena['PrereqCivic']) == (2, 0, 'CIVIC_GAMES_RECREATION')


def test_building_comparison_preserves_yield_cost_maintenance_and_river_condition(economy_root):
    rows = query(economy_root, 'show', 'MECHANIC_ECONOMY_PRODUCTION', '--level', 'evidence')['evidence']
    facts = [row['facts'] for row in rows]
    assert {'BuildingType': 'BUILDING_GRANARY', 'YieldChange': 1, 'YieldType': 'YIELD_FOOD'} in facts
    mill = next(row for row in facts if row.get('BuildingType') == 'BUILDING_WATER_MILL' and 'Cost' in row)
    assert (mill['Cost'], mill['Maintenance'], mill['RequiresAdjacentRiver']) == (80, 0, 1)
    granary = query(economy_root, 'show', 'BUILDING_GRANARY', '--level', 'rule')
    assert (granary['rule']['facts']['Cost'], granary['rule']['facts']['PrereqTech']) == (65, 'TECH_POTTERY')


def test_amenity_levels_keep_current_table_values_when_concept_text_is_older(economy_root):
    evidence = query(economy_root, 'show', 'MECHANIC_ECONOMY_AMENITIES', '--level', 'evidence')
    happy = next((entry['facts'] for entry in evidence['evidence']
                  if entry['facts'].get('HappinessType') == 'HAPPINESS_HAPPY'), None)
    assert happy is not None
    assert (happy['MinimumAmenityScore'], happy['MaximumAmenityScore'], happy['NonFoodYieldModifier']) == (3, 4, 10.0)
    rule = query(economy_root, 'show', 'MECHANIC_ECONOMY_AMENITIES', '--level', 'rule')['rule']
    assert any('Happinesses' in item['interpretation'] and '不一致' in item['interpretation']
               for item in rule['conditions'])

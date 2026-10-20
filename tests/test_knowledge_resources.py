"""Resources public CLI checks using the committed local-version fixture only."""
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def query(root, *args):
    result = subprocess.run([
        sys.executable, str(ROOT / 'civ6_knowledge/query.py'),
        '--root', str(root), *args,
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_coal_definition_separates_reveal_from_consumption_and_power(committed_knowledge_root):
    root = committed_knowledge_root
    card = query(root, 'show', 'RESOURCE_COAL', '--level', 'evidence')
    facts = [(row['source_ref']['table'], row['facts']) for row in card['evidence']]
    assert any(table == 'Resources' and row['PrereqTech'] == 'TECH_INDUSTRIALIZATION'
               for table, row in facts)
    assert any(table == 'Resource_Consumption' and row['ImprovedExtractionRate'] == 3
               and row['PowerProvided'] == 4 and row['CO2perkWh'] == 820
               for table, row in facts)
    assert card['profile']['game_version'] == '1.0.12.54 (951533)'
    assert card['profile']['current_session_verified'] is False


def test_flood_evidence_retains_independent_damage_yield_and_frequency_definitions(committed_knowledge_root):
    root = committed_knowledge_root
    flood = query(root, 'show', 'RANDOM_EVENT_FLOOD_MAJOR', '--level', 'evidence')
    facts = [(row['source_ref']['table'], row['facts']) for row in flood['evidence']]
    assert any(table == 'RandomEvent_Damages' and row['DamageType'] == 'POPULATION_LOSS'
               and row['Percentage'] == 15 for table, row in facts)
    assert any(table == 'RandomEvent_Yields' and row['FeatureType'] == 'FEATURE_FLOODPLAINS'
               and row['YieldType'] == 'YIELD_FOOD' and row['Percentage'] == 30
               for table, row in facts)
    assert any(table == 'RandomEvent_Frequencies' and row['RealismSettingType'] == 'REALISM_SETTING_MODERATE'
               and row['OccurrencesPerGame'] == 1.5 for table, row in facts)


def test_barrier_rule_keeps_interpretation_separate_and_evidence_expands_only_on_request(committed_knowledge_root):
    root = committed_knowledge_root
    ident = 'MECHANIC_RESOURCES_BARRIER'
    summary = query(root, 'search', '防洪堤', '--topic', 'resources')['results']
    assert any(card['id'] == ident for card in summary)
    assert all('rule' not in card and 'source_refs' not in card for card in summary)
    card = query(root, 'show', ident, '--level', 'rule')
    assert all(set(condition) == {'interpretation'} for condition in card['rule']['conditions'])
    assert '低地数量、淹没／沉没状态' in card['rule']['current_facts_needed']
    expanded = query(root, 'show', ident, '--level', 'evidence')
    assert any(row['source_ref']['table'] == 'Buildings'
               and row['facts']['PrereqTech'] == 'TECH_COMPUTERS'
               for row in expanded['evidence'])
    assert any(row['source_ref']['table'] == 'Buildings_XP2'
               and row['facts']['BlocksCoastalFlooding'] == 1
               and row['facts']['PreventsFloods'] == 0
               and row['facts']['CostMultiplierPerSeaLevel'] == 1
               for row in expanded['evidence'])

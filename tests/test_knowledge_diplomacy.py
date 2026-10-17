"""Diplomacy public CLI checks use the committed snapshot, not a game install."""
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def build(tmp_path):
    output = tmp_path / 'knowledge'
    result = subprocess.run([
        sys.executable, str(ROOT / 'civ6_knowledge/build.py'),
        '--source', str(ROOT / 'civ6_knowledge/sources/snapshot.json'),
        '--output', str(output),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def query(root, *args):
    result = subprocess.run([
        sys.executable, str(ROOT / 'civ6_knowledge/query.py'),
        '--root', str(root), *args,
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_research_alliance_keeps_both_civic_requirements_and_level_effects(tmp_path):
    root = build(tmp_path)
    action = query(root, 'show', 'DIPLOACTION_ALLIANCE_RESEARCH', '--level', 'evidence')
    assert any(row['source_ref']['table'] == 'DiplomaticActions'
               and row['facts']['InitiatorPrereqCivic'] == 'CIVIC_CIVIL_SERVICE'
               and row['facts']['TargetPrereqCivic'] == 'CIVIC_CIVIL_SERVICE'
               for row in action['evidence'])
    alliance = query(root, 'show', 'ALLIANCE_RESEARCH', '--level', 'evidence')
    assert any(row['source_ref']['table'] == 'AllianceEffects'
               and row['facts']['LevelRequirement'] == 2
               and row['facts']['ModifierID'] == 'ALLIANCE_RESEARCH_AGREEMENT'
               for row in alliance['evidence'])
    assert alliance['profile']['game_version'] == '1.0.12.54 (951533)'
    assert alliance['profile']['current_session_verified'] is False


def test_promise_costs_are_not_collapsed_into_one_diplomatic_penalty(tmp_path):
    root = build(tmp_path)
    card = query(root, 'show', 'DIPLOACTION_KEEP_PROMISE_DONT_CONVERT', '--level', 'evidence')
    assert any(row['source_ref']['table'] == 'DiplomaticActions_XP2'
               and row['facts']['FavorCost'] == 30
               and row['facts']['GrievanceCost'] == 0
               and row['facts']['GrievancesForRefusal'] == 25
               and row['facts']['GrievancesPerIncursion'] == 25
               for row in card['evidence'])


def test_route_guide_exposes_domestic_international_distinction_without_loading_evidence_in_summary(tmp_path):
    root = build(tmp_path)
    ident = 'MECHANIC_DIPLOMACY_ROUTES'
    found = query(root, 'search', '商路资格', '--topic', 'diplomacy')['results']
    assert [card['id'] for card in found] == [ident]
    assert 'rule' not in found[0] and 'source_refs' not in found[0]
    rule = query(root, 'show', ident, '--level', 'rule')['rule']
    assert all(set(condition) == {'interpretation'} for condition in rule['conditions'])
    assert rule['limitations']  # The local encyclopedia capacity caveat must travel with the rule.
    card = query(root, 'show', ident, '--level', 'evidence')
    assert any(row['source_ref']['table'] == 'District_TradeRouteYields'
               and row['facts']['YieldType'] == 'YIELD_SCIENCE'
               and row['facts']['YieldChangeAsDomesticDestination'] == 0.0
               and row['facts']['YieldChangeAsInternationalDestination'] == 1.0
               for row in card['evidence'])
    assert any(row['source_ref']['table'] == 'CivilopediaTexts'
               and row['facts']['Tag'] == 'LOC_PEDIA_CONCEPTS_PAGE_TRADE_3_CHAPTER_CONTENT_PARA_1'
               and row['facts']['Language'] == 'en_US'
               and row['facts']['Source'] == 'user/Cache/DebugLocalization.sqlite'
               for row in card['evidence'])


def test_suzerainty_keeps_competition_condition_with_the_minimum_threshold(tmp_path):
    root = build(tmp_path)
    ident = 'MECHANIC_DIPLOMACY_ENVOYS'
    card = query(root, 'show', ident, '--level', 'evidence')
    assert any(row['source_ref']['table'] == 'GlobalParameters'
               and row['facts']['Name'] == 'INFLUENCE_TOKENS_MINIMUM_FOR_SUZERAIN'
               and row['facts']['Value'] == '3' for row in card['evidence'])
    assert any(row['source_ref']['table'] == 'CivilopediaTexts'
               and row['facts']['Tag'] == 'LOC_PEDIA_CONCEPTS_PAGE_CITYSTATES_5_CHAPTER_CONTENT_PARA_1'
               and row['facts']['Language'] == 'en_US'
               and 'ties are not allowed' in row['facts']['Text']
               for row in card['evidence'])

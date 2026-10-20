"""Combat knowledge through the public build/query CLI; committed local snapshot only."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def command(script, *args):
    result = subprocess.run(
        [sys.executable, str(ROOT / 'civ6_knowledge' / script), *map(str, args)],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def query(root, *args):
    return command('query.py', '--root', root, *args)


def test_unit_base_movement_and_control_do_not_expand_into_summary(committed_knowledge_root):
    warrior = query(committed_knowledge_root, 'show', 'UNIT_WARRIOR', '--level', 'rule')
    assert warrior['topic'] == 'combat'
    assert warrior['rule']['facts']['BaseMoves'] == 2
    assert warrior['rule']['facts']['ZoneOfControl'] == 1
    assert warrior['rule']['facts']['Combat'] == 20
    matches = query(committed_knowledge_root, 'search', '勇士', '--topic', 'combat')['results']
    assert any(card['id'] == 'UNIT_WARRIOR' for card in matches)
    assert all(card['topic'] == 'combat' for card in matches)
    summary = query(committed_knowledge_root, 'show', 'UNIT_WARRIOR')
    assert not {'rule', 'source_refs', 'evidence'} & summary.keys()


def test_terrain_feature_and_promotion_conditions_remain_separate(committed_knowledge_root):
    hills = query(committed_knowledge_root, 'show', 'TERRAIN_GRASS_HILLS', '--level', 'rule')
    forest = query(committed_knowledge_root, 'show', 'FEATURE_FOREST', '--level', 'rule')
    assert hills['rule']['facts']['MovementCost'] == 2
    assert hills['rule']['facts']['DefenseModifier'] == 3
    assert forest['rule']['facts']['MovementChange'] == 1
    assert forest['rule']['facts']['RemoveTech'] == 'TECH_MINING'
    commando = query(committed_knowledge_root, 'show', 'PROMOTION_COMMANDO', '--level', 'evidence')
    prereqs = [row['facts'] for row in commando['evidence'] if row['source_ref']['table'] == 'UnitPromotionPrereqs']
    assert {'UnitPromotion': 'PROMOTION_COMMANDO', 'PrereqUnitPromotion': 'PROMOTION_AMPHIBIOUS'} in prereqs
    assert {'UnitPromotion': 'PROMOTION_COMMANDO', 'PrereqUnitPromotion': 'PROMOTION_BATTLECRY'} in prereqs


def test_siege_evidence_uses_gathering_storm_wall_exceptions(committed_knowledge_root):
    tower = query(committed_knowledge_root, 'show', 'UNIT_SIEGE_TOWER', '--level', 'evidence')
    text = tower['localization']['LOC_UNIT_SIEGE_TOWER_EXPANSION2_DESCRIPTION']['en_US']
    assert 'melee and anti-cavalry' in text
    assert 'Renaissance' in text and 'Urban Defenses' in text
    path = tower['localization_sources']['LOC_UNIT_SIEGE_TOWER_EXPANSION2_DESCRIPTION']['en_US']
    assert path == 'game/Contents/Assets/DLC/Expansion2/Text/en_US/Expansion2_Units_Text.xml'
    assert any(source['path'] == path and len(source['sha256']) == 64 for source in tower['sources'])
    ram = query(committed_knowledge_root, 'show', 'UNIT_BATTERING_RAM', '--level', 'evidence')
    text = ram['localization']['LOC_UNIT_BATTERING_RAM_EXPANSION2_DESCRIPTION']['en_US']
    assert 'melee and anti-cavalry' in text
    assert 'Medieval' in text and 'Renaissance' in text


def test_control_zone_keeps_river_exception_with_traceable_source(committed_knowledge_root):
    ident = 'MECHANIC_COMBAT_CONTROL_ZONE'
    card = query(committed_knowledge_root, 'show', ident, '--level', 'rule')
    assert card['rule']['facts']['origin'] == 'curated_interpretation'
    evidence = query(committed_knowledge_root, 'show', ident, '--level', 'evidence')
    paragraphs = [row['facts'] for row in evidence['evidence'] if row['source_ref']['table'] == 'CivilopediaTexts']
    assert any(row['Text'] == 'Rivers block Zone of Control from all units.' for row in paragraphs)
    assert any('Naval Raider' in row['Text'] and 'Religious units' in row['Text'] for row in paragraphs)
    tag = 'LOC_PEDIA_CONCEPTS_PAGE_MOVEMENT_3_CHAPTER_CONTENT_PARA_2'
    assert evidence['localization_sources'][tag]['en_US'] == 'user/Cache/DebugLocalization.sqlite'
    summary = query(committed_knowledge_root, 'show', ident)
    assert not {'rule', 'source_refs', 'evidence', 'curation_ref'} & summary.keys()


def test_upgrade_and_formation_keep_target_resources_and_different_eligibility(committed_knowledge_root):
    upgrade = query(committed_knowledge_root, 'show', 'MECHANIC_COMBAT_UPGRADE', '--level', 'evidence')
    rows = [row['facts'] for row in upgrade['evidence']]
    assert {'Unit': 'UNIT_WARRIOR', 'UpgradeUnit': 'UNIT_SWORDSMAN'} in rows
    assert any(row.get('UnitType') == 'UNIT_SWORDSMAN' and row.get('ResourceCost') == 20 for row in rows)
    assert any(row.get('UnitType') == 'UNIT_SWORDSMAN' and row.get('StrategicResource') == 'RESOURCE_IRON' for row in rows)
    formation = query(committed_knowledge_root, 'show', 'MECHANIC_COMBAT_FORMATIONS', '--level', 'evidence')
    rows = [row['facts'] for row in formation['evidence']]
    assert any(row.get('UnitType') == 'UNIT_SWORDSMAN' and row.get('CanFormMilitaryFormation') == 1 for row in rows)
    assert any(row.get('UnitType') == 'UNIT_SIEGE_TOWER' and row.get('CanFormMilitaryFormation') == 0 for row in rows)
    assert {'Name': 'COMBAT_CORPS_STRENGTH_MODIFIER', 'Value': '10'} in rows
    assert {'Name': 'COMBAT_ARMY_STRENGTH_MODIFIER', 'Value': '17'} in rows
    assert any('may not be broken apart' in row.get('Text', '') for row in rows)

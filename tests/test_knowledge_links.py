"""Regression coverage at portable knowledge CLI and isolated SQLite boundaries."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from test_knowledge_capture import setup_inputs
from test_knowledge_expansion_core import run, stage

ROOT = Path(__file__).resolve().parents[1] / 'civ6_knowledge'


def prepare(tmp_path, tables, topics=()):
    dist, source = stage(tmp_path)
    for topic in topics:
        (dist / 'topics' / (topic + '.json')).write_text(json.dumps({
            'schema_version': 1, 'topic': topic, 'cards': [],
        }))
    data = json.loads(source.read_text())
    data['tables'].update(tables)
    source.write_text(json.dumps(data))
    return dist, source


def build(dist, source, tmp_path):
    output = tmp_path / 'knowledge'
    result, data = run(dist / 'build.py', '--source', str(source), '--output', str(output))
    assert result.returncode == 0, data
    return output


def show(output, ident, level='evidence'):
    result, data = run(output / 'query.py', 'show', ident, '--level', level)
    assert result.returncode == 0, data
    return data


def test_belief_and_alliance_modifier_id_alias_reaches_effect_arguments(tmp_path):
    dist, source = prepare(tmp_path, {
        'Victories': [], 'Religions': [],
        'Beliefs': [{'BeliefType': 'BELIEF_RELIGIOUS_COLONIZATION'}],
        'DiplomaticActions': [], 'Alliances': [{'AllianceType': 'ALLIANCE_RESEARCH'}],
        'BeliefModifiers': [{'BeliefType': 'BELIEF_RELIGIOUS_COLONIZATION', 'ModifierID': 'RELIGIOUS_COLONIZATION_AUTO_SPREAD'}],
        'AllianceEffects': [{'AllianceType': 'ALLIANCE_RESEARCH', 'LevelRequirement': 2, 'ModifierID': 'ALLIANCE_RESEARCH_AGREEMENT'}],
        'Modifiers': [
            {'ModifierId': 'RELIGIOUS_COLONIZATION_AUTO_SPREAD', 'ModifierType': 'MODIFIER_PLAYER_RELIGION_ENABLE_AUTO_SPREAD'},
            {'ModifierId': 'ALLIANCE_RESEARCH_AGREEMENT', 'ModifierType': 'MODIFIER_ALLIANCE_RESEARCH_AGREEMENT'},
        ],
        'DynamicModifiers': [{'ModifierType': 'MODIFIER_PLAYER_RELIGION_ENABLE_AUTO_SPREAD', 'EffectType': 'EFFECT_ADJUST_RELIGION_AUTO_SPREAD'}],
        'ModifierArguments': [
            {'ModifierId': 'RELIGIOUS_COLONIZATION_AUTO_SPREAD', 'Name': 'Enable', 'Value': '1'},
            {'ModifierId': 'ALLIANCE_RESEARCH_AGREEMENT', 'Name': 'Amount', 'Value': '30'},
        ],
    }, topics=('victory', 'diplomacy'))
    output = build(dist, source, tmp_path)
    belief = show(output, 'BELIEF_RELIGIOUS_COLONIZATION')
    alliance = show(output, 'ALLIANCE_RESEARCH')
    assert any(row['facts'].get('ModifierType') == 'MODIFIER_PLAYER_RELIGION_ENABLE_AUTO_SPREAD' for row in belief['evidence'])
    assert any(row['facts'].get('EffectType') == 'EFFECT_ADJUST_RELIGION_AUTO_SPREAD' for row in belief['evidence'])
    assert any(row['facts'].get('Name') == 'Enable' and row['facts'].get('Value') == '1' for row in belief['evidence'])
    assert any(row['facts'].get('Name') == 'Amount' and row['facts'].get('Value') == '30' for row in alliance['evidence'])


def test_nested_requirement_and_modifier_references_close_without_depth_limit(tmp_path):
    # Five nested sets and a modifier cycle exceed the former three-pass cutoff.
    tables = {
        'Victories': [{'VictoryType': 'VICTORY_TECHNOLOGY', 'RequirementSetId': 'SET_0'}],
        'Religions': [], 'Beliefs': [{'BeliefType': 'BELIEF_TEST'}],
        'BeliefModifiers': [{'BeliefType': 'BELIEF_TEST', 'ModifierID': 'OUTER'}],
        'Modifiers': [{'ModifierId': 'OUTER'}, {'ModifierId': 'INNER', 'SubjectRequirementSetId': 'SET_0'}],
        'ModifierArguments': [
            {'ModifierId': 'OUTER', 'Name': 'ModifierID', 'Value': 'INNER'},
            {'ModifierId': 'INNER', 'Name': 'ModifierId', 'Value': 'OUTER'},
        ],
        'RequirementSets': [{'RequirementSetId': 'SET_' + str(i)} for i in range(5)],
        'RequirementSetRequirements': [{'RequirementSetId': 'SET_' + str(i), 'RequirementId': 'REQ_' + str(i)} for i in range(5)],
        'Requirements': [{'RequirementId': 'REQ_' + str(i), 'RequirementType': 'REQUIREMENT_REQUIREMENTSET_IS_MET'} for i in range(4)] + [
            {'RequirementId': 'REQ_4', 'RequirementType': 'REQUIREMENT_PLAYER_HAS_COMPLETED_PROJECT'},
        ],
        'RequirementArguments': [{'RequirementId': 'REQ_' + str(i), 'Name': 'RequirementSetId', 'Value': 'SET_' + str(i + 1)} for i in range(4)] + [
            {'RequirementId': 'REQ_4', 'Name': 'ProjectType', 'Value': 'PROJECT_LAUNCH_EARTH_SATELLITE'},
            {'RequirementId': 'REQ_4', 'Name': 'RequirementSetId', 'Value': 'SET_0'},
        ],
    }
    dist, source = prepare(tmp_path, tables, topics=('victory',))
    output = build(dist, source, tmp_path)
    for ident in ['VICTORY_TECHNOLOGY', 'BELIEF_TEST']:
        card = show(output, ident)
        refs = [(ref['table'], ref['row']) for ref in card['source_refs']]
        assert len(refs) == len(set(refs))
        assert any(row['facts'].get('Value') == 'PROJECT_LAUNCH_EARTH_SATELLITE' for row in card['evidence'])
        assert len([row for row in card['evidence'] if row['source_ref']['table'] == 'Requirements']) == 5
    belief = show(output, 'BELIEF_TEST')
    assert any(row['facts'].get('ModifierId') == 'INNER' and row['source_ref']['table'] == 'Modifiers' for row in belief['evidence'])


def test_building_card_keeps_terrain_feature_and_specialist_yield_rows(tmp_path):
    dist, source = prepare(tmp_path, {
        'Buildings': [{'BuildingType': ident} for ident in ['BUILDING_ALHAMBRA', 'BUILDING_GREAT_BATH', 'BUILDING_BROADCAST_CENTER']],
        'Building_ValidTerrains': [{'BuildingType': 'BUILDING_ALHAMBRA', 'TerrainType': 'TERRAIN_GRASS_HILLS'}],
        'Building_RequiredFeatures': [{'BuildingType': 'BUILDING_GREAT_BATH', 'FeatureType': 'FEATURE_FLOODPLAINS'}],
        'Building_CitizenYieldChanges': [{'BuildingType': 'BUILDING_BROADCAST_CENTER', 'YieldType': 'YIELD_CULTURE', 'YieldChange': 1}],
    })
    output = build(dist, source, tmp_path)
    expected = [
        ('BUILDING_ALHAMBRA', 'TerrainType', 'TERRAIN_GRASS_HILLS'),
        ('BUILDING_GREAT_BATH', 'FeatureType', 'FEATURE_FLOODPLAINS'),
        ('BUILDING_BROADCAST_CENTER', 'YieldChange', 1),
    ]
    for ident, field, value in expected:
        assert any(row['facts'].get(field) == value for row in show(output, ident)['evidence'])


def capture_fixture(tmp_path, game, user, manifest):
    output = tmp_path / 'captured.json'
    result = subprocess.run([
        sys.executable, str(ROOT / 'capture.py'), '--game-root', str(game),
        '--user-root', str(user), '--steam-manifest', str(manifest), '--output', str(output),
    ], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text())


def test_building_modifier_is_captured_then_expanded_through_public_queries(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    with sqlite3.connect(user / 'Cache/DebugGameplay.sqlite') as conn:
        conn.execute('CREATE TABLE BuildingModifiers (BuildingType TEXT, ModifierId TEXT)')
        conn.execute("INSERT INTO BuildingModifiers VALUES ('BUILDING_TEST','TEST_SCIENCE')")
    captured = capture_fixture(tmp_path, game, user, manifest)
    assert captured['tables']['BuildingModifiers'] == [{'BuildingType': 'BUILDING_TEST', 'ModifierId': 'TEST_SCIENCE'}]
    dist, source = prepare(tmp_path, {
        'Buildings': [{'BuildingType': 'BUILDING_TEST'}],
        'BuildingModifiers': captured['tables']['BuildingModifiers'],
        'Modifiers': [{'ModifierId': 'TEST_SCIENCE', 'ModifierType': 'MODIFIER_BUILDING_YIELD'}],
        'ModifierArguments': [{'ModifierId': 'TEST_SCIENCE', 'Name': 'Amount', 'Value': '7'}],
    })
    output = build(dist, source, tmp_path)
    assert any(row['facts'].get('Name') == 'Amount' and row['facts'].get('Value') == '7' for row in show(output, 'BUILDING_TEST')['evidence'])


def test_coverage_distinguishes_missing_active_associations_from_empty_tables(tmp_path):
    dist, source = prepare(tmp_path, {
        'Units': [], 'UnitPromotions': [{'UnitPromotionType': 'PROMOTION_TEST'}],
        'Terrains': [], 'Features': [], 'UnitPromotionPrereqs': [],
    }, topics=('combat',))
    output = build(dist, source, tmp_path)
    result, data = run(output / 'query.py', 'validate')
    assert result.returncode == 0, data
    coverage = data['coverage']
    assert 'UnitPromotionModifiers' in coverage['missing_optional_tables']
    assert 'ModifierArguments' in coverage['missing_optional_tables']
    assert 'RequirementArguments' in coverage['missing_optional_tables']
    assert 'UnitPromotionPrereqs' not in coverage['missing_optional_tables']
    assert 'UnitPromotionPrereqs' in coverage['empty_optional_tables']


def test_capture_fills_config_translation_and_base_game_text_without_overwrite(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    with sqlite3.connect(user / 'Cache/DebugGameplay.sqlite') as conn:
        conn.execute('CREATE TABLE Victories (VictoryType TEXT, Name TEXT)')
        conn.execute("INSERT INTO Victories VALUES ('VICTORY_DIPLOMATIC','LOC_VICTORY_DIPLOMATIC_NAME')")
        conn.execute('CREATE TABLE UnitPromotions (UnitPromotionType TEXT, Name TEXT)')
        conn.execute("INSERT INTO UnitPromotions VALUES ('PROMOTION_GDR_AA_DEFENSE','LOC_PROMOTION_GDR_AA_DEFENSE_NAME')")
    with sqlite3.connect(user / 'Cache/DebugLocalization.sqlite') as conn:
        conn.executemany('INSERT INTO LocalizedText VALUES (?,?,?)', [
            ('en_US', 'LOC_VICTORY_DIPLOMATIC_NAME', 'Cached Diplomatic Victory'),
            ('zh_Hans_CN', 'LOC_PROMOTION_GDR_AA_DEFENSE_NAME', '缓存无人机防空'),
        ])
    text = game / 'Assets/DLC/Expansion2/Text'
    (text / 'en_US').mkdir(parents=True)
    (text / 'Expansion2_Translations_ConfigText.xml').write_text('''<GameData><LocalizedText>
        <Replace Tag="LOC_VICTORY_DIPLOMATIC_NAME" Language="zh_Hans_CN"><Text>外交胜利</Text></Replace>
        <Replace Tag="LOC_VICTORY_DIPLOMATIC_NAME" Language="en_US"><Text>Do not replace cache</Text></Replace>
        </LocalizedText></GameData>''')
    (text / 'en_US/Expansion2_Promotions_Text.xml').write_text('''<GameData><BaseGameText>
        <Row Tag="LOC_PROMOTION_GDR_AA_DEFENSE_NAME"><Text>Drone Air Defense</Text></Row>
        </BaseGameText></GameData>''')
    captured = capture_fixture(tmp_path, game, user, manifest)
    assert captured['localization']['LOC_VICTORY_DIPLOMATIC_NAME'] == {'zh_Hans_CN': '外交胜利', 'en_US': 'Cached Diplomatic Victory'}
    assert captured['localization']['LOC_PROMOTION_GDR_AA_DEFENSE_NAME'] == {'zh_Hans_CN': '缓存无人机防空', 'en_US': 'Drone Air Defense'}
    path = captured['localization_sources']['LOC_VICTORY_DIPLOMATIC_NAME']['zh_Hans_CN']
    assert path.endswith('/Expansion2_Translations_ConfigText.xml')
    assert any(row['path'] == path and len(row['sha256']) == 64 for row in captured['sources'])


def test_summary_exposes_missing_name_languages_and_keeps_bilingual_search(tmp_path):
    dist, source = prepare(tmp_path, {
        'Victories': [{'VictoryType': 'VICTORY_DIPLOMATIC', 'Name': 'LOC_VICTORY_DIPLOMATIC_NAME'}],
        'Religions': [], 'Beliefs': [], 'Units': [], 'Terrains': [], 'Features': [],
        'UnitPromotions': [
            {'UnitPromotionType': 'PROMOTION_GDR_AA_DEFENSE', 'Name': 'LOC_PROMOTION_GDR_AA_DEFENSE_NAME'},
            {'UnitPromotionType': 'PROMOTION_NO_NAME'},
        ],
    }, topics=('victory', 'combat'))
    data = json.loads(source.read_text())
    data['localization'].update({
        'LOC_VICTORY_DIPLOMATIC_NAME': {'zh_Hans_CN': '外交胜利', 'en_US': 'Diplomatic Victory'},
        'LOC_PROMOTION_GDR_AA_DEFENSE_NAME': {'zh_Hans_CN': '无人机防空'},
    })
    source.write_text(json.dumps(data))
    output = build(dist, source, tmp_path)
    partial = show(output, 'PROMOTION_GDR_AA_DEFENSE', level='summary')
    assert partial['names'] == {'zh_Hans_CN': '无人机防空', 'en_US': '无人机防空'}
    assert partial['summary']['name_fallbacks'] == {'en_US': 'zh_Hans_CN'}
    missing = show(output, 'PROMOTION_NO_NAME', level='summary')
    assert missing['summary']['name_fallbacks'] == {'zh_Hans_CN': 'identifier', 'en_US': 'identifier'}
    assert show(output, 'VICTORY_DIPLOMATIC', level='summary')['summary']['name_fallbacks'] == {}
    for text in ['外交胜利', 'Diplomatic Victory', 'VICTORY_DIPLOMATIC']:
        result, data = run(output / 'query.py', 'search', text, '--topic', 'victory')
        assert result.returncode == 0 and data['results'][0]['id'] == 'VICTORY_DIPLOMATIC'

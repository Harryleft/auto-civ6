"""Civilization abilities through portable build/query and isolated capture inputs."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from test_knowledge_queries import snapshot
from test_knowledge_capture import setup_inputs, capture

ROOT = Path(__file__).resolve().parents[1] / 'civ6_knowledge'


def fixture():
    data = snapshot()
    data['knowledge_topics'] = ['civics', 'technologies', 'districts', 'congress', 'civilizations']
    data['tables'].update({
        'Civilizations': [{'CivilizationType': 'CIVILIZATION_TEST', 'Name': 'LOC_CIV'},
                          {'CivilizationType': 'CIVILIZATION_OTHER', 'Name': 'LOC_OTHER'}],
        'Leaders': [{'LeaderType': 'LEADER_BASE', 'Name': 'LOC_LEADER'},
                    {'LeaderType': 'LEADER_VARIANT', 'Name': 'LOC_LEADER', 'InheritFrom': 'LEADER_BASE'},
                    {'LeaderType': 'LEADER_OTHER', 'Name': 'LOC_OTHER'}],
        'Traits': [{'TraitType': 'TRAIT_CIV', 'Name': 'LOC_ABILITY', 'Description': 'LOC_DESC'},
                   {'TraitType': 'TRAIT_BASE', 'Name': 'LOC_BASE'},
                   {'TraitType': 'TRAIT_VARIANT', 'Name': 'LOC_VARIANT'},
                   {'TraitType': 'TRAIT_OTHER', 'Name': 'LOC_OTHER'}],
        'CivilizationLeaders': [{'CivilizationType': 'CIVILIZATION_TEST', 'LeaderType': 'LEADER_BASE'},
                                {'CivilizationType': 'CIVILIZATION_TEST', 'LeaderType': 'LEADER_VARIANT'},
                                {'CivilizationType': 'CIVILIZATION_OTHER', 'LeaderType': 'LEADER_OTHER'}],
        'CivilizationTraits': [{'CivilizationType': 'CIVILIZATION_TEST', 'TraitType': 'TRAIT_CIV'}],
        'LeaderTraits': [{'LeaderType': 'LEADER_BASE', 'TraitType': 'TRAIT_BASE'},
                         {'LeaderType': 'LEADER_VARIANT', 'TraitType': 'TRAIT_VARIANT'},
                         {'LeaderType': 'LEADER_OTHER', 'TraitType': 'TRAIT_OTHER'}],
        'TraitModifiers': [{'TraitType': 'TRAIT_CIV', 'ModifierId': 'MOD_NEGATIVE'},
                           {'TraitType': 'TRAIT_BASE', 'ModifierId': 'MOD_BASE'},
                           {'TraitType': 'TRAIT_OTHER', 'ModifierId': 'MOD_OTHER'}],
        'Modifiers': [{'ModifierId': 'MOD_NEGATIVE', 'ModifierType': 'MODIFIER_YIELD', 'SubjectRequirementSetId': 'REQSET_RIVER'},
                      {'ModifierId': 'MOD_BASE', 'ModifierType': 'MODIFIER_YIELD'},
                      {'ModifierId': 'MOD_OTHER', 'ModifierType': 'MODIFIER_YIELD'}],
        'ModifierArguments': [{'ModifierId': 'MOD_NEGATIVE', 'Name': 'Amount', 'Value': '-50'},
                              {'ModifierId': 'MOD_BASE', 'Name': 'Amount', 'Value': '1'},
                              {'ModifierId': 'MOD_OTHER', 'Name': 'Amount', 'Value': '999'}],
        'DynamicModifiers': [{'ModifierType': 'MODIFIER_YIELD', 'EffectType': 'EFFECT_ADJUST_YIELD'}],
        'RequirementSets': [{'RequirementSetId': 'REQSET_RIVER', 'RequirementSetType': 'REQUIREMENTSET_TEST_ALL'}],
        'RequirementSetRequirements': [{'RequirementSetId': 'REQSET_RIVER', 'RequirementId': 'REQ_RIVER'}],
        'Requirements': [{'RequirementId': 'REQ_RIVER', 'RequirementType': 'REQUIREMENT_PLOT_ADJACENT_TO_RIVER'}],
        'Units': [{'UnitType': 'UNIT_UNIQUE', 'Name': 'LOC_UNIT', 'TraitType': 'TRAIT_BASE', 'PrereqTech': 'TECH_WRITING'}],
        'Buildings': [{'BuildingType': 'BUILDING_UNIQUE', 'Name': 'LOC_BUILDING', 'TraitType': 'TRAIT_CIV', 'PrereqTech': 'TECH_POTTERY'}],
        'Improvements': [{'ImprovementType': 'IMPROVEMENT_UNIQUE', 'Name': 'LOC_IMPROVEMENT', 'TraitType': 'TRAIT_CIV', 'PrereqCivic': 'CIVIC_CODE_OF_LAWS'}],
        'UnitReplaces': [{'CivUniqueUnitType': 'UNIT_UNIQUE', 'ReplacesUnitType': 'UNIT_BASE'}],
        'BuildingReplaces': [{'CivUniqueBuildingType': 'BUILDING_UNIQUE', 'ReplacesBuildingType': 'BUILDING_BASE'}],
        'DistrictReplaces': [{'CivUniqueDistrictType': 'DISTRICT_CAMPUS', 'ReplacesDistrictType': 'DISTRICT_BASE'}],
    })
    data['tables']['Districts'][0]['TraitType'] = 'TRAIT_CIV'
    data['localization'].update({'LOC_CIV': {'zh_Hans_CN': '样本文明', 'en_US': 'Sample Civilization'},
                                 'LOC_LEADER': {'zh_Hans_CN': '同名领袖', 'en_US': 'Same Name'},
                                 'LOC_ABILITY': {'zh_Hans_CN': '样本能力', 'en_US': 'Sample Ability'},
                                 'LOC_DESC': {'zh_Hans_CN': '满足河流条件时科研减少50%。'}})
    return data


def build(tmp_path, data=None, ok=True):
    distribution = tmp_path / 'distribution'
    if not distribution.exists():
        shutil.copytree(ROOT, distribution, ignore=shutil.ignore_patterns('*.json', '__pycache__'))
        (distribution / 'topics/civilizations.json').write_text(json.dumps({'schema_version': 1, 'topic': 'civilizations', 'cards': []}))
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(data or fixture()))
    result = subprocess.run([sys.executable, '-B', str(distribution / 'build.py'), '--source', str(source), '--output', str(tmp_path / 'knowledge')], text=True, capture_output=True)
    assert result.returncode == (0 if ok else 2), result.stdout + result.stderr
    return tmp_path / 'knowledge' if ok else json.loads(result.stdout)


def query(root, *args):
    result = subprocess.run([sys.executable, '-B', str(root / 'query.py'), '--root', str(root), *args], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_civilization_queries_keep_identity_ownership_effect_conditions_and_sources(tmp_path):
    root = build(tmp_path)
    for name in ('样本文明', 'sAmPlE CiViLiZaTiOn', 'CIVILIZATION_TEST'):
        assert query(root, 'search', name, '--topic', 'civilizations')['results'][0]['id'] == 'CIVILIZATION_TEST'
    summary = query(root, 'show', 'CIVILIZATION_TEST')
    assert summary['summary']['entity_type'] == 'Civilizations'
    assert 'rule' not in summary and 'evidence' not in summary
    card = query(root, 'show', 'CIVILIZATION_TEST', '--level', 'rule')
    assert {trait['id'] for trait in card['rule']['traits']} == {'TRAIT_CIV'}
    assert {leader['id'] for leader in card['rule']['leaders']} == {'LEADER_BASE', 'LEADER_VARIANT'}
    trait = query(root, 'show', 'TRAIT_CIV', '--level', 'rule')
    assert trait['rule']['owners'][0]['id'] == 'CIVILIZATION_TEST'
    facts = [row['facts'] for row in query(root, 'show', 'CIVILIZATION_TEST', '--level', 'evidence')['evidence']]
    assert {'ModifierId': 'MOD_NEGATIVE', 'Name': 'Amount', 'Value': '-50'} in facts
    assert any(row.get('RequirementId') == 'REQ_RIVER' for row in facts)
    assert not any(row.get('ModifierId') == 'MOD_OTHER' or row.get('TraitType') == 'TRAIT_OTHER' for row in facts)
    assert query(root, 'validate')['status'] == 'valid'


def test_variant_inherits_only_its_ancestor_and_keeps_unique_unlocks_and_replacements(tmp_path):
    root = build(tmp_path)
    leader = query(root, 'show', 'LEADER_VARIANT', '--level', 'rule')
    assert {(item['id'], item['owner'], item['ownership']) for item in leader['rule']['traits']} == {
        ('TRAIT_BASE', 'LEADER_BASE', 'inherited'), ('TRAIT_VARIANT', 'LEADER_VARIANT', 'direct')}
    unique = leader['rule']['unique_content'][0]
    assert unique['id'] == 'UNIT_UNIQUE'
    assert unique['prerequisites'] == {'PrereqTech': 'TECH_WRITING'}
    assert unique['replaces'][0]['id'] == 'UNIT_BASE'
    assert len(query(root, 'search', '同名领袖', '--topic', 'civilizations')['results']) == 2
    civ = query(root, 'show', 'CIVILIZATION_TEST', '--level', 'rule')
    assert {item['id'] for item in civ['rule']['unique_content']} == {'BUILDING_UNIQUE', 'DISTRICT_CAMPUS', 'IMPROVEMENT_UNIQUE'}
    assert all(item['id'] != 'UNIT_UNIQUE' for item in civ['rule']['unique_content'])
    facts = [row['facts'] for row in query(root, 'show', 'LEADER_VARIANT', '--level', 'evidence')['evidence']]
    assert {'ModifierId': 'MOD_BASE', 'Name': 'Amount', 'Value': '1'} in facts
    assert not any(row.get('TraitType') == 'TRAIT_OTHER' or row.get('ModifierId') == 'MOD_NEGATIVE' for row in facts)


@pytest.mark.parametrize('fault, expected', [
    ('cycle', 'inheritance cycle'), ('missing_parent', 'missing inherited leader'),
    ('missing_trait', 'dangling trait ownership'), ('missing_leader', 'dangling civilization leader'),
    ('missing_table', 'missing civilization relationship table'),
])
def test_invalid_ownership_or_inheritance_never_silently_produces_abilities(tmp_path, fault, expected):
    data = fixture()
    if fault == 'cycle':
        data['tables']['Leaders'][0]['InheritFrom'] = 'LEADER_VARIANT'
    elif fault == 'missing_parent':
        data['tables']['Leaders'][0]['InheritFrom'] = 'LEADER_ABSENT'
    elif fault == 'missing_trait':
        data['tables']['LeaderTraits'][0]['TraitType'] = 'TRAIT_ABSENT'
    elif fault == 'missing_leader':
        data['tables']['CivilizationLeaders'][0]['LeaderType'] = 'LEADER_ABSENT'
    else:
        del data['tables']['LeaderTraits']
    assert expected in build(tmp_path, data, ok=False)['error']
    assert not (tmp_path / 'knowledge').exists()


def test_unique_building_effects_and_missing_effect_definition_are_observable(tmp_path):
    data = fixture()
    data['tables']['BuildingModifiers'] = [{'BuildingType': 'BUILDING_UNIQUE', 'ModifierId': 'MOD_BUILDING'}]
    data['tables']['ModifierArguments'].append({'ModifierId': 'MOD_BUILDING', 'Name': 'Amount', 'Value': '2'})
    # No Modifiers row: an argument alone must not look like a complete definition.
    root = build(tmp_path, data)
    card = query(root, 'show', 'CIVILIZATION_TEST', '--level', 'rule')
    assert {'field': 'ModifierId', 'id': 'MOD_BUILDING', 'status': 'unknown_missing_definition'} in card['rule']['unresolved_references']
    evidence = query(root, 'show', 'CIVILIZATION_TEST', '--level', 'evidence')
    assert any(row['facts'] == {'ModifierId': 'MOD_BUILDING', 'Name': 'Amount', 'Value': '2'} for row in evidence['evidence'])


def test_capture_keeps_cached_tables_separate_from_conditional_installed_rows(tmp_path):
    game, user, manifest = setup_inputs(tmp_path)
    package = game / 'Assets/DLC/Sample'
    (package / 'Data').mkdir(parents=True)
    (package / 'Text').mkdir()
    (package / 'Sample.modinfo').write_text('''<Mod id="sample-pack"><ActionCriteria><Criteria id="sample"><RuleSetInUse>RULESET_EXPANSION_2</RuleSetInUse></Criteria></ActionCriteria><InGameActions><UpdateDatabase id="data" criteria="sample"><File>Data/Sample.xml</File><File>Data/unsupported.sql</File></UpdateDatabase><UpdateText id="text"><File>Text/Sample.xml</File></UpdateText></InGameActions></Mod>''')
    (package / 'Data/Sample.xml').write_text('''<GameInfo><Civilizations><Row CivilizationType="CIVILIZATION_INSTALLED" Name="LOC_INSTALLED"/></Civilizations><Traits><Row TraitType="TRAIT_INSTALLED" Name="LOC_TRAIT"/></Traits><CivilizationTraits><Row CivilizationType="CIVILIZATION_INSTALLED" TraitType="TRAIT_INSTALLED"/></CivilizationTraits><ModifierArguments><Update><Where ModifierId="MOD_INSTALL" Name="Amount"/><Set Value="100"/></Update></ModifierArguments></GameInfo>''')
    (package / 'Data/unsupported.sql').write_text('UPDATE Traits SET Description="script";')
    (package / 'Text/Sample.xml').write_text('<GameData><LocalizedText><Row Language="zh_Hans_CN" Tag="LOC_INSTALLED"><Text>安装文明</Text></Row></LocalizedText></GameData>')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result, output = capture(tmp_path, game, user, manifest)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    rows = data['tables']['InstalledRuleRows']
    civ = next(row for row in rows if row['table'] == 'Civilizations')
    assert civ['facts']['CivilizationType'] == 'CIVILIZATION_INSTALLED'
    assert civ['scope'] == 'installed_definition_catalog'
    assert civ['operation'] == 'Row' and civ['package'] == 'sample-pack'
    assert civ['criteria'] == 'sample' and 'RULESET_EXPANSION_2' in civ['criteria_definition']
    assert 'Civilizations' not in data['tables']
    update = next(row for row in rows if row['operation'] == 'Update')
    assert update['where']['ModifierId'] == 'MOD_INSTALL' and update['facts']['Value'] == '100'
    assert data['profile']['installed_catalog_gaps'][0]['status'] == 'unsupported_sql_not_applied'
    assert data['localization']['LOC_INSTALLED']['zh_Hans_CN'] == '安装文明'
    assert all(p.read_bytes() == value for p, value in before.items())


def installed_fixture():
    data = fixture()
    context = {'package': 'sample-pack', 'manifest': 'game/Sample.modinfo', 'path': 'game/Data.xml',
               'action': 'data', 'criteria': 'sample', 'criteria_definition': '<Criteria><RuleSetInUse>RULESET_EXPANSION_2</RuleSetInUse></Criteria>',
               'scope': 'installed_definition_catalog', 'operation': 'Row', 'row': 0, 'where': {}}
    records = [
        ('Civilizations', {'CivilizationType': 'CIVILIZATION_INSTALLED', 'Name': 'LOC_INSTALLED'}),
        ('Leaders', {'LeaderType': 'LEADER_INSTALLED', 'Name': 'LOC_INSTALL_LEADER', 'InheritFrom': 'LEADER_BASE'}),
        ('Traits', {'TraitType': 'TRAIT_INSTALLED', 'Name': 'LOC_INSTALL_TRAIT', 'Description': 'LOC_INSTALL_DESC'}),
        ('CivilizationLeaders', {'CivilizationType': 'CIVILIZATION_INSTALLED', 'LeaderType': 'LEADER_INSTALLED'}),
        ('CivilizationTraits', {'CivilizationType': 'CIVILIZATION_INSTALLED', 'TraitType': 'TRAIT_INSTALLED'}),
        ('LeaderTraits', {'LeaderType': 'LEADER_INSTALLED', 'TraitType': 'TRAIT_INSTALLED'}),
        ('TraitModifiers', {'TraitType': 'TRAIT_INSTALLED', 'ModifierId': 'MOD_INSTALLED'}),
        ('Modifiers', {'ModifierId': 'MOD_INSTALLED', 'ModifierType': 'MODIFIER_YIELD'}),
        ('ModifierArguments', {'ModifierId': 'MOD_INSTALLED', 'Name': 'Amount', 'Value': '-50'}),
        ('Buildings', {'BuildingType': 'BUILDING_INSTALLED', 'Name': 'LOC_BUILDING', 'TraitType': 'TRAIT_INSTALLED', 'PrereqTech': 'TECH_WRITING'}),
        ('BuildingReplaces', {'CivUniqueBuildingType': 'BUILDING_INSTALLED', 'ReplacesBuildingType': 'BUILDING_BASE'}),
    ]
    data['tables']['InstalledRuleRows'] = [context | {'table': table, 'facts': facts} for table, facts in records]
    data['localization'].update({'LOC_INSTALLED': {'zh_Hans_CN': '安装文明', 'en_US': 'Installed Civilization'},
                                 'LOC_INSTALL_TRAIT': {'zh_Hans_CN': '安装能力'},
                                 'LOC_INSTALL_LEADER': {'en_US': 'Installed Leader'}})
    return data


def test_installed_catalog_is_searchable_but_never_claims_applied_or_overwrites_cache(tmp_path):
    data = installed_fixture()
    context = data['tables']['InstalledRuleRows'][0]
    data['tables']['InstalledRuleRows'].append(context | {'table': 'Civilizations', 'facts': {'CivilizationType': 'CIVILIZATION_TEST', 'Name': 'LOC_INSTALLED'}})
    data['tables']['InstalledRuleRows'].append(context | {'table': 'ModifierArguments', 'operation': 'Update', 'where': {'ModifierId': 'MOD_INSTALLED'}, 'facts': {'Value': '100'}})
    root = build(tmp_path, data)
    search = query(root, 'search', '安装文明', '--topic', 'civilizations')
    assert [row['id'] for row in search['results']] == ['CIVILIZATION_INSTALLED']
    summary = query(root, 'show', 'CIVILIZATION_INSTALLED')
    assert summary['summary']['source_scope'] == 'installed_definition_catalog'
    assert summary['summary']['applied'] is False and summary['summary']['ownership_verified'] is False
    assert summary['summary']['conditions_evaluated'] is False
    rule = query(root, 'show', 'CIVILIZATION_INSTALLED', '--level', 'rule')['rule']
    assert rule['traits'][0]['id'] == 'TRAIT_INSTALLED'
    assert rule['unique_content'][0]['id'] == 'BUILDING_INSTALLED'
    assert rule['unique_content'][0]['replaces'][0]['id'] == 'BUILDING_BASE'
    assert rule['conditions'][0]['criteria'] == 'sample'
    assert rule['source_scope'] == 'installed_definition_catalog' and rule['applied'] is False
    evidence = query(root, 'show', 'CIVILIZATION_INSTALLED', '--level', 'evidence')
    assert evidence['source_scope'] == 'installed_definition_catalog' and evidence['applied'] is False
    rows = [entry['facts'] for entry in evidence['evidence']]
    assert any(row.get('facts', {}).get('Value') == '-50' and row['operation'] == 'Row' for row in rows)
    assert any(row.get('facts', {}).get('Value') == '100' and row['operation'] == 'Update' for row in rows)
    cached = query(root, 'show', 'CIVILIZATION_TEST', '--level', 'rule')
    assert cached['names']['zh_Hans_CN'] == '样本文明'
    assert cached['rule']['source_scope'] == 'local_cached_rules_snapshot'
    assert query(root, 'validate')['status'] == 'valid'


def test_installed_missing_or_cyclic_parent_is_unknown_and_other_package_does_not_fill_it(tmp_path):
    data = installed_fixture()
    rows = data['tables']['InstalledRuleRows']
    rows[1]['facts']['InheritFrom'] = 'LEADER_UNKNOWN'
    rows.append(rows[1] | {'package': 'unrelated-pack', 'facts': {'LeaderType': 'LEADER_UNKNOWN', 'Name': 'LOC_OTHER', 'InheritFrom': 'LEADER_INSTALLED'}})
    root = build(tmp_path, data)
    leader = query(root, 'show', 'LEADER_INSTALLED', '--level', 'rule')
    assert any(row['id'] == 'LEADER_UNKNOWN' and row['status'] == 'unknown_missing_definition' for row in leader['rule']['unresolved_references'])
    assert not any(row['facts'].get('package') == 'unrelated-pack' for row in leader['rule']['related_records'])
    # Within the package, a cycle is also exposed instead of cut off silently.
    rows[-1]['package'] = 'sample-pack'
    root = build(tmp_path, data)
    leader = query(root, 'show', 'LEADER_INSTALLED', '--level', 'rule')
    assert any(row['status'] == 'unknown_inheritance_cycle' for row in leader['rule']['unresolved_references'])


def test_installed_evidence_keeps_localized_ability_sources_and_partial_definition_gaps(tmp_path):
    data = installed_fixture()
    data['localization']['LOC_INSTALL_DESC'] = {'zh_Hans_CN': '安装定义中的科研减益'}
    data['localization_sources'] = {'LOC_INSTALL_DESC': {'zh_Hans_CN': 'game/Text.xml'}}
    data['profile']['installed_catalog_gaps'] = [{'package': 'sample-pack', 'path': 'game/extra.sql', 'status': 'unsupported_sql_not_applied'}]
    root = build(tmp_path, data)
    rule = query(root, 'show', 'CIVILIZATION_INSTALLED', '--level', 'rule')['rule']
    assert rule['traits'][0]['description']['zh_Hans_CN'] == '安装定义中的科研减益'
    assert rule['coverage_gaps'][0]['status'] == 'unsupported_sql_not_applied'
    evidence = query(root, 'show', 'CIVILIZATION_INSTALLED', '--level', 'evidence')
    assert evidence['localization']['LOC_INSTALL_DESC']['zh_Hans_CN'] == '安装定义中的科研减益'
    assert evidence['localization_sources']['LOC_INSTALL_DESC']['zh_Hans_CN'] == 'game/Text.xml'


def test_installed_inheritance_labels_cache_references_as_unapplied_context(tmp_path):
    root = build(tmp_path, installed_fixture())
    leader = query(root, 'show', 'LEADER_INSTALLED', '--level', 'rule')
    assert {item['id'] for item in leader['rule']['traits']} == {'TRAIT_INSTALLED', 'TRAIT_BASE'}
    evidence = query(root, 'show', 'LEADER_INSTALLED', '--level', 'evidence')
    cache_rows = [row for row in evidence['evidence'] if row['source_ref']['table'] != 'InstalledRuleRows']
    assert cache_rows and all(row['source_scope'] == 'cached_reference_only' for row in cache_rows)
    installed_rows = [row for row in evidence['evidence'] if row['source_ref']['table'] == 'InstalledRuleRows']
    assert all(row['source_scope'] == 'installed_definition_catalog' for row in installed_rows)


def test_reverse_replacement_does_not_attach_another_civilizations_unique_unit(tmp_path):
    data = fixture()
    data['tables']['UnitReplaces'].append({'CivUniqueUnitType': 'UNIT_OTHER_UNIQUE', 'ReplacesUnitType': 'UNIT_UNIQUE'})
    root = build(tmp_path, data)
    evidence = query(root, 'show', 'LEADER_VARIANT', '--level', 'evidence')
    assert not any(row['facts'].get('CivUniqueUnitType') == 'UNIT_OTHER_UNIQUE' for row in evidence['evidence'])


def test_missing_localization_keeps_identifier_fallback_instead_of_invented_ability(tmp_path):
    data = fixture()
    data['localization'].pop('LOC_ABILITY')
    root = build(tmp_path, data)
    card = query(root, 'show', 'TRAIT_CIV')
    assert card['names'] == {'zh_Hans_CN': 'TRAIT_CIV', 'en_US': 'TRAIT_CIV'}
    assert card['summary']['name_fallbacks'] == {'zh_Hans_CN': 'identifier', 'en_US': 'identifier'}


def test_installed_same_id_from_multiple_packages_is_unknown_instead_of_combined(tmp_path):
    data = installed_fixture()
    first = data['tables']['InstalledRuleRows'][0]
    first['facts']['Description'] = 'LOC_FIRST_DESCRIPTION'
    data['localization']['LOC_FIRST_DESCRIPTION'] = {'zh_Hans_CN': '第一包的文明说明'}
    data['localization']['LOC_SECOND_DESCRIPTION'] = {'zh_Hans_CN': '第二包的文明说明'}
    data['tables']['InstalledRuleRows'].append(first | {'package': 'another-pack', 'facts': {'CivilizationType': 'CIVILIZATION_INSTALLED', 'Name': 'LOC_OTHER_NAME', 'Description': 'LOC_SECOND_DESCRIPTION'}})
    root = build(tmp_path, data)
    card = query(root, 'show', 'CIVILIZATION_INSTALLED', '--level', 'rule')
    assert card['names']['zh_Hans_CN'] == 'CIVILIZATION_INSTALLED'
    assert card['summary']['description'] == ''
    assert card['rule']['traits'] == [] and card['rule']['unique_content'] == []
    assert {'field': 'package', 'id': 'CIVILIZATION_INSTALLED', 'status': 'unknown_ambiguous_definition'} in card['rule']['unresolved_references']

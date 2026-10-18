#!/usr/bin/env python3
"""Build a portable, deterministic knowledge directory from an offline snapshot."""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import shutil
import sys

PRIMARY = {'Civics': ('civics', 'CivicType'), 'Technologies': ('technologies', 'TechnologyType'), 'Districts': ('districts', 'DistrictType'), 'Resolutions': ('congress', 'ResolutionType'), 'ProposalTypes': ('congress', 'ProposalType'), 'VotingBlockers': ('congress', 'VotingBlockerType')}
UNLOCKS = {'Units': 'UnitType', 'Buildings': 'BuildingType', 'Districts': 'DistrictType', 'Improvements': 'ImprovementType', 'Projects': 'ProjectType', 'Policies': 'PolicyType', 'Governments': 'GovernmentType', 'Resources': 'ResourceType', 'Routes': 'RouteType'}
CORE_RELATIONSHIPS = ('TechnologyPrereqs', 'CivicPrereqs', 'Boosts', 'District_Adjacencies', 'Adjacency_YieldChanges')
OPTIONAL_TABLES = tuple(UNLOCKS) + ('Districts_XP2', 'District_RequiredFeatures', 'District_ValidTerrains', 'DistrictReplaces', 'District_CitizenYieldChanges', 'GovernmentSlots', 'Government_SlotCounts', 'ResolutionEffects', 'CivilizationTraits', 'LeaderTraits', 'CivilopediaPages', 'CivilopediaPageChapterParagraphs', 'GlobalParameters')
BASE_SUPPORT_FILES = ('README.md', 'NEXT.md', 'capture.py', 'build.py', 'query.py', 'topics/civics.md', 'topics/technologies.md', 'topics/districts.md', 'topics/congress.md')

TOPIC_PRIMARY = {
    'civilizations': {'Civilizations': 'CivilizationType', 'Leaders': 'LeaderType', 'Traits': 'TraitType'},
    'economy': {'Yields': 'YieldType', 'Buildings': 'BuildingType', 'Policies': 'PolicyType'},
    'resources': {'Resources': 'ResourceType', 'RandomEvents': 'RandomEventType'},
    'combat': {'Units': 'UnitType', 'UnitPromotions': 'UnitPromotionType', 'Terrains': 'TerrainType', 'Features': 'FeatureType'},
    'diplomacy': {'DiplomaticActions': 'DiplomaticActionType', 'Alliances': 'AllianceType'},
    'victory': {'Victories': 'VictoryType', 'Religions': 'ReligionType', 'Beliefs': 'BeliefType'},
}
TOPIC_NAMES = ('civics', 'technologies', 'districts', 'congress', *TOPIC_PRIMARY)
ENTITY_RECORDS = {
    'Traits': ('TraitModifiers',),
    'Improvements': ('ImprovementModifiers', 'Improvement_YieldChanges', 'Improvement_ValidResources'),
    'Buildings': ('BuildingModifiers', 'Buildings_XP2', 'Building_YieldChanges', 'Building_YieldChangesBonusWithPower', 'BuildingPrereqs', 'Building_ResourceCosts', 'Building_ValidTerrains', 'Building_RequiredFeatures', 'Building_CitizenYieldChanges'),
    'Policies': ('PolicyModifiers',),
    'Resources': ('Resource_Consumption', 'Resource_YieldChanges', 'Resource_Harvests', 'Resource_ValidTerrains', 'Resource_ValidFeatures'),
    'RandomEvents': ('RandomEvent_Damages', 'RandomEvent_Yields', 'RandomEvent_Terrains', 'RandomEvent_Features', 'RandomEvent_Frequencies', 'RandomEvent_PillagedBuildings', 'RandomEvent_PillagedDistricts', 'RandomEvent_PillagedImprovements', 'RandomEvent_DamagedUnits'),
    'Units': ('Units_XP2', 'UnitUpgrades', 'Unit_BuildingPrereqs', 'UnitReplaces', 'TypeTags'),
    'UnitPromotions': ('UnitPromotionPrereqs', 'UnitPromotionModifiers'),
    'Terrains': ('Terrain_YieldChanges',),
    'Features': ('Feature_YieldChanges', 'Features_XP2', 'Feature_Floodplains', 'Feature_UnitMovements'),
    'DiplomaticActions': ('DiplomaticActions_XP1', 'DiplomaticActions_XP2', 'DiplomaticStateActions'),
    'Alliances': ('AllianceEffects',),
    'Beliefs': ('BeliefModifiers',),
}
REFERENCE_TABLES = {
    'ModifierId': ('Modifiers', 'ModifierArguments'),
    'ModifierType': ('DynamicModifiers',),
    'RequirementSetId': ('RequirementSets', 'RequirementSetRequirements'),
    'RequirementId': ('Requirements', 'RequirementArguments'),
}


def reference_index(tables):
    result = {key: {} for key in REFERENCE_TABLES}
    for key, sources in REFERENCE_TABLES.items():
        aliases = ('ModifierId', 'ModifierID') if key == 'ModifierId' else (key,)
        for table in sources:
            for index, row in enumerate(tables.get(table, [])):
                for alias in aliases:
                    if row.get(alias):
                        result[key].setdefault(row[alias], []).append((table, index))
    return result


def definitions(root):
    result = {}
    for topic in TOPIC_PRIMARY:
        path = root / 'topics' / (topic + '.json')
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('schema_version') != 1 or data.get('topic') != topic or not isinstance(data.get('cards'), list):
            raise ValueError('invalid topic definition: ' + topic)
        result[topic] = data['cards']
    return result


def support_files(root):
    return BASE_SUPPORT_FILES + tuple(name for topic in definitions(root) for name in ('topics/' + topic + '.md', 'topics/' + topic + '.json'))


def related_records(card, tables, attach, references):
    # These are database references, not proof that an effect applies in this game.
    for table in ENTITY_RECORDS.get(card['summary']['entity_type'], ()):
        for index, row in enumerate(tables.get(table, [])):
            if card['id'] in row.values():
                attach(card, table, index)
    # Follow explicit references only. A visited row set permits nested and
    # cyclic source data without a depth cutoff or evaluating any requirement.
    pending = deque((ref['table'], ref['row']) for ref in card['source_refs'])
    visited = set()
    while pending:
        table, index = pending.popleft()
        if (table, index) in visited:
            continue
        visited.add((table, index))
        row = tables[table][index]
        targets = []
        for key, value in row.items():
            if key == 'ModifierID':
                key = 'ModifierId'
            elif key.endswith('RequirementSetId'):
                key = 'RequirementSetId'
            if key in references and value:
                targets.append((key, value))
        argument = row.get('Name')
        if table in ('ModifierArguments', 'RequirementArguments') and argument in ('ModifierId', 'ModifierID', 'RequirementSetId', 'RequirementId') and row.get('Value'):
            targets.append(('ModifierId' if argument == 'ModifierID' else argument, row['Value']))
        for key, value in targets:
            if card['topic'] == 'civilizations' and not any(table == REFERENCE_TABLES[key][0] for table, _ in references[key].get(value, [])):
                missing = {'field': key, 'id': value, 'status': 'unknown_missing_definition'}
                if missing not in card['rule']['unresolved_references']:
                    card['rule']['unresolved_references'].append(missing)
            for target_table, target_index in references[key].get(value, []):
                attach(card, target_table, target_index)
                if (target_table, target_index) not in visited:
                    pending.append((target_table, target_index))
    card['rule']['related_records'] = [{'source_ref': ref, 'facts': tables[ref['table']][ref['row']]} for ref in card['source_refs'][1:]]


def civilization_records(topics, cards, tables, snapshot, attach, relation):
    """Follow explicit ownership; a civilization never acquires every leader's traits."""
    if 'civilizations' not in topics:
        return
    leaders = {row['LeaderType']: row for row in tables['Leaders']}
    traits = {row['TraitType']: index for index, row in enumerate(tables['Traits'])}
    civs = {row['CivilizationType'] for row in tables['Civilizations']}
    chains = {}
    for ident in leaders:
        chain = []
        current = ident
        while current:
            if current in chain:
                raise ValueError('leader inheritance cycle: ' + ' -> '.join([*chain, current]))
            if current not in leaders:
                raise ValueError('missing inherited leader: ' + current)
            chain.append(current)
            current = leaders[current].get('InheritFrom')
        chains[ident] = chain
    for table, key, known in [('CivilizationTraits', 'CivilizationType', civs),
                              ('LeaderTraits', 'LeaderType', leaders)]:
        if table not in tables:
            raise ValueError('missing civilization relationship table: ' + table)
        for row in tables[table]:
            if row.get(key) not in known or row.get('TraitType') not in traits:
                raise ValueError('dangling trait ownership: ' + table)
    if 'CivilizationLeaders' not in tables or 'TraitModifiers' not in tables:
        raise ValueError('missing civilization relationship table: CivilizationLeaders or TraitModifiers')
    for row in tables['CivilizationLeaders']:
        if row.get('LeaderType') not in leaders or row.get('CivilizationType') not in civs:
            raise ValueError('dangling civilization leader')
    for row in tables['TraitModifiers']:
        if row.get('TraitType') not in traits:
            raise ValueError('dangling trait modifier')
    replacements = {'Units': ('UnitReplaces', 'CivUniqueUnitType', 'ReplacesUnitType'),
                    'Buildings': ('BuildingReplaces', 'CivUniqueBuildingType', 'ReplacesBuildingType'),
                    'Districts': ('DistrictReplaces', 'CivUniqueDistrictType', 'ReplacesDistrictType')}
    for card in topics['civilizations']:
        ident = card['id']
        rule = card['rule']
        rule.update({'traits': [], 'leaders': [], 'civilizations': [], 'owners': [],
                     'unique_content': [], 'unresolved_references': [], 'source_scope': 'local_cached_rules_snapshot'})
        card['summary']['source_scope'] = 'local_cached_rules_snapshot'
        rule['limitations'] += ['文明与领袖定义不证明本局参加、资料片拥有或启用；对手隐藏议程和意图未知。',
                                '继承与能力定义不求值；脚本条件、实际收益和本局覆盖需查询游戏。']
        entity = card['summary']['entity_type']
        for index, row in enumerate(tables['CivilizationLeaders']):
            if row.get('CivilizationType') == ident or row.get('LeaderType') == ident:
                ref = attach(card, 'CivilizationLeaders', index)
                target = row['LeaderType'] if entity == 'Civilizations' else row['CivilizationType']
                rule['leaders' if entity == 'Civilizations' else 'civilizations'].append({'id': target, 'source_ref': ref})
                relation(card, 'leader' if entity == 'Civilizations' else 'civilization', target, ref)
        lineage = chains.get(ident, [ident])
        if entity == 'Leaders':
            for ancestor in lineage[1:]:
                ancestor_index = next(i for i, row in enumerate(tables['Leaders']) if row['LeaderType'] == ancestor)
                ref = attach(card, 'Leaders', ancestor_index)
                relation(card, 'inherits_leader', ancestor, ref)
        selected_traits = set()
        for table, owner_key in [('CivilizationTraits', 'CivilizationType'), ('LeaderTraits', 'LeaderType')]:
            for index, row in enumerate(tables[table]):
                owner, trait = row[owner_key], row['TraitType']
                owns = owner in lineage if entity == 'Leaders' and table == 'LeaderTraits' else owner == ident
                if owns or (entity == 'Traits' and trait == ident):
                    ref = attach(card, table, index)
                    if entity == 'Traits':
                        rule['owners'].append({'id': owner, 'source_ref': ref})
                        relation(card, 'owned_by', owner, ref)
                    else:
                        definition = tables['Traits'][traits[trait]]
                        attach(card, 'Traits', traits[trait])
                        selected_traits.add(trait)
                        rule['traits'].append({'id': trait, 'owner': owner,
                                               'ownership': 'direct' if owner == ident else 'inherited',
                                               'inherited_via': lineage[:lineage.index(owner)] if owner != ident else [],
                                               'names': names(snapshot, definition.get('Name'), trait),
                                               'description': names(snapshot, definition.get('Description'), ''), 'source_ref': ref})
                        relation(card, 'trait' if owner == ident else 'inherited_trait', trait, ref)
        if entity == 'Traits':
            selected_traits.add(ident)
        for index, row in enumerate(tables['TraitModifiers']):
            if row['TraitType'] in selected_traits:
                attach(card, 'TraitModifiers', index)
        for table, id_key in [('Units', 'UnitType'), ('Buildings', 'BuildingType'),
                              ('Districts', 'DistrictType'), ('Improvements', 'ImprovementType')]:
            for index, row in enumerate(tables.get(table, [])):
                if row.get('TraitType') not in selected_traits:
                    continue
                ref = attach(card, table, index)
                for related_table in ENTITY_RECORDS.get(table, ()):
                    for related_index, related_row in enumerate(tables.get(related_table, [])):
                        related_key = {'UnitReplaces': 'CivUniqueUnitType', 'TypeTags': 'Type'}.get(related_table, id_key)
                        if related_row.get(related_key) == row[id_key]:
                            attach(card, related_table, related_index)
                content = {'id': row[id_key], 'entity_type': table, 'trait': row['TraitType'],
                           'names': names(snapshot, row.get('Name'), row[id_key]), 'source_ref': ref,
                           'prerequisites': {key: row[key] for key in ('PrereqTech', 'PrereqCivic') if row.get(key)}, 'replaces': []}
                if table in replacements:
                    replace_table, unique_key, target_key = replacements[table]
                    for replace_index, replacement in enumerate(tables.get(replace_table, [])):
                        if replacement.get(unique_key) == row[id_key]:
                            replace_ref = attach(card, replace_table, replace_index)
                            content['replaces'].append({'id': replacement[target_key], 'source_ref': replace_ref})
                rule['unique_content'].append(content)
                relation(card, 'unique_content', row[id_key], ref)


def installed_civilizations(topics, cards, snapshot):
    """Expose package-local XML alternatives without applying their conditions.

    Cached identities always win. Cached parent/effect references can be shown
    as separately scoped evidence, but are never merged into an effective mod.
    SQL and updates remain evidence/coverage gaps, not evaluated definitions.
    """
    if 'civilizations' not in topics:
        return
    tables = snapshot['tables']
    installed = tables.get('InstalledRuleRows', [])
    primary = TOPIC_PRIMARY['civilizations']
    candidates = {}
    installed_index, cached_index = {}, {}
    for index, record in enumerate(installed):
        for key, value in (record['facts'] | record.get('where', {})).items():
            if isinstance(value, (str, int, float)):
                key = 'ModifierId' if key == 'ModifierID' else key
                installed_index.setdefault((record['package'], record['table'], key, value), []).append(index)
    for table, records in tables.items():
        if table == 'InstalledRuleRows':
            continue
        for index, record in enumerate(records):
            for key, value in record.items():
                if isinstance(value, (str, int, float)):
                    key = 'ModifierId' if key == 'ModifierID' else key
                    cached_index.setdefault((table, key, value), []).append(index)
    for index, record in enumerate(installed):
        table = record.get('table')
        if table in primary and record.get('operation') in ('Row', 'Replace'):
            ident = record['facts'].get(primary[table])
            if ident and ident not in cards:
                candidates.setdefault(ident, []).append(index)
    for ident, indices in candidates.items():
        packages = {installed[index]['package'] for index in indices}
        definitions = [installed[index]['facts'] for index in indices]
        tags = {row.get('Name') for row in definitions}
        descriptions = {row.get('Description') for row in definitions}
        tag = next(iter(tags)) if len(tags) == 1 else None
        entity = installed[indices[0]]['table']
        boundary = {'source_scope': 'installed_definition_catalog', 'applied': False,
                    'conditions_evaluated': False, 'ownership_verified': False, 'activation_verified': False}
        rule = {'facts': definitions[0] if all(row == definitions[0] for row in definitions) else {'definition_status': 'conditional_alternatives', 'id': ident},
                **boundary, 'traits': [], 'owners': [], 'leaders': [], 'civilizations': [],
                'unique_content': [], 'conditions': [], 'unresolved_references': [],
                'limitations': ['安装目录定义未应用；Row／Replace／Update／Delete、载入顺序、条件和 SQL 不求值。',
                                '拥有、启用和当前对局适用性未知；本机缓存引用单独标注，不构成有效合并规则。',
                                '脚本效果和隐藏议程未知；缺少关联不证明无能力。'],
                'current_facts_needed': ['当前文明与领袖 ID', '资料片／模组／模式与规则集', '能力条件及本局实际收益'],
                'coverage_gaps': [gap for gap in snapshot['profile'].get('installed_catalog_gaps', []) if gap.get('package') in packages]}
        card = {'id': ident, 'topic': 'civilizations', 'names': names(snapshot, tag, ident),
                'summary': {'entity_type': entity, 'description': names(snapshot, next(iter(descriptions)) if len(descriptions) == 1 else None, '')['zh_Hans_CN'],
                            'name_fallbacks': name_fallbacks(snapshot, tag), **boundary},
                'rule': rule, 'source_refs': [], 'relations': []}

        def unknown(field, value, status='unknown_missing_definition'):
            entry = {'field': field, 'id': value, 'status': status}
            if entry not in rule['unresolved_references']:
                rule['unresolved_references'].append(entry)

        def attach(table, index):
            ref = source_ref(table, index)
            if ref not in card['source_refs']:
                card['source_refs'].append(ref)
            if table == 'InstalledRuleRows':
                row = installed[index]
                condition = {key: row.get(key) for key in ('package', 'manifest', 'path', 'action', 'criteria', 'criteria_definition', 'action_properties')}
                if condition not in rule['conditions']:
                    rule['conditions'].append(condition)
                if row['operation'] not in ('Row', 'Replace', 'Update', 'Delete'):
                    unknown('operation', row['operation'], 'unsupported_operation_not_applied')
            return ref

        def find(table, key, value):
            # Prefer this identity's package, never another installed package.
            indices = sorted({index for package in packages for index in installed_index.get((package, table, key, value), [])})
            matches = [('InstalledRuleRows', index, installed[index]['facts']) for index in indices]
            if matches:
                return matches
            return [(table, index, tables[table][index]) for index in cached_index.get((table, key, value), [])]

        def rows(table, key, value):
            found = find(table, key, value)
            for table_name, index, _ in found:
                attach(table_name, index)
            return [(table_name, index, row) for table_name, index, row in found
                    if table_name != 'InstalledRuleRows' or installed[index]['operation'] in ('Row', 'Replace')]

        for index in indices:
            attach('InstalledRuleRows', index)
        if len(packages) != 1:
            unknown('package', ident, 'unknown_ambiguous_definition')
            # Multiple packages with the same identity are alternatives, not a
            # license to join their ability graphs.
        else:
            owners = [(ident, [])]
            if entity == 'Leaders':
                current, chain = ident, []
                while current:
                    if current in chain:
                        unknown('LeaderType', current, 'unknown_inheritance_cycle')
                        break
                    definitions = rows('Leaders', 'LeaderType', current)
                    if not definitions:
                        unknown('LeaderType', current)
                        break
                    if current != ident:
                        owners.append((current, list(chain)))
                    chain.append(current)
                    parents = {row.get('InheritFrom') for _, _, row in definitions}
                    if len(parents) != 1:
                        unknown('InheritFrom', current, 'unknown_ambiguous_definition')
                        break
                    current = next(iter(parents))
            for table, owner_key in [('CivilizationTraits', 'CivilizationType'), ('LeaderTraits', 'LeaderType')]:
                if entity not in ('Civilizations', 'Leaders'):
                    continue
                if (entity == 'Civilizations') != (table == 'CivilizationTraits'):
                    continue
                for owner, chain in owners:
                    for table_name, index, record in rows(table, owner_key, owner):
                        trait = record.get('TraitType')
                        if trait:
                            rule['traits'].append({'id': trait, 'owner': owner, 'ownership': 'direct' if owner == ident else 'inherited',
                                                   'inherited_via': chain, 'source_ref': source_ref(table_name, index)})
            trait_ids = {item['id'] for item in rule['traits']} | ({ident} if entity == 'Traits' else set())
            for trait in sorted(trait_ids):
                trait_definitions = rows('Traits', 'TraitType', trait)
                if not trait_definitions:
                    unknown('TraitType', trait)
                else:
                    for item in rule['traits']:
                        if item['id'] == trait:
                            trait_names = {row.get('Name') for _, _, row in trait_definitions}
                            trait_descriptions = {row.get('Description') for _, _, row in trait_definitions}
                            item['names'] = names(snapshot, next(iter(trait_names)) if len(trait_names) == 1 else None, trait)
                            item['description'] = names(snapshot, next(iter(trait_descriptions)) if len(trait_descriptions) == 1 else None, '')
                rows('TraitModifiers', 'TraitType', trait)
                if entity == 'Traits':
                    for table, key in [('CivilizationTraits', 'CivilizationType'), ('LeaderTraits', 'LeaderType')]:
                        for table_name, index, record in rows(table, 'TraitType', trait):
                            rule['owners'].append({'id': record[key], 'source_ref': source_ref(table_name, index)})
                for table, id_key in [('Units', 'UnitType'), ('Buildings', 'BuildingType'), ('Districts', 'DistrictType'), ('Improvements', 'ImprovementType')]:
                    for table_name, index, record in rows(table, 'TraitType', trait):
                        content_id = record.get(id_key)
                        if not content_id:
                            continue
                        item = {'id': content_id, 'entity_type': table, 'trait': trait,
                                'names': names(snapshot, record.get('Name'), content_id), 'source_ref': source_ref(table_name, index),
                                'prerequisites': {key: record[key] for key in ('PrereqTech', 'PrereqCivic') if record.get(key)}, 'replaces': []}
                        for related in ENTITY_RECORDS.get(table, ()):
                            rows(related, id_key, content_id)
                        for replacement, unique, target in [('UnitReplaces', 'CivUniqueUnitType', 'ReplacesUnitType'), ('BuildingReplaces', 'CivUniqueBuildingType', 'ReplacesBuildingType'), ('DistrictReplaces', 'CivUniqueDistrictType', 'ReplacesDistrictType')]:
                            if (table, replacement) not in (('Units', 'UnitReplaces'), ('Buildings', 'BuildingReplaces'), ('Districts', 'DistrictReplaces')):
                                continue
                            for rt, ri, replacement_row in rows(replacement, unique, content_id):
                                item['replaces'].append({'id': replacement_row[target], 'source_ref': source_ref(rt, ri)})
                        rule['unique_content'].append(item)
            if entity in ('Civilizations', 'Leaders'):
                owner_key = 'CivilizationType' if entity == 'Civilizations' else 'LeaderType'
                target_key = 'LeaderType' if entity == 'Civilizations' else 'CivilizationType'
                for table, index, row in rows('CivilizationLeaders', owner_key, ident):
                    rule['leaders' if entity == 'Civilizations' else 'civilizations'].append({'id': row[target_key], 'source_ref': source_ref(table, index)})
            # Traverse only explicit modifier/requirement references. Visited
            # records keep nested or cyclic effects finite, without evaluation.
            pending, visited = deque(card['source_refs']), set()
            while pending:
                ref = pending.popleft()
                marker = (ref['table'], ref['row'])
                if marker in visited:
                    continue
                visited.add(marker)
                record = tables[ref['table']][ref['row']]
                facts = record['facts'] if ref['table'] == 'InstalledRuleRows' else record
                targets = []
                for key, value in facts.items():
                    key = 'ModifierId' if key == 'ModifierID' else ('RequirementSetId' if key.endswith('RequirementSetId') else key)
                    if key in REFERENCE_TABLES and value:
                        targets.append((key, value))
                if facts.get('Name') in REFERENCE_TABLES and facts.get('Value'):
                    targets.append((facts['Name'], facts['Value']))
                for key, value in targets:
                    for position, table in enumerate(REFERENCE_TABLES[key]):
                        matches = rows(table, key, value)
                        if position == 0 and not matches:
                            unknown(key, value)
                        pending.extend(source_ref(t, i) for t, i, _ in matches)
        # Rule depth keeps fields/operations; repeated XML, package paths and
        # full source context are available through the evidence references.
        rule['related_records'] = []
        for ref in card['source_refs']:
            record = tables[ref['table']][ref['row']]
            installed_record = ref['table'] == 'InstalledRuleRows'
            rule['related_records'].append({'source_ref': ref,
                'source_scope': 'installed_definition_catalog' if installed_record else 'cached_reference_only',
                'facts': record['facts'] if installed_record else record,
                **({'table': record['table'], 'operation': record['operation'], 'where': record.get('where', {})} if installed_record else {})})
        cards[ident] = card
        topics['civilizations'].append(card)


def add_mechanisms(topics, cards, tables, curated):
    for topic, records in curated.items():
        for data in records:
            if not isinstance(data, dict):
                raise ValueError('mechanism must be an object')
            ident = data.get('id')
            if not isinstance(ident, str) or not ident.startswith('MECHANIC_') or ident in cards:
                raise ValueError('missing or duplicate mechanism ID')
            if not isinstance(data.get('names'), dict) or not all(isinstance(data['names'].get(k), str) and data['names'][k] for k in ('zh_Hans_CN', 'en_US')) or not isinstance(data.get('summary'), str):
                raise ValueError('invalid mechanism names or summary: ' + ident)
            for field in ('conditions', 'current_facts_needed', 'limitations', 'related'):
                if not isinstance(data.get(field, []), list) or any(not isinstance(v, str) for v in data.get(field, [])):
                    raise ValueError('invalid mechanism field: ' + field)
            selectors = data.get('source_selectors')
            if not isinstance(selectors, list) or not selectors:
                raise ValueError('mechanism requires source selectors: ' + ident)
            refs = []
            for selector in selectors:
                if not isinstance(selector, dict) or not isinstance(selector.get('table'), str) or not isinstance(selector.get('key'), str) or 'value' not in selector:
                    raise ValueError('invalid source selector: ' + ident)
                table, key, value = selector['table'], selector['key'], selector['value']
                matches = [source_ref(table, i) for i, row in enumerate(tables.get(table, [])) if key in row and row[key] == value]
                if not matches:
                    raise ValueError('unmatched source selector: ' + ident + ' ' + table + '.' + key)
                for ref in matches:
                    if ref not in refs:
                        refs.append(ref)
            card = {'id': ident, 'topic': topic, 'names': data['names'], 'summary': {'entity_type': 'CuratedMechanism', 'description': data['summary'], 'name_fallbacks': {}, 'applicability': 'curated_interpretation; current_session_and_action_unverified'},
                    'curation_ref': {'file': 'topics/' + topic + '.json', 'id': ident},
                    'rule': {'facts': {'mechanism': ident, 'origin': 'curated_interpretation'}, 'conditions': [{'interpretation': v} for v in data.get('conditions', [])], 'prerequisites': [], 'unlocks': [], 'boosts': [], 'adjacencies': [], 'related_records': [{'source_ref': r, 'facts': tables[r['table']][r['row']]} for r in refs], 'current_facts_needed': data.get('current_facts_needed', []), 'limitations': data.get('limitations', [])},
                    'source_refs': refs, 'relations': []}
            cards[ident] = card
            topics[topic].append(card)
    for topic, records in curated.items():
        for data in records:
            cards[data['id']]['relations'] = [{'type': 'curated_context', 'target': target, 'coverage': 'internal' if target in cards else 'external'} for target in data.get('related', [])]

VERSION_KEYS = ('game_version', 'macos_bundle_version', 'macos_bundle_build', 'steam_build_id', 'ruleset')


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def names(snapshot, tag, fallback):
    value = snapshot['localization'].get(tag, {})
    return {'zh_Hans_CN': value.get('zh_Hans_CN') or value.get('en_US') or fallback,
            'en_US': value.get('en_US') or value.get('zh_Hans_CN') or fallback}


def name_fallbacks(snapshot, tag):
    value = snapshot['localization'].get(tag, {})
    available = [language for language in ('zh_Hans_CN', 'en_US') if value.get(language)]
    return {language: available[0] if available else 'identifier'
            for language in ('zh_Hans_CN', 'en_US') if not value.get(language)}


def source_ref(table, index):
    return {'table': table, 'row': index}


def check_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError('snapshot must be an object')
    if snapshot.get('schema_version') != 1:
        raise ValueError('unsupported snapshot schema_version')
    for key in ('profile', 'tables', 'localization'):
        if not isinstance(snapshot.get(key), dict):
            raise ValueError('missing or invalid snapshot field: ' + key)
    for key, value in snapshot['localization'].items():
        if not isinstance(value, dict):
            raise ValueError('invalid localization object: ' + key)
    for field in ('code_evidence', 'sources'):
        if not isinstance(snapshot.get(field, []), list) or any(not isinstance(row, dict) for row in snapshot.get(field, [])):
            raise ValueError('invalid snapshot list: ' + field)
    for key in VERSION_KEYS:
        if not snapshot['profile'].get(key):
            raise ValueError('missing version field: ' + key)
    for table in PRIMARY:
        if not isinstance(snapshot['tables'].get(table), list):
            raise ValueError('missing primary table: ' + table)
    for table in CORE_RELATIONSHIPS:
        if not isinstance(snapshot['tables'].get(table), list):
            raise ValueError('missing core relationship table: ' + table)
    for table, rows in snapshot['tables'].items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('invalid table rows: ' + table)


def generate(snapshot, root=None):
    check_snapshot(snapshot)
    root = root or Path(__file__).resolve().parent
    curated = definitions(root)
    primary = dict(PRIMARY)
    for topic in curated:
        primary.update({table: (topic, key) for table, key in TOPIC_PRIMARY[topic].items()})
    declared = snapshot.get('knowledge_topics')
    if declared is not None and set(declared) != set(('civics', 'technologies', 'districts', 'congress', *curated)):
        raise ValueError('declared knowledge topics differ from available definitions')
    tables = snapshot['tables']
    references = reference_index(tables)
    topics = {topic: [] for topic in ('civics', 'technologies', 'districts', 'congress', *curated)}
    cards = {}
    for table, (topic, key) in primary.items():
        if not isinstance(tables.get(table), list):
            raise ValueError('missing active topic table: ' + table)
        for index, row in enumerate(tables[table]):
            ident = row.get(key)
            if not isinstance(ident, str) or not ident or ident in cards:
                raise ValueError('missing or duplicate primary ID: ' + table)
            label = names(snapshot, row.get('Name'), ident)
            card = {'id': ident, 'topic': topic, 'names': label,
                    'summary': {'entity_type': table, 'description': names(snapshot, row.get('Description'), '').get('zh_Hans_CN', ''), 'name_fallbacks': name_fallbacks(snapshot, row.get('Name')), 'applicability': 'version_snapshot_only; current_session_and_action_unverified'},
                    'rule': {'facts': row, 'conditions': [], 'prerequisites': [], 'unlocks': [], 'boosts': [], 'adjacencies': [],
                             'limitations': ['原始基础成本不是本局有效成本；前置与解锁不构成完整合法性判断。', '模组、模式、随机规则树与当前对局未核对；缺少记录不证明条件不存在。'],
                             'current_facts_needed': []},
                    'relations': [], 'source_refs': [source_ref(table, index)]}
            topics[topic].append(card)
            cards[ident] = card

    def attach(card, table, index):
        ref = source_ref(table, index)
        if ref not in card['source_refs']:
            card['source_refs'].append(ref)
        return ref

    def relation(card, kind, target, ref):
        card['relations'].append({'type': kind, 'target': target, 'coverage': 'internal' if target in cards else 'external', 'source_ref': ref})

    for table, entity, prerequisite in [('TechnologyPrereqs', 'Technology', 'PrereqTech'), ('CivicPrereqs', 'Civic', 'PrereqCivic')]:
        for index, row in enumerate(tables.get(table, [])):
            if row.get(entity) not in cards or row.get(prerequisite) not in cards:
                raise ValueError('dangling prerequisite: ' + table)
            card = cards[row[entity]]
            ref = attach(card, table, index)
            card['rule']['prerequisites'].append({'id': row[prerequisite], 'source_ref': ref})
            relation(card, 'prerequisite', row[prerequisite], ref)
            predecessor = cards[row[prerequisite]]
            reverse_ref = attach(predecessor, table, index)
            relation(predecessor, 'enables_research', card['id'], reverse_ref)

    for table, id_key in UNLOCKS.items():
        for index, row in enumerate(tables.get(table, [])):
            for prerequisite in ('PrereqTech', 'PrereqCivic'):
                parent = row.get(prerequisite)
                if not parent:
                    continue
                if parent not in cards or not row.get(id_key):
                    raise ValueError('dangling unlock: ' + table)
                card = cards[parent]
                ref = attach(card, table, index)
                card['rule']['unlocks'].append({'id': row[id_key], 'names': names(snapshot, row.get('Name'), row[id_key]), 'entity_type': table, 'conditions': row, 'source_ref': ref})
                relation(card, 'unlocks', row[id_key], ref)
                if row[id_key] in cards:
                    target = cards[row[id_key]]
                    target_ref = attach(target, table, index)
                    target['rule']['prerequisites'].append({'id': parent, 'source_ref': target_ref})
                    relation(target, 'prerequisite', parent, target_ref)

    for index, row in enumerate(tables.get('Boosts', [])):
        for key in ('TechnologyType', 'CivicType'):
            if row.get(key) in cards:
                card = cards[row[key]]
                ref = attach(card, 'Boosts', index)
                card['rule']['boosts'].append({'facts': row, 'trigger': names(snapshot, row.get('TriggerDescription'), ''), 'source_ref': ref, 'interpretation': '原始 Boost 值；最终比例和当前完成状态需查询游戏。'})

    adjacency = {row['ID']: (i, row) for i, row in enumerate(tables.get('Adjacency_YieldChanges', []))}
    for index, row in enumerate(tables.get('District_Adjacencies', [])):
        if row.get('DistrictType') not in cards or row.get('YieldChangeId') not in adjacency:
            raise ValueError('dangling district adjacency')
        card = cards[row['DistrictType']]
        definition_index, definition = adjacency[row['YieldChangeId']]
        attach(card, 'District_Adjacencies', index)
        ref = attach(card, 'Adjacency_YieldChanges', definition_index)
        card['rule']['adjacencies'].append({'id': definition['ID'], 'facts': definition, 'description': names(snapshot, definition.get('Description'), ''), 'source_ref': ref})
        for key in ('PrereqTech', 'PrereqCivic', 'ObsoleteTech', 'ObsoleteCivic'):
            if definition.get(key):
                relation(card, key, definition[key], ref)

    for table, key in [('District_RequiredFeatures', 'DistrictType'), ('District_ValidTerrains', 'DistrictType'), ('Districts_XP2', 'DistrictType'), ('DistrictReplaces', 'CivUniqueDistrictType')]:
        for index, row in enumerate(tables.get(table, [])):
            if row.get(key) not in cards:
                raise ValueError('dangling district condition: ' + table)
            card = cards[row[key]]
            ref = attach(card, table, index)
            card['rule']['conditions'].append({'facts': row, 'source_ref': ref})
            if table == 'DistrictReplaces':
                relation(card, 'replaces', row['ReplacesDistrictType'], ref)

    for index, row in enumerate(tables.get('ResolutionEffects', [])):
        card = cards.get(row.get('ResolutionType'))
        if not card:
            raise ValueError('dangling resolution effect')
        ref = attach(card, 'ResolutionEffects', index)
        card['rule']['conditions'].append({'facts': row, 'source_ref': ref})

    for card in topics['districts']:
        trait = card['rule']['facts'].get('TraitType')
        if trait:
            for table in ('CivilizationTraits', 'LeaderTraits'):
                for index, row in enumerate(tables.get(table, [])):
                    if row.get('TraitType') == trait:
                        ref = attach(card, table, index)
                        card['rule']['conditions'].append({'facts': row, 'source_ref': ref})

    civilization_records(topics, cards, tables, snapshot, attach, relation)
    for card in cards.values():
        topic = card['topic']
        if topic in ('civics', 'technologies'):
            card['rule']['current_facts_needed'] = ['已完成与可选研究', '当前有效成本及进度', '启用模式与随机树', '尤里卡或鼓舞完成情况', '文明／领袖及专属条件']
        elif topic == 'districts':
            card['rule']['current_facts_needed'] = ['城市人口与已建区域', '可落位坐标和地块占用', '地形／地貌／资源', '相邻地块与政策修正', '当前成本及文明限制']
            card['rule']['limitations'].append('人口要求、相邻收益及落位结果由当前游戏查询确认；不在离线卡片中计算合法坐标。')
        elif topic == 'congress':
            card['rule']['current_facts_needed'] = ['议会阶段和请求身份', '实际议题与候选目标', '提案资格和双向禁投结果', '外交支持点及实际费用', '提交后的原生确认']
            card['rule']['limitations'].append('禁投定义不代表玩家当前已被禁投；NoUpvote 和 NoDownvote 分别读取，未知资格保留 UNKNOWN；不生成默认选票。')
        elif topic in curated:
            card['rule']['current_facts_needed'] = ['本局对象与可见状态', '实际费用、修正与合法候选', '活动规则及条件是否满足']
            related_records(card, tables, attach, references)
        # Localized literal fields supplement raw identifiers without replacing evidence.
        card['rule']['localized_fields'] = {key: names(snapshot, value, value) for key, value in card['rule']['facts'].items() if isinstance(value, str) and value.startswith('LOC_')}
        for index, row in enumerate(tables.get('GlobalParameters', [])):
            parameter = str(row.get('Name', ''))
            relevant = (topic == 'districts' and any(x in parameter for x in ('DISTRICT', 'CITY_POP'))) or (topic in ('civics', 'technologies') and any(x in parameter for x in ('BOOST', 'CIVIC', 'TECH'))) or (topic == 'congress' and any(x in parameter for x in ('CONGRESS', 'VOTING', 'DIPLOMATIC_')))
            if relevant:
                ref = attach(card, 'GlobalParameters', index)
                card['rule']['conditions'].append({'facts': row, 'source_ref': ref})
    add_mechanisms(topics, cards, tables, curated)
    installed_civilizations(topics, cards, snapshot)
    return {topic: {'schema_version': 1, 'topic': topic, 'cards': sorted(values, key=lambda c: c['id'])} for topic, values in topics.items()}


def coverage(snapshot, root=None):
    root = root or Path(__file__).resolve().parent
    active = definitions(root)
    tables = list(PRIMARY) + [table for topic in active for table in TOPIC_PRIMARY[topic]]
    optional = set(OPTIONAL_TABLES)
    for topic in active:
        for table in TOPIC_PRIMARY[topic]:
            optional.update(ENTITY_RECORDS.get(table, ()))
    if 'civilizations' in active:
        optional.update(('CivilizationTraits', 'LeaderTraits', 'CivilizationLeaders', 'TraitModifiers',
                         'UnitReplaces', 'BuildingReplaces', 'DistrictReplaces'))
    if active:
        optional.update(table for group in REFERENCE_TABLES.values() for table in group)
    installed = snapshot['tables'].get('InstalledRuleRows', [])
    return {'complete_for_snapshot_tables': tables,
            'installed_definition_catalog': {'records': len(installed), 'packages': len({row['package'] for row in installed}),
                                             'gaps': snapshot['profile'].get('installed_catalog_gaps', []),
                                             'applied': False, 'conditions_evaluated': False, 'ownership_verified': False},
            'missing_optional_tables': sorted(optional - snapshot['tables'].keys()),
            'empty_optional_tables': sorted(table for table in optional if snapshot['tables'].get(table) == []),
            'not_covered': ['完整 Modifier 解释', '当前对局模组／模式核对', '完整动作合法性和有效费用', '策略最优性']}


def build(source, output):
    snapshot = json.loads(source.read_text(encoding='utf-8'))
    documents = generate(snapshot)
    support_root = Path(__file__).resolve().parent
    for name in support_files(support_root):
        if not (support_root / name).is_file():
            raise ValueError('missing support file: ' + name)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'rules').mkdir(exist_ok=True)
    (output / 'sources').mkdir(exist_ok=True)
    snapshot_bytes = encoded(snapshot)
    (output / 'sources/snapshot.json').write_bytes(snapshot_bytes)
    files = {}
    for topic, document in documents.items():
        relative = 'rules/' + topic + '.json'
        data = encoded(document)
        (output / relative).write_bytes(data)
        files[relative] = digest(data)
    manifest = {'schema_version': 1, 'profile': snapshot['profile'], 'snapshot': 'sources/snapshot.json', 'snapshot_sha256': digest(snapshot_bytes), 'files': files,
                'support_files': {name: digest((support_root / name).read_bytes()) for name in support_files(support_root)},
                'counts': {topic: len(doc['cards']) for topic, doc in documents.items()}, 'coverage': coverage(snapshot)}
    (output / 'manifest.json').write_bytes(encoded(manifest))
    for name in support_files(support_root):
        script = support_root / name
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if script.resolve() != destination.resolve():
            shutil.copyfile(script, destination)
    return {'status': 'built', 'counts': manifest['counts']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.source, args.output), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())

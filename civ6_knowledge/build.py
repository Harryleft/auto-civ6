#!/usr/bin/env python3
"""Build a portable, deterministic knowledge directory from an offline snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

PRIMARY = {'Civics': ('civics', 'CivicType'), 'Technologies': ('technologies', 'TechnologyType'), 'Districts': ('districts', 'DistrictType'), 'Resolutions': ('congress', 'ResolutionType'), 'ProposalTypes': ('congress', 'ProposalType'), 'VotingBlockers': ('congress', 'VotingBlockerType')}
UNLOCKS = {'Units': 'UnitType', 'Buildings': 'BuildingType', 'Districts': 'DistrictType', 'Improvements': 'ImprovementType', 'Projects': 'ProjectType', 'Policies': 'PolicyType', 'Governments': 'GovernmentType', 'Resources': 'ResourceType', 'Routes': 'RouteType'}
VERSION_KEYS = ('game_version', 'macos_bundle_version', 'macos_bundle_build', 'steam_build_id', 'ruleset')


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def names(snapshot, tag, fallback):
    value = snapshot['localization'].get(tag, {})
    return {'zh_Hans_CN': value.get('zh_Hans_CN') or value.get('en_US') or fallback,
            'en_US': value.get('en_US') or value.get('zh_Hans_CN') or fallback}


def source_ref(table, index):
    return {'table': table, 'row': index}


def check_snapshot(snapshot):
    if snapshot.get('schema_version') != 1:
        raise ValueError('unsupported snapshot schema_version')
    for key in ('profile', 'tables', 'localization'):
        if not isinstance(snapshot.get(key), dict):
            raise ValueError('missing or invalid snapshot field: ' + key)
    for key in VERSION_KEYS:
        if not snapshot['profile'].get(key):
            raise ValueError('missing version field: ' + key)
    for table in PRIMARY:
        if not isinstance(snapshot['tables'].get(table), list):
            raise ValueError('missing primary table: ' + table)
    for table, rows in snapshot['tables'].items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('invalid table rows: ' + table)


def generate(snapshot):
    check_snapshot(snapshot)
    tables = snapshot['tables']
    topics = {topic: [] for topic in ('civics', 'technologies', 'districts', 'congress')}
    cards = {}
    for table, (topic, key) in PRIMARY.items():
        for index, row in enumerate(tables[table]):
            ident = row.get(key)
            if not isinstance(ident, str) or not ident or ident in cards:
                raise ValueError('missing or duplicate primary ID: ' + table)
            label = names(snapshot, row.get('Name'), ident)
            card = {'id': ident, 'topic': topic, 'names': label,
                    'summary': {'entity_type': table, 'description': names(snapshot, row.get('Description'), '').get('zh_Hans_CN', ''), 'applicability': 'version_snapshot_only; current_session_and_action_unverified'},
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

    for card in cards.values():
        topic = card['topic']
        if topic in ('civics', 'technologies'):
            card['rule']['current_facts_needed'] = ['已完成与可选研究', '当前有效成本及进度', '启用模式与随机树', '尤里卡或鼓舞完成情况', '文明／领袖及专属条件']
        elif topic == 'districts':
            card['rule']['current_facts_needed'] = ['城市人口与已建区域', '可落位坐标和地块占用', '地形／地貌／资源', '相邻地块与政策修正', '当前成本及文明限制']
            card['rule']['limitations'].append('人口要求、相邻收益及落位结果由当前游戏查询确认；不在离线卡片中计算合法坐标。')
        else:
            card['rule']['current_facts_needed'] = ['议会阶段和请求身份', '实际议题与候选目标', '提案资格和双向禁投结果', '外交支持点及实际费用', '提交后的原生确认']
            card['rule']['limitations'].append('禁投定义不代表玩家当前已被禁投；NoUpvote 和 NoDownvote 分别读取，未知资格保留 UNKNOWN；不生成默认选票。')
        # Localized literal fields supplement raw identifiers without replacing evidence.
        card['rule']['localized_fields'] = {key: names(snapshot, value, value) for key, value in card['rule']['facts'].items() if isinstance(value, str) and value.startswith('LOC_')}
        for index, row in enumerate(tables.get('GlobalParameters', [])):
            parameter = str(row.get('Name', ''))
            relevant = (topic == 'districts' and any(x in parameter for x in ('DISTRICT', 'CITY_POP'))) or (topic in ('civics', 'technologies') and any(x in parameter for x in ('BOOST', 'CIVIC', 'TECH'))) or (topic == 'congress' and any(x in parameter for x in ('CONGRESS', 'VOTING', 'DIPLOMATIC_')))
            if relevant:
                ref = attach(card, 'GlobalParameters', index)
                card['rule']['conditions'].append({'facts': row, 'source_ref': ref})
    return {topic: {'schema_version': 1, 'topic': topic, 'cards': sorted(values, key=lambda c: c['id'])} for topic, values in topics.items()}


def build(source, output):
    snapshot = json.loads(source.read_text(encoding='utf-8'))
    documents = generate(snapshot)
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
                'counts': {topic: len(doc['cards']) for topic, doc in documents.items()}, 'coverage': {'complete_for_snapshot_tables': list(PRIMARY), 'not_covered': ['完整 Modifier 解释', '当前对局模组／模式核对', '完整动作合法性和有效费用', '策略最优性']}}
    (output / 'manifest.json').write_bytes(encoded(manifest))
    for name in ('build.py', 'query.py'):
        script = Path(__file__).with_name(name)
        destination = output / name
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

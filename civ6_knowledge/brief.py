"""Lossless rule view: factor repeated records, never select or rank rules."""
from copy import deepcopy
import hashlib
import json

PARENTS = {
    'Modifiers': 'ModifierId', 'ModifierArguments': 'ModifierId',
    'Requirements': 'RequirementId', 'RequirementArguments': 'RequirementId',
    'RequirementSets': 'RequirementSetId', 'RequirementSetRequirements': 'RequirementSetId',
    'TraitModifiers': 'TraitType', 'LeaderTraits': 'LeaderType',
    'DynamicModifiers': 'ModifierType',
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def profile_reference(profile):
    return {**{key: value for key, value in profile.items() if not isinstance(value, (dict, list))},
            'sha256': fingerprint(profile), 'details_level': 'evidence',
            'current_session_verified': False, 'active_mods_verified': False,
            'active_modes_verified': False}


def reference(value):
    return f"{value['table']}#{value['row']}"


def expand_reference(value):
    table, row = value.rsplit('#', 1)
    return {'table': table, 'row': int(row)}


def links(value, expand=False):
    if isinstance(value, list):
        return [links(item, expand) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == 'source_ref':
            result[key] = expand_reference(item) if expand else reference(item)
        elif key == 'source_refs':
            result[key] = [expand_reference(ref) if expand else reference(ref) for ref in item]
        else:
            result[key] = links(item, expand)
    return result


def common_values(rows):
    if len(rows) < 3:
        return {}
    # JSON identity distinguishes false from 0 and preserves null/absent.
    return {key: value for key, value in rows[0].items() if all(
        key in row and fingerprint(row[key]) == fingerprint(value) for row in rows[1:])}


def group_records(records):
    tables = {}
    for ordinal, record in enumerate(records):
        tables.setdefault(record['source_ref']['table'], []).append((ordinal, record))
    result = {}
    for table, entries in tables.items():
        facts = [record['facts'] for _, record in entries]
        metadata = [{k: v for k, v in record.items() if k not in {'source_ref', 'facts'}} for _, record in entries]
        common_facts, common_record = common_values(facts), common_values(metadata)
        parent = PARENTS.get(table)
        if not all(isinstance(row.get(parent), str) and row[parent] for row in facts):
            parent = None
        common_facts.pop(parent, None)
        groups = {}
        for (ordinal, record), meta in zip(entries, metadata):
            fields = {k: v for k, v in record['facts'].items() if k not in common_facts}
            group = fields.pop(parent) if parent else ''
            row = [ordinal, record['source_ref']['row'], fields]
            extra = {k: v for k, v in meta.items() if k not in common_record}
            if extra:
                row.append(extra)
            groups.setdefault(group, []).append(row)
        result[table] = {'common_facts': common_facts, 'common_record': common_record,
                         'by': parent, 'groups': groups}
    return result


def ungroup_records(tables):
    records = {}
    for table, data in tables.items():
        for parent_value, rows in data['groups'].items():
            for row in rows:
                ordinal, index, fields, *extra = row
                if ordinal in records:
                    raise ValueError('Duplicate rule record ordinal')
                facts = dict(data['common_facts'])
                if data['by']:
                    facts[data['by']] = parent_value
                facts.update(fields)
                records[ordinal] = {**data['common_record'], **(extra[0] if extra else {}),
                                    'source_ref': {'table': table, 'row': index}, 'facts': facts}
    if set(records) != set(range(len(records))):
        raise ValueError('Incomplete rule record sequence')
    return [records[index] for index in range(len(records))]


def compact(card, profile):
    data = deepcopy(card)
    records = data['rule'].pop('related_records', None)
    data = links(data)
    if records is not None:
        data['rule']['related_records'] = group_records(records)
    return {**data, 'view': 'brief', 'profile': profile_reference(profile),
            'encoding': {
                'version': 1, 'card_sha256': fingerprint(card), 'truncated': False,
                'source_ref': 'Table#row; expand with level=evidence',
                'related_records': 'Table groups. common_facts/common_record apply to EVERY row; by supplies its parent field. Rows: [ordinal, source_row, overriding_facts, optional_record_metadata]. No conditions evaluated or records omitted.',
                'expand': {'entry_id': card['id'], 'level': 'rule'},
            }}


def expand(brief):
    """Audit seam: reconstruct the full card and verify its semantic identity."""
    if brief.get('view') != 'brief' or brief.get('encoding', {}).get('version') != 1:
        raise ValueError('Unsupported brief encoding')
    value = {key: deepcopy(item) for key, item in brief.items()
             if key not in {'view', 'profile', 'encoding', 'status', 'snapshot', 'applicability'}}
    records = value['rule'].pop('related_records', None)
    value = links(value, expand=True)
    if records is not None:
        value['rule']['related_records'] = ungroup_records(records)
    if fingerprint(value) != brief['encoding']['card_sha256']:
        raise ValueError('Brief rule fingerprint mismatch')
    return value

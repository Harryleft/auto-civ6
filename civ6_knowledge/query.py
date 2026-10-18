#!/usr/bin/env python3
"""Read offline Civ6 knowledge with explicit summary, rule and evidence levels."""
import argparse
import json
from pathlib import Path
import sys

from build import TOPIC_NAMES, VERSION_KEYS, check_snapshot, coverage, digest, generate, support_files


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def load(root):
    manifest = read(root / 'manifest.json')
    if not isinstance(manifest, dict):
        raise ValueError('manifest must be an object')
    snapshot_path = root / 'sources/snapshot.json'
    if manifest.get('schema_version') != 1 or manifest.get('snapshot') != 'sources/snapshot.json':
        raise ValueError('unsupported manifest')
    if digest(snapshot_path.read_bytes()) != manifest['snapshot_sha256']:
        raise ValueError('snapshot fingerprint mismatch')
    cards = {}
    documents = {}
    for topic in manifest['counts']:
        if topic not in TOPIC_NAMES:
            raise ValueError('unknown manifest topic: ' + topic)
        relative = 'rules/' + topic + '.json'
        data = (root / relative).read_bytes()
        if digest(data) != manifest['files'][relative]:
            raise ValueError('rule fingerprint mismatch: ' + relative)
        doc = json.loads(data)
        documents[topic] = doc
        for card in doc['cards']:
            if card['id'] in cards:
                raise ValueError('duplicate card ID: ' + card['id'])
            cards[card['id']] = card
    snapshot = read(snapshot_path)
    check_snapshot(snapshot)
    return manifest, snapshot, documents, cards


def summary(card):
    return {key: card[key] for key in ('id', 'topic', 'names', 'summary')}


def compare(profile, expected):
    if not isinstance(profile, dict) or not isinstance(expected, dict):
        raise ValueError('profile must be an object')
    supplied = expected.get('profile', expected)
    if not isinstance(supplied, dict):
        raise ValueError('profile must be an object')
    mismatches = {key: {'knowledge': profile.get(key), 'requested': supplied.get(key)} for key in VERSION_KEYS if not supplied.get(key) or str(profile.get(key)) != str(supplied.get(key))}
    if mismatches:
        raise ValueError('profile incompatible: ' + json.dumps(mismatches, ensure_ascii=False, sort_keys=True))
    return {'status': 'version_snapshot_match', 'current_session_verified': False, 'action_applicability': 'unverified', 'active_mods_verified': False, 'active_modes_verified': False}


def execute(root, args):
    manifest, snapshot, documents, cards = load(root)
    if args.profile:
        compare(manifest['profile'], read(args.profile))
    if args.command == 'check-profile':
        return compare(manifest['profile'], read(args.expected))
    if args.command == 'validate':
        if set(manifest.get('support_files', {})) != set(support_files(root)):
            raise ValueError('missing support file coverage')
        for name in support_files(root):
            if digest((root / name).read_bytes()) != manifest['support_files'][name]:
                raise ValueError('support file fingerprint mismatch: ' + name)
        if snapshot['profile'] != manifest['profile']:
            raise ValueError('profile differs from source')
        expected = generate(snapshot, root)
        if expected != documents:
            raise ValueError('cards, relations or source references differ from source snapshot')
        counts = {topic: len(doc['cards']) for topic, doc in documents.items()}
        if counts != manifest['counts']:
            raise ValueError('coverage counts differ')
        if coverage(snapshot, root) != manifest['coverage']:
            raise ValueError('coverage differs from actual source tables')
        source_hashes = {entry.get('path'): entry.get('sha256') for entry in snapshot.get('sources', [])}
        for entry in snapshot.get('code_evidence', []):
            if not entry.get('text') or entry.get('line_start', 0) < 1 or entry.get('line_end', 0) < entry['line_start'] or len(entry.get('sha256', '')) != 64:
                raise ValueError('invalid code evidence: ' + str(entry.get('id')))
            if entry.get('path') in source_hashes and entry['sha256'] != source_hashes[entry['path']]:
                raise ValueError('code source fingerprint mismatch: ' + str(entry.get('id')))
            if entry.get('text_sha256') and digest(entry['text'].encode('utf-8')) != entry['text_sha256']:
                raise ValueError('code evidence fingerprint mismatch: ' + str(entry.get('id')))
        return {'status': 'valid', 'counts': counts, 'profile': manifest['profile'], 'coverage': manifest['coverage']}
    if args.command == 'topics':
        return {'topics': [{'id': topic, 'count': count, 'guide': 'topics/' + topic + '.md'} for topic, count in manifest['counts'].items()], 'coverage': manifest['coverage'], 'profile': manifest['profile']}
    if args.command == 'search':
        needle = args.text.casefold()
        matches = [card for ident, card in sorted(cards.items()) if (not args.topic or args.topic == card['topic']) and needle in ' '.join([ident, *card['names'].values()]).casefold()]
        return {'total': len(matches), 'limit': args.limit, 'truncated': len(matches) > args.limit, 'results': [summary(card) for card in matches[:args.limit]], 'status': 'found' if matches else 'not_found', 'coverage': 'captured_entities_only'}
    if args.ident not in cards:
        raise ValueError('unknown ID: ' + args.ident)
    card = cards[args.ident]
    if args.level == 'summary':
        return summary(card)
    if args.level == 'rule':
        return card
    evidence = [{'source_ref': ref, 'facts': snapshot['tables'][ref['table']][ref['row']]} for ref in card['source_refs']]
    if card['topic'] == 'civilizations':
        for entry in evidence:
            entry['source_scope'] = ('installed_definition_catalog' if entry['source_ref']['table'] == 'InstalledRuleRows'
                                     else 'cached_reference_only' if card['summary']['source_scope'] == 'installed_definition_catalog'
                                     else 'local_cached_rules_snapshot')
    tags = {value for entry in evidence for value in (
        entry['facts'].get('facts', entry['facts']) if entry['source_ref']['table'] == 'InstalledRuleRows' else entry['facts']
    ).values() if isinstance(value, str) and value.startswith('LOC_')}
    localization = {tag: snapshot['localization'][tag] for tag in sorted(tags) if tag in snapshot['localization']}
    prefix = {'technologies': 'technology-', 'civics': 'civic-', 'districts': 'district-', 'congress': 'congress-'}.get(card['topic'], card['topic'] + '-')
    excerpts = [entry for entry in snapshot.get('code_evidence', []) if entry['id'].startswith(prefix)]
    boundary = {key: card['summary'][key] for key in ('source_scope', 'applied', 'conditions_evaluated', 'ownership_verified', 'activation_verified') if key in card['summary']}
    return {'id': card['id'], **boundary, 'source_refs': card['source_refs'], 'evidence': evidence, 'localization': localization, 'localization_sources': {tag: snapshot.get('localization_sources', {}).get(tag, {}) for tag in localization}, 'code_evidence': excerpts, 'profile': manifest['profile'], 'sources': snapshot.get('sources', [])}


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError('limit must be positive')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--profile', type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('topics')
    search = sub.add_parser('search')
    search.add_argument('text')
    search.add_argument('--topic', choices=TOPIC_NAMES)
    search.add_argument('--limit', type=positive, default=10)
    show = sub.add_parser('show')
    show.add_argument('ident')
    show.add_argument('--level', choices=('summary', 'rule', 'evidence'), default='summary')
    sub.add_parser('validate')
    profile = sub.add_parser('check-profile')
    profile.add_argument('expected', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.root, args), ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())

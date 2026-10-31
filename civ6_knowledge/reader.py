"""Verified offline source reads shared by standalone and Runtime adapters.

A read binds parsed source/rules to one manifest. Callers must finish through
this context manager (or verify_unchanged after an awaited child query) before
publishing a result. No game access, builds or cross-query data cache live here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PRIMARY = {'Civics': ('civics', 'CivicType'), 'Technologies': ('technologies', 'TechnologyType'), 'Districts': ('districts', 'DistrictType'), 'Resolutions': ('congress', 'ResolutionType'), 'ProposalTypes': ('congress', 'ProposalType'), 'VotingBlockers': ('congress', 'VotingBlockerType')}
CORE_RELATIONSHIPS = ('TechnologyPrereqs', 'CivicPrereqs', 'Boosts', 'District_Adjacencies', 'Adjacency_YieldChanges')
VERSION_KEYS = ('game_version', 'macos_bundle_version', 'macos_bundle_build', 'steam_build_id', 'ruleset')
TOPIC_NAMES = ('civics', 'technologies', 'districts', 'congress', 'civilizations',
               'economy', 'resources', 'combat', 'diplomacy', 'victory')


def digest(data):
    return hashlib.sha256(data).hexdigest()


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


class VerifiedSnapshot:
    """One verified source with selected rule documents, never a live-game fact."""

    def __init__(self, root: Path, *, topics=None):
        self.root = Path(root)
        self._manifest_bytes = (self.root / 'manifest.json').read_bytes()
        self.manifest = manifest = json.loads(self._manifest_bytes)
        if not isinstance(manifest, dict):
            raise ValueError('manifest must be an object')
        if manifest.get('schema_version') != 1 or manifest.get('snapshot') != 'sources/snapshot.json':
            raise ValueError('unsupported manifest')
        if not isinstance(manifest.get('profile'), dict) or not isinstance(manifest.get('counts'), dict) or not isinstance(manifest.get('files'), dict):
            raise ValueError('Invalid knowledge manifest metadata')
        if any(topic not in TOPIC_NAMES for topic in manifest['counts']):
            raise ValueError('unknown manifest topic')
        source = (self.root / manifest['snapshot']).read_bytes()
        if digest(source) != manifest.get('snapshot_sha256'):
            raise ValueError('snapshot fingerprint mismatch')
        self.snapshot = json.loads(source)
        check_snapshot(self.snapshot)
        if self.snapshot['profile'] != manifest['profile']:
            raise ValueError('Knowledge profile differs from source snapshot')
        self.documents = {}
        self.cards = {}
        selected = manifest['counts'] if topics is None else tuple(topics)
        for topic in selected:
            if topic not in manifest['counts']:
                raise ValueError('unknown manifest topic: ' + topic)
            relative = 'rules/' + topic + '.json'
            data = (self.root / relative).read_bytes()
            if digest(data) != manifest['files'].get(relative):
                raise ValueError('rule fingerprint mismatch: ' + relative)
            doc = json.loads(data)
            if not isinstance(doc, dict) or doc.get('schema_version') != 1 or doc.get('topic') != topic or not isinstance(doc.get('cards'), list):
                raise ValueError('unsupported rule document: ' + relative)
            self.documents[topic] = doc
            for card in doc['cards']:
                if not isinstance(card, dict) or not isinstance(card.get('id'), str):
                    raise ValueError('invalid card: ' + relative)
                if card['id'] in self.cards:
                    raise ValueError('duplicate card ID: ' + card['id'])
                self.cards[card['id']] = card
        self.verify_unchanged()

    @property
    def profile(self):
        return self.manifest['profile']

    @property
    def source_reference(self):
        return {'path': self.manifest['snapshot'], 'sha256': self.manifest['snapshot_sha256']}

    def verify_unchanged(self):
        if (self.root / 'manifest.json').read_bytes() != self._manifest_bytes:
            raise ValueError('Knowledge manifest changed during query; query again')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.verify_unchanged()

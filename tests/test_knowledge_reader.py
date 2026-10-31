"""Independent CLI and Runtime adapters share verified source semantics."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from civ_mcp import knowledge, research_catalog
from test_knowledge_queries import build, cli


def test_installed_runtime_uses_bundled_reader_with_external_data(monkeypatch, tmp_path):
    root = build(tmp_path)
    installed = tmp_path / 'installed' / 'civ_mcp'
    installed.mkdir(parents=True)
    source = Path(knowledge.__file__).resolve().parents[2] / 'civ6_knowledge' / 'reader.py'
    resource = installed / '_civ6_knowledge_reader.py'
    resource.write_bytes(source.read_bytes())
    find_spec = knowledge.importlib.util.find_spec
    monkeypatch.setattr(knowledge.importlib.util, 'find_spec', lambda name:
        knowledge.importlib.util.spec_from_file_location(name, resource)
        if name == '_civ6_knowledge_reader' else find_spec(name))
    monkeypatch.setattr(knowledge, 'DEFAULT_ROOT', tmp_path / 'absent-checkout')
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    result = json.loads(asyncio.run(knowledge.search('writing')))
    assert result['status'] == 'found'
    assert result['applicability']['current_session_verified'] is False


@pytest.mark.parametrize('corruption', ['manifest_schema', 'snapshot_schema', 'profile', 'duplicate_id', 'rule_schema'])
def test_all_readers_reject_the_same_invalid_source(monkeypatch, tmp_path, corruption):
    root = build(tmp_path)
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    if corruption == 'manifest_schema':
        manifest['schema_version'] = 999
    elif corruption == 'profile':
        manifest['profile']['game_version'] = 'different'
    else:
        relative = 'sources/snapshot.json' if corruption == 'snapshot_schema' else 'rules/technologies.json'
        content = json.loads((root / relative).read_text())
        if corruption == 'duplicate_id':
            content['cards'].append(content['cards'][0])
        else:
            content['schema_version'] = 999
        raw = json.dumps(content).encode()
        (root / relative).write_bytes(raw)
        if corruption == 'snapshot_schema':
            manifest['snapshot_sha256'] = hashlib.sha256(raw).hexdigest()
        else:
            manifest['files'][relative] = hashlib.sha256(raw).hexdigest()
    path.write_text(json.dumps(manifest))
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    assert cli(root, 'search', 'writing', ok=False)['status'] == 'error'
    assert json.loads(asyncio.run(knowledge.search('writing')))['status'] == 'error'
    assert json.loads(asyncio.run(knowledge.research('technology', 'writing')))['status'] == 'error'


def test_research_cannot_publish_when_manifest_changes_during_query(monkeypatch, tmp_path):
    root = build(tmp_path)
    search = research_catalog.search

    def changing_source(*args, **kwargs):
        result = search(*args, **kwargs)
        path = root / 'manifest.json'
        path.write_bytes(path.read_bytes() + b' ')
        return result

    monkeypatch.setattr(research_catalog, 'search', changing_source)
    with pytest.raises(ValueError, match='manifest changed'):
        research_catalog.query(root, 'technology', query='writing')


def test_runtime_cannot_publish_valid_child_output_from_a_changed_manifest(monkeypatch, tmp_path):
    root = build(tmp_path)
    result = cli(root, 'search', 'writing', '--limit', '5')
    (root / 'query.py').write_text(
        "from pathlib import Path\nimport json\n"
        "p = Path(__file__).parent / 'manifest.json'\n"
        "p.write_bytes(p.read_bytes() + b' ')\n"
        + 'print(' + repr(json.dumps(result)) + ')\n'
    )
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    output = json.loads(asyncio.run(knowledge.search('writing')))
    assert output['status'] == 'error' and 'manifest changed' in output['error']

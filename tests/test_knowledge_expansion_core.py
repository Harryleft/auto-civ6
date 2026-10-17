"""Public commands for standalone topic definitions, isolated from game files."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_knowledge_queries import snapshot

ROOT = Path(__file__).resolve().parents[1] / 'civ6_knowledge'


def stage(tmp_path):
    distribution = tmp_path / 'distribution'
    shutil.copytree(ROOT, distribution, ignore=shutil.ignore_patterns('*.json', '__pycache__'))
    definition = {'schema_version': 1, 'topic': 'economy', 'cards': [{
        'id': 'MECHANIC_ECONOMY_FOOD', 'names': {'zh_Hans_CN': '食物与成长', 'en_US': 'Food and growth'},
        'summary': '食物机制示例', 'conditions': ['有效食物需读取本局。'],
        'source_selectors': [{'table': 'Yields', 'key': 'YieldType', 'value': 'YIELD_FOOD'}],
        'related': ['YIELD_FOOD'], 'current_facts_needed': ['城市食物盈余'], 'limitations': [],
    }]}
    (distribution / 'topics/economy.json').write_text(json.dumps(definition))
    (distribution / 'topics/economy.md').write_text('# 经济\n')
    data = snapshot()
    data['tables'].update({'Yields': [{'YieldType': 'YIELD_FOOD', 'Name': 'LOC_FOOD'}], 'Buildings': []})
    data['localization']['LOC_FOOD'] = {'zh_Hans_CN': '食物', 'en_US': 'Food'}
    source = tmp_path / 'source.json';source.write_text(json.dumps(data))
    return distribution, source


def run(script, *args):
    result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)
    return result, json.loads(result.stdout)


def test_curated_mechanism_and_source_facts_are_disclosed_separately(tmp_path):
    dist, source = stage(tmp_path);output = tmp_path / 'output'
    result, _ = run(dist / 'build.py', '--source', str(source), '--output', str(output))
    assert result.returncode == 0
    result, card = run(output / 'query.py', 'show', 'MECHANIC_ECONOMY_FOOD', '--level', 'rule')
    assert result.returncode == 0
    assert card['rule']['conditions'] == [{'interpretation': '有效食物需读取本局。'}]
    assert card['rule']['related_records'][0]['facts']['YieldType'] == 'YIELD_FOOD'
    assert card['relations'][0]['coverage'] == 'internal'
    result, search = run(output / 'query.py', 'search', '食物', '--topic', 'economy')
    assert result.returncode == 0 and search['total'] == 2
    assert all('rule' not in c for c in search['results'])
    assert run(output / 'query.py', 'validate')[1]['status'] == 'valid'


@pytest.mark.parametrize('failure, expected', [
    ('unmatched', 'unmatched source selector'),
    ('wrong_key', 'unmatched source selector'),
    ('missing_table', 'missing active topic table'),
    ('duplicate', 'duplicate mechanism ID'),
    ('missing_definition', 'declared knowledge topics differ'),
])
def test_incomplete_expansion_refuses_to_publish(tmp_path, failure, expected):
    dist, source = stage(tmp_path)
    definition_path = dist / 'topics/economy.json'
    definition = json.loads(definition_path.read_text())
    data = json.loads(source.read_text())
    if failure == 'unmatched':
        definition['cards'][0]['source_selectors'][0]['value'] = 'YIELD_ABSENT'
    elif failure == 'wrong_key':
        definition['cards'][0]['source_selectors'][0] = {'table': 'Yields', 'key': 'Absent', 'value': None}
    elif failure == 'missing_table':
        del data['tables']['Buildings']
    elif failure == 'duplicate':
        definition['cards'].append(definition['cards'][0])
    elif failure == 'missing_definition':
        data['knowledge_topics'] = ['civics', 'technologies', 'districts', 'congress', 'economy', 'resources']
    definition_path.write_text(json.dumps(definition))
    source.write_text(json.dumps(data))
    output = tmp_path / 'unpublished'
    result, error = run(dist / 'build.py', '--source', str(source), '--output', str(output))
    assert result.returncode == 2
    assert expected in error['error']
    assert not output.exists()

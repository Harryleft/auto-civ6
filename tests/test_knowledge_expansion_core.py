"""Public commands for standalone topic definitions, isolated from game files."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

from test_knowledge_queries import snapshot

ROOT = Path(__file__).resolve().parents[1] / 'civ6_knowledge'


def stage(tmp_path):
    distribution = tmp_path / 'distribution'
    shutil.copytree(ROOT, distribution, ignore=shutil.ignore_patterns('__pycache__'))
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

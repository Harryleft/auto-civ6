"""Resources public CLI checks using the committed local-version fixture only."""
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def build(tmp_path):
    output = tmp_path / 'knowledge'
    result = subprocess.run([
        sys.executable, str(ROOT / 'civ6_knowledge/build.py'),
        '--source', str(ROOT / 'civ6_knowledge/sources/snapshot.json'),
        '--output', str(output),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def query(root, *args):
    result = subprocess.run([
        sys.executable, str(ROOT / 'civ6_knowledge/query.py'),
        '--root', str(root), *args,
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_coal_definition_separates_reveal_from_consumption_and_power(tmp_path):
    root = build(tmp_path)
    card = query(root, 'show', 'RESOURCE_COAL', '--level', 'evidence')
    facts = [(row['source_ref']['table'], row['facts']) for row in card['evidence']]
    assert any(table == 'Resources' and row['PrereqTech'] == 'TECH_INDUSTRIALIZATION'
               for table, row in facts)
    assert any(table == 'Resource_Consumption' and row['ImprovedExtractionRate'] == 3
               and row['PowerProvided'] == 4 and row['CO2perkWh'] == 820
               for table, row in facts)
    assert card['profile']['game_version'] == '1.0.12.54 (951533)'
    assert card['profile']['current_session_verified'] is False

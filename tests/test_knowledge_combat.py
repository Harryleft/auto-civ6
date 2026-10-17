"""Combat knowledge through the public build/query CLI; committed local snapshot only."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def command(script, *args):
    result = subprocess.run(
        [sys.executable, str(ROOT / 'civ6_knowledge' / script), *map(str, args)],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope='module')
def knowledge(tmp_path_factory):
    output = tmp_path_factory.mktemp('combat') / 'knowledge'
    command('build.py', '--source', ROOT / 'civ6_knowledge/sources/snapshot.json', '--output', output)
    return output


def query(root, *args):
    return command('query.py', '--root', root, *args)


def test_unit_base_movement_and_control_do_not_expand_into_summary(knowledge):
    warrior = query(knowledge, 'show', 'UNIT_WARRIOR', '--level', 'rule')
    assert warrior['topic'] == 'combat'
    assert warrior['rule']['facts']['BaseMoves'] == 2
    assert warrior['rule']['facts']['ZoneOfControl'] == 1
    assert warrior['rule']['facts']['Combat'] == 20
    assert query(knowledge, 'search', '勇士', '--topic', 'combat')['results'][0]['id'] == 'UNIT_WARRIOR'
    summary = query(knowledge, 'show', 'UNIT_WARRIOR')
    assert not {'rule', 'source_refs', 'evidence'} & summary.keys()

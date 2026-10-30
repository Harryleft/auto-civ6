"""Explicit archived sources start new runs; ordinary recovery stays scoped."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from civ_mcp import checkpoints, game_launcher
from test_server import TestSaveTools as SaveToolFixture
from test_server import isolated_server  # Reuse its temporary save/registry paths.


def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoints,'CHECKPOINT_DIR',tmp_path/'records')
    monkeypatch.setattr(game_launcher,'SINGLE_SAVE_DIR',str(tmp_path))
    assert checkpoints.begin_baseline_run('benchmark_gold_tun_1') is None
    (tmp_path/'source.Civ6Save').write_bytes(b'original completed historical save')
    checkpoints.mark_complete('source')
    assert checkpoints.record_checkpoint('source',('sumeria',-1896272000),150) is None
    record=json.loads((checkpoints.CHECKPOINT_DIR/(hashlib.sha256(b'source').hexdigest()+'.json')).read_text())
    assert checkpoints.begin_baseline_run('benchmark_gold_tun_1') is None
    return record


def test_explicit_historical_source_never_becomes_ordinary_old_run_recovery(tmp_path,monkeypatch):
    record=archive(tmp_path,monkeypatch)
    assert checkpoints.expected_checkpoint('source') is None
    before={p:p.read_bytes() for p in checkpoints.CHECKPOINT_DIR.glob('*.json')}
    source=checkpoints.archived_checkpoint('source',record['run_id'],record['sha256'])
    assert source['turn']==150
    assert all(p.read_bytes()==v for p,v in before.items())
    assert checkpoints.begin_archived_run('source',record['run_id'],record['sha256']) is None
    current=json.loads((checkpoints.CHECKPOINT_DIR/'current-run.json').read_text())
    assert current['run_id']!=record['run_id'] and current['source_checkpoint']['run_id']==record['run_id']
    assert checkpoints.expected_checkpoint('source') is None
    for p,v in before.items():
        if p.name!='current-run.json': assert p.read_bytes()==v
    (tmp_path/'new.Civ6Save').write_bytes(b'new test result')
    checkpoints.mark_complete('new')
    assert checkpoints.record_checkpoint('new',('sumeria',-1896272000),151) is None
    assert checkpoints.expected_checkpoint('new')==('sumeria',-1896272000,151)


@pytest.mark.parametrize('change',['hash','run','file','unregistered'])
def test_archived_source_requires_exact_registered_identity_and_bytes(tmp_path,monkeypatch,change):
    record=archive(tmp_path,monkeypatch)
    run,sha,name=record['run_id'],record['sha256'],'source'
    if change=='hash':sha='f'*64
    if change=='run':run='e'*32
    if change=='file':(tmp_path/'source.Civ6Save').write_bytes(b'changed')
    if change=='unregistered':name='unknown'
    marker=(checkpoints.CHECKPOINT_DIR/'current-run.json').read_bytes()
    assert checkpoints.archived_checkpoint(name,run,sha) is None
    assert checkpoints.begin_archived_run(name,run,sha).startswith('ERR:OUTCOME_UNKNOWN')
    assert (checkpoints.CHECKPOINT_DIR/'current-run.json').read_bytes()==marker


@pytest.mark.parametrize('failure',['success','wrong_world','changed_file','pending','incomplete_arguments'])
def test_real_loader_only_rotates_run_after_complete_historical_confirmation(monkeypatch,failure):
    # The existing fixture supplies the real GameState/native-menu adapter.
    _,game=SaveToolFixture().prepare_real_menu_load()
    # Fixture identity is Rome; explicitly map its test-only origin.
    monkeypatch.setitem(checkpoints._BASELINES,'test_origin',('0'*64,('rome',4242,1)))
    name='MySave'
    record_path=checkpoints.CHECKPOINT_DIR/(hashlib.sha256(name.encode()).hexdigest()+'.json')
    record=json.loads(record_path.read_text())
    assert checkpoints.begin_baseline_run() is None
    marker=(checkpoints.CHECKPOINT_DIR/'current-run.json').read_bytes()
    options={'source_run_id':record['run_id'],'expected_sha256':record['sha256']}
    if failure=='pending':game._pending_save=True
    if failure=='incomplete_arguments':options.pop('expected_sha256')
    write=game.conn.execute_write
    if failure=='wrong_world':game.conn.identity=('rome',99)
    if failure=='changed_file':
        async def changed(query,timeout=None):
            value=await write(query,timeout)
            if 'local tr = p:GetTreasury()' in query:
                (Path(game_launcher.SINGLE_SAVE_DIR)/'MySave.Civ6Save').write_bytes(b'changed while loading')
            return value
        game.conn.execute_write=changed
    result=asyncio.run(game.load_game_save(name,**options))
    if failure=='success':
        assert result.startswith('Loaded MySave:') and not game.replacement_pending
        current=json.loads((checkpoints.CHECKPOINT_DIR/'current-run.json').read_text())
        assert current['run_id']!=record['run_id'] and current['source_checkpoint']['name']=='MySave'
        assert record_path.read_text() and json.loads(record_path.read_text())==record
    else:
        assert not result.startswith('Loaded ')
        assert (checkpoints.CHECKPOINT_DIR/'current-run.json').read_bytes()==marker
        if failure in {'pending','incomplete_arguments'}:assert not game.conn.load_calls
        else:assert len(game.conn.load_calls)==1 and game.replacement_pending

"""Domain commands and automated state checks over real isolated stdio."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from civ_mcp.devtools.commands import HelpRequested, parse_command
from civ_mcp.devtools.evidence import CommandError, Evidence
from civ_mcp import server
from civ_mcp.lua import CityInfo
from test_debug_cli import MOVE, ROOT, run_fake
from test_server import make_ctx, make_overview


@pytest.mark.parametrize('line,tool,arguments,dry_run', [
    ('unit move 42 22 9 --dry-run', 'unit_action', {'unit_id':42,'action':'move','target_x':22,'target_y':9}, True),
    ('unit action 42 improve --improvement IMPROVEMENT_FARM --dry-run', 'unit_action',
     {'unit_id':42,'action':'improve','improvement':'IMPROVEMENT_FARM'}, True),
    ('city production 65536', 'get_city_production', {'city_id':65536}, False),
    ('city set-production 1 DISTRICT DISTRICT_CAMPUS --x 2 --y 3 --dry-run', 'set_city_production',
     {'city_id':1,'item_type':'DISTRICT','item_name':'DISTRICT_CAMPUS','target_x':2,'target_y':3}, True),
    ('research set CIVIC_CODE_OF_LAWS --category civic --dry-run', 'set_research',
     {'tech_or_civic':'CIVIC_CODE_OF_LAWS','category':'civic'}, True),
    ('turn end --dry-run', 'end_turn', {}, True),
    ('save create "new independent name" --dry-run', 'save_game', {'save_name':'new independent name'}, True),
    ('save reload checkpoint --dry-run', 'reload_game_save', {'save_name':'checkpoint'}, True),
    ('save load checkpoint --dry-run', 'load_game_save', {'save_name':'checkpoint'}, True),
    ('knowledge search "writing rules" --limit 2', 'search_knowledge', {'query':'writing rules','limit':2}, False),
    ('knowledge show TECH_WRITING --level rule', 'get_knowledge', {'entry_id':'TECH_WRITING','level':'rule'}, False),
])
def test_typed_arguments_preserve_native_tool_contract(line, tool, arguments, dry_run):
    intent = parse_command(line)
    assert (intent.tool, intent.arguments, intent.dry_run) == (tool, arguments, dry_run)


@pytest.mark.parametrize('line', ['unit move 0 2', 'unit action 0 move --x 2',
    'city set-production 1 WRONG X', 'save create', 'turn end --force', 'unit move bad 2 3',
    'knowledge search "unclosed'])
def test_bad_domain_arguments_have_repair_hints(line):
    with pytest.raises(CommandError) as error:
        parse_command(line)
    assert error.value.code == 'INVALID_DEBUG_ARGUMENT' and error.value.hint


def test_nested_help_is_one_level_and_has_no_call():
    with pytest.raises(HelpRequested) as help_request:
        parse_command('unit --help')
    doc = help_request.value.document
    assert [child['name'] for child in doc['subcommands']] == ['list', 'move', 'action']
    assert '--dry-run' not in doc['usage']
    with pytest.raises(HelpRequested) as help_request:
        parse_command('unit move --help')
    assert '--dry-run' in help_request.value.document['usage']


def test_domain_commands_reuse_real_pending_owner(tmp_path):
    move = 'unit move 0 22 9\n'
    status, state, events, engine, _ = run_fake(tmp_path,
        'help unit\nunit move --help\nunit move 0 22 9 --dry-run\n' + move +
        'pending\nunit move 0 22 9 --dry-run\nverify state\npending\n' + move + 'pending\nquit\n')
    assert status == 'PASS' and engine['submissions'] == 1
    assert [r['runtime']['has_pending'] for r in events if r['event']=='pending'] == [True,True,False]
    verification = next(r for r in events if r['event']=='verification')
    assert verification['status']=='BLOCKED' and verification['errors'][0]['code']=='VERIFY_PENDING'
    assert state['tool_calls']==2  # blocked verification makes no game calls


@pytest.mark.parametrize('scenario,expected', [('ready','PASS'), ('changed-turn','BLOCKED'), ('read-error','FAIL')])
def test_automatic_verification_does_not_accept_changed_or_unreadable_world(tmp_path, scenario, expected):
    _, state, events, engine, evidence = run_fake(tmp_path, 'verify state\nquit\n', scenario=scenario)
    verification = state['verification']
    assert verification['status']==expected
    assert engine['submissions']==0
    expected_tools = ['get_game_overview'] if scenario=='read-error' else ['get_game_overview','get_game_snapshot']
    assert [r['tool'] for r in events if r['event']=='tool_started'] == expected_tools
    assert json.loads((evidence.path/'verification.json').read_text()) == verification
    if expected=='PASS':
        assert verification['identity'] == {'civilization':'rome','seed':4242,'player_id':0,'turn':12}
    else:
        assert verification['errors'][0]['hint']


@pytest.mark.parametrize('flag', ['_pending_save','_pending_load','_pending_end_turn'])
def test_snapshot_refuses_pending_before_any_engine_io(flag):
    from civ_mcp.game_state import GameState
    from test_action_confirmation import Engine
    engine = Engine()
    game = GameState(engine)
    setattr(game, flag, True)
    if flag=='_pending_end_turn':
        # Compatibility property delegates to the real owner.
        assert game.turn_pending
    ctx = make_ctx(game=game)
    result = json.loads(asyncio.run(server.get_game_snapshot(ctx)))
    assert result['status']=='blocked' and any(result['pending'].values())
    assert engine.reads==[] and engine.submissions==[]


def test_snapshot_checks_city_ownership_and_omitted_offmap_units():
    ctx = make_ctx()
    game = ctx.request_context.lifespan_context.game
    game.pending_action_summary = None
    game.congress_pending = False
    overview = replace(make_overview(), num_cities=1, num_units=1, civilization_type='CIVILIZATION_ROME', leader_type='LEADER_TRAJAN')
    city = CityInfo(1,'Rome',1,2,3,4,5,6,7,8,9,10,1,2,owner_id=0,game_identity=('rome',4242))
    game.script.update(get_game_identity=('rome',4242),get_game_overview=overview,get_units=[],get_cities=([city],[]))
    result = json.loads(asyncio.run(server.get_game_snapshot(ctx)))
    assert result['status']=='ready' and result['checks']['unit_count_range']
    city.owner_id=1
    result = json.loads(asyncio.run(server.get_game_snapshot(ctx)))
    assert result['status']=='unknown' and not result['checks']['city_scope']


@pytest.mark.parametrize('turn,code', [(12,0),(99,2)])
def test_verify_state_cli_has_one_json_result_and_expected_world_guard(tmp_path, turn, code):
    import subprocess
    import sys
    script = tmp_path/'driver.py'
    script.write_text('''
import sys
from mcp import StdioServerParameters
from civ_mcp.devtools import debug
from civ_mcp.cli import main
original = debug.serve
async def inject(*args, **kwargs):
    kwargs['server'] = StdioServerParameters(command=sys.executable,
        args=[%r, '--receipt', %r, '--snapshot', 'ready'], cwd=%r)
    return await original(*args, **kwargs)
debug.serve = inject
raise SystemExit(main())
''' % (str(ROOT/'tests/fake_debug_server.py'),str(tmp_path/'engine.json'),str(ROOT)))
    proof = tmp_path/'proof'
    result = subprocess.run([sys.executable,str(script),'verify','state','--expect-turn',str(turn),
        '--expect-civilization','CIVILIZATION_ROME','--output',str(proof)], cwd=ROOT,
        capture_output=True,text=True,timeout=20)
    assert result.returncode==code, result.stdout+result.stderr
    summary = json.loads(result.stdout)
    assert summary == json.loads((proof/'summary.json').read_text())
    assert summary['mode']=='live' and summary['data']['mcp_session_closed']
    assert summary['game_verification']==('passed_readonly' if code==0 else 'unconfirmed_readonly')
    if code:
        assert summary['errors'][0]['code']=='UNEXPECTED_WORLD'
    assert json.loads((tmp_path/'engine.json').read_text())['submissions']==0


def test_snapshot_does_not_consume_real_pending_action_confirmation(tmp_path):
    status, state, events, engine, _ = run_fake(tmp_path,
        MOVE + 'state snapshot\npending\nquit --abandon\n')
    result = next(row for row in events if row['event']=='tool_result' and row['tool']=='get_game_snapshot')
    snapshot = json.loads(result['result']['content'][0]['text'])
    assert snapshot['status']=='blocked' and snapshot['pending']['action']
    assert engine['submissions']==1 and engine['remaining_reads']==1
    assert status=='BLOCKED' and state['runtime']['pending']['action']


def test_typed_commands_cannot_bypass_offline_policy(tmp_path):
    from io import StringIO
    from mcp import StdioServerParameters
    from civ_mcp.devtools.debug import serve
    import sys
    evidence = Evidence(ROOT,['offline-domain-test'],str(tmp_path/'proof'))
    state,events={},[]
    params=StdioServerParameters(command=sys.executable,
        args=[str(ROOT/'tests/fake_debug_server.py'),'--receipt',str(tmp_path/'engine.json')],cwd=str(ROOT))
    status=asyncio.run(serve(evidence,state,offline=True,timeout=5,
        source=StringIO('unit move 0 22 9 --dry-run\nunit move 0 22 9\ncity list\nturn end\nquit\n'),
        emit=events.append,server=params))
    assert status=='PASS' and state['tool_calls']==0
    assert len([row for row in events if row.get('code')=='OFFLINE_ONLY'])==3
    assert json.loads((tmp_path/'engine.json').read_text())['submissions']==0

"""Complete named contracts and CLI-only diagnostics over real isolated stdio."""
import argparse
import asyncio
from io import StringIO
import json

from jsonschema import Draft202012Validator
import pytest

from civ_mcp import server
from civ_mcp.devtools.commands import GROUPS, domain_parser, parse_command
from civ_mcp.devtools.debug import hotseat_server, serve
from civ_mcp.devtools.evidence import CommandError, Evidence
from test_debug_cli import ROOT, MOVE, run_fake


CASES = [
    ('session status', 'get_runtime_status', {}),
    ('game snapshot', 'get_game_snapshot', {}),
    ('game launch --dry-run', 'launch_game', {}),
    ('game stop --dry-run', 'kill_game', {}),
    ('game recover checkpoint --dry-run', 'restart_and_load', {'save_name':'checkpoint'}),
    ('save load-index 2 --dry-run', 'load_save', {'save_index':2}),
    ('save menu-load checkpoint --dry-run', 'load_save_from_menu', {'save_name':'checkpoint'}),
    ('map settle 42', 'get_settle_advisor', {'unit_id':42}),
    ('map settle-global', 'get_global_settle_advisor', {}),
    ('map strategic', 'get_strategic_map', {}),
    ('city wonder 1 BUILDING_PYRAMIDS', 'get_wonder_advisor', {'city_id':1,'wonder_name':'BUILDING_PYRAMIDS'}),
    ('city tiles 1', 'get_purchasable_tiles', {'city_id':1}),
    ('city buy-tile 1 3 4 --dry-run', 'purchase_tile', {'city_id':1,'x':3,'y':4}),
    ('city decision 1 keep --dry-run', 'city_action', {'city_id':1,'action':'keep'}),
    ('great-person advisor 42', 'get_gp_advisor', {'unit_index':42}),
    ('era choose 0 --dry-run', 'choose_dedication', {'dedication_index':0}),
    ('religion spread', 'get_religion_spread', {}),
    ('religion found RELIGION_TAOISM BELIEF_A BELIEF_B --dry-run', 'found_religion', {'religion_type':'RELIGION_TAOISM','follower_belief':'BELIEF_A','founder_belief':'BELIEF_B'}),
    ('diplomacy pending', 'get_pending_diplomacy', {}),
    ('diplomacy respond 1 POSITIVE --dry-run', 'respond_to_diplomacy', {'other_player_id':1,'response':'POSITIVE'}),
    ('diplomacy close 1 --dry-run', 'close_diplomacy_session', {'other_player_id':1}),
    ('diplomacy action 1 DECLARE_FRIENDSHIP --dry-run', 'send_diplomatic_action', {'other_player_id':1,'action':'DECLARE_FRIENDSHIP'}),
    ('diplomacy peace 1 --dry-run', 'propose_peace', {'other_player_id':1}),
    ('diplomacy alliance 1 --type RESEARCH --dry-run', 'form_alliance', {'other_player_id':1,'alliance_type':'RESEARCH'}),
    ('trade pending', 'get_pending_trades', {}),
    ('trade routes', 'get_trade_routes', {}),
    ('trade destinations 42', 'get_trade_destinations', {'unit_id':42}),
    ('trade options 1', 'get_trade_options', {'other_player_id':1}),
    ('trade respond 1 --accept --dry-run', 'respond_to_trade', {'other_player_id':1,'accept':True}),
    ('trade respond 1 --reject --dry-run', 'respond_to_trade', {'other_player_id':1,'accept':False}),
    ('trade negotiate 1 --fingerprint abc --our-gold 20 --their-gpt 3 --mode equalize --dry-run', 'negotiate_incoming_trade', {'other_player_id':1,'expected_fingerprint':'abc','our_gold':20,'their_gpt':3,'mode':'equalize'}),
    ('spy list', 'get_spies', {}),
    ('spy action 42 counterspy 3 4 --dry-run', 'spy_action', {'unit_id':42,'action':'counterspy','target_x':3,'target_y':4}),
    ('congress open --dry-run', 'open_world_congress', {}),
    ('congress votes \'[{"hash":42,"option":1,"target":0,"votes":1}]\' --dry-run', 'queue_wc_votes', {'votes':'[{"hash":42,"option":1,"target":0,"votes":1}]'}),
    ('popup dismiss --dry-run', 'dismiss_popup', {}),
    ('developer lua "print(1)" --context ingame --dry-run', 'run_lua', {'code':'print(1)','context':'ingame'}),
]


@pytest.mark.parametrize('line,tool,arguments', CASES)
def test_new_named_commands_match_actual_public_contract(line, tool, arguments):
    intent=parse_command(line)
    assert (intent.tool,intent.arguments)==(tool,arguments)
    Draft202012Validator(server.mcp._tool_manager.get_tool(tool).parameters).validate(intent.arguments)


def test_every_base_mcp_tool_has_a_named_cli_entry():
    mapped=set()
    for group in GROUPS:
        for action in domain_parser(group)._actions:
            if isinstance(action,argparse._SubParsersAction):
                mapped.update(child.get_default('tool')for child in action.choices.values())
    assert {t.name for t in server.mcp._tool_manager.list_tools()} <= mapped


def test_project_default_does_not_register_a_direct_civ6_server():
    assert 'civ6' not in json.loads((ROOT/'.mcp.json').read_text()).get('mcpServers',{})


def test_complete_outgoing_deal_keeps_native_field_names_and_types():
    intent=parse_command('trade propose 1 --offer-gold 20 --offer-gold-per-turn 2 --request-resources RESOURCE_SILK --offer-open-borders --request-open-borders --joint-war-target 3 --joint-war-type -42 --mode test --dry-run')
    args=intent.arguments
    assert args['offer_gold']==20 and args['offer_gold_per_turn']==2
    assert args['offer_open_borders']is True and args['request_open_borders']is True
    assert args['request_resources']=='RESOURCE_SILK' and args['joint_war_type']==-42
    assert intent.dry_run
    Draft202012Validator(server.mcp._tool_manager.get_tool(intent.tool).parameters).validate(args)


@pytest.mark.parametrize('line', ['trade respond 1','trade respond 1 --accept --reject','trade negotiate 1',
    'diplomacy respond 1','city buy-tile 1 2','era choose bad','developer lua', 'congress votes'])
def test_incomplete_intents_are_rejected_before_submission(line):
    with pytest.raises(CommandError):parse_command(line)


def test_new_mutation_previews_use_actual_stdio_without_game_submission(tmp_path):
    lines=[line for line,_,_ in CASES if '--dry-run'in line and not line.startswith('developer')]
    lines+=['trade propose 1 --offer-gold 20 --mode test --dry-run']
    status,state,events,engine,_=run_fake(tmp_path,'\n'.join(lines)+'\npending\nquit\n')
    assert status=='PASS' and state['tool_calls']==0 and engine['submissions']==0
    assert len([e for e in events if e['event']=='dry_run'])==len(lines)
    assert not any(e['event']=='command_blocked'for e in events)


def test_default_lua_gate_keeps_original_pending_and_hides_raw_tool(tmp_path):
    status,state,events,engine,_=run_fake(tmp_path,'tools\n'+MOVE+
        'developer lua "print(1)" --dry-run\ncall run_lua {"code":"print(1)"}\npending\n'+MOVE+'quit\n')
    assert status=='PASS' and engine['submissions']==1
    assert 'run_lua' not in next(e['tools']for e in events if e['event']=='tools')
    assert [e['code']for e in events if e['event']=='command_blocked']==['DEVELOPER_MODE_REQUIRED']*2
    assert next(e['runtime']['has_pending']for e in events if e['event']=='pending')is True
    assert not any(e.get('tool')=='run_lua'for e in events if e['event']=='tool_started')


def test_lua_schema_preview_requires_explicit_developer_session(tmp_path):
    status,state,events,engine,_=run_fake(tmp_path,'developer lua "print(1)" --dry-run\nquit\n',developer=True)
    assert status=='PASS' and state['developer']is True and state['tool_calls']==0
    assert engine['submissions']==0
    assert any(e['event']=='dry_run'and e.get('tool')=='run_lua'for e in events)


def test_hotseat_cli_uses_real_controller_stdio_but_previews_do_not_touch_game(tmp_path):
    run=tmp_path/'hotseat';(run/'evidence').mkdir(parents=True)
    (run/'evidence/patch-manifest.json').write_text('{}')
    params,pending=hotseat_server(ROOT,0,run)
    evidence=Evidence(ROOT,['hotseat-cli-preview'],str(tmp_path/'proof'))
    state,events={},[]
    commands='hotseat begin --dry-run\nhotseat end --dry-run\nhotseat actions \'[]\' --dry-run\npending\nquit\n'
    status=asyncio.run(serve(evidence,state,offline=False,timeout=10,source=StringIO(commands),emit=events.append,server=params,shared_pending_path=pending))
    assert status=='PASS' and state['tool_calls']==0 and state['mcp_session_closed']
    assert state['runtime']['connected']is False and state['runtime']['shared_pending']is None
    assert len([e for e in events if e['event']=='dry_run'])==3
    assert not pending.exists()


def test_hotseat_existing_or_unverifiable_owner_is_not_replaced(tmp_path):
    with pytest.raises(CommandError,match='existing verified'):hotseat_server(ROOT,0,tmp_path)
    (tmp_path/'evidence').mkdir();(tmp_path/'evidence/patch-manifest.json').write_text('{}')
    pending=tmp_path/'evidence/shared-pending.json';pending.write_text('{broken')
    with pytest.raises(CommandError,match='still owns'):hotseat_server(ROOT,0,tmp_path)
    assert pending.read_text()=='{broken'


def test_shared_hotseat_pending_prevents_clean_exit_even_when_memory_is_empty(tmp_path):
    pending=tmp_path/'shared.json';pending.write_text('{"tool":"end_hotseat_turn","phase":"submitted"}')
    status,state,events,engine,_=run_fake(tmp_path,'pending\ngame snapshot\nverify state\nquit\nquit --abandon\n',shared_pending_path=pending)
    assert status=='BLOCKED' and engine['submissions']==0
    assert not state['runtime']['has_pending'] and state['runtime']['shared_pending']['phase']=='submitted'
    assert any(e.get('code')=='PENDING_ON_EXIT'for e in events)
    assert state['tool_calls']==0 and state['verification']['errors'][0]['code']=='VERIFY_PENDING'
    assert pending.exists()


def test_normal_game_cannot_silently_use_hotseat_handoff(tmp_path):
    status,state,events,engine,_=run_fake(tmp_path,'hotseat end --dry-run\nquit\n')
    assert status=='PASS' and state['tool_calls']==0 and engine['submissions']==0
    assert any(e.get('code')=='HOTSEAT_MODE_REQUIRED'for e in events)

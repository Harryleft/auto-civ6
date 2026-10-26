"""Historical high-frequency commands checked against real MCP input schemas."""

import json

import pytest
from jsonschema import Draft202012Validator

from civ_mcp import server
from civ_mcp.devtools.commands import parse_command
from civ_mcp.devtools.evidence import CommandError
from test_debug_cli import run_fake


CASES = [
    ('unit rebase 42 3 4 --dry-run','unit_action',{'unit_id':42,'action':'rebase','target_x':3,'target_y':4},True),
    ('unit attack 42 3 4 --dry-run','unit_action',{'unit_id':42,'action':'attack','target_x':3,'target_y':4},True),
    ('unit skip 42 --dry-run','unit_action',{'unit_id':42,'action':'skip'},True),
    ('unit fortify 42 --dry-run','unit_action',{'unit_id':42,'action':'fortify'},True),
    ('unit heal 42 --dry-run','unit_action',{'unit_id':42,'action':'heal'},True),
    ('unit improve 42 IMPROVEMENT_FARM --dry-run','unit_action',{'unit_id':42,'action':'improve','improvement':'IMPROVEMENT_FARM'},True),
    ('unit skip-remaining --dry-run','skip_remaining_units',{},True),
    ('unit promotions 42','get_unit_promotions',{'unit_id':42},False),
    ('unit promote 42 PROMOTION_BATTLECRY --dry-run','promote_unit',{'unit_id':42,'promotion_type':'PROMOTION_BATTLECRY'},True),
    ('unit upgrade 42 --dry-run','upgrade_unit',{'unit_id':42},True),
    ('unit path 42 3 4','get_pathing_estimate',{'unit_id':42,'target_x':3,'target_y':4},False),
    ('unit tasks','get_builder_tasks',{},False),
    ('city attack 65536 3 4 --dry-run','city_action',{'city_id':65536,'action':'attack','target_x':3,'target_y':4},True),
    ('city district 65536 DISTRICT_CAMPUS','get_district_advisor',{'city_id':65536,'district_type':'DISTRICT_CAMPUS'},False),
    ('city focus 65536 production --dry-run','set_city_focus',{'city_id':65536,'focus':'production'},True),
    ('city buy 65536 BUILDING BUILDING_GRANARY --dry-run','purchase_item',{'city_id':65536,'item_type':'BUILDING','item_name':'BUILDING_GRANARY','yield_type':'YIELD_GOLD'},True),
    ('city buy 65536 UNIT UNIT_BUILDER --yield YIELD_FAITH --dry-run','purchase_item',{'city_id':65536,'item_type':'UNIT','item_name':'UNIT_BUILDER','yield_type':'YIELD_FAITH'},True),
    ('map area 3 4 --radius 4','get_map_area',{'center_x':3,'center_y':4,'radius':4},False),
    ('map area 3 4','get_map_area',{'center_x':3,'center_y':4,'radius':2},False),
    ('victory show','get_victory_progress',{},False),
    ('policy show','get_policies',{},False),
    ('policy set 0=POLICY_AGOGE 1=NONE --dry-run','set_policies',{'assignments':'0=POLICY_AGOGE,1=NONE'},True),
    ('policy set "0=POLICY_AGOGE, 1=NONE" --dry-run','set_policies',{'assignments':'0=POLICY_AGOGE,1=NONE'},True),
    ('policy set 0=POLICY_AGOGE, 1=NONE --dry-run','set_policies',{'assignments':'0=POLICY_AGOGE,1=NONE'},True),
    ('city-state list','get_city_states',{},False),
    ('city-state envoy 5 --dry-run','send_envoy',{'player_id':5},True),
    ('governor list','get_governors',{},False),
    ('governor appoint GOVERNOR_THE_EDUCATOR --dry-run','appoint_governor',{'governor_type':'GOVERNOR_THE_EDUCATOR'},True),
    ('governor assign GOVERNOR_THE_EDUCATOR 65536 --dry-run','assign_governor',{'governor_type':'GOVERNOR_THE_EDUCATOR','city_id':65536},True),
    ('governor assign GOVERNOR_THE_EDUCATOR 65536 --owner 5 --dry-run','assign_governor',{'governor_type':'GOVERNOR_THE_EDUCATOR','city_id':65536,'target_owner':5},True),
    ('governor promote GOVERNOR_THE_EDUCATOR GOVERNOR_PROMOTION_LIBRARIAN --dry-run','promote_governor',{'governor_type':'GOVERNOR_THE_EDUCATOR','promotion_type':'GOVERNOR_PROMOTION_LIBRARIAN'},True),
    ('congress show','get_world_congress',{},False),
    ('notifications show','get_notifications',{},False),
]


@pytest.mark.parametrize('line,tool,arguments,dry_run', CASES)
def test_commands_map_to_real_mcp_contract(line, tool, arguments, dry_run):
    intent=parse_command(line)
    assert (intent.tool,intent.arguments,intent.dry_run)==(tool,arguments,dry_run)
    schema=server.mcp._tool_manager.get_tool(tool).parameters
    assert set(arguments)<=set(schema['properties'])
    Draft202012Validator(schema).validate(arguments)


@pytest.mark.parametrize('line', ['map area 2 3 --radius 0','map area 2 3 --radius 5',
    'unit attack 42 3','unit improve 42','city buy 1 PROJECT PROJECT_TEST',
    'city focus 1 wrong','policy set 0=POLICY_A,0=POLICY_B --dry-run',
    'policy set 0=POLICY_A 00=NONE --dry-run','policy set 0=POLICY_A,bad --dry-run',
    'policy set 0=POLICY_A,,1=NONE --dry-run','policy set wrong --dry-run',
    'governor assign GOVERNOR_THE_EDUCATOR','city attack 1 2'])
def test_invalid_commands_fail_locally_with_repair_hints(line):
    with pytest.raises(CommandError) as error:
        parse_command(line)
    assert error.value.code=='INVALID_DEBUG_ARGUMENT' and error.value.hint


def test_every_mutation_preview_uses_real_stdio_without_game_submission(tmp_path):
    lines=[line for line,_,_,dry_run in CASES if dry_run]
    status,state,events,engine,_=run_fake(tmp_path,'\n'.join(lines)+'\npending\nquit\n')
    assert status=='PASS' and engine['submissions']==0 and state['tool_calls']==0
    assert len([row for row in events if row['event']=='dry_run'])==len(lines)
    assert not any(row['event']=='command_blocked' for row in events)

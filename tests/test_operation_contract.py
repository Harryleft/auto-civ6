"""Shared static contracts keep CLI previews and dispatch aligned without game I/O."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.devtools.commands import parse_command
from civ_mcp.devtools.evidence import CommandError
from civ_mcp.operations import OPERATIONS, OFFLINE_TOOLS, operation, tool_annotations, validate_arguments
from test_server import make_ctx, app_of


def test_every_registered_tool_declares_its_static_capabilities():
    for tool in server.mcp._tool_manager.list_tools():
        assert tool.name in OPERATIONS
        assert bool(tool.annotations and tool.annotations.readOnlyHint) == operation(tool.name).readonly
    assert {name for name, spec in OPERATIONS.items() if not spec.game_io} == OFFLINE_TOOLS
    # A name resembling a query cannot acquire a read/gate exemption by accident.
    assert not operation('get_unregistered_mutation').readonly
    assert not operation('get_unregistered_mutation').allows_pending('turn')
    with pytest.raises(KeyError):
        tool_annotations('get_unregistered_mutation')


def test_query_and_confirmation_capabilities_are_separate():
    assert operation('get_units').readonly and operation('get_units').may_confirm_pending
    assert operation('get_cities').may_confirm_pending
    assert operation('get_game_snapshot').readonly
    assert not operation('get_game_snapshot').may_confirm_pending
    assert operation('get_game_snapshot').reject_shared_pending
    assert not operation('get_runtime_status').game_io
    assert not operation('get_runtime_status').may_confirm_pending


@pytest.mark.parametrize('tool,allowed,blocked', [
    ('open_world_congress', 'turn', 'replacement'),
    ('respond_to_diplomacy', 'turn', 'congress'),
    ('propose_trade', 'diplomacy', 'turn'),
    ('dismiss_popup', 'action', 'save'),
    ('restart_and_load', 'replacement', None),
])
def test_explicit_blocker_permissions_keep_original_scope(tool, allowed, blocked):
    assert operation(tool).allows_pending(allowed)
    if blocked:
        assert not operation(tool).allows_pending(blocked)


@pytest.mark.parametrize('line,tool,args,path', [
    ('unit action 7 move --dry-run', 'unit_action', {'unit_id': 7, 'action': 'move'}, 'target_x'),
    ('unit action 7 rebase --x 3 --dry-run', 'unit_action', {'unit_id': 7, 'action': 'rebase', 'target_x': 3}, 'target_y'),
    ('unit action 7 improve --dry-run', 'unit_action', {'unit_id': 7, 'action': 'improve'}, 'improvement'),
    ('unit action 7 spread_religion --x 3 --dry-run', 'unit_action', {'unit_id': 7, 'action': 'spread_religion', 'target_x': 3}, 'target_y'),
    ('unit action 7 dance --dry-run', 'unit_action', {'unit_id': 7, 'action': 'dance'}, 'action'),
])
def test_named_parser_and_dispatch_share_static_rejections(line, tool, args, path):
    issues = validate_arguments(tool, args)
    assert issues[0]['path'] == [path]
    with pytest.raises(CommandError) as caught:
        parse_command(line)
    assert caught.value.details['issues'] == issues
    ctx = make_ctx()
    assert asyncio.run(getattr(server, tool)(ctx, **args)) == 'Error: ' + issues[0]['message']
    assert app_of(ctx).game.calls == []


def test_city_attack_refuses_missing_coordinate_without_dispatch():
    args = {'city_id': 7, 'action': 'attack', 'target_x': 3}
    assert validate_arguments('city_action', args)[0]['path'] == ['target_y']
    ctx = make_ctx()
    assert asyncio.run(server.city_action(ctx, **args)) == 'Error: attack requires target_x and target_y'
    assert app_of(ctx).game.calls == []


def test_static_validation_preserves_case_and_optional_argument_behaviour():
    assert validate_arguments('unit_action', {'action': 'MOVE', 'target_x': 0, 'target_y': 0}) == []
    assert validate_arguments('unit_action', {'action': 'spread_religion'}) == []
    assert validate_arguments('unit_action', {'action': 'skip', 'improvement': 'ignored'}) == []
    assert validate_arguments('city_action', {'action': 'ATTACK', 'target_x': 0, 'target_y': 0})


def test_invalid_action_cannot_confirm_a_pending_congress_request():
    ctx = make_ctx()
    game = app_of(ctx).game
    game.congress_pending = True
    result = asyncio.run(server.unit_action(ctx, 7, 'move'))
    assert result == 'Error: move requires target_x and target_y'
    assert result.outcome['status'] == 'not_submitted'
    assert game.calls == []


def test_real_stdio_named_and_compatibility_calls_share_argument_issues(tmp_path):
    from test_debug_cli import run_fake

    _, state, events, engine, _ = run_fake(tmp_path, '\n'.join([
        'unit action 7 move --dry-run',
        'call --dry-run unit_action {"unit_id":7,"action":"move"}',
        'call unit_action {"unit_id":7,"action":"move"}',
        'call --dry-run city_action {"city_id":7,"action":"attack"}',
        'quit',
    ]) + '\n')
    blocked = [row for row in events if row['event'] == 'command_blocked']
    assert len(blocked) == 4
    assert blocked[0]['details']['issues'] == blocked[1]['details']['issues'] == blocked[2]['details']['issues']
    assert blocked[3]['details']['issues'][0]['path'] == ['target_x']
    assert engine['submissions'] == 0 and state['tool_calls'] == 0


@pytest.mark.parametrize('internal,public', [
    ('diplomacy_respond', 'respond_to_diplomacy'),
    ('respond_to_deal', 'respond_to_trade'),
])
def test_internal_response_aliases_share_only_the_original_dispatch_permissions(internal, public):
    assert operation(internal) is operation(public)
    assert operation(internal).allows_pending('diplomacy')
    assert operation(internal).allows_pending('turn')
    for domain in ('congress', 'save', 'replacement', 'popup', 'action'):
        assert not operation(internal).allows_pending(domain)

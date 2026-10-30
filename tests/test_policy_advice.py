"""Policy candidates must agree with the existing native plan preflight."""
import asyncio

from jsonschema import Draft202012Validator

from civ_mcp import server
from civ_mcp.devtools.commands import parse_command
from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_policies
from test_policy_lua_execution import PolicyConn


def read(conn, policy=None):
    game=GameState(conn)
    return asyncio.run(game.get_policies() if policy is None else game.get_policies(policy))


def test_zero_cost_and_native_slot_permission_do_not_override_changed_this_turn():
    conn=PolicyConn();conn.lua.execute('unlockCost=0;policyMade=true')
    result=read(conn)
    assert result.can_change_now is False and result.change_reason=='POLICIES_ALREADY_CHANGED'
    assert 'POLICIES_ALREADY_CHANGED' in narrate_policies(result)
    assert conn.submissions==[] and conn.unlocks==[]


def test_obsolete_cards_are_excluded_from_default_and_explicit_inspection_explains_why():
    conn=PolicyConn();conn.lua.execute('obsolete[2]=true;culture.CanSlotPolicy=function()return true end')
    result=read(conn)
    assert 'POLICY_CIVIL_PRESTIGE' not in [p.policy_type for p in result.available_policies]
    candidate=read(conn,'POLICY_CIVIL_PRESTIGE').available_policies[0]
    assert candidate.obsolete is True and candidate.can_use_now is False
    assert candidate.reason=='POLICY_OBSOLETE'


def test_unknown_obsolete_check_is_visible_and_cannot_be_a_positive_candidate():
    conn=PolicyConn();conn.lua.execute('culture.IsPolicyObsolete=nil')
    result=read(conn,'POLICY_CIVIL_PRESTIGE')
    assert result.available_policies[0].obsolete is None
    assert result.available_policies[0].can_use_now is None
    assert 'unknown' in narrate_policies(result)


def test_single_policy_named_query_has_real_tool_schema():
    intent=parse_command('policy show --policy POLICY_CIVIL_PRESTIGE')
    assert intent.arguments=={'policy_type':'POLICY_CIVIL_PRESTIGE'}
    Draft202012Validator(server.mcp._tool_manager.get_tool(intent.tool).parameters).validate(intent.arguments)

"""Domain command parameters use the same public tool schemas and lifecycle."""
import json
import asyncio

import pytest
from jsonschema import validate

from civ_mcp.devtools.commands import parse_command
from civ_mcp import server, knowledge


@pytest.mark.parametrize("line,tool,parameters", [
    ('government show', 'get_policies', {}),
    ('government list', 'get_public_report', {'section':'governments','limit':50,'offset':0}),
    ('government change GOVERNMENT_AUTOCRACY --dry-run', 'change_government', {'government_type': 'GOVERNMENT_AUTOCRACY'}),
    ('religion pantheon', 'get_pantheon_beliefs', {}),
    ('religion choose-pantheon BELIEF_DIVINE_SPARK --dry-run', 'choose_pantheon', {'belief_type': 'BELIEF_DIVINE_SPARK'}),
    ('great-person recruit 1 --dry-run', 'recruit_great_person', {'individual_id': 1}),
    ('great-person buy 1 --currency gold --dry-run', 'patronize_great_person', {'individual_id': 1, 'yield_type': 'YIELD_GOLD'}),
    ('great-person buy 1 --currency faith --dry-run', 'patronize_great_person', {'individual_id': 1, 'yield_type': 'YIELD_FAITH'}),
    ('great-person reject 1 --dry-run', 'reject_great_person', {'individual_id': 1}),
    ('empire resources', 'get_empire_resources', {}),
    ('era show', 'get_game_overview', {}),
    ('ranking show', 'get_victory_progress', {}),
    ('civilization abilities CIVILIZATION_SUMERIA', 'get_knowledge', {'entry_id': 'CIVILIZATION_SUMERIA', 'level': 'rule'}),
    ('technology set TECH_WRITING --dry-run', 'set_research', {'tech_or_civic': 'TECH_WRITING', 'category': 'tech'}),
    ('civic set CIVIC_CRAFTSMANSHIP --dry-run', 'set_research', {'tech_or_civic': 'CIVIC_CRAFTSMANSHIP', 'category': 'civic'}),
])
def test_domain_commands_use_public_schema(line, tool, parameters):
    intent = parse_command(line)
    assert intent.tool == tool and intent.arguments == parameters
    native = next(t for t in server.mcp._tool_manager.list_tools() if t.name == tool)
    validate(intent.arguments, native.parameters)
    if '--dry-run' in line:
        assert intent.dry_run


@pytest.mark.parametrize("kind,query,filters,expected", [
    ('technology', 'TECH_POTTERY', ['food', 'housing'], 'TECH_POTTERY'),
    ('technology', '写作', ['science'], 'TECH_WRITING'),
    ('technology', 'TECH_IRRIGATION', ['improvement'], 'TECH_IRRIGATION'),
    ('civic', '技艺', ['production'], 'CIVIC_CRAFTSMANSHIP'),
])
def test_research_query(kind, query, filters, expected):
    def test_query():
        return asyncio.run(knowledge.research(kind, query, filters, 'all'))
    value = json.loads(test_query())
    assert value['status'] == 'found', value
    assert any(c['id'] == expected for c in value['results'])
    assert value['current_session_verified'] is False
    assert all(c['match_evidence'] for c in value['results'])


def test_research_multifilter_chinese_and_pagination():
    intent = parse_command('technology search --filter 食物 --filter 住房 --match all --limit 1 --offset 1')
    assert intent.arguments['filters'] == ['食物', '住房']
    value = json.loads(asyncio.run(knowledge.research(**intent.arguments)))
    assert value['total'] > 1 and len(value['results']) == 1 and value['offset'] == 1


def test_unknown_filter_rejected_without_loading_game():
    value = json.loads(asyncio.run(knowledge.research('technology', filters=['not-a-filter'])))
    assert value['status'] == 'error'


@pytest.mark.parametrize('status,code', [('error','TOOL_REPORTED_ERROR'),('unknown','QUERY_UNCONFIRMED')])
def test_structured_query_errors_remain_errors_in_cli(status,code):
    from civ_mcp.devtools.debug import tool_error
    from mcp.types import CallToolResult, TextContent
    result = CallToolResult(content=[TextContent(type='text',text=json.dumps({'status':status,'error':'unavailable','data':None}))])
    error = tool_error('get_public_report',result)
    assert error['code']==code and error['details']['source']=='structured_json'


def test_ready_empty_report_is_not_an_error():
    from civ_mcp.devtools.debug import tool_error
    from mcp.types import CallToolResult, TextContent
    result = CallToolResult(content=[TextContent(type='text',text='{"status":"ready","data":{"events":[]}}')])
    assert tool_error('get_public_report',result) is None


def test_research_search_through_mcp_remains_offline_during_pending(monkeypatch,tmp_path):
    from test_knowledge_mcp import session_run
    from test_knowledge_queries import build
    from test_server import FakeGameState
    root=build(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR',str(root))
    class PendingGame(FakeGameState):
        @property
        def save_pending(self):return True
        @property
        def replacement_pending(self):return True
        @property
        def congress_pending(self):return True
    game=PendingGame()
    async def run(session):
        result=await session.call_tool('search_research',{'kind':'technology','query':'TECH_WRITING'})
        value=json.loads(result.content[0].text)
        assert value['status']=='found' and value['current_session_verified'] is False
    session_run(monkeypatch,game,run)


def test_research_rejects_tampered_cached_rules(tmp_path):
    from test_knowledge_queries import build
    from civ_mcp import research_catalog
    root = build(tmp_path)
    rules = root / 'rules/technologies.json'
    rules.write_bytes(rules.read_bytes() + b' ')
    with pytest.raises(ValueError, match='fingerprint mismatch'):
        research_catalog.query(root, 'technology')


def test_research_cost_modifier_does_not_count_as_positive_yield():
    from civ_mcp.research_catalog import search
    snapshot = {'tables': {
        'ModifierArguments': [{'ModifierId': 'cost', 'Name': 'YieldType', 'Value': 'YIELD_FAITH'},
                              {'ModifierId': 'cost', 'Name': 'Amount', 'Value': 25}],
        'Modifiers': [{'ModifierId': 'cost', 'ModifierType': 'MODIFIER_ADJUST_PURCHASE_COST'}],
        'PolicyModifiers': [{'PolicyType': 'POLICY_TEST', 'ModifierId': 'cost'}],
    }}
    cards = [{'id': 'CIVIC_TEST', 'names': {'en_US': 'Test'}, 'summary': {},
              'rule': {'unlocks': [{'id': 'POLICY_TEST', 'entity_type': 'Policies'}]}}]
    value = search(snapshot, cards, filters=['faith'])
    assert value['status'] == 'not_found'

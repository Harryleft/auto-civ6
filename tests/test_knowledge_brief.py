"""Lossless rule projection and public CLI/MCP contracts, without a game."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from civ_mcp import server
from civ_mcp.devtools.commands import parse_command
from test_knowledge_queries import ROOT, cli
from test_knowledge_civilizations import build as build_civilizations, installed_fixture
from test_knowledge_mcp import session_run
from test_server import FakeGameState

spec = importlib.util.spec_from_file_location('knowledge_brief', ROOT / 'civ6_knowledge/brief.py')
brief = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief)


def test_every_committed_card_is_exactly_reconstructable_including_all_conditions():
    root = ROOT / 'civ6_knowledge'
    profile = json.loads((root/'manifest.json').read_text())['profile']
    count = 0
    for path in (root/'rules').glob('*.json'):
        for card in json.loads(path.read_text())['cards']:
            original = deepcopy(card)
            packed = brief.compact(card, profile)
            assert brief.expand(packed) == card, card['id']
            assert packed['encoding']['truncated'] is False
            assert card == original
            count += 1
    assert count > 1800  # Covers all ten current topics, not just early-game cards.


def test_common_fields_preserve_null_absent_false_zero_duplicates_and_unknown_metadata():
    records = [
        {'source_ref': {'table':'ModifierArguments','row':i},
         'facts': {'ModifierId':'M','Name':'Amount','Value':'-50','Extra':None, 'flag':value},
         'operation':'Update','where':{'Name':'Amount'},'source_scope':'unapplied'}
        for i,value in enumerate([False,0,True])]
    records.append(deepcopy(records[0]))  # Same source, separate observation.
    del records[-1]['facts']['Extra']
    assert brief.ungroup_records(brief.group_records(records)) == records
    assert 'flag' not in brief.group_records(records)['ModifierArguments']['common_facts']


def test_tampered_condition_is_rejected_by_the_full_card_fingerprint():
    card={'id':'T','rule':{'conditions':[{'value':-5}], 'related_records':[]}, 'source_refs':[]}
    data=brief.compact(card,{})
    data['rule']['conditions'][0]['value']=5
    with pytest.raises(ValueError,match='fingerprint'):
        brief.expand(data)


def test_conditional_installed_rules_keep_requirements_negative_values_and_unapplied_status(tmp_path):
    root=build_civilizations(tmp_path, installed_fixture())
    full=cli(root,'show','CIVILIZATION_INSTALLED','--level','rule')
    packed=cli(root,'show','CIVILIZATION_INSTALLED','--level','brief')
    restored=brief.expand(packed)
    assert restored==full
    assert packed['rule']['applied'] is False
    assert packed['rule']['conditions']==full['rule']['conditions']
    assert any(r['facts'].get('facts',r['facts']).get('Value')=='-50' for r in restored['rule']['related_records'])
    assert packed['profile']['sha256']==brief.fingerprint(json.loads((root/'manifest.json').read_text())['profile'])


def test_real_stdio_brief_matches_portable_cli_and_full_rules_without_game(monkeypatch, committed_knowledge_root):
    root=committed_knowledge_root
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR',str(root))
    ids=['CIVILIZATION_SUMERIA','LEADER_GILGAMESH','TECH_ARCHERY','DISTRICT_CAMPUS',
         'CIVIC_MERCENARIES','TECH_SATELLITES','UNIT_SPY']
    # Public lookups below use the actual built distribution; no captures or writes.
    async def scenario(session):
        for ident in ids:
            full=cli(root,'show',ident,'--level','rule')
            expected=cli(root,'show',ident,'--level','brief')
            result=await session.call_tool('get_knowledge',{'entry_id':ident,'level':'brief'})
            data=json.loads(result.content[0].text)
            assert data['status']=='found',data
            assert brief.expand(data)==full
            assert all(data[key]==value for key,value in expected.items())
            assert data['applicability']['current_session_verified'] is False
    session_run(monkeypatch,FakeGameState(),scenario)


def test_named_brief_and_historical_load_use_real_schemas():
    from jsonschema import Draft202012Validator
    for text in ('knowledge show TECH_ARCHERY --level brief','civilization abilities LEADER_GILGAMESH',
                 'save load example --source-run '+'a'*32+' --sha256 '+'b'*64):
        intent=parse_command(text)
        Draft202012Validator(server.mcp._tool_manager.get_tool(intent.tool).parameters).validate(intent.arguments)
    assert parse_command('civilization abilities LEADER_GILGAMESH').arguments['level']=='brief'

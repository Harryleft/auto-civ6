"""Offline knowledge through public MCP and temporary standalone knowledge data."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import anyio
import pytest
from mcp import ClientSession

from civ_mcp import server
from test_knowledge_queries import build
from test_server import FakeConn, FakeGameState, FakeLogger, app_of, make_ctx


def fingerprints(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def session_run(monkeypatch, game, callback):
    conn, logger = FakeConn(gamecore_index=None), FakeLogger()
    monkeypatch.setattr(server, 'GameConnection', lambda: conn)
    monkeypatch.setattr(server, 'GameState', lambda _: game)
    monkeypatch.setattr(server, 'GameLogger', lambda: logger)

    async def go():
        client_write, server_read = anyio.create_memory_object_stream(10)
        server_write, client_read = anyio.create_memory_object_stream(10)
        lowlevel = server.mcp._mcp_server
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(lowlevel.run, server_read, server_write,
                             lowlevel.create_initialization_options())
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                await callback(session)
            tasks.cancel_scope.cancel()

    anyio.run(go)
    assert not game.calls and not conn.read_queries and not conn.state_calls
    return logger


def test_mcp_search_then_progressive_read_uses_standalone_rules_without_a_game(monkeypatch, tmp_path):
    root = build(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    before = fingerprints(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    game = FakeGameState()

    async def scenario(session):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert {"search_knowledge", "get_knowledge", "get_turn_status"} <= tools.keys()
        assert tools["get_turn_status"].annotations.readOnlyHint is True
        assert tools["get_turn_status"].inputSchema.get("properties", {}) == {}
        for name in ('search_knowledge', 'get_knowledge'):
            assert tools[name].annotations.readOnlyHint is True
            assert tools[name].annotations.idempotentHint is True
        schema = tools['search_knowledge'].inputSchema
        assert schema['required'] == ['query']
        assert schema['properties']['limit']['minimum'] == 1
        assert schema['properties']['limit']['maximum'] == 20
        assert tools['get_knowledge'].inputSchema['required'] == ['entry_id']
        result = await session.call_tool('search_knowledge', {'query': '写作', 'topic': 'technologies'})
        assert not result.isError
        search = json.loads(result.content[0].text)
        assert search['status'] == 'found' and search['results'][0]['id'] == 'TECH_WRITING'
        assert search['profile'] == manifest['profile']
        assert search['snapshot']['sha256'] == manifest['snapshot_sha256']
        assert search['applicability']['current_session_verified'] is False
        assert search['applicability']['action_applicability'] == 'unverified'
        for level in ('summary', 'rule', 'evidence'):
            result = await session.call_tool('get_knowledge', {'entry_id': search['results'][0]['id'], 'level': level})
            assert not result.isError
            data = json.loads(result.content[0].text)
            assert data['id'] == 'TECH_WRITING' and data['status'] == 'found'
            assert data['snapshot'] == search['snapshot']
            if level == 'summary':
                assert data['names']['zh_Hans_CN'] == '写作'
                assert 'rule' not in data and 'evidence' not in data
            elif level == 'rule':
                assert data['rule']['prerequisites'][0]['id'] == 'TECH_POTTERY'
            else:
                assert data['source_refs'] and data['evidence']
                assert any(row['facts'].get('TechnologyType') == 'TECH_WRITING' for row in data['evidence'])

    logger = session_run(monkeypatch, game, scenario)
    assert [row[0] for row in logger.tool_calls] == ['search_knowledge', 'get_knowledge', 'get_knowledge', 'get_knowledge']
    assert fingerprints(root) == before


def test_search_keeps_cli_name_id_filter_and_truncation_semantics(monkeypatch, tmp_path):
    root = build(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    ctx = make_ctx(game=None)
    for query in ('写作', 'wRiTiNg', 'tech_writing'):
        data = json.loads(asyncio.run(server.search_knowledge(ctx, query)))
        assert data['results'][0]['id'] == 'TECH_WRITING'
    data = json.loads(asyncio.run(server.search_knowledge(ctx, 'TECH_', 'technologies', 1)))
    original = json.loads(subprocess.check_output([sys.executable, '-B', str(root / 'query.py'), '--root', str(root), 'search', 'TECH_', '--topic', 'technologies', '--limit', '1'], text=True))
    assert all(data[key] == value for key, value in original.items())
    assert (data['total'], data['limit'], data['truncated']) == (2, 1, True)
    assert json.loads(asyncio.run(server.search_knowledge(ctx, 'writing', 'civics')))['status'] == 'not_found'
    assert json.loads(asyncio.run(server.search_knowledge(ctx, '--root=/tmp/no', limit=20)))['status'] == 'not_found'
    missing = json.loads(asyncio.run(server.get_knowledge(ctx, '../arbitrary-file')))
    assert missing['status'] == 'error' and 'unknown ID' in missing['error']


@pytest.mark.parametrize('pending', ['action', 'save', 'load', 'end_turn'])
def test_only_knowledge_queries_bypass_pending_work_without_touching_it(monkeypatch, tmp_path, pending):
    root = build(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    ctx = make_ctx()
    app = app_of(ctx)
    game = app.game
    if pending == 'action':
        marker = object()
        game._pending_action = marker
        game.pending_action_entity = ('unit', 7)
        game.script['confirm_pending_action'] = 'ERR:OUTCOME_UNKNOWN|old move'
    else:
        setattr(game, '_pending_' + pending, True)
    for invoke in (server.search_knowledge(ctx, 'writing'), server.get_knowledge(ctx, 'TECH_WRITING', 'rule')):
        assert json.loads(asyncio.run(invoke))['status'] == 'found'
    assert not game.calls
    assert not game.conn.read_queries and not game.conn.state_calls
    assert game._pending_action is marker if pending == 'action' else getattr(game, '_pending_' + pending) is True
    blocked = asyncio.run(server.run_lua(ctx, 'print(42)'))
    assert 'OUTCOME_UNKNOWN' in blocked
    assert not game.called('run_lua') and not game.conn.read_queries
    if pending == 'action':
        assert game._pending_action is marker and game.pending_action_entity == ('unit', 7)
    else:
        assert getattr(game, '_pending_' + pending) is True


def test_mcp_schemas_reject_invalid_arguments_and_keep_whitespace_query_as_explicit_error(monkeypatch, tmp_path):
    root = build(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))

    async def scenario(session):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        topic = tools['search_knowledge'].inputSchema['properties']['topic']['anyOf'][0]['enum']
        assert set(topic) == {'civics', 'technologies', 'districts', 'congress', 'economy', 'resources', 'combat', 'diplomacy', 'victory', 'civilizations'}
        assert tools['get_knowledge'].inputSchema['properties']['level']['enum'] == ['summary', 'rule', 'evidence']
        for name, args in [
            ('search_knowledge', {'query': ''}),
            ('search_knowledge', {'query': 'writing', 'topic': 'unknown'}),
            ('search_knowledge', {'query': 'writing', 'limit': 0}),
            ('search_knowledge', {'query': 'writing', 'limit': 21}),
            ('search_knowledge', {'query': 'writing', 'limit': True}),
            ('search_knowledge', {'query': 'writing', 'limit': '2'}),
            ('search_knowledge', {'query': 'writing', 'limit': 1.5}),
            ('get_knowledge', {'entry_id': ''}),
            ('get_knowledge', {'entry_id': 'TECH_WRITING', 'level': 'everything'}),
        ]:
            result = await session.call_tool(name, args)
            assert result.isError
        result = await session.call_tool('search_knowledge', {'query': '   '})
        assert json.loads(result.content[0].text)['status'] == 'error'

    session_run(monkeypatch, FakeGameState(), scenario)


@pytest.mark.parametrize('body', [
    {'id': 'TECH_WRITING'},
    {'status': 'found', 'results': [], 'total': 1, 'limit': 5, 'truncated': False},
    {'status': 'not_found', 'results': [], 'total': 0, 'limit': 20, 'truncated': False},
])
def test_valid_json_with_missing_or_contradictory_result_is_not_reported_as_success(monkeypatch, tmp_path, body):
    root = build(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    (root / 'query.py').write_text('print(' + repr(json.dumps(body)) + ')\n')
    ctx = make_ctx(game=None)
    result = asyncio.run(server.get_knowledge(ctx, 'TECH_WRITING')) if 'id' in body else asyncio.run(server.search_knowledge(ctx, 'writing'))
    assert json.loads(result)['status'] == 'error'


def test_relative_environment_directory_is_explicit_error_instead_of_cwd_selection(monkeypatch, tmp_path):
    build(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', 'knowledge')
    result = json.loads(asyncio.run(server.search_knowledge(make_ctx(game=None), 'writing')))
    assert result['status'] == 'error' and 'absolute' in result['error']


@pytest.mark.parametrize('corruption', [
    'manifest_json', 'snapshot_hash', 'rule_hash', 'snapshot_missing', 'rules_missing',
    'script_missing', 'output_not_json', 'output_array', 'explicit_error', 'failed_exit',
])
def test_broken_knowledge_or_cli_is_explicit_error_and_is_not_repaired(monkeypatch, tmp_path, corruption):
    root = build(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    if corruption == 'manifest_json':
        (root / 'manifest.json').write_text('{bad')
    elif corruption == 'snapshot_hash':
        source = root / 'sources/snapshot.json'
        source.write_bytes(source.read_bytes() + b' ')
    elif corruption == 'rule_hash':
        source = root / 'rules/technologies.json'
        source.write_bytes(source.read_bytes() + b' ')
    elif corruption == 'snapshot_missing':
        (root / 'sources/snapshot.json').unlink()
    elif corruption == 'rules_missing':
        (root / 'rules/technologies.json').unlink()
    elif corruption == 'script_missing':
        (root / 'query.py').unlink()
    else:
        scripts = {'output_not_json': "print('not JSON')", 'output_array': "print('[]')",
                   'explicit_error': "print('{\"status\":\"error\",\"error\":\"source unavailable\"}')",
                   'failed_exit': "print('{\"id\":\"TECH_WRITING\"}'); raise SystemExit(2)"}
        (root / 'query.py').write_text(scripts[corruption] + '\n')
    before = fingerprints(root)
    ctx = make_ctx(game=None)
    for operation in (server.search_knowledge(ctx, 'writing'), server.get_knowledge(ctx, 'TECH_WRITING')):
        data = json.loads(asyncio.run(operation))
        assert data['status'] == 'error' and data['error']
        assert data['applicability']['action_applicability'] == 'unverified'
        assert 'results' not in data and 'summary' not in data
    assert fingerprints(root) == before


def test_cli_timeout_is_bounded_and_reaps_its_child_without_repair_or_retry(monkeypatch, tmp_path):
    from civ_mcp import knowledge

    spawned = []
    ready_times = []
    create_process = asyncio.create_subprocess_exec

    async def track_process(*args, **kwargs):
        process = await create_process(*args, **kwargs)
        spawned.append(process)
        # 先确认测试脚本就绪，再开始极短的查询超时；不把系统启动延迟当成清理失败。
        try:
            assert (await asyncio.wait_for(process.stdout.readline(), timeout=15)).strip() == b'ready'
            ready_times.append(time.monotonic())
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', track_process)
    root = build(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    monkeypatch.setattr(knowledge, 'QUERY_TIMEOUT', .5 if sys.platform == 'win32' else .2)
    pid_file = tmp_path / 'cli-pid'
    (root / 'query.py').write_text(
        'from pathlib import Path\nimport os, time\n'
        + 'Path(' + repr(str(pid_file)) + ").write_text(str(os.getpid()))\n"
        + "print('ready', flush=True)\ntime.sleep(10)\n")
    before = fingerprints(root)
    result = json.loads(asyncio.run(server.search_knowledge(make_ctx(game=None), 'writing')))
    assert result['status'] == 'error' and 'timed out' in result['error']
    assert time.monotonic() - ready_times[0] < 5, '超时清理仍在等待测试 CLI 的 10 秒睡眠结束'
    # 直接核对该次启动的进程已被回收，避免 Windows 的 os.kill(pid, 0) 差异。
    assert len(spawned) == 1
    assert spawned[0].returncode is not None
    pid = int(pid_file.read_text())
    if sys.platform == 'win32':
        # 虚拟环境启动器与实际解释器可能有不同 PID，额外检查实际 CLI。
        result = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
            capture_output=True, check=True,
        )
        # tasklist uses the OEM code page even when Python runs in UTF-8 mode.
        # The quoted numeric CSV field is ASCII; no localized text is needed.
        assert f'"{pid}"'.encode('ascii') not in result.stdout
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert fingerprints(root) == before


def test_default_root_is_source_relative_not_cwd_and_explicit_bad_root_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('CIV6_KNOWLEDGE_DIR', raising=False)
    # A cwd-shaped decoy must not influence source-relative lookup.
    decoy = tmp_path / 'civ6_knowledge'
    decoy.mkdir()
    marker = tmp_path / 'decoy-executed'
    (decoy / 'query.py').write_text('from pathlib import Path; Path(' + repr(str(marker)) + ").touch()\n")
    result = json.loads(asyncio.run(server.search_knowledge(make_ctx(game=None), 'TECH_WRITING')))
    assert result['status'] == 'found' and result['results'][0]['id'] == 'TECH_WRITING'
    assert not marker.exists()
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(tmp_path / 'absent'))
    result = json.loads(asyncio.run(server.search_knowledge(make_ctx(game=None), 'TECH_WRITING')))
    assert result['status'] == 'error' and 'CIV6_KNOWLEDGE_DIR' in result['error']
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', '')
    assert json.loads(asyncio.run(server.search_knowledge(make_ctx(game=None), 'writing')))['status'] == 'error'


def test_returned_profile_cannot_disagree_with_fingerprinted_source(monkeypatch, tmp_path):
    root = build(tmp_path)
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['profile']['game_version'] = 'different'
    path.write_text(json.dumps(manifest))
    result = json.loads(asyncio.run(server.search_knowledge(make_ctx(game=None), 'writing')))
    assert result['status'] == 'error' and 'profile' in result['error'].lower()


def test_mcp_civilization_topic_and_three_layers_keep_installed_scope_without_game(monkeypatch, tmp_path):
    from test_knowledge_civilizations import build as build_civilizations, installed_fixture

    root = build_civilizations(tmp_path, installed_fixture())
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    before = fingerprints(root)

    async def scenario(session):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert {"search_knowledge", "get_knowledge", "get_turn_status"} <= tools.keys()
        assert tools["get_turn_status"].annotations.readOnlyHint is True
        assert tools["get_turn_status"].inputSchema.get("properties", {}) == {}
        topic = tools['search_knowledge'].inputSchema['properties']['topic']['anyOf'][0]['enum']
        assert 'civilizations' in topic
        result = await session.call_tool('search_knowledge', {'query': '安装文明', 'topic': 'civilizations'})
        search = json.loads(result.content[0].text)
        assert search['results'][0]['id'] == 'CIVILIZATION_INSTALLED'
        for level in ('summary', 'rule', 'evidence'):
            result = await session.call_tool('get_knowledge', {'entry_id': 'CIVILIZATION_INSTALLED', 'level': level})
            assert not result.isError
            data = json.loads(result.content[0].text)
            scoped = data['summary'] if level == 'summary' else data['rule'] if level == 'rule' else data
            assert scoped['source_scope'] == 'installed_definition_catalog' and scoped['applied'] is False
            assert data['applicability']['current_session_verified'] is False

    session_run(monkeypatch, FakeGameState(), scenario)
    assert fingerprints(root) == before


def test_mcp_civilization_unique_unit_ability_keeps_inactive_grant_and_conditions(monkeypatch, tmp_path):
    from test_knowledge_civilizations import build as build_civilizations, installed_unit_ability_fixture

    root = build_civilizations(tmp_path, installed_unit_ability_fixture())
    monkeypatch.setenv('CIV6_KNOWLEDGE_DIR', str(root))
    before = fingerprints(root)

    async def scenario(session):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert {"search_knowledge", "get_knowledge", "get_turn_status"} <= tools.keys()
        assert tools["get_turn_status"].annotations.readOnlyHint is True
        assert tools["get_turn_status"].inputSchema.get("properties", {}) == {}
        response = await session.call_tool('get_knowledge', {'entry_id': 'CIVILIZATION_INSTALLED', 'level': 'rule'})
        assert not response.isError
        card = json.loads(response.content[0].text)
        unit = next(item for item in card['rule']['unique_content'] if item['id'] == 'UNIT_INSTALLED')
        ability = unit['abilities'][0]
        assert ability['id'] == 'ABILITY_INSTALLED' and ability['facts']['Inactive'] == '1'
        assert ability['activation'] == 'unverified_trait_grant_conditions'
        assert ability['grant_references'][0]['trait'] == 'TRAIT_INSTALLED'
        assert card['rule']['applied'] is False
        response = await session.call_tool('get_knowledge', {'entry_id': 'CIVILIZATION_INSTALLED', 'level': 'evidence'})
        assert not response.isError
        evidence = json.loads(response.content[0].text)
        facts = [row['facts'].get('facts', row['facts']) for row in evidence['evidence']]
        assert {'ModifierId': 'MOD_INSTALLED_ABILITY', 'Name': 'Amount', 'Value': '17'} in facts
        assert {'RequirementId': 'REQ_RIVER', 'RequirementType': 'REQUIREMENT_PLOT_ADJACENT_TO_RIVER'} in facts
        assert not any(row.get('UnitAbilityType') == 'ABILITY_OTHER_PACKAGE' or row.get('Type') == 'UNIT_OTHER_INSTALLED' for row in facts)

    session_run(monkeypatch, FakeGameState(), scenario)
    assert fingerprints(root) == before

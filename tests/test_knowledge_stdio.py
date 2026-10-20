"""使用真实 stdio 管道验证知识子进程，覆盖 Windows 标准输入继承。"""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from test_knowledge_queries import build


def test_knowledge_search_and_read_through_real_stdio_without_game(tmp_path):
    root = build(tmp_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=['-X', 'utf8', '-m', 'civ_mcp'],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={'PYTHONUTF8': '1', 'CIV6_KNOWLEDGE_DIR': str(root)},
    )

    async def scenario():
        async with stdio_client(params) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=30)
            ) as session:
                await session.initialize()
                result = await session.call_tool('search_knowledge', {
                    'query': '写作', 'topic': 'technologies', 'limit': 1,
                })
                data = json.loads(result.content[0].text)
                assert data['status'] == 'found', data
                assert data['results'][0]['id'] == 'TECH_WRITING'
                # 连续请求仍走同一条协议管道，子进程不能占用客户端输入。
                for level in ('summary', 'rule', 'evidence'):
                    result = await session.call_tool('get_knowledge', {
                        'entry_id': 'TECH_WRITING', 'level': level,
                    })
                    data = json.loads(result.content[0].text)
                    assert data['status'] == 'found', data
                    assert data['id'] == 'TECH_WRITING'
                assert 'search_knowledge' in {
                    tool.name for tool in (await session.list_tools()).tools
                }

    asyncio.run(scenario())

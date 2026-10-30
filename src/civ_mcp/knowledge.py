"""Read the standalone knowledge CLI without using game state or rebuilding data."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Literal

KnowledgeTopic = Literal[
    'civics', 'technologies', 'districts', 'congress', 'economy',
    'resources', 'combat', 'diplomacy', 'victory', 'civilizations',
]
KnowledgeLevel = Literal['summary', 'brief', 'rule', 'evidence']
TOPICS = KnowledgeTopic.__args__
LEVELS = KnowledgeLevel.__args__
QUERY_TIMEOUT = 10.0
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / 'civ6_knowledge'
APPLICABILITY = {
    'status': 'static_snapshot', 'current_session_verified': False,
    'action_applicability': 'unverified', 'active_mods_verified': False,
    'active_modes_verified': False,
}


def _error(message: str) -> str:
    return json.dumps({'status': 'error', 'error': message, 'applicability': APPLICABILITY}, ensure_ascii=False)


async def _query(arguments: list[str], *, entry_id: str | None = None,
                 level: KnowledgeLevel = 'summary', requested_limit: int = 5) -> str:
    try:
        configured = os.environ.get('CIV6_KNOWLEDGE_DIR')
        if configured is not None and not configured.strip():
            raise ValueError('CIV6_KNOWLEDGE_DIR must name a knowledge directory')
        root = Path(configured).expanduser() if configured is not None else DEFAULT_ROOT
        if not root.is_absolute():
            raise ValueError('CIV6_KNOWLEDGE_DIR must be an absolute directory')
        root = root.resolve()
        script = root / 'query.py'
        if not script.is_file():
            raise ValueError('Knowledge query.py is unavailable; configure CIV6_KNOWLEDGE_DIR with a standalone knowledge directory')
        raw_manifest = (root / 'manifest.json').read_bytes()
        manifest = json.loads(raw_manifest)
        if (not isinstance(manifest, dict) or not isinstance(manifest.get('profile'), dict)
                or manifest.get('snapshot') != 'sources/snapshot.json'
                or not isinstance(manifest.get('snapshot_sha256'), str)
                or len(manifest['snapshot_sha256']) != 64):
            raise ValueError('Invalid knowledge manifest metadata')
        # The CLI verifies data fingerprints; the envelope must also describe
        # that source's profile, rather than an inconsistent manifest label.
        snapshot = json.loads((root / manifest['snapshot']).read_bytes())
        if not isinstance(snapshot, dict) or snapshot.get('profile') != manifest['profile']:
            raise ValueError('Knowledge profile differs from source snapshot')
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-X', 'utf8', '-B', str(script), '--root', str(root), *arguments,
            # 知识 CLI 不读取输入，避免继承 MCP 管道并在 Windows 下阻塞。
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=QUERY_TIMEOUT)
        except TimeoutError:
            raise ValueError('Knowledge query timed out') from None
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.communicate()
        try:
            result = json.loads(stdout.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise ValueError('Knowledge query returned invalid UTF-8 JSON') from None
        if not isinstance(result, dict):
            raise ValueError('Knowledge query did not return a JSON object')
        if process.returncode != 0 or result.get('status') == 'error':
            raise ValueError(result.get('error') or f'Knowledge query failed (exit {process.returncode})')
        if entry_id is None:
            total, limit, rows = result.get('total'), result.get('limit'), result.get('results')
            if (type(total) is not int or total < 0 or type(limit) is not int or limit != requested_limit
                    or not isinstance(rows, list) or len(rows) != min(total, limit)
                    or type(result.get('truncated')) is not bool
                    or result['truncated'] != (total > limit)
                    or result.get('status') != ('found' if total else 'not_found')
                    or any(not isinstance(row, dict) or not {'id', 'topic', 'names', 'summary'} <= row.keys() for row in rows)):
                raise ValueError('Invalid knowledge search response')
        else:
            fields = {'summary': ('topic', 'names', 'summary'),
                      'brief': ('topic', 'names', 'summary', 'rule', 'source_refs', 'relations', 'encoding'),
                      'rule': ('topic', 'names', 'summary', 'rule', 'source_refs', 'relations'),
                      'evidence': ('source_refs', 'evidence', 'profile', 'sources')}[level]
            if result.get('id') != entry_id or any(field not in result for field in fields):
                raise ValueError('Invalid knowledge entry response')
        if (root / 'manifest.json').read_bytes() != raw_manifest:
            raise ValueError('Knowledge manifest changed during query; query again')
        profile = manifest['profile']
        if level == 'brief':
            expected = hashlib.sha256(json.dumps(profile, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
            if result.get('profile', {}).get('sha256') != expected or result['encoding'].get('truncated') is not False:
                raise ValueError('Invalid brief profile or incomplete rule view')
            profile = result['profile']
        return json.dumps(result | {
            'status': result.get('status', 'found'), 'profile': profile,
            'snapshot': {'path': manifest['snapshot'], 'sha256': manifest['snapshot_sha256']},
            'applicability': APPLICABILITY,
        }, ensure_ascii=False)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return _error(str(exc))


async def search(query: str, topic: KnowledgeTopic | None = None, limit: int = 5) -> str:
    if not isinstance(query, str) or not query.strip():
        return _error('query must be a nonempty name or ID fragment')
    if topic is not None and topic not in TOPICS:
        return _error('unknown knowledge topic')
    if type(limit) is not int or not 1 <= limit <= 20:
        return _error('limit must be an integer from 1 to 20')
    arguments = ['search', '--limit', str(limit)]
    if topic is not None:
        arguments += ['--topic', topic]
    return await _query([*arguments, '--', query], requested_limit=limit)


async def get(entry_id: str, level: KnowledgeLevel = 'summary') -> str:
    if not isinstance(entry_id, str) or not entry_id.strip():
        return _error('entry_id must be nonempty')
    if level not in LEVELS:
        return _error('unknown knowledge level')
    return await _query(['show', '--level', level, '--', entry_id], entry_id=entry_id, level=level)


async def research(kind: str, query: str = "", filters: list[str] | None = None,
                   match: str = "any", limit: int = 20, offset: int = 0) -> str:
    from civ_mcp.research_catalog import query as catalog_query
    try:
        configured = os.environ.get('CIV6_KNOWLEDGE_DIR')
        root = Path(configured).expanduser() if configured is not None else DEFAULT_ROOT
        if not root.is_absolute() or (configured is not None and not configured.strip()):
            raise ValueError('CIV6_KNOWLEDGE_DIR must be an absolute knowledge directory')
        result = await asyncio.to_thread(catalog_query, root, kind, query=query,
                                         filters=filters, match=match, limit=limit, offset=offset)
        return json.dumps(result | {'applicability': APPLICABILITY}, ensure_ascii=False)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return _error(str(error))

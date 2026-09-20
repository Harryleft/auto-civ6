"""Memory Search：对过去的 Game Memory 做简单全文检索（v7 §M08）。

MVP 不引入向量库、reranker 或 GraphRAG。检索单位是 Game Memory 文件里的
**小节**（``## Turn N`` 之间的块，或文件头的元信息块），返回原文摘录与来源，
好让模型能回查原文件。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from civ_agent.memory.writer import memory_dir

_META_HEADING = "## 游戏基本信息"
_TURN_HEADING = re.compile(r"^## Turn (\d+)\s*$")
_HEADING = re.compile(r"^##\s+(.*)$")


@dataclass(frozen=True, slots=True)
class MemorySearchHit:
    """一条检索结果：来源可回查，摘录不是结论。"""

    game_id: str
    turn: int | None
    section: str
    excerpt: str
    source_file: str
    score: int


@dataclass(frozen=True, slots=True)
class _Section:
    game_id: str
    turn: int | None
    title: str
    body: str
    source_file: str


def _sections(path: Path) -> Iterator[_Section]:
    """把一个 Game Memory 文件切成小节；正文不含 ``##`` 标题行。"""

    text = path.read_text(encoding="utf-8")
    game_id = path.stem
    title = _META_HEADING
    turn: int | None = None
    body: list[str] = []
    started = False
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            if started:
                yield _Section(game_id, turn, title, "\n".join(body).strip(), path.name)
            title = heading.group(1).strip()
            turn_match = _TURN_HEADING.match(line)
            turn = int(turn_match.group(1)) if turn_match else None
            body = []
            started = True
            continue
        if started:
            body.append(line)
    if started:
        yield _Section(game_id, turn, title, "\n".join(body).strip(), path.name)


def _terms(query: str) -> list[str]:
    """按空白切词并小写；中文查询不切字，整串参与匹配。"""

    return [term for term in query.lower().split() if term]


def _excerpt(body: str, terms: list[str], *, width: int = 300) -> str:
    """给出包含命中词的片段，而不是整节正文。"""

    lowered = body.lower()
    position = -1
    for term in terms:
        position = lowered.find(term)
        if position != -1:
            break
    if position == -1:
        return body[:width]
    start = max(0, position - width // 3)
    end = min(len(body), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end]}{suffix}"


def search_memory(
    query: str,
    *,
    limit: int = 5,
    directory: Path | None = None,
) -> tuple[MemorySearchHit, ...]:
    """在全部历史 Game Memory 中做全文检索，按命中次数排序。

    ``limit`` 必须为正；``query`` 为空时返回空结果而不是抛出，以便调用方
    在模型没给出检索词时安全降级。
    """

    if limit < 1:
        raise ValueError("limit 必须是正整数。")
    terms = _terms(query)
    if not terms:
        return ()

    base = memory_dir(directory)
    if not base.is_dir():
        return ()

    hits: list[MemorySearchHit] = []
    for path in sorted(base.glob("*.md")):
        for section in _sections(path):
            haystack = f"{section.title}\n{section.body}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score == 0:
                continue
            hits.append(
                MemorySearchHit(
                    game_id=section.game_id,
                    turn=section.turn,
                    section=section.title,
                    excerpt=_excerpt(section.body, terms),
                    source_file=section.source_file,
                    score=score,
                )
            )

    hits.sort(key=lambda hit: (-hit.score, hit.game_id, hit.turn or 0))
    return tuple(hits[:limit])

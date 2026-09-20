"""Rule Search：给 DeepSeek 一个查规则的入口（v7 §11）。

只返回**规则事实**，不做战略推荐、不做 RAG、不用向量库或 reranker。
数据源是 ``docs/wiki/`` 下的人工蒸馏知识库，按 L0 决策速查 / L1 机制 /
L2 数据附录 分层；检索结果保留层级，好让调用方先看 L0。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

WIKI_DIR_ENV = "CIV6_AGENT_WIKI_DIR"

#: 方案 §11 建议的检索方向 → (wiki 文档, 中文/英文别名)
#: 别名用于把 "城市"、"经济学" 这类查询映射到对应文档。
_TOPICS: dict[str, tuple[str, tuple[str, ...]]] = {
    "cities": ("cities.md", ("城市", "城建", "区域", "忠诚", "总督")),
    "economy": ("economy.md", ("经济", "金币", "商路", "资源", "改良", "贸易")),
    "military": ("military.md", ("军事", "战斗", "单位", "升级", "攻城", "战争")),
    "science": ("science.md", ("科技", "研究", "尤里卡", "科技树")),
    "culture": ("culture.md", ("文化", "旅游", "伟作", "文化胜利")),
    "civics": ("civics.md", ("市政", "政策卡", "政府", "政体")),
    "diplomacy": ("diplomacy.md", ("外交", "同盟", "议会", "紧急事件", "城邦")),
    "religion": ("religion.md", ("宗教", "信仰", "万神殿", "教条", "传教士")),
    "victory": ("victory.md", ("胜利", "胜利条件", "胜利路线")),
    "wonders": ("wonders.md", ("奇观",)),
    "great_people": ("great-people.md", ("伟人", "伟人点数")),
    "barbarians": ("barbarians.md", ("蛮族", "营地", "清剿")),
}

_HEADING = re.compile(r"^(#{2,3})\s+(.*)$")
_LEVEL = re.compile(r"^L(\d)")
#: 无 L 层的行内小节（如文档引言）排在所有带层级的小节之后。
_NO_LEVEL = 9
_FILENAME_TOPIC = {document: topic for topic, (document, _) in _TOPICS.items()}


@dataclass(frozen=True, slots=True)
class RuleHit:
    """一条规则事实；``level`` 为 0/1/2，None 表示非 L 层小节。

    ``doc`` 是 wiki 文档名，调用方可据此读取全文（渐进式披露）。
    """

    topic: str
    doc: str
    section: str
    level: int | None
    excerpt: str
    score: int


@dataclass(frozen=True, slots=True)
class _Section:
    topic: str
    doc: str
    section: str
    level: int | None
    body: str


def wiki_dir(base: Path | None = None) -> Path:
    """解析知识库目录；环境变量优先，其次仓库内 ``docs/wiki``。"""

    if base is not None:
        return Path(base)
    configured = os.environ.get(WIKI_DIR_ENV, "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "docs" / "wiki"


def known_topics() -> tuple[str, ...]:
    """可供模型选择的检索方向（方案 §11 的清单）。"""

    return tuple(sorted(_TOPICS))


def _topic_of(doc: str) -> str:
    return _FILENAME_TOPIC.get(doc, Path(doc).stem.replace("-", "_"))


def _level_of(heading: str) -> int | None:
    match = _LEVEL.match(heading.strip())
    return int(match.group(1)) if match else None


def _sections(path: Path) -> Iterator[_Section]:
    """把一篇 wiki 文档切成 ``##`` / ``###`` 小节。

    ``###`` 子小节继承其所属 ``##`` 的 L 层级——文档结构是 L0/L1/L2 在
    ``##`` 上、具体机制在 ``###`` 上，所以子小节必须带上父级层级，否则
    "忠诚度机制" 会被当成无层级的散段。

    文档标题（``#``）与它到第一个 ``##`` 之间的引言作为一个无名小节保留，
    避免丢失定位说明。
    """

    topic = _topic_of(path.name)
    section = ""
    level: int | None = None
    parent_level: int | None = None
    body: list[str] = []
    started = False
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = _HEADING.match(line)
        if heading:
            if started:
                yield _Section(topic, path.name, section, level, "\n".join(body).strip())
            section = heading.group(2).strip()
            own_level = _level_of(section)
            if len(heading.group(1)) == 2:
                # ``##`` 定义本段的 L 层级，子小节继承它。
                parent_level = own_level
                level = own_level
            else:
                # ``###`` 自身通常不带 L 前缀，沿用父级；带则显式覆盖。
                level = own_level if own_level is not None else parent_level
            body = []
            started = True
            continue
        if started:
            body.append(line)
        elif line.startswith("# "):
            # 一级标题是文档标题，不作为小节正文。
            continue
        else:
            body.append(line)
    if started:
        yield _Section(topic, path.name, section, level, "\n".join(body).strip())


def _terms(query: str) -> list[str]:
    return [term for term in query.lower().split() if term]


def _matched_topics(terms: list[str]) -> set[str]:
    """查询词命中别名时，把该文档全部小节纳入候选。"""

    matched: set[str] = set()
    for topic, (_, aliases) in _TOPICS.items():
        haystack = (topic, *aliases)
        if any(term in alias.lower() for alias in haystack for term in terms):
            matched.add(topic)
    return matched


def _excerpt(body: str, terms: list[str], *, width: int = 400) -> str:
    lowered = body.lower()
    position = -1
    for term in terms:
        position = lowered.find(term)
        if position != -1:
            break
    if position == -1:
        return body[:width]
    start = max(0, position - width // 4)
    end = min(len(body), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end]}{suffix}"


def search_rules(
    query: str,
    *,
    limit: int = 5,
    directory: Path | None = None,
) -> tuple[RuleHit, ...]:
    """检索规则事实，按命中次数排序；同分时 L0 先于 L1/L2。

    排序以相关度为主：一个小节命中查询词越多越靠前，即使它是 L1/L2。层级只在
    分数相同时作为次级依据——因为 L1/L2 常常比 L0 的概览更精确地回答具体数值
    问题（例如"区域成本公式"）。每条结果的 ``level`` 让调用方自行决定是否先读
    L0 概览。

    空查询或目录不存在时返回空结果而不是抛错，方便调用方在模型没给出检索词时
    安全降级。
    """

    if limit < 1:
        raise ValueError("limit 必须是正整数。")
    terms = _terms(query)
    if not terms:
        return ()

    base = wiki_dir(directory)
    if not base.is_dir():
        return ()

    preferred = _matched_topics(terms)
    hits: list[RuleHit] = []
    for path in sorted(base.glob("*.md")):
        if path.name == "README.md":
            continue
        for section in _sections(path):
            haystack = f"{section.section}\n{section.body}".lower()
            score = sum(haystack.count(term) for term in terms)
            if section.topic in preferred:
                # 文档级命中说明整篇都相关，给一个低于逐字命中、高于无关的底分。
                score += 1
            if score == 0:
                continue
            hits.append(
                RuleHit(
                    topic=section.topic,
                    doc=section.doc,
                    section=section.section,
                    level=section.level,
                    excerpt=_excerpt(section.body, terms),
                    score=score,
                )
            )

    hits.sort(
        key=lambda hit: (
            -hit.score,
            hit.level if hit.level is not None else _NO_LEVEL,
            hit.doc,
        )
    )
    return tuple(hits[:limit])

"""Reuse observed research definitions while always reading live game state.

This cache belongs to one GameState. Callers supply a connection/reload
generation and clear it on lifecycle changes. It is deliberately neither a
global rules database nor a cache of research progress or legal choices.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from civ_mcp.lua.models import TechCivicStatus
from civ_mcp.lua.tech import build_tech_civics_query, parse_tech_civics_response


class _ReadConnection(Protocol):
    async def execute_read(self, lua_code: str) -> list[str]: ...


@dataclass(frozen=True)
class _ResearchRule:
    """Only invariant fields; availability and missing prerequisites stay live."""

    kind: str
    type_id: str
    name: str
    era: str
    boost_desc: str
    prereqs: str = ""
    unlocks: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.type_id}"

    @classmethod
    def from_line(cls, line: str) -> _ResearchRule | None:
        p = line.split("|")
        if p[0] == "TECH" and len(p) == 11:
            return cls(p[0], p[2], p[1], p[10], p[7], p[9], p[8])
        if p[0] == "CIVIC" and len(p) == 11:
            return cls(p[0], p[2], p[1], p[9], p[7], p[8], p[10])
        if p[0] in {"LOCKED_TECH", "LOCKED_CIVIC"} and len(p) == 7:
            return cls(p[0], p[2], p[1], p[4], p[6])
        # Older DTO records remain readable but do not prove complete rules.
        return None

    def merge(self, state: list[str]) -> str:
        if self.kind in {"TECH", "CIVIC"} and len(state) == 6:
            # Cost, progress, ETA and boost are all supplied by this response.
            parts = [self.kind, self.name, self.type_id, *state[2:], self.boost_desc]
            parts += (
                [self.unlocks, self.prereqs, self.era]
                if self.kind == "TECH"
                else [self.prereqs, self.era, self.unlocks]
            )
        elif self.kind in {"LOCKED_TECH", "LOCKED_CIVIC"} and len(state) == 4:
            parts = [
                self.kind,
                self.name,
                self.type_id,
                state[2],
                self.era,
                state[3],
                self.boost_desc,
            ]
        else:
            raise ValueError("科研缓存的动态记录格式不完整")
        return "|".join(parts)


class ResearchCache:
    """Incremental, per-session research-rule cache with a compatible DTO API.

    ``generation`` can be a tuple, e.g. (connection.generation, reload_epoch).
    None means lifecycle identity is unavailable and disables rule reuse.
    Missing/unknown/shuffled Lua rule identity also disables reuse. Every read
    still queries current research, costs, progress, boosts, completed sets,
    missing prerequisites and availability directly from the game.
    """

    def __init__(self) -> None:
        self._identity: str | None = None
        self._rules: dict[str, _ResearchRule] = {}
        self._scope: tuple[int, object] | None = None
        self._revision = 0
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._identity = None
        self._rules.clear()
        self._scope = None
        self._revision += 1

    async def read(
        self,
        conn: _ReadConnection,
        *,
        generation: object | None = None,
    ) -> TechCivicStatus:
        async with self._lock:
            scope = (id(conn), generation)
            if generation is None or scope != self._scope:
                self.clear()
                self._scope = scope
            revision = self._revision
            connection_generation = getattr(conn, "generation", None)
            try:
                lines = await conn.execute_read(
                    build_tech_civics_query(
                        cache_identity=self._identity,
                        known_rules=self._rules if generation is not None else None,
                    )
                )
                if revision != self._revision:
                    raise RuntimeError("科研查询期间对局已切换，需要重新读取")
                try:
                    if connection_generation != getattr(conn, "generation", None):
                        raise ValueError("科研查询期间连接已重建，需要重新读取规则")
                    return self._merge(lines, allow_cache=generation is not None)
                except ValueError:
                    # A missing rule or mismatched response must never turn a
                    # legal-choice query into a partially reconstructed DTO.
                    self.clear()
                    revision = self._revision
                    lines = await conn.execute_read(build_tech_civics_query())
                    if revision != self._revision:
                        raise RuntimeError("科研查询期间对局已切换，需要重新读取")
                    return self._merge(lines, allow_cache=False)
            except BaseException:
                # Read retries may reconnect internally. On a failed read no
                # cached identity is trusted by the next request.
                self.clear()
                raise

    def _merge(self, lines: list[str], *, allow_cache: bool) -> TechCivicStatus:
        identities = [
            line.split("|", 1)[1]
            for line in lines
            if line.startswith("RESEARCH_RULES|")
        ]
        identity = (
            identities[0]
            if allow_cache and len(identities) == 1 and identities[0]
            else None
        )
        rules = (
            dict(self._rules)
            if identity is not None and identity == self._identity
            else {}
        )
        expanded = []
        for line in lines:
            parts = line.split("|")
            kind = parts[0].removesuffix("_STATE")
            if parts[0] != kind and kind in {
                "TECH",
                "CIVIC",
                "LOCKED_TECH",
                "LOCKED_CIVIC",
            }:
                rule = rules.get(f"{kind}:{parts[1]}") if len(parts) > 1 else None
                if rule is None:
                    raise ValueError("科研缓存规则缺失或对局身份不一致")
                expanded.append(rule.merge(parts))
            else:
                expanded.append(line)
                rule = _ResearchRule.from_line(line)
                if rule is not None and identity is not None:
                    rules[rule.key] = rule
        status = parse_tech_civics_response(expanded)
        self._identity = identity
        self._rules = rules
        return status

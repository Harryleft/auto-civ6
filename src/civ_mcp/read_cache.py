"""Reuse typed reads within one collection, never across verification requests.

The socket cannot observe every human/AI state change, so elapsed turns are
not a safe lease. A short task-local collection is the deliberate boundary;
mutation submission, reconnect, reload and a different turn invalidate it.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Awaitable, Callable


@dataclass
class _Collection:
    owner: object
    stamp: object
    turn: int | None = None
    values: dict[object, Any] = field(default_factory=dict)


class ReadCache:
    def __init__(self, stamp: Callable[[], object | None]):
        self._stamp = stamp
        self._collection: ContextVar[_Collection | None] = ContextVar(
            "civ6_read_collection", default=None
        )
        self.hits = 0
        self.misses = 0

    @contextmanager
    def collection(self):
        current = self._current()
        if current is not None:
            yield
            return
        token = self._collection.set(_Collection(asyncio.current_task(), self._stamp()))
        try:
            yield
        finally:
            self._collection.reset(token)

    def _current(self) -> _Collection | None:
        current = self._collection.get()
        try:
            owner = asyncio.current_task()
        except RuntimeError:
            return None
        # Context variables are inherited by child tasks; a parallel request
        # must not inherit its parent's partly collected snapshot.
        if current is None or current.owner is not owner:
            return None
        return current

    def invalidate(self) -> None:
        current = self._current()
        if current is not None:
            current.values.clear()
            current.stamp = self._stamp()

    def set_turn(self, turn: int) -> None:
        current = self._current()
        if current is not None and current.turn != turn:
            current.turn = turn
            self.invalidate()

    async def read(self, key: object, load: Callable[[], Awaitable[Any]]) -> Any:
        current = self._current()
        stamp = self._stamp()
        if current is None or stamp is None:
            self.misses += 1
            return await load()
        if current.stamp != stamp:
            self.invalidate()
        if key in current.values:
            self.hits += 1
            return deepcopy(current.values[key])
        self.misses += 1
        value = await load()
        # Exceptions are never cached, and a reconnect/mutation during this
        # read cannot turn a partly collected value into reusable evidence.
        if stamp == self._stamp():
            current.values[key] = deepcopy(value)
        return value


def scoped_read(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        key = (method.__name__, args, tuple(sorted(kwargs.items())))
        return await self._read_cache().read(key, lambda: method(self, *args, **kwargs))

    return wrapped


def scoped_collection(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        with self._read_cache().collection():
            return await method(self, *args, **kwargs)

    return wrapped

"""Observation 组合：把 Runtime 的事实整理成一次决策循环所需的输入。

组合原则（v7 §9 / §10）：

- 只写读到的数据，不推断；拿不到的字段写 ``UNKNOWN``，不伪造成事实；
- 没遇到的文明不写进 ``opponents``；
- 每个字段都保留 ``coverage``，让"我不知道"与"游戏里就是空"可区分。

事实来源是 ``get_runtime_context`` 的 JSON（经 MCP），因此本模块不依赖任何
具体读模型版本，也不访问 MCP 或发起 mutation。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import Any, Protocol

UNKNOWN = "unknown"


class _OverviewLike(Protocol):
    """只声明组合所需的字段，避免绑定具体 Lua 模型版本。"""

    turn: int
    player_id: int
    civ_name: str
    leader_name: str
    difficulty: str
    num_cities: int
    total_population: int
    gold: float
    gold_per_turn: float
    science_yield: float
    culture_yield: float
    faith: float
    score: int


def _text(value: Any) -> str:
    """把可缺省的游戏字段转成可读文本；空值与缺失值一律记 unknown。"""

    if value is None:
        return UNKNOWN
    if isinstance(value, str):
        cleaned = value.strip()
        # Civ6 用 "NONE" / "" 表示"当前没有选择"，两者都不是事实值。
        if not cleaned or cleaned.upper() in {"NONE", "NULL"}:
            return UNKNOWN
        return cleaned
    return str(value)


def _number(value: Any) -> float | int | str:
    """保留数值原样；缺失即 unknown，不用 0 冒充。"""

    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return value
    return _text(value)


def _get(obj: Any, name: str) -> Any:
    """从对象或映射中取值，两者都支持才算读得到。"""

    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _find_fact(facts: dict[str, object], name: str) -> Any:
    """取出一个域事实的载荷，兼容两种来源。

    - **MCP 路径**：``get_runtime_context`` 返回的 JSON，形状为
      ``{"facts": {name: {"value": ..., "coverage": ...}}}``；
    - **内调路径**：``RuntimeContext.facts[name]`` 是 ``CivReadResult``，
      载荷在 ``.value``，来源标记在 ``.coverage``。

    读失败的项由 ``ContextBuilder`` 记进 ``unknown`` 而不写入 ``facts``，所以
    ``None`` 同时覆盖"该项读失败"与"上下文里没有这一项"两种情况。
    """

    holder = facts.get(name)
    if holder is None:
        return None
    if isinstance(holder, Mapping) and "value" in holder:
        return holder["value"]
    value = getattr(holder, "value", None)
    if value is not None:
        return value
    # 已经是裸载荷（例如直接喂给 build_observation 的测试数据）。
    return None if _has_coverage(holder) else holder


def _has_coverage(holder: Any) -> bool:
    """裸载荷与 ``CivReadResult`` 的区分依据：是否带来源标记。"""

    return getattr(holder, "coverage", None) is not None or (
        isinstance(holder, Mapping) and "coverage" in holder
    )


@dataclass(frozen=True, slots=True)
class OurState:
    """我方本回合基本信息（v7 §9 我方基本信息）。"""

    civilization: str = UNKNOWN
    leader: str = UNKNOWN
    cities: int | str = UNKNOWN
    total_population: int | str = UNKNOWN
    gold: float | str = UNKNOWN
    gold_per_turn: float | str = UNKNOWN
    science: float | str = UNKNOWN
    culture: float | str = UNKNOWN
    faith: float | str = UNKNOWN
    military_strength: int | str = UNKNOWN
    current_research: str = UNKNOWN
    current_civic: str = UNKNOWN
    government: str = UNKNOWN
    at_war_with: tuple[str, ...] = ()
    score: int | str = UNKNOWN

    def as_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class OpponentState:
    """一个**已遇到**对手的本回合信息（v7 §10）。

    未遇到的文明不会出现在 ``Observation.opponents`` 里。已遇到但拿不到的
    指标写 ``unknown``，下一回合重新读取后更新。
    """

    player_id: int
    civilization: str = UNKNOWN
    leader: str = UNKNOWN
    known_cities: int | str = UNKNOWN
    science: float | str = UNKNOWN
    culture: float | str = UNKNOWN
    military_strength: int | str = UNKNOWN
    diplomatic_state: str = UNKNOWN
    is_at_war: bool | str = UNKNOWN
    victory_progress: str = UNKNOWN

    def as_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def coverage_of(holder: Any) -> str | None:
    """读取一个事实持有者的来源覆盖度标记，兼容 dict 与 ``CivReadResult``。"""

    if isinstance(holder, Mapping):
        value = holder.get("coverage")
    else:
        value = getattr(holder, "coverage", None)
    return None if value is None else _text(value)


def coverage_map(facts: dict[str, object]) -> dict[str, str]:
    """把 ``facts`` 的来源覆盖度整理成可序列化映射。"""

    collected: dict[str, str] = {}
    for name, holder in facts.items():
        marker = coverage_of(holder)
        if marker is not None:
            collected[name] = marker
    return collected


def context_snapshot(context: Any) -> dict[str, Any]:
    """把一次 Runtime 上下文归一化成 ``build_observation`` 能吃的形状。

    接受两种来源：

    - MCP 路径：``get_runtime_context`` 的 JSON dict；
    - 内调路径：``RuntimeContext``（``facts`` 里是 ``CivReadResult``）。

    不猜测缺失字段：``unknown`` / ``coverage`` 缺失即视为空，但 ``facts`` 缺失
    会直接报错，因为那说明拿到的不是 Runtime 上下文。
    """

    if isinstance(context, Mapping):
        facts = context.get("facts")
        unknown = context.get("unknown") or ()
    else:
        facts = getattr(context, "facts", None)
        unknown = getattr(context, "unknown", ()) or ()

    if not isinstance(facts, Mapping):
        raise ValueError(
            "Runtime 上下文缺少 facts；期望 get_runtime_context 的 JSON 或 RuntimeContext。"
        )
    if not isinstance(unknown, Sequence) or isinstance(unknown, str | bytes):
        raise ValueError("Runtime 上下文的 unknown 必须是字符串序列。")

    return {
        "facts": dict(facts),
        "unknown": tuple(str(item) for item in unknown),
    }


@dataclass(frozen=True, slots=True)
class Observation:
    """一次决策循环的完整事实快照。"""

    turn: int
    our_state: OurState
    opponents: tuple[OpponentState, ...] = ()
    important_changes: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    coverage: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "our_state": self.our_state.as_dict(),
            "opponents": [opponent.as_dict() for opponent in self.opponents],
            "important_changes": list(self.important_changes),
            "unknown": list(self.unknown),
            "coverage": dict(self.coverage),
        }


def _government_of(facts: dict[str, object]) -> str:
    """当前政府名称；只有读到激活政府时才算已知。"""

    governments = _find_fact(facts, "governments")
    for government in _as_sequence(governments):
        if _get(government, "is_current") or _get(government, "active"):
            return _text(_get(government, "name"))
    return UNKNOWN


def _as_sequence(value: Any) -> Sequence[Any]:
    if value is None or isinstance(value, str | bytes | dict):
        return ()
    if isinstance(value, Iterable):
        return tuple(value)
    return ()


def _our_state(
    overview: Any, facts: dict[str, object], our_player_id: int
) -> OurState:
    cities = _as_sequence(_find_fact(facts, "cities"))
    population: int | str = UNKNOWN
    if cities:
        population = sum(int(_get(city, "population") or 0) for city in cities)
    elif _get(overview, "total_population") is not None:
        # 城市明细读失败时退回概览口径，并保留其为概览事实。
        population = _number(_get(overview, "total_population"))

    at_war = tuple(
        _text(_get(civ, "civ_name"))
        for civ in _as_sequence(_find_fact(facts, "diplomacy"))
        if _get(civ, "is_at_war") and _get(civ, "player_id") != our_player_id
    )
    # 概览不提供我方军事力量；胜利进度里有，取不到就记 unknown。
    ours_progress = _progress_of(facts, our_player_id)
    return OurState(
        civilization=_text(_get(overview, "civ_name")),
        leader=_text(_get(overview, "leader_name")),
        cities=_number(_get(overview, "num_cities")),
        total_population=population,
        gold=_number(_get(overview, "gold")),
        gold_per_turn=_number(_get(overview, "gold_per_turn")),
        science=_number(_get(overview, "science_yield")),
        culture=_number(_get(overview, "culture_yield")),
        faith=_number(_get(overview, "faith")),
        military_strength=_number(_get(ours_progress, "military_strength")),
        current_research=_tech_civics_text(facts, "current_research"),
        current_civic=_tech_civics_text(facts, "current_civic"),
        government=_government_of(facts),
        at_war_with=at_war,
        score=_number(_get(overview, "score")),
    )


def _tech_civics_text(facts: dict[str, object], kind: str) -> str:
    """当前科技/市政：优先稳定 type ID，其次本地化名称。

    ``TechCivicStatus.current_research`` / ``current_civic`` 是字符串字段，
    ``current_research_type`` / ``current_civic_type`` 才是稳定 ID。
    两者都可能为空，空即 unknown，不猜。
    """

    status = _find_fact(facts, "tech_civics")
    if status is None:
        return UNKNOWN
    identifier = _text(_get(status, f"{kind}_type"))
    if identifier != UNKNOWN:
        return identifier
    return _text(_get(status, kind))


def _progress_of(facts: dict[str, object], player_id: int) -> Any:
    """从胜利进度里取出某个玩家的条目；没有则 ``None``。"""

    for progress in _as_sequence(_find_fact(facts, "victory_progress")):
        if _get(progress, "player_id") == player_id:
            return progress
    return None


def _opponents(facts: dict[str, object], our_player_id: int) -> tuple[OpponentState, ...]:
    """只输出已遇到的文明；未遇到的不写。"""

    progress_by_player: dict[int, Any] = {}
    for progress in _as_sequence(_find_fact(facts, "victory_progress")):
        player_id = _get(progress, "player_id")
        if isinstance(player_id, int):
            progress_by_player[player_id] = progress

    opponents: list[OpponentState] = []
    for civ in _as_sequence(_find_fact(facts, "diplomacy")):
        player_id = _get(civ, "player_id")
        if player_id == our_player_id or not isinstance(player_id, int):
            continue
        if not _get(civ, "has_met"):
            continue
        progress = progress_by_player.get(player_id)
        opponents.append(
            OpponentState(
                player_id=player_id,
                civilization=_text(_get(civ, "civ_name")),
                leader=_text(_get(civ, "leader_name")),
                known_cities=_number(
                    _get(progress, "num_cities") or _get(civ, "num_cities")
                ),
                science=_number(_get(progress, "science_yield")),
                culture=_number(_get(progress, "culture_yield")),
                military_strength=_number(
                    _get(progress, "military_strength")
                    or _get(civ, "military_strength")
                ),
                diplomatic_state=_text(_get(civ, "diplomatic_state")),
                is_at_war=bool(_get(civ, "is_at_war")),
                victory_progress=_victory_progress_text(progress),
            )
        )
    return tuple(opponents)


def _victory_progress_text(progress: Any) -> str:
    """把对手胜利进度压成一行可比较文本；读不到即 unknown。"""

    if progress is None:
        return UNKNOWN
    score = _get(progress, "score")
    if score is None:
        return UNKNOWN
    parts = [f"score={score}"]
    for label, key in (("space", "science_vp"), ("diplomatic", "diplomatic_vp")):
        value = _get(progress, key)
        if value:
            parts.append(f"{label}={value}")
    return " ".join(parts)


def build_observation(context: Any) -> Observation:
    """把一次 Runtime 上下文组合成 Observation。

    接受 ``get_runtime_context`` 的 JSON 或内调 ``RuntimeContext``；两者都经
    ``context_snapshot`` 归一化，因此不依赖任何具体读模型版本。
    """

    snapshot = context_snapshot(context)
    facts = snapshot["facts"]
    overview = _find_fact(facts, "overview")
    if overview is None:
        raise ValueError("缺少 overview 事实，无法建立 Observation。")

    turn = _get(overview, "turn")
    if not isinstance(turn, int):
        raise ValueError("overview.turn 必须是整数。")

    our_player_id = _get(overview, "player_id")
    if not isinstance(our_player_id, int):
        raise ValueError("overview.player_id 必须是整数。")

    our_state = _our_state(overview, facts, our_player_id)
    return Observation(
        turn=turn,
        our_state=our_state,
        opponents=_opponents(facts, our_player_id),
        important_changes=(),
        unknown=snapshot["unknown"],
        coverage=coverage_map(facts),
    )


def diff(previous: Observation, current: Observation) -> Observation:
    """用两个快照算出"本回合重要变化"，只对可比字段做差。"""

    changes: list[str] = []
    for name, before in previous.our_state.as_dict().items():
        after = current.our_state.as_dict()[name]
        if _comparable(before) and _comparable(after) and before != after:
            changes.append(f"{name}: {before} → {after}")

    before_opponents = {item.player_id: item for item in previous.opponents}
    for opponent in current.opponents:
        before = before_opponents.get(opponent.player_id)
        label = opponent.civilization
        if before is None:
            changes.append(f"新遇到文明 {label} (player {opponent.player_id})")
            continue
        for name, value in opponent.as_dict().items():
            old = before.as_dict()[name]
            if name == "player_id" or not (_comparable(old) and _comparable(value)):
                continue
            if old != value:
                changes.append(f"{label}.{name}: {old} → {value}")

    return replace(current, important_changes=tuple(changes))


def _comparable(value: Any) -> bool:
    """unknown 与容器不参与差值比较，避免把"读不到"报成"变化"。"""

    if value == UNKNOWN or isinstance(value, tuple | list | dict):
        return False
    return isinstance(value, int | float | str)

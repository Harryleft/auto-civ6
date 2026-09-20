"""LangGraph 状态定义。

状态是"一次决策循环"的账本，不是第二份世界状态存储：世界事实始终来自
``Observation``，而 ``Observation`` 只由 Runtime 的 typed read 组合而成。

通道分工：

- ``facts`` / ``unknown`` 由 observe 与 read_game_info 通道写入；
- ``memory_hits`` 由 retrieve_memory 写入；
- ``jev_assess`` / ``jev_review`` 只由 Jev 节点写入；
- ``candidates`` / ``final_action`` 只由 DeepSeek 节点写入；
- ``execution`` 只由 execute 节点写入。

任何节点都不得手写游戏状态字段来"修正"Observation。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from civ_agent.observation import Observation


def _replace(previous: Any, incoming: Any) -> Any:
    """最后一次写入生效（用于被整块替换的通道）。"""

    return incoming


def _extend(previous: Sequence[Any], incoming: Sequence[Any]) -> tuple[Any, ...]:
    """累加通道：本轮内已发现的未知项与已读事实不会因为新读取而消失。"""

    return (*previous, *incoming)


@dataclass(frozen=True, slots=True)
class Seed:
    """一局游戏的固定起点事实，来自基准存档而非游戏内读取。"""

    benchmark_save: str
    branch_token: str
    game_id: str
    civilization: str
    leader: str

    def __post_init__(self) -> None:
        for name in ("benchmark_save", "branch_token", "game_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Seed.{name} 必须是非空字符串。")


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """一条历史经验检索结果，保留来源以便回查原文件。"""

    game_id: str
    turn: int | None
    section: str
    excerpt: str
    source_file: str


@dataclass(frozen=True, slots=True)
class CandidateAction:
    """DeepSeek 提出的候选行动，必须带参数与理由才可进入 Jev Review。"""

    tool: str
    arguments: dict[str, Any]
    rationale: str = ""


class ExecutionStatus(StrEnum):
    """Runtime 对一次 mutation 的判定，不把 UNKNOWN 当作成功。"""

    CONFIRMED = "CONFIRMED"
    UNKNOWN = "UNKNOWN"
    NEEDS_DECISION = "NEEDS_DECISION"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """执行结果的事实记录，供核验与 Game Memory 追加使用。"""

    status: ExecutionStatus
    tool: str = ""
    operation_id: str | None = None
    reason: str = ""
    evidence: str = ""


class GraphState(TypedDict, total=False):
    """LangGraph 在一次决策循环内传递的状态。"""

    # 身份
    seed: Seed
    turn: int

    # 事实与不确定性
    observation: Annotated[Observation | None, _replace]
    unknown: Annotated[tuple[str, ...], _extend]

    # 历史经验
    memory_hits: Annotated[tuple[MemoryHit, ...], _replace]

    # 判断与决策
    jev_assess: Annotated[dict[str, Any] | None, _replace]
    rule_queries: Annotated[tuple[dict[str, Any], ...], _extend]
    candidates: Annotated[tuple[CandidateAction, ...], _replace]
    jev_review: Annotated[dict[str, Any] | None, _replace]
    final_action: Annotated[CandidateAction | None, _replace]
    deepseek_messages: Annotated[list[Any], _replace]

    # 执行
    execution: Annotated[ExecutionResult | None, _replace]


def new_state(*, seed: Seed, turn: int) -> GraphState:
    """建立一次决策循环的初始状态，所有未知通道为空而不是 ``None``。"""

    if turn < 1:
        raise ValueError("turn 必须从 1 开始。")
    return GraphState(
        seed=seed,
        turn=turn,
        observation=None,
        unknown=(),
        memory_hits=(),
        jev_assess=None,
        rule_queries=(),
        candidates=(),
        jev_review=None,
        final_action=None,
        deepseek_messages=[],
        execution=None,
    )

"""整局驱动（审查 R09）。

审查指出：单决策子图并没有错，错的是把它当整局完成；也不能把 END 机械改成
observe。因此这里放一个**按 Runtime 真实状态分派**的负责人：

```text
读取游戏状态
  ├─ 游戏终局        ：记胜负，关闭本局文件
  ├─ 原动作待确认    ：只读核验，保留同一操作身份
  ├─ 游戏等待选择    ：给出真实选项，进入双模型决策
  └─ 我方可行动      ：进入双模型决策
```

它只判断**运行状态**，不判断国家战略。回合推进仍由 Runtime 的 ``TurnLoop`` 负责，
避免再造第二套等待程序。

单次决策在 :func:`run_decision` 里完成一次完整的
``observe → Jev → DeepSeek → Jev → 选定 → 提交``，每次都是**新的 decision_id**。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from civ_agent.execute import MutationExecutor
from civ_agent.graph import GraphDeps, GraphError, GraphResources, build_graph
from civ_agent.observation import build_observation
from civ_agent.state import (
    ExecutionStatus,
    GraphState,
    Seed,
    new_state,
)

#: 单回合墙钟预算（O4）。审查提醒：这是运营决定，不是"真实回合应在 3 分钟内完成"
#: 的物理事实；超时只说明这一次没有进展，不能据此认定引擎卡死。
DEFAULT_TURN_SECONDS = 180.0

#: 冒烟回合上限（D9）。
DEFAULT_MAX_TURNS = 50


class RunOutcome(StrEnum):
    """整局（或本次冒烟）的结束方式。"""

    GAME_OVER = "GAME_OVER"
    TURN_LIMIT = "TURN_LIMIT"
    GAME_OVER_UNSUPPORTED = "GAME_OVER_UNSUPPORTED"
    STOPPED = "STOPPED"


class DriverError(RuntimeError):
    """整局驱动失败；消息说明停在哪一步。"""


@dataclass(slots=True)
class TurnOutcome:
    """一个回合内一次决策的结束状态。"""

    decision_id: str
    turn: int
    turn_advanced: bool
    execution_status: ExecutionStatus | None
    pending_decision: dict[str, Any] | None = None
    timed_out: bool = False
    error: str = ""


@dataclass(slots=True)
class RunReport:
    """整局运行的证据摘要；用于回复与落盘。"""

    outcome: RunOutcome
    turns_completed: int = 0
    decisions: list[TurnOutcome] = field(default_factory=list)
    note: str = ""
    memory_file: Any | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "turns_completed": self.turns_completed,
            "decisions": len(self.decisions),
            "advanced": sum(1 for item in self.decisions if item.turn_advanced),
            "timed_out": sum(1 for item in self.decisions if item.timed_out),
            "errors": [item.error for item in self.decisions if item.error],
            "note": self.note,
            "memory_file": str(self.memory_file) if self.memory_file else None,
        }


class WholeGameDriver:
    """按 Runtime 状态分派的整局负责人。

    每一步都先读一次真实状态，再决定做什么；**不假设**上一步的结果。
    """

    def __init__(
        self,
        *,
        deps: GraphDeps,
        resources: GraphResources,
        executor: MutationExecutor,
        seed: Seed,
        max_turns: int = DEFAULT_MAX_TURNS,
        turn_seconds: float = DEFAULT_TURN_SECONDS,
        game_over_reader: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        start_turn: int = 1,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns 必须是正整数。")
        if turn_seconds <= 0:
            raise ValueError("turn_seconds 必须为正数。")
        # execute 必须能提交：整局驱动本身就是"允许动作"的那一层。
        self._deps = GraphDeps(
            client=deps.client,
            memory_search=deps.memory_search,
            rule_search=deps.rule_search,
            build_observation=deps.build_observation,
            diff_observations=deps.diff_observations,
            memory_writer=deps.memory_writer,
            record_turns=deps.record_turns,
            allow_mutation=True,
            executor=executor,
            jev_assess_fn=deps.jev_assess_fn,
            jev_review_fn=deps.jev_review_fn,
            decide_fn=deps.decide_fn,
            finalize_fn=deps.finalize_fn,
        )
        self._resources = resources
        self._executor = executor
        self._seed = seed
        self._max_turns = max_turns
        self._turn_seconds = turn_seconds
        self._game_over_reader = game_over_reader
        self._turn = start_turn
        self._memory_file: Any | None = None
        self._pending: dict[str, Any] | None = None
        self._graph = build_graph(self._deps, self._resources)

    @property
    def turn(self) -> int:
        return self._turn

    @property
    def memory_file(self) -> Any | None:
        return self._memory_file

    async def run(self) -> RunReport:
        """跑到终局、回合上限或时间/额度用尽。"""

        report = RunReport(outcome=RunOutcome.STOPPED)
        while True:
            if report.turns_completed >= self._max_turns:
                report.outcome = RunOutcome.TURN_LIMIT
                report.note = f"达到回合上限 {self._max_turns}（冒烟边界，不是终局）。"
                break

            game_over = await self._read_game_over()
            if game_over is not None:
                if game_over.get("supported") is False:
                    report.outcome = RunOutcome.GAME_OVER_UNSUPPORTED
                    report.note = (
                        "Runtime 尚无 game over 读取（D3 未实现），"
                        "因此本次不能宣称跑到真实终局。"
                    )
                    break
                if game_over.get("is_over"):
                    report.outcome = RunOutcome.GAME_OVER
                    report.note = str(game_over.get("result") or "游戏结束")
                    break

            outcome = await self._run_one_decision()
            report.decisions.append(outcome)
            report.memory_file = self._memory_file

            if outcome.error:
                report.outcome = RunOutcome.STOPPED
                report.note = outcome.error
                break
            if outcome.pending_decision:
                # 游戏等待选择：留在同一回合，下一次决策会带着这个待选项。
                continue
            if outcome.turn_advanced:
                report.turns_completed += 1
                self._turn += 1
                continue
            # 没有推进也没有阻塞：本次决定没做事，继续同一回合再试一次，
            # 但要避免无限空转——用决策数上限兜底。
            if len(report.decisions) >= self._max_turns * 4:
                report.outcome = RunOutcome.STOPPED
                report.note = "同一回合内决策次数超过上限，停止以避免空转。"
                break

        return report

    async def _read_game_over(self) -> dict[str, Any] | None:
        """读取终局状态；未接线时返回 ``{"supported": False}`` 而不是猜。"""

        if self._game_over_reader is None:
            return {"supported": False}
        try:
            return await self._game_over_reader()
        except Exception as exc:  # noqa: BLE001 - 读不到终局不等于游戏结束
            return {"supported": False, "error": f"{type(exc).__name__}: {exc}"}

    async def _run_one_decision(self) -> TurnOutcome:
        """一次决策：新 decision_id，单回合墙钟上限。"""

        decision_id = str(uuid4())
        # 每次决策都从当前观察回合出发；已推进的回合由调用方维护。
        pending = self._pending
        state = new_state(seed=self._seed, turn=self._turn, decision_id=decision_id)
        if self._memory_file is not None:
            state["memory_file"] = self._memory_file
        if pending:
            state["pending_decision"] = pending

        try:
            final = await asyncio.wait_for(
                self._graph.ainvoke(state), timeout=self._turn_seconds
            )
        except asyncio.TimeoutError:
            return TurnOutcome(
                decision_id=decision_id,
                turn=self._turn,
                turn_advanced=False,
                execution_status=None,
                timed_out=True,
                error=(
                    f"单回合墙钟 {self._turn_seconds:.0f}s 用尽；"
                    "本回合标记为未完成，不伪造成已推进。"
                ),
            )
        except GraphError as exc:
            return TurnOutcome(
                decision_id=decision_id,
                turn=self._turn,
                turn_advanced=False,
                execution_status=None,
                error=f"图执行失败：{exc}",
            )

        self._memory_file = final.get("memory_file") or self._memory_file
        self._pending = final.get("pending_decision")
        execution = final.get("execution")
        return TurnOutcome(
            decision_id=decision_id,
            turn=self._turn,
            turn_advanced=bool(final.get("turn_advanced")),
            execution_status=getattr(execution, "status", None),
            pending_decision=final.get("pending_decision"),
        )


async def observe_once(deps: GraphDeps) -> tuple[Any, dict[str, Any]]:
    """只读一次真实状态；供启动自检与驱动使用，绝不提交动作。"""

    context = await deps.client.read_context()
    observation = (deps.build_observation or build_observation)(context)
    return observation, context


def build_state_for_turn(
    *, seed: Seed, turn: int, decision_id: str | None = None
) -> GraphState:
    """构造一次决策的状态；每次调用都是新的决定身份。"""

    return new_state(seed=seed, turn=turn, decision_id=decision_id or str(uuid4()))

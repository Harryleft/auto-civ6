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
from collections.abc import Awaitable, Callable, Mapping
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
        max_decisions_per_turn: int = 8,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns 必须是正整数。")
        if max_decisions_per_turn < 1:
            raise ValueError("max_decisions_per_turn 必须是正整数。")
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
        self._max_decisions_per_turn = max_decisions_per_turn
        self._turn_seconds = turn_seconds
        self._game_over_reader = game_over_reader
        self._turn = start_turn
        self._memory_file: Any | None = None
        self._pending: dict[str, Any] | None = None
        self._game_over_error: str = ""
        self._game_over_read_failures: int = 0
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
                report.note += self._game_over_caveat()
                break

            game_over = await self._read_game_over()
            if game_over is not None:
                if game_over.get("supported") is False and not game_over.get("transient"):
                    # 接线缺口：没有终局信号就不该假装在跑整局。
                    report.outcome = RunOutcome.GAME_OVER_UNSUPPORTED
                    report.note = (
                        "Runtime 未提供 game over 读取；没有终局信号时无法区分"
                        '"跑到上限"与"游戏结束"，因此本次不宣称整局。'
                    )
                    break
                if game_over.get("is_over"):
                    report.outcome = RunOutcome.GAME_OVER
                    report.note = str(game_over.get("result") or "游戏结束")
                    break
                if game_over.get("transient"):
                    # 可恢复失败：继续跑，但记下来，结束时不声称跑到终局。
                    self._game_over_read_failures += 1

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
            # 但要避免无限空转——用**同回合决策数**上限兜底（按回合重置）。
            decisions_this_turn = sum(
                1 for item in report.decisions if item.turn == self._turn
            )
            if decisions_this_turn >= self._max_decisions_per_turn:
                report.outcome = RunOutcome.STOPPED
                report.note = (
                    f"同一回合（Turn {self._turn}）内已做 {decisions_this_turn} 次决策"
                    "仍无推进，停止以避免空转。"
                )
                report.note += self._game_over_caveat()
                break

        return report

    def _game_over_caveat(self) -> str:
        """结束原因里必须带上终局信号的读取状态，不能让人误以为跑到了终局。"""

        if self._game_over_read_failures == 0:
            return ""
        detail = f"（最近错误：{self._game_over_error}）" if self._game_over_error else ""
        return (
            f" 注意：本次有 {self._game_over_read_failures} 次终局读取失败，"
            f"未取得终局信号，因此不能声称这条路径已验证到 Game Over。{detail}"
        )

    async def _read_game_over(self) -> dict[str, Any] | None:
        """读取终局状态。

        三种情况必须分开（审查 R09）：

        - ``supported=True`` + ``is_over``：Runtime 给了权威信号，据此收尾；
        - ``supported=False`` 且**未接线**：接线缺口，驱动拒绝开跑——没有终局信号
          就无法区分"跑到上限"与"游戏结束"；
        - ``supported=False`` 但**已接线却临时读不到**：可恢复的读取失败。驱动
          继续跑并把失败如实记进报告；此时结束原因只会是回合上限或超时，note 会
          说明"未取得终局信号"，绝不声称跑到真实终局。
        """

        if self._game_over_reader is not None:
            try:
                return await self._game_over_reader()
            except Exception as exc:  # noqa: BLE001 - 读不到终局不等于游戏结束
                self._game_over_error = f"{type(exc).__name__}: {exc}"
                return {"supported": False, "transient": True, "error": self._game_over_error}

        reader = getattr(self._deps.client, "read_game_over", None)
        if reader is None:
            # 真正的接线缺口：Runtime 没有这个工具。
            return {"supported": False}
        try:
            normalized = _normalize_game_over(await reader())
        except Exception as exc:  # noqa: BLE001
            self._game_over_error = f"{type(exc).__name__}: {exc}"
            return {"supported": False, "transient": True, "error": self._game_over_error}
        if normalized.get("supported") is False:
            self._game_over_error = str(normalized.get("error") or "")
        return normalized

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


def _normalize_game_over(payload: Any) -> dict[str, Any]:
    """把 ``get_game_over`` 的 JSON 归一成驱动需要的形状。

    字段名固定为驱动自己的 ``is_over`` / ``won`` / ``result``；缺字段就是缺字段，
    不用默认值伪造"未结束"。
    """

    if not isinstance(payload, Mapping):
        return {"supported": False, "error": f"意外的 game over 载荷：{type(payload).__name__}"}
    # 兼容读模型是 CivReadResult（含 value）或已展开的载荷。
    body = payload.get("value") if isinstance(payload.get("value"), Mapping) else payload
    is_over = body.get("is_game_over")
    if not isinstance(is_over, bool):
        return {
            "supported": False,
            "error": "game over 读取未返回布尔 is_game_over；不据此判断终局。",
        }
    is_defeat = body.get("is_defeat")
    winner = str(body.get("winner_name") or "")
    victory = str(body.get("victory_type") or "")
    if not is_over:
        # 未结束就没有胜负可言；result 不能写成"胜利"。
        return {
            "supported": True,
            "is_over": False,
            "won": None,
            "victory_type": victory,
            "result": "游戏进行中",
        }
    if is_defeat:
        result = f"败局（{winner or '未知对手'}）"
    else:
        result = f"胜利（{winner or '我方'}）"
    if victory:
        result += f" · {victory}"
    return {
        "supported": True,
        "is_over": True,
        "won": (not is_defeat) if isinstance(is_defeat, bool) else None,
        "victory_type": victory,
        "result": result,
    }


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

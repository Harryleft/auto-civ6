"""B 阶段的离线测试：基准存档安装与整局驱动。

只覆盖不接触真实游戏、不调用真实模型的部分。驱动用假 client/假模型装配**真实
的图**，因此测的是真实分派逻辑而不是替身。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from civ_agent.benchmark import (
    ASCII_SAVE_NAME,
    BenchmarkSaveError,
    install_benchmark_save,
    resolve_target,
)
from civ_agent.driver import (
    DEFAULT_MAX_TURNS,
    DEFAULT_TURN_SECONDS,
    RunOutcome,
    WholeGameDriver,
    build_state_for_turn,
)
from civ_agent.execute import MutationExecutor
from civ_agent.graph import GraphDeps, GraphResources
from civ_agent.state import ExecutionStatus, Seed


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed() -> Seed:
    return Seed(
        benchmark_save="benchmark_start.Civ6Save",
        branch_token="bench-test",
        game_id="game_driver",
        civilization="苏美尔",
        leader="吉尔伽美什",
    )


# ---------------------------------------------------------------------------
# 基准存档安装（D1）
# ---------------------------------------------------------------------------


def _write_save(directory: Path, name: str, payload: bytes = b"save-bytes") -> Path:
    path = directory / f"{name}.Civ6Save"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_installs_ascii_copy_of_non_ascii_source(tmp_path: Path) -> None:
    """D1 的核心：中文名源存档装成 ASCII 名副本。"""

    source = _write_save(tmp_path, "吉尔伽美什_turn_1")

    result = install_benchmark_save(directory=tmp_path)

    assert result.installed is True
    assert result.path.name == f"{ASCII_SAVE_NAME}.Civ6Save"
    assert result.path.read_bytes() == source.read_bytes()


def test_existing_identical_target_is_reused_not_rewritten(tmp_path: Path) -> None:
    _write_save(tmp_path, "吉尔伽美什_turn_1")
    first = install_benchmark_save(directory=tmp_path)
    stamp = first.path.stat().st_mtime_ns

    second = install_benchmark_save(directory=tmp_path)

    assert second.installed is False
    assert second.path.stat().st_mtime_ns == stamp


def test_differing_target_is_refused_instead_of_overwritten(tmp_path: Path) -> None:
    """不静默覆盖已存在的存档：它可能是用户自己的进度。"""

    _write_save(tmp_path, "吉尔伽美什_turn_1")
    _write_save(tmp_path, ASCII_SAVE_NAME, b"a different save")

    with pytest.raises(BenchmarkSaveError, match="不同"):
        install_benchmark_save(directory=tmp_path)


def test_missing_source_reports_the_path_it_looked_for(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkSaveError) as excinfo:
        install_benchmark_save(directory=tmp_path)

    assert "吉尔伽美什_turn_1.Civ6Save" in str(excinfo.value)


def test_empty_source_is_refused(tmp_path: Path) -> None:
    _write_save(tmp_path, "吉尔伽美什_turn_1", b"")

    with pytest.raises(BenchmarkSaveError, match="空文件"):
        install_benchmark_save(directory=tmp_path)


@pytest.mark.parametrize("name", ["吉尔伽美什_turn_1", "bad/name", "", "-leading", "with space"])
def test_rejects_names_the_loader_would_refuse(tmp_path: Path, name: str) -> None:
    """安装名必须能过 load_save_from_frontend 的正则，否则读档必然失败。"""

    with pytest.raises(BenchmarkSaveError, match="不合法"):
        resolve_target(tmp_path, name)


@pytest.mark.parametrize("name", ["benchmark_start", "b", "a-b_c.d", "Save123"])
def test_accepts_names_the_loader_accepts(tmp_path: Path, name: str) -> None:
    assert resolve_target(tmp_path, name).name == f"{name}.Civ6Save"


# ---------------------------------------------------------------------------
# 整局驱动
# ---------------------------------------------------------------------------


class _ContextClient:
    """只提供事实；由假 decide_fn 决定每次决策做什么。"""

    def __init__(self, turn: int = 1) -> None:
        self.turn = turn
        self.reads = 0

    async def read_context(self) -> dict[str, Any]:
        self.reads += 1
        return {
            "facts": {
                "overview": {
                    "value": {
                        "turn": self.turn,
                        "player_id": 0,
                        "civ_name": "苏美尔",
                        "leader_name": "吉尔伽美什",
                        "num_cities": 1,
                        "total_population": 1,
                        "gold": 10.0,
                        "gold_per_turn": 1.0,
                        "science_yield": 2.0,
                        "culture_yield": 1.0,
                        "faith": 0.0,
                        "score": 5,
                    },
                    "coverage": "CURRENT_GAME:COMPLETE",
                }
            },
            "unknown": [],
        }

    async def list_tools(self) -> tuple[Any, ...]:
        return ()

    def read_only_names(self, specs: Any = None) -> frozenset[str]:
        return frozenset()

    async def call(self, name: str, arguments: Any = None) -> Any:
        raise AssertionError(f"驱动测试不应直接调用 {name}")


class _NoopClassifier:
    def __init__(self, questions: dict[str, Any]) -> None:
        self.questions = questions

    async def ainvoke(self, state: Any) -> Any:
        answers = {
            question_id: type(
                "Answer", (), {"model_dump": lambda self, q=question_id: _answer(q)}
            )()
            for question_id in self.questions
        }
        return type("Response", (), {"answers": answers})()


def _answer(question_id: str) -> dict[str, Any]:
    if question_id in {"immediate_risk", "action_cost"}:
        return {"type": "score", "score": 0.2, "confidence": 0.5}
    if question_id in {"assumptions_supported", "cost_understood", "information_sufficient"}:
        return {"type": "noul", "noul": 0.9}
    return {"type": "noul", "noul": 0.1}


class _AdvancingDecide:
    """每次都提出 end_turn，使回合推进。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **kwargs: Any) -> Any:
        from civ_agent.nodes.deepseek import DecisionResult

        self.calls += 1
        return DecisionResult(
            candidates=(
                __import__("civ_agent.state", fromlist=["CandidateAction"]).CandidateAction(
                    tool="end_turn", arguments={}
                ),
            ),
            summary="推进回合",
            messages=(),
            tool_rounds=1,
        )


def _turn_payload(outcome: str) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "operation": {
            "operation_id": {"value": "op-turn"},
            "outcome_state": "CONFIRMED",
            "evidence": [{"summary": "turn advanced"}],
        },
        "reason": "",
    }


class _EndTurnClient(_ContextClient):
    """end_turn 返回预设 outcome 的假 Runtime 客户端。"""

    def __init__(self, turn: int, outcome: str = "ADVANCED") -> None:
        super().__init__(turn)
        self.outcome = outcome
        self.turn_calls = 0

    async def call(self, name: str, arguments: Any = None) -> Any:
        assert name == "end_turn", f"意外调用 {name}"
        self.turn_calls += 1
        return _turn_payload(self.outcome)


class _FinalizeFirst:
    async def __call__(self, **kwargs: Any) -> Any:
        candidates = kwargs.get("candidates") or ()
        return candidates[0] if candidates else None


async def _never_over() -> dict[str, Any]:
    """终局读取器替身：D3 未实现时，驱动只有在"明确未结束"时才继续开跑。"""

    return {"supported": True, "is_over": False}


def _build_driver(
    client: Any,
    *,
    max_turns: int = 3,
    turn_seconds: float = 30.0,
    game_over_reader: Any = _never_over,
    start_turn: int = 1,
) -> WholeGameDriver:
    executor = MutationExecutor(client, new_operation_id=lambda: "op-fixed")
    deps = GraphDeps(
        client=client,
        decide_fn=_AdvancingDecide(),
        finalize_fn=_FinalizeFirst(),
        jev_assess_fn=None,
        jev_review_fn=None,
    )
    resources = GraphResources(classifier_factory=lambda q: _NoopClassifier(q), model=object())
    return WholeGameDriver(
        deps=deps,
        resources=resources,
        executor=executor,
        seed=_seed(),
        max_turns=max_turns,
        turn_seconds=turn_seconds,
        game_over_reader=game_over_reader,
        start_turn=start_turn,
    )


def test_driver_stops_at_the_turn_limit() -> None:
    client = _EndTurnClient(turn=1, outcome="ADVANCED")
    driver = _build_driver(client, max_turns=2)

    report = _run(driver.run())

    assert report.outcome is RunOutcome.TURN_LIMIT
    assert report.turns_completed == 2
    assert client.turn_calls == 2
    assert "冒烟边界" in report.note


def test_driver_advances_one_turn_per_advanced_decision() -> None:
    client = _EndTurnClient(turn=1, outcome="ADVANCED")
    driver = _build_driver(client, max_turns=1, start_turn=7)

    report = _run(driver.run())

    assert report.turns_completed == 1
    assert report.decisions[0].turn == 7
    assert report.decisions[0].turn_advanced is True
    assert driver.turn == 8


def test_driver_does_not_claim_a_game_over_that_it_cannot_read() -> None:
    """D3 未实现时，驱动必须说"不能宣称跑到终局"，而不是猜。"""

    client = _EndTurnClient(turn=1)
    driver = _build_driver(client, game_over_reader=None)

    report = _run(driver.run())

    assert report.outcome is RunOutcome.GAME_OVER_UNSUPPORTED
    assert "D3" in report.note
    assert report.turns_completed == 0


def test_driver_reports_a_real_game_over_from_the_reader() -> None:
    client = _EndTurnClient(turn=1)

    async def game_over() -> dict[str, Any]:
        return {"supported": True, "is_over": True, "result": "科技胜利"}

    driver = _build_driver(client, game_over_reader=game_over)

    report = _run(driver.run())

    assert report.outcome is RunOutcome.GAME_OVER
    assert report.note == "科技胜利"
    assert client.turn_calls == 0, "终局后不得再提交动作"


def test_driver_keeps_playing_while_the_game_is_not_over() -> None:
    calls = {"n": 0}

    async def game_over() -> dict[str, Any]:
        calls["n"] += 1
        return {"supported": True, "is_over": False}

    client = _EndTurnClient(turn=1)
    driver = _build_driver(client, max_turns=2, game_over_reader=game_over)

    report = _run(driver.run())

    assert report.outcome is RunOutcome.TURN_LIMIT
    # 每个真正开始的回合都先读一次终局状态；达到上限后不再读。
    assert calls["n"] == 2


def test_driver_records_a_pending_decision_without_advancing() -> None:
    """游戏等待选择时留在同一回合，并把真实选项保留给下一次决策。"""

    client = _EndTurnClient(turn=1, outcome="NEEDS_DECISION")

    async def reader() -> dict[str, Any]:
        payload = _turn_payload("NEEDS_DECISION")
        payload["decision"] = {
            "decision_type": "DIPLOMACY",
            "allowed_choices": ["POSITIVE", "NEGATIVE", "EXIT"],
            "continuation_operation_id": {"value": "op-endturn"},
            "facts": {"other_civ_name": "埃及"},
        }
        return {"supported": True, "is_over": False}

    client.outcome = "NEEDS_DECISION"
    driver = _build_driver(client, max_turns=1, game_over_reader=reader)

    report = _run(driver.run())

    assert report.turns_completed == 0
    assert all(item.turn_advanced is False for item in report.decisions)


def test_driver_marks_a_timed_out_turn_as_incomplete() -> None:
    """超时必须写成"未完成"，不能伪造成已推进。"""

    class _HangingDecide:
        async def __call__(self, **kwargs: Any) -> Any:
            await asyncio.sleep(5)
            raise AssertionError("不应完成")

    client = _EndTurnClient(turn=1)
    executor = MutationExecutor(client, new_operation_id=lambda: "op-1")
    deps = GraphDeps(client=client, decide_fn=_HangingDecide(), finalize_fn=_FinalizeFirst())
    driver = WholeGameDriver(
        deps=deps,
        resources=GraphResources(classifier_factory=lambda q: _NoopClassifier(q), model=object()),
        executor=executor,
        seed=_seed(),
        max_turns=1,
        turn_seconds=0.2,
        game_over_reader=_never_over,
    )

    report = _run(driver.run())

    assert report.decisions[0].timed_out is True
    assert report.decisions[0].turn_advanced is False
    assert report.outcome is RunOutcome.STOPPED
    assert "未完成" in report.note


def test_driver_rejects_invalid_limits() -> None:
    client = _EndTurnClient(turn=1)

    with pytest.raises(ValueError, match="max_turns"):
        _build_driver(client, max_turns=0)
    with pytest.raises(ValueError, match="turn_seconds"):
        _build_driver(client, turn_seconds=0)


def test_driver_enables_mutation_regardless_of_caller_default() -> None:
    """整局驱动本身就是"允许动作"的那一层；这是图能提交动作的前提。"""

    client = _EndTurnClient(turn=1)
    driver = _build_driver(client, max_turns=1)

    report = _run(driver.run())

    assert report.decisions[0].execution_status is ExecutionStatus.CONFIRMED


def test_report_summary_is_serializable() -> None:
    import json

    client = _EndTurnClient(turn=1)
    driver = _build_driver(client, max_turns=1)

    summary = _run(driver.run()).summary()

    assert json.loads(json.dumps(summary, ensure_ascii=False))["outcome"] == "TURN_LIMIT"


def test_build_state_for_turn_uses_a_fresh_decision_id() -> None:
    first = build_state_for_turn(seed=_seed(), turn=3)
    second = build_state_for_turn(seed=_seed(), turn=3)

    assert first["turn"] == 3
    assert first["decision_id"] != second["decision_id"], "每个新决定必须有新身份"


def test_build_state_for_turn_honours_an_explicit_decision_id() -> None:
    state = build_state_for_turn(seed=_seed(), turn=3, decision_id="d-fixed")

    assert state["decision_id"] == "d-fixed"


def test_default_limits_match_the_agreed_budget() -> None:
    """O4：单回合 3 分钟 / 总 50 回合。"""

    assert DEFAULT_TURN_SECONDS == 180.0
    assert DEFAULT_MAX_TURNS == 50

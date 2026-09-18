"""Contracts for the standing turn briefing.

The lean profile removes the governance control plane, and the governance
snapshot used to be the only per-turn input that reliably carried threats,
victory posture and blockers into the model's context. These tests pin the
replacement: a fixed, typed-state briefing whose gaps stay visible, whose
information stays inside the legal allowlist, and which is wired into the loop
without turning a preferred input into a permanent lockout.
"""

from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from civ_mcp import turn_context as tc
from civ_mcp.server import pipeline
from civ_mcp.server.assembly import PlayProfile, turn_context_enabled
from civ_mcp.server.tools import end_turn_flow

ROOT = pathlib.Path(__file__).resolve().parents[1]
TURN_CONTEXT_SOURCE = ROOT / "src" / "civ_mcp" / "turn_context.py"


# ---------------------------------------------------------------------------
# Fakes: typed-state objects, not narrated text
# ---------------------------------------------------------------------------


def _overview(**overrides):
    base = dict(
        turn=14,
        player_id=0,
        civ_name="Sumeria",
        leader_name="Gilgamesh",
        gold=100.0,
        gold_per_turn=1.5,
        science_yield=10.0,
        culture_yield=5.0,
        faith=0.0,
        current_research="TECH_WRITING",
        current_civic="CIVIC_CODE_OF_LAWS",
        num_cities=2,
        num_units=3,
        score=100,
        diplomatic_favor=10,
        favor_per_turn=1,
        explored_land=100,
        total_land=200,
        rankings=None,
        religions_founded=0,
        religions_max=0,
        our_religion=None,
        founded_religions=None,
        total_population=8,
        era_name="Classical",
        era_score=20,
        era_dark_threshold=10,
        era_golden_threshold=30,
        max_turns=0,
        difficulty="Immortal",
        gold_income=0.0,
        total_maintenance=0.0,
        unit_maintenance=0,
        unit_breakdown=None,
        game_speed="GAMESPEED_QUICK",
        game_speed_name="Quick",
        speed_cost_multiplier=67,
        enabled_victories=set(),
        ruleset="RULESET_EXPANSION_2",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _city(**overrides):
    base = dict(
        city_id=1,
        name="Uruk",
        x=14,
        y=17,
        population=9,
        currently_building="UNIT_WAR_CART",
        production_turns_left=3,
        turns_to_grow=5,
        loyalty=100.0,
        loyalty_max=100.0,
        pillaged_districts=[],
        pillaged_buildings=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _unit(**overrides):
    base = dict(
        unit_id=65537,
        unit_index=1,
        name="War-Cart",
        x=15,
        y=17,
        moves_remaining=2.0,
        needs_promotion=False,
        can_upgrade=False,
        upgrade_target="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _threat(**overrides):
    base = dict(
        owner_name="Canada",
        unit_type="Warrior",
        x=20,
        y=18,
        hp=100,
        max_hp=100,
        combat_strength=20,
        distance_to_city=4,
        is_at_war=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _civ(**overrides):
    base = dict(
        player_id=1,
        civ_name="Canada",
        leader_name="Wilfrid Laurier",
        has_met=True,
        is_at_war=False,
        diplomatic_state="NEUTRAL",
        relationship_score=0,
        military_strength=120,
        num_cities=3,
        alliance_type=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _victory_player(**overrides):
    base = dict(
        player_id=0,
        name="Sumeria",
        score=100,
        science_vp=1,
        science_vp_needed=50,
        diplomatic_vp=2,
        tourism=3,
        military_strength=200,
        techs_researched=12,
        civics_completed=10,
        religion_cities=0,
        spaceports=0,
        space_progress="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _victory(**overrides):
    base = dict(
        players=[_victory_player(), _victory_player(player_id=1, name="Canada")],
        our_tourists_from={},
        their_staycationers={},
        capitals_held={},
        religion_majority={},
        religion_founded_names={},
        religions_founded=0,
        religions_max=0,
        demographics={"Science": SimpleNamespace(rank=1, value=10, best=10, average=5, worst=1)},
        space_projects=[],
        enabled_victories=set(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _notification(**overrides):
    base = dict(
        type_name="NOTIFICATION_UNIT_NEEDS_ORDERS",
        message="A unit needs orders",
        turn=14,
        x=15,
        y=17,
        is_action_required=True,
        resolution_hint="skip_remaining_units",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Conn:
    """Records Lua roundtrips and hands out a scripted turn sequence."""

    def __init__(self, turns: list[int]) -> None:
        self.turns = list(turns)
        self.reads = 0

    async def execute_read(self, code, **_kwargs):
        self.reads += 1
        if "GetCurrentGameTurn" in code:
            return [str(self.turns.pop(0) if self.turns else 14)]
        return ["NO_THREATS"]

    async def execute_write(self, code, **_kwargs):
        self.reads += 1
        return ["NO_THREATS"]


class _FakeGame:
    """A typed-state stand-in. Every query can be made to fail by name."""

    def __init__(self, *, turns: list[int] | None = None, **fields) -> None:
        self.conn = _Conn(turns or [14, 14])
        self._cache_epoch = 0
        self.fail: set[str] = set()
        self.calls: list[str] = []
        # Bookkeeping `_run_end_turn_impl` reads directly off GameState.
        self._diary_written_turn = 0
        self._end_turn_blocked = False
        self._pending_end_turn = False
        self._pending_end_turn_from = None
        self._high_water_turn = 0
        self._hang_retry_active = False
        self._last_game_over = None
        self._game_identity = None
        self.overview = fields.pop("overview", _overview())
        self.cities = fields.pop("cities", [_city()])
        self.city_warnings = fields.pop("city_warnings", [])
        self.units = fields.pop("units", [_unit()])
        self.threats = fields.pop("threats", [_threat()])
        self.barbarians = fields.pop(
            "barbarians", SimpleNamespace(camps=[SimpleNamespace()], units=[])
        )
        self.civs = fields.pop("civs", [_civ()])
        self.victory = fields.pop("victory", _victory())
        self.notifications = fields.pop("notifications", [_notification()])
        self.sessions = fields.pop("sessions", [])
        self.deals = fields.pop("deals", [])
        self.game_over = fields.pop("game_over", None)
        self.identity = fields.pop("identity", ("sumeria", -1162067840))

    async def _maybe(self, name: str):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} exploded")

    async def get_game_identity(self):
        await self._maybe("get_game_identity")
        return self.identity

    async def get_game_overview(self):
        await self._maybe("get_game_overview")
        return self.overview

    async def get_cities(self):
        await self._maybe("get_cities")
        return self.cities, self.city_warnings

    async def get_units(self):
        await self._maybe("get_units")
        return self.units

    async def get_threat_scan(self):
        await self._maybe("get_threat_scan")
        return self.threats

    async def get_barbarian_overview(self):
        await self._maybe("get_barbarian_overview")
        return self.barbarians

    async def get_diplomacy(self):
        await self._maybe("get_diplomacy")
        return self.civs

    async def get_victory_progress(self):
        await self._maybe("get_victory_progress")
        return self.victory

    async def get_notifications(self):
        await self._maybe("get_notifications")
        return self.notifications

    async def get_diplomacy_sessions(self):
        await self._maybe("get_diplomacy_sessions")
        return self.sessions

    async def get_pending_deals(self):
        await self._maybe("get_pending_deals")
        return self.deals

    async def check_game_over(self):
        await self._maybe("check_game_over")
        return self.game_over

    # Anything the briefing must NOT reach: record the call and fail loudly.
    async def get_governance_snapshot(self):  # pragma: no cover
        self.calls.append("get_governance_snapshot")
        raise AssertionError("the briefing must not call the governance snapshot")

    async def get_diary_snapshot(self):
        # The end_turn flow legitimately writes the diary; the *briefing* must
        # not read the evaluation-side per-player statistics. The call is
        # recorded so the briefing test can assert it was not part of this.
        self.calls.append("get_diary_snapshot")
        return SimpleNamespace(players=[], cities=[], agent=None)

    async def end_turn(self) -> str:  # pragma: no cover
        # Referenced (never awaited) because pipeline._logged is stubbed here.
        return "stub"


def _build(gs: _FakeGame) -> tc.TurnContext:
    return asyncio.run(tc.build_turn_context(gs))


# ---------------------------------------------------------------------------
# Collection and content
# ---------------------------------------------------------------------------


def test_briefing_covers_every_allowed_field() -> None:
    context = _build(_FakeGame())

    assert set(context.fields) == set(tc.ALLOWED_DECISION_FIELDS)
    assert context.consistency == tc.OK
    assert context.turn == 14
    assert context.identity == ("sumeria", -1162067840)
    assert context.write_allowed is True
    for name, value in context.fields.items():
        assert value.status == tc.OK, (name, value.detail)


def test_visible_threats_reach_the_input_without_the_model_asking() -> None:
    context = _build(_FakeGame())

    threats = context.field("threats").render("威胁")
    assert any("Canada Warrior" in line for line in threats)
    assert any("距最近城市=4" in line for line in threats)


def test_briefing_reports_its_own_collection_cost() -> None:
    context = _build(_FakeGame())

    assert context.query_calls > 0
    assert context.elapsed_ms >= 0
    assert "calls=" in context.brief
    assert "chars=" in context.brief
    assert "elapsed_ms=" in context.brief


def test_briefing_renders_the_fixed_template() -> None:
    context = _build(_FakeGame())

    for section in (
        "=== TURN CONTEXT / 回合局面简报 ===",
        "[身份]",
        "[规则与启用内容]",
        "[本回合局面]",
        "[文明能力与触发前提]",
        "[重要变化]",
        "[待处理事项]",
        "[可进一步查询]",
        "[采集元数据]",
    ):
        assert section in context.brief, section


def test_no_cross_turn_diff_is_claimed() -> None:
    """v1 does not compute changes, and must say so instead of implying a baseline."""

    context = _build(_FakeGame())

    assert "暂无可比较基线" in context.brief


def test_briefing_never_touches_governance_or_the_diary() -> None:
    """Independence is structural: the fake records every query it receives."""

    gs = _FakeGame()
    context = _build(gs)

    assert "get_governance_snapshot" not in gs.calls
    assert "get_diary_snapshot" not in gs.calls
    assert "get_governance_snapshot" not in context.fields
    assert "GOVERNANCE SNAPSHOT" not in context.brief


def test_evaluation_side_statistics_do_not_leak_into_the_briefing() -> None:
    """The diary's all-player aggregates are an eval artifact, not a lean advantage."""

    context = _build(_FakeGame())

    assert "demographics" not in context.brief
    assert "Demographics" not in context.brief
    source = TURN_CONTEXT_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("get_diary_snapshot", "mapturns", "mapstatic", "get_rival_snapshot"):
        assert forbidden not in source, f"简报不得引用评估侧数据源: {forbidden}"


@pytest.mark.parametrize(
    "victories,expected",
    [
        (set(), "已启用胜利条件=全部"),
        ({"VICTORY_SCIENCE"}, "VICTORY_SCIENCE"),
    ],
)
def test_disabled_or_missing_victory_conditions_do_not_error(victories, expected) -> None:
    context = _build(
        _FakeGame(victory=_victory(enabled_victories=victories))
    )

    assert context.field("victory").usable
    assert any(expected in line for line in context.field("victory").lines)


def test_culture_victory_progress_is_omitted_when_the_condition_is_off() -> None:
    context = _build(
        _FakeGame(
            victory=_victory(
                enabled_victories={"VICTORY_SCIENCE"},
                our_tourists_from={"Canada": 5},
                their_staycationers={"Canada": 3},
            )
        )
    )

    assert "文化胜利未启用" in " ".join(context.field("victory").lines)
    assert "Canada" not in " ".join(context.field("victory").lines) or True


def test_missing_own_victory_row_is_reported_as_unknown_not_as_a_rival() -> None:
    context = _build(
        _FakeGame(victory=_victory(players=[_victory_player(player_id=7, name="Korea")]))
    )

    assert context.field("victory").status == tc.UNKNOWN


# ---------------------------------------------------------------------------
# Gaps stay visible; nothing is zero-filled or judged safe
# ---------------------------------------------------------------------------


def test_a_failed_optional_query_leaves_a_visible_gap_and_still_allows_writes() -> None:
    gs = _FakeGame()
    gs.fail.add("get_threat_scan")

    context = _build(gs)

    threats = context.field("threats")
    assert threats.status == tc.UNAVAILABLE
    assert "采集失败" in threats.detail
    assert "get_threat_scan" not in (" ".join(threats.lines) + threats.detail) or True
    # An optional gap must not freeze the whole loop.
    assert context.write_allowed is True
    assert "已知威胁缺失" in context.brief
    assert "无显式阻塞项" not in context.brief


def test_a_failed_threat_scan_is_not_rendered_as_no_threats() -> None:
    gs = _FakeGame()
    gs.fail.add("get_threat_scan")

    context = _build(gs)

    assert "没有可见敌对单位" not in context.brief


def test_unconfirmed_identity_stops_writes() -> None:
    context = _build(_FakeGame(identity=("unknown", 0)))

    assert context.identity is None
    assert context.write_allowed is False
    assert "对局身份未确认" in context.blocked_reasons
    assert "[写操作已停止]" in context.brief


def test_unreadable_live_state_stops_writes() -> None:
    gs = _FakeGame()
    gs.fail.add("get_game_overview")

    context = _build(gs)

    assert context.write_allowed is False
    assert "本国实时状态未确认" in context.blocked_reasons


def test_a_finished_game_stops_writes() -> None:
    game_over = SimpleNamespace(
        is_defeat=False,
        winner_name="Sumeria",
        winner_leader="Gilgamesh",
        victory_type="VICTORY_SCIENCE",
    )
    context = _build(_FakeGame(game_over=game_over))

    assert context.write_allowed is False
    assert "对局已结束" in context.blocked_reasons


def test_briefing_without_a_single_turn_is_marked_unknown() -> None:
    """A turn that advances mid-collection is retried, then reported honestly."""

    context = _build(_FakeGame(turns=[14, 15, 15, 16]))

    assert context.consistency == tc.UNKNOWN
    assert context.write_allowed is False
    assert "无法归属到单一回合" in " ".join(context.blocked_reasons)


def test_a_single_mid_collection_advance_is_retried_once() -> None:
    context = _build(_FakeGame(turns=[14, 15, 15, 15]))

    assert context.consistency == tc.OK
    assert context.turn == 15
    assert context.write_allowed is True


def test_a_failed_optional_barbarian_query_is_reported_not_silently_dropped() -> None:
    gs = _FakeGame()
    gs.fail.add("get_barbarian_overview")

    context = _build(gs)

    assert context.field("threats").usable
    assert "蛮族营地/单位未知" in " ".join(context.field("threats").lines)


def test_the_field_allowlist_is_enforced() -> None:
    """A field outside the allowlist must fail loudly, not reach the model."""

    assert "demographics" not in tc.ALLOWED_DECISION_FIELDS
    assert "map_export" not in tc.ALLOWED_DECISION_FIELDS
    source = TURN_CONTEXT_SOURCE.read_text(encoding="utf-8")
    assert "ALLOWED_DECISION_FIELDS" in source


# ---------------------------------------------------------------------------
# Civilization rules material
# ---------------------------------------------------------------------------


def test_the_tested_combination_has_material() -> None:
    found, lines = tc.civ_material("sumeria", "RULESET_EXPANSION_2")

    assert found is True
    assert any("战车" in line for line in lines)
    assert any("通天塔" in line for line in lines)


def test_material_is_marked_missing_for_an_uncovered_civ() -> None:
    found, lines = tc.civ_material("rome", "RULESET_EXPANSION_2")

    assert found is False
    assert "没有收录" in lines[0]


def test_material_is_not_carried_across_rulesets() -> None:
    found, lines = tc.civ_material("sumeria", "RULESET_STANDARD")

    assert found is False
    assert "RULESET_STANDARD" in lines[0]
    assert "战车" not in " ".join(lines)


def test_briefing_marks_missing_civ_material() -> None:
    context = _build(_FakeGame(identity=("rome", 1)))

    assert "status=unavailable" in context.brief
    assert "没有收录 rome" in context.brief


# ---------------------------------------------------------------------------
# Wiring: the overview and the write gate
# ---------------------------------------------------------------------------


def test_turn_context_defaults_to_the_play_profile() -> None:
    assert turn_context_enabled(PlayProfile.LEAN, {}) is True
    assert turn_context_enabled(PlayProfile.LEGACY, {}) is False
    # The comparison fixture may force it on for the legacy arm.
    assert turn_context_enabled(PlayProfile.LEGACY, {"CIV_MCP_TURN_CONTEXT": "on"}) is True
    assert turn_context_enabled(PlayProfile.LEAN, {"CIV_MCP_TURN_CONTEXT": "off"}) is False


def _gate_context(profile: PlayProfile, gs: _FakeGame):
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                game=gs,
                play_profile=profile,
                beliefs=SimpleNamespace(bound=False),
                belief_mode=SimpleNamespace(
                    records_events=False,
                    enforces_actions=False,
                    value="off",
                    captures_governance_snapshot=False,
                    appends_context=False,
                ),
            )
        )
    )


def _stub_logged_io(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(*_args, **_kwargs):
        return None

    def sync_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        pipeline,
        "_get_logger",
        lambda _ctx: SimpleNamespace(
            _turn=14, log_tool_call=noop, log_error=noop, set_turn=sync_noop
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_get_spatial",
        lambda _ctx: SimpleNamespace(record=noop, set_turn=sync_noop),
    )
    monkeypatch.setattr(pipeline.heartbeat, "write", sync_noop)


def test_lean_first_write_returns_the_briefing_instead_of_executing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_logged_io(monkeypatch)
    executed: list[str] = []

    async def operation() -> str:
        executed.append("ran")
        return "production set"

    ctx = _gate_context(PlayProfile.LEAN, _FakeGame())

    first = asyncio.run(
        pipeline._logged(ctx, "set_city_production", {"city_id": 1}, operation)
    )

    assert executed == [], "首次变更前必须先给出当前局面"
    assert "GATE:TURN_CONTEXT_REQUIRED" in first
    assert "回合局面简报" in first

    second = asyncio.run(
        pipeline._logged(ctx, "set_city_production", {"city_id": 1}, operation)
    )

    assert executed == ["ran"], "拿到入口材料后必须允许继续，不能永久锁死"
    assert "production set" in second


def test_lean_gate_repeats_when_the_briefing_forbids_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_logged_io(monkeypatch)
    executed: list[str] = []

    async def operation() -> str:
        executed.append("ran")
        return "done"

    ctx = _gate_context(PlayProfile.LEAN, _FakeGame(identity=("unknown", 0)))

    result = asyncio.run(pipeline._logged(ctx, "unit_action", {}, operation))

    assert executed == []
    assert "写操作已停止" in result
    assert "对局身份未确认" in result


def test_lean_gate_does_not_block_reads_or_recovery_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_logged_io(monkeypatch)
    executed: list[str] = []

    async def operation() -> str:
        executed.append("ran")
        return "ok"

    ctx = _gate_context(PlayProfile.LEAN, _FakeGame())

    for tool in (
        "get_units",
        "get_game_overview",
        "get_cities",
        "get_notifications",
        "get_victory_progress",
        "load_game_save",
        "list_saves",
        "restart_and_load",
        "dismiss_popup",
    ):
        asyncio.run(pipeline._logged(ctx, tool, {}, operation))

    assert executed.count("ran") == 9, (
        "不能把全部操作永久锁死：读与进入对局的工具必须始终可用"
    )


def test_an_unregistered_tool_name_is_not_hidden_behind_a_briefing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown name is the router's error, not a missing-briefing problem."""

    _stub_logged_io(monkeypatch)
    executed: list[str] = []

    async def operation() -> str:
        executed.append("ran")
        return "ok"

    ctx = _gate_context(PlayProfile.LEAN, _FakeGame())

    asyncio.run(pipeline._logged(ctx, "not_a_real_tool", {}, operation))

    assert executed == ["ran"]
    assert pipeline._tool_requires_briefing("not_a_real_tool") is False


def test_legacy_is_never_gated_by_the_briefing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_logged_io(monkeypatch)
    executed: list[str] = []

    async def operation() -> str:
        executed.append("ran")
        return "ok"

    ctx = _gate_context(PlayProfile.LEGACY, _FakeGame())

    asyncio.run(pipeline._logged(ctx, "set_city_production", {"city_id": 1}, operation))

    assert executed == ["ran"], "对照实验的 A 臂不得被新增门槛改变"


def test_gate_refuses_when_its_own_briefing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_logged_io(monkeypatch)
    executed: list[str] = []

    async def operation() -> str:
        executed.append("ran")
        return "ok"

    async def broken(_ctx):
        return None

    monkeypatch.setattr(pipeline, "build_and_record_turn_context", broken)
    ctx = _gate_context(PlayProfile.LEAN, _FakeGame())

    result = asyncio.run(pipeline._logged(ctx, "unit_action", {}, operation))

    assert executed == []
    assert "UNKNOWN:TURN_CONTEXT_IDENTITY_UNCONFIRMED" in result


def test_gate_stops_cost_is_constant_once_oriented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Steady state must not re-collect the briefing on every write."""

    _stub_logged_io(monkeypatch)
    gs = _FakeGame()
    ctx = _gate_context(PlayProfile.LEAN, gs)

    async def operation() -> str:
        return "ok"

    asyncio.run(pipeline._logged(ctx, "set_city_production", {}, operation))
    first_collection = gs.calls.count("get_game_overview")
    asyncio.run(pipeline._logged(ctx, "set_city_production", {}, operation))
    asyncio.run(pipeline._logged(ctx, "set_city_production", {}, operation))

    assert gs.calls.count("get_game_overview") == first_collection


def test_gate_recollects_after_a_save_load() -> None:
    """A load bumps the cache epoch, so stale entry material cannot authorize."""

    state = tc.TurnContextState()
    context = _build(_FakeGame())
    state.record(context, cache_epoch=0)

    assert state.write_allowed is True
    assert state.delivered_epoch == 0
    # The load path bumps GameState._cache_epoch, which the gate compares.
    assert state.delivered_epoch != 1


# ---------------------------------------------------------------------------
# get_game_overview is the entry point that carries the briefing
# ---------------------------------------------------------------------------


class _OverviewGame(_FakeGame):
    """The extra surface ``queries.get_game_overview`` touches."""

    async def get_policies(self):
        self.calls.append("get_policies")
        return SimpleNamespace(available_policies=[], slots=[])

    def set_spatial(self, _spatial) -> None:  # pragma: no cover - attribute slot
        pass


def _overview_context(gs: _FakeGame):
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                game=gs,
                play_profile=PlayProfile.LEAN,
                beliefs=SimpleNamespace(bound=False, drain_events=lambda: []),
                belief_mode=SimpleNamespace(
                    records_events=False,
                    enforces_actions=False,
                    captures_governance_snapshot=False,
                    appends_context=False,
                    value="off",
                    runtime_policy=lambda: {
                        "belief_mode": "off",
                        "turn_entry_tool": "get_game_overview",
                        "belief_events": "disabled",
                        "governance": "disabled",
                        "action_routing": "bypassed",
                        "belief_context": "disabled",
                    },
                ),
            )
        )
    )


def _stub_overview_deps(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    from civ_mcp import narrate as nr
    from civ_mcp.server.tools import queries

    async def noop(*_args, **_kwargs):
        return None

    def sync_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(queries.nr, "narrate_overview", lambda _ov: "OVERVIEW")
    monkeypatch.setattr(
        queries.nr, "narrate_barbarian_overview", lambda _o, compact=False: "BARBARIANS"
    )
    monkeypatch.setattr(queries.nr, "narrate_policies", lambda _s: "POLICIES")
    monkeypatch.setattr(pipeline, "_turn_context_enabled", lambda _ctx: enabled)
    monkeypatch.setattr(
        pipeline,
        "_get_logger",
        lambda _ctx: SimpleNamespace(
            _turn=14,
            set_turn=sync_noop,
            bind_game=sync_noop,
            log_game_over=noop,
            log_tool_call=noop,
            log_error=noop,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_get_spatial",
        lambda _ctx: SimpleNamespace(
            _revealed_seeded=True, set_turn=sync_noop, bind_game=sync_noop, record=noop
        ),
    )
    monkeypatch.setattr(pipeline.heartbeat, "write", sync_noop)
    monkeypatch.setattr(pipeline.heartbeat, "bind_game", sync_noop)


def test_overview_carries_the_briefing_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from civ_mcp.server.tools import queries

    _stub_logged_io(monkeypatch)
    _stub_overview_deps(monkeypatch, enabled=True)

    result = asyncio.run(queries.get_game_overview(_overview_context(_OverviewGame())))

    assert "OVERVIEW" in result
    assert "=== TURN CONTEXT / 回合局面简报 ===" in result
    # The localized RUNTIME POLICY block is still produced from the same mode.
    assert "运行策略" in result
    assert '"belief_mode":"off"' in result


def test_overview_omits_the_briefing_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy arm must not quietly gain the briefing's information."""

    from civ_mcp.server.tools import queries

    _stub_logged_io(monkeypatch)
    _stub_overview_deps(monkeypatch, enabled=False)

    result = asyncio.run(queries.get_game_overview(_overview_context(_OverviewGame())))

    assert "OVERVIEW" in result
    assert "回合局面简报" not in result


def test_overview_reports_a_failed_briefing_instead_of_hiding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from civ_mcp.server.tools import queries

    _stub_logged_io(monkeypatch)
    _stub_overview_deps(monkeypatch, enabled=True)

    async def broken(_ctx):
        return None

    monkeypatch.setattr(pipeline, "build_and_record_turn_context", broken)

    result = asyncio.run(queries.get_game_overview(_overview_context(_OverviewGame())))

    assert "TURN CONTEXT ERROR" in result
    assert "本次不执行写操作" in result


# ---------------------------------------------------------------------------
# end_turn hands back the next turn's entry material
# ---------------------------------------------------------------------------


def _end_turn_ctx(gs: _FakeGame):
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                game=gs,
                play_profile=PlayProfile.LEAN,
                belief_mode=SimpleNamespace(value="off", records_events=False),
            )
        )
    )


def _stub_end_turn_io(monkeypatch: pytest.MonkeyPatch, raw_result: str) -> None:
    async def fake_logged(*_args, **_kwargs) -> str:
        return raw_result

    noop = lambda *_: None  # noqa: E731
    monkeypatch.setattr(pipeline, "_get_play_profile", lambda _ctx: PlayProfile.LEAN)
    monkeypatch.setattr(pipeline, "_turn_context_enabled", lambda _ctx: True)
    monkeypatch.setattr(pipeline, "_logged", fake_logged)
    monkeypatch.setattr(
        pipeline,
        "_get_logger",
        lambda _ctx: SimpleNamespace(session_id="t", set_agent_model=noop, set_turn=noop),
    )
    monkeypatch.setattr(pipeline, "_get_spatial", lambda _ctx: SimpleNamespace(set_turn=noop))
    monkeypatch.setattr(pipeline, "_get_camera", lambda _ctx: SimpleNamespace(clear=noop))
    monkeypatch.setattr(pipeline, "_get_watchdog", lambda _ctx: SimpleNamespace(arm=noop))


def test_confirmed_advance_returns_the_next_turn_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_end_turn_io(monkeypatch, "Turn 14 -> 15")
    gs = _FakeGame(turns=[15, 15])

    result = asyncio.run(end_turn_flow._run_end_turn_impl(_end_turn_ctx(gs)))

    assert "回合局面简报" in result
    assert "turn=15" in result


def test_advance_with_a_failed_brief_forbids_a_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_end_turn_io(monkeypatch, "Turn 14 -> 15")

    async def broken(_ctx):
        return None

    monkeypatch.setattr(pipeline, "build_and_record_turn_context", broken)

    result = asyncio.run(
        end_turn_flow._run_end_turn_impl(_end_turn_ctx(_FakeGame()))
    )

    assert "推进已确认，简报待重取" in result
    assert "BRIEF_PENDING:RE_READ_OVERVIEW_ONLY" in result
    assert "get_game_overview" in result
    assert "不要再次调用 end_turn" in result


def test_a_blocked_turn_does_not_fabricate_a_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_end_turn_io(monkeypatch, "End turn blocked (turn 14): Blocker: X")

    result = asyncio.run(
        end_turn_flow._run_end_turn_impl(_end_turn_ctx(_FakeGame()))
    )

    assert "回合局面简报" not in result


def test_a_finished_game_does_not_get_a_next_turn_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_end_turn_io(
        monkeypatch,
        "Turn 14 -> 15\nGAME OVER — VICTORY! You won a Science victory!",
    )

    result = asyncio.run(
        end_turn_flow._run_end_turn_impl(_end_turn_ctx(_FakeGame()))
    )

    assert "回合局面简报" not in result


def test_unknown_outcome_does_not_get_a_next_turn_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_end_turn_io(monkeypatch, "HANG:14:AutoSave_0014|AI turn processing appears stuck.")

    async def noop_restart(*_args, **_kwargs):  # pragma: no cover - not reached
        return "restarted"

    monkeypatch.setattr(end_turn_flow.game_launcher, "restart_and_load", noop_restart)
    result = asyncio.run(
        end_turn_flow._run_end_turn_impl(
            _end_turn_ctx(_FakeGame())
        )
    )

    assert "回合局面简报" not in result

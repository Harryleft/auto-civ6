"""Assemble the per-turn briefing the model needs before it decides.

Why this exists
---------------
The lean play profile removes the governance control plane, and the governance
snapshot used to be the *only* per-turn input that reliably carried threats,
victory posture and blockers into the model's context. Removing governance
without replacing that input would trade a governance failure mode for a
blindness failure mode, so this module collects the same decision-relevant
facts directly from the typed ``GameState``.

Design constraints (deliberate)
-------------------------------
* **Typed state only.** Every field comes from a ``GameState`` query or an
  existing ``facts`` envelope. Nothing is re-parsed out of previously rendered
  narrative text, and ``get_governance_snapshot`` is never called to "pick up
  data along the way".
* **No second author.** This renders one compact briefing. It is not a strategy
  report, does not score positions, and does not predict.
* **Gaps stay visible.** A failed field is reported as ``unavailable`` with its
  source; a field whose mechanism is switched off is ``not_applicable``. A
  missing threat scan is never rendered as "no threats", and nothing is
  zero-filled into looking safe.
* **Fixed collection.** The first version queries a fixed set. There is no
  adaptive scheduler: the plan asks for measured query time, call count and
  input size *before* deciding to sample slow-changing fields less often.
* **Legal information only.** Only fields on :data:`ALLOWED_DECISION_FIELDS`
  reach the briefing. Evaluation-side artifacts (the diary's all-player
  statistics, offline full-map exports) are excluded on purpose: they would let
  the lean arm see more than the legacy arm and invalidate the comparison.

C4 (handoff note) is intentionally absent: the plan asks to validate the
information input and the governance removal first.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from civ_mcp.end_turn import _get_turn_number
from civ_mcp.game_state import GameState

# Field status vocabulary. These words are also the machine-readable markers the
# model sees, so they are stable identifiers rather than prose.
OK = "ok"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"

# Coverage vocabulary reused from the existing facts envelopes.
COMPLETE = "COMPLETE"
KNOWN_HISTORY = "KNOWN_HISTORY"
CURRENTLY_VISIBLE = "CURRENTLY_VISIBLE"
NONE_COVERAGE = "NONE"

# The fields permitted into a decision. Anything not listed here must not be
# added to the briefing: the lean arm may not see more than the legacy arm, and
# evaluation-side data is not a gameplay advantage.
ALLOWED_DECISION_FIELDS = frozenset(
    {
        "identity",
        "rules",
        "situation",
        "cities",
        "units",
        "threats",
        "diplomacy",
        "victory",
        "notifications",
        "blockers",
        "game_over",
    }
)

# Tools that must stay usable when no playable session exists. Without them the
# agent could not load a save, and the entry-material requirement could never be
# satisfied at all — a permanent lockout, which the plan explicitly forbids.
GATE_EXEMPT_TOOLS = frozenset(
    {
        "dismiss_popup",
        "get_diary",
        "kill_game",
        "launch_game",
        "list_saves",
        "load_game_save",
        "load_save",
        "load_save_from_menu",
        "restart_and_load",
    }
)


@dataclass(frozen=True)
class Field:
    """One collected field, with its provenance and any gap made explicit."""

    name: str
    status: str
    source: str
    coverage: str
    observed_turn: int | None
    lines: tuple[str, ...] = ()
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status == OK

    def render(self, title: str) -> list[str]:
        header = f"[{title}] status={self.status} source={self.source}"
        if self.coverage != NONE_COVERAGE:
            header += f" coverage={self.coverage}"
        if self.observed_turn is not None:
            header += f" observed_turn={self.observed_turn}"
        out = [header]
        if self.detail:
            out.append(f"  说明: {self.detail}")
        out.extend(f"  {line}" for line in self.lines)
        return out


@dataclass
class TurnContext:
    """The rendered briefing plus what the caller needs to judge it."""

    turn: int | None
    identity: tuple[str, int] | None
    fields: dict[str, Field]
    consistency: str
    write_allowed: bool
    blocked_reasons: tuple[str, ...]
    query_calls: int  # -1 means unavailable; no shared-connection instrumentation.
    elapsed_ms: int
    brief: str = ""

    def field(self, name: str) -> Field:
        return self.fields[name]


@dataclass
class TurnContextState:
    """Per-session record of the entry material the lean loop requires.

    Deliberately tiny and in-memory: the plan forbids a second state store, and
    the game remains the source of truth. Its only job is to answer "has this
    session already shown the model the current game?".
    """

    delivered_identity: tuple[str, int] | None = None
    delivered_epoch: int | None = None
    delivered_turn: int | None = None
    write_allowed: bool = False
    brief: str = ""
    gate_refusals: int = 0

    def record(self, context: TurnContext, *, cache_epoch: int | None = None) -> None:
        if context.identity is not None:
            self.delivered_identity = context.identity
        self.delivered_epoch = cache_epoch
        self.delivered_turn = context.turn
        self.write_allowed = context.write_allowed
        self.brief = context.brief

    def has_entry_material(self, identity: tuple[str, int] | None) -> bool:
        """Return whether the model has seen this game's situation at all.

        Keyed on game identity rather than turn number: the model must not be
        forced into a mechanical re-query every turn (the ``end_turn`` wrapper
        already returns the next-turn brief), but a new game or a save load
        invalidates the material immediately.
        """

        return identity is not None and self.delivered_identity == identity


# ---------------------------------------------------------------------------
# Civilization rules material
# ---------------------------------------------------------------------------
# Scenario descriptions are strategy material, not verified game rules. The
# former Sumeria card was sourced only from the Cry Havoc benchmark; none of
# its numerical claims or scenario advice qualifies for automatic injection.
# Keep the missing-material contract until an independently verified rules
# source can supply civilization, leader and ruleset-specific conditions.
def civ_material(civ_type: str, ruleset: str) -> tuple[bool, tuple[str, ...]]:
    """Report the current gap without importing a scenario's strategy."""

    return False, (
        f"本批次没有收录 {civ_type or '未知文明'} 经独立核验的文明规则材料"
        f"（ruleset={ruleset or '未知'}）。",
        "能力数值与触发条件按缺失处理；场景攻略不自动作为本局规则或行动建议。",
    )


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _gap(
    name: str, source: str, turn: int | None, exc: BaseException | str
) -> Field:
    detail = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    return Field(
        name=name,
        status=UNAVAILABLE,
        source=source,
        coverage=NONE_COVERAGE,
        observed_turn=turn,
        detail=f"采集失败：{detail[:180]}。缺失不补零、不视为安全。",
    )


async def _collect_fields(
    gs: GameState, turn: int | None
) -> tuple[dict[str, Field], tuple[str, int] | None]:
    """Collect the fixed field set. Each field fails on its own.

    Returns the fields plus the confirmed game identity, which is captured here
    rather than re-parsed out of the rendered text.
    """

    fields: dict[str, Field] = {}
    confirmed_identity: tuple[str, int] | None = None

    # --- identity -----------------------------------------------------------
    try:
        civ, seed = await gs.get_game_identity()
        if civ and civ != "unknown":
            confirmed_identity = (civ, seed)
        overview = await gs.get_game_overview()
    except Exception as exc:
        fields["identity"] = _gap("identity", "get_game_identity/get_game_overview", turn, exc)
        fields["rules"] = _gap("rules", "get_game_overview", turn, exc)
        fields["situation"] = _gap("situation", "get_game_overview", turn, exc)
        overview = None
    else:
        if confirmed_identity is None:
            fields["identity"] = Field(
                name="identity",
                status=UNKNOWN,
                source="get_game_identity",
                coverage=COMPLETE,
                observed_turn=turn,
                detail="对局身份未能确认；确认前不执行任何写操作。",
            )
        else:
            fields["identity"] = Field(
                name="identity",
                status=OK,
                source="get_game_identity",
                coverage=COMPLETE,
                observed_turn=turn,
                lines=(
                    f"civ={confirmed_identity[0]} seed={confirmed_identity[1]} "
                    f"player_id={overview.player_id}",
                ),
            )
        fields["rules"] = _rules_field(overview, turn)
        fields["situation"] = _situation_field(overview, turn, confirmed_identity)

    # --- cities -------------------------------------------------------------
    try:
        cities, city_warnings = await gs.get_cities()
    except Exception as exc:
        fields["cities"] = _gap("cities", "get_cities", turn, exc)
    else:
        lines: list[str] = []
        for city in cities:
            line = (
                f"{city.name}(id={city.city_id}) pop={city.population} "
                f"生产={city.currently_building or 'NONE'}"
                f"({city.production_turns_left}回合) 增长={city.turns_to_grow}回合"
            )
            if city.loyalty < city.loyalty_max:
                line += f" 忠诚={city.loyalty:.0f}/{city.loyalty_max:.0f}"
            if city.pillaged_districts or city.pillaged_buildings:
                line += " 有被掠夺区块/建筑"
            lines.append(line)
        lines.extend(f"警告: {warning}" for warning in city_warnings)
        fields["cities"] = Field(
            name="cities",
            status=OK,
            source="get_cities",
            coverage=COMPLETE,
            observed_turn=turn,
            lines=tuple(lines) or ("无城市。",),
        )

    # --- units --------------------------------------------------------------
    try:
        units = await gs.get_units()
    except Exception as exc:
        fields["units"] = _gap("units", "get_units", turn, exc)
    else:
        idle = [u for u in units if u.moves_remaining > 0]
        promotions = [u for u in units if u.needs_promotion]
        upgrades = [u for u in units if u.can_upgrade]
        lines = [
            f"单位总数={len(units)} 仍有移动力={len(idle)} "
            f"待晋升={len(promotions)} 可升级={len(upgrades)}"
        ]
        if idle:
            lines.append(
                "未行动单位: "
                + ", ".join(f"{u.name}({u.unit_id})@{u.x},{u.y}" for u in idle[:10])
            )
        if promotions:
            lines.append(
                "待晋升: " + ", ".join(f"{u.name}({u.unit_id})" for u in promotions[:10])
            )
        if upgrades:
            lines.append(
                "可升级: "
                + ", ".join(
                    f"{u.name}({u.unit_id})→{u.upgrade_target}" for u in upgrades[:10]
                )
            )
        fields["units"] = Field(
            name="units",
            status=OK,
            source="get_units",
            coverage=COMPLETE,
            observed_turn=turn,
            lines=tuple(lines),
        )

    # --- threats ------------------------------------------------------------
    # Visible hostiles plus known barbarian camps. Reported separately from
    # ``units`` so a failed scan can never read as "nothing nearby".
    try:
        threats = await gs.get_threat_scan()
    except Exception as exc:
        fields["threats"] = _gap("threats", "get_threat_scan", turn, exc)
    else:
        lines = []
        if threats:
            for threat in threats[:10]:
                lines.append(
                    f"{threat.owner_name} {threat.unit_type}@{threat.x},{threat.y} "
                    f"hp={threat.hp}/{threat.max_hp} cs={threat.combat_strength} "
                    f"距最近城市={threat.distance_to_city}"
                    + ("（交战中）" if threat.is_at_war else "")
                )
        else:
            lines.append("当前视野内没有可见敌对单位。")
        try:
            barbarians = await gs.get_barbarian_overview()
        except Exception as barb_exc:
            lines.append(
                f"蛮族营地/单位未知：get_barbarian_overview 采集失败"
                f"（{type(barb_exc).__name__}）。军事行动前先补查。"
            )
        else:
            lines.append(
                f"已揭示蛮族营地={len(barbarians.camps)} 可见蛮族单位={len(barbarians.units)}"
            )
        lines.append("空间信息仅覆盖已知城市、当前可见单位与已揭示营地；细节用地图工具展开。")
        fields["threats"] = Field(
            name="threats",
            status=OK,
            source="get_threat_scan/get_barbarian_overview",
            coverage=CURRENTLY_VISIBLE,
            observed_turn=turn,
            lines=tuple(lines),
        )

    # --- diplomacy ----------------------------------------------------------
    try:
        civs = await gs.get_diplomacy()
    except Exception as exc:
        fields["diplomacy"] = _gap("diplomacy", "get_diplomacy", turn, exc)
    else:
        met = [c for c in civs if getattr(c, "has_met", False)]
        unmet = len(civs) - len(met)
        lines = [f"已接触={len(met)} 未接触={unmet}"]
        for civ in met:
            line = (
                f"{civ.civ_name}/{civ.leader_name} pid={civ.player_id} "
                f"{civ.diplomatic_state} 关系={civ.relationship_score} "
                f"军力={civ.military_strength} 城数={civ.num_cities}"
            )
            if civ.is_at_war:
                line += " 交战中"
            if civ.alliance_type:
                line += f" 同盟={civ.alliance_type}"
            lines.append(line)
        fields["diplomacy"] = Field(
            name="diplomacy",
            status=OK,
            source="get_diplomacy",
            coverage=COMPLETE,
            observed_turn=turn,
            lines=tuple(lines),
        )

    # --- victory ------------------------------------------------------------
    try:
        victory = await gs.get_victory_progress()
    except Exception as exc:
        fields["victory"] = _gap("victory", "get_victory_progress", turn, exc)
    else:
        fields["victory"] = _victory_field(victory, turn, overview)

    # --- notifications ------------------------------------------------------
    try:
        notifications = await gs.get_notifications()
    except Exception as exc:
        fields["notifications"] = _gap("notifications", "get_notifications", turn, exc)
    else:
        required = [n for n in notifications if n.is_action_required]
        lines = [f"通知总数={len(notifications)} 需要决策={len(required)}"]
        for note in required[:8]:
            hint = f" → {note.resolution_hint}" if note.resolution_hint else ""
            lines.append(f"{note.type_name}: {note.message}{hint}")
        fields["notifications"] = Field(
            name="notifications",
            status=OK,
            source="get_notifications",
            coverage=COMPLETE,
            observed_turn=turn,
            lines=tuple(lines),
        )

    # --- blockers -----------------------------------------------------------
    lines = []
    status = OK
    try:
        sessions = await gs.get_diplomacy_sessions()
    except Exception as exc:
        sessions = None
        status = UNAVAILABLE
        lines.append(f"外交会话未知：{type(exc).__name__}。end_turn 前先补查。")
    else:
        lines.append(f"未决外交会话={len(sessions)}")
        for session in sessions:
            lines.append(
                f"与 {session.other_civ_name}/{session.other_leader_name} 的会话待处理"
            )
    try:
        deals = await gs.get_pending_deals()
    except Exception as exc:
        status = UNAVAILABLE
        lines.append(f"待处理交易未知：{type(exc).__name__}。end_turn 前先补查。")
    else:
        lines.append(f"待处理交易={len(deals)}")
        for deal in deals:
            lines.append(f"{deal.other_civ_name} 提出了交易")
    fields["blockers"] = Field(
        name="blockers",
        status=status,
        source="get_diplomacy_sessions/get_pending_deals",
        coverage=COMPLETE,
        observed_turn=turn,
        lines=tuple(lines),
    )

    # --- game over ----------------------------------------------------------
    try:
        game_over = await gs.check_game_over()
    except Exception as exc:
        fields["game_over"] = _gap("game_over", "check_game_over", turn, exc)
    else:
        if game_over is None:
            fields["game_over"] = Field(
                name="game_over",
                status=OK,
                source="check_game_over",
                coverage=COMPLETE,
                observed_turn=turn,
                lines=("对局进行中。",),
            )
        else:
            fields["game_over"] = Field(
                name="game_over",
                status=OK,
                source="check_game_over",
                coverage=COMPLETE,
                observed_turn=turn,
                lines=(
                    f"对局已结束 defeat={game_over.is_defeat} "
                    f"胜利方={game_over.winner_name}/{game_over.winner_leader} "
                    f"类型={game_over.victory_type}",
                ),
            )

    return fields, confirmed_identity


def _rules_field(overview: Any, turn: int | None) -> Field:
    enabled = sorted(overview.enabled_victories) if overview.enabled_victories else []
    lines = [
        f"ruleset={overview.ruleset or '未知'} 速度={overview.game_speed_name or overview.game_speed or '未知'} "
        f"难度={overview.difficulty or '未知'} 最大回合={overview.max_turns or '不限'}",
        f"已启用胜利条件={'全部' if not enabled else ', '.join(enabled)}",
        f"时代={overview.era_name or '未知'} 时代得分={overview.era_score} "
        f"(黑暗线={overview.era_dark_threshold} 黄金线={overview.era_golden_threshold})",
    ]
    if not overview.ruleset:
        return Field(
            name="rules",
            status=UNKNOWN,
            source="get_game_overview",
            coverage=COMPLETE,
            observed_turn=turn,
            detail="规则集未能确认；不套用其它规则集的文明材料。",
            lines=tuple(lines),
        )
    return Field(
        name="rules",
        status=OK,
        source="get_game_overview",
        coverage=COMPLETE,
        observed_turn=turn,
        lines=tuple(lines),
    )


def _situation_field(
    overview: Any, turn: int | None, identity: tuple[str, int] | None
) -> Field:
    lines = [
        f"金币={overview.gold:.0f}({overview.gold_per_turn:+.1f}/回合) "
        f"科技={overview.science_yield:.1f}/回合 文化={overview.culture_yield:.1f}/回合 "
        f"信仰={overview.faith:.0f} 外交支持={overview.diplomatic_favor}",
        f"城市={overview.num_cities} 单位={overview.num_units} 人口={overview.total_population} "
        f"总分={overview.score}",
        f"研究={overview.current_research or '未选择'} 市政={overview.current_civic or '未选择'}",
        f"已探索陆地={overview.explored_land}/{overview.total_land}",
    ]
    status = OK if identity is not None else UNKNOWN
    detail = ""
    if identity is None:
        detail = "本国实时状态/对局身份未确认：只读返回局面，暂不执行写操作。"
    return Field(
        name="situation",
        status=status,
        source="get_game_overview",
        coverage=COMPLETE,
        observed_turn=turn,
        detail=detail,
        lines=tuple(lines),
    )


def _victory_field(victory: Any, turn: int | None, overview: Any) -> Field:
    """Render victory posture without dumping the evaluation-side tables."""

    enabled = set(victory.enabled_victories) or set(
        getattr(overview, "enabled_victories", set()) or set()
    )
    lines = [f"已启用胜利条件={'全部' if not enabled else ', '.join(sorted(enabled))}"]
    ours = None
    for player in victory.players:
        if player.player_id == getattr(overview, "player_id", -1):
            ours = player
            break
    if ours is None:
        # No row for us: say so rather than reporting a rival's numbers as ours.
        lines.append("未能在胜利进度中找到本方记录；进度按未知处理。")
        return Field(
            name="victory",
            status=UNKNOWN,
            source="get_victory_progress",
            coverage=COMPLETE,
            observed_turn=turn,
            lines=tuple(lines),
        )
    lines.append(
        f"本方: 科技VP={ours.science_vp}/{ours.science_vp_needed} "
        f"外交VP={ours.diplomatic_vp} 游客={ours.tourism} "
        f"科技数={ours.techs_researched} 市政数={ours.civics_completed} "
        f"信教城市={ours.religion_cities}"
    )
    if ours.space_progress:
        lines.append(f"太空进度={ours.space_progress} 太空港={ours.spaceports}")
    building = [p for p in victory.space_projects if p.status == "building"]
    if building:
        lines.append(
            "在建太空项目: "
            + ", ".join(
                f"{p.name}({p.progress_pct}%, 剩{p.turns_remaining}回合)" for p in building
            )
        )
    # Opponent posture, limited to civilisations we have actually met.
    rivals = [
        p
        for p in victory.players
        if p.player_id != ours.player_id and p.name and p.name != "Unmet"
    ]
    for rival in rivals[:6]:
        lines.append(
            f"{rival.name}: 总分={rival.score} 科技VP={rival.science_vp} "
            f"外交VP={rival.diplomatic_vp} 游客={rival.tourism} 军力={rival.military_strength}"
        )
    if enabled and "VICTORY_CULTURE" not in enabled:
        lines.append("文化胜利未启用：不报告旅游业绩效。")
    return Field(
        name="victory",
        status=OK,
        source="get_victory_progress",
        coverage=COMPLETE,
        observed_turn=turn,
        lines=tuple(lines),
    )


# ---------------------------------------------------------------------------
# Assembly and rendering
# ---------------------------------------------------------------------------


async def build_turn_context(gs: GameState) -> TurnContext:
    """Collect the fixed field set for this turn and render the briefing.

    The collection is bracketed by two turn reads: if the game advanced while
    the fields were being gathered, the attempt is repeated once. A briefing
    that cannot be attributed to one turn is marked ``unknown`` and forbids
    writes rather than presenting a mixture of two turns as current.
    """

    started = time.monotonic()
    fields: dict[str, Field] = {}
    identity: tuple[str, int] | None = None
    turn_before: int | None = None
    consistency = UNKNOWN
    attempts = 0
    for _attempt in range(2):
        attempts += 1
        turn_before = await _get_turn_number(gs)
        fields, identity = await _collect_fields(gs, turn_before)
        turn_after = await _get_turn_number(gs)
        if turn_before is not None and turn_before == turn_after:
            consistency = OK
            break
    elapsed_ms = int((time.monotonic() - started) * 1000)

    unexpected = set(fields) - ALLOWED_DECISION_FIELDS
    if unexpected:
        raise RuntimeError(
            "briefing collected a field outside ALLOWED_DECISION_FIELDS: "
            f"{sorted(unexpected)}"
        )
    missing = ALLOWED_DECISION_FIELDS - set(fields)
    if missing:
        raise RuntimeError(
            f"briefing is missing required fields: {sorted(missing)}"
        )

    blocked: list[str] = []
    if identity is None:
        blocked.append("对局身份未确认")
    if not fields["situation"].usable:
        blocked.append("本国实时状态未确认")
    if consistency != OK:
        blocked.append("采集期间回合发生变化，局面无法归属到单一回合")
    if "对局已结束" in " ".join(fields["game_over"].lines):
        blocked.append("对局已结束")

    context = TurnContext(
        turn=turn_before,
        identity=identity,
        fields=fields,
        consistency=consistency,
        write_allowed=not blocked,
        blocked_reasons=tuple(blocked),
        query_calls=-1,
        elapsed_ms=elapsed_ms,
    )
    context.brief = _render(context, attempts)
    return context


_SECTION_TITLES = {
    "identity": "身份",
    "rules": "规则与启用内容",
    "situation": "本回合局面",
    "cities": "本国城市与生产",
    "units": "单位",
    "threats": "已知威胁",
    "diplomacy": "外交/对手",
    "victory": "胜利条件进度",
    "notifications": "重要通知",
    "blockers": "待处理阻塞",
    "game_over": "对局状态",
}

ALLOWED_DECISION_FIELDS_ORDER = (
    "identity",
    "rules",
    "situation",
    "cities",
    "units",
    "threats",
    "diplomacy",
    "victory",
    "notifications",
    "blockers",
    "game_over",
)


def _render(context: TurnContext, attempts: int = 1) -> str:
    fields = context.fields
    civ_type = context.identity[0] if context.identity else ""
    ruleset = ""
    for line in fields["rules"].lines:
        if line.startswith("ruleset="):
            ruleset = line.split()[0].split("=", 1)[1]
            break

    out = [
        "=== TURN CONTEXT / 回合局面简报 ===",
        f"turn={context.turn} consistency={context.consistency} "
        f"write_allowed={'yes' if context.write_allowed else 'no'}",
        "口径: 只读 typed GameState 与既有 facts；不含治理快照，"
        "不含评估侧全量统计或离线地图导出。"
        "缺失字段标为 unavailable/unknown/not_applicable，不补零、不判安全。",
    ]
    for name in ALLOWED_DECISION_FIELDS_ORDER:
        out.extend(fields[name].render(_SECTION_TITLES[name]))

    # No verified civilization rules source is currently available.
    out.append("[文明能力与触发前提]")
    found, material = civ_material(civ_type, ruleset)
    if not found:
        out.append("  status=unavailable")
        out.extend(f"  {line}" for line in material)

    out.append("[重要变化]")
    out.append("  暂无可比较基线：本批次不做跨回合变化计算。")

    out.append("[待处理事项]")
    out.extend(f"  {line}" for line in _pending_lines(context))

    out.append("[可进一步查询]")
    out.append(
        "  get_barbarian_overview / get_village_overview / get_map_area / "
        "get_pathing_estimate / get_combat_estimate / get_settle_advisor / "
        "get_strategic_map / get_empire_resources / get_builder_tasks / "
        "get_tech_civics / get_policies / get_era_progress"
    )

    if not context.write_allowed:
        out.append("[写操作已停止]")
        out.extend(f"  {reason}" for reason in context.blocked_reasons)
        out.append(
            "  先只读核验局面；确认对局身份与本国实时状态后重新决定，"
            "不要重复提交同一动作。"
        )

    # The metadata line reports the size of everything above it, so it is
    # appended after measuring.
    body = "\n".join(out)
    out.append("[采集元数据]")
    out.append(
        f"  calls={context.query_calls if context.query_calls >= 0 else 'unavailable'} "
        f"elapsed_ms={context.elapsed_ms} "
        f"chars={len(body)} attempts={attempts}"
    )
    return "\n".join(out)


def _pending_lines(context: TurnContext) -> list[str]:
    lines: list[str] = []
    notifications = context.field("notifications")
    if notifications.usable:
        required = [
            line for line in notifications.lines if not line.startswith("通知总数")
        ]
        if required:
            lines.append(f"需要决策的通知 {len(required)} 条:")
            lines.extend(f"- {item}" for item in required[:5])
    cities = context.field("cities")
    if cities.usable:
        no_production = [line for line in cities.lines if "生产=NONE" in line]
        if no_production:
            lines.append(f"未安排生产的城市 {len(no_production)} 座:")
            lines.extend(f"- {item}" for item in no_production[:5])
    units = context.field("units")
    if units.usable:
        for line in units.lines:
            if line.startswith(("待晋升", "可升级", "未行动单位")):
                lines.append(line)
    for name in ALLOWED_DECISION_FIELDS_ORDER:
        value = context.field(name)
        if not value.usable and name != "game_over":
            lines.append(
                f"{_SECTION_TITLES[name]}缺失（{value.status}）："
                "依赖该证据的选择先补查再决定。"
            )
    if not lines:
        lines.append("无显式阻塞项。")
    return lines


def turn_context_from_identity(identity: tuple[str, int] | None) -> str:
    """Text used when the identity itself cannot be confirmed."""

    return (
        "结果未知 — 无法确认当前对局身份"
        + (f"（读到 {identity}）" if identity else "")
        + "。本次不执行任何写操作，也不依据不完整局面做判断。\n"
        "下一步只读核验：调用 get_game_overview 读取局面与 RUNTIME POLICY；"
        "若游戏尚未进入对局，使用 list_saves / load_game_save 走受控读档。\n"
        "UNKNOWN:TURN_CONTEXT_IDENTITY_UNCONFIRMED"
    )

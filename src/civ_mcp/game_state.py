"""High-level game state API with server-side narration.

Wraps GameConnection + Lua into typed queries, actions, verification and
human-readable results. This layer does not depend on MCP.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from functools import wraps

from civ_mcp.actions import (
    ActionLifecycle, _ActionScope, _ActionEvidence, _PendingAction, _action_result, _extract_pre_hp,
    _extract_post_hp, _extract_city_defense, _format_attack_followup, _scope_probe,
)
from civ_mcp import lua as lq
from civ_mcp.lua import religion_defense
from civ_mcp.connection import GameConnection, LuaError
from civ_mcp.end_turn import TurnProgress
from civ_mcp.narrate import (
    narrate_combat_estimate,
    narrate_move_discoveries,
    narrate_notification_message,
    narrate_settle_candidates,
    narrate_victory_warnings,
)


log = logging.getLogger(__name__)

LOAD_TIMEOUT = 180.0

UNREGISTERED_CHECKPOINT_HINT = (
    "Native game autosaves (AutoSave_*) are never CLI-registered and cannot be loaded or recovered. "
    "Only checkpoints created by save create after native completion in this run are usable; "
    "recovery guidance lists those names."
)


def _unregistered_checkpoint_message(save_name: str, refusal: str) -> str:
    """Explain an unverified checkpoint name instead of only refusing the action."""
    from civ_mcp import checkpoints

    names = checkpoints.registered_names()
    listed = ", ".join(names[-8:]) if names else "none recorded for this run"
    return (
        f"Error: UNREGISTERED_CHECKPOINT|{save_name} has no CLI-registered checkpoint record, so its identity "
        f"cannot be verified; {refusal}. {UNREGISTERED_CHECKPOINT_HINT} "
        f"Registered checkpoints for this run: {listed}."
    )

ACTION_CONFIRM_TIMEOUT = 5.0
ACTION_CONFIRM_INTERVAL = 0.15
VICTORY_REPORT_TIMEOUT = 5.0


def _guard_pending_mutation(method):
    """Keep direct callers behind the same pending gates as MCP.

    The owning entrypoint handles matching before confirmation, so one repeated
    call cannot confirm the old spend and accidentally submit a second spend.
    Popup dismissal and explicit recovery remain separate lifecycle operations.
    """
    @wraps(method)
    async def guarded(self, *args, **kwargs):
        name = method.__name__
        if self.popup_pending:
            return self._popup_unknown
        if name == "send_diplomatic_action":
            action = kwargs.get("action", args[1] if len(args) > 1 else None)
            if isinstance(action, str) and action.upper() == "OPEN_BORDERS":
                name = "propose_trade"  # This alias resumes the same trade owner.
        if self.replacement_pending or self.save_pending:
            return "ERR:OUTCOME_UNKNOWN|Load or save completion is not confirmed; inspect or recover before gameplay."
        if self.diplomacy_pending and name not in {
            "test_trade", "propose_trade", "negotiate_incoming_trade",
            "respond_to_deal", "diplomacy_respond",
        }:
            return "ERR:OUTCOME_UNKNOWN|Native diplomacy attempt is pending; confirm its original request or use explicit recovery."
        if self.turn_pending and name not in {
            "diplomacy_respond", "respond_to_deal", "negotiate_incoming_trade",
            "open_world_congress", "queue_wc_votes",
        }:
            return "ERR:OUTCOME_UNKNOWN|End-turn outcome is pending; confirm end_turn before gameplay."
        if self.congress_pending and name not in {"open_world_congress", "queue_wc_votes"}:
            blocked = await self.congress_gate()
            if blocked:
                return blocked
        # Owners must match the intent before a generic gate can retire it.
        # This applies equally to the original movement/production protocols.
        if self._actions.pending is not None and self._actions.entrypoint != name:
            blocked = await self._action_gate()
            if blocked:
                return blocked
        return await method(self, *args, **kwargs)
    return guarded


class GameState:
    """High-level async API for Civ 6 game state + actions."""

    def __init__(self, connection: GameConnection):
        self.conn = connection
        self._pending_load = False
        self.reset_session()

    def _clear_world_caches(self) -> None:
        self._last_snapshot: lq.TurnSnapshot | None = None
        self._game_identity: tuple[str, int] | None = None
        self._local_player_id = 0
        self._revealed_tiles: set[tuple[int, int]] | None = None

    def reset_session(self) -> None:
        """Retire requests after explicit replacement; keep its verification gate."""
        self._clear_world_caches()
        self._pending_save = False
        self._popup_unknown: str | None = None
        # Replacement survives all cache/identity resets until verified.
        self._actions = ActionLifecycle(self.conn)
        self._turn_progress = TurnProgress()
        from civ_mcp.game_lifecycle import DiplomacyViewCleanup
        self._diplomacy_cleanup = DiplomacyViewCleanup()
        from civ_mcp.diplomacy import DiplomacyProgress
        self._diplomacy_progress = DiplomacyProgress()
        from civ_mcp.congress import CongressProgress
        self._congress_progress = CongressProgress()

    @property
    def congress_pending(self) -> bool:
        return self._congress_progress.pending

    @property
    def popup_pending(self) -> bool:
        """弹窗写入未知独立保留，不覆盖被弹窗暂停的原动作。"""
        return self._popup_unknown is not None

    async def confirm_hotseat_congress_handoff(self) -> str:
        """Confirm the original vote plan's native handoff without voting again."""
        return await self._congress_progress.confirm_hotseat_handoff(self.conn)

    async def congress_gate(self, *, allow_registered_turn: bool = False) -> str | None:
        if not self.congress_pending:
            return None
        result = await self._congress_progress.confirm(self.conn)
        request = self._congress_progress.request
        if request and not (allow_registered_turn and request.state == 'REGISTERED' and not request.invalidated):
            return result
        return None

    @property
    def diplomacy_pending(self) -> bool:
        return self._diplomacy_progress.pending or self._diplomacy_cleanup.pending

    @property
    def turn_pending(self) -> bool:
        """One fact for action/save gates and MCP while advancement is unconfirmed."""
        return self._turn_progress.pending

    def allows_incoming_trade_world(self, native_scope: str) -> bool:
        """Ask the turn owner about a freshly verified native diplomacy scope."""
        fields = native_scope.split("|")
        if len(fields) != 3 or not re.fullmatch(r"CIVILIZATION_[A-Z0-9_]+", fields[1]):
            return False
        if not all(re.fullmatch(r"-?\d+", fields[i]) for i in (0, 2)):
            return False
        player, civ, seed = int(fields[0]), fields[1], int(fields[2])
        if not 0 <= player <= 63:
            return False
        return self._turn_progress.allows_diplomacy_world((civ, seed, player))

    # Existing integrations/tests can still address these names. They never
    # carry a second copy of the request owned by TurnProgress.
    @property
    def _pending_end_turn(self) -> bool:
        return self._turn_progress.pending

    @_pending_end_turn.setter
    def _pending_end_turn(self, pending: bool) -> None:
        self._turn_progress.pending = pending

    @property
    def _pending_end_turn_from(self) -> int | None:
        return self._turn_progress.from_turn

    @_pending_end_turn_from.setter
    def _pending_end_turn_from(self, turn: int | None) -> None:
        self._turn_progress.from_turn = turn

    @property
    def _pending_action(self) -> _PendingAction | None:
        # Compatibility for session replacement and existing test adapters;
        # the action module remains the sole owner of the pending value.
        return self._actions.pending

    @_pending_action.setter
    def _pending_action(self, pending: _PendingAction | None) -> None:
        self._actions.pending = pending

    @property
    def pending_action_summary(self) -> str | None:
        return self._actions.summary

    @property
    def pending_action_entity(self) -> tuple[str, int] | None:
        return self._actions.entity

    @property
    def pending_action_kind(self) -> str | None:
        pending = self._actions.pending
        return pending.kind if pending else None

    @property
    def pending_action_blocks_mutations(self) -> bool:
        return self._actions.blocks_mutations

    @property
    def pending_action_tool(self) -> str | None:
        pending = self._actions.pending
        if pending is None:
            return None
        if pending.evidence is not None:
            return pending.evidence.tool
        entrypoint = self._actions.entrypoint
        if entrypoint == "spy_escape":
            return "spy_action"
        if pending.entity[0] == "unit":
            return "unit_action"
        return entrypoint

    def _pending_unknown(self, reason: str = "confirmation deadline reached") -> str:
        return self._actions.unknown(reason)

    async def confirm_pending_action(self, *, wait: bool = True) -> str:
        return await self._actions.confirm(wait=wait, timeout=ACTION_CONFIRM_TIMEOUT,
                                           interval=ACTION_CONFIRM_INTERVAL)

    async def _submit_action(self, pending, scope, code) -> str:
        return await self._actions.submit(pending, scope, code,
                                           timeout=ACTION_CONFIRM_TIMEOUT,
                                           interval=ACTION_CONFIRM_INTERVAL)

    async def _action_gate(self, entity: tuple[str, int] | None = None) -> str | None:
        if self.popup_pending:
            return self._popup_unknown
        blocked = await self.congress_gate()
        if blocked:
            return blocked
        if self._pending_load or self._pending_save:
            return "ERR:OUTCOME_UNKNOWN|Load or save completion is not confirmed; inspect or recover before gameplay."
        if self._diplomacy_progress.pending:
            return self._diplomacy_progress.trade_summary()
        if self._diplomacy_cleanup.pending:
            return "ERR:OUTCOME_UNKNOWN|Native diplomacy cleanup is unconfirmed; dismiss_popup only confirms it."
        if self.turn_pending:
            return "ERR:OUTCOME_UNKNOWN|End-turn outcome is pending; confirm end_turn before gameplay."
        if entity is not None and entity[0] == "unit":
            entity = (entity[0], entity[1] % 65536)
        pending = self._pending_action
        if pending is not None and (entity is None or entity == self.pending_action_entity
                                    or self.pending_action_blocks_mutations
                                    or pending.kind in ("pillage", "contribution")):
            await self.confirm_pending_action()
            if self._pending_action is not None:
                return self._pending_unknown("dependent action was not submitted")
        return None

    async def get_game_identity(self) -> tuple[str, int]:
        """Return (civ_type_lower, random_seed) for the current game.

        Always queries the game so we detect new-game loads.  When the
        identity changes, observation caches expire without retiring requests.
        """
        code = _scope_probe("GAME_SCOPE", game_identity=True) + f'print("{lq.SENTINEL}")'
        lines = await self.conn.execute_write(code)
        scopes = [line.split("|") for line in lines if line.startswith("GAME_SCOPE|")]
        if scopes:
            if len(scopes) != 1 or len(scopes[0]) != 5:
                raise ValueError("missing or ambiguous native scope")
            self._actions.observe_scope(*scopes[0][1:])
        rows = [line.split("|") for line in lines if line.startswith("GAMESEED|")]
        if not rows:
            return ("unknown", 0)
        if len(rows) != 1 or len(rows[0]) != 3:
            raise ValueError("missing or ambiguous native identity")
        new_id = self._actions.observe_identity(*rows[0][1:])
        if self._game_identity is not None and new_id != self._game_identity:
            log.info("Game changed: %s → %s", self._game_identity, new_id)
            self._clear_world_caches()
            self._diplomacy_cleanup.invalidate_scope()
            self._diplomacy_progress.invalidate_scope()
            self._congress_progress.invalidate_scope()
        self._turn_progress.invalidate_scope(new_id)
        self._game_identity = new_id
        return self._game_identity

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    async def get_game_overview(self, *, require_snapshot: bool = False) -> lq.GameOverview:
        await self._diplomacy_progress.ensure_observer(self.conn)
        # InGame context needed for GetFavor() (nil in GameCore)
        lines = await self.conn.execute_write(lq.build_overview_query())
        ov = lq.parse_overview_response(lines)
        self._local_player_id = ov.player_id
        if require_snapshot:
            # Load confirmation requires this attempt's complete evidence,
            # including failures that could retire the FireTuner stream.
            self._last_snapshot = await self._take_snapshot(ov, require_complete=True)
        # Bootstrap: capture baseline snapshot for first end_turn diff
        elif self._last_snapshot is None:
            try:
                self._last_snapshot = await self._take_snapshot(ov)
            except Exception:
                log.debug("Failed to bootstrap snapshot", exc_info=True)
        return ov



    async def check_game_over(self) -> lq.GameOverStatus | None:
        """Check if the game has ended (victory/defeat screen showing).

        Reads the same native outcome tuple in either context. Falls back to
        read-only GameCore when the InGame context is unavailable.
        """
        try:
            lines = await self.conn.execute_write(lq.build_gameover_check())
            return lq.parse_gameover_response(lines)
        except Exception:
            log.debug("Game-over check failed in InGame, trying GameCore")
        # Fallback: GameCore-only check (survives defeat screen)
        try:
            lines = await self.conn.execute_read(lq.build_gameover_check_gamecore())
            return lq.parse_gameover_response(lines)
        except Exception:
            log.debug("Game-over check failed in GameCore too", exc_info=True)
            return None

    async def get_units(
        self, *, on_confirmation: Callable[[str], None] | None = None,
    ) -> list[lq.UnitInfo]:
        """Read entities; optionally deliver this query's resolved action receipt."""
        if self._pending_action:
            confirmation = await self.confirm_pending_action(wait=False)
            # The receipt belongs only to this query, not a replayable cache.
            # An unresolved observation must not be presented as completion.
            if self._pending_action is None and on_confirmation is not None:
                on_confirmation(confirmation)
        lines = await self.conn.execute_write(lq.build_units_query())
        return lq.parse_units_response(lines)

    async def get_builder_tasks(
        self,
    ) -> lq.BuilderTaskReport:
        lines = await self.conn.execute_write(lq.build_builder_tasks_query())
        return lq.parse_builder_tasks(lines)

    async def get_spies(self) -> list[lq.SpyInfo]:
        lines = await self.conn.execute_write(lq.build_get_spies_query())
        if "SPY_ESCAPE_QUERY_UNKNOWN" in lines:
            raise LuaError("Current spy escape identity or legal routes are unknown; no route was selected.")
        if "SPY_TRAVEL_QUERY_UNKNOWN" in lines:
            raise LuaError("Own off-map spy roster is unknown; travelling spies could not be listed.")
        return lq.parse_spies_response(lines)

    async def _assign_spy(self, unit_id: int, mission: str, x: int, y: int, entrypoint: str) -> str:
        from civ_mcp.lua import spy_assignments
        mission = mission.upper() if isinstance(mission, str) else mission
        entity, intent = ("unit", unit_id), (mission, x, y)
        if self._actions.matches("spy_assignment", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        if (any(type(v) is not int or v < 0 for v in (unit_id, x, y))
                or not isinstance(mission, str) or mission not in spy_assignments.OPERATIONS):
            return "Error: INVALID_SPY_ASSIGNMENT|Expected unit ID, target coordinates and a supported mission"
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "spy_assignment", entity, spy_assignments.build_spy_assignment_query(unit_id, mission, x, y, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            intent=intent, evidence=_ActionEvidence(
                tool="spy_action", entrypoint=entrypoint, accepted=("SPY_ASSIGNMENT_REQUESTED|", "SPY_ASSIGNMENT_NOOP|"),
                refused="ERR:SPY_ASSIGNMENT_REFUSED|",
                confirm=lambda rows: spy_assignments.parse_spy_assignment(rows, unit_id, mission, token),
                guidance="Repeat the original spy assignment to confirm its start only. ",
            ),
        )
        return await self._submit_action(pending, scope, spy_assignments.build_spy_assignment(unit_id, mission, x, y, token))

    @_guard_pending_mutation
    async def spy_travel(self, unit_index: int, target_x: int, target_y: int) -> str:
        return await self._assign_spy(unit_index, "TRAVEL", target_x, target_y, "spy_travel")

    @_guard_pending_mutation
    async def spy_mission(
        self, unit_index: int, mission_type: str, target_x: int, target_y: int
    ) -> str:
        return await self._assign_spy(unit_index, mission_type, target_x, target_y, "spy_mission")

    @_guard_pending_mutation
    async def spy_escape(self, unit_id: int, district_type: str) -> str:
        if type(unit_id) is not int or unit_id < 0:
            raise ValueError("A full native spy identity is required")
        route = district_type.strip().upper()
        token = uuid.uuid4().hex
        code = lq.build_spy_escape_route(unit_id, route, witness_token=token)
        if self._actions.matches("spy_escape", ("unit", unit_id), item=route):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        pending = _PendingAction(
            "spy_escape", ("unit", unit_id), lq.build_verify_spy_escape(unit_id, route, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"item": route, "witness_token": token, "full_id": unit_id},
        )
        return await self._submit_action(pending, scope, code)

    async def get_threat_scan(self) -> list[lq.ThreatInfo]:
        lines = await self.conn.execute_read(lq.build_threat_scan_query())
        return lq.parse_threat_scan_response(lines)

    async def get_decision_context(self, include=(), city_ids=(), view="brief") -> dict:
        from civ_mcp.decision import read_context
        return await read_context(self, include, city_ids, view)

    async def get_city_yields(self, city_id=None, view="brief") -> dict:
        from civ_mcp.city_yields import read_context
        return await read_context(self, city_id, view)

    async def get_pathing_estimate(
        self, unit_index: int, target_x: int, target_y: int
    ) -> lq.PathingEstimate:
        lines = await self.conn.execute_write(
            lq.build_pathing_estimate_query(unit_index, target_x, target_y)
        )
        return lq.parse_pathing_estimate(lines)

    async def get_victory_progress(self) -> lq.VictoryProgress:
        lines = await self.conn.execute_write(lq.build_victory_progress_query())
        return lq.parse_victory_progress_response(lines)

    async def get_cities(
        self, *, on_confirmation: Callable[[str], None] | None = None,
    ) -> tuple[list[lq.CityInfo], list[str]]:
        """Read entities; optionally deliver this query's resolved action receipt."""
        if self._pending_action:
            confirmation = await self.confirm_pending_action(wait=False)
            if self._pending_action is None and on_confirmation is not None:
                on_confirmation(confirmation)
        lines = await self.conn.execute_write(lq.build_cities_query())
        return lq.parse_cities_response(lines)

    async def get_map_area(
        self, center_x: int, center_y: int, radius: int = 2
    ) -> list[lq.TileInfo]:
        lines = await self.conn.execute_read(
            lq.build_map_area_query(center_x, center_y, radius)
        )
        return lq.parse_map_response(lines)

    async def get_strategic_map(self) -> lq.StrategicMapData:
        lines = await self.conn.execute_read(lq.build_strategic_map_query())
        return lq.parse_strategic_map_response(lines)

    async def get_diplomacy(self) -> list[lq.CivInfo]:
        # Uses InGame context for GetDiplomaticAI access
        lines = await self.conn.execute_write(lq.build_diplomacy_query())
        return lq.parse_diplomacy_response(lines)

    async def get_tech_civics(self) -> lq.TechCivicStatus:
        # "write" selects InGame; this query is read-only. Native civic progress
        # and GetTurnsLeft/GetTurnsToProgressCivic are unavailable in GameCore.
        lines = await self.conn.execute_write(lq.build_tech_civics_query())
        return lq.parse_tech_civics_response(lines)

    async def get_city_growth(self, city_id=None, view="brief") -> dict:
        from civ_mcp.city_growth import read_context
        return await read_context(self, city_id, view)

    async def get_city_workforce(self, city_id, view="brief") -> dict:
        from civ_mcp.city_workforce import read_context
        return await read_context(self,city_id,view)

    async def get_public_report(self, section: str, **options) -> dict:
        """Read UI-equivalent facts without changing views or retiring pending."""
        from civ_mcp.lua import public_reports
        try:
            source = public_reports.build(section, **options)
        except ValueError as error:
            return {"status": "error", "data": None, "error": str(error)}
        try:
            rows = await self.conn.execute_in_named_state("InGame", source)
            return public_reports.parse(rows)
        except (LuaError, OSError, TimeoutError, ValueError) as error:
            return {"status": "unknown", "data": None, "error": str(error)}

    async def get_empire_resources(
        self,
    ) -> tuple[
        list[lq.ResourceStockpile],
        list[lq.OwnedResource],
        list[lq.NearbyResource],
        dict[str, int],
        bool | None,
    ]:
        # InGame context needed for GetResourceStockpileCap etc.
        lines = await self.conn.execute_write(lq.build_empire_resources_query())
        markers=[r for r in lines if r.startswith('RESOURCE_SCAN|')]
        known={'RESOURCE_SCAN|true|true':True,'RESOURCE_SCAN|true|false':False}.get(markers[0]) if len(markers)==1 else None
        return (*lq.parse_empire_resources_response(lines),known)

    # ------------------------------------------------------------------
    # Action methods (run in InGame context for UnitManager access)
    # ------------------------------------------------------------------

    @_guard_pending_mutation
    async def move_unit(self, unit_index: int, target_x: int, target_y: int) -> str:
        details = {"target": (target_x, target_y), "witness_token": uuid.uuid4().hex}
        if self._actions.matches("move", ("unit", unit_index), target=details["target"]):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        try:
            await self.dismiss_popup()
        except (OSError, TimeoutError, LuaError):
            pass
        # 清理发生在入口检查之后，也可能留下结果未知的写入。
        if self.popup_pending:
            return self._popup_unknown
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        pending = _PendingAction(
            "move", ("unit", unit_index),
            lq.build_unit_position_query(
                unit_index, move_target_x=target_x, move_target_y=target_y,
                witness_token=details["witness_token"],
            ),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner, details=details,
            success=f"MOVING_TO|{target_x},{target_y}",
        )
        result = await self._submit_action(pending, scope, lq.build_move_unit(
            unit_index, target_x, target_y, witness_token=details["witness_token"],
        ))
        # 保留探索反馈；集合只用于区分本次新揭示的格子。
        if self._pending_action is None and "|BLOCKED" not in result and self._revealed_tiles is not None:
            try:
                position = re.search(r"now_at:(\d+),(\d+)", result)
                if position:
                    x, y = map(int, position.groups())
                    lines = await self.conn.execute_read(lq.build_post_move_visibility_query(x, y))
                    tiles = lq.parse_post_move_visibility(lines)
                    newly_revealed = {(tx, ty) for tx, ty, _ in tiles} - self._revealed_tiles
                    self._revealed_tiles.update((tx, ty) for tx, ty, _ in tiles)
                    if newly_revealed:
                        data = [(tx, ty, meta) for tx, ty, meta in tiles if (tx, ty) in newly_revealed]
                        result += "\n" + narrate_move_discoveries(data, len(newly_revealed))
            except (OSError, ValueError):
                log.debug("Post-move visibility unavailable", exc_info=True)
        return result

    @_guard_pending_mutation
    async def rebase_unit(self, unit_id: int, target_x: int, target_y: int) -> str:
        from civ_mcp.lua import rebase

        if any(type(value) is not int or value < 0 for value in (unit_id, target_x, target_y)):
            return "Error: INVALID_REBASE_INTENT|Expected the original unit ID and nonnegative integer base coordinates."
        entity, intent = ("unit", unit_id), (target_x, target_y)
        if self._actions.matches("rebase", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "rebase", entity, rebase.build_rebase_query(unit_id, target_x, target_y, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"target": intent, "witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool="unit_action", entrypoint="rebase_unit", accepted=("REBASE_REQUESTED|",),
                refused="ERR:REBASE_REFUSED|",
                confirm=lambda rows: rebase.parse_rebase_confirmation(rows, unit_id, target_x, target_y, token),
                guidance="Repeat the original unit rebase and destination to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, rebase.build_rebase(unit_id, target_x, target_y, token))

    async def get_combat_preview(self, unit_id: int, target_x: int, target_y: int) -> str:
        """Read native current combat simulation without confirming or submitting."""
        import json
        from civ_mcp.snapshot import pending_facts
        from civ_mcp.lua.combat_preview import parse_binding, parse_refusal

        result = {"status": "unknown", "source": "InGame CombatManager native simulation",
                  "strength_source": "Native COMBAT_STRENGTH + STRENGTH_MODIFIER; no base-unit fallback",
                  "damage_source": "Native DAMAGE_TO for each side",
                  "eligibility_source": "Runtime current-turn preflight from native movement, ZOC, path and CanStartOperation if reached; refusal alone is not an engine rule",
                  "request": {"unit_id": unit_id, "target_x": target_x, "target_y": target_y},
                  "eligibility": "unknown", "estimate": None,
                  "limitations": "Current native estimate only; does not guarantee actual damage, survival, or next-turn state. No attack submitted."}
        def response(reason):
            return json.dumps(result | {"reason": reason}, ensure_ascii=False, allow_nan=False)
        pending = pending_facts(self)
        if any(pending.values()):
            return response("Pending operation exists; confirm its original request first. Preview did not read, confirm or clear it.")
        if any(type(v) is not int or v < 0 for v in (unit_id, target_x, target_y)):
            return response("Expected original full unit ID and nonnegative integer target coordinates.")
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return response(scope)
        # Recheck after the scope read: this reader never retires an owner.
        if any(pending_facts(self).values()):
            return response("Pending operation appeared during the scope read; preview not queried.")
        token = uuid.uuid4().hex
        try:
            rows = await self.conn.execute_write(lq.build_combat_estimate_query(
                unit_id, target_x, target_y,
                preview_scope=(scope.civilization, scope.seed, scope.turn, scope.owner), preview_token=token))
            rows = [line for row in rows for line in row.splitlines()]
            if any(pending_facts(self).values()):
                return response("Pending operation appeared during preview; estimate discarded without confirmation.")
            refused = [row for row in rows if row.startswith("COMBAT_PREVIEW_INELIGIBLE|")]
            binding = parse_binding(rows, token, unit_id, target_x, target_y, scope.owner)
            estimates = [row for row in rows if row.startswith("NATIVE_ESTIMATE|")]
            # A receipt is exactly binding+estimate OR binding+refusal. Never
            # accept a positive result while ignoring conflicting extra rows.
            if binding is None or len(rows) != 2 or (len(estimates), len(refused)) not in {(1, 0), (0, 1)}:
                reasons = [row for row in rows if row.startswith(("ESTIMATE_UNKNOWN|", "ERR:"))]
                return response("; ".join(reasons) or "Native preview receipts conflict or binding is unavailable or ambiguous.")
            if refused:
                reason = parse_refusal(refused[0], token)
                if reason is None:
                    return response("Native refusal identity or reason is unavailable or malformed.")
                result.update(binding, eligibility="current_turn_preflight_refused",
                              scope={"civilization": scope.civilization, "seed": scope.seed, "turn": scope.turn, "owner": scope.owner})
                return response(reason)
            estimate = lq.parse_combat_estimate(estimates, 0, 0, allow_fractional=True)
            if estimate is None or (estimate.attacker_hp, estimate.defender_hp) != (binding["attacker"]["hp"], binding["target"]["hp"]):
                return response("Native estimate or bound actor/target HP is unavailable or inconsistent.")
            if estimate.is_ranged != (binding["mode"] in {"RANGED", "BOMBARD"}):
                return response("Native mode and estimate disagree.")
            binding["attacker"].update(type=estimate.attacker_type, hp=estimate.attacker_hp, strength=estimate.attacker_cs)
            binding["target"].update(type=estimate.defender_type, hp=estimate.defender_hp, strength=estimate.defender_cs)
            result.update(binding, status="estimated", eligibility="current_turn_preflight_passed",
                          scope={"civilization": scope.civilization, "seed": scope.seed, "turn": scope.turn, "owner": scope.owner},
                          damage_to_attacker=estimate.est_damage_to_attacker, damage_to_target=estimate.est_damage_to_defender)
            result.pop("estimate")
            return json.dumps(result, ensure_ascii=False, allow_nan=False)
        except (OSError, TimeoutError, LuaError, ValueError):
            return response("Native combat preview unavailable; no substitute strengths or damage calculated.")

    @_guard_pending_mutation
    async def attack_unit(self, unit_index: int, target_x: int, target_y: int) -> str:
        return await self._attack_and_confirm(("unit", unit_index), target_x, target_y)

    @_guard_pending_mutation
    async def city_attack(self, city_id: int, target_x: int, target_y: int) -> str:
        return await self._attack_and_confirm(("city", city_id), target_x, target_y)

    async def _attack_and_confirm(self, entity: tuple[str, int], target_x: int, target_y: int) -> str:
        if self._actions.matches("attack", entity, target=(target_x, target_y)):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        estimate = None
        estimate_str = ""
        unknown_estimate = "Combat estimate unknown (native preview unavailable; conditional modifiers uncomputed).\n"
        if entity[0] == "unit":
            estimate_str = unknown_estimate
            try:
                await self.dismiss_popup()
            except (OSError, TimeoutError, LuaError):
                pass
            if self.popup_pending:
                return self._popup_unknown
            try:
                lines = await self.conn.execute_write(lq.build_combat_estimate_query(entity[1], target_x, target_y))
                estimate = lq.parse_combat_estimate(lines, 0, 0)
                if estimate:
                    estimate_str = narrate_combat_estimate(estimate) + "\n"
            except (OSError, TimeoutError, LuaError, ValueError):
                log.debug("Combat estimate unavailable", exc_info=True)
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        city_token = uuid.uuid4().hex if entity[0] == "city" else None
        siege_token = uuid.uuid4().hex if entity[0] == "unit" else None
        pending = _PendingAction(
            "attack", entity,
            lq.build_city_attack_followup(entity[1], target_x, target_y, city_token) if city_token else
            lq.build_attack_followup_query(target_x, target_y, entity[1], siege_token=siege_token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"target": (target_x, target_y), "city_witness_token": city_token,
                     "siege_witness_token": siege_token},
            success=f"CITY_RANGE_ATTACK|city:{entity[1]} -> {target_x},{target_y}" if city_token else "",
        )
        build = lq.build_attack_unit if entity[0] == "unit" else lq.build_city_attack
        code = (build(entity[1], target_x, target_y, witness_token=city_token) if city_token else
                build(entity[1], target_x, target_y, siege_token=siege_token))
        result = await self._submit_action(pending, scope, code)
        if result.startswith(("RANGE_ATTACK", "MELEE_ATTACK", "AIR_ATTACK", "CITY_RANGE_ATTACK", "UNIT_CITY_ATTACK_CONFIRMED")):
            if estimate and (result.startswith("AIR_ATTACK") or estimate.is_ranged != result.startswith("RANGE_ATTACK")):
                estimate_str = unknown_estimate
            return estimate_str + result
        if "OUTCOME_UNKNOWN" in result and pending.success and estimate_str:
            return result + "\n" + estimate_str
        return result

    @_guard_pending_mutation
    async def resolve_city_capture(self, city_id: int, action: str) -> str:
        from civ_mcp.lua import city_decisions
        entity, intent = ("city", city_id), (action,)
        if self._actions.matches("city_decision", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        if type(city_id) is not int or city_id < 0 or not isinstance(action, str) or action not in city_decisions.DIRECTIVES:
            return "Error: INVALID_CITY_DECISION|Expected the pending city ID and a supported decision"
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "city_decision", entity, city_decisions.build_city_decision_query(city_id, action, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            intent=intent, evidence=_ActionEvidence(
                tool="resolve_city_capture", entrypoint="resolve_city_capture",
                accepted=("CITY_DECISION_REQUESTED|",), refused="ERR:CITY_DECISION_REFUSED|",
                confirm=lambda rows: city_decisions.parse_city_decision(rows, city_id, action, token),
                guidance="Repeat the same decision for the original city to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, city_decisions.build_city_decision(city_id, action, token))

    @_guard_pending_mutation
    async def found_city(self, unit_index: int) -> str:
        if self._actions.matches("found", ("unit", unit_index)):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        try:
            await self.dismiss_popup()
        except (OSError, TimeoutError, LuaError):
            pass
        if self.popup_pending:
            return self._popup_unknown
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        pending = _PendingAction(
            "found", ("unit", unit_index), lq.build_unit_position_query(unit_index),
            identity=scope.identity, turn=scope.turn, owner=scope.owner,
        )
        result = await self._submit_action(pending, scope, lq.build_found_city(unit_index))
        if result.startswith("Error: CANNOT_FOUND"):
            try:
                result += "\n\n" + await self.get_settle_advisor(unit_index)
            except (OSError, TimeoutError, LuaError, ValueError):
                log.debug("Settle advisor failed", exc_info=True)
        return result

    async def get_settle_advisor(self, unit_index: int) -> str:
        lua = lq.build_settle_advisor_query(unit_index)
        lines = await self.conn.execute_write(lua)
        candidates = lq.parse_settle_advisor_response(lines)
        if candidates:
            return narrate_settle_candidates(candidates)
        for line in lines:
            if line.startswith("ERR:"):
                return line
        return "No local settlement terrain candidates confirmed within 5 tiles; use map settle-global for a separate revealed-map scan."

    async def get_global_settle_scan(self, **region) -> list[lq.SettleCandidate]:
        lua = lq.build_global_settle_scan(**region)
        lines = await self.conn.execute_write(lua)
        for line in lines:
            if line.startswith('ERR:'):
                raise ValueError(line)
        candidates=lq.parse_settle_advisor_response(lines)
        totals=[line.split('|')[1] for line in lines if line.startswith('SETTLE_TOTAL|')]
        if len(totals)!=1 or not totals[0].isdigit() or int(totals[0])!=len(candidates):
            raise ValueError('Native settlement scan incomplete; candidate total unknown')
        return candidates

    @_guard_pending_mutation
    async def fortify_unit(self, unit_index: int) -> str:
        return await self._set_unit_readiness(unit_index, "fortify")

    @_guard_pending_mutation
    async def skip_unit(self, unit_index: int) -> str:
        return await self._set_unit_readiness(unit_index, "skip")

    @_guard_pending_mutation
    async def cancel_unit_move(self, unit_index: int) -> str:
        if self._actions.matches("cancel_move", ("unit", unit_index)):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "cancel_move", ("unit", unit_index), lq.build_cancel_unit_move_query(unit_index, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"witness_token": token},
        )
        return await self._submit_action(pending, scope, lq.build_cancel_unit_move(unit_index, token))

    @_guard_pending_mutation
    async def pillage_unit(self, unit_id: int) -> str:
        if self._actions.matches("pillage", ("unit", unit_id)):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        lines = await self.conn.execute_write(lq.build_pillage_preflight(unit_id))
        rows = [row.split("|") for row in lines if row.startswith("PILLAGE_PREPARED|")]
        if len(rows) != 1 or len(rows[0]) != 5:
            return _action_result(lines)
        _, full_id, x, y, fingerprint = rows[0]
        if (not all(re.fullmatch(r"\d+", value) for value in (full_id, x, y))
                or not re.fullmatch(r"[-\d,]+(?:;(?:improvement|district|building):\d+:(?:true|false))+", fingerprint)):
            return "Error: PILLAGE_TARGET_UNKNOWN|No action submitted."
        token = uuid.uuid4().hex
        position = (int(x), int(y))
        pending = _PendingAction(
            "pillage", ("unit", unit_id), lq.build_pillage_query(int(full_id), token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"witness_token": token, "full_id": int(full_id),
                     "target": position, "fingerprint": fingerprint},
        )
        return await self._submit_action(pending, scope, lq.build_pillage_unit(
            int(full_id), *position, fingerprint, token))

    async def _set_unit_readiness(
        self, unit_index: int, kind: str, *, scope: _ActionScope | None = None,
    ) -> str:
        if self._actions.matches(kind, ("unit", unit_index)):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        if scope is None:
            scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            kind, ("unit", unit_index), lq.build_unit_readiness_query(unit_index, kind, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"witness_token": token},
        )
        build = lq.build_skip_unit if kind == "skip" else lq.build_fortify_unit
        return await self._submit_action(pending, scope, build(unit_index, token))

    @_guard_pending_mutation
    async def skip_remaining_units(self) -> str:
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        try:
            lines = await self.conn.execute_write(scope.guard(lq.build_skip_remaining_units()))
        except (OSError, TimeoutError, LuaError) as exc:
            return f"Error: READINESS_UNKNOWN|Candidate query failed: {exc}; no units submitted"
        result = _action_result(lines)
        if result != "READY_UNITS":
            return result
        try:
            candidates = [int(row.split("|", 1)[1]) for row in lines if row.startswith("READY_UNIT|")]
            if len(candidates) != len(set(candidates)) or any(uid < 0 for uid in candidates):
                raise ValueError("invalid candidate IDs")
        except ValueError:
            return "Error: READINESS_UNKNOWN|Malformed candidate list; no units submitted"
        results = []
        for unit_id in sorted(candidates):
            result = await self._set_unit_readiness(unit_id, "skip", scope=scope)
            results.append(f"unit:{unit_id}|{result}")
            if self._pending_action is not None or not result.startswith(("SKIPPED", "NOT_READY", "ALREADY_WAITING")):
                return "SKIP_BATCH_STOPPED|" + "\n".join(results)
        return f"SKIPPED|{len(candidates)} candidates checked" + ("\n" + "\n".join(results) if results else "")

    async def _set_unit_task(self, unit_id: int, kind: str, entrypoint: str) -> str:
        from civ_mcp.lua import unit_tasks
        entity, intent = ("unit", unit_id), (kind,)
        if self._actions.matches("unit_task", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        if type(unit_id) is not int or unit_id < 0:
            return "Error: INVALID_UNIT_ID|Expected a nonnegative integer"
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "unit_task", entity, unit_tasks.build_task_query(unit_id, kind, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            intent=intent, evidence=_ActionEvidence(
                tool="unit_action", entrypoint=entrypoint, accepted=("UNIT_TASK_REQUESTED|", "UNIT_TASK_NOOP|"),
                refused="ERR:UNIT_TASK_REFUSED|",
                confirm=lambda rows: unit_tasks.parse_task_confirmation(rows, unit_id, kind, token),
                guidance=f"Repeat {kind} for the original unit to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, unit_tasks.build_task(unit_id, kind, token))

    @_guard_pending_mutation
    async def automate_explore(self, unit_index: int) -> str:
        return await self._set_unit_task(unit_index, "automate", "automate_explore")

    @_guard_pending_mutation
    async def heal_unit(self, unit_index: int) -> str:
        return await self._set_unit_task(unit_index, "heal", "heal_unit")

    @_guard_pending_mutation
    async def alert_unit(self, unit_index: int) -> str:
        return await self._set_unit_task(unit_index, "alert", "alert_unit")

    @_guard_pending_mutation
    async def sleep_unit(self, unit_index: int) -> str:
        return await self._set_unit_task(unit_index, "sleep", "sleep_unit")

    @_guard_pending_mutation
    async def delete_unit(self, unit_index: int) -> str:
        from civ_mcp.lua import unit_transforms as transforms
        entity, intent = ("unit", unit_index), ()
        if self._actions.matches("delete_unit", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        if type(unit_index) is not int or unit_index < 0:
            return "Error: INVALID_UNIT_ID|Expected a nonnegative integer"
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "delete_unit", entity, transforms.build_delete_query(unit_index, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            intent=intent, evidence=_ActionEvidence(
                tool="unit_action", entrypoint="delete_unit", accepted=("UNIT_DELETE_REQUESTED|",),
                refused="ERR:UNIT_DELETE_REFUSED|",
                confirm=lambda rows: transforms.parse_confirmation(rows, token, unit_index, "DELETED"),
                guidance="Repeat delete for the original unit to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, transforms.build_delete(unit_index, token))

    async def get_unit_formations(self, unit_id: int) -> dict:
        from civ_mcp.lua import unit_formations
        try:
            source = unit_formations.build_options(unit_id)
        except ValueError as error:
            return {"status": "error", "data": None, "error": str(error)}
        try:
            return unit_formations.parse_options(await self.conn.execute_write(source))
        except (LuaError, OSError, TimeoutError, ValueError) as error:
            return {"status": "unknown", "data": None, "error": str(error)}

    @_guard_pending_mutation
    async def change_unit_formation(self, unit_id: int, action: str, target_unit_id: int | None = None) -> str:
        from civ_mcp.lua import unit_formations
        entity, intent = ("unit", unit_id), (action, target_unit_id)
        if self._actions.matches("unit_formation", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        token = uuid.uuid4().hex
        try:
            source = unit_formations.build_submit(unit_id, action, target_unit_id, token)
            query = unit_formations.build_confirmation(unit_id, action, target_unit_id, token)
        except ValueError as error:
            return f"Error: INVALID_FORMATION_ARGUMENT|{error}"
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        pending = _PendingAction(
            "unit_formation", entity, query, context="write", identity=scope.identity,
            turn=scope.turn, owner=scope.owner, intent=intent,
            evidence=_ActionEvidence(
                tool="set_unit_formation", entrypoint="change_unit_formation",
                accepted=("UNIT_FORMATION_REQUESTED|",), refused="ERR:UNIT_FORMATION_REFUSED|",
                confirm=lambda rows: unit_formations.parse_confirmation(rows, unit_id, action, token),
                guidance="Repeat set_unit_formation with the original actor, action and target to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, source)

    async def _change_unit_tile(self, unit_id: int, kind: str, entrypoint: str, improvement: str = "") -> str:
        from civ_mcp.lua import tile_actions
        entity, intent = ("unit", unit_id), (kind, improvement)
        if self._actions.matches("tile_action", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        if type(unit_id) is not int or unit_id < 0 or not isinstance(improvement, str):
            return "Error: INVALID_TILE_ACTION|Expected a unit ID and improvement name"
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "tile_action", entity, tile_actions.build_tile_action_query(unit_id, kind, improvement, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            intent=intent, evidence=_ActionEvidence(
                tool="unit_action", entrypoint=entrypoint, accepted=("TILE_ACTION_REQUESTED|", "TILE_ACTION_NOOP|"),
                refused="ERR:TILE_ACTION_REFUSED|",
                confirm=lambda rows: tile_actions.parse_tile_action(rows, unit_id, kind, token),
                guidance=f"Repeat {kind} with the original unit and parameters to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, tile_actions.build_tile_action(unit_id, kind, improvement, token))

    @_guard_pending_mutation
    async def improve_tile(self, unit_index: int, improvement_name: str) -> str:
        return await self._change_unit_tile(unit_index, "improve", "improve_tile", improvement_name)

    @_guard_pending_mutation
    async def remove_feature(self, unit_index: int) -> str:
        return await self._change_unit_tile(unit_index, "remove_feature", "remove_feature")

    @_guard_pending_mutation
    async def repair_improvement(self, unit_index: int) -> str:
        return await self._change_unit_tile(unit_index, "repair", "repair_improvement")

    @_guard_pending_mutation
    async def remove_improvement(self, unit_index: int) -> str:
        return await self._change_unit_tile(unit_index, "remove_improvement", "remove_improvement")

    @_guard_pending_mutation
    async def sacrifice_builder_charges(self, unit_id: int) -> str:
        if type(unit_id) is not int or unit_id < 0:
            raise ValueError("A full native builder identity is required")
        if self._actions.matches("contribution", ("unit", unit_id)):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        lines = await self.conn.execute_write(lq.build_contribution_preflight(unit_id))
        rows = [row.split("|") for row in lines if row.startswith("CONTRIBUTION_PREPARED|")]
        if len(rows) != 1 or len(rows[0]) != 2:
            return _action_result(lines)
        fingerprint = rows[0][1]
        if not re.fullmatch(r"[-\d.,]+", fingerprint):
            return "Error: CONTRIBUTION_TARGET_UNKNOWN|No command submitted."
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "contribution", ("unit", unit_id), lq.build_contribution_query(unit_id, fingerprint, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"witness_token": token, "fingerprint": fingerprint},
        )
        return await self._submit_action(pending, scope, lq.build_sacrifice_builder_charges(unit_id, fingerprint, token))

    @_guard_pending_mutation
    async def build_route(self, unit_index: int) -> str:
        return await self._change_unit_tile(unit_index, "build_route", "build_route")

    @_guard_pending_mutation
    async def set_city_production(
        self,
        city_id: int,
        item_type: str,
        item_name: str,
        target_x: int | None = None,
        target_y: int | None = None,
    ) -> str:
        target = ((target_x, target_y) if item_type.upper() == "DISTRICT"
                  and target_x is not None and target_y is not None else None)
        if self._actions.matches("production", ("city", city_id), item=item_name, target=target):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        pending = _PendingAction(
            "production", ("city", city_id), lq.build_verify_production(city_id, item_name, target),
            # "write" selects InGame; this confirmation Lua only reads facts.
            context="write" if target is not None else "read",
            identity=scope.identity, turn=scope.turn, owner=scope.owner, success=f"PRODUCING|{item_name}",
            details={"item": item_name, "target": target},
        )
        return await self._submit_action(pending, scope, lq.build_produce_item(
            city_id, item_type, item_name, target_x, target_y,
        ))

    @_guard_pending_mutation
    async def purchase_item(
        self,
        city_id: int,
        item_type: str,
        item_name: str,
        yield_type: str = "YIELD_GOLD",
    ) -> str:
        if (type(city_id) is not int or city_id < 0 or not isinstance(item_type, str)
                or not isinstance(item_name, str) or not isinstance(yield_type, str)):
            return "Error: INVALID_PURCHASE|Expected a full city ID and UNIT/BUILDING item with GOLD/FAITH currency."
        item_type, item_name, yield_type = item_type.upper(), item_name.upper(), yield_type.upper()
        if (item_type not in {"UNIT", "BUILDING"} or yield_type not in {"YIELD_GOLD", "YIELD_FAITH"}
                or not re.fullmatch(r"[A-Z][A-Z0-9_]*", item_name)):
            return "Error: INVALID_PURCHASE|Only UNIT/BUILDING and YIELD_GOLD/YIELD_FAITH are supported."
        entity, intent = ("city", city_id), (item_type, item_name, yield_type)
        if self._actions.matches("purchase_item", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "purchase_item", entity, lq.build_item_purchase_query(city_id, item_type, item_name, yield_type, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"item": item_name, "currency": yield_type, "witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool="purchase_item", entrypoint="purchase_item", accepted=("ITEM_PURCHASE_REQUESTED|",),
                refused="ERR:ITEM_PURCHASE_REFUSED|",
                confirm=lambda rows: lq.parse_item_purchase_confirmation(rows, token, city_id, item_name, yield_type),
                guidance="Repeat purchase_item with the original city, product and currency to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, lq.build_purchase_item(city_id, item_type, item_name, yield_type, token))

    async def list_city_production(self, city_id: int, item_type: str | None = None,
                                    item_name: str | None = None) -> list[lq.ProductionOption]:
        if (type(city_id) is not int or city_id < 0 or (item_type is None) != (item_name is None)
                or (item_type is not None and (item_type not in {'UNIT', 'BUILDING'}
                    or not isinstance(item_name, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', item_name)))):
            raise ValueError('Provide a full city ID; item_type UNIT/BUILDING and item_name must be paired.')
        lua = lq.build_city_production_query(city_id, item_type, item_name)
        # Must use InGame context — bq:CanProduce() throws "Not Implemented" in GameCore
        lines = await self.conn.execute_write(lua)
        return lq.parse_city_production_response(lines)

    @_guard_pending_mutation
    async def set_research(self, tech_name: str) -> str:
        return await self._player_choice("set_research", tech_name)

    @_guard_pending_mutation
    async def set_civic(self, civic_name: str) -> str:
        return await self._player_choice("set_civic", civic_name)

    async def _player_choice(self, operation: str, target: str | int) -> str:
        intent = (target,)
        entity = self.pending_action_entity
        if entity is not None and self._actions.matches(operation, entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            operation, ("player", scope.owner), lq.build_player_choice_confirmation(operation, target, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"target": target}, intent=intent,
            evidence=_ActionEvidence(
                tool="set_research" if operation == "set_civic" else operation, entrypoint=operation,
                accepted=("CHOICE_REQUESTED|",), refused="ERR:CHOICE_REFUSED|",
                confirm=lambda rows: lq.parse_player_choice_confirmation(rows, operation, target, token),
                guidance=f"Repeat {operation} with the original target to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, lq.build_player_choice(operation, target, token))

    # ------------------------------------------------------------------
    # Diplomacy methods
    # ------------------------------------------------------------------

    async def _read_diplomacy_context(self) -> lq.DiplomacyContext:
        """先读公共上下文；多个会话时在原生领袖界面核对当前对象。"""
        lua = lq.build_diplomacy_session_query()
        lines = await self.conn.execute_write(lua)
        if "CONTEXT_COMPLETE" not in lines:
            raise LuaError("Diplomacy session status is unknown")
        context = lq.parse_diplomacy_sessions(lines)
        execute = getattr(self.conn, "execute_in_named_state", None)
        if context.status == "UNKNOWN" and context.reason == "MULTIPLE_SESSIONS" and execute is not None:
            try:
                rows = await execute("DiplomacyActionView", lua)
                if "CONTEXT_COMPLETE" in rows:
                    return lq.parse_diplomacy_sessions(rows)
            except (OSError, TimeoutError, LuaError):
                # 原生证据缺失时保留 UNKNOWN，不猜测当前玩家。
                pass
        return context

    async def get_diplomacy_sessions(self) -> lq.DiplomacyContext:
        # 在没有会话时也开始观察，避免首次会面语句先于观察器到达。
        await self._diplomacy_progress.ensure_observer(self.conn)
        context = await self._read_diplomacy_context()
        for session in context.sessions:
            keys, _ = await self._diplomacy_progress.choices(self.conn, session.session_id, session.other_player_id)
            session.choice_status = "KNOWN" if keys is not None else "UNKNOWN"
            session.choices = [lq.DiplomacyChoice(key=key, text=key) for key in keys or []]
        await self._diplomacy_progress.describe_incoming(self.conn, context)
        return context

    @_guard_pending_mutation
    async def diplomacy_respond(self, other_player_id: int, response: str) -> str:
        sessions = await self.get_diplomacy_sessions()
        if self._diplomacy_progress.response is not None:
            return await self._diplomacy_progress.confirm_response(self.conn)
        candidates = [item for item in sessions.sessions if item.other_player_id == other_player_id]
        if sessions.status != "DIALOGUE" or len(candidates) != 1:
            return "ERR:OUTCOME_UNKNOWN|Current dialogue is not verified; no response submitted."
        return await self._diplomacy_progress.respond(self.conn, candidates[0].session_id, other_player_id, response)

    @_guard_pending_mutation
    async def send_diplomatic_action(self, other_player_id: int, action: str) -> str:
        if action.upper() == "OPEN_BORDERS":
            # Session-based OPEN_BORDERS causes AI turn hang.
            # Route through the trade deal API instead (mutual open borders).
            return await self.propose_trade(
                other_player_id,
                offer_items=[{"type": "AGREEMENT", "subtype": "OPEN_BORDERS"}],
                request_items=[{"type": "AGREEMENT", "subtype": "OPEN_BORDERS"}],
            )
        return await self._relationship_action("send_diplomatic_action", other_player_id, action.upper())

    async def _relationship_action(self, entrypoint: str, target: int, action: str) -> str:
        from civ_mcp.lua import relationships
        if not relationships.supported(action):
            return "Error: UNSUPPORTED_DIPLOMATIC_ACTION|" + action
        intent = (target, action)
        entity = ("player", target)
        if self._actions.matches("relationship", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "relationship", entity, relationships.build_confirmation(target, action, token, scope),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            intent=intent, details={"target": target, "action": action},
            evidence=_ActionEvidence(
                tool=entrypoint, entrypoint=entrypoint, accepted=("RELATIONSHIP_REQUESTED|",),
                refused="ERR:RELATIONSHIP_REFUSED|",
                confirm=lambda rows: relationships.parse_confirmation(rows, token),
                guidance=f"Repeat {entrypoint} with the original request to confirm only; get_diplomacy remains readable. ",
            ),
        )
        return await self._submit_action(pending, scope, relationships.build_request(target, action, token))

    # ------------------------------------------------------------------
    # Trade deal methods (InGame context)
    # ------------------------------------------------------------------

    async def get_deal_options(self, other_player_id: int) -> lq.DealOptions:
        lua = lq.build_deal_options_query(other_player_id)
        lines = await self.conn.execute_write(lua)
        return lq.parse_deal_options_response(lines)

    async def get_pending_deals(self) -> lq.DiplomacyContext:
        context = await self._read_diplomacy_context()
        context.outgoing_summary = await self._diplomacy_progress.outgoing_report(self.conn)
        await self._diplomacy_progress.describe_incoming(self.conn, context)
        return context

    @_guard_pending_mutation
    async def respond_to_deal(self, other_player_id: int, accept: bool) -> str:
        owned = await self._diplomacy_progress.respond_counter(self, other_player_id, accept)
        if owned is not None:
            return owned
        return await self._diplomacy_progress.respond_incoming(self, other_player_id, accept)

    @_guard_pending_mutation
    async def negotiate_incoming_trade(self, other_player_id: int, expected_fingerprint: str, *, our_gold: int | None = None, their_gold: int | None = None, our_gpt: int | None = None, their_gpt: int | None = None, mode: str = "inspect") -> str:
        edits = {key: value for key, value in {"our_gold": our_gold, "their_gold": their_gold, "our_gpt": our_gpt, "their_gpt": their_gpt}.items() if value is not None}
        return await self._diplomacy_progress.negotiate_incoming(self, other_player_id, expected_fingerprint, edits, mode)

    @_guard_pending_mutation
    async def propose_trade(self, other_player_id: int, offer_items: list[dict], request_items: list[dict]) -> str:
        return await self._diplomacy_progress.run_trade(self, other_player_id, offer_items, request_items, preview=False)

    @_guard_pending_mutation
    async def test_trade(self, other_player_id: int, offer_items: list[dict], request_items: list[dict]) -> str:
        return await self._diplomacy_progress.run_trade(self, other_player_id, offer_items, request_items, preview=True)

    @_guard_pending_mutation
    async def propose_peace(self, other_player_id: int) -> str:
        return await self._relationship_action("propose_peace", other_player_id, "MAKE_PEACE")

    @_guard_pending_mutation
    async def form_alliance(self, other_player_id: int, alliance_type: str) -> str:
        return await self._relationship_action("form_alliance", other_player_id, "ALLIANCE_" + alliance_type.upper())

    # ------------------------------------------------------------------
    # Policy methods (InGame context)
    # ------------------------------------------------------------------

    async def get_policies(self, policy_type: str | None = None) -> lq.GovernmentStatus:
        lua = lq.build_policies_query(policy_type)
        lines = await self.conn.execute_write(lua)
        for line in lines:
            if line.startswith("ERR:"):
                raise ValueError(line)
        return lq.parse_policies_response(lines)

    @_guard_pending_mutation
    async def set_policies(self, assignments: dict[int, str]) -> str:
        intent = tuple(sorted((slot, "NONE" if policy.upper() == "NONE" else policy)
                              for slot, policy in assignments.items()))
        entity = self.pending_action_entity
        if entity is not None and self._actions.matches("set_policies", entity, intent=intent):
            pending = self._actions.pending
            result = await self.confirm_pending_action(wait=False)
            return await self._continue_policy_plan(pending, result)
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "set_policies", ("player", scope.owner), lq.build_policy_confirmation(token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"assignments": intent, "witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool="set_policies", entrypoint="set_policies", accepted=("POLICY_PLAN_REQUESTED|",),
                refused="ERR:POLICY_PLAN_REFUSED|",
                confirm=lambda rows: lq.parse_policy_confirmation(rows, token),
                guidance="Repeat set_policies with the original assignments to confirm or continue its unstarted stage. ",
            ),
        )
        # A paid plan first submits only UNLOCK_POLICIES. Its native receipt
        # retains the complete plan even if this acknowledgement is lost.
        result = await self._submit_action(pending, scope, lq.build_policy_plan(dict(intent), token))
        return await self._continue_policy_plan(pending, result)

    async def _continue_policy_plan(self, pending: _PendingAction, result: str) -> str:
        if self._actions.pending is not pending or pending.details.get("scope_invalid"):
            return result
        token = pending.details["witness_token"]
        if not lq.policy_plan_ready(pending.observed, token):
            return result
        # Only the explicit owner may advance this paid, never-entered stage.
        # Ordinary confirmation and every other mutation gate remain read-only.
        try:
            await self.conn.execute_write(lq.build_policy_continuation(token))
        except (OSError, TimeoutError, LuaError) as exc:
            return self._pending_unknown(f"policy continuation acknowledgement unavailable: {exc}")
        return await self.confirm_pending_action()

    # ------------------------------------------------------------------
    # Governor methods (InGame context)
    # ------------------------------------------------------------------

    async def get_governors(self, include_ineligible: bool = False) -> lq.GovernorStatus:
        lua = lq.build_governors_query(include_ineligible)
        lines = await self.conn.execute_write(lua)
        return lq.parse_governors_response(lines)

    @_guard_pending_mutation
    async def appoint_governor(self, governor_type: str) -> str:
        return await self._governor_action("appoint_governor", governor_type)

    async def _governor_action(self, operation: str, governor_type: str,
                               *, promotion_type: str | None = None, city_id: int | None = None,
                               target_owner: int | None = None) -> str:
        if operation == "assign_governor":
            if type(city_id) is not int or city_id < 0:
                return "Error: INVALID_CITY_ID|Expected a nonnegative full city ID"
            if target_owner is not None and (type(target_owner) is not int or not 0 <= target_owner <= 63):
                return "Error: INVALID_TARGET_OWNER|Expected a player ID from 0 to 63"
            if target_owner is None and self._actions.pending is not None:
                target_owner = self._actions.pending.owner
        intent = (governor_type, promotion_type, city_id, target_owner)
        entity = self.pending_action_entity
        if entity is not None and self._actions.matches(operation, entity):
            if self._actions.matches(operation, entity, intent=intent):
                return await self.confirm_pending_action()
            if operation == "assign_governor":
                return self._pending_unknown("governor target changed; confirm the original request before a new assignment")
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        if operation == "assign_governor" and target_owner is None:
            target_owner = scope.owner
            intent = (governor_type, promotion_type, city_id, target_owner)
        token = uuid.uuid4().hex
        options = {"promotion_type": promotion_type, "city_id": city_id, "target_owner": target_owner}
        pending = _PendingAction(
            operation, ("player", scope.owner),
            lq.build_governor_confirmation(operation, governor_type, token, **options),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"governor": governor_type, "promotion": promotion_type, "city": city_id,
                     "target_owner": target_owner}, intent=intent,
            evidence=_ActionEvidence(
                tool=operation, entrypoint=operation, accepted=("GOVERNOR_REQUESTED|",),
                refused="ERR:GOVERNOR_REFUSED|",
                confirm=lambda rows: lq.parse_governor_confirmation(rows, operation, governor_type, token),
                guidance=f"Repeat {operation} with the original governor and target to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope,
            lq.build_governor_action(operation, governor_type, token, **options))

    @_guard_pending_mutation
    async def assign_governor(self, governor_type: str, city_id: int, target_owner: int | None = None) -> str:
        return await self._governor_action("assign_governor", governor_type, city_id=city_id,
                                         target_owner=target_owner)

    @_guard_pending_mutation
    async def promote_governor(self, governor_type: str, promotion_type: str) -> str:
        return await self._governor_action("promote_governor", governor_type, promotion_type=promotion_type)

    # ------------------------------------------------------------------
    # Promotion methods
    # ------------------------------------------------------------------

    async def get_unit_promotions(self, unit_id: int) -> lq.UnitPromotionStatus:
        from civ_mcp.lua import unit_promotions
        lua = lq.build_unit_promotions_query(unit_id)
        try:
            lines = await self.conn.execute_in_named_state("InGame", lua)
            return lq.parse_unit_promotions_response(lines, unit_id)
        except (LuaError, OSError, TimeoutError, ValueError) as error:
            return unit_promotions.unavailable(unit_id, str(error))

    @_guard_pending_mutation
    async def promote_unit(self, unit_id: int, promotion_type: str) -> str:
        from civ_mcp.lua import unit_transforms as transforms
        entity, intent = ("unit", unit_id), (promotion_type,)
        if self._actions.matches("promote_unit", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        if type(unit_id) is not int or unit_id < 0 or not isinstance(promotion_type, str):
            return "Error: INVALID_PROMOTION_INTENT|Expected a unit ID and promotion type"
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "promote_unit", entity, transforms.build_promote_query(unit_id, promotion_type, token),
            context="write", submit_context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            intent=intent, evidence=_ActionEvidence(
                tool="promote_unit", entrypoint="promote_unit", accepted=("UNIT_PROMOTION_REQUESTED|",),
                refused="ERR:UNIT_PROMOTION_REFUSED|",
                confirm=lambda rows: transforms.parse_confirmation(rows, token, unit_id, "PROMOTED"),
                guidance="Repeat promote_unit with the original promotion to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, transforms.build_promote(unit_id, promotion_type, token))

    # ------------------------------------------------------------------
    # City-state / Envoy methods (InGame context)
    # ------------------------------------------------------------------

    @_guard_pending_mutation
    async def close_diplomacy_session(self, other_player_id: int) -> str:
        """由调用方明确取消指定会话，不推断或代选对话回应。"""
        from civ_mcp.lua import diplomacy_close

        if type(other_player_id) is not int or not 0 <= other_player_id <= 62:
            return "Error: INVALID_PLAYER_ID|Expected a verified diplomacy participant."
        entity = ("player", other_player_id)
        if self._actions.matches("close_diplomacy_session", entity):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "close_diplomacy_session", entity, diplomacy_close.build_query(other_player_id, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"witness_token": token}, intent=(),
            evidence=_ActionEvidence(
                tool="close_diplomacy_session", entrypoint="close_diplomacy_session",
                accepted=("DIPLOMACY_CLOSE_REQUESTED|",), refused="ERR:DIPLOMACY_CLOSE_REFUSED|",
                confirm=lambda rows: diplomacy_close.parse_confirmation(rows, other_player_id, token),
                guidance="Repeat close_diplomacy_session for the same player to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, diplomacy_close.build_close(other_player_id, token))

    async def get_city_states(self) -> lq.EnvoyStatus:
        # UI influence caches can stay stale after a confirmed envoy. Bind the
        # engine observation to the local UI identity instead of returning it.
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            raise ValueError(scope)
        ui_lines = await self.conn.execute_write(lq.build_city_states_query())
        ui_status = lq.parse_city_states_response(ui_lines)
        eligibility = {city.player_id: city.can_send_envoy for city in ui_status.city_states}
        lines = await self.conn.execute_read(lq.build_city_states_observation(
            scope.owner, scope.civilization, scope.seed, scope.turn, eligibility))
        if any(line.startswith("ERR:") for line in lines) or sum(line.startswith("TOKENS|") for line in lines) != 1:
            raise ValueError("City-state engine observation unavailable: " + "; ".join(lines))
        after = await self._actions.prepare_scope(self._game_identity)
        if after != scope:
            raise ValueError("City-state local world/player/turn changed during observation")
        return lq.parse_city_states_response(lines)

    @_guard_pending_mutation
    async def send_envoy(self, city_state_player_id: int) -> str:
        if type(city_state_player_id) is not int or not 0 <= city_state_player_id <= 62:
            return "Error: INVALID_CITY_STATE|Expected a city-state player ID from get_city_states."
        entity = ("player", city_state_player_id)
        if self._actions.matches("envoy", entity):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        try:
            baseline_rows = await self.conn.execute_read(lq.build_envoy_preparation(
                scope.owner, city_state_player_id, scope.civilization, scope.seed, scope.turn, token))
            baseline = lq.parse_envoy_preparation(baseline_rows, token, scope.owner, city_state_player_id)
        except (OSError, TimeoutError, LuaError, ValueError) as error:
            return f"Error: ENVOY_REFUSED|{error}; no envoy request submitted."
        pending = _PendingAction(
            "envoy", entity, lq.build_envoy_query(city_state_player_id, token),
            gamecore_query=lq.build_envoy_core_query(city_state_player_id, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"witness_token": token}, intent=(),
            evidence=_ActionEvidence(
                tool="send_envoy", entrypoint="send_envoy", accepted=("ENVOY_REQUESTED|",), refused="ERR:ENVOY_REFUSED|",
                confirm=lambda rows: lq.parse_envoy_confirmation(rows, token, scope.owner, city_state_player_id, baseline),
                guidance="Repeat send_envoy for the same player ID to confirm only; use get_city_states to inspect. ",
            ),
        )
        return await self._submit_action(pending, scope, lq.build_send_envoy(city_state_player_id, token, *baseline))

    # ------------------------------------------------------------------
    # Pantheon methods (InGame context)
    # ------------------------------------------------------------------

    async def get_pantheon_status(self) -> lq.PantheonStatus:
        lua = lq.build_pantheon_status_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_pantheon_status_response(lines)

    @_guard_pending_mutation
    async def choose_pantheon(self, belief_type: str) -> str:
        kind, intent = "choose_pantheon", (belief_type,)
        entity = self.pending_action_entity
        if entity is not None and self._actions.matches(kind, entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            kind, ("player", scope.owner), lq.build_religion_confirmation(token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"belief": belief_type, "witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool=kind, entrypoint=kind, accepted=("RELIGION_PLAN_REQUESTED|",), refused="ERR:RELIGION_PLAN_REFUSED|",
                confirm=lambda rows: lq.parse_religion_confirmation(rows, kind, token),
                guidance="Repeat the original pantheon choice to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, lq.build_pantheon_plan(belief_type, token))

    # ------------------------------------------------------------------
    # Religion founding methods (InGame context)
    # ------------------------------------------------------------------

    async def get_religion_founding_status(self) -> lq.ReligionFoundingStatus:
        lua = lq.build_religion_beliefs_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_religion_beliefs_response(lines)

    @_guard_pending_mutation
    async def found_religion(
        self, religion_type: str, follower_belief: str, founder_belief: str
    ) -> str:
        kind, intent = "found_religion", (religion_type, follower_belief, founder_belief)
        entity = self.pending_action_entity
        if entity is not None and self._actions.matches(kind, entity, intent=intent):
            pending = self._actions.pending
            result = await self.confirm_pending_action(wait=False)
            return await self._continue_religion_plan(pending, result)
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            kind, ("player", scope.owner), lq.build_religion_confirmation(token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"religion": intent, "witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool=kind, entrypoint=kind, accepted=("RELIGION_PLAN_REQUESTED|",), refused="ERR:RELIGION_PLAN_REFUSED|",
                confirm=lambda rows: lq.parse_religion_confirmation(rows, kind, token),
                guidance="Repeat found_religion with the original religion and both beliefs to confirm or continue only its unstarted stage. ",
            ),
        )
        result = await self._submit_action(pending, scope, lq.build_religion_plan(*intent, token))
        return await self._continue_religion_plan(pending, result)

    async def _continue_religion_plan(self, pending: _PendingAction, result: str) -> str:
        # Two ADD_BELIEF stages can follow a confirmed FOUND_RELIGION. Queries
        # and unrelated mutation gates never call this explicit-owner continuation.
        for _ in range(2):
            if self._actions.pending is not pending or pending.details.get("scope_invalid"):
                return result
            token = pending.details["witness_token"]
            if not lq.religion_plan_ready(pending.observed, token):
                return result
            try:
                await self.conn.execute_write(lq.build_religion_continuation(token))
            except (OSError, TimeoutError, LuaError) as exc:
                return self._pending_unknown(f"religion continuation acknowledgement unavailable: {exc}")
            result = await self.confirm_pending_action()
        return result

    # ------------------------------------------------------------------
    # Unit upgrade methods (InGame context)
    # ------------------------------------------------------------------

    async def check_unit_upgrade(self, unit_id: int) -> str:
        unit_index = unit_id % 65536
        lua = lq.build_unit_upgrade_query(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    @_guard_pending_mutation
    async def upgrade_unit(self, unit_id: int) -> str:
        from civ_mcp.lua import unit_transforms as transforms
        entity, intent = ("unit", unit_id), ()
        if self._actions.matches("upgrade_unit", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        if type(unit_id) is not int or unit_id < 0:
            return "Error: INVALID_UNIT_ID|Expected a nonnegative integer"
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "upgrade_unit", entity, transforms.build_upgrade_query(unit_id, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            intent=intent, evidence=_ActionEvidence(
                tool="upgrade_unit", entrypoint="upgrade_unit", accepted=("UNIT_UPGRADE_REQUESTED|",),
                refused="ERR:UNIT_UPGRADE_REFUSED|",
                confirm=lambda rows: transforms.parse_confirmation(rows, token, unit_id, "UPGRADED"),
                guidance="Repeat upgrade_unit with the original unit to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, transforms.build_upgrade(unit_id, token))

    # ------------------------------------------------------------------
    # Dedications / Commemorations
    # ------------------------------------------------------------------

    async def get_dedications(self) -> lq.DedicationStatus:
        lua = lq.build_dedications_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_dedications_response(lines)

    @_guard_pending_mutation
    async def choose_dedication(self, dedication_index: int) -> str:
        return await self._player_choice("choose_dedication", dedication_index)

    # ------------------------------------------------------------------
    # District / wonder placement queries
    # ------------------------------------------------------------------

    async def get_district_advisor(
        self, city_id: int, district_type: str
    ) -> list[lq.DistrictPlacement] | str:
        """Returns placements list, or an error string if placement is impossible."""
        lua = lq.build_district_advisor_query(city_id, district_type)
        lines = await self.conn.execute_write(lua)
        # Check for error bail lines (parser only looks for DPLOT| and silently
        # discards errors, losing the actual reason for failure)
        for line in lines:
            if line.startswith("ERR:"):
                return line  # propagate the specific error to the agent
        return lq.parse_district_advisor_response(lines)

    async def get_wonder_advisor(
        self, city_id: int, wonder_name: str
    ) -> list[lq.WonderPlacement] | str:
        """Returns placements list, or an error string if placement is impossible."""
        lua = lq.build_wonder_advisor_query(city_id, wonder_name)
        lines = await self.conn.execute_write(lua)
        for line in lines:
            if line.startswith("ERR:"):
                return line
        return lq.parse_wonder_advisor_response(lines)

    # ------------------------------------------------------------------
    # Tile purchase methods (InGame context)
    # ------------------------------------------------------------------

    async def get_purchasable_tiles(self, city_id: int) -> list[lq.PurchasableTile] | str:
        lua = lq.build_purchasable_tiles_query(city_id)
        lines = await self.conn.execute_write(lua)
        for line in lines:
            if line.startswith("ERR:"):
                return line
        return lq.parse_purchasable_tiles_response(lines)

    @_guard_pending_mutation
    async def purchase_tile(self, city_id: int, x: int, y: int) -> str:
        if any(type(value) is not int or value < 0 for value in (city_id, x, y)):
            return "Error: INVALID_TILE_PURCHASE|Expected a full city ID and nonnegative integer coordinates."
        entity, intent = ("city", city_id), (x, y)
        if self._actions.matches("purchase_tile", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "purchase_tile", entity, lq.build_tile_purchase_query(city_id, x, y, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"target": (x, y), "witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool="purchase_tile", entrypoint="purchase_tile", accepted=("TILE_PURCHASE_REQUESTED|",), refused="ERR:TILE_PURCHASE_REFUSED|",
                confirm=lambda rows: lq.parse_tile_purchase_confirmation(rows, token, city_id, x, y),
                guidance="Repeat purchase_tile with the original city and coordinates to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, lq.build_purchase_tile(city_id, x, y, token))

    # ------------------------------------------------------------------
    # Government change (InGame context)
    # ------------------------------------------------------------------

    @_guard_pending_mutation
    async def change_government(self, government_type: str) -> str:
        return await self._player_choice("change_government", government_type)

    # ------------------------------------------------------------------
    # Great People (InGame context)
    # ------------------------------------------------------------------

    async def get_great_people(self) -> list[lq.GreatPersonInfo]:
        lua = lq.build_great_people_query()
        lines = await self.conn.execute_write(lua)
        for line in lines:
            if line.startswith("ERR:"):
                raise ValueError(line)
        return lq.parse_great_people_response(lines)

    async def get_great_people_report(self, individual_id=None, class_type=None):
        from civ_mcp.lua.great_people import build_great_people_query, parse_great_people_report
        lines=await self.conn.execute_write(build_great_people_query(individual_id,class_type))
        for line in lines:
            if line.startswith('ERR:'):raise ValueError(line)
        return parse_great_people_report(lines)

    async def get_gp_advisor(self, unit_index: int) -> lq.GPAdvisorResult | str | None:
        lua = lq.build_gp_advisor_query(unit_index)
        lines = await self.conn.execute_write(lua)
        for line in lines:
            if line.startswith("ERR:"):
                return line
        return lq.parse_gp_advisor_response(lines)

    @_guard_pending_mutation
    async def recruit_great_person(self, individual_id: int) -> str:
        return await self._great_person_pool_action("recruit_great_person", individual_id)

    @_guard_pending_mutation
    async def patronize_great_person(
        self, individual_id: int, yield_type: str = "YIELD_GOLD"
    ) -> str:
        return await self._great_person_pool_action("patronize_great_person", individual_id, yield_type)

    async def _great_person_pool_action(self, kind: str, individual: int, currency: str = "GP_POINTS") -> str:
        if type(individual) is not int or individual < 0:
            return "Error: INVALID_GREAT_PERSON|Expected a nonnegative individual ID."
        if kind == "patronize_great_person" and currency not in {"YIELD_GOLD", "YIELD_FAITH"}:
            return "Error: INVALID_CURRENCY|Expected YIELD_GOLD or YIELD_FAITH."
        entity, intent = ("great_person", individual), (currency,)
        if self._actions.matches(kind, entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            kind, entity, lq.build_great_person_pool_query(kind, individual, currency, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"currency": currency, "witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool=kind, entrypoint=kind, accepted=("GP_POOL_REQUESTED|",), refused="ERR:GP_POOL_REFUSED|",
                confirm=lambda rows: lq.parse_great_person_pool_confirmation(rows, token, kind, individual, currency),
                guidance="Repeat the original Great Person action, individual and currency to confirm only. ",
            ),
        )
        return await self._submit_action(pending, scope, lq.build_great_person_pool_action(kind, individual, currency, token))

    async def get_religion_defense(self) -> religion_defense.ReligionDefense:
        """Read current scoped facts without changing any pending owner or UI."""
        report = religion_defense.ReligionDefense()
        for context, execute, query in (
            ("InGame", self.conn.execute_write, religion_defense.build_query),
            ("GameCore", self.conn.execute_read, religion_defense.build_core_query),
            ("InGame:final", self.conn.execute_write, religion_defense.build_final_scope_query),
        ):
            try:
                report.add(await execute(query()))
            except (LuaError, OSError, asyncio.IncompleteReadError) as exc:
                report.unavailable.append(f"{context}: {type(exc).__name__}")
        return report

    async def get_religion_status(self) -> lq.ReligionStatus:
        lines = await self.conn.execute_write(lq.build_religion_status_query())
        return lq.parse_religion_status_response(lines)

    @_guard_pending_mutation
    async def reject_great_person(self, individual_id: int) -> str:
        return await self._great_person_pool_action("reject_great_person", individual_id)

    # ------------------------------------------------------------------
    # Trade route methods (InGame context)
    # ------------------------------------------------------------------

    async def get_trade_routes(self) -> lq.TradeRouteStatus:
        lua = lq.build_trade_routes_query()
        lines = await self.conn.execute_write(
            lua
        )  # InGame context (GetOutgoingRoutes is InGame-only)
        return lq.parse_trade_routes_response(lines)

    async def get_trade_destinations(
        self, unit_index: int
    ) -> list[lq.TradeDestination]:
        lua = lq.build_trade_destinations_query(unit_index)
        lines = await self.conn.execute_write(lua)
        return lq.parse_trade_destinations_response(lines)

    @_guard_pending_mutation
    async def make_trade_route(
        self, unit_index: int, target_x: int, target_y: int
    ) -> str:
        return await self._trader_action("trade_route", "make_trade_route", unit_index, target_x, target_y)

    # ------------------------------------------------------------------
    # Great Person activation (InGame context)
    # ------------------------------------------------------------------

    @_guard_pending_mutation
    async def activate_great_person(self, unit_index: int) -> str:
        return await self._unit_effect("activate_great_person", unit_index)

    async def _unit_effect(self, kind: str, unit_id: int, target_x: int | None = None, target_y: int | None = None) -> str:
        if type(unit_id) is not int or unit_id < 0:
            return "Error: INVALID_UNIT_ID|Expected the original full unit ID."
        target = None
        if target_x is not None or target_y is not None:
            if kind != "spread_religion" or any(type(v) is not int or v < 0 for v in (target_x, target_y)):
                return "Error: INVALID_SPREAD_TARGET|Supply both nonnegative integer city coordinates or neither; no action submitted."
            target = (target_x, target_y)
        entity, intent = ("unit", unit_id), target or ()
        if self._actions.matches(kind, entity, intent=intent):
            return await self.confirm_pending_action()
        pending = self._actions.pending
        if kind == "spread_religion" and pending is not None and pending.kind == kind:
            # Reject before a generic gate can confirm the old effect and let
            # this changed request spend another charge in the same call.
            return (f"ERR:PENDING_SPREAD_MISMATCH|Existing spread for unit {pending.entity[1]}, "
                    f"target={pending.intent}; new request not submitted. "
                    "Repeat the original unit ID and coordinates (or original omission) to confirm only.")
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        core_religion = None
        core_followers = None
        core_query = None
        if kind == "spread_religion":
            from civ_mcp.lua import spread_followers
            try:
                rows = await self.conn.execute_read(scope.guard(spread_followers.build_preflight(
                    unit_id, token, scope.owner, scope.civilization, scope.seed, scope.turn, target=target)))
                core_followers = spread_followers.parse_preflight(rows, unit_id, token, scope.owner)
                if core_followers is not None:
                    core_query = spread_followers.build_query(unit_id, token)
            except (OSError, TimeoutError, LuaError, ValueError):
                # 独立读数不可用时仍只能依靠既有实际压力增加证明，不能放宽成功条件。
                pass
        if kind == "launch_inquisition":
            from civ_mcp.lua import inquisition
            try:
                rows = await self.conn.execute_read(scope.guard(inquisition.build_core_preflight(
                    unit_id, token, scope.owner, scope.civilization, scope.seed, scope.turn)))
                core_religion = inquisition.parse_core_preflight(
                    rows, unit_id, token, scope.owner, scope.civilization, scope.seed, scope.turn)
                if core_religion is None:
                    return "Error: UNIT_EFFECT_REFUSED|GAMECORE_PREFLIGHT_UNCONFIRMED|" + "; ".join(rows)
                core_query = inquisition.build_core_query(unit_id, token, core_religion)
            except (OSError, TimeoutError, LuaError, ValueError) as exc:
                return f"Error: UNIT_EFFECT_REFUSED|GAMECORE_PREFLIGHT_UNAVAILABLE|{exc}; no action submitted."
        pending = _PendingAction(
            kind, entity, lq.build_unit_effect_query(kind, unit_id, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"witness_token": token, **({"target": target} if target is not None else {})}, intent=intent, gamecore_query=core_query,
            evidence=_ActionEvidence(
                tool="unit_action", entrypoint=kind, accepted=("UNIT_EFFECT_REQUESTED|",), refused="ERR:UNIT_EFFECT_REFUSED|",
                confirm=lambda rows: lq.parse_unit_effect_confirmation(rows, kind, unit_id, token),
                guidance=("Repeat the original unit effect" + (" with the same target" if target is not None else "")
                          + " to confirm only; rewards beyond reported evidence remain unmeasured. "),
            ),
        )
        return await self._submit_action(pending, scope, lq.build_unit_effect(
            kind, unit_id, token, core_religion=core_religion, core_followers=core_followers, target=target))

    @_guard_pending_mutation
    async def remove_heresy(self, unit_index: int) -> str:
        from civ_mcp.lua import remove_heresy
        if type(unit_index) is not int or unit_index < 0:
            return "Error: INVALID_UNIT_ID|Expected the original full unit ID."
        entity, intent = ("unit", unit_index), ()
        if self._actions.matches("remove_heresy", entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "remove_heresy", entity, remove_heresy.build_remove_heresy_query(unit_index, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool="unit_action", entrypoint="remove_heresy", accepted=("REMOVE_HERESY_REQUESTED|",),
                refused="ERR:REMOVE_HERESY_REFUSED|",
                confirm=lambda rows: remove_heresy.parse_remove_heresy_confirmation(rows, unit_index, token),
                guidance="Repeat the original remove_heresy action to confirm only; do not spend another charge. ",
            ),
        )
        return await self._submit_action(pending, scope, remove_heresy.build_remove_heresy(unit_index, token))

    @_guard_pending_mutation
    async def spread_religion(self, unit_index: int, target_x: int | None = None, target_y: int | None = None) -> str:
        return await self._unit_effect("spread_religion", unit_index, target_x, target_y)

    @_guard_pending_mutation
    async def condemn_heretic(self, unit_index: int) -> str:
        return await self._unit_effect("condemn_heretic", unit_index)

    @_guard_pending_mutation
    async def religious_attack(self, unit_id: int, target_x: int, target_y: int) -> str:
        from civ_mcp.lua import religious_combat
        if any(type(value) is not int or value < 0 for value in (unit_id, target_x, target_y)):
            return "Error: INVALID_RELIGIOUS_ATTACK|Expected full unit ID and nonnegative integer coordinates."
        kind, entity, intent = "religious_attack", ("unit", unit_id), (target_x, target_y)
        if self._actions.matches(kind, entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            kind, entity, religious_combat.build_religious_attack_query(unit_id, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool="unit_action", entrypoint=kind, accepted=("RELIGIOUS_ATTACK_REQUESTED|",),
                refused="ERR:RELIGIOUS_ATTACK_REFUSED|",
                confirm=lambda rows: religious_combat.parse_religious_attack(rows, unit_id, token),
                guidance="Repeat the same religious_attack to confirm only; no automatic resubmission. ",
            ),
        )
        return await self._submit_action(pending, scope, religious_combat.build_religious_attack(unit_id, target_x, target_y, token))

    @_guard_pending_mutation
    async def launch_inquisition(self, unit_index: int) -> str:
        return await self._unit_effect("launch_inquisition", unit_index)

    # ------------------------------------------------------------------
    # Trader teleport (InGame context)
    # ------------------------------------------------------------------

    @_guard_pending_mutation
    async def teleport_to_city(
        self, unit_index: int, target_x: int, target_y: int
    ) -> str:
        return await self._trader_action("teleport", "teleport_to_city", unit_index, target_x, target_y)

    async def _trader_action(self, kind: str, entrypoint: str, unit_id: int, x: int, y: int) -> str:
        if any(type(value) is not int or value < 0 for value in (unit_id, x, y)):
            return "Error: INVALID_TRADER_INTENT|Expected a full trader ID and nonnegative integer city coordinates."
        entity, intent = ("unit", unit_id), (x, y)
        if self._actions.matches(kind, entity, intent=intent):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        token = uuid.uuid4().hex
        pending = _PendingAction(
            kind, entity, lq.build_trader_action_query(kind, unit_id, x, y, token),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"target": (x, y), "witness_token": token}, intent=intent,
            evidence=_ActionEvidence(
                tool="unit_action", entrypoint=entrypoint, accepted=("TRADER_REQUESTED|",), refused="ERR:TRADER_REFUSED|",
                confirm=lambda rows: lq.parse_trader_confirmation(rows, token, kind, unit_id),
                guidance="Repeat the original trader action and destination to confirm only; inspect get_trade_routes. ",
            ),
        )
        return await self._submit_action(pending, scope, lq.build_trader_action(kind, unit_id, x, y, token))

    # ------------------------------------------------------------------
    # World Congress (InGame context)
    # ------------------------------------------------------------------

    async def get_world_congress(self) -> lq.WorldCongressStatus:
        lua = lq.build_world_congress_query()
        lines = await self.conn.execute_write(lua)
        if not any(line.startswith(("WC_STATUS|", "ERR:NO_WORLD_CONGRESS|")) for line in lines):
            raise LuaError("World Congress status is unknown")
        return lq.parse_world_congress_response(lines)

    @_guard_pending_mutation
    async def open_world_congress(self) -> str:
        """Explicitly enter a verified nonempty voting UI, never cast votes."""
        if self._pending_load or self._pending_save:
            return "ERR:OUTCOME_UNKNOWN|Load/save must be confirmed before opening Congress."
        if self._pending_action is not None:
            await self.confirm_pending_action()
            if self._pending_action is not None:
                return self._pending_unknown("Congress was not opened")
        return await self._congress_progress.submit(
            self.conn, 'open', allows_world=self._turn_progress.allows_diplomacy_world)

    @_guard_pending_mutation
    async def vote_world_congress(
        self, resolution_hash: int, option: int, target_index: int, num_votes: int
    ) -> str:
        return "ERR:WC_EXPLICIT_PLAN_REQUIRED|Use queue_wc_votes with the complete explicit native-stage plan."

    @_guard_pending_mutation
    async def submit_congress(self) -> str:
        return "ERR:WC_EXPLICIT_PLAN_REQUIRED|Use queue_wc_votes with the complete explicit native-stage plan."

    @_guard_pending_mutation
    async def queue_wc_votes(self, votes: list[dict]) -> str:
        """Submit one explicit native-stage plan, or report its existing record."""
        if self._pending_load or self._pending_save:
            return "ERR:OUTCOME_UNKNOWN|Load/save must be confirmed before Congress voting."
        return await self._congress_progress.submit(
            self.conn, 'votes', votes, allows_world=self._turn_progress.allows_diplomacy_world)

    # ------------------------------------------------------------------
    # City yield focus (InGame context)
    # ------------------------------------------------------------------

    @_guard_pending_mutation
    async def set_city_focus(self, city_id: int, focus: str) -> str:
        if type(city_id) is not int or city_id < 0 or not isinstance(focus, str):
            return "Error: FOCUS_INVALID|A native city ID and yield name are required."
        name = focus.upper()
        if name != "DEFAULT" and not name.startswith("YIELD_"):
            name = "YIELD_" + name
        if name != "DEFAULT" and not re.fullmatch(r"YIELD_[A-Z0-9_]+", name):
            return "Error: FOCUS_INVALID|Invalid yield name."
        if self._actions.matches("city_focus", ("city", city_id), item=name):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._actions.prepare_scope(self._game_identity)
        if isinstance(scope, str):
            return scope
        try:
            rows = await self.conn.execute_write(scope.guard(lq.build_city_yield_focus_query(city_id, name)))
            facts = {}
            for key in ("CITY", "CURRENT", "GOAL"):
                values = [row.split("|", 1)[1] for row in rows if row.startswith("CITY_FOCUS_" + key + "|")]
                if len(values) != 1:
                    return "Error: FOCUS_STATE_UNKNOWN|Native city or complete preferences unavailable; no action submitted."
                facts[key] = values[0]
        except (OSError, TimeoutError, LuaError):
            return "Error: FOCUS_STATE_UNKNOWN|Native Citizens read failed; no action submitted."
        if facts["CURRENT"] == facts["GOAL"]:
            return f"NO_CHANGE|city:{city_id}|focus:{name}|Native Citizens already match the target."
        token = uuid.uuid4().hex
        pending = _PendingAction(
            "city_focus", ("city", city_id),
            lq.build_city_yield_focus_query(city_id, name, witness_token=token, expected_city=facts["CITY"], expected_goal=facts["GOAL"]),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"item": name, "witness_token": token, "city": facts["CITY"].split(",")[0], "goal": facts["GOAL"]},
        )
        code = lq.build_set_yield_focus(city_id, name, expected_city=facts["CITY"], expected_current=facts["CURRENT"], expected_goal=facts["GOAL"], witness_token=token)
        return await self._submit_action(pending, scope, code)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def get_notifications(self) -> list[lq.GameNotification]:
        lua = lq.build_notifications_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_notifications_response(lines)

    # ------------------------------------------------------------------
    # Snapshot-diff for turn event detection
    # ------------------------------------------------------------------

    async def _take_snapshot(
        self, overview: lq.GameOverview | None = None, *, require_complete: bool = False
    ) -> lq.TurnSnapshot:
        """Capture current game state for diffing."""
        if overview is None:
            ov_lines = await self.conn.execute_write(lq.build_overview_query())
            overview = lq.parse_overview_response(ov_lines)

        # The unit builder uses InGame-only native operation/command checks,
        # including ranged target checks when a unit has moves remaining.
        unit_lines = await self.conn.execute_write(lq.build_units_query())
        units = lq.parse_units_response(unit_lines)

        city_lines = await self.conn.execute_write(lq.build_cities_query())
        cities, _ = lq.parse_cities_response(city_lines)

        try:
            stk_lines = await self.conn.execute_write(lq.build_stockpile_query())
            stockpiles = lq.parse_stockpile_response(stk_lines)
        except Exception:
            if require_complete:
                raise
            log.debug("Stockpile query failed", exc_info=True)
            stockpiles = []

        return lq.TurnSnapshot(
            turn=overview.turn,
            units={u.unit_id: u for u in units},
            cities={
                c.city_id: lq.CitySnapshot(
                    city_id=c.city_id,
                    name=c.name,
                    population=c.population,
                    currently_building=c.currently_building,
                    x=c.x,
                    y=c.y,
                    owner_id=c.owner_id,
                    game_identity=c.game_identity,
                    buildings=(
                        ["BUILDING_" + b for b in c.buildings]
                        if c.game_identity is not None
                        else None
                    ),
                    completed_districts=c.completed_districts,
                    food_surplus=c.food_surplus,
                    turns_to_grow=c.turns_to_grow,
                    loyalty=c.loyalty,
                    loyalty_per_turn=c.loyalty_per_turn,
                )
                for c in cities
            },
            current_research=overview.current_research,
            current_civic=overview.current_civic,
            stockpiles=stockpiles,
        )

    @staticmethod
    def _diff_snapshots(
        before: lq.TurnSnapshot, after: lq.TurnSnapshot
    ) -> list[lq.TurnEvent]:
        """Compare two snapshots and generate events."""
        events: list[lq.TurnEvent] = []

        # --- Unit events ---
        for uid, ub in before.units.items():
            if uid not in after.units:
                events.append(
                    lq.TurnEvent(
                        priority=1,
                        category="unit",
                        message=f"Your {ub.name} ({ub.unit_type}) no longer appears in the unit snapshot; cause unknown. Last seen at ({ub.x},{ub.y}).",
                    )
                )
            else:
                ua = after.units[uid]
                dmg = ub.health - ua.health
                if dmg > 0:
                    events.append(
                        lq.TurnEvent(
                            priority=2,
                            category="unit",
                            message=f"Your {ua.name} ({ua.unit_type}) took {dmg} damage! HP: {ua.health}/{ua.max_health} at ({ua.x},{ua.y}).",
                        )
                    )
                elif dmg < 0:
                    events.append(
                        lq.TurnEvent(
                            priority=3,
                            category="unit",
                            message=f"Your {ua.name} ({ua.unit_type}) healed {-dmg} HP. HP: {ua.health}/{ua.max_health}.",
                        )
                    )

        for uid, ua in after.units.items():
            if uid not in before.units:
                events.append(
                    lq.TurnEvent(
                        priority=3,
                        category="unit",
                        message=f"New unit: {ua.name} ({ua.unit_type}) at ({ua.x},{ua.y}).",
                    )
                )

        # --- City events ---
        for cid, cb in before.cities.items():
            if cid not in after.cities:
                events.append(
                    lq.TurnEvent(
                        priority=1,
                        category="city",
                        message=f"City {cb.name} was lost!",
                    )
                )
            else:
                ca = after.cities[cid]
                if ca.population > cb.population:
                    events.append(
                        lq.TurnEvent(
                            priority=3,
                            category="city",
                            message=f"{ca.name} grew to population {ca.population}.",
                        )
                    )
                if (
                    cb.currently_building not in (
                        "NONE", "nothing", "CORRUPTED_QUEUE", "UNKNOWN"
                    )
                    and ca.currently_building != cb.currently_building
                ):
                    now = ca.currently_building
                    if now in ("NONE", "nothing"):
                        now = "nothing"
                    elif now == "CORRUPTED_QUEUE":
                        now = "invalid queue (nonempty, zero item hash; unchanged)"
                    # A queue transition is not proof of production. Compare
                    # native products only across the same observed owner/game.
                    same_city = (
                        cb.game_identity is not None
                        and cb.game_identity == ca.game_identity
                        and cb.owner_id is not None
                        and cb.owner_id == ca.owner_id
                        and cb.x is not None
                        and cb.y is not None
                        and (cb.x, cb.y) == (ca.x, ca.y)
                    )
                    completed = False
                    item = cb.currently_building
                    if same_city and item.startswith("DISTRICT_"):
                        if (
                            cb.completed_districts is not None
                            and ca.completed_districts is not None
                        ):
                            new = set(ca.completed_districts) - set(cb.completed_districts)
                            completed = any(d.split("@", 1)[0] == item for d in new)
                    elif same_city and item.startswith("BUILDING_"):
                        if cb.buildings is not None and ca.buildings is not None:
                            completed = item in set(ca.buildings) - set(cb.buildings)
                    elif same_city and item.startswith("UNIT_"):
                        # Local-unit queries and matching city coordinates tie
                        # the new product to this owner/city. Adjacent naval
                        # spawns remain unconfirmed rather than guessed.
                        completed = any(
                            uid not in before.units
                            and unit.unit_type == item
                            and (unit.x, unit.y) == (ca.x, ca.y)
                            for uid, unit in after.units.items()
                        )
                    message = (
                        f"{ca.name} finished building {item}. Now: {now}."
                        if completed else
                        f"{ca.name} production queue changed from {item}; completion unconfirmed. Now: {now}."
                    )
                    events.append(
                        lq.TurnEvent(priority=2, category="city", message=message)
                    )

        for cid, ca in after.cities.items():
            if cid not in before.cities:
                events.append(
                    lq.TurnEvent(
                        priority=2,
                        category="city",
                        message=f"New city founded: {ca.name}!",
                    )
                )

        # --- Research/civic events ---
        if (
            before.current_research != "None"
            and after.current_research != before.current_research
        ):
            events.append(
                lq.TurnEvent(
                    priority=2,
                    category="research",
                    message=f"Research complete: {before.current_research}! Now: {after.current_research}.",
                )
            )

        if (
            before.current_civic != "None"
            and after.current_civic != before.current_civic
        ):
            events.append(
                lq.TurnEvent(
                    priority=2,
                    category="civic",
                    message=f"Civic complete: {before.current_civic}! Now: {after.current_civic}.",
                )
            )

        # --- Stockpile events ---
        before_stk = {s.name: s for s in before.stockpiles}
        after_stk = {s.name: s for s in after.stockpiles}
        for name, sa in after_stk.items():
            sb = before_stk.get(name)
            if sb and sb.amount > 0 and sa.amount == 0:
                net = sa.per_turn - sa.demand
                events.append(
                    lq.TurnEvent(
                        priority=2,
                        category="resources",
                        message=f"DEPLETED: {name} stockpile hit 0 ({net:+d}/t) — units requiring {name} may be disbanded.",
                    )
                )

        events.sort(key=lambda e: e.priority)
        return events

    @staticmethod
    def _build_turn_report(
        turn_before: int,
        turn_after: int,
        events: list[lq.TurnEvent],
        notifications: list[lq.GameNotification],
        stockpiles: list[lq.ResourceStockpile] | None = None,
        score: int | None = None,
    ) -> str:
        """Format turn events and notifications into a scannable report."""
        header = f"Turn {turn_before} -> {turn_after}"
        if score is not None:
            header += f" | Score: {score}"
        lines = [header]

        if stockpiles:
            visible = [
                s for s in stockpiles if s.amount > 0 or s.per_turn > 0 or s.demand > 0
            ]
            if visible:
                parts = []
                for s in visible:
                    net = s.per_turn - s.demand
                    parts.append(f"{s.name} {s.amount}/{s.cap} ({net:+d}/t)")
                lines.append(f"Resources: {', '.join(parts)}")

        if events:
            lines.append("")
            lines.append("== Events ==")
            icons = {1: "!!!", 2: ">>", 3: "--"}
            for e in events:
                icon = icons.get(e.priority, "--")
                lines.append(f"  {icon} {e.message}")

        # Use the enriched is_action_required field from the parser
        action_required = [n for n in notifications if n.is_action_required]
        # Only show informational notifications from the last 2 turns — older ones
        # are stale (e.g. "Wonder Completed" from 3 turns ago) and clutter the report.
        recent_cutoff = (turn_after or 0) - 2
        info_notifs = [
            n
            for n in notifications
            if not n.is_action_required and n.turn >= recent_cutoff
        ]

        if action_required:
            lines.append("")
            lines.append("== Action Required ==")
            for n in action_required:
                hint = f"  -> Use: {n.resolution_hint}" if n.resolution_hint else ""
                lines.append(f"  * {narrate_notification_message(n)}{hint}")

        if info_notifs:
            lines.append("")
            lines.append("== Notifications ==")
            for n in info_notifs:
                lines.append(f"  - {narrate_notification_message(n)}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    async def capture_turn_snapshot(self) -> lq.TurnSnapshot:
        """Capture reporting facts; request ownership lives in TurnProgress."""
        snapshot = await self._take_snapshot()
        self._last_snapshot = snapshot
        return snapshot

    async def report_turn_advance(
        self, before: int, after: int, baseline: lq.TurnSnapshot | None,
    ) -> str:
        """Present a confirmed advance without changing its request state."""
        current = None
        limited: list[str] = []
        try:
            current = await self.capture_turn_snapshot()
            if current.turn != after:
                # This observation is useful to a caller but cannot describe
                # the different turn whose advancement was just confirmed.
                current = None
                raise ValueError("Snapshot turn does not match confirmed turn")
        except (ConnectionError, TimeoutError, LuaError, ValueError):
            limited.append("Post-turn snapshot unavailable; read game state to confirm changes.")
        if baseline is not None and baseline.turn != before:
            baseline = None
            limited.append("Pre-turn snapshot unavailable; changes cannot be compared.")
        events = self._diff_snapshots(baseline, current) if baseline and current else []
        notifications: list[lq.GameNotification] = []
        try:
            lines = await self.conn.execute_write(lq.build_notifications_query())
            if not any(line.startswith("TOTAL|") for line in lines):
                limited.append("Notifications unavailable.")
            else:
                notifications = lq.parse_notifications_response(lines)
        except (ConnectionError, TimeoutError, LuaError, ValueError):
            limited.append("Notifications unavailable.")
        report = self._build_turn_report(
            before, after, events, notifications,
            stockpiles=current.stockpiles if current else None,
        )
        try:
            # TurnProgress has already retired the confirmed request. This
            # bounded read can only limit its report, never undo advancement.
            lines = await asyncio.wait_for(
                self.conn.execute_write(lq.build_victory_proximity_query()), timeout=VICTORY_REPORT_TIMEOUT,
            )
            victory = lq.parse_victory_progress_response(lines)
            if victory.turn != after or victory.local_player_id != self._local_player_id:
                raise ValueError("Victory observations do not match confirmed turn/player")
            warning = narrate_victory_warnings(victory)
            if warning:
                limited.append(warning)
        except (OSError, TimeoutError, LuaError, ValueError):
            limited.append("Victory warnings unavailable; use get_victory_progress.")
        return "\n".join([report, *limited])

    async def get_turn_status(self) -> str:
        """Read native phases without confirming or retiring pending advancement."""
        result = await self._turn_progress.get_status(self)
        if self._diplomacy_progress.pending:
            result += "\n" + self._diplomacy_progress.trade_summary()
            diagnostic = await self._diplomacy_progress.diagnostics(self.conn)
            if diagnostic:
                result += "\n" + diagnostic
        if self._diplomacy_cleanup.pending:
            result += "\ndiplomacy_cleanup: native callback outcome unknown; dismiss_popup only confirms its receipt; no callback or trade will be replayed."
        if self.popup_pending:
            result += "\npopup_cleanup: " + self._popup_unknown
        return result

    async def end_turn(self) -> str:
        """End the turn with snapshot-diff event detection."""
        if self.popup_pending:
            return self._popup_unknown
        if getattr(self, 'congress_pending', False):
            blocked = await self.congress_gate(allow_registered_turn=True)
            if blocked:
                return blocked
        if self.save_pending or getattr(self, "replacement_pending", False):
            return "ERR:OUTCOME_UNKNOWN|Load or save completion is not confirmed; inspect or recover before advancing."
        progress = getattr(self, "_diplomacy_progress", None)
        if progress is not None:
            if progress.incoming is not None:
                return progress.trade_summary()
            if self._diplomacy_cleanup.pending and not self.turn_pending:
                return "ERR:OUTCOME_UNKNOWN|Native diplomacy cleanup is unconfirmed; dismiss_popup only confirms it."
            if progress.pending and not self.turn_pending:
                return progress.trade_summary()
            await progress.ensure_observer(self.conn)
        return await self._turn_progress.execute(self)

    async def dismiss_popup(self) -> str:
        """Dismiss any blocking popup or UI overlay."""
        from civ_mcp.game_lifecycle import dismiss_popup_result

        if self.popup_pending:
            return self._popup_unknown
        if self.replacement_pending or self.save_pending:
            return "ERR:OUTCOME_UNKNOWN|Load or save completion is not confirmed; inspect or recover before dismissal."
        if self._diplomacy_progress.incoming is not None:
            return self._diplomacy_progress.trade_summary()
        if self._diplomacy_progress.trade is not None:
            return await self._diplomacy_progress.explicit_cleanup(self)
        if self._diplomacy_cleanup.pending:
            # 原外交回执可只读确认，不重进通用弹窗批次。
            cleanup = await self._diplomacy_cleanup.close(self.conn)
            if cleanup.status == "confirmed":
                return "Dismissed: DiplomacyActionView (native Close)"
            return "ERR:OUTCOME_UNKNOWN|" + cleanup.message
        # 在可能进入原生写入前锁存。旧批次没有逐项回执，不能靠界面消失追认或重发。
        self._popup_unknown = (
            "ERR:OUTCOME_UNKNOWN|Popup dismissal completion is unconfirmed; "
            "the original request is retained and will not be replayed. "
            "Inspect read-only facts or use explicit protected checkpoint recovery."
        )
        try:
            result = await dismiss_popup_result(self.conn, self._diplomacy_cleanup)
        except asyncio.CancelledError:
            if self._diplomacy_cleanup.pending:
                self._popup_unknown = None
            raise
        except (OSError, TimeoutError, LuaError) as exc:
            self._popup_unknown += f" Original failure: {exc}"
            return self._popup_unknown
        if result.status == "unknown":
            if self._diplomacy_cleanup.pending:
                self._popup_unknown = None
                return "ERR:OUTCOME_UNKNOWN|" + result.message
            self._popup_unknown += f" Original result: {result.message}"
            return self._popup_unknown
        self._popup_unknown = None
        return result.message

    async def list_saves(self) -> str:
        """List available save files."""
        from civ_mcp.game_lifecycle import list_saves

        return await list_saves(self.conn)

    async def save_game(self, save_name: str) -> str:
        """Save a confirmed active turn without resetting the current session."""
        from civ_mcp.game_lifecycle import _validate_save_name, save_game_result

        error = _validate_save_name(save_name)
        if error:
            return error
        blocked = await self._action_gate()
        if blocked:
            return blocked
        if self.turn_pending:
            return "Error: End-turn outcome is pending; confirm it before saving."
        if await self.check_game_over() is not None:
            return "Error: GAME_OVER|Saving is unavailable at the confirmed terminal boundary; no save submitted. Keep the last confirmed checkpoint and terminal evidence."
        identity = await self.get_game_identity()
        before = await self.get_game_overview()
        if identity == ("unknown", 0) or before.turn <= 0:
            return "Error: Current game identity and turn must be confirmed before saving."
        # Install the barrier before the first possible native submission. It
        # survives cancellation, including cancellation during post-save reads.
        self._pending_save = True
        saved = await save_game_result(self.conn, save_name)
        result = saved.message
        if saved.status != "confirmed":
            if saved.status == "not_submitted":
                self._pending_save = False
            return result
        try:
            after_identity = await self.get_game_identity()
            after = await self.get_game_overview()
        except (OSError, TimeoutError, LuaError, ValueError) as exc:
            self._pending_save = True
            return f"ERR:OUTCOME_UNKNOWN|{result} Saved-turn verification failed: {exc}. Inspect before recovery."
        if after_identity != identity or after.turn != before.turn:
            self._pending_save = True
            return f"ERR:OUTCOME_UNKNOWN|{result} Game or turn changed during saving; inspect before recovery."
        from civ_mcp.checkpoints import record_checkpoint, score_evidence
        error = record_checkpoint(save_name, identity, after.turn, score_evidence(before))
        if error:
            self._pending_save = True
            return error
        self._pending_save = False
        return f"{result} Civilization {identity[0]}, seed {identity[1]}, turn {after.turn}."

    @property
    def save_pending(self) -> bool:
        """A save acknowledgement or its saved-state verification is unresolved."""
        return self._pending_save

    @property
    def replacement_pending(self) -> bool:
        """A replacement is unconfirmed; save uncertainty is tracked separately."""
        return self._pending_load

    @_guard_pending_mutation
    async def load_save(self, save_index: int) -> str:
        """Resolve only a displayed index, then use the single verified loader."""
        from civ_mcp.game_lifecycle import listed_save_name

        name = listed_save_name(save_index)
        if name is None:
            return "Error: No matching cached save index; call list_saves or use load_game_save with an explicit confirmed name."
        return await self.load_game_save(name)

    @_guard_pending_mutation
    async def load_game_save(self, save_name: str, *, source_run_id: str | None = None,
                             expected_sha256: str | None = None) -> str:
        """Load once from MainMenu and confirm identity, readiness and snapshot."""
        return await self._replace_save(save_name, source_run_id=source_run_id, expected_sha256=expected_sha256)

    @_guard_pending_mutation
    async def reload_game_save(self, save_name: str) -> str:
        """Replace through the native menu while preserving the process."""
        return await self._replace_save(save_name, return_to_menu=True)

    @_guard_pending_mutation
    async def kill_game(self) -> str:
        """Explicit process stop; clear state only when shutdown is confirmed."""
        from civ_mcp import game_launcher, game_lifecycle

        self._pending_load = True
        await self.conn.disconnect()
        result = await game_launcher.stop_game()
        if result.confirmed:
            self.reset_session()
            game_lifecycle.clear_save_listing()
            self._pending_load = False
        return result.message

    @_guard_pending_mutation
    async def return_to_main_menu(self) -> str:
        """Submit one native exit to the main menu; verify it through frontend facts.

        Keeps the process and never loads or saves. A lost confirmation retains
        the replacement gate, so no gameplay read or action follows it blindly.
        """
        from civ_mcp import game_lifecycle

        self._pending_load = True
        result = await game_lifecycle.return_to_main_menu_result(self.conn)
        if result.status == "confirmed":
            self.reset_session()
            game_lifecycle.clear_save_listing()
            self._pending_load = False
            return "MAIN_MENU|Native main menu confirmed; no save or load was submitted."
        if result.status == "not_submitted":
            self._pending_load = False
            return f"Error: {result.message}"
        self._pending_load = True
        message = result.message
        return message if message.startswith("ERR:") else f"ERR:OUTCOME_UNKNOWN|{message}"

    @_guard_pending_mutation
    async def launch_game(self) -> str:
        from civ_mcp import game_launcher

        return (await game_launcher.start_game()).message

    async def restart_and_load(self, save_name: str | None = None) -> str:
        """Explicit cold recovery; retains the gate across process replacement."""
        from civ_mcp import checkpoints, game_launcher, game_lifecycle

        if not save_name:
            return "Error: Provide an explicit confirmed checkpoint save_name; autosave guessing is disabled."
        error = game_lifecycle._validate_save_name(save_name)
        if error:
            return error
        if checkpoints.expected_checkpoint(save_name) is None:
            return _unregistered_checkpoint_message(save_name, "no restart performed")
        self._pending_load = True
        await self.conn.disconnect()
        stopped = await game_launcher.stop_game()
        if not stopped.confirmed:
            return f"Error: Restart stopped because Civ6 shutdown was not confirmed: {stopped.message}"
        self.reset_session()
        game_lifecycle.clear_save_listing()
        started = await game_launcher.start_game()
        if not started.confirmed:
            return f"Error: {stopped.message} | {started.message}"
        loaded = await self._replace_save(save_name, recovering=True)
        return f"{loaded} | {stopped.message} | {started.message}"

    async def _replace_save(self, save_name: str, *, return_to_menu: bool = False, recovering: bool = False,
                            source_run_id: str | None = None, expected_sha256: str | None = None) -> str:
        """同一个连接负责加载与验证；确认窗口内不重复发送加载命令。"""
        from civ_mcp import checkpoints, game_lifecycle
        from civ_mcp.narrate import narrate_score_comparison

        error = game_lifecycle._validate_save_name(save_name)
        if error:
            return error
        if self._pending_load and not recovering:
            return "ERR:OUTCOME_UNKNOWN|An earlier load is unconfirmed; inspect state or use explicit recovery, not another load."
        if not recovering and (
            self._pending_save or self._pending_end_turn or self.diplomacy_pending or self.congress_pending
            or self.pending_action_entity is not None
        ):
            return "ERR:OUTCOME_UNKNOWN|An action/save/end-turn is pending; confirm it or use explicit restart_and_load before replacing the session."
        historical = source_run_id is not None or expected_sha256 is not None
        if historical and (not source_run_id or not expected_sha256):
            return 'Error: Historical loading requires both source_run_id and expected_sha256; no load sent.'

        def target_identity():
            if not historical:
                return checkpoints.expected_checkpoint(save_name)
            record = checkpoints.archived_checkpoint(save_name, source_run_id, expected_sha256)
            return None if record is None else (record['civilization'], record['seed'], record['turn'])

        source_record = checkpoints.archived_checkpoint(save_name, source_run_id, expected_sha256) if historical else None
        expected = ((source_record['civilization'], source_record['seed'], source_record['turn']) if source_record else None) if historical else target_identity()
        if expected is None:
            if historical:
                return 'Error: HISTORICAL_CHECKPOINT_UNVERIFIED|Source run, registered identity or SHA-256 does not match; no load sent. Preserve the source and inspect its original checkpoint record.'
            return _unregistered_checkpoint_message(save_name, "no load sent")
        saved_score = source_record['score'] if historical else checkpoints.expected_score(save_name)
        submitted = False
        replacing = recovering
        try:
            async with asyncio.timeout(LOAD_TIMEOUT):
                if return_to_menu:
                    # Install the barrier before ExitToMainMenu can be submitted.
                    # The helper only releases it when native code proves no exit ran.
                    self._pending_load = True
                    replacing = True
                    menu = await game_lifecycle.return_to_main_menu_result(self.conn)
                    if menu.status != "confirmed":
                        if menu.status == "not_submitted":
                            self._pending_load = False
                            replacing = recovering
                        return menu.message
                    self.reset_session()
                    game_lifecycle.clear_save_listing()
                # During startup FireTuner briefly opens before MainMenu exists,
                # then resets the socket. Retry only this read-only discovery;
                # the actual load below is still submitted exactly once.
                while True:
                    try:
                        frontend = await game_lifecycle.is_in_frontend(self.conn)
                        if frontend is False:
                            message = "A game is loaded; return to the main menu first."
                            return ("ERR:OUTCOME_UNKNOWN|" if replacing else "Error: ") + message
                        if frontend is True and "MainMenu" in self.conn.lua_states.values():
                            break
                    except (OSError, LuaError, ValueError):
                        pass
                    await asyncio.sleep(2)
                if target_identity() != expected:
                    prefix = "ERR:OUTCOME_UNKNOWN|" if replacing else "Error: "
                    return prefix + "Checkpoint file changed before load; no load sent."
                self._pending_load = True
                # A cancelled transport may already have dispatched the native load.
                submitted = True
                result = await game_lifecycle.load_save_from_frontend_result(self.conn, save_name)
                submitted = result.status in {"submitted", "unknown"}
                if not submitted:
                    self._pending_load = replacing
                    if replacing:
                        return "ERR:OUTCOME_UNKNOWN|Main menu reached but replacement is not verified. " + result.message
                    return result.message
                self.reset_session()
                game_lifecycle.clear_save_listing()
                while True:
                    try:
                        await self.conn.reconnect()
                        if self.conn.gamecore_index is not None and self.conn.ingame_index is not None:
                            from civ_mcp.end_turn import read_turn_state
                            turn, ready = await read_turn_state(self)
                            if ready is True:
                                identity = await self.get_game_identity()
                                ov = await self.get_game_overview(require_snapshot=True)
                                actual = (*identity, ov.turn)
                                if actual != expected or turn != ov.turn:
                                    return f"ERR:OUTCOME_UNKNOWN|Loaded state mismatch: expected {expected}, observed {actual}, active turn {turn}. No load replayed."
                                # Confirm identity and readiness after all snapshot
                                # reads too, rather than trusting their earlier state.
                                final_identity = await self.get_game_identity()
                                final_turn, final_ready = await read_turn_state(self)
                                if final_identity != identity or final_turn != ov.turn or final_ready is not True:
                                    raise ValueError("Loaded state changed or became unreadable during snapshot confirmation")
                                if target_identity() != expected:
                                    return "ERR:OUTCOME_UNKNOWN|Checkpoint file changed during load; recovery not verified."
                                if historical:
                                    ownership_error = checkpoints.begin_archived_run(save_name, source_run_id, expected_sha256)
                                    if ownership_error:
                                        return ownership_error
                                elif checkpoints.is_original_baseline_name(save_name):
                                    ownership_error = checkpoints.begin_baseline_run(save_name)
                                    if ownership_error:
                                        return ownership_error
                                self._pending_load = False
                                score_comparison = narrate_score_comparison(saved_score, checkpoints.score_evidence(ov))
                                return f"Loaded {save_name}: {identity[0]}, seed {identity[1]}, turn {ov.turn}.\n{score_comparison}"
                    except (OSError, LuaError, ValueError):
                        self._pending_load = True
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            if replacing or submitted:
                self._pending_load = True
            raise
        except TimeoutError:
            if replacing or submitted:
                self._pending_load = True
            await self.conn.disconnect()
            if not submitted and not replacing:
                return "Error: MainMenu was not ready within 180 seconds; no load command was sent."
            return "ERR:OUTCOME_UNKNOWN|Session replacement was not confirmed within 180 seconds. Use explicit restart_and_load for fault recovery."

    @_guard_pending_mutation
    async def execute_lua(self, code: str, context: str = "gamecore") -> str:
        """Escape hatch: run arbitrary Lua code."""
        from civ_mcp.game_lifecycle import execute_lua

        return await execute_lua(self.conn, code, context)

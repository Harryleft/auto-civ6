"""High-level game state API with server-side narration.

Wraps GameConnection + Lua into typed queries, actions, verification and
human-readable results. This layer does not depend on MCP.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

from civ_mcp.actions import (
    ActionLifecycle, _ActionScope, _PendingAction, _action_result, _extract_pre_hp,
    _extract_post_hp, _extract_city_defense, _format_attack_followup,
)
from civ_mcp import lua as lq
from civ_mcp.connection import GameConnection, LuaError
from civ_mcp.end_turn import TurnProgress
from civ_mcp.narrate import (
    narrate_combat_estimate,
    narrate_move_discoveries,
    narrate_notification_message,
    narrate_settle_candidates,
    narrate_test_trade,
    narrate_victory_warnings,
)


log = logging.getLogger(__name__)

LOAD_TIMEOUT = 180.0

ACTION_CONFIRM_TIMEOUT = 5.0
ACTION_CONFIRM_INTERVAL = 0.15
VICTORY_REPORT_TIMEOUT = 5.0


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
        # Replacement survives all cache/identity resets until verified.
        self._actions = ActionLifecycle(self.conn)
        self._turn_progress = TurnProgress()
        from civ_mcp.game_lifecycle import DiplomacyViewCleanup
        self._diplomacy_cleanup = DiplomacyViewCleanup()
        self._trade_result_pending_cleanup: str | None = None

    @property
    def turn_pending(self) -> bool:
        """One fact for action/save gates and MCP while advancement is unconfirmed."""
        return self._turn_progress.pending

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
        if self._pending_load or self._pending_save:
            return "ERR:OUTCOME_UNKNOWN|Load or save completion is not confirmed; inspect or recover before gameplay."
        if self.turn_pending:
            return "ERR:OUTCOME_UNKNOWN|End-turn outcome is pending; confirm end_turn before gameplay."
        if entity is not None and entity[0] == "unit":
            entity = (entity[0], entity[1] % 65536)
        pending = self._pending_action
        if pending is not None and (entity is None or entity == self.pending_action_entity):
            await self.confirm_pending_action()
            if self._pending_action is not None:
                return self._pending_unknown("dependent action was not submitted")
        return None

    async def get_game_identity(self) -> tuple[str, int]:
        """Return (civ_type_lower, random_seed) for the current game.

        Always queries the game so we detect new-game loads.  When the
        identity changes, observation caches expire without retiring requests.
        """
        code = (
            "local me = Game.GetLocalPlayer() "
            "local cfg = PlayerConfigurations[me] "
            'print("GAMESEED|" .. cfg:GetCivilizationTypeName() '
            '.. "|" .. tostring(GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"))) '
            'print("---END---")'
        )
        lines = await self.conn.execute_write(code)
        for line in lines:
            if line.startswith("GAMESEED|"):
                parts = line.split("|")
                civ = parts[1].replace("CIVILIZATION_", "").lower()
                seed = int(parts[2])
                new_id = (civ, seed)
                if self._game_identity is not None and new_id != self._game_identity:
                    log.info("Game changed: %s → %s", self._game_identity, new_id)
                    self._clear_world_caches()
                    self._turn_progress.invalidate_scope()
                    self._diplomacy_cleanup.invalidate_scope()
                self._game_identity = new_id
                return self._game_identity
        return ("unknown", 0)

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    async def get_game_overview(self, *, require_snapshot: bool = False) -> lq.GameOverview:
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

    async def get_units(self) -> list[lq.UnitInfo]:
        if self._pending_action:
            await self.confirm_pending_action(wait=False)
        lines = await self.conn.execute_write(lq.build_units_query())
        return lq.parse_units_response(lines)

    async def get_builder_tasks(
        self,
    ) -> lq.BuilderTaskReport:
        lines = await self.conn.execute_write(lq.build_builder_tasks_query())
        return lq.parse_builder_tasks(lines)

    async def get_spies(self) -> list[lq.SpyInfo]:
        lines = await self.conn.execute_write(lq.build_get_spies_query())
        return lq.parse_spies_response(lines)

    async def spy_travel(self, unit_index: int, target_x: int, target_y: int) -> str:
        lua = lq.build_spy_travel(unit_index, target_x, target_y)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def spy_mission(
        self, unit_index: int, mission_type: str, target_x: int, target_y: int
    ) -> str:
        lua = lq.build_spy_mission(unit_index, mission_type, target_x, target_y)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def spy_escape(self, unit_index: int, district_type: str) -> str:
        lua = lq.build_spy_escape_route(unit_index, district_type)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def get_threat_scan(self) -> list[lq.ThreatInfo]:
        lines = await self.conn.execute_read(lq.build_threat_scan_query())
        return lq.parse_threat_scan_response(lines)

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

    async def get_cities(self) -> tuple[list[lq.CityInfo], list[str]]:
        if self._pending_action:
            await self.confirm_pending_action(wait=False)
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

    async def get_empire_resources(
        self,
    ) -> tuple[
        list[lq.ResourceStockpile],
        list[lq.OwnedResource],
        list[lq.NearbyResource],
        dict[str, int],
    ]:
        # InGame context needed for GetResourceStockpileCap etc.
        lines = await self.conn.execute_write(lq.build_empire_resources_query())
        return lq.parse_empire_resources_response(lines)

    # ------------------------------------------------------------------
    # Action methods (run in InGame context for UnitManager access)
    # ------------------------------------------------------------------

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

    async def attack_unit(self, unit_index: int, target_x: int, target_y: int) -> str:
        return await self._attack_and_confirm(("unit", unit_index), target_x, target_y)

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
        pending = _PendingAction(
            "attack", entity,
            lq.build_city_attack_followup(entity[1], target_x, target_y, city_token) if city_token else
            lq.build_attack_followup_query(target_x, target_y, entity[1]),
            context="write", identity=scope.identity, turn=scope.turn, owner=scope.owner,
            details={"target": (target_x, target_y), "city_witness_token": city_token},
            success=f"CITY_RANGE_ATTACK|city:{entity[1]} -> {target_x},{target_y}" if city_token else "",
        )
        build = lq.build_attack_unit if entity[0] == "unit" else lq.build_city_attack
        code = (build(entity[1], target_x, target_y, witness_token=city_token) if city_token else
                build(entity[1], target_x, target_y))
        result = await self._submit_action(pending, scope, code)
        if result.startswith(("RANGE_ATTACK", "MELEE_ATTACK", "AIR_ATTACK", "CITY_RANGE_ATTACK")):
            if estimate and (result.startswith("AIR_ATTACK") or estimate.is_ranged != result.startswith("RANGE_ATTACK")):
                estimate_str = unknown_estimate
            return estimate_str + result
        if "OUTCOME_UNKNOWN" in result and pending.success and estimate_str:
            return result + "\n" + estimate_str
        return result

    async def resolve_city_capture(self, action: str) -> str:
        lua = lq.build_resolve_city_capture(action)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

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
        lines = await self.conn.execute_read(lua)
        candidates = lq.parse_settle_advisor_response(lines)
        if candidates:
            return narrate_settle_candidates(candidates)
        # Auto-fallback to global scan when no local candidates
        try:
            global_candidates = await self.get_global_settle_scan()
            if global_candidates:
                header = "No valid settle locations within 5 tiles. Best sites on revealed map:\n"
                return header + narrate_settle_candidates(global_candidates[:5])
        except Exception:
            log.debug("Global settle fallback failed", exc_info=True)
        return "No valid settle locations found within 5 tiles or on revealed map."

    async def get_global_settle_scan(self) -> list[lq.SettleCandidate]:
        lua = lq.build_global_settle_scan()
        lines = await self.conn.execute_read(lua)
        return lq.parse_settle_advisor_response(lines)

    async def fortify_unit(self, unit_index: int) -> str:
        return await self._set_unit_readiness(unit_index, "fortify")

    async def skip_unit(self, unit_index: int) -> str:
        return await self._set_unit_readiness(unit_index, "skip")

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

    async def automate_explore(self, unit_index: int) -> str:
        lua = lq.build_automate_explore(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def heal_unit(self, unit_index: int) -> str:
        lua = lq.build_heal_unit(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def alert_unit(self, unit_index: int) -> str:
        lua = lq.build_alert_unit(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def sleep_unit(self, unit_index: int) -> str:
        lua = lq.build_sleep_unit(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def delete_unit(self, unit_index: int) -> str:
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_delete_unit(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def improve_tile(self, unit_index: int, improvement_name: str) -> str:
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_improve_tile(unit_index, improvement_name)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def remove_feature(self, unit_index: int) -> str:
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_remove_feature(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def repair_improvement(self, unit_index: int) -> str:
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_repair_improvement(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def remove_improvement(self, unit_index: int) -> str:
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_remove_improvement(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def sacrifice_builder_charges(self, unit_index: int) -> str:
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_sacrifice_builder_charges(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def build_route(self, unit_index: int) -> str:
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_build_route(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

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

    async def purchase_item(
        self,
        city_id: int,
        item_type: str,
        item_name: str,
        yield_type: str = "YIELD_GOLD",
    ) -> str:
        blocked = await self._action_gate(("city", city_id))
        if blocked:
            return blocked
        lua = lq.build_purchase_item(city_id, item_type, item_name, yield_type)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def list_city_production(self, city_id: int) -> list[lq.ProductionOption]:
        lua = lq.build_city_production_query(city_id)
        # Must use InGame context — bq:CanProduce() throws "Not Implemented" in GameCore
        lines = await self.conn.execute_write(lua)
        return lq.parse_city_production_response(lines)

    async def set_research(self, tech_name: str) -> str:
        lua = lq.build_set_research(tech_name)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        if "RESEARCHING" in result:
            # Verify InGame actually accepted it by comparing tech INDEX.
            # RequestPlayerOperation is fire-and-forget — it can silently no-op
            # while GetResearchingTech() still returns the OLD tech's index (!= -1).
            verify = await self.conn.execute_read(
                f"local me = Game.GetLocalPlayer(); "
                f"local idx = nil; "
                f"for row in GameInfo.Technologies() do "
                f'if row.TechnologyType == "{tech_name}" then idx = row.Index; break end '
                f"end; "
                f"local cur = Players[me]:GetTechs():GetResearchingTech(); "
                f"print(cur == idx and 'MATCH' or 'MISMATCH:'..tostring(cur)..'~='..tostring(idx)); "
                f'print("{lq.SENTINEL}")'
            )
            matched = verify and verify[0] == "MATCH"
            if not matched:
                # InGame silently failed — fall back to GameCore
                gc_lua = lq.build_set_research_gamecore(tech_name)
                gc_lines = await self.conn.execute_read(gc_lua)
                return _action_result(gc_lines)
        return result

    async def set_civic(self, civic_name: str) -> str:
        lua = lq.build_set_civic(civic_name)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        if "PROGRESSING" in result:
            # Verify InGame actually accepted it by comparing civic INDEX.
            verify = await self.conn.execute_read(
                f"local me = Game.GetLocalPlayer(); "
                f"local idx = nil; "
                f"for row in GameInfo.Civics() do "
                f'if row.CivicType == "{civic_name}" then idx = row.Index; break end '
                f"end; "
                f"local cur = Players[me]:GetCulture():GetProgressingCivic(); "
                f"print(cur == idx and 'MATCH' or 'MISMATCH:'..tostring(cur)..'~='..tostring(idx)); "
                f'print("{lq.SENTINEL}")'
            )
            matched = verify and verify[0] == "MATCH"
            if not matched:
                # InGame silently failed — fall back to GameCore
                lua_gc = lq.build_set_civic_gamecore(civic_name)
                gc_lines = await self.conn.execute_read(lua_gc)
                return _action_result(gc_lines)
        return result

    # ------------------------------------------------------------------
    # Diplomacy methods
    # ------------------------------------------------------------------

    async def get_diplomacy_sessions(self) -> lq.DiplomacyContext:
        lua = lq.build_diplomacy_session_query()
        lines = await self.conn.execute_write(lua)
        if "CONTEXT_COMPLETE" not in lines:
            raise LuaError("Diplomacy session status is unknown")
        return lq.parse_diplomacy_sessions(lines)

    async def diplomacy_respond(self, other_player_id: int, response: str) -> str:
        # Capture dialogue text BEFORE response to detect goodbye phase
        pre_sessions = await self.get_diplomacy_sessions()
        pre_text = ""
        for s in pre_sessions.sessions:
            if s.other_player_id == other_player_id:
                pre_text = s.dialogue_text
                break

        # Phase 1: Send AddResponse only (no CloseSession — engine handles lifecycle)
        lua = lq.build_diplomacy_respond(other_player_id, response.upper())
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)

        # EXIT and error paths return immediately
        if "SESSION_CLOSED" in result or result.startswith("Error"):
            return result

        # Phase 2: Give engine ~9 frames (0.3s at 30fps) to process the
        # response and transition/close the session, then check state in
        # a separate TCP round-trip (same-frame checks see stale state).
        await asyncio.sleep(0.3)
        check_lines = await self.conn.execute_write(
            lq.build_check_diplomacy_session_state(other_player_id)
        )
        if not any("SESSION_OPEN" in l for l in check_lines):
            return f"OK:RESPONDED|{response.upper()}|SESSION_CLOSED"

        # Phase 3: Session still open — check if dialogue text changed.
        # If unchanged, we're in the goodbye phase. Auto-close.
        post_sessions = await self.get_diplomacy_sessions()
        if post_sessions.status == "UNKNOWN":
            return (f"OK:RESPONDED|{response.upper()}|SESSION_CONTINUES\n"
                    f"Diplomacy context UNKNOWN: {post_sessions.reason}; read again before responding.")
        post_text = ""
        for s in post_sessions.sessions:
            if s.other_player_id == other_player_id:
                post_text = s.dialogue_text
                break

        if post_sessions.status == "NONE":
            # Session disappeared between checks (race condition)
            return f"OK:RESPONDED|{response.upper()}|SESSION_CLOSED"

        if (pre_sessions.status == post_sessions.status == "DIALOGUE"
                and pre_text and post_text == pre_text
                and len(pre_sessions.sessions) == len(post_sessions.sessions) == 1
                and pre_sessions.sessions[0].session_id == post_sessions.sessions[0].session_id
                and post_sessions.sessions[0].other_player_id == other_player_id):
            # Dialogue unchanged → goodbye phase. Force close.
            log.info(
                "Goodbye phase detected (text unchanged) for player %d — auto-closing",
                other_player_id,
            )
            close_lua = lq.build_diplomacy_respond(other_player_id, "EXIT")
            await self.conn.execute_write(close_lua)
            return f"OK:RESPONDED|{response.upper()}|SESSION_CLOSED (auto-closed goodbye phase)"

        # Include the new dialogue text so the agent can see what the leader said
        post_reason = ""
        for s in post_sessions.sessions:
            if s.other_player_id == other_player_id:
                post_reason = s.reason_text
                break
        dialogue_note = f'\nLeader says: "{post_text}"'
        if post_reason:
            dialogue_note += f'\nReason/agenda: "{post_reason}"'
        return f"OK:RESPONDED|{response.upper()}|SESSION_CONTINUES{dialogue_note}"

    async def send_diplomatic_action(self, other_player_id: int, action: str) -> str:
        if action.upper() == "OPEN_BORDERS":
            # Session-based OPEN_BORDERS causes AI turn hang.
            # Route through the trade deal API instead (mutual open borders).
            return await self.propose_trade(
                other_player_id,
                offer_items=[{"type": "AGREEMENT", "subtype": "OPEN_BORDERS"}],
                request_items=[{"type": "AGREEMENT", "subtype": "OPEN_BORDERS"}],
            )
        is_war = action.upper().endswith("_WAR") and action.upper().startswith(
            "DECLARE_"
        )
        is_friendship = action.upper() == "DECLARE_FRIENDSHIP"
        access_action = (
            action.upper() if action.upper() in {"DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"} else None
        )
        lua = lq.build_send_diplo_action(other_player_id, action.upper())
        try:
            lines = await self.conn.execute_write(lua)
        except (OSError, TimeoutError, LuaError, ValueError):
            if is_friendship or access_action:
                return await self._confirm_diplomatic_relationship(other_player_id, None, access_action=access_action)
            raise
        if is_friendship or access_action:
            return await self._confirm_diplomatic_relationship(other_player_id, lines, access_action=access_action)
        result = _action_result(lines)

        # _action_result rewrites "ERR:" to "Error: " — only schedule the
        # background UI cleanup when the declaration actually succeeded.
        if is_war and not result.startswith("Error:"):
            # War session left open for ~8s so the leader animation plays.
            # Background task will close session + dismiss DiplomacyActionView.
            asyncio.create_task(self._cleanup_war_diplomacy(other_player_id))

        return result

    async def _confirm_diplomatic_relationship(
        self, other_player_id: int, lines: list[str] | None, *,
        alliance_type: str | None = None, access_action: str | None = None,
    ) -> str:
        """Confirm only the scoped native relationship; never submit or clean up."""
        kind = access_action or ("ALLIANCE" if alliance_type is not None else "FRIENDSHIP")
        relationship = f"{alliance_type} alliance" if alliance_type is not None else "Friendship declaration"
        if access_action:
            relationship = "Delegation" if access_action == "DIPLOMATIC_DELEGATION" else "Embassy"
        unconfirmed = (
            f"PROPOSED_UNCONFIRMED|{relationship} to player {other_player_id} is unconfirmed; "
            "the request may have been submitted. Query get_diplomacy before another proposal; "
            "do not automatically resend."
        )
        if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
            return unconfirmed
        result = _action_result(lines)
        if result.startswith("Error:") and not result.startswith("Error: OUTCOME_UNKNOWN"):
            return result
        scope = [line.split("|") for line in lines if line.startswith(f"{kind}_SCOPE|")]
        receipt_prefix = "ALREADY_ESTABLISHED|" if access_action and result.startswith("ALREADY_ESTABLISHED|") else "PROPOSED|"
        proposed = [line for line in lines if line.startswith(f"OK:{receipt_prefix}")]
        scope_length = 5 if access_action else (4 if alliance_type is not None else 3)
        if not result.startswith(receipt_prefix) or len(proposed) != 1 or len(scope) != 1 or len(scope[0]) != scope_length:
            return unconfirmed
        try:
            owner, target = int(scope[0][1]), int(scope[0][2])
        except ValueError:
            return unconfirmed
        if owner < 0 or owner == target or target != other_player_id:
            return unconfirmed
        state = "PRESENT" if access_action else (f"ALLIANCE_{alliance_type}" if alliance_type is not None else "DECLARED_FRIEND")
        if alliance_type is not None and scope[0][3] != state:
            return unconfirmed
        name = result.split("|", 1)[1]
        if access_action:
            try:
                seed = int(scope[0][3])
            except ValueError:
                return unconfirmed
            civilization = scope[0][4]
            if not re.fullmatch(r"CIVILIZATION_[A-Za-z0-9_]+", civilization):
                return unconfirmed
            query = lq.build_check_diplomatic_access(target, owner, access_action, seed, civilization)
        elif alliance_type is not None:
            query = lq.build_check_alliance_state(target, owner, alliance_type)
        else:
            query = lq.build_check_friendship_state(target, owner)
        try:
            # The deadline includes reads. Transient state never authorizes a
            # rejection or another submission of any diplomatic proposal.
            async with asyncio.timeout(ACTION_CONFIRM_TIMEOUT):
                while True:
                    evidence = await self.conn.execute_write(query)
                    if evidence == [f"{kind}|{owner}|{target}|{state}"]:
                        if access_action:
                            if receipt_prefix == "ALREADY_ESTABLISHED|":
                                return f"ACCEPTED|{relationship} already established with {name}"
                            return f"ACCEPTED|{name} accepted your {relationship.lower()}"
                        if alliance_type is not None:
                            return f"ACCEPTED|{alliance_type} alliance formed with {name}"
                        return f"ACCEPTED|Friendship established with {name}"
                    await asyncio.sleep(ACTION_CONFIRM_INTERVAL)
        except (OSError, TimeoutError, LuaError, ValueError):
            pass
        return unconfirmed

    async def _cleanup_war_diplomacy(self, other_player_id: int) -> None:
        """Background: dismiss war declaration diplomacy view after animation.

        Two-phase cleanup (must be separate Lua calls — the engine fires
        OnDiplomacySessionClosed asynchronously so the view needs a frame
        to transition from CONVERSATION_MODE to OVERVIEW_MODE):
        1. CloseSession — view transitions to OVERVIEW_MODE
        2. NaturalWonderPopup trick — forces Close() from OVERVIEW_MODE
        """
        await asyncio.sleep(8)
        try:
            # Phase 1: close session → view goes to OVERVIEW_MODE
            lua1 = lq.build_war_close_session(other_player_id)
            await self.conn.execute_write(lua1)

            # Let engine process OnDiplomacySessionClosed
            await asyncio.sleep(1)

            # Phase 2: force-dismiss the OVERVIEW_MODE view
            lua2 = lq.build_war_dismiss_view()
            await self.conn.execute_write(lua2)
        except Exception as e:
            log.warning("War diplomacy cleanup failed: %s", e)

    # ------------------------------------------------------------------
    # Trade deal methods (InGame context)
    # ------------------------------------------------------------------

    async def get_deal_options(self, other_player_id: int) -> lq.DealOptions:
        lua = lq.build_deal_options_query(other_player_id)
        lines = await self.conn.execute_write(lua)
        return lq.parse_deal_options_response(lines)

    async def get_pending_deals(self) -> lq.DiplomacyContext:
        lua = lq.build_pending_deals_query()
        lines = await self.conn.execute_write(lua)
        if "CONTEXT_COMPLETE" not in lines:
            raise LuaError("Pending deal status is unknown")
        return lq.parse_pending_deals_response(lines)

    async def respond_to_deal(self, other_player_id: int, accept: bool) -> str:
        lua = lq.build_respond_to_deal(other_player_id, accept)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def propose_trade(
        self,
        other_player_id: int,
        offer_items: list[dict],
        request_items: list[dict],
    ) -> str:
        if self._trade_result_pending_cleanup is not None:
            return "No new trade submitted; confirming previous native cleanup.\n" + await self._finish_trade_cleanup(self._trade_result_pending_cleanup)
        lua = lq.build_propose_trade(other_player_id, offer_items, request_items)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        return await self._finish_trade_cleanup(result)

    async def _finish_trade_cleanup(self, result: str) -> str:
        cleanup = await self._diplomacy_cleanup.close(self.conn)
        self._trade_result_pending_cleanup = result if cleanup.status == "unknown" else None
        return result + "\n" + cleanup.message

    async def test_trade(
        self,
        other_player_id: int,
        offer_items: list[dict],
        request_items: list[dict],
    ) -> str:
        if self._trade_result_pending_cleanup is not None:
            return "No new trade submitted; confirming previous native cleanup.\n" + await self._finish_trade_cleanup(self._trade_result_pending_cleanup)
        lua = lq.build_test_trade(other_player_id, offer_items, request_items)
        lines = await self.conn.execute_write(lua)
        result = lq.parse_test_trade_response(lines)
        return await self._finish_trade_cleanup(narrate_test_trade(result))

    async def propose_peace(self, other_player_id: int) -> str:
        lua = lq.build_propose_peace(other_player_id)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        if result.startswith("Error"):
            return result
        name = result.split("|", 1)[1] if "|" in result else f"player {other_player_id}"
        # War state is asynchronous. The deadline includes each read; observing
        # continued war is not evidence that the native AI rejected the offer.
        query = lq.build_check_war_state(other_player_id)
        try:
            async with asyncio.timeout(ACTION_CONFIRM_TIMEOUT):
                while True:
                    verify_lines = await self.conn.execute_write(query)
                    if verify_lines == ["AT_PEACE"]:
                        return f"ACCEPTED|Peace established with {name}"
                    await asyncio.sleep(ACTION_CONFIRM_INTERVAL)
        except (OSError, TimeoutError, LuaError, ValueError):
            pass
        return (
            f"PROPOSED_UNCONFIRMED|Peace proposed to {name}; outcome not confirmed. "
            "Query get_diplomacy before another proposal; do not automatically resend."
        )

    async def form_alliance(self, other_player_id: int, alliance_type: str) -> str:
        requested = alliance_type.upper()
        lua = lq.build_form_alliance(other_player_id, requested)
        try:
            lines = await self.conn.execute_write(lua)
        except (OSError, TimeoutError, LuaError, ValueError):
            lines = None
        return await self._confirm_diplomatic_relationship(other_player_id, lines, alliance_type=requested)

    # ------------------------------------------------------------------
    # Policy methods (InGame context)
    # ------------------------------------------------------------------

    async def get_policies(self) -> lq.GovernmentStatus:
        lua = lq.build_policies_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_policies_response(lines)

    async def set_policies(self, assignments: dict[int, str]) -> str:
        lua = lq.build_set_policies(assignments)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        if result.startswith("Error") or result.startswith("POLICIES_UNCHANGED|"):
            return result
        # Native policy application is asynchronous; only later matching
        # reads confirm it. An old frame never proves rejection.
        expected_slots: dict[int, str] = {}
        plan_count = None
        invalid_plan = False
        for line in lines:
            if line.startswith("POLICY_PLAN_COUNT|"):
                try:
                    if plan_count is not None:
                        invalid_plan = True
                    _, count = line.split("|")
                    plan_count = int(count)
                except ValueError:
                    invalid_plan = True
            elif line.startswith("POLICY_PLAN"):
                try:
                    _, slot, policy = line.split("|")
                    index = int(slot)
                    if index in expected_slots or not policy:
                        invalid_plan = True
                    expected_slots[index] = policy
                except ValueError:
                    invalid_plan = True
        complete_plan = (
            not invalid_plan and plan_count is not None and plan_count > 0
            and len(expected_slots) == plan_count
            and all(0 <= idx < plan_count for idx in expected_slots)
            and all(expected_slots.get(idx) == ("NONE" if policy.upper() == "NONE" else policy)
                    for idx, policy in assignments.items())
        )
        unconfirmed = (
            "POLICIES_UNCONFIRMED|Policy change submitted once; application not confirmed. "
            "Inspect get_policies before another change; do not automatically resend."
        )
        if not complete_plan:
            return unconfirmed + (
                "\nWARN:POLICY_PLAN_UNVERIFIED — final plan receipt missing or invalid."
            )
        mismatches = []
        try:
            async with asyncio.timeout(ACTION_CONFIRM_TIMEOUT):
                while True:
                    status = await self.get_policies()
                    slot_map = {s.slot_index: s.current_policy for s in status.slots}
                    mismatches = []
                    for idx, pol in expected_slots.items():
                        expected = None if pol.upper() == "NONE" else pol
                        actual = slot_map.get(idx)
                        if idx not in slot_map or actual != expected:
                            wanted = "EMPTY" if expected is None else pol
                            got = actual or "EMPTY"
                            mismatches.append(f"slot {idx} (wanted {wanted}, got {got})")
                    if set(slot_map) != set(expected_slots):
                        mismatches.append("slot layout differs from submitted plan")
                    if not mismatches:
                        return "POLICIES_SET|Final policy plan confirmed by subsequent readback."
                    await asyncio.sleep(ACTION_CONFIRM_INTERVAL)
        except (OSError, TimeoutError, LuaError, ValueError):
            pass
        if mismatches:
            unconfirmed += "\nLast read: " + ", ".join(mismatches)
        return unconfirmed

    # ------------------------------------------------------------------
    # Governor methods (InGame context)
    # ------------------------------------------------------------------

    async def get_governors(self) -> lq.GovernorStatus:
        lua = lq.build_governors_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_governors_response(lines)

    async def appoint_governor(self, governor_type: str) -> str:
        lua = lq.build_appoint_governor(governor_type)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def assign_governor(self, governor_type: str, city_id: int) -> str:
        lua = lq.build_assign_governor(governor_type, city_id)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def promote_governor(self, governor_type: str, promotion_type: str) -> str:
        lua = lq.build_promote_governor(governor_type, promotion_type)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        if "PROMOTED" in result:
            # Verify promotion actually applied (RequestPlayerOperation is async)
            verify = await self.conn.execute_write(
                f"local me = Game.GetLocalPlayer(); "
                f"local pGovs = Players[me]:GetGovernors(); "
                f'local gov = GameInfo.Governors["{governor_type}"]; '
                f'local promo = GameInfo.GovernorPromotions["{promotion_type}"]; '
                f"if gov and promo then "
                f"  local g = pGovs:GetGovernor(gov.Hash); "
                f"  if g and g:HasPromotion(promo.Index) then "
                f'    print("VERIFIED") '
                f"  else "
                f'    print("ERR:PROMOTION_FAILED|{promotion_type} was not applied") '
                f"  end "
                f"else "
                f'  print("ERR:LOOKUP_FAILED") '
                f"end; "
                f'print("{lq.SENTINEL}")'
            )
            if any("ERR:" in l for l in verify):
                return _action_result(verify)
        return result

    # ------------------------------------------------------------------
    # Promotion methods
    # ------------------------------------------------------------------

    async def get_unit_promotions(self, unit_id: int) -> lq.UnitPromotionStatus:
        unit_index = unit_id % 65536
        lua = lq.build_unit_promotions_query(unit_index)
        lines = await self.conn.execute_read(lua)
        return lq.parse_unit_promotions_response(lines)

    async def promote_unit(self, unit_id: int, promotion_type: str) -> str:
        unit_index = unit_id % 65536
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_promote_unit(unit_index, promotion_type)
        lines = await self.conn.execute_read(lua)  # GameCore context
        result = _action_result(lines)
        # GameCore SetPromotion doesn't clear the InGame NEEDS_PROMOTION
        # notification, which blocks end_turn until dismissed.
        # Use XP-threshold formula (matching end_turn handler) to decide
        # whether any unit still genuinely needs a promotion before dismissing.
        if not result.startswith("Error"):
            try:
                await self.conn.execute_write(
                    f"local me = Game.GetLocalPlayer(); "
                    f"local anyNeed = false; "
                    f"for i, u in Players[me]:GetUnits():Members() do "
                    f"  if u:GetX() ~= -9999 then "
                    f"    local ok, exp = pcall(function() return u:GetExperience() end); "
                    f"    if ok and exp then "
                    f"      local ui = GameInfo.Units[u:GetType()]; "
                    f'      local promClass = ui and ui.PromotionClass or ""; '
                    f'      if promClass ~= "" then '
                    f"        local promoCount = 0; "
                    f"        for p in GameInfo.UnitPromotions() do "
                    f"          if p.PromotionClass == promClass and exp:HasPromotion(p.Index) then "
                    f"            promoCount = promoCount + 1 "
                    f"          end "
                    f"        end; "
                    f"        local t1 = exp:GetExperienceForNextLevel(); "
                    f"        local xp = exp:GetExperiencePoints(); "
                    f"        local needed = t1 * (promoCount + 1) * (promoCount + 2) / 2; "
                    f"        if xp >= needed then "
                    f"          anyNeed = true "
                    f"        else "
                    f"          local stored = 0; "
                    f"          pcall(function() stored = exp:GetStoredPromotions() end); "
                    f"          if stored > 0 then "
                    f"            pcall(function() exp:ChangeStoredPromotions(-stored) end) "
                    f"          end "
                    f"        end "
                    f"      end "
                    f"    end "
                    f"  end "
                    f"  if anyNeed then break end "
                    f"end; "
                    f"if not anyNeed then "
                    f"  local list = NotificationManager.GetList(me); "
                    f"  if list then "
                    f"    for _, nid in ipairs(list) do "
                    f"      local e = NotificationManager.Find(me, nid); "
                    f"      if e and not e:IsDismissed() then "
                    f"        local bt = e:GetEndTurnBlocking(); "
                    f"        if bt and bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_UNIT_PROMOTION then "
                    f"          pcall(function() NotificationManager.SendActivated(me, nid) end); "
                    f"          pcall(function() NotificationManager.Dismiss(me, nid) end) "
                    f"        else "
                    f"          local tn = ''; "
                    f"          pcall(function() tn = e:GetTypeName() end); "
                    f"          if tn == 'NOTIFICATION_UNIT_PROMOTION_AVAILABLE' then "
                    f"            pcall(function() NotificationManager.Dismiss(me, nid) end) "
                    f"          end "
                    f"        end "
                    f"      end "
                    f"    end "
                    f"  end "
                    f"end; "
                    f'print("OK"); print("{lq.SENTINEL}")'
                )
            except Exception:
                pass  # non-fatal — end_turn blocker handler will catch it
        return result

    # ------------------------------------------------------------------
    # City-state / Envoy methods (InGame context)
    # ------------------------------------------------------------------

    async def get_city_states(self) -> lq.EnvoyStatus:
        lua = lq.build_city_states_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_city_states_response(lines)

    async def send_envoy(self, city_state_player_id: int) -> str:
        lua = lq.build_send_envoy(city_state_player_id)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        # _action_result already stripped the "OK:" prefix
        if result.startswith("ENVOY_SENT"):
            # Verify token actually decremented (async race condition workaround)
            await asyncio.sleep(0.1)
            try:
                verify_lines = await self.conn.execute_write(
                    f"local me = Game.GetLocalPlayer(); "
                    f"print(Players[me]:GetInfluence():GetTokensToGive()); "
                    f'print("{lq.SENTINEL}")'
                )
                if verify_lines and verify_lines[0].strip().lstrip("-").isdigit():
                    actual = int(verify_lines[0].strip())
                    result += f" (verified remaining: {actual})"
            except Exception:
                log.debug("Envoy verification failed", exc_info=True)
        return result

    # ------------------------------------------------------------------
    # Pantheon methods (InGame context)
    # ------------------------------------------------------------------

    async def get_pantheon_status(self) -> lq.PantheonStatus:
        lua = lq.build_pantheon_status_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_pantheon_status_response(lines)

    async def choose_pantheon(self, belief_type: str) -> str:
        lua = lq.build_choose_pantheon(belief_type)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    # ------------------------------------------------------------------
    # Religion founding methods (InGame context)
    # ------------------------------------------------------------------

    async def get_religion_founding_status(self) -> lq.ReligionFoundingStatus:
        lua = lq.build_religion_beliefs_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_religion_beliefs_response(lines)

    async def found_religion(
        self, religion_type: str, follower_belief: str, founder_belief: str
    ) -> str:
        lua = lq.build_found_religion(religion_type, follower_belief, founder_belief)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    # ------------------------------------------------------------------
    # Unit upgrade methods (InGame context)
    # ------------------------------------------------------------------

    async def check_unit_upgrade(self, unit_id: int) -> str:
        unit_index = unit_id % 65536
        lua = lq.build_unit_upgrade_query(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def upgrade_unit(self, unit_id: int) -> str:
        unit_index = unit_id % 65536
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_upgrade_unit(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    # ------------------------------------------------------------------
    # Dedications / Commemorations
    # ------------------------------------------------------------------

    async def get_dedications(self) -> lq.DedicationStatus:
        lua = lq.build_dedications_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_dedications_response(lines)

    async def choose_dedication(self, dedication_index: int) -> str:
        lua = lq.build_choose_dedication(dedication_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

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
        return lq.parse_wonder_advisor_response(lines)

    # ------------------------------------------------------------------
    # Tile purchase methods (InGame context)
    # ------------------------------------------------------------------

    async def get_purchasable_tiles(self, city_id: int) -> list[lq.PurchasableTile]:
        lua = lq.build_purchasable_tiles_query(city_id)
        lines = await self.conn.execute_write(lua)
        return lq.parse_purchasable_tiles_response(lines)

    async def purchase_tile(self, city_id: int, x: int, y: int) -> str:
        lua = lq.build_purchase_tile(city_id, x, y)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    # ------------------------------------------------------------------
    # Government change (InGame context)
    # ------------------------------------------------------------------

    async def change_government(self, government_type: str) -> str:
        lua = lq.build_change_government(government_type)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    # ------------------------------------------------------------------
    # Great People (InGame context)
    # ------------------------------------------------------------------

    async def get_great_people(self) -> list[lq.GreatPersonInfo]:
        lua = lq.build_great_people_query()
        lines = await self.conn.execute_write(lua)
        return lq.parse_great_people_response(lines)

    async def get_gp_advisor(self, unit_index: int) -> lq.GPAdvisorResult | None:
        lua = lq.build_gp_advisor_query(unit_index)
        lines = await self.conn.execute_write(lua)
        return lq.parse_gp_advisor_response(lines)

    async def recruit_great_person(self, individual_id: int) -> str:
        lua = lq.build_recruit_great_person(individual_id)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def patronize_great_person(
        self, individual_id: int, yield_type: str = "YIELD_GOLD"
    ) -> str:
        lua = lq.build_patronize_great_person(individual_id, yield_type)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def get_religion_status(self) -> lq.ReligionStatus:
        lines = await self.conn.execute_write(lq.build_religion_status_query())
        return lq.parse_religion_status_response(lines)

    async def reject_great_person(self, individual_id: int) -> str:
        lua = lq.build_reject_great_person(individual_id)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

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

    async def make_trade_route(
        self, unit_index: int, target_x: int, target_y: int
    ) -> str:
        lua = lq.build_make_trade_route(unit_index, target_x, target_y)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    # ------------------------------------------------------------------
    # Great Person activation (InGame context)
    # ------------------------------------------------------------------

    async def activate_great_person(self, unit_index: int) -> str:
        lua = lq.build_activate_great_person(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def spread_religion(self, unit_index: int) -> str:
        lua = lq.build_spread_religion(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    # ------------------------------------------------------------------
    # Trader teleport (InGame context)
    # ------------------------------------------------------------------

    async def teleport_to_city(
        self, unit_index: int, target_x: int, target_y: int
    ) -> str:
        lua = lq.build_teleport_to_city(unit_index, target_x, target_y)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    # ------------------------------------------------------------------
    # World Congress (InGame context)
    # ------------------------------------------------------------------

    async def get_world_congress(self) -> lq.WorldCongressStatus:
        lua = lq.build_world_congress_query()
        lines = await self.conn.execute_write(lua)
        if not any(line.startswith(("WC_STATUS|", "ERR:NO_WORLD_CONGRESS|")) for line in lines):
            raise LuaError("World Congress status is unknown")
        return lq.parse_world_congress_response(lines)

    async def open_world_congress(self) -> str:
        """Explicitly enter a verified nonempty voting UI, never cast votes."""
        if self._pending_load or self._pending_save:
            return "ERR:OUTCOME_UNKNOWN|Load/save must be confirmed before opening Congress."
        if self._pending_action is not None:
            await self.confirm_pending_action()
            if self._pending_action is not None:
                return self._pending_unknown("Congress was not opened")
        try:
            lines = await self.conn.execute_write(lq.build_open_world_congress())
        except (OSError, TimeoutError, LuaError) as exc:
            return (f"ERR:OUTCOME_UNKNOWN|Congress start may have executed: {exc}. "
                    "Inspect the existing start record and UI; no automatic resend.")
        result = _action_result(lines)
        if not any(line.startswith(("OK:WC_VOTING_OPEN|", "OK:WC_VOTING_ALREADY_OPEN|", "ERR:")) for line in lines):
            return "ERR:OUTCOME_UNKNOWN|Congress start returned no valid status; inspect without resending."
        return result

    async def vote_world_congress(
        self, resolution_hash: int, option: int, target_index: int, num_votes: int
    ) -> str:
        lua = lq.build_congress_vote(resolution_hash, option, target_index, num_votes)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def submit_congress(self) -> str:
        lua = lq.build_congress_submit()
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def queue_wc_votes(self, votes: list[dict]) -> str:
        """Submit one explicit native-stage plan, or report its existing record."""
        lua = lq.build_register_wc_voter(votes=votes)
        try:
            lines = await self.conn.execute_write(lua)
        except (OSError, TimeoutError, LuaError) as exc:
            return (f"ERR:OUTCOME_UNKNOWN|World Congress request may have executed: {exc}. "
                    "Use get_world_congress; no automatic vote/submission/continuation replay.")
        return _action_result(lines)

    # ------------------------------------------------------------------
    # City yield focus (InGame context)
    # ------------------------------------------------------------------

    async def set_city_focus(self, city_id: int, focus: str) -> str:
        lua = lq.build_set_yield_focus(city_id, focus)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

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
                net = sa.per_turn - sa.demand + sa.imported
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
                    net = s.per_turn - s.demand + s.imported
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
        if self._diplomacy_cleanup.pending:
            result += "\ndiplomacy_cleanup: native callback outcome unknown; dismiss_popup only confirms its receipt; no callback or trade will be replayed."
        return result

    async def end_turn(self) -> str:
        """End the turn with snapshot-diff event detection."""
        return await self._turn_progress.execute(self)

    async def dismiss_popup(self) -> str:
        """Dismiss any blocking popup or UI overlay."""
        from civ_mcp.game_lifecycle import dismiss_popup

        result = await dismiss_popup(self.conn, self._diplomacy_cleanup)
        if not self._diplomacy_cleanup.pending:
            self._trade_result_pending_cleanup = None
        return result

    async def list_saves(self) -> str:
        """List available save files."""
        from civ_mcp.game_lifecycle import list_saves

        return await list_saves(self.conn)

    async def save_game(self, save_name: str) -> str:
        """Save a confirmed active turn without resetting the current session."""
        from civ_mcp.game_lifecycle import _validate_save_name, save_game

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
        result = await save_game(self.conn, save_name)
        if not result.startswith("Saved:"):
            if "OUTCOME_UNKNOWN" in result:
                self._pending_save = True
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
        return f"{result} Civilization {identity[0]}, seed {identity[1]}, turn {after.turn}."

    @property
    def save_pending(self) -> bool:
        """A save acknowledgement or its saved-state verification is unresolved."""
        return self._pending_save

    @property
    def replacement_pending(self) -> bool:
        """A replacement is unconfirmed; save uncertainty is tracked separately."""
        return self._pending_load

    async def load_save(self, save_index: int) -> str:
        """Resolve only a displayed index, then use the single verified loader."""
        from civ_mcp.game_lifecycle import listed_save_name

        name = listed_save_name(save_index)
        if name is None:
            return "Error: No matching cached save index; call list_saves or use load_game_save with an explicit confirmed name."
        return await self.load_game_save(name)

    async def load_game_save(self, save_name: str) -> str:
        """Load once from MainMenu and confirm identity, readiness and snapshot."""
        return await self._replace_save(save_name)

    async def reload_game_save(self, save_name: str) -> str:
        """Replace through the native menu while preserving the process."""
        return await self._replace_save(save_name, return_to_menu=True)

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
            return "Error: Checkpoint completion and expected identity are unavailable; no restart performed."
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

    async def _replace_save(self, save_name: str, *, return_to_menu: bool = False, recovering: bool = False) -> str:
        """同一个连接负责加载与验证；确认窗口内不重复发送加载命令。"""
        from civ_mcp import checkpoints, game_lifecycle
        from civ_mcp.narrate import narrate_score_comparison

        error = game_lifecycle._validate_save_name(save_name)
        if error:
            return error
        if self._pending_load and not recovering:
            return "ERR:OUTCOME_UNKNOWN|An earlier load is unconfirmed; inspect state or use explicit recovery, not another load."
        if not recovering and (
            self._pending_save or self._pending_end_turn
            or self.pending_action_entity is not None
        ):
            return "ERR:OUTCOME_UNKNOWN|An action/save/end-turn is pending; confirm it or use explicit restart_and_load before replacing the session."
        expected = checkpoints.expected_checkpoint(save_name)
        if expected is None:
            return "Error: Checkpoint completion and expected identity are unavailable; no load sent."
        saved_score = checkpoints.expected_score(save_name)
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
                if checkpoints.expected_checkpoint(save_name) != expected:
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
                                if checkpoints.expected_checkpoint(save_name) != expected:
                                    return "ERR:OUTCOME_UNKNOWN|Checkpoint file changed during load; recovery not verified."
                                if save_name.removesuffix(".Civ6Save") == "benchmark_turn_1":
                                    ownership_error = checkpoints.begin_baseline_run()
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

    async def execute_lua(self, code: str, context: str = "gamecore") -> str:
        """Escape hatch: run arbitrary Lua code."""
        from civ_mcp.game_lifecycle import execute_lua

        return await execute_lua(self.conn, code, context)

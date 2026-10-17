"""High-level game state API with server-side narration.

Wraps GameConnection + Lua into typed queries, actions, verification and
human-readable results. This layer does not depend on MCP.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field


from civ_mcp import lua as lq
from civ_mcp.connection import GameConnection, LuaError
from civ_mcp.end_turn import TurnProgress
from civ_mcp.narrate import (
    narrate_combat_estimate,
    narrate_move_discoveries,
    narrate_settle_candidates,
    narrate_test_trade,
)


log = logging.getLogger(__name__)

ACTION_CONFIRM_TIMEOUT = 5.0
ACTION_CONFIRM_INTERVAL = 0.15


@dataclass
class _PendingAction:
    kind: str
    entity: tuple[str, int]
    query: str
    context: str = "read"
    success: str = ""
    identity: tuple[str, int] | None = None
    turn: int | None = None
    owner: int | None = None
    details: dict = field(default_factory=dict)
    observed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ActionScope:
    civilization: str
    seed: int
    turn: int
    owner: int

    @property
    def identity(self) -> tuple[str, int]:
        return self.civilization.removeprefix("CIVILIZATION_").lower(), self.seed

    def guard(self, code: str) -> str:
        # A fresh read alone cannot prevent a turn/world change before dispatch.
        # This guard must precede even witness/listener installation.
        return f'''-- ACTION_SUBMIT_SCOPE|{self.civilization}|{self.seed}|{self.turn}|{self.owner}
do
    local ok, scope = pcall(function()
        local owner = Game.GetLocalPlayer()
        return {{PlayerConfigurations[owner]:GetCivilizationTypeName(),
                GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
                Game.GetCurrentGameTurn(), owner}}
    end)
    if not ok or scope[1] ~= "{self.civilization}" or scope[2] ~= {self.seed}
            or scope[3] ~= {self.turn} or scope[4] ~= {self.owner} then
        print("ERR:ACTION_SCOPE_NO_SUBMIT|Native action scope changed or unavailable")
        print("{lq.SENTINEL}")
        return
    end
end
{code}'''


def _scoped_action_result(lines: list[str]) -> str:
    if any("ACTION_SCOPE_NO_SUBMIT" in line for line in lines):
        if lines == ["ERR:ACTION_SCOPE_NO_SUBMIT|Native action scope changed or unavailable"]:
            return _action_result(lines)
        return "Error: OUTCOME_UNKNOWN — incomplete or conflicting submission guard response"
    return _action_result(lines)



class GameState:
    """High-level async API for Civ 6 game state + actions."""

    def __init__(self, connection: GameConnection):
        self.conn = connection
        self.reset_session()

    def reset_session(self) -> None:
        """新游戏或显式读档后，从实际状态重新建立会话。"""
        self._pending_save = False
        self._pending_load = False
        self._pending_action: _PendingAction | None = None
        self._last_snapshot: lq.TurnSnapshot | None = None
        self._game_identity: tuple[str, int] | None = None
        self._turn_progress = TurnProgress()
        self._local_player_id = 0
        self._revealed_tiles: set[tuple[int, int]] | None = None

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
    def pending_action_summary(self) -> str | None:
        pending = self._pending_action
        if pending is None:
            return None
        return f"{pending.kind} {pending.entity[0]}:{pending.entity[1]} {pending.details}"

    @property
    def pending_action_entity(self) -> tuple[str, int] | None:
        if self._pending_action is None:
            return None
        kind, identifier = self._pending_action.entity
        return (kind, identifier % 65536 if kind == "unit" else identifier)

    async def _prepare_action_scope(self) -> _ActionScope | str:
        """Read submission identity without touching the narration baseline."""
        code = f'''
local ok, scope = pcall(function()
    local owner = Game.GetLocalPlayer()
    return {{PlayerConfigurations[owner]:GetCivilizationTypeName(),
            GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
            Game.GetCurrentGameTurn(), owner}}
end)
if ok then
    print("ACTION_SCOPE|" .. tostring(scope[1]) .. "|" .. tostring(scope[2])
          .. "|" .. tostring(scope[3]) .. "|" .. tostring(scope[4]))
end
print("{lq.SENTINEL}")
'''
        try:
            lines = await self.conn.execute_write(code)
            rows = [row.split("|") for row in lines if row.startswith("ACTION_SCOPE|")]
            if len(rows) != 1 or len(rows[0]) != 5:
                raise ValueError("missing or ambiguous native scope")
            _, civ, seed, turn, owner = rows[0]
            if not re.fullmatch(r"CIVILIZATION_[A-Z0-9_]+", civ):
                raise ValueError("invalid civilization")
            if not all(re.fullmatch(r"-?\d+", value) for value in (seed, turn, owner)):
                raise ValueError("invalid native scope numbers")
            scope = _ActionScope(civ, int(seed), int(turn), int(owner))
            if scope.turn < 0 or scope.owner < 0:
                raise ValueError("no local turn or player")
            if self._game_identity is not None and scope.identity != self._game_identity:
                raise ValueError("world changed; read the current game before acting")
            return scope
        except (OSError, TimeoutError, LuaError, ValueError) as exc:
            return f"Error: ACTION_SCOPE_UNAVAILABLE|{exc}; no action submitted."

    def _pending_unknown(self, reason: str = "confirmation deadline reached") -> str:
        pending = self._pending_action
        if pending is None:
            return "CONFIRMED|No pending action"
        observed = "; ".join(pending.observed) or "no confirmed observation"
        return (f"ERR:OUTCOME_UNKNOWN|{self.pending_action_summary}|{reason}|observed:{observed}. "
                "Use get_units/get_cities to confirm; do not resubmit. Explicit recovery is available if confirmation remains impossible.")

    async def confirm_pending_action(self, *, wait: bool = True) -> str:
        """Only observe a previously submitted action; never replay its mutation.

        The deadline includes connection and Lua time. Reconnection by itself
        cannot clear this state. Explicit session replacement is the only reset.
        """
        pending = self._pending_action
        if pending is None:
            return "CONFIRMED|No pending action"
        deadline = time.monotonic() + ACTION_CONFIRM_TIMEOUT
        try:
            async with asyncio.timeout(ACTION_CONFIRM_TIMEOUT):
                while True:
                    execute = self.conn.execute_write if pending.context == "write" else self.conn.execute_read
                    identity_probe = ""
                    if pending.identity is not None:
                        identity_probe = (
                            "local actionMe = Game.GetLocalPlayer(); "
                            "local actionCfg = PlayerConfigurations[actionMe]; "
                            'print("ACTION_IDENTITY|" .. actionCfg:GetCivilizationTypeName() .. "|" '
                            '.. tostring(GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")) .. "|" .. Game.GetCurrentGameTurn() .. "|" .. actionMe); '
                        )
                    pending.observed = await execute(identity_probe + pending.query)
                    confirmed = self._confirmed_action(pending)
                    if confirmed is not None:
                        self._pending_action = None
                        return confirmed
                    if not wait or time.monotonic() >= deadline:
                        return self._pending_unknown()
                    await asyncio.sleep(min(ACTION_CONFIRM_INTERVAL, max(0, deadline - time.monotonic())))
        except (OSError, TimeoutError, LuaError, ValueError) as exc:
            return self._pending_unknown(f"confirmation unavailable: {exc}")

    def _confirmed_action(self, pending: _PendingAction) -> str | None:
        if pending.identity is not None:
            rows = [line.split("|") for line in pending.observed if line.startswith("ACTION_IDENTITY|")]
            if len(rows) != 1 or len(rows[0]) != 5:
                return None
            fields = rows[0]
            identity = (fields[1].replace("CIVILIZATION_", "").lower(), int(fields[2]))
            if (identity != pending.identity or int(fields[3]) != pending.turn
                    or int(fields[4]) != pending.owner):
                return None
        if pending.kind == "production":
            target = pending.details.get("target")
            if target is not None:
                item = pending.details["item"]
                if f"PRODUCTION|{item}" not in pending.observed:
                    return None
                for line in pending.observed:
                    fields = line.split("|")
                    if len(fields) != 4 or fields[:2] != ["DISTRICT", item]:
                        continue
                    try:
                        position = tuple(float(value) for value in fields[2:])
                    except ValueError:
                        continue
                    if position == target:
                        return pending.success
            elif any(line.startswith("CONFIRMED|") for line in pending.observed):
                return pending.success
        if pending.kind == "attack":
            pre_hp = pending.details.get("pre_hp")
            post_hp = None
            target_line = None
            for line in pending.observed:
                if not line.startswith("UNIT|"):
                    continue
                fields = line.split("|")
                metadata = dict(field.split(":", 1) for field in fields[3:] if ":" in field)
                if metadata.get("owner") == str(pending.owner):
                    continue
                if pending.details.get("target_owner") is not None:
                    if metadata.get("owner") != str(pending.details["target_owner"]) or metadata.get("id") != str(pending.details["target_id"]):
                        continue
                post_hp = int(fields[2].split("/")[0])
                target_line = line
                break
            attacker = next((line for line in pending.observed if line.startswith("ATTACKER|")), "")
            metadata = dict(field.split(":", 1) for field in attacker.split("|")[1:] if ":" in field)
            attacks_before = pending.details.get("attacks_before")
            consumed = (attacks_before is not None and "attacks" in metadata
                        and int(metadata["attacks"]) < attacks_before and metadata.get("busy") == "false")
            changed = pre_hp is not None and post_hp is not None and pre_hp != post_hp
            core_status = next((line.split("|", 1)[1] for line in pending.observed if line.startswith("CORE_BUSY|")), metadata.get("busy", "unknown"))
            core_rows = [line for line in pending.observed if line.startswith("CORE_BUSY|")]
            city_started = core_rows == ["CORE_BUSY|false"] and self._city_attack_witness(pending)
            if (changed or consumed or city_started) and core_status == "false":
                damage = f"|damage dealt:{pre_hp-post_hp}" if pre_hp is not None and post_hp is not None else ""
                # Missing from the original tile is not evidence of death. The
                # consumed attack can be confirmed while the target stays unknown.
                observed = _format_attack_followup([target_line] if target_line else [], attacker_owner=pending.owner)
                return pending.success + damage + "\n  Post-combat: " + observed
        if pending.kind == "move":
            for line in pending.observed:
                if not line.startswith("POS|") or "GONE" in line:
                    continue
                fields = line.split("|")
                if len(fields) < 3:
                    continue
                x, y = int(fields[1]), int(fields[2])
                meta = dict(field.split(":", 1) for field in fields[3:] if ":" in field)
                moves = float(meta["moves"]) if "moves" in meta else None
                busy = meta.get("busy", "unknown")
                target = pending.details["target"]
                origin = pending.details.get("origin")
                # The pre-submission record survives a lost acknowledgement;
                # never install a listener retrospectively to fill that gap.
                witness = self._move_witness(pending, meta)
                if origin is None and witness is not None:
                    origin = witness[0]
                suffix = f"|now_at:{x},{y}"
                if origin:
                    suffix += f"|(moved dx:{x-origin[0]:+d} dy:{y-origin[1]:+d})"
                if (x, y) == target and busy == "false":
                    return pending.success + suffix
                if moves is not None and moves <= 0 and busy == "false":
                    return pending.success + suffix + "|STOPPED_MID_PATH (moves exhausted)"
                # Civ6's effective movement and ZOC facts can stop a segment
                # while ordinary moves remain. Path-preview turn labels alone
                # do not prove completion; missing native facts stay unknown.
                movement_moves = float(meta["movement_moves"]) if "movement_moves" in meta else None
                native_stop = None
                if movement_moves is not None and movement_moves <= 0:
                    native_stop = "native movement exhausted"
                elif meta.get("zoc") == "true":
                    native_stop = "entered zone of control"
                if origin is not None and (x, y) != origin and busy == "false" and native_stop:
                    if moves is not None:
                        suffix += f"|moves_remaining:{moves:g}"
                    return pending.success + suffix + f"|STOPPED_MID_PATH ({native_stop})"
                # A path preview alone never closes movement. This branch
                # additionally requires this request's native segment event,
                # full runtime ID, current identity/turn, and queued target.
                queued = [row for row in pending.observed if row.startswith("QUEUED_TARGET|")]
                steps = [row for row in pending.observed if row.startswith("PATH_STEP|")]
                if (witness is not None and witness[1] and witness[0] == origin
                        and len(queued) == 1 and len(steps) == 1
                        and origin is not None and (x, y) != origin and busy == "false"
                        and moves is not None and moves > 0
                        and queued[0] == f"QUEUED_TARGET|{target[0]}|{target[1]}"
                        and re.fullmatch(r"PATH_STEP\|-?\d+\|-?\d+\|turn:[1-9]\d*", steps[0])
                        and int(steps[0].rsplit(":", 1)[1]) > 1):
                    return (pending.success + suffix + f"|moves_remaining:{moves:g}"
                            "|STOPPED_MID_PATH (native segment complete; next step requires later turn)"
                            + "|" + "|".join(witness[2]))
                impassable = any(line.startswith(("DIAG|MOUNTAIN|", "DIAG|IMPASSABLE|")) for line in pending.observed)
                if (x, y) == origin and busy == "false" and impassable:
                    return pending.success + suffix + f"|BLOCKED ({lq.parse_blocked_diagnostic(pending.observed)})"
        if pending.kind == "found" and pending.details.get("position") is not None:
            if lq.parse_verify_city_at(pending.observed):
                owner = next((re.search(r"\|owner:(\d+)", line) for line in pending.observed if "CITY_EXISTS" in line), None)
                if owner is not None and int(owner[1]) == pending.owner:
                    return pending.success
        return None

    def _city_attack_witness(self, pending: _PendingAction) -> bool:
        """Only a pre-submission native record can confirm a missing target."""
        token = pending.details.get("city_witness_token")
        if pending.entity[0] != "city" or not token or pending.identity is None:
            return False
        records = [line.split("|") for line in pending.observed if line.startswith("CITY_ATTACK_WITNESS|")]
        sources = [line.split("|") for line in pending.observed if line.startswith("CITY_ATTACK_SOURCE|")]
        if len(records) != 1 or len(records[0]) != 17 or len(sources) != 1 or len(sources[0]) != 5:
            return False
        row, source = records[0], sources[0]
        try:
            if not all(re.fullmatch(r"\d+", row[index]) for index in (2, 3, 4, 5, 8, 11, 12)):
                return False
            if (row[1] != token or int(row[2]) != pending.owner or int(row[3]) != pending.entity[1]
                    or int(row[4]) != pending.owner or row[2:6] != source[1:5]
                    or (row[6].removeprefix("CIVILIZATION_").lower(), int(row[7])) != pending.identity
                    or int(row[8]) != pending.turn or tuple(map(int, row[9:11])) != pending.details["target"]
                    or int(row[11]) == pending.owner
                    or row[13:15] != ["started:true", "command_matches:true"]
                    or not row[15].startswith("rawCommand:") or not row[16].startswith("rawData:")):
                return False
            for key, index in (("target_owner", 11), ("target_id", 12)):
                if key in pending.details and int(row[index]) != pending.details[key]:
                    return False
            return True
        except ValueError:
            return False

    def _move_witness(self, pending: _PendingAction, meta: dict[str, str]) -> tuple[tuple[int, int], bool, list[str]] | None:
        """Validate the native request record, including the full runtime ID."""
        rows = [row.split("|") for row in pending.observed if row.startswith("MOVE_WITNESS|")]
        if len(rows) != 1 or len(rows[0]) != 14 or pending.identity is None or pending.turn is None:
            return None
        row = rows[0]
        try:
            identity = (row[4].replace("CIVILIZATION_", "").lower(), int(row[5]))
            if (row[1] != pending.details.get("witness_token")
                    or row[2] != str(pending.owner) or row[2] != meta.get("owner")
                    or row[3] != meta.get("id")
                    or int(row[3]) % 65536 != pending.entity[1] % 65536
                    or identity != pending.identity or int(row[6]) != pending.turn
                    or tuple(map(int, row[9:11])) != pending.details["target"]
                    or row[11] not in ("true", "false")):
                return None
            return (int(row[7]), int(row[8])), row[11] == "true", row[12:14]
        except ValueError:
            return None

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
        identity changes, all per-game cached state is reset.
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
                    pending = self._pending_action
                    pending_load = self._pending_load
                    pending_save = self._pending_save
                    self.reset_session()
                    self._pending_action = pending
                    self._pending_load = pending_load
                    self._pending_save = pending_save
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

        Tries InGame context first (full detection with UI checks).
        Falls back to GameCore context (read-only, survives defeat screen)
        when InGame fails — this catches victories that freeze the InGame UI.
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
    ) -> tuple[list[lq.BuilderTask], list[lq.BuilderInfo]]:
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
        lines = await self.conn.execute_read(lq.build_tech_civics_query())
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
        if (self._pending_action and self._pending_action.kind == "move"
                and self._pending_action.entity == ("unit", unit_index)
                and self._pending_action.details.get("target") == details["target"]):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        try:
            await self.dismiss_popup()
        except (OSError, TimeoutError, LuaError):
            pass
        scope = await self._prepare_action_scope()
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
        self._pending_action = pending
        try:
            lines = await self.conn.execute_write(scope.guard(lq.build_move_unit(
                unit_index, target_x, target_y, witness_token=details["witness_token"],
            )))
        except (OSError, TimeoutError, LuaError) as exc:
            return self._pending_unknown(f"submission acknowledgement unavailable: {exc}")
        result = _scoped_action_result(lines)
        if result.startswith(("MOVING_TO|", "CAPTURE_MOVE|")):
            pending.success = result
            origin = re.search(r"\|from:(-?\d+),(-?\d+)", result)
            if origin:
                pending.details["origin"] = tuple(map(int, origin.groups()))
        elif "OUTCOME_UNKNOWN" not in result:
            self._pending_action = None
            return result
        result = await self.confirm_pending_action()
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
        if (self._pending_action and self._pending_action.kind == "attack"
                and self._pending_action.entity == entity
                and self._pending_action.details.get("target") == (target_x, target_y)):
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
        scope = await self._prepare_action_scope()
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
        self._pending_action = pending
        build = lq.build_attack_unit if entity[0] == "unit" else lq.build_city_attack
        try:
            code = (build(entity[1], target_x, target_y, witness_token=city_token) if city_token else
                    build(entity[1], target_x, target_y))
            lines = await self.conn.execute_write(scope.guard(code))
        except (OSError, TimeoutError, LuaError) as exc:
            return self._pending_unknown(f"submission acknowledgement unavailable: {exc}")
        result = _scoped_action_result(lines)
        if result.startswith(("RANGE_ATTACK", "MELEE_ATTACK", "AIR_ATTACK", "CITY_RANGE_ATTACK")):
            if estimate and (result.startswith("AIR_ATTACK") or estimate.is_ranged != result.startswith("RANGE_ATTACK")):
                estimate_str = unknown_estimate
            pending.success = result
            pending.details["pre_hp"] = _extract_pre_hp(result)
            for name in ("target_owner", "target_id", "attacks_before"):
                match = re.search(rf"\|{name}:(\d+)", result)
                if match:
                    pending.details[name] = int(match[1])
            confirmed = await self.confirm_pending_action()
            return estimate_str + confirmed if self._pending_action is None else confirmed + ("\n" + estimate_str if estimate_str else "")
        if "OUTCOME_UNKNOWN" in result:
            return self._pending_unknown("submission acknowledgement missing")
        self._pending_action = None
        return result

    async def resolve_city_capture(self, action: str) -> str:
        lua = lq.build_resolve_city_capture(action)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def found_city(self, unit_index: int) -> str:
        if self._pending_action and self._pending_action.kind == "found" and self._pending_action.entity == ("unit", unit_index):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        try:
            await self.dismiss_popup()
        except (OSError, TimeoutError, LuaError):
            pass
        scope = await self._prepare_action_scope()
        if isinstance(scope, str):
            return scope
        pending = _PendingAction(
            "found", ("unit", unit_index), lq.build_unit_position_query(unit_index),
            identity=scope.identity, turn=scope.turn, owner=scope.owner,
        )
        self._pending_action = pending
        try:
            lines = await self.conn.execute_write(scope.guard(lq.build_found_city(unit_index)))
        except (OSError, TimeoutError, LuaError) as exc:
            return self._pending_unknown(f"submission acknowledgement unavailable: {exc}")
        result = _scoped_action_result(lines)
        if result.startswith("FOUNDED|"):
            x, y = map(int, result.split("|")[1].split(","))
            pending.query = lq.build_verify_city_at(x, y)
            pending.details = {"position": (x, y)}
            pending.success = result
            return await self.confirm_pending_action()
        if "OUTCOME_UNKNOWN" in result:
            return self._pending_unknown("submission acknowledgement missing; founding position unconfirmed")
        self._pending_action = None
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
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_fortify_unit(unit_index)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        if result.startswith("SLEEPING"):
            return "Unit is sleeping (this unit type cannot fortify)"
        return result

    async def skip_unit(self, unit_index: int) -> str:
        blocked = await self._action_gate(("unit", unit_index))
        if blocked:
            return blocked
        lua = lq.build_skip_unit(unit_index)
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

    async def skip_remaining_units(self) -> str:
        # First try to fortify/heal combat units (InGame context)
        fortify_result = ""
        try:
            lua_fort = lq.build_fortify_remaining_units()
            fort_lines = await self.conn.execute_write(lua_fort)
            fortify_result = _action_result(fort_lines)
        except Exception as e:
            log.debug("Fortify remaining failed: %s", e)
        # Then skip anything still with moves (GameCore context)
        lua = lq.build_skip_remaining_units()
        lines = await self.conn.execute_read(lua)
        skip_result = _action_result(lines)
        if fortify_result and not fortify_result.startswith("Error"):
            return f"{fortify_result}\n{skip_result}"
        return skip_result

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
        if (self._pending_action and self._pending_action.kind == "production"
                and self._pending_action.entity == ("city", city_id)
                and self._pending_action.details.get("item") == item_name
                and self._pending_action.details.get("target") == target):
            return await self.confirm_pending_action()
        blocked = await self._action_gate()
        if blocked:
            return blocked
        scope = await self._prepare_action_scope()
        if isinstance(scope, str):
            return scope
        pending = _PendingAction(
            "production", ("city", city_id), lq.build_verify_production(city_id, item_name, target),
            # "write" selects InGame; this confirmation Lua only reads facts.
            context="write" if target is not None else "read",
            identity=scope.identity, turn=scope.turn, owner=scope.owner, success=f"PRODUCING|{item_name}",
            details={"item": item_name, "target": target},
        )
        # Retain intent before dispatch: losing the acknowledgement does not
        # establish that CityManager.RequestOperation was never executed.
        self._pending_action = pending
        try:
            lines = await self.conn.execute_write(
                scope.guard(lq.build_produce_item(city_id, item_type, item_name, target_x, target_y))
            )
        except (OSError, TimeoutError, LuaError) as exc:
            return self._pending_unknown(f"submission acknowledgement unavailable: {exc}")
        result = _scoped_action_result(lines)
        if result.startswith("PRODUCING|"):
            pending.success = result
        elif not any("MAYBE:" in line for line in lines) and "OUTCOME_UNKNOWN" not in result:
            self._pending_action = None
            return result
        return await self.confirm_pending_action()

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

    async def get_diplomacy_sessions(self) -> list[lq.DiplomacySession]:
        lua = lq.build_diplomacy_session_query()
        lines = await self.conn.execute_write(lua)
        if not any(line == "NONE" or line.startswith("SESSION|") for line in lines):
            raise LuaError("Diplomacy session status is unknown")
        return lq.parse_diplomacy_sessions(lines)

    async def diplomacy_respond(self, other_player_id: int, response: str) -> str:
        # Capture dialogue text BEFORE response to detect goodbye phase
        pre_sessions = await self.get_diplomacy_sessions()
        pre_text = ""
        for s in pre_sessions:
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
        post_text = ""
        for s in post_sessions:
            if s.other_player_id == other_player_id:
                post_text = s.dialogue_text
                break

        if not post_sessions:
            # Session disappeared between checks (race condition)
            return f"OK:RESPONDED|{response.upper()}|SESSION_CLOSED"

        if post_text == pre_text:
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
        for s in post_sessions:
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
        lua = lq.build_send_diplo_action(other_player_id, action.upper())
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)

        # _action_result rewrites "ERR:" to "Error: " — only schedule the
        # background UI cleanup when the declaration actually succeeded.
        if is_war and not result.startswith("Error:"):
            # War session left open for ~8s so the leader animation plays.
            # Background task will close session + dismiss DiplomacyActionView.
            asyncio.create_task(self._cleanup_war_diplomacy(other_player_id))

        return result

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

    async def get_pending_deals(self) -> list[lq.PendingDeal]:
        lua = lq.build_pending_deals_query()
        lines = await self.conn.execute_write(lua)
        if not any(line == "DEALS_COMPLETE" for line in lines):
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
        lua = lq.build_propose_trade(other_player_id, offer_items, request_items)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        # Dismiss diplomacy UI left open by the trade session.
        # After CloseSession, the game transitions DiplomacyActionView to
        # OVERVIEW_MODE (intel screen). Need a brief delay for the C++ UI
        # state machine to settle, then dismiss it in a separate call.
        await asyncio.sleep(0.3)
        try:
            await self.conn.execute_write(
                'pcall(function() ContextPtr:LookUpControl("/InGame/DiplomacyActionView"):SetHide(true) end) '
                "pcall(function() Events.HideLeaderScreen() end) "
                "LuaEvents.DiplomacyActionView_ShowIngameUI() "
                f'print("{lq.SENTINEL}")'
            )
        except Exception:
            pass
        return result

    async def test_trade(
        self,
        other_player_id: int,
        offer_items: list[dict],
        request_items: list[dict],
    ) -> str:
        lua = lq.build_test_trade(other_player_id, offer_items, request_items)
        lines = await self.conn.execute_write(lua)
        result = lq.parse_test_trade_response(lines)
        return narrate_test_trade(result)

    async def propose_peace(self, other_player_id: int) -> str:
        lua = lq.build_propose_peace(other_player_id)
        lines = await self.conn.execute_write(lua)
        result = _action_result(lines)
        if result.startswith("Error"):
            return result
        # War state is async — verify with a second round-trip
        verify_lines = await self.conn.execute_write(
            lq.build_check_war_state(other_player_id)
        )
        at_peace = any("AT_PEACE" in l for l in verify_lines)
        name = result.split("|", 1)[1] if "|" in result else f"player {other_player_id}"
        if at_peace:
            return f"ACCEPTED|Peace established with {name}"
        else:
            return f"REJECTED|{name} rejected your peace offer"

    async def form_alliance(self, other_player_id: int, alliance_type: str) -> str:
        lua = lq.build_form_alliance(other_player_id, alliance_type.upper())
        lines = await self.conn.execute_write(lua)
        return _action_result(lines)

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
        if not result.startswith("Error"):
            # Post-verify: RequestPolicyChanges can silently no-op (e.g. during era transitions)
            status = await self.get_policies()
            slot_map = {s.slot_index: s.current_policy for s in status.slots}
            mismatches = []
            for idx, pol in assignments.items():
                expected = None if pol.upper() == "NONE" else pol
                actual = slot_map.get(idx)
                if actual != expected:
                    wanted = "EMPTY" if expected is None else pol
                    got = actual or "EMPTY"
                    mismatches.append(f"slot {idx} (wanted {wanted}, got {got})")
            if mismatches:
                result += (
                    f"\nWARN:SILENT_FAILURE — engine rejected: {', '.join(mismatches)}. "
                    "Try a different policy or retry next turn."
                )
        return result

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
                        message=f"Your {ub.name} ({ub.unit_type}) was killed! Last seen at ({ub.x},{ub.y}).",
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
                lines.append(f"  * {n.message}{hint}")

        if info_notifs:
            lines.append("")
            lines.append("== Notifications ==")
            for n in info_notifs:
                lines.append(f"  - {n.message}")

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
        return "\n".join([report, *limited])

    async def end_turn(self) -> str:
        """End the turn with snapshot-diff event detection."""
        return await self._turn_progress.execute(self)

    async def dismiss_popup(self) -> str:
        """Dismiss any blocking popup or UI overlay."""
        from civ_mcp.game_lifecycle import dismiss_popup

        return await dismiss_popup(self.conn)

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

    async def load_save(self, save_index: int) -> str:
        """Load a save by index; discard the old world's cached state."""
        from civ_mcp.game_lifecycle import load_save
        result = await load_save(self.conn, save_index)
        if not result.startswith(("Error", "ERR", "FAILED")):
            self.reset_session()
            await self.conn.disconnect()
        return result

    async def load_game_save(self, save_name: str) -> str:
        """Submit a native main-menu load; discard caches if submitted or unknown."""
        from civ_mcp.game_lifecycle import load_game_save
        result = await load_game_save(self.conn, save_name)
        if result.startswith(("Loading", "ERR:OUTCOME_UNKNOWN")):
            self.reset_session()
        return result


    async def execute_lua(self, code: str, context: str = "gamecore") -> str:
        """Escape hatch: run arbitrary Lua code."""
        from civ_mcp.game_lifecycle import execute_lua

        return await execute_lua(self.conn, code, context)


def _action_result(lines: list[str]) -> str:
    """Parse OK:/ERR: prefixed action responses.

    Scans all lines for the first OK:/ERR: prefix, since LuaEvent
    callbacks (e.g. ShowIngameUI → BulkHide debug prints) can inject
    spurious output before the actual result line.
    """
    if not lines:
        return "Error: OUTCOME_UNKNOWN — no action confirmation; read game state before retrying."
    for line in lines:
        if line.startswith("OK:"):
            return line[3:]
        if line.startswith("ERR:"):
            return f"Error: {line[4:]}"
    return "Error: OUTCOME_UNKNOWN — no action confirmation; read game state before retrying.\n" + "\n".join(lines)


def _format_attack_followup(lines: list[str], attacker_owner: int = 0) -> str:
    """Format the GameCore follow-up read after an attack.

    Filters out units belonging to ``attacker_owner`` so that after a melee
    kill (where the attacker moves onto the target tile) we don't misreport
    our own unit's HP as the defender's.

    Also includes city wall/garrison HP when attacking a walled city.
    """
    parts = []
    for line in lines:
        if line.startswith("UNIT|"):
            fields = line.split("|")
            if len(fields) >= 4:
                # fields: UNIT|TYPE|hp/max|owner:N
                owner_str = fields[3]  # "owner:N"
                try:
                    owner_id = int(owner_str.split(":")[1])
                except (IndexError, ValueError):
                    owner_id = -1
                label = "(yours) " if owner_id == attacker_owner else ""
                parts.append(f"{label}{fields[1]} {fields[2]}")
            elif len(fields) >= 3:
                parts.append(f"{fields[1]} {fields[2]}")
    city_def = _extract_city_defense(lines)
    if city_def:
        wall_hp, wall_max, gar_hp, gar_max = city_def
        if wall_max > 0:
            parts.append(f"Walls {wall_hp}/{wall_max}")
        if gar_max > 0:
            parts.append(f"City garrison {gar_hp}/{gar_max}")
    if not parts:
        return "Target state unknown (not visible at the original tile; elimination unconfirmed)"
    return ", ".join(parts)


def _extract_pre_hp(result: str) -> int | None:
    """Extract pre-attack enemy HP from attack result line."""
    import re

    # Ranged/city: pre_hp:80/100
    m = re.search(r"pre_hp:(\d+)/", result)
    if m:
        return int(m.group(1))
    # Melee: enemy HP:100 -> 80/100
    m = re.search(r"enemy HP:(\d+) ->", result)
    if m:
        return int(m.group(1))
    return None


def _extract_post_hp(followup_lines: list[str], attacker_owner: int = 0) -> int | None:
    """Extract post-combat *enemy* HP from followup query lines.

    Followup format: UNIT|UNIT_TYPE|hp/max|owner:N
    Skips units belonging to ``attacker_owner`` (after melee kill, attacker
    occupies the target tile and would otherwise be misread as defender).
    Returns HP of first enemy unit found (None if eliminated).
    """
    for line in followup_lines:
        if line.startswith("UNIT|"):
            parts = line.split("|")
            if len(parts) >= 4:
                try:
                    owner_id = int(parts[3].split(":")[1])
                except (IndexError, ValueError):
                    owner_id = -1
                if owner_id == attacker_owner:
                    continue  # our unit, not the target
            if len(parts) >= 3:
                hp_part = parts[2].split("/")[0]
                try:
                    return int(hp_part)
                except ValueError:
                    pass
    return None


def _extract_city_defense(
    followup_lines: list[str],
) -> tuple[int, int, int, int] | None:
    """Extract wall and garrison HP from CITY_DEF followup line.

    Returns ``(wall_hp, wall_max, garrison_hp, garrison_max)`` or *None*
    when the target tile has no city defenses.
    """
    for line in followup_lines:
        if line.startswith("CITY_DEF|"):
            # CITY_DEF|wall:74/100|garrison:197/200
            wall_hp = wall_max = gar_hp = gar_max = 0
            for part in line.split("|")[1:]:
                if part.startswith("wall:"):
                    hp, mx = part[5:].split("/")
                    wall_hp, wall_max = int(hp), int(mx)
                elif part.startswith("garrison:"):
                    hp, mx = part[9:].split("/")
                    gar_hp, gar_max = int(hp), int(mx)
            return (wall_hp, wall_max, gar_hp, gar_max)
    return None

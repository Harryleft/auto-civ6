"""Single submission and evidence-based confirmation for tracked game actions.

This module owns the pending intent. A transport acknowledgement is neither
completion nor permission to replay; each action keeps its native evidence.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from civ_mcp import lua as lq
from civ_mcp.connection import GameConnection, LuaError


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
    confirmed: str | None = None


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


class ActionLifecycle:
    def __init__(self, connection: GameConnection):
        self.conn = connection
        self.pending: _PendingAction | None = None

    def matches(self, kind: str, entity: tuple[str, int], **intent) -> bool:
        pending = self.pending
        return bool(pending and pending.kind == kind and pending.entity == entity
                    and all(pending.details.get(key) == value for key, value in intent.items()))

    async def submit(self, pending: _PendingAction, scope: _ActionScope, code: str,
                     *, timeout: float, interval: float) -> str:
        # Recheck after asynchronous preparation. Two callers can both pass
        # GameState's gate while reading scope, but only one may submit.
        if self.pending is not None:
            same_intent = (self.pending.kind == pending.kind
                           and self.pending.entity == pending.entity
                           and all(self.pending.details.get(key) == pending.details.get(key)
                                   for key in ("target", "item")))
            confirmed = await self.confirm(timeout=timeout, interval=interval)
            if same_intent:
                return confirmed
            if self.pending is not None:
                return self.unknown("dependent action was not submitted")
        self.pending = pending
        try:
            lines = await self.conn.execute_write(scope.guard(code))
        except (OSError, TimeoutError, LuaError) as exc:
            return pending.confirmed or self.unknown(f"submission acknowledgement unavailable: {exc}")
        result = _scoped_action_result(lines)
        accepted = {
            "move": ("MOVING_TO|", "CAPTURE_MOVE|"),
            "attack": ("RANGE_ATTACK", "MELEE_ATTACK", "AIR_ATTACK", "CITY_RANGE_ATTACK"),
            "found": ("FOUNDED|",),
            "production": ("PRODUCING|",),
            "skip": ("READINESS_REQUESTED|",),
            "fortify": ("READINESS_REQUESTED|",),
            "cancel_move": ("CANCEL_MOVE_REQUESTED|",),
        }[pending.kind]
        if result.startswith(accepted):
            pending.success = result
            if pending.kind == "move":
                origin = re.search(r"\|from:(-?\d+),(-?\d+)", result)
                if origin:
                    pending.details["origin"] = tuple(map(int, origin.groups()))
            elif pending.kind == "attack":
                pending.details["pre_hp"] = _extract_pre_hp(result)
                for name in ("target_owner", "target_id", "attacks_before"):
                    match = re.search(rf"\|{name}:(\d+)", result)
                    if match:
                        pending.details[name] = int(match[1])
            elif pending.kind == "found":
                position = tuple(map(int, result.split("|")[1].split(",")))
                pending.query = lq.build_verify_city_at(*position)
                pending.details["position"] = position
        elif "OUTCOME_UNKNOWN" not in result and not (
                pending.kind == "production" and any("MAYBE:" in line for line in lines)):
            if self.pending is pending:
                self.pending = None
            return result
        elif pending.kind in ("attack", "found"):
            reason = "submission acknowledgement missing"
            if pending.kind == "found":
                reason += "; founding position unconfirmed"
            return self.unknown(reason)
        # Keep the captured intent when a concurrent observer completed it
        # before its acknowledgement arrived; never report a different action.
        return await self._confirm(pending, wait=True, timeout=timeout, interval=interval)

    @property
    def summary(self) -> str | None:
        pending = self.pending
        if pending is None:
            return None
        return f"{pending.kind} {pending.entity[0]}:{pending.entity[1]} {pending.details}"

    @property
    def entity(self) -> tuple[str, int] | None:
        if self.pending is None:
            return None
        kind, identifier = self.pending.entity
        return (kind, identifier % 65536 if kind == "unit" else identifier)

    async def prepare_scope(self, known_identity: tuple[str, int] | None) -> _ActionScope | str:
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
            if known_identity is not None and scope.identity != known_identity:
                raise ValueError("world changed; read the current game before acting")
            return scope
        except (OSError, TimeoutError, LuaError, ValueError) as exc:
            return f"Error: ACTION_SCOPE_UNAVAILABLE|{exc}; no action submitted."

    def unknown(self, reason: str = "confirmation deadline reached") -> str:
        pending = self.pending
        if pending is None:
            return "CONFIRMED|No pending action"
        observed = "; ".join(pending.observed) or "no confirmed observation"
        return (f"ERR:OUTCOME_UNKNOWN|{self.summary}|{reason}|observed:{observed}. "
                "Use get_units/get_cities to confirm; do not resubmit. Explicit recovery is available if confirmation remains impossible.")

    async def confirm(self, *, wait: bool = True, timeout: float = 5.0, interval: float = 0.15) -> str:
        """Only observe a previously submitted action; never replay its mutation.

        The deadline includes connection and Lua time. Reconnection by itself
        cannot clear this state. Explicit session replacement is the only reset.
        """
        pending = self.pending
        if pending is None:
            return "CONFIRMED|No pending action"
        return await self._confirm(pending, wait=wait, timeout=timeout, interval=interval)

    async def _confirm(self, pending: _PendingAction, *, wait: bool, timeout: float, interval: float) -> str:
        deadline = time.monotonic() + timeout
        try:
            async with asyncio.timeout(timeout):
                while True:
                    if pending.confirmed is not None:
                        return pending.confirmed
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
                        pending.confirmed = confirmed
                        if self.pending is pending:
                            self.pending = None
                        return confirmed
                    if not wait or time.monotonic() >= deadline:
                        return self.unknown()
                    await asyncio.sleep(min(interval, max(0, deadline - time.monotonic())))
        except (OSError, TimeoutError, LuaError, ValueError) as exc:
            return pending.confirmed or self.unknown(f"confirmation unavailable: {exc}")

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
        if pending.kind in ("skip", "fortify"):
            rows = [row.split("|") for row in pending.observed if row.startswith("READINESS|")]
            if len(rows) != 1 or len(rows[0]) != 7:
                return None
            row = rows[0]
            if (row[1] != pending.details["witness_token"] or row[2] != str(pending.owner)
                    or not row[3].isdigit() or int(row[3]) % 65536 != pending.entity[1] % 65536
                    or row[4:6] != ["ready:false", "busy:false"]):
                return None
            label = row[6].removeprefix("result:")
            if label in ({"SKIPPED"} if pending.kind == "skip" else {"FORTIFIED", "SLEEPING"}):
                return f"{label}|unit:{row[3]}|ready:false"
        if pending.kind == "cancel_move":
            rows = [row.split("|") for row in pending.observed if row.startswith("CANCEL_MOVE|")]
            if len(rows) != 1 or len(rows[0]) != 9:
                return None
            row = rows[0]
            if (row[1] == pending.details["witness_token"] and row[2] == str(pending.owner)
                    and row[3].isdigit() and int(row[3]) % 65536 == pending.entity[1] % 65536
                    and re.fullmatch(r"from:-?\d+,-?\d+", row[4]) and row[5] == row[4].replace("from:", "now:", 1)
                    and row[6:] == ["queue:none", "busy:false", "entered:true"]):
                return f"MOVE_CANCELLED|unit:{row[3]}|at:{row[4][5:]}|Position retained; choose the next unit intent separately."
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
                # Completion belongs to the submitted segment. A new path
                # may backtrack or disappear after units block its route;
                # its turn labels cannot invalidate the native event.
                # Keep the full request identity and queued target checks.
                queued = [row for row in pending.observed if row.startswith("QUEUED_TARGET|")]
                if (witness is not None and witness[1] and witness[0] == origin
                        and len(queued) == 1
                        and origin is not None and (x, y) != origin and busy == "false"
                        and moves is not None and moves > 0
                        and queued[0] == f"QUEUED_TARGET|{target[0]}|{target[1]}"):
                    return (pending.success + suffix + f"|moves_remaining:{moves:g}"
                            "|STOPPED_MID_PATH (native segment complete)"
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

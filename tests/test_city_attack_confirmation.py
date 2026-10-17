"""A prearmed, scoped native command event confirms the shot, not a kill.

The raw CityCommandStarted callback is in shipped ActionPanel.lua:844.
No count/CanAttack/empty-target heuristic is substituted for this evidence.
"""
import asyncio
import re

import pytest

from civ_mcp.game_state import GameState


class CityAttackEngine:
    def __init__(self, *, witness=True):
        self.submissions = []
        self.witness = witness
        self.token = "missing-prearmed-token"
        self.ack_error = None
        self.read_error = None
        self.ack_rows = None
        self.observations = []

    async def execute_write(self, code):
        if 'print("ACTION_SCOPE|' in code:
            return ["ACTION_SCOPE|CIVILIZATION_SUMERIA|719665280|79|0"]
        if "CityManager.RequestCommand" in code:
            self.submissions.append(code)
            token = re.search(r'token="([a-f0-9]+)"', code)
            if token:
                self.token = token[1]
            if self.ack_error:
                raise self.ack_error
            if self.ack_rows is not None:
                return self.ack_rows
            return ["OK:CITY_RANGE_ATTACK|Uruk -> UNIT_WARRIOR@18,19|pre_hp:13/100"
                    "|target_owner:2|target_id:1769487"]
        if self.read_error:
            raise self.read_error
        self.observations.append(code)
        rows = ["ACTION_IDENTITY|CIVILIZATION_SUMERIA|719665280|79|0",
                "CORE_BUSY|false", "EMPTY"]
        if self.witness:
            # Proposed wire contract: scoped native CityCommandStarted facts.
            # Listener validates native RANGE_ATTACK; raw iData1 stays untyped.
            rows.append("CITY_ATTACK_SOURCE|0|65536|0|65536")
            rows.append(f"CITY_ATTACK_WITNESS|{self.token}|0|65536|0|65536"
                        "|CIVILIZATION_SUMERIA|719665280|79|18|19|2|1769487"
                        "|started:true|command_matches:true|rawCommand:sample|rawData:untyped")
        return rows


def test_native_city_command_witness_confirms_shot_without_claiming_missing_target_killed(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, "ACTION_CONFIRM_TIMEOUT", 0.01)
    engine = CityAttackEngine()
    state = GameState(engine)
    result = asyncio.run(state.city_attack(65536, 18, 19))
    assert "OUTCOME_UNKNOWN" not in result, result
    assert "CITY_RANGE_ATTACK" in result
    assert "Target state unknown" in result
    assert "killed" not in result.lower()
    assert "damage dealt:" not in result
    assert state.pending_action_summary is None
    assert len(engine.submissions) == 1


def test_empty_target_and_idle_without_native_witness_remain_unknown(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, "ACTION_CONFIRM_TIMEOUT", 0.01)
    engine = CityAttackEngine(witness=False)
    state = GameState(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.city_attack(65536, 18, 19))
    assert state.pending_action_summary
    assert len(engine.submissions) == 1


def test_non_numeric_city_center_identity_cannot_confirm_shot(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, "ACTION_CONFIRM_TIMEOUT", 0.01)
    class MalformedCenter(CityAttackEngine):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            result = []
            for row in rows:
                fields = row.split("|")
                if fields[0] == "CITY_ATTACK_SOURCE":
                    fields[4] = "NaN"
                if fields[0] == "CITY_ATTACK_WITNESS":
                    fields[5] = "NaN"
                result.append("|".join(fields))
            return result
    state = GameState(MalformedCenter())
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.city_attack(65536, 18, 19))
    assert state.pending_action_summary


def test_conflicting_core_status_does_not_confirm_native_started_command(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, "ACTION_CONFIRM_TIMEOUT", 0.01)
    class ConflictingCore(CityAttackEngine):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if "ACTION_IDENTITY|" in code:
                rows.append("CORE_BUSY|true")
            return rows
    state = GameState(ConflictingCore())
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.city_attack(65536, 18, 19))
    assert state.pending_action_summary


@pytest.fixture
def fast_confirmation(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, "ACTION_CONFIRM_TIMEOUT", 0.01)


@pytest.mark.parametrize("index,value", [
    (1, "old-token"), (2, "1"), (3, "131072"), (4, "1"), (5, "131072"),
    (6, "CIVILIZATION_ROME"), (7, "11"), (8, "80"), (9, "17"), (10, "20"),
    (11, "3"), (12, "1835023"), (13, "started:false"), (14, "command_matches:false"),
    (15, "unknown-command"), (16, "unknown-data"),
])
def test_foreign_old_or_incomplete_native_record_cannot_confirm_shot(fast_confirmation, index, value):
    class AlteredRecord(CityAttackEngine):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            result = []
            for row in rows:
                if row.startswith("CITY_ATTACK_WITNESS|"):
                    fields = row.split("|")
                    fields[index] = value
                    row = "|".join(fields)
                result.append(row)
            return result
    engine = AlteredRecord()
    state = GameState(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.city_attack(65536, 18, 19))
    assert state.pending_action_summary and len(engine.submissions) == 1


@pytest.mark.parametrize("prefix", ["CITY_ATTACK_WITNESS|", "CITY_ATTACK_SOURCE|"])
@pytest.mark.parametrize("alteration", ["missing", "duplicate", "truncated"])
def test_missing_duplicate_or_truncated_evidence_keeps_shot_pending(fast_confirmation, prefix, alteration):
    class BadRows(CityAttackEngine):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            matching = [row for row in rows if row.startswith(prefix)]
            if not matching:
                return rows
            if alteration == "duplicate":
                return rows + matching
            return [prefix + "truncated" if row.startswith(prefix) else row for row in rows] if alteration == "truncated" else [row for row in rows if not row.startswith(prefix)]
    state = GameState(BadRows())
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.city_attack(65536, 18, 19))
    assert state.pending_action_summary


@pytest.mark.parametrize("status", [None, "true", "unknown"])
def test_native_started_without_fresh_idle_remains_unknown(fast_confirmation, status):
    class NonIdle(CityAttackEngine):
        async def execute_write(self, code):
            rows = [row for row in await super().execute_write(code) if not row.startswith("CORE_BUSY|")]
            if "ACTION_IDENTITY|" in code and status is not None:
                rows.append("CORE_BUSY|" + status)
            return rows
    state = GameState(NonIdle())
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.city_attack(65536, 18, 19))


def test_late_event_is_only_read_without_shot_replay(fast_confirmation):
    engine = CityAttackEngine(witness=False)
    state = GameState(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.city_attack(65536, 18, 19))
    engine.witness = True
    assert "OUTCOME_UNKNOWN" not in asyncio.run(state.city_attack(65536, 18, 19))
    assert state.pending_action_summary is None and len(engine.submissions) == 1


@pytest.mark.parametrize("loss", ["ack", "read", "cancel_ack", "cancel_read"])
def test_lost_reply_or_cancelled_dispatch_retains_prearmed_record_without_replay(fast_confirmation, loss):
    engine = CityAttackEngine()
    exception = asyncio.CancelledError() if loss.startswith("cancel") else ConnectionError("lost")
    setattr(engine, "ack_error" if loss.endswith("ack") else "read_error", exception)
    state = GameState(engine)
    if loss.startswith("cancel"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(state.city_attack(65536, 18, 19))
    else:
        assert "OUTCOME_UNKNOWN" in asyncio.run(state.city_attack(65536, 18, 19))
    assert state.pending_action_summary
    engine.ack_error = engine.read_error = None
    result = asyncio.run(state.city_attack(65536, 18, 19))
    assert "CITY_RANGE_ATTACK" in result and "OUTCOME_UNKNOWN" not in result
    assert "Target state unknown" in result and "killed" not in result.lower()
    assert state.pending_action_summary is None and len(engine.submissions) == 1


def test_confirmed_request_cleans_only_its_listener_and_arms_before_one_shot(fast_confirmation):
    engine = CityAttackEngine()
    state = GameState(engine)
    assert "OUTCOME_UNKNOWN" not in asyncio.run(state.city_attack(65536, 18, 19))
    code = engine.submissions[0]
    assert code.index("ACTION_SCOPE_NO_SUBMIT") < code.index("Events.CityCommandStarted.Add(q.handler)") < code.index("CityManager.RequestCommand")
    assert code.count("CityManager.RequestCommand") == 1
    assert "cityID ~= q.cityId" in code and "districtID ~= q.districtId" in code
    assert "districtOwnerID ~= q.districtOwner" in code and "commandType ~= q.command" in code
    assert "Game.GetCurrentGameTurn() ~= q.turn" in code
    assert "targetId=enemy:GetID()" in code
    handler = code[code.index("q.handler = function"):code.index("local armed = pcall")]
    assert "enemy:GetID()" not in handler  # The target is never reconstructed after dispatch.
    assert "q.rawCommand, q.rawData = tostring(commandType), tostring(iData1)" in code
    assert "Events.CityCommandStarted.Remove(q.handler)" in code
    assert "RemoveAll" not in code and ".Clear(" not in code
    for error in ("ERR:NO_ENEMY", "ERR:NO_WALLS", "ERR:CANNOT_ATTACK"):
        assert code.index(error) < code.index("Events.CityCommandStarted.Add(q.handler)")
    followup = engine.observations[-1]
    assert "RequestCommand" not in followup and ".Add(" not in followup
    assert "q.closed = true" in followup and "Events.CityCommandStarted.Remove(q.handler)" in followup
    assert "center:GetID() ~= q.districtId" in followup and "center:GetCity():GetID() ~= q.cityId" in followup
    assert "q.targetX ~= 18 or q.targetY ~= 19" in followup


def test_definite_pre_submission_rejection_does_not_keep_action_pending(fast_confirmation):
    engine = CityAttackEngine()
    engine.ack_rows = ["ERR:CITY_ATTACK_WITNESS_UNAVAILABLE|Native command witness unavailable; no shot submitted."]
    state = GameState(engine)
    assert "CITY_ATTACK_WITNESS_UNAVAILABLE" in asyncio.run(state.city_attack(65536, 18, 19))
    assert state.pending_action_summary is None


def test_lost_ack_cannot_accept_a_record_targeting_our_own_unit(fast_confirmation):
    class WrongTargetOwner(CityAttackEngine):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            result = []
            for row in rows:
                if row.startswith("CITY_ATTACK_WITNESS|"):
                    fields = row.split("|")
                    fields[11] = "0"
                    row = "|".join(fields)
                result.append(row)
            return result
    engine = WrongTargetOwner()
    engine.ack_error = ConnectionError("ack lost")
    state = GameState(engine)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.city_attack(65536, 18, 19))
    engine.ack_error = None
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.confirm_pending_action(wait=False))
    assert state.pending_action_summary and len(engine.submissions) == 1

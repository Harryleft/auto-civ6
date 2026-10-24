"""Fixed-seat experiment: ownership, unknown outcomes, and native handoff."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def agent(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("hotseat_agent_test", Path(__file__).parents[1] / "scripts/hotseat_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "evidence").mkdir()
    module.run = tmp_path
    module.seat = 0

    async def logged(_ctx, _tool, _params, fn):
        return await fn()

    monkeypatch.setattr(module, "original_logged", logged)
    return module


class Connection:
    def __init__(self, *, local=0, human=True):
        self.local = local
        self.human = human
        self.closed = 0
        self.sent = 0

    async def execute_write(self, code):
        if 'print("HS|' in code:
            return [f"HS|true|{self.local}|1|false|true|{str(self.human).lower()}|true|-1896272000|CIVILIZATION_SUMERIA|CIVILIZATION_SWEDEN"]
        if 'print("CAN_END|' in code:
            return ["CAN_END|true"]
        if "UI.RequestAction" in code:
            self.sent += 1
            self.local = 1
            return ["HS_END_SENT"]
        raise AssertionError(code)

    async def disconnect(self):
        self.closed += 1


def context(conn):
    game = SimpleNamespace(conn=conn, pending_action_summary=None, save_pending=False,
                           replacement_pending=False, turn_pending=False)
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=SimpleNamespace(game=game)))


def test_wrong_seat_does_not_invoke_action_or_create_pending(agent):
    conn = Connection(local=1)

    async def forbidden():
        pytest.fail("wrong-seat action ran")

    result = asyncio.run(agent.guarded_logged(context(conn), "unit_action", {"action": "fortify"}, forbidden))
    assert result.startswith("WAIT|")
    assert not agent.pending_path().exists()
    assert conn.closed == 1


def test_builtin_ai_seat_fails_closed(agent):
    conn = Connection(human=False)

    async def forbidden():
        pytest.fail("action ran with an AI-controlled seat")

    result = asyncio.run(agent.guarded_logged(context(conn), "unit_action", {"action": "fortify"}, forbidden))
    assert "Expected original four-leaf" in result
    assert not agent.pending_path().exists()
    assert conn.closed == 1


def test_lost_action_response_blocks_another_server_session(agent):
    conn = Connection()

    async def lost():
        raise OSError("response lost after submission")

    result = asyncio.run(agent.guarded_logged(context(conn), "unit_action", {"action": "fortify"}, lost))
    assert "OUTCOME_UNKNOWN" in result
    original_pending = agent.pending_path().read_text()
    agent.session_id = "replacement-server"

    async def forbidden():
        pytest.fail("an uncertain action was replayed")

    result = asyncio.run(agent.guarded_logged(context(conn), "unit_action", {"action": "fortify"}, forbidden))
    assert "OUTCOME_UNKNOWN" in result
    assert agent.pending_path().read_text() == original_pending
    assert conn.closed == 2


def test_confirmed_domain_action_releases_gate_and_connection(agent):
    conn = Connection()

    async def confirmed():
        return "FORTIFIED|unit:131073|ready:false"

    result = asyncio.run(agent.guarded_logged(context(conn), "unit_action", {"action": "fortify"}, confirmed))
    assert result.startswith("FORTIFIED|")
    assert not agent.pending_path().exists()
    assert conn.closed == 1


def test_handoff_completes_without_global_turn_growth_and_never_resends(agent):
    conn = Connection()
    ctx = context(conn)
    result = asyncio.run(agent.end_hotseat_turn(ctx))
    assert result == "HS_HANDOFF|from:0|to:1|turn:1|both_human:true"
    assert conn.sent == 1
    assert not agent.pending_path().exists()
    assert asyncio.run(agent.end_hotseat_turn(ctx)).startswith("WAIT|")
    assert conn.sent == 1


def test_cheat_action_not_exposed(agent):
    conn = Connection()

    async def forbidden():
        pytest.fail("unsupported action ran")

    result = asyncio.run(agent.guarded_logged(context(conn), "unit_action", {"action": "teleport"}, forbidden))
    assert result.startswith("REFUSED|")
    assert not agent.pending_path().exists()


def test_transient_listener_rearm_retries_only_read_preflight(agent):
    class Rearming(Connection):
        reads = 0

        async def execute_write(self, code):
            if 'print("HS|' in code:
                self.reads += 1
                if self.reads == 1:
                    raise ConnectionRefusedError("listener retiring old client")
            return await super().execute_write(code)

    conn = Rearming()
    actions = []

    async def confirmed():
        actions.append("submitted")
        return "FORTIFIED|unit:131073|ready:false"

    result = asyncio.run(agent.guarded_logged(context(conn), "unit_action", {"action": "fortify"}, confirmed))
    assert result.startswith("FORTIFIED|")
    assert conn.reads == 2 and actions == ["submitted"]


def test_native_handoff_error_cannot_release_shared_gate(agent, monkeypatch):
    async def returned_error(_ctx, _tool, _params, _fn):
        return "Error: native Lua failed after a potentially submitted handoff"

    monkeypatch.setattr(agent, "original_logged", returned_error)
    result = asyncio.run(agent.end_hotseat_turn(context(Connection())))
    assert "OUTCOME_UNKNOWN" in result
    pending = json.loads(agent.pending_path().read_text())
    assert pending["tool"] == "end_hotseat_turn"


def test_read_only_queries_do_not_clear_an_uncertain_action(agent):
    prior = {"session": "older", "seat": 0, "tool": "unit_action", "params": {"action": "fortify"}, "phase": "submitted"}
    agent.write_pending(prior)

    async def read():
        return "fresh native facts"

    result = asyncio.run(agent.guarded_logged(context(Connection()), "get_units", {}, read))
    assert result == "fresh native facts"
    assert json.loads(agent.pending_path().read_text()) == prior

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
    async def no_blockers(_game, _turn):
        return None
    monkeypatch.setattr(module, "_check_blockers", no_blockers)
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


def test_native_choice_blocks_end_without_submitting(agent, monkeypatch):
    async def blocked(_game, _turn):
        return "Choose research before ending"
    monkeypatch.setattr(agent, "_check_blockers", blocked)
    conn = Connection()
    result = asyncio.run(agent.end_hotseat_turn(context(conn)))
    assert result == "BLOCKED|Choose research before ending"
    assert conn.sent == 0
    assert not agent.pending_path().exists()


def test_action_group_stops_after_unknown_receipt(agent, monkeypatch):
    calls = []
    async def call(name, arguments, context):
        calls.append((name, arguments))
        return "ERR:OUTCOME_UNKNOWN|first action not confirmed"
    monkeypatch.setattr(agent.server.mcp._tool_manager, "call_tool", call)
    commands = [{"tool": "unit_action", "arguments": {"unit_id": 10, "action": "fortify"}},
                {"tool": "set_research", "arguments": {"tech_or_civic": "TECH_MINING"}}]
    result = asyncio.run(agent.hotseat_actions(json.dumps(commands), context(Connection())))
    assert "OUTCOME_UNKNOWN" in result
    assert calls == [("unit_action", commands[0]["arguments"])]


def test_action_group_rejects_unsafe_tool_before_any_action(agent, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid group invoked a tool")
    monkeypatch.setattr(agent.server.mcp._tool_manager, "call_tool", forbidden)
    commands = [{"tool": "get_units"}, {"tool": "run_lua", "arguments": {"code": "cheat()"}}]
    assert asyncio.run(agent.hotseat_actions(json.dumps(commands), context(Connection()))).startswith("REFUSED|")


def test_confirmed_begin_survives_bundled_read_failure(agent, monkeypatch):
    class Beginning(Connection):
        async def execute_in_named_state(self, name, code):
            assert name == "PlayerChange"
            self.sent += 1
            return ["HS_BEGIN_SENT"]
    async def unavailable(*args, **kwargs):
        raise OSError("read failed after confirmed begin")
    monkeypatch.setattr(agent.server.mcp._tool_manager, "call_tool", unavailable)
    conn = Beginning()
    result = asyncio.run(agent.begin_hotseat_turn(context(conn)))
    assert result.startswith("HS_READY|")
    assert "read failed" in result and "OUTCOME_UNKNOWN" not in result
    assert conn.sent == 1
    assert not agent.pending_path().exists()


def test_trade_accept_exit_and_original_confirmation_keep_one_domain_owner(agent, monkeypatch):
    """An accepted proposal can still own a native dialogue until confirmed closed."""
    conn = Connection()
    ctx = context(conn)
    app = ctx.request_context.lifespan_context
    game = app.game
    game.diplomacy_pending = False
    game.congress_pending = False
    game.pending_action_entity = None
    app.lock = asyncio.Lock()
    class Logger:
        async def log_tool_call(self, *args):
            pass
    app.logger = Logger()
    monkeypatch.setattr(agent, "original_logged", agent.server._logged)
    closed, moves = False, []
    proposal = {"other_player_id": 3, "offer_items": [{"type": "AGREEMENT", "subtype": "OPEN_BORDERS"}],
                "request_items": [{"type": "AGREEMENT", "subtype": "OPEN_BORDERS"}]}
    async def propose():
        if game.diplomacy_pending and closed:
            game.diplomacy_pending = False
            return "ACCEPTED|owned proposal and native session closure confirmed"
        game.diplomacy_pending = True
        return "ACCEPTED|native acceptance; dialogue still needs EXIT"
    async def exit_dialogue():
        nonlocal closed
        closed = True
        return "OK:RESPONDED|SESSION_CLOSED"
    async def move():
        moves.append("submitted")
        return "MOVING_TO|18,11"
    async def scenario():
        assert (await agent.guarded_logged(ctx, "propose_trade", proposal, propose)).startswith("ACCEPTED|")
        assert agent.pending_path().exists()
        assert (await agent.guarded_logged(ctx, "respond_to_diplomacy", {"other_player_id": 3, "response": "EXIT"}, exit_dialogue)).startswith("OK:")
        assert json.loads(agent.pending_path().read_text())["tool"] == "propose_trade"
        blocked = await agent.guarded_logged(ctx, "unit_action", {"unit_id": 7, "action": "move"}, move)
        assert blocked.startswith("BLOCKED|") and moves == []
        assert json.loads(agent.pending_path().read_text())["tool"] == "propose_trade"
        assert (await agent.guarded_logged(ctx, "propose_trade", proposal, propose)).startswith("ACCEPTED|")
        assert not agent.pending_path().exists()
        assert (await agent.guarded_logged(ctx, "unit_action", {"unit_id": 7, "action": "move"}, move)).startswith("MOVING_TO|")
    asyncio.run(scenario())
    assert moves == ["submitted"]


def test_unknown_diplomacy_child_preserves_parent_and_never_allows_a_new_choice(agent):
    ctx = context(Connection())
    game = ctx.request_context.lifespan_context.game
    game.diplomacy_pending = False
    sent = []
    async def proposal():
        game.diplomacy_pending = True
        return "ACCEPTED|dialogue still open"
    async def response():
        if not sent:
            sent.append("EXIT submitted once")
            raise OSError("reply lost")
        return "OK:RESPONDED|SESSION_CLOSED"
    async def forbidden():
        pytest.fail("an unknown child was replaced by another intent")
    async def scenario():
        args = {"other_player_id": 3}
        await agent.guarded_logged(ctx, "propose_trade", args, proposal)
        choice = {"other_player_id": 3, "response": "EXIT"}
        assert "OUTCOME_UNKNOWN" in await agent.guarded_logged(ctx, "respond_to_diplomacy", choice, response)
        active = json.loads(agent.pending_path().read_text())
        assert active["tool"] == "respond_to_diplomacy" and active["parent"]["tool"] == "propose_trade"
        assert "OUTCOME_UNKNOWN" in await agent.guarded_logged(ctx, "propose_trade", args, forbidden)
        assert "OUTCOME_UNKNOWN" in await agent.guarded_logged(ctx, "respond_to_diplomacy", {**choice, "response": "ACCEPT"}, forbidden)
        await agent.guarded_logged(ctx, "respond_to_diplomacy", choice, response)
        assert json.loads(agent.pending_path().read_text())["tool"] == "propose_trade"
    asyncio.run(scenario())
    assert sent == ["EXIT submitted once"]


def test_votes_native_handoff_retires_only_exact_congress_owner(agent, monkeypatch):
    from civ_mcp.game_state import GameState
    from test_congress_hotseat import hotseat_connection
    conn=hotseat_connection()
    async def disconnect():
        pass
    conn.disconnect=disconnect
    async def read_phase(_):
        return ['HS','true',str(conn.lua.eval('Game.GetLocalPlayer()')), '67','false',
                'true','true','true','719665280','CIVILIZATION_SUMERIA','CIVILIZATION_SWEDEN']
    monkeypatch.setattr(agent,'phase',read_phase)
    game=GameState(conn)
    ctx=SimpleNamespace(request_context=SimpleNamespace(lifespan_context=SimpleNamespace(game=game)))
    result=asyncio.run(agent.guarded_logged(ctx,'queue_wc_votes',{'votes':'[]'},lambda:game.queue_wc_votes([])))
    assert result.startswith('HS_HANDOFF|from:0|to:1|turn:67')
    assert not agent.pending_path().exists() and not game.congress_pending
    assert conn.operations==[(0,'submit',{})] and conn.actions==['continue']

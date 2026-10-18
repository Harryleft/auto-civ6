"""Native diplomacy Close pairs event ownership without diplomatic responses."""
import asyncio
import pytest
from lupa.lua51 import LuaRuntime
from civ_mcp.game_lifecycle import DiplomacyViewCleanup
from test_trade_progress import quick_trade


class NativeDiplomacy:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows, self.calls = [], []
        self.lose_reply = False
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
me, queued, pending, open_sid, hidden, initialized, locks, closes, responses = 0, false, false, -1, true, true, 1, 0, 0
world_seed=7
GameConfiguration={GetValue=function() return world_seed end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end}}
Game={GetLocalPlayer=function() return me end}
Players={[0]={},[1]={}}
DiplomacyManager={FindOpenSessionID=function() return open_sid end,
 HasQueuedSession=function() return queued end,
 AddResponse=function() responses=responses+1 end,
 CloseSession=function() responses=responses+1 end}
DealManager={HasPendingDeal=function() return pending end}
ContextPtr={GetID=function() return "DiplomacyActionView" end, IsHidden=function() return hidden end}
UI={IsProcessingMessages=function() return false end}
function Close()
 closes=closes+1
 if initialized then locks=locks-1; initialized=false end
 hidden=true
end
''')

    async def execute_in_named_state(self, name, source, timeout=5):
        assert name == "DiplomacyActionView"
        self.calls.append(source)
        self.rows.clear()
        self.lua.execute(source)
        if self.lose_reply:
            self.lose_reply = False
            raise TimeoutError("native response lost")
        return [row for row in self.rows if row != "---END---"]


def test_hidden_initialized_view_releases_event_through_native_close():
    conn = NativeDiplomacy()
    result = asyncio.run(DiplomacyViewCleanup().close(conn))
    assert result.status == "confirmed"
    assert conn.lua.globals().locks == 0
    assert conn.lua.globals().closes == 1
    assert conn.lua.globals().responses == 0


@pytest.mark.parametrize("setup", ["open_sid=71", "queued=true", "pending=true", "DealManager.HasPendingDeal=nil"])
def test_current_or_unknown_diplomacy_is_not_force_closed(setup):
    conn = NativeDiplomacy()
    conn.lua.execute(setup)
    result = asyncio.run(DiplomacyViewCleanup().close(conn))
    assert result.status == "not_submitted"
    assert conn.lua.globals().locks == 1 and conn.lua.globals().closes == 0
    assert conn.lua.globals().responses == 0


def test_lost_close_response_is_only_confirmed_never_replayed():
    conn = NativeDiplomacy()
    cleanup = DiplomacyViewCleanup()
    conn.lose_reply = True
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    assert cleanup.pending
    assert asyncio.run(cleanup.close(conn)).status == "confirmed"
    assert not cleanup.pending
    assert conn.lua.globals().closes == 1 and conn.lua.globals().locks == 0
    assert conn.lua.globals().responses == 0


def test_native_callback_failure_is_unknown_and_never_replayed():
    conn = NativeDiplomacy()
    conn.lua.execute('function Close() closes=closes+1; error("native close failed") end')
    cleanup = DiplomacyViewCleanup()
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    assert conn.lua.globals().closes == 1 and cleanup.pending


def test_new_pending_deal_prevents_even_confirmation_from_closing_again():
    conn = NativeDiplomacy()
    cleanup = DiplomacyViewCleanup()
    conn.lose_reply = True
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    conn.lua.execute('pending=true')
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    assert conn.lua.globals().closes == 1 and conn.lua.globals().responses == 0


def test_context_mismatch_is_known_zero_submission():
    conn = NativeDiplomacy()
    conn.lua.execute('ContextPtr.GetID=function() return "InGame" end')
    cleanup = DiplomacyViewCleanup()
    assert asyncio.run(cleanup.close(conn)).status == "not_submitted"
    assert not cleanup.pending and conn.lua.globals().closes == 0


def test_propose_trade_keeps_accepted_result_when_close_reply_is_lost(quick_trade):
    from civ_mcp.game_state import GameState
    from test_trade_progress import TradeConn
    class LostClose(TradeConn):
        lost = False
        async def execute_in_named_state(self, name, source, timeout=5):
            rows = await super().execute_in_named_state(name, source, timeout)
            if "DIPLOMACY_NATIVE_CLOSE" in source and not self.lost:
                self.lost = True
                raise TimeoutError("close reply lost")
            return rows
    async def scenario():
        conn=LostClose(); state=GameState(conn)
        assert "ACCEPTED|" in await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        assert "SESSION_CLOSED" in await state.diplomacy_respond(1,"EXIT")
        assert "cleanup unknown" in await state.dismiss_popup()
        result=await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        assert "ACCEPTED|" in result and "cleanup confirmed" in result
        assert conn.lua.globals().requests==1 and conn.lua.globals().close_callbacks==1
    asyncio.run(scenario())


def test_hidden_new_world_cannot_confirm_old_native_receipt():
    conn = NativeDiplomacy()
    cleanup = DiplomacyViewCleanup()
    conn.lose_reply = True
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    conn.lua.execute('world_seed=99')
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    assert cleanup.pending and conn.lua.globals().closes == 1


def test_observed_identity_change_latches_cleanup_unknown_until_replacement():
    conn = NativeDiplomacy()
    cleanup = DiplomacyViewCleanup()
    conn.lose_reply = True
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    cleanup.invalidate_scope()
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    assert len(conn.calls) == 1 and cleanup.pending


def test_hidden_empty_view_without_this_nonce_cannot_confirm_unknown_close():
    conn = NativeDiplomacy()
    original = conn.execute_in_named_state
    async def lose_before_dispatch(*args, **kwargs):
        raise TimeoutError("dispatch outcome unknown")
    conn.execute_in_named_state = lose_before_dispatch
    cleanup = DiplomacyViewCleanup()
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    conn.execute_in_named_state = original
    assert asyncio.run(cleanup.close(conn)).status == "unknown"
    assert cleanup.pending and conn.lua.globals().closes == 0
    assert conn.lua.globals().hidden and conn.lua.globals().locks == 1


class CancelledTradeConn(NativeDiplomacy):
    def __init__(self):
        super().__init__()
        self.proposals = 0
        self.cancel_close_reply = True

    async def execute_write(self, source):
        if "DealManager.SendWorkingDeal" in source:
            self.proposals += 1
            if "EQUALIZE" in source:
                return ["CIV|1|Canada", "PROPOSED_ITEMS", "ITEM|US|GOLD|20|0||", "AI_COUNTER", "ITEM|THEM|GOLD|10|30||"]
            return ["OK:ACCEPTED|Trade accepted with Canada"]
        if "DIPLOMACY_VIEW_PRESENT" in source:
            return ["DIPLOMACY_VIEW_PRESENT"]
        if "INFORMATION_POPUPS" in source:
            return ["INFORMATION_POPUPS|DONE"]
        raise AssertionError("Unexpected game boundary call")

    async def execute_in_named_state(self, name, source, timeout=5):
        rows = await super().execute_in_named_state(name, source, timeout)
        if self.cancel_close_reply:
            self.cancel_close_reply = False
            raise asyncio.CancelledError()
        return rows


@pytest.mark.parametrize("first", ["propose_trade", "test_trade"])
@pytest.mark.parametrize("retry", ["propose_trade", "test_trade"])
def test_cancel_after_native_close_keeps_original_trade_and_never_resubmits(first, retry, quick_trade):
    from civ_mcp.game_state import GameState
    from test_trade_progress import TradeConn
    class CancelledClose(TradeConn):
        cancelled=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if "DIPLOMACY_NATIVE_CLOSE" in source and not self.cancelled:
                self.cancelled=True
                raise asyncio.CancelledError()
            return rows
    async def scenario():
        conn=CancelledClose(); state=GameState(conn)
        with pytest.raises(asyncio.CancelledError):
            result=await getattr(state,first)(1,[{"type":"GOLD","amount":20}],[])
            if first=="propose_trade":
                assert "ACCEPTED|" in result
                assert "SESSION_CLOSED" in await state.diplomacy_respond(1,"EXIT")
                await state.dismiss_popup()
        result=await getattr(state,retry)(1,[{"type":"GOLD","amount":20}],[])
        if first!=retry:
            assert "PENDING_TRADE_MISMATCH" in result and "ACCEPTED" not in result
            assert state.diplomacy_pending
            result=await getattr(state,first)(1,[{"type":"GOLD","amount":20}],[])
        assert "cleanup confirmed" in result
        assert conn.lua.globals().requests==1 and conn.lua.globals().close_callbacks==1
    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["propose_trade", "test_trade"])
def test_unrelated_dismiss_cancel_only_confirms_cleanup_before_any_new_trade(entry):
    from civ_mcp.game_state import GameState
    async def scenario():
        conn = CancelledTradeConn()
        state = GameState(conn)
        with pytest.raises(asyncio.CancelledError):
            await state.dismiss_popup()
        result = await getattr(state, entry)(1, [], [])
        assert "No new trade submitted" in result and "cleanup confirmed" in result
        assert conn.proposals == 0 and conn.lua.globals().closes == 1
    asyncio.run(scenario())


def test_unconfirmed_unrelated_cleanup_keeps_gameplay_save_and_turn_gates():
    from civ_mcp.game_state import GameState
    async def scenario():
        conn = CancelledTradeConn()
        state = GameState(conn)
        with pytest.raises(asyncio.CancelledError):
            await state.dismiss_popup()
        assert state.diplomacy_pending
        for operation in (state.end_turn, lambda: state.save_game("cleanup_gate"),
                          lambda: state.reload_game_save("checkpoint")):
            assert "OUTCOME_UNKNOWN" in await operation()
        assert conn.lua.globals().closes == 1 and conn.proposals == 0
        assert "Dismissed: DiplomacyActionView (native Close)" == await state.dismiss_popup()
        assert not state.diplomacy_pending
    asyncio.run(scenario())

"""Pending contributions can release native popup holds through real MCP dispatch."""

import asyncio
from contextlib import asynccontextmanager

import anyio
from mcp import ClientSession
import pytest

from civ_mcp import game_launcher, server
from civ_mcp.logger import GameLogger
from test_royal_contribution import ContributionConn


class PopupContributionConn(ContributionConn):
    def __init__(self):
        super().__init__()
        self.lua_states = {11: "ProjectBuiltPopup"}
        self.lua.execute('''
deferred=true;popup_visible=true;popup_closes=0;raw_writes=0
native_context="InGame"
local popup={IsHidden=function() return not popup_visible end}
ContextPtr={
 GetID=function() return native_context end,
 IsHidden=popup.IsHidden,
 LookUpControl=function(_,path)
  if path=="/InGame/ProjectBuiltPopup" then return popup end
  if path=="/InGame/DiplomacyDealView" and incoming_trade_visible then
   return {IsHidden=function() return false end}
  end
 end,
}
NotificationManager={GetList=function() return {} end}
EndTurnBlockingTypes={};PlayerOperations={}
InterfaceModeTypes={CINEMATIC=7,SELECTION=0}
UI.GetInterfaceMode=function() return 0 end
function Close()
 popup_closes=popup_closes+1
 if close_fails then error("native close unavailable") end
 popup_visible=false
 -- Releasing the native event hold can deliver the already submitted command.
 if release_on_close then applyContribution() end
end
''')

    async def execute_write(self, source):
        self.lua.globals().native_context = "InGame"
        return await super().execute_write(source)

    async def execute_in_state(self, index, source, timeout=5):
        assert index == 11
        self.lua.globals().native_context = "ProjectBuiltPopup"
        return await super().execute_write(source)

    async def disconnect(self):
        pass


@pytest.fixture
def memory_client(monkeypatch, tmp_path):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", .008)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", .001)

    async def no_os_dialogs():
        return []

    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", no_os_dialogs)
    log_path = tmp_path / "contribution-popup.jsonl"
    monkeypatch.setattr(server, "GameLogger", lambda: GameLogger(log_path))

    @asynccontextmanager
    async def client(conn):
        monkeypatch.setattr(server, "GameConnection", lambda: conn)
        client_write, server_read = anyio.create_memory_object_stream(10)
        server_write, client_read = anyio.create_memory_object_stream(10)
        lowlevel = server.mcp._mcp_server
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(lowlevel.run, server_read, server_write, lowlevel.create_initialization_options())
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                yield session
            tasks.cancel_scope.cancel()

    return client


def test_explicit_popup_close_preserves_pending_contribution_until_original_evidence(memory_client):
    async def scenario():
        conn = PopupContributionConn()
        original = {"unit_id": 196608, "action": "sacrifice_charges"}
        async with memory_client(conn) as session:
            first = await session.call_tool("unit_action", original)
            assert "OUTCOME_UNKNOWN" in first.content[0].text
            token = conn.lua.eval('__civmcpContribution.token')
            closed = await session.call_tool("dismiss_popup", {})
            assert conn.lua.eval('popup_closes') == 1
            assert "Dismissed: ProjectBuiltPopup" in closed.content[0].text
            assert "SACRIFICED" not in closed.content[0].text
            assert conn.lua.eval('__civmcpContribution.token') == token
            still_pending = await session.call_tool("unit_action", original)
            assert "OUTCOME_UNKNOWN" in still_pending.content[0].text
            assert conn.lua.eval('#submissions') == 1
            conn.lua.execute('applyContribution()')
            confirmed = await session.call_tool("unit_action", original)
            assert confirmed.content[0].text.startswith("SACRIFICED|unit:196608|charges:6")
            assert conn.lua.eval('#submissions') == 1
    asyncio.run(scenario())


def test_popup_release_delivers_the_queued_command_without_resubmission(memory_client):
    async def scenario():
        conn = PopupContributionConn()
        conn.lua.execute('release_on_close=true')
        original = {"unit_id": 196608, "action": "sacrifice_charges"}
        async with memory_client(conn) as session:
            first = await session.call_tool("unit_action", original)
            assert "COMMAND_NOT_OBSERVED" in first.content[0].text
            assert conn.lua.eval('builder.charges') == 6
            closed = await session.call_tool("dismiss_popup", {})
            assert "Dismissed: ProjectBuiltPopup" in closed.content[0].text
            assert "SACRIFICED" not in closed.content[0].text
            assert conn.lua.eval('popup_closes') == 1
            confirmed = await session.call_tool("unit_action", original)
            assert confirmed.content[0].text.startswith("SACRIFICED|unit:196608|charges:6")
            assert "progress:100->220" in confirmed.content[0].text
            assert conn.lua.eval('#submissions') == 1
    asyncio.run(scenario())


def test_only_explicit_popup_close_can_cross_the_pending_contribution_dispatch_gate(memory_client):
    async def scenario():
        conn = PopupContributionConn()
        original = {"unit_id": 196608, "action": "sacrifice_charges"}
        other_writes = [
            ("unit_action", {"unit_id": 65537, "action": "sleep"}),
            ("set_city_production", {"city_id": 65539, "item_type": "PROJECT", "item_name": "PROJECT_LAUNCH_MOON_LANDING"}),
            ("save_game", {"save_name": "pending_contribution_popup"}),
            ("run_lua", {"code": "raw_writes=raw_writes+1", "context": "ingame"}),
        ]
        async with memory_client(conn) as session:
            first = await session.call_tool("unit_action", original)
            assert "OUTCOME_UNKNOWN" in first.content[0].text
            token = conn.lua.eval('__civmcpContribution.token')
            for after_close in (False, True):
                if after_close:
                    await session.call_tool("dismiss_popup", {})
                for tool, params in other_writes:
                    blocked = await session.call_tool(tool, params)
                    assert "OUTCOME_UNKNOWN" in blocked.content[0].text
                assert conn.lua.eval('popup_closes') == int(after_close)
                assert conn.lua.eval('#submissions') == 1
                assert conn.lua.eval('raw_writes') == 0
                assert conn.lua.eval('__civmcpContribution.token') == token
    asyncio.run(scenario())


@pytest.mark.parametrize("setup,expected", [
    ('close_fails=true', 'outcome unknown'),
    ('Close=nil', 'outcome unknown'),
    ('popup_visible=false', 'No popups to dismiss'),
    ('popup_visible=false;incoming_trade_visible=true', 'incoming trade deal pending'),
])
def test_failed_or_absent_popup_never_retires_or_replays_the_contribution(setup, expected, memory_client):
    async def scenario():
        conn = PopupContributionConn()
        original = {"unit_id": 196608, "action": "sacrifice_charges"}
        async with memory_client(conn) as session:
            await session.call_tool("unit_action", original)
            token = conn.lua.eval('__civmcpContribution.token')
            conn.lua.execute(setup)
            closed = await session.call_tool("dismiss_popup", {})
            assert expected in closed.content[0].text
            assert "SACRIFICED" not in closed.content[0].text
            still_pending = await session.call_tool("unit_action", original)
            assert "OUTCOME_UNKNOWN" in still_pending.content[0].text
            assert conn.lua.eval('#submissions') == 1
            assert conn.lua.eval('__civmcpContribution.token') == token
    asyncio.run(scenario())

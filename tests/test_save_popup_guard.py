"""真实 Lua 验证动画期间保存零提交，关闭后不遗留保存门禁。"""
import asyncio

import pytest

from civ_mcp import checkpoints, game_lifecycle, lua as lq
from civ_mcp.game_state import GameState
from test_terminal_boundary_lua import SaveBoundaryConn, save_environment


def active_conn(directory):
    conn = SaveBoundaryConn(directory)
    conn.lua.execute('''
winner=nil
ContextPtr.LookUpControl=function(_,path)
 return {IsHidden=function() return path~="/InGame/"..tostring(visible_popup) end}
end
''')
    return conn


@pytest.mark.parametrize("popup", game_lifecycle.INFORMATION_POPUPS)
def test_information_popup_refuses_before_listener_or_file_and_can_save_after_close(save_environment, popup):
    conn = active_conn(save_environment)
    conn.lua.globals().visible_popup = popup
    state = GameState(conn)
    result = asyncio.run(state.save_game("popup_checkpoint"))
    assert "SAVE_POPUP_BLOCKED" in result and popup in result
    assert "no save submitted" in result
    assert not state.save_pending
    assert conn.lua.globals().save_requests == conn.lua.globals().save_adds == 0
    assert not (save_environment / "popup_checkpoint.Civ6Save").exists()
    conn.lua.globals().visible_popup = None
    assert asyncio.run(state.save_game("popup_checkpoint")).startswith("Saved:")
    assert conn.lua.globals().save_requests == 1
    assert not state.save_pending


@pytest.mark.parametrize("change,reason", [
    ('UI.GetInterfaceMode=function() return 2 end', 'SAVE_POPUP_BLOCKED'),
    ('UI.GetInterfaceMode=nil', 'SAVE_UI_UNAVAILABLE'),
    ('ContextPtr.LookUpControl=function() error("原生界面不可读") end', 'SAVE_UI_UNAVAILABLE'),
    ('ContextPtr.LookUpControl=function() return {IsHidden=function() return 0 end} end', 'SAVE_UI_UNAVAILABLE'),
])
def test_cinematic_or_unknown_ui_cannot_start_save(save_environment, change, reason):
    conn = active_conn(save_environment)
    conn.lua.execute(change)
    state = GameState(conn)
    assert reason in asyncio.run(state.save_game("unsafe"))
    assert not state.save_pending
    assert conn.lua.globals().save_requests == conn.lua.globals().save_adds == 0


def test_popup_appearing_after_overview_is_still_refused_atomically(save_environment):
    conn = active_conn(save_environment)
    original = conn.execute

    def execute(context, source):
        rows = original(context, source)
        if source == lq.build_overview_query():
            conn.lua.globals().visible_popup = "WonderBuiltPopup"
        return rows

    conn.execute = execute
    state = GameState(conn)
    assert "SAVE_POPUP_BLOCKED" in asyncio.run(state.save_game("late_popup"))
    assert conn.lua.globals().save_requests == conn.lua.globals().save_adds == 0
    assert not state.save_pending


def test_popup_refusal_preserves_verified_checkpoint(save_environment):
    conn = active_conn(save_environment)
    state = GameState(conn)
    assert asyncio.run(state.save_game("checkpoint")).startswith("Saved:")
    before = checkpoints.expected_checkpoint("checkpoint")
    conn.lua.globals().visible_popup = "WonderBuiltPopup"
    assert "SAVE_POPUP_BLOCKED" in asyncio.run(state.save_game("checkpoint"))
    assert checkpoints.expected_checkpoint("checkpoint") == before
    assert conn.lua.globals().save_requests == 1

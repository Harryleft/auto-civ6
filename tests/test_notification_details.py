"""Native notification details through public queries and confirmed reports."""

import asyncio

from lupa.lua51 import LuaRuntime
import pytest

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_notifications


class NotificationConn:
    """Execute the production query against only a fake native API."""

    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
Game={GetLocalPlayer=function() return 0 end}
Locale={Lookup=function(key)
    if key=="LOC_TITLE" then return "失败！" end
    if key=="LOC_SUMMARY" then return "一名未相遇的城邦已被击败。" end
    return key
end}
entry={
    IsDismissed=function() return false end,
    GetTypeName=function() return "NOTIFICATION_PLAYER_DEFEATED" end,
    GetMessage=function() return "LOC_TITLE" end,
    GetSummary=function() return "LOC_SUMMARY" end,
    GetAddedTurn=function() return 60 end,
    GetLocation=function() return -1,-1 end,
}
NotificationManager={GetList=function() return {294} end,
                     Find=function() return entry end}
''')

    async def execute_write(self, code):
        self.rows.clear()
        self.lua.execute(code)
        return list(self.rows)


def test_native_summary_identifies_anonymous_defeat_without_claiming_local_defeat():
    notifications = asyncio.run(GameState(NotificationConn()).get_notifications())
    notification, = notifications
    assert notification.message == "失败！"
    assert notification.summary == "一名未相遇的城邦已被击败。"
    assert not notification.is_action_required
    text = narrate_notifications(notifications)
    assert "失败！ — 一名未相遇的城邦已被击败。" in text


def test_localized_title_and_summary_cannot_split_wire_fields_or_rows():
    conn = NotificationConn()
    conn.lua.globals().title = "Title|one\r\nnext"
    conn.lua.globals().detail = "Subject|two\nnext\rline"
    conn.lua.execute('''
entry.GetMessage=function() return title end
entry.GetSummary=function() return detail end
''')
    notification, = asyncio.run(GameState(conn).get_notifications())
    assert notification.message == "Title/one next"
    assert notification.summary == "Subject/two next line"
    assert (notification.turn, notification.x, notification.y) == (60, -1, -1)
    assert len([row for row in conn.rows if row.startswith("NOTIF|")]) == 1


@pytest.mark.parametrize("title,summary,expected", [
    ("失败！", "失败！", "失败！"),
    (" 失败！ ", " 失败！ ", "失败！"),
    ("失败！", "失败！ 一名未相遇的城邦已被击败。", "失败！ 一名未相遇的城邦已被击败。"),
    ("一名未相遇的城邦已被击败。", "城邦已被击败", "一名未相遇的城邦已被击败。"),
    ("失败！", "", "失败！"),
    ("", "一名未相遇的城邦已被击败。", "一名未相遇的城邦已被击败。"),
])
def test_list_and_confirmed_report_share_concise_native_notification_text(title, summary, expected):
    from test_game_state import FakeConn

    row = f"NOTIF|NOTIFICATION_PLAYER_DEFEATED|{title}|60|-1,-1|{summary}"
    listing = narrate_notifications(asyncio.run(GameState(FakeConn([
        ("write", [row, "TOTAL|1|1"]),
    ])).get_notifications()))
    conn = FakeConn([
        ("write", ["61|0|Sumeria|Gilgamesh|29|12|13.2|13.5|20|Wheel|Mysticism|0|1|92"]),
        ("write", []), ("write", []), ("write", []),
        ("write", [row, "TOTAL|1|1"]),
        ("write", ["VICTORY_CONTEXT|61|0", "VICTORY_ENABLED_KNOWN|true", "VICTORY_COMPLETE"]),
    ])
    report = asyncio.run(GameState(conn).report_turn_advance(60, 61, None))
    assert f"  - {expected}" in listing.splitlines()
    assert f"  - {expected}" in report.splitlines()
    assert conn.exhausted


@pytest.mark.parametrize("summary_getter", [
    "nil", "function() return nil end", 'function() error("unsupported") end',
])
def test_unavailable_summary_preserves_localized_title(summary_getter):
    conn = NotificationConn()
    conn.lua.execute(f"entry.GetSummary={summary_getter}")
    notifications = asyncio.run(GameState(conn).get_notifications())
    notification, = notifications
    assert notification.message == "失败！" and notification.summary == ""
    assert narrate_notifications(notifications).splitlines()[-1] == "  - 失败！"


def test_legacy_wire_row_still_has_no_summary_and_keeps_action_hint():
    from test_game_state import FakeConn

    notifications = asyncio.run(GameState(FakeConn([("write", [
        "NOTIF|NOTIFICATION_CHOOSE_TECH|Choose research|60|19,17", "TOTAL|1|1",
    ])])).get_notifications())
    notification, = notifications
    assert notification.summary == "" and notification.is_action_required
    text = narrate_notifications(notifications)
    assert "Choose research at (19,17)" in text and "set_research" in text


@pytest.mark.parametrize("notification_type,required", [
    ("NOTIFICATION_PLAYER_DEFEATED", False),
    ("NOTIFICATION_CHOOSE_TECH", True),
])
def test_defeat_text_does_not_change_action_classification_terminal_or_pending_gate(notification_type, required):
    from test_game_state import FakeConn

    conn = FakeConn([
        ("write", [f"NOTIF|{notification_type}|失败！|60|-1,-1|一名未相遇的城邦已被击败。", "TOTAL|1|1"]),
        ("read", ["GAME_ACTIVE"]),
    ])
    game = GameState(conn)
    game._pending_end_turn = True

    async def read():
        notification, = await game.get_notifications()
        terminal = await game.check_game_over()
        return notification, terminal

    notification, terminal = asyncio.run(read())
    assert notification.is_action_required is required
    assert terminal is None and game.turn_pending
    assert conn.exhausted


def test_wonder_notification_keeps_native_anonymous_summary_without_map_enrichment():
    conn = NotificationConn()
    conn.lua.execute('''
hidden_reads=0
Map={GetPlot=function() hidden_reads=hidden_reads+1; return nil end}
entry.GetTypeName=function() return "NOTIFICATION_WONDER_COMPLETED" end
entry.GetMessage=function() return "Wonder completed" end
entry.GetSummary=function() return "An unmet civilization completed a wonder." end
entry.GetLocation=function() return 19,17 end
''')
    notifications = asyncio.run(GameState(conn).get_notifications())
    assert "An unmet civilization completed a wonder." in narrate_notifications(notifications)
    assert conn.lua.globals().hidden_reads == 0

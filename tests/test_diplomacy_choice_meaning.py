"""Current native choice meaning must survive the Lua -> GameState -> CLI path."""

import asyncio
import json

import pytest

from civ_mcp import narrate
from civ_mcp.game_state import GameState
from test_trade_progress import AsyncDiplomacyConn


def test_native_selection_with_war_meaning_is_exposed_without_submitting():
    conn = AsyncDiplomacyConn()
    state = GameState(conn)
    asyncio.run(state.get_diplomacy_sessions())
    conn.setup('''
native_selections={
 {Key="CHOICE_POSITIVE",Text="My troops are merely passing by."},
 {Key="CHOICE_NEGATIVE",Text="Prepare for war! (Declare War)",
  Tooltip="Declares a surprise war.",DiplomaticActionType="DIPLOACTION_DECLARE_SURPRISE_WAR"}}
''')
    # The fixture supplies native selection fields. It tests their complete
    # transport, not the game's warning-handler-to-war mapping (verified live).
    conn.statement()
    context = asyncio.run(state.get_diplomacy_sessions())
    text = narrate.narrate_diplomacy_sessions(context)
    assert "NEGATIVE" in text
    assert "Prepare for war! (Declare War)" in text
    assert "DIPLOACTION_DECLARE_SURPRISE_WAR" in text
    assert "Declares a surprise war." in text
    assert list(conn.lua.globals().mutations.values()) == []


def test_localized_choice_text_survives_delimiters_and_disabled_choices_are_absent():
    conn = AsyncDiplomacyConn()
    state = GameState(conn)
    asyncio.run(state.get_diplomacy_sessions())
    label = '只是路过 | "不会宣战"\n第二行'
    conn.setup('''
Locale.Lookup=function(value)
 if value=="LOC_RESPONSE" then return ''' + json.dumps(label, ensure_ascii=False) + ''' end
 return value
end
native_selections={
 {Key="CHOICE_POSITIVE",Text="LOC_RESPONSE",ToolTip="原生说明"},
 {Key="CHOICE_NEGATIVE",Text="Declare War",IsDisabled=true}}
''')
    conn.statement()
    context = asyncio.run(state.get_diplomacy_sessions())
    assert context.sessions[0].choice_keys == ["CHOICE_POSITIVE"]
    choice = context.sessions[0].choices[0]
    assert choice.text == label
    assert choice.tooltip == "原生说明"
    assert choice.diplomatic_action_type is None
    assert "NOT_AVAILABLE" in asyncio.run(state.diplomacy_respond(1, "NEGATIVE"))
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("text", ['nil', '"LOC_UNRESOLVED"', '17'])
def test_missing_native_meaning_is_explicitly_unknown(text):
    conn = AsyncDiplomacyConn()
    state = GameState(conn)
    asyncio.run(state.get_diplomacy_sessions())
    conn.setup('native_selections={{Key="CHOICE_NEGATIVE",Text=' + text + '}}')
    conn.statement()
    context = asyncio.run(state.get_diplomacy_sessions())
    assert context.sessions[0].choice_keys == ["CHOICE_NEGATIVE"]
    assert context.sessions[0].choices[0].text == ""
    assert "Meaning UNKNOWN" in narrate.narrate_diplomacy_sessions(context)
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("row", [
    "DIPLO_CHOICE|CHOICE_NEGATIVE|not-hex||",
    "DIPLO_CHOICE|CHOICE_NEGATIVE|ff||",
    "DIPLO_CHOICE|CHOICE_NEGATIVE|41|",
    "DIPLO_CHOICE|NOT_A_CHOICE|41||",
])
def test_malformed_meaning_does_not_authorize_a_response(row):
    from civ_mcp.diplomacy import DiplomacyProgress
    from test_diplomacy_response_confirmation import ScriptedConn

    conn = ScriptedConn([], ["DIPLO_CHOICES|KNOWN|5", row])
    progress = DiplomacyProgress()
    assert "UNKNOWN" in asyncio.run(progress.respond(conn, 2, 1, "NEGATIVE"))
    assert not any("OnSelectionButtonClicked" in source for source in conn.calls)
    assert progress.response is None

"""Incoming terms identify native agreement subtypes without changing deals."""

import pytest

from civ_mcp import narrate
from civ_mcp.game_state import GameState
from test_diplomacy_context_lua import DiplomacyConn, incoming, run


def agreement_connection(setup=""):
    conn = DiplomacyConn()
    incoming(conn)
    conn.setup('''
DealAgreementTypes={OPEN_BORDERS=11,JOINT_WAR=12,ALLIANCE=13}
GameInfo={DiplomaticActions={[547027585]={Name="LOC_PEACE"}}}
Locale.Lookup=function(value) if value=="LOC_PEACE" then return "Peace treaty" end return value end
agreement={GetFromPlayerID=function() return 1 end,
 GetType=function() return DealItemTypes.AGREEMENTS end,
 GetSubType=function() return 547027585 end,
 GetValueType=function() return -1 end,
 GetAmount=function() return 0 end,GetDuration=function() return 9 end,
 GetSubTypeNameID=function() return "LOC_PEACE" end}
deal.Items=function() local used=false; return function()
 if not used then used=true;return agreement end end end
''')
    conn.setup(setup)
    return conn


@pytest.mark.parametrize("setup", [
    "",
    "agreement.GetSubTypeNameID=nil",
    'agreement.GetSubTypeNameID=function() error("unavailable") end',
    "agreement.GetSubTypeNameID=function() return nil end",
    "agreement.GetSubTypeNameID=function() return 42 end",
    'agreement.GetSubTypeNameID=function() return "" end',
])
def test_current_peace_terms_have_native_name_without_submission(setup):
    conn = agreement_connection(setup)
    state = GameState(conn)
    deals = run(state.get_pending_deals())
    text = narrate.narrate_pending_deals(deals)
    assert deals.status == "INCOMING"
    item = deals.deals[0].items_from_them[0]
    assert item.name == "Peace treaty"
    assert item.duration == 9 and item.amount == 0
    assert "Peace treaty" in text
    assert conn.lua.globals().reads == 1
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("setup", [
    "GameInfo=nil;agreement.GetSubTypeNameID=nil",
    'GameInfo.DiplomaticActions=setmetatable({},{__index=function() error("missing") end});agreement.GetSubTypeNameID=nil',
    'GameInfo.DiplomaticActions={};agreement.GetSubTypeNameID=function() return false end',
])
def test_unresolved_agreement_keeps_raw_identity_without_guessing(setup):
    conn = agreement_connection(setup)
    deals = run(GameState(conn).get_pending_deals())
    name = deals.deals[0].items_from_them[0].name
    assert "unknown" in name.lower() and "547027585" in name and "-1" in name
    assert "Peace" not in name
    assert list(conn.lua.globals().mutations.values()) == []


def test_native_display_name_cannot_inject_protocol_rows():
    conn = agreement_connection('agreement.GetSubTypeNameID=function() return "Native|name\\nnext\\rline" end')
    deals = run(GameState(conn).get_pending_deals())
    assert deals.status == "INCOMING" and len(deals.deals) == 1
    assert deals.deals[0].items_from_them[0].name == "Native name next line"
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("subtype,value,name", [(11,-1,"Open Borders"),(12,-1,"Joint War"),(13,4,"Religious Alliance")])
def test_existing_agreement_labels_remain_unchanged(subtype,value,name):
    conn = agreement_connection(f'''agreement.GetSubType=function() return {subtype} end
agreement.GetValueType=function() return {value} end
agreement.GetSubTypeNameID=function() error("must not read") end
GameInfo=nil''')
    item = run(GameState(conn).get_pending_deals()).deals[0].items_from_them[0]
    assert item.name == name
    assert list(conn.lua.globals().mutations.values()) == []


def test_missing_native_subtype_and_enumeration_do_not_match_nil_alias():
    conn = agreement_connection('''DealAgreementTypes=nil;GameInfo=nil
agreement.GetSubType=function() return nil end;agreement.GetSubTypeNameID=nil''')
    name = run(GameState(conn).get_pending_deals()).deals[0].items_from_them[0].name
    assert "Unknown Agreement" in name and "subtype:nil" in name
    assert "Open Borders" not in name


def test_native_localization_error_preserves_readable_unknown_identity():
    conn = agreement_connection('Locale.Lookup=function(value) if value=="LOC_PEACE" then error("translation unavailable") end return value end')
    name = run(GameState(conn).get_pending_deals()).deals[0].items_from_them[0].name
    assert "Unknown Agreement" in name and "547027585" in name

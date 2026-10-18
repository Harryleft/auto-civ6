"""Execute Congress programs at the GameState/FakeConn seam, without Civ6.

Lua 5.1 matches the game's language; only native APIs are faked. In particular,
the fixture does not implement proposal eligibility or vote-plan validation.
"""

import asyncio

import pytest
from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState


class CongressConn:
    def __init__(self, proposals, *, stage="STAGE2", resolutions=None):
        self.lua = LuaRuntime()
        self.rows = []
        self.operations = []
        self.actions = []
        self.opens = []
        self.lose_reply = False
        globals_ = self.lua.globals()
        globals_.print = lambda row: self.rows.append(str(row))
        globals_.record_operation = lambda owner, operation, params: self.operations.append(
            (owner, operation, dict(params))
        )
        globals_.record_action = self.actions.append
        globals_.record_open = lambda: self.opens.append("open")
        globals_.proposals = (
            self.lua.table_from(proposals, recursive=True)
            if isinstance(proposals, (dict, list)) else proposals
        )
        globals_.resolutions = self.lua.table_from(resolutions or [], recursive=True)
        globals_.segment = {
            "STAGE1": "TURNSEG_WORLDCONGRESS_1",
            "STAGE2": "TURNSEG_WORLDCONGRESS_2",
            "RESOLUTION": "TURNSEG_WORLDCONGRESS_RESOLUTION",
        }.get(stage, "TURNSEG_NORMAL")
        self.lua.execute("""
in_session = true
Game = {
    GetLocalPlayer = function() return 0 end,
    GetCurrentGameTurn = function() return 67 end,
    GetCurrentTurnSegment = function() return segment end,
    GetWorldCongress = function() return {
        IsInSession = function() return in_session end,
        GetMeetingStatus = function() return {TurnsLeft=0} end,
        GetProposals = function() return proposals end,
        GetResolutions = function() return resolutions end,
        GetVotesandFavorCost = function() return {MaxVotes=5, [0]=0, [1]=10, [2]=30, [3]=60, [4]=100} end,
    } end,
}
PlayerConfigurations = {[0]={GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end}}
GameConfiguration = {GetValue=function() return 719665280 end, IsPaused=function() return false end}
Players = {[0]={GetFavor=function() return 10 end, GetDiplomacy=function() return {HasMet=function() return true end} end}}
Locale = {Lookup=function(value) return tostring(value) end}
DB = {MakeHash=function(value) return value end}
GameInfo = {
    Resolutions = setmetatable({[13]={Hash=13, ResolutionType="TEST", Name="Resolution"}}, {__call=function(self)
        local key = nil
        return function() key = next(self, key); return key and self[key] end
    end}),
    ProposalTypes = {EMERGENCY={}},
    VotingBlockers = {
        BOTH={NoUpvote=true, NoDownvote=true},
        UP={NoUpvote=true, NoDownvote=false},
        DOWN={NoUpvote=false, NoDownvote=true},
    },
}
local intro = {visible=true, IsVisible=function(self) return self.visible end}
local popup = {visible=false, IsVisible=function(self) return self.visible end}
ContextPtr = {LookUpControl=function(_, path)
    if path == "/InGame/WorldCongressIntro" then return intro end
    if path == "/InGame/WorldCongressPopup" then return popup end
end}
UIManager = {DequeuePopup=function(_, control) control.visible=false end}
LuaEvents = {
    WorldCongressIntro_ShowWorldCongress=function() record_open(); popup.visible=true end,
    DiplomacyActionView_HideCongress=function() popup.visible=false end,
    WorldCongressPopup_HideWorldCongressBetweenTurns=function() end,
}
local function event()
    local listeners = {}
    return {
        Add=function(handler) listeners[handler]=true end,
        Remove=function(handler) listeners[handler]=nil end,
        Fire=function() for handler in pairs(listeners) do handler(0) end end,
    }
end
Events = {WorldCongressStage1=event(), WorldCongressStage2=event()}
PlayerOperations = {
    WORLD_CONGRESS_RESOLUTION_VOTE="vote", WORLD_CONGRESS_SUBMIT_TURN="submit",
    PARAM_RESOLUTION_TYPE="hash", PARAM_WORLD_CONGRESS_VOTES="votes",
    PARAM_RESOLUTION_OPTION="option", PARAM_RESOLUTION_SELECTION="target",
}
ActionTypes = {ACTION_ENDTURN="continue"}
UI = {RequestPlayerOperation=record_operation, RequestAction=record_action}
""")

    async def execute_write(self, code):
        self.rows.clear()
        self.lua.execute(code)
        if self.lose_reply:
            self.lose_reply = False
            raise ConnectionError("lost after native execution")
        return [row for row in self.rows if row != "---END---"]


def proposal_status(*proposals):
    rows = [({"TypeName": f"FIXTURE_PROPOSAL_{i}", "Target": 100+i,
              "Name": "Fixture proposal", "Description": "Fixture description"} | proposal)
            if isinstance(proposal, dict) else proposal for i, proposal in enumerate(proposals)]
    return {"Proposals": {"EMERGENCY": {"ProposalsOfType": rows}}}


def test_all_disabled_proposals_use_empty_plan_without_opening_or_casting_votes():
    conn = CongressConn(proposal_status({"Disabled": True}))
    state = GameState(conn)

    assert "WC_NO_CHOICES" in asyncio.run(state.open_world_congress())
    assert conn.opens == []
    assert "WC_STAGE_CONTINUE_REQUESTED|stage:STAGE2" in asyncio.run(state.queue_wc_votes([]))
    assert conn.operations == [(0, "submit", {})]
    assert conn.actions == ["continue"]


@pytest.mark.parametrize("stage", ["STAGE1", "STAGE2"])
@pytest.mark.parametrize("status", [
    {},
    proposal_status(),
    proposal_status({"VotingRestriction": "BOTH"}),
    proposal_status({"Disabled": True}, {"VotingRestriction": "BOTH"}),
])
def test_no_eligible_proposals_allow_only_one_empty_submission(stage, status):
    conn = CongressConn(status, stage=stage)
    state = GameState(conn)
    assert asyncio.run(state.get_world_congress()).proposal_status == "EMPTY"
    assert "WC_NO_CHOICES" in asyncio.run(state.open_world_congress())
    assert f"WC_STAGE_CONTINUE_REQUESTED|stage:{stage}" in asyncio.run(state.queue_wc_votes([]))
    # Native event re-entry and repeated tool calls must not duplicate requests.
    conn.lua.execute("Events.WorldCongressStage1.Fire(); Events.WorldCongressStage2.Fire()")
    assert "WC_REQUEST|CONTINUE_REQUESTED" in asyncio.run(state.queue_wc_votes([]))
    assert conn.opens == []
    assert conn.operations == [(0, "submit", {})]
    assert conn.actions == ["continue"]


@pytest.mark.parametrize("proposal", [
    {},
    {"Disabled": False},
    {"VotingRestriction": "UP"},
    {"VotingRestriction": "DOWN"},
])
def test_any_eligible_proposal_opens_voting_but_rejects_an_empty_plan(proposal):
    conn = CongressConn(proposal_status({"Disabled": True}, proposal))
    state = GameState(conn)
    assert asyncio.run(state.get_world_congress()).proposal_status == "CHOICES"
    assert "WC_VOTING_OPEN|stage:STAGE2" in asyncio.run(state.open_world_congress())
    assert "Explicit proposal votes are required" in asyncio.run(state.queue_wc_votes([]))
    assert conn.opens == ["open"]
    assert conn.operations == []
    assert conn.actions == []


@pytest.mark.parametrize("status", [
    None,
    False,
    "unknown",
    {"Proposals": False},
    {"Proposals": {"EMERGENCY": {}}},
    {"Proposals": {"UNKNOWN": {"ProposalsOfType": []}}},
    proposal_status(False),
    proposal_status({"Disabled": "true"}),
    proposal_status({"VotingRestriction": "UNKNOWN"}),
    proposal_status({"Disabled": True, "VotingRestriction": "UNKNOWN"}),
    proposal_status({}, {"Disabled": "unknown"}),
])
def test_unknown_proposal_data_cannot_open_or_submit(status):
    conn = CongressConn(status)
    state = GameState(conn)
    assert asyncio.run(state.get_world_congress()).proposal_status == "UNKNOWN"
    assert "WC_CHOICES_UNKNOWN" in asyncio.run(state.open_world_congress())
    assert "WC_VOTE_PREFERENCES" in asyncio.run(state.queue_wc_votes([]))
    assert conn.opens == []
    assert conn.operations == []
    assert conn.actions == []


def test_disabled_proposals_do_not_allow_skipping_stage1_resolution_votes():
    conn = CongressConn(
        proposal_status({"Disabled": True}), stage="STAGE1",
        resolutions=[{"Type": 13, "PossibleTargets": ["YIELD_SCIENCE"]}],
    )
    state = GameState(conn)
    assert "WC_VOTING_OPEN|stage:STAGE1" in asyncio.run(state.open_world_congress())
    assert "Missing explicit vote for 13" in asyncio.run(state.queue_wc_votes([]))
    assert conn.operations == []
    assert conn.actions == []
    assert "WC_STAGE_CONTINUE_REQUESTED|stage:STAGE1" in asyncio.run(state.queue_wc_votes([
        {"hash": 13, "option": 1, "target": 0, "votes": 1},
    ]))
    assert conn.operations == [
        (0, "vote", {"hash": 13, "option": 1, "target": 0, "votes": 1}),
        (0, "submit", {}),
    ]
    assert conn.actions == ["continue"]


def test_empty_stage2_ignores_old_resolutions_and_does_not_replay_after_lost_reply():
    conn = CongressConn(
        proposal_status({"VotingRestriction": "BOTH"}),
        resolutions=[{"Type": 13, "PossibleTargets": ["YIELD_SCIENCE"]}],
    )
    state = GameState(conn)
    conn.lose_reply = True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.queue_wc_votes([]))
    assert "WC_REQUEST|CONTINUE_REQUESTED|stage:STAGE2" in asyncio.run(state.queue_wc_votes([]))
    assert conn.operations == [(0, "submit", {})]
    assert conn.actions == ["continue"]


def test_query_reports_all_disabled_proposals_as_no_eligible_choices():
    conn = CongressConn(proposal_status({"Disabled": True}))
    status = asyncio.run(GameState(conn).get_world_congress())
    assert status.proposal_status == "EMPTY"
    assert status.native_stage == "STAGE2"
    assert conn.opens == []
    assert conn.operations == []
    assert conn.actions == []


def test_query_explains_no_eligible_proposals_without_claiming_the_stage_is_empty():
    from civ_mcp.narrate import narrate_world_congress

    conn = CongressConn(proposal_status({"Disabled": True}), stage="STAGE1",
                        resolutions=[{"Type": 13}])
    text = narrate_world_congress(asyncio.run(GameState(conn).get_world_congress()))
    assert "Native proposal eligibility: EMPTY (no eligible proposals)" in text
    assert "Resolution #1" in text
    assert conn.opens == conn.operations == conn.actions == []


@pytest.mark.parametrize(("segment", "in_session", "expected"), [
    ("TURNSEG_WORLDCONGRESS_1", True, "STAGE1"),
    ("TURNSEG_WORLDCONGRESS_2", True, "STAGE2"),
    ("TURNSEG_WORLDCONGRESS_RESOLUTION", True, "RESOLUTION"),
    ("TURNSEG_NORMAL", True, "UNKNOWN"),
    ("TURNSEG_NORMAL", False, "NONE"),
])
def test_native_phase_and_full_query_share_stage_facts_without_writes(segment, in_session, expected):
    from civ_mcp.lua.congress import build_congress_phase_query, parse_congress_phase

    conn = CongressConn(proposal_status())
    conn.lua.globals().segment = segment
    conn.lua.globals().in_session = in_session
    phase = parse_congress_phase(asyncio.run(conn.execute_write(build_congress_phase_query())))
    status = asyncio.run(GameState(conn).get_world_congress())
    assert phase == (in_session, expected, 67, "NONE")
    assert status.native_stage == expected
    assert conn.opens == conn.operations == conn.actions == []


@pytest.mark.parametrize("stage", ["RESOLUTION", "UNKNOWN", "NONE"])
def test_nonvoting_or_unknown_native_stage_cannot_open_or_continue(stage):
    conn = CongressConn(proposal_status(), stage=stage)
    state = GameState(conn)
    assert "WC_OPEN_UNAVAILABLE" in asyncio.run(state.open_world_congress())
    assert "WC_PHASE_UNKNOWN" in asyncio.run(state.queue_wc_votes([]))
    assert conn.opens == conn.operations == conn.actions == []


def test_registered_plan_accepts_only_a_fresh_matching_stage_and_scope():
    conn = CongressConn(proposal_status(), stage="NONE")
    state = GameState(conn)
    conn.lua.globals().in_session = False
    assert "WC_VOTER_REGISTERED" in asyncio.run(state.queue_wc_votes([]))
    assert conn.opens == conn.operations == conn.actions == []
    conn.lua.execute('in_session=true; segment="TURNSEG_WORLDCONGRESS_2"; Events.WorldCongressStage2.Fire()')
    status = asyncio.run(state.get_world_congress())
    assert status.native_stage == "STAGE2"
    assert status.local_request == "CONTINUE_REQUESTED"
    assert conn.operations == [(0, "submit", {})]
    assert conn.actions == ["continue"]


@pytest.mark.parametrize("change", [
    'Game.GetCurrentGameTurn=function() return 68 end',
    'GameConfiguration.GetValue=function() return 999 end',
    'PlayerConfigurations[0].GetCivilizationTypeName=function() return "OTHER" end',
    'Game.GetLocalPlayer=function() return 1 end; PlayerConfigurations[1]=PlayerConfigurations[0]; Players[1]=Players[0]',
    'segment="TURNSEG_WORLDCONGRESS_1"',
])
def test_registered_plan_does_not_submit_after_its_scope_or_stage_changes(change):
    conn = CongressConn(proposal_status())
    state = GameState(conn)
    conn.lua.globals().in_session = False
    assert "WC_VOTER_REGISTERED" in asyncio.run(state.queue_wc_votes([]))
    conn.lua.execute(change + '; in_session=true; Events.WorldCongressStage2.Fire()')
    status = asyncio.run(state.get_world_congress())
    assert status.local_request in ("FAILED", "STALE")
    assert conn.opens == conn.operations == conn.actions == []


def test_completed_stage1_receipt_does_not_confirm_or_revote_stage2():
    conn = CongressConn(proposal_status(), stage="STAGE1", resolutions=[{"Type": 13}])
    state = GameState(conn)
    assert "WC_STAGE_CONTINUE_REQUESTED|stage:STAGE1" in asyncio.run(state.queue_wc_votes([
        {"hash": 13, "option": 1, "target": 0, "votes": 1},
    ]))
    conn.lua.globals().segment = "TURNSEG_WORLDCONGRESS_2"
    status = asyncio.run(state.get_world_congress())
    assert status.local_request == "STALE"
    assert status.resolutions == []
    assert "WC_STAGE_CONTINUE_REQUESTED|stage:STAGE2" in asyncio.run(state.queue_wc_votes([]))
    assert [op[1] for op in conn.operations] == ["vote", "submit", "submit"]
    assert conn.actions == ["continue", "continue"]


def test_missing_native_ui_context_never_starts_voting():
    conn = CongressConn(proposal_status({}))
    conn.lua.execute("ContextPtr.LookUpControl=function() return nil end")
    assert "OUTCOME_UNKNOWN" in asyncio.run(GameState(conn).open_world_congress())
    assert conn.opens == conn.operations == conn.actions == []


def test_submitted_stage_cannot_be_reopened_even_if_choices_change():
    conn = CongressConn(proposal_status())
    state = GameState(conn)
    assert "WC_STAGE_CONTINUE_REQUESTED" in asyncio.run(state.queue_wc_votes([]))
    conn.lua.globals().proposals = conn.lua.table_from(proposal_status({}), recursive=True)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.open_world_congress())
    assert conn.opens == []
    assert conn.operations == [(0, "submit", {})]
    assert conn.actions == ["continue"]


def test_lost_open_reply_only_confirms_visible_native_ui_without_reopening():
    conn = CongressConn(proposal_status({}))
    state = GameState(conn)
    conn.lose_reply = True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.open_world_congress())
    assert "WC_VOTING_ALREADY_OPEN" in asyncio.run(state.open_world_congress())
    assert conn.opens == ["open"]
    assert conn.operations == conn.actions == []

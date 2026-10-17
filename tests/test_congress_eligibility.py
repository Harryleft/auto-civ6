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
        globals_.segment = f"TURNSEG_WORLDCONGRESS_{1 if stage == 'STAGE1' else 2}"
        self.lua.execute("""
Game = {
    GetLocalPlayer = function() return 0 end,
    GetCurrentGameTurn = function() return 67 end,
    GetCurrentTurnSegment = function() return segment end,
    GetWorldCongress = function() return {
        IsInSession = function() return true end,
        GetProposals = function() return proposals end,
        GetResolutions = function() return resolutions end,
        GetVotesandFavorCost = function() return {MaxVotes=5, [0]=0} end,
    } end,
}
PlayerConfigurations = {[0]={GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end}}
GameConfiguration = {GetValue=function() return 719665280 end, IsPaused=function() return false end}
Players = {[0]={GetFavor=function() return 10 end}}
DB = {MakeHash=function(value) return value end}
GameInfo = {
    Resolutions = {[13]={}},
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
    return {"Proposals": {"EMERGENCY": {"ProposalsOfType": list(proposals)}}}


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

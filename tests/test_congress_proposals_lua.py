"""World Congress public GameState seam with real Lua and isolated native fixtures.

Proposal values are schema fixtures from official UI source, not a turn-167 dump.
"""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_world_congress
from test_congress_eligibility import CongressConn


FAIR_KEY = "WC_EMERGENCY_WORLD_FAIR_-1_NONE"
FAIR = {"TypeName": "WC_EMERGENCY_WORLD_FAIR", "Type": 61, "Target": -1,
        "Name": "World's Fair", "Description": "Competition description", "EmergencyType": 91}
RESOLUTION = {"Type": 13, "PossibleTargets": ["YIELD_SCIENCE"]}


class ProposalConn(CongressConn):
    def __init__(self, *, stage="STAGE1"):
        super().__init__({}, stage=stage, resolutions=[RESOLUTION])
        self.lua.globals().proposals = self.lua.table_from({"Proposals": {
            "COMPETITION": {"FavorCost": {"MaxVotes": 3, 0: 2, 1: 7, 2: 15},
                            "ProposalsOfType": [FAIR]},
        }}, recursive=True)
        self.lua.execute('''
favor=25
costs={MaxVotes=3, [0]=0, [1]=6, [2]=18}
local world=Game.GetWorldCongress()
world.GetVotesandFavorCost=function() return costs end
Game.GetWorldCongress=function() return world end
Players[0].GetFavor=function() return favor end
GameInfo.ProposalTypes.COMPETITION={Sort=1}
GameInfo.ProposalTypes.EMERGENCY={Sort=2}
PlayerOperations.WORLD_CONGRESS_DISCUSSION_VOTE="discussion"
PlayerOperations.PARAM_PLAYER_ONE="sender"
PlayerOperations.PARAM_PLAYER_TWO="target"
''')


def run(awaitable):
    return asyncio.run(awaitable)


def test_query_discloses_competition_identity_none_sender_rules_limits_and_category_costs():
    conn = ProposalConn()
    status = run(GameState(conn).get_world_congress())
    assert status.proposal_status == "CHOICES"
    assert len(status.proposals) == 1
    proposal = status.proposals[0]
    assert proposal.proposal_key == FAIR_KEY
    assert proposal.sender_id is None and proposal.target_id == -1
    assert proposal.name == "World's Fair" and proposal.description == "Competition description"
    assert proposal.favor_costs == [2, 7, 15]
    assert proposal.can_upvote and proposal.can_downvote and proposal.eligible
    text = narrate_world_congress(status)
    assert FAIR_KEY in text and "World's Fair" in text and "Competition description" in text
    assert '"proposal_key"' in text and '"option"' in text
    assert conn.operations == conn.actions == conn.opens == []


def test_complete_mixed_plan_reserves_minimum_costs_and_uses_native_discussion_arguments():
    conn = ProposalConn()
    state = GameState(conn)
    result = run(state.queue_wc_votes([
        {"hash": 13, "option": 1, "target": 0, "votes": 3},
        {"proposal_key": FAIR_KEY, "option": 2, "votes": 3},
    ]))
    assert "WC_STAGE_CONTINUE_REQUESTED|stage:STAGE1" in result
    assert conn.operations == [
        (0, "vote", {"hash": 13, "option": 1, "target": 0, "votes": 3}),
        (0, "discussion", {"target": -1, "votes": -2}),
        (0, "submit", {}),
    ]
    assert conn.actions == ["continue"]
    assert "cost=18" in result and "cost=7" in result and "favor=25/25" in result


RES_VOTE = {"hash": 13, "option": 1, "target": 0, "votes": 3}
PROP_VOTE = {"proposal_key": FAIR_KEY, "option": 1, "votes": 3}


def test_budget_priority_follows_input_order_but_native_send_order_still_starts_with_resolutions():
    conn = ProposalConn()
    result = run(GameState(conn).queue_wc_votes([PROP_VOTE, RES_VOTE]))
    assert conn.operations == [
        (0, "vote", {"hash": 13, "option": 1, "target": 0, "votes": 2}),
        (0, "discussion", {"target": -1, "votes": 3}),
        (0, "submit", {}),
    ]
    assert "favor=21/25" in result
    status = run(GameState(conn).get_world_congress())
    assert "cost=15" in status.vote_allocation and "cost=6" in status.vote_allocation


def test_stage2_submits_only_proposals_and_includes_the_real_sender():
    conn = ProposalConn(stage="STAGE2")
    conn.lua.execute('proposals.Proposals.COMPETITION.ProposalsOfType[1].Sender=3')
    key = "WC_EMERGENCY_WORLD_FAIR_-1_3"
    state = GameState(conn)
    status = run(state.get_world_congress())
    assert status.resolutions == [] and status.proposals[0].sender_id == 3
    result = run(state.queue_wc_votes([{"proposal_key": key, "option": 2, "votes": 2}]))
    assert "WC_STAGE_CONTINUE_REQUESTED|stage:STAGE2" in result
    assert conn.operations == [(0, "discussion", {"sender": 3, "target": -1, "votes": -2}), (0, "submit", {})]
    assert conn.actions == ["continue"]


def test_optional_type_and_category_fallback_costs_match_official_proposal_schema():
    conn = ProposalConn(stage="STAGE2")
    conn.lua.execute('''
proposals.Proposals.COMPETITION.FavorCost=nil
proposals.Proposals.COMPETITION.ProposalsOfType[1].Type=nil
''')
    state = GameState(conn)
    status = run(state.get_world_congress())
    assert status.proposal_status == "CHOICES"
    assert status.proposals[0].proposal_type is None
    assert status.proposals[0].favor_costs == [0, 6, 18]
    assert "favor=18/25" in run(state.queue_wc_votes([PROP_VOTE]))
    assert conn.operations[0] == (0, "discussion", {"target": -1, "votes": 3})


@pytest.mark.parametrize("setup", [
    'proposals.Proposals.COMPETITION.ProposalsOfType[1].Disabled=true',
    'proposals.Proposals.COMPETITION.ProposalsOfType[1].VotingRestriction="BOTH"',
])
def test_all_proposals_forbidden_are_visible_but_need_no_implicit_vote(setup):
    conn = ProposalConn(stage="STAGE2")
    conn.lua.execute(setup)
    state = GameState(conn)
    status = run(state.get_world_congress())
    assert status.proposal_status == "EMPTY" and len(status.proposals) == 1
    assert not status.proposals[0].eligible
    assert "options: none" in narrate_world_congress(status)
    assert "WC_NO_CHOICES" in run(state.open_world_congress())
    assert "WC_STAGE_CONTINUE_REQUESTED" in run(state.queue_wc_votes([]))
    assert conn.operations == [(0, "submit", {})] and conn.actions == ["continue"]


@pytest.mark.parametrize("restriction, illegal, legal", [("UP", 1, 2), ("DOWN", 2, 1)])
def test_one_direction_restriction_is_enforced_before_submission_and_failed_plan_can_be_corrected(restriction, illegal, legal):
    conn = ProposalConn(stage="STAGE2")
    conn.lua.execute(f'proposals.Proposals.COMPETITION.ProposalsOfType[1].VotingRestriction="{restriction}"')
    state = GameState(conn)
    status = run(state.get_world_congress())
    assert status.proposal_status == "CHOICES"
    assert "blocked" in run(state.queue_wc_votes([PROP_VOTE | {"option": illegal}]))
    assert conn.operations == conn.actions == []
    assert "WC_STAGE_CONTINUE_REQUESTED" in run(state.queue_wc_votes([PROP_VOTE | {"option": legal}]))
    assert conn.operations[0][2]["votes"] == (3 if legal == 1 else -3)


@pytest.mark.parametrize("plan", [
    [], [RES_VOTE], [PROP_VOTE],
    [RES_VOTE, PROP_VOTE | {"proposal_key": "WC_OLD_-1_NONE"}],
    [RES_VOTE | {"target": 99}, PROP_VOTE],
])
def test_incomplete_stale_or_bad_target_plan_never_sends_any_operation(plan):
    conn = ProposalConn()
    assert "WC_VOTE_PREFERENCES" in run(GameState(conn).queue_wc_votes(plan))
    assert conn.operations == conn.actions == []


@pytest.mark.parametrize("setup", [
    'proposals.Proposals.COMPETITION.ProposalsOfType[1].TypeName=nil',
    'proposals.Proposals.COMPETITION.ProposalsOfType[1].Target=nil',
    'proposals.Proposals.COMPETITION.ProposalsOfType[1].Sender="3"',
    'proposals.Proposals.COMPETITION.ProposalsOfType[1].Disabled="false"',
    'proposals.Proposals.COMPETITION.ProposalsOfType[1].VotingRestriction="UNKNOWN"',
    'proposals.Proposals.COMPETITION.ProposalsOfType[1].ProposalBlockerType="UNKNOWN"',
    'proposals.Proposals.COMPETITION.FavorCost={MaxVotes=3, [0]=0, [2]=15}',
    'proposals.Proposals.COMPETITION.FavorCost={MaxVotes=3, [0]=-1, [1]=7, [2]=15}',
    'proposals.Proposals.COMPETITION.FavorCost={MaxVotes=3, [0]=2, [1]=1, [2]=15}',
    'proposals.Proposals.COMPETITION.FavorCost={MaxVotes=3, [0]=2, [1]=math.huge, [2]=15}',
    'proposals.Proposals.COMPETITION.FavorCost={MaxVotes=3, [0]=2, [1]=0/0, [2]=15}',
    'proposals.Proposals.COMPETITION.FavorCost=0',
    'proposals.Proposals.OTHER={ProposalsOfType={}}',
])
def test_unknown_proposal_identity_restriction_or_cost_is_shared_by_query_open_and_vote(setup):
    conn = ProposalConn()
    conn.lua.execute(setup)
    state = GameState(conn)
    status = run(state.get_world_congress())
    assert status.proposal_status == "UNKNOWN"
    assert "UNKNOWN" in narrate_world_congress(status)
    assert "WC_CHOICES_UNKNOWN" in run(state.open_world_congress())
    assert "WC_VOTE_PREFERENCES" in run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert conn.operations == conn.actions == conn.opens == []


@pytest.mark.parametrize("setup", [
    'costs[1]=nil', 'costs[0]=-1', 'costs.MaxVotes=0',
    'costs[1]="6"', 'costs[1]=math.huge', 'costs[1]=0/0',
    'favor=1', 'favor=-1', 'favor=0/0',
])
def test_missing_invalid_cost_or_insufficient_minimum_budget_rejects_before_any_vote(setup):
    conn = ProposalConn()
    conn.lua.execute(setup)
    assert "WC_VOTE_PREFERENCES" in run(GameState(conn).queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert conn.operations == conn.actions == []


def test_first_resolution_vote_is_not_assumed_free_when_reserving_later_proposal_minimum():
    conn = ProposalConn()
    conn.lua.execute('costs={MaxVotes=2,[0]=4,[1]=10}; favor=6')
    result = run(GameState(conn).queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert "favor=6/6" in result
    assert conn.operations == [
        (0, "vote", {"hash": 13, "option": 1, "target": 0, "votes": 1}),
        (0, "discussion", {"target": -1, "votes": 1}), (0, "submit", {}),
    ]


def test_non_session_proposals_are_not_presented_as_current_ballots():
    conn = ProposalConn(stage="NONE")
    conn.lua.globals().in_session = False
    status = run(GameState(conn).get_world_congress())
    assert status.proposals == [] and not status.is_in_session
    assert conn.operations == conn.actions == []


def test_unreadable_native_costs_are_unknown_in_query_and_never_grant_free_votes():
    conn = ProposalConn()
    conn.lua.execute('''
proposals.Proposals.COMPETITION.FavorCost=nil
Game.GetWorldCongress().GetVotesandFavorCost=function() error("cost read unavailable") end
''')
    state = GameState(conn)
    status = run(state.get_world_congress())
    assert status.vote_cost_status == "UNKNOWN"
    assert status.proposal_status == "UNKNOWN"
    assert "WC_VOTE_PREFERENCES" in run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert conn.operations == conn.actions == []


def test_previous_stage_allocation_is_not_reported_as_the_current_stage_plan():
    conn = ProposalConn()
    state = GameState(conn)
    assert "WC_STAGE_CONTINUE_REQUESTED" in run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    conn.lua.globals().segment = "TURNSEG_WORLDCONGRESS_2"
    status = run(state.get_world_congress())
    assert status.local_request == "STALE"
    assert status.vote_allocation == ""


@pytest.mark.parametrize("plan", [
    [PROP_VOTE, PROP_VOTE],
    [RES_VOTE, RES_VOTE],
    [PROP_VOTE | {"hash": 13}],
    [RES_VOTE | {"proposal_key": FAIR_KEY}],
    [PROP_VOTE | {"target": -1}],
    [PROP_VOTE | {"option": True}],
    [PROP_VOTE | {"option": 0}],
    [PROP_VOTE | {"votes": 0}],
    [PROP_VOTE | {"proposal_key": ""}],
    [PROP_VOTE | {"proposal_key": "bad\nkey"}],
    [PROP_VOTE | {"extra": "unknown"}],
])
def test_mixed_identifiers_duplicates_and_invalid_proposal_input_never_reach_native_connection(plan):
    from test_congress_votes import FakeConn
    conn = FakeConn()
    with pytest.raises(ValueError):
        run(GameState(conn).queue_wc_votes(plan))
    assert conn.calls == []


@pytest.mark.parametrize("setup", [
    'proposals.Proposals.COMPETITION.ProposalsOfType[2]=proposals.Proposals.COMPETITION.ProposalsOfType[1]',
    '''proposals.Proposals.COMPETITION.ProposalsOfType[2]={TypeName="WC_OTHER",Target=-1,
            Name="Other competition",Description="Same native sender/target pair"}''',
    'proposals.Proposals.COMPETITION.ProposalsOfType[2]={TypeName="WC_UNKNOWN",Target=4,Name="Missing description"}',
])
def test_duplicate_or_ambiguous_native_proposals_and_unknown_siblings_fail_closed(setup):
    conn = ProposalConn()
    conn.lua.execute(setup)
    state = GameState(conn)
    assert run(state.get_world_congress()).proposal_status == "UNKNOWN"
    assert "WC_CHOICES_UNKNOWN" in run(state.open_world_congress())
    assert "WC_VOTE_PREFERENCES" in run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert conn.operations == conn.actions == conn.opens == []


@pytest.mark.parametrize("failure", ["vote", "discussion", "submit", "continue"])
def test_partial_native_failure_never_replays_votes_submit_or_continuation(failure):
    conn = ProposalConn()
    conn.lua.globals().fail_operation = failure
    conn.lua.execute('''
UI.RequestPlayerOperation=function(owner, operation, parameters)
    record_operation(owner, operation, parameters)
    if operation==fail_operation then error("native result lost") end
end
UI.RequestAction=function(action)
    record_action(action)
    if fail_operation=="continue" then error("continuation result lost") end
end
''')
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    sent, actions = list(conn.operations), list(conn.actions)
    conn.lua.execute('Events.WorldCongressStage1.Fire(); Events.WorldCongressStage2.Fire()')
    repeated = run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert "WC_REQUEST|" in repeated
    assert conn.operations == sent and conn.actions == actions
    status = run(state.get_world_congress())
    assert status.local_request in {"VOTES_REQUESTED", "SUBMIT_REQUESTED", "CONTINUE_REQUESTED"}
    assert "favor=25/25" in status.vote_allocation


def test_lost_complete_reply_only_reads_the_original_receipt_and_allocation_on_repeat():
    conn = ProposalConn()
    state = GameState(conn)
    conn.lose_reply = True
    assert "OUTCOME_UNKNOWN" in run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    sent = list(conn.operations)
    repeat = run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert "CONTINUE_REQUESTED" in repeat and "favor=25/25" in repeat
    assert conn.operations == sent and conn.actions == ["continue"]


def test_event_reentry_during_each_native_operation_does_not_reenter_the_plan():
    conn = ProposalConn()
    conn.lua.execute('''
UI.RequestPlayerOperation=function(owner, operation, parameters)
    record_operation(owner, operation, parameters)
    Events.WorldCongressStage1.Fire(); Events.WorldCongressStage2.Fire()
end
''')
    state = GameState(conn)
    assert "WC_STAGE_CONTINUE_REQUESTED" in run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert [op[1] for op in conn.operations] == ["vote", "discussion", "submit"]
    assert conn.actions == ["continue"]


def test_pending_plan_waits_for_its_actual_native_stage_and_unknown_scope_cannot_send():
    conn = ProposalConn(stage="NONE")
    conn.lua.globals().in_session = False
    state = GameState(conn)
    assert "WC_VOTER_REGISTERED" in run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert conn.operations == conn.actions == []
    conn.lua.execute('in_session=true; segment="TURNSEG_WORLDCONGRESS_1"; Events.WorldCongressStage1.Fire()')
    assert [op[1] for op in conn.operations] == ["vote", "discussion", "submit"]
    assert conn.actions == ["continue"]


def test_scope_changing_during_prevalidation_sends_nothing():
    conn = ProposalConn()
    conn.lua.execute('''
Game.GetWorldCongress().GetVotesandFavorCost=function()
    Game.GetCurrentGameTurn=function() return 68 end
    return costs
end
''')
    assert "scope changed during validation" in run(GameState(conn).queue_wc_votes([RES_VOTE, PROP_VOTE]))
    assert conn.operations == conn.actions == []


def test_emergency_with_native_category_hash_and_real_players_is_readable_without_optional_type():
    conn = ProposalConn(stage="STAGE2")
    conn.lua.execute('''
GameInfo.ProposalTypes[777]={ProposalType="WC_PROPOSAL_TYPE_EMERGENCY",Name="Emergency proposals"}
PlayerConfigurations[2]={GetCivilizationShortDescription=function() return "Sweden" end}
PlayerConfigurations[3]={GetCivilizationShortDescription=function() return "Canada" end}
proposals={Proposals={[777]={ProposalsOfType={{TypeName="WC_EMERGENCY_RELIGIOUS",Target=2,Sender=3,
    Name="Religious emergency",Description="Emergency goal",EmergencyType=-228935954}}}}}
''')
    state = GameState(conn)
    status = run(state.get_world_congress())
    row = status.proposals[0]
    assert row.sender_name == "Canada" and row.target_name == "Sweden"
    assert row.proposal_type is None and row.category_name == "Emergency proposals"
    assert "Emergency proposals" in narrate_world_congress(status)
    assert "WC_STAGE_CONTINUE_REQUESTED" in run(state.queue_wc_votes([
        {"proposal_key": "WC_EMERGENCY_RELIGIOUS_2_3", "option": 1, "votes": 2},
    ]))
    assert conn.operations[0] == (0, "discussion", {"sender": 3, "target": 2, "votes": 2})


def test_utf8_proposal_text_preserves_chinese_while_normalizing_ascii_protocol_bytes():
    import locale

    previous = locale.setlocale(locale.LC_CTYPE)
    try:
        # Darwin UTF-8 ctype marks some UTF-8 continuation bytes as %c.
        # Other hosts still exercise public Unicode and protocol normalization.
        for name in ("zh_CN.UTF-8", "en_US.UTF-8"):
            try:
                locale.setlocale(locale.LC_CTYPE, name)
                break
            except locale.Error:
                pass
        conn = ProposalConn()
        row = conn.lua.globals().proposals.Proposals.COMPETITION.ProposalsOfType[1]
        row.Name = "世界博览会|政策"
        row.Description = "中文\t说明\x00结束\x1f\x7f"
        conn.lua.globals().GameInfo.ProposalTypes.COMPETITION.Name = "竞赛|议题"
        conn.lua.globals().GameInfo.Resolutions[13].Name = "外交决议"
        status = run(GameState(conn).get_world_congress())
        assert status.proposal_status == "CHOICES"
        assert status.proposals[0].name == "世界博览会 政策"
        assert status.proposals[0].description == "中文 说明 结束  "
        assert status.proposals[0].category_name == "竞赛 议题"
        text = narrate_world_congress(status)
        assert "世界博览会 政策" in text and "外交决议" in text
        assert "�" not in text
        assert conn.operations == conn.actions == []
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)


@pytest.mark.parametrize("finished", [False, True])
def test_requested_or_finished_congress_window_only_guides_confirmation_not_a_new_plan(finished):
    conn = ProposalConn()
    state = GameState(conn)
    assert "WC_STAGE_CONTINUE_REQUESTED" in run(state.queue_wc_votes([RES_VOTE, PROP_VOTE]))
    if finished:
        conn.lua.execute('in_session=false; segment="TURNSEG_NORMAL"')
    status = run(state.get_world_congress())
    assert status.turns_until_next == 0
    assert status.local_request == ("FINISHED" if finished else "CONTINUE_REQUESTED")
    text = narrate_world_congress(status)
    assert "FIRES THIS TURN" not in text
    assert "queue_wc_votes(" not in text
    assert "end_turn" in text and status.local_request in text
    assert "favor=25/25" in text
    assert [op[1] for op in conn.operations] == ["vote", "discussion", "submit"]
    assert conn.actions == ["continue"]

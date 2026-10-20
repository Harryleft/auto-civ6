"""Terminal outcome and save boundaries through real GameState and Lua 5.1."""

import asyncio
import sys

import pytest

from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState


class TerminalConn:
    def __init__(self):
        self.lua=LuaRuntime()
        self.rows=[]
        self.calls=[]
        self.fail_ingame=False
        self.extra_rows=[]
        self.lua.globals().print=lambda row:self.rows.append(str(row))
        self.lua.execute('''
winner,selector=17,-77
teams={[0]=3,[1]=1,[2]=17}
alive=true
Game={GetLocalPlayer=function() return 0 end,
      GetCurrentGameTurn=function() return 244 end,GetMaxTurns=function() return 0 end,
      GetWinningTeam=function() return winner,selector end}
GameInfo={Victories={[-77]={VictoryType="VICTORY_TECHNOLOGY"}}}
Locale={Lookup=function(value) return value end}
Players,PlayerConfigurations={},{}
for id=0,2 do
    local owner=id
    Players[id]={IsMajor=function() return true end,IsAlive=function() return owner~=0 or alive end,
        GetTeam=function() return teams[owner] end,
        GetStats=function() return {
            GetTourism=function() return 9999 end,
            GetScienceVictoryPoints=function() return 36 end,
            GetScienceVictoryPointsTotalNeeded=function() return 33.5 end,
            GetDiplomaticVictoryPoints=function() return 20 end,
        } end,
        GetCulture=function() return {GetStaycationers=function() return 1 end} end,
    }
    local name=owner==2 and "Sweden" or "Sumeria"
    PlayerConfigurations[id]={GetCivilizationShortDescription=function() return name end,
                              GetLeaderName=function() return owner==2 and "Kristina" or "Gilgamesh" end}
end
ContextPtr={LookUpControl=function() return {IsHidden=function() return true end} end}
''')

    def execute(self,context,source):
        self.calls.append(context)
        if context=='write' and self.fail_ingame:
            raise ConnectionError('InGame unavailable')
        self.rows.clear()
        self.lua.execute(source)
        return [row for row in self.rows if row!='---END---']+self.extra_rows

    async def execute_write(self,source,**kwargs):
        return self.execute('write',source)

    async def execute_read(self,source,**kwargs):
        return self.execute('read',source)


def run(awaitable):
    return asyncio.run(awaitable)


def test_native_technology_result_survives_decimal_progress_and_gamecore_fallback():
    conn=TerminalConn()
    status=run(GameState(conn).check_game_over())
    assert status is not None and status.is_defeat
    assert status.victory_type=='VICTORY_TECHNOLOGY'
    assert status.winner_name=='Sweden'
    assert conn.calls==['write']
    conn.fail_ingame=True
    status=run(GameState(conn).check_game_over())
    assert status is not None and status.victory_type=='VICTORY_TECHNOLOGY'


@pytest.mark.parametrize("official_type,extra", [
    ("VICTORY_TECHNOLOGY", "VP_DATA|2|336|420|36|33.5|12"),
    ("VICTORY_CULTURE", "VP_DATA|2|malformed|unknown|broken"),
    ("VICTORY_SCORE", "VP_DATA|2|9999|1|36|33|20"),
])
def test_progress_metadata_cannot_break_or_overwrite_the_official_outcome(official_type,extra):
    conn=TerminalConn()
    conn.lua.globals().official_type=official_type
    conn.lua.execute('GameInfo.Victories[-77].VictoryType=official_type')
    conn.extra_rows=[extra]
    status=run(GameState(conn).check_game_over())
    assert status is not None and status.victory_type==official_type
    assert conn.calls==['write']


class SaveBoundaryConn(TerminalConn):
    def __init__(self,directory):
        super().__init__()
        self.terminal_after_overview=False
        self.lua.globals().write_save=lambda name:(directory/(name+'.Civ6Save')).write_bytes(b'native completed checkpoint')
        self.lua.execute('''
GameConfiguration={GetValue=function() return 719665280 end}
PlayerConfigurations[0].GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end
save_requests,save_adds=0,0
UI={GetInterfaceMode=function() return 1 end}
InterfaceModeTypes={SELECTION=1,CINEMATIC=2}
finish_save=true
local listeners={}
Events={SaveComplete={
    Add=function(handler) save_adds=save_adds+1; listeners[handler]=true end,
    Remove=function(handler) listeners[handler]=nil end,
}}
function emit_save_complete()
    local callbacks={}
    for handler in pairs(listeners) do table.insert(callbacks,handler) end
    for _,handler in ipairs(callbacks) do handler() end
end
Network={SaveGame=function(file)
    save_requests=save_requests+1
    if finish_save then
        write_save(file.Name)
        emit_save_complete()
    end
    return true
end}
SaveLocations={LOCAL_STORAGE=1}
SaveTypes={SINGLE_PLAYER=2}
''')

    def execute(self,context,source):
        from civ_mcp import lua as lq
        if source==lq.build_overview_query():
            self.calls.append(context)
            if self.terminal_after_overview:
                self.lua.execute('winner=17')
            return ['244|0|Sumeria|Gilgamesh|822|72|123.6|54.2|1394|Refining|Mobilization|2|7|326|287|6|27|119|47']
        return super().execute(context,source)


@pytest.fixture
def save_environment(monkeypatch,tmp_path):
    from civ_mcp import checkpoints,game_launcher,game_lifecycle
    monkeypatch.setattr(game_launcher,'SINGLE_SAVE_DIR',str(tmp_path))
    monkeypatch.setattr(checkpoints,'CHECKPOINT_DIR',tmp_path/'metadata')
    # 文件与事件双证据测试保留 Windows 调度余量，不改变运行层实际超时。
    monkeypatch.setattr(game_lifecycle,'SAVE_CONFIRM_TIMEOUT',0.2 if sys.platform == 'win32' else 0.03)
    monkeypatch.setattr(game_lifecycle,'SAVE_POLL_INTERVAL',0.001)
    assert checkpoints.begin_baseline_run() is None
    return tmp_path


def test_confirmed_terminal_rejects_save_without_native_request_listener_or_new_pending(save_environment):
    conn=SaveBoundaryConn(save_environment)
    state=GameState(conn)
    result=run(state.save_game('terminal'))
    assert result.startswith('Error: GAME_OVER|'),result
    assert not state.save_pending
    assert conn.lua.globals().save_requests==conn.lua.globals().save_adds==0
    assert not (save_environment/'terminal.Civ6Save').exists()



def test_terminal_appearing_during_save_preparation_is_refused_before_native_listener(save_environment):
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    conn.terminal_after_overview=True
    state=GameState(conn)
    result=run(state.save_game('late_terminal'))
    assert result.startswith('Error: GAME_OVER|'),result
    assert not state.save_pending
    assert conn.lua.globals().save_requests==conn.lua.globals().save_adds==0
    assert not (save_environment/'late_terminal.Civ6Save').exists()


def test_native_terminal_refusal_preserves_an_existing_verified_checkpoint(save_environment):
    from civ_mcp import checkpoints
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    state=GameState(conn)
    assert run(state.save_game('checkpoint')).startswith('Saved:')
    expected=('sumeria',719665280,244)
    assert checkpoints.expected_checkpoint('checkpoint')==expected
    before=(save_environment/'checkpoint.Civ6Save').read_bytes()
    conn.terminal_after_overview=True
    assert run(state.save_game('checkpoint')).startswith('Error: GAME_OVER|')
    assert checkpoints.expected_checkpoint('checkpoint')==expected
    assert (save_environment/'checkpoint.Civ6Save').read_bytes()==before
    assert conn.lua.globals().save_requests==conn.lua.globals().save_adds==1
    assert not state.save_pending


@pytest.mark.parametrize('victory_type',[
    'VICTORY_TECHNOLOGY','VICTORY_CULTURE','VICTORY_RELIGIOUS',
    'VICTORY_DIPLOMATIC','VICTORY_CONQUEST','VICTORY_SCORE',
])
@pytest.mark.parametrize('fallback',[False,True])
def test_every_native_victory_mapping_is_identical_in_both_contexts(victory_type,fallback):
    conn=TerminalConn()
    conn.fail_ingame=fallback
    conn.lua.globals().official_type=victory_type
    conn.lua.execute('GameInfo.Victories[-77].VictoryType=official_type')
    status=run(GameState(conn).check_game_over())
    assert status is not None and status.victory_type==victory_type
    assert status.is_defeat and status.winner_name=='Sweden'
    assert conn.calls==(['write','read'] if fallback else ['write'])


@pytest.mark.parametrize('fallback',[False,True])
def test_same_team_wins_even_when_another_player_is_displayed(fallback):
    conn=TerminalConn()
    conn.fail_ingame=fallback
    conn.lua.execute('Game.GetLocalPlayer=function() return 2 end; teams[0]=17')
    status=run(GameState(conn).check_game_over())
    assert status is not None and not status.is_defeat
    assert status.winner_name=='Sumeria'
    assert status.victory_type=='VICTORY_TECHNOLOGY'


@pytest.mark.parametrize('setup',[
    'selector=nil',
    'selector=999',
    'selector=1.5; GameInfo.Victories[1.5]=GameInfo.Victories[-77]',
    'selector=math.huge; GameInfo.Victories[math.huge]=GameInfo.Victories[-77]',
    'selector=true; GameInfo.Victories[true]=GameInfo.Victories[-77]',
    'GameInfo.Victories=nil',
    'GameInfo.Victories[-77].VictoryType=nil',
    'GameInfo.Victories[-77].VictoryType=12',
    'GameInfo.Victories[-77].VictoryType=""',
    'GameInfo.Victories=setmetatable({},{__index=function() error("type unavailable") end})',
])
def test_missing_or_malformed_type_stays_unknown_despite_high_progress(setup):
    conn=TerminalConn()
    conn.lua.execute(setup)
    status=run(GameState(conn).check_game_over())
    assert status is not None and status.is_defeat
    assert status.victory_type=='Unknown'
    assert conn.calls==['write']


def test_no_winner_is_active_despite_high_progress_and_visible_endgame_control():
    conn=TerminalConn()
    conn.lua.execute('winner=nil; ContextPtr.LookUpControl=function() return {IsHidden=function() return false end} end')
    assert run(GameState(conn).check_game_over()) is None


@pytest.mark.parametrize('fallback',[False,True])
def test_player_elimination_remains_terminal_without_a_formal_winning_team(fallback):
    conn=TerminalConn()
    conn.fail_ingame=fallback
    conn.lua.execute('winner=nil; alive=false')
    status=run(GameState(conn).check_game_over())
    assert status is not None and status.is_defeat and not status.player_alive
    assert status.victory_type=='Elimination'


def test_existing_turn_limit_detection_does_not_invent_a_score_victory():
    conn=TerminalConn()
    conn.lua.execute('winner=nil; Game.GetMaxTurns=function() return 244 end')
    status=run(GameState(conn).check_game_over())
    assert status is not None and status.is_defeat
    assert status.victory_type=='TurnLimit'


def test_unknown_terminal_type_still_refuses_a_new_save(save_environment):
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('selector=nil')
    state=GameState(conn)
    assert run(state.save_game('unknown_type')).startswith('Error: GAME_OVER|')
    assert not state.save_pending
    assert conn.lua.globals().save_requests==conn.lua.globals().save_adds==0


def test_active_turn_save_still_requires_one_native_request_and_exact_file_completion(save_environment):
    from civ_mcp import checkpoints
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    state=GameState(conn)
    assert run(state.save_game('active')).startswith('Saved: active; native completion and file confirmed')
    assert not state.save_pending
    assert conn.lua.globals().save_requests==conn.lua.globals().save_adds==1
    assert checkpoints.expected_checkpoint('active')==('sumeria',719665280,244)


def test_old_unknown_save_still_blocks_new_saves_and_menu_restore_even_at_terminal(save_environment):
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil; finish_save=false')
    state=GameState(conn)
    assert run(state.save_game('unknown')).startswith('ERR:OUTCOME_UNKNOWN|')
    assert state.save_pending
    calls=len(conn.calls)
    conn.lua.execute('winner=17')
    assert run(state.save_game('another')).startswith('ERR:OUTCOME_UNKNOWN|')
    assert run(state.reload_game_save('checkpoint')).startswith('ERR:OUTCOME_UNKNOWN|')
    assert state.save_pending and len(conn.calls)==calls
    assert conn.lua.globals().save_requests==conn.lua.globals().save_adds==1


def test_a_hung_possible_submission_invalidates_old_recovery_even_when_save_file_is_unchanged(save_environment):
    from civ_mcp import checkpoints
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    state=GameState(conn)
    assert run(state.save_game('checkpoint')).startswith('Saved:')
    assert checkpoints.expected_checkpoint('checkpoint') is not None
    conn.lua.execute('finish_save=false')
    original=conn.execute_write
    async def execute(source,**kwargs):
        result=await original(source,**kwargs)
        if 'Network.SaveGame' in source:
            await asyncio.Future()
        return result
    conn.execute_write=execute
    assert run(state.save_game('checkpoint')).startswith('ERR:OUTCOME_UNKNOWN|')
    assert state.save_pending
    assert checkpoints.expected_checkpoint('checkpoint') is None
    assert conn.lua.globals().save_requests==2


def test_cancelled_save_preserves_pending_and_invalidates_old_checkpoint(save_environment):
    from civ_mcp import checkpoints,server
    from test_server import make_ctx
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    state=GameState(conn)
    ctx=make_ctx(game=state)
    assert run(state.save_game('checkpoint')).startswith('Saved:')
    conn.lua.execute('finish_save=false')
    original=conn.execute_write

    async def cancel_after_native_entry():
        entered=asyncio.Event()

        async def execute(source,**kwargs):
            requests=conn.lua.globals().save_requests
            result=await original(source,**kwargs)
            if conn.lua.globals().save_requests>requests:
                entered.set()
                await asyncio.Future()
            return result

        conn.execute_write=execute
        task=asyncio.create_task(server.save_game(ctx,'checkpoint'))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(cancel_after_native_entry())
    assert state.save_pending
    assert checkpoints.expected_checkpoint('checkpoint') is None
    calls=len(conn.calls)
    assert run(state.save_game('another')).startswith('ERR:OUTCOME_UNKNOWN|')
    assert run(state.reload_game_save('checkpoint')).startswith('ERR:OUTCOME_UNKNOWN|')
    assert run(state.end_turn()).startswith('ERR:OUTCOME_UNKNOWN|')
    assert run(server.end_turn(ctx)).startswith('ERR:OUTCOME_UNKNOWN|')
    assert run(server.run_lua(ctx,'print("unexpected")')).startswith('ERR:OUTCOME_UNKNOWN|')
    assert conn.lua.globals().save_requests==2
    assert len(conn.calls)==calls
    assert run(state.get_game_identity())==('sumeria',719665280)
    assert state.save_pending


@pytest.mark.parametrize('failure',['throw','false'])
@pytest.mark.parametrize('lost_reply',[False,True])
def test_native_save_failure_without_completion_never_confirms_a_fresh_file(save_environment,failure,lost_reply):
    from civ_mcp import checkpoints
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    conn.lua.globals().save_failure=failure
    conn.lua.execute('''
Network.SaveGame=function(file)
    save_requests=save_requests+1
    write_save(file.Name)
    if save_failure=='throw' then error('native failed after file creation') end
    return false
end
''')
    original=conn.execute_write

    async def execute(source,**kwargs):
        result=await original(source,**kwargs)
        if lost_reply and 'Network.SaveGame' in source:
            raise ConnectionError('save response lost')
        return result

    conn.execute_write=execute
    state=GameState(conn)
    result=run(state.save_game('checkpoint'))
    assert result.startswith('ERR:OUTCOME_UNKNOWN|'),result
    assert state.save_pending
    assert (save_environment/'checkpoint.Civ6Save').exists()
    assert checkpoints.expected_checkpoint('checkpoint') is None
    assert run(state.save_game('another')).startswith('ERR:OUTCOME_UNKNOWN|')
    assert conn.lua.globals().save_requests==1


def test_cancellation_during_final_identity_read_keeps_the_completed_file_untrusted(save_environment):
    from civ_mcp import checkpoints
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    original=conn.execute_write

    async def execute(source,**kwargs):
        if conn.lua.globals().save_requests and 'GAMESEED|' in source:
            raise asyncio.CancelledError()
        return await original(source,**kwargs)

    conn.execute_write=execute
    state=GameState(conn)
    with pytest.raises(asyncio.CancelledError):
        run(state.save_game('checkpoint'))
    assert state.save_pending
    assert (save_environment/'checkpoint.Civ6Save').exists()
    assert checkpoints.expected_checkpoint('checkpoint') is None
    assert conn.lua.globals().save_requests==1


@pytest.mark.parametrize('setup',[
    'Network.SaveGame=nil',
    'SaveLocations=nil',
    'SaveTypes.SINGLE_PLAYER=nil',
    'Events.SaveComplete.Add=function() error("listener unavailable") end',
])
def test_native_save_capability_refusal_preserves_checkpoint_and_allows_later_save(save_environment,setup):
    from civ_mcp import checkpoints
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    state=GameState(conn)
    assert run(state.save_game('checkpoint')).startswith('Saved:')
    expected=checkpoints.expected_checkpoint('checkpoint')
    conn.lua.execute('original_save=Network.SaveGame; original_locations=SaveLocations; original_types=SaveTypes.SINGLE_PLAYER; original_add=Events.SaveComplete.Add')
    conn.lua.execute(setup)
    result=run(state.save_game('checkpoint'))
    assert result.startswith('Error:'),result
    assert not state.save_pending
    assert checkpoints.expected_checkpoint('checkpoint')==expected
    assert conn.lua.globals().save_requests==1
    conn.lua.execute('Network.SaveGame=original_save; SaveLocations=original_locations; SaveTypes.SINGLE_PLAYER=original_types; Events.SaveComplete.Add=original_add')
    assert run(state.save_game('checkpoint')).startswith('Saved:')
    assert conn.lua.globals().save_requests==2


def test_unknown_overwrite_cannot_reuse_old_record_when_record_removal_fails(save_environment,monkeypatch):
    from pathlib import Path
    from civ_mcp import checkpoints
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    state=GameState(conn)
    assert run(state.save_game('checkpoint')).startswith('Saved:')
    assert checkpoints.expected_checkpoint('checkpoint') is not None
    conn.lua.execute('finish_save=false')
    original_unlink=Path.unlink

    def unlink(path,*args,**kwargs):
        if path.parent==checkpoints.CHECKPOINT_DIR:
            raise PermissionError('checkpoint directory became read-only')
        return original_unlink(path,*args,**kwargs)

    monkeypatch.setattr(Path,'unlink',unlink)
    assert run(state.save_game('checkpoint')).startswith('ERR:OUTCOME_UNKNOWN|')
    assert state.save_pending
    assert checkpoints.expected_checkpoint('checkpoint') is None
    assert run(state.restart_and_load('checkpoint')).startswith('Error: Checkpoint')
    assert conn.lua.globals().save_requests==2


@pytest.mark.parametrize('failure',['throw','false'])
@pytest.mark.parametrize('event_first',[False,True])
def test_native_failure_can_confirm_only_after_both_real_completion_and_exact_file(save_environment,failure,event_first):
    from civ_mcp import checkpoints
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    conn.lua.globals().save_failure=failure
    conn.lua.globals().event_first=event_first
    conn.lua.execute('''
Network.SaveGame=function(file)
    save_requests=save_requests+1
    if event_first then emit_save_complete() else write_save(file.Name) end
    if save_failure=='throw' then error('native return failed') end
    return false
end
''')
    state=GameState(conn)
    original=conn.execute_write

    async def go():
        entered,release=asyncio.Event(),asyncio.Event()

        async def execute(source,**kwargs):
            requests=conn.lua.globals().save_requests
            result=await original(source,**kwargs)
            if conn.lua.globals().save_requests>requests:
                entered.set()
                await release.wait()
            return result

        conn.execute_write=execute
        task=asyncio.create_task(state.save_game('checkpoint'))
        await entered.wait()
        assert state.save_pending and not task.done()
        assert checkpoints.expected_checkpoint('checkpoint') is None
        if event_first:
            conn.lua.globals().write_save('checkpoint')
        else:
            conn.lua.globals().emit_save_complete()
        release.set()
        return await task

    assert run(go()).startswith('Saved:')
    assert not state.save_pending
    assert checkpoints.expected_checkpoint('checkpoint')==('sumeria',719665280,244)
    assert conn.lua.globals().save_requests==1


def test_unreadable_save_path_is_refused_before_native_submission(save_environment,monkeypatch):
    from pathlib import Path
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute('winner=nil')
    state=GameState(conn)
    original_stat=Path.stat

    def stat(path,*args,**kwargs):
        if path==save_environment/'checkpoint.Civ6Save':
            raise PermissionError('save directory unavailable')
        return original_stat(path,*args,**kwargs)

    monkeypatch.setattr(Path,'stat',stat)
    result=run(state.save_game('checkpoint'))
    assert result.startswith('Error:'),result
    assert not state.save_pending
    assert conn.lua.globals().save_requests==conn.lua.globals().save_adds==0


def test_living_teammate_display_does_not_change_the_official_result_or_type():
    conn=TerminalConn()
    conn.lua.execute('Game.GetLocalPlayer=function() return 2 end; teams[0]=17; alive=false')
    status=run(GameState(conn).check_game_over())
    assert status is not None and not status.is_defeat
    assert status.winner_name=='Sweden' and status.victory_type=='VICTORY_TECHNOLOGY'


def test_near_religious_majority_and_testvictory_cannot_fill_a_missing_native_type():
    conn=TerminalConn()
    conn.lua.execute('''
selector=nil
Game.TestVictory=function() return true end
for _,player in pairs(Players) do
    player.GetReligion=function() return {
        GetReligionTypeCreated=function() return 7 end,
        GetReligionInMajorityOfCities=function() return 7 end,
    } end
end
''')
    status=run(GameState(conn).check_game_over())
    assert status is not None and status.victory_type=='Unknown'
    assert conn.calls==['write']


@pytest.mark.parametrize('setup',[
    'Game.GetWinningTeam=nil',
    'Game.GetWinningTeam=function() error("winner API unavailable") end',
    'Game.GetWinningTeam=function() return "17" end',
    'winner=nil; Players[0].IsAlive=function() return nil end',
])
def test_native_save_state_unknown_is_zero_submit_without_new_pending(save_environment,setup):
    conn=SaveBoundaryConn(save_environment)
    conn.lua.execute(setup)
    state=GameState(conn)
    result=run(state.save_game('unavailable'))
    assert result.startswith('Error: SAVE_STATE_UNAVAILABLE|'),result
    assert not state.save_pending
    assert conn.lua.globals().save_requests==conn.lua.globals().save_adds==0

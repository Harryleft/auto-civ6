"""A lost checkpoint response must not cause a second native save."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def endurance(monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("hotseat_endurance_test", scripts / "hotseat_endurance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async def active(_):
        return {"terminal": False}
    monkeypatch.setattr(module, "match_status", active)
    return module


def test_lost_save_response_keeps_pending_and_refuses_replay(endurance, monkeypatch, tmp_path):
    (tmp_path / "evidence").mkdir()
    sends = []

    class Lost:
        async def execute_write(self, code):
            sends.append(code)
            raise OSError("response lost after native save submission")

        async def disconnect(self):
            pass

    async def phase(_):
        return ["HS", "true", "0", "12"]

    monkeypatch.setattr(endurance, "GameConnection", Lost)
    monkeypatch.setattr(endurance, "phase", phase)
    with pytest.raises(OSError):
        asyncio.run(endurance.checkpoint(tmp_path, {"turn": 12}))
    assert (tmp_path / "evidence/coordinator-pending.json").exists()
    with pytest.raises(RuntimeError, match="remains unresolved"):
        asyncio.run(endurance.checkpoint(tmp_path, {"turn": 12}))
    assert len(sends) == 1


def test_existing_checkpoint_refuses_before_native_submission(endurance, monkeypatch, tmp_path):
    run = tmp_path / "20261024-090636-t002-t100"
    (run / "evidence").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    target = run / "evidence/dual_endurance_20261024_090636_t012.Civ6Save"
    target.write_bytes(b"preserved checkpoint")
    def forbidden():
        pytest.fail("an existing checkpoint caused a native connection/save")
    monkeypatch.setattr(endurance, "GameConnection", forbidden)
    with pytest.raises(RuntimeError, match="already preserved"):
        asyncio.run(endurance.checkpoint(run, {"turn": 12}))
    assert target.read_bytes() == b"preserved checkpoint"
    assert not (run / "evidence/coordinator-pending.json").exists()


def test_completed_invocation_keeps_original_finish_and_never_saves_again(endurance, monkeypatch, tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    state = {"status": "completed", "finished": 240, "latest": {"turn": 101}}
    (evidence / "controller-state.json").write_text(json.dumps(state))
    def forbidden(*args, **kwargs):
        pytest.fail("completed test performed another game operation")
    monkeypatch.setattr(endurance, "locked_snapshot", forbidden)
    monkeypatch.setattr(sys, "argv", ["hotseat_endurance.py", "--run", str(tmp_path), "--through", "100"])
    endurance.main()
    assert json.loads((evidence / "controller-state.json").read_text()) == state


def test_timing_separates_preparation_and_requires_native_handoffs(endurance, tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "timing-start.json").write_text(json.dumps({"tracking_started_at": 100, "gameplay_started_at": 150}))
    (evidence / "codex-t002-approved-timing.json").write_text(json.dumps({"started": 200, "finished": 210}))
    rows = [{"seat": 1, "turn": 2, "started": 211, "finished": 231, "seconds": 20},
            {"seat": 0, "turn": 3, "started": 232, "finished": 238, "seconds": 6}]
    (evidence / "agent-timing.jsonl").write_text("\n".join(map(json.dumps, rows)))
    receipts = [{"seat": seat, "tool": "end_hotseat_turn", "before": ["HS", "true", str(seat), "2"],
                 "result": "HS_HANDOFF|confirmed"} for seat in (0, 1)]
    (evidence / "agent-tools.jsonl").write_text("\n".join(map(json.dumps, receipts)))
    result = endurance.timing_report(tmp_path, finished=240)
    assert result["preparation_seconds"] == 100
    assert result["total_wall_seconds"] == 140
    assert result["play_wall_seconds"] == 40
    assert result["agent_seconds"] == {"0": 16, "1": 20}
    assert result["rounds"][0] == {"turn": 2, "timeline": 0, "retained_in_current_game": True,
                                    "agents_seconds": 30, "wall_seconds": 31,
                                    "both_handoffs_confirmed": True}
    assert result["rounds"][1]["both_handoffs_confirmed"] is False
    recovery = {"from_timeline": 0, "to_timeline": 1, "checkpoint_turn": 2,
                "failed_turn": 3, "started": 238, "finished": 260}
    (evidence / "recoveries.jsonl").write_text(json.dumps(recovery) + "\n")
    with (evidence / "agent-timing.jsonl").open("a") as stream:
        stream.write("\n" + json.dumps({"timeline": 1, "seat": 0, "turn": 2,
                                      "started": 261, "finished": 266, "seconds": 5}))
    result = endurance.timing_report(tmp_path, finished=270)
    assert result["repair_seconds"] == 22
    assert result["abandoned_agent_seconds"] == 36
    assert result["replayed_agent_seconds"] == 5
    assert all(not r["retained_in_current_game"] for r in result["rounds"] if r["timeline"] == 0)
    fresh = next(r for r in result["rounds"] if r["timeline"] == 1)
    assert fresh["retained_in_current_game"] is True
    assert fresh["both_handoffs_confirmed"] is False  # Old handoffs cannot certify the restored round.


def test_timing_counts_native_congress_handoff_but_not_unconfirmed_votes(endurance, tmp_path):
    evidence=tmp_path/'evidence';evidence.mkdir()
    (evidence/'timing-start.json').write_text(json.dumps({'tracking_started_at':100}))
    rows=[{'seat':seat,'turn':67,'started':200+seat*10,'finished':209+seat*10,'seconds':9} for seat in (0,1)]
    (evidence/'agent-timing.jsonl').write_text('\n'.join(map(json.dumps,rows)))
    receipts=[{'seat':seat,'tool':'queue_wc_votes','before':['HS','true',str(seat),'67'],
               'result':'HS_HANDOFF|from:%s|via:WORLD_CONGRESS'%seat} for seat in (0,1)]
    (evidence/'agent-tools.jsonl').write_text('\n'.join(map(json.dumps,receipts)))
    assert endurance.timing_report(tmp_path,finished=230)['rounds'][0]['both_handoffs_confirmed']
    receipts[1]['result']='ERR:OUTCOME_UNKNOWN|CONTINUE_REQUESTED'
    (evidence/'agent-tools.jsonl').write_text('\n'.join(map(json.dumps,receipts)))
    assert not endurance.timing_report(tmp_path,finished=230)['rounds'][0]['both_handoffs_confirmed']


def test_until_game_over_records_terminal_without_saving_or_clearing_pending(endurance, monkeypatch, tmp_path):
    evidence=tmp_path/'evidence';evidence.mkdir()
    (evidence/'controller-state.json').write_text(json.dumps({'status':'prepared','latest':{'turn':101}}))
    pending={'tool':'unit_action','phase':'submitted','session':'old'}
    (evidence/'shared-pending.json').write_text(json.dumps(pending))
    facts={'turn':185,'seat':1,'phase':None,'players':[],
           'match':{'terminal':True,'winner_team':3,'victory_type':'VICTORY_CULTURE'}}
    monkeypatch.setattr(endurance,'locked_snapshot',lambda _:facts)
    monkeypatch.setattr(endurance,'timing_report',lambda *_:None)
    def forbidden(*_,**__):pytest.fail('terminal game dispatched a model/save')
    monkeypatch.setattr(endurance,'checkpoint',forbidden)
    monkeypatch.setattr(endurance.subprocess,'run',forbidden)
    monkeypatch.setattr(sys,'argv',['hotseat_endurance.py','--run',str(tmp_path),'--until-game-over'])
    endurance.main()
    state=json.loads((evidence/'controller-state.json').read_text())
    assert state['status']=='completed' and state['terminal']['winner_team']==3 and state['pending_request_retained']
    assert json.loads((evidence/'shared-pending.json').read_text())==pending
    assert json.loads((evidence/'terminal.json').read_text())==facts


def test_terminal_checkpoint_refuses_without_native_save(endurance,monkeypatch,tmp_path):
    (tmp_path/'evidence').mkdir()
    class Connection:
        async def disconnect(self):pass
        async def execute_write(self,code):pytest.fail('native save entered after terminal')
    async def terminal(_):return {'terminal':True}
    monkeypatch.setattr(endurance,'GameConnection',Connection)
    monkeypatch.setattr(endurance,'match_status',terminal)
    with pytest.raises(RuntimeError,match='no save submitted'):
        asyncio.run(endurance.checkpoint(tmp_path,{'turn':185}))
    assert not (tmp_path/'evidence/coordinator-pending.json').exists()


def test_terminal_snapshot_survives_missing_ingame_context(endurance,monkeypatch,tmp_path):
    terminal={'terminal':True,'turn':200,'local':1,'winner_team':3,'victory_type':'VICTORY_CULTURE','alive':[True,True]}
    async def status(_):return terminal
    calls=[]
    class CoreOnly:
        async def execute_write(self,code):pytest.fail('terminal snapshot depended on missing InGame context')
        async def execute_in_named_state(self,name,code):
            calls.append(name)
            return ['STAT|0|180|3|21|6|10|38|24','STAT|1|234|5|33|7|100|65|27']
        async def disconnect(self):pass
    monkeypatch.setattr(endurance,'GameConnection',CoreOnly)
    monkeypatch.setattr(endurance,'match_status',status)
    result=asyncio.run(endurance.snapshot(tmp_path))
    assert result['match']==terminal and result['phase'] is None and len(result['players'])==2
    assert calls==['GameCore_Tuner']

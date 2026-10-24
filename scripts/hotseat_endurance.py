#!/usr/bin/env python3
"""Foreground coordinator for one authorized native-hotseat match.

Models select all game actions. This finite coordinator dispatches the owning
CLI, records elapsed time, and writes native checkpoints between human turns.
An unknown shared action or save stops dispatch; nothing is replayed.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from civ_mcp.connection import GameConnection
from hotseat_agent import phase, match_status

ROOT = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")


def write(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def append(path, value):
    with path.open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def timing_report(run, finished=None):
    """Keep wall time distinct from additive agent/save durations."""
    evidence = run / "evidence"
    now = finished or time.time()
    rows = []
    first = evidence / "codex-t002-approved-timing.json"
    if first.exists():
        v = json.loads(first.read_text())
        rows.append(v | {"seat": 0, "turn": 2, "seconds": v["finished"] - v["started"]})
    path = evidence / "agent-timing.jsonl"
    if path.exists():
        rows.extend(json.loads(l) for l in path.read_text().splitlines())
    savespath = evidence / "checkpoints.jsonl"
    saves = [json.loads(l) for l in savespath.read_text().splitlines()] if savespath.exists() else []
    starts = json.loads((evidence / "timing-start.json").read_text())
    play_started = min((v["started"] for v in rows), default=now)
    journal = evidence / "agent-tools.jsonl"
    receipts = [json.loads(l) for l in journal.read_text().splitlines()] if journal.exists() else []
    recovery_path = evidence / "recoveries.jsonl"
    recoveries = [json.loads(l) for l in recovery_path.read_text().splitlines()] if recovery_path.exists() else []
    repairs_path = evidence / "repairs.jsonl"
    repairs = [json.loads(l) for l in repairs_path.read_text().splitlines()] if repairs_path.exists() else []
    rounds = []
    for timeline, turn in sorted({(v.get("timeline", 0), v["turn"]) for v in rows}):
        group = [v for v in rows if v["turn"] == turn and v.get("timeline", 0) == timeline]
        # A finished CLI response is not proof of completed play. Native handoff
        # receipts in the shared journal certify each agent's completed turn.
        completed = {v["seat"] for v in receipts if v.get("tool") in {"end_hotseat_turn", "queue_wc_votes"}
                     and str(v.get("result", "")).startswith("HS_HANDOFF|")
                     and v.get("timeline", 0) == timeline
                     and int(v.get("before", [None] * 4)[3] or -1) == turn}
        retained = not any(r["from_timeline"] == timeline and turn >= r["checkpoint_turn"] for r in recoveries)
        rounds.append({"turn": turn, "timeline": timeline, "retained_in_current_game": retained,
                       "agents_seconds": sum(v["seconds"] for v in group),
                       "wall_seconds": max(v["finished"] for v in group) - min(v["started"] for v in group),
                       "both_handoffs_confirmed": completed == {0, 1}})
    report = {"tracking_started": starts["tracking_started_at"], "play_started": play_started,
              "measured_at": now, "total_wall_seconds": now - starts["tracking_started_at"],
              "play_wall_seconds": now - play_started,
              "preparation_seconds": play_started - starts["tracking_started_at"],
              "agent_seconds": {str(seat): sum(v["seconds"] for v in rows if v["seat"] == seat) for seat in (0, 1)},
              "repair_seconds": sum(r["finished"] - r["started"] for r in recoveries + repairs),
              "forward_confirmation_count": len(repairs),
              "abandoned_agent_seconds": sum(v["seconds"] for v in rows if any(
                  r["from_timeline"] == v.get("timeline", 0) and v["turn"] >= r["checkpoint_turn"] for r in recoveries)),
              "replayed_agent_seconds": sum(v["seconds"] for v in rows if any(
                  r["to_timeline"] == v.get("timeline", 0) and v["turn"] <= r["failed_turn"] for r in recoveries)),
              "save_seconds": sum(v["finished"] - v["started"] for v in saves), "rounds": rounds}
    report["previous_stage_seconds"] = starts.get("previous_stage_seconds", 0)
    report["cumulative_wall_seconds"] = report["previous_stage_seconds"] + report["total_wall_seconds"]
    write(evidence / "timing-summary.json", report)
    return report


async def snapshot(run):
    conn = GameConnection()
    try:
        outcome = await match_status(conn)
        f = None if outcome["terminal"] else await phase(conn)
        code = '''
for id=0,1 do
 local p=Players[id];local pop=0;local units=0
 for _,c in p:GetCities():Members() do pop=pop+c:GetPopulation() end
 for _,u in p:GetUnits():Members() do units=units+1 end
 print("STAT|"..id.."|"..p:GetScore().."|"..p:GetCities():GetCount().."|"..pop.."|"..units.."|"..
 p:GetTreasury():GetGoldBalance().."|"..p:GetTechs():GetScienceYield().."|"..p:GetCulture():GetCultureYield())
end
'''
        # A global win is conclusive even if the terminal screen has already
        # removed InGame. Its statistics are a separate best-effort read.
        statistics_error = None
        if outcome["terminal"]:
            try:
                rows = await conn.execute_in_named_state("GameCore_Tuner", code)
            except Exception as exc:
                rows = []
                statistics_error = str(exc)
        else:
            rows = await conn.execute_write(code)
        stats = []
        for row in rows:
            if row.startswith("STAT|"):
                v = row.split("|")
                stats.append(dict(zip(["seat", "score", "cities", "population", "units", "gold", "science", "culture"], map(float, v[1:]))))
        if len(stats) != 2 and not outcome["terminal"]:
            raise ValueError("Incomplete native statistics: " + repr(rows))
        result = {"time": time.time(), "phase": f, "turn": outcome["turn"], "seat": outcome["local"],
                  "players": stats, "match": outcome}
        if statistics_error:
            result["statistics_error"] = statistics_error
        return result
    finally:
        await conn.disconnect()


def locked_snapshot(run):
    with (run / "evidence/control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return asyncio.run(snapshot(run))


async def checkpoint(run, facts):
    evidence = run / "evidence"
    timeline_file = evidence / "current-timeline.json"
    timeline = json.loads(timeline_file.read_text())["timeline"] if timeline_file.exists() else 0
    suffix = f"_e{timeline}" if timeline else ""
    name = "dual_endurance_" + run.name.split("-t")[0].replace("-", "_") + suffix + f"_t{facts['turn']:03}"
    pending = evidence / "coordinator-pending.json"
    if pending.exists():
        raise RuntimeError("Earlier native save remains unresolved")
    if (evidence / "shared-pending.json").exists():
        raise RuntimeError("Gameplay request remains unresolved; no save submitted")
    source = Path.home() / "Library/Application Support/Sid Meier's Civilization VI/Sid Meier's Civilization VI/Saves/Hotseat" / (name + ".Civ6Save")
    dest = evidence / source.name
    if source.exists() or dest.exists() or (evidence / (name + ".json")).exists():
        raise RuntimeError("Checkpoint name already preserved; no save submitted")
    started = time.time()
    conn = GameConnection()
    try:
        if (await match_status(conn))["terminal"]:
            raise RuntimeError("Native game over; retain the last checkpoint, no save submitted")
        f = await phase(conn)
        if int(f[3]) != facts["turn"] or int(f[2]) != facts.get("seat", 0):
            raise RuntimeError("Checkpoint seat/turn changed")
        write(pending, {"name": name, "started": started, "phase": "possibly_submitted"})
        rows = await conn.execute_write(f'''
assert(GameConfiguration.IsHotseat() and Players[0]:IsHuman() and Players[1]:IsHuman())
assert(Game.GetCurrentGameTurn()=={facts['turn']} and Game.GetLocalPlayer()=={facts.get('seat', 0)})
assert(__enduranceSave==nil or __enduranceSave.done)
__enduranceSave={{done=false,name="{name}"}}
local q=__enduranceSave
q.handler=function()q.done=true;Events.SaveComplete.Remove(q.handler)end
Events.SaveComplete.Add(q.handler)
Network.SaveGame({{Name=q.name,Location=SaveLocations.LOCAL_STORAGE,Type=Network.GetGameConfigurationSaveType(),IsAutosave=false,IsQuicksave=false}})
print("SAVE_SENT|"..q.name)
''')
        if rows != ["SAVE_SENT|" + name]:
            raise RuntimeError("Native save receipt incomplete: " + repr(rows))
        deadline = time.monotonic() + 30
        while True:
            rows = await conn.execute_write('print("SAVE_DONE|"..tostring(__enduranceSave and __enduranceSave.done))')
            if rows == ["SAVE_DONE|true"]:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Native SaveComplete not observed")
            await asyncio.sleep(.2)
        data = source.read_bytes()
        if len(data) < 1000:
            raise RuntimeError("Save file incomplete")
        after = await phase(conn)
        if after[2:4] != f[2:4]:
            raise RuntimeError("Game advanced during checkpoint")
        with dest.open("xb") as stream:
            stream.write(data)
        record = {"name": name, "path": str(source), "copy": str(dest), "size": len(data),
                  "sha256": hashlib.sha256(data).hexdigest(), "turn": facts["turn"],
                  "both_human": True, "started": started, "finished": time.time(),
                  "source_baseline": "benchmark_gold_tun_1", "status": "native_completion_and_file_confirmed"}
        record["timeline"] = timeline
        write(evidence / (name + ".json"), record)
        append(evidence / "checkpoints.jsonl", record)
        pending.unlink()
        return record
    finally:
        await conn.disconnect()


def agent_args(run, state, seat, prompt, prefix):
    server_args = ["run", "--locked", "--directory", str(ROOT), "python", str(ROOT / "scripts/hotseat_agent.py"), "--seat", str(seat), "--run", str(run)]
    if seat == 0:
        config = "{civ6={command=" + json.dumps(UV) + ",args=" + json.dumps(server_args) + ',default_tools_approval_mode="approve",startup_timeout_sec=30,tool_timeout_sec=120}}'
        args = [shutil.which("codex"), "exec"]
        if state.get("codex_session"):
            args += ["resume"]
        args += ["--ignore-user-config", "-c", 'approval_policy="never"', "-c", 'sandbox_mode="read-only"', "-c", 'model_reasoning_effort="low"', "-c", "mcp_servers=" + config,
                 "--json", "-o", str(prefix) + "-final.txt"]
        if state.get("codex_session"):
            args += [state["codex_session"]]
        return args + [prompt]
    config = {"mcpServers": {"civ6": {"command": UV, "args": server_args}}}
    args = [shutil.which("claude"), "--print", "--strict-mcp-config", "--mcp-config", json.dumps(config),
            "--tools", "", "--allowedTools", "mcp__civ6__*", "--permission-mode", "dontAsk", "--effort", "low",
            "--output-format", "stream-json", "--verbose", "--disable-slash-commands"]
    args += ["--resume" if state.get("claude_started") else "--session-id", state["claude_session"]]
    return args + [prompt]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--through", type=int, default=100)
    parser.add_argument("--until-game-over", action="store_true", help="Continue this match until an official native winning team exists")
    args = parser.parse_args()
    run = args.run.resolve()
    evidence = run / "evidence"
    statepath = evidence / "controller-state.json"
    if statepath.exists():
        state = json.loads(statepath.read_text())
    else:
        state = {"codex_session": json.loads((evidence / "codex-session.json").read_text())["session_id"],
                 "claude_session": str(uuid.uuid4()), "started": time.time(), "invocations": 0, "last_checkpoint": 2}
    if state.get("inflight"):
        raise RuntimeError("Previous model invocation has not been reconciled")
    if state.get("status") == "completed" and (state.get("terminal") or (not args.until_game_over and state["latest"]["turn"] > args.through)):
        print(json.dumps({"event": "already_completed", "finished": state["finished"], "turn": state["latest"]["turn"]}))
        return
    no_progress = 0
    while True:
        facts = locked_snapshot(run)
        append(evidence / "snapshots.jsonl", facts)
        state["latest"] = facts
        write(statepath, state)
        print(json.dumps({"event": "state", **facts}, ensure_ascii=False), flush=True)
        if facts.get("match", {}).get("terminal"):
            state.update(status="completed", terminal=facts["match"], finished=time.time())
            state["pending_request_retained"] = any((evidence / name).exists() for name in ("shared-pending.json", "coordinator-pending.json"))
            write(evidence / "terminal.json", facts)
            write(statepath, state)
            timing_report(run, state["finished"])
            break
        if (evidence / "shared-pending.json").exists() or (evidence / "coordinator-pending.json").exists():
            raise RuntimeError("Unresolved request; dispatch stopped for explicit reconciliation")
        alive = facts.get("match", {}).get("alive", [True, True])
        checkpoint_seat = 0 if alive[0] else 1
        finite_complete = not args.until_game_over and facts["turn"] > args.through
        if facts["seat"] == checkpoint_seat and (facts["turn"] - state["last_checkpoint"] >= 10 or finite_complete):
            with (evidence / "control.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                saved = asyncio.run(checkpoint(run, facts))
            state["last_checkpoint"] = facts["turn"]
            write(statepath, state)
            print(json.dumps({"event": "checkpoint", "turn": facts["turn"], "seconds": saved["finished"] - saved["started"]}), flush=True)
        if finite_complete:
            state["status"] = "completed"
            state["finished"] = time.time()
            write(statepath, state)
            timing_report(run, state["finished"])
            break
        seat, turn = facts["seat"], facts["turn"]
        if seat not in (0, 1):
            raise RuntimeError("Unexpected native current seat")
        if not alive[seat]:
            raise RuntimeError("Eliminated native current seat needs explicit UI handling; no model dispatched")
        civ = "苏美尔" if seat == 0 else "瑞典"
        prompt = (f"用户授权四叶草神级原生双人热座持续实测，Codex只控制苏美尔，Claude Code只控制瑞典。现在轮到你控制{civ}(player{seat})的T{turn}。"
                  "只使用提供的civ6 MCP：先get_game_overview，再begin_hotseat_turn，依据本回合真实单位/城市/科研状态决策并执行。"
                  "像正常单人游戏一样发展扩张、探索防御、争取获胜，首都未建就建立，处理生产/科技/市政/政策/时代等必要选择。不要只一律跳过。"
                  "可以保留适合的驻防和长期队列；尽量紧凑查询，避免反复无意义查询。"
                  "已知ID和前提的独立查询/动作可用hotseat_actions显式分组，减少交互等待；依赖结果的动作应读回后再决定。"
                  "工具坐标和ID必须源于真实状态；规则不清楚查知识。禁止shell/文件修改/任意Lua/作弊/automation/玩家切换/退出/重启/读档。"
                  "最后调用end_hotseat_turn，BLOCKED就解决实际阻塞继续；收到HS_HANDOFF后立即回复并结束本次会话响应，不要等待或操作另一席位。"
                  "遇OUTCOME_UNKNOWN保留原意图，只读核对，仍未知则报告停止，绝不能换参数或重新发送绕过。不要写日记。")
        if args.until_game_over:
            prompt += ("本次从已确认T101续局，一直运行到原生整局胜负出现。遵循项目六项策略检查：开始读取get_victory_progress，"
                       "根据当前公开排名、本方获胜里程碑、所有启用路线的对手动向及响应准备时间决定优先事项，"
                       "不要只维持生产队列等待。周期完整复核六项，在现有会话中简记当前目标、准备缺口和下一具体动作，"
                       "不建立额外日记。HS_GAME_OVER或GAME_OVER时立即停止游戏动作并报告，不能只凭分数领先宣布胜利。")
        notice = state.get("resume_notice", {})
        if (notice.get("timeline") == state.get("timeline", 0)
                and notice.get("seat") == seat and notice.get("turn") == turn):
            prompt = notice["text"] + "\n" + prompt
        state["invocations"] += 1
        prefix = evidence / f"{'codex' if seat == 0 else 'claude'}-t{turn:03}-{state['invocations']:03}"
        invocation = {"seat": seat, "turn": turn, "prefix": str(prefix), "started": time.time()}
        invocation["timeline"] = state.get("timeline", 0)
        state["inflight"] = invocation
        write(statepath, state)
        with Path(str(prefix) + ".jsonl").open("x") as out, Path(str(prefix) + ".stderr").open("x") as err:
            proc = subprocess.run(agent_args(run, state, seat, prompt, prefix), stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=ROOT)
        invocation |= {"finished": time.time(), "returncode": proc.returncode}
        invocation["seconds"] = invocation["finished"] - invocation["started"]
        append(evidence / "agent-timing.jsonl", invocation)
        timing_report(run)
        state.pop("inflight")
        if seat == 0 and not state.get("codex_session"):
            events = [json.loads(l) for l in Path(str(prefix) + ".jsonl").read_text().splitlines()]
            state["codex_session"] = next(v["thread_id"] for v in events if v.get("type") == "thread.started")
        if seat == 1:
            state["claude_started"] = True
        write(statepath, state)
        print(json.dumps({"event": "agent_finished", **invocation}), flush=True)
        if proc.returncode:
            raise RuntimeError("Agent process failed; inspect its preserved output")
        after = locked_snapshot(run)
        if after.get("match", {}).get("terminal"):
            continue
        no_progress = no_progress + 1 if after["phase"][2:4] == facts["phase"][2:4] else 0
        if no_progress >= 2:
            raise RuntimeError("Agent twice returned without native handoff; explicit diagnosis required")


if __name__ == "__main__":
    main()

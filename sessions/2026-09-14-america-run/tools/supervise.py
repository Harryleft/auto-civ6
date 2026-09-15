#!/usr/bin/env python3
"""总控：把「守护进程 + 自动执政官」跑成无人值守循环，崩溃后自动恢复。

为什么需要它：
  - Civ VI 引擎有 JobSet/TBB 线程生命周期 bug，AI 回合处理期可能卡死或崩溃
    （仓库 docs/agent-recovery.md 有记录；缓解措施是 MaxJobThreads=4）。
  - MCP 是串行的：end_turn 一旦挂住，别的调用进不来，只能杀掉 MCP 进程重启。
  - 所以需要一个外层：守护进程 → 执政官 → 出错就恢复游戏 → 再来一轮。

恢复流程：杀游戏（如仍在）→ 重启 → 等主菜单 → 用 FrontEnd API 读最新
0_MCP_*.Civ6Save（end_turn 每回合自动存，损失 0 回合）。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(r"S:\vibe_coding\civ6-map-analysis")
REPO = Path(r"S:\vibe_coding\auto-civ6")
LOG = HERE / "_supervise.log"
SAVES = Path(os.path.expanduser(
    r"~\Documents\My Games\Sid Meier's Civilization VI\Saves\Single"))

sys.path.insert(0, str(REPO / "src"))


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _uv_python(script: str) -> list[str]:
    return ["uv", "run", "--directory", str(REPO), "python", str(HERE / script)]


def start_daemon() -> subprocess.Popen:
    # 先清掉上一轮的日志与命令/结果文件，再启动守护进程。
    # 否则会读到上一轮残留的 "MCP ready"，误判为就绪而提前启动执政官；
    # 而守护进程启动时会删除并重建 _cmd.jsonl，把刚写进去的命令抹掉，
    # 执政官就会一直等结果直到超时。
    for name in ("_daemon.log", "_cmd.jsonl", "_out.jsonl", "_play.log"):
        try:
            (HERE / name).unlink()
        except OSError:
            pass

    env = {**os.environ, "CIV_MCP_BELIEF_MODE": "observe"}
    p = subprocess.Popen(_uv_python("mcp_daemon.py"), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(90):
        time.sleep(1)
        try:
            txt = (HERE / "_daemon.log").read_text(encoding="utf-8")
        except OSError:
            continue
        if "MCP ready" in txt:
            log("守护进程就绪")
            time.sleep(2)          # 让 MCP 侧连接稳定下来再发命令
            return p
        if p.poll() is not None:
            break
    log("警告：守护进程未在 90 秒内就绪")
    return p


def stop_daemon(p: subprocess.Popen) -> None:
    # 只杀守护进程自己的进程树（它会派生 uv/civ-mcp 子进程）。
    # 绝不能 taskkill /IM python.exe —— 那会把总控自己也杀掉。
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        p.wait(timeout=15)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(3)


def latest_mcp_save() -> str | None:
    saves = sorted(SAVES.glob("0_MCP_*.Civ6Save"),
                   key=lambda p: (p.stat().st_mtime_ns, p.name))
    return saves[-1].stem if saves else None


def game_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CivilizationVI_DX12.exe"],
                         capture_output=True, text=True).stdout
    return "CivilizationVI_DX12" in out


def kill_game() -> None:
    subprocess.run(["taskkill", "/F", "/IM", "CivilizationVI_DX12.exe"],
                   capture_output=True)
    time.sleep(10)


async def _wait_menu_and_load(save: str) -> bool:
    from civ_mcp.connection import GameConnection
    from civ_mcp.game_lifecycle import load_save_from_frontend

    conn = GameConnection()
    for attempt in range(40):          # 最多 ~200 秒等主菜单
        try:
            if conn.is_connected:
                await conn.reconnect()
            else:
                await conn.connect()
            if "MainMenu" in conn.lua_states.values():
                log(f"主菜单就绪（第 {attempt} 次探测）")
                break
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(5)
    else:
        log("主菜单迟迟未出现")
        return False

    result = await load_save_from_frontend(conn, save)
    log(f"读档请求: {result}")

    for _ in range(20):                 # 最多 ~100 秒等进入对局
        await asyncio.sleep(5)
        try:
            await conn.reconnect()
        except Exception:  # noqa: BLE001
            continue
        if conn.gamecore_index is not None and conn.ingame_index is not None:
            await conn.disconnect()
            return True
    try:
        await conn.disconnect()
    except Exception:  # noqa: BLE001
        pass
    return False


def recover() -> bool:
    save = latest_mcp_save()
    if save is None:
        log("找不到 0_MCP_*.Civ6Save，无法恢复")
        return False
    log(f"恢复：目标存档 {save}")
    if game_running():
        log("先结束仍在运行的文明 VI")
        kill_game()
    time.sleep(3)
    # Windows 上拉起 Steam 协议链接最可靠的是 os.startfile；
    # subprocess 里跑 cmd 的 start 经常静默失败。
    try:
        os.startfile("steam://run/289070")  # noqa: S606
        log("已请求启动文明 VI (os.startfile)")
    except Exception as exc:  # noqa: BLE001
        log(f"os.startfile 失败: {exc}，改用 cmd start")
        subprocess.Popen(["cmd", "/c", "start", "", "steam://run/289070"])
    ok = asyncio.run(_wait_menu_and_load(save))
    log(f"恢复结果: {'成功' if ok else '失败'}")
    return ok


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    turns = int(sys.argv[2]) if len(sys.argv) > 2 else 40

    for r in range(rounds):
        log(f"===== 第 {r + 1}/{rounds} 轮：启动守护进程 =====")
        daemon = start_daemon()
        try:
            log(f"运行执政官（{turns} 回合上限）")
            rc = subprocess.run(
                _uv_python("play.py") + ["--max-turns", str(turns)],
                cwd=str(HERE),
            ).returncode
            log(f"执政官退出，rc={rc}")
        finally:
            stop_daemon(daemon)

        if rc == 0:
            log("执政官正常收尾（可能是游戏结束或达到回合上限）")
            return 0

        log("异常退出，进入恢复流程")
        if not recover():
            log("恢复失败，停止")
            return 1
        time.sleep(5)

    log("达到轮次上限，停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

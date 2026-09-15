#!/usr/bin/env python3
"""常驻 Civ VI MCP 守护进程。

为什么需要它：
  1. FireTuner 经不起反复连接——每次「连接/断开」都会在 4318 留 TIME_WAIT，
     几十次之后 tuner 会退化成「能握手但不执行」，只能重启游戏。
     所以整个对局期间只允许一个 MCP 进程，长期持有连接。
  2. Civ VI 在窗口失焦时会暂停游戏核心，GameCore/InGame 的 Lua 完全不执行。
     所以每次真正调用工具前，先把游戏窗口切到前台。

工作方式：
  - 启动一个 `uv run --directory <repo> civ-mcp` 子进程，走 stdio 上的 MCP 协议。
  - 轮询 CMD 文件（JSONL，每行一个 {"tool": ..., "args": {...}}）。
  - 执行前 focus 游戏窗口，执行后把结果追加到 OUT 文件（JSONL）。
  - 收到 {"tool": "__stop__"} 就退出。

用法：
  python mcp_daemon.py            # 前台常驻（建议用后台作业启动）
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(r"S:\vibe_coding\auto-civ6")
HERE = Path(r"S:\vibe_coding\civ6-map-analysis")
CMD = HERE / "_cmd.jsonl"
OUT = HERE / "_out.jsonl"
LOG = HERE / "_daemon.log"

sys.path.insert(0, str(REPO))


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


# ── 焦点：调用前把游戏切到前台 ─────────────────────────────────────────────
# 必须真正抢到前台：Civ VI 在窗口失焦时会暂停游戏核心，GameCore/InGame 的
# Lua 就完全不执行，MCP 调用会一直等到超时。单纯 SetForegroundWindow 经常被
# Windows 的前台锁定规则挡下，所以走 AttachThreadInput 强制附加到当前前台线程。
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
_user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_kernel32.GetCurrentThreadId.restype = ctypes.c_ulong


def _find_civ() -> int:
    for title in ("Sid Meier's Civilization VI (DX12)", "Sid Meier's Civilization VI"):
        h = _user32.FindWindowW(None, title)
        if h:
            return h
    return 0


def focus_game() -> bool:
    h = _find_civ()
    if not h:
        return False
    if _user32.GetForegroundWindow() == h:
        return True

    fg = _user32.GetForegroundWindow()
    t_fg = _user32.GetWindowThreadProcessId(fg, None)
    t_me = _kernel32.GetCurrentThreadId()
    try:
        _user32.AttachThreadInput(t_me, t_fg, True)
        _user32.ShowWindow(h, 9)          # SW_RESTORE
        _user32.BringWindowToTop(h)
        _user32.SetForegroundWindow(h)
        _user32.AttachThreadInput(t_me, t_fg, False)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(1.0)
    return _user32.GetForegroundWindow() == h


class FocusKeeper:
    """在一次工具调用期间持续把游戏抢回前台。

    只聚焦一次是不够的：Windows 的前台锁定、以及别的进程抢焦点，
    都会让游戏在调用过程中失去前台，而 Civ VI 一旦失焦就暂停游戏核心，
    GameCore/InGame 的 Lua 立刻不执行，整个 MCP 调用会一直挂到超时。
    """

    def __init__(self, interval: float = 1.2) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failures = 0

    def __enter__(self) -> "FocusKeeper":
        focus_game()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not focus_game():
                self.failures += 1
            self._stop.wait(self._interval)


# ── 主循环 ────────────────────────────────────────────────────────────────
async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    for p in (CMD, OUT, LOG):
        if p.exists():
            p.unlink()

    CMD.touch()
    OUT.touch()

    params = StdioServerParameters(
        command="uv",
        args=["run", "--directory", str(REPO), "civ-mcp"],
        # MCP SDK 默认只传安全子集环境变量（会丢掉 CIV_MCP_BELIEF_MODE 等），
        # 这里显式透传完整环境。
        env=dict(os.environ),
    )

    log("starting MCP server...")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            log(f"MCP ready, {len(names)} tools")
            (HERE / "_tools.txt").write_text("\n".join(names), encoding="utf-8")

            pos = 0
            while True:
                try:
                    text = CMD.read_text(encoding="utf-8")
                except OSError:
                    await asyncio.sleep(0.5)
                    continue

                lines = [ln for ln in text.splitlines() if ln.strip()]
                while pos < len(lines):
                    raw = lines[pos]
                    pos += 1
                    try:
                        cmd = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    tool = cmd.get("tool")
                    if tool == "__stop__":
                        log("stop requested")
                        return 0

                    t0 = time.time()
                    keeper = FocusKeeper()
                    try:
                        with keeper:
                            res = await session.call_tool(tool, cmd.get("args") or {})
                        payload = "\n".join(
                            i.text for i in res.content if i.type == "text"
                        )
                        record = {
                            "ok": not res.isError,
                            "tool": tool,
                            "focus_failures": keeper.failures,
                            "seconds": round(time.time() - t0, 1),
                            "result": payload,
                        }
                    except Exception as exc:  # noqa: BLE001
                        record = {
                            "ok": False,
                            "tool": tool,
                            "focus_failures": keeper.failures,
                            "seconds": round(time.time() - t0, 1),
                            "result": f"{type(exc).__name__}: {exc}",
                        }
                    with OUT.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                        fh.flush()
                    log(f"{tool} ok={record['ok']} focus_fail={keeper.failures} "
                        f"{record['seconds']}s")

                await asyncio.sleep(0.4)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

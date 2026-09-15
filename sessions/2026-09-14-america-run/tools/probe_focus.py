#!/usr/bin/env python3
"""在保持 Civ VI 前台的前提下测试 GameCore/InGame 的 Lua 执行。

Civ VI 在窗口失去焦点时会暂停游戏核心，导致 GameCore_Tuner 的 Lua 不执行。
从终端运行时，终端会抢走焦点，所以这里在连接后先主动把游戏窗口切到前台，
再发查询。
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import time

REPO_SRC = r"S:\vibe_coding\auto-civ6\src"
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from civ_mcp.connection import GameConnection  # noqa: E402
from civ_mcp import lua as lq  # noqa: E402


def focus_game() -> bool:
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "Sid Meier's Civilization VI (DX12)")
    if not hwnd:
        hwnd = user32.FindWindowW(None, "Sid Meier's Civilization VI")
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.5)
    return user32.GetForegroundWindow() == hwnd


async def main() -> int:
    conn = GameConnection()
    await conn.connect()
    print(f"states={len(conn.lua_states)} gamecore={conn.gamecore_index} ingame={conn.ingame_index}")

    focused = focus_game()
    print(f"focus_game -> {focused}")

    code = "print('GC_PONG')\nprint([==[" + lq.SENTINEL + "]==])"
    for label, idx in (("GameCore", conn.gamecore_index), ("InGame", conn.ingame_index)):
        if idx is None:
            print(f"{label}: 状态缺失")
            continue
        for attempt in (1, 2):
            try:
                r = await conn.execute_in_state(idx, code, timeout=12.0, require_sentinel=True)
                print(f"{label} attempt{attempt}: OK -> {r!r}")
                break
            except Exception as exc:  # noqa: BLE001
                print(f"{label} attempt{attempt}: FAIL -> {type(exc).__name__}: {str(exc)[:110]}")

    await conn.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

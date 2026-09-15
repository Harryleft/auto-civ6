#!/usr/bin/env python3
"""用 Civ VI 的 FrontEnd API 从主菜单直接载入一个存档/建局配置。

这是仓库 `src/civ_mcp/game_lifecycle.py::load_save_from_frontend` 的一次性调用，
不启动 MCP 服务，连完即断（FireTuner 只允许一个客户端）。

用法（在 auto-civ6 仓库的 venv 下）：
    uv run --directory S:/vibe_coding/auto-civ6 python <本脚本> --list
    uv run --directory S:/vibe_coding/auto-civ6 python <本脚本> --name ai-civ6-map-01
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

REPO_SRC = r"S:\vibe_coding\auto-civ6\src"
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from civ_mcp.connection import GameConnection  # noqa: E402
from civ_mcp.game_lifecycle import load_recovery_save_from_frontend  # noqa: E402
from civ_mcp.game_lifecycle import load_save_from_frontend  # noqa: E402
from civ_mcp.game_launcher import get_latest_recovery_save  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="存档/配置文件名（不带 .Civ6Save/.Civ6Cfg 后缀）")
    ap.add_argument("--recovery", action="store_true",
                    help="复用仓库的恢复逻辑：挑最新 0_MCP_*/AutoSave_* 并加载")
    ap.add_argument("--list", action="store_true", help="只列出 Lua 状态，不做任何动作")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4318)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    conn = GameConnection(host=args.host, port=args.port)
    await conn.connect()

    print("=== Lua states ===")
    for idx, name in sorted(conn.lua_states.items()):
        print(f"  [{idx}] {name}")

    have = set(conn.lua_states.values())
    print()
    print(f"MainMenu        : {'YES' if 'MainMenu' in have else 'no'}")
    print(f"GameCore_Tuner  : {'YES' if 'GameCore_Tuner' in have else 'no'}")
    print(f"InGame          : {'YES' if 'InGame' in have else 'no'}")

    rc = 0
    if args.recovery and not args.list:
        name = get_latest_recovery_save()
        print()
        print(f"仓库恢复逻辑选中的存档: {name!r}")
        if name is None:
            print("没有可用的恢复存档")
            rc = 3
        else:
            result = await load_recovery_save_from_frontend(conn, name)
            print(f"结果: {result}")
            if result.startswith("Error"):
                rc = 2
    elif args.name and not args.list:
        print()
        print(f"=== 通过 FrontEnd API 载入 {args.name!r} ===")
        result = await load_save_from_frontend(conn, args.name)
        print(f"结果: {result}")
        if result.startswith("Error"):
            rc = 2

    await conn.disconnect()
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

#!/usr/bin/env python3
"""OCR 导航：主菜单 → 单人模式 → 加载游戏 → 选中存档/配置 → 载入。

直接复用仓库 `civ_mcp.game_launcher._navigate_to_save_sync`（跨平台，
Windows 走 winrt OCR + SendInput 点击）。

用法：
    uv run --directory S:/vibe_coding/auto-civ6 python <本脚本> ai-civ6-map-01
    uv run --directory S:/vibe_coding/auto-civ6 python <本脚本> AutoSave_0105 --tab Autosaves
"""

from __future__ import annotations

import argparse
import logging
import sys

REPO_SRC = r"S:\vibe_coding\auto-civ6\src"
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("save", help="要载入的存档/配置显示名（不带扩展名）")
    ap.add_argument("--tab", default=None,
                    help="过滤标签，如 Autosaves；省略则用默认（普通存档）列表")
    ap.add_argument("--ocr-only", action="store_true",
                    help="只对 Civ6 窗口做一次 OCR 并把文字打印出来，不点击")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    from civ_mcp import game_launcher as gl

    if args.ocr_only:
        win = gl._find_game_window()
        if win is None:
            print("找不到 Civ6 窗口")
            return 1
        print(f"窗口: x={win.x} y={win.y} w={win.w} h={win.h}")
        results = gl._ocr_game_window(win)
        print(f"OCR 命中 {len(results)} 段文字：")
        for text, x, y, w, h in results:
            print(f"   ({x:5d},{y:5d}) {w:4d}x{h:3d}  {text}")
        return 0

    print(f"目标: {args.save}  tab={args.tab}")
    result = gl._navigate_to_save_sync(args.save, tab=args.tab)
    print(f"\nRESULT: {result}")
    return 0 if not str(result).startswith("FAILED") else 2


if __name__ == "__main__":
    raise SystemExit(main())

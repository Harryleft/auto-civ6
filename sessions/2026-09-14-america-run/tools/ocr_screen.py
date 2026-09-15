#!/usr/bin/env python3
"""抓取整个屏幕并用 Windows OCR 读出文字（不需要看图）。

用于判断 Civ VI 的真实画面内容：`PrintWindow` 在 DX12 下常常只返回背景层，
而屏幕抓取拿到的是实际显示内容。

用法：
    uv run --directory S:/vibe_coding/auto-civ6 python <本脚本> [图片路径]
若给了图片路径就 OCR 该图，否则现场抓屏。
"""

from __future__ import annotations

import sys

REPO_SRC = r"S:\vibe_coding\auto-civ6\src"
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from PIL import Image, ImageGrab  # noqa: E402

from civ_mcp import game_launcher as gl  # noqa: E402


def main() -> int:
    if len(sys.argv) > 1:
        img = Image.open(sys.argv[1]).convert("RGB")
        print(f"图片: {sys.argv[1]}  {img.width}x{img.height}")
    else:
        img = ImageGrab.grab()
        print(f"抓屏: {img.width}x{img.height}")
        img.convert("RGB").save(r"S:\vibe_coding\civ6-map-analysis\_grab.png")

    results = gl._ocr_winrt(img, 0, 0, img.width, img.height)
    print(f"OCR 命中 {len(results)} 段文字：")
    for text, x, y, w, h in results:
        print(f"   ({x:5d},{y:5d}) {w:4d}x{h:3d}  {text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

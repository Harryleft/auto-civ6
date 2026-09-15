#!/usr/bin/env python3
"""Civ VI 中文界面 OCR 导航（Windows）。

仓库自带的 `_ocr_winrt` 用 `try_create_from_user_profile_languages()` 会选到
英文识别器，导致中文菜单读不出来（表现为一串乱码）。本脚本显式使用
`zh-Hans-CN` 引擎，并把 OCR 结果里的空格去掉后再匹配，从而能可靠点击中文菜单。

子命令：
    ocr                       打印当前屏幕（Civ6 窗口）的 OCR 结果
    click <文字> [--nth N]    点击匹配到的第 N 个匹配项（默认第 1 个）
    focus                     把 Civ6 窗口切到前台
"""

from __future__ import annotations

import argparse
import asyncio
import io
import re
import sys
import time
import ctypes

REPO_SRC = r"S:\vibe_coding\auto-civ6\src"
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from PIL import Image, ImageGrab  # noqa: E402

from civ_mcp import game_launcher as gl  # noqa: E402


def _norm(s: str) -> str:
    """去掉所有空白并转小写，兼容中文 OCR 的字间空格。"""
    return re.sub(r"\s+", "", s).lower()


# ── OCR（显式中文引擎） ────────────────────────────────────────────────────

async def _ocr_async(png: bytes, tag: str = "zh-Hans-CN"):
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_language(Language(tag))
    if engine is None:
        raise RuntimeError(f"无法创建 {tag} OCR 引擎")
    return await engine.recognize_async(bitmap)


def ocr_screen() -> tuple[Image.Image, list[tuple[str, int, int, int, int]]]:
    """抓屏并 OCR，返回 (图像, [(归一化文字, cx, cy, w, h), ...])。"""
    img = ImageGrab.grab().convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = asyncio.run(_ocr_async(buf.getvalue()))

    iw, ih = img.size
    out: list[tuple[str, int, int, int, int]] = []
    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        x0 = min(w.bounding_rect.x for w in words)
        y0 = min(w.bounding_rect.y for w in words)
        x1 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
        y1 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
        cx = int((x0 + x1) / 2)
        cy = int((y0 + y1) / 2)
        out.append((_norm(line.text), cx, cy, int(x1 - x0), int(y1 - y0)))
    return img, out


# ── 窗口聚焦 ───────────────────────────────────────────────────────────────

def focus_game() -> bool:
    """把 Civ6 主窗口切到前台。返回是否成功。"""
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "Sid Meier's Civilization VI (DX12)")
    if not hwnd:
        hwnd = user32.FindWindowW(None, "Sid Meier's Civilization VI")
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 5)          # SW_SHOW
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.6)
    return user32.GetForegroundWindow() == hwnd


def click_at(x: int, y: int) -> None:
    gl._click_win32(x, y)


# ── 匹配 ───────────────────────────────────────────────────────────────────

def find_all(lines, needle: str):
    n = _norm(needle)
    return [ln for ln in lines if n in ln[0]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ocr", "click", "focus", "loop"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--nth", type=int, default=1)
    ap.add_argument("--min-y", type=int, default=None, help="只考虑 y 大于该值的匹配项")
    ap.add_argument("--max-y", type=int, default=None, help="只考虑 y 小于该值的匹配项")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--wait", type=float, default=1.5, help="点击后等待秒数")
    ap.add_argument("--no-focus", action="store_true")
    args = ap.parse_args()

    if args.cmd == "focus":
        print("focus:", focus_game())
        return 0

    if args.cmd == "ocr":
        img, lines = ocr_screen()
        print(f"抓屏 {img.width}x{img.height}，命中 {len(lines)} 行：")
        for text, x, y, w, h in lines:
            print(f"   ({x:5d},{y:5d}) {w:4d}x{h:3d}  {text}")
        return 0

    if args.cmd == "click":
        if not args.text:
            print("需要 --text"); return 2
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if not args.no_focus:
                focus_game()
            _img, lines = ocr_screen()
            hits = find_all(lines, args.text)
            if args.min_y is not None:
                hits = [h for h in hits if h[2] >= args.min_y]
            if args.max_y is not None:
                hits = [h for h in hits if h[2] <= args.max_y]
            if len(hits) >= args.nth:
                text, x, y, w, h = hits[args.nth - 1]
                print(f"命中 '{args.text}' -> 第{args.nth}个 ({x},{y}) [{text}]")
                click_at(x, y)
                time.sleep(args.wait)
                print("clicked")
                return 0
            print(f"未找到 '{args.text}'（当前 {len(hits)} 个匹配），重试…")
            time.sleep(2)
        print(f"超时：未找到 '{args.text}'")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
